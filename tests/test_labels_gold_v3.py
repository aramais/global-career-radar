from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path

import pytest

from job_intake.annotation.benchmark import parse_jsonl, read_jsonl
from job_intake.annotation.cohorts import SCHEMA, select_cases
from job_intake.annotation.gold import _snapshot, validate_gold
from job_intake.annotation.text import TEXT_VERSION, source_hash
from job_intake.labeling.gold_v3 import (
    METHOD_MAP,
    TAG_MAP_VERSION,
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
    assert manifest["tag_map_version"] == TAG_MAP_VERSION == "v2-to-v3-tags-v4"
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


def test_existing_v2_cohorts_select_migrated_curated_rows():
    record = job()
    member = {"job_uid": "uid", "source_hash": source_hash(record), "duplicate_group": "g",
              "label_method": "assisted", "original_labeled_at": "2026-10-17"}
    manifest = {"schema": SCHEMA, "cohort_id": "calibration", "dataset_role": "calibration",
                "rubric_version": "vacancy-tags-v2", "created_at": "2026-10-19T12:00:00+00:00",
                "members": [member]}
    curated = v3(record=record, method="curated", labeled_at="2026-10-17")
    assert select_cases([curated], manifest) == [curated]
    with pytest.raises(ValueError, match="provenance changed"):
        select_cases([{**curated, "method": "blind"}], manifest)
    legacy = v2(record=record, method="assisted")
    assert select_cases([legacy], manifest) == [legacy]
    with pytest.raises(ValueError, match="provenance changed"):
        select_cases([{**legacy, "method": "curated"}], manifest)


@pytest.mark.parametrize("country", [
    None, "", " ", "unknown", "unspecified", "TBD", "not available", "N/D", "UNKNOWN-123",
    "LATAM", "Americas", "Brazil or United States", "country not yet decided",
])
def test_authorization_without_an_established_country_never_invents_a_gate(country):
    authorization = {"value": "country_required", "quote": "Work authorization is required."}
    if country is not None:
        authorization["country"] = country
    result, dropped = migrate_tags({"work_authorization": authorization})
    assert "hiring_eligibility" not in result
    assert dropped == [("work_authorization", "country_required",
                        "authorization country is missing or unsupported")]
    result, dropped = migrate_tags({
        "hiring_geography": {"value": "brazil_explicit", "quote": "Hiring in Brazil."},
        "work_authorization": authorization,
    })
    assert "hiring_eligibility" not in result
    assert len(dropped) == 2 and dropped[1][0] == "hiring_geography"


def test_unknown_authorization_country_is_reported_as_dropped_in_manifest(tmp_path):
    record = job("Work authorization is required.")
    row = v2(record=record, tags={"work_authorization": {
        "value": "country_required", "quote": "Work authorization is required.",
    }})
    assert validate_gold([("uid", record)], [row])["valid"]
    source = write(tmp_path / "v2.jsonl", [row])
    out = tmp_path / "v3.jsonl"
    manifest = migrate_gold_v2(source, out, rubric_version=RUBRIC_VERSION)
    migrated = read(out)[0]
    assert migrated["tags"] == {} and migrated["gate_outcome"] == row["gate_outcome"]
    assert manifest["counts"]["migrated_tags"] == 0
    assert manifest["dropped_tags"] == [{
        "job_uid": "uid", "tag": "work_authorization", "value": "country_required",
        "reason": "authorization country is missing or unsupported",
    }]


@pytest.mark.parametrize("location", [
    "São Paulo", "Sao Paulo", "São Paulo, Brazil", "São Paulo - SP",
    "São Paulo/SP/Brasil", "São Paulo (SP), Brazil", "São Paulo, São Paulo, Brazil",
    "City of São Paulo", "Cidade de São Paulo", "São Paulo city", "São Paulo capital",
])
def test_office_location_migration_keeps_explicit_sao_paulo_city(location):
    result, dropped = migrate_tags({"work_mode": {
        "value": "onsite", "office_location": location, "quote": "Office.",
    }})
    assert result["work_mode"]["value"] == "office_sao_paulo"
    assert result["work_mode"]["office_location"] == location
    assert dropped == []


@pytest.mark.parametrize("location", [
    "Campinas, São Paulo state", "Campinas, São Paulo", "Santos, São Paulo",
    "São Caetano do Sul, São Paulo", "Barueri - São Paulo", "Campinas", "London, UK",
    "Campinas, SP, Brazil", "London - UK", "London",
])
def test_office_location_migration_does_not_treat_other_cities_as_sao_paulo(location):
    record = job(f"Office in {location}.")
    row = v2(record=record, tags={"work_mode": {
        "value": "onsite", "office_location": location, "quote": f"Office in {location}.",
    }})
    assert validate_gold([("uid", record)], [row])["valid"]
    result, dropped = migrate_tags(row["tags"])
    assert result["work_mode"]["value"] == "office_elsewhere"
    assert dropped == []


@pytest.mark.parametrize("location", [
    "São Paulo state", "State of São Paulo", "Estado de São Paulo", "São Paulo (state)",
    "Greater São Paulo", "Região Metropolitana de São Paulo", "São Paulo / Campinas", "Brazil",
    "Brazil, São Paulo", "BR / Sao Paulo", "SP state", "Estado de SP", "State of SP",
    "LATAM", "Americas", "Latin America", "South America", "global", "TBD", "N/D",
    "Location TBD", "TBC", "Office location to be confirmed", "Flexible location",
    "Unknown city name", "Oxford", "Campinas, location to be confirmed",
    "London / São Paulo", "Santos / São Paulo", "London or São Paulo",
    "London; São Paulo", "London | São Paulo", "London and São Paulo",
    "London, UK / São Paulo", "Campinas, São Paulo or São Paulo city",
    "London, Brazil", "Campinas, UK",
])
def test_ambiguous_office_city_is_not_guessed(location):
    result, dropped = migrate_tags({"work_mode": {
        "value": "hybrid", "office_location": location, "quote": "Office.",
    }})
    assert result == {}
    assert dropped == [("work_mode", "hybrid", "office city is not established by the location")]


@pytest.mark.parametrize("mode", ["onsite", "hybrid"])
@pytest.mark.parametrize("location", [
    "Location TBD", "TBC", "Office location to be confirmed", "Unknown city name",
    "London / São Paulo", "São Paulo / London", "London or São Paulo",
    "London; São Paulo", "Santos / São Paulo", "Campinas, location to be confirmed",
])
def test_migration_drops_unestablished_office_in_valid_gold_and_manifest(tmp_path, mode, location):
    quote = f"Office location: {location}."
    record = job(quote)
    row = v2(record=record, tags={"work_mode": {
        "value": mode, "office_location": location, "quote": quote,
    }})
    assert validate_gold([("uid", record)], [row])["valid"]
    source = write(tmp_path / "source.jsonl", [row])
    original = source.read_bytes()
    out = tmp_path / "migrated.jsonl"

    manifest = migrate_gold_v2(source, out, rubric_version=RUBRIC_VERSION)

    migrated = read(out)[0]
    assert validate_gold_row(migrated, record=record) == []
    assert migrated["tags"] == {}
    assert migrated["gate_outcome"] == row["gate_outcome"]
    assert source.read_bytes() == original
    assert manifest["counts"]["migrated_tags"] == 0
    assert manifest["counts"]["dropped_tags"] == 1
    assert manifest["dropped_tags"] == [{
        "job_uid": "uid", "tag": "work_mode", "value": mode,
        "reason": "office city is not established by the location",
    }]
    assert json.loads(out.with_suffix(".manifest.json").read_text())["dropped_tags"] \
        == manifest["dropped_tags"]


@pytest.mark.parametrize("country,expected", [
    ("Brasil", "open_to_brazil"), ("BR", "open_to_brazil"), ("BRA", "open_to_brazil"),
    ("U.S.", "restricted_elsewhere"), ("USA", "restricted_elsewhere"),
    ("United States of America", "restricted_elsewhere"), ("Canadá", "restricted_elsewhere"),
    ("CA", "restricted_elsewhere"), ("United Kingdom", "restricted_elsewhere"),
    ("GBR", "restricted_elsewhere"), ("Germany", "restricted_elsewhere"),
    ("DE", "restricted_elsewhere"), ("Portugal", "restricted_elsewhere"),
    ("European Union", "restricted_elsewhere"), ("North America", "restricted_elsewhere"),
])
def test_authorization_migration_uses_explicit_supported_geography_aliases(country, expected):
    result, dropped = migrate_tags({"work_authorization": {
        "value": "country_required", "country": country, "quote": "Authorization required.",
    }})
    assert result["hiring_eligibility"]["value"] == expected
    assert dropped == []


def test_migration_parses_exactly_the_single_captured_source_read(tmp_path, monkeypatch):
    source = write(tmp_path / "v2.jsonl", [v2(relevance=2)])
    raw = source.read_bytes()
    changed = json.dumps(v2(relevance=3), ensure_ascii=False) + "\n"
    read_bytes = Path.read_bytes
    read_text = Path.read_text
    reads = []

    def capture_then_change(path):
        value = read_bytes(path)
        if path == source:
            reads.append(path)
            path.write_text(changed, encoding="utf-8")
        return value

    def refuse_second_source_read(path, *args, **kwargs):
        assert path != source, "migration must parse the bytes already captured for SHA-256"
        return read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_bytes", capture_then_change)
    monkeypatch.setattr(Path, "read_text", refuse_second_source_read)
    out = tmp_path / "v3.jsonl"
    manifest = migrate_gold_v2(source, out, rubric_version=RUBRIC_VERSION)
    assert reads == [source]
    assert read(out)[0]["relevance"] == 2
    assert manifest["source"]["sha256"] == hashlib.sha256(raw).hexdigest()
    assert manifest["source"]["sha256"] != hashlib.sha256(read_bytes(source)).hexdigest()


@pytest.mark.parametrize("content,message", [
    ('\n{"a": 1, "a": 2}\n', "Invalid JSONL at line 2"),
    ('{"a":1}\n[]\n', "JSONL line 2 must be an object"),
    ('{"a":\n', "Invalid JSONL at line 1"),
])
def test_captured_jsonl_parser_keeps_file_parser_errors(tmp_path, content, message):
    path = tmp_path / "rows.jsonl"
    path.write_text(content, encoding="utf-8")
    for parse in (lambda: parse_jsonl(content), lambda: read_jsonl(path)):
        with pytest.raises(ValueError) as exc:
            parse()
        assert str(exc.value) == message


def test_captured_jsonl_parser_keeps_file_parser_success_and_numeric_semantics(tmp_path):
    content = '\n{"a":1}\n \n{"b":2}\n'
    path = tmp_path / "rows.jsonl"
    path.write_text(content, encoding="utf-8")
    assert read_jsonl(path) == parse_jsonl(content) == [{"a": 1}, {"b": 2}]
    content = '{"value":NaN}\n'
    path.write_text(content, encoding="utf-8")
    assert math.isnan(read_jsonl(path)[0]["value"])
    assert math.isnan(parse_jsonl(content)[0]["value"])


def test_migration_rolls_back_both_outputs_after_manifest_publish_failure(tmp_path, monkeypatch):
    source = write(tmp_path / "v2.jsonl", [v2()])
    before = source.read_bytes()
    out = tmp_path / "v3.jsonl"
    manifest_path = out.with_suffix(".manifest.json")
    link = os.link

    def fail_manifest(temporary, path):
        if path == manifest_path:
            raise OSError("injected manifest failure")
        return link(temporary, path)

    with monkeypatch.context() as patch:
        patch.setattr(os, "link", fail_manifest)
        with pytest.raises(OSError, match="injected manifest failure"):
            migrate_gold_v2(source, out, rubric_version=RUBRIC_VERSION)
    assert source.read_bytes() == before
    assert list(tmp_path.iterdir()) == [source]
    # A handled failure leaves the same new paths usable without deleting anything.
    migrate_gold_v2(source, out, rubric_version=RUBRIC_VERSION)
    assert out.exists() and manifest_path.exists()


def test_migration_stages_both_files_before_publishing_either(tmp_path, monkeypatch):
    source = write(tmp_path / "v2.jsonl", [v2()])
    out = tmp_path / "v3.jsonl"
    fsync = os.fsync
    calls = 0

    def fail_second_stage(fd):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected second staging failure")
        return fsync(fd)

    monkeypatch.setattr(os, "fsync", fail_second_stage)
    with pytest.raises(OSError, match="injected second staging failure"):
        migrate_gold_v2(source, out, rubric_version=RUBRIC_VERSION)
    assert list(tmp_path.iterdir()) == [source]


@pytest.mark.parametrize("foreign_path", ["out", "manifest"])
def test_migration_preserves_concurrent_foreign_output_on_publish_failure(
    tmp_path, monkeypatch, foreign_path,
):
    source = write(tmp_path / "v2.jsonl", [v2()])
    out = tmp_path / "v3.jsonl"
    manifest_path = out.with_suffix(".manifest.json")
    foreign = out if foreign_path == "out" else manifest_path
    link = os.link

    def replace_before_manifest(temporary, path):
        if path == manifest_path:
            foreign.unlink(missing_ok=True)
            foreign.write_text("foreign output", encoding="utf-8")
            raise OSError("injected manifest failure after a competing write")
        return link(temporary, path)

    monkeypatch.setattr(os, "link", replace_before_manifest)
    with pytest.raises(OSError, match="competing write"):
        migrate_gold_v2(source, out, rubric_version=RUBRIC_VERSION)
    assert foreign.read_text(encoding="utf-8") == "foreign output"
    other = manifest_path if foreign_path == "out" else out
    assert not other.exists()
    assert set(tmp_path.iterdir()) == {source, foreign}


def test_migration_does_not_remove_foreign_out_created_after_preflight(tmp_path, monkeypatch):
    source = write(tmp_path / "v2.jsonl", [v2()])
    out = tmp_path / "v3.jsonl"
    link = os.link

    def competing_first_link(temporary, path):
        if path == out:
            path.write_text("foreign gold", encoding="utf-8")
        return link(temporary, path)

    monkeypatch.setattr(os, "link", competing_first_link)
    with pytest.raises(FileExistsError):
        migrate_gold_v2(source, out, rubric_version=RUBRIC_VERSION)
    assert out.read_text(encoding="utf-8") == "foreign gold"
    assert not out.with_suffix(".manifest.json").exists()
    assert set(tmp_path.iterdir()) == {source, out}


@pytest.mark.parametrize("raw,error,message", [
    (b"", ValueError, "Cannot migrate an empty gold file"),
    (b" \n\n", ValueError, "Cannot migrate an empty gold file"),
    (b"\xff", UnicodeDecodeError, "utf-8"),
])
def test_migration_invalid_capture_never_publishes_outputs(tmp_path, raw, error, message):
    source = tmp_path / "source.jsonl"
    source.write_bytes(raw)
    with pytest.raises(error, match=message):
        migrate_gold_v2(source, tmp_path / "v3.jsonl", rubric_version=RUBRIC_VERSION)
    assert list(tmp_path.iterdir()) == [source]
    assert source.read_bytes() == raw


def test_migration_missing_source_does_not_create_output_directories(tmp_path):
    with pytest.raises(FileNotFoundError):
        migrate_gold_v2(tmp_path / "missing.jsonl", tmp_path / "new" / "v3.jsonl",
                        rubric_version=RUBRIC_VERSION)
    assert list(tmp_path.iterdir()) == []


def test_migration_source_cannot_be_the_manifest_companion(tmp_path):
    out = tmp_path / "v3.jsonl"
    source = write(out.with_suffix(".manifest.json"), [v2()])
    before = source.read_bytes()
    with pytest.raises(ValueError, match="must not replace the v2 source"):
        migrate_gold_v2(source, out, rubric_version=RUBRIC_VERSION)
    assert source.read_bytes() == before and not out.exists()
