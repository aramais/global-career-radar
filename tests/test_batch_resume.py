import json
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from job_intake.config.settings import LLMConfig
from job_intake.models.job import EvaluatedJob, FilterDecision, JobEvaluation, JobRecord
from job_intake.scoring.llm import BatchReranker


def _job(description):
    return EvaluatedJob(
        JobRecord(source="synthetic", company="Example", title="Product Manager",
                  original_url="https://example.invalid/job", description_clean=description),
        JobEvaluation(FilterDecision.PASS, fit_score=10),
        profile_id="product", profile_version="version-one",
    )


class RemoteBatch:
    """A fake remote service whose task lifetime outlives a Python reranker instance."""

    def __init__(self):
        self.uploads = []
        self.tasks = []
        self.retrieved = []
        self.status = "in_progress"
        self.reject_create = False
        self.files = SimpleNamespace(create=self.upload, list=self.list_files, content=self.output)
        self.batches = SimpleNamespace(
            create=self.create, list=self.list_batches, retrieve=self.retrieve,
        )

    def upload(self, *, file, purpose):
        record = SimpleNamespace(id=f"file-{len(self.uploads)}", filename=file[0], content=file[1])
        self.uploads.append(record)
        return record

    def list_files(self, *, purpose):
        return list(self.uploads)

    def create(self, *, input_file_id, endpoint, completion_window, metadata):
        if self.reject_create:
            raise RuntimeError("Synthetic unknown creation outcome")
        task = SimpleNamespace(id=f"batch-{len(self.tasks)}", input_file_id=input_file_id,
                               metadata=metadata, status=self.status)
        self.tasks.append(task)
        return task

    def list_batches(self):
        return list(self.tasks)

    def retrieve(self, identifier):
        self.retrieved.append(identifier)
        return SimpleNamespace(id=identifier, status=self.status, output_file_id=identifier)

    def output(self, identifier):
        task = next(task for task in self.tasks if task.id == identifier)
        upload = next(upload for upload in self.uploads if upload.id == task.input_file_id)
        lines = []
        for request in map(json.loads, upload.content.decode().splitlines()):
            prompt = request["body"]["input"]
            score = 3 if "Role one" in prompt else 5
            lines.append({"custom_id": request["custom_id"], "response": {"body": {
                "output_text": json.dumps({"semantic_score": score}),
            }}})
        return SimpleNamespace(text="\n".join(map(json.dumps, lines)))


@pytest.fixture
def service(tmp_path, monkeypatch):
    prompt = tmp_path / "prompt.txt"
    prompt.write_text("JSON {profile_version} {description}")
    config = LLMConfig(enabled=True, batch_enabled=True, prompt_path=str(prompt),
                       api_key_env="SYNTHETIC_BATCH_KEY", batch_max_wait=0,
                       batch_state_dir=str(tmp_path / "batch-state"))
    remote = RemoteBatch()
    monkeypatch.setenv(config.api_key_env, "synthetic-key")
    monkeypatch.setattr("openai.OpenAI", lambda **kwargs: remote)
    return remote, config


def test_timeout_then_restart_imports_completed_results_with_original_mapping(service):
    remote, config = service
    first = BatchReranker(config)
    first_jobs = [_job("Role one"), _job("Role two")]
    first.rerank_many(first_jobs)
    assert first.last_stats["pending"] == 1
    assert all(job.evaluation.semantic_score is None for job in first_jobs)
    remote.status = "completed"
    second_jobs = [_job("Role two"), _job("Role one")]
    second = BatchReranker(replace(config, batch_max_wait=1, batch_poll_interval=0.1))
    second.rerank_many(second_jobs)
    assert [job.evaluation.semantic_score for job in second_jobs] == [5, 3]
    assert len(remote.uploads) == len(remote.tasks) == 1
    assert second.last_stats["resumed"] == second.last_stats["completed"] == 1
    assert second.last_stats["errors"] == second.last_stats["pending"] == 0
    assert remote.retrieved == ["batch-0", "batch-0"]
    with sqlite3.connect(f"{config.batch_state_dir}/batch-state.sqlite3") as database:
        assert database.execute("SELECT COUNT(*) FROM batch_requests").fetchone()[0] == 2


def test_subsets_and_new_jobs_reuse_existing_request_tasks(service):
    remote, config = service
    BatchReranker(config).rerank_many([_job("Role one"), _job("Role two")])
    remote.status = "completed"
    subset = [_job("Role two")]
    BatchReranker(config).rerank_many(subset)
    assert subset[0].evaluation.semantic_score == 5
    assert len(remote.tasks) == len(remote.uploads) == 1
    expanded = [_job("Role two"), _job("Role three"), _job("Role one")]
    BatchReranker(config).rerank_many(expanded)
    assert [job.evaluation.semantic_score for job in expanded] == [5, 5, 3]
    assert len(remote.tasks) == len(remote.uploads) == 2
    assert len(remote.uploads[1].content.decode().splitlines()) == 1


@pytest.mark.parametrize("change", ["source", "model", "profile", "prompt"])
def test_changed_requests_never_import_old_results(service, change):
    remote, config = service
    remote.status = "completed"
    BatchReranker(config).rerank_many([_job("Role one")])
    second = _job("Role one")
    if change == "source":
        second.record.location_text = "Brazil"
    elif change == "model":
        config = replace(config, model="different-model")
    elif change == "profile":
        second.profile_version = "version-two"
    else:
        with open(config.prompt_path, "w") as stream:
            stream.write("Changed JSON {description}")
    BatchReranker(config).rerank_many([second])
    assert len(remote.tasks) == len(remote.uploads) == 2


@pytest.mark.parametrize("checkpoint", ["uploaded", "submitted"])
def test_crash_after_remote_mutation_recovers_ids_without_duplicate_submission(
    service, monkeypatch, checkpoint,
):
    remote, config = service
    remote.status = "completed"
    first = BatchReranker(config)
    write_state = first._write_state
    crashed = False

    def crash_once(path, state):
        nonlocal crashed
        if state["phase"] == checkpoint and not crashed:
            crashed = True
            raise OSError("Synthetic checkpoint interruption")
        write_state(path, state)

    monkeypatch.setattr(first, "_write_state", crash_once)
    first.rerank_many([_job("Role one")])
    assert first.last_stats["errors"] == 1
    second = [_job("Role one")]
    BatchReranker(config).rerank_many(second)
    assert second[0].evaluation.semantic_score == 3
    assert len(remote.uploads) == len(remote.tasks) == 1


def test_unknown_create_outcome_does_not_blindly_resubmit(service):
    remote, config = service
    remote.reject_create = True
    first = BatchReranker(config)
    first.rerank_many([_job("Role one")])
    assert first.last_stats["errors"] == 1
    remote.reject_create = False
    second = BatchReranker(config)
    jobs = [_job("Role one")]
    second.rerank_many(jobs)
    assert second.last_stats["errors"] == 1
    assert jobs[0].evaluation.semantic_score is None
    assert len(remote.uploads) == 1
    assert remote.tasks == []
    assert "Unresolved batch creation" in jobs[0].evaluation.audit_log[-1]


def test_corrupt_saved_mapping_fails_closed(service):
    remote, config = service
    BatchReranker(config).rerank_many([_job("Role one")])
    with sqlite3.connect(f"{config.batch_state_dir}/batch-state.sqlite3") as database:
        key, serialized = database.execute("SELECT key,state FROM batch_tasks").fetchone()
        state = json.loads(serialized)
        state["mapping"]["0"] = "unrelated-request"
        database.execute("UPDATE batch_tasks SET state=? WHERE key=?", (json.dumps(state), key))
    reranker = BatchReranker(config)
    reranker.rerank_many([_job("Role one")])
    assert reranker.last_stats["errors"] == 1
    assert len(remote.tasks) == len(remote.uploads) == 1


def test_concurrent_runs_share_one_paid_submission(service):
    remote, config = service
    rerankers = [BatchReranker(config), BatchReranker(config)]
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda reranker: reranker.rerank_many([
            _job("Role one"), _job("Role two"),
        ]), rerankers))
    assert len(remote.tasks) == len(remote.uploads) == 1
    assert all(job.evaluation.semantic_score is None for jobs in results for job in jobs)
    assert all(reranker.last_stats["pending"] == 1 for reranker in rerankers)


def test_batch_provider_error_details_are_not_persisted_or_logged(service, monkeypatch, caplog):
    remote, config = service

    def fail(**kwargs):
        raise RuntimeError("Synthetic private-key and provider request content")

    monkeypatch.setattr(remote.files, "create", fail)
    job = _job("Role one")
    reranker = BatchReranker(config)
    reranker.rerank_many([job])
    assert reranker.last_stats["errors"] == 1
    assert "private-key" not in caplog.text
    assert "private-key" not in " ".join(job.evaluation.audit_log)
    with sqlite3.connect(f"{config.batch_state_dir}/batch-state.sqlite3") as database:
        serialized = database.execute("SELECT state FROM batch_tasks").fetchone()[0]
    assert "private-key" not in serialized


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits")
def test_batch_state_is_readable_only_by_its_owner(service):
    remote, config = service
    BatchReranker(config).rerank_many([_job("Role one")])
    path = Path(config.batch_state_dir) / "batch-state.sqlite3"
    assert path.stat().st_mode & 0o777 == 0o600
