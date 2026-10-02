from dataclasses import replace

import pytest
import yaml
from sqlalchemy import func, select

from job_intake.config.settings import AppConfig, LLMConfig, SourceDefinition, TelegramConfig
from job_intake.models.job import JobRecord
from job_intake.pipeline import JobIntakePipeline
from job_intake.storage.models import AlertOutboxORM, JobORM, JobProfileEvaluationORM


def _config(tmp_path, *, telegram=False, llm=False):
    rules_path = tmp_path / "rules.yaml"
    profiles_path = tmp_path / "profiles.yaml"
    prompt_path = tmp_path / "prompt.txt"
    rules_path.write_text(
        yaml.safe_dump(
            {
                "recall_first": True,
                "required_languages": ["English"],
                "target_geographies": ["Brazil", "worldwide"],
                "onsite_locations": ["Sao Paulo"],
            }
        )
    )
    profiles_path.write_text(
        yaml.safe_dump(
            {
                "streams": [
                    {
                        "id": "product",
                        "name": "Product",
                        "context": "Product management",
                        "keywords": ["Product Manager"],
                        "rules": {"positive_title_signals": ["product manager"]},
                        "scoring": {
                            "title_weights": {"product manager": 8},
                            "threshold_a": 6,
                            "threshold_b": 3,
                        },
                    },
                    {
                        "id": "analytics",
                        "name": "Analytics",
                        "context": "Analytics leadership",
                        "keywords": ["Head of Analytics"],
                        "rules": {"positive_title_signals": ["head of analytics"]},
                        "scoring": {
                            "title_weights": {"head of analytics": 9},
                            "threshold_a": 6,
                            "threshold_b": 3,
                        },
                    },
                ]
            },
            sort_keys=False,
        )
    )
    prompt_path.write_text("{profile_context} {description}")
    return AppConfig(
        database_url=f"sqlite:///{tmp_path / 'jobs.db'}",
        log_level="WARNING",
        rules_path=rules_path,
        search_profiles_path=profiles_path,
        company_watchlist_path=tmp_path / "companies.yaml",
        export_dir=tmp_path,
        sources=[SourceDefinition("good", "html"), SourceDefinition("bad", "html")],
        telegram=TelegramConfig(enabled=telegram),
        llm=LLMConfig(enabled=llm, api_key_env="PHASE1_TEST_AI_KEY", prompt_path=str(prompt_path)),
    )


def _record(description="Own the roadmap. English is the working language."):
    return JobRecord(
        source="good",
        source_job_id="42",
        company="Acme",
        title="Product Manager",
        original_url="https://example.com/jobs/42",
        location_text="Worldwide",
        remote_text="Remote",
        description_clean=description,
        source_metadata={"working_language": "English", "description_complete": True},
    )


def _sources(monkeypatch, record, *, fail_second=True):
    class Adapter:
        errors = []

        def __init__(self, name):
            self.name = name

        def fetch_jobs(self):
            if self.name == "bad":
                if fail_second:
                    raise RuntimeError("source unavailable")
                return []
            return [record]

    monkeypatch.setattr("job_intake.pipeline.build_adapter", lambda source: Adapter(source.name))


def test_independent_profiles_and_source_failure_preserve_committed_job(tmp_path, monkeypatch):
    config = _config(tmp_path)
    _sources(monkeypatch, _record())
    pipeline = JobIntakePipeline(config)
    result = pipeline.run()
    assert result["source_errors"] == 1
    assert result["persisted"] == 1
    assert result["evaluations"] == 2
    with pipeline.database.session() as session:
        assert session.scalar(select(func.count()).select_from(JobORM)) == 1
        job = session.scalar(select(JobORM))
        scores = {p.profile_id: p.fit_score for p in job.profile_evaluations}
        assert scores == {"product": 8, "analytics": 0}
        assert job.best_profile_id == "product"


def test_reevaluate_new_criteria_and_disabled_winner_is_offline_and_preserves_dates(
    tmp_path, monkeypatch
):
    config = _config(tmp_path)
    _sources(monkeypatch, _record(), fail_second=False)
    pipeline = JobIntakePipeline(config)
    pipeline.run()
    with pipeline.database.session() as session:
        job = session.scalar(select(JobORM))
        before = job.first_seen_at, job.last_seen_at
    data = yaml.safe_load(config.search_profiles_path.read_text())
    data["streams"][0]["enabled"] = False
    data["streams"][1]["rules"]["positive_title_signals"] = ["product manager"]
    data["streams"][1]["scoring"]["title_weights"] = {"product manager": 15}
    config.search_profiles_path.write_text(yaml.safe_dump(data))
    pipeline = JobIntakePipeline(config)

    def forbidden(*args, **kwargs):
        pytest.fail("Offline reevaluation performed an external action")

    monkeypatch.setattr("job_intake.pipeline.build_adapter", forbidden)
    pipeline.reranker.rerank = forbidden
    pipeline.telegram.send = forbidden
    assert pipeline.reevaluate_saved()["evaluations"] == 1
    with pipeline.database.session() as session:
        job = session.scalar(select(JobORM))
        assert job.best_profile_id == "analytics"
        assert job.fit_score == 15
        assert (job.first_seen_at, job.last_seen_at) == before


def test_alert_outbox_is_committed_before_send_and_retries_without_losing_job(
    tmp_path, monkeypatch
):
    config = _config(tmp_path, telegram=True)
    _sources(monkeypatch, _record())
    pipeline = JobIntakePipeline(config)

    def fail_send(message):
        with pipeline.database.session() as session:
            assert session.scalar(select(func.count()).select_from(JobORM)) == 1
            assert session.scalar(select(func.count()).select_from(JobProfileEvaluationORM)) == 2
            assert session.scalar(select(AlertOutboxORM)).status == "pending"
        raise RuntimeError("Telegram unavailable")

    pipeline.telegram.send = fail_send
    result = pipeline.run()
    assert result["alert_errors"] == 1
    with pipeline.database.session() as session:
        assert session.scalar(select(AlertOutboxORM)).status == "pending"
    messages = []
    pipeline.telegram.send = lambda message: messages.append(message) or True
    assert pipeline.run()["alerts"] == 1
    assert len(messages) == 1
    with pipeline.database.session() as session:
        assert session.scalar(select(AlertOutboxORM)).status == "sent"
    assert pipeline.run()["alerts"] == 0


def test_semantic_cache_is_per_profile_and_invalidated_by_content_prompt_and_model(
    tmp_path, monkeypatch
):
    config = _config(tmp_path, llm=True)
    record = _record("A" * 900 + " first tail. English is the working language.")
    _sources(monkeypatch, record, fail_second=False)
    pipeline = JobIntakePipeline(config)
    monkeypatch.setenv("PHASE1_TEST_AI_KEY", "fake-not-a-real-key")
    calls = []

    def fake_rerank(item):
        calls.append(item.profile_id)
        prompt = pipeline.reranker._render_prompt(item)
        if len(calls) > 6:
            assert prompt.startswith("Updated:")
        delta = 1 if item.profile_id == "product" else 5
        item.evaluation.semantic_score = delta
        item.evaluation.fit_score += delta
        return item

    pipeline.reranker.rerank = fake_rerank
    assert pipeline.run()["llm_calls"] == 2
    assert pipeline.run()["llm_cache_hits"] == 2
    with pipeline.database.session() as session:
        scores = {
            p.profile_id: p.semantic_score for p in session.scalars(select(JobProfileEvaluationORM))
        }
        assert scores == {"product": 1, "analytics": 5}
    record.description_clean = "A" * 900 + " changed tail. English is the working language."
    assert pipeline.run()["llm_calls"] == 2
    config.llm.model = "another-model"
    assert pipeline.run()["llm_calls"] == 2
    (tmp_path / "prompt.txt").write_text("Updated: {profile_context} {description}")
    assert pipeline.run()["llm_calls"] == 2
    record.source_metadata["working_language"] = ["English", "Portuguese"]
    assert pipeline.run()["llm_calls"] == 2
    assert len(calls) == 10


def test_batch_uses_latest_observation_for_repeated_identity(tmp_path, monkeypatch):
    config = _config(tmp_path, llm=True)
    config.llm = replace(config.llm, batch_enabled=True)
    old = _record("First description. English is the working language.")
    latest = _record("Updated description. English is the working language.")

    class Adapter:
        errors = []

        def fetch_jobs(self):
            return [old, latest]

    config.sources = config.sources[:1]
    monkeypatch.setattr("job_intake.pipeline.build_adapter", lambda source: Adapter())
    pipeline = JobIntakePipeline(config)

    def fake_batch(items):
        assert len(items) == 2
        assert all(item.record.description_clean == latest.description_clean for item in items)
        for item in items:
            item.evaluation.semantic_score = 2
            item.evaluation.fit_score += 2

    pipeline.batch_reranker.rerank_many = fake_batch
    result = pipeline.run()
    assert result["record_errors"] == 0
    with pipeline.database.session() as session:
        assert session.scalar(select(JobORM)).description_clean == latest.description_clean


def test_batch_commits_deterministic_data_before_waiting(tmp_path, monkeypatch):
    config = _config(tmp_path, llm=True)
    config.llm = replace(config.llm, batch_enabled=True)
    _sources(monkeypatch, _record(), fail_second=False)
    pipeline = JobIntakePipeline(config)

    def fake_batch(items):
        with pipeline.database.session() as session:
            assert session.scalar(select(func.count()).select_from(JobORM)) == 1
        for item in items:
            item.evaluation.semantic_score = 2
            item.evaluation.fit_score += 2
        return items

    pipeline.batch_reranker.rerank_many = fake_batch
    result = pipeline.run()
    assert result["llm_batched"] == 2
    with pipeline.database.session() as session:
        assert session.scalar(select(JobORM)).fit_score == 10


def test_profile_queries_follow_enabled_streams(tmp_path):
    pipeline = JobIntakePipeline(_config(tmp_path))
    source = SourceDefinition("remote", "dailyremote", params={"use_profile_queries": True})
    urls = pipeline._source_with_queries(source).params["search_urls"]
    assert len(urls) == 2
    assert any("Product+Manager" in url for url in urls)
    assert "search_urls" not in source.params


def test_digest_excludes_disabled_or_outdated_profile_evaluations(tmp_path, monkeypatch):
    config = _config(tmp_path)
    _sources(monkeypatch, _record(), fail_second=False)
    pipeline = JobIntakePipeline(config)
    pipeline.run()
    assert "Product Manager" in pipeline.send_daily_digest()
    data = yaml.safe_load(config.search_profiles_path.read_text())
    data["streams"][0]["enabled"] = False
    config.search_profiles_path.write_text(yaml.safe_dump(data))
    assert "Product Manager" not in JobIntakePipeline(config).send_daily_digest()


def test_pending_alert_uses_current_reevaluated_risks(tmp_path, monkeypatch):
    config = _config(tmp_path, telegram=True)
    _sources(monkeypatch, _record(), fail_second=False)
    pipeline = JobIntakePipeline(config)
    pipeline.telegram.send = lambda message: False
    assert pipeline.run()["alert_errors"] == 1
    rules = yaml.safe_load(config.rules_path.read_text())
    rules["review_phrases"] = ["roadmap"]
    rules["recall_first"] = False
    config.rules_path.write_text(yaml.safe_dump(rules))
    pipeline = JobIntakePipeline(config)
    pipeline.reevaluate_saved()
    messages = []
    pipeline.telegram.send = lambda message: messages.append(message) or True
    stats = pipeline._stats()
    pipeline._dispatch_alerts(stats)
    assert stats["alerts"] == 1
    assert "review_flag:roadmap" in messages[0]
