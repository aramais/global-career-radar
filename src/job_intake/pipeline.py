from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import select

from job_intake.adapters.factory import build_adapter
from job_intake.alerts.digest import build_daily_digest, build_instant_alert
from job_intake.alerts.telegram import TelegramNotifier
from job_intake.config.settings import AppConfig, load_app_config, load_yaml_mapping
from job_intake.filtering import FilterRules, RuleEngine
from job_intake.scoring.llm import BatchReranker, build_reranker, should_skip_llm
from job_intake.scoring.pre_score import DeterministicScorer, SearchProfiles
from job_intake.scoring.tiering import finalize_tier
from job_intake.storage.database import Database
from job_intake.storage.dedup import JobDeduplicator
from job_intake.storage.models import JobORM
from job_intake.storage.repository import JobRepository
from job_intake.utils.logging import configure_logging

LOGGER = logging.getLogger(__name__)


class JobIntakePipeline:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.database = Database(config.database_url)
        self.database.create_schema()
        self.rules = FilterRules.from_mapping(load_yaml_mapping(config.rules_path))
        self.search_profiles = SearchProfiles.from_mapping(load_yaml_mapping(config.search_profiles_path))
        self.rule_engine = RuleEngine(self.rules)
        self.scorer = DeterministicScorer(self.search_profiles)
        self.reranker = build_reranker(config.llm)
        self.batch_reranker = (
            BatchReranker(config.llm) if config.llm.enabled and config.llm.batch_enabled else None
        )
        self.telegram = TelegramNotifier(config.telegram)
        self.deduplicator = JobDeduplicator()

    def run(self) -> dict[str, int]:
        stats = {
            "ingested": 0,
            "persisted": 0,
            "alerts": 0,
            "llm_calls": 0,
            "llm_cache_hits": 0,
            "llm_skipped": 0,
            "llm_batched": 0,
        }

        with self.database.session() as session:
            repository = JobRepository(
                session, alert_dedup_hours=self.config.telegram.alert_dedup_hours
            )
            pending_batch: list = []
            for source in self.config.sources:
                if not source.enabled:
                    continue
                adapter = build_adapter(source)
                records = adapter.fetch_jobs()
                LOGGER.info("source_fetched %s jobs from %s", len(records), source.name)
                stats["ingested"] += len(records)
                for record in records:
                    evaluated = self.rule_engine.apply(record)
                    evaluated.evaluation = self.scorer.score(
                        record.source,
                        record.company,
                        record.title,
                        record.description_clean or record.description_raw,
                        evaluated.evaluation,
                    )
                    if self._apply_cached_rerank(record, evaluated, repository):
                        stats["llm_cache_hits"] += 1
                    elif self._should_skip_llm(evaluated):
                        stats["llm_skipped"] += 1
                    elif self.batch_reranker is not None:
                        pending_batch.append(evaluated)
                        continue  # finalized after the batch completes
                    else:
                        before = evaluated.evaluation.semantic_score
                        evaluated = self.reranker.rerank(evaluated)
                        if evaluated.evaluation.semantic_score != before:
                            stats["llm_calls"] += 1
                    stats["alerts"] += self._finalize_persist_alert(evaluated, repository, session)
                    stats["persisted"] += 1

            if pending_batch:
                for evaluated in self.batch_reranker.rerank_many(pending_batch):
                    stats["llm_batched"] += 1
                    stats["alerts"] += self._finalize_persist_alert(evaluated, repository, session)
                    stats["persisted"] += 1

            session.commit()
        LOGGER.info(
            "run_complete ingested=%s persisted=%s alerts=%s llm_calls=%s "
            "llm_cache_hits=%s llm_skipped=%s llm_batched=%s",
            stats["ingested"],
            stats["persisted"],
            stats["alerts"],
            stats["llm_calls"],
            stats["llm_cache_hits"],
            stats["llm_skipped"],
            stats["llm_batched"],
        )
        return stats

    def _should_skip_llm(self, evaluated) -> bool:
        if not (self.config.llm.enabled and self.config.llm.skip_high_confidence):
            return False
        if should_skip_llm(
            evaluated.evaluation,
            self.search_profiles.threshold_a,
            self.search_profiles.threshold_b,
            self.config.llm.high_confidence_margin,
        ):
            evaluated.evaluation.audit_log.append(
                "LLM skipped (high deterministic confidence)."
            )
            return True
        return False

    def _finalize_persist_alert(self, evaluated, repository, session) -> int:
        """Finalize tier, persist, and send an instant alert if warranted. Returns alerts sent."""
        evaluated = finalize_tier(
            evaluated,
            self.search_profiles.threshold_a,
            self.search_profiles.threshold_b,
        )
        result = repository.upsert_evaluated_job(evaluated)
        if result.should_alert and self.config.telegram.instant_a_tier:
            job = session.scalar(select(JobORM).where(JobORM.job_uid == result.job_uid))
            if job is None:
                return 0
            message = build_instant_alert(job)
            if self.telegram.send(message):
                repository.mark_alert_sent(
                    result.job_uid, evaluated.evaluation.tier, "telegram", message
                )
                return 1
        return 0

    def prune(
        self, older_than_days: int, tiers: tuple[str, ...] = ("C",), do_vacuum: bool = False
    ) -> int:
        with self.database.session() as session:
            repository = JobRepository(session)
            removed = repository.prune_low_tier(older_than_days, tiers)
            session.commit()
        if do_vacuum:
            self.database.vacuum()
        LOGGER.info("prune_complete removed=%s vacuum=%s", removed, do_vacuum)
        return removed

    def _apply_cached_rerank(self, record, evaluated, repository) -> bool:
        """Reuse a previous LLM semantic score when the job is unchanged.

        Deterministic scoring is cheap and always re-run; only the paid LLM call is
        cached. A cache hit requires: LLM enabled, an existing row with an identical
        ``content_hash``, and a previously persisted ``semantic_score``. On hit the
        stored semantic contribution is re-applied without calling the API.
        """
        if not self.config.llm.enabled:
            return False
        existing = repository.find_by_record(record)
        if existing is None or existing.semantic_score is None:
            return False
        identity = self.deduplicator.build_identity(record)
        if existing.content_hash != identity.content_hash:
            return False

        evaluation = evaluated.evaluation
        evaluation.semantic_score = existing.semantic_score
        evaluation.fit_score += existing.semantic_score
        evaluation.bridge_role = evaluation.bridge_role or existing.bridge_role
        if existing.fit_reason:
            evaluation.fit_reason = existing.fit_reason
        if existing.risks:
            evaluation.risks = sorted(set(evaluation.risks + list(existing.risks)))
        evaluation.audit_log.append(
            f"Semantic rerank reused from cache ({existing.semantic_score:.2f} points)."
        )
        return True

    def send_daily_digest(self, hours: int = 24) -> str:
        with self.database.session() as session:
            repository = JobRepository(session)
            jobs = repository.recent_jobs_for_digest(hours=hours)
            message = build_daily_digest(jobs)
            if self.config.telegram.daily_digest_enabled:
                self.telegram.send(message)
            return message

    def export_csv(self, output_path: Path) -> Path:
        with self.database.session() as session:
            repository = JobRepository(session)
            return repository.export_shortlisted_csv(output_path)

    def render_html(self, output_path: Path, limit: int = 100) -> Path:
        from job_intake.review.report import render_html_report

        with self.database.session() as session:
            repository = JobRepository(session)
            jobs = repository.list_jobs(limit=limit)
            return render_html_report(jobs, output_path)

    def add_feedback(self, job_uid: str, label: str, note: str = "") -> None:
        with self.database.session() as session:
            repository = JobRepository(session)
            repository.add_feedback(job_uid, label, note)
            session.commit()


def build_pipeline(config_path: str | Path) -> JobIntakePipeline:
    config = load_app_config(config_path)
    configure_logging(config.log_level)
    return JobIntakePipeline(config)
