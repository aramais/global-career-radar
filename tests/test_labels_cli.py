"""P0 CLI: gold migrate-v3, v3-aware gold validate and eval accuracy rules|labels."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest
from typer.testing import CliRunner

from job_intake import cli
from job_intake.annotation.frozen_eval import create_run, record_from_snapshot, source_snapshot
from job_intake.annotation.gold import _snapshot
from job_intake.annotation.text import TEXT_VERSION, source_hash
from job_intake.config.settings import AppConfig, LLMConfig, TelegramConfig
from job_intake.labeling.predictions import BASELINE_LABELER, baseline_predictions
from job_intake.labeling.schema import LABELS_SCHEMA, PREDICTION_SCHEMA
from job_intake.models.job import JobRecord

ROOT = Path(__file__).parents[1]


def job():
    return JobRecord(source="test", company="Example", title="Analytics Manager",
                     original_url="https://example.test/job",
                     description_clean="You will lead pricing experiments.")


def gold_row(record, **overrides):
    return {
        "schema": LABELS_SCHEMA, "job_uid": "uid", "source_hash": source_hash(record),
        "text_version": TEXT_VERSION, "source": _snapshot(record), "labeler": "human",
        "method": "blind", "rubric_version": "labels-rubric-2026-10-20",
        "first_labeled_at": "2026-10-17", "labeled_at": "2026-10-17",
        "role_label": "analytics-leadership", "relevance": 2, "gate_outcome": "unknown",
        "gate_reasons": [], "tags": {}, "notes": "", **overrides,
    }


def write_jsonl(path, rows):
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
                    encoding="utf-8")
    return path


@pytest.fixture
def stored(tmp_path, monkeypatch):
    path = tmp_path / "jobs.db"
    record = job()
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE jobs (job_uid TEXT, source TEXT, company TEXT, title TEXT, "
            "original_url TEXT, description_raw TEXT, description_clean TEXT, "
            "source_metadata TEXT, annotation TEXT, updated_at TEXT, content_hash TEXT, "
            "status TEXT, role_label TEXT, role_classification TEXT)"
        )
        connection.execute(
            "INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("uid", record.source, record.company, record.title, record.original_url,
             record.description_raw, record.description_clean, "{}",
             json.dumps({"source_hash": source_hash(record)}), "2026-10-10", "content", "open",
             "analytics-leadership",
             json.dumps({"label": "analytics-leadership", "version": "test",
                         "source_hash": source_hash(record), "content_hash": "content"})),
        )
        connection.execute(
            "CREATE TABLE job_relevance_evaluations (job_uid TEXT PRIMARY KEY, profile_id TEXT, "
            "profile_version TEXT, content_hash TEXT, decision TEXT, tier TEXT, fit_score REAL)"
        )
        connection.execute(
            "INSERT INTO job_relevance_evaluations VALUES (?,?,?,?,?,?,?)",
            ("uid", "analytics-leadership", "current", "content", "review", "B", 10),
        )
    config = tmp_path / "config" / "settings.yaml"
    config.parent.mkdir()
    config.write_text("protected config")
    settings = AppConfig(
        database_url=f"sqlite:///{path}", log_level="INFO", sources=[],
        rules_path=tmp_path / "rules.yaml", search_profiles_path=tmp_path / "profiles.yaml",
        company_watchlist_path=tmp_path / "companies.yaml", export_dir=tmp_path,
        telegram=TelegramConfig(), llm=LLMConfig(),
    )
    current = {"settings": settings}
    monkeypatch.setattr(cli, "load_app_config", lambda _: current["settings"])

    def forbidden(*args, **kwargs):
        raise AssertionError("P0 commands must not build the pipeline or call APIs")

    monkeypatch.setattr(cli, "build_pipeline", forbidden)
    monkeypatch.setattr(cli, "JobIntakePipeline", forbidden)
    monkeypatch.setattr("requests.sessions.Session.request", forbidden)
    return path, record, config, current


def invoke(*args):
    return CliRunner().invoke(cli.app, [str(arg) for arg in args])


def snapshot(path):
    return path.read_bytes(), path.stat().st_mtime_ns


def text(result):
    """Error text without Rich panel borders or wrapping."""
    return " ".join(result.output.translate(str.maketrans("", "", "│╭╮╰╯─")).split())


@pytest.mark.parametrize("command", [["gold", "migrate-v3"], ["eval", "accuracy"]])
def test_help_never_opens_database(command):
    result = invoke(*command, "--help")
    assert result.exit_code == 0, result.output
    assert "--config" in result.output


def test_migrate_v3_writes_new_files_and_refuses_existing(stored, tmp_path):
    database, record, config, _ = stored
    v2 = {key: value for key, value in gold_row(record).items()
          if key not in {"labeler", "rubric_version", "first_labeled_at", "gate_reasons"}}
    source = write_jsonl(tmp_path / "v2.jsonl", [{**v2, "schema": "vacancy-tags-v2"}])
    before = source.read_bytes()
    out = tmp_path / "v3.jsonl"
    result = invoke("gold", "migrate-v3", "--config", config, "--source", source, "--out", out,
                    "--rubric", ROOT / "config" / "labeling_rubric.yaml")
    assert result.exit_code == 0, result.output
    assert "decision labels=1" in result.output
    row = json.loads(out.read_text(encoding="utf-8"))
    assert row["schema"] == LABELS_SCHEMA and row["rubric_version"] == "labels-rubric-2026-10-20"
    assert out.with_suffix(".manifest.json").is_file()
    written = out.read_bytes()
    again = invoke("gold", "migrate-v3", "--config", config, "--source", source, "--out", out,
                   "--rubric", ROOT / "config" / "labeling_rubric.yaml")
    assert again.exit_code == 2 and "already exists" in text(again)
    assert out.read_bytes() == written and source.read_bytes() == before
    clash = invoke("gold", "migrate-v3", "--config", config, "--source", source,
                   "--out", database)
    assert clash.exit_code == 2 and "must not overwrite" in text(clash)


def test_gold_validate_recognizes_v3_by_schema(stored, tmp_path):
    database, record, config, current = stored
    current["settings"] = replace(current["settings"], rules_path=ROOT / "config/rules.yaml",
                                  search_profiles_path=ROOT / "config/search_profiles.yaml")
    before = snapshot(database)
    gold = write_jsonl(tmp_path / "v3.jsonl", [gold_row(record)])
    output = tmp_path / "validation.json"
    result = invoke("gold", "validate", "--config", config, "--gold", gold, "--output", output)
    assert result.exit_code == 0, result.output
    assert "Gold vacancy-labels-v3 valid=True; labeled=1; relevant=1" in result.output
    assert json.loads(output.read_text())["schema"] == LABELS_SCHEMA
    bad = write_jsonl(tmp_path / "bad.jsonl", [gold_row(record, relevance=5)])
    result = invoke("gold", "validate", "--config", config, "--gold", bad,
                    "--output", tmp_path / "bad.json")
    assert result.exit_code == 1 and "valid=False" in result.output
    mixed = write_jsonl(tmp_path / "mixed.jsonl",
                        [gold_row(record), {**gold_row(record), "schema": "vacancy-tags-v2"}])
    result = invoke("gold", "validate", "--config", config, "--gold", mixed,
                    "--output", tmp_path / "mixed.json")
    assert result.exit_code == 2 and "one supported schema" in text(result)
    assert snapshot(database) == before


def test_rules_baseline_reports_relevance_scale_read_only(stored, tmp_path, monkeypatch):
    database, record, config, _ = stored
    before = snapshot(database)
    gold = write_jsonl(tmp_path / "v3.jsonl", [gold_row(record)])
    monkeypatch.chdir(tmp_path)
    result = invoke("eval", "accuracy", "--config", config, "--gold", gold, "--variant", "rules")
    assert result.exit_code == 0, result.output
    assert "Relevance 0–3 (rules): N=1, coverage=1.0" in result.output
    output = tmp_path / "data/local/eval/accuracy-rules.json"
    report = json.loads(output.read_text())
    assert report["scope"] == "vacancy_level_labels_v3" and report["api_calls"] == 0
    assert report["baseline_tier_map"]["tier_relevance"] == {"A": 3, "B": 2, "C": 1}
    prediction = report["predictions"][0]
    assert prediction["labeler"] == BASELINE_LABELER and prediction["relevance"] == 2
    scale = report["evaluation"]["relevance_scale"]
    assert scale["exact"]["value"] == 1 and scale["relevant"]["recall"]["value"] == 1
    assert "tags" not in report["evaluation"]
    markdown = output.with_suffix(".md").read_text()
    assert "## Relevance scale 0–3" in markdown and "A→3, B→2, C→1" in markdown
    assert snapshot(database) == before


def test_labels_variant_scores_supplied_predictions(stored, tmp_path):
    database, record, config, _ = stored
    gold = write_jsonl(tmp_path / "v3.jsonl", [gold_row(record, relevance=3)])
    row = {"schema": PREDICTION_SCHEMA, "job_uid": "uid", "source_hash": source_hash(record),
           "labeler": "llm:gemini-3.5-flash-lite", "variant": "labels", "status": "labeled",
           "role_label": "analytics-leadership", "relevance": 2, "gate_outcome": "unknown",
           "gate_reasons": [], "tags": {}}
    predictions = write_jsonl(tmp_path / "predictions.jsonl", [row])
    output = tmp_path / "labels.json"
    result = invoke("eval", "accuracy", "--config", config, "--gold", gold, "--variant",
                    "labels", "--predictions", predictions, "--output", output)
    assert result.exit_code == 0, result.output
    report = json.loads(output.read_text())
    scale = report["evaluation"]["relevance_scale"]
    assert scale["exact"]["value"] == 0 and scale["within_one"]["value"] == 1
    assert report["prediction_source"]["labelers"] == {"llm:gemini-3.5-flash-lite": 1}
    write_jsonl(predictions, [{**row, "source_hash": "0" * 64}])
    result = invoke("eval", "accuracy", "--config", config, "--gold", gold, "--variant",
                    "labels", "--predictions", predictions, "--output", tmp_path / "x.json")
    assert result.exit_code == 2 and "bind" in text(result)
    write_jsonl(predictions, [{**row, "relevance": "2"}])
    result = invoke("eval", "accuracy", "--config", config, "--gold", gold, "--variant",
                    "labels", "--predictions", predictions, "--output", tmp_path / "y.json")
    assert result.exit_code == 2 and "relevance" in text(result)


@pytest.mark.parametrize("args,message", [
    (["--variant", "labels"], "requires --predictions"),
    (["--variant", "labels", "--predictions", "{other}", "--output", "{other}"],
     "must not overwrite"),
    (["--variant", "rules", "--predictions", "{other}"], "use --variant labels"),
    (["--variant", "saved", "--predictions", "{other}"], "labels variant only"),
    (["--variant", "llm"], "variant must be saved"),
    (["--variant", "rules", "--output", "{gold}"], "must not overwrite"),
])
def test_invalid_variant_inputs_are_refused_before_writing(stored, tmp_path, args, message):
    _, record, config, _ = stored
    gold = write_jsonl(tmp_path / "v3.jsonl", [gold_row(record)])
    other = write_jsonl(tmp_path / "predictions.jsonl", [])
    values = [str(arg).format(gold=gold, other=other) for arg in args]
    if "--output" not in values:
        values += ["--output", str(tmp_path / "report.json")]
    result = invoke("eval", "accuracy", "--config", config, "--gold", gold, *values)
    assert result.exit_code == 2, result.output
    assert message in text(result)
    assert not (tmp_path / "report.json").exists()
    assert other.read_text() == ""


def test_rules_variant_needs_v3_gold(stored, tmp_path):
    _, record, config, _ = stored
    v2 = {**gold_row(record), "schema": "vacancy-tags-v2"}
    gold = write_jsonl(tmp_path / "v2.jsonl", [v2])
    result = invoke("eval", "accuracy", "--config", config, "--gold", gold, "--variant", "rules",
                    "--output", tmp_path / "report.json")
    assert result.exit_code == 2 and "migrate-v3" in text(result)


def test_baseline_predictions_are_accepted_by_frozen_create_run():
    record = job()
    label = {**gold_row(record), "schema": "vacancy-tags-v2"}
    snapshot = source_snapshot(record)
    bundle = {"bundle_id": "b", "versions": {"rules": "tier-map-v1"},
              "rows": [{"job_uid": "uid", "source_hash": source_hash(record_from_snapshot(
                  snapshot)), "label": label}]}
    rows = baseline_predictions([("uid", record_from_snapshot(snapshot))],
                                {"uid": {"tier": "B", "role_label": "analytics-leadership",
                                         "decision": "review", "fit_score": 10.0}})
    assert rows[0]["schema"] == PREDICTION_SCHEMA and rows[0]["relevance"] == 2
    run = create_run(bundle, predictions=rows, variant="rules", version="tier-map-v1",
                     run_id="rules-1")
    assert run["evaluation"]["relevance_scale"]["exact"]["value"] == 1
    other = baseline_predictions([("uid", record)], {"uid": {"role_label": "other"}})
    stale = baseline_predictions([("uid", record)], {})
    assert other[0]["relevance"] == 0 and stale[0]["relevance"] is None
