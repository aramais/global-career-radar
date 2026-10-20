"""Frozen evidence is complete on publication and never replaces an occupied path."""

from __future__ import annotations

import errno
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from typer.testing import CliRunner

from job_intake.annotation import cohorts
from job_intake.annotation.benchmark import read_jsonl
from job_intake.annotation.cohorts import digest, read_object, write_new, write_new_text
from job_intake.annotation.evaluation_cli import app
from job_intake.annotation.frozen_eval import (
    comparison_markdown,
    load_bundle,
    validate_run,
    write_comparison_report,
)


class FailingStage:
    """Use a real sibling file, injecting an I/O failure at one boundary."""

    def __init__(self, stream, failure):
        self.stream, self.failure = stream, failure

    def __getattr__(self, name):
        return getattr(self.stream, name)

    def __enter__(self):
        self.stream.__enter__()
        return self

    def __exit__(self, *args):
        self.stream.__exit__(*args)
        if self.failure == "close":
            raise OSError(errno.EIO, "synthetic close failure")

    def write(self, value):
        if self.failure == "write":
            self.stream.write(value[:len(value) // 2])
            self.stream.flush()
            raise OSError(errno.EIO, "synthetic partial write")
        return self.stream.write(value)

    def flush(self):
        if self.failure == "flush":
            raise OSError(errno.EIO, "synthetic flush failure")
        return self.stream.flush()


def failing_stages(monkeypatch, failure, *, name=None):
    create = cohorts.tempfile.NamedTemporaryFile

    def stage(*args, **kwargs):
        selected = name is None or kwargs.get("prefix") == name + "."
        return FailingStage(create(*args, **kwargs), failure if selected else None)

    monkeypatch.setattr(cohorts.tempfile, "NamedTemporaryFile", stage)


def test_serialization_and_relative_path_return_are_preserved(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = Path("nested/result.json")
    value = {"я": "Пример", "a": [1, True, None]}
    assert write_new(path, value) is path
    assert path.read_text(encoding="utf-8") == (
        '{\n  "a": [\n    1,\n    true,\n    null\n  ],\n  "я": "Пример"\n}\n'
    )
    assert read_object(path) == value
    assert list(path.parent.iterdir()) == [path]


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_serialization_never_creates_output_artifacts(tmp_path, value):
    path = tmp_path / "absent-parent" / "result.json"
    with pytest.raises(ValueError):
        write_new(path, {"nested": [value]})
    assert not path.parent.exists()


@pytest.mark.parametrize("content", ["", "Пример sem newline", "Пример\nsegunda linha\n"])
def test_text_publication_preserves_exact_utf8_and_path(tmp_path, content):
    path = tmp_path / "result.txt"
    assert write_new_text(path, content) is path
    assert path.read_bytes() == content.encode("utf-8")
    assert list(tmp_path.iterdir()) == [path]


def test_partial_unicode_text_write_leaves_no_final_and_allows_retry(tmp_path, monkeypatch):
    path = tmp_path / "result.md"
    content = "# Пример\n\nSão Paulo: подтверждённый результат.\n"
    with monkeypatch.context() as injected:
        failing_stages(injected, "write")
        with pytest.raises(OSError, match="synthetic partial write"):
            write_new_text(path, content)
    assert not path.exists() and list(tmp_path.iterdir()) == []
    assert write_new_text(path, content) == path
    assert path.read_bytes() == content.encode("utf-8")


@pytest.mark.parametrize("failure", ["write", "flush", "fsync", "close", "publication"])
def test_io_failure_before_publication_cleans_staging_and_allows_retry(
    tmp_path, monkeypatch, failure,
):
    path = tmp_path / "result.json"
    value = {"rows": [1, 2, 3], "schema": "synthetic"}

    def fail(*args, **kwargs):
        raise OSError(errno.EIO, "synthetic I/O failure")

    with monkeypatch.context() as injected:
        if failure in {"write", "flush", "close"}:
            failing_stages(injected, failure)
        elif failure == "fsync":
            injected.setattr(cohorts.os, "fsync", fail)
        else:
            injected.setattr(cohorts.os, "link", fail)
        with pytest.raises(OSError) as error:
            write_new(path, value)
        assert error.value.errno == errno.EIO
    assert not os.path.lexists(path)
    assert list(tmp_path.iterdir()) == []
    assert write_new(path, value) == path
    assert read_object(path) == value


def test_publication_waits_for_flush_fsync_and_close(tmp_path, monkeypatch):
    path = tmp_path / "result.json"
    value = {"complete": "Пример"}
    create, fsync, link = cohorts.tempfile.NamedTemporaryFile, cohorts.os.fsync, cohorts.os.link
    stages, events = [], []

    class ObservedStage(FailingStage):
        def flush(self):
            result = super().flush()
            events.append("flushed")
            return result

        def __exit__(self, *args):
            super().__exit__(*args)
            events.append("closed")

    def stage(*args, **kwargs):
        stream = ObservedStage(create(*args, **kwargs), None)
        stages.append(stream)
        assert Path(stream.name).parent == path.parent
        return stream

    def synced(descriptor):
        fsync(descriptor)
        events.append("synced")

    def publish(source, destination):
        assert not os.path.lexists(path)
        assert stages[0].closed
        assert events == ["flushed", "synced", "closed"]
        assert read_object(Path(source)) == value
        link(source, destination)

    monkeypatch.setattr(cohorts.tempfile, "NamedTemporaryFile", stage)
    monkeypatch.setattr(cohorts.os, "fsync", synced)
    monkeypatch.setattr(cohorts.os, "link", publish)
    write_new(path, value)
    assert read_object(path) == value
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("occupied", ["file", "directory", "symlink", "dangling_symlink"])
def test_any_occupied_lexical_output_is_preserved(tmp_path, occupied):
    path = tmp_path / "result.json"
    target = tmp_path / "target.json"
    before = b'{"existing": "evidence"}\n'
    if occupied == "file":
        path.write_bytes(before)
    elif occupied == "directory":
        path.mkdir()
    else:
        if occupied == "symlink":
            target.write_bytes(before)
        path.symlink_to(target)
    entries = set(tmp_path.iterdir())
    with pytest.raises(FileExistsError):
        write_new(path, {"replacement": True})
    assert set(tmp_path.iterdir()) == entries
    if occupied == "file":
        assert path.read_bytes() == before
    elif occupied == "directory":
        assert path.is_dir() and list(path.iterdir()) == []
    else:
        assert path.is_symlink() and path.readlink() == target
        if occupied == "symlink":
            assert target.read_bytes() == before
        else:
            assert not target.exists()


def test_competing_output_created_after_staging_is_never_overwritten(tmp_path, monkeypatch):
    path = tmp_path / "result.json"
    foreign = b'{"winner": "another process"}\n'
    link = cohorts.os.link

    def publish(source, destination):
        assert read_object(Path(source)) == {"winner": "this process"}
        path.write_bytes(foreign)
        link(source, destination)

    monkeypatch.setattr(cohorts.os, "link", publish)
    with pytest.raises(FileExistsError):
        write_new(path, {"winner": "this process"})
    assert path.read_bytes() == foreign
    assert list(tmp_path.iterdir()) == [path]


def test_concurrent_publishers_leave_one_complete_winner(tmp_path, monkeypatch):
    path = tmp_path / "result.json"
    barrier = threading.Barrier(4)
    link = cohorts.os.link

    def publish(source, destination):
        barrier.wait(timeout=10)
        link(source, destination)

    def writer(index):
        value = {"writer": index, "rows": list(range(100))}
        try:
            write_new(path, value)
        except FileExistsError:
            return None
        return value

    monkeypatch.setattr(cohorts.os, "link", publish)
    with ThreadPoolExecutor(max_workers=4) as pool:
        winners = [value for value in pool.map(writer, range(4)) if value is not None]
    assert len(winners) == 1 and read_object(path) == winners[0]
    assert list(tmp_path.iterdir()) == [path]


def test_cleanup_never_removes_a_foreign_replacement_of_staging(tmp_path, monkeypatch):
    path = tmp_path / "result.json"
    foreign = b"another process owns this staging path"
    staging = None

    def publication_fails(source, destination):
        nonlocal staging
        staging = Path(source)
        replacement = tmp_path / "foreign.tmp"
        replacement.write_bytes(foreign)
        replacement.replace(staging)
        raise OSError(errno.EIO, "synthetic publication failure")

    monkeypatch.setattr(cohorts.os, "link", publication_fails)
    with pytest.raises(OSError):
        write_new(path, {"rows": [1]})
    assert not path.exists()
    assert staging.read_bytes() == foreign


@pytest.mark.parametrize("foreign_replacement", [False, True])
def test_unknown_staging_identity_preserves_file_and_original_error(
    tmp_path, monkeypatch, foreign_replacement,
):
    path = tmp_path / "result.json"
    foreign = b"another process owns this staging path"
    create = cohorts.tempfile.NamedTemporaryFile
    stages = []
    original_error = OSError(errno.EIO, "synthetic fstat failure")

    def stage(*args, **kwargs):
        stream = create(*args, **kwargs)
        stages.append(Path(stream.name))
        return stream

    def identity_unavailable(descriptor):
        if foreign_replacement:
            replacement = tmp_path / "foreign.tmp"
            replacement.write_bytes(foreign)
            replacement.replace(stages[0])
        raise original_error

    monkeypatch.setattr(cohorts.tempfile, "NamedTemporaryFile", stage)
    monkeypatch.setattr(cohorts.os, "fstat", identity_unavailable)
    with pytest.raises(OSError) as error:
        write_new(path, {"rows": [1]})
    assert error.value is original_error
    assert not os.path.lexists(path)
    assert len(stages) == 1 and stages[0].exists()
    assert stages[0].read_bytes() == (foreign if foreign_replacement else b"")
    assert list(tmp_path.iterdir()) == stages


def test_cleanup_failure_after_commit_does_not_remove_complete_output(tmp_path, monkeypatch):
    path = tmp_path / "result.json"
    value = {"rows": [1, 2, 3]}
    unlink = Path.unlink

    def cleanup_fails(temporary, *args, **kwargs):
        if temporary.name.startswith(path.name + "."):
            raise OSError(errno.EIO, "synthetic staging cleanup failure")
        return unlink(temporary, *args, **kwargs)

    with monkeypatch.context() as injected:
        injected.setattr(Path, "unlink", cleanup_fails)
        with pytest.raises(OSError):
            write_new(path, value)
    assert read_object(path) == value
    with pytest.raises(FileExistsError):
        write_new(path, {"replacement": True})
    assert read_object(path) == value


def test_frozen_run_cli_can_retry_same_path_after_partial_stage_write(tmp_path, monkeypatch):
    from test_frozen_evaluation import bundle

    frozen = bundle(n=1)
    frozen["cohort"]["exposure_log"] = str(tmp_path / "unexposed.jsonl")
    frozen["bundle_id"] = digest({key: value for key, value in frozen.items()
                                  if key != "bundle_id"})
    source, output = tmp_path / "bundle.json", tmp_path / "run.json"
    write_new(source, frozen)
    before = source.read_bytes()
    command = ["run", "--bundle", str(source), "--variant", "baseline", "--version", "v1",
               "--run-id", "retry", "--output", str(output)]
    with monkeypatch.context() as injected:
        failing_stages(injected, "write")
        failed = CliRunner().invoke(app, command)
    assert failed.exit_code != 0 and "synthetic partial write" in failed.output
    assert not output.exists() and source.read_bytes() == before
    assert list(tmp_path.iterdir()) == [source]
    retried = CliRunner().invoke(app, command)
    assert retried.exit_code == 0, retried.output
    assert "Recorded run retry" in retried.output
    validate_run(load_bundle(source), read_object(output))
    assert source.read_bytes() == before


def test_markdown_report_failure_preserves_complete_json_without_partial_markdown(
    tmp_path, monkeypatch,
):
    path = tmp_path / "comparison.json"
    markdown = path.with_suffix(".md")
    report = {"bundle_id": "synthetic", "status": "insufficient_evidence", "gates": []}
    with monkeypatch.context() as injected:
        failing_stages(injected, "write", name=markdown.name)
        with pytest.raises(OSError, match="synthetic partial write"):
            write_comparison_report(report, path)
    assert read_object(path) == report
    before = path.read_bytes()
    assert not markdown.exists() and list(tmp_path.iterdir()) == [path]
    # The pair is not a transaction: a new whole-report attempt needs new paths.
    with pytest.raises(ValueError, match="new JSON and Markdown"):
        write_comparison_report(report, path)
    retried = tmp_path / "retried.json"
    assert write_comparison_report(report, retried) == (retried, retried.with_suffix(".md"))
    assert retried.with_suffix(".md").read_text(encoding="utf-8") == comparison_markdown(report)
    assert path.read_bytes() == before


@pytest.mark.parametrize("occupied", ["json", "markdown"])
@pytest.mark.parametrize("kind", ["file", "dangling_symlink"])
def test_comparison_refuses_occupied_destination_before_publishing_either_file(
    tmp_path, occupied, kind,
):
    path = tmp_path / "comparison.json"
    markdown = path.with_suffix(".md")
    target = tmp_path / "absent.txt"
    destination = path if occupied == "json" else markdown
    before = b"existing evidence\n"
    if kind == "file":
        destination.write_bytes(before)
    else:
        destination.symlink_to(target)
    entries = set(tmp_path.iterdir())
    report = {"bundle_id": "synthetic", "status": "insufficient_evidence", "gates": []}
    with pytest.raises(ValueError, match="new JSON and Markdown"):
        write_comparison_report(report, path)
    assert set(tmp_path.iterdir()) == entries and not target.exists()
    if kind == "file":
        assert destination.read_bytes() == before
    else:
        assert destination.readlink() == target


def test_union_frame_cli_partial_write_retry_and_occupied_output_preserve_inputs(
    tmp_path, monkeypatch,
):
    baseline, candidate, output = [tmp_path / name for name in
                                   ("baseline.jsonl", "candidate.jsonl", "union.jsonl")]
    frames = [
        [{"job_uid": "old", "source_hash": "a" * 64, "title": "Пример"},
         {"job_uid": "shared", "source_hash": "b" * 64}],
        [{"job_uid": "new", "source_hash": "c" * 64, "fetch_status": "budget_not_processed"},
         {"job_uid": "shared", "source_hash": "b" * 64}],
    ]
    for path, rows in zip((baseline, candidate), frames, strict=True):
        path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
                        encoding="utf-8")
    before = baseline.read_bytes(), candidate.read_bytes()
    command = ["union-frame", "--baseline", str(baseline), "--candidate", str(candidate),
               "--output", str(output)]
    with monkeypatch.context() as injected:
        failing_stages(injected, "write", name=output.name)
        failed = CliRunner().invoke(app, command)
    assert failed.exit_code != 0 and "synthetic partial write" in failed.output
    assert not output.exists() and set(tmp_path.iterdir()) == {baseline, candidate}
    assert (baseline.read_bytes(), candidate.read_bytes()) == before
    retried = CliRunner().invoke(app, command)
    assert retried.exit_code == 0, retried.output
    rows = read_jsonl(output)
    assert [row["job_uid"] for row in rows] == ["new", "old", "shared"]
    assert [row["stratum"] for row in rows] == ["candidate_only", "baseline_only", "shared"]
    assert rows[1]["observations"]["baseline"]["title"] == "Пример"
    completed = output.read_bytes()
    assert completed.endswith(b"\n") and "Пример".encode() in completed
    occupied = CliRunner().invoke(app, command)
    assert occupied.exit_code != 0
    assert output.read_bytes() == completed
    assert (baseline.read_bytes(), candidate.read_bytes()) == before
