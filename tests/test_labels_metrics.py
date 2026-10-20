from __future__ import annotations

import copy

import pytest

from job_intake.annotation.accuracy import evaluate_vacancies
from job_intake.annotation.cohorts import digest
from job_intake.annotation.frozen_eval import (
    compare_runs,
    comparison_markdown,
    create_run,
    task_metrics,
    validate_run,
)
from job_intake.labeling.metrics import (
    compare_stability,
    quadratic_weighted_kappa,
    queue_order,
    relevance_scale,
    spearman,
)
from job_intake.labeling.schema import LABELS_SCHEMA


def case(uid, relevance, *, gate="unknown", tags=None, schema=LABELS_SCHEMA):
    return {"schema": schema, "job_uid": uid, "role_label": "analytics-leadership",
            "relevance": relevance, "gate_outcome": gate, "tags": tags or {}}


def predicted(relevance, **fields):
    return {"relevance": relevance, "tags": {}, **fields}


def test_qwk_is_one_on_agreement_known_on_a_hand_example_and_none_for_one_class():
    assert quadratic_weighted_kappa([(0, 0), (1, 1), (3, 3)]) == 1
    # Uniform marginals: observed weighted disagreement 2/9, expected 10/9.
    assert quadratic_weighted_kappa([(0, 0), (1, 1), (2, 3), (3, 2)]) == pytest.approx(0.8)
    assert quadratic_weighted_kappa([(2, 2), (2, 2)]) is None
    assert quadratic_weighted_kappa([(2, 0), (2, 3)]) is None
    assert quadratic_weighted_kappa([]) is None
    cases = [case("a", 2), case("b", 2)]
    block = relevance_scale(cases, {"a": predicted(2), "b": predicted(2)})
    assert block["qwk"]["value"] is None and "Undefined" in block["qwk"]["note"]


def test_spearman_handles_ties_and_no_variance():
    assert spearman([(0, 0), (1, 1), (2, 2)]) == pytest.approx(1)
    assert spearman([(0, 2), (1, 1), (2, 0)]) == pytest.approx(-1)
    assert spearman([(0, 1), (1, 1)]) is None


def test_missing_prediction_stays_in_the_denominator():
    cases = [case("a", 3), case("b", 2), case("c", 0), {"job_uid": "d", "relevance": None}]
    block = relevance_scale(cases, {"a": predicted(3), "c": predicted(0)})
    assert block["N"] == 3
    assert (block["coverage"]["numerator"], block["coverage"]["denominator"]) == (2, 3)
    assert (block["exact"]["numerator"], block["exact"]["denominator"]) == (2, 3)
    assert block["relevant"]["recall"]["numerator"] == 1
    assert block["relevant"]["recall"]["denominator"] == 2
    assert block["confusion"]["missing"] == {"0": 0, "1": 0, "2": 1, "3": 0}
    assert block["confusion"]["matrix"]["3"] == {"0": 0, "1": 0, "2": 0, "3": 1}
    assert block["mae"]["denominator"] == 2 and block["mae"]["value"] == 0


def test_recall_and_precision_use_predicted_relevance_not_tier():
    cases = [case("a", 3), case("b", 1), case("c", 2)]
    predictions = {"a": predicted(1, tier="A"), "b": predicted(2, tier="C"),
                   "c": predicted(3, tier="C")}
    block = relevance_scale(cases, predictions)
    assert (block["relevant"]["recall"]["numerator"],
            block["relevant"]["recall"]["denominator"]) == (1, 2)
    assert (block["relevant"]["precision"]["numerator"],
            block["relevant"]["precision"]["denominator"]) == (1, 2)
    assert block["within_one"]["numerator"] == 2


def test_queue_order_and_precision_at_k():
    rows = [
        ("z", predicted(2, gate_outcome="eligible")),
        ("y", predicted(3, gate_outcome="ineligible")),
        ("x", predicted(3, gate_outcome="eligible", confidence="low", fit_score=1)),
        ("w", predicted(3, gate_outcome="eligible", confidence="high", fit_score=1)),
        ("v", predicted(3, gate_outcome="eligible", confidence="high", fit_score=9)),
        ("u", predicted(3, gate_outcome="eligible", confidence="high", fit_score=9)),
        ("t", {"relevance": None}),
    ]
    assert queue_order(rows) == ["u", "v", "w", "x", "y", "z"]
    cases = [case(uid, relevance) for uid, relevance in
             (("u", 3), ("v", 0), ("w", 2), ("x", 1), ("y", 3), ("z", 2), ("t", 3))]
    block = relevance_scale(cases, dict(rows))
    at5 = block["precision_at"]["5"]
    assert (at5["numerator"], at5["denominator"], at5["status"]) == (3, 5, "measured")
    at10 = block["precision_at"]["10"]
    assert (at10["numerator"], at10["denominator"], at10["status"]) == (4, 6, "partial")
    assert block["ndcg_at"]["5"]["status"] == "partial"
    assert block["ndcg_at"]["5"]["random_baseline"] is not None
    assert relevance_scale(cases, dict(rows)) == block


def test_tags_use_only_human_labeled_v3_tags():
    gold_tags = {
        "function_core": {"value": "analytics_for_decisions", "quote": "x"},
        "domain_fit": {"industry": "fintech", "requirement": "none", "quote": ""},
        "preferred_focus": {"values": ["pricing_monetization", "experimentation_causal"],
                            "quote": ""},
    }
    cases = [case("a", 2, tags=gold_tags), case("b", 1),
             case("c", 1, schema="vacancy-tags-v2",
                  tags={"work_mode": {"value": "remote", "quote": ""}})]
    predictions = {
        "a": predicted(2, tags={
            "function_core": {"value": "analytics_reporting", "evidence": []},
            "domain_fit": {"industry": "fintech", "requirement": "none", "evidence": []},
            "preferred_focus": {"values": ["pricing_monetization"], "evidence": []},
        }),
        "b": predicted(1, tags={"function_core": {"value": "unspecified", "evidence": []}}),
        "c": predicted(1, tags={"work_mode": {"value": "remote", "evidence": []}}),
    }
    tags = relevance_scale(cases, predictions)["tags"]
    assert tags["function_core"]["N"] == 1 and tags["function_core"]["accuracy"]["value"] == 0
    assert tags["domain_fit.industry"]["accuracy"]["value"] == 1
    assert tags["work_mode"]["N"] == 0
    focus = tags["preferred_focus"]
    assert focus["N"] == 1 and focus["accuracy"]["value"] == 0
    assert (focus["micro_f1"]["numerator"], focus["micro_f1"]["denominator"]) == (2, 3)


def test_gate_metrics_reuse_blocker_recall_and_false_blocks():
    cases = [case("a", 2, gate="ineligible"), case("b", 2, gate="eligible"),
             case("c", 1, gate="unknown")]
    block = relevance_scale(cases, {"a": predicted(2, gate_outcome="unknown"),
                                    "b": predicted(2, gate_outcome="ineligible"),
                                    "c": predicted(1, gate_outcome="unknown")})
    assert block["gate"]["blocker_recall"]["value"] == 0
    assert block["gate"]["false_block_rate"]["value"] == 1
    assert (block["gate"]["unknown_rate"]["numerator"],
            block["gate"]["unknown_rate"]["denominator"]) == (2, 3)


def test_evaluate_vacancies_adds_relevance_scale_only_with_int_relevance():
    cases = [case("a", 3, gate="eligible"), case("b", 0, gate="eligible")]
    legacy = {"a": {"tier": "A", "fit_score": 9, "decision": "pass", "tags": {}},
              "b": {"tier": "C", "fit_score": 1, "decision": "review", "tags": {}}}
    before = evaluate_vacancies(cases, legacy)
    assert "relevance_scale" not in before
    with_relevance = {uid: {**row, "relevance": 3 if uid == "a" else 0}
                      for uid, row in legacy.items()}
    after = evaluate_vacancies(cases, with_relevance)
    assert after["relevance_scale"]["exact"]["value"] == 1
    assert {key: value for key, value in after.items() if key != "relevance_scale"} == before
    assert "relevance_scale" in task_metrics(cases, with_relevance)
    assert "relevance_scale" not in task_metrics(cases, legacy)


def frozen(n=4, *, minimum=160):
    cases = [{**case(f"u{i}", i % 4, gate="eligible"), "schema": "vacancy-tags-v2",
              "method": "blind", "source_hash": f"{i:064x}"} for i in range(n)]
    result = {
        "versions": {"baseline": "v1", "candidate": "v2"}, "cohort": None,
        "gate_policy": {"version": "audit-quality-gates-v2", "minimum_cases": minimum,
                        "minimum_relevant": 40, "minimum_full_tag_cases": 60,
                        "minimum_blind_cases": 30, "maximum_accuracy_regression": 0.0,
                        "maximum_false_block_increase": 0.0, "maximum_cost_ratio": 1.0,
                        "maximum_time_ratio": 1.0},
        "rows": [{"job_uid": row["job_uid"], "source_hash": row["source_hash"], "label": row}
                 for row in cases],
    }
    result["bundle_id"] = digest(result)
    return result


def outputs(bundle, *, relevance=True):
    return [{"job_uid": row["job_uid"], "source_hash": row["source_hash"], "tier": "B",
             "fit_score": 1.0, "decision": "review", "gate_outcome": "eligible",
             **({"relevance": row["label"]["relevance"]} if relevance else {})}
            for row in bundle["rows"]]


def test_old_runs_without_relevance_scale_stay_valid():
    bundle = frozen()
    run = create_run(bundle, predictions=outputs(bundle), variant="candidate", version="v2",
                     run_id="r1")
    assert run["evaluation"]["relevance_scale"]["qwk"]["value"] == 1
    validate_run(bundle, run)
    legacy = copy.deepcopy(run)
    legacy["evaluation"].pop("relevance_scale")
    legacy["run_hash"] = digest({k: v for k, v in legacy.items() if k != "run_hash"})
    validate_run(bundle, legacy)
    tampered = copy.deepcopy(run)
    tampered["evaluation"]["relevance_scale"]["qwk"]["value"] = 0.5
    tampered["run_hash"] = digest({k: v for k, v in tampered.items() if k != "run_hash"})
    with pytest.raises(ValueError, match="disagree"):
        validate_run(bundle, tampered)


def test_comparison_reports_relevance_scale_descriptively():
    bundle = frozen()
    baseline = create_run(bundle, predictions=outputs(bundle, relevance=False),
                          variant="baseline", version="v1", run_id="b")
    candidate = create_run(bundle, predictions=outputs(bundle), variant="candidate",
                           version="v2", run_id="c")
    result = compare_runs(bundle, baseline, candidate)
    assert set(result["relevance_scale"]) == {"candidate"}
    assert result["relevance_scale"]["candidate"]["qwk"] == 1
    assert all(gate["name"] != "relevance_scale" for gate in result["gates"])
    assert "Relevance scale 0–3 (descriptive, not a gate): candidate QWK 1" in (
        comparison_markdown(result))
    legacy = compare_runs(bundle, baseline, create_run(
        bundle, predictions=outputs(bundle, relevance=False), variant="candidate",
        version="v2", run_id="c2"))
    assert "relevance_scale" not in legacy
    assert "Relevance scale" not in comparison_markdown(legacy)


def tagged(relevance, function_core, focus=()):
    return {"relevance": relevance, "tags": {
        "function_core": {"value": function_core, "evidence": []},
        "domain_fit": {"industry": "fintech", "requirement": "none", "evidence": []},
        "preferred_focus": {"values": list(focus), "evidence": []},
    }}


def test_v3_stability_kappa_covers_relevance_and_each_tag():
    first = {"a": tagged(3, "analytics_for_decisions", ["pricing_monetization"]),
             "b": tagged(1, "analytics_reporting"), "c": tagged(0, "non_analytics")}
    same = compare_stability(first, first)
    assert same["fields"]["relevance"]["kappa"] == 1
    assert same["fields"]["function_core"]["kappa"] == 1
    assert same["fields"]["domain_fit.industry"]["kappa"] is None
    assert same["fields"]["work_mode"]["paired"] == 0
    second = {"a": tagged(2, "analytics_for_decisions", ["experimentation_causal"]),
              "b": tagged(1, "analytics_reporting")}
    changed = compare_stability(first, second)
    assert changed["fields"]["relevance"]["paired"] == 2
    assert changed["fields"]["relevance"]["missing"] == 1
    assert changed["fields"]["relevance"]["agreement"]["numerator"] == 1
    assert changed["fields"]["preferred_focus"]["agreement"]["numerator"] == 1
    assert changed["acceptance_passed"] is False


def test_repeat_run_stability_includes_relevance_for_labels_runs():
    bundle = frozen(minimum=4)
    baseline = create_run(bundle, predictions=outputs(bundle, relevance=False),
                          variant="baseline", version="v1", run_id="b")
    candidate = create_run(bundle, predictions=outputs(bundle), variant="candidate",
                           version="v2", run_id="c")
    repeat = create_run(bundle, predictions=outputs(bundle), variant="candidate",
                        version="v2", run_id="r")
    gate = next(row for row in compare_runs(bundle, baseline, candidate, repeat=repeat)["gates"]
                if row["name"] == "repeat_run_stability")
    assert gate["status"] == "pass"
    assert gate["measurement"]["labels_v3"]["fields"]["relevance"]["kappa"] == 1
    drifted = outputs(bundle)
    drifted[0]["relevance"] = (drifted[0]["relevance"] + 1) % 4
    repeat = create_run(bundle, predictions=drifted, variant="candidate", version="v2",
                        run_id="r2")
    gate = next(row for row in compare_runs(bundle, baseline, candidate, repeat=repeat)["gates"]
                if row["name"] == "repeat_run_stability")
    assert gate["status"] == "fail"
    legacy = create_run(bundle, predictions=outputs(bundle, relevance=False),
                        variant="candidate", version="v2", run_id="c3")
    legacy_repeat = create_run(bundle, predictions=outputs(bundle, relevance=False),
                               variant="candidate", version="v2", run_id="r3")
    gate = next(row for row in compare_runs(bundle, baseline, legacy,
                                            repeat=legacy_repeat)["gates"]
                if row["name"] == "repeat_run_stability")
    assert gate["status"] == "pass" and "labels_v3" not in gate["measurement"]
