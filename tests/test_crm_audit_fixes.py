"""Regressions for CRM integrity, bounded queries and lazy detail read models."""

from __future__ import annotations

import io
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from email.message import Message
from types import SimpleNamespace

import pytest
from sqlalchemy import event, insert, select, update
from test_crm_server import service as service

from job_intake.crm.models import ApplicationORM, CompanyORM, StageEventORM
from job_intake.crm.repository import CRMRepository
from job_intake.crm.schemas import CRMConflictError
from job_intake.crm.server import CRMRequestHandler
from job_intake.storage.database import Database
from job_intake.storage.models import JobORM, JobProfileEvaluationORM


def seed_jobs(service, count=1, *, description="Full description", annotations=None):
    now = datetime.now(UTC)
    jobs, evaluations = [], []
    for index in range(count):
        uid = f"audit-job-{index:04}"
        jobs.append({
            "job_uid": uid, "role_label": "product", "fingerprint": uid, "content_hash": uid,
            "source": "fixture",
            "role_classification": {"label": "product", "content_hash": uid,
                                    "version": "taxonomy-v1", "source_hash": uid},
            "company": "Fixture", "title": "Unique old target" if index == 0 else f"Job {index}",
            "original_url": f"https://example.invalid/{uid}", "description_raw": description,
            "description_clean": description, "annotation": annotations or {}, "status": "open",
            "filter_decision": "pass", "first_seen_at": now - timedelta(days=count - index),
            "last_seen_at": now - timedelta(days=count - index),
        })
        evaluations.append({
            "job_uid": uid, "profile_id": "product", "profile_name": "Product",
            "profile_version": service.streams[0].version, "content_hash": uid,
            "classification_version": "taxonomy-v1", "classification_source_hash": uid,
            "decision": "pass" if index == 0 else "reject",
            "deterministic_score": 20 if index == 0 else 0,
            "fit_score": 20 if index == 0 else 0, "tier": "A" if index == 0 else "C",
            "bucket": "Bucket A" if index == 0 else "Bucket C", "fit_reason": "Synthetic evidence",
        })
    with service.database.session() as session:
        session.execute(insert(JobORM), jobs)
        session.execute(insert(JobProfileEvaluationORM), evaluations)
        session.commit()
    return [job["job_uid"] for job in jobs]


def test_job_search_matches_unicode_case_and_displayed_raw_fallback(service):
    uid = seed_jobs(service)[0]
    with service.database.session() as session:
        session.execute(update(JobORM).where(JobORM.job_uid == uid).values(
            company="КОМПАНИЯ São Paulo", description_clean="",
            description_raw="Raw-only description needle",
        ))
        session.commit()
    for query in ("компания", "SÃO PAULO", "description needle"):
        assert [job["job_uid"] for job in service.jobs({"query": query})["jobs"]] == [uid]


def test_current_stage_correction_updates_existing_event_and_period_metric(service):
    with service.database.session() as session:
        app = CRMRepository(session).create_application(
            company="Fixture", title="Imported", stage="team_interview",
        )
        app_id, event_id = app.id, app.history[0].id
        session.commit()
    payload = {"stage": "team_interview", "occurred_at": "2026-10-02",
               "note": "Confirmed interview", "expected_version": 1}
    updated = service.mutate("POST", f"/api/applications/{app_id}/stage", payload)
    assert updated["version"] == 2
    assert len(updated["history"]) == 1
    assert updated["history"][0]["id"] == event_id
    assert updated["history"][0]["from_stage"] is None
    assert updated["history"][0]["occurred_at"] == "2026-10-02T03:00:00+00:00"
    assert updated["history"][0]["note"] == "Confirmed interview"
    repeated = service.mutate("POST", f"/api/applications/{app_id}/stage",
                              {**payload, "expected_version": 2})
    assert repeated == updated
    assert service.metrics({"since": "2026-10-01", "until": "2026-10-31"})[
        "metrics"]["team_interview"] == 1


def test_current_stage_missing_fields_preserve_known_date_and_note(service):
    app = service.mutate("POST", "/api/applications", {"company": "Fixture", "title": "Role"})
    route = f"/api/applications/{app['id']}/stage"
    app = service.mutate("POST", route, {"stage": "applied", "occurred_at": "2026-10-02",
                                        "note": "Original", "expected_version": app["version"]})
    repeated = service.mutate("POST", route,
                              {"stage": "applied", "expected_version": app["version"]})
    assert repeated == app
    edited = service.mutate("POST", route, {"stage": "applied", "note": "Corrected",
                                           "expected_version": app["version"]})
    assert edited["history"][-1]["occurred_at"] == app["history"][-1]["occurred_at"]
    assert edited["applied_at"] == app["applied_at"]
    assert edited["history"][-1]["note"] == "Corrected"


def test_current_applied_stage_date_correction_and_explicit_clear(service):
    app = service.mutate("POST", "/api/applications", {"company": "Fixture", "title": "Role"})
    route = f"/api/applications/{app['id']}/stage"
    for day in ("2026-10-02", "2026-10-04", None):
        app = service.mutate("POST", route, {"stage": "applied", "occurred_at": day,
                                            "note": "" if day is None else "Applied",
                                            "expected_version": app["version"]})
        assert app["applied_at"] == app["history"][-1]["occurred_at"]
        assert app["applied_at"] == (f"{day}T03:00:00+00:00" if day else None)
    assert len(app["history"]) == 2
    assert app["history"][-1]["note"] == ""


def test_company_stale_form_returns_http_conflict_without_losing_notes(service):
    company = service.mutate("POST", "/api/companies", {"name": "Fixture", "notes": "Original"})
    path = f"/api/companies/{company['id']}"
    saved = service.mutate("PATCH", path, {"notes": "First writer", "expected_version": 1})
    assert saved["version"] == 2
    payload = json.dumps({"notes": "Stale writer", "expected_version": 1}).encode()
    headers = Message()
    for key, value in {"Host": "127.0.0.1:9999", "Origin": "http://127.0.0.1:9999",
                       "X-CSRF-Token": service.csrf_token, "Content-Type": "application/json",
                       "Content-Length": str(len(payload))}.items():
        headers[key] = value
    handler = object.__new__(CRMRequestHandler)
    handler.path, handler.headers, handler.rfile = path, headers, io.BytesIO(payload)
    handler.server = SimpleNamespace(service=service, server_port=9999,
                                     origin="http://127.0.0.1:9999")
    responses = []
    handler._json = lambda status, body: responses.append((status, body))
    handler._dispatch("PATCH")
    assert responses[0][0] == 409
    assert service.state({"include_jobs": "false"})["companies"][0]["notes"] == "First writer"


def test_company_mapper_rejects_two_concurrent_session_writes(service):
    company = service.mutate("POST", "/api/companies", {"name": "Fixture"})
    with service.database.session() as first, service.database.session() as second:
        first_repo, second_repo = CRMRepository(first), CRMRepository(second)
        second_snapshot = second.get(CompanyORM, company["id"])
        assert second_snapshot.version == 1
        first_repo.update_company(company["id"], expected_version=1, notes="First")
        first.commit()
        with pytest.raises(CRMConflictError):
            second_repo.update_company(company["id"], expected_version=1, notes="Lost")
        second.rollback()
    assert service.state({})["companies"][0]["notes"] == "First"


def test_legacy_company_version_migration_is_additive_and_idempotent(tmp_path):
    path = tmp_path / "legacy-company.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute("""CREATE TABLE crm_companies (
            id INTEGER PRIMARY KEY, name TEXT NOT NULL, normalized_name TEXT NOT NULL UNIQUE,
            url TEXT, notes TEXT NOT NULL, source_key TEXT, source_metadata JSON NOT NULL,
            created_at DATETIME NOT NULL, updated_at DATETIME NOT NULL)""")
        connection.execute("""INSERT INTO crm_companies VALUES
            (1,'Fixture','fixture',NULL,'Keep notes','source','{"evidence":"keep"}',
             '2026-10-01 12:00:00','2026-10-01 12:00:00')""")
    database = Database(f"sqlite:///{path}")
    database.create_schema()
    database.create_schema()
    with database.session() as session:
        company = session.get(CompanyORM, 1)
        assert company.version == 1
        assert company.notes == "Keep notes"
        assert company.source_metadata == {"evidence": "keep"}
        CRMRepository(session).update_company(1, expected_version=1, notes="Updated")
        session.commit()
    database.create_schema()
    with database.session() as session:
        assert session.get(CompanyORM, 1).version == 2
        assert session.get(CompanyORM, 1).notes == "Updated"
    database.engine.dispose()


def test_search_and_score_filter_reach_old_job_beyond_1000_and_report_total(service):
    ids = seed_jobs(service, 1001)
    assert service.jobs({})["total"] == 1001
    found = service.jobs({"query": "Unique old target", "tier": "A", "score_min": "20",
                          "score_max": "20", "limit": "1"})
    assert found["total"] == 1
    assert [job["job_uid"] for job in found["jobs"]] == ids[:1]
    assert not found["has_more"]
    first = service.jobs({"sort": "score-asc", "limit": "500"})
    second = service.jobs({"sort": "score-asc", "limit": "500", "offset": "500"})
    last = service.jobs({"sort": "score-asc", "limit": "500", "offset": "1000"})
    combined = first["jobs"] + second["jobs"] + last["jobs"]
    assert len({job["job_uid"] for job in combined}) == 1001
    assert combined[-1]["job_uid"] == ids[0]
    assert first["has_more"] and second["has_more"] and not last["has_more"]


def test_job_filter_and_sort_use_current_profile_version_and_source_hash(service):
    ids = seed_jobs(service, 4)
    with service.database.session() as session:
        assessments = {row.job_uid: row for row in session.scalars(select(JobProfileEvaluationORM))}
        assessments[ids[1]].tier, assessments[ids[1]].fit_score = "B", 25
        assessments[ids[2]].profile_version = "stale version"
        assessments[ids[3]].content_hash = "stale content"
        session.commit()
    assert [job["job_uid"] for job in service.jobs({"sort": "tier-asc"})["jobs"]] == ids
    assert [job["job_uid"] for job in service.jobs({"sort": "score-desc"})["jobs"]] == [
        ids[1], ids[0], ids[2], ids[3],
    ]
    assert [job["job_uid"] for job in service.jobs({"sort": "tier-desc"})["jobs"]] == [
        ids[1], ids[0], ids[2], ids[3],
    ]
    assert service.jobs({"profile_id": "product", "tier": "unrated"})["total"] == 2
    assert service.jobs({"tier": "unrated"})["total"] == 2
    assert service.jobs({"query": "%"})["total"] == 0
    with pytest.raises(ValueError):
        service.jobs({"limit": "501"})
    with pytest.raises(ValueError):
        service.jobs({"score_min": "nan"})


def test_compact_state_omits_heavy_details_and_detail_endpoints_preserve_evidence(service):
    annotation = {"version": "grounded-v1", "claims": [{"source_snippet": "Evidence"}],
                  "units": [{"text": "private duplicated source"}], "cache_key": "private"}
    uid = seed_jobs(service, description="D" * 10000, annotations=annotation)[0]
    app = service.mutate("POST", "/api/applications/from-job", {"job_uid": uid})
    state = service.state({})
    assert "description" not in state["jobs"][0] and "annotation" not in state["jobs"][0]
    assert "history" not in state["applications"][0]
    assert "source_metadata" not in state["applications"][0]
    detail = service.job(uid)
    assert detail["description"] == "D" * 10000
    assert detail["annotation"] == {"version": "grounded-v1", "claims": annotation["claims"]}
    assert detail["profiles"][0]["fit_reason"] == "Synthetic evidence"
    assert service.application(app["id"])["history"] == app["history"]
    assert set(service.metrics({})) == {"metrics", "actions", "as_of", "period"}
    assert len(json.dumps(state)) < 10000


def test_crm_query_count_is_bounded_with_many_jobs_and_application_relations(service):
    ids = seed_jobs(service, 1000, description="D" * 10000)
    with service.database.session() as session:
        session.execute(insert(ApplicationORM), [{"id": index + 1, "company": "Fixture",
                        "title": "Role", "job_uid": uid} for index, uid in enumerate(ids)])
        session.execute(insert(StageEventORM), [{"application_id": index + 1, "to_stage": "saved"}
                                               for index in range(len(ids))])
        session.commit()
    queries = []

    def count_select(_connection, _cursor, statement, _params, _context, _executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            queries.append(statement)

    event.listen(service.database.engine, "before_cursor_execute", count_select)
    try:
        state = service.state({"limit": "500"})
    finally:
        event.remove(service.database.engine, "before_cursor_execute", count_select)
    assert len(state["jobs"]) == 500 and state["job_page"]["total"] == 1000
    assert len(state["applications"]) == 1000
    assert len(queries) <= 20
    assert len(json.dumps(state)) < 1000000
