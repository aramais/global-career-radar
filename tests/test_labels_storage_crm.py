"""Labels persist independently of source, legacy ranking and human CRM state."""

from copy import deepcopy
from datetime import UTC, datetime

import pytest
from sqlalchemy import inspect, select, text

from job_intake.annotation.text import source_hash
from job_intake.crm.jobs import job_detail, list_job_page
from job_intake.models.job import EvaluatedJob, FilterDecision, JobEvaluation, JobRecord, JobTier
from job_intake.storage.database import Database
from job_intake.storage.models import JobORM, JobProfileEvaluationORM
from job_intake.storage.repository import JobRepository, JobSourceConflictError


@pytest.fixture
def session():
    database = Database("sqlite:///:memory:")
    database.create_schema()
    with database.session() as session:
        yield session
    database.engine.dispose()


def item(name="Analytics lead", *, tier=JobTier.B):
    return EvaluatedJob(
        JobRecord(source="fixture", company="Example", title=name,
                  original_url="https://example.invalid/" + name.replace(" ", "-"),
                  description_clean="Lead analytics and partner with business stakeholders.",
                  annotation={"version": "fixture", "method": "local", "claims": []}),
        JobEvaluation(FilterDecision.REVIEW, tier=tier, fit_score=12.5,
                      raw_fit_score=14.0, risks=["language_unconfirmed"]),
        profile_id="analytics", profile_name="Analytics", profile_version="v1",
    )


def labels(record, relevance=3):
    return {"schema": "vacancy-labels-v3", "status": "labeled", "relevance": relevance,
            "role_label": "analytics", "source_hash": source_hash(record),
            "prompt_version": "p1", "aggregation_version": "a1", "rubric_version": "r1",
            "tags": {"function_core": {"value": "analytics_for_decisions",
                                       "evidence": ["description_clean:000000"],
                                       "basis": "explicit"}},
            "gate_outcome": "unknown", "gate_reasons": [], "escalated": True,
            "errors": [{"message": "DO_NOT_PUBLISH"}], "usage": [{"secret": "hidden"}]}


def snapshot(row, *, exclude=()):
    return {column.key: deepcopy(getattr(row, column.key))
            for column in inspect(type(row)).columns if column.key not in exclude}


def seed(session, name="Analytics lead", **kwargs):
    value = item(name, **kwargs)
    result = JobRepository(session).upsert_evaluations([value])
    session.commit()
    return value, session.scalar(select(JobORM).where(JobORM.job_uid == result.job_uid))


def test_full_evaluation_persists_labels_columns_on_both_tables(session):
    value = item()
    value.record.annotation["labels_v3"] = labels(value.record)
    value.evaluation.relevance = 3
    value.evaluation.labels_status = "labeled"
    result = JobRepository(session).upsert_evaluations([value])
    session.commit()
    job = session.scalar(select(JobORM).where(JobORM.job_uid == result.job_uid))
    evaluation = session.scalar(select(JobProfileEvaluationORM))
    for row in (job, evaluation):
        assert (row.relevance, row.labels_status, row.labels_version) == (3, "labeled", "p1:a1")
    assert job.annotation["labels_v3"] == value.record.annotation["labels_v3"]
    assert source_hash(value.record) == value.record.annotation["labels_v3"]["source_hash"]


def test_shadow_update_changes_only_labels_and_preserves_human_purpose_and_alerts(session):
    value, job = seed(session)
    job.purpose_assessment.human_p1 = "suitable"
    job.purpose_assessment.human_p2 = "unsuitable"
    session.commit()
    exclude = {"annotation", "relevance", "labels_status", "labels_version", "updated_at"}
    before_job = snapshot(job, exclude=exclude)
    evaluation = job.profile_evaluations[0]
    before_evaluation = snapshot(evaluation, exclude=exclude)
    before_purpose = snapshot(job.purpose_assessment)
    before_annotation = deepcopy(job.annotation)
    before_events = [(event.event_type, event.payload) for event in job.events]
    assert JobRepository(session).update_labels(
        job.job_uid, labels(value.record), expected_source_hash=source_hash(value.record),
        expected_source_revision=job.source_revision,
    )
    session.commit()
    assert snapshot(job, exclude=exclude) == before_job
    assert snapshot(evaluation, exclude=exclude) == before_evaluation
    assert snapshot(job.purpose_assessment) == before_purpose
    assert [(event.event_type, event.payload) for event in job.events] == before_events
    retained = {key: val for key, val in job.annotation.items() if key != "labels_v3"}
    assert retained == before_annotation
    assert evaluation.relevance == 3
    assert job.alert_outbox == []


@pytest.mark.parametrize("conflict", ["hash", "revision", "labels_source"])
def test_labels_write_refuses_stale_source_without_writing(session, conflict):
    value, job = seed(session)
    prediction = labels(value.record)
    kwargs = {"expected_source_hash": source_hash(value.record),
              "expected_source_revision": job.source_revision}
    if conflict == "hash":
        kwargs["expected_source_hash"] = "stale"
    elif conflict == "revision":
        kwargs["expected_source_revision"] += 1
    else:
        prediction["source_hash"] = "stale"
    before = snapshot(job)
    with pytest.raises(JobSourceConflictError):
        JobRepository(session).update_labels(job.job_uid, prediction, **kwargs)
    assert snapshot(job) == before
    assert job.profile_evaluations[0].relevance is None


def test_archived_labels_require_explicit_inclusion_without_restoring(session):
    value, job = seed(session)
    job.archived_at = datetime.now(UTC)
    session.commit()
    archived_at, version = job.archived_at, job.archive_version
    repo = JobRepository(session)
    assert repo.update_labels(job.job_uid, labels(value.record)) is False
    assert repo.update_labels(job.job_uid, labels(value.record), include_archived=True)
    session.commit()
    assert job.archived_at == archived_at and job.archive_version == version
    assert job.relevance == 3


@pytest.mark.parametrize("relevance", [True, -1, 4, 2.0, "3"])
def test_invalid_ordinal_never_reaches_database(session, relevance):
    value, job = seed(session)
    with pytest.raises(ValueError, match="relevance"):
        JobRepository(session).update_labels(job.job_uid, labels(value.record, relevance))
    assert job.relevance is None and "labels_v3" not in job.annotation


def test_additive_labels_migration_preserves_old_rows_in_both_tables(tmp_path):
    database = Database(f"sqlite:///{tmp_path / 'legacy.db'}")
    with database.engine.begin() as connection:
        for table in ("jobs", "job_relevance_evaluations"):
            connection.execute(text(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY, "
                                    "job_uid VARCHAR(64) UNIQUE, sentinel TEXT)"))
            connection.execute(text(f"INSERT INTO {table} VALUES (1, 'old', 'keep')"))
    database.create_schema()
    database.create_schema()
    for table in ("jobs", "job_relevance_evaluations"):
        columns = {column["name"]: column for column in inspect(database.engine).get_columns(table)}
        assert {"relevance", "labels_status", "labels_version"} <= columns.keys()
        assert all(columns[name]["nullable"] for name in (
            "relevance", "labels_status", "labels_version"))
        with database.engine.connect() as connection:
            query = f"SELECT sentinel, relevance, labels_status, labels_version FROM {table}"
            assert connection.execute(text(query)).one() == ("keep", None, None, None)
    database.engine.dispose()


def test_crm_labels_ordinal_does_not_replace_existing_profile_assessment(session):
    value, job = seed(session)
    JobRepository(session).update_labels(job.job_uid, labels(value.record))
    session.commit()
    page = list_job_page(session, {"analytics": "v1"}, {})
    row = page["jobs"][0]
    assert row["relevance"]["id"] == "analytics"
    assert row["relevance"]["score"] == 12.5
    assert (row["labels_relevance"], row["labels_status"]) == (3, "labeled")
    assert "labels_v3" not in row and "annotation" not in row
    detail = job_detail(session, {"analytics": "v1"}, job.job_uid)
    assert detail["labels_v3"]["tags"]["function_core"]["value"] == "analytics_for_decisions"
    assert detail["labels_v3"]["escalated"] is True
    assert "DO_NOT_PUBLISH" not in repr(detail)
    assert "usage" not in detail["labels_v3"]
    assert "labels_v3" not in detail["annotation"]


@pytest.mark.parametrize("sort, expected", [("relevance-desc", [3, 2, 0, None]),
                                           ("relevance-asc", [0, 2, 3, None])])
def test_relevance_sort_happens_before_pagination_nulls_last_and_default_unchanged(
    session, sort, expected,
):
    for index, ordinal in enumerate([None, 0, 3, 2]):
        value, job = seed(session, f"Role {index}", tier=JobTier.A if ordinal == 0 else JobTier.C)
        if ordinal is not None:
            JobRepository(session).update_labels(job.job_uid, labels(value.record, ordinal))
        session.commit()
    default = list_job_page(session, {"analytics": "v1"}, {})
    assert default["jobs"][0]["labels_relevance"] == 0  # tier A still wins by default
    full = list_job_page(session, {"analytics": "v1"}, {"sort": sort})
    assert [row.get("labels_relevance") for row in full["jobs"]] == expected
    second = list_job_page(session, {"analytics": "v1"}, {
        "sort": sort, "offset": "1", "limit": "2"})
    assert [row.get("labels_relevance") for row in second["jobs"]] == expected[1:3]


def test_client_relevance_sort_matches_server_null_handling():
    from test_crm_ui import _run_javascript

    assert _run_javascript("filterAndSortJobs(["
                           "{job_uid:'none',labels_relevance:null},"
                           "{job_uid:'zero',labels_relevance:0},"
                           "{job_uid:'top',labels_relevance:3},"
                           "{job_uid:'two',labels_relevance:2}],"
                           "{sort:'relevance-desc'}).map(job=>job.job_uid)") == [
                               "top", "two", "zero", "none"]


def test_crm_machine_whitelist_rejects_nested_internal_data(session):
    value, job = seed(session)
    prediction = labels(value.record)
    prediction["tags"]["function_core"]["internal_error"] = "DO_NOT_PUBLISH"
    prediction["tags"]["function_core"]["evidence"].append({"error": "DO_NOT_PUBLISH"})
    prediction["tags"]["unknown_internal_tag"] = {"value": "DO_NOT_PUBLISH"}
    prediction["gate_reasons"] = ["office_outside_sp", "DO_NOT_PUBLISH"]
    JobRepository(session).update_labels(job.job_uid, prediction)
    session.commit()
    detail = job_detail(session, {"analytics": "v1"}, job.job_uid)
    assert detail["labels_v3"]["gate_reasons"] == ["office_outside_sp"]
    assert "DO_NOT_PUBLISH" not in repr(detail)


def test_crm_never_presents_labels_from_an_older_source_as_current(session):
    value, job = seed(session)
    JobRepository(session).update_labels(job.job_uid, labels(value.record))
    session.commit()
    job.description_clean = "The advertisement changed after the prediction."
    session.commit()
    detail = job_detail(session, {"analytics": "v1"}, job.job_uid)
    assert detail["labels_v3"] is None
    assert detail["labels_relevance"] is None
    assert detail["labels_status"] == "stale" and detail["labels_stale"] is True
    assert job.annotation["labels_v3"]["relevance"] == 3  # historical value stays stored


@pytest.mark.parametrize("status", ["error", "rules_only"])
def test_failed_attempt_preserves_all_accepted_columns_and_crm_state(session, status):
    from job_intake.storage.models import AlertOutboxORM

    value, job = seed(session, tier=JobTier.A)
    repo = JobRepository(session)
    assert repo.update_labels(job.job_uid, labels(value.record, 3))
    job.alert_pending = True
    job.purpose_assessment.human_p1 = "suitable"
    job.purpose_assessment.human_p2 = "unsuitable"
    job.purpose_assessment.predicted_p2 = "unsuitable"
    outbox = AlertOutboxORM(job_uid=job.job_uid, channel="telegram", status="pending",
                            message="Preserve this pending alert", attempts=0)
    session.add(outbox)
    session.commit()
    before_job = snapshot(job, exclude={"annotation", "updated_at"})
    before_annotation = deepcopy(job.annotation)
    before_evaluation = snapshot(job.profile_evaluations[0])
    before_purpose = snapshot(job.purpose_assessment)
    before_outbox = snapshot(outbox)
    before_events = [(event.event_type, event.payload) for event in job.events]
    failed = {**labels(value.record, 1), "status": status, "tags": {},
              "errors": [{"stage": "tag", "code": "schema_violation"}]}
    assert repo.record_label_attempt(
        job.job_uid, failed, expected_source_hash=source_hash(value.record),
        expected_source_revision=job.source_revision,
    )
    session.commit()
    assert snapshot(job, exclude={"annotation", "updated_at"}) == before_job
    assert snapshot(job.profile_evaluations[0]) == before_evaluation
    assert snapshot(job.purpose_assessment) == before_purpose
    assert snapshot(outbox) == before_outbox
    assert [(event.event_type, event.payload) for event in job.events] == before_events
    assert job.annotation["labels_v3_attempt"] == failed
    assert {key: val for key, val in job.annotation.items()
            if key != "labels_v3_attempt"} == before_annotation


@pytest.mark.parametrize("conflict", ["hash", "revision", "attempt_source"])
def test_record_attempt_has_same_optimistic_source_guards_as_labels(session, conflict):
    value, job = seed(session)
    attempt = labels(value.record, 1)
    attempt["status"] = "error"
    kwargs = {"expected_source_hash": source_hash(value.record),
              "expected_source_revision": job.source_revision}
    if conflict == "hash":
        kwargs["expected_source_hash"] = "stale"
    elif conflict == "revision":
        kwargs["expected_source_revision"] += 1
    else:
        attempt["source_hash"] = "stale"
    before = snapshot(job)
    with pytest.raises(JobSourceConflictError):
        JobRepository(session).record_label_attempt(job.job_uid, attempt, **kwargs)
    assert snapshot(job) == before


@pytest.mark.parametrize("method", ["update_labels", "record_label_attempt"])
@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_attempt_and_accepted_payloads_never_reach_storage(session, method, invalid):
    value, job = seed(session)
    prediction = labels(value.record)
    prediction["usage"] = [{"output_tokens": invalid}]
    before = snapshot(job)
    with pytest.raises(ValueError, match="finite JSON"):
        getattr(JobRepository(session), method)(job.job_uid, prediction)
    assert snapshot(job) == before


def test_archived_attempt_requires_explicit_inclusion_and_keeps_archive(session):
    value, job = seed(session)
    job.archived_at = datetime.now(UTC)
    session.commit()
    before = snapshot(job, exclude={"annotation", "updated_at"})
    attempt = {**labels(value.record), "status": "rules_only"}
    repo = JobRepository(session)
    assert repo.record_label_attempt(job.job_uid, attempt) is False
    assert repo.record_label_attempt(job.job_uid, attempt, include_archived=True)
    session.commit()
    assert snapshot(job, exclude={"annotation", "updated_at"}) == before
    assert job.annotation["labels_v3_attempt"] == attempt
