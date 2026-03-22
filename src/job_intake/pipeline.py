from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import select

from job_intake.adapters.factory import build_adapter
from job_intake.alerts.digest import build_daily_digest, build_instant_alert
from job_intake.alerts.telegram import TelegramNotifier
from job_intake.config.settings import AppConfig, load_app_config, load_yaml_mapping
from job_intake.filtering import FilterRules, RuleEngine
from job_intake.scoring.llm import build_reranker
from job_intake.scoring.pre_score import DeterministicScorer, SearchProfiles
from job_intake.scoring.tiering import finalize_tier
from job_intake.storage.database import Database
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
        self.telegram = TelegramNotifier(config.telegram)

    def run(self) -> dict[str, int]:
        ingested = 0
        persisted = 0
        alerts = 0

        with self.database.session() as session:
            repository = JobRepository(session)
            for source in self.config.sources:
                if not source.enabled:
                    continue
                adapter = build_adapter(source)
                records = adapter.fetch_jobs()
                LOGGER.info("source_fetched %s jobs from %s", len(records), source.name)
                ingested += len(records)
                for record in records:
                    evaluated = self.rule_engine.apply(record)
                    evaluated.evaluation = self.scorer.score(
                        record.source,
                        record.company,
                        record.title,
                        record.description_clean or record.description_raw,
                        evaluated.evaluation,
                    )
                    evaluated = self.reranker.rerank(evaluated)
                    evaluated = finalize_tier(evaluated)
                    result = repository.upsert_evaluated_job(evaluated)
                    persisted += 1
                    if result.should_alert and self.config.telegram.instant_a_tier:
                        job = session.scalar(select(JobORM).where(JobORM.job_uid == result.job_uid))
                        if job is None:
                            continue
                        message = build_instant_alert(job)
                        if self.telegram.send(message):
                            repository.mark_alert_sent(result.job_uid, evaluated.evaluation.tier, "telegram", message)
                            alerts += 1
            session.commit()
        return {"ingested": ingested, "persisted": persisted, "alerts": alerts}

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
