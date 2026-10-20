"""Human gold v2 → v3 migration and file-level v3 validation, without model calls.

Decisions, notes, source snapshots and hashes move 1:1. Only labeled v2 tags with a
v3 analogue move; everything else is listed in the manifest, never guessed.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import UTC, datetime
from pathlib import Path

from job_intake.annotation.text import TEXT_VERSION, source_hash
from job_intake.labeling.schema import (
    GATE_OUTCOMES,
    LABELS_SCHEMA,
    RELEVANCE_VALUES,
    check_gold_row,
)
from job_intake.models.job import JobRecord

MIGRATION_SCHEMA = "vacancy-labels-v3-migration-v1"
TAG_MAP_VERSION = "v2-to-v3-tags-v1"
METHOD_MAP = {"blind": "blind", "assisted": "curated"}
_HIRING_GEOGRAPHY = {
    "brazil_explicit": "open_to_brazil", "includes_brazil_region": "open_to_brazil",
    "excludes_brazil": "restricted_elsewhere", "restricted_elsewhere": "restricted_elsewhere",
    "unspecified": "unspecified",
}
_HIRING_PRIORITY = ("restricted_elsewhere", "open_to_brazil", "unspecified")
_WORKING_LANGUAGE = {
    "english_required": "english", "other_language_required": "other_language_required",
    "unspecified": "unspecified",
}
_SAO_PAULO = re.compile(r"\bs[aã]o paulo\b", re.I)
_BRAZIL = re.compile(r"\b(?:brazil|brasil)\b", re.I)


def gold_file_schema(cases: list[dict]) -> str:
    """The single schema of a gold file; mixed or unknown files are refused."""
    from job_intake.annotation.gold import GOLD_SCHEMA

    schemas = {case.get("schema", GOLD_SCHEMA) if isinstance(case, dict) else None
               for case in cases}
    if len(schemas) != 1 or next(iter(schemas)) not in {GOLD_SCHEMA, LABELS_SCHEMA}:
        raise ValueError("Gold rows must share one supported schema: "
                         f"{GOLD_SCHEMA} or {LABELS_SCHEMA}")
    return next(iter(schemas))


def _hiring(tags: dict) -> tuple[dict | None, list[tuple[str, str, str]]]:
    candidates, dropped = [], []
    geography = tags.get("hiring_geography")
    if isinstance(geography, dict) and geography.get("value") in _HIRING_GEOGRAPHY:
        candidates.append((_HIRING_GEOGRAPHY[geography["value"]], geography["quote"]))
    authorization = tags.get("work_authorization")
    if isinstance(authorization, dict):
        value = authorization.get("value")
        if value == "country_required":
            brazil = bool(_BRAZIL.search(str(authorization.get("country", ""))))
            candidates.append(("open_to_brazil" if brazil else "restricted_elsewhere",
                               authorization["quote"]))
        elif value == "unspecified":
            candidates.append(("unspecified", authorization["quote"]))
        else:
            dropped.append(("work_authorization", str(value), "no v3 analogue"))
    for value in _HIRING_PRIORITY:
        for candidate, quote in candidates:
            if candidate == value:
                label = {"value": value, "quote": quote}
                if value != "unspecified":
                    label["basis"] = "explicit"
                return label, dropped
    return None, dropped


def _work_mode(label: dict) -> tuple[dict | None, str | None]:
    value, quote = label.get("value"), label.get("quote", "")
    if value in {"remote", "remote_or_hybrid"}:
        return {"value": "remote", "quote": quote, "basis": "explicit"}, None
    if value == "unspecified":
        return {"value": "unspecified", "quote": quote}, None
    location = label.get("office_location")
    if value in {"hybrid", "onsite"} and isinstance(location, str) and location.strip():
        if len(location) > 80:
            return None, "office_location longer than 80 characters"
        mode = "office_sao_paulo" if _SAO_PAULO.search(location) else "office_elsewhere"
        return {"value": mode, "office_location": location, "quote": quote,
                "basis": "explicit"}, None
    return None, "office mode without a location has no v3 analogue"


def migrate_tags(tags: dict) -> tuple[dict, list[tuple[str, str, str]]]:
    """Map labeled v2 tags with a v3 analogue; return (v3 tags, dropped tag records)."""
    result, dropped = {}, []
    hiring, hiring_dropped = _hiring(tags)
    dropped.extend(hiring_dropped)
    if hiring is not None:
        result["hiring_eligibility"] = hiring
    if isinstance(tags.get("work_mode"), dict):
        mode, reason = _work_mode(tags["work_mode"])
        if mode is None:
            dropped.append(("work_mode", str(tags["work_mode"].get("value")), reason))
        else:
            result["work_mode"] = mode
    language = tags.get("working_language")
    if isinstance(language, dict):
        value = _WORKING_LANGUAGE.get(language.get("value"))
        if value is None:
            dropped.append(("working_language", str(language.get("value")), "no v3 analogue"))
        else:
            result["working_language"] = {"value": value, "quote": language["quote"]}
            if value != "unspecified":
                result["working_language"]["basis"] = "explicit"
    for tag in sorted(set(tags) - {"hiring_geography", "work_authorization", "work_mode",
                                   "working_language"}):
        value = tags[tag].get("value") if isinstance(tags[tag], dict) else None
        dropped.append((tag, str(value), "no v3 analogue"))
    return result, dropped


def migrate_case(case: dict, *, rubric_version: str) -> tuple[dict, list[tuple[str, str, str]]]:
    from job_intake.annotation.gold import GOLD_SCHEMA

    if not isinstance(case, dict) or case.get("schema", GOLD_SCHEMA) != GOLD_SCHEMA:
        raise ValueError(f"Migration accepts only {GOLD_SCHEMA} rows")
    method = case.get("method")
    if method is not None and method not in METHOD_MAP:
        raise ValueError("Unknown v2 method; refuse to guess provenance")
    tags = case.get("tags") or {}
    if not isinstance(tags, dict):
        raise ValueError("v2 tags must be an object")
    migrated_tags, dropped = migrate_tags(tags)
    labeled = bool(migrated_tags) or any(
        case.get(field) is not None for field in ("role_label", "relevance", "gate_outcome")
    )
    row = {
        "schema": LABELS_SCHEMA, "job_uid": case.get("job_uid"),
        "source_hash": case.get("source_hash"), "text_version": case.get("text_version"),
        **({"source": copy.deepcopy(case["source"])} if "source" in case else {}),
        "labeler": "human", "method": METHOD_MAP.get(method),
        "rubric_version": rubric_version if labeled else None,
        "first_labeled_at": case.get("labeled_at"), "labeled_at": case.get("labeled_at"),
        **({"label_scope": case["label_scope"]} if "label_scope" in case else {}),
        "role_label": case.get("role_label"), "relevance": case.get("relevance"),
        "gate_outcome": case.get("gate_outcome"), "gate_reasons": [],
        "tags": migrated_tags, "notes": case.get("notes"),
    }
    return row, dropped


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def migrate_gold_v2(source: Path, out: Path, *, rubric_version: str) -> dict:
    """v2 labeled → v3: role_label/relevance/gate_outcome/notes/source/hashes 1:1;
    tags: only labeled v2 tags with a v3 analogue (hiring_geography+work_authorization →
    hiring_eligibility by table; work_mode; working_language); method: blind→blind,
    assisted→curated; first_labeled_at=labeled_at. Refuses an existing out. Writes out
    and out.with_suffix('.manifest.json') with SHA-256 of input and output."""
    from job_intake.annotation.benchmark import read_jsonl
    from job_intake.annotation.gold_workbook import (
        ensure_new_outputs,
        jsonl_content,
        write_new_text,
    )

    if not isinstance(rubric_version, str) or not rubric_version.strip():
        raise ValueError("rubric_version must be nonempty")
    manifest_path = out.with_suffix(".manifest.json")
    if source.expanduser().resolve() in {out.expanduser().resolve(),
                                         manifest_path.expanduser().resolve()}:
        raise ValueError("Migration outputs must not replace the v2 source")
    ensure_new_outputs([out, manifest_path])
    raw = source.read_bytes()
    cases = read_jsonl(source)
    if not cases:
        raise ValueError("Cannot migrate an empty gold file")
    if len({case.get("job_uid") for case in cases}) != len(cases):
        raise ValueError("v2 gold job_uid values must be unique")
    rows, dropped = [], []
    for case in cases:
        row, lost = migrate_case(case, rubric_version=rubric_version)
        errors = check_gold_row(row)[0]
        if errors:
            raise ValueError(f"Migrated row {row['job_uid']} is invalid: " + "; ".join(
                error["code"] for error in errors
            ))
        rows.append(row)
        dropped.extend({"job_uid": row["job_uid"], "tag": tag, "value": value, "reason": reason}
                       for tag, value, reason in lost)
    content = jsonl_content(rows)
    complete = [row for row in rows if row["role_label"] is not None
                and row["relevance"] is not None and row["gate_outcome"] is not None]
    manifest = {
        "schema": MIGRATION_SCHEMA, "created_at": datetime.now(UTC).isoformat(),
        "api_calls": 0, "database_access": "none",
        "source": {"path": str(source.expanduser().resolve()), "sha256": _sha256(raw),
                   "rows": len(cases)},
        "output": {"path": str(out.expanduser().resolve()),
                   "sha256": _sha256(content.encode("utf-8")), "schema": LABELS_SCHEMA,
                   "rows": len(rows)},
        "rubric_version": rubric_version, "method_map": METHOD_MAP,
        "tag_map_version": TAG_MAP_VERSION,
        "counts": {
            "rows": len(rows),
            "labeled_rows": sum(row["rubric_version"] is not None for row in rows),
            "decision_labeled": len(complete),
            "relevant": sum(row["relevance"] >= 2 for row in complete),
            "relevance_only": sum(row["relevance"] is not None and row not in complete
                                  for row in rows),
            "methods": {method: sum(row["method"] == method for row in rows)
                        for method in ("blind", "curated", "assisted_llm")},
            "migrated_tags": sum(len(row["tags"]) for row in rows),
            "dropped_tags": len(dropped),
        },
        "dropped_tags": dropped,
        "notes": [
            "Decisions, notes, source snapshots, hashes and label_scope are copied 1:1.",
            "rubric_version marks rows that carry human labels; the v2 labels predate the "
            "rubric file and were made under the same user criteria.",
            "gate_reasons start empty: v2 recorded no gate reasons.",
        ],
    }
    write_new_text(content, out)
    write_new_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", manifest_path)
    return manifest


def validate_gold_v3(
    rows: list[tuple[str, JobRecord]], cases: list[dict], *,
    profile_ids: list[str] | tuple[str, ...] | None = None,
    source_history: dict[str, list[dict]] | None = None,
) -> dict:
    """File-level v3 validation with the same report shape as ``gold.validate_gold``.

    Quotes must rebind uniquely to the current source; rebound unit IDs appear in the
    report only, the human JSONL stays quote-based.
    """
    from job_intake.annotation.source_compat import check_source_compatibility

    records = dict(rows)
    errors, warnings, rebound, compatibility = [], [], [], []
    seen = set()

    def finding(target: list, uid: object, code: str, message: str) -> None:
        target.append({"job_uid": uid if isinstance(uid, str) else None,
                       "code": code, "message": message})

    if not isinstance(cases, list) or not cases:
        finding(errors, None, "empty_gold", "Gold must contain at least one vacancy.")
        cases = []
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            finding(errors, None, "invalid_case", f"Case {index + 1} must be an object.")
            continue
        uid = case.get("job_uid")
        if not isinstance(uid, str) or not uid.strip() or uid in seen:
            finding(errors, uid, "invalid_job_uid", "job_uid must be nonempty and unique.")
            continue
        seen.add(uid)
        if uid not in records:
            finding(errors, uid, "missing_source", "Vacancy is missing from the active source.")
            continue
        record = records[uid]
        row_errors, bindings = check_gold_row(case, record=record, profile_ids=profile_ids)
        for item in row_errors:
            finding(errors, uid, item["code"], item["message"])
        if isinstance(case.get("source_hash"), str) and re.fullmatch(
            r"[0-9a-f]{64}", case["source_hash"],
        ) and case["source_hash"] != source_hash(record):
            proof = check_source_compatibility(case, record,
                                               history=(source_history or {}).get(uid))
            if proof["compatible"]:
                compatibility.append({"job_uid": uid, **proof})
                finding(warnings, uid, "source_observations_compatible",
                        "Legacy source pin preserved: exact source facts/units match; "
                        "only collection observations changed.")
            else:
                finding(warnings, uid, "source_hash_changed",
                        "Source hash changed; review source drift before trusting prior labels.")
        if isinstance(case.get("text_version"), str) and case["text_version"] != TEXT_VERSION:
            finding(warnings, uid, "text_version_changed",
                    "Parser version changed; quotes have been rebound to current units.")
        if any(case.get(field) is None for field in ("role_label", "relevance", "gate_outcome")):
            finding(warnings, uid, "unlabeled_case",
                    "Human decisions are incomplete; this case cannot enter decision metrics.")
        if not row_errors:
            rebound.append({**copy.deepcopy(case), "tags": {
                tag: {**label, "evidence_ids": bindings.get(tag, [])}
                for tag, label in case["tags"].items()
            }})
    labeled = [case for case in rebound if case["role_label"] is not None
               and case["relevance"] in RELEVANCE_VALUES
               and case["gate_outcome"] in GATE_OUTCOMES]
    relevant = sum(case["relevance"] >= 2 for case in labeled)
    return {
        "schema": LABELS_SCHEMA, "valid": not errors, "rows": rebound,
        "counts": {"cases": len(cases), "valid_cases": len(rebound), "labeled": len(labeled),
                   "relevant": relevant,
                   "relevance_labeled": sum(case["relevance"] is not None for case in rebound),
                   "tagged": sum(bool(case["tags"]) for case in rebound)},
        "labeled_count": len(labeled), "relevant_count": relevant,
        "errors": errors, "warnings": warnings, "source_compatibility": compatibility,
    }
