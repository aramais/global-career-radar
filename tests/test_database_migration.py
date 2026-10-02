"""A saved legacy SQLite database must survive repeated additive migrations."""

import json
import sqlite3
from contextlib import closing

import pytest
from sqlalchemy import inspect, select

from job_intake.storage.database import Database
from job_intake.storage.models import AlertOutboxORM, JobEventORM, JobORM, JobProfileEvaluationORM

# This fixture is intentionally fixed SQL from the schema before search profiles,
# rather than building the legacy database from today's ORM model.
LEGACY_SCHEMA = """
CREATE TABLE jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_uid VARCHAR(64) NOT NULL UNIQUE,
    fingerprint VARCHAR(64) NOT NULL,
    content_hash VARCHAR(64) NOT NULL,
    source VARCHAR(100) NOT NULL,
    source_job_id VARCHAR(255),
    company VARCHAR(255) NOT NULL,
    title VARCHAR(255) NOT NULL,
    original_url TEXT NOT NULL,
    apply_url TEXT,
    posted_at DATETIME,
    location_text TEXT,
    remote_text TEXT,
    employment_type VARCHAR(100),
    salary_text TEXT,
    timezone_text TEXT,
    description_raw TEXT NOT NULL,
    description_clean TEXT NOT NULL,
    status VARCHAR(32) NOT NULL,
    detected_blockers JSON NOT NULL,
    matched_signals JSON NOT NULL,
    filter_decision VARCHAR(32) NOT NULL,
    fit_score FLOAT NOT NULL,
    fit_reason TEXT NOT NULL,
    tier VARCHAR(4) NOT NULL,
    bucket VARCHAR(32) NOT NULL,
    risks JSON NOT NULL,
    audit_log JSON NOT NULL,
    bridge_role BOOLEAN NOT NULL,
    last_alerted_tier VARCHAR(4),
    source_metadata JSON NOT NULL,
    first_seen_at DATETIME NOT NULL,
    last_seen_at DATETIME NOT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
);
CREATE TABLE job_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_uid VARCHAR(64) NOT NULL REFERENCES jobs(job_uid),
    event_type VARCHAR(64) NOT NULL,
    payload JSON NOT NULL,
    created_at DATETIME NOT NULL
);
"""


def create_legacy_database(path, include_previous_additions):
    timestamp = "2026-03-22 10:00:00.000000"
    data = {
        "job_uid": "legacy-job-uid",
        "fingerprint": "legacy-fingerprint",
        "content_hash": "legacy-content-hash",
        "source": "dailyremote",
        "source_job_id": "123",
        "company": "Legacy Example",
        "title": "Head of Analytics",
        "original_url": "https://example.com/jobs/123",
        "apply_url": "https://example.com/jobs/123/apply",
        "posted_at": timestamp,
        "location_text": "Brazil",
        "remote_text": "Remote",
        "employment_type": "Full Time",
        "salary_text": "$100000–$150000",
        "timezone_text": "Americas",
        "description_raw": "<p>Lead analytics.</p>",
        "description_clean": "Lead analytics.",
        "status": "open",
        "detected_blockers": "[]",
        "matched_signals": '["title:head of analytics"]',
        "filter_decision": "review",
        "fit_score": 12.5,
        "fit_reason": "Original manual review",
        "tier": "B",
        "bucket": "Bucket B",
        "risks": '["working_language_unconfirmed"]',
        "audit_log": '["legacy decision"]',
        "bridge_role": 1,
        "last_alerted_tier": "A",
        "source_metadata": json.dumps({"listing_url": "https://example.com/search"}),
        "first_seen_at": timestamp,
        "last_seen_at": timestamp,
        "created_at": timestamp,
        "updated_at": timestamp,
    }
    with closing(sqlite3.connect(path)) as connection:
        connection.executescript(LEGACY_SCHEMA)
        if include_previous_additions:
            # More recent legacy installations already have these two columns.
            connection.execute("ALTER TABLE jobs ADD COLUMN semantic_score FLOAT")
            connection.execute("ALTER TABLE jobs ADD COLUMN last_alerted_at DATETIME")
            data.update(semantic_score=2.5, last_alerted_at=timestamp)
        columns = ", ".join(data)
        placeholders = ", ".join("?" for _ in data)
        connection.execute(
            f"INSERT INTO jobs ({columns}) VALUES ({placeholders})", tuple(data.values())
        )
        connection.execute(
            "INSERT INTO job_events (job_uid, event_type, payload, created_at) VALUES (?, ?, ?, ?)",
            ("legacy-job-uid", "created", '{"tier":"B","legacy":true}', timestamp),
        )
        connection.commit()
    return data


@pytest.mark.parametrize("include_previous_additions", [False, True])
def test_additive_migration_preserves_legacy_data_and_is_idempotent(
    tmp_path, include_previous_additions
):
    path = tmp_path / "legacy.db"
    original = create_legacy_database(path, include_previous_additions)
    database = Database(f"sqlite:///{path}")
    try:
        database.create_schema()
        inspector = inspect(database.engine)
        assert {
            "jobs",
            "job_events",
            "job_feedback",
            "job_profile_evaluations",
            "alert_outbox",
        } <= set(inspector.get_table_names())
        columns = {column["name"]: column for column in inspector.get_columns("jobs")}
        assert {
            "semantic_score",
            "last_alerted_at",
            "best_profile_id",
            "best_profile_name",
            "alert_pending",
        } <= set(columns)
        assert columns["alert_pending"]["nullable"] is False
        with closing(sqlite3.connect(path)) as connection:
            connection.row_factory = sqlite3.Row
            migrated = connection.execute("SELECT * FROM jobs").fetchone()
            # The migration preserves all original values, including source dates.
            assert {key: migrated[key] for key in original} == original

        with database.session() as session:
            job = session.scalar(select(JobORM).where(JobORM.job_uid == "legacy-job-uid"))
            assert job is not None
            assert job.best_profile_id is None
            assert job.best_profile_name is None
            assert job.alert_pending is False
            assert job.source_metadata == {"listing_url": "https://example.com/search"}
            assert job.risks == ["working_language_unconfirmed"]
            assert job.profile_evaluations == []
            assert job.alert_outbox == []
            assert len(job.events) == 1
            assert job.events[0].payload == {"tier": "B", "legacy": True}

            # The newly created tables/columns can reference an existing legacy job.
            job.best_profile_id = "analytics"
            job.best_profile_name = "Analytics Leadership"
            job.alert_pending = True
            session.add(
                JobProfileEvaluationORM(
                    job_uid=job.job_uid,
                    profile_id="analytics",
                    profile_name="Analytics Leadership",
                    profile_version="version-1",
                    content_hash=job.content_hash,
                    decision="review",
                    deterministic_score=12.5,
                    fit_score=12.5,
                    tier="B",
                    bucket="Bucket B",
                    fit_reason="Original manual review",
                )
            )
            session.add(
                AlertOutboxORM(
                    job_uid=job.job_uid,
                    channel="telegram",
                    status="pending",
                    message="Saved alert",
                )
            )
            session.commit()

        with closing(sqlite3.connect(path)) as connection:
            connection.row_factory = sqlite3.Row
            # Application writes legitimately advance updated_at. The second
            # migration must preserve this newly saved state as well.
            saved_state = dict(connection.execute("SELECT * FROM jobs").fetchone())
        database.create_schema()
        with closing(sqlite3.connect(path)) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute("SELECT * FROM jobs").fetchone()
            assert row is not None
            assert dict(row) == saved_state
            assert connection.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 1
            assert connection.execute("SELECT COUNT(*) FROM job_events").fetchone()[0] == 1
            assert row["best_profile_id"] == "analytics"
            assert row["best_profile_name"] == "Analytics Leadership"
            assert row["alert_pending"] == 1

        with database.session() as session:
            job = session.scalar(select(JobORM).where(JobORM.job_uid == "legacy-job-uid"))
            event = session.scalar(select(JobEventORM))
            assert event.job is job
            assert event.payload == {"tier": "B", "legacy": True}
            assert len(job.profile_evaluations) == 1
            assert job.profile_evaluations[0].profile_id == "analytics"
            assert len(job.alert_outbox) == 1
            assert job.alert_outbox[0].message == "Saved alert"
    finally:
        database.engine.dispose()
