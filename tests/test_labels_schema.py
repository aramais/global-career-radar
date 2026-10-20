from __future__ import annotations

import json
from pathlib import Path

import pytest

from job_intake.annotation.text import TEXT_VERSION, source_hash
from job_intake.labeling.schema import (
    GATE_REASONS,
    INDUSTRIES,
    LABELS_SCHEMA,
    PREDICTION_SCHEMA,
    TAG_NAMES,
    TAG_VALUES,
    gold_schema,
    prediction_schema,
    response_schema,
    validate_gold_row,
)
from job_intake.models.job import JobRecord

ROOT = Path(__file__).parents[1]


def job(text="Responsibilities:\nYou will lead pricing experiments.\nRemote within Brazil."):
    return JobRecord(source="test", company="Example", title="Analytics Manager",
                     original_url="https://example.test/job", description_clean=text)


def row(record=None, **overrides):
    record = record or job()
    return {
        "schema": LABELS_SCHEMA, "job_uid": "uid", "source_hash": source_hash(record),
        "text_version": TEXT_VERSION, "labeler": "human", "method": "blind",
        "rubric_version": "labels-rubric-2026-10-20", "first_labeled_at": "2026-10-17",
        "labeled_at": "2026-10-20T10:00:00-03:00", "role_label": "analytics-leadership",
        "relevance": 2, "gate_outcome": "unknown", "gate_reasons": [], "tags": {},
        "notes": "", **overrides,
    }


def codes(errors):
    return {error["code"] for error in errors}


def test_enums_follow_the_user_decisions_of_2026_10_20():
    assert LABELS_SCHEMA == "vacancy-labels-v3"
    assert PREDICTION_SCHEMA == "vacancy-labels-v3-prediction"
    assert TAG_NAMES == ("hiring_eligibility", "work_mode", "working_language",
                         "function_core", "domain_fit", "preferred_focus")
    assert len(INDUSTRIES) == 14 and INDUSTRIES[-1] == "other"
    assert {"mobility", "healthtech", "pharma_biotech", "public_sector_education"} <= set(
        INDUSTRIES)
    assert "health_pharma_biotech" not in INDUSTRIES
    assert TAG_VALUES["working_language"] == (
        "english", "portuguese_with_english", "portuguese_only", "other_language_required",
        "unspecified",
    )
    assert TAG_VALUES["work_mode"] == ("remote", "office_sao_paulo", "office_elsewhere",
                                       "unspecified")
    assert len(GATE_REASONS) == 6 and "personal_constraint" in GATE_REASONS


def test_gold_schema_is_closed_with_nullable_decisions_and_six_tags():
    schema = gold_schema()
    assert schema["additionalProperties"] is False
    assert {"schema", "job_uid", "source_hash", "labeler", "role_label", "relevance",
            "gate_outcome", "gate_reasons", "tags", "notes"} <= set(schema["required"])
    assert schema["properties"]["relevance"]["enum"] == [0, 1, 2, 3, None]
    assert schema["properties"]["method"]["enum"] == ["blind", "curated", "assisted_llm", None]
    tags = schema["properties"]["tags"]
    assert tags["additionalProperties"] is False and "required" not in tags
    assert list(tags["properties"]) == list(TAG_NAMES)
    for tag, values in TAG_VALUES.items():
        assert tags["properties"][tag]["properties"]["value"]["enum"] == list(values)
        assert tags["properties"][tag]["required"] == ["value", "quote"]
    assert "$ref" not in json.dumps(schema)


def test_prediction_schema_binds_frozen_runs_and_keeps_legacy_fields():
    schema = prediction_schema()
    assert {"job_uid", "source_hash", "labeler", "variant", "status"} <= set(schema["required"])
    assert {"decision", "tier", "fit_score", "relevance_by_policy", "usage"} <= set(
        schema["properties"])
    evidence = schema["properties"]["tags"]["properties"]["function_core"]["properties"][
        "evidence"]
    assert evidence["maxItems"] == 3
    assert "personal_constraint" not in schema["properties"]["gate_reasons"]["items"]["enum"]


@pytest.mark.parametrize("name,builder", [("gold", gold_schema), ("prediction", prediction_schema)])
def test_schema_snapshots_equal_the_functions(name, builder):
    path = ROOT / "docs" / "schemas" / f"vacancy-labels-v3-{name}.schema.json"
    assert json.loads(path.read_text(encoding="utf-8")) == builder()


def test_response_schema_orders_facts_reason_decisions_and_restricts_ids():
    schema = response_schema(["title:000000", "description_clean:000001", "company:000002"],
                             ["analytics-leadership", "other", "analytics-leadership"],
                             duty_unit_ids=["description_clean:000001"],
                             title_unit_ids=["title:000000"])
    properties = schema["properties"]
    assert list(properties) == schema["required"] == [
        "hiring_eligibility", "work_mode", "working_language", "function_core", "domain_fit",
        "preferred_focus", "reason", "role_label", "relevance", "confidence",
    ]
    assert properties["role_label"]["enum"] == ["analytics-leadership", "other"]
    assert properties["relevance"] == {"type": "integer", "enum": [0, 1, 2, 3]}
    assert properties["function_core"]["properties"]["evidence"]["items"]["enum"] == [
        "description_clean:000001", "title:000000"]
    assert properties["preferred_focus"]["properties"]["evidence"]["items"]["enum"] == [
        "description_clean:000001"]
    assert properties["hiring_eligibility"]["properties"]["evidence"]["items"]["enum"] == [
        "title:000000", "description_clean:000001", "company:000002"]
    assert "required_domain" in properties["domain_fit"]["required"]
    assert all(value["additionalProperties"] is False for value in properties.values()
               if value.get("type") == "object")
    assert "$ref" not in json.dumps(schema)


def test_response_schema_without_ids_uses_max_items_zero():
    schema = response_schema([], [])
    evidence = schema["properties"]["work_mode"]["properties"]["evidence"]
    assert evidence == {"type": "array", "maxItems": 0, "items": {"type": "string"}}
    assert schema["properties"]["role_label"]["enum"] == ["other"]


def test_valid_row_and_null_is_not_zero():
    assert validate_gold_row(row()) == []
    template = row(method=None, first_labeled_at=None, labeled_at=None, rubric_version=None,
                   role_label=None, relevance=None, gate_outcome=None)
    assert validate_gold_row(template) == []
    assert validate_gold_row(row(relevance=0)) == []
    assert codes(validate_gold_row(row(relevance=True))) == {"invalid_relevance"}
    assert codes(validate_gold_row(row(relevance=4))) == {"invalid_relevance"}


def test_human_may_leave_unspecified_without_quote_but_not_a_restriction():
    record = job()
    unspecified = {"hiring_eligibility": {"value": "unspecified", "quote": ""}}
    assert validate_gold_row(row(record, tags=unspecified), record=record) == []
    restricted = {"hiring_eligibility": {"value": "restricted_elsewhere", "quote": ""}}
    assert codes(validate_gold_row(row(record, tags=restricted), record=record)) == {
        "missing_quote"}
    required = {"domain_fit": {"industry": "fintech", "requirement": "required",
                               "required_domain": "fintech", "quote": " "}}
    assert codes(validate_gold_row(row(tags=required))) == {"missing_quote"}


def test_implied_basis_never_sets_a_restrictive_value():
    record = job()
    implied = {"working_language": {"value": "portuguese_only",
                                    "quote": "You will lead pricing experiments.",
                                    "basis": "implied"}}
    assert codes(validate_gold_row(row(record, tags=implied), record=record)) == {
        "implied_restriction"}
    english = {"working_language": {"value": "english", "quote": "", "basis": "implied"}}
    assert validate_gold_row(row(record, tags=english), record=record) == []


def test_quotes_bind_exactly_once_to_the_supplied_source():
    record = job("Remote within Brazil. Remote within Brazil.")
    found = {"hiring_eligibility": {"value": "open_to_brazil", "quote": "pricing"}}
    assert codes(validate_gold_row(row(record, tags=found), record=record)) == {
        "quote_not_found"}
    twice = {"hiring_eligibility": {"value": "open_to_brazil", "quote": "Remote within Brazil"}}
    assert codes(validate_gold_row(row(record, tags=twice), record=record)) == {
        "ambiguous_quote"}
    assert validate_gold_row(row(record, tags=twice)) == []


def test_domain_fit_requires_a_domain_only_for_a_domain_requirement():
    def domain(**label):
        return validate_gold_row(row(tags={"domain_fit": {"quote": "", **label}}))

    assert domain(industry="pharma_biotech", requirement="none") == []
    assert domain(industry="consulting_outsourcing", requirement="required",
                  required_domain="other", domain="clinical trials",
                  quote="x") == []
    assert codes(domain(industry="fintech", requirement="preferred")) == {"invalid_tag_detail"}
    assert codes(domain(industry="fintech", requirement="none", required_domain="fintech")) == {
        "invalid_tag_detail"}
    assert codes(domain(industry="banking", requirement="none")) == {"invalid_tag_value"}


def test_preferred_focus_is_a_unique_subset():
    assert validate_gold_row(row(tags={"preferred_focus": {
        "values": ["pricing_monetization", "experimentation_causal"], "quote": ""}})) == []
    assert codes(validate_gold_row(row(tags={"preferred_focus": {
        "values": ["pricing_monetization", "pricing_monetization"], "quote": ""}}))) == {
        "invalid_tag_value"}


@pytest.mark.parametrize("overrides,code", [
    ({"schema": "vacancy-tags-v2"}, "invalid_schema"),
    ({"labeler": "llm:gemini"}, "invalid_labeler"),
    ({"method": "assisted"}, "invalid_method"),
    ({"gate_outcome": "eligible", "gate_reasons": ["vacancy_closed"]}, "invalid_gate_reasons"),
    ({"gate_reasons": ["visa"]}, "invalid_gate_reasons"),
    ({"labeled_at": None}, "missing_label_provenance"),
    ({"first_labeled_at": "2026-10-21"}, "invalid_labeled_at"),
    ({"extra": 1}, "invalid_fields"),
    ({"tags": {"role_family": {"value": "analytics", "quote": ""}}}, "invalid_tags"),
])
def test_invalid_rows_have_stable_codes(overrides, code):
    assert code in codes(validate_gold_row(row(**overrides)))


def test_role_label_is_checked_against_enabled_profiles():
    assert validate_gold_row(row(role_label="other"), profile_ids=["analytics-leadership"]) == []
    assert codes(validate_gold_row(row(role_label="data-engineering"),
                                   profile_ids=["analytics-leadership"])) == {"invalid_role_label"}
