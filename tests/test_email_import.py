"""Mail ingestion uses the existing profiles and keeps mailbox operations outside CLI."""

import json
from pathlib import Path

import pytest
import yaml
from sqlalchemy import func, select
from typer.testing import CliRunner

from job_intake import cli
from job_intake.config import settings as settings_module
from job_intake.config.settings import AppConfig, LLMConfig, SourceDefinition, TelegramConfig
from job_intake.storage.database import Database
from job_intake.storage.models import AlertOutboxORM, JobORM, JobProfileEvaluationORM


@pytest.fixture
def mail_config(tmp_path):
    config_dir = Path(__file__).resolve().parents[1] / "config"
    return AppConfig(
        database_url=f"sqlite:///{tmp_path / 'jobs.db'}",
        log_level="WARNING",
        rules_path=config_dir / "rules.yaml",
        search_profiles_path=config_dir / "search_profiles.yaml",
        company_watchlist_path=config_dir / "company_watchlist.yaml",
        export_dir=tmp_path,
        sources=[SourceDefinition("other-source", "dailyremote")],
        telegram=TelegramConfig(enabled=True),
        llm=LLMConfig(enabled=True),
    )


def test_gmail_cli_import_is_offline_private_and_idempotent(tmp_path, monkeypatch, mail_config):
    snapshot = tmp_path / "messages.json"
    snapshot.write_text(
        json.dumps(
            {
                "messages": [
                    {
                        "id": "synthetic-gmail-id",
                        "internal_date": "1790946210000",
                        "payload": {
                            "mime_type": "text/html",
                            "headers": [
                                {"name": "From", "value": "private@example.org"},
                                {"name": "To", "value": "recipient@example.org"},
                                {"name": "Subject", "value": "Private subject"},
                            ],
                            "body": {
                                "content": '<article><a href="https://www.linkedin.com/comm/jobs/'
                                'view/123/?trackingId=synthetic-private-tracking">'
                                "Product Manager at Acme</a>"
                                "<p>Own discovery and roadmap. Remote in Brazil.</p></article>"
                            },
                        },
                    }
                ]
            }
        )
    )
    monkeypatch.setattr(cli, "load_app_config", lambda _: mail_config)

    def no_network(*args, **kwargs):
        raise AssertionError("Email import must not make network calls")

    monkeypatch.setattr("requests.Session.request", no_network)
    runner = CliRunner()
    for _ in range(2):
        result = runner.invoke(cli.app, ["import-gmail", str(snapshot)])
        assert result.exit_code == 0, result.output
        assert "persisted=1" in result.output
        assert "evaluations=4" in result.output

    database = Database(mail_config.database_url)
    with database.session() as session:
        assert session.scalar(select(func.count()).select_from(JobORM)) == 1
        assert session.scalar(select(func.count()).select_from(JobProfileEvaluationORM)) == 4
        assert session.scalar(select(func.count()).select_from(AlertOutboxORM)) == 0
        job = session.scalar(select(JobORM))
        assert job.source == "email-vacancies"
        assert job.original_url == "https://www.linkedin.com/jobs/view/123"
        assert job.status == "unknown"
        assert job.posted_at is None
        assert job.source_metadata["description_complete"] is False
        assert job.source_metadata["email_date"].startswith("2026-10-02")
        assert not job.alert_pending
        stored = json.dumps(job.source_metadata) + job.description_clean + job.original_url
        assert "private@example.org" not in stored
        assert "recipient@example.org" not in stored
        assert "Private subject" not in stored
        assert "synthetic-private-tracking" not in stored


def test_eml_cli_import_does_not_run_other_sources(tmp_path, monkeypatch, mail_config):
    directory = tmp_path / "emails"
    directory.mkdir()
    (directory / "selected.eml").write_text(
        "Date: Fri, 02 Oct 2026 13:00:00 +0000\n"
        "Content-Type: text/plain; charset=utf-8\n\n"
        "Head of Analytics at Acme\nhttps://example.com/jobs/123\n"
        "Pricing and experimentation.\n"
    )
    monkeypatch.setattr(cli, "load_app_config", lambda _: mail_config)
    result = CliRunner().invoke(cli.app, ["import-email", str(directory)])
    assert result.exit_code == 0, result.output
    assert "persisted=1" in result.output
    assert "evaluations=4" in result.output


def test_failed_mail_import_reports_failure_without_mail_contents(
    tmp_path, monkeypatch, mail_config
):
    snapshot = tmp_path / "malformed.json"
    snapshot.write_text('{"messages": [{"id": "private@example.org"}]}')
    monkeypatch.setattr(cli, "load_app_config", lambda _: mail_config)
    result = CliRunner().invoke(cli.app, ["import-gmail", str(snapshot)])
    assert result.exit_code == 1
    assert "source_errors=1" in result.output
    assert "private@example.org" not in result.output


@pytest.mark.parametrize("command", ["import-email", "import-gmail"])
def test_mail_cli_preserves_symlink_for_adapter_rejection(
    tmp_path, monkeypatch, mail_config, command
):
    target = tmp_path / "actual-input"
    if command == "import-email":
        target.mkdir()
    else:
        target.write_text('{"messages": []}')
    link = tmp_path / "linked-input"
    link.symlink_to(target, target_is_directory=target.is_dir())
    monkeypatch.setattr(cli, "load_app_config", lambda _: mail_config)
    result = CliRunner().invoke(cli.app, [command, str(link)])
    assert result.exit_code == 1, result.output
    assert "source_errors=1" in result.output


def test_mail_config_preserves_symlink_paths(tmp_path, monkeypatch):
    config_directory = tmp_path / "config"
    config_directory.mkdir()
    directory = tmp_path / "actual-emails"
    directory.mkdir()
    directory_link = tmp_path / "linked-emails"
    directory_link.symlink_to(directory, target_is_directory=True)
    snapshot = tmp_path / "actual-snapshot.json"
    snapshot.write_text('{"messages": []}')
    snapshot_link = tmp_path / "linked-snapshot.json"
    snapshot_link.symlink_to(snapshot)
    path = config_directory / "settings.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "sources": [
                    {
                        "name": "files",
                        "type": "email_files",
                        "params": {"directory": "linked-emails"},
                    },
                    {
                        "name": "gmail",
                        "type": "gmail_snapshot",
                        "params": {"snapshot_file": "linked-snapshot.json"},
                    },
                ]
            }
        )
    )
    monkeypatch.setattr(settings_module, "load_dotenv", lambda _: None)
    settings = settings_module.load_app_config(path)
    assert Path(settings.sources[0].params["directory"]) == directory_link
    assert Path(settings.sources[1].params["snapshot_file"]) == snapshot_link
