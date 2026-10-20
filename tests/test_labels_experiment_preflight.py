"""Experiment reports reject destinations before inspecting holdout results."""

from __future__ import annotations

import json

import pytest
from test_frozen_evaluation import bundle, runs
from typer.testing import CliRunner

from job_intake.annotation import evaluation_cli
from job_intake.annotation.cohorts import digest, independent_holdout, write_new
from job_intake.annotation.experiments import declare_experiment


@pytest.fixture
def experiment_files(tmp_path):
    frozen = bundle()
    journal = tmp_path / "exposure.json"
    frozen["cohort"]["exposure_log"] = str(journal)
    frozen["bundle_id"] = digest({key: value for key, value in frozen.items()
                                  if key != "bundle_id"})
    declared = declare_experiment(
        frozen, experiment_id="preflight", factor="compact_payload",
        variants={"baseline": {"compact_payload": False},
                  "candidate": {"compact_payload": True}},
        budget_usd=1, goals={"quality": "no regression"},
    )
    bundle_path = write_new(tmp_path / "bundle.json", frozen)
    declaration = write_new(tmp_path / "declaration.json", declared)
    recorded = [write_new(tmp_path / f"run-{i}.json", run)
                for i, run in enumerate(runs(frozen))]
    args = ["experiment-report", "--bundle", str(bundle_path),
            "--declaration", str(declaration), "--exposure-log", str(journal)]
    for path in recorded:
        args.extend(["--runs", str(path)])
    return frozen, journal, [bundle_path, declaration, *recorded], args


@pytest.mark.parametrize("slot", ["json", "markdown", "journal", "bundle", "declaration",
                                 "run0", "run1", "run2"])
def test_experiment_report_preflights_all_destinations_before_measurement(
    experiment_files, tmp_path, monkeypatch, slot,
):
    frozen, journal, inputs, args = experiment_files
    originals = {path: path.read_bytes() for path in inputs}
    alias = tmp_path / "alias"
    alias.symlink_to(tmp_path, target_is_directory=True)
    output, missing = tmp_path / "report.json", tmp_path / "missing"
    occupied = None
    if slot in {"json", "markdown"}:
        occupied = output if slot == "json" else output.with_suffix(".md")
        occupied.symlink_to(missing)
    else:
        protected = dict(zip(["bundle", "declaration", "run0", "run1", "run2"],
                             inputs, strict=True))
        output = alias / (journal if slot == "journal" else protected[slot]).name
    measured = []
    measure = evaluation_cli.measure_experiment

    def observe_measurement(*args, **kwargs):
        measured.append(True)
        return measure(*args, **kwargs)

    monkeypatch.setattr(evaluation_cli, "measure_experiment", observe_measurement)
    result = CliRunner().invoke(evaluation_cli.app, [*args, "--output", str(output)])
    assert result.exit_code == 2, (result.output, result.exception)
    assert measured == []
    assert not journal.exists() and not missing.exists()
    assert all(path.read_bytes() == content for path, content in originals.items())
    assert independent_holdout(frozen["cohort"]) is True
    if occupied is not None:
        assert occupied.is_symlink()


def test_experiment_report_publishes_pair_and_records_one_exposure(experiment_files, tmp_path):
    frozen, journal, inputs, args = experiment_files
    originals = {path: path.read_bytes() for path in inputs}
    output = tmp_path / "report.json"
    result = CliRunner().invoke(evaluation_cli.app, [*args, "--output", str(output)])
    assert result.exit_code == 0, (result.output, result.exception)
    report = json.loads(output.read_text())
    assert report["schema"] == "audit-experiment-result-v1"
    assert report["status"] == "insufficient_evidence"
    assert output.with_suffix(".md").is_file()
    assert len(journal.read_text().splitlines()) == 1
    assert independent_holdout(frozen["cohort"]) is False
    assert all(path.read_bytes() == content for path, content in originals.items())
