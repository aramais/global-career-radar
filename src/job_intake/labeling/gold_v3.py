"""Human gold v2 → v3 migration and file-level v3 validation, without model calls.

Decisions, notes, source snapshots and hashes move 1:1. Only labeled v2 tags with a
v3 analogue move; everything else is listed in the manifest, never guessed.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import tempfile
import unicodedata
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
TAG_MAP_VERSION = "v2-to-v3-tags-v3"
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
_SAO_PAULO_CITY = re.compile(
    r"(?:(?:city|municipality) of |(?:cidade|municipio) de )?sao paulo"
    r"(?: city| capital| \(city\))?(?:\s*\(sp\))?"
    r"(?:\s*[,/–-]\s*(?:sp|sao paulo))?"
    r"(?:\s*[,/–-]\s*(?:brazil|brasil|br))?",
    re.I,
)
_BRAZIL_AUTHORIZATION_ALIASES = frozenset({"brazil", "brasil", "br", "bra"})
# This migration recognizes explicit aliases, not arbitrary nonempty text. A
# country absent here is preserved as a dropped tag for review, never guessed.
_FOREIGN_AUTHORIZATION_ALIASES = frozenset({
    "us", "usa", "u s", "u s a", "united states", "united states of america", "estados unidos",
    "uk", "gb", "gbr", "united kingdom", "reino unido", "canada", "ca", "can",
    "australia", "au", "aus", "new zealand", "nz", "nzl", "nova zelandia",
    "germany", "de", "deu", "deutschland", "alemanha", "france", "fr", "fra", "franca",
    "spain", "es", "esp", "espanha", "portugal", "pt", "prt", "ireland", "ie", "irl",
    "netherlands", "nl", "nld", "paises baixos", "italy", "it", "ita", "italia",
    "switzerland", "ch", "che", "suica", "sweden", "se", "swe", "suecia",
    "norway", "no", "nor", "noruega", "denmark", "dk", "dnk", "dinamarca",
    "poland", "pl", "pol", "polonia", "finland", "fi", "fin", "finlandia",
    "belgium", "be", "bel", "belgica", "austria", "at", "aut",
    "mexico", "mx", "mex", "argentina", "ar", "arg", "chile", "cl", "chl",
    "colombia", "co", "col", "peru", "pe", "per", "uruguay", "uy", "ury", "uruguai",
    "india", "in", "ind", "singapore", "sg", "sgp", "singapura", "japan", "jp", "jpn",
    "japao", "china", "cn", "chn", "south korea", "kr", "kor", "coreia do sul",
    "israel", "il", "isr", "united arab emirates", "uae", "ae", "are",
    "south africa", "za", "zaf", "africa do sul",
    # These explicit regions exclude Brazil; inclusive or ambiguous regions do not.
    "europe", "europa", "european union", "eu", "european economic area", "eea",
    "north america", "america do norte", "emea", "apac", "asia pacific", "asia",
    "africa", "middle east", "oceania", "australia and new zealand", "anz",
})
_SAO_PAULO_STATE_QUALIFIERS = frozenset({
    "sp", "sao paulo", "sao paulo state", "state of sao paulo", "estado de sao paulo",
    "brazil", "brasil", "br", "bra",
})
# This is a conservative parser alias table, not a preference or a complete city
# gazetteer. Unsupported cities and location phrases are dropped for review.
_OTHER_OFFICE_CITIES = dict.fromkeys(
    ("campinas", "santos", "sao caetano do sul", "barueri"), _SAO_PAULO_STATE_QUALIFIERS,
) | {
    "london": frozenset({"uk", "gb", "gbr", "united kingdom", "england", "reino unido"}),
}


def _geography_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    return " ".join("".join(char for char in normalized
                            if not unicodedata.combining(char)).casefold().split())


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
            country = authorization.get("country")
            alias = re.sub(r"[^\w]+", " ", _geography_name(country)).strip() \
                if isinstance(country, str) else ""
            if alias in _BRAZIL_AUTHORIZATION_ALIASES:
                candidates.append(("open_to_brazil", authorization["quote"]))
            elif alias in _FOREIGN_AUTHORIZATION_ALIASES:
                candidates.append(("restricted_elsewhere", authorization["quote"]))
            else:
                dropped.append(("work_authorization", value,
                                "authorization country is missing or unsupported"))
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
        normalized = _geography_name(location)
        if _SAO_PAULO_CITY.fullmatch(normalized):
            mode = "office_sao_paulo"
        else:
            # Recognize the entire location, not just the leading fragment. All
            # suffixes must be known administrative qualifiers for that city.
            # Slash/semicolon/or lists are not administrative qualifiers: e.g.
            # "London / São Paulo" and "Santos / São Paulo" are ambiguous.
            parts = re.split(r"\s*,\s*|\s+[–-]\s+", normalized)
            qualifiers = _OTHER_OFFICE_CITIES.get(parts[0])
            if qualifiers is None or any(part not in qualifiers for part in parts[1:]):
                return None, "office city is not established by the location"
            mode = "office_elsewhere"
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


def _write_migration_outputs(outputs: list[tuple[Path, str]]) -> None:
    """Stage both outputs, publish without replacement, roll back owned links on error.

    The two paths cannot be made visible in one filesystem operation. A normal
    exception rolls back our published links; an interrupted process may leave an
    incomplete pair. Never remove a path another process replaced with its file.
    """
    from job_intake.annotation.gold_workbook import ensure_new_outputs

    ensure_new_outputs([path for path, _ in outputs])
    staged: list[tuple[Path, Path, tuple[int, int]]] = []
    published: list[tuple[Path, tuple[int, int]]] = []
    try:
        for output, content in outputs:
            path = output.expanduser().resolve()
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path.parent, prefix=path.name + ".",
                suffix=".tmp", delete=False,
            ) as handle:
                temporary = Path(handle.name)
                identity = os.fstat(handle.fileno())
                staged.append((temporary, path, (identity.st_dev, identity.st_ino)))
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
        for temporary, path, identity in staged:
            # Register before linking so an interrupt immediately after publication
            # still has an ownership record. A refused link cannot match our inode.
            published.append((path, identity))
            os.link(temporary, path)
    except BaseException as exc:
        for path, identity in reversed(published):
            try:
                current = path.lstat()
                if (current.st_dev, current.st_ino) == identity:
                    path.unlink()
            except FileNotFoundError:
                pass
            except OSError as cleanup_error:
                exc.add_note(f"Could not roll back migration output {path}: {cleanup_error}")
        raise
    finally:
        for temporary, _, _ in staged:
            temporary.unlink(missing_ok=True)


def migrate_gold_v2(source: Path, out: Path, *, rubric_version: str) -> dict:
    """v2 labeled → v3: role_label/relevance/gate_outcome/notes/source/hashes 1:1;
    tags: only labeled v2 tags with a v3 analogue (hiring_geography+work_authorization →
    hiring_eligibility by table; work_mode; working_language); method: blind→blind,
    assisted→curated; first_labeled_at=labeled_at. Refuses an existing out. Writes out
    and out.with_suffix('.manifest.json') with SHA-256 of the bytes parsed and output.
    Both files are staged before publication; a handled write failure rolls back
    published links still owned by this call. A process crash may leave a partial pair.
    """
    from job_intake.annotation.benchmark import parse_jsonl
    from job_intake.annotation.gold_workbook import (
        ensure_new_outputs,
        jsonl_content,
    )

    if not isinstance(rubric_version, str) or not rubric_version.strip():
        raise ValueError("rubric_version must be nonempty")
    manifest_path = out.with_suffix(".manifest.json")
    if source.expanduser().resolve() in {out.expanduser().resolve(),
                                         manifest_path.expanduser().resolve()}:
        raise ValueError("Migration outputs must not replace the v2 source")
    ensure_new_outputs([out, manifest_path])
    raw = source.read_bytes()
    cases = parse_jsonl(raw.decode("utf-8"))
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
    _write_migration_outputs([
        (out, content),
        (manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"),
    ])
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
