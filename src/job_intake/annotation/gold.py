"""Vacancy-level human gold and blind sampling, without models or database writes."""

from __future__ import annotations

import copy
import math
import random
import re
import sqlite3
import unicodedata
from collections import Counter, defaultdict
from datetime import UTC, datetime
from urllib.parse import quote

from job_intake.annotation.benchmark import read_jsonl  # noqa: F401
from job_intake.annotation.eval_runner import read_saved_records
from job_intake.annotation.text import TEXT_VERSION, normalize_quote, source_hash, source_units
from job_intake.config.settings import AppConfig, load_yaml_mapping
from job_intake.models.job import FilterDecision, JobRecord
from job_intake.profiles import load_streams

GOLD_SCHEMA = "vacancy-tags-v2"
DEFAULT_PROFILE_IDS = (
    "product-management", "analytics-leadership", "business-analytics", "data-science-management",
)
TAG_VALUES = {
    "hiring_geography": (
        "brazil_explicit", "includes_brazil_region", "excludes_brazil", "restricted_elsewhere",
        "unspecified",
    ),
    "work_mode": ("remote", "remote_or_hybrid", "hybrid", "onsite", "unspecified"),
    "working_language": (
        "english_required", "other_language_required", "english_optional", "unspecified",
    ),
    "work_authorization": ("country_required", "not_required", "unspecified"),
    "employment_type": ("full_time", "contract", "part_time", "unspecified"),
    "vacancy_closed": ("true", "false"),
    "role_family": (
        "product", "analytics", "data_science", "business_strategy_finance", "engineering",
        "design", "sales_marketing_partnerships", "support_ops", "other",
    ),
    "analytics_intensity": ("core", "supporting", "none", "unspecified"),
    "seniority": ("ic", "lead", "manager", "head_director", "executive", "unspecified"),
    "project_management": ("duty", "skill_only", "explicitly_absent", "unspecified"),
    "people_interaction": ("duty", "skill_only", "explicitly_absent", "unspecified"),
    "decision_influence": ("duty", "skill_only", "explicitly_absent", "unspecified"),
    "isolated_analysis": ("explicit", "not_indicated"),
    "direct_reports": ("yes", "explicitly_none", "unspecified"),
    "hands_on_share": ("predominant", "partial", "none", "unspecified"),
    "business_economics": ("duty", "skill_only", "unspecified"),
    "finance_gm_partnership": ("duty", "skill_only", "unspecified"),
    "causal_experimentation": ("duty", "skill_only", "unspecified"),
    "marketplace_domain": ("yes", "unspecified"),
}
DEFAULT_STRATA = {
    "suppressed": 40, "full_review": 40, "never_ai": 30, "local_reject": 20, "difficult": 30,
}
_SOURCE_FIELDS = (
    "source", "company", "title", "original_url", "apply_url", "source_job_id", "posted_at",
    "location_text", "remote_text", "employment_type", "salary_text", "timezone_text",
    "description_raw", "description_clean", "status",
)
_METADATA_FIELDS = (
    "applicant_location_requirements", "working_language", "description_complete",
)
_REQUIRED = {
    "job_uid", "source_hash", "text_version", "labeled_at", "method", "tags", "relevance",
    "gate_outcome", "notes", "role_label",
}
_ROOT_FIELDS = _REQUIRED | {"schema", "source", "label_scope"}
_PRODUCT_TITLE = re.compile(r"\bproduct\b|\bPM\b", re.I)
_OTHER_PRODUCT_TITLE = re.compile(r"\b(?:design(?:er)?|marketing)\b", re.I)
_ANALYTICS = re.compile(r"\b(?:analy\w*|data|metrics?|experiments?|statistics)\b", re.I)
_NO_QUOTE_VALUES = {"unspecified", "not_indicated", "false"}
_LABEL_DATE = re.compile(
    r"\d{4}-\d{2}-\d{2}(?:[Tt]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[Zz]|[+-]\d{2}:\d{2}))?"
)
_DIFFICULT_FEATURES = (
    "us_visa", "brazil_exclusion", "language_question", "remote_or_hybrid", "negated_list",
    "pm_without_analytics", "ic_stakeholders",
)


def gold_json_schema(*, template: bool = False) -> dict:
    """Closed JSON Schema; only templates permit unanswered human decision fields."""
    nullable = [None] if template else []
    label_properties = {}
    for tag, values in TAG_VALUES.items():
        properties = {
            "value": {"type": "string", "enum": list(values)},
            "quote": {"type": "string"},
        }
        if tag == "work_authorization":
            properties["country"] = {"type": "string", "minLength": 1}
        if tag == "work_mode":
            properties["office_location"] = {"type": "string", "minLength": 1}
        label_properties[tag] = {
            "type": "object", "additionalProperties": False, "required": ["value", "quote"],
            "properties": properties,
        }
    source_properties = {
        field: {"type": ["string", "null"]} for field in _SOURCE_FIELDS
    }
    source_properties["source_metadata"] = {
        "type": "object", "additionalProperties": False, "properties": {
            "description_complete": {"type": "boolean"},
            **{field: {"anyOf": [
                {"type": "string"}, {"type": "array", "items": {"type": "string"}},
            ]} for field in _METADATA_FIELDS if field != "description_complete"},
        },
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": f"{GOLD_SCHEMA} human {'template' if template else 'gold'}",
        "type": "object", "additionalProperties": False, "required": sorted(_REQUIRED),
        "properties": {
            "schema": {"const": GOLD_SCHEMA},
            "job_uid": {"type": "string", "minLength": 1},
            "source_hash": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
            "text_version": {"type": "string", "minLength": 1},
            "labeled_at": {"anyOf": [
                {"type": "string", "format": "date"},
                {"type": "string", "format": "date-time"},
                *([{"type": "null"}] if template else []),
            ]},
            "method": {"enum": ["blind", "assisted", *nullable]},
            "tags": {"type": "object", "additionalProperties": False,
                     "properties": label_properties},
            "role_label": {"enum": [*DEFAULT_PROFILE_IDS, "other", *nullable]},
            "relevance": {"enum": [0, 1, 2, 3, *nullable]},
            "gate_outcome": {"enum": ["eligible", "ineligible", "unknown", *nullable]},
            "notes": {"type": "string"},
            "label_scope": {"enum": ["blind_decisions", "full_tags", "constraints"]},
            "source": {"type": "object", "additionalProperties": False,
                       "properties": source_properties},
        },
    }


def _snapshot(record: JobRecord) -> dict:
    result = {}
    for field in _SOURCE_FIELDS:
        value = getattr(record, field)
        result[field] = value.isoformat() if isinstance(value, datetime) else value
    # Whitelist source facts rather than copying arbitrary metadata with pipeline output.
    result["source_metadata"] = {
        field: copy.deepcopy(record.source_metadata[field]) for field in _METADATA_FIELDS
        if field in record.source_metadata
    }
    return result


def _quote_bindings(record: JobRecord, text: str) -> list[list[str]]:
    """Bind exact normalized quotes, including quotes across adjacent source units."""
    needle = normalize_quote(text)
    if not needle:
        return []
    groups: list[list[dict]] = []
    for unit in source_units(record):
        if groups and (
            groups[-1][-1]["field"] == unit["field"]
            and groups[-1][-1]["offset_end"] == unit["offset_start"]
        ):
            groups[-1].append(unit)
        else:
            groups.append([unit])
    matches = []
    for group in groups:
        characters, owners = [], []
        for unit in group:
            for character in unicodedata.normalize("NFC", unit["text"]):
                if character.isspace():
                    if characters and characters[-1] != " ":
                        characters.append(" ")
                        owners.append(unit["unit_id"])
                else:
                    characters.append(character)
                    owners.append(unit["unit_id"])
        haystack = "".join(characters)
        for match in re.finditer(re.escape(needle), haystack):
            matches.append(list(dict.fromkeys(owners[match.start():match.end()])))
    return matches


def _source_error(snapshot: object) -> str | None:
    if not isinstance(snapshot, dict) or set(snapshot) - {*_SOURCE_FIELDS, "source_metadata"}:
        return "source snapshot contains unknown or derived fields"
    if any(value is not None and not isinstance(value, str)
           for field, value in snapshot.items() if field != "source_metadata"):
        return "source snapshot fields must be text or null"
    metadata = snapshot.get("source_metadata", {})
    if not isinstance(metadata, dict) or set(metadata) - set(_METADATA_FIELDS):
        return "source metadata contains unknown or derived fields"
    for field, value in metadata.items():
        if field == "description_complete":
            if type(value) is not bool:
                return "description_complete must be boolean"
        elif not isinstance(value, str) and not (
            isinstance(value, list) and all(isinstance(item, str) for item in value)
        ):
            return f"{field} must be text or a list of text"
    return None


def validate_gold(
    rows: list[tuple[str, JobRecord]], cases: list[dict], *,
    profile_ids: list[str] | tuple[str, ...] | None = None,
    source_history: dict[str, list[dict]] | None = None,
) -> dict:
    """Validate human labels without inferring labels or depending on model claim IDs.

    Unanswered templates are valid preparation material, but never semantic gold.
    Source drift is visible and quotes must still rebind uniquely to the current source.
    Rebound IDs are returned in the report only; the human JSONL remains quote-based.
    """
    records = dict(rows)
    expected_profiles = set(profile_ids or DEFAULT_PROFILE_IDS)
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
        before = len(errors)
        if _REQUIRED - set(case) or set(case) - _ROOT_FIELDS:
            finding(errors, uid, "invalid_fields", "Missing required or unknown gold fields.")
        if not isinstance(uid, str) or not uid.strip() or uid in seen:
            finding(errors, uid, "invalid_job_uid", "job_uid must be nonempty and unique.")
            continue
        seen.add(uid)
        if uid not in records:
            finding(errors, uid, "missing_source", "Vacancy is missing from the active source.")
            continue
        record = records[uid]
        if case.get("schema", GOLD_SCHEMA) != GOLD_SCHEMA:
            finding(errors, uid, "invalid_schema",
                    "Unsupported gold schema; v1 requires manual migration to role_label "
                    "and scalar relevance. Old ratings are never converted by argmax.")
        if "label_scope" in case and case["label_scope"] not in (
            "blind_decisions", "full_tags", "constraints",
        ):
            finding(errors, uid, "invalid_label_scope", "Unknown labeling scope.")
        if not isinstance(case.get("source_hash"), str) or not re.fullmatch(
            r"[0-9a-f]{64}", case["source_hash"],
        ):
            finding(errors, uid, "invalid_source_hash", "source_hash must be a SHA-256 digest.")
        elif case["source_hash"] != source_hash(record):
            from job_intake.annotation.source_compat import check_source_compatibility

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
        if not isinstance(case.get("text_version"), str) or not case["text_version"].strip():
            finding(errors, uid, "invalid_text_version", "text_version must be nonempty.")
        elif case["text_version"] != TEXT_VERSION:
            finding(warnings, uid, "text_version_changed",
                    "Parser version changed; quotes have been rebound to current units.")
        if not isinstance(case.get("notes"), str):
            finding(errors, uid, "invalid_notes", "notes must be text.")
        if "source" in case and (message := _source_error(case["source"])):
            finding(errors, uid, "invalid_source_snapshot", message)
        role_label = case.get("role_label")
        if role_label is not None and (
            not isinstance(role_label, str) or role_label not in expected_profiles | {"other"}
        ):
            finding(errors, uid, "invalid_role_label",
                    "role_label must be one enabled stable profile ID, other, or null.")
        relevance = case.get("relevance")
        if relevance is not None and (type(relevance) is not int or relevance not in range(4)):
            finding(errors, uid, "invalid_relevance",
                    "relevance must be a scalar integer 0–3 or null; legacy per-profile "
                    "ratings require manual migration, never automatic argmax.")
        if case.get("gate_outcome") not in ("eligible", "ineligible", "unknown", None):
            finding(errors, uid, "invalid_gate_outcome", "Unknown gate_outcome.")
        if case.get("method") not in ("blind", "assisted", None):
            finding(errors, uid, "invalid_method", "method must be blind, assisted or null.")
        labeled_at = case.get("labeled_at")
        if labeled_at is not None:
            try:
                if not isinstance(labeled_at, str) or not _LABEL_DATE.fullmatch(labeled_at):
                    raise ValueError
                # The design permits a calendar date as well as a complete timestamp.
                datetime.fromisoformat(labeled_at.replace("Z", "+00:00").replace("z", "+00:00"))
            except ValueError:
                finding(errors, uid, "invalid_labeled_at",
                        "labeled_at must be an ISO date or RFC3339 timestamp.")
        tags = case.get("tags")
        validated_tags = {}
        if not isinstance(tags, dict) or set(tags) - set(TAG_VALUES):
            finding(errors, uid, "invalid_tags", "tags must use only the 19 closed tag names.")
            tags = {}
        for tag, label in tags.items():
            allowed_fields = {"value", "quote"}
            if tag == "work_authorization":
                allowed_fields.add("country")
            if tag == "work_mode":
                allowed_fields.add("office_location")
            if not isinstance(label, dict) or set(label) - allowed_fields or not {
                "value", "quote",
            } <= set(label):
                finding(errors, uid, "invalid_tag_label", f"Invalid label fields for {tag}.")
                continue
            value, evidence = label["value"], label["quote"]
            if not isinstance(value, str) or value not in TAG_VALUES[tag]:
                finding(errors, uid, "invalid_tag_value", f"Unknown enum value for {tag}.")
                continue
            if any(not isinstance(label[field], str) or not label[field].strip()
                   for field in set(label) - {"value", "quote"}):
                finding(errors, uid, "invalid_tag_detail", f"Empty country/location for {tag}.")
            if not isinstance(evidence, str) or (
                not normalize_quote(evidence) and value not in _NO_QUOTE_VALUES
            ):
                finding(errors, uid, "missing_quote", f"{tag} requires an attributable quote.")
                continue
            bindings = _quote_bindings(record, evidence) if normalize_quote(evidence) else []
            if evidence.strip() and len(bindings) != 1:
                code = "ambiguous_quote" if bindings else "quote_not_found"
                finding(errors, uid, code, f"{tag} quote must match exactly one source occurrence.")
                continue
            validated_tags[tag] = {**label, "evidence_ids": bindings[0] if bindings else []}
        any_labels = bool(tags) or role_label is not None or relevance is not None \
            or case.get("gate_outcome") is not None
        if any_labels and (case.get("method") is None or labeled_at is None):
            finding(errors, uid, "missing_label_provenance",
                    "Human labels require method and labeled_at; templates leave labels null.")
        complete = role_label is not None and relevance is not None \
            and case.get("gate_outcome") is not None
        if not complete:
            finding(warnings, uid, "unlabeled_case",
                    "Human decisions are incomplete; this case cannot enter decision metrics.")
        if len(errors) == before:
            rebound.append({**copy.deepcopy(case), "tags": validated_tags})
    labeled = [case for case in rebound if case["role_label"] is not None
               and case["relevance"] is not None and case["gate_outcome"] is not None]
    return {
        "schema": GOLD_SCHEMA, "valid": not errors, "rows": rebound,
        "counts": {"cases": len(cases), "valid_cases": len(rebound), "labeled": len(labeled),
                   "relevant": sum(case["relevance"] >= 2 for case in labeled)},
        "labeled_count": len(labeled),
        "relevant_count": sum(case["relevance"] >= 2 for case in labeled),
        "errors": errors, "warnings": warnings,
        "source_compatibility": compatibility,
    }


def parse_strata(strata: str | dict[str, int] | None, size: int = 160) -> dict[str, int]:
    if type(size) is not int or size < 1:
        raise ValueError("size must be positive")
    if strata is None:
        fractions = {key: count * size / 160 for key, count in DEFAULT_STRATA.items()}
        result = {key: math.floor(count) for key, count in fractions.items()}
        for key in sorted(fractions, key=lambda key: -(fractions[key] - result[key]))[
            :size - sum(result.values())
        ]:
            result[key] += 1
        return result
    if isinstance(strata, str):
        result = {}
        try:
            for entry in strata.split(","):
                name, count = entry.strip().split("=", 1)
                if name in result:
                    raise ValueError
                result[name] = int(count)
        except ValueError:
            raise ValueError("strata must be comma-separated name=count pairs") from None
    elif isinstance(strata, dict):
        result = dict(strata)
    else:
        raise ValueError("strata must be a mapping or name=count pairs")
    if set(result) - set(DEFAULT_STRATA) or any(
        type(count) is not int or count < 0 for count in result.values()
    ):
        raise ValueError("Unknown stratum or invalid nonnegative count")
    if sum(result.values()) != size:
        raise ValueError("Stratum counts must sum to size")
    return {name: result.get(name, 0) for name in DEFAULT_STRATA}


def difficult_features(record: JobRecord) -> list[str]:
    """Source-text sampling heuristics, deliberately not gold or policy labels."""
    text = "\n".join(unit["text"] for unit in source_units(record))
    flags = []
    if re.search(
        r"(?-i:\bUS\b|\bU\.S\.(?=\W|$))|"
        r"\b(?:United States|visa|sponsorship|authorization|work permit)\b", text, re.I,
    ):
        flags.append("us_visa")
    if re.search(r"\b(?:except|excluding|not (?:in|from)|cannot hire)[^.;\n]{0,60}Brazil\b",
                 text, re.I):
        flags.append("brazil_exclusion")
    if re.search(r"[^.!\n?]*(?:language|English|Portuguese)[^.!\n?]*\?", text, re.I):
        flags.append("language_question")
    if re.search(r"\bremote\b", text, re.I) and re.search(r"\bhybrid\b", text, re.I):
        flags.append("remote_or_hybrid")
    if re.search(r"<li\b[^>]*>[^<]*(?:\bno\b|\bnot\b|\bwithout\b|\bexcept\b)",
                 record.description_raw, re.I):
        flags.append("negated_list")
    if _product_title(record.title) and not _ANALYTICS.search(text):
        flags.append("pm_without_analytics")
    if _ANALYTICS.search(record.title) and not re.search(
        r"\b(?:lead|manager|head|director|chief|VP)\b", record.title, re.I,
    ) and re.search(r"\b(?:stakeholders?|partners?|cross.functional)\b", text, re.I):
        flags.append("ic_stakeholders")
    return flags


def _product_title(title: str) -> bool:
    """The product ownership titles in §10.1, excluding design/marketing titles."""
    return bool(_PRODUCT_TITLE.search(title) and not _OTHER_PRODUCT_TITLE.search(title))


def _ai_seen(annotation: dict) -> bool:
    route = annotation.get("prefilter", {})
    if isinstance(route, dict) and (
        route.get("routing") in {"suppressed", "full_review"}
        or route.get("usage") or route.get("stages")
    ):
        return True
    return bool(annotation.get("ai_enabled") or annotation.get("usage")
                or annotation.get("stages") or annotation.get("tags"))


class GoldSampleShortageError(ValueError):
    def __init__(self, manifest: dict) -> None:
        self.manifest = manifest
        short = ", ".join(
            f"{name}: {manifest['selected_counts'][name]}/{count}"
            for name, count in manifest["requested_counts"].items()
            if manifest["selected_counts"][name] < count
        )
        super().__init__("Insufficient unique vacancies for requested strata: " + short)


def sample_gold(
    settings: AppConfig, *, size: int = 160, seed: int = 20261013,
    strata: str | dict[str, int] | None = None,
    include_saved_rejects: bool = False,
) -> tuple[list[dict], dict]:
    """Select unique unlabeled cases; output no saved assessments in blind material.

    All products suppressed by AI and the highest full-review scores are fixed.
    Flexible strata use matching so overlapping candidates cannot silently reduce a quota.
    A shortage is a transparent error with a manifest, never filled by unrelated cases.
    """
    counts = parse_strata(strata, size)
    if type(seed) is not int:
        raise ValueError("seed must be an integer")
    rng = random.Random(seed)
    path, rows, problems = read_saved_records(settings.database_url, limit=None)
    if problems:
        raise ValueError("Invalid source rows must be repaired before making a gold sample")
    streams = load_streams(load_yaml_mapping(settings.rules_path),
                           load_yaml_mapping(settings.search_profiles_path))
    engines = [stream.engine for stream in streams]
    records = dict(rows)
    scores = {}
    saved_rejects = set()
    connection = sqlite3.connect(f"file:{quote(str(path), safe='/')}?mode=ro", uri=True)
    try:
        connection.execute("PRAGMA query_only=ON")
        columns = {row[1] for row in connection.execute("PRAGMA table_info(jobs)")}
        if "fit_score" not in columns:
            raise ValueError("Gold sampling requires saved fit_score; schema will not be migrated")
        if include_saved_rejects and "filter_decision" not in columns:
            raise ValueError("Saved reject fill requires canonical filter_decision")
        if "filter_decision" in columns:
            saved_rejects = {
                uid for uid, in connection.execute(
                    "SELECT job_uid FROM jobs WHERE filter_decision = ?", ("reject",),
                ) if uid in records
            }
        for uid, score in connection.execute("SELECT job_uid, fit_score FROM jobs"):
            if uid in records:
                if not isinstance(score, int | float) or not math.isfinite(score):
                    raise ValueError("Gold sampling requires finite saved scores")
                scores[uid] = score
    finally:
        connection.close()
    pools = defaultdict(list)
    features = {}
    source_rejects = set()
    for uid, record in sorted(rows):
        route = record.annotation.get("prefilter", {})
        routing = route.get("routing") if isinstance(route, dict) else None
        if routing in {"suppressed", "full_review"}:
            pools[routing].append(uid)
        if not _ai_seen(record.annotation):
            pools["never_ai"].append(uid)
        source_only = copy.copy(record)
        source_only.annotation = {}
        if all(engine.evaluate(source_only).decision == FilterDecision.REJECT
               for engine in engines):
            pools["local_reject"].append(uid)
            source_rejects.add(uid)
        features[uid] = difficult_features(record)
        if features[uid]:
            pools["difficult"].append(uid)
    saved_annotation_rejects = {
        uid for uid in saved_rejects if _ai_seen(records[uid].annotation)
    }
    if include_saved_rejects:
        pools["local_reject"] = sorted(source_rejects | saved_annotation_rejects)
    forced_products = [uid for uid in pools["suppressed"] if _product_title(records[uid].title)]
    if len(forced_products) > counts["suppressed"]:
        raise ValueError("suppressed quota is smaller than the mandatory product-title cases")
    fixed = dict.fromkeys(forced_products, "suppressed")
    for uid in sorted(pools["full_review"], key=lambda uid: (-scores[uid], uid))[
        :counts["full_review"]
    ]:
        fixed[uid] = "full_review"
    if include_saved_rejects:
        preferred_rejects = sorted(source_rejects - set(fixed))
        rng.shuffle(preferred_rejects)
        for uid in preferred_rejects[:counts["local_reject"]]:
            fixed[uid] = "local_reject"
    candidates = {}
    for name in counts:
        candidates[name] = [uid for uid in pools[name] if uid not in fixed]
        rng.shuffle(candidates[name])
        if name == "difficult":
            # Rare kinds are considered first; candidates remain only source heuristics.
            frequencies = Counter(flag for uid in candidates[name] for flag in features[uid])
            candidates[name].sort(key=lambda uid: min(frequencies[flag] for flag in features[uid]))
    owners: dict[str, str] = {}

    def assign(name: str, visited: set[str]) -> bool:
        for uid in candidates[name]:
            if uid in visited:
                continue
            visited.add(uid)
            previous = owners.get(uid)
            if previous is None or assign(previous, visited):
                owners[uid] = name
                return True
        return False

    fixed_counts = Counter(fixed.values())
    # The full_review stratum is fixed at its true top score rather than backfilled.
    for name in ("difficult", "local_reject", "suppressed", "never_ai"):
        for _ in range(counts[name] - fixed_counts[name]):
            if not assign(name, set()):
                break
    selected = {**fixed, **owners}
    selected_counts = Counter(selected.values())
    candidate_features = Counter(flag for flags in features.values() for flag in flags)
    selected_features = Counter(flag for uid in selected for flag in features[uid])
    manifest = {
        "schema": GOLD_SCHEMA, "created_at": datetime.now(UTC).isoformat(), "seed": seed,
        "database": str(path), "database_access": "read_only", "api_calls": 0,
        "requested_size": size, "selected_size": len(selected),
        "requested_counts": counts,
        "selected_counts": {name: selected_counts[name] for name in counts},
        "candidate_counts": {name: len(pools[name]) for name in counts},
        "mandatory_suppressed_product_job_uids": forced_products,
        "include_saved_rejects": include_saved_rejects,
        "local_reject_provenance": {
            "source_rule_candidates": len(source_rejects),
            "saved_reject_candidates": len(saved_rejects),
            "saved_annotation_reject_candidates": len(saved_annotation_rejects),
            "saved_only_candidates": len(saved_annotation_rejects - source_rejects),
            "selected_source_rule": sum(
                name == "local_reject" and uid in source_rejects for uid, name in selected.items()
            ),
            "selected_saved_only": sum(
                name == "local_reject" and uid not in source_rejects
                for uid, name in selected.items()
            ),
        },
        "blind_job_uids": [uid for uid, name in selected.items() if name == "never_ai"],
        "selection": {uid: {
            "stratum": name, "difficulty_features": features[uid], "saved_fit_score": scores[uid],
            "selection_kind": ("source_rule_reject" if uid in source_rejects else
                               "saved_annotation_reject") if name == "local_reject" else name,
        } for uid, name in selected.items()},
        "difficulty_feature_counts": dict(Counter(
            flag for uid, name in selected.items() if name == "difficult" for flag in features[uid]
        )),
        "candidate_difficulty_feature_counts": {
            flag: candidate_features[flag] for flag in _DIFFICULT_FEATURES
        },
        "selected_case_difficulty_feature_counts": {
            flag: selected_features[flag] for flag in _DIFFICULT_FEATURES
        },
        "absent_difficulty_features": [
            flag for flag in _DIFFICULT_FEATURES if not candidate_features[flag]
        ],
        "uncovered_difficulty_features": [
            flag for flag in _DIFFICULT_FEATURES
            if candidate_features[flag] and not selected_features[flag]
        ],
        "limitations": ["No human labels exist yet; relevance and accuracy are unmeasured.",
                         "Difficulty features select source text for review; they are not labels.",
                         "Candidate strata overlap; selected vacancies are unique."],
    }
    if manifest["local_reject_provenance"]["selected_saved_only"]:
        manifest["limitations"].append(
            "Explicit saved-reject fill enabled: local_reject also includes canonical saved "
            "deterministic exclusions after annotation; provenance is separate from source rules."
        )
    if len(selected) != size or any(
        selected_counts[name] != count for name, count in counts.items()
    ):
        raise GoldSampleShortageError(manifest)
    full_tags = [uid for uid, name in selected.items() if name == "difficult"]
    remaining = [uid for uid in selected if uid not in full_tags and uid not in
                 manifest["blind_job_uids"]]
    rng.shuffle(remaining)
    manifest["full_tag_job_uids"] = (full_tags + remaining)[:min(60, size)]
    order = list(selected)
    rng.shuffle(order)
    cases = [{
        "schema": GOLD_SCHEMA, "job_uid": uid, "source_hash": source_hash(records[uid]),
        "text_version": TEXT_VERSION, "source": _snapshot(records[uid]),
        "label_scope": "blind_decisions" if uid in manifest["blind_job_uids"] else (
            "full_tags" if uid in manifest["full_tag_job_uids"] else "constraints"
        ),
        "labeled_at": None, "method": None, "tags": {},
        "role_label": None, "relevance": None, "gate_outcome": None, "notes": "",
    } for uid in order]
    return cases, manifest
