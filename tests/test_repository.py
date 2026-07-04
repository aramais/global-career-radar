from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from job_intake.models.job import (
    EvaluatedJob,
    FilterDecision,
    JobEvaluation,
    JobRecord,
    JobTier,
)
from job_intake.storage.database import Database
from job_intake.storage.models import JobORM
from job_intake.storage.repository import JobRepository


def _session():
    db = Database("sqlite:///:memory:")
    db.create_schema()
    return db.session()


def _evaluated(
    title: str, tier: JobTier, decision: FilterDecision = FilterDecision.PASS
) -> EvaluatedJob:
    record = JobRecord(
        source="test",
        company="Example",
        title=title,
        original_url=f"https://example.com/{title.replace(' ', '-')}",
        description_clean="Pricing and experimentation leadership.",
    )
    evaluation = JobEvaluation(decision=decision, fit_score=20.0, tier=tier, bridge_role=True)
    return EvaluatedJob(record=record, evaluation=evaluation)


# --- P3-3 retention -----------------------------------------------------------


def test_prune_removes_old_low_tier_only() -> None:
    session = _session()
    repo = JobRepository(session)

    repo.upsert_evaluated_job(_evaluated("Old C role", JobTier.C, FilterDecision.REVIEW))
    repo.upsert_evaluated_job(_evaluated("Fresh C role", JobTier.C, FilterDecision.REVIEW))
    repo.upsert_evaluated_job(_evaluated("Old A role", JobTier.A))
    session.flush()

    old = datetime.now(timezone.utc) - timedelta(days=200)
    for job in session.scalars(select(JobORM)):
        if job.title.startswith("Old"):
            job.last_seen_at = old
    session.flush()

    removed = repo.prune_low_tier(older_than_days=90, tiers=("C",))
    session.flush()

    remaining = {j.title for j in session.scalars(select(JobORM))}
    assert removed == 1  # only the old C row
    assert "Old C role" not in remaining
    assert "Fresh C role" in remaining  # too recent
    assert "Old A role" in remaining  # A-tier never pruned


# --- P3-5 alert de-duplication ------------------------------------------------


def test_alert_dedup_window_suppresses_repeat() -> None:
    session = _session()
    repo = JobRepository(session, alert_dedup_hours=24.0)

    first = repo.upsert_evaluated_job(_evaluated("Bridge role", JobTier.A))
    assert first.should_alert is True  # brand new A-tier
    repo.mark_alert_sent(first.job_uid, JobTier.A, "telegram", "msg")
    session.flush()

    # Job flips to B (no alert), then back to A -> tier_changed makes the base decision
    # want to alert again, but it is still within the 24h dedup window -> suppressed.
    repo.upsert_evaluated_job(_evaluated("Bridge role", JobTier.B))
    session.flush()
    back_to_a = repo.upsert_evaluated_job(_evaluated("Bridge role", JobTier.A))
    assert back_to_a.tier_changed is True
    assert back_to_a.should_alert is False


def test_alert_fires_again_after_window() -> None:
    session = _session()
    repo = JobRepository(session, alert_dedup_hours=24.0)

    first = repo.upsert_evaluated_job(_evaluated("Bridge role", JobTier.A))
    repo.mark_alert_sent(first.job_uid, JobTier.A, "telegram", "msg")
    session.flush()

    stored = session.scalar(select(JobORM).where(JobORM.job_uid == first.job_uid))
    stored.last_alerted_at = datetime.now(timezone.utc) - timedelta(hours=48)
    session.flush()

    repo.upsert_evaluated_job(_evaluated("Bridge role", JobTier.B))
    session.flush()
    back_to_a = repo.upsert_evaluated_job(_evaluated("Bridge role", JobTier.A))
    assert back_to_a.should_alert is True  # dedup window has elapsed
