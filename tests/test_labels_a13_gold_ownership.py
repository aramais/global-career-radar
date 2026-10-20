"""Manual Gold publication cleans up only the staging inode it created."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest
from test_gold_ui import labeling as legacy_labeling  # noqa: F401
from test_labels_gold_ui import labeling as labeling

from job_intake.annotation import gold_ui
from job_intake.annotation.gold_ui import GoldLabelStore, GoldUIError


@pytest.fixture(params=["labeling", "legacy_labeling"])
def manual_gold(request):
    return request.getfixturevalue(request.param)


def _save(fixture):
    config, path, database, *_ = fixture
    store = GoldLabelStore(config, path)
    expected = store.detail("uid-0")["version"]
    return store, {"expected_version": expected, "relevance": 3}, path, database


@pytest.mark.parametrize("replacement", ["file", "symlink", "missing"])
def test_failed_manual_publication_keeps_foreign_staging(manual_gold, monkeypatch, replacement):
    store, patch, path, database = _save(manual_gold)
    before = path.read_bytes(), database.read_bytes()
    seen = []
    owned = path.parent / "moved-owned-staging"
    foreign = path.parent / "foreign-content"
    foreign.write_bytes(b"another writer owns this file")
    error = OSError("primary replace failed")

    def replace(staging, destination):
        staging = Path(staging)
        seen.append(staging)
        staging.rename(owned)
        if replacement == "file":
            staging.write_bytes(foreign.read_bytes())
        elif replacement == "symlink":
            staging.symlink_to(foreign)
        raise error

    monkeypatch.setattr(gold_ui.os, "replace", replace)
    with pytest.raises(GoldUIError) as caught:
        store.save("uid-0", patch)
    assert caught.value.__cause__ is error
    assert (path.read_bytes(), database.read_bytes()) == before
    assert owned.is_file()
    if replacement == "file":
        assert seen[0].read_bytes() == foreign.read_bytes()
    elif replacement == "symlink":
        assert seen[0].is_symlink() and foreign.read_bytes() == b"another writer owns this file"
    else:
        assert not os.path.lexists(seen[0])


def test_failed_manual_publication_removes_its_own_staging(manual_gold, monkeypatch):
    store, patch, path, database = _save(manual_gold)
    before = path.read_bytes(), database.read_bytes()
    seen = []
    error = OSError("primary replace failed")

    def replace(staging, destination):
        seen.append(Path(staging))
        raise error

    monkeypatch.setattr(gold_ui.os, "replace", replace)
    with pytest.raises(GoldUIError) as caught:
        store.save("uid-0", patch)
    assert caught.value.__cause__ is error
    assert not os.path.lexists(seen[0])
    assert (path.read_bytes(), database.read_bytes()) == before


@pytest.mark.parametrize("cleanup_operation", ["lstat", "unlink"])
def test_manual_staging_cleanup_cannot_mask_primary_error(
    manual_gold, monkeypatch, cleanup_operation,
):
    store, patch, path, database = _save(manual_gold)
    before = path.read_bytes(), database.read_bytes()
    seen = []
    error = OSError("primary replace failed")
    original = getattr(Path, cleanup_operation)

    def replace(staging, destination):
        seen.append(Path(staging))
        raise error

    def cleanup(target, *args, **kwargs):
        if seen and target == seen[0]:
            raise PermissionError("secondary cleanup denied")
        return original(target, *args, **kwargs)

    monkeypatch.setattr(gold_ui.os, "replace", replace)
    monkeypatch.setattr(Path, cleanup_operation, cleanup)
    with pytest.raises(GoldUIError) as caught:
        store.save("uid-0", patch)
    assert caught.value.__cause__ is error
    assert any("cleanup" in note or "staging" in note
               for note in getattr(error, "__notes__", []))
    assert (path.read_bytes(), database.read_bytes()) == before
    assert os.path.lexists(seen[0])


@pytest.mark.parametrize("failure_point", ["write", "flush", "fsync"])
def test_manual_write_error_cleans_owned_staging_before_publication(
    manual_gold, monkeypatch, failure_point,
):
    store, patch, path, database = _save(manual_gold)
    before = path.read_bytes(), database.read_bytes()
    factory = gold_ui.tempfile.NamedTemporaryFile
    seen = []
    descriptor = []
    error = OSError("staging write failed")
    fsync = gold_ui.os.fsync

    def durable_sync(fd):
        if descriptor and descriptor[0] == fd:
            raise error
        return fsync(fd)

    def staged_file(*args, **kwargs):
        handle = factory(*args, **kwargs)
        seen.append(Path(handle.name))
        descriptor.append(handle.fileno())

        def fail(*args):
            raise error

        if failure_point != "fsync":
            setattr(handle, failure_point, fail)
        return handle

    monkeypatch.setattr(gold_ui.tempfile, "NamedTemporaryFile", staged_file)
    if failure_point == "fsync":
        monkeypatch.setattr(gold_ui.os, "fsync", durable_sync)
    with pytest.raises(GoldUIError) as caught:
        store.save("uid-0", patch)
    assert caught.value.__cause__ is error
    assert not os.path.lexists(seen[0])
    assert (path.read_bytes(), database.read_bytes()) == before


def test_staging_identity_comes_from_open_descriptor_before_path_changes(
    manual_gold, monkeypatch,
):
    store, patch, path, database = _save(manual_gold)
    path.chmod(0o640)
    before = path.read_bytes(), database.read_bytes()
    factory = gold_ui.tempfile.NamedTemporaryFile
    seen = []
    owned = path.parent / "moved-open-staging"

    def staged_file(*args, **kwargs):
        handle = factory(*args, **kwargs)
        staging = Path(handle.name)
        seen.append(staging)
        staging.rename(owned)
        staging.write_bytes(b"foreign file stays foreign")
        staging.chmod(0o600)
        return handle

    monkeypatch.setattr(gold_ui.tempfile, "NamedTemporaryFile", staged_file)
    with pytest.raises(GoldUIError, match="ownership changed"):
        store.save("uid-0", patch)
    assert seen[0].read_bytes() == b"foreign file stays foreign"
    assert stat.S_IMODE(seen[0].stat().st_mode) == 0o600
    assert stat.S_IMODE(owned.stat().st_mode) == 0o640
    assert (path.read_bytes(), database.read_bytes()) == before


def test_successful_manual_save_leaves_only_backup_audit_and_canonical(manual_gold):
    store, patch, path, database = _save(manual_gold)
    before_db = database.read_bytes()
    old_content = path.read_bytes()
    result = store.save("uid-0", patch)
    assert result["changed"] and result["labels"]["relevance"] == 3
    assert database.read_bytes() == before_db
    assert path.read_bytes() != old_content
    assert list(path.parent.glob("*.tmp")) == []
    backups = list((path.parent / "backups").glob("*.bak"))
    assert len(backups) == 1 and backups[0].read_bytes() == old_content
    assert len(list((path.parent / "backups").glob("*.audit.json"))) == 1
