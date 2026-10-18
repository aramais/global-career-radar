from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from job_intake.annotation.gold import _snapshot, _source_error
from job_intake.annotation.text import source_hash
from job_intake.models.job import JobRecord, JobStatus
from job_intake.utils.text import canonicalize_url, normalize_text, stable_hash


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("Duplicate JSON field")
        result[key] = value
    return result


def _constant(value):
    raise ValueError("Non-finite JSON: " + value)


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_pairs,
                      parse_constant=_constant)


def read_jsonl(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            value = json.loads(line, object_pairs_hook=_pairs, parse_constant=_constant)
            if not isinstance(value, dict):
                raise ValueError("Expected JSONL objects")
            rows.append(value)
    return rows


def write_json(path: Path, value, *, exclusive: bool = False) -> None:
    write_text(path, json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
               exclusive=exclusive)


def write_jsonl(path: Path, rows: list[dict], *, exclusive: bool = False) -> None:
    write_text(path, "".join(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n"
                             for row in rows), exclusive=exclusive)


def write_text(path: Path, value: str, *, exclusive: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if exclusive:
        with path.open("x", encoding="utf-8") as handle:
            os.chmod(path, 0o600)
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        return
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         delete=False) as handle:
            temporary = Path(handle.name)
            os.chmod(temporary, 0o600)
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def record_from_snapshot(source: dict) -> JobRecord:
    problem = _source_error(source)
    if problem:
        raise ValueError("Invalid discovery source: " + problem)
    values = dict(source)
    values["status"] = JobStatus(values["status"])
    if values.get("posted_at"):
        values["posted_at"] = datetime.fromisoformat(values["posted_at"])
    return JobRecord(**values)


def load_candidates(run_dir: Path) -> list[dict]:
    rows = read_jsonl(run_dir / "candidates.jsonl")
    seen = set()
    for row in rows:
        uid = row.get("job_uid")
        if not isinstance(uid, str) or not uid or uid in seen:
            raise ValueError("Discovery candidates require unique job_uid")
        seen.add(uid)
        record = record_from_snapshot(row.get("source"))
        if row.get("source_hash") != source_hash(record) or row["source"] != _snapshot(record):
            raise ValueError("Discovery candidate source hash/snapshot mismatch")
    return rows


def url_identity(url: str) -> str:
    """Normalize ATS aliases without merging different requisitions or companies."""
    canonical = canonicalize_url(url)
    parsed = urlsplit(canonical)
    parts = [part for part in parsed.path.split("/") if part]
    host = (parsed.hostname or "").lower()
    if host in {"boards.greenhouse.io", "job-boards.greenhouse.io"} \
            and len(parts) >= 3 and parts[-2] == "jobs":
        return f"greenhouse:{parts[0]}:{parts[-1]}"
    if host in {"jobs.lever.co", "jobs.eu.lever.co"} and len(parts) >= 2:
        return f"{host}:{parts[0]}:{parts[1]}"
    if host == "jobs.ashbyhq.com" and len(parts) >= 2:
        return f"ashby:{parts[0]}:{parts[1]}"
    # Greenhouse links on company sites retain gh_jid, which is a real identity parameter.
    query = parse_qs(parsed.query)
    if query.get("gh_jid"):
        return f"greenhouse-custom:{host}:{query['gh_jid'][0]}"
    return canonical


def candidate_keys(record: JobRecord) -> set[str]:
    keys = {"url:" + url_identity(url) for url in (record.apply_url, record.original_url) if url}
    # Exact full-body equality catches mirrors; distinct known req IDs stay separate.
    body = normalize_text(record.description_clean or record.description_raw)
    if body:
        basis = "|".join((normalize_text(record.company), normalize_text(record.title),
                          normalize_text(record.source_job_id), body))
        keys.add("body:" + stable_hash(basis))
    return keys


def candidate_uid(record: JobRecord) -> str:
    identity = url_identity(record.apply_url or record.original_url)
    return hashlib.sha256(identity.encode()).hexdigest()
