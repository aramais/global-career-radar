from __future__ import annotations

import copy
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from typer.testing import CliRunner

from job_intake.annotation.gold import _snapshot
from job_intake.annotation.text import source_hash
from job_intake.cli import app
from job_intake.config.settings import load_app_config
from job_intake.discovery.artifacts import (
    candidate_keys,
    load_candidates,
    read_json,
    read_jsonl,
    write_jsonl,
)
from job_intake.discovery.providers import ProviderError
from job_intake.discovery.report import build_report, report_markdown
from job_intake.discovery.review import DiscoveryLabelStore
from job_intake.discovery.service import Budget, prepare_labeling, run_discovery
from job_intake.models.job import JobRecord, JobStatus


def vacancy(index=0, **kwargs):
    return JobRecord(
        source="discovery", company="Acme", title="Analytics Manager",
        original_url=f"https://jobs.example.com/jobs/{index}",
        description_clean=(f"Responsibilities\nYou will lead pricing experiments {index}.\n"
                           "You will manage projects and partner with Finance "
                           "on business decisions."),
        source_metadata={"description_complete": True}, **kwargs,
    )


def plan():
    seed = vacancy("seed")
    return {"schema": "discovery-plan-v1", "seeds": [
        {"job_uid": "seed", "source_hash": source_hash(seed), "source": _snapshot(seed),
         "relevance": 3, "role_label": "analytics-leadership",
         "duties": ["You will manage projects and partner with Finance on business decisions."]}],
        "queries": [{"id": f"q{i}", "query": f"analytics pricing projects {i}",
                     "seed_ids": ["seed"], "kind": "seed"} for i in range(4)],
        "excluded_urls": [seed.original_url], "excluded_job_uids": ["seed"],
        "criteria_summary": "Analytics with project responsibility and influence on decisions"}


class Search:
    def search(self, query, max_results=10):
        index = int(query.rsplit(" ", 1)[1])
        return [{"url": f"https://jobs.example.com/jobs/{index}", "title": "Analytics Manager",
                 "snippet": "Not used as a full description"}]


class Fetcher:
    def fetch(self, hit):
        return vacancy(hit["url"].rsplit("/", 1)[1])


def execute(tmp_path, **kwargs):
    run = tmp_path / "run"
    manifest = run_discovery(plan(), run, search=Search(), fetcher=Fetcher(),
                             budget_usd=.02, **kwargs)
    return run, manifest


def test_full_discovery_label_report_cycle(tmp_path):
    run, manifest = execute(tmp_path)
    assert manifest["cost_usd"] == pytest.approx(.02)
    assert manifest["counts"]["candidates"] == 4
    batch = prepare_labeling(run, size=4, random_size=1)
    assert len(batch["top_ids"]) == 3 and len(batch["random_ids"]) == 1
    store = DiscoveryLabelStore(load_app_config("config/settings.yaml"), run)
    rows = read_jsonl(run / "labels.jsonl")
    for index, row in enumerate(rows):
        detail = store.detail(row["job_uid"])
        assert detail["can_save"]
        payload = {"expected_version": detail["version"], "review_seconds": 30,
                   "role_label": "analytics-leadership", "relevance": 3 if index == 0 else 0,
                   "gate_outcome": "ineligible" if index == 0 else "unknown", "notes": "Human"}
        store.save(row["job_uid"], payload)
        if index == 0:
            store.save(row["job_uid"], payload)  # A lost response is retried, not charged twice.
    report = build_report(run)
    assert report["counts"]["relevant"] == report["counts"]["strong"] == 1
    assert report["counts"]["reviewed"] == 4
    assert report["time"]["human_active_seconds"] == 120
    assert report["time"]["human_seconds_per_relevant"] == 120
    assert report["cost"]["usd_per_relevant"] == pytest.approx(.02)
    assert report["hiring_among_relevant"]["ineligible"] == 1
    assert report["cohorts"]["top"]["reviewed"] == 3
    assert report["cohorts"]["random"]["reviewed"] == 1
    assert "Подтверждено релевантных" in report_markdown(report)
    with pytest.raises(ValueError, match="already exists"):
        prepare_labeling(run)


def test_no_labels_and_zero_positives_are_not_zero_cost_per_positive(tmp_path):
    run, _ = execute(tmp_path)
    report = build_report(run)
    assert report["counts"]["reviewed"] == 0
    assert report["cost"]["usd_per_relevant"] is None
    assert report["time"]["human_seconds_per_relevant"] is None


def test_unknown_post_charge_stays_in_budget_and_report(tmp_path):
    class FailingSearch:
        def search(self, query, max_results):
            raise ProviderError("safe failure", paid_request_attempted=True)
    run = tmp_path / "failure"
    result = run_discovery(plan(), run, search=FailingSearch(), fetcher=Fetcher(),
                           budget_usd=.01, max_concurrency=4)
    assert result["cost_usd"] == pytest.approx(.01)
    assert len(result["expenses"]) == 2
    assert build_report(run)["cost"]["uncertain_requests"] == 2


def test_parallel_budget_reservations_cannot_overspend():
    budget = Budget(.01)
    with ThreadPoolExecutor(max_workers=16) as executor:
        slots = list(executor.map(lambda _: budget.reserve(.003), range(50)))
    assert sum(slot is not None for slot in slots) == 3
    assert budget.spent == pytest.approx(.009)


def test_snapshot_hash_survives_private_source_metadata(tmp_path):
    class RichFetcher:
        def fetch(self, hit):
            row = Fetcher().fetch(hit)
            row.source_metadata["extraction_transport"] = "ATS API"
            return row
    run = tmp_path / "metadata"
    run_discovery(plan(), run, search=Search(), fetcher=RichFetcher(), budget_usd=.02)
    assert len(load_candidates(run)) == 4
    prepare_labeling(run)
    store = DiscoveryLabelStore(load_app_config("config/settings.yaml"), run)
    assert all(store.detail(row["job_uid"])["can_save"]
               for row in read_jsonl(run / "labels.jsonl"))


def test_known_closed_incomplete_and_duplicate_candidates_are_not_new_successes(tmp_path):
    class MixedFetcher:
        def fetch(self, hit):
            index = int(hit["url"].rsplit("/", 1)[1])
            row = vacancy(index)
            if index == 1:
                row.status = JobStatus.CLOSED
            if index == 2:
                row.source_metadata["description_complete"] = False
            return row
    run = tmp_path / "mixed"
    result = run_discovery(plan(), run, search=Search(), fetcher=MixedFetcher(), budget_usd=.02,
                           known_records=[vacancy(0)])
    assert result["counts"]["candidates"] == 1
    assert result["counts"]["known"] == 1
    assert result["counts"]["closed"] == result["counts"]["missing_text"] == 1
    assert result["cost_usd"] == pytest.approx(.02)


def test_different_requisition_ids_are_never_merged_by_body():
    first = vacancy(0, source_job_id="req-1")
    second = copy.deepcopy(first)
    second.original_url = "https://jobs.example.com/jobs/2"
    second.source_job_id = "req-2"
    assert not candidate_keys(first) & candidate_keys(second)


def test_report_rejects_source_drift_foreign_labels_and_forged_model_answers(tmp_path):
    run, _ = execute(tmp_path)
    prepare_labeling(run)
    original = read_jsonl(run / "labels.jsonl")
    bad = copy.deepcopy(original)
    bad[0].update(relevance=3, method="model", labeled_at="2026-10-18")
    write_jsonl(run / "labels.jsonl", bad)
    with pytest.raises(ValueError, match="human relevance"):
        build_report(run)
    write_jsonl(run / "labels.jsonl", original)
    candidates = read_jsonl(run / "candidates.jsonl")
    candidates[0]["priority"] = 999
    write_jsonl(run / "candidates.jsonl", candidates)
    with pytest.raises(ValueError, match="changed"):
        build_report(run)


def test_cli_plan_batch_and_report_are_usable(tmp_path):
    run, _ = execute(tmp_path)
    runner = CliRunner()
    result = runner.invoke(app, ["discovery", "prepare-labeling", "--run", str(run),
                                 "--size", "3", "--random-size", "1"])
    assert result.exit_code == 0, result.output
    result = runner.invoke(app, ["discovery", "report", "--run", str(run)])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["counts"]["queued"] == 3
    path = tmp_path / "report.md"
    result = runner.invoke(app, ["discovery", "report", "--run", str(run), "--output", str(path)])
    assert result.exit_code == 0 and path.exists()
    before = path.read_bytes()
    assert runner.invoke(app, ["discovery", "report", "--run", str(run),
                               "--output", str(path)]).exit_code == 1
    assert path.read_bytes() == before


def test_bad_plan_and_output_reuse_fail_before_external_search(tmp_path):
    bad = plan()
    bad["seeds"][0]["relevance"] = True
    with pytest.raises(ValueError):
        run_discovery(bad, tmp_path / "bad", search=Search(), fetcher=Fetcher(), budget_usd=1)
    assert not (tmp_path / "bad").exists()
    run, _ = execute(tmp_path)
    before = read_json(run / "manifest.json")
    with pytest.raises(FileExistsError):
        run_discovery(plan(), run, search=Search(), fetcher=Fetcher(), budget_usd=1)
    assert read_json(run / "manifest.json") == before


def test_committed_pending_review_is_in_read_only_report_once(tmp_path, monkeypatch):
    run, _ = execute(tmp_path)
    prepare_labeling(run, size=1, random_size=0)
    store = DiscoveryLabelStore(load_app_config("config/settings.yaml"), run)
    uid = read_jsonl(run / "labels.jsonl")[0]["job_uid"]
    payload = {"expected_version": store.detail(uid)["version"], "review_seconds": 17,
               "relevance": 2, "role_label": "analytics-leadership", "gate_outcome": "unknown"}
    monkeypatch.setattr(store, "_append_event", lambda _: (_ for _ in ()).throw(
        OSError("simulated interruption after label commit")))
    with pytest.raises(ValueError):
        store.save(uid, payload)
    pending = run / ".review-pending.json"
    assert pending.exists()
    before = pending.read_bytes()
    first, second = build_report(run), build_report(run)
    assert first["time"]["human_active_seconds"] == second["time"]["human_active_seconds"] == 17
    assert first["time"]["human_seconds_per_relevant"] == 17
    assert pending.read_bytes() == before


def test_followup_cli_run_excludes_candidates_from_sibling_run(tmp_path, monkeypatch):
    from job_intake.discovery import cli
    first, _ = execute(tmp_path)
    monkeypatch.setattr(cli, "_records", lambda *_: {})
    monkeypatch.setattr(cli, "PerplexitySearch", lambda *_: Search())
    monkeypatch.setattr(cli, "PublicJobFetcher", Fetcher)
    result = CliRunner().invoke(app, ["discovery", "run", "--plan", str(first / "plan.json"),
                                      "--output", str(tmp_path / "second"),
                                      "--budget-usd", ".02"])
    assert result.exit_code == 0, result.output
    second = read_json(tmp_path / "second" / "manifest.json")
    assert second["counts"]["known"] == 4
    assert second["counts"]["candidates"] == 0
