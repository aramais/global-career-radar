"""Real CLI exports/writes on temporary databases, with no external API calls."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import select
from typer.testing import CliRunner

from job_intake.annotation.frozen_eval import source_snapshot
from job_intake.annotation.text import TEXT_VERSION, source_hash, source_units
from job_intake.cli import app
from job_intake.config.settings import AppConfig, LabelingConfig, LLMConfig, TelegramConfig
from job_intake.labeling import cli as command
from job_intake.labeling.predictions import check_prediction_rows
from job_intake.labeling.schema import LABELS_SCHEMA
from job_intake.labeling.tagger import TaggerStageClients
from job_intake.models.job import EvaluatedJob, FilterDecision, JobEvaluation, JobRecord, JobTier
from job_intake.storage.database import Database
from job_intake.storage.models import JobORM
from job_intake.storage.repository import JobRepository

ROOT = Path(__file__).parents[1]


def invoke(*args):
    return CliRunner().invoke(app, ["label", *map(str, args)], terminal_width=180)


@pytest.fixture
def stored(tmp_path, monkeypatch):
    path = tmp_path / "jobs.db"
    database = Database(f"sqlite:///{path}")
    database.create_schema()
    settings = AppConfig(
        database_url=f"sqlite:///{path}",
        log_level="INFO",
        sources=[],
        rules_path=ROOT / "config/rules.yaml",
        search_profiles_path=ROOT / "config/search_profiles.yaml",
        company_watchlist_path=ROOT / "config/company_watchlist.yaml",
        export_dir=tmp_path,
        telegram=TelegramConfig(),
        llm=LLMConfig(),
        labeling=LabelingConfig(
            rubric_path=str(ROOT / "config/labeling_rubric.yaml"),
            precedents_path=str(tmp_path / "absent.jsonl"),
            cache_dir=str(tmp_path / "cache"),
            ledger_dir=str(tmp_path / "ledger"),
        ),
    )
    record = JobRecord(
        source="fixture",
        company="Example",
        title="Analytics Manager",
        original_url="https://example.test/one",
        description_clean="Responsibilities\nYou will lead analytics for business decisions.\n"
        "You will partner with leadership on projects.\n"
        "Requirements\nEnglish required.",
        source_metadata={"description_complete": True},
        annotation={"legacy": "KEEP", "claims": []},
    )
    with database.session() as session:
        result = JobRepository(session).upsert_evaluations(
            [
                EvaluatedJob(
                    record,
                    JobEvaluation(FilterDecision.REVIEW, tier=JobTier.B, fit_score=12.5),
                    profile_id="analytics-leadership",
                    profile_name="Analytics",
                    profile_version="v1",
                )
            ]
        )
        uid = result.job_uid
        session.commit()
    current = {"settings": settings}
    monkeypatch.setattr(command, "load_app_config", lambda _: current["settings"])
    calls = []

    def fake(self, stage, model, prompt):
        calls.append((stage, prompt))
        units = source_units(replace(record))
        duty = next(unit["unit_id"] for unit in units if "You will lead" in unit["text"])
        requirement = next(unit["unit_id"] for unit in units if "English required" in unit["text"])
        payload = {
            name: {"value": "unspecified", "evidence": [], "basis": "explicit"}
            for name in ("hiring_eligibility", "work_mode", "working_language", "function_core")
        }
        payload["work_mode"]["office_location"] = ""
        payload["function_core"].update(value="analytics_for_decisions", evidence=[duty])
        payload["working_language"].update(value="english", evidence=[requirement])
        payload.update(
            domain_fit={
                "industry": "fintech",
                "requirement": "none",
                "required_domain": None,
                "domain": "",
                "evidence": [duty],
                "basis": "explicit",
            },
            preferred_focus={"values": [], "evidence": []},
            reason="PRIVATE_REASON",
            role_label="analytics-leadership",
            relevance=2,
            confidence="high",
        )
        return payload, {"input_tokens": 100, "output_tokens": 50}

    monkeypatch.setattr(TaggerStageClients, "generate_stage", fake)

    def forbidden(*args, **kwargs):
        raise AssertionError("External requests are forbidden in CLI regression tests")

    monkeypatch.setattr("requests.sessions.Session.request", forbidden)
    yield path, database, uid, record, current, calls
    database.engine.dispose()


@pytest.mark.parametrize(
    "args",
    [
        ["--ai"],
        ["--ai", "--max-cost", "nan"],
        ["--ai", "--max-cost", "0"],
        ["--ai", "--max-cost", "6"],
        ["--offline", "--ai", "--max-cost", "1"],
        ["--job-uid", "one", "--limit", "1"],
        ["--concurrency", "2"],
        ["--refresh-stale"],
        ["--run-id", "../../escape"],
        ["--gold", "not-opened.jsonl"],
    ],
)
def test_cli_invalid_options_refuse_before_loading_any_config(monkeypatch, args):
    monkeypatch.setattr(
        command, "load_app_config", lambda _: pytest.fail("config must stay closed")
    )
    result = invoke(*args)
    assert result.exit_code == 2, result.output


def test_help_is_read_only_and_exposes_budget_and_archive_options(monkeypatch):
    monkeypatch.setattr(command, "load_app_config", lambda _: pytest.fail("help must stay pure"))
    result = invoke("--help")
    assert result.exit_code == 0 and "--max-cost" in result.output
    assert "--include-archived" in result.output


def test_default_write_refuses_disabled_rollout_without_migration(stored, monkeypatch):
    path, _, _, _, _, calls = stored
    before = path.read_bytes()
    monkeypatch.setattr(Database, "create_schema", lambda _: pytest.fail("no migration on refusal"))
    result = invoke()
    assert result.exit_code == 2 and "enabled" in result.output
    assert path.read_bytes() == before and not calls


@pytest.mark.parametrize("concurrency", [1, 3])
def test_export_generates_schema_valid_predictions_without_opening_rw_db(
    stored, tmp_path, monkeypatch, concurrency
):
    path, _, uid, _, _, calls = stored
    before = path.read_bytes(), path.stat().st_mtime_ns
    monkeypatch.setattr(Database, "create_schema", lambda _: pytest.fail("export cannot migrate"))
    output = tmp_path / "predictions.jsonl"
    result = invoke("--export", output, "--concurrency", concurrency, "--ai", "--max-cost", "1")
    assert result.exit_code == 0, (result.output, result.exception)
    rows = [json.loads(line) for line in output.read_text().splitlines()]
    assert check_prediction_rows(rows)[uid]["relevance"] == 2
    assert rows[0]["status"] == "labeled" and len(calls) == 1
    assert (path.read_bytes(), path.stat().st_mtime_ns) == before
    assert "PRIVATE_REASON" not in output.read_text()
    ledger = output.with_suffix(".ledger.jsonl")
    assert len(ledger.read_text().splitlines()) == 1
    assert "SOURCE" not in ledger.read_text()


@pytest.mark.parametrize("clash", ["output", "ledger", "dangling", "input"])
def test_export_refuses_occupied_paths_before_any_model(stored, tmp_path, clash):
    path, _, _, _, _, calls = stored
    output = tmp_path / "predictions.jsonl"
    if clash == "output":
        output.write_text("KEEP")
    elif clash == "ledger":
        output.with_suffix(".ledger.jsonl").write_text("KEEP")
    elif clash == "dangling":
        output.symlink_to(tmp_path / "missing")
    else:
        output = path
    before = path.read_bytes()
    result = invoke("--export", output, "--ai", "--max-cost", "1")
    assert result.exit_code == 2, result.output
    assert not calls and path.read_bytes() == before


def test_shadow_persists_full_result_and_keeps_legacy_ranking_and_annotation(stored):
    _, database, uid, _, current, calls = stored
    current["settings"] = replace(
        current["settings"], labeling=replace(current["settings"].labeling, enabled=True)
    )
    with database.session() as session:
        job = session.scalar(select(JobORM).where(JobORM.job_uid == uid))
        before = job.tier, job.fit_score, job.filter_decision, deepcopy(job.annotation)
    result = invoke("--ai", "--max-cost", "1")
    assert result.exit_code == 0, (result.output, result.exception)
    with database.session() as session:
        job = session.scalar(select(JobORM).where(JobORM.job_uid == uid))
        assert (job.tier, job.fit_score, job.filter_decision) == before[:3]
        assert {
            key: value for key, value in job.annotation.items() if key != "labels_v3"
        } == before[3]
        labels = job.annotation["labels_v3"]
        assert labels["annotation_input_hash"] and labels["cache_key"]
        assert labels["p2_candidate"] is False
        assert job.relevance == 2 and job.labels_status == "labeled"
    assert len(calls) == 1


def test_budget_stop_exports_completed_rows_and_no_unbudgeted_post(stored, tmp_path):
    _, _, _, _, _, calls = stored
    output = tmp_path / "stopped.jsonl"
    result = invoke("--export", output, "--ai", "--max-cost", "0.000000001")
    assert result.exit_code == 1, (result.output, result.exception)
    assert not calls and output.read_bytes() == b""
    assert '"budget_exhausted": true' in result.output


def test_archive_scope_is_explicit_and_does_not_unarchive(stored, tmp_path):
    _, database, uid, _, _, _ = stored
    with database.session() as session:
        job = session.scalar(select(JobORM).where(JobORM.job_uid == uid))
        job.archived_at = datetime.now(UTC)
        session.commit()
    empty, all_jobs = tmp_path / "active.jsonl", tmp_path / "all.jsonl"
    assert invoke("--export", empty).exit_code == 0
    assert empty.read_bytes() == b""
    result = invoke("--export", all_jobs, "--include-archived")
    assert result.exit_code == 0, result.output
    assert json.loads(all_jobs.read_text())["job_uid"] == uid
    with database.session() as session:
        assert session.scalar(select(JobORM).where(JobORM.job_uid == uid)).archived_at


def test_gold_snapshot_export_is_blind_and_rejects_a_changed_pin(stored, tmp_path):
    path, _, uid, record, _, calls = stored
    gold = tmp_path / "gold.jsonl"
    row = {
        "schema": LABELS_SCHEMA,
        "job_uid": uid,
        "source_hash": source_hash(record),
        "text_version": TEXT_VERSION,
        "source": source_snapshot(record),
        "labeler": "human",
        "method": "blind",
        "rubric_version": "labels-rubric-2026-10-20",
        "first_labeled_at": "2026-10-20",
        "labeled_at": "2026-10-20",
        "role_label": "analytics-leadership",
        "relevance": 3,
        "gate_outcome": "unknown",
        "gate_reasons": [],
        "tags": {},
        "notes": "HUMAN_PRIVATE_NOTE",
    }
    gold.write_text(json.dumps(row) + "\n")
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    result = invoke(
        "--export", tmp_path / "gold-pred.jsonl", "--gold", gold, "--ai", "--max-cost", "1"
    )
    assert result.exit_code == 0, (result.output, result.exception)
    assert "HUMAN_PRIVATE_NOTE" not in calls[0][1]
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    row["source_hash"] = "0" * 64
    gold.write_text(json.dumps(row) + "\n")
    result = invoke(
        "--export", tmp_path / "invalid-pin.jsonl", "--gold", gold, "--ai", "--max-cost", "1"
    )
    assert result.exit_code == 2 and len(calls) == 1


def test_export_does_not_migrate_minimal_historical_jobs_schema(stored, tmp_path):
    path, _, _, _, _, _ = stored
    with sqlite3.connect(path) as connection:
        for name in ("relevance", "labels_status", "labels_version"):
            connection.execute(f"ALTER TABLE jobs DROP COLUMN {name}")
    before = path.read_bytes()
    result = invoke("--export", tmp_path / "old-schema.jsonl")
    assert result.exit_code == 0, (result.output, result.exception)
    assert path.read_bytes() == before


def test_labels_mode_persists_through_pipeline_and_offline_replays_without_api(stored):
    _, database, uid, _, current, calls = stored
    current["settings"] = replace(
        current["settings"],
        labeling=replace(current["settings"].labeling, enabled=True, mode="labels"),
    )
    result = invoke("--ai", "--max-cost", "1")
    assert result.exit_code == 0, (result.output, result.exception)
    with database.session() as session:
        job = session.scalar(select(JobORM).where(JobORM.job_uid == uid))
        assert job.relevance == 2 and job.labels_status == "labeled"
        assert job.annotation["labels_v3"]["annotation_input_hash"]
        assert job.annotation["labels_v3"]["p2_candidate"] is False
        assert "labels_prompt" in job.version_components
    assert len(calls) == 1
    result = invoke("--offline")
    assert result.exit_code == 0, (result.output, result.exception)
    assert len(calls) == 1


@pytest.mark.parametrize("export", [False, True])
def test_budget_stop_retains_first_completed_vacancy(stored, tmp_path, monkeypatch, export):
    from job_intake.labeling.budget import BudgetExceeded, BudgetGuard

    _, database, _, record, current, calls = stored
    current["settings"] = replace(
        current["settings"], labeling=replace(current["settings"].labeling, enabled=True)
    )
    second = replace(record, original_url="https://example.test/second")
    with database.session() as session:
        JobRepository(session).upsert_evaluations(
            [
                EvaluatedJob(
                    second, JobEvaluation(FilterDecision.REVIEW), profile_id="analytics-leadership"
                )
            ]
        )
        session.commit()
    reserve = BudgetGuard.reserve
    reservations = []

    def first_only(self, prompt, spec):
        if reservations:
            raise BudgetExceeded()
        reservations.append(True)
        return reserve(self, prompt, spec)

    monkeypatch.setattr(BudgetGuard, "reserve", first_only)
    output = tmp_path / "partial.jsonl"
    args = ["--ai", "--max-cost", "1"] + (["--export", output] if export else [])
    result = invoke(*args)
    assert result.exit_code == 1, (result.output, result.exception)
    assert len(calls) == 1
    if export:
        assert len(output.read_text().splitlines()) == 1
    else:
        with database.session() as session:
            jobs = session.scalars(select(JobORM)).all()
            assert sum(job.labels_status == "labeled" for job in jobs) == 1


@pytest.mark.parametrize("offline", [False, True])
def test_shadow_failed_retry_keeps_accepted_labels(stored, tmp_path, monkeypatch, offline):
    from job_intake.annotation.ai import AnnotationModelError

    _, database, uid, _, current, _ = stored
    current["settings"] = replace(
        current["settings"], labeling=replace(current["settings"].labeling, enabled=True)
    )
    result = invoke("--ai", "--max-cost", "1")
    assert result.exit_code == 0, (result.output, result.exception)
    with database.session() as session:
        job = session.scalar(select(JobORM).where(JobORM.job_uid == uid))
        accepted = deepcopy(job.annotation["labels_v3"])
        ranking = job.relevance, job.labels_status, job.tier, job.fit_score
    current["settings"] = replace(
        current["settings"],
        labeling=replace(current["settings"].labeling, cache_dir=str(tmp_path / "empty-cache")),
    )

    def failed(*args, **kwargs):
        raise AnnotationModelError("PRIVATE_PROVIDER_BODY")

    monkeypatch.setattr(TaggerStageClients, "generate_stage", failed)
    result = invoke("--offline") if offline else invoke("--ai", "--max-cost", "1")
    assert result.exit_code == (0 if offline else 1), (result.output, result.exception)
    with database.session() as session:
        job = session.scalar(select(JobORM).where(JobORM.job_uid == uid))
        assert job.annotation["labels_v3"] == accepted
        assert (job.relevance, job.labels_status, job.tier, job.fit_score) == ranking
        assert job.annotation["labels_v3_attempt"]["status"] == (
            "rules_only" if offline else "error"
        )
        assert "PRIVATE_PROVIDER_BODY" not in json.dumps(job.annotation)
