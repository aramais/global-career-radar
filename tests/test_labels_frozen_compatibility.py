"""New labels runs use corrected metrics; immutable old runs retain their version."""

from __future__ import annotations

import copy
import json

import pytest

from job_intake.annotation.cohorts import digest
from job_intake.annotation.frozen_eval import DEFAULT_POLICY, compare_runs, create_run, validate_run
from job_intake.annotation.gold import GOLD_SCHEMA
from job_intake.labeling.metrics import LEGACY_METRICS_VERSION, METRICS_VERSION, relevance_scale
from job_intake.labeling.schema import LABELS_SCHEMA, PREDICTION_SCHEMA


def bundle_and_prediction():
    cases = [
        {"schema": LABELS_SCHEMA, "job_uid": uid, "role_label": "analytics-leadership",
         "relevance": relevance, "gate_outcome": "unknown", "tags": {}}
        for uid, relevance in (("a", 3), ("b", 2), ("c", 0))
    ]
    bundle = {
        "bundle_id": "frozen-test", "versions": {"labels": "test"},
        "rows": [{"job_uid": case["job_uid"], "source_hash": "0" * 64, "label": case}
                 for case in cases],
    }
    prediction = {
        "schema": PREDICTION_SCHEMA, "job_uid": "a", "source_hash": "0" * 64,
        "labeler": "rules:test", "variant": "labels", "status": "rules_only",
        "role_label": "analytics-leadership", "relevance": 3, "gate_outcome": "unknown",
        "gate_reasons": [], "tags": {},
    }
    return bundle, prediction


def test_new_frozen_run_uses_corrected_precision_denominator():
    bundle, prediction = bundle_and_prediction()
    run = create_run(bundle, predictions=[prediction], variant="labels", version="test",
                     run_id="new")
    scale = run["evaluation"]["relevance_scale"]
    assert scale["version"] == METRICS_VERSION
    assert scale["precision_at"]["5"]["denominator"] == 3
    assert scale["precision_at"]["5"]["value"] == pytest.approx(1 / 3, abs=1e-6)
    validate_run(bundle, run)


def test_frozen_v1_metrics_are_recomputed_without_rewriting_the_run():
    bundle, prediction = bundle_and_prediction()
    run = create_run(bundle, predictions=[prediction], variant="labels", version="test",
                     run_id="historical")
    cases = [row["label"] for row in bundle["rows"]]
    old_scale = relevance_scale(cases, run["predictions"], version=LEGACY_METRICS_VERSION)
    assert old_scale["precision_at"]["5"]["value"] == 1
    run["evaluation"]["relevance_scale"] = old_scale
    run["run_hash"] = digest({key: value for key, value in run.items() if key != "run_hash"})
    before = copy.deepcopy(run)
    validate_run(bundle, run)
    assert run == before


def test_frozen_v1_metrics_do_not_bypass_v3_prediction_validation():
    bundle, prediction = bundle_and_prediction()
    run = create_run(bundle, predictions=[prediction], variant="labels", version="test",
                     run_id="v1-metrics")
    run["evaluation"]["relevance_scale"] = relevance_scale(
        [row["label"] for row in bundle["rows"]], run["predictions"],
        version=LEGACY_METRICS_VERSION,
    )
    run["predictions"]["a"]["confidence"] = {}
    run["run_hash"] = digest({key: value for key, value in run.items() if key != "run_hash"})
    with pytest.raises(ValueError, match="confidence"):
        validate_run(bundle, run)


def test_frozen_unknown_metrics_version_is_rejected():
    bundle, prediction = bundle_and_prediction()
    run = create_run(bundle, predictions=[prediction], variant="labels", version="test",
                     run_id="unsupported")
    run["evaluation"]["relevance_scale"]["version"] = "relevance-scale-unknown"
    run["run_hash"] = digest({key: value for key, value in run.items() if key != "run_hash"})
    with pytest.raises(ValueError, match="version"):
        validate_run(bundle, run)


def test_frozen_creation_rejects_malformed_v3_predictions():
    bundle, prediction = bundle_and_prediction()
    prediction["tags"] = {"preferred_focus": {}}
    with pytest.raises(ValueError, match="preferred_focus"):
        create_run(bundle, predictions=[prediction], variant="labels", version="test",
                   run_id="invalid")


@pytest.mark.parametrize("schema", [None, "vacancy-labels-v3-prediction-typo"])
def test_frozen_labels_cannot_bypass_validation_with_a_bad_discriminator(schema):
    bundle, prediction = bundle_and_prediction()
    if schema is None:
        prediction.pop("schema")
    else:
        prediction["schema"] = schema
    prediction["confidence"] = {}
    with pytest.raises(ValueError, match="Invalid v3 prediction"):
        create_run(bundle, predictions=[prediction], variant="labels", version="test",
                   run_id="invalid-schema")


def test_frozen_v2_validation_rejects_malformed_predictions_before_metrics():
    bundle, prediction = bundle_and_prediction()
    run = create_run(bundle, predictions=[prediction], variant="labels", version="test",
                     run_id="imported")
    run["predictions"]["a"]["confidence"] = {}
    run["run_hash"] = digest({key: value for key, value in run.items() if key != "run_hash"})
    with pytest.raises(ValueError, match="confidence"):
        validate_run(bundle, run)


@pytest.mark.parametrize("include_prediction", [False, True])
def test_frozen_partial_labels_keep_tag_and_gate_metrics_without_ordinal_predictions(
    include_prediction,
):
    bundle, prediction = bundle_and_prediction()
    label = bundle["rows"][0]["label"]
    label.update(relevance=None, gate_outcome="ineligible",
                 tags={"work_mode": {"value": "remote", "quote": ""}})
    bundle["rows"] = bundle["rows"][:1]
    prediction.update(relevance=None, gate_outcome="unknown",
                      tags={"work_mode": {"value": "office_sao_paulo", "evidence": []}})
    run = create_run(bundle, predictions=[prediction] if include_prediction else [],
                     variant="labels", version="test", run_id="partial")
    scale = run["evaluation"]["relevance_scale"]
    assert scale["N"] == 0
    assert scale["tags"]["work_mode"]["N"] == 1
    assert scale["tags"]["work_mode"]["accuracy"]["numerator"] == 0
    assert scale["gate"]["N"] == 1
    assert scale["gate"]["blocker_recall"]["denominator"] == 1
    validate_run(bundle, run)


@pytest.mark.parametrize(
    "discriminator", ["reserved_variant", "custom_variant", "labeler", "bundle"],
)
def test_custom_frozen_variants_require_schema_for_v3_outputs(discriminator):
    bundle, prediction = bundle_and_prediction()
    bundle["versions"] = {"candidate": "test"}
    for row in bundle["rows"]:
        row["label"]["schema"] = LABELS_SCHEMA if discriminator == "bundle" else GOLD_SCHEMA
    prediction.pop("schema")
    prediction.pop("labeler")
    prediction.pop("variant")
    if discriminator in {"reserved_variant", "custom_variant"}:
        prediction["variant"] = "labels" if discriminator == "reserved_variant" else "experiment"
    elif discriminator == "labeler":
        prediction["labeler"] = "llm:experiment"
    prediction["confidence"] = {}
    with pytest.raises(ValueError, match="Invalid v3 prediction"):
        create_run(bundle, predictions=[prediction], variant="candidate", version="test",
                   run_id="missing-schema")


def test_custom_frozen_validation_checks_row_variant_before_metrics():
    bundle, prediction = bundle_and_prediction()
    bundle["versions"] = {"candidate": "test"}
    for row in bundle["rows"]:
        row["label"]["schema"] = GOLD_SCHEMA
    run = create_run(bundle, predictions=[prediction], variant="candidate", version="test",
                     run_id="imported")
    supplied = run["predictions"]["a"]
    supplied.pop("schema")
    supplied.pop("labeler")
    supplied["confidence"] = {}
    run["run_hash"] = digest({key: value for key, value in run.items() if key != "run_hash"})
    with pytest.raises(ValueError, match="Invalid v3 prediction"):
        validate_run(bundle, run)


def test_custom_frozen_variant_rejects_implied_restrictive_fact():
    bundle, prediction = bundle_and_prediction()
    bundle["versions"] = {"candidate": "test"}
    prediction["variant"] = "experiment"
    prediction["tags"] = {"hiring_eligibility": {
        "value": "restricted_elsewhere", "basis": "implied", "evidence": [],
    }}
    with pytest.raises(ValueError, match="basis"):
        create_run(bundle, predictions=[prediction], variant="candidate", version="test",
                   run_id="implied")


def test_legacy_frozen_predictions_without_v3_markers_remain_valid():
    bundle, prediction = bundle_and_prediction()
    bundle["versions"] = {"candidate": "test"}
    for row in bundle["rows"]:
        row["label"]["schema"] = GOLD_SCHEMA
    for field in ("schema", "labeler", "variant", "status", "relevance"):
        prediction.pop(field)
    run = create_run(bundle, predictions=[prediction], variant="candidate", version="test",
                     run_id="legacy")
    assert "relevance_scale" not in run["evaluation"]
    validate_run(bundle, run)


@pytest.mark.parametrize("v3_bundle", [False, True])
def test_tag_only_frozen_repeat_reports_labels_stability(v3_bundle):
    bundle, prediction = bundle_and_prediction()
    bundle.update(versions={"baseline": "test", "candidate": "test"},
                  gate_policy=copy.deepcopy(DEFAULT_POLICY), cohort=None)
    bundle["rows"] = bundle["rows"][:2]
    predictions = []
    for row, function in zip(
        bundle["rows"], ("analytics_reporting", "analytics_for_decisions"), strict=True,
    ):
        row["label"].update(
            schema=LABELS_SCHEMA if v3_bundle else GOLD_SCHEMA, relevance=None,
            tags={"function_core": {"value": function, "quote": ""}},
        )
        predictions.append({
            **prediction, "job_uid": row["job_uid"], "variant": "custom-experiment",
            "relevance": None,
            "tags": {"function_core": {"value": function, "evidence": []}},
        })
    runs = [
        create_run(bundle, predictions=predictions, variant=variant, version="test", run_id=name)
        for variant, name in (("baseline", "baseline"), ("candidate", "first"),
                              ("candidate", "repeat"))
    ]
    report = compare_runs(bundle, runs[0], runs[1], repeat=runs[2])
    assert runs[1]["evaluation"]["relevance_scale"]["N"] == 0
    measure = report["stability"]["labels_v3"]["fields"]["function_core"]
    assert measure["paired"] == 2
    assert measure["kappa"] == 1


@pytest.mark.parametrize("score", [10**400, -(10**400)])
def test_frozen_ranking_handles_large_finite_integer_scores(score):
    bundle, prediction = bundle_and_prediction()
    prediction.update(fit_score=score, tier="A", decision="pass")
    prediction = json.loads(json.dumps(prediction))
    run = create_run(bundle, predictions=[prediction], variant="labels", version="test",
                     run_id="large-score")
    assert run["evaluation"]["graded_ranking"]["ndcg@5"]["prediction_n"] == 1
    assert run["predictions"]["a"]["fit_score"] == score
    validate_run(bundle, run)
