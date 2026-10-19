"""Idempotent source import with references to immutable discovery human answers."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import replace
from pathlib import Path

from job_intake.annotation.eval_runner import sqlite_path
from job_intake.annotation.text import source_hash
from job_intake.config.settings import load_app_config, load_yaml_mapping
from job_intake.discovery.artifacts import record_from_snapshot
from job_intake.discovery.promote import promotion_drafts
from job_intake.pipeline import JobIntakePipeline
from job_intake.profiles import load_streams
from job_intake.storage.aliases import source_aliases
from job_intake.storage.dedup import JobDeduplicator
from job_intake.storage.models import JobSourceAliasORM
from job_intake.storage.repository import JobRepository


def _validate(draft: dict, profile_ids: list[str]):
    if draft.get("schema") != "discovery-promotion-v1":
        raise ValueError("Unknown promotion schema")
    record = record_from_snapshot(draft.get("source"))
    if source_hash(record) != draft.get("source_hash"):
        raise ValueError("Promotion source changed")
    reference = draft.get("human_reference", {})
    verified = promotion_drafts(
        Path(reference["run"]), objective=draft["objective"], job_uids=[draft["job_uid"]],
        profile_ids=profile_ids,
    )
    if len(verified) != 1 or verified[0] != draft:
        raise ValueError("Human confirmation changed; prepare promotion again")
    return record


def _preview(database: Path, draft: dict, record) -> dict:
    uid = JobDeduplicator().build_identity(record).job_uid
    result = {
        "discovery_job_uid": draft["job_uid"],
        "job_uid": uid,
        "action": "import",
        "objective": draft["objective"],
        "human_reference": draft["human_reference"],
    }
    if database.is_file():
        with closing(sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True)) as connection:
            connection.execute("PRAGMA query_only=ON")
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'",
                )
            }
            if "job_source_aliases" in tables:
                linked = connection.execute(
                    "SELECT job_uid FROM job_source_aliases WHERE alias_key=?",
                    (draft["idempotency_key"],),
                ).fetchone()
                if linked:
                    return {**result, "job_uid": linked[0], "action": "already_imported"}
                for key, _, _ in source_aliases(record):
                    linked = connection.execute(
                        "SELECT job_uid FROM job_source_aliases WHERE alias_key=?",
                        (key,),
                    ).fetchone()
                    if linked:
                        result.update(job_uid=linked[0], action="link_existing_source")
            if (
                "jobs" in tables
                and connection.execute(
                    "SELECT 1 FROM jobs WHERE job_uid=?",
                    (result["job_uid"],),
                ).fetchone()
            ):
                result["action"] = "link_existing_source"
                row = connection.execute(
                    "SELECT content_hash,role_classification,status,last_seen_at FROM jobs "
                    "WHERE job_uid=?",
                    (result["job_uid"],),
                ).fetchone()
                try:
                    classification = json.loads(row[1] or "{}")
                except (TypeError, ValueError):
                    classification = {}
                current_hash = (
                    classification.get("source_hash")
                    if (classification.get("content_hash") == row[0])
                    else None
                )
                result.update(
                    current_source_hash=current_hash,
                    current_status=row[2],
                    current_last_seen_at=row[3],
                    source_match=(
                        current_hash == draft["source_hash"] if current_hash is not None else None
                    ),
                    human_reference_stale=current_hash != draft["source_hash"],
                    source_snapshot_retained_separately=True,
                )
    return result


def promote_drafts(config, drafts: list[dict], *, dry_run: bool = True) -> dict:
    settings = load_app_config(config) if isinstance(config, (Path, str)) else config
    roles = [stream.id for stream in load_streams(
        load_yaml_mapping(settings.rules_path), load_yaml_mapping(settings.search_profiles_path),
    )]
    records = [_validate(draft, roles) for draft in drafts]
    if len({draft["idempotency_key"] for draft in drafts}) != len(drafts):
        raise ValueError("Duplicate promotion draft")
    database = sqlite_path(settings.database_url)
    preview = [
        _preview(database, draft, record) for draft, record in zip(drafts, records, strict=True)
    ]
    if dry_run or not drafts:
        return {"dry_run": dry_run, "api_calls": 0, "items": preview}
    settings = replace(
        settings,
        sources=[],
        telegram=replace(settings.telegram, enabled=False),
        llm=replace(settings.llm, enabled=False),
        annotation=replace(settings.annotation, ai_enabled=False),
    )
    pipeline = JobIntakePipeline(settings)
    results = []
    for draft, record, proposed in zip(drafts, records, preview, strict=True):
        if proposed["action"] == "already_imported":
            results.append(proposed)
            continue
        with pipeline.database.session() as session:
            existing = session.get(JobSourceAliasORM, draft["idempotency_key"])
            if existing is not None:
                results.append(
                    {**proposed, "job_uid": existing.job_uid, "action": "already_imported"}
                )
                continue
            saved = JobRepository(session).find_by_record(record)
            canonical_uid = saved.job_uid if saved is not None else None
        if canonical_uid is None:
            items, keys, pending = pipeline._evaluate_record(
                record, pipeline._stats(), use_llm=False
            )
            if pending:
                raise ValueError("Promotion cannot schedule paid work")
        with pipeline.database.session() as session:
            saved = JobRepository(session).find_by_record(record)
            if saved is not None:
                canonical_uid = saved.job_uid
            elif canonical_uid is None:
                result = JobRepository(session).upsert_evaluations(
                    items, keys, observed=False, queue_alert=False
                )
                canonical_uid = result.job_uid
            else:
                raise ValueError("Existing vacancy disappeared during promotion")
            session.add(
                JobSourceAliasORM(
                    alias_key=draft["idempotency_key"],
                    kind="discovery_promotion",
                    job_uid=canonical_uid,
                    provenance={
                        "source_hash": draft["source_hash"],
                        "source_snapshot": draft["source"],
                        "objective": draft["objective"],
                        "human_reference": draft["human_reference"],
                    },
                )
            )
            session.commit()
            results.append(
                {
                    **proposed,
                    "job_uid": canonical_uid,
                    "action": "linked" if saved is not None else "imported",
                }
            )
    pipeline.database.engine.dispose()
    return {"dry_run": False, "api_calls": 0, "items": results}
