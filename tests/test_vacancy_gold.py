from __future__ import annotations

import copy
import json
import sqlite3
from collections import Counter

import pytest

from job_intake.annotation.gold import (
    DEFAULT_PROFILE_IDS,
    DEFAULT_STRATA,
    TAG_VALUES,
    GoldSampleShortageError,
    difficult_features,
    gold_json_schema,
    parse_strata,
    sample_gold,
    validate_gold,
)
from job_intake.annotation.text import TEXT_VERSION, source_hash
from job_intake.config.settings import AppConfig, LLMConfig, TelegramConfig
from job_intake.models.job import JobRecord


def job(text="Responsibilities:\nYou will lead projects. You will manage stakeholder decisions."):
    return JobRecord(source="test", company="Example", title="Analytics Manager",
                     original_url="https://example.test/job", description_clean=text)


def label(record, **overrides):
    return {
        "job_uid": "uid", "source_hash": source_hash(record), "text_version": TEXT_VERSION,
        "labeled_at": "2026-10-13", "method": "blind", "tags": {},
        "relevance": dict.fromkeys(DEFAULT_PROFILE_IDS, 2), "gate_outcome": "eligible",
        "notes": "", **overrides,
    }


def test_gold_schema_has_exact_19_closed_tags_and_partial_labels():
    schema = gold_json_schema()
    assert len(TAG_VALUES) == 19
    assert not schema["additionalProperties"]
    tags = schema["properties"]["tags"]
    assert not tags["additionalProperties"]
    assert "required" not in tags
    for tag, values in TAG_VALUES.items():
        assert tags["properties"][tag]["properties"]["value"]["enum"] == list(values)
        assert not tags["properties"][tag]["additionalProperties"]
    assert None not in schema["properties"]["method"]["enum"]
    assert None in gold_json_schema(template=True)["properties"]["method"]["enum"]
    assert schema["properties"]["labeled_at"]["anyOf"] == [
        {"type": "string", "format": "date"}, {"type": "string", "format": "date-time"},
    ]
    assert gold_json_schema(template=True)["properties"]["labeled_at"]["anyOf"][-1] == {
        "type": "null",
    }


@pytest.mark.parametrize("labeled_at", [
    "2026-10-13", "2026-10-13T15:00:00Z", "2026-10-13T15:00:00-03:00",
    "2026-10-13t15:00:00.123z",
])
def test_gold_accepts_schema_date_and_timestamp_formats(labeled_at):
    record = job()
    assert validate_gold([("uid", record)], [label(record, labeled_at=labeled_at)])["valid"]


@pytest.mark.parametrize("labeled_at", [
    "20261013", "2026-W42-2", "2026-10-13junk", "2026-10-13 15:00:00",
    "2026-10-13T15:00:00", "2026-10-13T15:00:00+0000", "2026-02-30",
])
def test_gold_rejects_dates_outside_schema_iso_formats(labeled_at):
    record = job()
    report = validate_gold([("uid", record)], [label(record, labeled_at=labeled_at)])
    assert not report["valid"]
    assert report["errors"][0]["code"] == "invalid_labeled_at"


def test_gold_rebinds_quote_spanning_adjacent_units_without_model_claim_ids():
    record = job()
    row = label(record, tags={"project_management": {
        "value": "duty",
        "quote": "You will lead projects.\n You will manage stakeholder decisions.",
    }})
    before = copy.deepcopy(row)
    report = validate_gold([("uid", record)], [row])
    assert report["valid"]
    assert report["labeled_count"] == 1
    assert report["relevant_count"] == 1
    assert len(report["rows"][0]["tags"]["project_management"]["evidence_ids"]) == 2
    assert row == before


def test_gold_survives_parser_version_change_with_explicit_drift_warnings():
    record = job()
    row = label(record, source_hash="a" * 64, text_version="source-units-old", tags={
        "people_interaction": {"value": "duty", "quote": "manage stakeholder decisions"},
    })
    report = validate_gold([("uid", record)], [row])
    assert report["valid"]
    assert {entry["code"] for entry in report["warnings"]} == {
        "source_hash_changed", "text_version_changed",
    }
    assert report["rows"][0]["tags"]["people_interaction"]["evidence_ids"]


@pytest.mark.parametrize("text,evidence,code", [
    ("You will lead.\nYou will lead.", "You will lead.", "ambiguous_quote"),
    ("You will lead.", "you will lead.", "quote_not_found"),
    ("You will lead.", "You will lead!", "quote_not_found"),
    ("You will lead.", "You will manage.", "quote_not_found"),
])
def test_gold_rejects_ambiguous_missing_or_modified_quotes(text, evidence, code):
    record = job(text)
    report = validate_gold([("uid", record)], [label(record, tags={
        "project_management": {"value": "duty", "quote": evidence},
    })])
    assert not report["valid"]
    assert code in {entry["code"] for entry in report["errors"]}


def test_gold_does_not_bind_across_unrelated_source_fields():
    record = job()
    record.location_text = "LATAM"
    record.remote_text = "English"
    report = validate_gold([("uid", record)], [label(record, tags={
        "hiring_geography": {"value": "includes_brazil_region", "quote": "LATAM English"},
    })])
    assert report["errors"][0]["code"] == "quote_not_found"


@pytest.mark.parametrize("patch,code", [
    ({"tags": {"skill": {"value": "duty", "quote": "projects"}}}, "invalid_tags"),
    ({"tags": {"project_management": {"value": "confirmed", "quote": "projects"}}},
     "invalid_tag_value"),
    ({"tags": {"project_management": {
        "value": "duty", "quote": "projects", "claim_id": "model-claim",
    }}}, "invalid_tag_label"),
    ({"tags": {"vacancy_closed": {"value": True, "quote": ""}}}, "invalid_tag_value"),
    ({"tags": {"project_management": {"value": "duty", "quote": ""}}}, "missing_quote"),
    ({"relevance": dict.fromkeys(DEFAULT_PROFILE_IDS, True)}, "invalid_relevance"),
    ({"relevance": {"model_profile": 2}}, "invalid_relevance"),
    ({"method": "model"}, "invalid_method"),
    ({"method": []}, "invalid_method"),
    ({"gate_outcome": []}, "invalid_gate_outcome"),
    ({"labeled_at": "not-a-date"}, "invalid_labeled_at"),
    ({"method": None}, "missing_label_provenance"),
    ({"labeled_at": None}, "missing_label_provenance"),
    ({"source_hash": "not-a-digest"}, "invalid_source_hash"),
    ({"claim_id": "model-claim"}, "invalid_fields"),
    ({"source": {"annotation": {"score": 100}}}, "invalid_source_snapshot"),
    ({"source": {"source_metadata": {"decision": "pass"}}}, "invalid_source_snapshot"),
])
def test_gold_rejects_invalid_schemas_and_no_fabricated_provenance(patch, code):
    record = job()
    report = validate_gold([("uid", record)], [label(record, **patch)])
    assert not report["valid"]
    assert code in {entry["code"] for entry in report["errors"]}


def test_gold_unlabeled_template_and_partial_tags_are_valid_but_not_semantic_decisions():
    record = job()
    template = label(record, labeled_at=None, method=None,
                     relevance=dict.fromkeys(DEFAULT_PROFILE_IDS), gate_outcome=None)
    report = validate_gold([("uid", record)], [template])
    assert report["valid"]
    assert report["labeled_count"] == report["relevant_count"] == 0
    assert report["warnings"][0]["code"] == "unlabeled_case"
    partial = label(record, relevance=dict.fromkeys(DEFAULT_PROFILE_IDS), gate_outcome=None,
                    tags={"working_language": {"value": "unspecified", "quote": ""}})
    report = validate_gold([("uid", record)], [partial])
    assert report["valid"]
    assert report["labeled_count"] == 0
    assert report["rows"][0]["tags"]["working_language"]["evidence_ids"] == []


def test_gold_duplicates_missing_jobs_and_empty_file_fail():
    record = job()
    assert validate_gold([("uid", record)], [])["errors"][0]["code"] == "empty_gold"
    assert validate_gold([], [label(record)])["errors"][0]["code"] == "missing_source"
    report = validate_gold([("uid", record)], [label(record), label(record)])
    assert report["errors"][0]["code"] == "invalid_job_uid"


@pytest.fixture
def sample_database(tmp_path):
    path = tmp_path / "sample.db"
    rules = tmp_path / "rules.yaml"
    profiles = tmp_path / "profiles.yaml"
    rules.write_text("recall_first: true\n")
    profiles.write_text("streams:\n" + "".join(f"  - id: {uid}\n" for uid in DEFAULT_PROFILE_IDS))
    settings = AppConfig(
        database_url=f"sqlite:///{path}", log_level="INFO", sources=[],
        rules_path=rules, search_profiles_path=profiles,
        company_watchlist_path=tmp_path / "companies.yaml", export_dir=tmp_path,
        telegram=TelegramConfig(), llm=LLMConfig(),
    )
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE jobs (job_uid TEXT PRIMARY KEY, source TEXT, company TEXT, title TEXT, "
            "original_url TEXT, description_raw TEXT, description_clean TEXT, "
            "source_metadata TEXT, "
            "annotation TEXT, updated_at TEXT, archived_at TEXT, status TEXT, fit_score REAL)"
        )
        groups = [("suppressed", 50), ("full_review", 50), ("never_ai", 60),
                  ("local_reject", 30), ("difficult", 40)]
        for group, count in groups:
            for index in range(count):
                uid = f"{group}-{index:03d}"
                title = "Product Owner" if group == "suppressed" and index < 7 else "Specialist"
                annotation = {"private_pipeline_output": "hidden"}
                if group in {"suppressed", "full_review"}:
                    annotation["prefilter"] = {"routing": group}
                text = "U.S. work authorization is required." if group == "difficult" else (
                    "Responsibilities:\nYou will lead projects and work with partners."
                )
                connection.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                    uid, "test", "Example", title, "https://example.test/" + uid,
                    text, text, json.dumps({"working_language": "English", "score": 999,
                                           "annotation": "hidden", "description_complete": True}),
                    json.dumps(annotation), "2026-10-13", None,
                    "closed" if group == "local_reject" else "open", index,
                ))
    return path, settings


def test_sample_exact_strata_unique_and_blind_without_saved_decisions(sample_database):
    path, settings = sample_database
    before = path.read_bytes()
    cases, manifest = sample_gold(settings)
    assert path.read_bytes() == before
    assert manifest["database_access"] == "read_only"
    assert manifest["api_calls"] == 0
    assert manifest["selected_counts"] == DEFAULT_STRATA
    assert len(cases) == len({case["job_uid"] for case in cases}) == 160
    assert len(manifest["mandatory_suppressed_product_job_uids"]) == 7
    assert set(manifest["mandatory_suppressed_product_job_uids"]) <= {
        case["job_uid"] for case in cases
    }
    assert {uid for uid, selection in manifest["selection"].items()
            if selection["stratum"] == "full_review"} == {
        f"full_review-{index:03d}" for index in range(10, 50)
    }
    assert Counter(case["label_scope"] for case in cases) == {
        "blind_decisions": 30, "full_tags": 60, "constraints": 70,
    }
    assert manifest["candidate_difficulty_feature_counts"]["brazil_exclusion"] == 0
    assert "brazil_exclusion" in manifest["absent_difficulty_features"]
    assert manifest["uncovered_difficulty_features"] == []
    text = json.dumps(cases)
    for forbidden in ("private_pipeline_output", "hidden", '"score"', '"annotation"',
                      '"prefilter"', '"saved_fit_score"', '"stratum"'):
        assert forbidden not in text
    assert all(case["gate_outcome"] is None and case["tags"] == {} for case in cases)
    assert all(set(case["relevance"].values()) == {None} for case in cases)
    again, manifest_again = sample_gold(settings)
    assert again == cases
    assert manifest_again["selection"] == manifest["selection"]


def test_sample_shortage_is_explicit_and_never_fills_with_other_strata(sample_database):
    path, settings = sample_database
    with sqlite3.connect(path) as connection:
        connection.execute("DELETE FROM jobs WHERE job_uid LIKE 'difficult-%' "
                           "AND job_uid NOT LIKE 'difficult-00_'")
    before = path.read_bytes()
    with pytest.raises(GoldSampleShortageError, match="difficult: 10/30") as caught:
        sample_gold(settings)
    assert caught.value.manifest["candidate_counts"]["difficult"] == 17
    assert caught.value.manifest["selected_counts"]["difficult"] == 10
    assert caught.value.manifest["selected_size"] < 160
    assert path.read_bytes() == before


def test_sample_missing_database_never_creates_it(sample_database):
    _, settings = sample_database
    settings.database_url = settings.database_url.replace("sample.db", "missing.db")
    with pytest.raises(ValueError, match="will not create"):
        sample_gold(settings)


def test_saved_reject_fill_is_opt_in_and_preserves_source_rule_rejects(sample_database):
    path, settings = sample_database
    with sqlite3.connect(path) as connection:
        connection.execute("DELETE FROM jobs WHERE job_uid LIKE 'local_reject-%' "
                           "AND job_uid >= 'local_reject-018'")
        connection.execute("ALTER TABLE jobs ADD COLUMN filter_decision TEXT DEFAULT 'review'")
        connection.execute("UPDATE jobs SET filter_decision='reject' "
                           "WHERE job_uid LIKE 'local_reject-%'")
        # These have genuine saved AI provenance but no source-only blocking language.
        connection.execute("UPDATE jobs SET filter_decision='reject', annotation=? "
                           "WHERE job_uid IN ('never_ai-000','never_ai-001')",
                           (json.dumps({"ai_enabled": True}),))
    before = path.read_bytes()
    with pytest.raises(GoldSampleShortageError, match="local_reject: 18/20"):
        sample_gold(settings)
    cases, manifest = sample_gold(settings, include_saved_rejects=True)
    assert len(cases) == 160
    assert manifest["selected_counts"] == DEFAULT_STRATA
    assert manifest["local_reject_provenance"]["selected_source_rule"] == 18
    assert manifest["local_reject_provenance"]["selected_saved_only"] == 2
    assert all(manifest["selection"][f"local_reject-{index:03d}"]["selection_kind"] ==
               "source_rule_reject" for index in range(18))
    assert manifest["selection"]["never_ai-000"]["selection_kind"] == "saved_annotation_reject"
    assert any("saved-reject fill" in text for text in manifest["limitations"])
    assert path.read_bytes() == before


@pytest.mark.parametrize("strata,size", [
    ({"unknown": 160}, 160), ({"suppressed": -1, "never_ai": 161}, 160),
    ({"suppressed": True, "never_ai": 159}, 160), ("suppressed=40,broken", 160),
    ("suppressed=40,suppressed=120", 160), ({"suppressed": 40}, 160), (None, 0),
])
def test_invalid_strata_are_rejected(strata, size):
    with pytest.raises(ValueError):
        parse_strata(strata, size)


def test_custom_strata_and_size_are_supported():
    assert parse_strata(None) == DEFAULT_STRATA
    assert sum(parse_strata(None, 13).values()) == 13
    assert parse_strata("suppressed=2,full_review=2,never_ai=1", 5) == {
        "suppressed": 2, "full_review": 2, "never_ai": 1, "local_reject": 0, "difficult": 0,
    }


def test_difficulty_features_are_source_heuristics_and_do_not_create_labels():
    record = job("Will English be our working language? Remote or hybrid. Except Brazil.")
    record.description_raw = "<ul><li>No visa sponsorship.</li></ul>"
    features = difficult_features(record)
    assert {"language_question", "remote_or_hybrid", "brazil_exclusion", "negated_list"} <= {
        *features,
    }
    assert record.annotation == {}


def test_us_difficulty_heuristic_does_not_confuse_country_with_about_us():
    assert "us_visa" not in difficult_features(job("About us: work with us on projects."))
    assert "us_visa" in difficult_features(job("This job is US only."))
    assert "us_visa" in difficult_features(job("This job is U.S. only."))
