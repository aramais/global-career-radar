from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml
from sqlalchemy import select
from test_annotation_integration import _config, _record, _sources

from job_intake.crm.repository import CRMRepository
from job_intake.models.job import JobStatus
from job_intake.pipeline import JobIntakePipeline
from job_intake.purpose import (
    PurposeConflictError,
    predict_purpose,
    serialize_purpose,
    update_human_purpose,
)
from job_intake.storage.models import JobORM, JobPurposeAssessmentORM, SourceRunORM
from job_intake.storage.repair import apply_repair, prepare_repair, rollback_repair
from job_intake.storage.source_lifecycle import record_source_snapshot


@pytest.fixture
def setup(tmp_path, monkeypatch):
    config = _config(tmp_path)
    configdir = tmp_path / "config"
    configdir.mkdir()
    path = configdir / "settings.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "database_url": config.database_url,
                "rules_path": "../rules.yaml",
                "search_profiles_path": "../profiles.yaml",
                "sources": [],
                "annotation": {"cache_dir": config.annotation.cache_dir},
                "prefilter": {"cache_dir": str(tmp_path / "prefilter-cache")},
            }
        )
    )
    pipeline = JobIntakePipeline(config)
    record = _record(
        description_clean=(
            "Responsibilities\nLead analytics projects with cross-functional teams.\n"
            "Own experiments to guide business decisions and partner with stakeholders."
        )
    )
    _sources(monkeypatch, record)
    assert pipeline.run()["persisted"] == 1
    with pipeline.database.session() as session:
        job = session.scalar(select(JobORM))
        uid = job.job_uid
        app = CRMRepository(session).create_from_job(uid, profile_id="product")
        app.notes = "Keep manual CRM notes and stage"
        session.commit()
    yield path, pipeline, uid
    pipeline.database.engine.dispose()


def test_repair_prepares_copy_then_applies_exact_patch_and_preserves_crm(setup, tmp_path):
    config, pipeline, uid = setup
    live = Path(pipeline.config.database_url.removeprefix("sqlite:///"))
    before = hashlib.sha256(live.read_bytes()).hexdigest()
    output = tmp_path / "repair"
    report = prepare_repair(config, output)
    assert report["api_calls"] == 0 and report["jobs"] == 1
    assert hashlib.sha256(live.read_bytes()).hexdigest() == before
    assert report["protected_state_unchanged"]
    result = apply_repair(output)
    assert result["status"] == "applied"
    assert apply_repair(output) == result
    # Emulate a crash after transaction commit, before the external marker write.
    (output / "applied.json").unlink()
    assert apply_repair(output) == result
    with pipeline.database.session() as session:
        job = session.get(JobORM, 1)
        assert job.raw_fit_score is not None
        assert job.version_components
        assert job.purpose_assessment.human_p2 == "unknown"
        assert (
            CRMRepository(session).list_applications()[0].notes == "Keep manual CRM notes and stage"
        )
        assert job.job_uid == uid


def test_repair_aborts_after_live_crm_drift(setup, tmp_path):
    config, pipeline, _ = setup
    output = tmp_path / "repair"
    prepare_repair(config, output)
    with pipeline.database.session() as session:
        CRMRepository(session).list_applications()[0].notes = "New user edit after preview"
        session.commit()
    with pytest.raises(ValueError, match="Database changed"):
        apply_repair(output)
    with pipeline.database.session() as session:
        assert CRMRepository(session).list_applications()[0].notes == "New user edit after preview"


def test_repair_aborts_after_gold_or_code_config_drift(setup, tmp_path):
    config, _, _ = setup
    output = tmp_path / "repair"
    prepare_repair(config, output)
    config.write_text(config.read_text() + "\nlog_level: DEBUG\n")
    with pytest.raises(ValueError, match="Code or config changed"):
        apply_repair(output)


def test_rollback_restores_assessments_and_is_idempotent(setup, tmp_path):
    config, pipeline, _ = setup
    with pipeline.database.session() as session:
        job = session.scalar(select(JobORM))
        original = job.fit_score, job.annotation, job.role_classification
    output = tmp_path / "repair"
    prepare_repair(config, output)
    apply_repair(output)
    result = rollback_repair(output)
    assert result["status"] == "rolled_back"
    assert rollback_repair(output) == result
    with pytest.raises(ValueError, match="rolled back"):
        apply_repair(output)
    with pipeline.database.session() as session:
        job = session.scalar(select(JobORM))
        assert (job.fit_score, job.annotation, job.role_classification) == original


def test_rollback_refuses_to_overwrite_new_human_actions(setup, tmp_path):
    config, pipeline, _ = setup
    output = tmp_path / "repair"
    prepare_repair(config, output)
    apply_repair(output)
    with pipeline.database.session() as session:
        CRMRepository(session).list_applications()[0].notes = "New human action after apply"
        session.commit()
    with pytest.raises(ValueError, match="Database changed after apply"):
        rollback_repair(output)
    with pipeline.database.session() as session:
        assert CRMRepository(session).list_applications()[0].notes == "New human action after apply"


def test_independent_human_purpose_and_optimistic_guard_survive_reevaluation(setup):
    _, pipeline, uid = setup
    with pipeline.database.session() as session:
        purpose = session.get(JobPurposeAssessmentORM, uid)
        version, source = purpose.version, purpose.prediction_source_hash
        update_human_purpose(
            session,
            uid,
            {
                "human_p1": "unsuitable",
                "human_p2": "suitable",
                "expected_version": version,
                "source_hash": source,
                "human_note": "Practice tasks",
            },
        )
        session.commit()
    assert pipeline.reevaluate_saved()["persisted"] == 1
    with pipeline.database.session() as session:
        purpose = session.get(JobPurposeAssessmentORM, uid)
        assert purpose.human_p1 == "unsuitable" and purpose.human_p2 == "suitable"
        assert purpose.human_note == "Practice tasks"
        with pytest.raises(PurposeConflictError):
            update_human_purpose(
                session,
                uid,
                {"expected_version": version, "source_hash": source, "human_p2": "unsuitable"},
            )


def test_confirming_p2_or_editing_note_does_not_reconfirm_stale_p1(setup):
    _, pipeline, uid = setup
    with pipeline.database.session() as session:
        row = session.get(JobPurposeAssessmentORM, uid)
        update_human_purpose(
            session,
            uid,
            {
                "expected_version": row.version,
                "source_hash": row.prediction_source_hash,
                "human_p1": "suitable",
                "human_p2": "suitable",
            },
        )
        row.prediction_source_hash = "changed-source"
        session.flush()
        update_human_purpose(
            session,
            uid,
            {
                "expected_version": row.version,
                "source_hash": row.prediction_source_hash,
                "human_p2": "suitable",
                "human_note": "Reviewed P2",
            },
        )
        result = serialize_purpose(row)
        assert result["human_p1_stale"] is True
        assert result["human_p2_stale"] is False


def test_rubric_change_requires_independent_reconfirmation_and_note_is_not_confirmation(setup):
    _, pipeline, uid = setup
    with pipeline.database.session() as session:
        row = session.get(JobPurposeAssessmentORM, uid)
        row.human_p1 = row.human_p2 = "suitable"
        row.human_source_hash = row.prediction_source_hash
        row.human_rubric_version = "previous-rubric"
        session.flush()
        assert serialize_purpose(row)["human_stale"] is True
        for changes in ({"human_note": "Clarify later"}, {"human_p2": "suitable"}):
            update_human_purpose(
                session,
                uid,
                {
                    "expected_version": row.version,
                    "source_hash": row.prediction_source_hash,
                    **changes,
                },
            )
            assert serialize_purpose(row)["human_p1_stale"] is True
        assert serialize_purpose(row)["human_p2_stale"] is False
        assert row.human_rubric_version == "previous-rubric"


def _snapshot(status="success", complete=True, seen=False):
    return {
        "source": "fixture",
        "board_id": "fixture",
        "scope": "board",
        "status": status,
        "snapshot_complete": complete,
        "seen_job_ids": ["42"] if seen else [],
        "seen_urls": ["https://example.com/job/42"] if seen else [],
    }


def test_only_complete_successes_accrue_missing_grace_and_close(setup):
    _, pipeline, _ = setup
    now = datetime(2026, 10, 1, tzinfo=UTC)
    with pipeline.database.session() as session:
        assert record_source_snapshot(session, _snapshot(), now=now) == []
        session.commit()
    with pipeline.database.session() as session:
        assert (
            record_source_snapshot(
                session, _snapshot("partial", False), now=now + timedelta(days=20)
            )
            == []
        )
        session.commit()
    with pipeline.database.session() as session:
        assert record_source_snapshot(session, _snapshot(), now=now + timedelta(days=21)) == []
        assert session.scalar(select(JobORM)).missing_verified_seconds == 0
        session.commit()
    with pipeline.database.session() as session:
        closures = record_source_snapshot(session, _snapshot(), now=now + timedelta(days=28))
        assert len(closures) == 1 and closures[0].status == JobStatus.CLOSED
        assert closures[0].source_metadata["closure_reason"] == "missing_from_complete_snapshots"
        session.commit()
    items, keys, _ = pipeline._evaluate_record(closures[0], pipeline._stats(), use_llm=False)
    pipeline._save(items, keys, observed=False, queue_alert=False)
    with pipeline.database.session() as session:
        assert session.scalar(select(JobORM)).status == "closed"
        assert (
            sum(
                row.details.get("scope") == "board"
                for row in session.scalars(
                    select(SourceRunORM),
                )
            )
            == 4
        )


def test_successful_observation_clears_missing_counters(setup):
    _, pipeline, _ = setup
    with pipeline.database.session() as session:
        record_source_snapshot(session, _snapshot())
        session.flush()
        record_source_snapshot(session, _snapshot(seen=True))
        job = session.scalar(select(JobORM))
        assert job.missing_since is None and job.missing_successes == 0
        assert job.last_complete_seen_at is not None


def test_search_results_cannot_close_absent_vacancies(setup):
    _, pipeline, _ = setup
    with pipeline.database.session() as session:
        snapshot = {**_snapshot(), "scope": "search"}
        record_source_snapshot(session, snapshot, grace_days=0)
        record_source_snapshot(session, snapshot, grace_days=0)
        assert session.scalar(select(JobORM)).missing_since is None


def test_known_ats_snapshot_never_closes_other_provider_same_source_name(setup):
    _, pipeline, _ = setup
    with pipeline.database.session() as session:
        job = session.scalar(select(JobORM))
        job.source_metadata = {"ats": "ashby", "board_name": "different"}
        snapshot = {**_snapshot(), "adapter_type": "GreenhouseAdapter"}
        record_source_snapshot(session, snapshot, grace_days=0)
        session.flush()
        record_source_snapshot(session, snapshot, grace_days=0)
        assert job.missing_since is None and job.missing_successes == 0


def test_only_verified_full_board_reopens_missing_closed_job(setup):
    _, pipeline, _ = setup
    with pipeline.database.session() as session:
        job = session.scalar(select(JobORM))
        job.status = "closed"
        job.source_metadata = {
            **job.source_metadata,
            "description_complete": False,
            "closure_reason": "missing_from_complete_snapshots",
        }
        session.commit()
    with pipeline.database.session() as session:
        assert record_source_snapshot(session, _snapshot("partial", False, seen=True)) == []
        session.commit()
    with pipeline.database.session() as session:
        reopened = record_source_snapshot(session, _snapshot(seen=True))
        assert len(reopened) == 1
        session.commit()
    items, keys, _ = pipeline._evaluate_record(reopened[0], pipeline._stats(), use_llm=False)
    pipeline._save(items, keys, observed=False, queue_alert=False)
    with pipeline.database.session() as session:
        assert session.scalar(select(JobORM)).status == "open"


def test_confirmed_closed_role_cannot_be_recommended_for_real_interview_practice(setup):
    _, pipeline, _ = setup
    record = _record(
        source_job_id="closed-practice",
        status=JobStatus.CLOSED,
        description_clean=(
            "Responsibilities\nLead analytics projects with cross-functional teams.\n"
            "Own experiments to guide business decisions and partner with stakeholders."
        ),
    )
    items, _, _ = pipeline._evaluate_record(record, pipeline._stats(), use_llm=False)
    purpose = predict_purpose(items[0])
    assert purpose["predicted_p2"] == "unsuitable"
    assert purpose["prediction_reason"]["p2"] == ["vacancy_closed"]
    assert purpose["prediction_reason"]["evidence"]


def test_generic_business_decisions_are_not_analytics_practice_evidence(setup):
    _, pipeline, _ = setup
    record = _record(
        source_job_id="generic",
        title="Program Manager",
        description_clean=(
            "Responsibilities\nLead delivery projects and partner with stakeholders.\n"
            "Guide business decisions for the commercial organization."
        ),
    )
    items, keys, _ = pipeline._evaluate_record(record, pipeline._stats(), use_llm=False)
    pipeline._save(items, keys, observed=False, queue_alert=False)
    with pipeline.database.session() as session:
        purpose = session.get(
            JobPurposeAssessmentORM, pipeline.deduplicator.build_identity(record).job_uid
        )
        assert purpose.predicted_p2 == "unknown"


def test_ats_board_alias_keeps_uid_and_crm_across_watchlist_rename(setup):
    _, pipeline, uid = setup
    original = _record(
        source="old company",
        source_metadata={
            "ats": "greenhouse",
            "board_token": "acme",
            "api_url": "https://boards-api.greenhouse.io/x",
            "description_complete": True,
        },
    )
    items, keys, _ = pipeline._evaluate_record(original, pipeline._stats(), use_llm=False)
    pipeline._save(items, keys, observed=False, queue_alert=False)
    canonical = pipeline.deduplicator.build_identity(original).job_uid
    renamed = _record(source="renamed company", source_metadata=dict(original.source_metadata))
    items, keys, _ = pipeline._evaluate_record(renamed, pipeline._stats(), use_llm=False)
    pipeline._save(items, keys, observed=False, queue_alert=False)
    assert pipeline.deduplicator.build_identity(renamed).job_uid == canonical
    with pipeline.database.session() as session:
        assert len(list(session.scalars(select(JobORM)))) == 2
        assert CRMRepository(session).list_applications()[0].job_uid == uid


def _discovery_drafts(tmp_path, record):
    from job_intake.annotation.gold import _snapshot
    from job_intake.annotation.text import TEXT_VERSION, source_hash
    from job_intake.discovery.promote import promotion_drafts

    run = tmp_path / "discovery"
    run.mkdir()
    source = _snapshot(record)
    candidate = {
        "job_uid": "discovery-source",
        "source": source,
        "source_hash": source_hash(record),
    }
    label = {
        **candidate,
        "schema": "vacancy-tags-v2",
        "text_version": TEXT_VERSION,
        "method": "blind",
        "labeled_at": "2026-10-19T12:00:00+00:00",
        "label_scope": "blind_decisions",
        "tags": {},
        "role_label": "product",
        "relevance": 3,
        "gate_outcome": "eligible",
        "notes": "Original human answer",
    }
    (run / "candidates.jsonl").write_text(json.dumps(candidate) + "\n")
    (run / "labels.jsonl").write_text(json.dumps(label) + "\n")
    return promotion_drafts(run, profile_ids=["product"])


def test_promotion_links_historical_snapshot_without_reopening_current_closed_job(setup, tmp_path):
    from job_intake.storage.promote import promote_drafts
    from job_intake.storage.source_state import record_from_saved

    config, pipeline, uid = setup
    drafts = _discovery_drafts(tmp_path, _record())
    with pipeline.database.session() as session:
        current = record_from_saved(session.scalar(select(JobORM)))
    current.status = JobStatus.CLOSED
    items, keys, _ = pipeline._evaluate_record(current, pipeline._stats(), use_llm=False)
    pipeline._save(items, keys, observed=False, queue_alert=False)
    live = Path(pipeline.config.database_url.removeprefix("sqlite:///"))
    before = hashlib.sha256(live.read_bytes()).hexdigest()
    preview = promote_drafts(config, drafts)
    assert preview["items"][0]["action"] == "link_existing_source"
    assert preview["items"][0]["human_reference_stale"] is True
    assert hashlib.sha256(live.read_bytes()).hexdigest() == before
    result = promote_drafts(config, drafts, dry_run=False)
    assert result["items"][0]["action"] == "linked"
    assert promote_drafts(config, drafts, dry_run=False)["items"][0]["action"] == "already_imported"
    with pipeline.database.session() as session:
        job = session.scalar(select(JobORM))
        assert job.job_uid == uid and job.status == "closed"
        assert job.description_clean == current.description_clean
        assert len(CRMRepository(session).list_applications()) == 1
        assert job.purpose_assessment.human_p1 == "unknown"


def test_promotion_new_source_creates_no_application_or_human_labels(setup, tmp_path):
    from job_intake.storage.promote import promote_drafts

    config, pipeline, _ = setup
    drafts = _discovery_drafts(tmp_path, _record(source_job_id="new-source"))
    result = promote_drafts(config, drafts, dry_run=False)
    assert result["items"][0]["action"] == "imported"
    with pipeline.database.session() as session:
        assert len(list(session.scalars(select(JobORM)))) == 2
        assert len(CRMRepository(session).list_applications()) == 1
        purpose = session.get(JobPurposeAssessmentORM, result["items"][0]["job_uid"])
        assert purpose.human_p1 == purpose.human_p2 == "unknown"
