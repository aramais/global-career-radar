from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

from sqlalchemy import select

from job_intake.adapters.factory import build_adapter
from job_intake.alerts.digest import build_daily_digest, build_instant_alert
from job_intake.alerts.telegram import TelegramNotifier
from job_intake.annotation.prefilter import VacancyPrefilter
from job_intake.annotation.progress import ProgressCallback, emit_progress
from job_intake.annotation.service import VacancyAnnotator, apply_annotation_constraints
from job_intake.annotation.text import role_description, source_hash
from job_intake.classification import RoleClassifier
from job_intake.config.settings import (
    AppConfig,
    SourceDefinition,
    load_app_config,
    load_yaml_mapping,
)
from job_intake.models.job import EvaluatedJob, FilterDecision, JobRecord, JobStatus, JobTier
from job_intake.profiles import SearchStream, load_streams, with_other_stream
from job_intake.scoring.llm import BatchReranker, build_reranker, should_skip_llm
from job_intake.scoring.tiering import finalize_tier
from job_intake.storage.database import Database
from job_intake.storage.dedup import JobDeduplicator
from job_intake.storage.models import AlertOutboxORM
from job_intake.storage.queries import classification_is_current
from job_intake.storage.repository import JobRepository, validate_job_uids
from job_intake.utils.logging import configure_logging
from job_intake.utils.text import stable_hash

LOGGER = logging.getLogger(__name__)


class JobIntakePipeline:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        common_rules = load_yaml_mapping(config.rules_path)
        self.streams = with_other_stream(
            load_streams(common_rules, load_yaml_mapping(config.search_profiles_path)),
            common_rules,
        )
        self.classifier = RoleClassifier(self.streams, config.annotation)
        self.database = Database(config.database_url)
        self.database.create_schema()
        self.reranker = build_reranker(config.llm)
        self.batch_reranker = (
            BatchReranker(config.llm) if config.llm.enabled and config.llm.batch_enabled else None
        )
        self.telegram = TelegramNotifier(config.telegram)
        self.deduplicator = JobDeduplicator()
        self.annotator = VacancyAnnotator(config.annotation)
        self.prefilter = VacancyPrefilter(config.prefilter)

    @property
    def profile_versions(self) -> dict[str, str]:
        return {stream.id: stream.version for stream in self.streams}

    def _source_with_queries(self, source: SourceDefinition) -> SourceDefinition:
        params = dict(source.params)
        if source.type == "dailyremote" and params.get("use_profile_queries"):
            template = params.get(
                "search_url_template", "https://dailyremote.com/remote-jobs?search={query}"
            )
            keywords = dict.fromkeys(k for stream in self.streams for k in stream.keywords)
            params["search_urls"] = [template.replace("{query}", quote_plus(k)) for k in keywords]
        return replace(source, params=params)

    @staticmethod
    def _stats() -> dict[str, Any]:
        return {
            "ingested": 0,
            "persisted": 0,
            "evaluations": 0,
            "alerts": 0,
            "source_errors": 0,
            "record_errors": 0,
            "alert_errors": 0,
            "llm_calls": 0,
            "llm_cache_hits": 0,
            "llm_skipped": 0,
            "llm_batched": 0,
            "llm_batch_pending": 0,
            "llm_batch_errors": 0,
            "annotation_extraction_calls": 0,
            "annotation_review_calls": 0,
            "annotation_cache_hits": 0,
            "annotation_errors": 0,
            "classification_calls": 0,
            "classification_cache_hits": 0,
            "classification_errors": 0,
            "prefilter_screen_calls": 0,
            "prefilter_verify_calls": 0,
            "prefilter_cache_hits": 0,
            "prefilter_errors": 0,
            "prefilter_suppressed": 0,
            "prefilter_full_review": 0,
            "prefilter_local_skipped": 0,
            "errors": [],
        }

    @staticmethod
    def _error(stats: dict[str, Any], kind: str, label: str, exc: Any) -> None:
        stats[kind] += 1
        message = f"{label}: {exc}"
        stats["errors"].append(message)
        LOGGER.warning("%s %s", kind, message)

    def run(self) -> dict[str, Any]:
        stats = self._stats()
        if self.config.llm.enabled:
            prompt = Path(self.config.llm.prompt_path).read_text(encoding="utf-8")
            self.reranker.prompt_template = prompt
            if self.batch_reranker is not None:
                self.batch_reranker.prompt_template = prompt
        pending_groups: dict[
            str, tuple[list[EvaluatedJob], dict[str, str], list[EvaluatedJob]]
        ] = {}
        for source in self.config.sources:
            if not source.enabled:
                continue
            try:
                adapter = build_adapter(self._source_with_queries(source))
                records = adapter.fetch_jobs()
            except Exception as exc:
                self._error(stats, "source_errors", source.name, exc)
                continue
            for error in getattr(adapter, "errors", []):
                self._error(stats, "source_errors", source.name, error)
            stats["ingested"] += len(records)
            for record in records:
                try:
                    items, keys, pending = self._evaluate_record(record, stats, use_llm=True)
                    # Commit before any optional batch wait or notification.
                    self._save(items, keys, observed=True, queue_alert=not pending)
                    stats["persisted"] += 1
                    stats["evaluations"] += len(items)
                    job_uid = self.deduplicator.build_identity(record).job_uid
                    if pending:
                        pending_groups[job_uid] = (items, keys, pending)
                    else:
                        pending_groups.pop(job_uid, None)
                except Exception as exc:
                    self._error(stats, "record_errors", record.original_url or record.title, exc)
        self._finish_batches(pending_groups, stats, queue_alert=True)
        self._dispatch_alerts(stats)
        LOGGER.info("run_complete %s", json.dumps(stats, ensure_ascii=False))
        return stats

    def _finish_batches(
        self,
        pending_groups: dict[str, tuple[list[EvaluatedJob], dict[str, str], list[EvaluatedJob]]],
        stats: dict[str, Any],
        *,
        queue_alert: bool,
    ) -> None:
        """Resolve pending evaluations identically for intake and saved-data runs."""
        pending_llm = [item for _, _, pending in pending_groups.values() for item in pending]
        if pending_llm:
            try:
                self.batch_reranker.rerank_many(pending_llm)
                stats["llm_batched"] += len(pending_llm)
                batch_stats = getattr(self.batch_reranker, "last_stats", {})
                stats["llm_batch_pending"] += batch_stats.get("pending", 0)
                stats["llm_batch_errors"] += batch_stats.get("errors", 0)
            except Exception as exc:
                self._error(stats, "record_errors", "batch reranking", exc)
            for items, keys, _ in pending_groups.values():
                try:
                    self._finalize(items)
                    self._save(items, keys, observed=False, queue_alert=queue_alert)
                except Exception as exc:
                    self._error(stats, "record_errors", items[0].record.title, exc)

    def _cache_key(self, record: JobRecord, stream: SearchStream) -> str:
        prompt = self.reranker.prompt_template
        return stable_hash(
            json.dumps(
                {
                    "content": self.deduplicator.build_identity(record).content_hash,
                    "metadata": record.source_metadata,
                    "profile": stream.version,
                    "classification": self.classifier.version,
                    "role_label": stream.id,
                    "llm": asdict(self.config.llm),
                    "prompt": prompt,
                },
                sort_keys=True,
                ensure_ascii=False,
            )
        )

    def _evaluate_record(
        self, record: JobRecord, stats: dict[str, Any], *, use_llm: bool
    ) -> tuple[list[EvaluatedJob], dict[str, str], list[EvaluatedJob]]:
        items = []
        keys = {}
        pending = []
        with self.database.session() as session:
            repository = JobRepository(session)
            saved = repository.find_by_record(record)
            cached_evaluations = (
                repository.profile_evaluations_for_job(saved.job_uid)
                if saved is not None and use_llm and self.config.llm.enabled else {}
            )
            if saved is not None and not record.annotation:
                record.annotation = saved.annotation or {}
            # Choose occupation before evaluating relevance or per-role conditions.
            before_classification = dict(self.classifier.stats)
            stream, classification = self.classifier.classify(
                record,
                use_ai=False,
                previous=saved.role_classification if saved is not None else None,
            )
            engine = stream.engine
            local_item = engine.apply(record)
            requested_ai = (
                use_llm and self.config.annotation.enabled and self.config.annotation.ai_enabled
            )
            if (
                stream.id == "other" and requested_ai
                and classification["method"] != "model"
                and local_item.evaluation.decision != FilterDecision.REJECT
            ):
                stream, classification = self.classifier.classify(record, use_ai=True)
                engine = stream.engine
                local_item = engine.apply(record)
            for name, value in self.classifier.stats.items():
                stats["classification_" + name] += value - before_classification[name]
            route = None
            if requested_ai and local_item.evaluation.decision == FilterDecision.REJECT:
                blockers = sorted(set(local_item.evaluation.blocker_signals))
                route = {
                    "routing": "local_reject", "decision": "clearly_irrelevant",
                    "reason_code": "hard_blocker",
                    "reason": "The classified role has explicit hard blockers.",
                    "blockers": blockers, "source_hash": source_hash(record),
                }
                stats["prefilter_local_skipped"] += 1
                emit_progress(
                    self.annotator.progress, "prefilter_local_skip", routing="local_reject",
                    reason_code="hard_blocker", blockers=len(blockers),
                )
            elif self.config.prefilter.enabled:
                if requested_ai:
                    before = dict(self.prefilter.stats)
                    route = self.prefilter.route(record)
                    for name, value in self.prefilter.stats.items():
                        stats["prefilter_" + name] += value - before[name]
                else:
                    # Offline re-evaluation retains only a revalidated current route.
                    route = self.prefilter.cached_route(record)
                if route is not None:
                    counter = (
                        "prefilter_suppressed" if route["routing"] == "suppressed"
                        else "prefilter_full_review"
                    )
                    stats[counter] += 1
            suppressed = route is not None and route["routing"] == "suppressed"
            before = dict(self.annotator.stats)
            self.annotator.annotate(
                record, use_ai=use_llm and not suppressed
                and not (route is not None and route["routing"] == "local_reject"),
            )
            # Routing is deliberately outside source claims and their independent review.
            record.annotation.pop("prefilter", None)
            if route is not None:
                record.annotation["prefilter"] = route
            for name, value in self.annotator.stats.items():
                stats["annotation_" + name] += value - before[name]
            item = local_item
            apply_annotation_constraints(record, item.evaluation, engine)
            item.profile_id, item.profile_name = stream.id, stream.name
            item.profile_version, item.profile_context = stream.version, stream.context
            item.role_classification = classification
            if stream.id == "other":
                # No relevance estimate against an unrelated occupation.
                item.evaluation.fit_score = item.evaluation.deterministic_score = 0.0
                item.evaluation.bridge_role = False
                item.evaluation.fit_reason = classification["reason"]
                if item.evaluation.decision != FilterDecision.REJECT:
                    item.evaluation.decision = FilterDecision.REVIEW
                item.evaluation.risks.append("role_classification:needs_review")
                item.evaluation.audit_log.append("Outside role taxonomy; relevance unscored.")
                items.append(item)
                self._finalize(items)
                return items, keys, pending
            stream.scorer.score(
                record.source,
                record.company,
                record.title,
                role_description(record),
                item.evaluation,
            )
            if suppressed and item.evaluation.decision != FilterDecision.REJECT:
                # Retain the source and CRM linkage; a routing verdict isn't an
                # independently reviewed final rejection or a hiring fact.
                item.evaluation.decision = FilterDecision.REVIEW
                item.evaluation.risks = sorted(set(
                    item.evaluation.risks + ["prefilter:" + route["reason_code"]]
                ))
                item.evaluation.bridge_role = False
                item.evaluation.fit_score = 0.0
                item.evaluation.deterministic_score = 0.0
                item.evaluation.fit_reason = "Preliminary filter: " + route["reason_code"]
                item.evaluation.audit_log.append(
                    "Independent preliminary checks agree on a mismatch; "
                    "full AI review skipped. Source retained for manual review."
                )
            if (
                use_llm
                and self.config.llm.enabled
                and not suppressed
                and not (self.config.annotation.enabled and self.config.annotation.ai_enabled)
                and item.evaluation.decision != FilterDecision.REJECT
            ):
                key = self._cache_key(record, stream)
                keys[stream.id] = key
                cached = cached_evaluations.get(stream.id)
                if (
                    cached is not None
                    and cached.semantic_score is not None
                    and cached.llm_cache_key == key
                ):
                    item.evaluation.semantic_score = cached.semantic_score
                    item.evaluation.fit_score += cached.semantic_score
                    item.evaluation.bridge_role |= cached.bridge_role
                    item.evaluation.fit_reason = cached.fit_reason
                    item.evaluation.risks = sorted(set(item.evaluation.risks + cached.risks))
                    item.evaluation.audit_log.append("Semantic score reused for this profile.")
                    stats["llm_cache_hits"] += 1
                elif self.config.llm.skip_high_confidence and should_skip_llm(
                    item.evaluation,
                    stream.scoring.threshold_a,
                    stream.scoring.threshold_b,
                    self.config.llm.high_confidence_margin,
                ):
                    stats["llm_skipped"] += 1
                    item.evaluation.audit_log.append("LLM skipped: deterministic confidence.")
                elif self.batch_reranker is not None:
                    pending.append(item)
                else:
                    if os.getenv(self.config.llm.api_key_env):
                        stats["llm_calls"] += 1
                    item = self.reranker.rerank(item)
            else:
                item.evaluation.audit_log.append("Deterministic evaluation; no AI request.")
            items.append(item)
        self._finalize(items)
        return items, keys, pending

    def _finalize(self, items: list[EvaluatedJob]) -> None:
        by_id = {s.id: s for s in self.streams}
        for item in items:
            scoring = by_id[item.profile_id].scoring
            finalize_tier(item, scoring.threshold_a, scoring.threshold_b)

    def _save(
        self, items: list[EvaluatedJob], keys: dict[str, str], *, observed: bool, queue_alert: bool
    ) -> None:
        with self.database.session() as session:
            repository = JobRepository(
                session, alert_dedup_hours=self.config.telegram.alert_dedup_hours
            )
            repository.upsert_evaluations(
                items,
                keys,
                observed=observed,
                queue_alert=(
                    queue_alert
                    and self.config.telegram.enabled
                    and self.config.telegram.instant_a_tier
                ),
            )
            session.commit()

    def _dispatch_alerts(self, stats: dict[str, Any]) -> None:
        if not self.config.telegram.enabled or not self.config.telegram.instant_a_tier:
            return
        with self.database.session() as session:
            ids = list(
                session.scalars(select(AlertOutboxORM.id).where(AlertOutboxORM.status == "pending"))
            )
        for outbox_id in ids:
            try:
                with self.database.session() as session:
                    outbox = session.get(AlertOutboxORM, outbox_id)
                    if outbox is None or outbox.status != "pending":
                        continue
                    job = outbox.job
                    if (
                        job.archived_at is not None
                        or job.tier != "A"
                        or job.best_profile_id not in self.profile_versions
                        or not any(
                            p.profile_id == job.best_profile_id
                            and p.profile_version == self.profile_versions[p.profile_id]
                            and p.content_hash == job.content_hash
                            and classification_is_current(job, p)
                            for p in job.profile_evaluations
                        )
                    ):
                        outbox.status = "cancelled"
                        session.commit()
                        continue
                    outbox.attempts += 1
                    outbox.message = build_instant_alert(job)
                    session.commit()
                    # Vacancy and outbox have already been committed.
                    try:
                        sent = self.telegram.send(outbox.message)
                    except Exception as exc:
                        sent = False
                        outbox.last_error = str(exc)
                    if sent:
                        JobRepository(session).mark_alert_sent(
                            job.job_uid, JobTier.A, outbox.channel, outbox.message
                        )
                        outbox.status = "sent"
                        outbox.last_error = None
                        stats["alerts"] += 1
                    else:
                        outbox.last_error = outbox.last_error or "Delivery unavailable"
                        self._error(stats, "alert_errors", job.job_uid, outbox.last_error)
                    session.commit()
            except Exception as exc:
                self._error(stats, "alert_errors", str(outbox_id), exc)

    def reevaluate_saved(
        self, *, use_ai: bool = False, limit: int | None = None,
        progress: ProgressCallback | None = None,
        job_uids: list[str] | None = None,
    ) -> dict[str, Any]:
        """Apply profiles offline; progress does not change evaluations or cache keys."""
        selection = validate_job_uids(job_uids)
        if selection is not None and limit is not None:
            raise ValueError("Use either selected job UIDs or limit, not both")
        previous_progress = self.annotator.progress
        previous_prefilter_progress = self.prefilter.progress
        previous_classifier_progress = self.classifier.client.progress
        self.annotator.set_progress(progress)
        self.prefilter.set_progress(progress)
        self.classifier.client.progress = progress
        started = time.monotonic()
        emit_progress(progress, "run_start")
        try:
            selected = {"job_uids": selection} if selection is not None else {}
            result = self._reevaluate_saved(
                use_ai=use_ai, limit=limit, progress=progress, **selected,
            )
            emit_progress(
                progress, "run_complete", total=result["persisted"] + result["record_errors"],
                persisted=result["persisted"], elapsed=time.monotonic() - started,
                **{key: value for key, value in result.items() if key.startswith("prefilter_")},
            )
            return result
        finally:
            self.annotator.set_progress(previous_progress)
            self.prefilter.set_progress(previous_prefilter_progress)
            self.classifier.client.progress = previous_classifier_progress

    def _reevaluate_saved(
        self, *, use_ai: bool, limit: int | None, progress: ProgressCallback | None,
        job_uids: list[str] | None = None,
    ) -> dict[str, Any]:
        """Apply profiles to saved data; AI is explicit opt-in, notifications stay disabled."""
        stats = self._stats()
        pending_groups = {}
        with self.database.session() as session:
            selected = {"job_uids": job_uids} if job_uids is not None else {}
            jobs = JobRepository(session).list_jobs(limit=limit, archived=False, **selected)
            if job_uids is not None:
                by_uid = {job.job_uid: job for job in jobs}
                if set(job_uids) - set(by_uid):
                    raise ValueError(
                        "Requested jobs were not selected; check IDs and archive state"
                    )
                # A resumed run follows the frozen selection, even after saves reorder updated_at.
                jobs = [by_uid[uid] for uid in job_uids]
            records = [
                JobRecord(
                    source=j.source,
                    company=j.company,
                    title=j.title,
                    original_url=j.original_url,
                    apply_url=j.apply_url,
                    source_job_id=j.source_job_id,
                    posted_at=j.posted_at,
                    location_text=j.location_text,
                    remote_text=j.remote_text,
                    employment_type=j.employment_type,
                    salary_text=j.salary_text,
                    timezone_text=j.timezone_text,
                    description_raw=j.description_raw,
                    description_clean=j.description_clean,
                    status=JobStatus(j.status),
                    source_metadata=j.source_metadata,
                    annotation=j.annotation or {},
                )
                for j in jobs
            ]
        emit_progress(progress, "jobs_loaded", total=len(records))
        for index, record in enumerate(records, 1):
            started = time.monotonic()
            annotation_errors_before = stats["annotation_errors"]
            prefilter_errors_before = stats["prefilter_errors"]
            emit_progress(
                progress, "job_start", index=index, total=len(records),
                company=record.company, title=record.title,
            )
            try:
                items, keys, pending = self._evaluate_record(record, stats, use_llm=use_ai)
                self._save(items, keys, observed=False, queue_alert=False)
                stats["persisted"] += 1
                stats["evaluations"] += len(items)
                emit_progress(
                    progress, "job_complete", evaluations=len(items),
                    elapsed=time.monotonic() - started,
                    errors=(stats["annotation_errors"] - annotation_errors_before
                            + stats["prefilter_errors"] - prefilter_errors_before),
                    annotation_errors=stats["annotation_errors"] - annotation_errors_before,
                    prefilter_errors=stats["prefilter_errors"] - prefilter_errors_before,
                )
                if pending:
                    job_uid = self.deduplicator.build_identity(record).job_uid
                    pending_groups[job_uid] = (items, keys, pending)
            except Exception as exc:
                emit_progress(progress, "job_error", error_type=type(exc).__name__)
                self._error(stats, "record_errors", record.title, exc)
        self._finish_batches(pending_groups, stats, queue_alert=False)
        return stats

    def send_daily_digest(self, hours: int = 24) -> str:
        with self.database.session() as session:
            jobs = JobRepository(session).recent_jobs_for_digest(
                hours=hours, active_profile_versions=self.profile_versions
            )
            message = build_daily_digest(jobs)
        if self.config.telegram.daily_digest_enabled:
            self.telegram.send(message)
        return message

    def export_csv(
        self,
        output_path: Path,
        *,
        profile_id: str | None = None,
        include_rejected: bool = False,
        shortlist: bool = False,
    ) -> Path:
        self._validate_profile(profile_id)
        with self.database.session() as session:
            return JobRepository(session).export_shortlisted_csv(
                output_path,
                wide=not shortlist,
                include_rejected=include_rejected,
                profile_id=profile_id,
                active_profile_versions=self.profile_versions,
            )

    def render_html(
        self, output_path: Path, limit: int = 100, profile_id: str | None = None
    ) -> Path:
        from job_intake.review.report import render_html_report

        self._validate_profile(profile_id)
        with self.database.session() as session:
            jobs = JobRepository(session).list_jobs(limit=limit, archived=False)
            return render_html_report(
                jobs,
                output_path,
                profile_id=profile_id,
                active_profile_ids=list(self.profile_versions),
                active_profile_versions=self.profile_versions,
                active_profile_names={s.id: s.name for s in self.streams},
            )

    def _validate_profile(self, profile_id: str | None) -> None:
        if profile_id is not None and profile_id not in self.profile_versions:
            raise ValueError(f"Unknown or disabled profile: {profile_id}")

    def prune(
        self, older_than_days: int, tiers: tuple[str, ...] = ("C",), do_vacuum: bool = False
    ) -> int:
        with self.database.session() as session:
            removed = JobRepository(session).prune_low_tier(older_than_days, tiers)
            session.commit()
        if do_vacuum:
            self.database.vacuum()
        return removed

    def add_feedback(self, job_uid: str, label: str, note: str = "") -> None:
        with self.database.session() as session:
            JobRepository(session).add_feedback(job_uid, label, note)
            session.commit()


def build_pipeline(config_path: str | Path) -> JobIntakePipeline:
    config = load_app_config(config_path)
    configure_logging(config.log_level)
    return JobIntakePipeline(config)
