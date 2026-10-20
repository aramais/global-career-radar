"""Frozen profession recall uses human role labels and versioned historical replay."""

from __future__ import annotations

import copy

import pytest

from job_intake.annotation.cohorts import digest, read_object, write_new
from job_intake.annotation.frozen_eval import (
    BUNDLE_SCHEMA,
    DEFAULT_POLICY,
    LEGACY_TASK_METRICS_VERSION,
    TASK_METRICS_VERSION,
    compare_runs,
    create_run,
    load_bundle,
    source_snapshot,
    task_metrics,
    validate_run,
)
from job_intake.annotation.text import TEXT_VERSION, source_hash
from job_intake.labeling.schema import LABELS_SCHEMA, PREDICTION_SCHEMA, validate_gold_row
from job_intake.models.job import JobRecord


def gold(uid="partial", *, role=None, relevance=3):
    return {
        "schema": LABELS_SCHEMA, "job_uid": uid, "source_hash": "0" * 64,
        "text_version": TEXT_VERSION, "labeler": "human", "method": "blind",
        "rubric_version": "test", "first_labeled_at": "2026-10-20",
        "labeled_at": "2026-10-20", "role_label": role, "relevance": relevance,
        "gate_outcome": "unknown", "gate_reasons": [], "tags": {}, "notes": "",
    }


def frozen(cases):
    record = JobRecord(source="test", company="Example", title="Analytics Manager",
                       original_url="https://example.test/jobs/1")
    rows = []
    for case in cases:
        case = {**case, "source_hash": source_hash(record)}
        assert not validate_gold_row(case)
        rows.append({"job_uid": case["job_uid"], "source_hash": case["source_hash"],
                     "source": source_snapshot(record), "label": case})
    bundle = {
        "schema": BUNDLE_SCHEMA, "rows": rows,
        "versions": {"baseline": "before", "candidate": "after"}, "cohort": None,
        "gate_policy": {**DEFAULT_POLICY, "minimum_cases": 1, "minimum_relevant": 0,
                        "minimum_full_tag_cases": 0, "minimum_blind_cases": 0},
    }
    return {**bundle, "bundle_id": digest(bundle)}


def run_for(bundle, *, variant="baseline", run_id="first", predictions=None):
    return create_run(bundle, predictions=[] if predictions is None else predictions,
                      variant=variant, version=bundle["versions"][variant], run_id=run_id)


def rehash(run):
    run["run_hash"] = digest({key: value for key, value in run.items() if key != "run_hash"})


@pytest.mark.parametrize("predictions", [
    {}, {"partial": {}}, {"partial": {"role_label": None}},
    {"partial": {"role_label": "analytics-leadership"}},
    {"partial": {"role_label": "other"}},
])
def test_valid_partial_gold_has_no_role_recall_target(predictions):
    case = gold()
    assert validate_gold_row(case) == []
    result = task_metrics([case], predictions)
    assert result["task_metrics_version"] == TASK_METRICS_VERSION
    assert result["independent_axes"]["role_recall"] == {
        "value": None, "numerator": 0, "denominator": 0, "ci95": None,
    }
    assert result["role_classification"]["accuracy"]["denominator"] == 0
    assert result["relevance_scale"]["relevant"]["recall"]["denominator"] == 1


@pytest.mark.parametrize(("predictions", "matches"), [
    ({}, 0), ({"known": {}}, 0), ({"known": {"role_label": None}}, 0),
    ({"known": {"role_label": "other"}}, 0),
    ({"known": {"role_label": "analytics-leadership"}}, 1),
])
def test_missing_or_abstaining_predictions_remain_in_known_role_denominator(predictions, matches):
    case = gold("known", role="analytics-leadership", relevance=2)
    assert validate_gold_row(case) == []
    metric = task_metrics([case], predictions)["independent_axes"]["role_recall"]
    assert metric["numerator"] == matches
    assert metric["denominator"] == 1
    assert metric["value"] == matches


def test_role_recall_uses_its_own_human_sample_and_counts_other_as_known():
    cases = [
        gold("match", role="analytics-leadership"),
        gold("other", role="other", relevance=2),
        gold("wrong", role="business-analytics"),
        gold("abstained", role="analytics-leadership"),
        gold("empty", role="analytics-leadership"),
        gold("unknown"), gold("missing-role"),
        gold("low", role="analytics-leadership", relevance=1),
        gold("no-relevance", role="analytics-leadership", relevance=None),
        gold("empty-role", role=""), gold("whitespace-role", role="  "),
    ]
    cases[6].pop("role_label")
    predictions = {
        "match": {"role_label": "analytics-leadership"},
        "wrong": {"role_label": "other"}, "abstained": {"role_label": None},
        "empty": {}, "unknown": {"role_label": "analytics-leadership"},
        "low": {"role_label": "analytics-leadership"},
    }
    metric = task_metrics(cases, predictions)["independent_axes"]["role_recall"]
    assert (metric["numerator"], metric["denominator"], metric["value"]) == (1, 5, 0.2)


def test_create_validate_compare_json_roundtrip_keeps_corrected_sample(tmp_path):
    bundle = frozen([gold(), gold("known", role="analytics-leadership")])
    row = bundle["rows"][1]
    prediction = {
        "schema": PREDICTION_SCHEMA, "job_uid": row["job_uid"],
        "source_hash": row["source_hash"], "labeler": "rules:test", "variant": "experiment",
        "status": "rules_only", "role_label": "analytics-leadership", "relevance": 3,
        "gate_outcome": "unknown", "gate_reasons": [], "tags": {},
    }
    write_new(tmp_path / "bundle.json", bundle)
    bundle = load_bundle(tmp_path / "bundle.json")
    runs = []
    for variant, run_id in (("baseline", "before"), ("candidate", "after"),
                            ("candidate", "repeat")):
        run = run_for(bundle, variant=variant, run_id=run_id, predictions=[prediction])
        path = write_new(tmp_path / f"{run_id}.json", run)
        runs.append(read_object(path))
    before = copy.deepcopy(runs)
    for run in runs:
        validate_run(bundle, run)
        metric = run["evaluation"]["independent_axes"]["role_recall"]
        assert (metric["numerator"], metric["denominator"], metric["value"]) == (1, 1, 1)
    report = compare_runs(bundle, runs[0], runs[1], repeat=runs[2])
    assert report["task_metrics_version"] == TASK_METRICS_VERSION
    gate = next(row for row in report["gates"] if row["name"] == "role_recall")
    assert gate["N"] == 1
    assert gate["candidate"]["value"] == 1
    assert runs == before


def legacy_recorded_run(bundle, *, variant="baseline", run_id="legacy", explicit=False):
    # A separately specified historical metric, not the legacy implementation under test.
    # The partial row's missing role matched its missing prediction; the known role did not.
    run = run_for(bundle, variant=variant, run_id=run_id)
    run["evaluation"]["independent_axes"]["role_recall"] = {
        "value": 0.5, "numerator": 1, "denominator": 2, "ci95": [0.094531, 0.905469],
    }
    if explicit:
        run["evaluation"]["task_metrics_version"] = LEGACY_TASK_METRICS_VERSION
    else:
        run["evaluation"].pop("task_metrics_version")
    rehash(run)
    return run


@pytest.mark.parametrize("explicit", [False, True])
def test_independent_synthetic_legacy_metric_replays_without_changing_bytes(tmp_path, explicit):
    bundle = frozen([gold(), gold("known", role="analytics-leadership")])
    run = legacy_recorded_run(bundle, explicit=explicit)
    path = write_new(tmp_path / "legacy-run.json", run)
    before = path.read_bytes()
    recorded = read_object(path)
    validate_run(bundle, recorded)
    assert path.read_bytes() == before
    assert recorded == run
    corrected = task_metrics([row["label"] for row in bundle["rows"]], {})
    assert corrected["independent_axes"]["role_recall"]["denominator"] == 1
    assert corrected["independent_axes"]["role_recall"]["value"] == 0
    replay = task_metrics([row["label"] for row in bundle["rows"]], {},
                          version=LEGACY_TASK_METRICS_VERSION)
    assert replay["independent_axes"]["role_recall"] == run["evaluation"]["independent_axes"][
        "role_recall"
    ]


def test_legacy_comparison_retains_recorded_formula():
    bundle = frozen([gold(), gold("known", role="analytics-leadership")])
    baseline = legacy_recorded_run(bundle, run_id="before")
    candidate = legacy_recorded_run(bundle, variant="candidate", run_id="after", explicit=True)
    repeat = legacy_recorded_run(bundle, variant="candidate", run_id="repeat")
    before = copy.deepcopy((baseline, candidate, repeat))
    report = compare_runs(bundle, baseline, candidate, repeat=repeat)
    assert report["task_metrics_version"] == LEGACY_TASK_METRICS_VERSION
    gate = next(row for row in report["gates"] if row["name"] == "role_recall")
    assert gate["N"] == 2
    assert gate["baseline"]["value"] == gate["candidate"]["value"] == 0.5
    assert (baseline, candidate, repeat) == before


@pytest.mark.parametrize("legacy_slot", ["baseline", "candidate", "repeat"])
def test_comparison_rejects_mixed_task_metrics_versions(legacy_slot):
    bundle = frozen([gold(), gold("known", role="analytics-leadership")])
    runs = {
        name: run_for(bundle, variant="baseline" if name == "baseline" else "candidate",
                      run_id=name)
        for name in ("baseline", "candidate", "repeat")
    }
    runs[legacy_slot] = legacy_recorded_run(
        bundle, variant="baseline" if legacy_slot == "baseline" else "candidate",
        run_id=legacy_slot,
    )
    with pytest.raises(ValueError, match="different frozen task metrics versions"):
        compare_runs(bundle, runs["baseline"], runs["candidate"], repeat=runs["repeat"])


@pytest.mark.parametrize("version", ["frozen-task-metrics-v999", None, 2, {}])
def test_unknown_task_metrics_versions_are_rejected(version):
    bundle = frozen([gold()])
    run = run_for(bundle)
    run["evaluation"]["task_metrics_version"] = version
    rehash(run)
    with pytest.raises(ValueError, match="Unsupported frozen task metrics version"):
        validate_run(bundle, run)
    with pytest.raises(ValueError, match="Unsupported frozen task metrics version"):
        task_metrics([gold()], {}, version=version)
