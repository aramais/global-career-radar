import csv
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event, insert, select

from job_intake.crm.repository import CRMRepository
from job_intake.models.job import EvaluatedJob, FilterDecision, JobEvaluation, JobRecord, JobTier
from job_intake.storage.database import Database
from job_intake.storage.models import JobEventORM, JobORM, JobProfileEvaluationORM
from job_intake.storage.repository import JobRepository
from job_intake.utils.csv import spreadsheet_safe_value


@pytest.mark.parametrize("value", [
    '=HYPERLINK("https://example.invalid")', "+SUM(1,2)", "-1+2", "@SUM(1)",
    "\t=1+2", "\r=1+2", "\n=1+2", "  =1+2", "\ufeff=1+2",
])
def test_csv_formula_text_is_neutralized(value):
    assert spreadsheet_safe_value(value) == "'" + value


@pytest.mark.parametrize("value", ["Product Manager", "https://example.org/job", 1, -3.5, None])
def test_csv_policy_preserves_plain_text_and_numeric_values(value):
    assert spreadsheet_safe_value(value) == value


def test_vacancy_export_sanitizes_all_untrusted_columns(tmp_path):
    database = Database("sqlite:///:memory:")
    database.create_schema()
    with database.session() as session:
        repository = JobRepository(session)
        repository.upsert_evaluated_job(EvaluatedJob(
            record=JobRecord(
                source="test", company="=1+2", title="@SUM(1)", original_url="+untrusted",
                description_clean="\t=1+2", salary_text="-1+2", remote_text="\n=1+2",
            ),
            evaluation=JobEvaluation(
                decision=FilterDecision.PASS, tier=JobTier.A, fit_score=20,
                fit_reason="=1+2", matched_signals=["=1+2"], risks=["=1+2"],
            ),
        ))
        path = repository.export_shortlisted_csv(tmp_path / "export.csv")
    with path.open(newline="") as handle:
        row = next(csv.DictReader(handle))
    for name in (
        "company", "title", "original_url", "description", "salary", "remote",
        "fit_reason", "matched_signals", "risks",
    ):
        assert row[name].startswith("'"), name
    assert float(row["fit_score"]) == 20


def populate(database, count):
    with database.engine.begin() as connection:
        connection.execute(insert(JobORM), [
            {
                "job_uid": f"j-{index}", "fingerprint": f"f-{index}", "content_hash": "hash",
                "source": "test", "company": "Example", "title": f"Role {index}",
                "original_url": f"https://example.org/{index}", "description_raw": "text",
                "description_clean": "text", "status": "open", "filter_decision": "pass",
                "tier": "C", "last_seen_at": datetime.now(UTC) - timedelta(days=120),
            } for index in range(count)
        ])
        connection.execute(insert(JobProfileEvaluationORM), [
            {
                "job_uid": f"j-{index}", "profile_id": "product", "profile_name": "Product",
                "profile_version": "v1", "content_hash": "hash", "decision": "pass",
                "deterministic_score": index, "fit_score": index, "tier": "A",
                "bucket": "A", "fit_reason": "Match",
            } for index in range(count)
        ])
        connection.execute(insert(JobEventORM), [
            {"job_uid": f"j-{index}", "event_type": "created"} for index in range(count)
        ])


def test_small_export_selects_ranked_current_rows_before_loading(tmp_path):
    database = Database("sqlite:///:memory:")
    database.create_schema()
    populate(database, 600)
    queries = []
    event.listen(database.engine, "before_cursor_execute", lambda c, u, sql, p, x, m:
                 queries.append(sql) if sql.lstrip().upper().startswith("SELECT") else None)
    with database.session() as session:
        path = JobRepository(session).export_shortlisted_csv(
            tmp_path / "limited.csv", limit=1, active_profile_versions={"product": "v1"},
        )
        # Only the selected job, its evaluations, and the selected alias are materialized.
        assert len(session.identity_map) <= 3
    with path.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1
    assert rows[0]["job_uid"] == "j-599"
    assert float(rows[0]["fit_score"]) == 599
    assert len(queries) <= 3


def test_prune_batches_dependencies_instead_of_loading_each_job():
    database = Database("sqlite:///:memory:")
    database.create_schema()
    populate(database, 600)
    queries = []
    event.listen(database.engine, "before_cursor_execute", lambda c, u, sql, p, x, m:
                 queries.append(sql) if sql.lstrip().upper().startswith("SELECT") else None)
    with database.session() as session:
        assert JobRepository(session).prune_low_tier(90) == 600
        session.commit()
        assert not list(session.scalars(select(JobORM)))
        assert not list(session.scalars(select(JobEventORM)))
        assert not list(session.scalars(select(JobProfileEvaluationORM)))
    assert len(queries) <= 8


def test_prune_preserves_pending_crm_edits_in_the_same_session():
    database = Database("sqlite:///:memory:")
    database.create_schema()
    populate(database, 1)
    with database.session() as session:
        crm = CRMRepository(session)
        app = crm.create_from_job("j-0")
        company = crm.upsert_company("Example", notes="Initial")
        session.commit()
        app_id, company_id = app.id, company.id
        app.notes = "Important pending application update"
        company.notes = "Important pending company update"
        assert JobRepository(session).prune_low_tier(90) == 1
        session.commit()
        assert crm.get_application(app_id).notes == "Important pending application update"
        assert crm.get_application(app_id).job_uid is None
        assert session.get(type(company), company_id).notes == "Important pending company update"
