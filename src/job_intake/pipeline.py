from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

from sqlalchemy import select

from job_intake.adapters.factory import build_adapter
from job_intake.alerts.digest import build_daily_digest, build_instant_alert
from job_intake.alerts.telegram import TelegramNotifier
from job_intake.config.settings import (
    AppConfig,
    SourceDefinition,
    load_app_config,
    load_yaml_mapping,
)
from job_intake.models.job import EvaluatedJob, FilterDecision, JobRecord, JobStatus, JobTier
from job_intake.profiles import SearchStream, load_streams
from job_intake.scoring.llm import BatchReranker, build_reranker, should_skip_llm
from job_intake.scoring.tiering import finalize_tier
from job_intake.storage.database import Database
from job_intake.storage.dedup import JobDeduplicator
from job_intake.storage.models import AlertOutboxORM
from job_intake.storage.repository import JobRepository
from job_intake.utils.logging import configure_logging
from job_intake.utils.text import stable_hash

LOGGER = logging.getLogger(__name__)


class JobIntakePipeline:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.streams = load_streams(
            load_yaml_mapping(config.rules_path), load_yaml_mapping(config.search_profiles_path)
        )
        self.database = Database(config.database_url)
        self.database.create_schema()
        self.reranker = build_reranker(config.llm)
        self.batch_reranker = (
            BatchReranker(config.llm) if config.llm.enabled and config.llm.batch_enabled else None
        )
        self.telegram = TelegramNotifier(config.telegram)
        self.deduplicator = JobDeduplicator()

    @property
    def profile_versions(self) -> dict[str, str]:
        return {stream.id: stream.version for stream in self.streams}

    def _source_with_queries(self, source: SourceDefinition) -> SourceDefinition:
        params = dict(source.params)
        if source.type == "dailyremote" and params.get("use_profile_queries"):
            template = params.get("search_url_template", "https://dailyremote.com/?search={query}")
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
        pending_llm = [item for _, _, pending in pending_groups.values() for item in pending]
        if pending_llm:
            try:
                self.batch_reranker.rerank_many(pending_llm)
                stats["llm_batched"] += len(pending_llm)
            except Exception as exc:
                self._error(stats, "record_errors", "batch reranking", exc)
            for items, keys, _ in pending_groups.values():
                try:
                    self._finalize(items)
                    self._save(items, keys, observed=False, queue_alert=True)
                except Exception as exc:
                    self._error(stats, "record_errors", items[0].record.title, exc)
        self._dispatch_alerts(stats)
        LOGGER.info("run_complete %s", json.dumps(stats, ensure_ascii=False))
        return stats

    def _cache_key(self, record: JobRecord, stream: SearchStream) -> str:
        prompt = self.reranker.prompt_template
        return stable_hash(
            json.dumps(
                {
                    "content": self.deduplicator.build_identity(record).content_hash,
                    "metadata": record.source_metadata,
                    "profile": stream.version,
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
            for stream in self.streams:
                item = stream.engine.apply(record)
                item.profile_id, item.profile_name = stream.id, stream.name
                item.profile_version, item.profile_context = stream.version, stream.context
                stream.scorer.score(
                    record.source,
                    record.company,
                    record.title,
                    record.description_clean or record.description_raw,
                    item.evaluation,
                )
                if (
                    use_llm
                    and self.config.llm.enabled
                    and item.evaluation.decision != FilterDecision.REJECT
                ):
                    key = self._cache_key(record, stream)
                    keys[stream.id] = key
                    cached = repository.find_profile_evaluation(record, stream.id)
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
                        job.tier != "A"
                        or job.best_profile_id not in self.profile_versions
                        or not any(
                            p.profile_id == job.best_profile_id
                            and p.profile_version == self.profile_versions[p.profile_id]
                            and p.content_hash == job.content_hash
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

    def reevaluate_saved(self) -> dict[str, Any]:
        """Apply profiles offline; preserve discovery dates and send no notifications."""
        stats = self._stats()
        with self.database.session() as session:
            jobs = JobRepository(session).list_jobs(limit=None)
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
                )
                for j in jobs
            ]
        for record in records:
            try:
                items, keys, _ = self._evaluate_record(record, stats, use_llm=False)
                self._save(items, keys, observed=False, queue_alert=False)
                stats["persisted"] += 1
                stats["evaluations"] += len(items)
            except Exception as exc:
                self._error(stats, "record_errors", record.title, exc)
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
            jobs = JobRepository(session).list_jobs(limit=limit)
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
