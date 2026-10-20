"""P1 v3 manual labeling through CRM HTTP, using temporary datasets only."""

from __future__ import annotations

import copy
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import update
from test_crm_server import _request
from test_crm_server import running_server as running_server
from test_crm_server import service as service
from test_gold_web_server import headers, seed_gold

from job_intake.annotation import gold_ui
from job_intake.annotation.eval_runner import read_saved_records, sqlite_path
from job_intake.annotation.gold import _snapshot
from job_intake.annotation.gold_ui import GoldLabelStore, GoldUIError
from job_intake.annotation.text import source_hash
from job_intake.labeling.gold_v3 import migrate_gold_v2
from job_intake.labeling.schema import LABELS_SCHEMA, check_gold_row, validate_gold_row
from job_intake.storage.models import JobORM


def seed_v3(service):
    old, uids = seed_gold(service)
    rubric = Path(__file__).parents[1] / "config/labeling_rubric.yaml"
    (service.config.rules_path.parent / "labeling_rubric.yaml").write_bytes(rubric.read_bytes())
    path = old.with_name("vacancy-labels-v3.jsonl")
    migrate_gold_v2(old, path, rubric_version="labels-rubric-2026-10-20")
    return old, path, uids


def signature(path):
    return hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns


def test_v3_http_six_tags_partial_save_journal_and_blindness(running_server):
    server = running_server
    legacy, path, uids = seed_v3(server.service)
    database = sqlite_path(server.service.config.database_url)
    before = {p: signature(p) for p in (legacy, database)}
    untouched = next(line for line in path.read_bytes().splitlines(keepends=True)
                     if json.loads(line)["job_uid"] == uids[0])
    status, page = _request(server, "GET", "/api/gold?status=unlabeled")
    assert status == 200
    assert page["schema"] == LABELS_SCHEMA
    assert len(page["label_options"]["tags"]) == 6
    route = f"/api/gold/{uids[1]}"
    status, detail = _request(server, "GET", route)
    assert status == 200 and detail["can_save"]
    assert detail["schema"] == LABELS_SCHEMA
    assert detail["labels"]["tags"] == {} and detail["labels"]["gate_reasons"] == []
    assert not {"annotation", "labels_v3", "tier", "score", "fit_score",
                "role_classification"} & detail.keys()
    assert not {"annotation", "labels_v3", "tier", "score", "fit_score",
                "role_classification"} & detail["source"].keys()
    tags = {
        "hiring_eligibility": {"value": "unspecified", "quote": ""},
        "work_mode": {"value": "unspecified", "quote": ""},
        "working_language": {"value": "unspecified", "quote": ""},
        "function_core": {"value": "analytics_for_decisions",
                          "quote": "Lead analytics projects."},
        "domain_fit": {"industry": "other", "requirement": "none", "quote": ""},
        "preferred_focus": {"values": [], "quote": ""},
    }
    status, saved = _request(server, "PATCH", route, {
        "expected_version": detail["version"], "tags": tags, "gate_reasons": [],
        "review_seconds": 12.5,
    }, **headers(server))
    assert status == 200, saved
    assert not saved["complete"] and saved["labels"]["relevance"] is None
    assert saved["labels"]["tags"] == tags
    assert _request(server, "GET", route)[1]["labels"] == saved["labels"]
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    updated = next(row for row in rows if row["job_uid"] == uids[1])
    assert not validate_gold_row(updated, profile_ids=["product"])
    assert updated["labeler"] == "human" and updated["method"] == "blind"
    assert updated["first_labeled_at"] and updated["rubric_version"]
    assert "review_seconds" not in updated
    journals = list((path.parent / "backups").glob("*.audit.json"))
    assert len(journals) == 1
    assert json.loads(journals[0].read_text())["review_seconds"] == 12.5
    assert next(line for line in path.read_bytes().splitlines(keepends=True)
                if json.loads(line)["job_uid"] == uids[0]) == untouched
    assert all(signature(p) == value for p, value in before.items())


@pytest.mark.parametrize("fields", [
    {"tags": {"work_mode": {"value": "office_elsewhere", "quote": ""}}},
    {"tags": {"working_language": {"value": "portuguese_only",
                                     "quote": "Not in the source."}}},
    {"tags": {"preferred_focus": {"values": ["invented"], "quote": ""}}},
    {"tags": {"domain_fit": {"industry": "other", "requirement": "required",
                               "required_domain": "other", "quote": ""}}},
    {"gate_reasons": ["unknown_reason"]},
    {"gate_outcome": "eligible", "gate_reasons": ["personal_constraint"]},
    {"review_seconds": True},
    {"review_seconds": 10**400},
    {"review_seconds": float("nan")},
    {"labeler": "llm:test"},
])
def test_v3_http_rejects_invalid_edits_without_writing(running_server, fields):
    server = running_server
    legacy, path, uids = seed_v3(server.service)
    route = f"/api/gold/{uids[1]}"
    detail = _request(server, "GET", route)[1]
    database = sqlite_path(server.service.config.database_url)
    before = {p: signature(p) for p in (legacy, path, database)}
    payload = {"expected_version": detail["version"], **copy.deepcopy(fields)}
    assert _request(server, "PATCH", route, payload, **headers(server))[0] == 400
    assert all(signature(p) == value for p, value in before.items())
    assert not (path.parent / "backups").exists()


def test_v3_http_stale_edit_preserves_first_save_and_legacy_source(running_server):
    server = running_server
    legacy, path, uids = seed_v3(server.service)
    route = f"/api/gold/{uids[0]}"
    detail = _request(server, "GET", route)[1]
    payload = {"expected_version": detail["version"], "relevance": 0,
               "gate_outcome": "ineligible", "gate_reasons": ["personal_constraint"]}
    status, saved = _request(server, "PATCH", route, payload, **headers(server))
    assert status == 200, saved
    assert saved["labels"]["relevance"] == 0
    after = signature(path)
    assert _request(server, "PATCH", route, {**payload, "relevance": 3},
                    **headers(server))[0] == 409
    assert signature(path) == after
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    curated = next(row for row in rows if row["job_uid"] == uids[0])
    assert curated["method"] == "curated"
    assert curated["first_labeled_at"] == "2026-10-17"
    assert next(json.loads(line) for line in legacy.read_text().splitlines()
                if json.loads(line)["job_uid"] == uids[0])["relevance"] == 3


def test_explicit_v2_http_dataset_still_opens_when_v3_exists(running_server):
    server = running_server
    legacy, path, uids = seed_v3(server.service)
    server.service.gold_store = GoldLabelStore(server.service.config, path=legacy)
    before_v3 = signature(path)
    status, page = _request(server, "GET", "/api/gold")
    assert status == 200 and page["schema"] == "vacancy-tags-v2"
    route = f"/api/gold/{uids[1]}"
    status, detail = _request(server, "GET", route)
    assert status == 200 and detail["can_save"]
    assert set(detail["labels"]) == {"role_label", "relevance", "gate_outcome", "notes"}
    status, saved = _request(server, "PATCH", route, {
        "expected_version": detail["version"], "relevance": 0,
    }, **headers(server))
    assert status == 200 and saved["labels"]["relevance"] == 0
    assert signature(path) == before_v3


def test_default_symlink_http_and_explicit_store_share_version_and_target(running_server):
    server = running_server
    legacy, path, uids = seed_v3(server.service)
    target = path.with_name("canonical.jsonl")
    path.rename(target)
    path.symlink_to(target.name)
    explicit = GoldLabelStore(server.service.config, path=path)
    stale = explicit.detail(uids[1])
    before = {p: signature(p) for p in (legacy, sqlite_path(server.service.config.database_url))}
    route = f"/api/gold/{uids[1]}"
    status, detail = _request(server, "GET", route)
    assert status == 200 and detail["version"] == stale["version"]
    status, saved = _request(server, "PATCH", route, {
        "expected_version": detail["version"], "relevance": 2,
    }, **headers(server))
    assert status == 200, saved
    with pytest.raises(GoldUIError) as error:
        explicit.save(uids[1], {"expected_version": stale["version"],
                                "gate_outcome": "unknown"})
    assert error.value.status == 409
    assert path.is_symlink() and path.read_bytes() == target.read_bytes()
    assert server.service.gold_store.path == explicit.path == target.resolve()
    assert explicit.detail(uids[1])["labels"]["relevance"] == 2
    assert explicit.detail(uids[1])["version"] == saved["version"]
    assert all(signature(p) == value for p, value in before.items())


def test_http_save_preserves_utc_chronology_across_offset_date_rollover(
    running_server, monkeypatch,
):
    server = running_server
    legacy, path, uids = seed_v3(server.service)
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    first = "2026-10-21T00:30:00+14:00"
    case = next(row for row in rows if row["job_uid"] == uids[0])
    case.update(first_labeled_at=first, labeled_at="2026-10-21T00:31:00+14:00")
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    current = datetime(2026, 10, 20, 16, 0, 0, 123456, tzinfo=UTC)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return current.astimezone(tz) if tz is not None else current.replace(tzinfo=None)

    monkeypatch.setattr(gold_ui, "datetime", Clock)
    before = {p: signature(p) for p in (legacy, sqlite_path(server.service.config.database_url))}
    route = f"/api/gold/{uids[0]}"
    status, detail = _request(server, "GET", route)
    assert status == 200 and detail["can_save"]
    status, saved = _request(server, "PATCH", route, {
        "expected_version": detail["version"], "relevance": 0,
    }, **headers(server))
    assert status == 200, saved
    updated = next(json.loads(line) for line in path.read_text().splitlines()
                   if json.loads(line)["job_uid"] == uids[0])
    assert updated["first_labeled_at"] == first and updated["method"] == "curated"
    assert datetime.fromisoformat(updated["labeled_at"]).astimezone(UTC) == current
    assert updated["labeled_at"].endswith("-03:00")
    assert not validate_gold_row(updated, profile_ids=["product"])
    assert all(signature(p) == value for p, value in before.items())


def test_http_metadata_only_constraints_rebind_and_save_without_machine_fields(running_server):
    server = running_server
    legacy, path, uids = seed_v3(server.service)
    uid = uids[1]
    metadata = {"applicant_location_requirements": ["Applicants must live in Europe."],
                "working_language": "German required.",
                "labels_v3": {"relevance": 3, "reason": "machine-only"}}
    with server.service.database.session() as session:
        session.execute(update(JobORM).where(JobORM.job_uid == uid).values(
            source_metadata=metadata,
        ))
        session.commit()
    _, records, problems = read_saved_records(server.service.config.database_url, job_uids=[uid])
    assert not problems
    record = dict(records)[uid]
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    next(row for row in rows if row["job_uid"] == uid).update(
        source=_snapshot(record), source_hash=source_hash(record),
    )
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    before = {p: signature(p) for p in (legacy, sqlite_path(server.service.config.database_url))}
    route = f"/api/gold/{uid}"
    status, detail = _request(server, "GET", route)
    assert status == 200 and detail["can_save"]
    assert detail["source"]["source_metadata"] == {
        "applicant_location_requirements": ["Applicants must live in Europe."],
        "working_language": "German required.",
    }
    status, saved = _request(server, "PATCH", route, {
        "expected_version": detail["version"],
        "tags": {
            "hiring_eligibility": {"value": "restricted_elsewhere", "basis": "explicit",
                                   "quote": "Applicants must live in Europe."},
            "working_language": {"value": "other_language_required", "basis": "explicit",
                                 "quote": "German required."},
        },
    }, **headers(server))
    assert status == 200, saved
    updated = next(json.loads(line) for line in path.read_text().splitlines()
                   if json.loads(line)["job_uid"] == uid)
    errors, bindings = check_gold_row(updated, record=record, profile_ids=["product"])
    assert not errors
    assert bindings == {
        "hiring_eligibility": ["source_metadata.applicant_location_requirements:000001"],
        "working_language": ["source_metadata.working_language:000001"],
    }
    assert all(signature(p) == value for p, value in before.items())
