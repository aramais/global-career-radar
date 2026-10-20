"""Independent regressions for export boundaries and complete LabelResult objects."""

from __future__ import annotations

import copy
import json
from dataclasses import asdict, replace

import pytest
from test_pipeline_profiles import _config, _record

from job_intake.annotation.frozen_eval import source_snapshot
from job_intake.annotation.text import TEXT_VERSION, source_hash
from job_intake.config.settings import LabelingConfig
from job_intake.labeling.predictions import check_prediction_rows
from job_intake.labeling.runner import export_records, label_records
from job_intake.labeling.schema import LABELS_SCHEMA, LabelResult


def gold_case(record):
    return {
        "schema": LABELS_SCHEMA, "job_uid": "case", "source_hash": source_hash(record),
        "text_version": TEXT_VERSION, "source": source_snapshot(record), "labeler": "human",
        "method": "blind", "rubric_version": "test", "first_labeled_at": "2026-10-20",
        "labeled_at": "2026-10-20", "role_label": "product", "relevance": 3,
        "gate_outcome": "unknown", "gate_reasons": [], "tags": {},
        "notes": "PRIVATE HUMAN EXPLANATION",
    }


def settings(tmp_path, *, mode="shadow", tier_source="score"):
    return replace(_config(tmp_path), labeling=LabelingConfig(
        enabled=True, mode=mode, tier_source=tier_source,
        cache_dir=str(tmp_path / "cache"), ledger_dir=str(tmp_path / "ledger"),
    ))


def fake_tagger(monkeypatch, result):
    class Fake:
        def __init__(self, *args, **kwargs):
            pass

        def label(self, *args, use_ai):
            assert use_ai is False
            return copy.deepcopy(result)

        def relabel_offline(self, *args):
            return copy.deepcopy(result)

    monkeypatch.setattr("job_intake.labeling.tagger.VacancyTagger", Fake)


def test_gold_snapshot_export_never_passes_human_answers_or_opens_db(tmp_path, monkeypatch):
    config = settings(tmp_path)
    record = _record()
    path = tmp_path / "gold.jsonl"
    case = gold_case(record)
    path.write_text(json.dumps(case) + "\n")

    def forbidden(*args, **kwargs):
        pytest.fail("Gold snapshot inference must not open the working DB")

    monkeypatch.setattr("job_intake.labeling.runner.read_saved_records", forbidden)
    rows = export_records(config, gold=path, profile_ids=["product", "analytics"])
    assert len(rows) == 1
    uid, exported = rows[0]
    assert uid == "case" and exported.annotation == {}
    assert "PRIVATE HUMAN EXPLANATION" not in str(asdict(exported))
    human_fields = {"relevance", "role_label", "tags", "gate_outcome", "notes"}
    assert not human_fields & asdict(exported).keys()


def test_gold_snapshot_export_refuses_unproved_source_pin_before_any_model(tmp_path):
    config = settings(tmp_path)
    path = tmp_path / "gold.jsonl"
    case = gold_case(_record())
    case["source_hash"] = "0" * 64
    path.write_text(json.dumps(case) + "\n")
    with pytest.raises(ValueError, match="source_hash|snapshot"):
        export_records(config, gold=path, profile_ids=["product", "analytics"])


def test_complete_labelresult_export_is_schema_checked_and_source_bound(tmp_path, monkeypatch):
    config = settings(tmp_path)
    record = _record()
    result = LabelResult(
        status="labeled", role_label="product", relevance=2, rubric_status="confirmed",
        rubric_version="v3", prompt_version="prompt", aggregation_version="aggregation",
        source_hash=source_hash(record), annotation_input_hash="private-input-hash",
        cache_key="private-cache-key", rules_relevance=2, llm_relevance=2,
        relevance_by_policy={"llm": 2, "rules": 2, "min": 2, "max": 2},
        confidence="high", p2_candidate=False,
    )
    fake_tagger(monkeypatch, result)
    predictions, stopped = label_records(config, [("uid", record)], use_ai=False)
    assert not stopped
    check_prediction_rows(predictions, profile_ids=["product", "analytics"])
    prediction = predictions[0]
    assert prediction["source_hash"] == source_hash(record)
    assert prediction["schema"] == "vacancy-labels-v3-prediction"
    assert prediction["relevance_by_policy"] == result.relevance_by_policy
    assert prediction["rubric_status"] == "confirmed"
    assert not {"annotation_input_hash", "cache_key"} & prediction.keys()


@pytest.mark.parametrize("mode,score_positive", [("shadow", True), ("labels", False)])
def test_export_rebuilds_chosen_role_score_only_in_labels_mode(
    tmp_path, monkeypatch, mode, score_positive,
):
    config = settings(tmp_path, mode=mode)
    record = _record()
    fake_tagger(monkeypatch, LabelResult(
        status="labeled", role_label="analytics", relevance=2, rubric_status="confirmed",
        rubric_version="v3", prompt_version="prompt", aggregation_version="aggregation",
    ))
    predictions, stopped = label_records(config, [("uid", record)], use_ai=False)
    assert not stopped and predictions[0]["role_label"] == "analytics"
    assert (predictions[0]["fit_score"] > 0) is score_positive


def test_error_result_never_uses_fallback_ordinal_for_export_tier(tmp_path, monkeypatch):
    config = settings(tmp_path, mode="labels", tier_source="relevance")
    record = _record()
    fake_tagger(monkeypatch, LabelResult(
        status="error", role_label="product", relevance=0, rubric_status="confirmed",
        rubric_version="v3", prompt_version="prompt", aggregation_version="aggregation",
    ))
    predictions, stopped = label_records(config, [("uid", record)], use_ai=False)
    assert not stopped and predictions[0]["tier"] == "A"
