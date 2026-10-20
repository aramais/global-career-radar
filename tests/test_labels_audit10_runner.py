"""Audit-10 export regressions use the same accepted hiring gate as intake."""

from __future__ import annotations

import copy

import pytest
from test_labels_audit10_pipeline import build as build
from test_labels_runner_review import fake_tagger, settings
from test_pipeline_profiles import _record

from job_intake.annotation.text import source_hash
from job_intake.labeling.runner import label_records
from job_intake.labeling.schema import LabelResult


@pytest.mark.parametrize("mode", ["labels", "shadow"])
@pytest.mark.parametrize("tier_source", ["score", "relevance"])
def test_export_matches_real_intake_gate_and_tier_from_validated_cached_labels(
    build, mode, tier_source,
):
    pipeline, record, items, _ = build(mode=mode, tier_source=tier_source)
    labels = record.annotation["labels_v3"]
    assert labels["gate_outcome"] == "ineligible" and labels["status"] == "labeled"
    # The fixture populated the real cache with agreeing tag/escalation replies.
    # Export reuses those validated facts without a client or a network request.
    predictions, stopped = label_records(
        pipeline.config, [("case", copy.deepcopy(record))], use_ai=False,
    )
    assert not stopped
    prediction = predictions[0]
    assert prediction["status"] == "labeled" and prediction["gate_outcome"] == "ineligible"
    assert prediction["decision"] == str(items[0].evaluation.decision)
    assert prediction["tier"] == str(items[0].evaluation.tier)
    assert prediction["fit_score"] == items[0].evaluation.fit_score
    assert prediction["decision"] == ("reject" if mode == "labels" else "pass")
    assert prediction["tier"] == ("C" if mode == "labels" and tier_source == "score" else "A")
    assert pipeline.tagger.client.calls == ["tag", "escalate"]


@pytest.mark.parametrize("tier_source,expected_tier", [("score", "C"), ("relevance", "B")])
def test_export_keeps_intake_source_conflict_cap_after_accepted_hiring_gate(
    build, tier_source, expected_tier,
):
    pipeline, record, items, _ = build(tier_source=tier_source, source_conflicts=True)
    predictions, stopped = label_records(
        pipeline.config, [("case", copy.deepcopy(record))], use_ai=False,
    )
    assert not stopped
    assert predictions[0]["decision"] == str(items[0].evaluation.decision) == "reject"
    assert predictions[0]["tier"] == str(items[0].evaluation.tier) == expected_tier


@pytest.mark.parametrize("change", ["stale_source", "proposed", "error", "rules_only"])
def test_export_ignores_unaccepted_hiring_and_relevance_for_final_decision(
    tmp_path, monkeypatch, change,
):
    config = settings(tmp_path, mode="labels", tier_source="relevance")
    record = _record()
    result = LabelResult(
        status="labeled", role_label="product", relevance=0, rubric_status="confirmed",
        rubric_version="v3", prompt_version="prompt", aggregation_version="aggregation",
        source_hash=source_hash(record), gate_outcome="ineligible",
        gate_reasons=["hiring_region_restricted"],
    )
    if change == "stale_source":
        result.source_hash = "0" * 64
    elif change == "proposed":
        result.rubric_status = "proposed"
    else:
        result.status = change
    fake_tagger(monkeypatch, result)
    predictions, stopped = label_records(config, [("case", record)], use_ai=False)
    assert not stopped
    assert predictions[0]["decision"] == "pass"
    assert predictions[0]["tier"] == "A"


def test_export_applies_confirmed_gated_result_before_assigning_relevance_tier(
    tmp_path, monkeypatch,
):
    config = settings(tmp_path, mode="labels", tier_source="relevance")
    record = _record()
    fake_tagger(monkeypatch, LabelResult(
        status="gated", role_label="product", relevance=0, rubric_status="confirmed",
        rubric_version="v3", prompt_version="prompt", aggregation_version="aggregation",
        source_hash=source_hash(record), gate_outcome="unknown",
    ))
    predictions, stopped = label_records(config, [("case", record)], use_ai=False)
    assert not stopped
    assert predictions[0]["decision"] == "review" and predictions[0]["tier"] == "C"
