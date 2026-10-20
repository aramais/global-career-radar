"""Frozen verdicts require each task's own human observations to be covered."""

from __future__ import annotations

import copy
import json

import pytest

from job_intake.annotation.cohorts import SCHEMA, digest
from job_intake.annotation.frozen_eval import (
    BUNDLE_SCHEMA,
    DEFAULT_POLICY,
    GATE_COVERAGE_VERSION,
    LEGACY_TASK_METRICS_VERSION,
    compare_runs,
    create_run,
    load_bundle,
    source_snapshot,
    task_metrics,
    validate_run,
)
from job_intake.annotation.gold import TAG_VALUES, validate_gold
from job_intake.annotation.text import TEXT_VERSION, source_hash
from job_intake.labeling.schema import LABELS_SCHEMA, PREDICTION_SCHEMA, validate_gold_row
from job_intake.models.job import JobRecord


def frozen(tmp_path, *, n=2, extra=None, default_policy=False):
    record = JobRecord(source="test", company="Synthetic", title="Analytics Manager",
                       original_url="https://example.test/job",
                       description_clean="Responsibilities:\nLead analytics decisions.")
    tags = {name: {"value": "unspecified", "quote": ""} for name in TAG_VALUES}
    tags.update(vacancy_closed={"value": "false", "quote": ""},
                isolated_analysis={"value": "not_indicated", "quote": ""},
                role_family={"value": "analytics", "quote": "Analytics Manager"})
    label = {"schema": "vacancy-tags-v2", "source_hash": source_hash(record),
             "text_version": TEXT_VERSION, "labeled_at": "2026-10-20", "method": "blind",
             "role_label": "analytics-leadership", "relevance": 3,
             "gate_outcome": "eligible", "tags": tags, "notes": ""}
    cases = [{**copy.deepcopy(label), "job_uid": f"full-{i}"} for i in range(n)]
    if extra is not None:
        cases.append({**copy.deepcopy(label), "job_uid": "partial", "tags": {}, **extra})
    validation = validate_gold([(case["job_uid"], record) for case in cases], cases)
    assert validation["valid"], validation["errors"]
    policy = copy.deepcopy(DEFAULT_POLICY)
    if not default_policy:
        policy.update(minimum_cases=1, minimum_relevant=1,
                      minimum_full_tag_cases=0, minimum_blind_cases=1)
    cohort = {
        "schema": SCHEMA, "cohort_id": "synthetic-independent", "dataset_role": "holdout",
        "rubric_version": "human-tags-v2", "created_at": "2026-10-20T12:00:00+00:00",
        "exposure_log": str(tmp_path / "unexposed.jsonl"),
        "provenance_review": {"reviewed_by": "synthetic", "reviewed_at": "2026-10-20",
                              "prior_exposures": [], "clean_history_confirmed": True},
        "members": [{"job_uid": case["job_uid"], "source_hash": case["source_hash"],
                     "duplicate_group": "synthetic-source", "label_method": "blind"}
                    for case in cases],
    }
    bundle = {"schema": BUNDLE_SCHEMA, "versions": {"baseline": "v1", "candidate": "v2"},
              "cohort": cohort, "gate_policy": policy,
              "rows": [{"job_uid": case["job_uid"], "source_hash": case["source_hash"],
                        "source": source_snapshot(record), "label": case} for case in cases]}
    bundle["bundle_id"] = digest(bundle)
    return bundle


def outputs(bundle):
    result = []
    for row in bundle["rows"]:
        case = row["label"]
        ranked = type(case["relevance"]) is int
        result.append({"job_uid": row["job_uid"], "source_hash": row["source_hash"],
                       "role_label": case["role_label"], "gate_outcome": case["gate_outcome"],
                       "decision": "pass" if ranked else None, "tier": "A" if ranked else None,
                       "fit_score": 20 if ranked else None, "tags": copy.deepcopy(case["tags"])})
    return result


def runs(bundle, mutate=None):
    result = []
    for variant, name in (("baseline", "baseline"), ("candidate", "candidate"),
                          ("candidate", "repeat")):
        predictions = outputs(bundle)
        if mutate:
            mutate(name, predictions)
        run = create_run(bundle, predictions=predictions, variant=variant,
                         version=bundle["versions"][variant], run_id=name,
                         operational={"complete": True, "prices_fresh": True,
                                      "uncertain_bills": 0, "cost_usd": 0.1, "wall_seconds": 2})
        result.append(json.loads(json.dumps(run)))
    return result


def comparison(bundle, recorded):
    return compare_runs(bundle, recorded[0], recorded[1], repeat=recorded[2])


def gate(report, name):
    return next(row for row in report["gates"] if row["name"] == name)


def test_default_policy_audit_160_plus_partial_cannot_pass(tmp_path):
    bundle = frozen(tmp_path, n=160, extra={"role_label": None}, default_policy=True)
    assert bundle["gate_policy"] == DEFAULT_POLICY
    path = tmp_path / "bundle.json"
    path.write_text(json.dumps(bundle), encoding="utf-8")
    bundle = load_bundle(path)

    def abstain(_, predictions):
        predictions[-1].update(schema=PREDICTION_SCHEMA, labeler="rules:test",
                               variant="experiment", status="rules_only", gate_reasons=[],
                               relevance=None, decision=None, tier=None, fit_score=None)

    recorded = runs(bundle, abstain)
    original = copy.deepcopy(recorded)
    for run in recorded:
        validate_run(bundle, run)
        evaluation = run["evaluation"]
        assert evaluation["task_prediction_coverage"]["ranking"]["numerator"] == 160
        assert evaluation["task_prediction_coverage"]["ranking"]["denominator"] == 160
        assert evaluation["graded_ranking"]["ndcg@5"]["status"] == "partial"
        assert evaluation["independent_axes"]["actionable_p1_recall"]["denominator"] == 161
    report = comparison(bundle, recorded)
    assert report["gate_coverage_version"] == GATE_COVERAGE_VERSION
    assert report["independent_holdout"] is True
    assert report["status"] == "insufficient_evidence"
    for name in ("ndcg@5", "ndcg@10", "actionable_p1_recall", "repeat_run_stability"):
        assert gate(report, name)["status"] == "insufficient_evidence"
    for name in ("human_decisions", "human_relevant", "human_full_tags", "human_blind_decisions"):
        assert gate(report, name)["status"] == "pass"
    coverage = gate(report, "ndcg@5")["prediction_coverage"]["candidate"]
    assert (coverage["numerator"], coverage["denominator"]) == (160, 161)
    assert recorded == original


def test_complete_default_policy_still_passes(tmp_path):
    bundle = frozen(tmp_path, n=160, default_policy=True)
    assert comparison(bundle, runs(bundle))["status"] == "pass"


@pytest.mark.parametrize("slot", ["baseline", "candidate", "repeat"])
@pytest.mark.parametrize("field", ["gate_outcome", "decision", "tier"])
@pytest.mark.parametrize("value", [None, "bogus"])
def test_partial_actionable_requires_gate_decision_and_tier(tmp_path, slot, field, value):
    bundle = frozen(tmp_path, extra={"role_label": None})

    def missing(name, predictions):
        if name == slot:
            predictions[-1][field] = value

    report = comparison(bundle, runs(bundle, missing))
    name = "repeat_run_stability" if slot == "repeat" else "actionable_p1_recall"
    assert gate(report, name)["status"] == "insufficient_evidence"


@pytest.mark.parametrize("slot", ["baseline", "candidate", "repeat"])
@pytest.mark.parametrize(("field", "value"), [
    ("tier", None), ("tier", "bogus"), ("fit_score", None), ("fit_score", True),
])
def test_ranking_uses_partial_human_sample_and_requires_finite_score(tmp_path, slot, field, value):
    bundle = frozen(tmp_path, extra={"role_label": None, "gate_outcome": None})

    def missing(name, predictions):
        if name == slot:
            predictions[-1][field] = value

    report = comparison(bundle, runs(bundle, missing))
    name = "repeat_run_stability" if slot == "repeat" else "ndcg@5"
    assert gate(report, name)["status"] == "insufficient_evidence"


def test_actionable_does_not_require_score_and_other_is_not_ranked(tmp_path):
    bundle = frozen(tmp_path, extra={"role_label": "other"})

    def unscored(_, predictions):
        predictions[-1]["fit_score"] = None

    report = comparison(bundle, runs(bundle, unscored))
    assert gate(report, "actionable_p1_recall")["status"] == "pass"
    assert gate(report, "ndcg@5")["prediction_coverage"]["candidate"]["denominator"] == 2
    assert report["status"] == "pass"


@pytest.mark.parametrize("human_gate", ["eligible", "ineligible"])
def test_gate_only_human_observations_need_no_decision_or_score(tmp_path, human_gate):
    bundle = frozen(tmp_path, extra={"role_label": None, "relevance": None,
                                     "gate_outcome": human_gate})
    report = comparison(bundle, runs(bundle))
    name = "blocker_recall" if human_gate == "ineligible" else "false_block_rate"
    assert gate(report, name)["status"] == "pass"
    assert report["status"] == "pass"


@pytest.mark.parametrize("human_gate", ["eligible", "ineligible"])
@pytest.mark.parametrize("slot", ["baseline", "candidate", "repeat"])
@pytest.mark.parametrize("value", [None, "bogus"])
def test_gate_only_abstention_is_missing_on_its_own_sample(tmp_path, human_gate, slot, value):
    bundle = frozen(tmp_path, extra={"role_label": None, "relevance": None,
                                     "gate_outcome": human_gate})

    def missing(name, predictions):
        if name == slot:
            predictions[-1]["gate_outcome"] = value

    report = comparison(bundle, runs(bundle, missing))
    name = "blocker_recall" if human_gate == "ineligible" else "false_block_rate"
    if slot == "repeat":
        name = "repeat_run_stability"
    assert gate(report, name)["status"] == "insufficient_evidence"


@pytest.mark.parametrize("value", [None, "", "  "])
def test_role_only_missing_prediction_does_not_count_as_covered(tmp_path, value):
    bundle = frozen(tmp_path, extra={"relevance": None, "gate_outcome": None})

    def missing(_, predictions):
        predictions[-1]["role_label"] = value

    report = comparison(bundle, runs(bundle, missing))
    assert gate(report, "profession_accuracy")["status"] == "insufficient_evidence"
    assert gate(report, "repeat_run_stability")["status"] == "insufficient_evidence"


def test_role_only_and_relevance_only_complete_observations_are_sufficient(tmp_path):
    for extra in ({"relevance": None, "gate_outcome": None},
                  {"role_label": None, "gate_outcome": None}):
        bundle = frozen(tmp_path, extra=extra)
        assert comparison(bundle, runs(bundle))["status"] == "pass"


@pytest.mark.parametrize("name", ["project_management", "people_interaction", "decision_influence",
                                   "work_mode"])
@pytest.mark.parametrize("value", [None, "bogus", ""])
def test_tag_only_missing_or_invalid_output_is_not_evidence(tmp_path, name, value):
    human_value = "onsite" if name == "work_mode" else "unspecified"
    bundle = frozen(tmp_path, extra={"role_label": None, "relevance": None, "gate_outcome": None,
                                     "tags": {name: {"value": human_value,
                                                     "quote": "Analytics Manager"}}})

    def missing(_, predictions):
        predictions[-1]["tags"][name]["value"] = value

    report = comparison(bundle, runs(bundle, missing))
    metric = "negative_restriction_recall" if name == "work_mode" else "scope." + name
    assert gate(report, metric)["status"] == "insufficient_evidence"
    assert gate(report, "repeat_run_stability")["status"] == "insufficient_evidence"


def test_negative_recall_needs_only_the_human_negative_observations(tmp_path):
    bundle = frozen(tmp_path, extra={"role_label": None, "relevance": None, "gate_outcome": None,
                                     "tags": {"work_mode": {"value": "onsite",
                                                            "quote": "Analytics Manager"}}})

    def negative_only(_, predictions):
        for row in predictions[:-1]:
            row["tags"].pop("working_language")

    report = comparison(bundle, runs(bundle, negative_only))
    assert gate(report, "negative_restriction_recall")["status"] == "pass"
    # Other annotated tags are still required to establish repeatability.
    assert gate(report, "repeat_run_stability")["status"] == "insufficient_evidence"


@pytest.mark.parametrize("slot", ["baseline", "candidate", "repeat"])
def test_completely_missing_partial_prediction_is_insufficient(tmp_path, slot):
    bundle = frozen(tmp_path, extra={"role_label": None})

    def missing(name, predictions):
        if name == slot:
            predictions.pop()

    report = comparison(bundle, runs(bundle, missing))
    name = "repeat_run_stability" if slot == "repeat" else "ndcg@5"
    assert gate(report, name)["status"] == "insufficient_evidence"


def test_v3_repeat_accepts_complete_negative_domain_and_empty_focus(tmp_path):
    bundle = frozen(tmp_path)
    for row in bundle["rows"]:
        row["label"].update(schema=LABELS_SCHEMA, labeler="human", rubric_version="test",
                            first_labeled_at="2026-10-20", gate_reasons=[], tags={
                                "domain_fit": {"industry": "fintech", "requirement": "required",
                                               "required_domain": "fintech",
                                               "quote": "Lead analytics decisions."},
                                "preferred_focus": {"values": ["pricing_monetization"],
                                                    "quote": ""}})
        assert not validate_gold_row(row["label"])
    bundle["bundle_id"] = digest({k: v for k, v in bundle.items() if k != "bundle_id"})

    def negative(_, predictions):
        for row in predictions:
            row.update(schema=PREDICTION_SCHEMA, labeler="rules:test", variant="experiment",
                       status="rules_only", relevance=3, gate_reasons=[], tags={
                           "domain_fit": {"industry": "fintech", "requirement": "none",
                                          "required_domain": None, "evidence": []},
                           "preferred_focus": {"values": [], "evidence": []}})

    report = comparison(bundle, runs(bundle, negative))
    assert gate(report, "repeat_run_stability")["status"] == "pass"


@pytest.mark.parametrize("explicit_marker", [False, True])
def test_historical_task_metrics_and_run_bytes_remain_immutable(tmp_path, explicit_marker):
    bundle = frozen(tmp_path, extra={"role_label": None})
    recorded = runs(bundle)
    for run in recorded:
        run["evaluation"] = task_metrics([row["label"] for row in bundle["rows"]],
                                         run["predictions"], version=LEGACY_TASK_METRICS_VERSION)
        if not explicit_marker:
            run["evaluation"].pop("task_metrics_version")
        run["run_hash"] = digest({k: v for k, v in run.items() if k != "run_hash"})
    before = json.dumps(recorded, sort_keys=True)
    for run in recorded:
        validate_run(bundle, run)
    report = comparison(bundle, recorded)
    assert report["task_metrics_version"] == LEGACY_TASK_METRICS_VERSION
    assert report["gate_coverage_version"] == GATE_COVERAGE_VERSION
    assert gate(report, "role_recall")["status"] == "insufficient_evidence"
    assert json.dumps(recorded, sort_keys=True) == before
