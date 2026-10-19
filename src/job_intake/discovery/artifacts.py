from __future__ import annotations

import hashlib
import html
import json
import os
import re
import tempfile
import unicodedata
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
                          normalize_text(record.source_job_id),
                          regional_context(record), body))
        keys.add("body:" + stable_hash(basis))
    return keys


def _review_text(value: str | None) -> str:
    """Normalize presentation without dropping negation, numbers, or non-Latin text."""
    text = unicodedata.normalize("NFKC", html.unescape(value or "")).casefold()
    text = text.translate(str.maketrans({"–": "-", "—": "-", "’": "'", "‘": "'"}))
    return re.sub(r"\s+", " ", text).strip()


_SALARY_CURRENCY = re.compile(
    r"(\b(?:salary|compensation|pay)\b(?:(?![.!?](?:\s|$)).){0,160}?\d[\d,.]*\s+)"
    r"(?:usd|cad|aud|nzd|eur|gbp|brl)\b"
)


def review_content_keys(record: JobRecord) -> set[str]:
    """Identify repeated descriptions for review, independently of requisition identity.

    A regional copy can have a different ATS ID, URL, location or pay currency while
    asking the reviewer to read the same responsibilities again. These fingerprints
    only suppress that repeated review; they must never merge source records, alter
    ``candidate_uid`` or transfer human relevance/eligibility labels between records.

    Exact normalized bodies are grouped only within the same named company and title.
    For substantial descriptions, a second key ignores a currency code immediately
    after an amount in an explicit salary statement. Everything else, including pay
    amounts and hiring restrictions written in the description, remains significant.
    There is deliberately no fuzzy similarity threshold or broad boilerplate removal.
    """
    company, title = _review_text(record.company), _review_text(record.title)
    body = _review_text(record.description_clean or record.description_raw)
    if not company or not title or not body:
        return set()
    basis = "|".join((company, title, body))
    keys = {"review-body-v1:" + stable_hash(basis)}
    if len(body) >= 500 and len(body.split()) >= 60:
        currency_neutral = _SALARY_CURRENCY.sub(r"\1[currency]", body)
        if currency_neutral != body:
            basis = "|".join((company, title, currency_neutral))
            keys.add("review-salary-currency-v1:" + stable_hash(basis))
    return keys


def regional_context(record: JobRecord) -> str:
    """An unknown region is distinct from a proven country or hiring condition."""
    facts = {field: getattr(record, field, None) for field in (
        "location_text", "remote_text", "timezone_text", "employment_type",
    )}
    facts["hiring"] = {key: value for key, value in record.source_metadata.items()
                       if any(term in key.lower() for term in (
                           "eligib", "applicant", "location", "country", "authoriz", "visa"))}
    return stable_hash(json.dumps(facts, sort_keys=True, ensure_ascii=False, default=str))


def review_queue_keys(record: JobRecord) -> set[str]:
    """Suppress a repeated review only within an identical regional context.

    ``review_content_keys`` remains a cluster relationship; it never transfers
    source identity, eligibility or human labels between regional variants.
    """
    context = regional_context(record)
    return {key + ":region:" + context for key in review_content_keys(record)}


def candidate_uid(record: JobRecord) -> str:
    identity = url_identity(record.apply_url or record.original_url)
    return hashlib.sha256(identity.encode()).hexdigest()
