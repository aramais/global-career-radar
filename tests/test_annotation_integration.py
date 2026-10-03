"""Cross-layer source annotation safety, persistence and offline reevaluation."""

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime

import pytest
import yaml
from sqlalchemy import inspect, select, text
from test_database_migration import create_legacy_database
from typer.testing import CliRunner

from job_intake.annotation.ai import EXTRACTION_PROMPT, REVIEW_PROMPT
from job_intake.annotation.service import VacancyAnnotator, apply_annotation_constraints
from job_intake.cli import app
from job_intake.config.settings import (
    AnnotationConfig,
    AppConfig,
    LLMConfig,
    SourceDefinition,
    TelegramConfig,
)
from job_intake.crm import CRMRepository
from job_intake.filtering import FilterRules, RuleEngine
from job_intake.models.job import FilterDecision, JobRecord
from job_intake.pipeline import JobIntakePipeline
from job_intake.storage.database import Database
from job_intake.storage.models import AlertOutboxORM, JobORM, JobProfileEvaluationORM

TEST_KEY_ENV = "ANNOTATION_INTEGRATION_TEST_KEY"


def _record(**changes) -> JobRecord:
    return JobRecord(**{
        "source": "fixture", "source_job_id": "42", "company": "Example",
        "title": "Product Manager", "original_url": "https://example.com/job/42",
        "location_text": "Brazil", "remote_text": "Remote",
        "posted_at": datetime(2026, 9, 15, 12, tzinfo=UTC),
        "description_clean": "Own the product roadmap.",
        "source_metadata": {"working_language": "English", "description_complete": True},
        **changes,
    })


def _config(tmp_path, *, ai=False, annotation=True) -> AppConfig:
    rules = tmp_path / "rules.yaml"
    profiles = tmp_path / "profiles.yaml"
    rules.write_text(yaml.safe_dump({
        "recall_first": True, "required_languages": ["English"],
        "target_geographies": ["Brazil", "worldwide"], "onsite_locations": ["São Paulo"],
        "positive_title_signals": ["product manager"],
        "blocker_phrases": ["US work authorization required"],
    }), encoding="utf-8")
    profiles.write_text(yaml.safe_dump({"streams": [{
        "id": "product", "name": "Product", "context": "Product management",
        "scoring": {"title_weights": {"product manager": 20},
                    "threshold_a": 14, "threshold_b": 6},
    }]}), encoding="utf-8")
    return AppConfig(
        database_url=f"sqlite:///{tmp_path / 'jobs.db'}", log_level="WARNING",
        rules_path=rules, search_profiles_path=profiles,
        company_watchlist_path=tmp_path / "unused-watchlist.yaml", export_dir=tmp_path,
        sources=[SourceDefinition("fixture", "html")],
        telegram=TelegramConfig(enabled=False), llm=LLMConfig(enabled=False),
        annotation=AnnotationConfig(
            enabled=annotation, ai_enabled=ai, api_key_env=TEST_KEY_ENV,
            cache_dir=str(tmp_path / "annotation-cache"),
        ),
    )


def _sources(monkeypatch, record) -> None:
    class FixtureAdapter:
        errors = []

        def fetch_jobs(self):
            return [record]

    monkeypatch.setattr("job_intake.pipeline.build_adapter", lambda source: FixtureAdapter())


def _model_stub(pipeline, monkeypatch, *, kind, value, field, status="SUPPORTED",
                requirement="informational", polarity="affirmative", fail_review=False):
    """Return realistic source references through both actual model stages."""
    monkeypatch.setenv(TEST_KEY_ENV, "offline-test-placeholder")
    calls = []

    def generate(model, prompt):
        calls.append(model)
        if model == pipeline.config.annotation.extract_model:
            payload = json.loads(prompt[len(EXTRACTION_PROMPT):])
            units = [unit for unit in payload["units"] if unit["field"] == field]
            if not units:
                return {"claims": []}, {}
            unit = next((unit for unit in units if value in unit["text"]), units[0])
            return {"claims": [{
                "kind": kind, "value": value, "unit_id": unit["unit_id"],
                "source_snippet": unit["text"], "requirement": requirement,
                "polarity": polarity, "is_inference": False,
            }]}, {}
        assert model == pipeline.config.annotation.review_model
        if fail_review:
            raise RuntimeError("Synthetic reviewer failure")
        payload = json.loads(prompt[len(REVIEW_PROMPT):])
        return {"reviews": [{
            "claim_id": claim["claim_id"], "review_status": status,
            "review_notes": "Offline integration fixture", "is_inference": False,
        } for claim in payload["claims"]]}, {}

    monkeypatch.setattr(pipeline.annotator.client, "generate", generate)
    return calls


def _tables_snapshot(database, *, crm_only=False):
    names = sorted(inspect(database.engine).get_table_names())
    if crm_only:
        names = [name for name in names if name.startswith("crm_")]
    with database.engine.connect() as connection:
        return {name: [tuple(row) for row in connection.execute(
            text(f'SELECT * FROM "{name}" ORDER BY 1, 2')
        )] for name in names}


@pytest.mark.parametrize("previous_additions", [False, True])
def test_annotation_migration_defaults_preserves_legacy_rows_and_is_repeatable(
    tmp_path, previous_additions,
) -> None:
    path = tmp_path / "legacy.db"
    original = create_legacy_database(path, previous_additions)
    database = Database(f"sqlite:///{path}")
    try:
        database.create_schema()
        columns = {column["name"]: column for column in inspect(
            database.engine
        ).get_columns("jobs")}
        assert columns["annotation"]["nullable"] is False
        with closing(sqlite3.connect(path)) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute("SELECT * FROM jobs").fetchone()
            assert {key: row[key] for key in original} == original
            assert json.loads(row["annotation"]) == {}
        with database.session() as session:
            job = session.scalar(select(JobORM))
            assert job.annotation == {}
            job.annotation = {"version": "fixture", "claims": [{"value": "Preserved"}]}
            session.commit()
        before = _tables_snapshot(database)
        database.create_schema()
        database.create_schema()
        assert _tables_snapshot(database) == before
    finally:
        database.engine.dispose()


def test_offline_annotation_persists_without_touching_source_dates_crm_or_outbox(
    tmp_path, monkeypatch,
) -> None:
    config = _config(tmp_path, annotation=False)
    _sources(monkeypatch, _record())
    pipeline = JobIntakePipeline(config)
    assert pipeline.run()["persisted"] == 1
    with pipeline.database.session() as session:
        job = session.scalar(select(JobORM))
        assert job.annotation == {}
        old = datetime(2026, 9, 20, 10)
        job.first_seen_at = job.last_seen_at = job.created_at = old
        dates = job.posted_at, job.first_seen_at, job.last_seen_at, job.created_at
        source_metadata = dict(job.source_metadata)
        crm = CRMRepository(session)
        application = crm.create_from_job(job.job_uid, profile_id="product")
        crm.update_application(application.id, notes="Manual note", channel="referral",
                               next_action="Write to recruiter", cv_version="cv-v3")
        crm.mark_stage(application.id, "applied", occurred_at="2026-09-22")
        contact = crm.upsert_contact(name="Fixture recruiter", company="Example")
        crm.link_contact(application.id, contact.id, "recruiter")
        session.add(AlertOutboxORM(job_uid=job.job_uid, channel="telegram", status="pending",
                                  message="Existing pending alert"))
        session.commit()
    before = _tables_snapshot(pipeline.database, crm_only=True)
    with pipeline.database.session() as session:
        outbox_before = [(row.id, row.status, row.message) for row in session.scalars(
            select(AlertOutboxORM)
        )]
    config.annotation.enabled = True
    config.annotation.ai_enabled = True
    config.telegram.enabled = True

    def forbidden(*args, **kwargs):
        pytest.fail("Offline annotation attempted fetching, AI or notifications")

    monkeypatch.setattr("job_intake.pipeline.build_adapter", forbidden)
    monkeypatch.setattr(pipeline.annotator.client, "generate", forbidden)
    monkeypatch.setattr(pipeline.reranker, "rerank", forbidden)
    monkeypatch.setattr(pipeline.telegram, "send", forbidden)
    result = pipeline.reevaluate_saved()
    assert result["persisted"] == 1
    assert result["annotation_extraction_calls"] == result["annotation_review_calls"] == 0
    assert result["alerts"] == result["record_errors"] == 0
    assert _tables_snapshot(pipeline.database, crm_only=True) == before
    with pipeline.database.session() as session:
        job = session.scalar(select(JobORM))
        assert (job.posted_at, job.first_seen_at, job.last_seen_at, job.created_at) == dates
        assert job.source_metadata == source_metadata
        assert job.annotation["method"] == "deterministic"
        assert job.annotation["units"]
        assert job.annotation["summary"]["knowns"]
        assert job.fit_score == 20  # The fit score has no normalization to 0–15.
        assert [(row.id, row.status, row.message) for row in session.scalars(
            select(AlertOutboxORM)
        )] == outbox_before


@pytest.mark.parametrize("constraint", [
    "US work authorization required.", "Must reside in Germany.",
])
def test_annotation_cannot_lift_existing_hard_rejection(tmp_path, monkeypatch, constraint) -> None:
    _sources(monkeypatch, _record(description_clean=constraint))
    pipeline = JobIntakePipeline(_config(tmp_path))
    assert pipeline.run()["record_errors"] == 0
    with pipeline.database.session() as session:
        job = session.scalar(select(JobORM))
        assert job.annotation["summary"]["knowns"]
        assert job.filter_decision == "reject"
        assert job.fit_score == 0
        assert job.tier == "C"


def test_english_advert_does_not_become_a_confirmed_working_language(tmp_path, monkeypatch) -> None:
    _sources(monkeypatch, _record(source_metadata={"description_complete": True}))
    pipeline = JobIntakePipeline(_config(tmp_path))
    pipeline.run()
    with pipeline.database.session() as session:
        job = session.scalar(select(JobORM))
        assert "working_language" in job.annotation["summary"]["unknowns"]
        assert "language:working_language_unconfirmed" in job.risks
        assert job.filter_decision == "review"
        assert job.tier == "B"


@pytest.mark.parametrize("review_status", ["UNSUPPORTED", "WEAKLY_SUPPORTED"])
def test_unconfirmed_model_interpretation_cannot_promote_vacancy(
    tmp_path, monkeypatch, review_status,
) -> None:
    _sources(monkeypatch, _record(
        description_clean="All documents are in English.",
        source_metadata={"description_complete": True},
    ))
    pipeline = JobIntakePipeline(_config(tmp_path, ai=True))
    calls = _model_stub(pipeline, monkeypatch, kind="working_language", value="English",
                        field="description_clean", status=review_status)
    assert pipeline.run()["record_errors"] == 0
    assert calls == [
        pipeline.config.annotation.extract_model, pipeline.config.annotation.review_model,
    ]
    with pipeline.database.session() as session:
        job = session.scalar(select(JobORM))
        assert job.annotation["status"] == "reviewed"
        assert "working_language" in job.annotation["summary"]["unknowns"]
        assert job.filter_decision == "review"
        assert job.tier == "B"


def test_partial_model_review_cannot_promote_even_an_otherwise_strong_vacancy(
    tmp_path, monkeypatch,
) -> None:
    _sources(monkeypatch, _record())
    pipeline = JobIntakePipeline(_config(tmp_path, ai=True))
    _model_stub(pipeline, monkeypatch, kind="working_language", value="English",
                field="source_metadata.working_language", fail_review=True)
    result = pipeline.run()
    assert result["annotation_errors"] == 1
    assert result["record_errors"] == 0
    with pipeline.database.session() as session:
        job = session.scalar(select(JobORM))
        assert job.annotation["status"] == "partial"
        assert "annotation:incomplete_source_review" in job.risks
        assert job.filter_decision == "review"
        assert job.tier == "B"


def test_offline_reevaluation_preserves_unresolved_partial_model_review(
    tmp_path, monkeypatch,
) -> None:
    _sources(monkeypatch, _record())
    pipeline = JobIntakePipeline(_config(tmp_path, ai=True))
    _model_stub(pipeline, monkeypatch, kind="working_language", value="English",
                field="source_metadata.working_language", fail_review=True)
    assert pipeline.run()["annotation_errors"] == 1

    def forbidden(*args, **kwargs):
        pytest.fail("Offline reevaluation called a model")

    monkeypatch.setattr(pipeline.annotator.client, "generate", forbidden)
    result = pipeline.reevaluate_saved()
    assert result["record_errors"] == 0
    assert result["annotation_extraction_calls"] == result["annotation_review_calls"] == 0
    with pipeline.database.session() as session:
        job = session.scalar(select(JobORM))
        assert job.annotation["status"] == "partial"
        assert "annotation:incomplete_source_review" in job.risks
        assert job.filter_decision == "review"
        assert job.tier == "B"


def test_offline_review_cache_survives_sqlite_publication_date_round_trip(tmp_path, monkeypatch):
    _sources(monkeypatch, _record(
        description_clean="All internal communication uses English.",
        source_metadata={"description_complete": True},
    ))
    pipeline = JobIntakePipeline(_config(tmp_path, ai=True))
    _model_stub(pipeline, monkeypatch, kind="working_language", value="English",
                field="description_clean")
    assert pipeline.run()["annotation_review_calls"] == 1
    with pipeline.database.session() as session:
        job = session.scalar(select(JobORM))
        assert job.annotation["status"] == "reviewed"
        assert job.filter_decision == "pass"
        original_hash = job.annotation["source_hash"]

    def forbidden(*args, **kwargs):
        pytest.fail("Persisted reviewed annotation unnecessarily called a model")

    monkeypatch.setattr(pipeline.annotator.client, "generate", forbidden)
    result = pipeline.reevaluate_saved()
    assert result["record_errors"] == 0
    assert result["annotation_cache_hits"] == 1
    with pipeline.database.session() as session:
        job = session.scalar(select(JobORM))
        assert job.annotation["source_hash"] == original_hash
        assert job.annotation["method"] == "two_pass"
        assert job.annotation["status"] == "reviewed"
        assert job.filter_decision == "pass"
        assert job.tier == "A"


def test_conflicting_model_review_keeps_vacancy_for_manual_review(tmp_path, monkeypatch) -> None:
    _sources(monkeypatch, _record())
    pipeline = JobIntakePipeline(_config(tmp_path, ai=True))
    _model_stub(pipeline, monkeypatch, kind="working_language", value="English",
                field="source_metadata.working_language", status="CONFLICTING")
    pipeline.run()
    with pipeline.database.session() as session:
        job = session.scalar(select(JobORM))
        assert job.annotation["summary"]["contradictions"]
        assert "annotation:conflicting_requirements" in job.risks
        assert job.filter_decision == "review"
        assert job.tier == "B"


def test_exclusive_prose_working_language_conflicts_with_structured_english(
    tmp_path, monkeypatch,
) -> None:
    _sources(monkeypatch, _record(description_clean="The only working language is Portuguese."))
    pipeline = JobIntakePipeline(_config(tmp_path))
    pipeline.run()
    with pipeline.database.session() as session:
        job = session.scalar(select(JobORM))
        assert job.annotation["summary"]["contradictions"]
        assert job.filter_decision == "review"
        assert job.tier == "B"


def test_bilingual_source_languages_are_not_a_contradiction(tmp_path, monkeypatch) -> None:
    _sources(monkeypatch, _record(source_metadata={
        "working_language": ["English", "Portuguese"], "description_complete": True,
    }))
    pipeline = JobIntakePipeline(_config(tmp_path))
    pipeline.run()
    with pipeline.database.session() as session:
        job = session.scalar(select(JobORM))
        assert job.annotation["summary"]["contradictions"] == []
        assert job.filter_decision == "pass"
        assert job.tier == "A"


def test_supported_required_foreign_hiring_location_is_enforced(tmp_path, monkeypatch) -> None:
    _sources(monkeypatch, _record(description_clean="Hiring is limited to residents of Germany."))
    pipeline = JobIntakePipeline(_config(tmp_path, ai=True))
    _model_stub(pipeline, monkeypatch, kind="hiring_location", value="Germany",
                field="description_clean", requirement="required")
    pipeline.run()
    with pipeline.database.session() as session:
        job = session.scalar(select(JobORM))
        assert job.filter_decision == "reject"
        assert job.fit_score == 0
        assert job.tier == "C"
        assert session.scalar(select(JobProfileEvaluationORM)).decision == "reject"


def test_supported_model_target_country_exclusion_is_enforced(tmp_path, monkeypatch) -> None:
    _sources(monkeypatch, _record(
        location_text="Worldwide",
        description_clean="Brazil is excluded from our eligible hiring regions.",
    ))
    pipeline = JobIntakePipeline(_config(tmp_path, ai=True))
    _model_stub(pipeline, monkeypatch, kind="hiring_location", value="Brazil",
                field="description_clean", requirement="required", polarity="negative")
    assert pipeline.run()["record_errors"] == 0
    with pipeline.database.session() as session:
        job = session.scalar(select(JobORM))
        assert any(claim["kind"] == "hiring_location" and claim["polarity"] == "negative"
                   for claim in job.annotation["summary"]["knowns"])
        assert job.filter_decision == "reject"
        assert job.fit_score == 0
        assert job.tier == "C"


def test_annotation_disabled_does_not_apply_preexisting_claims(tmp_path) -> None:
    # Explicit constraints stay deterministic even if callers hold saved annotation.
    rules = FilterRules.from_mapping({
        "recall_first": True, "required_languages": ["English"],
        "target_geographies": ["Brazil"], "positive_title_signals": ["product manager"],
    })
    record = _record(source_metadata={})
    annotator = VacancyAnnotator(AnnotationConfig(enabled=False))
    record.annotation = {"summary": {"knowns": [
        {"kind": "working_language", "value": "English", "requirement": "informational",
         "polarity": "affirmative"},
    ]}}
    assert annotator.annotate(record) == {}
    engine = RuleEngine(rules)
    result = engine.evaluate(record)
    apply_annotation_constraints(record, result, engine)
    assert result.decision == FilterDecision.REVIEW
    assert "language:working_language_unconfirmed" in result.risks


def test_annotate_cli_is_offline_by_default_even_with_ai_configured(tmp_path, monkeypatch) -> None:
    config = _config(tmp_path, ai=True, annotation=False)
    _sources(monkeypatch, _record())
    pipeline = JobIntakePipeline(config)
    pipeline.run()
    monkeypatch.setattr("job_intake.cli.load_app_config", lambda path: config)

    def forbidden(*args, **kwargs):
        pytest.fail("Default annotate command attempted an external action")

    monkeypatch.setattr("job_intake.pipeline.build_adapter", forbidden)
    monkeypatch.setattr("job_intake.annotation.ai.AnnotationModelClient.generate", forbidden)
    monkeypatch.setattr("job_intake.alerts.telegram.TelegramNotifier.send", forbidden)
    result = CliRunner().invoke(app, ["annotate", "--config", "fixture.yaml", "--limit", "1"])
    assert result.exit_code == 0, result.output
    assert "Annotated 1 jobs" in result.output
    assert "Extraction calls=0, review calls=0" in result.output
    with pipeline.database.session() as session:
        assert session.scalar(select(JobORM)).annotation["method"] == "deterministic"


def test_annotate_cli_with_ai_requires_key_before_opening_or_mutating_database(
    tmp_path, monkeypatch,
) -> None:
    config = _config(tmp_path)
    monkeypatch.delenv(TEST_KEY_ENV, raising=False)
    monkeypatch.setattr("job_intake.cli.load_app_config", lambda path: config)

    def forbidden(*args, **kwargs):
        pytest.fail("Missing-key annotate command touched the database")

    monkeypatch.setattr("job_intake.cli.JobIntakePipeline", forbidden)
    result = CliRunner().invoke(app, ["annotate", "--config", "fixture.yaml", "--ai"])
    assert result.exit_code == 1
    assert "Set " + TEST_KEY_ENV + " locally" in result.output
    assert not (tmp_path / "jobs.db").exists()
