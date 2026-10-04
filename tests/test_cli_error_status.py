from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from job_intake.cli import app
from job_intake.pipeline import JobIntakePipeline


def test_run_reports_annotation_failures_and_returns_failure(monkeypatch):
    stats = JobIntakePipeline._stats()
    stats.update(ingested=1, persisted=1, evaluations=4, annotation_errors=1)
    monkeypatch.setattr(
        "job_intake.cli.build_pipeline", lambda _: SimpleNamespace(run=lambda: stats),
    )
    result = CliRunner().invoke(app, ["run", "--config", "unused.yaml"])
    assert result.exit_code == 1
    assert "annotation_errors=1" in result.output


def test_run_with_complete_annotation_remains_successful(monkeypatch):
    stats = JobIntakePipeline._stats()
    monkeypatch.setattr(
        "job_intake.cli.build_pipeline", lambda _: SimpleNamespace(run=lambda: stats),
    )
    result = CliRunner().invoke(app, ["run", "--config", "unused.yaml"])
    assert result.exit_code == 0
    assert "annotation_errors=0" in result.output


@pytest.mark.parametrize("field", ["llm_batch_pending", "llm_batch_errors"])
def test_run_reports_unfinished_batch_as_failure(monkeypatch, field):
    stats = JobIntakePipeline._stats()
    stats[field] = 1
    monkeypatch.setattr(
        "job_intake.cli.build_pipeline", lambda _: SimpleNamespace(run=lambda: stats),
    )
    result = CliRunner().invoke(app, ["run", "--config", "unused.yaml"])
    assert result.exit_code == 1
    assert f"{field}=1" in result.output
