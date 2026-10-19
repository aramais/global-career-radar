"""Verified local audit snapshots without initializing or migrating the database."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sqlite3
import tempfile
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

from sqlalchemy.engine import make_url

from job_intake.annotation.accuracy import ACCURACY_VERSION
from job_intake.annotation.eval_runner import sqlite_path
from job_intake.annotation.input_contract import INPUT_CONTRACT_VERSION, MODEL_KEY_VERSION
from job_intake.annotation.text import SOURCE_UNITS_VERSION, TEXT_VERSION
from job_intake.config.settings import load_app_config, load_yaml_mapping

SNAPSHOT_VERSION = "audit-state-snapshot-v1"
_TABLES = (
    "jobs", "job_relevance_evaluations", "job_profile_evaluations", "job_events",
    "job_feedback", "alert_outbox", "annotation_history",
)
_SECRET_NAME = re.compile(
    r"(?:^|[._-])(?:env|secrets?|credentials?|tokens?|cookies?|api[._-]?keys?|"
    r"id_rsa|id_ed25519)(?:$|[._-])", re.I,
)
_SECRET_CONTENT = re.compile(
    rb"pplx-[A-Za-z0-9]{20,}|AIza[A-Za-z0-9_-]{35}|"
    rb"sk-(?:proj-|ant-api[0-9]{2}-)?[A-Za-z0-9_-]{32,}|"
    rb"-----BEGIN (?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----",
)


class SnapshotError(ValueError):
    """A safe snapshot diagnostic which contains no source or credential values."""


def _safe_path(path: Path) -> Path:
    """Check the lexical path before resolving it; never follow a symlink."""
    absolute = path.expanduser().absolute()
    for part in (absolute, *absolute.parents):
        if part.is_symlink():
            raise SnapshotError("Snapshot refuses symlinks")
        if (_SECRET_NAME.search(part.name)
                or part.suffix.lower() in {".pem", ".key", ".p12", ".pfx"}):
            raise SnapshotError("Snapshot refuses credential and environment paths")
    return absolute


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    tail = b""
    with _safe_path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            if _SECRET_CONTENT.search(tail + block):
                raise SnapshotError(
                    "Snapshot refuses artifacts containing API keys or private keys",
                )
            digest.update(block)
            tail = block[-256:]
    return digest.hexdigest()


def _inventory(roots: dict[str, Path]) -> dict[str, tuple[Path, str]]:
    result = {}
    for name, root in sorted(roots.items()):
        root = _safe_path(root)
        if not root.exists():
            continue
        if not root.is_dir():
            raise SnapshotError("Snapshot artifact roots must be directories")
        for path in sorted(root.rglob("*")):
            _safe_path(path)
            if path.is_file():
                relative = (Path(name) / path.relative_to(root)).as_posix()
                result[relative] = (path, _sha256(path))
            elif not path.is_dir():
                raise SnapshotError("Snapshot refuses special files")
    return result


def _read_only(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{quote(str(path), safe='/')}?mode=ro", uri=True)
    connection.execute("PRAGMA query_only=ON")
    return connection


def _database_state(connection: sqlite3.Connection) -> dict:
    tables = {row[0] for row in connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table'",
    )}
    if "jobs" not in tables:
        raise SnapshotError("Snapshot requires an existing jobs table; no migration is performed")
    selected = sorted(set(_TABLES) | {table for table in tables if table.startswith("crm_")})
    def identifier(name: str) -> str:
        return '"' + name.replace('"', '""') + '"'

    schemas = {
        table: [row[1] for row in connection.execute(f'PRAGMA table_info({identifier(table)})')]
        for table in selected if table in tables
    }
    counts = {
        table: connection.execute(f'SELECT COUNT(*) FROM {identifier(table)}').fetchone()[0]
        for table in schemas
    }
    if "archived_at" in schemas["jobs"]:
        counts["active_jobs"] = connection.execute(
            "SELECT COUNT(*) FROM jobs WHERE archived_at IS NULL",
        ).fetchone()[0]
    return {"schema_columns": schemas, "row_counts": counts}


def _logical_digest(connection: sqlite3.Connection) -> str:
    digest = hashlib.sha256()
    for statement in connection.iterdump():
        digest.update(statement.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def _verify_restore(database: Path) -> dict:
    """Actually restore the copied database to a new file and compare all SQL data."""
    with closing(_read_only(database)) as saved:
        if saved.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            raise SnapshotError("Snapshot database failed quick_check")
        state = _database_state(saved)
        logical_hash = _logical_digest(saved)
        with tempfile.TemporaryDirectory(prefix="job-intake-restore-check-") as directory:
            restored_path = Path(directory) / "restored.db"
            with closing(sqlite3.connect(restored_path)) as restored:
                saved.backup(restored)
            with closing(_read_only(restored_path)) as restored:
                if restored.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                    raise SnapshotError("Restored snapshot failed quick_check")
                if _database_state(restored) != state or _logical_digest(restored) != logical_hash:
                    raise SnapshotError("Restored snapshot differs from its backup")
    return {**state, "quick_check": "ok", "restore_verified": True,
            "restored_logical_sha256": logical_hash}


def snapshot_audit_state(config: Path, output: Path) -> dict:
    """Copy a stable DB, gold, paid caches and discovery to a new local directory.

    Concurrent changes make the entire snapshot fail; an incomplete destination is
    never published. The output must be outside every selected artifact root.
    Environment files, cookie files and symlinks are never included.
    """
    config = _safe_path(config)
    output = _safe_path(output)
    if output.exists():
        raise SnapshotError("Snapshot output already exists; refusing overwrite")
    if not output.parent.is_dir():
        raise SnapshotError("Snapshot output parent must already exist")
    settings = load_app_config(config)
    raw_settings = load_yaml_mapping(config)
    database_value = make_url(settings.database_url).database
    if not database_value:
        raise SnapshotError("Snapshot requires an existing SQLite database")
    _safe_path(Path(database_value))
    database = sqlite_path(settings.database_url)
    if not database.is_file():
        raise SnapshotError("Snapshot database does not exist; it will not be created")
    roots = {
        "gold": database.parent / "gold",
        "annotations": config.parent.parent / raw_settings.get("annotation", {}).get(
            "cache_dir", "data/local/annotations",
        ),
        "prefilter": config.parent.parent / raw_settings.get("prefilter", {}).get(
            "cache_dir", "data/local/prefilter",
        ),
        "discovery": database.parent / "discovery",
    }
    for root in roots.values():
        root = _safe_path(root)
        if output == root or output.is_relative_to(root):
            raise SnapshotError("Snapshot output cannot be inside an artifact root")
    config_paths = {
        "settings": config,
        "rules": _safe_path(config.parent / raw_settings.get("rules_path", "rules.yaml")),
        "profiles": _safe_path(config.parent / raw_settings.get(
            "search_profiles_path", "search_profiles.yaml",
        )),
    }
    config_hashes = {name: _sha256(path) for name, path in config_paths.items() if path.is_file()}
    database_files = {"database": database, "wal": database.with_name(database.name + "-wal")}
    with closing(_read_only(database)) as source:
        version_before = source.execute("PRAGMA data_version").fetchone()[0]
        database_hashes = {name: _sha256(path) for name, path in database_files.items()
                           if path.is_file()}
        state_before = _database_state(source)
        before = _inventory(roots)
        with tempfile.TemporaryDirectory(prefix=f".{output.name}-", dir=output.parent) as temporary:
            staging = Path(temporary)
            with closing(sqlite3.connect(staging / "job_intake.db")) as target:
                source.backup(target)
            for relative, (path, expected_hash) in before.items():
                destination = staging / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(_safe_path(path), destination)
                if _sha256(destination) != expected_hash:
                    raise SnapshotError("Source artifacts changed during snapshot copy")
            verification = _verify_restore(staging / "job_intake.db")
            after = _inventory(roots)
            database_after = {name: _sha256(path) for name, path in database_files.items()
                              if path.is_file()}
            config_after = {name: _sha256(path) for name, path in config_paths.items()
                            if path.is_file()}
            state_after = _database_state(source)
            version_after = source.execute("PRAGMA data_version").fetchone()[0]
            if (before != after or database_hashes != database_after
                    or version_before != version_after or state_before != state_after
                    or config_hashes != config_after):
                raise SnapshotError(
                    "Concurrent source changes detected; snapshot was not published",
                )
            if verification["row_counts"] != state_before["row_counts"]:
                raise SnapshotError("Snapshot database row counts differ from the stable source")
            files = {relative: {"sha256": digest, "bytes": (staging / relative).stat().st_size}
                     for relative, (_, digest) in before.items()}
            files["job_intake.db"] = {"sha256": _sha256(staging / "job_intake.db"),
                                      "bytes": (staging / "job_intake.db").stat().st_size}
            manifest = {
                "version": SNAPSHOT_VERSION, "created_at": datetime.now(UTC).isoformat(),
                "source_database": str(database), "database_access": "read_only",
                "source_data_version_before": version_before,
                "source_data_version_after": version_after,
                "source_database_sha256_before": database_hashes,
                "source_database_sha256_after": database_after,
                "source_artifacts_unchanged": True, "configuration_sha256": config_hashes,
                "versions": {"text": TEXT_VERSION, "source_units": SOURCE_UNITS_VERSION,
                             "accuracy": ACCURACY_VERSION,
                             "annotation_input": INPUT_CONTRACT_VERSION,
                             "model_cache_key": MODEL_KEY_VERSION},
                "missing_artifact_roots": [name for name, root in roots.items()
                                           if not root.exists()],
                "verification": verification, "files": files,
            }
            (staging / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                encoding="utf-8",
            )
            # mkdir with exclusive creation makes competing snapshots fail without overwrite.
            output.mkdir()
            try:
                ordered = sorted(staging.iterdir(), key=lambda item: item.name == "manifest.json")
                for path in ordered:
                    shutil.move(str(path), output / path.name)
            except BaseException:
                shutil.rmtree(output)
                raise
    return manifest
