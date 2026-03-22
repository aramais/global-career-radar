from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import csv

from sqlalchemy import select

from job_intake.models.job import EvaluatedJob, JobTier
from job_intake.storage.dedup import JobDeduplicator
from job_intake.storage.models import FeedbackORM, JobEventORM, JobORM


@dataclass(slots=True)
class UpsertResult:
    job_uid: str
    is_new: bool
    changed: bool
    tier_changed: bool
    should_alert: bool


class JobRepository:
    def __init__(self, session, deduplicator: JobDeduplicator | None = None) -> None:
        self.session = session
        self.deduplicator = deduplicator or JobDeduplicator()

    def upsert_evaluated_job(self, item: EvaluatedJob) -> UpsertResult:
        identity = self.deduplicator.build_identity(item.record)
        existing = self.session.scalar(select(JobORM).where(JobORM.job_uid == identity.job_uid))
        now = datetime.now(timezone.utc)

        if existing is None:
            existing = JobORM(
                job_uid=identity.job_uid,
                fingerprint=identity.fingerprint,
                content_hash=identity.content_hash,
                source=item.record.source,
                source_job_id=item.record.source_job_id,
                company=item.record.company,
                title=item.record.title,
                original_url=item.record.original_url,
                apply_url=item.record.apply_url,
                posted_at=item.record.posted_at,
                location_text=item.record.location_text,
                remote_text=item.record.remote_text,
                employment_type=item.record.employment_type,
                salary_text=item.record.salary_text,
                timezone_text=item.record.timezone_text,
                description_raw=item.record.description_raw,
                description_clean=item.record.description_clean,
                status=item.record.status.value,
                detected_blockers=item.evaluation.blocker_signals,
                matched_signals=item.evaluation.matched_signals,
                filter_decision=item.evaluation.decision.value,
                fit_score=item.evaluation.fit_score,
                fit_reason=item.evaluation.fit_reason,
                tier=item.evaluation.tier.value,
                bucket=item.evaluation.bucket,
                risks=item.evaluation.risks,
                audit_log=item.evaluation.audit_log,
                bridge_role=item.evaluation.bridge_role,
                source_metadata=item.record.source_metadata,
            )
            self.session.add(existing)
            self.session.add(
                JobEventORM(
                    job_uid=identity.job_uid,
                    event_type="created",
                    payload={"tier": item.evaluation.tier.value},
                )
            )
            self.session.flush()
            return UpsertResult(
                job_uid=identity.job_uid,
                is_new=True,
                changed=True,
                tier_changed=True,
                should_alert=item.evaluation.tier == JobTier.A,
            )

        previous_tier = existing.tier
        previous_hash = existing.content_hash
        existing.content_hash = identity.content_hash
        existing.fingerprint = identity.fingerprint
        existing.source = item.record.source
        existing.source_job_id = item.record.source_job_id
        existing.company = item.record.company
        existing.title = item.record.title
        existing.original_url = item.record.original_url
        existing.apply_url = item.record.apply_url
        existing.posted_at = item.record.posted_at
        existing.location_text = item.record.location_text
        existing.remote_text = item.record.remote_text
        existing.employment_type = item.record.employment_type
        existing.salary_text = item.record.salary_text
        existing.timezone_text = item.record.timezone_text
        existing.description_raw = item.record.description_raw
        existing.description_clean = item.record.description_clean
        existing.status = item.record.status.value
        existing.detected_blockers = item.evaluation.blocker_signals
        existing.matched_signals = item.evaluation.matched_signals
        existing.filter_decision = item.evaluation.decision.value
        existing.fit_score = item.evaluation.fit_score
        existing.fit_reason = item.evaluation.fit_reason
        existing.bucket = item.evaluation.bucket
        existing.tier = item.evaluation.tier.value
        existing.risks = item.evaluation.risks
        existing.audit_log = item.evaluation.audit_log
        existing.bridge_role = item.evaluation.bridge_role
        existing.source_metadata = item.record.source_metadata
        existing.last_seen_at = now

        changed = previous_hash != identity.content_hash
        tier_changed = previous_tier != item.evaluation.tier.value
        if changed or tier_changed:
            self.session.add(
                JobEventORM(
                    job_uid=identity.job_uid,
                    event_type="updated",
                    payload={
                        "previous_tier": previous_tier,
                        "new_tier": item.evaluation.tier.value,
                        "changed": changed,
                    },
                )
            )
        should_alert = item.evaluation.tier == JobTier.A and (
            existing.last_alerted_tier != JobTier.A.value or tier_changed
        )
        return UpsertResult(
            job_uid=identity.job_uid,
            is_new=False,
            changed=changed,
            tier_changed=tier_changed,
            should_alert=should_alert,
        )

    def mark_alert_sent(self, job_uid: str, tier: JobTier, channel: str, message: str) -> None:
        job = self.session.scalar(select(JobORM).where(JobORM.job_uid == job_uid))
        if job is None:
            return
        job.last_alerted_tier = tier.value
        self.session.add(
            JobEventORM(
                job_uid=job_uid,
                event_type="alert_sent",
                payload={"tier": tier.value, "channel": channel, "message": message},
            )
        )

    def recent_jobs_for_digest(self, hours: int = 24) -> list[JobORM]:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        stmt = (
            select(JobORM)
            .where(JobORM.last_seen_at >= cutoff, JobORM.tier.in_(["A", "B"]))
            .order_by(JobORM.tier.asc(), JobORM.fit_score.desc())
        )
        return list(self.session.scalars(stmt))

    def list_jobs(self, limit: int = 100, tier: str | None = None) -> list[JobORM]:
        stmt = select(JobORM).order_by(JobORM.updated_at.desc()).limit(limit)
        if tier:
            stmt = stmt.where(JobORM.tier == tier)
        return list(self.session.scalars(stmt))

    def add_feedback(self, job_uid: str, label: str, note: str = "") -> None:
        self.session.add(FeedbackORM(job_uid=job_uid, label=label, note=note))

    def export_shortlisted_csv(self, output_path: Path, limit: int = 500) -> Path:
        rows = self.list_jobs(limit=limit)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    "job_uid",
                    "company",
                    "title",
                    "tier",
                    "bucket",
                    "fit_score",
                    "filter_decision",
                    "matched_signals",
                    "detected_blockers",
                    "fit_reason",
                    "apply_url",
                    "original_url",
                ],
            )
            writer.writeheader()
            for row in rows:
                if row.tier == "C":
                    continue
                writer.writerow(
                    {
                        "job_uid": row.job_uid,
                        "company": row.company,
                        "title": row.title,
                        "tier": row.tier,
                        "bucket": row.bucket,
                        "fit_score": row.fit_score,
                        "filter_decision": row.filter_decision,
                        "matched_signals": ", ".join(row.matched_signals),
                        "detected_blockers": ", ".join(row.detected_blockers),
                        "fit_reason": row.fit_reason,
                        "apply_url": row.apply_url,
                        "original_url": row.original_url,
                    }
                )
        return output_path
