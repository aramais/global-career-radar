import hashlib
import json
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from typer.testing import CliRunner

from job_intake import sources_cli
from job_intake.config.settings import AppConfig, LLMConfig, SourceDefinition, TelegramConfig
from job_intake.models.job import JobRecord
from job_intake.pipeline import JobIntakePipeline
from job_intake.storage.models import AlertOutboxORM, JobORM, SourceRunORM


@pytest.fixture
def settings(tmp_path):
    rules = tmp_path / "rules.yaml"
    profiles = tmp_path / "profiles.yaml"
    rules.write_text("recall_first: true\ntarget_geographies: [Brazil, worldwide]\n")
    profiles.write_text("title_weights: {analytics manager: 8}\n")
    return AppConfig(
        database_url=f"sqlite:///{tmp_path / 'jobs.db'}", log_level="WARNING",
        rules_path=rules, search_profiles_path=profiles, company_watchlist_path=tmp_path / "none",
        export_dir=tmp_path, telegram=TelegramConfig(enabled=False), llm=LLMConfig(enabled=False),
        sources=[SourceDefinition("dailyremote", "dailyremote", params={
            "auth_policy": "allow_anonymous", "search_urls": ["https://dailyremote.com/remote-jobs"],
        })],
    )


def saved(settings, *, count=1):
    pipeline = JobIntakePipeline(settings)
    uids = []
    for index in range(count):
        record = JobRecord(
            source="dailyremote", source_job_id=str(index), company="Example",
            title="Analytics Manager", original_url=f"https://dailyremote.com/remote-job/job-{index}",
            location_text="Brazil", description_clean=f"Lead analytics project {index}.",
            source_metadata={"description_complete": False,
                             "listing_url": f"https://dailyremote.com/search-{index % 2}"},
        )
        items, keys, _ = pipeline._evaluate_record(record, pipeline._stats(), use_llm=False)
        pipeline._save(items, keys, observed=True, queue_alert=False)
        uids.append(pipeline.deduplicator.build_identity(record).job_uid)
    return pipeline, uids


def test_check_missing_database_does_not_create_or_migrate(settings):
    path = sources_cli.sqlite_path(settings.database_url)
    assert sources_cli.stored_health(settings)["database_exists"] is False
    assert not path.exists()


def test_stored_check_and_refresh_preview_preserve_db_bytes(settings, monkeypatch):
    pipeline, _uids = saved(settings, count=2)
    with pipeline.database.session() as session:
        session.add(SourceRunORM(source="dailyremote", status="partial", snapshot_complete=False,
                                 record_count=2, details={"scope": "search"}))
        session.commit()
    path = sources_cli.sqlite_path(settings.database_url)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    result = sources_cli.stored_health(settings)
    assert result["runs"][0]["status"] == "partial" and result["api_calls"] == 0
    preview = sources_cli.refresh_details(settings, dry_run=True)
    assert preview["selected"] == 2 and preview["persisted"] == 0
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_refresh_selection_alternates_listing_contexts(settings):
    _pipeline, uids = saved(settings, count=4)
    selected = sources_cli.refresh_candidates(settings, limit=4, stale_days=7)
    contexts = [record.source_metadata["listing_url"] for _uid, record, _reason in selected]
    assert contexts[0] != contexts[1] and contexts[2] != contexts[3]
    assert {uid for uid, _record, _reason in selected} == set(uids)


def test_fresh_full_description_is_not_refetched_without_reason(settings):
    pipeline, uids = saved(settings)
    with pipeline.database.session() as session:
        job = session.scalar(select(JobORM))
        job.source_metadata = {**job.source_metadata, "description_complete": True,
                               "detail_fetched_at": datetime.now(UTC).isoformat()}
        session.commit()
    assert sources_cli.refresh_candidates(settings, limit=20, stale_days=7) == []
    selected = sources_cli.refresh_candidates(settings, limit=20, stale_days=7,
                                              now=datetime.now(UTC) + timedelta(days=8))
    assert selected[0][0] == uids[0] and selected[0][2] == "expired"


def test_refresh_writes_only_offline_evaluation_and_preserves_last_seen(settings, monkeypatch):
    pipeline, uids = saved(settings)
    with pipeline.database.session() as session:
        before = session.scalar(select(JobORM)).last_seen_at
    def fetched(_settings, record, _cache):
        result = deepcopy(record)
        result.description_clean = "Lead pricing experiments and analytics projects."
        result.source_metadata = {**result.source_metadata, "description_complete": True}
        return result
    monkeypatch.setattr(sources_cli, "fetch_saved_detail", fetched)
    result = sources_cli.refresh_details(settings)
    assert result["persisted"] == 1
    assert result["ai_calls"] == result["notifications"] == 0
    with pipeline.database.session() as session:
        job = session.scalar(select(JobORM))
        assert job.job_uid == uids[0] and job.last_seen_at == before
        assert "pricing experiments" in job.description_clean
        assert session.scalar(select(func.count()).select_from(AlertOutboxORM)) == 0


@pytest.mark.parametrize(("code", "body", "expected"), [
    (200, "Active vacancy", "reachable"),
    (200, "Esta vaga foi encerrada", "explicit_closed_page"),
    (200, "This job is closed", "explicit_closed_page"),
    (404, "Missing", "not_found_unconfirmed"),
    (410, "Gone", "not_found_unconfirmed"),
    (403, "Sign in", "auth_required"),
    (302, "", "redirect_unchecked"),
    (503, "Unavailable", "http_error"),
])
def test_recheck_typed_outcomes_never_close_saved_jobs(settings, monkeypatch, code, body, expected):
    pipeline, _uids = saved(settings)
    class Session:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def get(self, _url, **kwargs):
            assert kwargs == {"timeout": 20, "allow_redirects": False}
            return SimpleNamespace(status_code=code, text=body, close=lambda: None)
    monkeypatch.setattr(sources_cli, "build_session", Session)
    path = sources_cli.sqlite_path(settings.database_url)
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    result = sources_cli.recheck_urls(settings)
    assert result["outcomes"][0]["outcome"] == expected
    assert result["database_writes"] == result["automatic_closures"] == 0
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    with pipeline.database.session() as session:
        assert session.scalar(select(JobORM)).status == "open"


def test_recheck_respects_required_auth_without_anonymous_fallback(settings):
    _pipeline, _uids = saved(settings)
    settings = replace(settings, sources=[SourceDefinition("dailyremote", "dailyremote", params={
        "auth_policy": "require_authenticated",
    })])
    result = sources_cli.recheck_urls(settings)
    assert result["outcomes"][0]["outcome"] == "auth_required"


def test_sources_cli_exposes_read_only_default_check_and_refresh_preview(settings, monkeypatch):
    saved(settings)
    monkeypatch.setattr(sources_cli, "load_app_config", lambda _path: settings)
    runner = CliRunner()
    checked = runner.invoke(sources_cli.app, ["check"])
    assert checked.exit_code == 0, checked.output
    assert json.loads(checked.output)["mode"] == "stored"
    preview = runner.invoke(sources_cli.app, ["refresh-details", "--dry-run", "--limit", "1"])
    assert preview.exit_code == 0, preview.output
    assert json.loads(preview.output)["selected"] == 1


def test_failed_refresh_cannot_reuse_old_success_or_extend_full_source_ttl(settings, monkeypatch):
    from job_intake.adapters.html_page import HtmlPageAdapter

    record = JobRecord(source="html", company="Example", title="Analytics Manager",
                       original_url="https://other.example/jobs/1", description_clean="Full source",
                       source_metadata={"description_complete": True, "detail_outcome": "success"})
    monkeypatch.setattr(sources_cli, "_adapter_for_record", lambda *_: HtmlPageAdapter(
        "html", {"url": "https://configured.example/careers"},
    ))
    with pytest.raises(ValueError, match="incomplete"):
        sources_cli.fetch_saved_detail(settings, record, {})
    assert record.source_metadata["description_complete"] is True
    assert record.source_metadata["detail_outcome"] == "success"
