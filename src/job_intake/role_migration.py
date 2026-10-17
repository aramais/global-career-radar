"""Complete a local role-first transition, with a backup before schema writes."""

from __future__ import annotations

import csv
import hashlib
import json
import re
import shutil
import sqlite3
from collections import Counter
from contextlib import ExitStack, contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from job_intake.annotation.eval_runner import sqlite_path
from job_intake.annotation.text import source_hash
from job_intake.config.settings import AppConfig
from job_intake.models.job import JobRecord, JobStatus
from job_intake.pipeline import JobIntakePipeline
from job_intake.storage.dedup import JobDeduplicator

IMMUTABLE_JOB_FIELDS = (
    "id", "job_uid", "fingerprint", "content_hash", "source", "source_job_id", "company",
    "title", "original_url", "apply_url", "posted_at", "location_text", "remote_text",
    "employment_type", "salary_text", "timezone_text", "description_raw", "description_clean",
    "status", "source_metadata", "first_seen_at", "last_seen_at", "created_at", "archived_at",
    "archive_version", "last_alerted_tier", "last_alerted_at", "alert_pending",
)


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode()).hexdigest()


def _file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_db(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _rows(connection, table: str) -> list[dict]:
    # Table names come exclusively from sqlite_master, never command arguments.
    escaped = table.replace('"', '""')
    return sorted((dict(row) for row in connection.execute(f'SELECT * FROM "{escaped}"')),
                  key=lambda row: json.dumps(row, sort_keys=True))


def snapshot(path: Path) -> dict:
    """Read preservation evidence without creating/migrating the database."""
    with _read_db(path) as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        jobs = _rows(db, "jobs")
        preserved = {name for name in tables if name.startswith("crm_")} | (
            tables & {"job_profile_evaluations", "alert_outbox", "job_feedback"}
        )
        immutable = [{key: row[key] for key in IMMUTABLE_JOB_FIELDS if key in row}
                     for row in jobs]
        immutable.sort(key=lambda row: row["job_uid"])
        archived = {row["job_uid"]: row for row in jobs if row.get("archived_at")}
        return {
            "jobs": len(jobs), "active": len(jobs) - len(archived),
            "immutable_jobs_sha256": _digest(immutable), "archived": archived,
            "preserved_tables": {name: {"rows": len(rows), "sha256": _digest(rows)}
                                 for name in sorted(preserved) if (rows := _rows(db, name))
                                 is not None},
            "existing_events": {str(row["id"]): _digest(row)
                                for row in _rows(db, "job_events")},
        }


@contextmanager
def _offline_guard():
    attempts = {"network": 0, "model": 0, "telegram": 0}

    def deny(kind):
        def blocked(*_args, **_kwargs):
            attempts[kind] += 1
            raise RuntimeError(f"Role migration forbids {kind} access")
        return blocked

    with ExitStack() as stack:
        for target in ("socket.create_connection", "socket.socket.connect",
                       "socket.socket.connect_ex"):
            stack.enter_context(patch(target, deny("network")))
        stack.enter_context(patch(
            "job_intake.annotation.ai.AnnotationModelClient.generate", deny("model"),
        ))
        stack.enter_context(patch(
            "job_intake.alerts.telegram.TelegramNotifier.send", deny("telegram"),
        ))
        yield attempts


def verify_preservation(before: dict, path: Path) -> dict:
    after = snapshot(path)
    for key in ("jobs", "active", "immutable_jobs_sha256", "preserved_tables"):
        if before[key] != after[key]:
            raise RuntimeError(f"Preservation invariant failed: {key}")
    for uid, original in before["archived"].items():
        current = after["archived"].get(uid, {})
        if any(current.get(key) != value for key, value in original.items()):
            raise RuntimeError(f"Archived vacancy changed: {uid}")
        if "role_label" not in original and (
            current.get("role_label") is not None
            or current.get("role_classification") not in (None, "{}")
        ):
            raise RuntimeError(f"Archived vacancy was unexpectedly reclassified: {uid}")
    if any(after["existing_events"].get(key) != value
           for key, value in before["existing_events"].items()):
        raise RuntimeError("Existing job event history changed")
    return {"jobs": after["jobs"], "active": after["active"],
            "archived": len(after["archived"]),
            "preserved_tables": after["preserved_tables"],
            "new_job_events": len(after["existing_events"]) - len(before["existing_events"])}


def _verify_current(path: Path, pipeline: JobIntakePipeline) -> dict:
    with _read_db(path) as db:
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("SQLite integrity_check failed")
        if db.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError("SQLite foreign_key_check failed")
        jobs = [row for row in _rows(db, "jobs") if not row["archived_at"]]
        evaluations = _rows(db, "job_relevance_evaluations")
        by_uid = {}
        for evaluation in evaluations:
            by_uid.setdefault(evaluation["job_uid"], []).append(evaluation)
        labels = Counter()
        decisions = Counter()
        for row in jobs:
            entries = by_uid.get(row["job_uid"], [])
            if len(entries) != 1:
                raise RuntimeError("Every active vacancy must have exactly one current evaluation")
            item = entries[0]
            classification = json.loads(row["role_classification"])
            record = JobRecord(**{key: row[key] for key in (
                "source", "source_job_id", "company", "title", "original_url", "apply_url",
                "location_text", "remote_text", "employment_type", "salary_text", "timezone_text",
                "description_raw", "description_clean",
            )}, status=JobStatus(row["status"]),
                posted_at=datetime.fromisoformat(row["posted_at"]) if row["posted_at"] else None,
                source_metadata=json.loads(row["source_metadata"]))
            content = JobDeduplicator().build_identity(record).content_hash
            label = row["role_label"]
            checks = (
                label in pipeline.profile_versions,
                classification.get("label") == label == item["profile_id"],
                classification.get("version") == pipeline.classifier.version
                == item["classification_version"],
                classification.get("source_hash") == source_hash(record)
                == item["classification_source_hash"],
                classification.get("content_hash") == row["content_hash"]
                == item["content_hash"] == content,
                item["profile_version"] == pipeline.profile_versions.get(label),
            )
            if not all(checks):
                raise RuntimeError(f"Current provenance invariant failed: {row['job_uid']}")
            labels[label] += 1
            decisions[item["decision"]] += 1
        return {"active_current": len(jobs), "total_evaluations": len(evaluations),
                "role_counts": dict(labels), "decision_counts": dict(decisions)}


def _verify_exports(csv_path: Path, html_path: Path, current: dict) -> dict:
    with csv_path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        if "role_label" not in (reader.fieldnames or []) or "profile_scores" in reader.fieldnames:
            raise RuntimeError("CSV must expose a single role without the legacy score matrix")
    expected = min(500, current["active_current"] - current["decision_counts"].get("reject", 0))
    if len(rows) != expected or any(not row["role_label"] for row in rows):
        raise RuntimeError("CSV role/row count invariant failed")
    if any(row["filter_decision"] == "reject" for row in rows):
        raise RuntimeError("Canonical CSV unexpectedly includes rejected vacancies")
    if any(row["fit_score"] or row["tier"] for row in rows if row["role_label"] == "other"):
        raise RuntimeError("Unclassified roles must not display a relevance score")
    html = html_path.read_text(encoding="utf-8")
    match = re.search(r'<script type="application/json" id="jobs-data">(.*?)</script>', html, re.S)
    if match is None:
        raise RuntimeError("HTML does not contain review data")
    data = json.loads(match[1])
    if len(data) != current["active_current"] or any(
        not row["role_label"] or len(row["evaluations"]) > 1 for row in data
    ):
        raise RuntimeError("HTML single-role coverage invariant failed")
    return {"csv": str(csv_path), "csv_rows": len(rows), "csv_limit": 500,
            "csv_excludes_rejected": True, "html": str(html_path), "html_rows": len(data),
            "csv_sha256": _file_digest(csv_path), "html_sha256": _file_digest(html_path)}


def complete_migration(config: AppConfig, *, apply: bool = False,
                       backup_root: Path | None = None) -> dict:
    """Dry-run is strictly read-only. Apply creates a unique backup and updates active jobs."""
    path = sqlite_path(config.database_url)
    if not path.is_file():
        raise ValueError("Role migration requires an existing SQLite database")
    before = snapshot(path)
    report = {"database": str(path), "mode": "apply" if apply else "dry-run",
              "jobs": before["jobs"], "active": before["active"],
              "archived": len(before["archived"]), "api_calls": 0}
    if not apply:
        return report
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    backup = (backup_root or path.parent / "backups") / (
        f"role-first-2026-10-17-{stamp}-{uuid4().hex[:8]}"
    )
    backup.mkdir(parents=True, mode=0o700)
    report_path = backup / "completion.json"
    report.update(backup=str(backup), report=str(report_path), started_at=stamp, status="started")
    try:
        with _read_db(path) as source, sqlite3.connect(backup / path.name) as destination:
            source.backup(destination)
        backup_before = snapshot(backup / path.name)
        if backup_before != before or snapshot(path) != before:
            raise RuntimeError("Database changed during backup; retry with a fresh snapshot")
        files = {}
        for name in ("jobs.csv", "review.html"):
            original = path.parent / name
            if original.exists():
                shutil.copy2(original, backup / name)
                files[name] = {"sha256": _file_digest(original), "size": original.stat().st_size}
        manifest = {"database": str(path),
                    "database_backup_sha256": _file_digest(backup / path.name),
                    "baseline": before, "original_exports": files}
        (backup / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
        )
        offline = replace(config, sources=[], llm=replace(config.llm, enabled=False),
                          annotation=replace(config.annotation, ai_enabled=False),
                          telegram=replace(config.telegram, enabled=False))
        with _offline_guard() as attempts:
            report["offline_attempts"] = attempts
            pipeline = JobIntakePipeline(offline)
            try:
                stats = pipeline.reevaluate_saved()
                if (stats["persisted"] != before["active"]
                        or stats["evaluations"] != before["active"]):
                    raise RuntimeError("Offline migration did not process every active vacancy")
                if stats["errors"] or any(stats[key] for key in (
                    "classification_calls", "llm_calls", "annotation_extraction_calls",
                    "annotation_review_calls", "prefilter_screen_calls", "prefilter_verify_calls",
                )) or any(attempts.values()):
                    raise RuntimeError("Migration encountered errors or attempted external access")
                report["preservation"] = verify_preservation(before, path)
                current = _verify_current(path, pipeline)
                report["current"] = current
                # Stage reports, verify them, then replace the canonical files.
                staged_csv, staged_html = backup / "new-jobs.csv", backup / "new-review.html"
                pipeline.export_csv(staged_csv)
                pipeline.render_html(staged_html, limit=before["active"])
                _verify_exports(staged_csv, staged_html, current)
                csv_path, html_path = path.parent / "jobs.csv", path.parent / "review.html"
                staged_csv.replace(csv_path)
                staged_html.replace(html_path)
                report["exports"] = _verify_exports(csv_path, html_path, current)
                report["preservation"] = verify_preservation(before, path)
                report["offline_attempts"] = attempts
                report["pipeline"] = stats
            finally:
                pipeline.database.engine.dispose()
        report["status"] = "verified"
    except Exception as exc:
        report.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                               encoding="utf-8")
    return report
