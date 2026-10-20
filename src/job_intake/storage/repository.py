from __future__ import annotations

import csv
import json
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import and_, case, delete, or_, select, update
from sqlalchemy.orm import aliased, selectinload

from job_intake.alerts.digest import DigestJob
from job_intake.annotation.input_contract import annotation_input_hash
from job_intake.annotation.text import source_hash
from job_intake.filtering import refresh_eligibility_status
from job_intake.models.job import EvaluatedJob, FilterDecision, JobRecord, JobTier
from job_intake.storage.aliases import register_aliases, resolve_alias
from job_intake.storage.dedup import JobDeduplicator
from job_intake.storage.models import (
    AlertOutboxORM,
    FeedbackORM,
    JobEventORM,
    JobORM,
    JobProfileEvaluationORM,
    JobPurposeAssessmentORM,
)
from job_intake.storage.queries import classification_is_current, ranked_profile_evaluations
from job_intake.storage.source_state import (
    collection_only_source_change,
    record_from_saved,
    retained_observation_markers,
    source_snapshot,
    would_discard_source,
)
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


class JobSourceConflictError(ValueError):
    """The evaluated source is incomplete or changed while it was being evaluated."""


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


def _validate_labels(labels: dict) -> None:
    if not isinstance(labels, dict) or labels.get("schema") != "vacancy-labels-v3":
        raise ValueError("Invalid machine labels schema")
    relevance = labels.get("relevance")
    if relevance is not None and (type(relevance) is not int or relevance not in range(4)):
        raise ValueError("Invalid machine labels relevance")
    if not isinstance(labels.get("status"), str) or labels["status"] not in {
        "labeled", "rules_only", "gated", "error",
    }:
        raise ValueError("Invalid machine labels status")
    try:
        json.dumps(labels, allow_nan=False)
    except (ValueError, TypeError, OverflowError):
        raise ValueError("Machine labels must contain finite JSON values") from None


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

    def upsert_evaluated_job(
        self, item: EvaluatedJob, *, observed: bool = True,
        retain_labels_on_fallback: bool = False,
    ) -> UpsertResult:
        refresh_eligibility_status(item.evaluation)
        self.find_by_record(item.record)
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

        if item.record._source_reconciled and (
            (existing.source_revision if existing is not None else None)
            != item.record._expected_source_revision
        ):
            raise JobSourceConflictError("Source changed after reconciliation; evaluate it again")

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
            self._assessment_fields(existing, item)
            self.session.add(
                JobEventORM(
                    job_uid=identity.job_uid,
                    event_type="created",
                    payload={"tier": item.evaluation.tier.value},
                )
            )
            self.session.flush()
            register_aliases(self.session, item.record, identity.job_uid)
            return UpsertResult(
                job_uid=identity.job_uid,
                is_new=True,
                changed=True,
                tier_changed=True,
                should_alert=(item.evaluation.tier == JobTier.A
                              and item.evaluation.decision != FilterDecision.REJECT),
            )

        if would_discard_source(item.record, existing):
            raise JobSourceConflictError("Reconcile the incomplete source before evaluating it")
        retained = (self._retain_accepted_labels(item, existing)
                    if retain_labels_on_fallback else None)
        previous_revision = existing.source_revision
        conditions = [JobORM.job_uid == identity.job_uid,
                      JobORM.source_revision == previous_revision]
        if retained is not None:
            # Label-only updates keep source_revision but change updated_at.
            # Retention must not overwrite a newer accepted same-source result.
            conditions.append(JobORM.updated_at == existing.updated_at)
        claimed = self.session.execute(
            update(JobORM).where(*conditions).values(source_revision=previous_revision + 1),
            execution_options={"synchronize_session": False},
        )
        if claimed.rowcount != 1:
            raise JobSourceConflictError("Source changed before save; evaluate it again")
        existing.source_revision = previous_revision + 1
        previous_source = record_from_saved(existing)
        if existing.annotation.get("method") == "two_pass" and (
            existing.annotation != item.record.annotation
        ):
            self.session.add(JobEventORM(
                job_uid=identity.job_uid, event_type="annotation_superseded",
                payload={
                    "reason": (
                        "source_changed" if source_hash(previous_source) != source_hash(item.record)
                        else "annotation_replaced"
                    ),
                    "source_revision": previous_revision,
                    "source_snapshot": source_snapshot(previous_source),
                    "annotation": deepcopy(existing.annotation),
                    "active": False,
                },
            ))
        if source_snapshot(previous_source) != source_snapshot(item.record):
            self.session.add(JobEventORM(
                job_uid=identity.job_uid, event_type="source_superseded",
                payload={
                    "source_revision": previous_revision,
                    "source_hash": source_hash(previous_source),
                    "source_snapshot": source_snapshot(previous_source),
                },
            ))
        if retained is not None and retained[0] != retained[1]:
            self.session.add(JobEventORM(
                job_uid=identity.job_uid, event_type="labels_source_rebound",
                payload={
                    "source_revision": previous_revision,
                    "previous_source_hash": retained[0], "source_hash": retained[1],
                    "annotation_input_hash": annotation_input_hash(item.record),
                    "reason": "collection_observations_changed",
                },
            ))
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
        self._assessment_fields(existing, item)
        existing.source_metadata = item.record.source_metadata
        existing.annotation = item.record.annotation
        existing.role_label = item.profile_id
        existing.role_classification = classification
        existing.best_profile_id = item.profile_id
        existing.best_profile_name = item.profile_name or item.profile_id
        register_aliases(self.session, item.record, identity.job_uid)
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
        alert_eligible = (existing.archived_at is None and item.evaluation.tier == JobTier.A
                          and item.evaluation.decision != FilterDecision.REJECT)
        if observed and tier_changed and alert_eligible:
            existing.alert_pending = True
        should_alert = alert_eligible and (
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

    @staticmethod
    def _retain_accepted_labels(item: EvaluatedJob, saved: JobORM) -> tuple[str, str] | None:
        """Retain only compatible accepted v3 fields while legacy reevaluation proceeds.

        This opt-in is for regular shadow saves. It deliberately changes neither
        the new score/tier/purpose nor their current profile versions. A failed
        attempt remains separate, and an observation-only source pin can move
        only under the same complete-source proof used by labels-mode retention.
        """
        attempt = item.record.annotation.get("labels_v3")
        accepted = (saved.annotation or {}).get("labels_v3")
        if (not isinstance(attempt, dict) or attempt.get("status") not in {"error", "rules_only"}
                or not isinstance(accepted, dict) or accepted.get("status") != "labeled"):
            return None
        _validate_labels(attempt)
        _validate_labels(accepted)
        previous = record_from_saved(saved)
        old_hash, new_hash = source_hash(previous), source_hash(item.record)
        model_input = annotation_input_hash(previous)
        classification = saved.role_classification or {}
        if (attempt.get("source_hash") != new_hash or accepted.get("source_hash") != old_hash
                or accepted.get("annotation_input_hash") != model_input
                or item.profile_id != saved.role_label
                or accepted.get("role_label") != saved.role_label
                or classification.get("label") != saved.role_label
                or classification.get("source_hash") != old_hash):
            return None
        binding = (saved.annotation or {}).get("labels_v3_source_binding")
        if (isinstance(binding, dict) and binding.get("source_hash") == old_hash
                and binding.get("annotation_input_hash") == model_input
                and isinstance(binding.get("observation_markers"), dict)):
            previous._source_observation_markers = retained_observation_markers(
                binding["observation_markers"],
            )
        if old_hash != new_hash and not collection_only_source_change(previous, item.record):
            return None
        item.record.annotation["labels_v3"] = {**deepcopy(accepted), "source_hash": new_hash}
        item.record.annotation["labels_v3_attempt"] = deepcopy(attempt)
        if old_hash != new_hash:
            markers = {**previous._source_observation_markers,
                       **item.record._source_observation_markers}
            nested = item.record.source_metadata.get("collection_observations", {})
            markers = {key: value for key, value in retained_observation_markers(markers).items()
                       if isinstance(nested, dict) and key in nested and nested[key] == value}
            item.record.annotation["labels_v3_source_binding"] = {
                "source_hash": new_hash, "annotation_input_hash": model_input,
                "observation_markers": markers,
            }
        elif previous._source_observation_markers:
            item.record.annotation["labels_v3_source_binding"] = deepcopy(binding)
        for name in ("relevance", "labels_status", "labels_version"):
            setattr(item.evaluation, name, deepcopy(getattr(saved, name)))
        return old_hash, new_hash

    @staticmethod
    def _assessment_fields(row, item: EvaluatedJob) -> None:
        for name in ("raw_fit_score", "score_cap_reasons", "eligibility_status",
                     "eligibility_evidence_status", "relevance", "labels_status",
                     "labels_version"):
            setattr(row, name, deepcopy(getattr(item.evaluation, name)))
        labels = item.record.annotation.get("labels_v3")
        if isinstance(labels, dict) and row.labels_version is None:
            row.labels_version = JobRepository._labels_version(labels)
        row.version_components = dict(item.version_components)

    @staticmethod
    def _labels_version(labels: dict) -> str | None:
        prompt, aggregation = labels.get("prompt_version"), labels.get("aggregation_version")
        return f"{prompt}:{aggregation}" if prompt and aggregation else None

    def update_labels(
        self, job_uid: str, labels: dict, *, expected_source_hash: str | None = None,
        expected_source_revision: int | None = None, include_archived: bool = False,
    ) -> bool:
        """Save machine labels without changing source, ranking, purpose or alerts.

        The caller commits one vacancy at a time. A source change during model work
        is rejected instead of attaching predictions to a newer advertisement.
        """
        return self._write_labels(
            job_uid, labels, expected_source_hash=expected_source_hash,
            expected_source_revision=expected_source_revision,
            include_archived=include_archived, attempt=False,
        )

    def record_label_attempt(
        self, job_uid: str, labels: dict, *, expected_source_hash: str | None = None,
        expected_source_revision: int | None = None, include_archived: bool = False,
        observed: bool = False,
    ) -> bool:
        """Retain the accepted assessment; advance freshness only for observed intake."""
        return self._write_labels(
            job_uid, labels, expected_source_hash=expected_source_hash,
            expected_source_revision=expected_source_revision,
            include_archived=include_archived, attempt=True, observed=observed,
        )

    def record_collection_label_attempt(
        self, job_uid: str, record: JobRecord, labels: dict, *, expected_source_revision: int,
        include_archived: bool = False, observed: bool = False,
    ) -> bool:
        """Retain an accepted assessment across a proved observation-only change.

        The source pin, failed attempt and provenance move in one transaction.
        Matching model input alone cannot ignore changed status or unknown facts.
        """
        _validate_labels(labels)
        if labels["status"] not in {"error", "rules_only"}:
            raise ValueError("Collection preservation requires an unsuccessful label attempt")
        if type(include_archived) is not bool or type(observed) is not bool:
            raise ValueError("Observation and archive flags must be Boolean")
        if type(expected_source_revision) is not int or expected_source_revision < 1:
            raise ValueError("expected_source_revision must be a positive integer")
        job = self.session.scalar(select(JobORM).where(JobORM.job_uid == job_uid)
                                  .execution_options(populate_existing=True))
        if job is None or job.archived_at is not None and not include_archived:
            return False
        if job.source_revision != expected_source_revision:
            raise JobSourceConflictError("Source changed before labels save; label it again")
        previous = record_from_saved(job)
        old_hash, new_hash = source_hash(previous), source_hash(record)
        if labels.get("source_hash") != new_hash:
            raise JobSourceConflictError("Source changed before labels save; label it again")
        accepted = (job.annotation or {}).get("labels_v3")
        classification = job.role_classification or {}
        model_input = annotation_input_hash(previous)
        binding = (job.annotation or {}).get("labels_v3_source_binding")
        if (isinstance(binding, dict) and binding.get("source_hash") == old_hash
                and binding.get("annotation_input_hash") == model_input
                and isinstance(binding.get("observation_markers"), dict)):
            previous._source_observation_markers = retained_observation_markers(
                binding["observation_markers"],
            )
        if (
            old_hash == new_hash or not collection_only_source_change(previous, record)
            or not isinstance(accepted, dict) or accepted.get("status") != "labeled"
            or accepted.get("source_hash") != old_hash
            or accepted.get("annotation_input_hash") != model_input
            or classification.get("source_hash") != old_hash
            or classification.get("label") != job.role_label
        ):
            return False
        identity = self.deduplicator.build_identity(record)
        if identity.fingerprint != job.fingerprint:
            return False
        annotation = deepcopy(job.annotation)
        annotation["labels_v3"] = {**deepcopy(accepted), "source_hash": new_hash}
        annotation["labels_v3_attempt"] = deepcopy(labels)
        markers = {**previous._source_observation_markers, **record._source_observation_markers}
        nested = record.source_metadata.get("collection_observations", {})
        markers = {key: value for key, value in retained_observation_markers(markers).items()
                   if isinstance(nested, dict) and key in nested and nested[key] == value}
        annotation["labels_v3_source_binding"] = {
            "source_hash": new_hash, "annotation_input_hash": model_input,
            "observation_markers": markers,
        }
        now = datetime.now(UTC)
        values = {
            "source_metadata": deepcopy(record.source_metadata),
            "source_revision": expected_source_revision + 1,
            "content_hash": identity.content_hash,
            "role_classification": {**deepcopy(classification), "source_hash": new_hash,
                                    "content_hash": identity.content_hash},
            "annotation": annotation, "updated_at": now,
        }
        if observed:
            values["last_seen_at"] = now
        # Label-only writes keep source_revision but advance updated_at. Claim
        # both snapshots so a newer accepted result is never replaced by our copy.
        conditions = [JobORM.job_uid == job_uid,
                      JobORM.source_revision == expected_source_revision,
                      JobORM.updated_at == job.updated_at]
        if not include_archived:
            conditions.append(JobORM.archived_at.is_(None))
        updated = self.session.execute(update(JobORM).where(*conditions).values(**values)
                                      .execution_options(synchronize_session=False))
        if updated.rowcount != 1:
            raise JobSourceConflictError("Source changed before labels save; label it again")
        self.session.execute(update(JobProfileEvaluationORM).where(
            JobProfileEvaluationORM.job_uid == job_uid,
            JobProfileEvaluationORM.profile_id == job.role_label,
            JobProfileEvaluationORM.content_hash == job.content_hash,
            JobProfileEvaluationORM.classification_source_hash == old_hash,
        ).values(classification_source_hash=new_hash, content_hash=identity.content_hash,
                 updated_at=JobProfileEvaluationORM.updated_at)
         .execution_options(synchronize_session=False))
        self.session.add(JobEventORM(job_uid=job_uid, event_type="source_superseded", payload={
            "source_revision": expected_source_revision, "source_hash": old_hash,
            "source_snapshot": source_snapshot(previous),
        }))
        self.session.add(JobEventORM(job_uid=job_uid, event_type="labels_source_rebound", payload={
            "source_revision": expected_source_revision,
            "previous_source_hash": old_hash, "source_hash": new_hash,
            "annotation_input_hash": model_input, "reason": "collection_observations_changed",
        }))
        register_aliases(self.session, record, job_uid)
        self.session.expire(job)
        self.session.flush()
        return True

    def _write_labels(
        self, job_uid: str, labels: dict, *, expected_source_hash: str | None,
        expected_source_revision: int | None, include_archived: bool, attempt: bool,
        observed: bool = False,
    ) -> bool:
        _validate_labels(labels)
        relevance = labels.get("relevance")
        if type(include_archived) is not bool:
            raise ValueError("include_archived must be Boolean")
        if type(observed) is not bool:
            raise ValueError("observed must be Boolean")
        if expected_source_revision is not None and (
            type(expected_source_revision) is not int or expected_source_revision < 1
        ):
            raise ValueError("expected_source_revision must be a positive integer")
        if expected_source_hash is not None and (
            not isinstance(expected_source_hash, str) or not expected_source_hash
        ):
            raise ValueError("expected_source_hash must be nonempty text")
        job = self.session.scalar(select(JobORM).where(JobORM.job_uid == job_uid)
                                  .execution_options(populate_existing=True))
        if job is None or (job.archived_at is not None and not include_archived):
            return False
        current_hash = source_hash(record_from_saved(job))
        if (
            labels.get("source_hash") != current_hash
            or (expected_source_hash is not None and expected_source_hash != current_hash)
            or (expected_source_revision is not None
                and expected_source_revision != job.source_revision)
        ):
            raise JobSourceConflictError("Source changed before labels save; label it again")
        annotation = deepcopy(job.annotation or {})
        annotation["labels_v3_attempt" if attempt else "labels_v3"] = deepcopy(labels)
        values = {} if attempt else {
            "relevance": relevance, "labels_status": labels["status"],
            "labels_version": self._labels_version(labels),
        }
        if observed:
            values["last_seen_at"] = datetime.now(UTC)
        conditions = [JobORM.job_uid == job_uid, JobORM.source_revision == job.source_revision]
        if not include_archived:
            conditions.append(JobORM.archived_at.is_(None))
        saved = self.session.execute(update(JobORM).where(*conditions).values(
            annotation=annotation, updated_at=datetime.now(UTC), **values,
        ).execution_options(synchronize_session=False))
        if saved.rowcount != 1:
            raise JobSourceConflictError("Source changed before labels save; label it again")
        if not attempt:
            self.session.execute(update(JobProfileEvaluationORM).where(
                JobProfileEvaluationORM.job_uid == job_uid,
                JobProfileEvaluationORM.content_hash == job.content_hash,
                JobProfileEvaluationORM.profile_id == job.role_label,
                JobProfileEvaluationORM.classification_source_hash == current_hash,
            ).values(**values).execution_options(synchronize_session="fetch"))
        self.session.expire(job)
        self.session.flush()
        return True

    def upsert_evaluations(
        self,
        items: list[EvaluatedJob],
        cache_keys: dict[str, str] | None = None,
        *,
        observed: bool = True,
        queue_alert: bool = False,
        retain_labels_on_fallback: bool = False,
    ) -> UpsertResult:
        """Atomically save one assigned role and its single relevance evaluation."""
        if len(items) != 1:
            raise ValueError("Exactly one role relevance evaluation is required")
        best = items[0]
        result = self.upsert_evaluated_job(
            best, observed=observed, retain_labels_on_fallback=retain_labels_on_fallback,
        )
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
            self._assessment_fields(row, item)
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
        from job_intake.purpose import save_prediction

        save_prediction(self.session, result.job_uid, best)
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
        found = self.session.scalar(select(JobORM).where(JobORM.job_uid == identity.job_uid))
        if found is None:
            alias = resolve_alias(self.session, record)
            if alias is not None:
                found = self.session.scalar(select(JobORM).where(JobORM.job_uid == alias))
                if found is not None:
                    record._canonical_job_uid = found.job_uid
        return found

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
            ~select(JobPurposeAssessmentORM.job_uid).where(
                JobPurposeAssessmentORM.job_uid == JobORM.job_uid,
                or_(JobPurposeAssessmentORM.human_p1 != "unknown",
                    JobPurposeAssessmentORM.human_p2 != "unknown"),
            ).exists(),
        )
        targets = select(JobORM.job_uid).where(*conditions)
        # Historical profile matrices retain their original non-cascading FK.
        # They are no longer ORM-mapped, so remove only the pruned jobs' rows
        # explicitly while preserving all retained/archived vacancy history.
        if inspect(self.session.connection()).has_table("job_profile_evaluations"):
            legacy = table("job_profile_evaluations", column("job_uid"))
            self.session.execute(delete(legacy).where(legacy.c.job_uid.in_(targets)))
        for model in (JobEventORM, FeedbackORM, JobProfileEvaluationORM, AlertOutboxORM,
                      JobPurposeAssessmentORM):
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
