import json
import socket
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from job_intake.config.settings import load_app_config
from job_intake.models.job import JobRecord
from job_intake.pipeline import JobIntakePipeline
from job_intake.role_migration import complete_migration, snapshot, verify_preservation


@pytest.fixture
def legacy(tmp_path):
    config = load_app_config(Path(__file__).parents[1] / "config/settings.yaml")
    config.database_url = f"sqlite:///{tmp_path / 'jobs.db'}"
    pipeline = JobIntakePipeline(config)
    for index, title in enumerate(("Data Science Manager", "Software Engineer", "Product Manager")):
        record = JobRecord(
            source="test", source_job_id=str(index), company="Test", title=title,
            original_url=f"https://example.test/job/{index}",
            posted_at=datetime(2026, 10, 13, 12, tzinfo=UTC),
            description_raw="Lead the team. English is the working language. Remote in Brazil.",
            description_clean="Lead the team. English is the working language. Remote in Brazil.",
        )
        items, keys, _ = pipeline._evaluate_record(record, pipeline._stats(), use_llm=False)
        pipeline._save(items, keys, observed=True, queue_alert=False)
    pipeline.database.engine.dispose()
    path = tmp_path / "jobs.db"
    with sqlite3.connect(path) as db:
        db.execute("UPDATE jobs SET archived_at='2026-10-15 12:00:00' WHERE source_job_id='2'")
        for index in db.execute("PRAGMA index_list(job_relevance_evaluations)").fetchall():
            if index[3] == "c":
                db.execute(f'DROP INDEX "{index[1]}"')
        db.execute("ALTER TABLE job_relevance_evaluations RENAME TO job_profile_evaluations")
        db.execute("DROP INDEX ix_jobs_role_label")
        db.execute("ALTER TABLE jobs DROP COLUMN role_label")
        db.execute("ALTER TABLE jobs DROP COLUMN role_classification")
        db.execute("INSERT INTO job_feedback(job_uid,label,note,created_at) "
                   "SELECT job_uid,'interested','Preserve',created_at FROM jobs LIMIT 1")
    (tmp_path / "jobs.csv").write_text("old,export\n")
    (tmp_path / "review.html").write_text("<p>Old report</p>")
    return config, path


def test_dry_run_preserves_schema_files_and_directory(legacy):
    config, path = legacy
    before = {file.name: file.read_bytes() for file in path.parent.iterdir() if file.is_file()}
    report = complete_migration(config)
    assert report == {"database": str(path), "mode": "dry-run", "jobs": 3,
                      "active": 2, "archived": 1, "api_calls": 0}
    assert {file.name: file.read_bytes() for file in path.parent.iterdir()
            if file.is_file()} == before
    assert not (path.parent / "backups").exists()


def test_apply_backs_up_legacy_before_constructor_and_is_repeatable(legacy):
    config, path = legacy
    before = snapshot(path)
    report = complete_migration(config, apply=True)
    assert report["status"] == "verified"
    assert report["current"]["active_current"] == 2
    assert report["current"]["role_counts"] == {"data-science-management": 1, "other": 1}
    assert report["exports"]["csv_rows"] == 2
    assert report["exports"]["html_rows"] == 2
    assert report["offline_attempts"] == {"network": 0, "model": 0, "telegram": 0}
    backup = Path(report["backup"])
    assert (backup / "jobs.csv").read_text() == "old,export\n"
    assert (backup / "review.html").read_text() == "<p>Old report</p>"
    assert snapshot(backup / path.name) == before
    with sqlite3.connect(backup / path.name) as db:
        assert "role_label" not in {row[1] for row in db.execute("PRAGMA table_info(jobs)")}
    assert json.loads((backup / "manifest.json").read_text())["baseline"] == before
    verify_preservation(before, path)
    second = complete_migration(config, apply=True)
    assert second["current"] == report["current"]
    assert second["backup"] != report["backup"]
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT count(*) FROM job_relevance_evaluations").fetchone()[0] == 2
        archived = db.execute(
            "SELECT role_label FROM jobs WHERE archived_at IS NOT NULL",
        ).fetchone()
        assert archived[0] is None


def test_failure_keeps_backup_and_does_not_silently_restore(legacy, monkeypatch):
    config, path = legacy
    before = snapshot(path)

    def change_feedback(_self, **_kwargs):
        with sqlite3.connect(path) as db:
            db.execute("UPDATE job_feedback SET note='Concurrent edit'")
        raise RuntimeError("Interrupted")

    monkeypatch.setattr(JobIntakePipeline, "reevaluate_saved", change_feedback)
    with pytest.raises(RuntimeError, match="Interrupted"):
        complete_migration(config, apply=True)
    reports = list((path.parent / "backups").glob("*/completion.json"))
    assert len(reports) == 1
    report = json.loads(reports[0].read_text())
    assert report["status"] == "failed"
    assert snapshot(reports[0].parent / path.name) == before
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT note FROM job_feedback").fetchone()[0] == "Concurrent edit"


def test_immutable_source_digest_is_independent_of_derived_annotation_order(legacy):
    _, path = legacy
    before = snapshot(path)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE jobs SET annotation=? WHERE source_job_id='0'", ('{"z":1}',))
        db.execute("UPDATE jobs SET annotation=? WHERE source_job_id='1'", ('{"a":1}',))
    assert snapshot(path)["immutable_jobs_sha256"] == before["immutable_jobs_sha256"]
    verify_preservation(before, path)


def test_network_regression_is_blocked_before_connect(legacy, monkeypatch):
    config, path = legacy

    def unexpected_network(_self, **_kwargs):
        socket.create_connection(("example.test", 443))

    monkeypatch.setattr(JobIntakePipeline, "reevaluate_saved", unexpected_network)
    with pytest.raises(RuntimeError, match="forbids network"):
        complete_migration(config, apply=True)
    report = next((path.parent / "backups").glob("*/completion.json"))
    assert json.loads(report.read_text())["status"] == "failed"
