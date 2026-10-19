from __future__ import annotations

import json
import sqlite3

import pytest
from typer.testing import CliRunner

from job_intake import cli
from job_intake.annotation.eval_runner import (
    compare_saved_gold,
    evaluate_log,
    read_saved_records,
    run_evaluation,
    write_report,
)
from job_intake.annotation.progress import AnnotationProgressReporter
from job_intake.annotation.service import VacancyAnnotator, _drafts
from job_intake.annotation.text import chunk_source, source_identity, source_units
from job_intake.config.settings import AppConfig, LLMConfig, TelegramConfig
from job_intake.models.job import JobRecord


@pytest.fixture
def stored(tmp_path, monkeypatch):
    path = tmp_path / "jobs.db"
    settings = AppConfig(
        database_url=f"sqlite:///{path}", log_level="INFO", sources=[],
        rules_path=tmp_path / "rules.yaml", search_profiles_path=tmp_path / "profiles.yaml",
        company_watchlist_path=tmp_path / "companies.yaml", export_dir=tmp_path,
        telegram=TelegramConfig(), llm=LLMConfig(),
    )
    job = JobRecord(
        source="test", source_job_id="17", company="Example", title="Analytics Manager",
        original_url="https://example.test/17",
        description_clean="Working language is English.",
    )
    VacancyAnnotator(settings.annotation).annotate(job, use_ai=False)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE jobs (job_uid TEXT PRIMARY KEY, source TEXT, company TEXT, "
            "title TEXT, original_url TEXT, source_job_id TEXT, description_raw TEXT, "
            "description_clean TEXT, source_metadata TEXT, annotation TEXT, updated_at TEXT, "
            "archived_at TEXT, status TEXT)"
        )
        for uid, updated, archived in (
            ("older", "2026-10-01", None), ("recent", "2026-10-10", None),
            ("archived", "2026-10-11", "2026-10-11"),
        ):
            connection.execute(
                "INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (uid, job.source, job.company, job.title, job.original_url, job.source_job_id,
                 job.description_raw, job.description_clean, json.dumps(job.source_metadata),
                 json.dumps(job.annotation), updated, archived, job.status.value),
            )
    monkeypatch.setattr(cli, "load_app_config", lambda _: settings)

    def forbidden(*args, **kwargs):
        raise AssertionError("eval must not instantiate or execute the pipeline")

    monkeypatch.setattr(cli, "JobIntakePipeline", forbidden)
    monkeypatch.setattr(cli, "build_pipeline", forbidden)
    return path, settings, job


@pytest.mark.parametrize("replace_journal", [False, True])
def test_accuracy_rejects_journal_override_or_overwrite_before_report_write(
    stored, tmp_path, monkeypatch, replace_journal,
):
    from job_intake.annotation import accuracy

    journal = tmp_path / "exposure.json"
    journal.write_text("")
    manifest = {"cohort_id": "audit", "dataset_role": "holdout", "exposure_log": str(journal)}
    monkeypatch.setattr(accuracy, "run_accuracy", lambda *args, **kwargs: {
        "selection": {"cohort": manifest},
    })

    def unexpected_write(*args, **kwargs):
        pytest.fail("Invalid journal destination must be rejected before writing a report")

    monkeypatch.setattr(accuracy, "write_accuracy_report", unexpected_write)
    output = tmp_path / "report.json" if replace_journal else journal
    args = ["eval", "accuracy", "--gold", str(tmp_path / "gold.jsonl"), "--output", str(output)]
    if replace_journal:
        args.extend(["--exposure-log", str(tmp_path / "alternate.jsonl")])
    result = CliRunner().invoke(cli.app, args)
    assert result.exit_code != 0
    assert journal.read_text() == ""
    if replace_journal:
        assert not output.exists()


def test_eval_selects_latest_active_rows_without_mutating_database(stored, tmp_path):
    path, _, _ = stored
    before = path.read_bytes()
    report_path = tmp_path / "report.json"
    result = CliRunner().invoke(cli.app, ["eval", "--limit", "1", "--output", str(report_path)])
    assert result.exit_code == 0, result.output
    assert "API calls=0; database=read-only" in result.output
    assert "Semantic accuracy is not measured" in result.output
    report = json.loads(report_path.read_text())
    assert [row["job_uid"] for row in report["jobs"]] == ["recent"]
    assert report["database_access"] == "read_only"
    assert report["integrity_passed"] is report["passed"] is True
    assert path.read_bytes() == before
    with sqlite3.connect(path) as connection:
        assert len(list(connection.execute("PRAGMA table_info(jobs)"))) == 13


def test_eval_isolates_invalid_saved_json_and_returns_failure(stored, tmp_path):
    path, _, _ = stored
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE jobs SET annotation='not JSON' WHERE job_uid='recent'")
    result = CliRunner().invoke(cli.app, ["eval", "--all", "--output", str(tmp_path / "bad.json")])
    assert result.exit_code == 1, result.output
    report = json.loads((tmp_path / "bad.json").read_text())
    assert report["counts"]["jobs"] == 2
    assert report["counts"]["jobs_with_errors"] == 1
    assert report["finding_counts"]["invalid_annotation_json"] == 1
    assert not report["passed"]


@pytest.mark.parametrize("target", ["database", "log", "rules"])
def test_eval_rejects_output_overwriting_inputs(stored, tmp_path, target):
    path, settings, _ = stored
    log = tmp_path / "run.log"
    log.write_text("original log")
    settings.rules_path.write_text("original rules")
    output = {"database": path, "log": log, "rules": settings.rules_path}[target]
    before = output.read_bytes()
    result = CliRunner().invoke(cli.app, ["eval", "--log", str(log), "--output", str(output)])
    assert result.exit_code == 2, result.output
    assert "must not overwrite" in result.output
    assert output.read_bytes() == before


def test_eval_does_not_create_missing_database_or_migrate_schema(tmp_path):
    missing = tmp_path / "missing.db"
    with pytest.raises(ValueError, match="will not create"):
        read_saved_records(f"sqlite:///{missing}")
    assert not missing.exists()
    invalid = tmp_path / "invalid.db"
    with sqlite3.connect(invalid) as connection:
        connection.execute("CREATE TABLE jobs (job_uid TEXT)")
    before = invalid.read_bytes()
    with pytest.raises(ValueError, match="will not migrate"):
        read_saved_records(f"sqlite:///{invalid}")
    assert invalid.read_bytes() == before


def test_eval_single_requested_uid_does_not_select_unrelated_jobs(stored, tmp_path):
    _, _, _ = stored
    output = tmp_path / "one.json"
    result = CliRunner().invoke(cli.app, [
        "eval", "--job-uid", "older", "--output", str(output),
    ])
    assert result.exit_code == 0, result.output
    assert [row["job_uid"] for row in json.loads(output.read_text())["jobs"]] == ["older"]
    with pytest.raises(ValueError, match="Requested jobs"):
        read_saved_records(stored[1].database_url, job_uids=["missing"])


def test_archived_loading_is_explicit_and_preserves_active_only_default(stored):
    path, settings, _ = stored
    before = path.read_bytes()
    assert {uid for uid, _ in read_saved_records(settings.database_url, limit=None)[1]} == {
        "older", "recent",
    }
    assert [uid for uid, _ in read_saved_records(
        settings.database_url, limit=None, job_uids=["archived"], include_archived=True,
    )[1]] == ["archived"]
    with pytest.raises(ValueError, match="Requested jobs"):
        read_saved_records(settings.database_url, limit=None, job_uids=["archived"])
    assert path.read_bytes() == before


def test_claim_gold_can_evaluate_archived_cohort_member(stored, tmp_path):
    path, settings, record = stored
    units = source_units(record)
    chunk = chunk_source(units, settings.annotation.chunk_chars)[0]
    drafts, _ = _drafts({"claims": []}, source_identity(record), chunk["units"], chunk["chunk_id"])
    record.annotation["stages"] = {chunk["chunk_id"]: {"extraction": {"claims": []}}}
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE jobs SET annotation=? WHERE job_uid='archived'",
                           (json.dumps(record.annotation),))
    gold = tmp_path / "archived-claims.jsonl"
    gold.write_text(json.dumps({"case_id": "archived", "units": units,
                                "drafts": [dict(drafts[0], expected_review_status="SUPPORTED")]})
                    + "\n")
    before = path.read_bytes()
    report = run_evaluation(settings, job_uids=["archived"], gold_path=gold)
    assert report["selection"]["include_archived"] is True
    assert [row["job_uid"] for row in report["jobs"]] == ["archived"]
    assert path.read_bytes() == before


def test_eval_log_reports_last_run_without_copying_raw_data(tmp_path):
    path = tmp_path / "log.txt"
    path.write_text(
        "Starting annotation (AI)\nAnnotated 525 jobs, 2100 profile evaluations.\n"
        "private body KEY-secret\nStarting annotation (AI)\n"
        "prefilter failed (ValueError); continuing with full annotation.\n"
        "chunk 1/2: AnnotationModelError; partial evidence retained.\n"
        "Reported tokens from new completed requests: input=100, output=20, requests=1.\n"
        "Annotated 10 jobs, 40 profile evaluations. Extraction calls=9, review calls=11, "
        "cache hits=7, errors=10. Prefilter screen calls=10, verify calls=1, cache hits=0, "
        "suppressed=1, full review=9, local skipped=0, errors=9.\n"
    )
    report = evaluate_log(path)
    assert report["runs_detected"] == 2
    assert report["complete"]
    assert report["summary"]["jobs"] == 10
    assert report["error_types"] == {
        "prefilter:ValueError": 1, "annotation:AnnotationModelError": 1,
    }
    assert report["completed_request_usage"]["output_tokens"] == 20
    assert "KEY-secret" not in json.dumps(report)
    assert any(row["code"] == "legacy_model_error" for row in report["findings"])


def test_eval_gold_measures_missing_review_against_identical_saved_draft(stored, tmp_path):
    _, settings, job = stored
    units = source_units(job)
    chunk = chunk_source(units, settings.annotation.chunk_chars)[0]
    drafts, _ = _drafts({"claims": []}, source_identity(job), chunk["units"], chunk["chunk_id"])
    assert drafts
    job.annotation["stages"] = {chunk["chunk_id"]: {"extraction": {"claims": []}}}
    gold = [{"case_id": "job-17", "units": units, "drafts": [
        dict(drafts[0], expected_review_status="SUPPORTED"),
    ]}]
    path = tmp_path / "gold.jsonl"
    path.write_text(json.dumps(gold[0]) + "\n")
    report = compare_saved_gold([("job-17", job)], path)
    assert report["metrics"]["review_coverage"] == 0
    assert report["metrics"]["review_status_accuracy"] == 0
    assert report["failures"][0]["predicted"] == "MISSING"
    job.description_clean = "Working language is Portuguese."
    with pytest.raises(ValueError, match="source units"):
        compare_saved_gold([("job-17", job)], path)


def test_report_writer_protects_inputs_and_empty_selection_fails(stored, tmp_path):
    path, settings, _ = stored
    with pytest.raises(ValueError, match="overwrite"):
        write_report({}, path, protected_paths=[path])
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE jobs SET archived_at='2026-10-10'")
    report = run_evaluation(settings)
    assert report["counts"]["jobs"] == 0
    assert report["passed"] is False


def test_failed_request_progress_reports_finish_reason_and_usage_once():
    lines = []
    reporter = AnnotationProgressReporter(lines.append)
    reporter({"event": "request_failed", "stage": "review", "elapsed": 1,
              "code": "output_truncated", "finish_reason": "MAX_TOKENS",
              "input_tokens": 100, "output_tokens": 8192, "usage_known": True})
    reporter({"event": "chunk_error", "error_type": "AnnotationModelError",
              "code": "output_truncated", "finish_reason": "MAX_TOKENS"})
    reporter({"event": "request_failed", "stage": "review", "elapsed": 1,
              "code": "request_timeout"})
    reporter({"event": "run_complete", "persisted": 1, "total": 1, "elapsed": 2})
    text = "\n".join(lines)
    assert "code=output_truncated, finish=MAX_TOKENS" in text
    assert "input=100, output=8192, attempts=2, usage unavailable=1" in text
    assert reporter.completed_requests == 0
    assert reporter.failed_output_tokens == 8192


def test_eval_log_recognizes_new_diagnostics_without_legacy_cause_warning(tmp_path):
    path = tmp_path / "log.txt"
    path.write_text(
        "Starting annotation (AI)\n"
        "chunk 1/1: review request failed in 1.0s; code=output_truncated, "
        "finish=MAX_TOKENS; tokens input=100, output=8192.\n"
        "chunk 1/1: AnnotationModelError; code=output_truncated, "
        "finish=MAX_TOKENS; partial evidence retained.\n"
        "Reported tokens from failed attempts: input=100, output=8192, "
        "attempts=1, usage unavailable=0.\n"
    )
    report = evaluate_log(path)
    assert report["request_error_codes"] == {"output_truncated": 1}
    assert report["failed_attempt_usage"]["output_tokens"] == 8192
    assert report["error_types"] == {"annotation:AnnotationModelError": 1}
    assert report["findings"] == []


def test_eval_interrupted_log_recovers_saved_progress_and_separate_request_usage(tmp_path):
    lines = []
    reporter = AnnotationProgressReporter(lines.append)
    reporter({"event": "initializing", "mode": "AI"})
    reporter({"event": "jobs_loaded", "total": 50})
    for index in range(1, 18):
        reporter({"event": "job_start", "index": index, "total": 50,
                  "company": "Private company", "title": "Private title"})
        reporter({"event": "request_start", "stage": "prefilter screen", "attempt": 1,
                  "attempts": 3, "timeout": 60})
        reporter({"event": "request_complete", "stage": "prefilter screen", "elapsed": 1,
                  "input_tokens": 100, "output_tokens": 20, "usage_known": True})
        if index == 4:
            reporter({"event": "chunk_start", "index": 1, "total": 2})
            reporter({"event": "request_start", "stage": "review", "attempt": 1,
                      "attempts": 3, "timeout": 60})
            reporter({"event": "request_failed", "stage": "review", "elapsed": 1,
                      "code": "output_truncated", "finish_reason": "MAX_TOKENS",
                      "input_tokens": 1000, "output_tokens": 8192, "usage_known": True})
            reporter({"event": "chunk_error", "error_type": "AnnotationModelError",
                      "code": "output_truncated", "finish_reason": "MAX_TOKENS"})
        reporter({"event": "job_complete", "evaluations": 4, "elapsed": 2,
                  "annotation_errors": int(index == 4), "prefilter_errors": 0})
    reporter({"event": "job_start", "index": 18, "total": 50,
              "company": "Secret company", "title": "Secret title"})
    reporter({"event": "chunk_start", "index": 2, "total": 2})
    reporter({"event": "request_start", "stage": "extract", "attempt": 1,
              "attempts": 3, "timeout": 60})
    path = tmp_path / "interrupted.log"
    path.write_text("\n".join(lines) + "\n^C\nprivate KEY-do-not-expose\n")
    report = evaluate_log(path)
    assert report["complete"] is False
    assert report["summary"] == {}
    assert report["progress"] == {
        "jobs_loaded": 50, "jobs_started": 18, "jobs_saved": 17, "last_job_index": 18,
    }
    assert report["completed_request_usage"] == {
        "input_tokens": 1700, "output_tokens": 340, "requests": 17, "usage_unavailable": 0,
    }
    assert report["failed_attempt_usage"] == {
        "input_tokens": 1000, "output_tokens": 8192, "attempts": 1, "usage_unavailable": 0,
    }
    assert report["request_counts"] == {
        "started": 19, "completed": 17, "failed": 1, "pending": 1,
    }
    assert report["request_usage_source"] == {"completed": "events", "failed": "events"}
    assert report["request_error_codes"] == {"output_truncated": 1}
    assert [finding["code"] for finding in report["findings"]] == [
        "unfinished_request_usage_unknown",
    ]
    assert "Secret" not in json.dumps(report)
    assert "KEY-do-not-expose" not in json.dumps(report)


def test_eval_prefilter_validation_after_completed_response_has_known_usage(tmp_path):
    path = tmp_path / "validation.log"
    path.write_text(
        "[0.0s] Starting annotation (AI); loading saved jobs.\n"
        "[0.1s] Loaded 50 vacancies.\n"
        "[0.1s] Job 1/50: Private company — Private title\n"
        "[0.1s] Job 1/50 : prefilter screen request attempt 1/3 (timeout 60s).\n"
        "[1.0s] Job 1/50 : prefilter screen request completed in 0.9s; "
        "tokens input=2500, output=317.\n"
        "[1.0s] Job 1/50: prefilter failed (ValueError); "
        "continuing with full annotation.\n"
    )
    report = evaluate_log(path)
    assert report["completed_request_usage"]["input_tokens"] == 2500
    assert report["completed_request_usage"]["output_tokens"] == 317
    assert report["failed_attempt_usage"] == {}
    assert report["request_counts"] == {
        "started": 1, "completed": 1, "failed": 0, "pending": 0,
    }
    assert [row["code"] for row in report["findings"]] == [
        "legacy_prefilter_validation_error",
    ]


def test_eval_request_event_fallback_separates_unknown_usage_and_ignores_chunk_duplicates(tmp_path):
    path = tmp_path / "unknown.log"
    path.write_text(
        "Starting annotation (AI)\n"
        "Job 1/2: Company — Title\n"
        "Job 1/2 chunk 1/1: extract request completed in 1.0s; "
        "tokens input=10, output=0; usage incomplete.\n"
        "Job 1/2 chunk 1/1: review request failed in 1.0s; "
        "code=request_timeout; usage unavailable.\n"
        "Job 1/2 chunk 1/1: AnnotationModelError; code=request_timeout; "
        "partial evidence retained.\n"
        "Job 1/2 chunk 1/1: KEY-private request completed in 1.0s; "
        "tokens input=9999, output=9999.\n"
    )
    report = evaluate_log(path)
    assert report["completed_request_usage"] == {
        "input_tokens": 10, "output_tokens": 0, "requests": 1, "usage_unavailable": 1,
    }
    assert report["failed_attempt_usage"] == {
        "input_tokens": 0, "output_tokens": 0, "attempts": 1, "usage_unavailable": 1,
    }
    assert report["request_error_codes"] == {"request_timeout": 1}
    assert report["request_counts"]["pending"] == 0
    assert [row["code"] for row in report["findings"]] == [
        "failed_request_usage_unknown",
    ]


def test_eval_completed_summary_is_authoritative_and_usage_is_not_counted_twice(tmp_path):
    path = tmp_path / "complete.log"
    path.write_text(
        "Starting annotation (AI)\n"
        "Job 1/1: Company — Title\n"
        "Job 1/1 : prefilter screen request completed in 1.0s; "
        "tokens input=100, output=20.\n"
        "Reported tokens from new completed requests: input=100, output=20, requests=1.\n"
        "Annotated 1 jobs, 4 profile evaluations. Extraction calls=0, review calls=0, "
        "cache hits=0, errors=0. Prefilter screen calls=1, verify calls=0, cache hits=0, "
        "suppressed=1, full review=0, local skipped=0, errors=0.\n"
    )
    report = evaluate_log(path)
    assert report["complete"] is True
    assert report["summary"]["jobs"] == 1
    assert report["completed_request_usage"] == {
        "input_tokens": 100, "output_tokens": 20, "requests": 1,
    }
    assert report["request_usage_source"]["completed"] == "summary"
