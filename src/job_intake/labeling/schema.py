"""Closed vacancy-labels-v3 enums, JSON Schemas and the human row validator.

The human and every machine labeler share one record and one set of enums; the
``labeler`` and ``rubric_version`` fields tell sources apart. Tag values are
immutable within a schema version: adding a value requires a new version.
"""

from __future__ import annotations

import copy
import re
from datetime import UTC, date, datetime

from job_intake.models.job import JobRecord
from job_intake.utils.identifiers import STREAM_ID_PATTERN

LABELS_SCHEMA = "vacancy-labels-v3"
PREDICTION_SCHEMA = "vacancy-labels-v3-prediction"
RELEVANCE_VALUES = (0, 1, 2, 3)
GATE_OUTCOMES = ("eligible", "ineligible", "unknown")
GATE_REASONS = (
    "hiring_region_restricted", "work_authorization_required", "office_outside_sp",
    "other_language_required", "vacancy_closed", "personal_constraint",
)
HUMAN_ONLY_GATE_REASONS = frozenset({"personal_constraint"})
METHODS = ("blind", "curated", "assisted_llm")
LABEL_SCOPES = ("blind_decisions", "full_tags", "constraints")
BASIS = ("explicit", "implied")
CONFIDENCE = ("high", "medium", "low")
STATUSES = ("labeled", "rules_only", "gated", "error")
DECISIONS = ("pass", "review", "reject")
TIERS = ("A", "B", "C")
INDUSTRIES = (
    "marketplace_classifieds", "ecommerce_retail", "mobility", "fintech", "saas_b2b",
    "consumer_apps", "gaming_betting", "healthtech", "pharma_biotech", "cybersecurity",
    "hardware_iot", "consulting_outsourcing", "public_sector_education", "other",
)
DOMAIN_REQUIREMENTS = ("none", "preferred", "required")
PREFERRED_FOCUS = (
    "pricing_monetization", "experimentation_causal", "unit_economics_pnl_finance",
    "marketplace_economics",
)
TAG_VALUES = {
    "hiring_eligibility": ("open_to_brazil", "restricted_elsewhere", "unspecified"),
    "work_mode": ("remote", "office_sao_paulo", "office_elsewhere", "unspecified"),
    "working_language": (
        "english", "portuguese_with_english", "portuguese_only", "other_language_required",
        "unspecified",
    ),
    "function_core": (
        "analytics_for_decisions", "analytics_reporting", "solo_analysis",
        "product_without_analytics", "non_analytics", "unspecified",
    ),
}
TAG_NAMES = (*TAG_VALUES, "domain_fit", "preferred_focus")
RESTRICTIVE_VALUES = {
    "hiring_eligibility": frozenset({"restricted_elsewhere"}),
    "work_mode": frozenset({"office_elsewhere"}),
    "working_language": frozenset({"portuguese_only", "other_language_required"}),
}
OFFICE_LOCATION_MAX = 80
DOMAIN_MAX = 60
EVIDENCE_MAX = 3
REASON_MAX = 400
# JSON Schema patterns search rather than fullmatch. The negative lookahead is a
# strict end anchor, unlike "$", which also matches before a final newline.
_ROLE_ID = rf"^{STREAM_ID_PATTERN}(?![\s\S])"
_GOLD_REQUIRED = {
    "schema", "job_uid", "source_hash", "text_version", "labeler", "method", "rubric_version",
    "first_labeled_at", "labeled_at", "role_label", "relevance", "gate_outcome",
    "gate_reasons", "tags", "notes",
}
_GOLD_FIELDS = _GOLD_REQUIRED | {"source", "label_scope"}
_PREDICTION_REQUIRED = (
    "schema", "job_uid", "source_hash", "labeler", "variant", "status", "role_label",
    "relevance", "gate_outcome", "gate_reasons", "tags",
)
_LABEL_DATE = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}"
    r"(?:[Tt](?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]"
    r"(?:\.(?P<fraction>[0-9]+))?(?:[Zz]|[+-](?:[01][0-9]|2[0-3]):[0-5][0-9]))?"
)


def _date_schema(*, nullable: bool) -> dict:
    return {"anyOf": [
        {"type": "string", "format": "date"}, {"type": "string", "format": "date-time"},
        *([{"type": "null"}] if nullable else []),
    ]}


def _nonblank_text(*, maximum: int | None = None, nullable: bool = False) -> dict:
    schema = {"type": ["string", "null"] if nullable else "string",
              "minLength": 1, "pattern": r"\S"}
    if maximum is not None:
        schema["maxLength"] = maximum
    return schema


def _restrictive_schema(condition: dict, *, human: bool) -> dict:
    """A normalized restrictive label cannot have an implied basis."""
    properties = {"basis": {"const": "explicit"}}
    if human:
        properties["quote"] = _nonblank_text()
    return {"if": condition, "then": {"properties": properties}}


def _tag_schemas(kind: str) -> dict[str, dict]:
    """Human tags cite one verbatim quote; machine tags cite up to three unit IDs."""
    if kind == "gold":
        evidence_name = "quote"
        evidence = {"type": "string"}
    else:
        evidence_name = "evidence"
        evidence = {"type": "array", "maxItems": EVIDENCE_MAX, "uniqueItems": True,
                    "items": {"type": "string", "minLength": 1}}
    basis = {"enum": list(BASIS)}
    result = {}
    for tag, values in TAG_VALUES.items():
        properties = {"value": {"type": "string", "enum": list(values)}}
        if tag == "work_mode":
            properties["office_location"] = _nonblank_text(maximum=OFFICE_LOCATION_MAX)
        properties.update({evidence_name: evidence, "basis": basis})
        result[tag] = {"type": "object", "additionalProperties": False,
                       "required": ["value", evidence_name], "properties": properties}
        if restricted := RESTRICTIVE_VALUES.get(tag):
            result[tag]["allOf"] = [_restrictive_schema(
                {"required": ["value"],
                 "properties": {"value": {"enum": sorted(restricted)}}}, human=kind == "gold",
            )]
    result["domain_fit"] = {
        "type": "object", "additionalProperties": False,
        "required": ["industry", "requirement", evidence_name],
        "properties": {
            "industry": {"type": "string", "enum": list(INDUSTRIES)},
            "requirement": {"type": "string", "enum": list(DOMAIN_REQUIREMENTS)},
            "required_domain": {"enum": [*INDUSTRIES, None]},
            "domain": _nonblank_text(maximum=DOMAIN_MAX),
            evidence_name: evidence, "basis": basis,
        },
        "if": {"required": ["requirement"],
               "properties": {"requirement": {"enum": ["preferred", "required"]}}},
        "then": {"required": ["required_domain"],
                 "properties": {"required_domain": {"enum": list(INDUSTRIES)}}},
        "else": {"properties": {"required_domain": {"type": "null"}}},
        "allOf": [
            {"if": {"required": ["required_domain"],
                    "properties": {"required_domain": {"const": "other"}}},
             "then": {"required": ["domain"]}},
            _restrictive_schema(
                {"required": ["requirement"],
                 "properties": {"requirement": {"const": "required"}}},
                human=kind == "gold",
            ),
        ],
    }
    result["preferred_focus"] = {
        "type": "object", "additionalProperties": False,
        "required": ["values", evidence_name],
        "properties": {
            "values": {"type": "array", "uniqueItems": True,
                       "items": {"type": "string", "enum": list(PREFERRED_FOCUS)}},
            evidence_name: evidence, "basis": basis,
        },
    }
    return result


def gold_schema() -> dict:
    """Closed human row; null decisions mean "not labeled", never 0 or unknown."""
    from job_intake.annotation.gold import gold_json_schema

    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": f"{LABELS_SCHEMA} human gold",
        "type": "object", "additionalProperties": False, "required": sorted(_GOLD_REQUIRED),
        "properties": {
            "schema": {"const": LABELS_SCHEMA},
            "job_uid": _nonblank_text(),
            "source_hash": {"type": "string", "pattern": "^[0-9a-f]{64}$",
                            "minLength": 64, "maxLength": 64},
            "text_version": _nonblank_text(),
            "source": copy.deepcopy(gold_json_schema()["properties"]["source"]),
            "labeler": {"const": "human"},
            "method": {"enum": [*METHODS, None]},
            "rubric_version": _nonblank_text(nullable=True),
            "first_labeled_at": _date_schema(nullable=True),
            "labeled_at": _date_schema(nullable=True),
            "label_scope": {"enum": list(LABEL_SCOPES)},
            "role_label": {
                "type": ["string", "null"], "pattern": _ROLE_ID,
                "description": "An enabled search_profiles stream ID or other; checked "
                               "against the current role taxonomy by the validator.",
            },
            "relevance": {"type": ["integer", "null"], "enum": [*RELEVANCE_VALUES, None]},
            "gate_outcome": {"enum": [*GATE_OUTCOMES, None]},
            "gate_reasons": {"type": "array", "uniqueItems": True,
                             "items": {"enum": list(GATE_REASONS)}},
            "tags": {"type": "object", "additionalProperties": False,
                     "properties": _tag_schemas("gold")},
            "notes": {"type": "string"},
        },
        "allOf": [
            {"if": {"required": ["gate_outcome"],
                    "properties": {"gate_outcome": {"const": "eligible"}}},
             "then": {"properties": {"gate_reasons": {"maxItems": 0}}}},
            {"if": {"anyOf": [
                {"required": ["tags"], "properties": {"tags": {"minProperties": 1}}},
                {"required": ["gate_reasons"],
                 "properties": {"gate_reasons": {"minItems": 1}}},
                *({"required": [field],
                   "properties": {field: {"not": {"type": "null"}}}}
                  for field in ("role_label", "relevance", "gate_outcome")),
            ]}, "then": {"properties": {
                "method": {"enum": list(METHODS)},
                "first_labeled_at": _date_schema(nullable=False),
                "labeled_at": _date_schema(nullable=False),
            }}},
        ],
    }


def prediction_schema() -> dict:
    """One machine row per vacancy, bindable by frozen_eval.create_run."""
    nullable_int = {"type": ["integer", "null"], "enum": [*RELEVANCE_VALUES, None]}
    nullable_text = _nonblank_text(nullable=True)
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": f"{LABELS_SCHEMA} machine prediction",
        "type": "object", "additionalProperties": False, "required": list(_PREDICTION_REQUIRED),
        "properties": {
            "schema": {"const": PREDICTION_SCHEMA},
            "job_uid": _nonblank_text(),
            "source_hash": {"type": "string", "pattern": "^[0-9a-f]{64}$",
                            "minLength": 64, "maxLength": 64},
            "labeler": {"type": "string",
                        "pattern": r"^(?:rules|llm|embedding):\S+(?![\s\S])"},
            "variant": _nonblank_text(),
            "status": {"enum": list(STATUSES)},
            "rubric_version": nullable_text,
            "rubric_status": {"enum": ["confirmed", "proposed", None]},
            "prompt_version": nullable_text,
            "aggregation_version": nullable_text,
            "role_label": {"type": ["string", "null"], "pattern": _ROLE_ID},
            "relevance": nullable_int,
            "gate_outcome": {"enum": [*GATE_OUTCOMES, None]},
            "gate_reasons": {"type": "array", "uniqueItems": True,
                             "items": {"enum": sorted(set(GATE_REASONS)
                                                      - HUMAN_ONLY_GATE_REASONS)}},
            "tags": {"type": "object", "additionalProperties": False,
                     "properties": _tag_schemas("prediction")},
            "rules_relevance": nullable_int,
            "llm_relevance": nullable_int,
            "llm_relevance_stale": {"type": "boolean"},
            "relevance_by_policy": {
                "type": "object", "additionalProperties": False,
                "properties": {policy: {"type": "integer", "enum": list(RELEVANCE_VALUES)}
                               for policy in ("llm", "rules", "max", "min")},
            },
            "confidence": {"enum": [*CONFIDENCE, None]},
            "escalated": {"type": "boolean"},
            "escalation_triggers": {"type": "array", "items": {"type": "string"}},
            "validation_warnings": {"type": "array", "items": {"type": "string"}},
            "route": nullable_text,
            "p2_candidate": {"type": ["boolean", "null"]},
            "decision": {"enum": [*DECISIONS, None]},
            "tier": {"enum": [*TIERS, None]},
            "fit_score": {"type": ["number", "null"]},
            "usage": {"type": "array", "items": {
                "type": "object", "required": ["stage", "model"],
                "properties": {"stage": _nonblank_text(), "model": _nonblank_text()},
            }},
            "latency_ms": {"type": ["integer", "null"], "minimum": 0},
            "errors": {"type": "array", "items": {"type": "object"}},
        },
        "allOf": [
            {"if": {"required": ["gate_outcome"],
                    "properties": {"gate_outcome": {"const": "eligible"}}},
             "then": {"properties": {"gate_reasons": {"maxItems": 0}}}},
        ],
    }


def _evidence_ids(ids: list[str]) -> dict:
    if not ids:
        return {"type": "array", "maxItems": 0, "items": {"type": "string"}}
    return {"type": "array", "maxItems": EVIDENCE_MAX,
            "items": {"type": "string", "enum": ids}}


def response_schema(
    unit_ids: list[str], role_ids: list[str], *,
    duty_unit_ids: list[str] | None = None, title_unit_ids: list[str] | None = None,
) -> dict:
    """Provider schema without ``$ref``: facts first, then reason, then decisions.

    ``function_core`` may cite duties and the title; ``preferred_focus`` duties only.
    Without explicit duty IDs both fall back to all units and S3 enforces sections.
    """
    units = list(dict.fromkeys(unit_ids))
    duties = units if duty_unit_ids is None else [
        uid for uid in dict.fromkeys(duty_unit_ids) if uid in units
    ]
    titles = [uid for uid in dict.fromkeys(title_unit_ids or []) if uid in units]
    basis = {"type": "string", "enum": list(BASIS)}

    def fact(tag: str, evidence: list[str], **extra: dict) -> dict:
        properties = {"value": {"type": "string", "enum": list(TAG_VALUES[tag])}, **extra,
                      "evidence": _evidence_ids(evidence), "basis": basis}
        return {"type": "object", "additionalProperties": False,
                "required": list(properties), "properties": properties}

    domain = {
        "requirement": {"type": "string", "enum": list(DOMAIN_REQUIREMENTS)},
        "industry": {"type": "string", "enum": list(INDUSTRIES)},
        "required_domain": {"type": ["string", "null"], "enum": [*INDUSTRIES, None]},
        "domain": {"type": "string", "maxLength": DOMAIN_MAX},
        "evidence": _evidence_ids(units), "basis": basis,
    }
    focus = {
        "values": {"type": "array", "uniqueItems": True,
                   "items": {"type": "string", "enum": list(PREFERRED_FOCUS)}},
        "evidence": _evidence_ids(duties),
    }
    properties = {
        "hiring_eligibility": fact("hiring_eligibility", units),
        "work_mode": fact("work_mode", units, office_location={
            "type": "string", "maxLength": OFFICE_LOCATION_MAX,
        }),
        "working_language": fact("working_language", units),
        "function_core": fact("function_core", [*duties, *(uid for uid in titles
                                                          if uid not in duties)]),
        "domain_fit": {"type": "object", "additionalProperties": False,
                       "required": list(domain), "properties": domain},
        "preferred_focus": {"type": "object", "additionalProperties": False,
                            "required": list(focus), "properties": focus},
        "reason": {"type": "string", "maxLength": REASON_MAX},
        "role_label": {"type": "string", "enum": [
            *(role for role in dict.fromkeys(role_ids) if role != "other"), "other",
        ]},
        "relevance": {"type": "integer", "enum": list(RELEVANCE_VALUES)},
        "confidence": {"type": "string", "enum": list(CONFIDENCE)},
    }
    return {"type": "object", "additionalProperties": False, "required": list(properties),
            "properties": properties}


def _parse_label_date(value: object) -> tuple[date | datetime, str] | None:
    if not isinstance(value, str) or (match := _LABEL_DATE.fullmatch(value)) is None:
        return None
    try:
        parsed = date.fromisoformat(value) if len(value) == 10 else datetime.fromisoformat(
            value.replace("Z", "+00:00").replace("z", "+00:00"),
        )
    except ValueError:
        return None
    # datetime stores microseconds, while RFC3339 permits longer fractions. Keep
    # their exact decimal ordering without rounding or rewriting the source text.
    return parsed, (match["fraction"] or "").rstrip("0")


def _valid_date(value: object) -> bool:
    return _parse_label_date(value) is not None


def _label_date_follows(first: tuple[date | datetime, str],
                        last: tuple[date | datetime, str]) -> bool:
    """Compare instants exactly; preserve legacy dates as calendar precision.

    A date-only value has no time or zone. If either side is date-only, compare
    the expressed calendar days and allow equality: order within that day is
    unknown. This compatibility rule never invents a midnight or changes old gold.
    Two timestamps instead compare their aware UTC instants, including all
    fractional digits. An aware epoch subtraction also supports boundary years
    whose UTC conversion would fall outside datetime's representable years.
    """
    first_value, last_value = first[0], last[0]
    if isinstance(first_value, datetime) and isinstance(last_value, datetime):
        epoch = datetime(1970, 1, 1, tzinfo=UTC)
        return (first_value.replace(microsecond=0) - epoch, first[1]) > (
            last_value.replace(microsecond=0) - epoch, last[1],
        )
    first_day = first_value.date() if isinstance(first_value, datetime) else first_value
    last_day = last_value.date() if isinstance(last_value, datetime) else last_value
    return first_day > last_day


def _tag_errors(tag: str, label: object, record: JobRecord | None,
                bindings: dict[str, list[str]]) -> list[tuple[str, str]]:
    from job_intake.annotation.gold import _quote_bindings
    from job_intake.annotation.text import normalize_quote

    if tag not in TAG_NAMES:
        return [("invalid_tags", f"Unknown v3 tag {tag}.")]
    schema = _tag_schemas("gold")[tag]
    if not isinstance(label, dict) or set(label) - set(schema["properties"]) \
            or not set(schema["required"]) <= set(label):
        return [("invalid_tag_label", f"Invalid label fields for {tag}.")]
    errors = []
    restrictive = False
    if tag in TAG_VALUES:
        if label["value"] not in TAG_VALUES[tag]:
            return [("invalid_tag_value", f"Unknown enum value for {tag}.")]
        restrictive = label["value"] in RESTRICTIVE_VALUES.get(tag, ())
    elif tag == "domain_fit":
        if label["industry"] not in INDUSTRIES or label["requirement"] not in DOMAIN_REQUIREMENTS:
            return [("invalid_tag_value", "Unknown industry or requirement for domain_fit.")]
        required_domain = label.get("required_domain")
        if label["requirement"] == "none" and required_domain is not None:
            errors.append(("invalid_tag_detail",
                           "required_domain is only set for a preferred/required domain."))
        if label["requirement"] != "none" and required_domain not in INDUSTRIES:
            errors.append(("invalid_tag_detail",
                           "A preferred/required domain needs required_domain."))
        if required_domain == "other" and "domain" not in label:
            errors.append(("invalid_tag_detail",
                           "required_domain other needs a domain description."))
        restrictive = label["requirement"] == "required"
    else:
        values = label["values"]
        if not isinstance(values, list) or len(set(map(str, values))) != len(values) \
                or any(value not in PREFERRED_FOCUS for value in values):
            return [("invalid_tag_value", "preferred_focus values must be unique enum values.")]
    for field, limit in (("office_location", OFFICE_LOCATION_MAX), ("domain", DOMAIN_MAX)):
        if field in label and (not isinstance(label[field], str) or not label[field].strip()
                               or len(label[field]) > limit):
            errors.append(("invalid_tag_detail", f"{field} must be text of at most {limit}."))
    if "basis" in label and label["basis"] not in BASIS:
        errors.append(("invalid_tag_value", f"Unknown basis for {tag}."))
    elif label.get("basis") == "implied" and restrictive:
        errors.append(("implied_restriction",
                       f"{tag}: an implied basis never establishes a restrictive value."))
    quote = label["quote"]
    if not isinstance(quote, str):
        return [*errors, ("missing_quote", f"{tag} quote must be text.")]
    if restrictive and not normalize_quote(quote):
        errors.append(("missing_quote", f"{tag} requires an attributable quote."))
    elif record is not None and normalize_quote(quote):
        matches = _quote_bindings(record, quote)
        if len(matches) != 1:
            code = "ambiguous_quote" if matches else "quote_not_found"
            errors.append((code, f"{tag} quote must match exactly one source occurrence."))
        else:
            bindings[tag] = matches[0]
    return errors


def check_gold_row(
    row: object, *, record: JobRecord | None = None,
    profile_ids: list[str] | tuple[str, ...] | None = None,
) -> tuple[list[dict], dict[str, list[str]]]:
    """Return (errors, quote bindings). Quotes rebind only when a source is supplied."""
    from job_intake.annotation.gold import _source_error

    errors: list[dict] = []
    bindings: dict[str, list[str]] = {}

    def error(code: str, message: str) -> None:
        errors.append({"code": code, "message": message})

    if not isinstance(row, dict):
        return [{"code": "invalid_case", "message": "Gold row must be an object."}], {}
    if _GOLD_REQUIRED - set(row) or set(row) - _GOLD_FIELDS:
        error("invalid_fields", "Missing required or unknown v3 gold fields.")
    if row.get("schema") != LABELS_SCHEMA:
        error("invalid_schema", f"Gold row schema must be {LABELS_SCHEMA}.")
    if not isinstance(row.get("job_uid"), str) or not row["job_uid"].strip():
        error("invalid_job_uid", "job_uid must be nonempty.")
    if not isinstance(row.get("source_hash"), str) \
            or not re.fullmatch(r"[0-9a-f]{64}", row["source_hash"]):
        error("invalid_source_hash", "source_hash must be a SHA-256 digest.")
    if not isinstance(row.get("text_version"), str) or not row["text_version"].strip():
        error("invalid_text_version", "text_version must be nonempty.")
    if row.get("labeler") != "human":
        error("invalid_labeler", "Gold rows are human labels.")
    if row.get("method") not in (*METHODS, None):
        error("invalid_method", "method must be blind, curated, assisted_llm or null.")
    rubric = row.get("rubric_version")
    if rubric is not None and (not isinstance(rubric, str) or not rubric.strip()):
        error("invalid_rubric_version", "rubric_version must be nonempty text or null.")
    for field in ("first_labeled_at", "labeled_at"):
        if row.get(field) is not None and not _valid_date(row[field]):
            error("invalid_labeled_at", f"{field} must be an ISO date or RFC3339 timestamp.")
    if "label_scope" in row and row["label_scope"] not in LABEL_SCOPES:
        error("invalid_label_scope", "Unknown labeling scope.")
    role = row.get("role_label")
    allowed_roles = None if profile_ids is None else {*profile_ids, "other"}
    if role is not None and (
        not isinstance(role, str) or not re.fullmatch(_ROLE_ID, role)
        or (allowed_roles is not None and role not in allowed_roles)
    ):
        error("invalid_role_label",
              "role_label must be one enabled stable profile ID, other, or null.")
    relevance = row.get("relevance")
    if relevance is not None and (type(relevance) is not int or relevance not in RELEVANCE_VALUES):
        error("invalid_relevance", "relevance must be an integer 0–3 or null.")
    if row.get("gate_outcome") not in (*GATE_OUTCOMES, None):
        error("invalid_gate_outcome", "Unknown gate_outcome.")
    reasons = row.get("gate_reasons")
    if not isinstance(reasons, list) or any(reason not in GATE_REASONS for reason in reasons) \
            or len(set(map(str, reasons))) != len(reasons):
        error("invalid_gate_reasons", "gate_reasons must be unique closed reason codes.")
        reasons = []
    elif reasons and row.get("gate_outcome") == "eligible":
        error("invalid_gate_reasons", "An eligible vacancy cannot carry gate reasons.")
    if not isinstance(row.get("notes"), str):
        error("invalid_notes", "notes must be text.")
    if "source" in row and (message := _source_error(row["source"])):
        error("invalid_source_snapshot", message)
    tags = row.get("tags")
    if not isinstance(tags, dict):
        error("invalid_tags", "tags must be an object of the six v3 tags.")
        tags = {}
    for tag, label in tags.items():
        for code, message in _tag_errors(tag, label, record, bindings):
            error(code, message)
    labeled = bool(tags) or bool(reasons) or any(
        row.get(field) is not None for field in ("role_label", "relevance", "gate_outcome")
    )
    if labeled and (row.get("method") is None or row.get("labeled_at") is None
                    or row.get("first_labeled_at") is None):
        error("missing_label_provenance",
              "Human labels require method, first_labeled_at and labeled_at.")
    first, last = (_parse_label_date(row.get(field))
                   for field in ("first_labeled_at", "labeled_at"))
    if first is not None and last is not None and _label_date_follows(first, last):
        error("invalid_labeled_at", "first_labeled_at cannot follow labeled_at.")
    # Keep stable human-facing error codes above; the shared schema is the structural
    # backstop. Source binding, dynamic roles and chronological comparisons remain runtime.
    if not errors:
        from job_intake.labeling.schema_validation import json_schema_errors

        errors.extend(json_schema_errors(row, kind="gold"))
    return errors, bindings


def validate_gold_row(
    row: object, *, record: JobRecord | None = None,
    profile_ids: list[str] | tuple[str, ...] | None = None,
) -> list[dict]:
    """Errors for one human v3 row; an empty list means the row is valid."""
    return check_gold_row(row, record=record, profile_ids=profile_ids)[0]
