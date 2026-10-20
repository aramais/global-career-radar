"""V3 gold reaches the supported frozen build/save/load path, without DB writes."""

from __future__ import annotations

import copy
import json

import pytest
from test_frozen_evaluation import label, record
from test_frozen_evaluation import saved as saved

from job_intake.annotation.cohorts import digest, write_new
from job_intake.annotation.frozen_eval import (
    _has_full_human_tags,
    build_bundle,
    create_run,
    load_bundle,
    saved_predictions_for_bundle,
    validate_run,
)
from job_intake.annotation.gold import validate_gold
from job_intake.labeling.predictions import check_prediction_rows
from job_intake.labeling.schema import LABELS_SCHEMA, TAG_NAMES


def v3_label():
    value = label()
    value.update(
        schema=LABELS_SCHEMA, labeler="human", rubric_version="labels-test",
        first_labeled_at=value["labeled_at"], gate_reasons=[], tags={
            "hiring_eligibility": {"value": "unspecified", "quote": ""},
            "work_mode": {"value": "remote", "quote": ""},
            "working_language": {"value": "unspecified", "quote": ""},
            "function_core": {"value": "analytics_for_decisions",
                              "quote": "Lead pricing decisions with stakeholders."},
            "domain_fit": {"industry": "other", "requirement": "none", "quote": ""},
            "preferred_focus": {"values": [], "quote": ""},
        },
    )
    return value


def write_gold(path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def test_shared_gold_validator_dispatches_v3_without_rewriting_answers():
    value = v3_label()
    before = copy.deepcopy(value)
    validated = validate_gold([("uid", record())], [value])
    assert validated["valid"], validated["errors"]
    assert validated["schema"] == LABELS_SCHEMA
    assert value == before
    assert set(validated["rows"][0]["tags"]) == set(TAG_NAMES)


def test_v3_build_save_load_run_roundtrip_is_read_only(saved, tmp_path, monkeypatch):
    config, database, gold = saved
    write_gold(gold, [v3_label()])
    before = database.read_bytes(), gold.read_bytes()

    def forbidden(*args, **kwargs):
        pytest.fail("No provider calls during freeze/load/evaluation")

    monkeypatch.setattr("requests.sessions.Session.request", forbidden)
    frozen = build_bundle(config, gold=gold, versions={"baseline": "rules-v1", "labels": "v3"})
    assert frozen["rows"][0]["label"] == v3_label()
    assert frozen["rows"][0]["source_origin"] == "saved_database"
    assert frozen["rows"][0]["saved_prediction"]["labeler"] == "rules:tier-map-v1"
    output = tmp_path / "v3-bundle.json"
    write_new(output, frozen)
    loaded = load_bundle(output)
    assert loaded == frozen
    run = create_run(loaded, predictions=None, variant="baseline", version="rules-v1",
                     run_id="v3-baseline")
    validate_run(loaded, run)
    exported = saved_predictions_for_bundle(config, loaded)
    check_prediction_rows(exported)
    assert exported[0]["source_hash"] == frozen["rows"][0]["source_hash"]
    candidate = create_run(loaded, predictions=exported, variant="labels", version="v3",
                           run_id="v3-candidate")
    validate_run(loaded, candidate)
    assert (database.read_bytes(), gold.read_bytes()) == before


@pytest.mark.parametrize("mutation", ["unknown_field", "bool_relevance", "bad_tag", "bad_quote"])
def test_build_and_load_reject_structurally_or_semantically_invalid_v3_gold(
    saved, tmp_path, mutation,
):
    config, database, gold = saved
    valid = v3_label()
    write_gold(gold, [valid])
    frozen = build_bundle(config, gold=gold, versions={"baseline": "v1"})
    invalid = copy.deepcopy(valid)
    if mutation == "unknown_field":
        invalid["provider_dump"] = "private"
    elif mutation == "bool_relevance":
        invalid["relevance"] = True
    elif mutation == "bad_tag":
        invalid["tags"]["work_mode"]["value"] = "invented"
    else:
        invalid["tags"]["function_core"]["quote"] = "Absent source sentence"
    write_gold(gold, [invalid])
    before = database.read_bytes(), gold.read_bytes()
    with pytest.raises(ValueError, match="Invalid gold"):
        build_bundle(config, gold=gold, versions={"baseline": "v1"})
    frozen["rows"][0]["label"] = invalid
    frozen["bundle_id"] = digest({
        key: value for key, value in frozen.items() if key != "bundle_id"
    })
    output = tmp_path / "invalid-bundle.json"
    write_new(output, frozen)
    with pytest.raises(ValueError, match="Invalid v3 frozen labels"):
        load_bundle(output)
    assert (database.read_bytes(), gold.read_bytes()) == before


def test_v3_freeze_checks_current_enabled_role_membership(saved):
    config, _, gold = saved
    config.rules_path.write_text("{}\n")
    config.search_profiles_path.write_text("streams:\n- id: different-role\n  titles: []\n")
    write_gold(gold, [v3_label()])
    with pytest.raises(ValueError, match="Invalid gold"):
        build_bundle(config, gold=gold, versions={"baseline": "v1"})


def test_mixed_v2_v3_gold_is_rejected_instead_of_silently_using_one_validator(saved):
    config, _, gold = saved
    second = label("another-uid")
    write_gold(gold, [v3_label(), second])
    with pytest.raises(ValueError, match="Invalid gold"):
        build_bundle(config, gold=gold, versions={"baseline": "v1"}, allow_source_snapshot=True)


def test_full_tag_coverage_uses_the_declared_gold_schema():
    assert _has_full_human_tags(v3_label())
    partial = v3_label()
    partial["tags"].pop("preferred_focus")
    assert not _has_full_human_tags(partial)
    partial["tags"]["unknown_tag"] = {}
    assert len(partial["tags"]) == 6 and not _has_full_human_tags(partial)
    legacy = label()
    legacy["tags"] = {str(index): {} for index in range(19)}
    assert _has_full_human_tags(legacy)  # Preserve the historical v2 count contract.
