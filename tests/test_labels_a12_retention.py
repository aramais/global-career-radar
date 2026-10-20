"""Collection observations do not replace accepted labels with offline fallback."""

from __future__ import annotations

import copy
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from test_labels_failure_preservation import saved_labels as saved_labels
from test_labels_failure_preservation import state
from test_pipeline_profiles import _sources

from job_intake.annotation.input_contract import annotation_input_hash
from job_intake.annotation.text import source_hash
from job_intake.crm.jobs import serialize_job
from job_intake.models.job import JobStatus
from job_intake.pipeline import JobIntakePipeline
from job_intake.storage.models import AlertOutboxORM, JobEventORM, JobORM
from job_intake.storage.repository import JobRepository, JobSourceConflictError


def _miss(pipeline, labeling, tmp_path):
    pipeline.tagger.config = replace(labeling, cache_dir=str(tmp_path / "empty-cache"))


@pytest.mark.parametrize("metadata", [
    {"fetched_at": "2026-10-20T23:11:01Z"},
    {"collection_id": "next-collection", "feed_position": 42},
    {"collection_observations": {"fetched_at": "2026-10-20T23:11:01Z"}},
    {"description_complete": False, "description_source": "listing_card",
     "fetched_at": "2026-10-20T23:11:01Z"},
    {"description_complete": False, "description_source": "email_excerpt"},
    {"description_truncated": True, "description_source": "listing_card"},
    {"description_complete": False, "description_source": "custom_listing_origin"},
])
@pytest.mark.parametrize("archived", [False, True])
def test_public_intake_retains_assessment_and_rebinds_only_collection_source_pin(
    saved_labels, monkeypatch, metadata, archived,
):
    pipeline, record, labeling, tmp_path = saved_labels
    if archived:
        with pipeline.database.session() as session:
            session.scalar(select(JobORM)).archived_at = datetime.now(UTC)
            session.commit()
    before = state(pipeline)
    observed = copy.deepcopy(record)
    observed.source_metadata.update(metadata)
    assert source_hash(observed) != source_hash(record)
    _miss(pipeline, labeling, tmp_path)
    _sources(monkeypatch, observed, fail_second=False)
    stats = pipeline.run()
    after = state(pipeline)
    assert stats["record_errors"] == 0 and stats["labels_calls"] == 0
    assert stats["persisted"] == 1
    assert annotation_input_hash(observed) == annotation_input_hash(record)
    assert after["job"]["tier"] == "A" and after["job"]["relevance"] == 3
    assert after["job"]["labels_status"] == "labeled"
    for field in ("fit_score", "filter_decision", "eligibility_status", "archived_at",
                  "archive_version", "first_seen_at", "last_alerted_at", "alert_pending"):
        assert after["job"][field] == before["job"][field]
    assert after["purpose"] == before["purpose"]
    assert after["job"]["source_revision"] == before["job"]["source_revision"] + 1
    assert after["job"]["last_seen_at"] >= before["job"]["last_seen_at"]
    expected_labels = {**before["annotation"]["labels_v3"], "source_hash": source_hash(observed)}
    assert after["annotation"]["labels_v3"] == expected_labels
    assert after["attempt"]["status"] == "rules_only"
    assert after["attempt"]["source_hash"] == source_hash(observed)
    for before_eval, after_eval in zip(before["evaluations"], after["evaluations"], strict=True):
        assert after_eval == {**before_eval, "classification_source_hash": source_hash(observed),
                              "content_hash": after["job"]["content_hash"]}
    with pipeline.database.session() as session:
        job = session.scalar(select(JobORM))
        public = serialize_job(job, pipeline.profile_versions, detailed=True)
        assert public["labels_relevance"] == 3 and public["labels_status"] == "labeled"
        assert public["labels_v3"]["relevance"] == 3
        events = session.scalars(select(JobEventORM).where(
            JobEventORM.event_type == "labels_source_rebound",
        )).all()
        assert len(events) == 1
        assert events[0].payload["previous_source_hash"] == source_hash(record)
        assert events[0].payload["source_hash"] == source_hash(observed)
        assert session.scalars(select(AlertOutboxORM)).all() == []


@pytest.mark.parametrize("change", [
    "description", "status", "language", "unknown_metadata", "nested_requirement", "incomplete",
    "nested_completeness", "source_conflicts",
])
def test_fallback_cannot_rebind_a_changed_meaningful_source(saved_labels, monkeypatch, change):
    pipeline, record, labeling, tmp_path = saved_labels
    observed = copy.deepcopy(record)
    observed.source_metadata["fetched_at"] = "2026-10-20T23:11:01Z"
    if change == "description":
        observed.description_clean += "\nRequirements\nFrench required."
    elif change == "status":
        observed.status = JobStatus.CLOSED
    elif change == "language":
        observed.source_metadata["working_language"] = "French"
    elif change == "unknown_metadata":
        observed.source_metadata["new_policy"] = "Residents of Canada only"
    elif change == "nested_requirement":
        observed.source_metadata["collection_observations"] = {"working_language": "French"}
    elif change == "nested_completeness":
        observed.source_metadata["collection_observations"] = {
            "incoming_description_complete": False,
        }
    elif change == "source_conflicts":
        observed.source_metadata["source_conflicts"] = [{"field": "working_language"}]
    else:
        observed.source_metadata["description_complete"] = False
        observed.source_metadata["description_source"] = "listing_card"
        observed.description_clean = "This new listing requires French for all role duties."
    _miss(pipeline, labeling, tmp_path)
    _sources(monkeypatch, observed, fail_second=False)
    stats = pipeline.run()
    assert stats["record_errors"] == 0
    after = state(pipeline)
    assert after["job"]["labels_status"] != "labeled"
    with pipeline.database.session() as session:
        assert session.scalars(select(JobEventORM).where(
            JobEventORM.event_type == "labels_source_rebound",
        )).all() == []
    if change == "status":
        assert after["job"]["status"] == "closed"


def test_collection_rebind_refuses_a_concurrently_changed_revision(saved_labels):
    pipeline, record, labeling, tmp_path = saved_labels
    observed = copy.deepcopy(record)
    observed.source_metadata["fetched_at"] = "2026-10-20T23:11:01Z"
    _miss(pipeline, labeling, tmp_path)
    items, keys, _ = pipeline._evaluate_record(observed, pipeline._stats(), use_llm=False)
    with pipeline.database.session() as session:
        session.scalar(select(JobORM)).source_revision += 1
        session.commit()
    before = state(pipeline)
    with pytest.raises(JobSourceConflictError, match="Source changed"):
        pipeline._save(items, keys, observed=True, queue_alert=False)
    assert state(pipeline) == before


def test_collection_rebind_cannot_overwrite_a_newer_same_source_label(
    saved_labels, monkeypatch,
):
    pipeline, record, labeling, tmp_path = saved_labels
    observed = copy.deepcopy(record)
    observed.source_metadata["fetched_at"] = "2026-10-20T23:11:01Z"
    _miss(pipeline, labeling, tmp_path)
    items, keys, _ = pipeline._evaluate_record(observed, pipeline._stats(), use_llm=False)
    method = JobRepository.record_collection_label_attempt
    newer = {**state(pipeline)["annotation"]["labels_v3"], "relevance": 2}
    interleaved = []

    def race(repository, *args, **kwargs):
        execute = repository.session.execute

        def after_read(statement, *values, **options):
            if (not interleaved and getattr(getattr(statement, "table", None), "name", None)
                    == "jobs" and "source_revision" in statement.compile().params):
                with pipeline.database.session() as session:
                    competing = JobRepository(session)
                    job = session.scalar(select(JobORM))
                    assert competing.update_labels(
                        job.job_uid, newer, expected_source_hash=source_hash(record),
                        expected_source_revision=job.source_revision,
                    )
                    session.commit()
                interleaved.append(True)
            return execute(statement, *values, **options)

        with monkeypatch.context() as injected:
            injected.setattr(repository.session, "execute", after_read)
            return method(repository, *args, **kwargs)

    monkeypatch.setattr(JobRepository, "record_collection_label_attempt", race)
    with pytest.raises(JobSourceConflictError, match="Source changed"):
        pipeline._save(items, keys, observed=True, queue_alert=False)
    after = state(pipeline)
    assert interleaved == [True]
    assert after["job"]["relevance"] == 2
    assert after["annotation"]["labels_v3"] == newer
    assert after["attempt"] is None
    assert after["job"]["source_revision"] == 1


def test_repeated_collection_rebind_keeps_the_same_accepted_result(saved_labels, monkeypatch):
    pipeline, record, labeling, tmp_path = saved_labels
    before = state(pipeline)
    _miss(pipeline, labeling, tmp_path)
    observations = [
        {"description_complete": False, "description_source": "listing_card", "fetched_at": "one"},
        {"description_complete": False, "description_source": "email_excerpt", "fetched_at": "two"},
        {"fetched_at": "three"},
    ]
    for metadata in observations:
        observed = copy.deepcopy(record)
        observed.source_metadata.update(metadata)
        _sources(monkeypatch, observed, fail_second=False)
        assert pipeline.run()["record_errors"] == 0
    after = state(pipeline)
    assert after["job"]["tier"] == "A" and after["job"]["relevance"] == 3
    assert after["job"]["source_revision"] == before["job"]["source_revision"] + len(observations)
    assert after["purpose"] == before["purpose"]
    assert after["annotation"]["labels_v3"] == {
        **before["annotation"]["labels_v3"], "source_hash": source_hash(observed),
    }


def test_retention_does_not_upgrade_the_accepted_policy_version(saved_labels, monkeypatch):
    original, record, labeling, tmp_path = saved_labels
    before = state(original)
    changed = replace(labeling, final_policy="rules", tier_source="score",
                      cache_dir=str(tmp_path / "empty-cache"))
    pipeline = JobIntakePipeline(replace(original.config, labeling=changed))
    observed = copy.deepcopy(record)
    observed.source_metadata["fetched_at"] = "2026-10-20T23:11:01Z"
    _sources(monkeypatch, observed, fail_second=False)
    try:
        assert pipeline.run()["record_errors"] == 0
        after = state(pipeline)
        assert after["job"]["version_components"] == before["job"]["version_components"]
        assert (after["evaluations"][0]["profile_version"]
                == before["evaluations"][0]["profile_version"])
        assert after["evaluations"][0]["profile_version"] != pipeline.profile_versions["product"]
        with pipeline.database.session() as session:
            job = session.scalar(select(JobORM))
            public = serialize_job(job, pipeline.profile_versions, detailed=True)
            assert public["evaluation_state"] == "needs_reevaluation"
    finally:
        pipeline.database.engine.dispose()
