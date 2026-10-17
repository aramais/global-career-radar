"""Pruning must handle the retained profile matrix's original restrictive FK."""

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, inspect, select, text
from test_database_migration import create_legacy_database

from job_intake.crm import CRMRepository
from job_intake.models.job import EvaluatedJob, FilterDecision, JobEvaluation, JobRecord, JobTier
from job_intake.storage.database import Database
from job_intake.storage.models import JobORM, JobProfileEvaluationORM
from job_intake.storage.repository import JobRepository

# Frozen pre-role-first table: UNIQUE(job_uid, profile_id), with a FK that does
# not cascade. A simplified historical fixture without this FK misses the bug.
LEGACY_PROFILE_SCHEMA = """
CREATE TABLE job_profile_evaluations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_uid VARCHAR(64) NOT NULL REFERENCES jobs(job_uid),
    profile_id VARCHAR(100) NOT NULL,
    profile_name VARCHAR(255) NOT NULL,
    profile_version VARCHAR(64) NOT NULL,
    content_hash VARCHAR(64) NOT NULL,
    llm_cache_key VARCHAR(64),
    decision VARCHAR(32) NOT NULL,
    deterministic_score FLOAT NOT NULL,
    semantic_score FLOAT,
    fit_score FLOAT NOT NULL,
    tier VARCHAR(4) NOT NULL,
    bucket VARCHAR(32) NOT NULL,
    matched_signals JSON NOT NULL,
    blocker_signals JSON NOT NULL,
    reasons JSON NOT NULL,
    fit_reason TEXT NOT NULL,
    bridge_role BOOLEAN NOT NULL,
    risks JSON NOT NULL,
    audit_log JSON NOT NULL,
    updated_at DATETIME NOT NULL,
    UNIQUE(job_uid, profile_id)
);
"""


def create_database_with_profile_history(path):
    original = create_legacy_database(path, include_previous_additions=True)
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.executescript(LEGACY_PROFILE_SCHEMA)
        for uid in ("retained-recent", "retained-high-tier", "retained-archived"):
            row = {
                **original, "job_uid": uid, "original_url": "https://example.test/" + uid,
                "apply_url": "https://example.test/" + uid + "/apply",
            }
            columns = ",".join(row)
            placeholders = ",".join("?" for _ in row)
            connection.execute(
                f"INSERT INTO jobs ({columns}) VALUES ({placeholders})", tuple(row.values()),
            )
        for uid in ("legacy-job-uid", "retained-recent", "retained-high-tier", "retained-archived"):
            for profile, score in (("product-management", 30), ("data-science-management", 10)):
                row = {
                    "job_uid": uid, "profile_id": profile, "profile_name": profile,
                    "profile_version": "historical-v1", "content_hash": original["content_hash"],
                    "llm_cache_key": "old-cache", "decision": "review",
                    "deterministic_score": score, "semantic_score": None, "fit_score": score,
                    "tier": "B", "bucket": "Bucket B", "matched_signals": "[]",
                    "blocker_signals": "[]", "reasons": json.dumps(["historical reason"]),
                    "fit_reason": "Historical fit to " + profile, "bridge_role": 1,
                    "risks": "[]", "audit_log": json.dumps(["historical matrix"]),
                    "updated_at": original["updated_at"],
                }
                columns = ",".join(row)
                placeholders = ",".join("?" for _ in row)
                connection.execute(
                    f"INSERT INTO job_profile_evaluations ({columns}) VALUES ({placeholders})",
                    tuple(row.values()),
                )
        connection.commit()
        return connection.execute(
            "SELECT * FROM job_profile_evaluations WHERE job_uid != ? ORDER BY id",
            ("legacy-job-uid",),
        ).fetchall()


def test_prune_migrated_matrix_deletes_only_target_history_and_preserves_crm(tmp_path):
    path = tmp_path / "legacy-profile-retention.db"
    retained_history = create_database_with_profile_history(path)
    database = Database(f"sqlite:///{path}")
    try:
        database.create_schema()
        database.create_schema()
        with database.session() as session:
            assert session.scalar(text("PRAGMA foreign_keys")) == 1
            inspector = inspect(session.connection())
            assert inspector.get_foreign_keys("job_profile_evaluations")[0]["referred_table"] == (
                "jobs"
            )
            assert not inspector.get_foreign_keys("job_profile_evaluations")[0]["options"].get(
                "ondelete"
            )
            assert any(
                constraint["column_names"] == ["job_uid"]
                for constraint in inspector.get_unique_constraints("job_relevance_evaluations")
            )
            jobs = {job.job_uid: job for job in session.scalars(select(JobORM))}
            old = datetime.now(UTC) - timedelta(days=120)
            for uid, job in jobs.items():
                job.last_seen_at = datetime.now(UTC) if uid == "retained-recent" else old
                job.tier = "B" if uid == "retained-high-tier" else "C"
                job.archived_at = datetime.now(UTC) if uid == "retained-archived" else None
                job.role_label = "data-science-management"
                session.add(JobProfileEvaluationORM(
                    job_uid=uid, profile_id="data-science-management",
                    profile_name="Data Science Management", profile_version="current-v2",
                    content_hash=job.content_hash, decision="review", deterministic_score=0,
                    fit_score=0, tier=job.tier, bucket="Bucket C", fit_reason="Current relevance",
                ))
            session.flush()
            crm = CRMRepository(session)
            application = crm.create_from_job("legacy-job-uid", profile_id="product-management")
            crm.mark_stage(application.id, "applied", note="Historical referral")
            crm.update_application(
                application.id, notes="Keep this application", next_action="Follow up",
            )
            archived_application = crm.create_from_job(
                "retained-archived", profile_id="product-management",
            )
            application_id, archived_application_id = application.id, archived_application.id
            expected_history = [(event.to_stage, event.note) for event in application.history]
            session.commit()

        with database.session() as session:
            loaded_application = CRMRepository(session).get_application(application_id)
            assert loaded_application.job_uid == "legacy-job-uid"
            assert JobRepository(session).prune_low_tier(older_than_days=90) == 1
            assert loaded_application.job_uid is None
            session.rollback()
            assert loaded_application.job_uid == "legacy-job-uid"
            assert session.scalar(text(
                "SELECT COUNT(*) FROM job_profile_evaluations WHERE job_uid = 'legacy-job-uid'"
            )) == 2
            assert session.scalar(select(func.count()).select_from(JobProfileEvaluationORM)) == 4
            assert JobRepository(session).prune_low_tier(older_than_days=90) == 1
            assert loaded_application.job_uid is None
            session.commit()

        with database.session() as session:
            assert set(session.scalars(select(JobORM.job_uid))) == {
                "retained-recent", "retained-high-tier", "retained-archived",
            }
            assert session.scalar(select(func.count()).select_from(JobProfileEvaluationORM)) == 3
            actual_history = session.execute(text(
                "SELECT * FROM job_profile_evaluations ORDER BY id"
            )).all()
            assert [tuple(row) for row in actual_history] == retained_history
            application = CRMRepository(session).get_application(application_id)
            assert application.job_uid is None
            assert application.profile_id == "product-management"
            assert application.company == "Legacy Example"
            assert application.title == "Head of Analytics"
            assert application.stage == "applied" and application.notes == "Keep this application"
            assert application.next_action == "Follow up"
            assert [
                (event.to_stage, event.note) for event in application.history
            ] == expected_history
            assert CRMRepository(session).get_application(archived_application_id).job_uid == (
                "retained-archived"
            )
            assert JobRepository(session).prune_low_tier(older_than_days=90) == 0
            assert list(session.execute(text("PRAGMA foreign_key_check"))) == []
    finally:
        database.engine.dispose()


def test_prune_without_legacy_matrix_still_removes_current_relevance(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'current.db'}")
    try:
        database.create_schema()
        with database.session() as session:
            assert not inspect(session.connection()).has_table("job_profile_evaluations")
            repository = JobRepository(session)
            result = repository.upsert_evaluations([EvaluatedJob(
                record=JobRecord(
                    source="test", company="Example", title="Data Science Manager",
                    original_url="https://example.test/current", description_clean="Lead research.",
                ),
                evaluation=JobEvaluation(decision=FilterDecision.REVIEW, tier=JobTier.C),
                profile_id="data-science-management", profile_version="current-v2",
            )])
            job = session.scalar(select(JobORM).where(JobORM.job_uid == result.job_uid))
            job.last_seen_at = datetime.now(UTC) - timedelta(days=120)
            session.commit()
        with database.session() as session:
            assert JobRepository(session).prune_low_tier(older_than_days=90) == 1
            session.commit()
            assert session.scalar(select(func.count()).select_from(JobORM)) == 0
            assert session.scalar(select(func.count()).select_from(JobProfileEvaluationORM)) == 0
            assert list(session.execute(text("PRAGMA foreign_key_check"))) == []
    finally:
        database.engine.dispose()
