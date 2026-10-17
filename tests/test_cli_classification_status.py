from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from job_intake import cli
from job_intake.config.settings import AppConfig, LLMConfig, TelegramConfig
from job_intake.pipeline import JobIntakePipeline


@pytest.mark.parametrize("command", ["run", "reevaluate", "annotate"])
def test_classification_failure_is_visible_even_when_vacancy_is_saved(
    tmp_path, monkeypatch, command,
):
    stats = JobIntakePipeline._stats()
    stats.update(persisted=1, evaluations=1, classification_calls=1, classification_errors=1)
    pipeline = SimpleNamespace(run=lambda: stats, reevaluate_saved=lambda **kwargs: stats)
    monkeypatch.setattr(cli, "build_pipeline", lambda _: pipeline)
    monkeypatch.setattr(cli, "JobIntakePipeline", lambda _: pipeline)
    settings = AppConfig(
        database_url=f"sqlite:///{tmp_path / 'never-opened.db'}", log_level="INFO",
        rules_path=tmp_path / "rules.yaml", search_profiles_path=tmp_path / "profiles.yaml",
        company_watchlist_path=tmp_path / "companies.yaml", export_dir=tmp_path,
        sources=[], telegram=TelegramConfig(), llm=LLMConfig(),
    )
    monkeypatch.setattr(cli, "load_app_config", lambda _: settings)
    result = CliRunner().invoke(cli.app, [command, "--config", "unused.yaml"])
    assert result.exit_code == 1, result.output
    assert "classification_calls=1" in result.output
    assert "classification_errors=1" in result.output
    assert not (tmp_path / "never-opened.db").exists()
