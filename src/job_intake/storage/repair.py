"""Prepare offline repairs on a copy, then apply the reviewed patch under a writer lock."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter
from contextlib import closing
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from job_intake.annotation.eval_runner import sqlite_path
from job_intake.config.settings import load_app_config
from job_intake.pipeline import JobIntakePipeline
from job_intake.storage.models import JobORM
from job_intake.storage.snapshot import (
    _inventory,
    _logical_digest,
    _read_only,
    snapshot_audit_state,
)
from job_intake.storage.source_state import _fact_metadata, record_from_saved

REPAIR_VERSION = "offline-repair-v1"
_PATCH_TABLES = (
    "jobs",
    "job_relevance_evaluations",
    "job_events",
    "job_source_aliases",
    "job_purpose_assessments",
    "source_runs",
)
_STABLE_JOB_FIELDS = (
    "job_uid",
    "posted_at",
    "first_seen_at",
    "last_seen_at",
    "created_at",
    "source",
    "source_job_id",
    "original_url",
    "apply_url",
    "description_raw",
    "description_clean",
    "status",
    "archived_at",
    "archive_version",
    "alert_pending",
    "last_alerted_at",
    "last_alerted_tier",
    "company",
    "title",
    "location_text",
    "remote_text",
    "employment_type",
    "salary_text",
    "timezone_text",
)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _identifier(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _rows(connection, table: str) -> list[dict]:
    columns = [row[1] for row in connection.execute(f"PRAGMA table_info({_identifier(table)})")]
    if not columns:
        return []
    return [
        dict(zip(columns, row, strict=True))
        for row in connection.execute(
            f"SELECT * FROM {_identifier(table)} ORDER BY 1",
        )
    ]


def _protected(connection) -> str:
    names = [
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'",
        )
        if row[0].startswith("crm_")
        or row[0]
        in {
            "alert_outbox",
            "job_feedback",
            "job_profile_evaluations",
        }
    ]
    data = {name: _rows(connection, name) for name in sorted(names)}
    defaults = {"archive_version": 1, "alert_pending": 0}
    data["job_source_and_dates"] = [
        {key: row.get(key, defaults.get(key)) for key in _STABLE_JOB_FIELDS}
        for row in _rows(connection, "jobs")
    ]
    for row, original in zip(data["job_source_and_dates"], _rows(connection, "jobs"), strict=True):
        row["source_metadata_facts"] = _fact_metadata(
            json.loads(
                original.get("source_metadata") or "{}",
            )
        )
    data["human_purpose"] = [
        {
            key: row.get(key)
            for key in (
                "job_uid",
                "human_p1",
                "human_p2",
                "human_note",
                "human_source_hash",
                "human_rubric_version",
                "human_p1_rubric_version",
                "human_p2_rubric_version",
                "human_p1_source_hash",
                "human_p2_source_hash",
            )
        }
        for row in _rows(connection, "job_purpose_assessments")
        if (
            row.get("human_p1", "unknown") != "unknown"
            or row.get("human_p2", "unknown") != "unknown"
            or row.get("human_note")
            or row.get("human_source_hash")
        )
    ]
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()


def _code_hash(config: Path) -> str:
    package = Path(__file__).resolve().parents[1]
    settings = load_app_config(config)
    paths = {
        *package.rglob("*.py"),
        config.resolve(),
        settings.rules_path,
        settings.search_profiles_path,
        settings.company_watchlist_path,
        package.parent.parent / "config/purpose_rubric.yaml",
    }
    return hashlib.sha256(
        json.dumps(
            {str(path): _digest(path) for path in sorted(paths) if path.is_file()}, sort_keys=True
        ).encode()
    ).hexdigest()


def _domain_hash(connection) -> str:
    tables = sorted(
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name!='repair_applications'",
        )
    )
    value = {
        name: {
            "rows": _rows(connection, name),
            "columns": connection.execute(
                f"PRAGMA table_info({_identifier(name)})",
            ).fetchall(),
        }
        for name in tables
    }
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def _application(database: Path, report_hash: str):
    with closing(_read_only(database)) as connection:
        if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='repair_applications'"
        ).fetchone():
            row = connection.execute(
                "SELECT result FROM repair_applications WHERE report_hash=?", (report_hash,)
            ).fetchone()
            if row:
                return json.loads(row[0])
    return None


def _state(rows: list[dict]) -> dict[str, dict]:
    result = {
        row["job_uid"]: {
            key: row.get(key)
            for key in (
                "role_label",
                "tier",
                "filter_decision",
                "fit_score",
                "raw_fit_score",
                "eligibility_status",
                "eligibility_evidence_status",
                "content_hash",
                "fit_reason",
                "score_cap_reasons",
            )
        }
        for row in rows
    }
    for row in rows:
        try:
            annotation = json.loads(row.get("annotation") or "{}")
        except (TypeError, ValueError):
            annotation = {}
        result[row["job_uid"]].update(
            annotation_method=annotation.get("method"), annotation_status=annotation.get("status")
        )
    return result


def prepare_repair(config: Path, output: Path) -> dict:
    """Create an immutable report/candidate without changing live DB, labels or caches."""
    if output.exists():
        raise ValueError("Repair output exists; choose a new directory")
    output.mkdir(parents=True)
    try:
        settings = load_app_config(config)
        contract = _code_hash(config)
        snapshot = snapshot_audit_state(config, output / "backup")
        candidate = output / "candidate.db"
        with closing(_read_only(output / "backup/job_intake.db")) as source:
            before = _state(_rows(source, "jobs"))
            protected_before = _protected(source)
            with closing(sqlite3.connect(candidate)) as target:
                source.backup(target)
        prepared_config = replace(
            settings,
            database_url=f"sqlite:///{candidate.resolve()}",
            sources=[],
            telegram=replace(settings.telegram, enabled=False),
            llm=replace(settings.llm, enabled=False),
            annotation=replace(
                settings.annotation, ai_enabled=False, cache_dir=str(output / "backup/annotations")
            ),
            prefilter=replace(settings.prefilter, cache_dir=str(output / "backup/prefilter")),
        )
        pipeline = JobIntakePipeline(prepared_config)
        stats = pipeline._stats()
        recovery = Counter()
        with pipeline.database.session() as session:
            records = [
                record_from_saved(job)
                for job in session.scalars(
                    select(JobORM).order_by(JobORM.job_uid),
                )
            ]
        for record in records:
            previous = deepcopy(record.annotation)
            recoverable = pipeline.annotator.inspect_cached_review(record)
            if recoverable is not None:
                record.annotation = recoverable["annotation"]
                recovery["compatible_paid_file"] += 1
            elif previous.get("method") == "two_pass":
                recovery["saved_paid_requires_validation"] += 1
            else:
                recovery["no_compatible_paid_review"] += 1
            items, keys, pending = pipeline._evaluate_record(record, stats, use_llm=False)
            if pending:
                raise ValueError("Offline repair unexpectedly scheduled paid work")
            pipeline._save(items, keys, observed=False, queue_alert=False)
            stats["persisted"] += 1
            recovery["active_" + record.annotation.get("method", "none")] += 1
        pipeline.database.engine.dispose()
        with closing(_read_only(candidate)) as target:
            after = _state(_rows(target, "jobs"))
            if set(before) != set(after) or _protected(target) != protected_before:
                raise ValueError("Offline repair changed CRM, source facts, dates or notifications")
            if target.execute("PRAGMA foreign_key_check").fetchall():
                raise ValueError("Offline candidate has invalid foreign keys")
        transitions = [
            {"job_uid": uid, "before": before[uid], "after": after[uid]}
            for uid in sorted(before)
            if before[uid] != after[uid]
        ]
        report = {
            "version": REPAIR_VERSION,
            "status": "prepared",
            "created_at": datetime.now(UTC).isoformat(),
            "api_calls": 0,
            "config": str(config.resolve()),
            "code_contract": contract,
            "source_database": snapshot["source_database"],
            "source_logical_sha256": snapshot["verification"]["restored_logical_sha256"],
            "candidate_sha256": _digest(candidate),
            "protected_sha256": protected_before,
            "source_manifest_sha256": _digest(output / "backup/manifest.json"),
            "stats": stats,
            "cache_recovery": dict(recovery),
            "jobs": len(before),
            "transitions": transitions,
            "protected_state_unchanged": True,
            "candidate_read_only_evaluation_only": True,
        }
        if contract != _code_hash(config):
            raise ValueError("Code or config changed during preparation; prepare a new repair")
        path = output / "report.json"
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        (output / "report.sha256").write_text(_digest(path) + "\n")
        (output / "report.md").write_text(
            f"# Offline repair\n\nJobs: {len(before)}; changed assessments: {len(transitions)}.\n"
            f"API calls: 0. Source/CRM/dates/notifications preserved.\n\n"
            "Prepared candidate is not the live database. Exact transitions are in report.json.\n",
        )
        return report
    except BaseException:
        # Keep incomplete artifacts for diagnosis, but never publish a ready report.
        if not (output / "report.json").exists():
            (output / "INCOMPLETE").write_text("Preparation failed; do not apply.\n")
        raise


def _verify_artifacts(report_dir: Path, report: dict) -> dict:
    manifest_path = report_dir / "backup/manifest.json"
    if _digest(manifest_path) != report["source_manifest_sha256"]:
        raise ValueError("Repair backup manifest changed")
    manifest = json.loads(manifest_path.read_text())
    local = Path(report["source_database"]).parent
    # Saved manifests name artifact roots independently of their configured location.
    settings = load_app_config(report["config"])
    roots = {
        "gold": local / "gold",
        "annotations": Path(settings.annotation.cache_dir),
        "prefilter": Path(settings.prefilter.cache_dir),
        "discovery": local / "discovery",
    }
    inventory = _inventory(roots)
    expected = {
        name: item["sha256"] for name, item in manifest["files"].items() if name != "job_intake.db"
    }
    if {name: digest for name, (_, digest) in inventory.items()} != expected:
        raise ValueError("Gold, discovery or paid cache changed; prepare a new repair")
    return manifest


def _apply_schema(connection, candidate: Path) -> None:
    connection.execute("ATTACH DATABASE ? AS prepared", (str(candidate.resolve()),))
    existing = {row[0] for row in connection.execute("SELECT name FROM main.sqlite_master")}
    for table in _PATCH_TABLES:
        ddl = connection.execute(
            "SELECT sql FROM prepared.sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        if ddl is None:
            continue
        if table not in existing:
            connection.execute(ddl[0])
        current = {
            row[1]
            for row in connection.execute(
                f"PRAGMA main.table_info({_identifier(table)})",
            )
        }
        for _, name, kind, required, default, _ in connection.execute(
            f"PRAGMA prepared.table_info({_identifier(table)})",
        ):
            if name not in current:
                addition = f"{_identifier(name)} {kind}"
                if required:
                    addition += " NOT NULL"
                if default is not None:
                    addition += " DEFAULT " + str(default)
                connection.execute(f"ALTER TABLE {_identifier(table)} ADD COLUMN {addition}")
    for name, sql in connection.execute(
        "SELECT name,sql FROM prepared.sqlite_master WHERE type='index' AND sql IS NOT NULL",
    ).fetchall():
        if name not in existing:
            table = connection.execute(
                "SELECT tbl_name FROM prepared.sqlite_master WHERE name=?",
                (name,),
            ).fetchone()[0]
            if table in _PATCH_TABLES:
                connection.execute(sql)


def apply_repair(report_dir: Path) -> dict:
    """Apply only prepared assessment tables; abort on live data/config/code drift."""
    path = report_dir / "report.json"
    if _digest(path) != (report_dir / "report.sha256").read_text().strip():
        raise ValueError("Repair report changed")
    report = json.loads(path.read_text())
    if report.get("version") != REPAIR_VERSION or (report_dir / "INCOMPLETE").exists():
        raise ValueError("Invalid or incomplete repair")
    applied = report_dir / "applied.json"
    previous = _application(Path(report["source_database"]), _digest(path))
    if previous is not None:
        if previous["status"] == "rolled_back":
            raise ValueError("Repair was rolled back; prepare a new repair")
        applied.write_text(json.dumps(previous, indent=2) + "\n")
        return previous
    config = Path(report["config"])
    if _code_hash(config) != report["code_contract"]:
        raise ValueError("Code or config changed; prepare a new repair")
    candidate = report_dir / "candidate.db"
    if _digest(candidate) != report["candidate_sha256"]:
        raise ValueError("Prepared candidate changed")
    settings = load_app_config(config)
    live = sqlite_path(settings.database_url)
    if live != Path(report["source_database"]):
        raise ValueError("Configured database changed")
    _verify_artifacts(report_dir, report)
    fresh = report_dir / "before-apply-backup"
    if not fresh.exists():
        snapshot_audit_state(config, fresh)
    with closing(sqlite3.connect(live, timeout=5)) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
        try:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'",
                )
            }
            if "repair_applications" in tables:
                previous = connection.execute(
                    "SELECT result FROM repair_applications WHERE report_hash=?",
                    (_digest(path),),
                ).fetchone()
                if previous is not None:
                    connection.rollback()
                    result = json.loads(previous[0])
                    applied.write_text(json.dumps(result, indent=2) + "\n")
                    return result
            if _logical_digest(connection) != report["source_logical_sha256"]:
                raise ValueError("Database changed since dry-run; prepare a new repair")
            _apply_schema(connection, candidate)
            for table in _PATCH_TABLES:
                columns = connection.execute(
                    f"PRAGMA prepared.table_info({_identifier(table)})",
                ).fetchall()
                if not columns:
                    continue
                names = [row[1] for row in columns]
                keys = [row[1] for row in sorted(columns, key=lambda row: row[5]) if row[5]]
                quoted = ",".join(map(_identifier, names))
                assignments = ",".join(
                    f"{_identifier(name)}=excluded.{_identifier(name)}"
                    for name in names
                    if name not in keys
                )
                placeholders = ",".join("?" for _ in names)
                statement = (
                    f"INSERT INTO {_identifier(table)} ({quoted}) VALUES ({placeholders}) "
                    f"ON CONFLICT ({','.join(map(_identifier, keys))}) DO UPDATE SET " + assignments
                )
                rows = connection.execute(f"SELECT {quoted} FROM prepared.{_identifier(table)}")
                connection.executemany(statement, rows.fetchall())
            if _protected(connection) != report["protected_sha256"]:
                raise ValueError("Repair would alter source/CRM/history/notifications")
            if connection.execute("PRAGMA foreign_key_check").fetchall():
                raise ValueError("Repair would break foreign keys")
            _verify_artifacts(report_dir, report)
            result = {
                "version": REPAIR_VERSION,
                "status": "applied",
                "jobs": report["jobs"],
                "applied_at": datetime.now(UTC).isoformat(),
                "api_calls": 0,
                "database": str(live),
                "fresh_backup": str(fresh.resolve()),
                "fresh_backup_sha256": _digest(fresh / "job_intake.db"),
                "protected_state_unchanged": True,
                "post_apply_domain_sha256": _domain_hash(connection),
            }
            connection.execute(
                "CREATE TABLE IF NOT EXISTS repair_applications "
                "(report_hash TEXT PRIMARY KEY, result TEXT NOT NULL)"
            )
            connection.execute(
                "INSERT INTO repair_applications VALUES (?,?)", (_digest(path), json.dumps(result))
            )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
    applied.write_text(json.dumps(result, indent=2) + "\n")
    return result


def rollback_repair(report_dir: Path) -> dict:
    """Restore legacy assessments only while no post-apply writer has changed the DB.

    Additive columns remain compatible with older code. Their unknown legacy
    values are restored; new human/CRM actions require a new reviewed repair.
    Source files and human JSONL are never overwritten by rollback.
    """
    report_path = report_dir / "report.json"
    if _digest(report_path) != (report_dir / "report.sha256").read_text().strip():
        raise ValueError("Repair report changed")
    report = json.loads(report_path.read_text())
    database = Path(report["source_database"])
    applied = _application(database, _digest(report_path))
    if applied is None:
        raise ValueError("Repair has not been applied")
    if applied["status"] == "rolled_back":
        return applied
    backup = Path(applied["fresh_backup"]) / "job_intake.db"
    if _digest(backup) != applied["fresh_backup_sha256"]:
        raise ValueError("Rollback backup changed")
    with closing(sqlite3.connect(database, timeout=5)) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("BEGIN IMMEDIATE")
        try:
            if _domain_hash(connection) != applied["post_apply_domain_sha256"]:
                raise ValueError(
                    "Database changed after apply; preserve/reconcile new actions first"
                )
            connection.execute("ATTACH DATABASE ? AS original", (str(backup.resolve()),))
            for table in reversed(_PATCH_TABLES):
                if table == "jobs":
                    continue
                old = connection.execute(
                    "SELECT 1 FROM original.sqlite_master WHERE name=? AND type='table'",
                    (table,),
                ).fetchone()
                connection.execute(f"DELETE FROM main.{_identifier(table)}")
                if old:
                    cols = [
                        row[1]
                        for row in connection.execute(
                            f"PRAGMA original.table_info({_identifier(table)})",
                        )
                    ]
                    names = ",".join(map(_identifier, cols))
                    connection.execute(
                        f"INSERT INTO main.{_identifier(table)} ({names}) "
                        f"SELECT {names} FROM original.{_identifier(table)}"
                    )
            old_columns = [row[1] for row in connection.execute("PRAGMA original.table_info(jobs)")]
            current_columns = connection.execute("PRAGMA main.table_info(jobs)").fetchall()
            extras = [row for row in current_columns if row[1] not in old_columns]
            assignments = ",".join(
                f"{_identifier(col)}=?" for col in old_columns if col != "job_uid"
            )
            values = connection.execute(
                "SELECT " + ",".join(map(_identifier, old_columns)) + " FROM original.jobs"
            ).fetchall()
            for row in values:
                original = dict(zip(old_columns, row, strict=True))
                connection.execute(
                    "UPDATE jobs SET " + assignments + " WHERE job_uid=?",
                    (
                        *[original[col] for col in old_columns if col != "job_uid"],
                        original["job_uid"],
                    ),
                )
            for _, name, _, required, default, _ in extras:
                if required and default is None:
                    raise ValueError("Legacy rollback needs an explicit compatible column default")
                connection.execute(
                    f"UPDATE jobs SET {_identifier(name)}="
                    + (str(default) if default is not None else "NULL")
                )
            if _protected(connection) != report["protected_sha256"]:
                raise ValueError("Rollback would change protected user state")
            if connection.execute("PRAGMA foreign_key_check").fetchall():
                raise ValueError("Rollback would break foreign keys")
            result = {
                **applied,
                "status": "rolled_back",
                "rolled_back_at": datetime.now(UTC).isoformat(),
                "schema_policy": "compatible_additive_columns_retained",
            }
            connection.execute(
                "UPDATE repair_applications SET result=? WHERE report_hash=?",
                (json.dumps(result), _digest(report_path)),
            )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
    (report_dir / "rolled-back.json").write_text(json.dumps(result, indent=2) + "\n")
    return result
