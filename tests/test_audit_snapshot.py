from __future__ import annotations

import hashlib
import json
import sqlite3

import pytest

from job_intake.storage import snapshot


@pytest.fixture
def audit_state(tmp_path):
    (tmp_path / "config").mkdir()
    local = tmp_path / "data/local"
    local.mkdir(parents=True)
    database = local / "jobs.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE jobs (job_uid TEXT, archived_at TEXT, annotation TEXT)")
        connection.executemany("INSERT INTO jobs VALUES (?,?,?)", [
            ("active", None, '{"mode":"two_pass","usage":{"output_tokens":100}}'),
            ("archived", "2026-10-19", "{}"),
        ])
        connection.execute("CREATE TABLE job_feedback (job_uid TEXT, rating INTEGER)")
        connection.execute("INSERT INTO job_feedback VALUES ('active',3)")
        connection.execute("CREATE TABLE crm_applications (job_uid TEXT, notes TEXT)")
        connection.execute("INSERT INTO crm_applications VALUES ('active','Keep CRM link')")
    config = tmp_path / "config/settings.yaml"
    config.write_text(f"database_url: sqlite:///{database}\n")
    (config.parent / "rules.yaml").write_text("recall_first: true\n")
    (config.parent / "search_profiles.yaml").write_text("streams: []\n")
    for directory in ("gold", "annotations", "prefilter", "discovery"):
        root = local / directory
        root.mkdir()
        (root / "record.jsonl").write_text(json.dumps({"kind": directory, "value": 3}) + "\n")
    (local / "backups").mkdir()
    return config, database, local / "backups/snapshot"


def test_audit_snapshot_preserves_source_bytes_and_restores_all_sql_data(audit_state):
    config, database, output = audit_state
    local = database.parent
    sources = [database, *[local / directory / "record.jsonl"
                          for directory in ("gold", "annotations", "prefilter", "discovery")]]
    before = {path: path.read_bytes() for path in sources}
    manifest = snapshot.snapshot_audit_state(config, output)
    assert manifest["database_access"] == "read_only"
    assert manifest["verification"]["quick_check"] == "ok"
    assert manifest["verification"]["restore_verified"] is True
    assert manifest["verification"]["row_counts"] == {
        "jobs": 2, "active_jobs": 1, "job_feedback": 1, "crm_applications": 1,
    }
    assert manifest["source_data_version_before"] == manifest["source_data_version_after"]
    assert manifest["source_database_sha256_before"] == manifest["source_database_sha256_after"]
    assert {path: path.read_bytes() for path in sources} == before
    assert json.loads((output / "manifest.json").read_text()) == manifest
    for relative, details in manifest["files"].items():
        assert hashlib.sha256((output / relative).read_bytes()).hexdigest() == details["sha256"]
    assert not (output / ".env").exists()
    with sqlite3.connect(f"file:{output / 'job_intake.db'}?mode=ro", uri=True) as connection:
        row = connection.execute("SELECT annotation FROM jobs WHERE job_uid='active'").fetchone()
        assert row == (
            '{"mode":"two_pass","usage":{"output_tokens":100}}',
        )


def test_snapshot_refuses_an_existing_output_before_any_copy(audit_state, monkeypatch):
    config, _, output = audit_state
    output.mkdir()
    (output / "keep.txt").write_text("keep")
    monkeypatch.setattr(snapshot, "load_app_config", lambda _: pytest.fail("Must refuse first"))
    with pytest.raises(ValueError, match="already exists"):
        snapshot.snapshot_audit_state(config, output)
    assert (output / "keep.txt").read_text() == "keep"


@pytest.mark.parametrize("name", [
    ".env", "credentials.json", "private.key", "cookies.txt", "api-key.json",
])
def test_snapshot_refuses_credential_files_in_selected_roots(audit_state, name):
    config, database, output = audit_state
    (database.parent / "gold" / name).write_text("must not copy")
    with pytest.raises(ValueError, match="credential and environment"):
        snapshot.snapshot_audit_state(config, output)
    assert not output.exists()


@pytest.mark.parametrize("value", [
    "pplx-" + "a" * 40, "AIza" + "a" * 35, "-----BEGIN PRIVATE KEY-----",
])
def test_snapshot_refuses_embedded_api_or_private_keys(audit_state, value):
    config, database, output = audit_state
    (database.parent / "gold/record.jsonl").write_text(json.dumps({"value": value}))
    with pytest.raises(ValueError, match="containing API keys or private keys"):
        snapshot.snapshot_audit_state(config, output)
    assert not output.exists()


def test_snapshot_refuses_artifact_symlinks(audit_state):
    config, database, output = audit_state
    (database.parent / "gold/link.json").symlink_to(config)
    with pytest.raises(ValueError, match="symlinks"):
        snapshot.snapshot_audit_state(config, output)
    assert not output.exists()


def test_snapshot_refuses_configured_cache_root_symlinks_before_resolving(audit_state):
    config, database, output = audit_state
    (database.parent / "linked-cache").symlink_to(database.parent / "annotations")
    config.write_text(config.read_text() + "annotation:\n  cache_dir: data/local/linked-cache\n")
    with pytest.raises(ValueError, match="symlinks"):
        snapshot.snapshot_audit_state(config, output)
    assert not output.exists()


def test_snapshot_detects_changed_artifacts_and_publishes_no_incomplete_backup(
    audit_state, monkeypatch,
):
    config, database, output = audit_state
    original_copy = snapshot.shutil.copyfile
    changed = database.parent / "gold/record.jsonl"

    def copy_then_change(source, destination):
        result = original_copy(source, destination)
        if source == changed:
            changed.write_text('{"new_label":0}\n')
        return result

    monkeypatch.setattr(snapshot.shutil, "copyfile", copy_then_change)
    with pytest.raises(ValueError, match="Concurrent source changes"):
        snapshot.snapshot_audit_state(config, output)
    assert not output.exists()
    assert not list(output.parent.glob(".snapshot-*"))


def test_snapshot_detects_a_concurrent_database_commit(audit_state, monkeypatch):
    config, database, output = audit_state
    original_verify = snapshot._verify_restore

    def verify_then_commit(path):
        result = original_verify(path)
        with sqlite3.connect(database) as connection:
            connection.execute("INSERT INTO job_feedback VALUES ('active',0)")
        return result

    monkeypatch.setattr(snapshot, "_verify_restore", verify_then_commit)
    with pytest.raises(ValueError, match="Concurrent source changes"):
        snapshot.snapshot_audit_state(config, output)
    assert not output.exists()


def test_missing_database_is_never_created(audit_state):
    config, database, output = audit_state
    missing = database.with_name("missing.db")
    config.write_text(f"database_url: sqlite:///{missing}\n")
    with pytest.raises(ValueError, match="does not exist"):
        snapshot.snapshot_audit_state(config, output)
    assert not missing.exists() and not output.exists()


def test_snapshot_output_cannot_be_a_child_of_copied_artifacts(audit_state):
    config, database, _ = audit_state
    output = database.parent / "gold/snapshot"
    with pytest.raises(ValueError, match="inside an artifact root"):
        snapshot.snapshot_audit_state(config, output)
    assert not output.exists()
