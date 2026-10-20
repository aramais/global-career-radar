"""S4 deterministic aggregation of v3 tags; free to recompute, never calls a model.

Pure rules, final policy and escalation decisions do not perform I/O. Hiring
conditions are independent from relevance; model-only blockers need confirmation.
"""

from __future__ import annotations

import re
import unicodedata

from job_intake.labeling.rubric import Rubric

# v4: S3 recognizes more explicit hiring/office wording (audit 14); facts keys unchanged.
AGGREGATION_VERSION = "labels-aggregation-v4"
_LEGAL_FORM = re.compile(
    r"(?:[\s,]+(?:inc|incorporated|llc|ltd|limited|ltda|gmbh|s\s?[./]?\s?a)\.?)+$"
)
_P2_FUNCTIONS = frozenset({"analytics_for_decisions", "analytics_reporting"})


def normalize_company(name: str | None) -> str:
    """Casefolded name without diacritics, punctuation or a trailing legal form."""
    if not isinstance(name, str):
        return ""
    text = unicodedata.normalize("NFKD", name)
    text = "".join(character for character in text if not unicodedata.combining(character))
    # Punctuation can surround or separate the legal form: (LLC), -LLC, S.A.
    # Normalize it first so every supported suffix has the same boundary.
    text = " ".join(re.sub(r"[\W_]+", " ", text.casefold()).split())
    text = _LEGAL_FORM.sub("", text)
    return text.strip()


def _value(tags: dict, tag: str, field: str = "value") -> object:
    label = tags.get(tag) if isinstance(tags, dict) else None
    return label.get(field) if isinstance(label, dict) else None


def rules_relevance(
    role_label: str | None, tags: dict, rubric: Rubric, *, company: str | None = None,
) -> int:
    """Relevance 0–3 from tags by the confirmed aggregation policy (§6.5).

    A missing tag counts as ``unspecified``/empty. A preferred or required domain
    without ``required_domain`` counts as ``other``, an unfamiliar domain. Proposed
    sections never apply: without confirmed domain preferences there is neither an
    industry upgrade nor an industry or domain penalty, and the strong-company upgrade
    needs a confirmed list.
    """
    policy = rubric.aggregation
    if policy is None:
        raise ValueError("aggregation_not_confirmed: rules_relevance needs a confirmed policy")
    function_core = _value(tags, "function_core")
    base_table = policy["base_by_function_core"]
    result = base_table.get(function_core, base_table["unspecified"])
    if role_label == "other":
        result = min(result, policy["other_role_max"])
    preferences = rubric.domain_preferences is not None
    industry = _value(tags, "domain_fit", "industry")
    if result == 2:
        focus = _value(tags, "preferred_focus", "values")
        upgrades = {
            "preferred_focus": isinstance(focus, list) and bool(focus),
            "liked_industry": preferences and industry in rubric.liked_industries,
            "strong_company": bool(rubric.strong_company_names) and bool(company)
            and normalize_company(company) in {
                normalize_company(name) for name in rubric.strong_company_names
            },
        }
        if any(upgrades[name] for name in policy["upgrade_to_3_if_any"]):
            result = 3
    if preferences and industry in rubric.disliked_industries:
        result -= policy["disliked_industry_penalty"]
    requirement = _value(tags, "domain_fit", "requirement")
    if preferences and requirement in ("preferred", "required"):
        domain = _value(tags, "domain_fit", "required_domain") or "other"
        if domain not in rubric.own_domains:
            result -= policy["unfamiliar_domain_penalty"][requirement]
    return max(policy["min"], min(policy["max"], result))


def p2_candidate(function_core: str | None, relevance: int | None, gate_outcome: str | None,
                 ) -> bool:
    """Rubric p2: an analytics core with relevance 1, or relevance 2–3 when hiring is
    ineligible. Product management without analytics and non-analytics never qualify."""
    if function_core not in _P2_FUNCTIONS or type(relevance) is not int:
        return False
    return relevance == 1 or (relevance >= 2 and gate_outcome == "ineligible")


def gate_outcome(tags, evaluation, gates):
    """Return the source-grounded hiring outcome, reasons and model-only block flag."""
    if evaluation.eligibility_status == "ineligible" or gates.gate_outcome == "ineligible":
        return "ineligible", list(gates.gate_reasons), False
    reasons = []
    for name, value, reason in (
        ("hiring_eligibility", "restricted_elsewhere", "hiring_region_restricted"),
        ("work_mode", "office_elsewhere", "office_outside_sp"),
        ("working_language", "portuguese_only", "other_language_required"),
        ("working_language", "other_language_required", "other_language_required"),
    ):
        if (_value(tags, name) == value and _value(tags, name, "basis") == "explicit"
                and reason not in reasons):
            reasons.append(reason)
    if reasons:
        return "ineligible", reasons, True
    if (_value(tags, "hiring_eligibility") == "open_to_brazil"
            and _value(tags, "hiring_eligibility", "basis") == "explicit"
            and _value(tags, "work_mode") in {"remote", "office_sao_paulo"}
            and _value(tags, "working_language") in {"english", "portuguese_with_english"}):
        return "eligible", [], False
    return "unknown", [], False


def escalation_triggers(first, rules_rel, classification, warnings, llm_only_block):
    triggers = []
    if abs(first["relevance"] - rules_rel) >= 1:
        triggers.append("relevance_disagreement")
    if first["confidence"] == "low":
        triggers.append("low_confidence")
    if first["relevance"] == 3 or rules_rel == 3:
        triggers.append("top_candidate")
    if llm_only_block:
        triggers.append("llm_only_block")
    if any(warning.startswith(("tag:function_core:", "tag:hiring_eligibility:"))
           for warning in warnings):
        triggers.append("decisive_tag_downgraded")
    if classification.get("label") not in {None, "other", first["role_label"]}:
        triggers.append("role_disagreement")
    return triggers


def final_relevance(llm_rel, rules_rel, policy):
    if policy not in {"llm", "rules", "max", "min"}:
        raise ValueError("Unknown relevance policy")
    if llm_rel is None or policy == "rules":
        return rules_rel
    if policy == "llm":
        return llm_rel
    return max(llm_rel, rules_rel) if policy == "max" else min(llm_rel, rules_rel)
