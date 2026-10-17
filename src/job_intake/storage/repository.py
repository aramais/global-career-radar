from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import and_, case, delete, or_, select, update
from sqlalchemy.orm import aliased, selectinload

from job_intake.alerts.digest import DigestJob
from job_intake.annotation.text import source_hash
from job_intake.models.job import EvaluatedJob, JobRecord, JobTier
from job_intake.storage.dedup import JobDeduplicator
from job_intake.storage.models import (
    AlertOutboxORM,
    FeedbackORM,
    JobEventORM,
    JobORM,
    JobProfileEvaluationORM,
)
from job_intake.storage.queries import classification_is_current, ranked_profile_evaluations
from job_intake.utils.csv import spreadsheet_safe_value


@dataclass(slots=True)
class UpsertResult:
    job_uid: str
    is_new: bool
    changed: bool
    tier_changed: bool
    should_alert: bool


class JobArchiveConflictError(ValueError):
    """The archive state changed after the caller loaded the vacancy."""


def validate_job_uids(job_uids: list[str] | None) -> list[str] | None:
    """An explicit selection must never become an unrestricted query."""
    if job_uids is None:
        return None
    if (
        not isinstance(job_uids, list) or not job_uids
        or not all(isinstance(uid, str) and uid.strip() for uid in job_uids)
    ):
        raise ValueError("Selected job UIDs must be a nonempty list of nonblank IDs")
    if len(set(job_uids)) != len(job_uids):
        raise ValueError("Selected job UIDs must be unique")
    return list(job_uids)


class JobRepository:
    def __init__(
        self,
        session,
        deduplicator: JobDeduplicator | None = None,
        alert_dedup_hours: float = 0.0,
    ) -> None:
        self.session = session
        self.deduplicator = deduplicator or JobDeduplicator()
        self.alert_dedup_hours = alert_dedup_hours

    def upsert_evaluated_job(self, item: EvaluatedJob, *, observed: bool = True) -> UpsertResult:
        identity = self.deduplicator.build_identity(item.record)
        classification = dict(getattr(item, "role_classification", None) or {
            "label": item.profile_id,
            "name": item.profile_name or item.profile_id,
            "version": "assigned-v1",
            "source_hash": source_hash(item.record),
            "content_hash": identity.content_hash,
            "method": "assigned",
            "reason": "Explicit role assignment supplied by the importing caller.",
        })
        if (
            classification.get("label") != item.profile_id
            or classification.get("content_hash") != identity.content_hash
            or classification.get("source_hash") != source_hash(item.record)
            or not isinstance(classification.get("version"), str)
            or not classification.get("version")
        ):
            raise ValueError("Role classification must match the evaluation and source content")
        existing = self.session.scalar(select(JobORM).where(JobORM.job_uid == identity.job_uid))
        now = datetime.now(UTC)

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
                posted_at=(
                    item.record.posted_at.astimezone(UTC)
                    if item.record.posted_at and item.record.posted_at.tzinfo
                    else item.record.posted_at
                ),
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
                semantic_score=item.evaluation.semantic_score,
                fit_reason=item.evaluation.fit_reason,
                tier=item.evaluation.tier.value,
                bucket=item.evaluation.bucket,
                risks=item.evaluation.risks,
                audit_log=item.evaluation.audit_log,
                bridge_role=item.evaluation.bridge_role,
                source_metadata=item.record.source_metadata,
                annotation=item.record.annotation,
                role_label=item.profile_id,
                role_classification=classification,
                best_profile_id=item.profile_id,
                best_profile_name=item.profile_name or item.profile_id,
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
        existing.posted_at = (
            item.record.posted_at.astimezone(UTC)
            if item.record.posted_at and item.record.posted_at.tzinfo
            else item.record.posted_at
        )
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
        existing.semantic_score = item.evaluation.semantic_score
        existing.fit_reason = item.evaluation.fit_reason
        existing.bucket = item.evaluation.bucket
        existing.tier = item.evaluation.tier.value
        existing.risks = item.evaluation.risks
        existing.audit_log = item.evaluation.audit_log
        existing.bridge_role = item.evaluation.bridge_role
        existing.source_metadata = item.record.source_metadata
        existing.annotation = item.record.annotation
        existing.role_label = item.profile_id
        existing.role_classification = classification
        existing.best_profile_id = item.profile_id
        existing.best_profile_name = item.profile_name or item.profile_id
        if observed:
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
        if tier_changed and item.evaluation.tier == JobTier.A and existing.archived_at is None:
            existing.alert_pending = True
        should_alert = existing.archived_at is None and item.evaluation.tier == JobTier.A and (
            existing.last_alerted_tier != JobTier.A.value or existing.alert_pending
        )
        if should_alert and not self._dedup_window_elapsed(existing.last_alerted_at, now):
            should_alert = False
        return UpsertResult(
            job_uid=identity.job_uid,
            is_new=False,
            changed=changed,
            tier_changed=tier_changed,
            should_alert=should_alert,
        )

    def upsert_evaluations(
        self,
        items: list[EvaluatedJob],
        cache_keys: dict[str, str] | None = None,
        *,
        observed: bool = True,
        queue_alert: bool = False,
    ) -> UpsertResult:
        """Atomically save one assigned role and its single relevance evaluation."""
        if len(items) != 1:
            raise ValueError("Exactly one role relevance evaluation is required")
        best = items[0]
        result = self.upsert_evaluated_job(best, observed=observed)
        self.session.flush()
        identity = self.deduplicator.build_identity(best.record)
        for item in items:
            row = self.session.scalar(
                select(JobProfileEvaluationORM).where(
                    JobProfileEvaluationORM.job_uid == result.job_uid,
                )
            )
            if row is None:
                row = JobProfileEvaluationORM(job_uid=result.job_uid, profile_id=item.profile_id)
                self.session.add(row)
            row.profile_id = item.profile_id
            row.profile_name = item.profile_name or item.profile_id
            row.profile_version = item.profile_version
            row.content_hash = identity.content_hash
            job = self.session.scalar(select(JobORM).where(JobORM.job_uid == result.job_uid))
            row.classification_version = job.role_classification["version"]
            row.classification_source_hash = job.role_classification["source_hash"]
            row.llm_cache_key = (cache_keys or {}).get(item.profile_id)
            for name in (
                "deterministic_score",
                "semantic_score",
                "fit_score",
                "fit_reason",
                "bridge_role",
                "bucket",
                "matched_signals",
                "blocker_signals",
                "reasons",
                "risks",
                "audit_log",
            ):
                value = getattr(item.evaluation, name)
                setattr(row, name, list(value) if isinstance(value, list) else value)
            row.decision = item.evaluation.decision.value
            row.tier = item.evaluation.tier.value
        self.session.flush()
        if queue_alert and result.should_alert:
            from job_intake.alerts.digest import build_instant_alert

            job = self.session.scalar(select(JobORM).where(JobORM.job_uid == result.job_uid))
            outbox = self.session.scalar(
                select(AlertOutboxORM).where(
                    AlertOutboxORM.job_uid == result.job_uid,
                    AlertOutboxORM.channel == "telegram",
                )
            )
            if outbox is None:
                outbox = AlertOutboxORM(job_uid=result.job_uid, channel="telegram", attempts=0)
                self.session.add(outbox)
            outbox.status = "pending"
            outbox.message = build_instant_alert(job)
            outbox.last_error = None
        return result

    def find_profile_evaluation(
        self, record: JobRecord, profile_id: str
    ) -> JobProfileEvaluationORM | None:
        job = self.find_by_record(record)
        if job is None:
            return None
        return self.session.scalar(
            select(JobProfileEvaluationORM).where(
                JobProfileEvaluationORM.job_uid == job.job_uid,
                JobProfileEvaluationORM.profile_id == profile_id,
            )
        )

    def profile_evaluations_for_job(self, job_uid: str) -> dict[str, JobProfileEvaluationORM]:
        return {
            row.profile_id: row for row in self.session.scalars(
                select(JobProfileEvaluationORM).where(JobProfileEvaluationORM.job_uid == job_uid)
            )
        }

    def _dedup_window_elapsed(self, last_alerted_at: datetime | None, now: datetime) -> bool:
        """True when enough time has passed since the last alert to alert again."""
        if self.alert_dedup_hours <= 0 or last_alerted_at is None:
            return True
        # SQLite returns naive datetimes even for timezone=True columns; assume UTC.
        if last_alerted_at.tzinfo is None:
            last_alerted_at = last_alerted_at.replace(tzinfo=UTC)
        return now - last_alerted_at >= timedelta(hours=self.alert_dedup_hours)

    def find_by_record(self, record: JobRecord) -> JobORM | None:
        identity = self.deduplicator.build_identity(record)
        return self.session.scalar(select(JobORM).where(JobORM.job_uid == identity.job_uid))

    def mark_alert_sent(self, job_uid: str, tier: JobTier, channel: str, message: str) -> None:
        job = self.session.scalar(select(JobORM).where(JobORM.job_uid == job_uid))
        if job is None:
            return
        job.last_alerted_tier = tier.value
        job.last_alerted_at = datetime.now(UTC)
        job.alert_pending = False
        self.session.add(
            JobEventORM(
                job_uid=job_uid,
                event_type="alert_sent",
                payload={"tier": tier.value, "channel": channel, "message": message},
            )
        )

    def recent_jobs_for_digest(
        self, hours: int = 24, *, active_profile_versions: dict[str, str] | None = None
    ) -> list[JobORM | DigestJob]:
        cutoff = datetime.now(UTC) - timedelta(hours=hours)
        stmt = (
            select(JobORM)
            .where(JobORM.last_seen_at >= cutoff, JobORM.archived_at.is_(None))
            .order_by(JobORM.tier.asc(), JobORM.fit_score.desc())
        )
        jobs = []
        for job in self.session.scalars(stmt.options(selectinload(JobORM.profile_evaluations))):
            profiles = [
                p
                for p in job.profile_evaluations
                if (
                    p.profile_id == job.role_label
                    and job.role_label != "other"
                    and classification_is_current(job, p)
                    and (active_profile_versions is None
                         or active_profile_versions.get(p.profile_id) == p.profile_version)
                    and p.content_hash == job.content_hash
                    and p.tier in ("A", "B")
                    and p.decision != "reject"
                )
            ]
            if not profiles:
                continue
            best = profiles[0]
            jobs.append(
                DigestJob(
                    company=job.company,
                    title=job.title,
                    tier=best.tier,
                    fit_score=best.fit_score,
                    risks=best.risks,
                    matched_signals=best.matched_signals,
                    fit_reason=best.fit_reason,
                    apply_url=job.apply_url,
                    original_url=job.original_url,
                )
            )
        jobs.sort(key=lambda job: (job.tier, -job.fit_score, job.title))
        return jobs

    def list_jobs(
        self, limit: int | None = 100, tier: str | None = None, *, archived: bool | None = None,
        job_uids: list[str] | None = None,
    ) -> list[JobORM]:
        selection = validate_job_uids(job_uids)
        stmt = select(JobORM).options(selectinload(JobORM.profile_evaluations)).order_by(
            JobORM.updated_at.desc(), JobORM.job_uid
        )
        if selection is not None:
            stmt = stmt.where(JobORM.job_uid.in_(selection))
        if archived is not None:
            stmt = stmt.where(
                JobORM.archived_at.is_not(None) if archived else JobORM.archived_at.is_(None)
            )
        if limit is not None:
            stmt = stmt.limit(limit)
        if tier:
            stmt = stmt.where(JobORM.tier == tier)
        return list(self.session.scalars(stmt))

    def set_archived(
        self, job_uid: str, archived: bool, *, expected_version: int,
    ) -> JobORM | None:
        if type(archived) is not bool:
            raise ValueError("archived must be Boolean")
        if type(expected_version) is not int or expected_version < 1:
            raise ValueError("expected_version must be a positive integer")
        job = self.session.scalar(
            select(JobORM).where(JobORM.job_uid == job_uid)
            .execution_options(populate_existing=True)
        )
        if job is None:
            return None
        if job.archive_version != expected_version:
            raise JobArchiveConflictError("Archive version changed")
        if (job.archived_at is not None) == archived:
            return job
        result = self.session.execute(
            update(JobORM).where(
                JobORM.job_uid == job_uid, JobORM.archive_version == expected_version,
            ).values(
                archived_at=datetime.now(UTC) if archived else None,
                archive_version=expected_version + 1,
                **({"alert_pending": False} if archived else {}),
            ).execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            raise JobArchiveConflictError("Archive version changed")
        self.session.expire(job)
        self.session.add(JobEventORM(
            job_uid=job_uid, event_type="archived" if archived else "restored",
            payload={"archive_version": expected_version + 1},
        ))
        if archived:
            self.session.execute(
                update(AlertOutboxORM).where(
                    AlertOutboxORM.job_uid == job_uid, AlertOutboxORM.status == "pending",
                ).values(status="cancelled")
            )
        self.session.flush()
        return job

    def add_feedback(self, job_uid: str, label: str, note: str = "") -> None:
        self.session.add(FeedbackORM(job_uid=job_uid, label=label, note=note))

    def prune_low_tier(self, older_than_days: int, tiers: tuple[str, ...] = ("C",)) -> int:
        """Delete low-tier jobs not seen for ``older_than_days`` days.

        Delete dependencies in batches. Database SET NULL keeps CRM applications
        and their history; no source relationship is traversed for each job.
        """
        from sqlalchemy import column, inspect, table

        # Sessions disable autoflush. Preserve pending CRM edits before refreshing
        # objects affected by database-level SET NULL and bulk deletes.
        self.session.flush()
        cutoff = datetime.now(UTC) - timedelta(days=older_than_days)
        conditions = (
            JobORM.last_seen_at < cutoff,
            JobORM.tier.in_(list(tiers)),
            JobORM.archived_at.is_(None),
        )
        targets = select(JobORM.job_uid).where(*conditions)
        # Historical profile matrices retain their original non-cascading FK.
        # They are no longer ORM-mapped, so remove only the pruned jobs' rows
        # explicitly while preserving all retained/archived vacancy history.
        if inspect(self.session.connection()).has_table("job_profile_evaluations"):
            legacy = table("job_profile_evaluations", column("job_uid"))
            self.session.execute(delete(legacy).where(legacy.c.job_uid.in_(targets)))
        for model in (JobEventORM, FeedbackORM, JobProfileEvaluationORM, AlertOutboxORM):
            self.session.execute(
                delete(model).where(model.job_uid.in_(targets))
                .execution_options(synchronize_session="fetch")
            )
        result = self.session.execute(
            delete(JobORM).where(*conditions).execution_options(synchronize_session="fetch")
        )
        # Loaded applications may still hold the source UID that the database unlinked.
        self.session.expire_all()
        return result.rowcount

    def export_shortlisted_csv(
        self,
        output_path: Path,
        limit: int = 500,
        *,
        wide: bool = False,
        include_rejected: bool = False,
        profile_id: str | None = None,
        active_profile_versions: dict[str, str] | None = None,
    ) -> Path:
        # Resolve active evaluations before filtering/limiting: the saved aggregate
        # may belong to a disabled profile or criteria that have since changed.
        if type(limit) is not int or limit < 0:
            raise ValueError("limit must be a nonnegative integer")
        ranked = ranked_profile_evaluations(active_profile_versions, profile_id)
        selected_evaluation = aliased(JobProfileEvaluationORM, ranked)
        current_sql = selected_evaluation.id.is_not(None)
        tier_sql = selected_evaluation.tier
        score_sql = selected_evaluation.fit_score
        decision_sql = selected_evaluation.decision
        query = (
            select(JobORM, selected_evaluation)
            .outerjoin(ranked, and_(
                ranked.c.job_uid == JobORM.job_uid, ranked.c.evaluation_rank == 1,
            ))
            .where(JobORM.archived_at.is_(None))
            .options(selectinload(JobORM.profile_evaluations))
        )
        if profile_id:
            query = query.where(JobORM.role_label == profile_id)
        if not include_rejected:
            query = query.where(or_(~current_sql, decision_sql != "reject"))
        if not wide:
            query = query.where(current_sql, JobORM.role_label != "other",
                                tier_sql.in_(("A", "B")))
        query = query.order_by(
            case((~current_sql, 3), else_=case(
                {"A": 0, "B": 1, "C": 2}, value=tier_sql, else_=3,
            )),
            case((current_sql, score_sql), else_=0).desc(),
            JobORM.last_seen_at.desc(), JobORM.job_uid,
        ).limit(limit)
        entries = []
        for row, selected in self.session.execute(query):
            entries.append((row, selected, selected is not None))
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
                    "role_label",
                    "role_name",
                    "classification_reason",
                    "classification_state",
                    "risks",
                    "location",
                    "remote",
                    "salary",
                    "description",
                    "evaluation_state",
                ],
            )
            writer.writeheader()
            for row, selected, current in entries:
                evaluation = selected
                scored = current and row.role_label != "other"
                classified = classification_is_current(
                    row, next(iter(row.profile_evaluations), None),
                )
                values = {
                        "job_uid": row.job_uid,
                        "company": row.company,
                        "title": row.title,
                        "tier": evaluation.tier if scored else "",
                        "bucket": evaluation.bucket if scored else "",
                        "fit_score": evaluation.fit_score if scored else "",
                        "filter_decision": selected.decision
                        if current
                        else "",
                        "matched_signals": ", ".join(evaluation.matched_signals) if current else "",
                        "detected_blockers": ", ".join(
                            selected.blocker_signals
                        )
                        if current
                        else "",
                        "fit_reason": evaluation.fit_reason if current else "",
                        "apply_url": row.apply_url,
                        "original_url": row.original_url,
                        "role_label": row.role_label or "",
                        "role_name": (row.role_classification or {}).get("name", ""),
                        "classification_reason": (row.role_classification or {}).get("reason", ""),
                        "classification_state": ("current" if classified
                                                 else "needs_reclassification"),
                        "risks": ", ".join(evaluation.risks) if current else "",
                        "location": row.location_text,
                        "remote": row.remote_text,
                        "salary": row.salary_text,
                        "description": row.description_clean or row.description_raw,
                        "evaluation_state": ("unscored" if classified and row.role_label == "other"
                                             else "current" if current else "needs_reevaluation"),
                    }
                writer.writerow({
                    key: spreadsheet_safe_value(value) for key, value in values.items()
                })
        return output_path
