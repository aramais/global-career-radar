"""Progress remains observational: temporary databases, fake models and no live requests."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest
import requests
from sqlalchemy import select
from test_annotation_ai import gemini_response, install_responses
from test_annotation_integration import TEST_KEY_ENV, _config, _record, _sources
from test_annotation_jev import answer, claim, response, spec
from test_annotation_service import ModelFake
from test_annotation_service import record as source_record
from typer.testing import CliRunner

from job_intake.annotation import ai
from job_intake.annotation.ai import AnnotationModelClient, AnnotationModelError
from job_intake.annotation.jev import JevReviewClient
from job_intake.annotation.progress import AnnotationProgressReporter
from job_intake.annotation.service import VacancyAnnotator
from job_intake.cli import app
from job_intake.config.settings import AnnotationConfig
from job_intake.pipeline import JobIntakePipeline
from job_intake.storage.models import JobORM, JobProfileEvaluationORM


@pytest.fixture(autouse=True)
def forbid_live_requests(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Progress test attempted a live HTTP request")

    monkeypatch.setattr(requests, "post", forbidden)


def fake_annotator(config, progress=None):
    annotator = VacancyAnnotator(config, progress=progress)
    fake = ModelFake(config)
    annotator.client = fake
    return annotator, fake


def events_named(events, name):
    return [event for event in events if event["event"] == name]


def saved_result(pipeline):
    with pipeline.database.session() as session:
        job = session.scalar(select(JobORM))
        evaluation = session.scalar(select(JobProfileEvaluationORM))
        return (
            job.annotation, evaluation.profile_version, evaluation.decision,
            evaluation.fit_score, evaluation.tier, evaluation.risks,
        )


@pytest.mark.parametrize("flag", ["--verbose", "-v"])
def test_verbose_offline_cli_keeps_summary_and_evaluations(tmp_path, monkeypatch, flag):
    config = _config(tmp_path, ai=True, annotation=False)
    _sources(monkeypatch, _record())
    pipeline = JobIntakePipeline(config)
    assert pipeline.run()["persisted"] == 1
    monkeypatch.setattr("job_intake.cli.load_app_config", lambda path: config)

    def forbidden(*args, **kwargs):
        pytest.fail("Offline progress enabled fetching, models or notifications")

    monkeypatch.setattr("job_intake.pipeline.build_adapter", forbidden)
    monkeypatch.setattr(AnnotationModelClient, "generate", forbidden)
    monkeypatch.setattr("job_intake.alerts.telegram.TelegramNotifier.send", forbidden)
    runner = CliRunner()
    quiet = runner.invoke(app, ["annotate", "--config", "fixture.yaml", "--limit", "1"])
    assert quiet.exit_code == 0, quiet.output
    before = saved_result(pipeline)
    verbose = runner.invoke(
        app, ["annotate", "--config", "fixture.yaml", "--limit", "1", flag],
    )
    assert verbose.exit_code == 0, verbose.output
    assert verbose.stdout == quiet.stdout
    assert quiet.stderr == ""
    assert "Starting annotation (offline)" in verbose.stderr
    assert "Loaded 1 vacancies" in verbose.stderr
    assert "Job 1/1: Example" in verbose.stderr
    assert "annotation local" in verbose.stderr
    assert "saved 1 profile evaluations" in verbose.stderr
    assert "Finished: 1/1" in verbose.stderr
    assert saved_result(pipeline) == before
    assert not Path(config.annotation.cache_dir).exists()


def test_verbose_ai_missing_review_key_stops_before_database(tmp_path, monkeypatch):
    config = _config(tmp_path)
    config.annotation = replace(
        config.annotation, extract_api_key_env=TEST_KEY_ENV,
        review_api_key_env="PROGRESS_REVIEW_TEST_KEY",
    )
    monkeypatch.setenv(TEST_KEY_ENV, "private-extraction-key")
    monkeypatch.delenv("PROGRESS_REVIEW_TEST_KEY", raising=False)
    monkeypatch.setattr("job_intake.cli.load_app_config", lambda path: config)

    def forbidden(*args, **kwargs):
        pytest.fail("Missing review key opened or mutated the database")

    monkeypatch.setattr("job_intake.cli.JobIntakePipeline", forbidden)
    result = CliRunner().invoke(app, ["annotate", "--ai", "--verbose"])
    assert result.exit_code == 1
    assert "Set PROGRESS_REVIEW_TEST_KEY locally" in result.stderr
    assert "Starting annotation" not in result.stderr
    assert "private-extraction-key" not in result.output
    assert not (tmp_path / "jobs.db").exists()


def test_verbose_ai_cli_reports_models_stages_and_saved_cache(tmp_path, monkeypatch):
    config = _config(tmp_path, annotation=False)
    _sources(monkeypatch, _record(description_clean=source_record().description_clean))
    pipeline = JobIntakePipeline(config)
    assert pipeline.run()["persisted"] == 1
    monkeypatch.setenv(TEST_KEY_ENV, "test-private-key")
    monkeypatch.setattr("job_intake.cli.load_app_config", lambda path: config)
    fake = ModelFake(config.annotation)
    monkeypatch.setattr(
        AnnotationModelClient, "generate", lambda self, model, prompt: fake.generate(model, prompt),
    )

    def forbidden(*args, **kwargs):
        pytest.fail("AI annotation progress attempted fetching or notifications")

    monkeypatch.setattr("job_intake.pipeline.build_adapter", forbidden)
    monkeypatch.setattr("job_intake.alerts.telegram.TelegramNotifier.send", forbidden)
    arguments = ["annotate", "--ai", "--verbose", "--limit", "1"]
    result = CliRunner().invoke(app, arguments)
    assert result.exit_code == 0, result.output
    extract, review = (config.annotation.resolved_stage(stage) for stage in ("extract", "review"))
    assert f"Extract: {extract.provider}/{extract.model}" in result.stderr
    assert f"review: {review.provider}/{review.model}" in result.stderr
    assert "source chunks" in result.stderr
    assert f"extract: {extract.provider}/{extract.model} started" in result.stderr
    assert f"review: {review.provider}/{review.model} started" in result.stderr
    assert "annotation reviewed" in result.stderr
    before = saved_result(pipeline)
    assert [stage for stage, _ in fake.calls] == ["extract", "review"]
    cached = CliRunner().invoke(app, arguments)
    assert cached.exit_code == 0, cached.output
    assert "cache hit (saved annotation)" in cached.stderr
    assert "Extraction calls=0, review calls=0" in cached.stdout
    assert len(fake.calls) == 2 and saved_result(pipeline) == before


def test_progress_job_complete_observes_committed_save_and_restores_callback(
    tmp_path, monkeypatch,
):
    config = _config(tmp_path, annotation=False)
    _sources(monkeypatch, _record())
    pipeline = JobIntakePipeline(config)
    assert pipeline.run()["persisted"] == 1
    config.annotation.enabled = True
    previous_events = []
    previous_progress = previous_events.append
    pipeline.annotator.set_progress(previous_progress)
    events = []
    committed = []

    def progress(event):
        events.append(event)
        if event["event"] == "job_complete":
            # A separate session must already see the complete annotation and score.
            committed.append(saved_result(pipeline))

    result = pipeline.reevaluate_saved(progress=progress)
    assert result["persisted"] == 1
    assert len(committed) == 1
    assert committed[0][0]["status"] == "local"
    assert committed[0][3] == 20
    assert events[0]["event"] == "run_start"
    assert events_named(events, "jobs_loaded")[0]["total"] == 1
    assert events[-1]["event"] == "run_complete"
    assert events[-1]["persisted"] == events[-1]["total"] == 1
    assert pipeline.annotator.progress is previous_progress
    assert not previous_events


def test_progress_records_job_failure_and_continues_with_next_job(tmp_path, monkeypatch):
    config = _config(tmp_path, annotation=False)
    _sources(monkeypatch, _record())
    pipeline = JobIntakePipeline(config)
    assert pipeline.run()["persisted"] == 1
    _sources(monkeypatch, _record(
        source_job_id="43", original_url="https://example.com/job/43", title="Program Manager",
    ))
    assert pipeline.run()["persisted"] == 1
    original_save = pipeline._save
    saves = 0

    def save(*args, **kwargs):
        nonlocal saves
        saves += 1
        if saves == 1:
            raise RuntimeError("private provider body and key")
        return original_save(*args, **kwargs)

    monkeypatch.setattr(pipeline, "_save", save)
    events = []
    result = pipeline.reevaluate_saved(progress=events.append)
    assert result["persisted"] == result["record_errors"] == 1
    assert [event["index"] for event in events_named(events, "job_start")] == [1, 2]
    assert events_named(events, "job_error") == [
        {"event": "job_error", "error_type": "RuntimeError"},
    ]
    assert len(events_named(events, "job_complete")) == 1
    assert events[-1]["persisted"] == 1 and events[-1]["total"] == 2
    assert "private provider" not in json.dumps(events)


def test_verbose_empty_database_reports_zero_without_external_actions(tmp_path, monkeypatch):
    config = _config(tmp_path, ai=True)
    monkeypatch.setattr("job_intake.cli.load_app_config", lambda path: config)

    def forbidden(*args, **kwargs):
        pytest.fail("Empty annotation run attempted an external action")

    monkeypatch.setattr("job_intake.pipeline.build_adapter", forbidden)
    monkeypatch.setattr(AnnotationModelClient, "generate", forbidden)
    result = CliRunner().invoke(app, ["annotate", "--verbose"])
    assert result.exit_code == 0, result.output
    assert "Annotated 0 jobs" in result.stdout
    assert "Loaded 0 vacancies" in result.stderr
    assert "Finished: 0/0" in result.stderr
    assert "Job 1/" not in result.stderr


def test_chunk_progress_does_not_change_annotation_or_cache_bytes(tmp_path, monkeypatch):
    monkeypatch.setenv(TEST_KEY_ENV, "test-private-key")
    config = AnnotationConfig(
        ai_enabled=True, api_key_env=TEST_KEY_ENV, chunk_chars=1000,
        cache_dir=str(tmp_path / "quiet-cache"),
    )
    description = source_record().description_clean + "\n" + "\n".join(
        f"Responsibility {index}: coordinate clear decisions with project stakeholders."
        for index in range(30)
    )
    quiet, _ = fake_annotator(config)
    quiet_result = quiet.annotate(source_record(description_clean=description), use_ai=True)
    events = []
    traced_config = replace(config, cache_dir=str(tmp_path / "verbose-cache"))
    traced, _ = fake_annotator(traced_config, events.append)
    traced_result = traced.annotate(source_record(description_clean=description), use_ai=True)
    assert traced_result == quiet_result
    chunks = traced_result["coverage"]["chunks"]
    assert chunks > 1
    assert [event["index"] for event in events_named(events, "chunk_start")] == list(
        range(1, chunks + 1)
    )
    assert len(events_named(events, "chunk_complete")) == chunks
    starts = events_named(events, "stage_start")
    assert {event["stage"] for event in starts} == {"extract", "review"}
    for event in starts:
        stage = config.resolved_stage(event["stage"])
        assert (event["provider"], event["model"]) == (stage.provider, stage.model)
    assert events[-1]["event"] == "annotation_complete"
    assert events[-1]["status"] == "reviewed"
    quiet_files = {
        path.relative_to(config.cache_dir): path.read_bytes()
        for path in Path(config.cache_dir).rglob("*.json")
    }
    traced_files = {
        path.relative_to(traced_config.cache_dir): path.read_bytes()
        for path in Path(traced_config.cache_dir).rglob("*.json")
    }
    assert traced_files == quiet_files


def test_cached_progress_skips_model_stages_and_saved_annotation_skips_chunks(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv(TEST_KEY_ENV, "test-private-key")
    config = AnnotationConfig(
        ai_enabled=True, api_key_env=TEST_KEY_ENV, cache_dir=str(tmp_path / "cache"),
    )
    initial, _ = fake_annotator(config)
    expected = initial.annotate(source_record(), use_ai=True)
    events = []
    cached, model = fake_annotator(config, events.append)
    assert cached.annotate(source_record(), use_ai=True) == expected
    assert not model.calls
    assert {event["stage"] for event in events_named(events, "cache_hit")} == {
        "extract", "review",
    }
    assert not events_named(events, "stage_start")
    assert events[-1]["status"] == "reviewed"
    events.clear()
    assert cached.annotate(source_record(annotation=expected), use_ai=True) == expected
    assert events_named(events, "cache_hit")[0]["stage"] == "saved annotation"
    assert not events_named(events, "chunk_start")
    assert not model.calls


def test_review_error_progress_preserves_drafts_and_resumes_cached_extraction(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv(TEST_KEY_ENV, "test-private-key")
    config = AnnotationConfig(
        ai_enabled=True, api_key_env=TEST_KEY_ENV, cache_dir=str(tmp_path / "cache"),
    )
    events = []
    initial, model = fake_annotator(config, events.append)
    model.fail_reviews = 1
    partial = initial.annotate(source_record(), use_ai=True)
    assert partial["status"] == "partial" and partial["claims"]
    assert events_named(events, "chunk_error")[0]["error_type"] == "RuntimeError"
    assert not events_named(events, "chunk_complete")
    assert [event["stage"] for event in events_named(events, "stage_complete")] == ["extract"]
    assert events[-1]["status"] == "partial"
    events.clear()
    resumed, model = fake_annotator(config, events.append)
    assert resumed.annotate(source_record(), use_ai=True)["status"] == "reviewed"
    assert [stage for stage, _ in model.calls] == ["review"]
    assert events_named(events, "cache_hit")[0]["stage"] == "extract"
    assert len(events_named(events, "chunk_complete")) == 1


def test_incomplete_review_never_reports_successful_review_or_chunk(tmp_path, monkeypatch):
    monkeypatch.setenv(TEST_KEY_ENV, "test-private-key")
    config = AnnotationConfig(
        ai_enabled=True, api_key_env=TEST_KEY_ENV, cache_dir=str(tmp_path / "cache"),
    )
    events = []
    annotator, fake = fake_annotator(config, events.append)
    original_generate = fake.generate

    def incomplete(model, prompt):
        if model == config.review_model:
            return {"reviews": []}, {}
        return original_generate(model, prompt)

    monkeypatch.setattr(fake, "generate", incomplete)
    result = annotator.annotate(source_record(), use_ai=True)
    assert result["status"] == "partial"
    assert events_named(events, "chunk_error")[0]["error_type"] == "ValueError"
    assert [event["stage"] for event in events_named(events, "stage_complete")] == ["extract"]
    assert not events_named(events, "chunk_complete")
    assert events[-1]["status"] == "partial"


@pytest.mark.parametrize("exhausted", [False, True])
def test_request_progress_reports_bounded_retries_without_prompt_or_secret(
    monkeypatch, exhausted,
):
    secret = "progress-private-key"
    private = "private prompt and raw transport detail"
    monkeypatch.setenv(TEST_KEY_ENV, secret)
    config = SimpleNamespace(
        provider="gemini", api_key_env=TEST_KEY_ENV, request_timeout=25,
        max_output_tokens=1200, reasoning_effort="low", max_retries=2, retry_backoff=3,
    )
    failure = requests.ConnectionError(f"https://private.test/?key={secret}: {private}")
    replies = [failure] * 3 if exhausted else [failure, gemini_response()]
    calls = install_responses(monkeypatch, replies)
    sleeps = []
    monkeypatch.setattr(ai.time, "sleep", sleeps.append)
    events = []
    client = AnnotationModelClient(config, progress=events.append)
    if exhausted:
        with pytest.raises(AnnotationModelError, match="remained unavailable"):
            client.generate("gemini-2.5-pro", private)
    else:
        assert client.generate("gemini-2.5-pro", private)[0] == {"claims": []}
    assert len(calls) == (3 if exhausted else 2)
    assert sleeps == ([3, 6] if exhausted else [3])
    starts = events_named(events, "request_start")
    assert [event["attempt"] for event in starts] == list(range(1, len(calls) + 1))
    assert all(event["attempts"] == 3 and event["timeout"] == 25 for event in starts)
    assert all(event["provider"] == "gemini" for event in starts)
    retries = events_named(events, "request_retry")
    assert [event["attempt"] for event in retries] == list(range(2, len(calls) + 1))
    completions = events_named(events, "request_complete")
    assert len(completions) == (0 if exhausted else 1)
    if not exhausted:
        assert completions[0]["attempt"] == 2
        assert completions[0]["input_tokens"] == 110
        assert completions[0]["output_tokens"] == 50  # Includes 30 thinking tokens.
        assert completions[0]["usage_known"] is True
    messages = []
    reporter = AnnotationProgressReporter(messages.append)
    for event in events:
        reporter(event)
    serialized = json.dumps(events) + "\n".join(messages)
    assert secret not in serialized and private not in serialized
    assert "https://private.test" not in serialized


def test_jev_progress_reports_batches_and_retry_without_source_payload(monkeypatch):
    secret = "progress-private-key"
    private = "private source quote and provider response"
    monkeypatch.setenv("TYPESAFE_API_KEY", secret)
    sleeps = []
    monkeypatch.setattr("job_intake.annotation.jev.time.sleep", sleeps.append)
    calls = []

    def post(url, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return response({"error": private}, status=503)
        return response({
            "model": "jev-1.13.0",
            "answers": {key: answer() for key in kwargs["json"]["questions"]},
            "usage": {"input_tokens": 45, "output_tokens": 6},
        })

    monkeypatch.setattr(requests, "post", post)
    events = []
    client = JevReviewClient(
        spec(jev_batch_size=1, max_retries=1, retry_backoff=2), progress=events.append,
    )
    claims = [{**claim(index), "source_snippet": private} for index in (1, 2)]
    result, _ = client.review("private-source", {"units": [{"text": private}]}, claims)
    assert len(result["reviews"]) == 2 and len(calls) == 3
    assert [(event["index"], event["total"]) for event in events_named(
        events, "review_batch"
    )] == [(1, 2), (2, 2)]
    starts = events_named(events, "request_start")
    assert [event["attempt"] for event in starts] == [1, 2, 1]
    assert all(event["provider"] == "jev" and event["attempts"] == 2 for event in starts)
    assert events_named(events, "request_retry")[0]["attempt"] == 2
    assert sleeps == [2]
    assert secret not in json.dumps(events) and private not in json.dumps(events)


def test_reporter_heartbeat_has_current_context_and_stops_on_exit():
    heartbeat = Event()
    messages = []

    def write(message):
        messages.append(message)
        if "Still running" in message:
            heartbeat.set()

    with AnnotationProgressReporter(write, heartbeat_seconds=0.01) as progress:
        progress({"event": "job_start", "index": 2, "total": 3,
                  "company": "Example\x1b[2J\nCompany", "title": "Product Manager"})
        progress({"event": "chunk_start", "index": 1, "total": 2})
        progress({"event": "stage_start", "stage": "review", "provider": "jev",
                  "model": "jev-1.13.0", "prompt": "private prompt payload"})
        assert heartbeat.wait(timeout=1), messages
    assert progress._thread is not None and not progress._thread.is_alive()
    assert any("Still running: Job 2/3 chunk 1/2 review: jev/jev-1.13.0" in message
               for message in messages)
    assert all(message.startswith("[") and "s] " in message for message in messages)
    assert "\x1b" not in "\n".join(messages)
    assert "private prompt payload" not in "\n".join(messages)


def test_reporter_prefilter_routes_cache_and_totals_exclude_private_payloads():
    messages = []
    progress = AnnotationProgressReporter(messages.append)
    progress({"event": "job_start", "index": 1, "total": 3,
              "company": "Example", "title": "Project Manager"})
    progress({"event": "prefilter_models", "screen_provider": "gemini",
              "screen_model": "gemini-3.5-flash-lite", "verify_provider": "gemini",
              "verify_model": "gemini-3.8-flash"})
    progress({"event": "prefilter_start"})
    progress({"event": "prefilter_stage", "stage": "screen", "provider": "gemini",
              "model": "gemini-3.5-flash-lite", "prompt": "private prompt"})
    assert progress.activity == "prefilter screen: gemini/gemini-3.5-flash-lite"
    progress({"event": "prefilter_cache_hit", "stage": "verify",
              "model": "gemini-3.8-flash", "response": "private response"})
    progress({"event": "prefilter_complete", "routing": "suppressed",
              "decision": "clearly_irrelevant", "reason_code": "non_target_role",
              "source_snippet": "private source"})
    assert progress.activity == "saving preliminary decision"
    progress({"event": "prefilter_error", "stage": "verify", "error_type": "ValueError",
              "error": "private exception"})
    assert progress.activity == "continuing with full annotation"
    progress({"event": "prefilter_local_skip", "routing": "local_reject",
              "reason_code": "hard_blocker", "blockers": 2})
    progress({"event": "run_complete", "persisted": 3, "total": 3, "elapsed": 12,
              "prefilter_suppressed": 1, "prefilter_full_review": 1,
              "prefilter_local_skipped": 1, "prefilter_cache_hits": 1,
              "prefilter_errors": 0})
    serialized = "\n".join(messages)
    assert "Prefilter screen: gemini/gemini-3.5-flash-lite" in serialized
    assert "prefilter screen: gemini/gemini-3.5-flash-lite started" in serialized
    assert "prefilter cache hit (verify)" in serialized
    assert "route=suppressed, decision=clearly_irrelevant, reason=non_target_role" in serialized
    assert "prefilter failed (ValueError); continuing with full annotation" in serialized
    assert "skipped AI by local rules (hard_blocker, blockers=2)" in serialized
    assert "Prefilter totals: suppressed=1, full review=1, local skipped=1" in serialized
    assert "private" not in serialized


def test_reporter_uncertain_prefilter_route_prepares_full_annotation():
    messages = []
    progress = AnnotationProgressReporter(messages.append)
    progress({"event": "job_start", "index": 1, "total": 1,
              "company": "Example", "title": "Analytics Lead"})
    progress({"event": "prefilter_complete", "routing": "full_review",
              "decision": "uncertain", "reason_code": "incomplete_source"})
    assert progress.activity == "preparing full annotation"
    assert "route=full_review, decision=uncertain, reason=incomplete_source" in messages[-1]


@pytest.mark.parametrize("metadata,expected", [
    ({"promptTokenCount": 110, "candidatesTokenCount": 20,
      "thoughtsTokenCount": 30, "totalTokenCount": 160}, (110, 50, True)),
    ({}, (0, 0, False)),
    ({"promptTokenCount": True, "candidatesTokenCount": "private usage payload",
      "thoughtsTokenCount": 30, "totalTokenCount": 31}, (0, 30, False)),
])
def test_model_progress_reports_only_normalized_usage_and_sums_new_requests(
    monkeypatch, metadata, expected,
):
    monkeypatch.setenv(TEST_KEY_ENV, "private-key")
    config = AnnotationConfig(ai_enabled=True, api_key_env=TEST_KEY_ENV)
    events = []
    calls = install_responses(monkeypatch, [gemini_response(usageMetadata=metadata)] * 2)
    client = AnnotationModelClient(config, progress=lambda event: events.append({
        **event, "stage": "prefilter screen",
    }))
    for _ in range(2):
        assert client.generate("gemini-3.8-flash", "private source prompt")[0] == {"claims": []}
    assert len(calls) == 2
    completions = events_named(events, "request_complete")
    assert len(completions) == 2
    assert all(
        (event["input_tokens"], event["output_tokens"], event["usage_known"]) == expected
        for event in completions
    )
    assert all(set(event) == {
        "event", "stage", "provider", "model", "attempt", "elapsed",
        "input_tokens", "output_tokens", "usage_known",
    } for event in completions)
    messages = []
    reporter = AnnotationProgressReporter(messages.append)
    for event in events:
        reporter(event)
    # Cache hits retain evidence, but never count as a new paid request.
    reporter({"event": "cache_hit", "stage": "review"})
    reporter({"event": "prefilter_cache_hit", "stage": "verify"})
    reporter({"event": "run_complete", "persisted": 1, "total": 1, "elapsed": 1})
    serialized = json.dumps(events) + "\n".join(messages)
    assert f"tokens input={expected[0]}, output={expected[1]}" in serialized
    assert (
        f"input={expected[0] * 2}, output={expected[1] * 2}, requests=2"
        in messages[-1]
    )
    assert ("Usage incomplete for 2 completed requests" in messages[-1]) is (not expected[2])
    assert "private" not in serialized


def test_cached_run_reports_zero_new_tokens_without_inventing_invoice_totals():
    messages = []
    reporter = AnnotationProgressReporter(messages.append)
    reporter({"event": "cache_hit", "stage": "saved annotation",
              "input_tokens": 999, "output_tokens": 999})
    reporter({"event": "prefilter_cache_hit", "stage": "screen",
              "input_tokens": 999, "output_tokens": 999})
    reporter({"event": "run_complete", "persisted": 1, "total": 1, "elapsed": 1})
    assert "input=0, output=0, requests=0" in messages[-1]
    assert "999" not in "\n".join(messages)
