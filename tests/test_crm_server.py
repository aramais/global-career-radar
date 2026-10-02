import csv
import io
import json
from datetime import UTC, datetime
from http.client import HTTPConnection
from threading import Thread

import pytest

from job_intake.config.settings import AppConfig, LLMConfig, TelegramConfig
from job_intake.crm.repository import CRMRepository
from job_intake.crm.schemas import CRMConflictError
from job_intake.crm.server import CRMHTTPServer, CRMService, RequestError, csv_export
from job_intake.models.job import EvaluatedJob, FilterDecision, JobEvaluation, JobRecord, JobTier
from job_intake.storage.repository import JobRepository


@pytest.fixture
def service(tmp_path):
    rules = tmp_path / "rules.yaml"
    profiles = tmp_path / "profiles.yaml"
    rules.write_text("{}")
    profiles.write_text("streams:\n  - id: product\n    name: Product\n")
    config = AppConfig(
        database_url=f"sqlite:///{tmp_path / 'db/jobs.db'}",
        log_level="INFO",
        rules_path=rules,
        search_profiles_path=profiles,
        company_watchlist_path=tmp_path / "companies.yaml",
        export_dir=tmp_path,
        sources=[],
        telegram=TelegramConfig(),
        llm=LLMConfig(),
    )
    return CRMService(config)


def test_personal_workflow_persists_history_contact_referral_and_next_action(service):
    app = service.mutate(
        "POST",
        "/api/applications",
        {
            "company": "Acme",
            "title": "Product Manager",
            "stage": "saved",
            "profile_id": "product",
            "url": "https://example.com/job/1",
        },
    )
    contact = service.mutate("POST", "/api/contacts", {"name": "Referral Person"})
    app = service.mutate(
        "POST",
        f"/api/applications/{app['id']}/contacts",
        {
            "contact_id": str(contact["id"]),
            "relationship": "referral",
            "expected_version": app["version"],
        },
    )
    assert app["channel"] == "unknown"
    app = service.mutate(
        "PATCH",
        f"/api/applications/{app['id']}",
        {
            "channel": "referral",
            "referral_state": "referred",
            "cv_version": "PM-v2",
            "next_action": "Prepare for the team",
            "next_action_due": "2000-01-01",
            "expected_version": app["version"],
        },
    )
    app = service.mutate(
        "POST",
        f"/api/applications/{app['id']}/stage",
        {
            "stage": "applied",
            "occurred_at": "2026-10-02",
            "expected_version": app["version"],
        },
    )
    app = service.mutate(
        "POST",
        f"/api/applications/{app['id']}/stage",
        {
            "stage": "team_interview",
            "occurred_at": "2026-10-12",
            "expected_version": app["version"],
        },
    )
    state = service.state({"since": "2026-10-01", "until": "2026-10-31"})
    assert state["metrics"]["applied"] == state["metrics"]["team_interview"] == 1
    assert len(state["actions"]) == 1
    assert [event["stage"] for event in app["history"]] == ["saved", "applied", "team_interview"]
    assert app["cv_version"] == "PM-v2"
    with pytest.raises(CRMConflictError):
        service.mutate(
            "PATCH",
            f"/api/applications/{app['id']}",
            {
                "notes": "Stale write",
                "expected_version": 1,
            },
        )
    app = service.mutate(
        "DELETE",
        f"/api/applications/{app['id']}/contacts",
        {
            "contact_id": contact["id"],
            "relationship": "referral",
            "expected_version": app["version"],
        },
    )
    assert not app["contacts"]
    assert len(service.state({})["contacts"]) == 1


def test_period_uses_sao_paulo_day_and_separates_unknown_historical_dates(service):
    with service.database.session() as session:
        repo = CRMRepository(session)
        repo.create_application(company="Unknown", title="Historical", stage="applied")
        repo.create_application(
            company="Inside",
            title="Late Oct",
            stage="team_interview",
            stage_occurred_at=datetime(2026, 11, 1, 2, 59, tzinfo=UTC),
        )
        repo.create_application(
            company="Outside",
            title="Nov",
            stage="team_interview",
            stage_occurred_at=datetime(2026, 11, 1, 3, 0, tzinfo=UTC),
        )
        session.commit()
    state = service.state({"since": "2026-10-01", "until": "2026-10-31"})
    assert state["metrics"]["applied"] == 0
    assert state["metrics"]["team_interview"] == 1
    assert state["metrics"]["unknown_by_stage"]["applied"] == 1
    assert state["metrics"]["by_channel"]["unknown"]["team_from_applied_rate"] is None


def test_saved_job_idempotence_does_not_assign_disabled_profile(service):
    with service.database.session() as session:
        item = EvaluatedJob(
            record=JobRecord(
                source="test",
                source_job_id="1",
                company="Acme",
                title="Product Manager",
                original_url="https://example.com/1",
            ),
            evaluation=JobEvaluation(decision=FilterDecision.PASS, tier=JobTier.A, fit_score=20),
            profile_id="disabled",
            profile_version="old",
        )
        result = JobRepository(session).upsert_evaluations([item])
        session.commit()
    app = service.mutate("POST", "/api/applications/from-job", {"job_uid": result.job_uid})
    assert app["profile_id"] is None
    repeat = service.mutate("POST", "/api/applications/from-job", {"job_uid": result.job_uid})
    assert app["id"] == repeat["id"]
    with pytest.raises(RequestError):
        service.mutate("POST", "/api/applications/from-job", {"job_uid": [1]})


@pytest.fixture
def running_server(service):
    server = CRMHTTPServer(service, port=0)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def _request(server, method, path, payload=None, **headers):
    connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    content = json.dumps(payload) if payload is not None else None
    headers.setdefault("Content-Type", "application/json")
    connection.request(method, path, body=content, headers=headers)
    response = connection.getresponse()
    body = response.read()
    result = json.loads(body) if body else None
    status = response.status
    connection.close()
    return status, result


def test_http_enforces_host_origin_token_and_optimistic_updates(running_server):
    server = running_server
    assert _request(server, "GET", "/api/state", Host="example.com")[0] == 403
    status, state = _request(server, "GET", "/api/state")
    assert status == 200
    headers = {"X-CSRF-Token": state["csrf_token"], "Origin": server.origin}
    data = {"company": "Acme", "title": "Product Manager", "stage": "saved"}
    assert _request(server, "POST", "/api/applications", data)[0] == 403
    bad_origin = {**headers, "Origin": "https://evil.example"}
    assert _request(server, "POST", "/api/applications", data, **bad_origin)[0] == 403
    status, app = _request(server, "POST", "/api/applications", data, **headers)
    assert status == 200
    route = f"/api/applications/{app['id']}"
    assert (
        _request(
            server,
            "PATCH",
            route,
            {"notes": "Saved", "expected_version": app["version"]},
            **headers,
        )[0]
        == 200
    )
    assert (
        _request(
            server,
            "PATCH",
            route,
            {"notes": "Stale", "expected_version": app["version"]},
            **headers,
        )[0]
        == 409
    )
    assert _request(server, "GET", "/api/state")[1]["applications"][0]["notes"] == "Saved"


def test_non_finite_json_is_rejected_without_changing_optional_date(running_server):
    server = running_server
    connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    connection.request(
        "POST",
        "/api/applications",
        body='{"company":"Acme","title":"Role","applied_at":NaN}',
        headers={
            "Content-Type": "application/json",
            "Origin": server.origin,
            "X-CSRF-Token": server.service.csrf_token,
        },
    )
    response = connection.getresponse()
    assert response.status == 400
    response.read()
    connection.close()
    assert server.service.state({})["applications"] == []


def test_csv_export_preserves_history_and_neutralizes_formulas():
    text = csv_export(
        [
            {
                "company": "  =HYPERLINK(1)",
                "notes": "+cmd",
                "history": [{"stage": "applied", "occurred_at": None}],
            }
        ]
    ).decode("utf-8-sig")
    row = next(csv.DictReader(io.StringIO(text)))
    assert row["company"].startswith("'") and row["notes"].startswith("'")
    assert json.loads(row["history"])[0]["occurred_at"] is None
