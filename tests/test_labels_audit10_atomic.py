"""Audit 10 regressions: immutable outputs must not damage inputs or consume holdout."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from test_frozen_evaluation import bundle, runs
from test_labels_cli import gold_row
from test_labels_command import stored as command_stored
from test_labels_gold_v3 import RUBRIC_VERSION, migrate_gold_v2, v2, write
from test_labels_schema import row
from typer.testing import CliRunner

from job_intake import cli
from job_intake.annotation import evaluation_cli, gold_workbook
from job_intake.annotation.cohorts import digest, independent_holdout, write_new
from job_intake.labeling import gold_v3
from job_intake.labeling.schema import gold_schema
from job_intake.labeling.schema_validation import json_schema_errors
from job_intake.storage.database import Database


@pytest.fixture
def local_labels(tmp_path, monkeypatch):
    yield from command_stored.__wrapped__(tmp_path, monkeypatch)


@pytest.mark.parametrize("suffix", ["-wal", "-shm", "-journal"])
@pytest.mark.parametrize("ai", [False, True])
def test_label_export_refuses_sqlite_sidecar_names_before_inference(
    local_labels, tmp_path, monkeypatch, suffix, ai,
):
    database, _, _, _, _, calls = local_labels
    alias = tmp_path / "alias"
    alias.symlink_to(tmp_path, target_is_directory=True)
    output = alias / (database.name + suffix)
    before = database.read_bytes(), database.stat().st_mtime_ns
    monkeypatch.setattr(Database, "create_schema", lambda _: pytest.fail("no migration"))
    result = CliRunner().invoke(cli.app, [
        "label", "--export", str(output), *(["--ai", "--max-cost", "1"] if ai else []),
    ])
    assert result.exit_code == 2, (result.output, result.exception)
    assert not output.exists() and not calls
    assert (database.read_bytes(), database.stat().st_mtime_ns) == before


@pytest.mark.parametrize("command,base,pair", [
    ("eval", "latest", False),
    ("validate", "gold-validation", False),
    ("rules-diff", "rules-diff", True),
])
def test_repeated_implicit_reports_get_new_names_but_explicit_reports_stay_immutable(
    local_labels, tmp_path, monkeypatch, command, base, pair,
):
    database, _, uid, record, current, _ = local_labels
    before = database.read_bytes(), database.stat().st_mtime_ns
    monkeypatch.setattr(cli, "load_app_config", lambda _: current["settings"])
    monkeypatch.chdir(tmp_path)
    gold = write(tmp_path / "gold.jsonl", [gold_row(record, job_uid=uid)])
    args = {
        "eval": ["eval"],
        "validate": ["gold", "validate", "--gold", str(gold)],
        "rules-diff": ["eval", "rules-diff", "--baseline", "e61d483"],
    }[command]
    if command == "rules-diff":
        from job_intake.annotation import rules_diff

        run = rules_diff.run_rules_diff
        repository = Path(__file__).resolve().parents[1]
        monkeypatch.setattr(rules_diff, "run_rules_diff", lambda *args, **kwargs:
                            run(*args, **kwargs, repository=repository))
    runner = CliRunner()
    first = runner.invoke(cli.app, args)
    assert first.exit_code in {0, 1}, (first.output, first.exception)
    default = tmp_path / "data/local/eval" / (base + ".json")
    originals = {path: path.read_bytes() for path in (
        (default, default.with_suffix(".md")) if pair else (default,)
    )}
    second = runner.invoke(cli.app, args)
    assert second.exit_code in {0, 1}, (second.output, second.exception)
    fresh = list(default.parent.glob(base + "-*.json"))
    assert len(fresh) == 1 and json.loads(fresh[0].read_text())
    if pair:
        assert fresh[0].with_suffix(".md").is_file()
    explicit = runner.invoke(cli.app, [*args, "--output", str(default)])
    assert explicit.exit_code == 2, (explicit.output, explicit.exception)
    assert all(path.read_bytes() == content for path, content in originals.items())
    assert (database.read_bytes(), database.stat().st_mtime_ns) == before


@pytest.mark.parametrize("slot", ["out", "manifest"])
def test_migration_refuses_dangling_final_symlinks(tmp_path, slot):
    source = write(tmp_path / "v2.jsonl", [v2()])
    before = source.read_bytes()
    output = tmp_path / "v3.jsonl"
    occupied = output if slot == "out" else output.with_suffix(".manifest.json")
    missing = tmp_path / "missing"
    occupied.symlink_to(missing)
    with pytest.raises(ValueError):
        migrate_gold_v2(source, output, rubric_version=RUBRIC_VERSION)
    assert occupied.is_symlink() and not missing.exists()
    assert source.read_bytes() == before
    assert not output.exists() and not output.with_suffix(".manifest.json").exists()


def test_gold_text_writer_refuses_dangling_final_symlink(tmp_path):
    target, output = tmp_path / "missing", tmp_path / "gold.jsonl"
    output.symlink_to(target)
    with pytest.raises(ValueError):
        gold_workbook.write_new_text(output, "human answers")
    assert output.is_symlink() and not target.exists()


def test_failed_migration_never_deletes_replaced_foreign_staging_file(tmp_path, monkeypatch):
    source = write(tmp_path / "v2.jsonl", [v2()])
    output = tmp_path / "v3.jsonl"
    replacements = []

    def replace_then_fail(staging, destination):
        staging = Path(staging)
        staging.rename(tmp_path / "owned-staging")
        staging.write_text("FOREIGN PROCESS FILE")
        replacements.append(staging)
        raise OSError("original publication failure")

    monkeypatch.setattr(gold_v3.os, "link", replace_then_fail)
    with pytest.raises(OSError, match="original publication failure"):
        migrate_gold_v2(source, output, rubric_version=RUBRIC_VERSION)
    assert len(replacements) == 1
    assert replacements[0].read_text() == "FOREIGN PROCESS FILE"
    assert not output.exists() and not output.with_suffix(".manifest.json").exists()


def test_unresolved_authorization_prevents_combined_open_hiring_fact(tmp_path):
    case = v2(tags={
        "hiring_geography": {"value": "brazil_explicit", "quote": "Remote within Brazil."},
        "work_authorization": {"value": "country_required", "country": "Kenya",
                               "quote": "Kenyan work authorization required."},
    })
    source = write(tmp_path / "v2.jsonl", [case])
    before = source.read_bytes()
    output = tmp_path / "v3.jsonl"
    manifest = migrate_gold_v2(source, output, rubric_version=RUBRIC_VERSION)
    migrated = json.loads(output.read_text())
    assert "hiring_eligibility" not in migrated["tags"]
    for field in ("role_label", "relevance", "gate_outcome", "notes"):
        assert migrated[field] == case[field]
    assert source.read_bytes() == before
    dropped = manifest["dropped_tags"]
    assert {item["tag"] for item in dropped} == {"hiring_geography", "work_authorization"}


@pytest.mark.parametrize("slot", ["json", "markdown", "journal"])
def test_compare_refuses_dangling_outputs_before_consuming_holdout(tmp_path, slot):
    frozen = bundle()
    journal = tmp_path / "exposure.jsonl"
    frozen["cohort"]["exposure_log"] = str(journal)
    frozen["bundle_id"] = digest({key: value for key, value in frozen.items()
                                  if key != "bundle_id"})
    bundle_path = write_new(tmp_path / "bundle.json", frozen)
    recorded = [write_new(tmp_path / f"run-{i}.json", run)
                for i, run in enumerate(runs(frozen))]
    output = journal if slot == "journal" else tmp_path / "report.json"
    missing = tmp_path / "missing"
    if slot != "journal":
        occupied = output if slot == "json" else output.with_suffix(".md")
        occupied.symlink_to(missing)
    result = CliRunner().invoke(evaluation_cli.app, [
        "compare", "--bundle", str(bundle_path), "--baseline", str(recorded[0]),
        "--candidate", str(recorded[1]), "--repeat", str(recorded[2]),
        "--output", str(output), "--exposure-log", str(journal),
    ])
    assert result.exit_code == 2, (result.output, result.exception)
    assert not journal.exists() and not missing.exists()
    assert independent_holdout(frozen["cohort"]) is True


def test_portable_integer_schema_and_strict_runtime_numeric_boundary_are_explicit():
    case = row(relevance=2.0)
    # JSON Schema's integer is a mathematical value; runtime deliberately rejects
    # Python floats to avoid silent coercion of human/machine decisions.
    assert Draft202012Validator(gold_schema()).is_valid(case)
    assert json_schema_errors(case, kind="gold")
