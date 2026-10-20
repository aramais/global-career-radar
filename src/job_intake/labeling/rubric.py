"""Labeling rubric loader: anchors, aggregation policy, preferences and precedents.

Values come from ``config/labeling_rubric.yaml``; the user owns them. A section whose
``status`` is ``proposed`` is loaded for transparency but never applied: it reaches
neither the prompt nor the aggregation and does not change their hashes. The anchors
and notes are the root section: they always describe the prompt, and the root status
only reports whether the user has confirmed them.
"""

from __future__ import annotations

import copy
import json
import re
from dataclasses import dataclass
from pathlib import Path

from job_intake.config.settings import load_yaml_mapping
from job_intake.labeling.schema import (
    DOMAIN_REQUIREMENTS,
    INDUSTRIES,
    PREFERRED_FOCUS,
    REASON_MAX,
    RELEVANCE_VALUES,
    TAG_VALUES,
)
from job_intake.utils.text import stable_hash

DEFAULT_RUBRIC_PATH = Path("config/labeling_rubric.yaml")
SECTION_STATUSES = ("confirmed", "proposed")
UPGRADE_CONDITIONS = ("preferred_focus", "liked_industry", "strong_company")
PRECEDENT_COHORT_ROLES = frozenset({"calibration", "development"})
_ROOT_FIELDS = {
    "version", "status", "source", "anchors", "level_note", "employer_note", "gate_note",
    "aggregation", "domain_preferences", "strong_companies", "p2",
}
_SECTION_FIELDS = {
    "aggregation": {
        "status", "base_by_function_core", "other_role_max", "upgrade_to_3_if_any",
        "disliked_industry_penalty", "unfamiliar_domain_penalty", "min", "max",
    },
    "domain_preferences": {"status", "liked_industries", "disliked_industries", "own_domains"},
    "strong_companies": {"status", "names"},
    "p2": {"status", "rule"},
}
_PRECEDENT_FIELDS = {"job_uid", "source_hash", "cohort_id", "title", "lines", "labels", "reason"}
_PRECEDENT_LABELS = {"role_label", "relevance", "function_core", "domain_fit", "preferred_focus"}
_PRECEDENT_LINE = re.compile(r"\[[^|\]\s]+\|[a-z_.]+\] \S")


class RubricError(ValueError):
    """Invalid rubric or precedent file; ``code`` is stable for callers and tests."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


def _hash(value: object) -> str:
    return stable_hash(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                  separators=(",", ":")))


@dataclass(frozen=True, slots=True)
class Rubric:
    version: str
    status: str
    source: str
    anchors: dict[str, str]
    level_note: str
    employer_note: str
    gate_note: str
    section_status: dict[str, str]
    aggregation: dict | None
    domain_preferences: dict | None
    strong_company_names: tuple[str, ...]
    p2_rule: str | None
    prompt_rubric_hash: str
    aggregation_hash: str

    @property
    def rubric_version(self) -> str:
        return self.version

    @property
    def rubric_status(self) -> str:
        """``confirmed`` only when the anchors and the aggregation are confirmed."""
        return "confirmed" if self.status == "confirmed" and self.aggregation is not None \
            else "proposed"

    @property
    def liked_industries(self) -> frozenset[str]:
        return frozenset((self.domain_preferences or {}).get("liked_industries", ()))

    @property
    def disliked_industries(self) -> frozenset[str]:
        return frozenset((self.domain_preferences or {}).get("disliked_industries", ()))

    @property
    def own_domains(self) -> frozenset[str]:
        return frozenset((self.domain_preferences or {}).get("own_domains", ()))


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RubricError("invalid_rubric", f"{field} must be nonempty text")
    return value


def _rating(value: object, field: str) -> int:
    if type(value) is not int or value not in RELEVANCE_VALUES:
        raise RubricError("invalid_rubric", f"{field} must be an integer 0–3")
    return value


def _industries(value: object, field: str) -> list[str]:
    if not isinstance(value, list) or len(set(map(str, value))) != len(value) \
            or any(item not in INDUSTRIES for item in value):
        raise RubricError("invalid_rubric", f"{field} must list unique INDUSTRIES values")
    return list(value)


def _section(data: dict, name: str) -> dict:
    section = data[name]
    if not isinstance(section, dict) or set(section) != _SECTION_FIELDS[name]:
        raise RubricError("invalid_rubric", f"{name} must have exactly "
                          + ", ".join(sorted(_SECTION_FIELDS[name])))
    if section["status"] not in SECTION_STATUSES:
        raise RubricError("invalid_rubric", f"{name}.status must be confirmed or proposed")
    return section


def _aggregation(section: dict) -> dict:
    base = section["base_by_function_core"]
    if not isinstance(base, dict) or set(base) != set(TAG_VALUES["function_core"]):
        raise RubricError("invalid_rubric",
                          "aggregation.base_by_function_core must cover every function_core")
    upgrades = section["upgrade_to_3_if_any"]
    if not isinstance(upgrades, list) or len(set(map(str, upgrades))) != len(upgrades) \
            or any(item not in UPGRADE_CONDITIONS for item in upgrades):
        raise RubricError("invalid_rubric", "aggregation.upgrade_to_3_if_any has unknown items")
    penalty = section["unfamiliar_domain_penalty"]
    if not isinstance(penalty, dict) or set(penalty) != {"preferred", "required"}:
        raise RubricError("invalid_rubric",
                          "aggregation.unfamiliar_domain_penalty needs preferred and required")
    result = {
        "base_by_function_core": {key: _rating(value, "aggregation.base_by_function_core."
                                               + key) for key, value in base.items()},
        "other_role_max": _rating(section["other_role_max"], "aggregation.other_role_max"),
        "upgrade_to_3_if_any": list(upgrades),
        "disliked_industry_penalty": _rating(section["disliked_industry_penalty"],
                                             "aggregation.disliked_industry_penalty"),
        "unfamiliar_domain_penalty": {
            key: _rating(penalty[key], "aggregation.unfamiliar_domain_penalty." + key)
            for key in ("preferred", "required")
        },
        "min": _rating(section["min"], "aggregation.min"),
        "max": _rating(section["max"], "aggregation.max"),
    }
    if result["min"] > result["max"]:
        raise RubricError("invalid_rubric", "aggregation.min must not exceed max")
    return result


def parse_rubric(data: dict) -> Rubric:
    """Validate a closed rubric mapping and derive the three independent versions."""
    if not isinstance(data, dict) or set(data) - _ROOT_FIELDS \
            or not _ROOT_FIELDS - {"source"} <= set(data):
        raise RubricError("invalid_rubric", "rubric must have exactly the documented sections")
    if data["status"] not in SECTION_STATUSES:
        raise RubricError("invalid_rubric", "status must be confirmed or proposed")
    anchors = data["anchors"]
    if not isinstance(anchors, dict) or {str(key) for key in anchors} != {"0", "1", "2", "3"}:
        raise RubricError("invalid_rubric", "anchors must define relevance 0, 1, 2 and 3")
    anchors = {str(key): _text(value, f"anchors.{key}") for key, value in anchors.items()}
    anchors = {key: anchors[key] for key in ("0", "1", "2", "3")}
    notes = {name: _text(data[name], name) for name in ("level_note", "employer_note", "gate_note")}
    sections = {name: _section(data, name) for name in _SECTION_FIELDS}
    status = {name: section["status"] for name, section in sections.items()}
    aggregation = _aggregation(sections["aggregation"])
    preferences = sections["domain_preferences"]
    preferences = {key: _industries(preferences[key], "domain_preferences." + key)
                   for key in ("liked_industries", "disliked_industries", "own_domains")}
    if set(preferences["liked_industries"]) & set(preferences["disliked_industries"]):
        raise RubricError("invalid_rubric", "an industry cannot be both liked and disliked")
    names = sections["strong_companies"]["names"]
    if not isinstance(names, list) or any(not isinstance(name, str) or not name.strip()
                                          for name in names):
        raise RubricError("invalid_rubric", "strong_companies.names must be nonempty text")
    p2_rule = _text(sections["p2"]["rule"], "p2.rule")
    applied = {name: status[name] == "confirmed" for name in status}
    effective_preferences = preferences if applied["domain_preferences"] else None
    effective_names = tuple(names) if applied["strong_companies"] else ()
    prompt_part = {
        "anchors": anchors, **notes, "domain_preferences": effective_preferences,
        "strong_companies": list(effective_names),
    }
    aggregation_part = {
        "aggregation": aggregation if applied["aggregation"] else None,
        "domain_preferences": effective_preferences,
        "strong_companies": list(effective_names),
        "p2": p2_rule if applied["p2"] else None,
    }
    return Rubric(
        version=_text(data["version"], "version"), status=data["status"],
        source=str(data.get("source") or ""), anchors=anchors,
        level_note=notes["level_note"], employer_note=notes["employer_note"],
        gate_note=notes["gate_note"], section_status=status,
        aggregation=aggregation_part["aggregation"],
        domain_preferences=copy.deepcopy(effective_preferences),
        strong_company_names=effective_names, p2_rule=aggregation_part["p2"],
        prompt_rubric_hash=_hash(prompt_part), aggregation_hash=_hash(aggregation_part),
    )


def load_rubric(path: str | Path = DEFAULT_RUBRIC_PATH) -> Rubric:
    return parse_rubric(load_yaml_mapping(path))


def precedents_hash(precedents: list[dict]) -> str:
    return _hash(precedents)


def _precedent_errors(row: object, role_ids: set[str] | None) -> str | None:
    if not isinstance(row, dict) or set(row) != _PRECEDENT_FIELDS:
        return "precedent fields must be exactly " + ", ".join(sorted(_PRECEDENT_FIELDS))
    if any(not isinstance(row[key], str) or not row[key].strip()
           for key in ("job_uid", "cohort_id", "title", "reason")):
        return "job_uid, cohort_id, title and reason must be nonempty text"
    if not isinstance(row["source_hash"], str) \
            or not re.fullmatch(r"[0-9a-f]{64}", row["source_hash"]):
        return "source_hash must be a SHA-256 digest"
    if len(row["reason"]) > REASON_MAX:
        return f"reason must be at most {REASON_MAX} characters"
    lines = row["lines"]
    if not isinstance(lines, list) or not lines or any(
        not isinstance(line, str) or not _PRECEDENT_LINE.match(line) for line in lines
    ):
        return "lines must be '[unit_id|section] text' source lines"
    labels = row["labels"]
    if not isinstance(labels, dict) or set(labels) - _PRECEDENT_LABELS \
            or not {"role_label", "relevance", "function_core"} <= set(labels):
        return "labels need role_label, relevance and function_core; no other fields"
    role = labels["role_label"]
    if not isinstance(role, str) or (role_ids is not None and role not in role_ids | {"other"}):
        return "labels.role_label must be an enabled stream ID or other"
    if type(labels["relevance"]) is not int or labels["relevance"] not in RELEVANCE_VALUES:
        return "labels.relevance must be an integer 0–3"
    if labels["function_core"] not in TAG_VALUES["function_core"]:
        return "labels.function_core is not a v3 value"
    domain = labels.get("domain_fit", {"requirement": "none", "industry": "other"})
    if not isinstance(domain, dict) or set(domain) - {
        "requirement", "industry", "required_domain", "domain",
    } or domain.get("requirement") not in DOMAIN_REQUIREMENTS \
            or domain.get("industry") not in INDUSTRIES \
            or domain.get("required_domain", "other") not in INDUSTRIES:
        return "labels.domain_fit must use v3 requirement and industry values"
    focus = labels.get("preferred_focus", [])
    if not isinstance(focus, list) or len(set(map(str, focus))) != len(focus) \
            or any(item not in PREFERRED_FOCUS for item in focus):
        return "labels.preferred_focus must be unique v3 focus values"
    return None


def load_precedents(
    path: Path, *, registry: Path | None = None, role_ids: list[str] | None = None,
    minimum: int = 6, maximum: int = 10,
) -> list[dict]:
    """Validate the user-assembled precedent file; never generate precedents.

    Precedents come only from registered calibration or development cohorts, cover
    every relevance value and never carry gold notes. A holdout overlap is refused.
    Each later display to a model is recorded by the caller with
    ``cohorts.record_example_exposure``.
    """
    from job_intake.annotation.benchmark import read_jsonl
    from job_intake.annotation.cohorts import (
        DEFAULT_REGISTRY,
        registered_cohorts,
        validate_registered_seed_usage,
    )

    registry = registry or DEFAULT_REGISTRY
    rows = read_jsonl(path)
    allowed_roles = set(role_ids) if role_ids is not None else None
    for index, row in enumerate(rows, start=1):
        if message := _precedent_errors(row, allowed_roles):
            raise RubricError("invalid_precedent", f"line {index}: {message}")
    if not minimum <= len(rows) <= maximum:
        raise RubricError("precedent_count", f"use {minimum}–{maximum} precedents")
    if len({row["job_uid"] for row in rows}) != len(rows):
        raise RubricError("invalid_precedent", "precedent job_uid values must be unique")
    if {row["labels"]["relevance"] for row in rows} != set(RELEVANCE_VALUES):
        raise RubricError("precedent_balance", "precedents must cover relevance 0, 1, 2 and 3")
    cohorts = {manifest["cohort_id"]: manifest for manifest in registered_cohorts(registry)}
    for row in rows:
        manifest = cohorts.get(row["cohort_id"])
        if manifest is None or manifest["dataset_role"] not in PRECEDENT_COHORT_ROLES:
            raise RubricError("precedent_cohort",
                              "precedents must come from a registered calibration or "
                              "development cohort")
        if not any(member["job_uid"] == row["job_uid"]
                   and member["source_hash"] == row["source_hash"]
                   for member in manifest["members"]):
            raise RubricError("precedent_cohort", "precedent is not a member of its cohort")
    try:
        validate_registered_seed_usage(rows, registry=registry, purpose="prompt_demo")
    except ValueError as exc:
        raise RubricError("precedent_holdout", str(exc)) from exc
    return copy.deepcopy(rows)
