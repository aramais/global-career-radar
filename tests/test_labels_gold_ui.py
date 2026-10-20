"""P1 human v3 labeling uses source-only data and preserves canonical provenance."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

from job_intake.annotation import gold_ui
from job_intake.annotation.gold import _snapshot
from job_intake.annotation.gold_ui import GoldLabelStore, GoldUIError
from job_intake.annotation.text import TEXT_VERSION, source_hash
from job_intake.config.settings import AppConfig, LLMConfig, TelegramConfig
from job_intake.labeling.schema import LABELS_SCHEMA, TAG_NAMES, validate_gold_row
from job_intake.models.job import JobRecord

ROOT = Path(__file__).parents[1]
ROLES = [{"id": "analytics-leadership", "name": "Analytics leadership"},
         {"id": "other", "name": "Other / unclear"}]


def write_cases(path, cases):
    path.write_bytes((json.dumps(cases[0], ensure_ascii=False) + "\r\n\n"
                      + "\n".join(json.dumps(row, separators=(",", ":"))
                                   for row in cases[1:])).encode())


def read_cases(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def snapshot(path):
    return path.read_bytes(), path.stat().st_mtime_ns


@pytest.fixture
def labeling(tmp_path, monkeypatch):
    monkeypatch.setattr(gold_ui, "configured_roles", lambda _: ROLES)
    database = tmp_path / "jobs.db"
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "labeling_rubric.yaml").write_bytes(
        (ROOT / "config/labeling_rubric.yaml").read_bytes(),
    )
    config = AppConfig(
        database_url=f"sqlite:///{database}", log_level="INFO", sources=[],
        rules_path=config_dir / "rules.yaml", search_profiles_path=config_dir / "profiles.yaml",
        company_watchlist_path=config_dir / "companies.yaml", export_dir=tmp_path,
        telegram=TelegramConfig(), llm=LLMConfig(),
    )
    records, cases = [], []
    for index in range(3):
        record = JobRecord(
            source="test", company=f"Example {index}", title="Analytics Manager",
            original_url=f"https://example.test/jobs/{index}",
            description_clean=("You will lead analytics and pricing experiments for business "
                               "decisions. Remote worldwide. English is required. "
                               "Experience in insurance is required."),
            source_metadata={"working_language": "English", "description_complete": True,
                             "labels_v3": {"relevance": 3, "secret": "machine-secret"}},
            annotation={"labels_v3": {"relevance": 3}, "tier": "A", "score": 100},
        )
        uid = f"uid-{index}"
        records.append((uid, record))
        cases.append({
            "schema": LABELS_SCHEMA, "job_uid": uid, "source_hash": source_hash(record),
            "text_version": TEXT_VERSION, "source": _snapshot(record), "labeler": "human",
            "label_scope": "full_tags", "rubric_version": None,
            "first_labeled_at": None, "labeled_at": None, "method": None,
            "tags": {}, "role_label": None, "relevance": None, "gate_outcome": None,
            "gate_reasons": [], "notes": "",
        })
    cases[0].update(role_label="analytics-leadership", relevance=2, gate_outcome="unknown",
                    method="curated", first_labeled_at="2026-10-17", labeled_at="2026-10-18",
                    rubric_version="historical-rubric", tags={
                        "work_mode": {"value": "remote", "quote": "Remote worldwide."},
                    })
    with sqlite3.connect(database) as connection:
        values = {"job_uid": records[0][0], **_snapshot(records[0][1]), "annotation": "{}",
                  "updated_at": "2026-10-20", "archived_at": None}
        connection.execute("CREATE TABLE jobs (" + ",".join(
            name + " TEXT" for name in values
        ) + ")")
        for uid, record in records:
            values = {"job_uid": uid, **_snapshot(record),
                      "annotation": json.dumps(record.annotation), "updated_at": "2026-10-20",
                      "archived_at": None}
            values["source_metadata"] = json.dumps(record.source_metadata)
            connection.execute("INSERT INTO jobs VALUES (" + ",".join("?" for _ in values)
                               + ")", list(values.values()))
    path = tmp_path / "gold" / "labels.jsonl"
    path.parent.mkdir()
    write_cases(path, cases)
    return config, path, database, cases, records


def six_tags():
    duty = "You will lead analytics and pricing experiments for business decisions."
    return {
        "hiring_eligibility": {"value": "open_to_brazil", "quote": "Remote worldwide."},
        "work_mode": {"value": "remote", "quote": "Remote worldwide."},
        "working_language": {"value": "english", "quote": "English is required.",
                             "basis": "explicit"},
        "function_core": {"value": "analytics_for_decisions", "quote": duty},
        "domain_fit": {"industry": "fintech", "requirement": "required",
                       "required_domain": "other", "domain": "insurance", "basis": "explicit",
                       "quote": "Experience in insurance is required."},
        "preferred_focus": {"values": ["pricing_monetization"], "quote": duty},
    }


def test_v3_options_human_fields_and_source_are_blind_without_mutation(labeling):
    config, path, database, _, _ = labeling
    before = snapshot(path), snapshot(database)
    store = GoldLabelStore(config, path)
    page = store.list_cases({})
    detail = store.detail("uid-0")
    assert page["schema"] == detail["schema"] == LABELS_SCHEMA
    assert set(detail["label_options"]["tags"]) == set(TAG_NAMES)
    assert detail["label_options"]["tags"]["domain_fit"]["domain_max"] == 60
    assert "personal_constraint" in detail["label_options"]["gate_reasons"]
    assert detail["labels"]["tags"] == {
        "work_mode": {"value": "remote", "quote": "Remote worldwide."},
    }
    assert detail["labels"]["gate_reasons"] == []
    serialized = json.dumps([page, detail])
    for forbidden in ("machine-secret", '"annotation"', '"labels_v3"', '"tier"', '"score"'):
        assert forbidden not in serialized
    assert detail["can_save"] and not store.detail("uid-1")["complete"]
    assert (snapshot(path), snapshot(database)) == before


def test_unknown_tag_names_cannot_leak_machine_text_through_blind_diagnostics(labeling):
    config, path, database, cases, _ = labeling
    cases[0]["tags"]["MACHINE_RELEVANCE_THREE_PRIVATE_MARKER"] = {
        "value": "MACHINE_VALUE_PRIVATE_MARKER", "quote": "",
    }
    write_cases(path, cases)
    before = snapshot(path), snapshot(database)
    detail = GoldLabelStore(config, path).detail("uid-0")
    serialized = json.dumps(detail)
    assert not detail["can_save"] and detail["source_state"] == "invalid"
    assert "MACHINE_RELEVANCE_THREE_PRIVATE_MARKER" not in serialized
    assert "MACHINE_VALUE_PRIVATE_MARKER" not in serialized
    assert any("Unknown v3 tag" in warning for warning in detail["warnings"])
    assert (snapshot(path), snapshot(database)) == before


def test_v3_six_tags_save_partial_provenance_and_quotes_without_changing_other_rows(labeling):
    config, path, database, _, records = labeling
    store = GoldLabelStore(config, path)
    lines, before_db = path.read_bytes().splitlines(keepends=True), snapshot(database)
    result = store.save("uid-1", {
        "expected_version": store.detail("uid-1")["version"], "tags": six_tags(),
        "review_seconds": 12.1254,
    })
    updated = read_cases(path)[1]
    assert result["changed"] and result["can_save"] and not result["complete"]
    assert updated["tags"] == six_tags()
    assert updated["method"] == "blind" and updated["labeler"] == "human"
    assert updated["first_labeled_at"] == updated["labeled_at"]
    assert updated["rubric_version"] == "labels-rubric-2026-10-20"
    assert updated["role_label"] is updated["relevance"] is updated["gate_outcome"] is None
    assert validate_gold_row(updated, record=dict(records)["uid-1"],
                             profile_ids=["analytics-leadership"]) == []
    assert path.read_bytes().splitlines(keepends=True)[:2] == lines[:2]
    assert path.read_bytes().splitlines(keepends=True)[3:] == lines[3:]
    assert snapshot(database) == before_db
    assert "review_seconds" not in updated
    audit = json.loads(next((path.parent / "backups").glob("*.audit.json")).read_text())
    assert audit["review_seconds"] == 12.125
    assert audit["changed_fields"] == ["tags"]
    assert six_tags()["domain_fit"]["quote"] not in json.dumps(audit)
    assert store.list_cases({"status": "partial"})["cases"][0]["job_uid"] == "uid-1"


def test_v3_edit_preserves_original_provenance_and_source(labeling):
    config, path, database, cases, _ = labeling
    store = GoldLabelStore(config, path)
    before_db = snapshot(database)
    store.save("uid-0", {"expected_version": store.detail("uid-0")["version"],
                         "notes": "Human correction", "gate_reasons": ["personal_constraint"]})
    updated = read_cases(path)[0]
    for field in ("first_labeled_at", "method", "rubric_version", "source", "source_hash",
                  "text_version", "label_scope", "tags"):
        assert updated[field] == cases[0][field]
    assert updated["method"] == "curated"
    assert updated["gate_reasons"] == ["personal_constraint"]
    assert snapshot(database) == before_db


def test_v3_reason_only_partial_has_provenance_and_clear_does_not_erase_history(labeling):
    config, path, _, _, _ = labeling
    store = GoldLabelStore(config, path)
    saved = store.save("uid-1", {"expected_version": store.detail("uid-1")["version"],
                                 "gate_reasons": ["personal_constraint"]})
    first = read_cases(path)[1]
    assert not saved["complete"] and first["method"] == "blind"
    assert first["first_labeled_at"] == first["labeled_at"]
    assert store.list_cases({"status": "partial"})["total"] == 1
    store.save("uid-1", {"expected_version": saved["version"], "gate_reasons": []})
    cleared = read_cases(path)[1]
    assert cleared["first_labeled_at"] == first["first_labeled_at"]
    assert cleared["rubric_version"] == first["rubric_version"]
    assert store.list_cases({"status": "unlabeled"})["total"] == 2


@pytest.mark.parametrize("patch", [
    {"tags": []}, {"gate_reasons": "personal_constraint"},
    {"tags": {"function_core": {"value": "invented", "quote": ""}}},
    {"tags": {"function_core": {"value": "analytics_reporting", "quote": "Not in source"}}},
    {"tags": {"work_mode": {"value": "office_elsewhere", "quote": ""}}},
    {"tags": {"work_mode": {"value": "office_elsewhere", "quote": "Remote worldwide.",
                            "basis": "implied"}}},
    {"tags": {"domain_fit": {"industry": "other", "requirement": "required",
                              "required_domain": "other", "quote": "Remote worldwide."}}},
    {"tags": {"preferred_focus": {"values": ["pricing_monetization"] * 2, "quote": ""}}},
    {"tags": {"function_core": {"value": "analytics_reporting", "quote": "", "score": 100}}},
    {"gate_reasons": ["not-a-reason"]}, {"gate_reasons": ["personal_constraint"] * 2},
    {"gate_outcome": "eligible", "gate_reasons": ["personal_constraint"]},
    {"method": "assisted_llm"}, {"rubric_version": "new"}, {"labeler": "llm:test"},
])
def test_invalid_v3_manual_patch_does_not_publish_or_change_database(labeling, patch):
    config, path, database, _, _ = labeling
    store = GoldLabelStore(config, path)
    before = snapshot(path), snapshot(database)
    with pytest.raises(GoldUIError) as failure:
        store.save("uid-1", {"expected_version": store.detail("uid-1")["version"], **patch})
    assert failure.value.status == 400
    assert (snapshot(path), snapshot(database)) == before
    assert not (path.parent / "backups").exists()


@pytest.mark.parametrize("schema", ["unknown", None, [], "vacancy-tags-v2"])
def test_unknown_or_mixed_schema_is_rejected_as_dataset_error(labeling, schema):
    config, path, database, cases, _ = labeling
    cases[1]["schema"] = schema
    write_cases(path, cases)
    before = snapshot(path), snapshot(database)
    with pytest.raises(GoldUIError) as failure:
        GoldLabelStore(config, path).list_cases({})
    assert failure.value.status == 500
    assert (snapshot(path), snapshot(database)) == before


def test_default_prefers_existing_v3_but_keeps_selected_file_stable(labeling):
    config, path, _, cases, _ = labeling
    v2 = {key: value for key, value in cases[1].items()
          if key not in {"labeler", "first_labeled_at", "rubric_version", "gate_reasons"}}
    v2["schema"] = "vacancy-tags-v2"
    old = path.parent / "vacancy-tags-v2-labeled.jsonl"
    old.write_text(json.dumps(v2))
    existing = GoldLabelStore(config)
    assert existing.path == old
    v3 = path.parent / "vacancy-labels-v3.jsonl"
    v3.write_bytes(path.read_bytes())
    assert existing.path == old
    assert existing.detail("uid-1")["schema"] == "vacancy-tags-v2"
    assert "label_options" not in existing.list_cases({})
    assert GoldLabelStore(config).path == v3
    assert GoldLabelStore(config, old).path == old


@pytest.mark.parametrize("default_name", ["vacancy-labels-v3.jsonl",
                                          "vacancy-tags-v2-labeled.jsonl"])
def test_default_and_explicit_symlinks_share_target_lock_version_and_merge_rows(labeling,
                                                                               default_name):
    config, path, database, _, _ = labeling
    target = path.parent.parent / "canonical.jsonl"
    path.rename(target)
    alias = path.parent / default_name
    alias.symlink_to(target)
    default, explicit = GoldLabelStore(config), GoldLabelStore(config, alias)
    assert default.path == explicit.path == target.resolve()
    versions = {uid: default.detail(uid)["version"] for uid in ("uid-0", "uid-1")}
    assert versions["uid-0"] == explicit.detail("uid-0")["version"]
    before_db = snapshot(database)

    default.save("uid-0", {"expected_version": versions["uid-0"], "relevance": 3})
    before_stale = snapshot(target)
    with pytest.raises(GoldUIError) as failure:
        explicit.save("uid-0", {"expected_version": versions["uid-0"],
                                "gate_outcome": "eligible"})
    assert failure.value.status == 409 and snapshot(target) == before_stale
    # The other row's version still applies; its update merges the latest file.
    explicit.save("uid-1", {"expected_version": versions["uid-1"], "relevance": 1})
    assert alias.is_symlink() and alias.resolve() == target
    assert alias.read_bytes() == target.read_bytes()
    assert [case["relevance"] for case in read_cases(target)] == [3, 1, None]
    assert default.detail("uid-1")["labels"]["relevance"] == 1
    assert target.with_name(target.name + ".lock").exists()
    assert not alias.with_name(alias.name + ".lock").exists()
    assert len(list((target.parent / "backups").glob("*.bak"))) == 2
    assert not (alias.parent / "backups").exists()
    assert snapshot(database) == before_db


@pytest.mark.parametrize("first,last", [
    ("2026-10-21T00:30:00.123456789+14:00", "2026-10-21T00:31:00.123456789+14:00"),
    ("2026-10-20", "2026-10-20"),
    ("2026-10-20", "2026-10-20T00:00:00+14:00"),
])
def test_store_save_preserves_first_precision_and_records_full_aware_sp_timestamp(
    labeling, monkeypatch, first, last,
):
    config, path, database, cases, records = labeling
    cases[0].update(first_labeled_at=first, labeled_at=last)
    write_cases(path, cases)
    now = datetime(2026, 10, 20, 13, 0, 0, 987654, tzinfo=gold_ui._LOCAL_ZONE)

    class Clock:
        @staticmethod
        def now(tz):
            return now.astimezone(tz)

    monkeypatch.setattr(gold_ui, "datetime", Clock)
    store = GoldLabelStore(config, path)
    detail = store.detail("uid-0")
    assert detail["can_save"]
    before_db = snapshot(database)
    saved = store.save("uid-0", {"expected_version": detail["version"], "relevance": 3})
    updated = read_cases(path)[0]
    assert saved["changed"] and saved["can_save"]
    assert updated["first_labeled_at"] == first
    assert updated["labeled_at"] == "2026-10-20T13:00:00.987654-03:00"
    assert validate_gold_row(updated, record=dict(records)["uid-0"],
                             profile_ids=["analytics-leadership"]) == []
    assert snapshot(database) == before_db


def test_first_manual_label_records_same_aware_timestamp_for_both_provenance_fields(
    labeling, monkeypatch,
):
    config, path, _, _, _ = labeling
    now = datetime(2026, 10, 20, 13, 0, 0, 123456, tzinfo=gold_ui._LOCAL_ZONE)

    class Clock:
        @staticmethod
        def now(tz):
            return now.astimezone(tz)

    monkeypatch.setattr(gold_ui, "datetime", Clock)
    store = GoldLabelStore(config, path)
    store.save("uid-1", {"expected_version": store.detail("uid-1")["version"], "relevance": 2})
    updated = read_cases(path)[1]
    assert updated["first_labeled_at"] == updated["labeled_at"] == (
        "2026-10-20T13:00:00.123456-03:00"
    )


@pytest.mark.parametrize("seconds", [None, True, -1, 1801, float("nan"), float("inf"), 10**400])
def test_invalid_review_duration_is_rejected_before_any_gold_write(labeling, seconds):
    config, path, database, _, _ = labeling
    store = GoldLabelStore(config, path)
    before = snapshot(path), snapshot(database)
    with pytest.raises(GoldUIError, match="review_seconds"):
        store.save("uid-1", {"expected_version": store.detail("uid-1")["version"],
                             "review_seconds": seconds, "relevance": 2})
    assert (snapshot(path), snapshot(database)) == before
    assert not (path.parent / "backups").exists()


def test_unchanged_review_journals_time_without_rewriting_gold_and_retry_is_deduplicated(labeling):
    config, path, database, _, _ = labeling
    store = GoldLabelStore(config, path)
    payload = {"expected_version": store.detail("uid-0")["version"],
               "relevance": 2, "review_seconds": 5}
    before = snapshot(path), snapshot(database)
    assert store.save("uid-0", payload)["changed"] is False
    assert store.save("uid-0", payload)["changed"] is False
    assert (snapshot(path), snapshot(database)) == before
    audits = list((path.parent / "backups").glob("*.audit.json"))
    assert len(audits) == 1 and not list((path.parent / "backups").glob("*.bak"))
    event = json.loads(audits[0].read_text())
    assert event["operation"] == "manual_gold_review" and event["review_seconds"] == 5
    assert event["before_row_sha256"] == event["after_row_sha256"] == payload["expected_version"]


def test_v3_stale_version_and_interrupted_publish_preserve_canonical_gold(labeling, monkeypatch):
    config, path, database, _, _ = labeling
    store = GoldLabelStore(config, path)
    version = store.detail("uid-1")["version"]
    store.save("uid-1", {"expected_version": version, "relevance": 0})
    before = snapshot(path), snapshot(database)
    with pytest.raises(GoldUIError) as failure:
        store.save("uid-1", {"expected_version": version, "tags": six_tags(),
                             "review_seconds": 2})
    assert failure.value.status == 409 and (snapshot(path), snapshot(database)) == before
    current = store.detail("uid-1")["version"]

    def interrupted(*args):
        raise OSError("Interrupted atomic publish")

    monkeypatch.setattr(gold_ui.os, "replace", interrupted)
    with pytest.raises(GoldUIError) as failure:
        store.save("uid-1", {"expected_version": current, "tags": six_tags(), "review_seconds": 3})
    assert failure.value.status == 500 and (snapshot(path), snapshot(database)) == before
    assert not list(path.parent.glob("*.tmp"))


def test_v3_source_drift_blocks_save_and_machine_fields_stay_hidden(labeling):
    config, path, database, cases, _ = labeling
    cases[1]["labels_v3"] = {"relevance": "machine-secret"}
    cases[1]["source"]["annotation"] = {"tier": "machine-secret"}
    cases[1]["tags"] = {"function_core": {"value": "analytics_reporting", "quote": "",
                                         "labels_v3": {"relevance": "machine-secret"}}}
    write_cases(path, cases)
    store = GoldLabelStore(config, path)
    detail = store.detail("uid-1")
    assert not detail["can_save"]
    assert "machine-secret" not in json.dumps(detail)
    assert "labels_v3" not in json.dumps(store.list_cases({}))
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE jobs SET description_clean='A changed source' "
                           "WHERE job_uid='uid-0'")
    before = snapshot(path), snapshot(database)
    current = store.detail("uid-0")
    assert not current["can_save"] and current["source_state"] == "drift"
    with pytest.raises(GoldUIError) as failure:
        store.save("uid-0", {"expected_version": current["version"], "relevance": 3})
    assert failure.value.status == 409 and (snapshot(path), snapshot(database)) == before


def test_all_source_connections_remain_query_only(labeling, monkeypatch):
    config, path, database, _, _ = labeling
    connect = sqlite3.connect
    opened, queries = [], []

    def traced(name, *args, **kwargs):
        opened.append((name, kwargs.get("uri")))
        connection = connect(name, *args, **kwargs)
        connection.set_trace_callback(queries.append)
        return connection

    monkeypatch.setattr(sqlite3, "connect", traced)
    before = snapshot(database)
    store = GoldLabelStore(config, path)
    store.save("uid-1", {"expected_version": store.detail("uid-1")["version"], "tags": six_tags()})
    assert opened and all("mode=ro" in name and uri for name, uri in opened)
    assert sum(query == "PRAGMA query_only=ON" for query in queries) == len(opened)
    assert snapshot(database) == before
