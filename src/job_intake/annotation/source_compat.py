"""Prove legacy gold pins against unchanged facts without changing public hashes or labels."""

from __future__ import annotations

import copy
import sqlite3
from dataclasses import fields
from datetime import datetime
from urllib.parse import quote

from job_intake.annotation.cohorts import digest
from job_intake.annotation.eval_runner import _object_json, sqlite_path
from job_intake.annotation.text import source_hash, source_identity, source_units
from job_intake.models.job import JobRecord, JobStatus


def record_snapshot(record: JobRecord) -> dict:
    result = {
        field.name: copy.deepcopy(getattr(record, field.name))
        for field in fields(JobRecord)
        if field.init and field.name != "annotation"
    }
    if result["posted_at"] is not None:
        result["posted_at"] = result["posted_at"].isoformat()
    result["status"] = str(result["status"])
    return result


def snapshot_record(snapshot: dict) -> JobRecord:
    allowed = {
        field.name for field in fields(JobRecord) if field.init and field.name != "annotation"
    }
    if not isinstance(snapshot, dict) or set(snapshot) - allowed:
        raise ValueError("Invalid historical source snapshot")
    values = copy.deepcopy(snapshot)
    if not isinstance(values.get("source_metadata", {}), dict) or any(
        not isinstance(values.get(field), str)
        for field in ("source", "company", "title", "original_url")
    ):
        raise ValueError("Invalid historical source facts")
    if values.get("posted_at"):
        values["posted_at"] = datetime.fromisoformat(values["posted_at"])
    values["status"] = JobStatus(values.get("status", "unknown"))
    return JobRecord(**values)


def source_facts(record: JobRecord) -> dict:
    # Lazy import avoids text -> source_state -> text initialization cycles.
    from job_intake.storage.source_state import OBSERVATION_FIELDS

    snapshot = record_snapshot(record)
    snapshot["source_metadata"] = {
        key: value
        for key, value in record.source_metadata.items()
        if key not in OBSERVATION_FIELDS and key != "collection_observations"
    }
    return snapshot


def read_source_history(database_url: str, cases: list[dict]) -> dict[str, list[dict]]:
    """Existing history only, schema-first/query-only; never infer a missing old snapshot."""
    pins = {case["job_uid"]: case["source_hash"] for case in cases}
    if not pins:
        return {}
    path = sqlite_path(database_url)
    connection = sqlite3.connect(f"file:{quote(str(path), safe='/')}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    result = {}
    try:
        connection.execute("PRAGMA query_only=ON")
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(job_events)")}
        if not {"job_uid", "event_type", "payload"} <= columns:
            return {}
        selected = "job_uid,event_type,payload" + (",id" if "id" in columns else "")
        for row in connection.execute(
            f"SELECT {selected} FROM job_events WHERE event_type IN "
            "('source_superseded','annotation_superseded')"
        ):
            uid = row["job_uid"]
            if uid not in pins:
                continue
            try:
                payload = _object_json(row["payload"], "source_history")
                record = snapshot_record(payload["source_snapshot"])
            except (ValueError, TypeError, KeyError):
                continue
            if source_hash(record) != pins[uid]:
                continue
            result.setdefault(uid, []).append(
                {
                    "source_snapshot": record_snapshot(record),
                    "method": "hash_verified_job_event",
                    "event_id": row["id"] if "id" in columns else None,
                }
            )
    finally:
        connection.close()
    return result


def check_source_compatibility(
    case: dict, record: JobRecord, *, history: list[dict] | None = None
) -> dict:
    from job_intake.annotation.gold import _snapshot
    from job_intake.storage.source_state import OBSERVATION_FIELDS

    pin, current = case.get("source_hash"), source_hash(record)
    if pin == current:
        return {
            "compatible": True,
            "method": "exact_public_hash",
            "original_hash": pin,
            "current_hash": current,
        }
    candidates = list(history or [])
    snapshot = case.get("source")
    if isinstance(snapshot, dict):
        candidates.append({"method": "hash_verified_gold_snapshot", "source_snapshot": snapshot})
    observations = record.source_metadata.get("collection_observations")
    if isinstance(observations, dict):
        # This candidate is accepted only if its complete legacy public hash
        # reproduces the original pin; merely similar normalized text is insufficient.
        previous = copy.deepcopy(record)
        previous.source_metadata = {
            key: copy.deepcopy(value)
            for key, value in record.source_metadata.items()
            if key != "collection_observations"
        }
        previous.source_metadata.update(
            {
                key: copy.deepcopy(value)
                for key, value in observations.items()
                if key in OBSERVATION_FIELDS
            }
        )
        candidates.append(
            {
                "method": "hash_verified_reversed_observation_move",
                "source_snapshot": record_snapshot(previous),
            }
        )
    facts = source_facts(record)
    for candidate in candidates:
        try:
            previous = snapshot_record(candidate["source_snapshot"])
        except (ValueError, TypeError, KeyError):
            continue
        if (
            source_hash(previous) != pin
            or source_facts(previous) != facts
            or source_identity(previous) != source_identity(record)
            or source_units(previous) != source_units(record)
        ):
            continue
        if snapshot is not None and snapshot != _snapshot(previous):
            continue
        return {
            "compatible": True,
            "method": candidate["method"],
            "original_hash": pin,
            "current_hash": current,
            "facts_hash": digest(facts),
            "source_snapshot": record_snapshot(previous),
            "event_id": candidate.get("event_id"),
        }
    return {
        "compatible": False,
        "method": "unproved_source_drift",
        "original_hash": pin,
        "current_hash": current,
    }
