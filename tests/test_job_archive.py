from __future__ import annotations

import csv
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from http.client import HTTPConnection
from threading import Thread

import pytest
from sqlalchemy import select

from job_intake.config.settings import AppConfig, LLMConfig, TelegramConfig
from job_intake.crm.repository import CRMRepository, serialize_application
from job_intake.crm.server import CRMHTTPServer, CRMService, RequestError
from job_intake.models.job import EvaluatedJob, FilterDecision, JobEvaluation, JobRecord, JobTier
from job_intake.storage.models import AlertOutboxORM, JobEventORM, JobORM
from job_intake.storage.repository import JobArchiveConflictError, JobRepository


@pytest.fixture
def service(tmp_path):
    rules = tmp_path / "rules.yaml"
    profiles = tmp_path / "profiles.yaml"
    rules.write_text("{}")
    profiles.write_text("streams:\n  - id: product\n    name: Product\n")
    return CRMService(AppConfig(
        database_url=f"sqlite:///{tmp_path / 'jobs.db'}", log_level="WARNING",
        rules_path=rules, search_profiles_path=profiles,
        company_watchlist_path=tmp_path / "companies.yaml", export_dir=tmp_path,
        sources=[], telegram=TelegramConfig(), llm=LLMConfig(),
    ))


def evaluated(title="Product Manager", tier=JobTier.A):
    return EvaluatedJob(
        record=JobRecord(
            source="test", company="Example", title=title,
            original_url="https://example.com/" + title.replace(" ", "-"),
            description_clean="Remote product work from Brazil.",
            annotation={"test": "preserve evidence"},
        ),
        evaluation=JobEvaluation(
            decision=FilterDecision.PASS, fit_score=20, tier=tier, bridge_role=True,
        ), profile_id="product", profile_version="test-version",
    )


def seed(service, *, queue_alert=False, title="Product Manager", tier=JobTier.A):
    with service.database.session() as session:
        result = JobRepository(session).upsert_evaluations(
            [evaluated(title, tier)], queue_alert=queue_alert,
        )
        session.commit()
        return result.job_uid


def toggle(service, uid, archived, version):
    return service.mutate("PATCH", f"/api/jobs/{uid}/archive", {
        "archived": archived, "expected_version": version,
    })


def batch(service, jobs, archived=True):
    return service.mutate("PATCH", "/api/jobs/archive", {
        "archived": archived,
        "jobs": [{"job_uid": uid, "expected_version": version} for uid, version in jobs],
    })


def test_bulk_archive_and_restore_only_selected_jobs(service):
    selected = [seed(service, title=f"Selected {i}", queue_alert=True) for i in range(2)]
    remaining = seed(service, title="Keep active")
    with service.database.session() as session:
        app = CRMRepository(session).create_from_job(selected[0], profile_id="product")
        app.notes = "Keep my notes"
        session.commit()
        before = serialize_application(app)
    result = batch(service, [(uid, 1) for uid in selected])
    assert result["count"] == 2
    assert [row["job_uid"] for row in result["jobs"]] == selected
    assert all(row["archived"] and row["archive_version"] == 2 for row in result["jobs"])
    assert [row["job_uid"] for row in service.state({})["jobs"]] == [remaining]
    assert service.state({})["applications"] == [before]
    with service.database.session() as session:
        assert all(row.status == "cancelled" for row in session.scalars(select(AlertOutboxORM)))
        assert len(list(session.scalars(select(JobEventORM).where(
            JobEventORM.event_type == "archived"
        )))) == 2
    result = batch(service, [(uid, 2) for uid in selected], False)
    assert result["count"] == 2
    assert all(not row["archived"] and row["archive_version"] == 3 for row in result["jobs"])
    assert len(service.state({})["jobs"]) == 3


@pytest.mark.parametrize("failure", ["stale", "missing"])
def test_bulk_archive_rolls_back_every_job_and_alert_on_late_failure(service, failure):
    first = seed(service, title="First", queue_alert=True)
    second = seed(service, title="Second", queue_alert=True)
    with service.database.session() as session:
        job = session.scalar(select(JobORM).where(JobORM.job_uid == first))
        job.alert_pending = True
        session.commit()
    if failure == "stale":
        toggle(service, second, True, 1)
    with pytest.raises(RequestError) as error:
        batch(service, [(first, 1), (second if failure == "stale" else "missing", 1)])
    assert error.value.status == (409 if failure == "stale" else 404)
    with service.database.session() as session:
        job = session.scalar(select(JobORM).where(JobORM.job_uid == first))
        assert job.archived_at is None and job.archive_version == 1 and job.alert_pending
        alert = session.scalar(select(AlertOutboxORM).where(AlertOutboxORM.job_uid == first))
        assert alert.status == "pending"
        assert not list(session.scalars(select(JobEventORM).where(
            JobEventORM.job_uid == first, JobEventORM.event_type == "archived"
        )))


@pytest.mark.parametrize("payload", [
    {"archived": True, "jobs": []},
    {"archived": True, "jobs": None},
    {"archived": True, "jobs": {}},
    {"archived": "true", "jobs": [{"job_uid": "uid", "expected_version": 1}]},
    {"archived": True, "jobs": [None]},
    {"archived": True, "jobs": [{"job_uid": "", "expected_version": 1}]},
    {"archived": True, "jobs": [{"job_uid": " ", "expected_version": 1}]},
    {"archived": True, "jobs": [{"job_uid": 1, "expected_version": 1}]},
    {"archived": True, "jobs": [{"job_uid": "uid", "expected_version": True}]},
    {"archived": True, "jobs": [{"job_uid": "uid", "expected_version": 0}]},
    {"archived": True, "jobs": [{"job_uid": "uid"}]},
    {"archived": True, "jobs": [{"job_uid": "uid", "expected_version": 1, "tier": "C"}]},
    {"archived": True, "jobs": [{"job_uid": "uid", "expected_version": 1}], "tier": "C"},
    {"archived": True, "jobs": [{"job_uid": "uid", "expected_version": 1}] * 1001},
])
def test_bulk_archive_validates_entire_payload(service, payload):
    uid = seed(service)
    with pytest.raises(RequestError) as error:
        service.mutate("PATCH", "/api/jobs/archive", payload)
    assert error.value.status == 400
    assert service.state({})["jobs"][0]["job_uid"] == uid
    assert not service.state({})["jobs"][0]["archived"]


def test_bulk_archive_rejects_duplicate_ids_before_writing(service):
    uid = seed(service)
    with pytest.raises(RequestError) as error:
        batch(service, [(uid, 1), (uid, 1)])
    assert error.value.status == 400
    assert not service.state({})["jobs"][0]["archived"]


def test_archive_restore_filters_and_preserves_evidence_scores_and_crm(service):
    uid = seed(service)
    with service.database.session() as session:
        job = session.scalar(select(JobORM).where(JobORM.job_uid == uid))
        before = (job.content_hash, job.status, job.fit_score, job.tier,
                  job.first_seen_at, job.last_seen_at, deepcopy(job.annotation))
        app = CRMRepository(session).create_from_job(uid, profile_id="product")
        app.notes = "Keep my manual notes"
        session.commit()
        saved_app = serialize_application(app)
    archived = toggle(service, uid, True, 1)
    assert archived["archived"] and archived["archived_at"]
    assert archived["archive_version"] == 2
    assert service.state({})["jobs"] == []
    assert service.state({"job_archive": "active"})["jobs"] == []
    assert [job["job_uid"] for job in service.state({"job_archive": "archived"})["jobs"]] == [uid]
    assert [job["job_uid"] for job in service.state({"job_archive": "all"})["jobs"]] == [uid]
    assert service.state({})["applications"] == [saved_app]
    with service.database.session() as session:
        job = session.scalar(select(JobORM).where(JobORM.job_uid == uid))
        assert (job.content_hash, job.status, job.fit_score, job.tier,
                job.first_seen_at, job.last_seen_at, job.annotation) == before
    restored = toggle(service, uid, False, 2)
    assert restored == {"job_uid": uid, "archived": False,
                        "archived_at": None, "archive_version": 3}
    assert len(service.state({})["jobs"]) == 1
    assert service.state({"job_archive": "archived"})["jobs"] == []
    with service.database.session() as session:
        events = list(session.scalars(select(JobEventORM).where(
            JobEventORM.event_type.in_(["archived", "restored"])
        ).order_by(JobEventORM.id)))
        assert [event.event_type for event in events] == ["archived", "restored"]


def test_archive_survives_reimport_and_reevaluation_without_new_alerts(service):
    uid = seed(service, queue_alert=True)
    toggle(service, uid, True, 1)
    with service.database.session() as session:
        repo = JobRepository(session)
        result = repo.upsert_evaluations([evaluated()], observed=False, queue_alert=True)
        session.commit()
        job = repo.find_by_record(evaluated().record)
        assert job.archived_at is not None and job.archive_version == 2
        assert result.should_alert is False
        assert repo.recent_jobs_for_digest() == []
        outbox = session.scalar(select(AlertOutboxORM).where(AlertOutboxORM.job_uid == uid))
        assert outbox.status == "cancelled"
        assert not job.alert_pending
    assert service.state({})["jobs"] == []


def test_archive_version_does_not_conflict_with_annotation_update(service):
    uid = seed(service)
    with service.database.session() as session:
        job = session.scalar(select(JobORM).where(JobORM.job_uid == uid))
        job.annotation = {"status": "reviewed", "new_evidence": True}
        session.commit()
    assert toggle(service, uid, True, 1)["archive_version"] == 2


def test_duplicate_target_is_idempotent_and_stale_version_is_rejected(service):
    uid = seed(service)
    toggle(service, uid, True, 1)
    assert toggle(service, uid, True, 2)["archive_version"] == 2
    with pytest.raises(RequestError) as error:
        toggle(service, uid, False, 1)
    assert error.value.status == 409
    with service.database.session() as session:
        assert len(list(session.scalars(select(JobEventORM).where(
            JobEventORM.event_type == "archived"
        )))) == 1


def test_compare_and_swap_rejects_concurrent_archive_changes(service):
    uid = seed(service)
    with service.database.session() as stale:
        job = stale.scalar(select(JobORM).where(JobORM.job_uid == uid))
        assert job.archive_version == 1
        toggle(service, uid, True, 1)
        # A cached identity-map value must not hide a newer archive state.
        with pytest.raises(JobArchiveConflictError):
            JobRepository(stale).set_archived(uid, True, expected_version=1)


@pytest.mark.parametrize("value", [None, "true", 1, [], {}])
def test_api_requires_boolean_archive_state(service, value):
    uid = seed(service)
    with pytest.raises(RequestError):
        toggle(service, uid, value, 1)
    assert not service.state({})["jobs"][0]["archived"]


def test_unknown_job_filter_and_fields_fail_without_changes(service):
    uid = seed(service)
    with pytest.raises(RequestError):
        service.state({"job_archive": "deleted"})
    with pytest.raises(RequestError):
        service.mutate("PATCH", f"/api/jobs/{uid}/archive", {
            "archived": True, "expected_version": 1, "tier": "C",
        })
    with pytest.raises(RequestError) as error:
        toggle(service, "missing", True, 1)
    assert error.value.status == 404
    assert not service.state({})["jobs"][0]["archived"]


def test_archive_is_excluded_from_exports_and_automatic_pruning(service, tmp_path):
    archived_uid = seed(service, title="Old low tier", tier=JobTier.C)
    active_uid = seed(service, title="Active role", tier=JobTier.B)
    toggle(service, archived_uid, True, 1)
    with service.database.session() as session:
        repo = JobRepository(session)
        job = session.scalar(select(JobORM).where(JobORM.job_uid == archived_uid))
        job.last_seen_at = datetime.now(UTC) - timedelta(days=200)
        session.flush()
        assert repo.prune_low_tier(90) == 0
        path = repo.export_shortlisted_csv(tmp_path / "jobs.csv", wide=True)
        assert [row["job_uid"] for row in csv.DictReader(path.open())] == [active_uid]


@pytest.mark.parametrize("bulk", [False, True])
def test_http_archive_requires_csrf_then_can_restore(service, bulk):
    import json

    uid = seed(service)
    server = CRMHTTPServer(service, port=0)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        def request(archived, version, token=None):
            connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            headers = {"Content-Type": "application/json"}
            if token:
                headers["X-CSRF-Token"] = token
            payload = ({"archived": archived,
                        "jobs": [{"job_uid": uid, "expected_version": version}]}
                       if bulk else {"archived": archived, "expected_version": version})
            connection.request("PATCH", "/api/jobs/archive" if bulk else f"/api/jobs/{uid}/archive",
                               json.dumps(payload), headers)
            response = connection.getresponse()
            status, data = response.status, json.loads(response.read())
            connection.close()
            return status, data

        assert request(True, 1)[0] == 403
        status, row = request(True, 1, service.csrf_token)
        assert status == 200 and (row["jobs"][0] if bulk else row)["archived"]
        status, row = request(False, 2, service.csrf_token)
        assert status == 200 and not (row["jobs"][0] if bulk else row)["archived"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
