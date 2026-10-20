from __future__ import annotations

import hashlib
import json

import pytest

from job_intake.annotation.gold import _snapshot, validate_gold
from job_intake.annotation.text import TEXT_VERSION, source_hash
from job_intake.labeling.gold_v3 import (
    METHOD_MAP,
    gold_file_schema,
    migrate_gold_v2,
    migrate_tags,
    validate_gold_v3,
)
from job_intake.labeling.schema import LABELS_SCHEMA, validate_gold_row
from job_intake.models.job import JobRecord

RUBRIC_VERSION = "labels-rubric-2026-10-20"


def job(text="Responsibilities:\nYou will lead pricing experiments.\nOffice in São Paulo."):
    return JobRecord(source="test", company="Example", title="Analytics Manager",
                     original_url="https://example.test/job", description_clean=text)


def v2(uid="uid", record=None, **overrides):
    record = record or job()
    return {
        "schema": "vacancy-tags-v2", "job_uid": uid, "source_hash": source_hash(record),
        "text_version": TEXT_VERSION, "source": _snapshot(record), "label_scope": "full_tags",
        "labeled_at": "2026-10-17", "method": "blind", "tags": {},
        "role_label": "analytics-leadership", "relevance": 2, "gate_outcome": "unknown",
        "notes": "Это биотех - это интересно.", **overrides,
    }


def write(path, rows):
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
                    encoding="utf-8")
    return path


def read(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_decisions_and_provenance_move_one_to_one(tmp_path):
    record = job()
    rows = [
        v2("blind", record),
        v2("curated", record, method="assisted", label_scope="constraints", relevance=3,
           role_label=None, gate_outcome=None, labeled_at="2026-10-17T13:43:00-03:00"),
        v2("template", record, method=None, labeled_at=None, role_label=None, relevance=None,
           gate_outcome=None, notes=""),
    ]
    source = write(tmp_path / "v2.jsonl", rows)
    assert validate_gold([(row["job_uid"], record) for row in rows], rows)["valid"]
    before = source.read_bytes()
    manifest = migrate_gold_v2(source, tmp_path / "v3.jsonl", rubric_version=RUBRIC_VERSION)
    migrated = {row["job_uid"]: row for row in read(tmp_path / "v3.jsonl")}
    assert source.read_bytes() == before
    for old in rows:
        new = migrated[old["job_uid"]]
        assert new["schema"] == LABELS_SCHEMA and new["labeler"] == "human"
        for field in ("source_hash", "text_version", "source", "label_scope", "role_label",
                      "relevance", "gate_outcome", "notes"):
            assert new[field] == old[field]
        assert new["first_labeled_at"] == new["labeled_at"] == old["labeled_at"]
        assert new["method"] == METHOD_MAP.get(old["method"])
        assert new["gate_reasons"] == [] and new["tags"] == {}
        assert validate_gold_row(new, record=record) == []
    assert migrated["curated"]["method"] == "curated"
    assert migrated["blind"]["rubric_version"] == RUBRIC_VERSION
    assert migrated["template"]["rubric_version"] is None
    assert manifest["counts"]["decision_labeled"] == 1
    assert manifest["counts"]["relevance_only"] == 1
    assert manifest["counts"]["methods"] == {"blind": 1, "curated": 1, "assisted_llm": 0}


def test_manifest_records_input_and_output_sha256(tmp_path):
    source = write(tmp_path / "v2.jsonl", [v2()])
    out = tmp_path / "gold" / "v3.jsonl"
    manifest = migrate_gold_v2(source, out, rubric_version=RUBRIC_VERSION)
    saved = json.loads(out.with_suffix(".manifest.json").read_text(encoding="utf-8"))
    assert saved == manifest
    assert manifest["source"]["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert manifest["output"]["sha256"] == hashlib.sha256(out.read_bytes()).hexdigest()
    assert manifest["api_calls"] == 0 and manifest["database_access"] == "none"


@pytest.mark.parametrize("occupied", ["out", "manifest", "source"])
def test_migration_refuses_any_occupied_destination(tmp_path, occupied):
    source = write(tmp_path / "v2.jsonl", [v2()])
    out = tmp_path / "v3.jsonl"
    if occupied == "out":
        out.write_text("existing gold")
    elif occupied == "manifest":
        out.with_suffix(".manifest.json").write_text("{}")
    else:
        out = source
    before = {path: path.read_bytes() for path in tmp_path.iterdir()}
    with pytest.raises(ValueError):
        migrate_gold_v2(source, out, rubric_version=RUBRIC_VERSION)
    assert {path: path.read_bytes() for path in tmp_path.iterdir()} == before


@pytest.mark.parametrize("row", [
    {**v2(), "schema": "vacancy-tags-v1"}, {**v2(), "schema": LABELS_SCHEMA},
    v2(method="llm"),
])
def test_migration_refuses_other_schemas_and_unknown_provenance(tmp_path, row):
    source = write(tmp_path / "v2.jsonl", [row])
    with pytest.raises(ValueError):
        migrate_gold_v2(source, tmp_path / "v3.jsonl", rubric_version=RUBRIC_VERSION)
    assert not (tmp_path / "v3.jsonl").exists()


@pytest.mark.parametrize("geography,authorization,expected", [
    ("brazil_explicit", None, "open_to_brazil"),
    ("includes_brazil_region", None, "open_to_brazil"),
    ("excludes_brazil", None, "restricted_elsewhere"),
    ("restricted_elsewhere", None, "restricted_elsewhere"),
    ("unspecified", None, "unspecified"),
    (None, ("country_required", "United States"), "restricted_elsewhere"),
    (None, ("country_required", "Brazil"), "open_to_brazil"),
    ("brazil_explicit", ("country_required", "United States"), "restricted_elsewhere"),
    ("unspecified", ("not_required", None), "unspecified"),
    (None, ("not_required", None), None),
])
def test_hiring_tags_merge_into_hiring_eligibility(geography, authorization, expected):
    tags = {}
    if geography:
        tags["hiring_geography"] = {"value": geography, "quote": "geo"}
    if authorization:
        tags["work_authorization"] = {"value": authorization[0], "quote": "auth",
                                      **({"country": authorization[1]}
                                         if authorization[1] else {})}
    result, _ = migrate_tags(tags)
    assert result.get("hiring_eligibility", {}).get("value") == expected
    if expected == "restricted_elsewhere":
        assert result["hiring_eligibility"]["basis"] == "explicit"


def test_work_mode_and_language_map_by_table_and_report_dropped_values():
    result, dropped = migrate_tags({
        "work_mode": {"value": "hybrid", "quote": "Office in São Paulo.",
                      "office_location": "São Paulo, Brazil"},
        "working_language": {"value": "english_required", "quote": "English"},
        "seniority": {"value": "manager", "quote": "Manager"},
    })
    assert result["work_mode"] == {"value": "office_sao_paulo", "quote": "Office in São Paulo.",
                                   "office_location": "São Paulo, Brazil", "basis": "explicit"}
    assert result["working_language"] == {"value": "english", "quote": "English",
                                          "basis": "explicit"}
    assert dropped == [("seniority", "manager", "no v3 analogue")]
    result, dropped = migrate_tags({
        "work_mode": {"value": "onsite", "quote": "Office"},
        "working_language": {"value": "english_optional", "quote": "English is a plus"},
    })
    assert result == {}
    assert {tag for tag, _, _ in dropped} == {"work_mode", "working_language"}
    result, _ = migrate_tags({"work_mode": {"value": "remote_or_hybrid", "quote": "Remote"},
                              "working_language": {"value": "other_language_required",
                                                   "quote": "French"}})
    assert result["work_mode"]["value"] == "remote"
    assert result["working_language"]["value"] == "other_language_required"
    result, _ = migrate_tags({"work_mode": {"value": "onsite", "quote": "Office",
                                            "office_location": "Campinas"}})
    assert result["work_mode"]["value"] == "office_elsewhere"


def test_migrated_tags_and_dropped_tags_reach_the_manifest(tmp_path):
    record = job()
    tags = {"work_mode": {"value": "hybrid", "quote": "Office in São Paulo.",
                          "office_location": "São Paulo"},
            "project_management": {"value": "duty", "quote": "You will lead pricing experiments."}}
    source = write(tmp_path / "v2.jsonl", [v2(record=record, tags=tags)])
    manifest = migrate_gold_v2(source, tmp_path / "v3.jsonl", rubric_version=RUBRIC_VERSION)
    assert manifest["counts"]["migrated_tags"] == 1
    assert manifest["dropped_tags"] == [{"job_uid": "uid", "tag": "project_management",
                                         "value": "duty", "reason": "no v3 analogue"}]
    report = validate_gold_v3([("uid", record)], read(tmp_path / "v3.jsonl"))
    assert report["valid"]
    assert report["rows"][0]["tags"]["work_mode"]["evidence_ids"]


def v3(uid="uid", record=None, **overrides):
    record = record or job()
    return {
        "schema": LABELS_SCHEMA, "job_uid": uid, "source_hash": source_hash(record),
        "text_version": TEXT_VERSION, "labeler": "human", "method": "blind",
        "rubric_version": RUBRIC_VERSION, "first_labeled_at": "2026-10-20",
        "labeled_at": "2026-10-20", "role_label": "analytics-leadership", "relevance": 3,
        "gate_outcome": "eligible", "gate_reasons": [], "tags": {}, "notes": "", **overrides,
    }


def test_validate_gold_v3_report_matches_the_v2_shape():
    record = job()
    cases = [v3("a", record), v3("b", record, relevance=None), v3("c", record, relevance=1)]
    report = validate_gold_v3([(uid, record) for uid in "abc"], cases,
                              profile_ids=["analytics-leadership"])
    assert report["valid"] and report["schema"] == LABELS_SCHEMA
    assert report["labeled_count"] == 2 and report["relevant_count"] == 1
    assert report["counts"]["relevance_labeled"] == 2
    assert [warning["code"] for warning in report["warnings"]] == ["unlabeled_case"]
    assert set(report) >= {"rows", "errors", "warnings", "source_compatibility"}


def test_validate_gold_v3_errors_and_drift():
    record = job()
    changed = job("Responsibilities:\nSomething else.")
    report = validate_gold_v3([("a", record), ("drift", changed)], [
        v3("a", record), v3("a", record), v3("missing", record), v3("drift", record),
        v3("bad", record, relevance=7),
    ])
    codes = {(error["job_uid"], error["code"]) for error in report["errors"]}
    assert codes == {("a", "invalid_job_uid"), ("missing", "missing_source"),
                     ("bad", "missing_source")}
    assert ("drift", "source_hash_changed") in {
        (warning["job_uid"], warning["code"]) for warning in report["warnings"]}
    report = validate_gold_v3([("bad", record)], [v3("bad", record, relevance=7)])
    assert [error["code"] for error in report["errors"]] == ["invalid_relevance"]
    assert not report["valid"] and report["rows"] == []


def test_gold_file_schema_detects_one_supported_schema():
    assert gold_file_schema([v2(), v2("b")]) == "vacancy-tags-v2"
    assert gold_file_schema([{"job_uid": "legacy"}]) == "vacancy-tags-v2"
    assert gold_file_schema([v3()]) == LABELS_SCHEMA
    with pytest.raises(ValueError):
        gold_file_schema([v2(), v3()])
    with pytest.raises(ValueError):
        gold_file_schema([{**v2(), "schema": "vacancy-tags-v1"}])
