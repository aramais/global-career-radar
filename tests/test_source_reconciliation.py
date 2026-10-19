"""Intake must score the preserved source and retain paid evidence on replacement."""

from copy import deepcopy

import pytest
from sqlalchemy import select
from test_annotation_integration import _config, _model_stub, _record, _sources

from job_intake.models.job import FilterDecision, JobStatus
from job_intake.pipeline import JobIntakePipeline
from job_intake.storage.models import JobEventORM, JobORM
from job_intake.storage.repository import JobRepository, JobSourceConflictError
from job_intake.storage.source_state import reconcile_source


def _full():
    return _record(description_clean=(
        "Responsibilities\nOwn the product roadmap.\n"
        + "Partner with commercial teams and lead experiments to inform decisions.\n" * 35
    ))


def test_card_is_reconciled_before_classifier_and_score(tmp_path, monkeypatch):
    pipeline = JobIntakePipeline(_config(tmp_path))
    full = _full()
    _sources(monkeypatch, full)
    assert pipeline.run()["persisted"] == 1
    seen = []
    original = pipeline.classifier.classify

    def classify(record, **kwargs):
        seen.append(record.description_clean)
        return original(record, **kwargs)

    monkeypatch.setattr(pipeline.classifier, "classify", classify)
    card = _record(
        description_clean="Own the product roadmap.", location_text=None, remote_text=None,
        source_metadata={"description_complete": False, "description_source": "listing_card",
                         "detail_error": "503", "collection_id": "new-collection"},
    )
    _sources(monkeypatch, card)
    assert pipeline.run()["persisted"] == 1
    assert seen == [full.description_clean]
    with pipeline.database.session() as session:
        job = session.scalar(select(JobORM))
        assert job.description_clean == full.description_clean
        assert job.location_text == "Brazil"
        assert job.source_metadata["description_complete"] is True
        assert job.source_metadata["collection_observations"]["detail_error"] == "503"
        assert job.annotation["description_complete"] is True


def test_new_restriction_in_incomplete_update_is_not_ignored(tmp_path, monkeypatch):
    pipeline = JobIntakePipeline(_config(tmp_path))
    _sources(monkeypatch, _full())
    pipeline.run()
    card = _record(description_clean="US work authorization required.", source_metadata={
        "description_complete": False, "description_source": "listing_card",
    })
    _sources(monkeypatch, card)
    assert pipeline.run()["persisted"] == 1
    with pipeline.database.session() as session:
        job = session.scalar(select(JobORM))
        assert "Own the product roadmap." in job.description_clean
        assert "US work authorization required." in job.description_clean
        assert job.filter_decision == FilterDecision.REJECT.value
        assert "source_update_unverified" in job.risks


def test_new_restriction_cannot_match_inside_previous_negation(tmp_path, monkeypatch):
    pipeline = JobIntakePipeline(_config(tmp_path))
    previous = _record(description_clean="No US work authorization required.")
    _sources(monkeypatch, previous)
    pipeline.run()
    card = _record(description_clean="US work authorization required.", source_metadata={
        "description_complete": False,
    })
    _sources(monkeypatch, card)
    assert pipeline.run()["persisted"] == 1
    with pipeline.database.session() as session:
        job = session.scalar(select(JobORM))
        assert job.filter_decision == "reject"
        assert job.source_metadata["source_conflicts"]


def test_generic_card_cannot_erase_saved_hiring_limit_and_repeats_are_stable(
    tmp_path, monkeypatch,
):
    pipeline = JobIntakePipeline(_config(tmp_path))
    full = _record(location_text="United States only")
    _sources(monkeypatch, full)
    pipeline.run()
    for _ in range(2):
        card = _record(location_text="Worldwide", source_metadata={"description_complete": False})
        _sources(monkeypatch, card)
        assert pipeline.run()["persisted"] == 1
        with pipeline.database.session() as session:
            job = session.scalar(select(JobORM))
            assert job.filter_decision != "pass"
            assert "United States only" in job.location_text
            assert "Worldwide" in job.location_text
            assert len(job.source_metadata["source_conflicts"]) == 1
            assert job.location_text.count("United States only") == 1


def test_explicit_closure_survives_reconciliation(tmp_path, monkeypatch):
    pipeline = JobIntakePipeline(_config(tmp_path))
    _sources(monkeypatch, _full())
    pipeline.run()
    card = _record(description_clean="", status=JobStatus.CLOSED,
                   source_metadata={"description_complete": False})
    _sources(monkeypatch, card)
    assert pipeline.run()["persisted"] == 1
    with pipeline.database.session() as session:
        job = session.scalar(select(JobORM))
        assert job.status == "closed"
        assert job.description_clean == _full().description_clean


def test_default_open_on_card_does_not_reopen_closed_details(tmp_path, monkeypatch):
    pipeline = JobIntakePipeline(_config(tmp_path))
    full = _full()
    full.status = JobStatus.CLOSED
    _sources(monkeypatch, full)
    pipeline.run()
    card = _record(description_clean="Own the product roadmap.",
                   source_metadata={"description_complete": False})
    _sources(monkeypatch, card)
    assert pipeline.run()["persisted"] == 1
    with pipeline.database.session() as session:
        assert session.scalar(select(JobORM)).status == "closed"


def test_repository_rejects_card_evaluated_without_reconciliation(tmp_path, monkeypatch):
    pipeline = JobIntakePipeline(_config(tmp_path))
    _sources(monkeypatch, _full())
    pipeline.run()
    items, _, _ = pipeline._evaluate_record(_full(), pipeline._stats(), use_llm=False)
    item = items[0]
    item.record._source_reconciled = False
    item.record.description_clean = "Own the product roadmap."
    item.record.source_metadata = {"description_complete": False}
    item.role_classification = {}  # Importers may explicitly assign a role.
    with pipeline.database.session() as session, pytest.raises(JobSourceConflictError):
        JobRepository(session).upsert_evaluated_job(item)


def test_concurrent_evaluation_cannot_overwrite_newer_source(tmp_path, monkeypatch):
    pipeline = JobIntakePipeline(_config(tmp_path))
    _sources(monkeypatch, _full())
    pipeline.run()
    old, keys, _ = pipeline._evaluate_record(_full(), pipeline._stats(), use_llm=False)
    fresh = _full()
    fresh.description_clean += "\nNew complete responsibilities: manage pricing research."
    new, newkeys, _ = pipeline._evaluate_record(fresh, pipeline._stats(), use_llm=False)
    pipeline._save(new, newkeys, observed=True, queue_alert=False)
    with pytest.raises(JobSourceConflictError, match="changed after reconciliation"):
        pipeline._save(old, keys, observed=True, queue_alert=False)
    with pipeline.database.session() as session:
        assert session.scalar(select(JobORM)).description_clean == fresh.description_clean


def test_batch_second_save_uses_revision_of_first_save(tmp_path, monkeypatch):
    pipeline = JobIntakePipeline(_config(tmp_path))
    item, keys, _ = pipeline._evaluate_record(_full(), pipeline._stats(), use_llm=False)
    pipeline._save(item, keys, observed=True, queue_alert=False)
    first_revision = item[0].record._expected_source_revision
    with pipeline.database.session() as session:
        seen_at = session.scalar(select(JobORM)).last_seen_at
    pipeline._save(item, keys, observed=False, queue_alert=False)
    with pipeline.database.session() as session:
        job = session.scalar(select(JobORM))
        assert job.source_revision == first_revision + 1
        assert job.last_seen_at == seen_at


def test_replaced_paid_annotation_and_exact_source_remain_in_history(tmp_path, monkeypatch):
    pipeline = JobIntakePipeline(_config(tmp_path, ai=True))
    _sources(monkeypatch, _full())
    _model_stub(pipeline, monkeypatch, kind="responsibility", value="Own the product roadmap.",
                field="description_clean")
    assert pipeline.run()["persisted"] == 1
    with pipeline.database.session() as session:
        previous = deepcopy(session.scalar(select(JobORM)).annotation)
    assert previous["method"] == "two_pass"
    changed = _record(description_clean="New complete duties: own a different roadmap.")
    _sources(monkeypatch, changed)
    pipeline.config.annotation.ai_enabled = False
    assert pipeline.run()["persisted"] == 1
    with pipeline.database.session() as session:
        job = session.scalar(select(JobORM))
        assert job.annotation["method"] == "deterministic"
        events = list(session.scalars(select(JobEventORM).where(
            JobEventORM.event_type == "annotation_superseded"
        )))
        assert len(events) == 1
        assert events[0].payload["annotation"] == previous
        assert (events[0].payload["source_snapshot"]["description_clean"]
                == _full().description_clean)
        assert events[0].payload["active"] is False
        assert events[0].payload["reason"] == "source_changed"


def test_repeated_listing_observations_keep_paid_review_without_model_calls(tmp_path, monkeypatch):
    pipeline = JobIntakePipeline(_config(tmp_path, ai=True))
    _sources(monkeypatch, _full())
    calls = _model_stub(pipeline, monkeypatch, kind="responsibility",
                        value="Own the product roadmap.", field="description_clean")
    assert pipeline.run()["persisted"] == 1
    paid_calls = len(calls)
    with pipeline.database.session() as session:
        usage = deepcopy(session.scalar(select(JobORM)).annotation["usage"])
    for timestamp in ("first observation", "second observation"):
        card = _record(description_clean="Own the product roadmap.", source_metadata={
            "description_complete": False, "description_source": "listing_card",
            "listing_observed_at": timestamp, "detail_error": "Detail limit reached",
        })
        _sources(monkeypatch, card)
        stats = pipeline.run()
        assert stats["persisted"] == 1
        assert stats["annotation_extraction_calls"] == stats["annotation_review_calls"] == 0
        assert len(calls) == paid_calls
        with pipeline.database.session() as session:
            annotation = session.scalar(select(JobORM)).annotation
            assert annotation["method"] == "two_pass"
            assert annotation["status"] == "reviewed"
            assert annotation["usage"] == usage


def test_reconciliation_does_not_alias_saved_mutable_json(tmp_path, monkeypatch):
    pipeline = JobIntakePipeline(_config(tmp_path))
    _sources(monkeypatch, _full())
    pipeline.run()
    with pipeline.database.session() as session:
        saved = session.scalar(select(JobORM))
        before = deepcopy(saved.annotation)
        incoming = _record(source_metadata={"description_complete": False})
        reconcile_source(incoming, saved)
        incoming.annotation["claims"].clear()
        assert saved.annotation == before
