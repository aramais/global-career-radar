"""Accuracy reports never replace saved evidence, in any variant or writer entry point."""

from __future__ import annotations

import copy
import errno
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from test_frozen_evaluation import bundle, predictions
from test_labels_cli import gold_row, invoke, snapshot, text, write_jsonl
from test_labels_cli import stored as stored

from job_intake.annotation import accuracy, cohorts
from job_intake.annotation.accuracy import (
    accuracy_markdown,
    accuracy_report_destinations,
    run_accuracy,
    write_accuracy_report,
)
from job_intake.annotation.frozen_eval import create_run, validate_run
from job_intake.labeling.predictions import baseline_predictions


@pytest.fixture
def reports(stored, tmp_path):
    database, record, config, current = stored
    human = gold_row(record)
    v3 = write_jsonl(tmp_path / "gold-v3.jsonl", [human])
    v2 = write_jsonl(tmp_path / "gold-v2.jsonl", [{
        **{key: value for key, value in human.items()
           if key not in {"labeler", "rubric_version", "first_labeled_at", "gate_reasons"}},
        "schema": "vacancy-tags-v2",
    }])
    machine = baseline_predictions([("uid", record)], {
        "uid": {"role_label": "analytics-leadership", "decision": "review",
                "tier": "B", "fit_score": 10, "gate_outcome": "unknown"},
    })
    predicted = write_jsonl(tmp_path / "predictions.jsonl", machine)
    settings = current["settings"]
    report = run_accuracy(settings, gold=v3, variant="rules")
    inputs = {path: snapshot(path) for path in (
        database, v3, v2, config, predicted, settings.rules_path, settings.search_profiles_path,
    )}
    return {"report": report, "config": config, "v3": v3, "v2": v2,
            "predictions": predicted, "inputs": inputs}


def command(reports, variant, output=None):
    args = ["eval", "accuracy", "--config", reports["config"], "--variant", variant,
            "--gold", reports["v2"] if variant == "saved" else reports["v3"]]
    if variant == "labels":
        args += ["--predictions", reports["predictions"]]
    elif variant == "saved":
        args += ["--prices", Path(__file__).parents[1] / "config" / "eval_prices.yaml"]
    if output is not None:
        args += ["--output", output]
    return args


def assert_inputs_unchanged(reports):
    assert all(snapshot(path) == before for path, before in reports["inputs"].items())


@pytest.mark.parametrize("variant", ["rules", "labels", "saved"])
def test_cli_refuses_valid_frozen_reference_without_replacing_its_bytes(
    reports, tmp_path, variant,
):
    frozen = bundle(1)
    run = create_run(frozen, predictions=predictions(frozen), variant="baseline",
                     version="v1", run_id="saved-reference")
    output = cohorts.write_new(tmp_path / "reference.json", run)
    before = snapshot(output)
    validate_run(frozen, json.loads(output.read_text()))
    result = invoke(*command(reports, variant, output))
    assert result.exit_code == 2 and "already exists" in text(result)
    assert snapshot(output) == before and not output.with_suffix(".md").exists()
    validate_run(frozen, json.loads(output.read_text()))
    assert_inputs_unchanged(reports)


@pytest.mark.parametrize("variant", ["rules", "labels", "saved"])
def test_cli_new_reports_keep_formats_and_default_repeat_requires_a_new_path(
    reports, tmp_path, monkeypatch, variant,
):
    monkeypatch.chdir(tmp_path)
    result = invoke(*command(reports, variant))
    assert result.exit_code == 0, result.output
    output = tmp_path / "data" / "local" / "eval" / f"accuracy-{variant}.json"
    markdown = output.with_suffix(".md")
    report = json.loads(output.read_text())
    assert report["variant"] == variant and report["api_calls"] == 0
    assert report["database_access"] == "read_only"
    assert markdown.read_text() == accuracy_markdown(report)
    assert report["evaluation"]["counts"]["gold_rows"] == 1
    if variant in {"rules", "labels"}:
        assert report["evaluation"]["relevance_scale"]["N"] == 1
    before = snapshot(output), snapshot(markdown)
    repeated = invoke(*command(reports, variant))
    assert repeated.exit_code == 2 and "already exists" in text(repeated)
    assert (snapshot(output), snapshot(markdown)) == before
    fresh = tmp_path / f"new-{variant}.json"
    retry = invoke(*command(reports, variant, fresh))
    assert retry.exit_code == 0, retry.output
    assert json.loads(fresh.read_text())["variant"] == variant
    assert_inputs_unchanged(reports)


@pytest.mark.parametrize("entry", ["writer", "cli"])
@pytest.mark.parametrize("slot", ["json", "markdown"])
@pytest.mark.parametrize("kind", ["file", "directory", "symlink", "dangling_symlink"])
def test_both_lexical_names_are_checked_before_formatting_or_evaluation(
    reports, tmp_path, monkeypatch, entry, slot, kind,
):
    output = tmp_path / "occupied.json"
    markdown = output.with_suffix(".md")
    destination = output if slot == "json" else markdown
    target = tmp_path / "unrelated.jsonl"
    evidence = b'{"schema":"unrelated-gold","relevance":3}\n'
    if kind == "file":
        destination.write_bytes(evidence)
    elif kind == "directory":
        destination.mkdir()
    else:
        if kind == "symlink":
            target.write_bytes(evidence)
        destination.symlink_to(target)
    entries = set(tmp_path.iterdir())

    def forbidden(*args, **kwargs):
        pytest.fail("Occupied outputs must be refused before rendering or evaluating")

    monkeypatch.setattr(accuracy, "accuracy_markdown", forbidden)
    monkeypatch.setattr(accuracy, "run_accuracy", forbidden)
    if entry == "writer":
        with pytest.raises(ValueError, match="already exists"):
            write_accuracy_report({}, output)
    else:
        result = invoke(*command(reports, "rules", output))
        assert result.exit_code == 2 and "already exists" in text(result)
    assert set(tmp_path.iterdir()) == entries
    if kind == "file":
        assert destination.read_bytes() == evidence
    elif kind == "directory":
        assert destination.is_dir() and list(destination.iterdir()) == []
    else:
        assert destination.is_symlink() and destination.readlink() == target
        if kind == "symlink":
            assert target.read_bytes() == evidence
        else:
            assert not target.exists()
    other = markdown if slot == "json" else output
    assert not os.path.lexists(other)
    assert_inputs_unchanged(reports)


@pytest.mark.parametrize("slot", ["json", "markdown"])
def test_protected_input_aliases_are_checked_by_resolved_identity(
    reports, tmp_path, slot,
):
    output = tmp_path / "alias.json"
    destination = output if slot == "json" else output.with_suffix(".md")
    destination.symlink_to(reports["v3"])
    before = snapshot(reports["v3"])
    with pytest.raises(ValueError, match="must not overwrite inputs"):
        write_accuracy_report({}, output, protected_paths=[reports["v3"]])
    assert snapshot(reports["v3"]) == before and destination.is_symlink()
    other = output.with_suffix(".md") if slot == "json" else output
    assert not other.exists()


def test_fresh_names_under_a_directory_symlink_pin_the_parent_without_rejecting_it(
    reports, tmp_path,
):
    directory = tmp_path / "canonical"
    directory.mkdir()
    alias = tmp_path / "alias-directory"
    alias.symlink_to(directory)
    output = alias / "report.json"
    paths = write_accuracy_report(reports["report"], output)
    assert paths == (directory / "report.json", directory / "report.md")
    assert alias.is_symlink()
    assert json.loads(output.read_text()) == reports["report"]
    assert paths[1].read_text() == accuracy_markdown(reports["report"])
    assert_inputs_unchanged(reports)


def test_direct_writer_preserves_json_key_order_unicode_and_markdown_bytes(reports, tmp_path):
    report = {"я": "Пример", **reports["report"]}
    output = tmp_path / "ordered.json"
    expected_json = (json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)
                     + "\n").encode()
    expected_markdown = accuracy_markdown(report).encode()
    paths = write_accuracy_report(report, output)
    assert paths[0].read_bytes() == expected_json
    assert paths[1].read_bytes() == expected_markdown


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_report_serialization_fails_before_creating_either_artifact(
    reports, tmp_path, value,
):
    report = {**reports["report"], "nested": [{"invalid": value}]}
    output = tmp_path / "uncreated" / "report.json"
    with pytest.raises(ValueError, match="Out of range float"):
        write_accuracy_report(report, output)
    assert not output.parent.exists()
    assert_inputs_unchanged(reports)


@pytest.mark.parametrize("slot", ["json", "markdown"])
def test_publisher_created_after_preflight_is_not_overwritten(
    reports, tmp_path, monkeypatch, slot,
):
    output = tmp_path / "raced.json"
    destination = output if slot == "json" else output.with_suffix(".md")
    foreign = b"another publisher owns this final\n"
    real_link = cohorts.os.link

    def raced(source, target):
        if Path(target) == destination:
            destination.write_bytes(foreign)
        real_link(source, target)

    monkeypatch.setattr(cohorts.os, "link", raced)
    with pytest.raises(FileExistsError):
        write_accuracy_report(reports["report"], output)
    assert destination.read_bytes() == foreign
    assert not list(tmp_path.glob("raced.*.tmp"))
    if slot == "json":
        assert not output.with_suffix(".md").exists()
    else:
        assert json.loads(output.read_text()) == reports["report"]
    assert_inputs_unchanged(reports)


def test_concurrent_whole_report_publishers_leave_one_complete_pair(reports, tmp_path, monkeypatch):
    output = tmp_path / "concurrent.json"
    barrier = threading.Barrier(4)
    real_link = cohorts.os.link

    def concurrent(source, target):
        if Path(target) == output:
            barrier.wait(timeout=10)
        real_link(source, target)

    def writer(index):
        report = {**copy.deepcopy(reports["report"]), "test_writer": index}
        try:
            write_accuracy_report(report, output)
        except FileExistsError:
            return None
        return report

    monkeypatch.setattr(cohorts.os, "link", concurrent)
    with ThreadPoolExecutor(max_workers=4) as pool:
        winners = [report for report in pool.map(writer, range(4)) if report is not None]
    assert len(winners) == 1 and json.loads(output.read_text()) == winners[0]
    assert output.with_suffix(".md").read_text() == accuracy_markdown(winners[0])
    assert not list(tmp_path.glob("concurrent.*.tmp"))
    assert_inputs_unchanged(reports)


@pytest.mark.parametrize("slot", ["json", "markdown"])
def test_partial_stage_failure_never_publishes_a_partial_report_and_retry_is_explicit(
    reports, tmp_path, monkeypatch, slot,
):
    output = tmp_path / "interrupted.json"
    destination = output if slot == "json" else output.with_suffix(".md")
    real_stage = cohorts.tempfile.NamedTemporaryFile

    class PartialWriter:
        def __init__(self, stream):
            self.stream = stream

        def __getattr__(self, name):
            return getattr(self.stream, name)

        def __enter__(self):
            self.stream.__enter__()
            return self

        def __exit__(self, *args):
            return self.stream.__exit__(*args)

        def write(self, content):
            self.stream.write(content[:20])
            self.stream.flush()
            raise OSError(errno.EIO, "synthetic partial accuracy write")

    def stage(*args, **kwargs):
        stream = real_stage(*args, **kwargs)
        return PartialWriter(stream) if kwargs["prefix"] == destination.name + "." else stream

    with monkeypatch.context() as injected:
        injected.setattr(cohorts.tempfile, "NamedTemporaryFile", stage)
        with pytest.raises(OSError, match="partial accuracy write"):
            write_accuracy_report(reports["report"], output)
    assert not destination.exists() and not list(tmp_path.glob("interrupted.*.tmp"))
    if slot == "json":
        assert not output.with_suffix(".md").exists()
        write_accuracy_report(reports["report"], output)
    else:
        assert json.loads(output.read_text()) == reports["report"]
        before = snapshot(output)
        with pytest.raises(ValueError, match="already exists"):
            write_accuracy_report(reports["report"], output)
        assert snapshot(output) == before
        fresh = tmp_path / "retry.json"
        write_accuracy_report(reports["report"], fresh)
        assert fresh.with_suffix(".md").read_text() == accuracy_markdown(reports["report"])
    assert_inputs_unchanged(reports)


def test_direct_writer_rejects_identical_siblings_before_creating_any_file(tmp_path):
    output = tmp_path / "report.md"
    with pytest.raises(ValueError, match="distinct"):
        accuracy_report_destinations(output)
    assert list(tmp_path.iterdir()) == []
