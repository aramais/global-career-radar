"""Source retention must not erase the user's application and contact history."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, text
from test_database_migration import create_legacy_database

from job_intake.crm import CRMRepository
from job_intake.crm.models import ApplicationORM, StageEventORM
from job_intake.models.job import (
    EvaluatedJob,
    FilterDecision,
    JobEvaluation,
    JobRecord,
    JobTier,
)
from job_intake.storage.database import Database
from job_intake.storage.models import (
    AlertOutboxORM,
    FeedbackORM,
    JobEventORM,
    JobORM,
    JobProfileEvaluationORM,
)
from job_intake.storage.repository import JobRepository


@pytest.mark.parametrize("schema", ["current", "legacy", "legacy_previous_additions"])
def test_prune_unlinks_source_and_preserves_application_history_and_contacts(tmp_path, schema):
    path = tmp_path / "retention.db"
    if schema != "current":
        create_legacy_database(
            path, include_previous_additions=schema == "legacy_previous_additions"
        )
    database = Database(f"sqlite:///{path}")
    database.create_schema()
    database.create_schema()
    with database.session() as session:
        assert session.scalar(text("PRAGMA foreign_keys")) == 1
        intake = JobRepository(session)
        if schema == "current":
            intake.upsert_evaluations(
                [
                    EvaluatedJob(
                        JobRecord(
                            source="test",
                            company="Acme",
                            title="Head of Analytics",
                            original_url="https://example.org/jobs/1",
                            description_clean="Lead team",
                        ),
                        JobEvaluation(decision=FilterDecision.REVIEW, tier=JobTier.C),
                        profile_id="analytics",
                        profile_version="v1",
                    ),
                ]
            )
        job = session.scalar(select(JobORM))
        job.tier = "C"
        job.last_seen_at = datetime.now(UTC) - timedelta(days=100)
        session.add(AlertOutboxORM(job_uid=job.job_uid, status="pending", message="Previous alert"))
        intake.add_feedback(job.job_uid, "useful", "Saved historical feedback")
        session.flush()
        crm = CRMRepository(session)
        application = crm.create_from_job(job.job_uid, profile_id="analytics")
        crm.mark_stage(
            application.id,
            "applied",
            occurred_at="2026-10-01T12:00:00Z",
            note="Via referral",
        )
        contact = crm.upsert_contact("Alex", company=job.company, email="alex@example.org")
        crm.link_contact(application.id, contact.id, "referral")
        crm.update_application(
            application.id,
            channel="referral",
            referral_state="referred",
            notes="Do not lose personal notes",
            next_action="Follow up",
            next_action_due="2026-10-07",
        )
        application_id = application.id
        expected_history = [
            (event.to_stage, event.note, event.occurred_at) for event in application.history
        ]
        expected_job_fields = (application.company, application.title, application.url)
        session.commit()

    with database.session() as session:
        assert JobRepository(session).prune_low_tier(older_than_days=90) == 1
        session.commit()

    with database.session() as session:
        application = CRMRepository(session).get_application(application_id)
        assert application is not None
        assert application.job_uid is None
        assert (application.company, application.title, application.url) == expected_job_fields
        assert application.channel == "referral"
        assert application.referral_state == "referred"
        assert application.notes == "Do not lose personal notes"
        assert application.next_action == "Follow up"
        assert application.stage == "applied"
        actual_history = [
            (event.to_stage, event.note, event.occurred_at) for event in application.history
        ]

        # SQLite returns datetimes without tzinfo, so compare the explicit stored values.
        def normalize(events):
            return [
                (stage, note, moment.replace(tzinfo=None) if moment else None)
                for stage, note, moment in events
            ]

        assert normalize(actual_history) == normalize(expected_history)
        assert len(application.contact_links) == 1
        assert application.contact_links[0].contact.email == "alex@example.org"
        assert session.scalar(select(func.count()).select_from(ApplicationORM)) == 1
        assert session.scalar(select(func.count()).select_from(StageEventORM)) == 2
        for table in (JobORM, JobEventORM, FeedbackORM, JobProfileEvaluationORM, AlertOutboxORM):
            assert session.scalar(select(func.count()).select_from(table)) == 0
        assert list(session.execute(text("PRAGMA foreign_key_check"))) == []
    database.engine.dispose()
