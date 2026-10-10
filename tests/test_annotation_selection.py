"""Frozen selections never expand or follow mutable updated_at ordering."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier

import pytest
import requests
from sqlalchemy import select
from test_annotation_integration import _config, _record

from job_intake.pipeline import JobIntakePipeline
from job_intake.storage.models import JobORM
from job_intake.storage.repository import JobRepository


@pytest.fixture
def saved_jobs(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Selection regression attempted an external request")

    monkeypatch.setattr(requests, "post", forbidden)
    records = [_record(
        source_job_id=str(number), original_url=f"https://example.com/job/{number}",
        title=f"Product Manager {number}",
    ) for number in (42, 43, 44)]

    class Adapter:
        errors = []

        def fetch_jobs(self):
            return records

    monkeypatch.setattr("job_intake.pipeline.build_adapter", lambda source: Adapter())
    config = _config(tmp_path, annotation=False)
    pipeline = JobIntakePipeline(config)
    assert pipeline.run()["persisted"] == 3
    config.annotation.enabled = True
    with pipeline.database.session() as session:
        jobs = list(session.scalars(select(JobORM)))
        identities = {job.source_job_id: job.job_uid for job in jobs}
    return pipeline, identities


def snapshot(pipeline):
    with pipeline.database.session() as session:
        return {job.job_uid: (job.annotation, job.updated_at) for job in session.scalars(
            select(JobORM)
        )}


def test_exact_selection_excludes_newest_outsider_and_resumes_same_order(saved_jobs):
    pipeline, identities = saved_jobs
    frozen = [identities["42"], identities["43"]]
    outsider = identities["44"]
    with pipeline.database.session() as session:
        for number in (42, 43, 44):
            job = session.scalar(select(JobORM).where(JobORM.job_uid == identities[str(number)]))
            job.updated_at = datetime(2026, 1, number - 41, tzinfo=UTC)
        session.commit()
    original = snapshot(pipeline)
    for _ in range(2):
        events = []
        result = pipeline.reevaluate_saved(job_uids=frozen, progress=events.append)
        assert result["persisted"] == 2
        assert result["record_errors"] == 0
        assert [event["title"] for event in events if event["event"] == "job_start"] == [
            "Product Manager 42", "Product Manager 43",
        ]
        assert next(event for event in events if event["event"] == "jobs_loaded")["total"] == 2
        assert snapshot(pipeline)[outsider] == original[outsider]
    saved = snapshot(pipeline)
    assert all(saved[uid][0]["status"] == "local" for uid in frozen)


@pytest.mark.parametrize("selection", [[], [" "], [None]])
def test_explicit_invalid_selection_cannot_fall_back_to_all(saved_jobs, monkeypatch, selection):
    pipeline, _ = saved_jobs

    def forbidden(*args, **kwargs):
        pytest.fail("Invalid selection reached AI or persistence")

    monkeypatch.setattr(pipeline, "_evaluate_record", forbidden)
    before = snapshot(pipeline)
    with pytest.raises(ValueError, match="nonempty list of nonblank"):
        pipeline.reevaluate_saved(use_ai=True, job_uids=selection)
    assert snapshot(pipeline) == before


@pytest.mark.parametrize("failure", ["missing", "archived", "duplicate", "limit"])
def test_whole_selection_is_validated_before_any_paid_call(saved_jobs, monkeypatch, failure):
    pipeline, identities = saved_jobs
    frozen = [identities["42"], identities["43"]]
    kwargs = {}
    if failure == "missing":
        frozen.append("missing-job-uid")
    elif failure == "archived":
        with pipeline.database.session() as session:
            job = session.scalar(select(JobORM).where(JobORM.job_uid == identities["43"]))
            job.archived_at = datetime(2026, 10, 10, tzinfo=UTC)
            session.commit()
    elif failure == "duplicate":
        frozen.append(identities["42"])
    else:
        kwargs["limit"] = 1

    def forbidden(*args, **kwargs):
        pytest.fail("Incomplete selection reached AI or persistence")

    monkeypatch.setattr(pipeline, "_evaluate_record", forbidden)
    monkeypatch.setattr(pipeline, "_save", forbidden)
    before = snapshot(pipeline)
    with pytest.raises(ValueError):
        pipeline.reevaluate_saved(use_ai=True, job_uids=frozen, **kwargs)
    assert snapshot(pipeline) == before


def test_repository_uid_filter_is_bound_and_empty_selection_is_rejected(saved_jobs):
    pipeline, identities = saved_jobs
    with pipeline.database.session() as session:
        repository = JobRepository(session)
        selected = repository.list_jobs(
            limit=None, archived=False, job_uids=[identities["42"], identities["43"]],
        )
        assert {job.job_uid for job in selected} == {identities["42"], identities["43"]}
        assert repository.list_jobs(limit=None, job_uids=["x' OR 1=1 --"]) == []
        with pytest.raises(ValueError, match="nonempty"):
            repository.list_jobs(job_uids=[])


def test_three_disjoint_workers_commit_to_same_sqlite_without_losing_jobs(saved_jobs):
    first, identities = saved_jobs
    # Constructors initialize schema sequentially; each worker owns its engine,
    # sessions, annotator and profile evaluations, matching the operational split.
    workers = [first, JobIntakePipeline(first.config), JobIntakePipeline(first.config)]
    ready = Barrier(3)

    def worker(task):
        pipeline, uid = task
        ready.wait(timeout=10)
        outcomes = [pipeline.reevaluate_saved(job_uids=[uid]) for _ in range(5)]
        assert all(result["persisted"] == 1 and not result["record_errors"] for result in outcomes)
        return sum(result["persisted"] for result in outcomes)

    with ThreadPoolExecutor(max_workers=3) as pool:
        assert sum(pool.map(worker, zip(workers, identities.values(), strict=True))) == 15
    final = snapshot(first)
    assert len(final) == 3
    assert all(annotation["status"] == "local" for annotation, _ in final.values())
