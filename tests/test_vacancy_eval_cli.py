"""CLI compatibility and input protection for the read-only phase-zero tools."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from job_intake import cli
from job_intake.config.settings import AppConfig, LLMConfig, TelegramConfig


@pytest.fixture
def settings(tmp_path, monkeypatch):
    config = tmp_path / "config" / "settings.yaml"
    config.parent.mkdir()
    config.write_text("protected config")
    database = tmp_path / "jobs.db"
    database.write_bytes(b"protected database")
    value = AppConfig(
        database_url=f"sqlite:///{database}", log_level="INFO", sources=[],
        rules_path=config.parent / "rules.yaml",
        search_profiles_path=config.parent / "profiles.yaml",
        company_watchlist_path=config.parent / "companies.yaml", export_dir=tmp_path,
        telegram=TelegramConfig(), llm=LLMConfig(),
    )
    monkeypatch.setattr(cli, "load_app_config", lambda _: value)

    def forbidden(*args, **kwargs):
        raise AssertionError("Evaluation must not construct the application pipeline")

    monkeypatch.setattr(cli, "build_pipeline", forbidden)
    monkeypatch.setattr(cli, "JobIntakePipeline", forbidden)
    return value, config, database


@pytest.mark.parametrize("command", [
    ["eval", "accuracy"], ["eval", "rules-diff"], ["gold", "sample"], ["gold", "validate"],
])
def test_new_command_help_never_opens_database(command):
    result = CliRunner().invoke(cli.app, [*command, "--help"])
    assert result.exit_code == 0, result.output
    assert "--config" in result.output


@pytest.mark.parametrize("command", ["sample", "validate", "accuracy", "rules-diff"])
def test_new_reports_cannot_replace_database(settings, command, tmp_path):
    _, config, database = settings
    gold = tmp_path / "gold.jsonl"
    gold.write_text("original human labels")
    before = database.read_bytes()
    options = ["--config", str(config)]
    if command == "sample":
        args = ["gold", command, *options, "--out", str(database)]
    elif command == "validate":
        args = ["gold", command, *options, "--gold", str(gold), "--output", str(database)]
    elif command == "accuracy":
        args = ["eval", command, *options, "--gold", str(gold), "--output", str(database)]
    else:
        args = ["eval", command, *options, "--baseline", "HEAD", "--output", str(database)]
    result = CliRunner().invoke(cli.app, args)
    assert result.exit_code == 2, result.output
    assert "must not overwrite" in result.output
    assert database.read_bytes() == before


def test_accuracy_markdown_cannot_replace_gold(settings, tmp_path):
    _, config, _ = settings
    gold = tmp_path / "report.md"
    gold.write_text("human labels must survive")
    result = CliRunner().invoke(cli.app, [
        "eval", "accuracy", "--config", str(config), "--gold", str(gold),
        "--output", str(gold.with_suffix(".json")),
    ])
    assert result.exit_code == 2, result.output
    assert "must not overwrite" in result.output
    assert gold.read_text() == "human labels must survive"
    assert not gold.with_suffix(".json").exists()


def test_accuracy_requires_distinct_json_and_markdown(settings, tmp_path):
    _, config, _ = settings
    result = CliRunner().invoke(cli.app, [
        "eval", "accuracy", "--config", str(config), "--gold", str(tmp_path / "gold.jsonl"),
        "--output", str(tmp_path / "report.md"),
    ])
    assert result.exit_code == 2, result.output
    assert "must be distinct" in result.output
    assert not (tmp_path / "report.md").exists()
