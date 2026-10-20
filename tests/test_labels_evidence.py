"""Fact validation is separate from model decisions and provider constraints."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
import test_labels_tagger as support
from test_labels_tagger import payload

from job_intake.annotation.ai import _anthropic_schema, _gemini_schema
from job_intake.annotation.scope import duty_unit_ids
from job_intake.annotation.text import source_units
from job_intake.labeling.gates import free_gates
from job_intake.labeling.prompt import build_prompt, response_schema_from_prompt
from job_intake.labeling.rubric import load_rubric
from job_intake.labeling.validate import LabelValidationError, validate_tags
from job_intake.models.job import FilterDecision, JobEvaluation, JobStatus

config = support.config
record = support.record
streams = support.streams

ROOT = Path(__file__).parents[1]


def checked(raw, record):
    return validate_tags(raw, source_units(replace(record)), ["analytics-leadership"])


@pytest.mark.parametrize(
    "change",
    [
        lambda raw: raw.update(relevance=True),
        lambda raw: raw.update(relevance=2.0),
        lambda raw: raw.update(confidence="API-SECRET"),
        lambda raw: raw.update(extra="secret"),
        lambda raw: raw["function_core"].update(value="unknown"),
        lambda raw: raw["preferred_focus"].update(values=["pricing_monetization"] * 2),
        lambda raw: raw["domain_fit"].update(industry="unknown"),
        lambda raw: raw["work_mode"].update(office_location="x" * 81),
    ],
)
def test_malformed_types_enums_and_extra_fields_reject_atomically(record, change):
    raw = payload(record)
    change(raw)
    with pytest.raises(LabelValidationError, match="Invalid labels-v3 model structure"):
        checked(raw, record)


@pytest.mark.parametrize(
    "tag",
    [
        "hiring_eligibility",
        "work_mode",
        "working_language",
        "function_core",
        "domain_fit",
        "preferred_focus",
    ],
)
def test_unknown_evidence_id_downgrades_only_affected_tag(record, tag):
    raw = payload(record)
    raw[tag]["evidence"] = ["API-SECRET"]
    result, warnings = checked(raw, record)
    assert f"tag:{tag}:unknown_unit" in warnings
    assert result["tags"][tag]["evidence"] == []
    assert result["relevance"] == 2 and result["role_label"] == "analytics-leadership"
    assert "API-SECRET" not in str((result, warnings))


@pytest.mark.parametrize(
    "tag,value",
    [
        ("hiring_eligibility", "restricted_elsewhere"),
        ("work_mode", "office_elsewhere"),
        ("working_language", "portuguese_only"),
        ("working_language", "other_language_required"),
    ],
)
def test_implied_restrictions_are_never_accepted(record, tag, value):
    raw = payload(record)
    raw[tag].update(value=value, basis="implied", evidence=raw["working_language"]["evidence"])
    result, warnings = checked(raw, record)
    assert result["tags"][tag]["value"] == "unspecified"
    assert f"tag:{tag}:implied_restriction" in warnings


@pytest.mark.parametrize(
    "text",
    [
        "You may lead analytics.",
        "Experience leading analytics.",
        "You will lead analytics?",
        "You might manage analytics.",
    ],
)
def test_candidate_history_and_uncertain_scope_do_not_prove_function(record, text):
    record.description_clean = "Responsibilities\n" + text + "\nRequirements\nEnglish required."
    units = source_units(replace(record))
    unit = next(u for u in units if text in u["text"])
    raw = payload(record) if "You will lead" in text else None
    if raw is None:
        # Build from the unchanged fixture, then select the current exact unit.
        base = replace(
            record,
            description_clean="Responsibilities\nYou will lead analytics.\n"
            "Requirements\nEnglish required.",
        )
        raw = payload(base)
    raw["function_core"]["evidence"] = [unit["unit_id"]]
    result, warnings = checked(raw, record)
    assert result["tags"]["function_core"]["value"] == "unspecified"
    assert any(w.startswith("tag:function_core:") for w in warnings)


def test_preferred_focus_cannot_cite_requirements_or_title(record):
    raw = payload(record)
    raw["preferred_focus"].update(
        values=["pricing_monetization"], evidence=raw["working_language"]["evidence"]
    )
    result, warnings = checked(raw, record)
    assert result["tags"]["preferred_focus"]["values"] == []
    assert "tag:preferred_focus:disallowed_section" in warnings


def test_domain_industry_is_separate_from_unsupported_candidate_requirement(record):
    raw = payload(record)
    raw["domain_fit"].update(requirement="required", required_domain="pharma_biotech")
    result, warnings = checked(raw, record)
    assert result["tags"]["domain_fit"]["industry"] == "fintech"
    assert result["tags"]["domain_fit"]["requirement"] == "none"
    assert "tag:domain_fit:disallowed_requirement_section" in warnings


def test_prompt_and_all_provider_schemas_have_duty_boundary_and_no_notes(record, streams):
    from job_intake.profiles import role_taxonomy

    rubric = load_rubric(ROOT / "config/labeling_rubric.yaml")
    units = source_units(replace(record))
    examples = [
        {
            "title": "Synthetic",
            "lines": [],
            "labels": {"relevance": 2},
            "reason": "compact",
            "notes": "PRIVATE-NOTES",
        }
    ]
    prompt, schema = build_prompt(record, units, role_taxonomy(streams), rubric, examples)
    assert prompt == build_prompt(record, units, role_taxonomy(streams), rubric, examples)[0]
    assert "PRIVATE-NOTES" not in prompt and "untrusted" in prompt
    assert (
        schema
        == response_schema_from_prompt(prompt)
        == _gemini_schema(prompt)
        == _anthropic_schema(prompt)
    )
    assert schema["properties"]["preferred_focus"]["properties"]["evidence"]["items"][
        "enum"
    ] == sorted(duty_unit_ids(units))
    assert "claims" not in _anthropic_schema(prompt)["properties"]


@pytest.mark.parametrize(
    "metadata,route",
    [
        ({"description_complete": False}, "needs_fetch"),
        ({"description_truncated": True}, "needs_fetch"),
        ({"truncated": True}, "needs_fetch"),
    ],
)
def test_incomplete_sources_stop_before_any_model(record, config, metadata, route):
    record.source_metadata = metadata
    assert free_gates(record, JobEvaluation(FilterDecision.REVIEW), {}, config).route == route


def test_closed_and_too_long_are_free_gates(record, config):
    record.status = JobStatus.CLOSED
    result = free_gates(record, JobEvaluation(FilterDecision.REVIEW), {}, config)
    assert result.stop and result.gate_reasons == ["vacancy_closed"] and result.relevance is None
    record.status = JobStatus.OPEN
    record.description_clean += " x" * 200
    result = free_gates(
        record, JobEvaluation(FilterDecision.REVIEW), {}, replace(config, max_source_chars=256)
    )
    assert result.stop and result.route == "too_long"


def test_rules_hiring_block_still_allows_relevance_labeling(record, config):
    evaluation = JobEvaluation(
        FilterDecision.REJECT,
        eligibility_status="ineligible",
        blocker_signals=["work_authorization_required"],
    )
    result = free_gates(record, evaluation, {"label": "analytics-leadership"}, config)
    assert not result.stop and result.gate_outcome == "ineligible"
    assert result.gate_reasons == ["work_authorization_required"]


@pytest.mark.parametrize("policy,expected", [("llm", 3), ("rules", 1), ("max", 3), ("min", 1)])
def test_final_policies_are_independent(policy, expected):
    from job_intake.labeling.aggregate import final_relevance

    assert final_relevance(3, 1, policy) == expected
    assert final_relevance(None, 2, policy) == 2


@pytest.mark.parametrize(
    "kind,expected",
    [
        ("difference", "relevance_disagreement"),
        ("confidence", "low_confidence"),
        ("top", "top_candidate"),
        ("block", "llm_only_block"),
        ("downgrade", "decisive_tag_downgraded"),
        ("role", "role_disagreement"),
    ],
)
def test_all_six_escalation_triggers(kind, expected):
    from job_intake.labeling.aggregate import escalation_triggers

    first = {"relevance": 2, "confidence": "high", "role_label": "analytics-leadership"}
    rules = 2
    classification = {"label": "analytics-leadership"}
    warnings = []
    if kind == "difference":
        rules = 1
    if kind == "confidence":
        first["confidence"] = "low"
    if kind == "top":
        first["relevance"] = rules = 3
    if kind == "downgrade":
        warnings = ["tag:function_core:no_evidence"]
    if kind == "role":
        classification = {"label": "business-analytics"}
    assert expected in escalation_triggers(first, rules, classification, warnings, kind == "block")


@pytest.mark.parametrize(
    "language,basis,outcome",
    [
        ("english", "explicit", "eligible"),
        ("english", "implied", "eligible"),
        ("portuguese_with_english", "explicit", "eligible"),
        ("unspecified", "explicit", "unknown"),
    ],
)
def test_eligible_needs_all_three_hiring_facts(language, basis, outcome):
    from job_intake.labeling.aggregate import gate_outcome
    from job_intake.labeling.gates import GateResult

    tags = {
        "hiring_eligibility": {"value": "open_to_brazil", "basis": "explicit"},
        "work_mode": {"value": "remote", "basis": "explicit"},
        "working_language": {"value": language, "basis": basis},
    }
    assert gate_outcome(tags, JobEvaluation(FilterDecision.REVIEW), GateResult()) == (
        outcome,
        [],
        False,
    )
    tags["hiring_eligibility"]["basis"] = "implied"
    assert gate_outcome(tags, JobEvaluation(FilterDecision.REVIEW), GateResult())[0] == "unknown"


def test_title_gating_is_narrow_and_positive_profile_title_overrides(record, config, streams):
    record.title = "Software Engineer"
    evaluation = JobEvaluation(FilterDecision.REVIEW)
    assert (
        free_gates(record, evaluation, {"label": "other", "method": "rules"}, config).route
        == "title_out_of_family"
    )
    assert not free_gates(record, evaluation, {"label": "other", "method": "model"}, config).stop
    record.title = "Data Science Manager and Software Engineer"
    assert not free_gates(
        record, evaluation, {"label": "other", "method": "rules"}, config, streams=streams
    ).stop


@pytest.mark.parametrize(
    "tag,value,text",
    [
        ("hiring_eligibility", "open_to_brazil", "Hiring is not open to Brazil."),
        ("work_mode", "remote", "Remote work is not available."),
        (
            "function_core",
            "analytics_for_decisions",
            "You will not lead analytics for business decisions.",
        ),
        ("preferred_focus", "pricing_monetization", "You will not own pricing."),
        ("working_language", "other_language_required", "You may speak French."),
    ],
)
def test_negative_or_modal_evidence_does_not_become_a_positive_fact(record, tag, value, text):
    raw = payload(record)
    # Fixed, trusted units exercise the content check independently of parser headings.
    units = [{"unit_id": "unit:one", "field": "description_clean", "section": "role", "text": text}]
    if tag == "preferred_focus":
        raw[tag].update(values=[value], evidence=["unit:one"])
    else:
        raw[tag].update(value=value, evidence=["unit:one"])
    result, warnings = validate_tags(raw, units, ["analytics-leadership"])
    assert result["tags"][tag].get("value", "unspecified") == "unspecified"
    assert not result["tags"][tag].get("values")
    assert f"tag:{tag}:conditional_evidence" in warnings


def test_not_only_does_not_invert_affirmative_analytics_duties(record):
    raw = payload(record)
    units = [
        {
            "unit_id": "unit:one",
            "field": "description_clean",
            "section": "role",
            "text": "You will not only lead analytics but also own business decisions.",
        }
    ]
    raw["function_core"]["evidence"] = ["unit:one"]
    result, _ = validate_tags(raw, units, ["analytics-leadership"])
    assert result["tags"]["function_core"]["value"] == "analytics_for_decisions"


def test_unknown_model_tag_name_never_reaches_diagnostics(record):
    raw = payload(record)
    raw["API_SECRET_SOURCE_TAG"] = {"value": "private"}
    with pytest.raises(LabelValidationError) as caught:
        checked(raw, record)
    assert caught.value.code == "schema_violation"
    assert "API_SECRET_SOURCE_TAG" not in str(caught.value)
