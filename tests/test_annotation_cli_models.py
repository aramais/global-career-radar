from __future__ import annotations

from dataclasses import replace

import pytest
from typer.testing import CliRunner

from job_intake import cli
from job_intake.config.settings import (
    AnnotationConfig,
    AppConfig,
    LLMConfig,
    TelegramConfig,
)


@pytest.fixture
def loaded_config(tmp_path, monkeypatch):
    config = AppConfig(
        database_url=f"sqlite:///{tmp_path / 'never-opened.db'}", log_level="INFO",
        rules_path=tmp_path / "rules.yaml", search_profiles_path=tmp_path / "profiles.yaml",
        company_watchlist_path=tmp_path / "companies.yaml", export_dir=tmp_path,
        sources=[], telegram=TelegramConfig(enabled=True), llm=LLMConfig(enabled=True),
        annotation=AnnotationConfig(api_key_env="ANNOTATION_CLI_SHARED_KEY"),
    )
    monkeypatch.setattr(cli, "load_app_config", lambda path: config)
    for name in (
        "ANNOTATION_CLI_SHARED_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "TYPESAFE_API_KEY",
        "ANNOTATION_CLI_EXTRACT_KEY", "ANNOTATION_CLI_REVIEW_KEY", "LLM_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    return config


@pytest.fixture
def pipeline_spy(monkeypatch):
    calls = []

    class Pipeline:
        def __init__(self, settings):
            calls.append(settings)

        def reevaluate_saved(self, **kwargs):
            calls.append(kwargs)
            return {
                "persisted": 2, "evaluations": 2, "annotation_extraction_calls": 0,
                "annotation_review_calls": 0, "annotation_cache_hits": 0,
                "annotation_errors": 0, "record_errors": 0, "errors": [],
            }

    monkeypatch.setattr(cli, "JobIntakePipeline", Pipeline)
    return calls


def invoke(*args):
    return CliRunner().invoke(cli.app, ["annotate", "--config", "fixture.yaml", *args])


def test_ai_checks_review_key_even_when_extraction_key_exists(
    loaded_config, pipeline_spy, monkeypatch,
):
    monkeypatch.setenv("OPENAI_API_KEY", "fake-extraction-secret")
    result = invoke("--ai", "--preset", "budget-jev")
    assert result.exit_code == 1, result.output
    assert "Set TYPESAFE_API_KEY locally" in result.output
    assert "fake-extraction-secret" not in result.output
    assert pipeline_spy == []
    assert not loaded_config.rules_path.with_name("never-opened.db").exists()


def test_ai_reports_both_missing_keys_before_any_pipeline_mutation(loaded_config, pipeline_spy):
    result = invoke("--ai", "--preset", "budget-jev")
    assert result.exit_code == 1
    assert "Set OPENAI_API_KEY locally" in result.output
    assert "Set TYPESAFE_API_KEY locally" in result.output
    assert pipeline_spy == []


def test_shared_missing_key_message_is_deduplicated(loaded_config, pipeline_spy):
    result = invoke("--ai")
    assert result.exit_code == 1
    assert result.output.count("Set ANNOTATION_CLI_SHARED_KEY locally") == 1
    assert pipeline_spy == []


def test_whitespace_key_is_missing(loaded_config, pipeline_spy, monkeypatch):
    monkeypatch.setenv("ANNOTATION_CLI_SHARED_KEY", " \n")
    result = invoke("--ai")
    assert result.exit_code == 1
    assert pipeline_spy == []


def test_budget_jev_preset_resolves_independent_routes(loaded_config, pipeline_spy, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "fake-extract")
    monkeypatch.setenv("TYPESAFE_API_KEY", "fake-review")
    result = invoke("--ai", "--preset", "budget-jev", "--limit", "3")
    assert result.exit_code == 0, result.output
    annotation = pipeline_spy[0].annotation
    extract, review = annotation.resolved_stage("extract"), annotation.resolved_stage("review")
    assert (extract.provider, extract.model, extract.reasoning_effort) == (
        "openai", "gpt-6-luna", "none",
    )
    assert (review.provider, review.model, review.api_key_env, review.base_url) == (
        "jev", "jev-1.13.0", "TYPESAFE_API_KEY", "https://api.typesafe.ai/v1",
    )
    assert pipeline_spy[1] == {"use_ai": True, "limit": 3}
    assert pipeline_spy[0].llm.enabled is False
    assert pipeline_spy[0].telegram.enabled is False


def test_balanced_preset_remains_offline_without_ai(loaded_config, pipeline_spy):
    result = invoke("--preset", "balanced")
    assert result.exit_code == 0, result.output
    annotation = pipeline_spy[0].annotation
    assert annotation.ai_enabled is False
    assert annotation.resolved_stage("extract").model == "gemini-3.5-flash-lite"
    assert annotation.resolved_stage("extract").reasoning_effort == "minimal"
    assert annotation.resolved_stage("review").model == "gemini-3.8-flash"
    assert pipeline_spy[1]["use_ai"] is False


def test_explicit_stage_flags_override_preset(loaded_config, pipeline_spy, monkeypatch):
    monkeypatch.setenv("ANNOTATION_CLI_EXTRACT_KEY", "fake-extract")
    monkeypatch.setenv("ANNOTATION_CLI_REVIEW_KEY", "fake-review")
    result = invoke(
        "--ai", "--preset", "budget-jev", "--extract-provider", "openai_compatible",
        "--extract-model", "vendor/extract-model:free", "--extract-base-url",
        "http://127.0.0.1:1234/v1", "--extract-api-key-env", "ANNOTATION_CLI_EXTRACT_KEY",
        "--review-provider", "openrouter", "--review-model", "vendor/review-model",
        "--review-base-url", "https://router.example/v1", "--review-api-key-env",
        "ANNOTATION_CLI_REVIEW_KEY", "--review-reasoning-effort", "high",
    )
    assert result.exit_code == 0, result.output
    annotation = pipeline_spy[0].annotation
    extract, review = annotation.resolved_stage("extract"), annotation.resolved_stage("review")
    assert (extract.provider, extract.model, extract.base_url) == (
        "openai_compatible", "vendor/extract-model:free", "http://127.0.0.1:1234/v1",
    )
    assert (review.provider, review.model, review.base_url, review.reasoning_effort) == (
        "openrouter", "vendor/review-model", "https://router.example/v1", "high",
    )


def test_stage_switch_uses_provider_key_not_shared_legacy_key(
    loaded_config, pipeline_spy, monkeypatch,
):
    monkeypatch.setenv("ANNOTATION_CLI_SHARED_KEY", "fake-gemini-key")
    result = invoke("--ai", "--extract-provider", "openai", "--extract-model", "gpt-6-luna")
    assert result.exit_code == 1
    assert "Set OPENAI_API_KEY locally" in result.output
    assert "Set ANNOTATION_CLI_SHARED_KEY locally" not in result.output
    assert pipeline_spy == []


def test_legacy_provider_switch_preserves_old_model_defaults(loaded_config, pipeline_spy):
    result = invoke("--provider", "openai")
    assert result.exit_code == 0, result.output
    annotation = pipeline_spy[0].annotation
    assert annotation.extract_model == "gpt-5-mini"
    assert annotation.review_model == "gpt-5"
    assert annotation.resolved_stage("extract").api_key_env == "OPENAI_API_KEY"
    assert annotation.resolved_stage("review").provider == "openai"


def test_legacy_provider_switch_clears_prior_stage_routes(loaded_config, pipeline_spy):
    loaded_config.annotation = replace(
        loaded_config.annotation, review_provider="jev", review_model="jev-1.13.0",
        review_api_key_env="ANNOTATION_CLI_REVIEW_KEY",
    )
    result = invoke("--provider", "gemini")
    assert result.exit_code == 0, result.output
    annotation = pipeline_spy[0].annotation
    assert annotation.resolved_stage("review").provider == "gemini"
    assert annotation.review_model == "gemini-2.5-pro"
    assert annotation.resolved_stage("review").api_key_env == "GEMINI_API_KEY"


@pytest.mark.parametrize("args", [
    ("--preset", "unknown"), ("--preset", "balanced", "--provider", "openai"),
    ("--review-provider", "unknown"), ("--review-provider", "openai_compatible"),
    ("--review-base-url", "https://user:secret@api.example/v1"),
    ("--extract-provider", "jev", "--extract-model", "jev-1.13.0"),
    ("--review-model", "gemini-2.5-flash-lite"),
])
def test_invalid_cli_model_configuration_fails_before_pipeline(loaded_config, pipeline_spy, args):
    result = invoke(*args)
    assert result.exit_code == 2, result.output
    assert pipeline_spy == []


def test_shared_compatible_provider_requires_explicit_models_and_stage_endpoints(
    loaded_config, pipeline_spy,
):
    result = invoke(
        "--provider", "openai_compatible", "--extract-model", "extract-model",
        "--review-model", "review-model", "--extract-base-url", "https://extract.example/v1",
        "--review-base-url", "https://review.example/v1",
    )
    assert result.exit_code == 0, result.output
    assert pipeline_spy[0].annotation.resolved_stage("review").api_key_env == "LLM_API_KEY"
