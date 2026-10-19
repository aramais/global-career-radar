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


def test_batch_queries_are_billed_per_post_with_batch_level_provenance(tmp_path):
    class MultiSearch:
        def search(self, query, max_results):
            assert isinstance(query, list)
            return Search().search(query[0], max_results)

    output = tmp_path / "batched"
    result = run_discovery(plan(), output, search=MultiSearch(), fetcher=Fetcher(),
                           budget_usd=.01, query_batch_size=2)
    assert result["cost_usd"] == pytest.approx(.01)
    assert len(result["expenses"]) == 2
    assert result["expenses"][0]["query_ids"] == ["q0", "q1"]
    assert result["expenses"][0]["query_units"] == 2
    candidates = read_jsonl(output / "candidates.jsonl")
    assert len(candidates) == 2
    assert candidates[0]["observations"][0]["query_ids"] == ["q0", "q1"]


def test_authentication_error_stops_discovery_and_known_unbilled_attempts_cost_zero(tmp_path):
    class UnauthorizedSearch:
        def search(self, query, max_results):
            raise ProviderError("401", paid_request_attempted=True, status_code=401,
                                billing_known_free=True)
    output = tmp_path / "unauthorized"
    result = run_discovery(plan(), output, search=UnauthorizedSearch(), fetcher=Fetcher(),
                           budget_usd=.01, max_concurrency=1)
    assert len(result["expenses"]) == 1
    assert result["stop_reason"] == "authentication_error"
    assert result["cost_usd"] == 0
    assert build_report(output)["cost"]["uncertain_requests"] == 0


def test_target_stops_at_five_complete_candidates_without_next_search_or_extra_fetches(tmp_path):
    calls, fetched = [], []

    class ManyResults:
        def search(self, query, max_results):
            calls.append(query)
            return [{"url": f"https://jobs.example.com/jobs/{i}"} for i in range(10)]

    class TrackedFetcher:
        def fetch(self, hit):
            fetched.append(hit["url"])
            return Fetcher().fetch(hit)

    output = tmp_path / "five"
    result = run_discovery(plan(), output, search=ManyResults(), fetcher=TrackedFetcher(),
                           budget_usd=1, target_candidates=5, max_concurrency=16)
    assert len(calls) == 1 and len(fetched) == 5
    assert result["counts"]["candidates"] == 5
    assert result["counts"]["search_attempts"] == 1
    assert result["cost_usd"] == pytest.approx(.005)
    assert result["stop_reason"] == "target_reached"
    assert result["limits"]["target_candidates"] == 5
    assert len(load_candidates(output)) == 5
    assert [row["outcome"] for row in read_jsonl(output / "discoveries.jsonl")].count(
        "target_reached") == 5
    prepare_labeling(output, size=5, random_size=0)
    report = build_report(output)
    assert report["counts"]["queued"] == 5 and report["counts"]["relevant"] == 0


def test_target_excludes_known_closed_incomplete_failed_and_duplicate_jobs(tmp_path):
    calls, fetched = [], []
    first = ["0", "1", "2", "3"]
    second = ["3", "mirror", "4", "5"]

    class MixedSearch:
        def search(self, query, max_results):
            calls.append(query)
            assert len(calls) <= 2, "No further POST once two new candidates exist"
            return [{"url": f"https://jobs.example.com/jobs/{i}"}
                    for i in (first if len(calls) == 1 else second)]

    class MixedFetcher:
        def fetch(self, hit):
            index = hit["url"].rsplit("/", 1)[1]
            fetched.append(index)
            if index == "4":
                raise ProviderError("unavailable")
            record = vacancy(3 if index == "mirror" else int(index))
            if index == "1":
                record.status = JobStatus.CLOSED
            elif index == "2":
                record.source_metadata["description_complete"] = False
            return record

    output = tmp_path / "mixed-target"
    result = run_discovery(plan(), output, search=MixedSearch(), fetcher=MixedFetcher(),
                           budget_usd=1, target_candidates=2, known_records=[vacancy(0)])
    assert len(calls) == 2 and fetched.count("3") == 1
    assert result["counts"]["candidates"] == 2
    assert result["counts"]["known"] == result["counts"]["closed"] == 1
    assert result["counts"]["missing_text"] == result["counts"]["duplicates"] == 1
    assert result["counts"]["fetch_attempts"] == 6
    assert result["stop_reason"] == "target_reached"
    rows = load_candidates(output)
    assert {obs["query_id"] for obs in rows[0]["observations"]} == {"q0", "q1"}
    assert len(rows[0]["observations"]) == 3


@pytest.mark.parametrize(("budget", "expected_count", "reason"), [
    (.005, 1, "budget_limit"), (1, 4, "plan_exhausted"),
])
def test_unreached_target_reports_budget_or_exhausted_plan(
    tmp_path, budget, expected_count, reason,
):
    result = run_discovery(plan(), tmp_path / "limited", search=Search(), fetcher=Fetcher(),
                           budget_usd=budget, target_candidates=5)
    assert result["counts"]["candidates"] == expected_count
    assert result["stop_reason"] == reason
    assert result["cost_usd"] <= budget


def test_target_honors_total_detail_limit_before_dispatching_next_search(tmp_path):
    calls = []

    class Results:
        def search(self, query, max_results):
            calls.append(query)
            return [{"url": f"https://jobs.example.com/jobs/{i}"} for i in range(10)]

    class ClosedFetcher:
        def fetch(self, hit):
            record = Fetcher().fetch(hit)
            record.status = JobStatus.CLOSED
            return record

    result = run_discovery(plan(), tmp_path / "details", search=Results(), fetcher=ClosedFetcher(),
                           budget_usd=1, target_candidates=5, max_details=5)
    assert len(calls) == 1 and result["counts"]["fetch_attempts"] == 5
    assert result["counts"]["candidates"] == 0
    assert result["stop_reason"] == "detail_limit"


def test_target_honors_time_limit_before_dispatching_next_search(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from job_intake.discovery import service

    clock, calls = [100.0], []
    monkeypatch.setattr(service, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    class SlowSearch:
        def search(self, query, max_results):
            calls.append(query)
            return Search().search(query, max_results)

    class SlowFetcher:
        def fetch(self, hit):
            clock[0] += 2
            return Fetcher().fetch(hit)

    result = run_discovery(plan(), tmp_path / "time", search=SlowSearch(), fetcher=SlowFetcher(),
                           budget_usd=1, target_candidates=5, max_seconds=1)
    assert len(calls) == 1 and result["counts"]["candidates"] == 1
    assert result["stop_reason"] == "time_limit"


def test_target_with_query_batching_stops_after_single_paid_batch(tmp_path):
    calls = []

    class BatchedSearch:
        def search(self, query, max_results):
            calls.append(query)
            return [{"url": f"https://jobs.example.com/jobs/{i}"} for i in range(10)]

    result = run_discovery(plan(), tmp_path / "batch-target", search=BatchedSearch(),
                           fetcher=Fetcher(), budget_usd=1, target_candidates=3,
                           query_batch_size=2)
    assert len(calls) == 1 and isinstance(calls[0], list) and len(calls[0]) == 2
    assert result["counts"]["candidates"] == 3
    assert result["cost_usd"] == pytest.approx(.005)
    assert result["stop_reason"] == "target_reached"


@pytest.mark.parametrize("target", [True, 0, -1, 1.5, 201])
def test_invalid_target_fails_before_creating_run(tmp_path, target):
    with pytest.raises(ValueError, match="target-candidates"):
        run_discovery(plan(), tmp_path / "invalid", search=Search(), fetcher=Fetcher(),
                      budget_usd=1, target_candidates=target)
    assert not (tmp_path / "invalid").exists()


def test_target_cli_runs_incrementally_and_shows_reached_goal(tmp_path, monkeypatch):
    from job_intake.discovery import cli

    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(plan()), encoding="utf-8")
    monkeypatch.setattr(cli, "_records", lambda *_: {})
    monkeypatch.setattr(cli, "PerplexitySearch", lambda *_: Search())
    monkeypatch.setattr(cli, "PublicJobFetcher", Fetcher)
    output = tmp_path / "cli-target"
    result = CliRunner().invoke(app, ["discovery", "run", "--plan", str(plan_path),
                                      "--output", str(output), "--budget-usd", "1",
                                      "--target-candidates", "2"])
    assert result.exit_code == 0, result.output
    assert "кандидатов=2/2" in result.output and "target_reached" in result.output
    assert read_json(output / "manifest.json")["cost_usd"] == pytest.approx(.01)
