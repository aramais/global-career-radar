"""Blind discovery labels over saved candidates, independent of the jobs database."""

from __future__ import annotations

import fcntl
import json
import math
import os
import tempfile
import threading
from datetime import UTC, datetime
from pathlib import Path

from job_intake.annotation.gold import _snapshot, _source_error
from job_intake.annotation.gold_ui import (
    GoldLabelStore,
    GoldUIError,
    _fsync_directory,
    _reject_constant,
    _sha,
    _unique_object,
)
from job_intake.annotation.text import source_hash
from job_intake.config.settings import AppConfig
from job_intake.discovery.artifacts import review_content_keys
from job_intake.models.job import JobRecord, JobStatus

MAX_REVIEW_SECONDS = 1800


def _json_lines(path: Path, content: bytes | None = None) -> list[dict]:
    rows = []
    for line in (path.read_bytes() if content is None else content).splitlines():
        if not line.strip():
            continue
        row = json.loads(line, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
        if not isinstance(row, dict):
            raise ValueError("JSONL rows must be objects")
        rows.append(row)
    return rows


def _source_record(snapshot: dict) -> JobRecord:
    if _source_error(snapshot):
        raise ValueError("Invalid candidate source snapshot")
    values = dict(snapshot)
    if values.get("posted_at"):
        values["posted_at"] = datetime.fromisoformat(values["posted_at"].replace("Z", "+00:00"))
    values["status"] = JobStatus(values.get("status", "open"))
    record = JobRecord(**values)
    if _snapshot(record) != snapshot:
        raise ValueError("Incomplete or noncanonical candidate source snapshot")
    return record


class DiscoveryLabelStore(GoldLabelStore):
    """Same gold schema, with candidate snapshots and retry-safe review accounting."""

    def __init__(self, config: AppConfig, run_dir: Path):
        self.run_dir = run_dir.expanduser().resolve()
        super().__init__(config, self.run_dir / "labels.jsonl")
        self.candidates_path = self.run_dir / "candidates.jsonl"
        self.events_path = self.run_dir / "review-events.jsonl"
        self.pending_path = self.run_dir / ".review-pending.json"
        self._review_local = threading.local()

    def _source_records(self, uid: str) -> tuple:
        rows, seen = [], set()
        content = self.candidates_path.read_bytes()
        batch_path = self.run_dir / "batch.json"
        if batch_path.exists():
            batch = json.loads(batch_path.read_bytes(), object_pairs_hook=_unique_object,
                               parse_constant=_reject_constant)
            if not isinstance(batch, dict):
                raise ValueError("Invalid discovery batch")
            if "candidates_sha256" in batch and batch["candidates_sha256"] != _sha(content):
                raise ValueError("Discovery candidates changed after labeling batch preparation")
        for candidate in _json_lines(self.candidates_path, content):
            candidate_uid = candidate.get("job_uid")
            if not isinstance(candidate_uid, str) or not candidate_uid or candidate_uid in seen:
                raise ValueError("Invalid or duplicate candidate job_uid")
            seen.add(candidate_uid)
            if candidate_uid != uid:
                continue
            record = _source_record(candidate.get("source"))
            if candidate.get("source_hash") != source_hash(record):
                raise ValueError("Candidate source hash does not match its snapshot")
            rows.append((uid, record))
        if not rows:
            raise ValueError("Requested jobs missing from discovery candidates")
        return self.candidates_path, rows, {}

    def list_cases(self, query: dict) -> dict:
        return {**super().list_cases(query), "mode": "discovery"}

    def _detail(self, case: dict, line: bytes) -> dict:
        detail = {**super()._detail(case, line), "mode": "discovery"}
        if not detail.get("can_save"):
            return {**detail, "regional_variants": []}
        keys = review_content_keys(_source_record(case["source"]))
        variants = []
        for candidate in _json_lines(self.candidates_path):
            if candidate["job_uid"] == case["job_uid"]:
                continue
            record = _source_record(candidate["source"])
            if keys & review_content_keys(record):
                variants.append({"job_uid": candidate["job_uid"], "title": record.title,
                                 "location": record.location_text, "url": record.original_url})
        return {**detail, "regional_variants": variants}

    def _events(self) -> list[dict]:
        return _json_lines(self.events_path) if self.events_path.exists() else []

    def _append_event(self, event: dict) -> None:
        if any(row.get("event_id") == event["event_id"] for row in self._events()):
            return
        descriptor = os.open(self.events_path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(descriptor, "ab") as handle:
            handle.write((json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n").encode())
            handle.flush()
            os.fsync(handle.fileno())
        _fsync_directory(self.run_dir)

    def _recover(self) -> None:
        """Finish a saved label's event if a process stopped between the two writes."""
        if not self.pending_path.exists():
            return
        event = json.loads(self.pending_path.read_bytes(), object_pairs_hook=_unique_object,
                           parse_constant=_reject_constant)
        _, lines, cases = self._read()
        index, _ = self._find(event["job_uid"], cases)
        version = _sha(lines[index])
        if version == event["version"]:
            self._append_event(event)
        elif version != event["before_version"]:
            # Another correctly serialized save cannot pass a pending transaction.
            raise GoldUIError("Незавершённый журнал разметки требует проверки", 409)
        self.pending_path.unlink()
        _fsync_directory(self.run_dir)

    def _publish(self, before: bytes, after: bytes, case: dict, old_line: bytes,
                 new_line: bytes, changed_fields: list[str]) -> None:
        event = {**self._review_local.event, "before_version": _sha(old_line),
                 "version": _sha(new_line), "source_hash": case["source_hash"],
                 "changed_fields": changed_fields}
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.run_dir, prefix=".review-pending-",
                                             delete=False) as handle:
                temporary = Path(handle.name)
                os.chmod(temporary, 0o600)
                handle.write((json.dumps(event, allow_nan=False) + "\n").encode())
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.pending_path)
            _fsync_directory(self.run_dir)
            super()._publish(before, after, case, old_line, new_line, changed_fields)
            self._append_event(event)
            self.pending_path.unlink()
            _fsync_directory(self.run_dir)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def save(self, uid: str, payload: dict) -> dict:
        if not isinstance(payload, dict):
            raise GoldUIError("Ожидается JSON-объект")
        payload = dict(payload)
        seconds = payload.pop("review_seconds", None)
        if type(seconds) not in (int, float) or not math.isfinite(seconds) \
                or not 0 <= seconds <= MAX_REVIEW_SECONDS:
            raise GoldUIError("review_seconds должен быть числом от 0 до 1800")
        updates = self._patch(payload)
        event_id = _sha(json.dumps([uid, payload["expected_version"], updates],
                                  sort_keys=True, ensure_ascii=False).encode())
        self._read()  # Missing setup never creates artifacts.
        try:
            with (self.run_dir / ".review-events.lock").open("a+b") as lock:
                os.chmod(lock.name, 0o600)
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                self._recover()
                previous = next((row for row in self._events()
                                 if row.get("event_id") == event_id), None)
                if previous is not None:
                    detail = self.detail(uid)
                    if detail["version"] != previous["version"] or not detail["can_save"]:
                        raise GoldUIError("Разметка уже изменена. Обновите карточку.", 409)
                    return {**detail, "changed": False, "replayed": True}
                self._review_local.event = {
                    "schema": "discovery-review-v1", "event_id": event_id, "job_uid": uid,
                    "seconds": round(seconds, 3), "created_at": datetime.now(UTC).isoformat(),
                }
                return super().save(uid, payload)
        except (OSError, ValueError) as error:
            if isinstance(error, GoldUIError):
                raise
            raise GoldUIError("Не удалось сохранить discovery-разметку: " + str(error),
                              500) from error
        finally:
            self._review_local.event = None
