"""Audit 14 regressions: real gold export, documented purpose rules and S3 recall."""

from __future__ import annotations

import copy
import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from job_intake.annotation.frozen_eval import record_from_snapshot, source_snapshot
from job_intake.annotation.gold import _snapshot
from job_intake.annotation.text import TEXT_VERSION, source_hash, source_units
from job_intake.config.settings import AppConfig, LLMConfig, TelegramConfig
from job_intake.labeling.runner import export_records
from job_intake.labeling.schema import LABELS_SCHEMA
from job_intake.labeling.validate import unspecified_tags, validate_tags
from job_intake.models.job import JobRecord
from job_intake.purpose import LEGACY_PURPOSE_RUBRIC_VERSION, PURPOSE_RUBRIC_VERSION

ROOT = Path(__file__).parents[1]
ROLE = "analytics-leadership"


def vacancy(**changes):
    record = JobRecord(
        source="test", company="Example", title="Analytics Manager",
        original_url="https://example.test/job",
        description_clean="Responsibilities\nYou will lead analytics for decisions.",
        source_metadata={"description_complete": True, "remote_scope": "Brazil"},
    )
    return replace(record, **changes)


def store(path: Path, record: JobRecord) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE jobs (job_uid TEXT, source TEXT, company TEXT, title TEXT, "
            "original_url TEXT, description_raw TEXT, description_clean TEXT, "
            "source_metadata TEXT, annotation TEXT, updated_at TEXT, content_hash TEXT, "
            "status TEXT, role_label TEXT, role_classification TEXT)"
        )
        connection.execute(
            "INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("uid", record.source, record.company, record.title, record.original_url,
             record.description_raw, record.description_clean,
             json.dumps(record.source_metadata), json.dumps({"claims": ["saved"]}),
             "2026-10-20", "content", "open", ROLE, "{}"),
        )


def gold_row(record: JobRecord) -> dict:
    # Gold sampling keeps a whitelisted source view; the pin covers the full record.
    return {
        "schema": LABELS_SCHEMA, "job_uid": "uid", "source_hash": source_hash(record),
        "text_version": TEXT_VERSION, "source": _snapshot(record), "labeler": "human",
        "method": "blind", "rubric_version": "labels-rubric-2026-10-20",
        "first_labeled_at": "2026-10-20", "labeled_at": "2026-10-20", "role_label": ROLE,
        "relevance": 2, "gate_outcome": "unknown", "gate_reasons": [], "tags": {},
        "notes": "PRIVATE HUMAN NOTE",
    }


@pytest.fixture
def setup(tmp_path):
    record = vacancy()
    database = tmp_path / "jobs.db"
    store(database, record)
    gold = tmp_path / "gold.jsonl"
    gold.write_text(json.dumps(gold_row(record)) + "\n", encoding="utf-8")
    settings = AppConfig(
        database_url=f"sqlite:///{database}", log_level="INFO", sources=[],
        rules_path=tmp_path / "rules.yaml", search_profiles_path=tmp_path / "profiles.yaml",
        company_watchlist_path=tmp_path / "companies.yaml", export_dir=tmp_path,
        telegram=TelegramConfig(), llm=LLMConfig(),
    )
    return settings, record, database, gold


def test_reduced_gold_snapshot_resolves_from_read_only_saved_source(setup):
    settings, record, database, gold = setup
    assert source_hash(record_from_snapshot(gold_row(record)["source"])) != source_hash(record)
    before = database.read_bytes()
    [(uid, exported)] = export_records(settings, gold=gold, profile_ids=[ROLE])
    assert uid == "uid" and source_hash(exported) == source_hash(record)
    assert exported == record_from_snapshot(source_snapshot(record))
    assert exported.annotation == {}
    assert "PRIVATE HUMAN NOTE" not in json.dumps(source_snapshot(exported))
    assert database.read_bytes() == before


def test_reduced_gold_snapshot_refuses_changed_saved_facts(setup, tmp_path):
    settings, record, _, _ = setup
    changed = tmp_path / "changed.db"
    store(changed, replace(record, description_clean="Responsibilities\nYou will sell ads."))
    with pytest.raises(ValueError, match="differs from the saved source facts"):
        export_records(replace(settings, database_url=f"sqlite:///{changed}"),
                       gold=tmp_path / "gold.jsonl", profile_ids=[ROLE])


def test_reduced_gold_snapshot_without_saved_source_names_the_bundle_path(setup, tmp_path):
    settings, _, _, gold = setup
    missing = replace(settings, database_url=f"sqlite:///{tmp_path / 'absent.db'}")
    with pytest.raises(ValueError, match="no SQLite source.*freeze a bundle"):
        export_records(missing, gold=gold, profile_ids=[ROLE])
    other = tmp_path / "other.db"
    store(other, vacancy(original_url="https://example.test/other"))
    with sqlite3.connect(other) as connection:
        connection.execute("UPDATE jobs SET job_uid = 'other'")
    with pytest.raises(ValueError, match="no valid saved source"):
        export_records(replace(settings, database_url=f"sqlite:///{other}"),
                       gold=gold, profile_ids=[ROLE])


def test_purpose_rubric_documents_the_rule_active_in_each_mode():
    rubric = yaml.safe_load((ROOT / "config/purpose_rubric.yaml").read_text(encoding="utf-8"))
    labels = yaml.safe_load((ROOT / "config/labeling_rubric.yaml").read_text(encoding="utf-8"))
    assert rubric["version"] == PURPOSE_RUBRIC_VERSION and rubric["p2_mode"] == "labels"
    assert rubric["p2"] == labels["p2"]["rule"]
    legacy = rubric["legacy_p2"]
    assert legacy["version"] == LEGACY_PURPOSE_RUBRIC_VERSION
    assert legacy["mode"] == "legacy, shadow"
    assert legacy["rule"] == (
        "analytics/data work with similar tasks, even if level or hiring does not fit next work"
    )


def checked(sentence: str, tag: str, value: str, office_location: str = "") -> str:
    record = JobRecord(
        source="test", company="Example", title="Analytics Manager",
        original_url="https://example.test/proof",
        description_clean=f"Responsibilities\nYou will analyze data.\n\nRequirements\n{sentence}\n",
    )
    units = source_units(record)
    unit = next(unit for unit in units if sentence.rstrip(".") in unit["text"])
    raw = copy.deepcopy(unspecified_tags())
    raw["work_mode"]["office_location"] = office_location
    raw["domain_fit"]["domain"] = ""
    raw[tag].update(value=value, evidence=[unit["unit_id"]], basis="explicit")
    raw.update(reason="probe", role_label="other", relevance=1, confidence="medium")
    return validate_tags(raw, units, [ROLE])[0]["tags"][tag]["value"]


@pytest.mark.parametrize("sentence,value", [
    ("This position is not open to candidates in Brazil.", "restricted_elsewhere"),
    ("Candidates in Brazil are not eligible for this role.", "restricted_elsewhere"),
    ("Open to candidates worldwide except Brazil.", "restricted_elsewhere"),
    ("This role is open to candidates based in the US or Canada.", "restricted_elsewhere"),
    ("We can only hire in the US, UK and Germany.", "restricted_elsewhere"),
    ("Open to candidates in the US.", "restricted_elsewhere"),
    ("We hire remote employees across LATAM, including Brazil.", "open_to_brazil"),
    ("Open to candidates worldwide.", "open_to_brazil"),
    ("This role is fully remote and open to candidates worldwide.", "open_to_brazil"),
    ("You can work from anywhere in Latin America, including Brazil.", "open_to_brazil"),
    ("This role is open to candidates in the US and Brazil.", "open_to_brazil"),
])
def test_explicit_hiring_wording_is_kept(sentence, value):
    assert checked(sentence, "hiring_eligibility", value) == value


@pytest.mark.parametrize("sentence,value", [
    ("We hire talent for our US office.", "restricted_elsewhere"),
    ("We hire employees across the US.", "restricted_elsewhere"),
    ("Open to candidates with SQL experience.", "open_to_brazil"),
    ("Available to start immediately.", "restricted_elsewhere"),
    ("This role is open to all.", "open_to_brazil"),
    ("We are not hiring in Brazil at this time.", "restricted_elsewhere"),
    ("This role is open to candidates in the US and Brazil.", "restricted_elsewhere"),
    ("We hire remote employees across LATAM, including Brazil.", "restricted_elsewhere"),
    ("You can work from anywhere in the US.", "restricted_elsewhere"),
    ("You can work from anywhere in the US.", "open_to_brazil"),
    ("Open to candidates worldwide except Brazil.", "open_to_brazil"),
    ("This position is not open to candidates in Canada.", "restricted_elsewhere"),
    ("Candidates in Brazil are not eligible for this role.", "open_to_brazil"),
    ("Candidates in Europe are preferred.", "restricted_elsewhere"),
    ("You must overlap four hours with EST.", "restricted_elsewhere"),
])
def test_unproven_or_contrary_hiring_wording_abstains(sentence, value):
    assert checked(sentence, "hiring_eligibility", value) == "unspecified"


@pytest.mark.parametrize("sentence,value,location,kept", [
    ("This is a hybrid role with 3 days per week in our São Paulo office.",
     "office_sao_paulo", "São Paulo", True),
    ("Hybrid: you will work from our office in São Paulo three days a week.",
     "office_sao_paulo", "São Paulo", True),
    ("This is a hybrid role with 3 days per week in our London office.",
     "office_elsewhere", "London", True),
    ("Hybrid: London or São Paulo.", "office_sao_paulo", "São Paulo", False),
    ("Hybrid role in our Greater São Paulo office.", "office_sao_paulo", "São Paulo", False),
    ("Hybrid role in our São Paulo and London offices.", "office_sao_paulo", "São Paulo", False),
    ("Hybrid role in our São Paulo state office.", "office_sao_paulo", "São Paulo", False),
    ("Hybrid role, 2 days a week in our Barueri office.", "office_sao_paulo", "São Paulo", False),
    ("This is a hybrid role based in Campinas, SP.", "office_sao_paulo", "São Paulo", False),
    ("You may occasionally visit our Berlin office.", "office_elsewhere", "Berlin", False),
])
def test_office_city_is_extracted_from_the_phrase_not_its_tail(sentence, value, location, kept):
    expected = value if kept else "unspecified"
    assert checked(sentence, "work_mode", value, location) == expected
