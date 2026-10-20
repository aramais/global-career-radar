"""Validate closed model structure and conservatively bind evidence to source."""

from __future__ import annotations

import copy
import re

from jsonschema import Draft202012Validator, validators

from job_intake.annotation.scope import (
    CANDIDATE_HISTORY,
    CONDITIONAL_SCOPE,
    NEGATED_LANGUAGE,
    OPTIONAL,
    ROLE_SCOPE,
    UNCONFIRMED,
    duty_unit_ids,
)
from job_intake.labeling.proofs import explicit_brazil_exclusion, hiring_evidence_warning
from job_intake.labeling.schema import RESTRICTIVE_VALUES, TAG_NAMES, response_schema

_STRICT = validators.extend(
    Draft202012Validator,
    type_checker=Draft202012Validator.TYPE_CHECKER.redefine(
        "integer", lambda _c, value: type(value) is int
    ),
)
# Persisted cache diagnostics are data too: only current tag/code pairs may replay.
VALIDATION_WARNING_CODES = frozenset(
    {
        "unknown_unit",
        "disallowed_section",
        "implied_restriction",
        "candidate_history",
        "conditional_evidence",
        "negated_language",
        "no_evidence",
        "missing_required_domain",
        "disallowed_requirement_section",
        "implied_requirement",
        "unsupported_evidence",
        "location_mismatch",
    }
)


def is_validation_warning(value: object) -> bool:
    if not isinstance(value, str):
        return False
    parts = value.split(":")
    return (
        len(parts) == 3
        and parts[0] == "tag"
        and parts[1] in TAG_NAMES
        and parts[2] in VALIDATION_WARNING_CODES
    )


_MODAL = re.compile(r"\b(?:may|might|could)\b", re.I)
_NEGATION = re.compile(r"\b(?:not|never|without|no)\b(?!\s+only\b)", re.I)


class LabelValidationError(ValueError):
    def __init__(self, code: str = "schema_violation") -> None:
        self.code = code
        super().__init__("Invalid labels-v3 model structure")


def unspecified_tags() -> dict:
    return {
        **{
            name: {"value": "unspecified", "evidence": [], "basis": "explicit"}
            for name in ("hiring_eligibility", "work_mode", "working_language", "function_core")
        },
        "domain_fit": {
            "industry": "other",
            "requirement": "none",
            "required_domain": None,
            "evidence": [],
            "basis": "explicit",
        },
        "preferred_focus": {"values": [], "evidence": []},
    }


def validate_tags(raw: dict, units: list[dict], role_ids: list[str]) -> tuple[dict, list[str]]:
    # Validate types/enums independently from evidence membership, which downgrades
    # the affected tag rather than discarding an otherwise well-formed response.
    schema = response_schema([], role_ids, duty_unit_ids=[], title_unit_ids=[])
    for name in TAG_NAMES:
        schema["properties"][name]["properties"]["evidence"] = {
            "type": "array",
            "maxItems": 3,
            "uniqueItems": True,
            "items": {"type": "string", "minLength": 1},
        }
    if not isinstance(raw, dict) or not _STRICT(schema).is_valid(raw):
        raise LabelValidationError()
    by_id = {unit["unit_id"]: unit for unit in units}
    duties = duty_unit_ids(units)
    titles = {unit["unit_id"] for unit in units if unit["field"] == "title"}
    tags = {name: copy.deepcopy(raw[name]) for name in TAG_NAMES}
    warnings = []
    for name, tag in tags.items():
        ids = tag["evidence"]
        contexts = [by_id[uid] for uid in ids if uid in by_id]
        code = None
        if any(uid not in by_id for uid in ids):
            code = "unknown_unit"
        elif (
            name in {"hiring_eligibility", "work_mode"}
            and any(unit.get("section") == "company" for unit in contexts)
            or name == "function_core"
            and any(uid not in duties | titles for uid in ids)
            or name == "preferred_focus"
            and any(uid not in duties for uid in ids)
            or name == "working_language"
            and not (tag["value"] == "english" and tag["basis"] == "implied")
            and any(
                unit.get("section") not in {"requirements", "role"}
                and not unit.get("field", "").startswith("source_metadata.")
                for unit in contexts
            )
        ):
            code = "disallowed_section"
        text = " ".join(unit["text"] for unit in contexts)
        restrictive = (
            tag.get("value") in RESTRICTIVE_VALUES.get(name, ())
            or name == "domain_fit"
            and tag["requirement"] == "required"
        )
        if not code and restrictive and tag.get("basis") != "explicit":
            code = "implied_restriction"
        if (
            not code
            and name == "function_core"
            and CANDIDATE_HISTORY.search(text)
            and not ROLE_SCOPE.search(text)
        ):
            code = "candidate_history"
        if not code and (
            any(unit["text"].rstrip().endswith("?") for unit in contexts)
            or CONDITIONAL_SCOPE.search(text)
            or UNCONFIRMED.search(text)
            or _MODAL.search(text)
            or (_NEGATION.search(text) and not (
                name == "hiring_eligibility" and explicit_brazil_exclusion(tag, contexts)
            ))
            or (
                OPTIONAL.search(text) and (name != "domain_fit" or tag["requirement"] == "required")
            )
        ):
            code = "conditional_evidence"
        if not code and name == "working_language" and NEGATED_LANGUAGE.search(text):
            code = "negated_language"
        fact_present = (
            tag.get("value", "unspecified") != "unspecified"
            or bool(tag.get("values"))
            or name == "domain_fit"
        )
        if not code and fact_present and not ids:
            code = "no_evidence"
        if not code:
            code = hiring_evidence_warning(name, tag, contexts, source=units)
        if name == "domain_fit" and not code:
            if tag["requirement"] != "none" and (
                not tag.get("required_domain")
                or (tag["required_domain"] == "other" and not tag.get("domain", "").strip())
            ):
                code = "missing_required_domain"
            elif tag["requirement"] != "none" and not any(
                unit.get("section") == "requirements" for unit in contexts
            ):
                code = "disallowed_requirement_section"
            elif tag["requirement"] == "preferred" and tag.get("basis") == "implied":
                code = "implied_requirement"
            if code:
                # Employer industry is independent of the candidate requirement.
                tag.update(requirement="none", required_domain=None)
                tag.pop("domain", None)
                warnings.append(f"tag:{name}:{code}")
                code = None
        if code:
            tags[name] = unspecified_tags()[name]
            warnings.append(f"tag:{name}:{code}")
        else:
            for field in ("office_location", "domain"):
                if field in tag:
                    if tag[field].strip():
                        tag[field] = tag[field].strip()
                    else:
                        tag.pop(field)
            if name == "domain_fit" and tag["requirement"] == "none":
                tag["required_domain"] = None
                tag.pop("domain", None)
    return {
        "tags": tags,
        "role_label": raw["role_label"],
        "relevance": raw["relevance"],
        "confidence": raw["confidence"],
    }, warnings
