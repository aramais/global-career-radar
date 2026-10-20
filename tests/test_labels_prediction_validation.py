"""Regression checks for malformed v3 predictions before they reach metrics (H3)."""

from __future__ import annotations

import copy
import json

import pytest

from job_intake.labeling.metrics import queue_order, relevance_scale
from job_intake.labeling.predictions import check_prediction_rows
from job_intake.labeling.schema import PREDICTION_SCHEMA


def prediction(**fields):
    return {"schema": PREDICTION_SCHEMA, "job_uid": "uid", "source_hash": "a" * 64,
            "labeler": "llm:test", "variant": "labels", "status": "labeled",
            "role_label": "analytics-leadership", "relevance": 2, "gate_outcome": "unknown",
            "gate_reasons": [], "tags": {}, **fields}


@pytest.mark.parametrize("field", [
    "schema", "job_uid", "source_hash", "labeler", "variant", "status", "role_label",
    "relevance", "gate_outcome", "gate_reasons", "tags",
])
def test_every_required_prediction_field_is_checked(field):
    row = prediction()
    row.pop(field)
    with pytest.raises(ValueError, match=rf"row 1: \$\.{field} violates required"):
        check_prediction_rows([row])


@pytest.mark.parametrize("fields,path", [
    ({"unexpected": "private value"}, "$"),
    ({"job_uid": " "}, "$.job_uid"),
    ({"variant": "\n"}, "$.variant"),
    ({"labeler": "human"}, "$.labeler"),
    ({"labeler": "llm:test\n"}, "$.labeler"),
    ({"source_hash": "bad"}, "$.source_hash"),
    ({"source_hash": "a" * 64 + "\n"}, "$.source_hash"),
    ({"role_label": "Invalid Role"}, "$.role_label"),
    ({"role_label": "analytics-leadership\n"}, "$.role_label"),
    ({"status": "unknown"}, "$.status"),
    ({"relevance": True}, "$.relevance"),
    ({"relevance": 2.0}, "$.relevance"),
    ({"rules_relevance": False}, "$.rules_relevance"),
    ({"llm_relevance": 4}, "$.llm_relevance"),
    ({"relevance_by_policy": {"llm": True}}, "$.relevance_by_policy.llm"),
    ({"relevance_by_policy": {"custom": 1}}, "$.relevance_by_policy"),
    ({"confidence": {}}, "$.confidence"),
    ({"escalated": "false"}, "$.escalated"),
    ({"gate_outcome": "open"}, "$.gate_outcome"),
    ({"gate_outcome": "eligible", "gate_reasons": ["vacancy_closed"]}, "$.gate_reasons"),
    ({"gate_reasons": ["personal_constraint"]}, "$.gate_reasons[0]"),
    ({"gate_reasons": ["vacancy_closed", "vacancy_closed"]}, "$.gate_reasons"),
    ({"tags": {"role_family": {}}}, "$.tags"),
    ({"tags": {"preferred_focus": {}}}, "$.tags.preferred_focus"),
    ({"tags": {"preferred_focus": {"values": [[]], "evidence": []}}},
     "$.tags.preferred_focus.values[0]"),
    ({"tags": {"preferred_focus": {"values": ["pricing_monetization"] * 2,
                                    "evidence": []}}}, "$.tags.preferred_focus.values"),
    ({"tags": {"function_core": {"value": "private value", "evidence": []}}},
     "$.tags.function_core.value"),
    ({"tags": {"function_core": {"value": "unspecified", "evidence": [None]}}},
     "$.tags.function_core.evidence[0]"),
    ({"tags": {"function_core": {"value": "unspecified", "evidence": ["u"] * 4}}},
     "$.tags.function_core.evidence"),
    ({"tags": {"function_core": {"value": "unspecified", "evidence": [], "extra": 1}}},
     "$.tags.function_core"),
    ({"tags": {"work_mode": {"value": "remote", "evidence": [], "office_location": ""}}},
     "$.tags.work_mode.office_location"),
    ({"tags": {"domain_fit": {"industry": "fintech", "requirement": "required",
                              "required_domain": "other", "evidence": []}}},
     "$.tags.domain_fit.domain"),
    ({"tags": {"domain_fit": {"industry": "fintech", "requirement": "none",
                              "required_domain": "fintech", "evidence": []}}},
     "$.tags.domain_fit.required_domain"),
    ({"latency_ms": True}, "$.latency_ms"),
    ({"latency_ms": -1}, "$.latency_ms"),
    ({"fit_score": "high"}, "$.fit_score"),
    ({"usage": [{"stage": 1, "model": "test"}]}, "$.usage[0].stage"),
    ({"usage": [{"stage": "tag"}]}, "$.usage[0].model"),
    ({"errors": ["message"]}, "$.errors[0]"),
])
def test_full_closed_prediction_schema_is_applied(fields, path):
    row = prediction(**copy.deepcopy(fields))
    with pytest.raises(ValueError) as error:
        check_prediction_rows([row])
    message = str(error.value)
    assert "row 1:" in message and path in message
    assert "private value" not in message


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
@pytest.mark.parametrize("field", ["fit_score", "usage", "errors"])
def test_non_finite_numbers_are_refused_at_any_json_depth(field, value):
    fields = {field: value if field == "fit_score" else
              [{"stage": "tag", "model": "test", "tokens": value}]}
    with pytest.raises(ValueError, match="finite JSON number") as error:
        check_prediction_rows([prediction(**fields)])
    assert field in str(error.value)


def test_finite_number_check_also_handles_float_subclasses():
    class JsonFloat(float):
        pass

    with pytest.raises(ValueError, match="finite JSON number"):
        check_prediction_rows([prediction(fit_score=JsonFloat("nan"))])


def test_valid_predictions_and_explicit_abstentions_are_preserved():
    row = prediction(tags={
        "domain_fit": {"industry": "fintech", "requirement": "preferred",
                       "required_domain": "other", "domain": "insurance",
                       "evidence": ["description_clean:000001"], "basis": "explicit"},
        "preferred_focus": {"values": [], "evidence": []},
    }, fit_score=3.5, usage=[{"stage": "tag", "model": "test", "input_tokens": 10}],
        relevance_by_policy={"llm": 2, "rules": 1})
    abstention = prediction(job_uid="missing", relevance=None, role_label=None,
                            gate_outcome=None, status="rules_only")
    assert check_prediction_rows([row, abstention]) == {"uid": row, "missing": abstention}
    assert check_prediction_rows([]) == {}


def test_duplicate_prediction_ids_are_refused_with_row_and_field():
    with pytest.raises(ValueError, match=r"row 2: \$\.job_uid must be unique"):
        check_prediction_rows([prediction(), prediction()])


def test_large_integer_scores_keep_exact_order_after_json_roundtrip():
    scores = {"huge": 10**400, "negative": -(10**400), "float": 3.5, "zero": 0}
    rows = json.loads(json.dumps([
        prediction(job_uid=uid, fit_score=score) for uid, score in scores.items()
    ]))
    predictions = check_prediction_rows(rows)
    assert queue_order(list(predictions.items())) == ["huge", "float", "zero", "negative"]
    assert {uid: row["fit_score"] for uid, row in predictions.items()} == scores
    cases = [{"job_uid": uid, "relevance": 2 if uid == "huge" else 0} for uid in scores]
    block = relevance_scale(cases, predictions)
    assert block["precision_at"]["5"]["numerator"] == 1
    assert block["precision_at"]["5"]["denominator"] == 4
    assert block["ndcg_at"]["5"]["value"] == 1


@pytest.mark.parametrize("role", ["analytics-leadership", "new-role", "other", None])
def test_prediction_role_membership_accepts_runtime_taxonomy_and_abstention(role):
    row = prediction(role_label=role)
    assert check_prediction_rows([row], profile_ids=["analytics-leadership", "new-role"]) == {
        "uid": row,
    }


def test_prediction_role_membership_diagnostic_does_not_echo_input():
    with pytest.raises(ValueError) as error:
        check_prediction_rows([prediction(role_label="unknown-private-role")], profile_ids=[])
    assert "row 1: $.role_label" in str(error.value)
    assert "unknown-private-role" not in str(error.value)


@pytest.mark.parametrize("row", [None, [], "record"])
def test_prediction_rows_must_be_objects(row):
    with pytest.raises(ValueError, match=r"row 1: \$ violates type"):
        check_prediction_rows([row])
