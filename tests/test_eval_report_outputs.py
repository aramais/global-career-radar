"""Every evaluation report is new evidence, including v3 gold validation."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from test_classifier_comparison import cohort as cohort
from test_classifier_comparison import comparison
from test_cohorts_atomic import failing_stages
from test_frozen_evaluation import bundle, predictions
from test_labels_cli import gold_row, invoke, snapshot, write_jsonl
from test_labels_cli import stored as stored

from job_intake.annotation import cohorts
from job_intake.annotation.eval_runner import (
    report_destinations,
    write_report,
    write_text_report,
)
from job_intake.annotation.frozen_eval import create_run, validate_run
from job_intake.annotation.rules_diff import (
    render_rules_diff_markdown,
    write_rules_diff_report,
)


def write(kind, output):
    if kind == "json":
        return write_report({"z": "Пример", "a": [1, True, None]}, output, protected_paths=[])
    return write_text_report("Пример\nsecond line\n", output, protected_paths=[])


@pytest.mark.parametrize("kind", ["json", "text"])
def test_new_report_preserves_bytes_return_path_and_input_object(tmp_path, monkeypatch, kind):
    monkeypatch.chdir(tmp_path)
    output = Path("nested/report.json" if kind == "json" else "nested/report.md")
    result = write(kind, output)
    assert result == output.resolve()
    expected = '{\n  "z": "Пример",\n  "a": [\n    1,\n    true,\n    null\n  ]\n}\n' \
        if kind == "json" else "Пример\nsecond line\n"
    assert result.read_bytes() == expected.encode()
    assert list(result.parent.iterdir()) == [result]


@pytest.mark.parametrize("kind", ["json", "text"])
@pytest.mark.parametrize("occupied", ["file", "directory", "symlink", "dangling_symlink"])
def test_shared_writer_refuses_every_occupied_final_name(tmp_path, kind, occupied):
    output, target = tmp_path / "report.json", tmp_path / "target.json"
    before = b'{"existing_reference":true}\n'
    if occupied == "file":
        output.write_bytes(before)
    elif occupied == "directory":
        output.mkdir()
    else:
        if occupied == "symlink":
            target.write_bytes(before)
        output.symlink_to(target)
    entries = set(tmp_path.iterdir())
    with pytest.raises(ValueError, match="already exists"):
        write(kind, output)
    assert set(tmp_path.iterdir()) == entries
    if occupied == "file":
        assert output.read_bytes() == before
    elif occupied == "directory":
        assert list(output.iterdir()) == []
    else:
        assert output.is_symlink() and output.readlink() == target
        assert target.read_bytes() == before if occupied == "symlink" else not target.exists()


def test_common_preflight_resolves_parent_and_input_identity_without_following_final(tmp_path):
    real, alias = tmp_path / "real", tmp_path / "alias"
    real.mkdir()
    alias.symlink_to(real, target_is_directory=True)
    protected = real / "source.json"
    protected.write_text("input")
    with pytest.raises(ValueError, match="must not overwrite inputs"):
        report_destinations([alias / protected.name], protected_paths=[protected])
    with pytest.raises(ValueError, match="distinct"):
        report_destinations([real / "new.json", alias / "new.json"], protected_paths=[])
    paths = report_destinations([alias / "new.json", alias / "new.md"], protected_paths=[])
    assert paths == (real / "new.json", real / "new.md")
    assert write_text_report("new", alias / "new.md", protected_paths=[protected]) == paths[1]
    assert paths[1].read_text() == "new" and protected.read_text() == "input"


def test_common_preflight_expands_home_before_pinning_names(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    assert report_destinations([Path("~/new.json")], protected_paths=[]) == (tmp_path / "new.json",)
    with pytest.raises(ValueError, match="already exists"):
        report_destinations([Path("~")], protected_paths=[])


@pytest.mark.parametrize("kind", ["json", "text"])
def test_shared_writer_partial_io_failure_has_no_final_and_retry_is_safe(tmp_path, monkeypatch,
                                                                        kind):
    output = tmp_path / "report.json"
    with monkeypatch.context() as injected:
        failing_stages(injected, "write")
        with pytest.raises(OSError, match="synthetic partial write"):
            write(kind, output)
    assert list(tmp_path.iterdir()) == []
    write(kind, output)
    assert output.read_text(encoding="utf-8").startswith("{\n" if kind == "json" else "Пример")


@pytest.mark.parametrize("kind", ["json", "text"])
def test_shared_writer_never_clobbers_a_post_preflight_competitor(tmp_path, monkeypatch, kind):
    output = tmp_path / "report.json"
    foreign = b'{"winner":"another publisher"}\n'
    link = cohorts.os.link

    def publish(source, destination):
        output.write_bytes(foreign)
        link(source, destination)

    monkeypatch.setattr(cohorts.os, "link", publish)
    with pytest.raises(FileExistsError):
        write(kind, output)
    assert output.read_bytes() == foreign
    assert list(tmp_path.iterdir()) == [output]


def rules_report():
    return {
        "counts": {"jobs": 1, "changed_evaluations": 0, "flipped_evaluations": 0,
                   "decision_flips": 0, "tier_flips": 0, "score_changes": 0,
                   "profiles_added": [], "profiles_removed": []},
        "baseline": {"ref": "synthetic", "policy": "test", "text_version": "text-v1",
                     "code_fingerprint": "before"},
        "current": {"policy": "test", "text_version": "text-v1", "code_fingerprint": "after"},
        "source_snapshot": {"fingerprint": "synthetic"}, "flips": [],
    }


def pair(kind, cohort):
    if kind == "rules":
        return rules_report(), write_rules_diff_report, render_rules_diff_markdown
    config, gold, _, _ = cohort
    return comparison.compare(config, gold=gold, baseline="baseline.json"), \
        comparison.save_comparison, comparison.render_markdown


@pytest.mark.parametrize("kind", ["rules", "classifier"])
def test_paired_reports_preserve_legacy_json_markdown_and_inputs(tmp_path, cohort, kind):
    report, writer, renderer = pair(kind, cohort)
    original = copy.deepcopy(report)
    output = tmp_path / "report.json"
    paths = writer(report, output, protected_paths=[])
    assert paths == (output, output.with_suffix(".md"))
    assert output.read_text(encoding="utf-8") == (
        json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    )
    assert paths[1].read_text(encoding="utf-8") == renderer(report)
    assert report == original


@pytest.mark.parametrize("kind", ["rules", "classifier"])
@pytest.mark.parametrize("slot", ["json", "markdown"])
@pytest.mark.parametrize("occupied", ["file", "dangling_symlink"])
def test_paired_reports_check_both_names_before_any_render_or_publish(tmp_path, kind, slot,
                                                                   occupied):
    output = tmp_path / "report.json"
    target = output if slot == "json" else output.with_suffix(".md")
    missing = tmp_path / "missing.json"
    if occupied == "file":
        target.write_text("existing evidence")
    else:
        target.symlink_to(missing)
    entries = set(tmp_path.iterdir())
    writer = write_rules_diff_report if kind == "rules" else comparison.save_comparison
    # {} could not render. A ValueError proves both paths are checked first.
    with pytest.raises(ValueError, match="already exists"):
        writer({}, output, protected_paths=[])
    assert set(tmp_path.iterdir()) == entries and not missing.exists()
    assert target.read_text() == "existing evidence" if occupied == "file" \
        else target.readlink() == missing


@pytest.mark.parametrize("kind", ["rules", "classifier"])
def test_paired_markdown_failure_leaves_complete_json_without_partial_markdown(
    tmp_path, monkeypatch, cohort, kind,
):
    report, writer, _ = pair(kind, cohort)
    output = tmp_path / "report.json"
    with monkeypatch.context() as injected:
        failing_stages(injected, "write", name=output.with_suffix(".md").name)
        with pytest.raises(OSError, match="synthetic partial write"):
            writer(report, output, protected_paths=[])
    assert json.loads(output.read_text()) == report
    assert not output.with_suffix(".md").exists()
    before = output.read_bytes()
    with pytest.raises(ValueError, match="already exists"):
        writer(report, output, protected_paths=[])
    assert output.read_bytes() == before


def test_gold_validate_v3_refuses_a_valid_frozen_reference_and_preserves_inputs(stored, tmp_path):
    database, record, config, _ = stored
    gold = write_jsonl(tmp_path / "gold.jsonl", [gold_row(record)])
    frozen = bundle(n=1)
    reference = create_run(frozen, predictions=predictions(frozen), variant="baseline",
                           version="v1", run_id="synthetic-reference")
    validate_run(frozen, reference)
    output = tmp_path / "frozen-reference.json"
    output.write_text(json.dumps(reference), encoding="utf-8")
    before = {path: snapshot(path) for path in (database, config, gold, output)}
    result = invoke("gold", "validate", "--config", config, "--gold", gold, "--output", output)
    assert result.exit_code == 2 and "already exists" in result.output
    assert {path: snapshot(path) for path in before} == before
    validate_run(frozen, json.loads(output.read_text()))


def test_gold_validate_v3_still_writes_a_new_report_readonly(stored, tmp_path):
    database, record, config, _ = stored
    gold = write_jsonl(tmp_path / "gold.jsonl", [gold_row(record)])
    before = {path: snapshot(path) for path in (database, config, gold)}
    output = tmp_path / "validation.json"
    result = invoke("gold", "validate", "--config", config, "--gold", gold, "--output", output)
    assert result.exit_code == 0, result.output
    assert json.loads(output.read_text())["valid"] is True
    assert {path: snapshot(path) for path in before} == before
