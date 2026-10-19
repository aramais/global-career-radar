from __future__ import annotations

import copy
import json
import sqlite3

import pytest
from test_frozen_evaluation import label, record
from test_frozen_evaluation import saved as saved_fixture

from job_intake.annotation.accuracy import run_accuracy
from job_intake.annotation.cohorts import write_new
from job_intake.annotation.frozen_eval import (
    build_bundle,
    load_bundle,
    saved_predictions_for_bundle,
)
from job_intake.annotation.gold import _snapshot, validate_gold
from job_intake.annotation.gold_ui import GoldLabelStore
from job_intake.annotation.gold_workbook import validate_current_cases
from job_intake.annotation.source_compat import (
    check_source_compatibility,
    read_source_history,
    record_snapshot,
)
from job_intake.annotation.text import source_hash


@pytest.fixture
def saved(tmp_path):
    return saved_fixture.__wrapped__(tmp_path)


def migrated_records(*, existing_observations=False):
    original = record()
    original.source_metadata = {
        "ats": "test",
        "description_complete": False,
        "working_language": "English",
        "listing_observed_at": "2026-10-01",
        "detail_error": "Timeout",
    }
    if existing_observations:
        original.source_metadata["collection_observations"] = {"old_collection": "prior"}
    current = copy.deepcopy(original)
    for field in ("listing_observed_at", "detail_error"):
        current.source_metadata.pop(field)
    current.source_metadata["collection_observations"] = {
        "listing_observed_at": "2026-10-01",
        "detail_error": "Timeout",
        "incoming_description_complete": False,
    }
    case = {**label(), "source_hash": source_hash(original), "source": _snapshot(original)}
    return original, current, case


def test_observation_move_requires_exact_hash_proof_and_preserves_original_pin():
    original, current, case = migrated_records()
    before = copy.deepcopy(case)
    assert source_hash(current) != case["source_hash"]
    proof = check_source_compatibility(case, current)
    assert proof["compatible"]
    assert proof["method"] == "hash_verified_reversed_observation_move"
    report = validate_gold([("uid", current)], [case])
    assert report["counts"]["labeled"] == 1
    assert report["source_compatibility"][0]["original_hash"] == source_hash(original)
    assert {row["code"] for row in report["warnings"]} == {"source_observations_compatible"}
    assert case == before and report["rows"][0]["source_hash"] == before["source_hash"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("description_clean", "Changed duties"),
        ("description_raw", "<p>Different markup</p>"),
        ("location_text", "United States only"),
        ("status", "closed"),
        ("source_job_id", "different"),
    ],
)
def test_fact_or_identity_change_cannot_reuse_labels(field, value):
    original, current, case = migrated_records()
    setattr(current, field, value)
    history = [{"source_snapshot": record_snapshot(original), "method": "hash_verified_job_event"}]
    assert not check_source_compatibility(case, current, history=history)["compatible"]


def test_significant_metadata_and_whitespace_are_not_normalized_away():
    original, current, case = migrated_records()
    history = [{"source_snapshot": record_snapshot(original), "method": "hash_verified_job_event"}]
    current.source_metadata["working_language"] = "French"
    assert not check_source_compatibility(case, current, history=history)["compatible"]
    current.source_metadata["working_language"] = "English"
    current.description_clean = current.description_clean.replace(" ", "  ")
    assert not check_source_compatibility(case, current, history=history)["compatible"]


def migrate_database(saved, *, with_history=True, existing_observations=True):
    config, database, gold = saved
    original, current, case = migrated_records(existing_observations=existing_observations)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE jobs SET source_metadata=?", (json.dumps(current.source_metadata),)
        )
        if with_history:
            connection.execute(
                "CREATE TABLE job_events (id INTEGER PRIMARY KEY,job_uid TEXT, "
                "event_type TEXT,payload TEXT)"
            )
            connection.execute(
                "INSERT INTO job_events VALUES (1,'uid','source_superseded',?)",
                (
                    json.dumps(
                        {
                            "source_hash": source_hash(original),
                            "source_snapshot": record_snapshot(original),
                        }
                    ),
                ),
            )
    gold.write_text(json.dumps(case) + "\n")
    return config, database, gold, case, original, current


def test_hash_verified_history_covers_preexisting_observation_map(saved, monkeypatch):
    config, database, gold, case, _, current = migrate_database(saved)
    before_db, before_gold = database.read_bytes(), gold.read_bytes()
    history = read_source_history(config.database_url, [case])
    assert check_source_compatibility(case, current, history=history["uid"])["compatible"]
    assert not check_source_compatibility(case, current)["compatible"]
    monkeypatch.setattr(
        "requests.sessions.Session.request",
        lambda *args, **kwargs: pytest.fail("No API in compatibility read"),
    )
    report = run_accuracy(
        config, gold=gold, prices=__import__("pathlib").Path("config/eval_prices.yaml")
    )
    assert report["evaluation"]["counts"]["source_drift_withheld"] == 0
    assert report["evaluation"]["counts"]["decision_labeled"] == 1
    assert report["gold"]["validation"]["source_compatibility"][0]["event_id"] == 1
    assert database.read_bytes() == before_db and gold.read_bytes() == before_gold


def test_ui_and_workbook_allow_compatible_source_without_repinning(saved, monkeypatch):
    config, _, gold, case, _, _ = migrate_database(saved)
    store = GoldLabelStore(config, gold)
    monkeypatch.setattr(
        store, "_roles", lambda: [{"value": "analytics-leadership", "label": "Analytics"}]
    )
    detail = store.detail("uid")
    assert detail["can_save"] and detail["source_state"] == "compatible_observations"
    assert detail["source_compatibility"]["original_hash"] == case["source_hash"]
    assert "source_snapshot" not in detail["source_compatibility"]
    before = gold.read_bytes()
    validate_current_cases(config, [case], [{"id": "analytics-leadership", "name": "Analytics"}])
    assert gold.read_bytes() == before


def test_bundle_preserves_legacy_label_pin_and_validates_current_source(saved, tmp_path):
    config, _, gold, case, _, current = migrate_database(saved)
    result = build_bundle(config, gold=gold, versions={"baseline": "before", "candidate": "after"})
    row = result["rows"][0]
    assert row["source_hash"] == source_hash(current)
    assert row["label"]["source_hash"] == case["source_hash"]
    assert row["source_hash"] != row["label"]["source_hash"]
    assert row["source_compatibility"]["method"] == "hash_verified_job_event"
    target = tmp_path / "bundle.json"
    write_new(target, result)
    assert load_bundle(target) == result


def test_current_saved_outputs_bind_to_same_frozen_facts_with_provenance(saved):
    config, database, gold = saved
    old = build_bundle(config, gold=gold, versions={"baseline": "before", "candidate": "after"})
    config, _, _, _, _, _ = migrate_database(saved)
    # This fixture originally had no metadata, so its old frozen facts differ:
    # a significant newly-added working_language/ATS fact must be rejected.
    with pytest.raises(ValueError, match="facts changed"):
        saved_predictions_for_bundle(config, old)
    config, _, gold, _, _, current = migrate_database_compatible(saved)
    frozen = build_bundle(config, gold=gold, versions={"baseline": "before", "candidate": "after"})
    with sqlite3.connect(database) as connection:
        meta = copy.deepcopy(current.source_metadata)
        meta["collection_observations"]["listing_observed_at"] = "2026-10-20"
        connection.execute("UPDATE jobs SET source_metadata=?", (json.dumps(meta),))
    outputs = saved_predictions_for_bundle(config, frozen)
    assert outputs[0]["source_hash"] == frozen["rows"][0]["source_hash"]
    assert outputs[0]["source_binding"]["method"] == "hash_verified_frozen_bundle_facts"


def migrate_database_compatible(saved):
    config, database, gold = saved
    # Existing verified history remains valid; restore the original current observation.
    original, current, case = migrated_records(existing_observations=True)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE jobs SET source_metadata=?", (json.dumps(current.source_metadata),)
        )
    gold.write_text(json.dumps(case) + "\n")
    return config, database, gold, case, original, current


def test_unproved_compatibility_remains_drift_when_history_is_missing(saved):
    config, _, gold, _, _, _ = migrate_database(saved, with_history=False)
    report = run_accuracy(
        config, gold=gold, prices=__import__("pathlib").Path("config/eval_prices.yaml")
    )
    assert report["evaluation"]["counts"]["source_drift_withheld"] == 1
    with pytest.raises(ValueError, match="Source drift"):
        build_bundle(config, gold=gold, versions={"baseline": "before"})
