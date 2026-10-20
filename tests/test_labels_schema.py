from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from job_intake.annotation.gold import _snapshot
from job_intake.annotation.text import TEXT_VERSION, source_hash
from job_intake.labeling.predictions import check_prediction_rows
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
from job_intake.labeling.schema_validation import json_schema_errors
from job_intake.models.job import JobRecord
from job_intake.profiles import load_streams

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


@pytest.mark.parametrize("value", [
    "2026-10-20", "2024-02-29", "2026-10-20T00:00:00Z",
    "2026-10-20t23:59:59z", "2026-10-20T12:00:00.123456789+23:59",
    "2026-10-20T12:00:00-23:59", "2026-10-20T12:00:00-00:00",
])
def test_valid_provenance_dates_and_aware_timestamps_keep_their_original_precision(value):
    case = row(first_labeled_at=value, labeled_at=value)
    assert validate_gold_row(case) == []
    assert json_schema_errors(case, kind="gold") == []
    assert case["first_labeled_at"] == case["labeled_at"] == value


@pytest.mark.parametrize("value", [
    "2026-02-29", "2026-13-01", "0000-01-01", "２０２６-10-20",
    "2026-10-20T24:00:00Z", "2026-10-20T12:60:00Z", "2026-10-20T12:00:60Z",
    "2026-10-20T12:00:00+00:60", "2026-10-20T12:00:00-00:60",
    "2026-10-20T12:00:00+24:00", "2026-10-20T12:00:00+23:99",
    "2026-10-20T12:00:00", "2026-10-20 12:00:00Z", "2026-10-20T12:00:00.Z",
    "2026-10-20T12:00:00+0300", "2026-10-20T12:00:00Z\n", 20261020,
])
def test_invalid_date_syntax_calendar_and_offset_are_rejected_by_both_runtime_paths(value):
    case = row(first_labeled_at=value, labeled_at=value)
    assert codes(validate_gold_row(case)) == {"invalid_labeled_at"}
    assert json_schema_errors(case, kind="gold")


@pytest.mark.parametrize("first,last,valid", [
    ("2026-10-20T14:00:00-03:00", "2026-10-20T13:00:00-03:00", False),
    ("2026-10-21T00:30:00+14:00", "2026-10-20T13:00:00-03:00", True),
    ("2026-10-20T13:00:00-03:00", "2026-10-21T00:30:00+14:00", False),
    ("2026-10-21T00:30:00+14:00", "2026-10-20T10:30:00Z", True),
    ("2026-10-20T12:00:00+00:30", "2026-10-20T11:29:59Z", False),
    ("2026-10-20T12:00:00.1234568Z", "2026-10-20T12:00:00.1234567Z", False),
    ("2026-10-20T12:00:00.1234567Z", "2026-10-20T12:00:00.1234568Z", True),
    ("2026-10-20T12:00:00.123456700Z", "2026-10-20T09:00:00.1234567-03:00", True),
    ("0001-01-01T00:00:00+23:59", "0001-01-01T00:00:00Z", True),
    ("9999-12-31T23:59:59Z", "9999-12-31T23:59:59-23:59", True),
])
def test_two_aware_provenance_timestamps_compare_exact_instants_in_utc(first, last, valid):
    errors = validate_gold_row(row(first_labeled_at=first, labeled_at=last))
    assert (not errors) is valid
    if not valid:
        assert codes(errors) == {"invalid_labeled_at"}


@pytest.mark.parametrize("first,last,valid", [
    ("2026-10-20", "2026-10-20", True),
    ("2026-10-21", "2026-10-20", False),
    ("2026-10-20", "2026-10-20T00:00:00+14:00", True),
    ("2026-10-20T23:59:59-12:00", "2026-10-20", True),
    ("2026-10-20", "2026-10-21T00:00:00+14:00", True),
    ("2026-10-21T00:00:00+14:00", "2026-10-20", False),
    ("2026-10-21", "2026-10-20T23:59:59-12:00", False),
])
def test_date_only_provenance_compares_calendar_precision_without_inventing_time(first, last,
                                                                                valid):
    case = row(first_labeled_at=first, labeled_at=last)
    assert (not validate_gold_row(case)) is valid
    assert case["first_labeled_at"] == first and case["labeled_at"] == last


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


@pytest.mark.parametrize("role", ["new_enabled_role", "new--role", "new-role-", "1_role"])
def test_all_loader_accepted_id_forms_work_in_gold_prediction_and_response(role):
    ids = [stream.id for stream in load_streams({}, {"streams": [{"id": role}]})]
    assert ids == [role]
    human = row(role_label=role)
    assert Draft202012Validator(gold_schema()).is_valid(human)
    assert validate_gold_row(human, profile_ids=ids) == []
    prediction = {
        "schema": PREDICTION_SCHEMA, "job_uid": "uid", "source_hash": "a" * 64,
        "labeler": "llm:test", "variant": "labels", "status": "labeled",
        "role_label": role, "relevance": 2, "gate_outcome": "unknown",
        "gate_reasons": [], "tags": {},
    }
    assert Draft202012Validator(prediction_schema()).is_valid(prediction)
    assert check_prediction_rows([prediction], profile_ids=ids) == {"uid": prediction}
    response = {
        **{tag: {"value": "unspecified", "evidence": [], "basis": "explicit"}
           for tag in TAG_VALUES},
        "domain_fit": {"requirement": "none", "industry": "other", "required_domain": None,
                       "domain": "", "evidence": [], "basis": "explicit"},
        "preferred_focus": {"values": [], "evidence": []},
        "reason": "", "role_label": role, "relevance": 2, "confidence": "high",
    }
    response["work_mode"]["office_location"] = ""
    assert Draft202012Validator(response_schema([], ids)).is_valid(response)


@pytest.mark.parametrize("role", ["Bad_role", "_role", "-role", "role name", "role/1",
                                   "role.1", "équipe", "role\n", "role\r", ""])
def test_invalid_stream_id_syntax_is_rejected_by_loader_and_v3_schemas(role):
    with pytest.raises(ValueError, match="Invalid stream id"):
        load_streams({}, {"streams": [{"id": role}]})
    human = row(role_label=role)
    assert not Draft202012Validator(gold_schema()).is_valid(human)
    assert "invalid_role_label" in codes(validate_gold_row(human, profile_ids=[role]))
    prediction = {
        "schema": PREDICTION_SCHEMA, "job_uid": "uid", "source_hash": "a" * 64,
        "labeler": "llm:test", "variant": "labels", "status": "labeled",
        "role_label": role, "relevance": 2, "gate_outcome": "unknown",
        "gate_reasons": [], "tags": {},
    }
    assert not Draft202012Validator(prediction_schema()).is_valid(prediction)
    with pytest.raises(ValueError, match=r"\$\.role_label"):
        check_prediction_rows([prediction], profile_ids=[role])


@pytest.mark.parametrize("builder", [gold_schema, prediction_schema,
                                    lambda: response_schema([], [])])
def test_schemas_are_valid_draft_2020_12_documents(builder):
    Draft202012Validator.check_schema(builder())


@pytest.mark.parametrize("label,valid", [
    ({"value": "remote", "quote": ""}, True),
    ({"value": "remote", "quote": "", "office_location": "São Paulo"}, True),
    ({"value": "remote", "quote": "", "office_location": ""}, False),
    ({"value": "remote", "quote": "", "office_location": "  "}, False),
    ({"value": "remote", "quote": "", "office_location": "x" * 81}, False),
    ({"value": "office_elsewhere", "quote": "  "}, False),
    ({"value": "office_elsewhere", "quote": "Office in London.", "basis": "implied"}, False),
])
def test_gold_schema_and_runtime_agree_on_work_mode_structure(label, valid):
    case = row(tags={"work_mode": label})
    assert Draft202012Validator(gold_schema()).is_valid(case) is valid
    assert (not validate_gold_row(case)) is valid


@pytest.mark.parametrize("detail,valid", [
    ({}, False), ({"domain": ""}, False), ({"domain": "   "}, False),
    ({"domain": "x" * 61}, False), ({"domain": "clinical trials"}, True),
])
def test_other_required_domain_requires_a_nonblank_description_in_both_contracts(detail, valid):
    case = row(tags={"domain_fit": {
        "industry": "consulting_outsourcing", "requirement": "required",
        "required_domain": "other", "quote": "Clinical trials experience required.", **detail,
    }})
    assert Draft202012Validator(gold_schema()).is_valid(case) is valid
    assert (not validate_gold_row(case)) is valid
    assert (not json_schema_errors(case, kind="gold")) is valid


@pytest.mark.parametrize("fields", [
    {"job_uid": " "}, {"text_version": " "}, {"rubric_version": " "},
    {"method": None}, {"labeled_at": None}, {"first_labeled_at": None},
    {"gate_outcome": "eligible", "gate_reasons": ["vacancy_closed"]},
    {"tags": {"hiring_eligibility": {"value": "restricted_elsewhere", "quote": ""}}},
    {"tags": {"working_language": {"value": "other_language_required", "quote": " "}}},
    {"tags": {"working_language": {"value": "portuguese_only", "quote": "required",
                                    "basis": "implied"}}},
])
def test_gold_schema_matches_runtime_constraints_without_source_context(fields):
    case = row(**fields)
    assert not Draft202012Validator(gold_schema()).is_valid(case)
    assert validate_gold_row(case)


def test_schema_and_runtime_both_accept_unanswered_and_partial_gold():
    unanswered = row(method=None, first_labeled_at=None, labeled_at=None, rubric_version=None,
                     role_label=None, relevance=None, gate_outcome=None)
    partial = row(relevance=None, tags={"work_mode": {"value": "remote", "quote": ""}})
    for case in (unanswered, partial):
        assert Draft202012Validator(gold_schema()).is_valid(case)
        assert validate_gold_row(case) == []


def test_runtime_only_checks_are_distinct_from_the_structural_schema():
    record = job()
    case = row(tags={"work_mode": {"value": "remote", "quote": "Office in London."}})
    assert Draft202012Validator(gold_schema()).is_valid(case)
    assert codes(validate_gold_row(case, record=record)) == {"quote_not_found"}
    assert codes(validate_gold_row(row(), profile_ids=[])) == {"invalid_role_label"}


@pytest.mark.parametrize("value", [True, 2.0, float("nan")])
def test_shared_validator_refuses_non_integer_or_non_finite_decisions(value):
    assert json_schema_errors(row(relevance=value), kind="gold")


def test_direct_snapshot_str_enum_is_valid_without_a_json_roundtrip():
    record = job()
    snapshot = _snapshot(record)
    assert isinstance(snapshot["status"], str) and type(snapshot["status"]) is not str
    case = row(record, source=snapshot)
    assert json_schema_errors(case, kind="gold") == []
    assert validate_gold_row(case, record=record) == []
