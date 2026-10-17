"""Manual web labels persist without leaking predictions or changing sources."""

from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from job_intake.annotation import gold_ui
from job_intake.annotation.gold import _snapshot
from job_intake.annotation.gold_ui import GoldLabelStore, GoldUIError
from job_intake.annotation.text import TEXT_VERSION, source_hash
from job_intake.config.settings import AppConfig, LLMConfig, TelegramConfig
from job_intake.models.job import JobRecord

ROLES = [{"id": "analytics-leadership", "name": "Analytics leadership"},
         {"id": "product-management", "name": "Product management"},
         {"id": "other", "name": "Other / unclear"}]


@pytest.fixture
def labeling(tmp_path, monkeypatch):
    monkeypatch.setattr(gold_ui, "configured_roles", lambda _: ROLES)
    database = tmp_path / "jobs.db"
    config = AppConfig(
        database_url=f"sqlite:///{database}", log_level="INFO", sources=[],
        rules_path=tmp_path / "rules.yaml", search_profiles_path=tmp_path / "profiles.yaml",
        company_watchlist_path=tmp_path / "companies.yaml", export_dir=tmp_path,
        telegram=TelegramConfig(), llm=LLMConfig(),
    )
    records, cases = [], []
    for index in range(3):
        record = JobRecord(
            source="test", company=f"Example {index}", title="Analytics Manager",
            original_url=f"https://example.test/jobs/{index}",
            description_clean=f"Lead analytics projects for team {index}. Remote worldwide.",
            source_metadata={"working_language": "English", "description_complete": True,
                             "derived_prediction": "must never reach the UI"},
            annotation={"scores": [100], "model_secret": "hidden"},
        )
        uid = f"uid-{index}"
        records.append((uid, record))
        cases.append({
            "schema": "vacancy-tags-v2", "job_uid": uid, "source_hash": source_hash(record),
            "text_version": TEXT_VERSION, "source": _snapshot(record),
            "label_scope": "blind_decisions", "labeled_at": None, "method": None,
            "tags": {}, "role_label": None, "relevance": None, "gate_outcome": None,
            "notes": "",
        })
    cases[0].update(role_label="analytics-leadership", relevance=0, gate_outcome="unknown",
                    method="blind", labeled_at="2026-10-10", notes="Original note",
                    tags={"work_mode": {"value": "remote", "quote": "Remote worldwide."}})
    cases[2].update(relevance=3, method="assisted", labeled_at="2026-10-16")
    with sqlite3.connect(database) as connection:
        connection.execute("""CREATE TABLE jobs (
            job_uid TEXT PRIMARY KEY, source TEXT, company TEXT, title TEXT,
            original_url TEXT, apply_url TEXT, source_job_id TEXT, posted_at TEXT,
            location_text TEXT, remote_text TEXT, employment_type TEXT, salary_text TEXT,
            timezone_text TEXT, description_raw TEXT, description_clean TEXT, status TEXT,
            source_metadata TEXT, annotation TEXT, updated_at TEXT, archived_at TEXT
        )""")
        for uid, record in records:
            snapshot = _snapshot(record)
            snapshot["source_metadata"] = json.dumps(record.source_metadata)
            values = {"job_uid": uid, **snapshot, "annotation": json.dumps(record.annotation),
                      "updated_at": "2026-10-17", "archived_at": None}
            connection.execute(
                "INSERT INTO jobs (" + ",".join(values) + ") VALUES ("
                + ",".join("?" for _ in values) + ")", list(values.values()),
            )
    path = tmp_path / "gold" / "labels.jsonl"
    path.parent.mkdir()
    # Preserve heterogeneous formatting and blank lines of untouched rows.
    path.write_bytes((json.dumps(cases[0], ensure_ascii=False) + "\r\n\n"
                      + json.dumps(cases[1], ensure_ascii=False, separators=(",", ":")) + "\n"
                      + json.dumps(cases[2], ensure_ascii=False)).encode("utf-8"))
    return config, path, database, cases


def read_cases(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_list_is_blind_searchable_and_counts_partial_not_complete(labeling):
    config, path, database, _ = labeling
    before = path.read_bytes(), database.read_bytes()
    store = GoldLabelStore(config, path)
    result = store.list_cases({})
    assert result["stats"] == {"total": 3, "labeled": 1, "partial": 1, "unlabeled": 1,
                               "relevant": 1, "strong": 1}
    assert result["profiles"][0] == {"value": "analytics-leadership",
                                       "label": "Analytics leadership"}
    assert store.list_cases({"status": "partial"})["cases"][0]["job_uid"] == "uid-2"
    assert store.list_cases({"status": "unlabeled"})["total"] == 1
    assert store.list_cases({"status": "complete", "q": "ORIGINAL NOTE"})["total"] == 1
    page = store.list_cases({"q": "example", "offset": "1", "limit": "1"})
    assert page["has_more"] and page["total"] == 3
    assert page["cases"][0]["job_uid"] == "uid-1"
    assert store.list_cases({"limit": 999})["limit"] == 200
    detail = store.detail("uid-0")
    assert detail["can_save"] and detail["source_state"] == "current"
    assert "derived_prediction" not in json.dumps(detail)
    assert "model_secret" not in json.dumps(detail)
    assert "annotation" not in json.dumps(detail)
    assert store.detail("uid-1")["warnings"] == []
    assert store.detail("uid-2")["warnings"] == []
    assert before == (path.read_bytes(), database.read_bytes())
    assert sorted(item.name for item in path.parent.iterdir()) == ["labels.jsonl"]


def test_partial_zero_null_restart_preserves_other_rows_and_assisted_method(labeling):
    config, path, database, _ = labeling
    before_lines, before_db = path.read_bytes().splitlines(keepends=True), database.read_bytes()
    store = GoldLabelStore(config, path)
    saved = store.save("uid-2", {
        "expected_version": store.detail("uid-2")["version"],
        "role_label": "product-management", "gate_outcome": "eligible",
    })
    assert saved["changed"] and saved["complete"]
    assert saved["labels"]["relevance"] == 3
    persisted = read_cases(path)[2]
    assert persisted["method"] == "assisted"
    assert persisted["labeled_at"] == datetime.now(ZoneInfo("America/Sao_Paulo")).date().isoformat()
    assert path.read_bytes().splitlines(keepends=True)[:3] == before_lines[:3]
    reopened = GoldLabelStore(config, path)
    assert reopened.detail("uid-2")["version"] == saved["version"]
    zero = reopened.save("uid-1", {
        "expected_version": reopened.detail("uid-1")["version"], "relevance": 0,
    })
    assert zero["labels"]["relevance"] == 0 and not zero["complete"]
    assert read_cases(path)[1]["method"] == "blind"
    cleared = reopened.save("uid-1", {"expected_version": zero["version"], "relevance": None})
    assert cleared["labels"]["relevance"] is None
    assert database.read_bytes() == before_db


def test_curated_evidence_unchanged_backup_audit_and_noop(labeling):
    config, path, _, cases = labeling
    store = GoldLabelStore(config, path)
    original = path.read_bytes()
    current = store.detail("uid-0")
    noop = store.save("uid-0", {"expected_version": current["version"], "relevance": 0})
    assert noop["changed"] is False and noop["version"] == current["version"]
    assert path.read_bytes() == original
    assert not (path.parent / "backups").exists()
    saved = store.save("uid-0", {"expected_version": current["version"], "relevance": 2,
                                 "notes": "A human correction"})
    assert saved["changed"]
    updated = read_cases(path)[0]
    assert updated["tags"] == cases[0]["tags"]
    assert updated["source"] == cases[0]["source"]
    assert updated["source_hash"] == cases[0]["source_hash"]
    backups = list((path.parent / "backups").glob("*.bak"))
    audits = list((path.parent / "backups").glob("*.audit.json"))
    assert len(backups) == len(audits) == 1
    assert backups[0].read_bytes() == original
    audit = json.loads(audits[0].read_text())
    assert audit["before_file_sha256"] == hashlib.sha256(original).hexdigest()
    assert audit["after_file_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert audit["after_row_sha256"] == saved["version"]
    assert audit["changed_fields"] == ["relevance", "notes"]
    assert "A human correction" not in json.dumps(audit)


def test_stale_same_row_conflicts_without_losing_labels(labeling):
    config, path, _, _ = labeling
    first, second = GoldLabelStore(config, path), GoldLabelStore(config, path)
    version = first.detail("uid-1")["version"]
    first.save("uid-1", {"expected_version": version, "relevance": 3})
    before = path.read_bytes()
    with pytest.raises(GoldUIError) as failure:
        second.save("uid-1", {"expected_version": version, "notes": "Stale tab"})
    assert failure.value.status == 409
    assert path.read_bytes() == before
    assert len(list((path.parent / "backups").glob("*.bak"))) == 1


def test_concurrent_other_rows_merge_across_store_instances(labeling):
    config, path, _, _ = labeling
    stores = [GoldLabelStore(config, path), GoldLabelStore(config, path)]
    versions = [store.detail(uid)["version"]
                for store, uid in zip(stores, ("uid-0", "uid-1"), strict=True)]

    def save(index):
        return stores[index].save(f"uid-{index}", {
            "expected_version": versions[index], "notes": f"Concurrent note {index}",
        })

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, range(2)))
    assert all(result["changed"] for result in results)
    assert [case["notes"] for case in read_cases(path)[:2]] == [
        "Concurrent note 0", "Concurrent note 1",
    ]
    assert len(list((path.parent / "backups").glob("*.bak"))) == 2


@pytest.mark.parametrize("patch", [
    {"relevance": True}, {"relevance": False}, {"relevance": 3.0}, {"relevance": "3"},
    {"relevance": -1}, {"relevance": 4}, {"role_label": "invented"}, {"role_label": []},
    {"gate_outcome": "pass"}, {"gate_outcome": []}, {"notes": None},
    {"method": "assisted"}, {"source": {}}, {"tags": {}},
])
def test_invalid_patch_cannot_change_file(labeling, patch):
    config, path, _, _ = labeling
    store = GoldLabelStore(config, path)
    before = path.read_bytes()
    with pytest.raises(GoldUIError) as failure:
        store.save("uid-1", {"expected_version": store.detail("uid-1")["version"], **patch})
    assert failure.value.status == 400
    assert path.read_bytes() == before
    assert not (path.parent / "backups").exists()


@pytest.mark.parametrize("query", [
    {"offset": True}, {"limit": False}, {"offset": -1}, {"limit": 0}, {"limit": "1.0"},
    {"q": []}, {"status": "done"}, {"unknown": "value"},
])
def test_invalid_list_query_rejected(labeling, query):
    config, path, _, _ = labeling
    with pytest.raises(GoldUIError):
        GoldLabelStore(config, path).list_cases(query)


def test_source_drift_blocks_rebinding_and_leaves_snapshot_visible(labeling):
    config, path, database, cases = labeling
    store = GoldLabelStore(config, path)
    version = store.detail("uid-1")["version"]
    before = path.read_bytes()
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE jobs SET description_clean='Different job' "
                           "WHERE job_uid='uid-1'")
    detail = store.detail("uid-1")
    assert detail["source_state"] == "drift" and not detail["can_save"]
    assert detail["source"] == cases[1]["source"]
    with pytest.raises(GoldUIError) as failure:
        store.save("uid-1", {"expected_version": version, "relevance": 3})
    assert failure.value.status == 409 and path.read_bytes() == before
    assert not (path.parent / "backups").exists()


@pytest.mark.parametrize("source_state", ["missing", "archived", "malformed"])
def test_missing_archived_or_malformed_database_still_displays_snapshot(labeling, source_state):
    config, path, database, cases = labeling
    if source_state == "malformed":
        database.write_bytes(b"Not a SQLite database")
    else:
        with sqlite3.connect(database) as connection:
            if source_state == "missing":
                connection.execute("DELETE FROM jobs WHERE job_uid='uid-1'")
            else:
                connection.execute("UPDATE jobs SET archived_at='2026-10-17' WHERE job_uid='uid-1'")
    detail = GoldLabelStore(config, path).detail("uid-1")
    assert not detail["can_save"]
    assert detail["source"] == cases[1]["source"]
    assert detail["source_state"] == ("unavailable" if source_state == "malformed" else "missing")


def test_missing_gold_and_memory_constructor_are_lazy(labeling, tmp_path):
    config, _, _, _ = labeling
    missing = tmp_path / "not-created" / "gold.jsonl"
    store = GoldLabelStore(config, missing)
    assert not missing.parent.exists()
    with pytest.raises(GoldUIError) as failure:
        store.list_cases({})
    assert failure.value.status == 404
    assert not missing.parent.exists()
    memory = copy.copy(config)
    memory.database_url = "sqlite:///:memory:"
    lazy = GoldLabelStore(memory)
    with pytest.raises(GoldUIError) as failure:
        lazy.list_cases({})
    assert failure.value.status == 404


def test_default_path_uses_database_parent_and_missing_uid_is_404(labeling):
    config, path, _, _ = labeling
    assert GoldLabelStore(config).path == path.parent / "vacancy-tags-v2-labeled.jsonl"
    with pytest.raises(GoldUIError) as failure:
        GoldLabelStore(config, path).detail("unknown")
    assert failure.value.status == 404


def test_invalid_gold_and_derived_snapshot_data_never_leak(labeling):
    config, path, _, cases = labeling
    cases[1]["source"]["annotation"] = {"scores": "secret prediction"}
    cases[1]["source"]["source_metadata"]["model_secret"] = "another prediction"
    path.write_text("\n".join(json.dumps(case) for case in cases))
    detail = GoldLabelStore(config, path).detail("uid-1")
    assert not detail["can_save"]
    assert "secret prediction" not in json.dumps(detail)
    assert "another prediction" not in json.dumps(detail)
    path.write_text('{"job_uid":"x","job_uid":"duplicate"}')
    with pytest.raises(GoldUIError) as failure:
        GoldLabelStore(config, path).list_cases({})
    assert failure.value.status == 500


def test_failed_atomic_publish_preserves_original_and_keeps_recovery_record(labeling, monkeypatch):
    config, path, _, _ = labeling
    store = GoldLabelStore(config, path)
    before = path.read_bytes()
    version = store.detail("uid-1")["version"]

    def interrupted(*args):
        raise OSError("Interrupted publication")

    monkeypatch.setattr(gold_ui.os, "replace", interrupted)
    with pytest.raises(GoldUIError) as failure:
        store.save("uid-1", {"expected_version": version, "relevance": 3})
    assert failure.value.status == 500 and path.read_bytes() == before
    assert len(list((path.parent / "backups").glob("*.bak"))) == 1
    assert len(list((path.parent / "backups").glob("*.audit.json"))) == 1
    assert not list(path.parent.glob("*.tmp"))


def test_audit_write_failure_prevents_gold_commit(labeling, monkeypatch):
    config, path, _, _ = labeling
    store = GoldLabelStore(config, path)
    before = path.read_bytes()
    version = store.detail("uid-1")["version"]
    original_open = Path.open

    def cannot_audit(destination, *args, **kwargs):
        if destination.name.endswith(".audit.json"):
            raise PermissionError("Audit directory not writable")
        return original_open(destination, *args, **kwargs)

    monkeypatch.setattr(Path, "open", cannot_audit)
    with pytest.raises(GoldUIError) as failure:
        store.save("uid-1", {"expected_version": version, "relevance": 3})
    assert failure.value.status == 500
    assert path.read_bytes() == before
    assert len(list((path.parent / "backups").glob("*.bak"))) == 1


def test_concurrent_same_row_has_one_commit_and_one_conflict(labeling):
    config, path, _, _ = labeling
    stores = [GoldLabelStore(config, path), GoldLabelStore(config, path)]
    version = stores[0].detail("uid-1")["version"]

    def save(index):
        try:
            stores[index].save("uid-1", {"expected_version": version, "relevance": index + 2})
        except GoldUIError as error:
            return error.status
        return 200

    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses = list(pool.map(save, range(2)))
    assert sorted(statuses) == [200, 409]
    assert len(list((path.parent / "backups").glob("*.bak"))) == 1
