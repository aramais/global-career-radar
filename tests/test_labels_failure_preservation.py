"""A failed new attempt cannot replace a same-source accepted evaluation."""

from __future__ import annotations

import copy
import json
from dataclasses import replace
from datetime import UTC, datetime

import pytest
import yaml
from sqlalchemy import select
from test_labels_tagger import FakeClient, payload
from test_pipeline_profiles import _config, _record, _sources

from job_intake.annotation.ai import AnnotationModelError
from job_intake.config.settings import LabelingConfig
from job_intake.labeling.budget import BudgetGuard
from job_intake.pipeline import JobIntakePipeline
from job_intake.purpose import update_human_purpose
from job_intake.storage.models import (
    JobORM,
    JobProfileEvaluationORM,
    JobPurposeAssessmentORM,
)
from job_intake.storage.repository import JobSourceConflictError
from job_intake.storage.source_state import record_from_saved


def budget(config):
    return BudgetGuard(1, {"models": {
        model: {"input_per_million": 1, "output_per_million": 1}
        for model in (config.tag_model, config.escalate_model)
    }})


def state(pipeline):
    def values(row):
        return {column.name: copy.deepcopy(getattr(row, column.name))
                for column in row.__table__.columns}

    with pipeline.database.session() as session:
        job = session.scalar(select(JobORM))
        purpose = session.get(JobPurposeAssessmentORM, job.job_uid)
        row = values(job)
        row.pop("updated_at")
        annotation = row.pop("annotation")
        attempt = annotation.pop("labels_v3_attempt", None)
        return {
            "job": row, "annotation": annotation, "attempt": attempt,
            "purpose": values(purpose),
            "evaluations": [values(value) for value in session.scalars(
                select(JobProfileEvaluationORM).order_by(JobProfileEvaluationORM.id)
            )],
        }


@pytest.fixture
def saved_labels(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Regressions must not call a real model")

    monkeypatch.setattr("requests.sessions.Session.request", forbidden)
    config = _config(tmp_path)
    profiles = yaml.safe_load(config.search_profiles_path.read_text())
    for stream in profiles["streams"]:
        stream["scoring"].update(title_weights={}, threshold_a=100, threshold_b=50)
    config.search_profiles_path.write_text(yaml.safe_dump(profiles))
    labeling = LabelingConfig(
        enabled=True, mode="labels", ai_enabled=True, tier_source="relevance",
        cache_dir=str(tmp_path / "accepted-cache"), ledger_dir=str(tmp_path / "ledger"),
        precedents_path=str(tmp_path / "no-precedents"), escalation={"enabled": False},
    )
    pipeline = JobIntakePipeline(replace(config, labeling=labeling))
    record = _record(
        "Responsibilities\nYou will lead analytics for business decisions.\n"
        "You will partner with leadership on projects.\nRequirements\nEnglish required."
    )
    pipeline.tagger.client = FakeClient([payload(record, role_label="product", relevance=3)])
    pipeline.tagger.budget = budget(labeling)
    pipeline._label_ai_authorized = True
    items, keys, _ = pipeline._evaluate_record(record, pipeline._stats(), use_llm=True)
    pipeline._save(items, keys, observed=True, queue_alert=False)
    pipeline._label_ai_authorized = False
    with pipeline.database.session() as session:
        row = session.scalar(select(JobPurposeAssessmentORM))
        update_human_purpose(session, row.job_uid, {
            "expected_version": row.version, "source_hash": row.prediction_source_hash,
            "human_p1": "suitable", "human_p2": "unsuitable", "human_note": "Human answer",
        })
        session.commit()
    before = state(pipeline)
    assert before["job"]["tier"] == "A" and before["job"]["fit_score"] == 0
    assert before["job"]["relevance"] == 3
    assert before["purpose"]["predicted_p2"] == "unsuitable"
    return pipeline, record, labeling, tmp_path


@pytest.mark.parametrize("status", ["error", "rules_only"])
@pytest.mark.parametrize("archived", [False, True])
def test_failed_or_empty_offline_attempt_keeps_accepted_fields_and_human_purpose(
    saved_labels, status, archived,
):
    pipeline, _, config, tmp_path = saved_labels
    if archived:
        with pipeline.database.session() as session:
            session.scalar(select(JobORM)).archived_at = datetime.now(UTC)
            session.commit()
    before = state(pipeline)
    pipeline.tagger.config = replace(config, cache_dir=str(tmp_path / "empty-cache"))
    pipeline.tagger.client = FakeClient(
        [AnnotationModelError("PRIVATE PROVIDER RESPONSE")] if status == "error" else []
    )
    result = pipeline.label_saved(
        use_ai=status == "error", offline=status == "rules_only",
        include_archived=archived, budget=budget(config) if status == "error" else None,
    )
    after = state(pipeline)
    assert result["persisted"] == 1 and result["labels_errors"] == (status == "error")
    assert after["attempt"]["status"] == status
    assert "PRIVATE PROVIDER RESPONSE" not in json.dumps(after["attempt"])
    assert {key: value for key, value in after.items() if key != "attempt"} == {
        key: value for key, value in before.items() if key != "attempt"
    }
    assert pipeline._label_ai_authorized is False
    assert pipeline._label_include_archived is False


@pytest.mark.parametrize("change_source", [False, True])
def test_stale_failed_label_cannot_overwrite_concurrent_source_revision(
    saved_labels, change_source,
):
    pipeline, _, config, tmp_path = saved_labels
    with pipeline.database.session() as session:
        record = record_from_saved(session.scalar(select(JobORM)))
    pipeline.tagger.config = replace(config, cache_dir=str(tmp_path / "empty-cache"))
    pipeline.tagger.client = FakeClient([AnnotationModelError("private")])
    pipeline.tagger.budget = budget(config)
    pipeline._label_ai_authorized = True
    try:
        items, keys, _ = pipeline._evaluate_record(record, pipeline._stats(), use_llm=True)
    finally:
        pipeline._label_ai_authorized = False
    with pipeline.database.session() as session:
        job = session.scalar(select(JobORM))
        job.source_revision += 1
        if change_source:
            job.description_clean += "\nA concurrently fetched source sentence."
        session.commit()
    before = state(pipeline)
    with pytest.raises(JobSourceConflictError, match="Source changed"):
        pipeline._save(items, keys, observed=False, queue_alert=False)
    assert state(pipeline) == before


@pytest.mark.parametrize("operation", ["run", "annotate", "reevaluate"])
def test_ordinary_pipeline_calls_use_cached_labels_without_paid_authorization(
    saved_labels, monkeypatch, operation,
):
    pipeline, record, _, _ = saved_labels

    def forbidden(*args, **kwargs):
        pytest.fail("Only the explicit budgeted label command may generate new labels")

    pipeline.tagger.client.generate_stage = forbidden
    assert pipeline.config.labeling.ai_enabled is True
    assert pipeline._label_ai_authorized is False
    if operation == "run":
        _sources(monkeypatch, record, fail_second=False)
        result = pipeline.run()
    else:
        result = pipeline.reevaluate_saved(use_ai=operation == "annotate")
    assert result["persisted"] == 1
    assert result["labels_cache_hits"] == 1
    assert state(pipeline)["job"]["tier"] == "A"


def test_label_saved_ai_requires_budget_before_any_attempt(saved_labels):
    pipeline, _, _, _ = saved_labels
    before = state(pipeline)
    with pytest.raises(ValueError, match="explicit budget"):
        pipeline.label_saved(use_ai=True)
    assert state(pipeline) == before
    assert pipeline._label_ai_authorized is False
