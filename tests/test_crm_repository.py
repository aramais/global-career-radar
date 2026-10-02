from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select

from job_intake.crm import CRMConflictError, CRMRepository, CRMValidationError
from job_intake.crm.models import ApplicationORM, CompanyORM
from job_intake.crm.repository import serialize_application
from job_intake.models.job import EvaluatedJob, FilterDecision, JobEvaluation, JobRecord
from job_intake.storage.database import Database
from job_intake.storage.repository import JobRepository


@pytest.fixture
def repo():
    database = Database("sqlite:///:memory:")
    database.create_schema()
    with database.session() as session:
        yield CRMRepository(session)


def test_stage_history_preserves_unknown_dates_and_explicit_applied_date(repo):
    item = repo.create_application(
        company="Acme",
        title="Head of Analytics",
        stage="hr_screen",
        source_key="JobCRM:12",
        original_status="HR Screening",
    )
    assert item.applied_at is None
    assert len(item.history) == 1
    assert item.history[0].occurred_at is None
    assert item.history[0].recorded_at is not None
    assert item.history[0].to_stage == "hr_screen"
    applied = datetime(2026, 10, 2, 12, tzinfo=UTC)
    item = repo.mark_stage(item.id, "applied", occurred_at=applied, expected_version=1)
    assert item.version == 2
    assert item.applied_at == applied
    assert item.history[-1].from_stage == "hr_screen"
    repo.mark_stage(item.id, "applied", occurred_at=applied, expected_version=2)
    assert len(item.history) == 2
    assert item.version == 2


def test_source_idempotence_preserves_manual_changes_and_distinct_rows(repo):
    item = repo.create_application(
        company="Acme",
        title="PM",
        url="https://example.org/jobs/1",
        source_key="Book:Applications:2",
        source_metadata={"row": 2},
        stage="applied",
    )
    repo.update_application(item.id, notes="Personally verified", channel="referral")
    same = repo.create_application(
        company="Acme",
        title="PM",
        source_key="Book:Applications:2",
        stage="saved",
    )
    assert same.id == item.id
    assert same.notes == "Personally verified"
    assert same.stage == "applied"
    assert same.channel == "referral"
    assert same.source_metadata == {"row": 2}
    duplicate_row = repo.create_application(
        company="Acme",
        title="PM",
        url="https://example.org/jobs/1",
        source_key="Book:Applications:3",
        original_status="Duplicate",
        stage="archived",
    )
    assert duplicate_row.id != item.id


def test_saved_job_creation_deduplicates_url_without_collapsing_distinct_roles(repo):
    job_repo = JobRepository(repo.session)
    first = job_repo.upsert_evaluated_job(
        EvaluatedJob(
            JobRecord(
                source="test",
                source_job_id="1",
                company="Acme",
                title="PM",
                original_url="https://example.org/jobs/1?utm_source=linkedin",
            ),
            JobEvaluation(decision=FilterDecision.REVIEW),
        )
    )
    second = job_repo.upsert_evaluated_job(
        EvaluatedJob(
            JobRecord(
                source="test",
                source_job_id="2",
                company="Acme",
                title="Analytics Lead",
                original_url="https://example.org/jobs/2",
            ),
            JobEvaluation(decision=FilterDecision.REVIEW),
        )
    )
    manual = repo.create_application(
        company="Acme",
        title="PM",
        url="https://example.org/jobs/1",
    )
    linked = repo.create_from_job(first.job_uid, profile_id="product-management")
    assert linked.id == manual.id
    assert linked.job_uid == first.job_uid
    assert linked.profile_id == "product-management"
    assert repo.create_from_job(first.job_uid).id == linked.id
    other = repo.create_from_job(second.job_uid)
    assert other.id != linked.id
    assert len(repo.list_applications()) == 2


def test_contact_links_are_idempotent_and_never_infer_channel_or_referral_state(repo):
    item = repo.create_application(company="Acme", title="PM")
    contact = repo.upsert_contact(name="Alex", company="Acme", email="Alex@example.org")
    assert repo.upsert_contact(name="Changed", email="alex@example.org").id == contact.id
    link = repo.link_contact(item.id, contact.id, "referral", expected_version=1)
    assert item.version == 2
    assert repo.link_contact(item.id, contact.id, "referral", expected_version=2) is link
    assert item.version == 2
    assert item.channel == "unknown"
    assert item.referral_state == "none"
    payload = serialize_application(item)
    assert payload["contacts"][0]["relationship"] == "referral"
    assert payload["contacts"][0]["email"] == "Alex@example.org"
    repo.update_contact(contact.id, expected_version=1, notes="Asked for introduction")
    assert contact.version == 2
    repo.unlink_contact(item.id, contact.id, "referral", expected_version=2)
    assert not item.contact_links
    assert item.version == 3


def test_version_conflicts_leave_record_unchanged(repo):
    item = repo.create_application(company="Acme", title="PM")
    repo.update_application(
        item.id, expected_version=1, next_action="Find referral", cv_version="pm2"
    )
    with pytest.raises(CRMConflictError):
        repo.mark_stage(item.id, "applied", expected_version=1)
    assert item.stage == "saved"
    assert item.cv_version == "pm2"


def test_due_actions_exclude_terminal_stages_and_empty_actions(repo):
    due = repo.create_application(
        company="Acme",
        title="Due",
        next_action="Follow up",
        next_action_due="2026-10-01",
    )
    repo.create_application(
        company="Acme",
        title="Later",
        next_action="Prepare",
        next_action_due="2026-10-10",
    )
    repo.create_application(company="Acme", title="Blank", next_action_due="2026-10-01")
    repo.create_application(
        company="Acme",
        title="Rejected",
        stage="rejected",
        next_action="Follow up",
        next_action_due="2026-10-01",
    )
    assert [item.id for item in repo.due_actions("2026-10-02")] == [due.id]
    assert due.next_action_due == date(2026, 10, 1)


def test_company_upsert_preserves_manual_fields_and_source_provenance(repo):
    first = repo.upsert_company(
        name="Acme Company",
        url="https://example.org/careers",
        notes="Manual notes",
        source_key="manual",
        source_metadata={"source": "manual"},
    )
    duplicate = repo.upsert_company(
        name=" ACME   COMPANY ",
        url="https://bad.example/other",
        notes="Imported notes",
        source_key="Book:Companies:4",
        source_metadata={"row": 4},
    )
    assert duplicate.id == first.id
    assert duplicate.name == "Acme Company"
    assert duplicate.url == "https://example.org/careers"
    assert duplicate.notes == "Manual notes"
    assert duplicate.source_metadata["additional_sources"][0]["metadata"] == {"row": 4}
    assert len(list(repo.session.scalars(select(CompanyORM)))) == 1


def test_funnel_counts_distinct_explicit_history_and_separates_unknown_dates(repo):
    historical = repo.create_application(
        company="Acme",
        title="Old",
        stage="hr_screen",
        channel="unknown",
        profile_id="analytics",
    )
    repo.mark_stage(historical.id, "team_interview")
    live = repo.create_application(
        company="Acme",
        title="Live",
        channel="referral",
        profile_id="product",
        stage="applied",
        applied_at="2026-10-01T12:00:00Z",
    )
    repo.mark_stage(live.id, "team_interview", occurred_at="2026-10-02T13:00:00Z")
    repo.mark_stage(live.id, "assessment", occurred_at="2026-10-03T13:00:00Z")
    repo.mark_stage(live.id, "team_interview", occurred_at="2026-10-04T13:00:00Z")
    metrics = repo.funnel_metrics(since="2026-10-01", until="2026-10-05")
    assert metrics["reached_stages"]["applied"] == 1
    assert metrics["reached_stages"]["team_interview"] == 2
    assert metrics["dated_reached_stages"]["team_interview"] == 1
    assert metrics["unknown_date_reached_stages"]["team_interview"] == 1
    assert metrics["by_channel"]["unknown"]["team_from_applied_rate"] is None
    assert metrics["by_channel"]["referral"]["team_from_applied_rate"] == 1.0
    assert repo.funnel_metrics(profile_id="analytics")["total"] == 1


def test_dated_cohort_with_unknown_team_date_does_not_report_zero_conversion(repo):
    item = repo.create_application(
        company="Acme",
        title="PM",
        channel="cold",
        stage="applied",
        applied_at="2026-10-01T12:00:00Z",
    )
    repo.mark_stage(item.id, "team_interview")
    metrics = repo.funnel_metrics(since="2026-10-01", until="2026-10-02")
    assert metrics["by_channel"]["cold"]["team_from_applied_rate"] is None
    assert metrics["by_channel"]["cold"]["unknown_date_team_interviews"] == 1


@pytest.mark.parametrize(
    ("known_team_date", "known_count_in_period"),
    [("2026-09-30T12:00:00Z", 0), ("2026-10-03T12:00:00Z", 1)],
)
def test_known_stage_date_removes_unknown_even_when_outside_selected_period(
    repo,
    known_team_date,
    known_count_in_period,
):
    item = repo.create_application(
        company="Acme",
        title="PM",
        channel="referral",
        stage="applied",
        applied_at="2026-10-01T12:00:00Z",
    )
    repo.mark_stage(item.id, "team_interview")
    repo.mark_stage(item.id, "assessment", occurred_at="2026-10-02T12:00:00Z")
    repo.mark_stage(item.id, "team_interview", occurred_at=known_team_date)
    metrics = repo.funnel_metrics(since="2026-10-01", until="2026-10-31")
    assert metrics["reached_stages"]["team_interview"] == 1
    assert metrics["dated_reached_stages"]["team_interview"] == known_count_in_period
    assert metrics["unknown_date_reached_stages"]["team_interview"] == 0
    assert metrics["by_channel"]["referral"]["unknown_date_team_interviews"] == 0
    assert metrics["by_channel"]["referral"]["team_from_applied_rate"] == 1.0


def test_cleared_application_date_falls_back_to_dated_stage_history(repo):
    item = repo.create_application(
        company="Acme",
        title="PM",
        channel="cold",
        stage="applied",
        applied_at="2026-10-01T12:00:00Z",
    )
    repo.mark_stage(item.id, "team_interview", occurred_at="2026-10-03T12:00:00Z")
    repo.update_application(item.id, applied_at=None)
    metrics = repo.funnel_metrics(since="2026-10-01", until="2026-10-31")
    assert item.applied_at is None
    assert metrics["dated_reached_stages"]["applied"] == 1
    assert metrics["unknown_date_reached_stages"]["applied"] == 0
    channel = metrics["by_channel"]["cold"]
    assert channel["dated_applied"] == 1
    assert channel["unknown_date_applications"] == 0
    assert channel["team_from_applied_rate"] == 1.0


def test_channel_cohort_uses_earliest_known_application_when_field_is_empty(repo):
    item = repo.create_application(
        company="Acme",
        title="PM",
        channel="cold",
        stage="applied",
        applied_at="2026-09-10T12:00:00Z",
    )
    repo.mark_stage(item.id, "contacted", occurred_at="2026-09-12T12:00:00Z")
    repo.mark_stage(item.id, "applied", occurred_at="2026-10-01T12:00:00Z")
    repo.update_application(item.id, applied_at=None)
    october = repo.funnel_metrics(since="2026-10-01", until="2026-10-31")
    september = repo.funnel_metrics(since="2026-09-01", until="2026-09-30")
    assert october["dated_reached_stages"]["applied"] == 0
    assert october["by_channel"]["cold"]["dated_applied"] == 0
    assert september["by_channel"]["cold"]["dated_applied"] == 1
    assert october["by_channel"]["cold"]["unknown_date_applications"] == 0


def test_correcting_application_date_updates_funnel_and_preserves_history(repo):
    item = repo.create_application(
        company="Acme",
        title="PM",
        channel="cold",
        stage="applied",
        applied_at="2026-10-01T12:00:00Z",
    )
    repo.update_application(item.id, applied_at="2026-09-15T12:00:00Z")
    october = repo.funnel_metrics(since="2026-10-01", until="2026-10-31")
    september = repo.funnel_metrics(since="2026-09-01", until="2026-09-30")
    assert october["dated_reached_stages"]["applied"] == 0
    assert october["by_channel"]["cold"]["dated_applied"] == 0
    assert september["dated_reached_stages"]["applied"] == 1
    assert september["by_channel"]["cold"]["dated_applied"] == 1
    assert item.history[0].occurred_at.date().isoformat() == "2026-10-01"


def test_channel_conversion_uses_known_outcomes_only_by_period_end(repo):
    item = repo.create_application(
        company="Acme",
        title="PM",
        channel="referral",
        stage="applied",
        applied_at="2026-10-30T12:00:00Z",
    )
    repo.mark_stage(item.id, "team_interview", occurred_at="2026-11-03T12:00:00Z")
    october = repo.funnel_metrics(since="2026-10-01", until="2026-10-31")
    through_interview = repo.funnel_metrics(since="2026-10-01", until="2026-11-03")
    assert october["by_channel"]["referral"]["dated_applied"] == 1
    assert october["by_channel"]["referral"]["dated_team_interview"] == 0
    assert october["by_channel"]["referral"]["team_from_applied_rate"] == 0.0
    assert through_interview["by_channel"]["referral"]["dated_team_interview"] == 1
    assert through_interview["by_channel"]["referral"]["team_from_applied_rate"] == 1.0


def test_explicit_application_date_added_later_is_evidence_without_inferred_stages(repo):
    imported = repo.create_application(company="Acme", title="Imported", stage="hr_screen")
    edited = repo.create_application(company="Acme", title="Edited", stage="applied")
    for item in (imported, edited):
        repo.update_application(item.id, applied_at="2026-10-02T12:00:00Z")
    metrics = repo.funnel_metrics(since="2026-10-01", until="2026-10-02")
    assert metrics["reached_stages"]["applied"] == 2
    assert metrics["dated_reached_stages"]["applied"] == 2
    assert metrics["unknown_date_reached_stages"]["applied"] == 0
    assert metrics["reached_stages"]["team_interview"] == 0
    assert imported.stage == "hr_screen"
    assert imported.history[0].occurred_at is None


def test_date_only_period_end_includes_whole_day_and_excludes_outside_cohort(repo):
    old = repo.create_application(
        company="Acme",
        title="Old",
        channel="referral",
        stage="applied",
        applied_at="2026-09-01T12:00:00Z",
    )
    repo.mark_stage(old.id, "rejected", occurred_at="2026-09-10T00:00:00Z")
    current = repo.create_application(
        company="Acme",
        title="Current",
        channel="referral",
        stage="applied",
        applied_at="2026-10-01T12:00:00Z",
    )
    repo.mark_stage(current.id, "team_interview", occurred_at="2026-10-02T23:59:00Z")
    metrics = repo.funnel_metrics(since="2026-10-01", until="2026-10-02")
    assert metrics["dated_reached_stages"]["team_interview"] == 1
    assert metrics["by_channel"]["referral"]["team_from_applied_rate"] == 1.0
    assert metrics["by_channel"]["referral"]["unknown_date_applications"] == 0


def test_optimistic_mapper_guards_against_concurrent_sessions(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'crm.db'}")
    database.create_schema()
    with database.session() as session:
        original = CRMRepository(session).create_application(company="Acme", title="PM")
        application_id = original.id
        session.commit()
    with database.session() as first, database.session() as second:
        first_repo, second_repo = CRMRepository(first), CRMRepository(second)
        first_repo.get_application(application_id)
        second_snapshot = second_repo.get_application(application_id)
        assert second_snapshot.version == 1
        first_repo.update_application(application_id, expected_version=1, notes="First writer")
        first.commit()
        with pytest.raises(CRMConflictError):
            second_repo.update_application(application_id, expected_version=1, notes="Stale writer")
        second.rollback()
    with database.session() as session:
        assert CRMRepository(session).get_application(application_id).notes == "First writer"


@pytest.mark.parametrize(
    "fields",
    [
        {"company": ""},
        {"url": "javascript:alert(1)"},
        {"url": "https://user:pass@example.org"},
        {"channel": "guessed"},
        {"stage": "interview"},
        {"next_action_due": "tomorrow"},
        {"applied_at": "yesterday"},
        {"profile_id": 10},
        {"job_uid": "missing"},
        {"source_metadata": {"value": float("nan")}},
    ],
)
def test_invalid_inputs_never_create_partial_applications(repo, fields):
    arguments = {"company": "Acme", "title": "PM", **fields}
    with pytest.raises(CRMValidationError):
        repo.create_application(**arguments)
    assert not list(repo.session.scalars(select(ApplicationORM)))
