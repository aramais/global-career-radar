"""Regular shadow reevaluation retains accepted labels independently of legacy scoring."""

from __future__ import annotations

import copy
from dataclasses import asdict, replace
from datetime import UTC, datetime

import pytest
import yaml
from sqlalchemy import select
from test_labels_failure_preservation import budget
from test_labels_tagger import FakeClient, payload
from test_pipeline_profiles import _config, _record, _sources

from job_intake.annotation.text import source_hash
from job_intake.config.settings import LabelingConfig
from job_intake.crm.jobs import job_detail, list_job_page
from job_intake.pipeline import JobIntakePipeline
from job_intake.purpose import update_human_purpose
from job_intake.storage.models import (
    AlertOutboxORM,
    JobEventORM,
    JobORM,
    JobPurposeAssessmentORM,
)
from job_intake.storage.repository import JobRepository, JobSourceConflictError
from job_intake.storage.source_state import record_from_saved


def _state(pipeline):
    with pipeline.database.session() as session:
        job = session.scalar(select(JobORM))
        profile = job.profile_evaluations[0]
        purpose = job.purpose_assessment
        return {
            "uid": job.job_uid,
            "labels": copy.deepcopy(job.annotation["labels_v3"]),
            "attempt": copy.deepcopy(job.annotation.get("labels_v3_attempt")),
            "label_fields": (job.relevance, job.labels_status, job.labels_version),
            "profile_labels": (profile.relevance, profile.labels_status, profile.labels_version),
            "tier": job.tier,
            "fit_score": job.fit_score,
            "decision": job.filter_decision,
            "profile_version": profile.profile_version,
            "source_hash": source_hash(record_from_saved(job)),
            "revision": job.source_revision,
            "archived_at": job.archived_at,
            "purpose": {column.name: copy.deepcopy(getattr(purpose, column.name))
                        for column in purpose.__table__.columns},
            "crm_list": {key: value for key, value in list_job_page(
                session, pipeline.profile_versions, {"job_archive": "all"},
            )["jobs"][0].items() if key.startswith("labels")},
            "crm_detail": {key: value for key, value in job_detail(
                session, pipeline.profile_versions, job.job_uid,
            ).items() if key.startswith("labels")},
        }


@pytest.fixture
def shadow_labels(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Shadow regressions must not call a real provider")

    monkeypatch.setattr("requests.sessions.Session.request", forbidden)
    config = _config(tmp_path)
    labeling = LabelingConfig(
        enabled=True, mode="shadow", ai_enabled=True,
        cache_dir=str(tmp_path / "cache"), ledger_dir=str(tmp_path / "ledger"),
        precedents_path=str(tmp_path / "absent.jsonl"), escalation={"enabled": False},
    )
    pipeline = JobIntakePipeline(replace(config, labeling=labeling))
    record = _record(
        "Responsibilities\nYou will lead analytics for business decisions.\n"
        "You will partner with leadership on projects.\nRequirements\nEnglish required."
    )
    items, keys, _ = pipeline._evaluate_record(record, pipeline._stats(), use_llm=False)
    pipeline._save(items, keys, observed=True, queue_alert=False)
    pipeline.tagger.client = FakeClient([payload(record, role_label="product", relevance=3)])
    pipeline.tagger.budget = budget(labeling)
    accepted = pipeline.tagger.label(
        record, items[0].evaluation, items[0].role_classification, use_ai=True,
    )
    assert accepted.status == "labeled"
    with pipeline.database.session() as session:
        job = session.scalar(select(JobORM))
        JobRepository(session).update_labels(
            job.job_uid, asdict(accepted), expected_source_hash=source_hash(record),
        )
        purpose = session.get(JobPurposeAssessmentORM, job.job_uid)
        update_human_purpose(session, job.job_uid, {
            "expected_version": purpose.version, "source_hash": purpose.prediction_source_hash,
            "human_p1": "suitable", "human_p2": "unsuitable", "human_note": "Human choice",
        })
        session.commit()
    pipeline.tagger.config = replace(labeling, cache_dir=str(tmp_path / "empty-cache"))
    yield pipeline, record, accepted, tmp_path
    pipeline.database.engine.dispose()


@pytest.mark.parametrize("status", ["rules_only", "error"])
@pytest.mark.parametrize("archived", [False, True])
def test_regular_shadow_fallback_retains_labels_and_attempt_without_touching_human_answers(
    shadow_labels, monkeypatch, status, archived,
):
    pipeline, _, accepted, _ = shadow_labels
    if archived:
        with pipeline.database.session() as session:
            session.scalar(select(JobORM)).archived_at = datetime.now(UTC)
            session.commit()
    if status == "error":
        monkeypatch.setattr(pipeline.tagger, "relabel_offline", lambda *args: replace(
            accepted, status="error", relevance=None, errors=[{"code": "cache_read_failed"}],
        ))
    before = _state(pipeline)
    stats = pipeline.reevaluate_saved(use_ai=False, include_archived=archived)
    after = _state(pipeline)
    assert stats["persisted"] == 1 and stats["record_errors"] == 0
    assert stats["labels_calls"] == 0
    for key in ("labels", "label_fields", "profile_labels", "crm_list", "crm_detail",
                "tier", "fit_score", "decision", "archived_at", "purpose"):
        assert after[key] == before[key]
    assert after["attempt"]["status"] == status
    assert after["attempt"]["source_hash"] == before["source_hash"]
    # Legacy reevaluation ran and was saved; this is not the labels-mode early return.
    assert after["revision"] == before["revision"] + 1


def test_shadow_fallback_still_saves_changed_legacy_score_profile_version_and_tier(shadow_labels):
    original, _, _, _ = shadow_labels
    before = _state(original)
    profiles = yaml.safe_load(original.config.search_profiles_path.read_text())
    profiles["streams"][0]["scoring"]["title_weights"] = {"product manager": 1}
    original.config.search_profiles_path.write_text(yaml.safe_dump(profiles))
    pipeline = JobIntakePipeline(original.config)
    pipeline.tagger.config = original.tagger.config
    try:
        stats = pipeline.reevaluate_saved(use_ai=False)
        after = _state(pipeline)
        assert stats["persisted"] == 1 and stats["record_errors"] == 0
        assert before["tier"] == "A" and after["tier"] == "C"
        assert before["fit_score"] == 8 and after["fit_score"] == 1
        assert after["profile_version"] != before["profile_version"]
        for key in ("labels", "label_fields", "profile_labels", "crm_list", "crm_detail"):
            assert after[key] == before[key]
        assert after["purpose"]["human_p1"] == before["purpose"]["human_p1"]
        assert after["purpose"]["human_p2"] == before["purpose"]["human_p2"]
    finally:
        pipeline.database.engine.dispose()


@pytest.mark.parametrize("metadata", [
    {"fetched_at": "2026-10-20T23:21:01Z"},
    {"description_complete": False, "description_source": "listing_card"},
])
def test_shadow_collection_only_retention_rebinds_labels_and_keeps_legacy_upsert(
    shadow_labels, monkeypatch, metadata,
):
    pipeline, record, _, _ = shadow_labels
    before = _state(pipeline)
    observed = copy.deepcopy(record)
    observed.source_metadata.update(metadata)
    _sources(monkeypatch, observed, fail_second=False)
    stats = pipeline.run()
    after = _state(pipeline)
    assert stats["persisted"] == 1 and stats["record_errors"] == 0
    assert after["source_hash"] != before["source_hash"]
    assert after["labels"] == {**before["labels"], "source_hash": after["source_hash"]}
    assert after["label_fields"] == before["label_fields"]
    assert after["profile_labels"] == before["profile_labels"]
    assert after["attempt"]["status"] == "rules_only"
    assert after["attempt"]["source_hash"] == after["source_hash"]
    assert after["revision"] == before["revision"] + 1
    with pipeline.database.session() as session:
        events = session.scalars(select(JobEventORM).where(
            JobEventORM.event_type == "labels_source_rebound",
        )).all()
        assert len(events) == 1
        assert events[0].payload["previous_source_hash"] == before["source_hash"]
        assert events[0].payload["source_hash"] == after["source_hash"]


@pytest.mark.parametrize("change", ["description", "unknown_metadata", "status"])
def test_shadow_cannot_keep_a_prior_label_for_changed_meaningful_source(
    shadow_labels, monkeypatch, change,
):
    pipeline, record, _, _ = shadow_labels
    observed = copy.deepcopy(record)
    if change == "description":
        observed.description_clean += "\nRequirements\nFrench required."
    elif change == "unknown_metadata":
        observed.source_metadata["new_policy"] = "Candidates from Canada only"
    else:
        from job_intake.models.job import JobStatus

        observed.status = JobStatus.CLOSED
    _sources(monkeypatch, observed, fail_second=False)
    stats = pipeline.run()
    assert stats["persisted"] == 1 and stats["record_errors"] == 0
    assert _state(pipeline)["labels"]["status"] != "labeled"


def test_shadow_replaces_the_prior_label_when_current_cache_has_a_successful_result(
    shadow_labels,
):
    pipeline, record, _, _ = shadow_labels
    items, _, _ = pipeline._evaluate_record(record, pipeline._stats(), use_llm=False)
    pipeline.tagger.client = FakeClient([payload(record, role_label="product", relevance=2)])
    pipeline.tagger.budget = budget(pipeline.config.labeling)
    fresh = pipeline.tagger.label(
        record, items[0].evaluation, items[0].role_classification, use_ai=True,
    )
    assert fresh.status == "labeled" and fresh.relevance == 2
    stats = pipeline.reevaluate_saved(use_ai=False)
    after = _state(pipeline)
    assert stats["persisted"] == 1 and stats["record_errors"] == 0
    assert after["labels"]["relevance"] == 2
    assert after["label_fields"][0] == 2 and after["profile_labels"][0] == 2


def test_shadow_fallback_keeps_legacy_alert_queue_eligibility(shadow_labels):
    pipeline, record, _, _ = shadow_labels
    pipeline.config.telegram.enabled = True
    items, keys, _ = pipeline._evaluate_record(record, pipeline._stats(), use_llm=False)
    pipeline._save(items, keys, observed=True, queue_alert=True)
    with pipeline.database.session() as session:
        alerts = session.scalars(select(AlertOutboxORM)).all()
        assert len(alerts) == 1 and alerts[0].status == "pending"
        assert session.scalar(select(JobORM)).labels_status == "labeled"


def test_shadow_retention_keeps_the_accepted_labels_version(shadow_labels):
    pipeline, record, accepted, _ = shadow_labels
    historical = replace(accepted, prompt_version="accepted-prompt-v1",
                         aggregation_version="accepted-aggregation-v1")
    with pipeline.database.session() as session:
        job = session.scalar(select(JobORM))
        JobRepository(session).update_labels(
            job.job_uid, asdict(historical), expected_source_hash=source_hash(record),
        )
        session.commit()
    before = _state(pipeline)
    assert pipeline.reevaluate_saved(use_ai=False)["persisted"] == 1
    after = _state(pipeline)
    assert after["labels"] == before["labels"]
    assert after["label_fields"] == before["label_fields"]
    assert after["profile_labels"] == before["profile_labels"]
    assert after["label_fields"][2] == "accepted-prompt-v1:accepted-aggregation-v1"


def test_shadow_role_change_does_not_assign_the_prior_labels_to_another_profile(shadow_labels):
    original, _, _, _ = shadow_labels
    profiles = yaml.safe_load(original.config.search_profiles_path.read_text())
    profiles["streams"] = [profiles["streams"][1]]
    original.config.search_profiles_path.write_text(yaml.safe_dump(profiles))
    pipeline = JobIntakePipeline(original.config)
    pipeline.tagger.config = original.tagger.config
    try:
        stats = pipeline.reevaluate_saved(use_ai=False)
        assert stats["persisted"] == 1 and stats["record_errors"] == 0
        with pipeline.database.session() as session:
            job = session.scalar(select(JobORM))
            assert job.role_label == "other"
            assert job.annotation["labels_v3"]["role_label"] == "other"
            assert job.labels_status == "rules_only" and job.relevance != 3
            assert job.relevance == job.annotation["labels_v3"]["relevance"]
            assert len(job.profile_evaluations) == 1
            assert job.profile_evaluations[0].profile_id == "other"
            assert job.profile_evaluations[0].relevance == job.relevance
    finally:
        pipeline.database.engine.dispose()


def test_shadow_repeated_listing_rebind_then_full_observation_retains_accepted_labels(
    shadow_labels, monkeypatch,
):
    pipeline, record, _, _ = shadow_labels
    before = _state(pipeline)
    for metadata in (
        {"description_complete": False, "description_source": "listing_card", "fetched_at": "one"},
        {"description_complete": False, "description_source": "email_excerpt", "fetched_at": "two"},
        {"fetched_at": "three"},
    ):
        observed = copy.deepcopy(record)
        observed.source_metadata.update(metadata)
        _sources(monkeypatch, observed, fail_second=False)
        stats = pipeline.run()
        assert stats["persisted"] == 1 and stats["record_errors"] == 0
        after = _state(pipeline)
        assert after["labels"] == {**before["labels"], "source_hash": after["source_hash"]}
        assert after["label_fields"] == before["label_fields"]
        assert after["profile_labels"] == before["profile_labels"]


def test_shadow_retention_cannot_overwrite_a_newer_same_source_label(shadow_labels, monkeypatch):
    pipeline, record, accepted, _ = shadow_labels
    items, keys, _ = pipeline._evaluate_record(record, pipeline._stats(), use_llm=False)
    method = JobRepository.upsert_evaluated_job
    interleaved = []

    def race(repository, *args, **kwargs):
        execute = repository.session.execute

        def after_read(statement, *values, **options):
            if (not interleaved and getattr(getattr(statement, "table", None), "name", None)
                    == "jobs" and "source_revision" in statement.compile().params):
                with pipeline.database.session() as session:
                    job = session.scalar(select(JobORM))
                    JobRepository(session).update_labels(
                        job.job_uid, asdict(replace(accepted, relevance=2)),
                        expected_source_hash=source_hash(record),
                        expected_source_revision=job.source_revision,
                    )
                    session.commit()
                interleaved.append(True)
            return execute(statement, *values, **options)

        with monkeypatch.context() as injected:
            injected.setattr(repository.session, "execute", after_read)
            return method(repository, *args, **kwargs)

    monkeypatch.setattr(JobRepository, "upsert_evaluated_job", race)
    with pytest.raises(JobSourceConflictError, match="Source changed"):
        pipeline._save(items, keys, observed=False, queue_alert=False)
    assert interleaved == [True]
    assert _state(pipeline)["labels"]["relevance"] == 2
