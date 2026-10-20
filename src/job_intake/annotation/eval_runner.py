"""Read-only evaluation of saved annotations, with optional logs and manual gold.

Never instantiate the pipeline/database service: those can initialize or migrate SQLite.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

from job_intake.annotation.ai import _ERROR_CODES
from job_intake.annotation.benchmark import evaluate_reviews, read_jsonl
from job_intake.annotation.cohorts import write_new_text
from job_intake.annotation.evaluation import evaluate_records
from job_intake.annotation.jev import normalize_jev_review
from job_intake.annotation.service import _drafts
from job_intake.annotation.text import chunk_source, source_identity, source_units
from job_intake.config.settings import AppConfig
from job_intake.models.job import JobRecord, JobStatus

_REQUIRED_COLUMNS = {
    "job_uid", "source", "company", "title", "original_url", "description_raw",
    "description_clean", "source_metadata", "annotation", "updated_at",
}
_RECORD_COLUMNS = (
    "source", "company", "title", "original_url", "apply_url", "source_job_id",
    "posted_at", "location_text", "remote_text", "employment_type", "salary_text",
    "timezone_text", "description_raw", "description_clean", "status", "source_metadata",
    "annotation",
)
_LOG_ERROR_TYPES = {
    "ValueError", "TypeError", "KeyError", "OSError", "RuntimeError", "AnnotationModelError",
    "PrefilterValidationError", "PermissionError", "FileNotFoundError", "Exception",
}
_LOG_ERROR_CODES = set(_ERROR_CODES) | {
    "invalid_decision", "invalid_evidence", "quote_shortened", "quote_unattributable",
    "not_duty", "no_evidence",
}
_LOG_NUMBER = r"[0-9]{1,12}"
_LOG_STAGE = r"(?:extract|review|prefilter screen|prefilter verify|model)"


def sqlite_path(database_url: str) -> Path:
    """Resolve the same relative SQLite path as the application, without opening it."""
    try:
        url = make_url(database_url)
    except ArgumentError:
        raise ValueError("Invalid database URL") from None
    if url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:":
        raise ValueError("eval requires an existing SQLite file")
    if url.query:
        raise ValueError("eval does not accept SQLite URL query options")
    return Path(url.database).expanduser().resolve()


def _object_json(value: object, field: str) -> dict:
    if value is None or value == "":
        return {}
    if not isinstance(value, str):
        raise ValueError(f"Invalid {field} JSON")
    def unique_object(pairs: list[tuple]) -> dict:
        result = {}
        for key, entry in pairs:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = entry
        return result

    def reject_constant(value: str) -> None:
        raise ValueError("Non-finite JSON value")

    result = json.loads(value, object_pairs_hook=unique_object, parse_constant=reject_constant)
    if not isinstance(result, dict):
        raise ValueError(f"Invalid {field} JSON")
    return result


def read_saved_records(
    database_url: str, *, limit: int | None = 10, job_uids: list[str] | None = None,
    include_archived: bool = False,
) -> tuple[Path, list[tuple[str, JobRecord]], dict[str, list[dict]]]:
    """Schema-first SELECT, mode=ro and query_only; a missing DB cannot be created."""
    if limit is not None and (type(limit) is not int or limit < 1):
        raise ValueError("limit must be positive")
    path = sqlite_path(database_url)
    if not path.is_file():
        raise ValueError("SQLite database does not exist; eval will not create it")
    connection = sqlite3.connect(f"file:{quote(str(path), safe='/')}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    problems: dict[str, list[dict]] = {}
    records = []
    try:
        connection.execute("PRAGMA query_only=ON")
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(jobs)")}
        if not columns >= _REQUIRED_COLUMNS:
            raise ValueError("Unsupported jobs schema; eval will not migrate it")
        fields = ["job_uid", *[field for field in _RECORD_COLUMNS if field in columns]]
        query = "SELECT " + ", ".join(fields) + " FROM jobs"
        conditions, parameters = [], []
        if "archived_at" in columns and not include_archived:
            conditions.append("archived_at IS NULL")
        if job_uids:
            conditions.append("job_uid IN (" + ",".join("?" for _ in job_uids) + ")")
            parameters.extend(job_uids)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY updated_at DESC, job_uid"
        if limit is not None:
            query += " LIMIT ?"
            parameters.append(limit)
        for row in connection.execute(query, parameters):
            values = dict(row)
            uid = values.pop("job_uid")
            findings = []
            for field in ("annotation", "source_metadata"):
                try:
                    values[field] = _object_json(values[field], field)
                except (TypeError, ValueError, RecursionError):
                    values[field] = {}
                    findings.append({"severity": "error", "code": "invalid_" + field + "_json",
                                     "message": f"Saved {field} is not a valid JSON object."})
            if values.get("posted_at"):
                try:
                    values["posted_at"] = datetime.fromisoformat(values["posted_at"])
                except (ValueError, TypeError):
                    values["posted_at"] = None
                    findings.append({"severity": "warning", "code": "invalid_posted_at",
                                     "message": "Saved publication date cannot be parsed."})
            try:
                values["status"] = JobStatus(values.get("status", "unknown"))
            except ValueError:
                values["status"] = JobStatus.UNKNOWN
                findings.append({"severity": "warning", "code": "invalid_job_status",
                                 "message": "Saved vacancy status is unknown."})
            for field in ("source", "company", "title", "original_url", "description_raw",
                          "description_clean"):
                if not isinstance(values[field], str):
                    values[field] = ""
                    findings.append({"severity": "error", "code": "invalid_source_field",
                                     "message": f"Saved {field} is not text."})
            records.append((uid, JobRecord(**values)))
            if findings:
                problems[uid] = findings
    finally:
        connection.close()
    if job_uids and set(job_uids) - {uid for uid, _ in records}:
        raise ValueError("Requested jobs were not selected; check IDs, archive state and limit")
    return path, records, problems


def evaluate_log(path: Path) -> dict:
    """Extract metadata from the last run; never expose raw logs or execute their text."""
    content = path.read_text(encoding="utf-8")
    starts = list(re.finditer(r"^.*Starting annotation \(", content, re.MULTILINE))
    latest = content[starts[-1].start():] if starts else content
    failures = Counter()
    codes = Counter()
    legacy_prefilter = legacy_annotation = False
    details = []
    loaded = None
    started_jobs, saved_jobs = set(), set()
    last_job_index = None
    completed_events = {"input_tokens": 0, "output_tokens": 0, "requests": 0,
                        "usage_unavailable": 0}
    failed_events = {"input_tokens": 0, "output_tokens": 0, "attempts": 0,
                     "usage_unavailable": 0}
    request_states, pending_requests = {}, set()
    requests_started = unreported_error_usage = 0
    for line in latest.splitlines():
        # Only retain numeric progress and known terminal event metadata. Vacancy
        # titles, model names, exception messages and arbitrary log text stay private.
        clean = re.sub(r"^\[[0-9]{1,12}(?:\.[0-9]{1,6})?s\] ", "", line)
        context_match = re.match(
            rf"Job ({_LOG_NUMBER})/({_LOG_NUMBER})(?: chunk ({_LOG_NUMBER})/"
            rf"({_LOG_NUMBER}))?\s*:", clean,
        )
        context = (
            (int(context_match[1]), int(context_match[3]) if context_match[3] else None)
            if context_match else (None, None)
        )
        match = re.fullmatch(rf"Loaded ({_LOG_NUMBER}) vacancies\.", clean)
        if match:
            loaded = int(match[1])
        match = re.fullmatch(rf"Job ({_LOG_NUMBER})/({_LOG_NUMBER}): .+ — .+", clean)
        if match and 1 <= int(match[1]) <= int(match[2]):
            started_jobs.add(int(match[1]))
            last_job_index = int(match[1])
        match = re.fullmatch(
            rf"Job ({_LOG_NUMBER})/({_LOG_NUMBER}): saved ({_LOG_NUMBER}) profile "
            r"evaluations in [0-9]{1,12}(?:\.[0-9]{1,6})?s; annotation errors="
            rf"{_LOG_NUMBER}(?:, prefilter errors={_LOG_NUMBER})?\.", clean,
        )
        if match and 1 <= int(match[1]) <= int(match[2]):
            saved_jobs.add(int(match[1]))
        if re.search(rf": {_LOG_STAGE}: [^\n]+ started\.$", clean):
            request_states[context] = "stage_started"
        if re.search(
            rf"(?:^|: ){_LOG_STAGE} request attempt {_LOG_NUMBER}/{_LOG_NUMBER} "
            r"\(timeout [0-9]{1,12}(?:\.[0-9]{1,6})?s\)\.$", clean,
        ):
            requests_started += 1
            pending_requests.add(context)
            request_states[context] = "started"
        match = re.search(
            rf"(?:^|: ){_LOG_STAGE} request completed in "
            r"[0-9]{1,12}(?:\.[0-9]{1,6})?s; "
            rf"tokens input=({_LOG_NUMBER}), output=({_LOG_NUMBER})"
            r"(; usage incomplete)?\.$", clean,
        )
        if match:
            completed_events["input_tokens"] += int(match[1])
            completed_events["output_tokens"] += int(match[2])
            completed_events["requests"] += 1
            completed_events["usage_unavailable"] += int(bool(match[3]))
            pending_requests.discard(context)
            request_states[context] = "completed"
        elif re.search(
            rf"(?:^|: ){_LOG_STAGE} completed in "
            r"[0-9]{1,12}(?:\.[0-9]{1,6})?s\.$", clean,
        ):
            # Older progress streams report stage completion without request usage.
            pending_requests.discard(context)
            request_states[context] = "completed"
        match = re.search(
            rf"(?:^|: ){_LOG_STAGE} request failed in "
            r"[0-9]{1,12}(?:\.[0-9]{1,6})?s(?:; [^\n]+)?; "
            rf"(?:tokens input=({_LOG_NUMBER}), output=({_LOG_NUMBER})|"
            r"(usage unavailable))\.$", clean,
        )
        if match:
            failed_events["attempts"] += 1
            if match[3]:
                failed_events["usage_unavailable"] += 1
            else:
                failed_events["input_tokens"] += int(match[1])
                failed_events["output_tokens"] += int(match[2])
            pending_requests.discard(context)
            request_states[context] = "failed"
        match = re.search(r"prefilter failed \(([A-Za-z_][A-Za-z_0-9]*)\)", line)
        if match:
            error_type = match[1] if match[1] in _LOG_ERROR_TYPES else "UnknownError"
            failures["prefilter:" + error_type] += 1
            legacy_prefilter |= error_type == "ValueError" and "code=" not in line
            # A response can succeed and subsequently fail schema/evidence
            # validation. Its tokens have already been counted as completed usage.
            unreported_error_usage += int(request_states.get(context) not in {
                "completed", "failed",
            })
            pending_requests.discard(context)
            request_states[context] = "ended"
        match = re.search(
            r": ([A-Za-z_][A-Za-z_0-9]*)(?:; code=[^;]+)?; partial evidence retained", line,
        )
        if match:
            error_type = match[1] if match[1] in _LOG_ERROR_TYPES else "UnknownError"
            failures["annotation:" + error_type] += 1
            legacy_annotation |= error_type == "AnnotationModelError" and "code=" not in line
            unreported_error_usage += int(request_states.get(context) not in {
                "completed", "failed",
            })
            pending_requests.discard(context)
            request_states[context] = "ended"
        match = re.search(r"code=([A-Za-z_]+)", line)
        if match and match[1] in _LOG_ERROR_CODES and (
            "request failed" in line or "prefilter failed" in line
        ):
            codes[match[1]] += 1
    summary = re.search(
        r"Annotated (\d+) jobs, (\d+) profile evaluations\. Extraction calls=(\d+), "
        r"review calls=(\d+), cache hits=(\d+), errors=(\d+)\. Prefilter screen calls=(\d+), "
        r"verify calls=(\d+), cache hits=(\d+), suppressed=(\d+), full review=(\d+), "
        r"local skipped=(\d+), errors=(\d+)", latest,
    )
    names = ("jobs", "profile_evaluations", "extraction_calls", "review_calls",
             "annotation_cache_hits", "annotation_errors", "screen_calls", "verify_calls",
             "prefilter_cache_hits", "suppressed", "full_review", "local_skipped",
             "prefilter_errors")
    totals = dict(zip(names, map(int, summary.groups()), strict=True)) if summary else {}
    usage = re.search(
        r"Reported tokens from new completed requests: input=(\d+), output=(\d+), requests=(\d+)",
        latest,
    )
    tokens = dict(zip(("input_tokens", "output_tokens", "requests"),
                      map(int, usage.groups()), strict=True)) if usage else {}
    completed_source = "summary" if usage else "events" if completed_events["requests"] else (
        "unavailable"
    )
    if not usage and completed_events["requests"]:
        tokens = completed_events
    failed_usage = re.search(
        r"Reported tokens from failed attempts: input=(\d+), output=(\d+), "
        r"attempts=(\d+), usage unavailable=(\d+)", latest,
    )
    failed_tokens = dict(zip(("input_tokens", "output_tokens", "attempts", "usage_unavailable"),
                             map(int, failed_usage.groups()), strict=True)) if failed_usage else {}
    failed_source = "summary" if failed_usage else "events" if failed_events["attempts"] else (
        "unavailable"
    )
    if not failed_usage and failed_events["attempts"]:
        failed_tokens = failed_events
    if legacy_prefilter:
        details.append({
            "code": "legacy_prefilter_validation_error",
            "message": "Prefilter validation failed; legacy logs omit its exact cause.",
        })
    if legacy_annotation:
        details.append({"code": "legacy_model_error",
                        "message": "Model review failed; legacy logs omit status/finish reason."})
    if unreported_error_usage or failed_tokens.get("usage_unavailable"):
        details.append({"code": "failed_request_usage_unknown",
                        "message": "Completed-request token totals do not cover all failed calls."})
    if pending_requests:
        details.append({
            "code": "unfinished_request_usage_unknown",
            "message": "The log ends with unfinished requests; their token usage is unknown.",
        })
    return {"scope": "latest_annotation_run", "runs_detected": len(starts) or 1,
            "complete": bool(summary), "summary": totals, "error_types": dict(failures),
            "request_error_codes": dict(codes),
            "completed_request_usage": tokens, "failed_attempt_usage": failed_tokens,
            "progress": {"jobs_loaded": loaded, "jobs_started": len(started_jobs),
                         "jobs_saved": len(saved_jobs), "last_job_index": last_job_index},
            "request_counts": {"started": requests_started,
                               "completed": completed_events["requests"],
                               "failed": failed_events["attempts"],
                               "pending": len(pending_requests)},
            "request_usage_source": {"completed": completed_source, "failed": failed_source},
            "findings": details}


def compare_saved_gold(rows: list[tuple[str, JobRecord]], path: Path) -> dict:
    """Compare saved exact-ID reviews to manually labelled *identical* source/drafts."""
    gold = read_jsonl(path)
    by_uid = dict(rows)
    predictions = []
    fields = ("kind", "value", "unit_id", "source_snippet", "requirement", "polarity",
              "is_inference")
    for case in gold:
        uid = case.get("case_id")
        if uid not in by_uid:
            raise ValueError("Gold case_id must identify a selected saved job_uid")
        record = by_uid[uid]
        units = source_units(record)
        unit_text = {row["unit_id"]: row["text"] for row in units}
        if not isinstance(case.get("units"), list) or any(
            not isinstance(row, dict) or row.get("text") != unit_text.get(row.get("unit_id"))
            for row in case["units"]
        ):
            raise ValueError("Gold source units must match the current vacancy exactly")
        annotation = record.annotation
        size = annotation.get("chunk_chars", 6000)
        if type(size) is not int or size < 256:
            raise ValueError("Cannot compare gold with invalid saved chunk size")
        drafts, reviews = {}, []
        stages = annotation.get("stages", {})
        if not isinstance(stages, dict):
            raise ValueError("Cannot compare gold with invalid saved stages")
        for chunk in chunk_source(units, size):
            stage = stages.get(chunk["chunk_id"], {})
            if not isinstance(stage, dict) or "extraction" not in stage:
                continue
            prepared, _ = _drafts(
                stage["extraction"], source_identity(record),
                [unit for unit in chunk["units"] if unit["text"].strip()], chunk["chunk_id"],
            )
            drafts.update({draft["claim_id"]: draft for draft in prepared})
            payload = stage.get("review", {"reviews": []})
            if annotation.get("review_provider") == "jev":
                payload = normalize_jev_review(
                    payload, prepared, model=annotation.get("review_model", ""),
                )
            if isinstance(payload, dict) and isinstance(payload.get("reviews"), list):
                reviews.extend(payload["reviews"])
        if not isinstance(case.get("drafts"), list) or any(
            not isinstance(row, dict) or row.get("claim_id") not in drafts
            or any(row.get(field) != drafts[row["claim_id"]].get(field) for field in fields)
            for row in case["drafts"]
        ):
            raise ValueError("Gold drafts must match the current saved extraction exactly")
        # Extra saved claims outside the manually labelled subset are not scored.
        labelled = {row["claim_id"] for row in case["drafts"]}
        predictions.append({"case_id": uid, "reviews": [
            row for row in reviews if not isinstance(row, dict) or row.get("claim_id") in labelled
        ]})
    return evaluate_reviews(gold, predictions)


def run_evaluation(
    settings: AppConfig, *, limit: int | None = 10, job_uids: list[str] | None = None,
    log_path: Path | None = None, gold_path: Path | None = None,
) -> dict:
    path, rows, load_findings = read_saved_records(
        settings.database_url, limit=limit, job_uids=job_uids,
        include_archived=gold_path is not None,
    )
    report = evaluate_records(rows, settings.annotation, settings.prefilter)
    for job in report["jobs"]:
        problems = load_findings.get(job["job_uid"], [])
        job["findings"].extend(problems)
        if any(row["severity"] == "error" for row in problems):
            job["integrity"] = "error"
        elif problems and job["integrity"] == "ok":
            job["integrity"] = "warning"
    report["counts"]["jobs_with_errors"] = sum(
        job["integrity"] == "error" for job in report["jobs"]
    )
    report["counts"]["jobs_with_warnings"] = sum(
        job["integrity"] == "warning" for job in report["jobs"]
    )
    report["finding_counts"] = dict(sorted(Counter(
        finding["code"] for job in report["jobs"] for finding in job["findings"]
    ).items()))
    report.update(generated_at=datetime.now(UTC).isoformat(),
                  selection={"order": "updated_at DESC, job_uid", "limit": limit,
                             "job_uids": job_uids or [],
                             "include_archived": gold_path is not None},
                  database=str(path), database_access="read_only")
    if log_path:
        report["log"] = evaluate_log(log_path)
    if gold_path:
        report["gold"] = compare_saved_gold(rows, gold_path)
    report["integrity_passed"] = bool(rows) and report["counts"]["unannotated"] == 0 and not any(
        finding["severity"] == "error" for job in report["jobs"] for finding in job["findings"]
    ) and not report.get("gold", {}).get("failures") and not report.get(
        "gold", {}
    ).get("counts", {}).get("invalid_reviews")
    # Compatibility alias: neither name is a semantic-accuracy acceptance gate.
    report["passed"] = report["integrity_passed"]
    return report


def report_destinations(
    outputs: list[Path], *, protected_paths: list[Path],
) -> tuple[Path, ...]:
    """Protect input identities and refuse occupied names before publishing any file."""
    # Resolve directory aliases, not final symlinks: each final name itself must
    # be free, including a dangling symlink whose target does not exist.
    expanded = (path.expanduser() for path in outputs)
    paths = tuple(path.parent.resolve() / path.name for path in expanded)
    identities = {path.resolve() for path in paths}
    if len(identities) != len(paths):
        raise ValueError("Report destinations must be distinct and must not overwrite each other")
    if identities & {path.expanduser().resolve() for path in protected_paths}:
        raise ValueError("Report must not overwrite inputs: database, config, log or gold")
    if any(path.exists() or path.is_symlink() for path in paths):
        raise ValueError("Report output already exists; choose new paths")
    return paths


def write_report(report: dict, output: Path, *, protected_paths: list[Path]) -> Path:
    return write_text_report(
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        output, protected_paths=protected_paths,
    )


def write_text_report(content: str, output: Path, *, protected_paths: list[Path]) -> Path:
    """Publish a complete new report exclusively, preserving all existing evidence."""
    (path,) = report_destinations([output], protected_paths=protected_paths)
    return write_new_text(path, content)
