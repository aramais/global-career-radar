from __future__ import annotations

import json
from copy import deepcopy

import pytest

from job_intake.annotation.gold import _snapshot
from job_intake.annotation.text import source_hash
from job_intake.config.settings import load_app_config
from job_intake.discovery.artifacts import candidate_uid, read_jsonl, write_json, write_jsonl
from job_intake.discovery.review import DiscoveryLabelStore
from job_intake.discovery.service import prepare_labeling, run_discovery
from job_intake.models.job import JobRecord


def vacancy(index: str | int, **changes) -> JobRecord:
    values = {
        "source": "discovery", "source_job_id": f"req-{index}", "company": "Acme",
        "title": "Analytics Manager", "original_url": f"https://jobs.example.com/jobs/{index}",
        "description_clean": (
            f"Responsibilities for initiative {index}: lead the analytics team and partner with "
            "Product on business decisions. Manage the experiment roadmap and coach analysts "
            "while coordinating stakeholders. Own the forecasting framework and communicate "
            "recommendations to senior leadership. Define clear success metrics and improve "
            "pricing through careful research. Develop the team, align priorities and set the "
            "analytics strategy for the business. This position requires authorization to work "
            "in the advertised country. The expected salary range for this position is "
            "$130,000 – $150,000 USD per year, based on experience, skills, and qualifications."
        ),
        "source_metadata": {"description_complete": True},
    }
    values.update(changes)
    return JobRecord(**values)


def plan(query_count=1):
    seed, negative = vacancy("seed"), vacancy("negative")
    return {
        "schema": "discovery-plan-v1", "seeds": [{
            "job_uid": "seed", "source_hash": source_hash(seed), "source": _snapshot(seed),
            "relevance": 3, "duties": ["Lead the analytics team."]}],
        "counterexamples": [{
            "job_uid": "negative", "source_hash": source_hash(negative),
            "source": _snapshot(negative), "relevance": 0,
            "duties": ["Build and operate production pipelines."]}],
        "queries": [{"id": f"q{i}", "query": f"analytics projects {i}",
                     "seed_ids": ["seed"]} for i in range(query_count)],
        "excluded_urls": [], "excluded_job_uids": [],
    }


class Search:
    def __init__(self, records):
        self.records = records
        self.calls = 0

    def search(self, query, max_results=10):
        self.calls += 1
        return [{"url": record.original_url, "title": record.title, "snippet": "Preview"}
                for record in self.records]


class Fetcher:
    def __init__(self, records):
        self.records = {record.original_url: record for record in records}

    def fetch(self, hit):
        return self.records[hit["url"]]


class Selector:
    model = "fake-grounded-selector"

    def __init__(self, decisions, *, reservation=.01):
        self.decisions = iter(decisions)
        self.reservation = reservation
        self.prepared = []
        self.calls = 0

    def prepare(self, record, examples):
        assert examples["seeds"] and examples["counterexamples"]
        self.prepared.append(record.original_url)
        return record.description_clean, {"duty-1": record.description_clean}

    def reserve_cost(self, prompt):
        return self.reservation

    def select(self, prompt, units):
        self.calls += 1
        outcome = next(self.decisions)
        if isinstance(outcome, Exception):
            raise outcome
        decision = {"decision": outcome, "priority": 90, "evidence": [units["duty-1"]],
                    "selection_method": "fake-grounded-selection", "reason": "Grounded"}
        return decision, {"usage_known": True, "cost_usd": .001}


def run(tmp_path, records, selector, *, budget=.1, target=1, known=None, queries=1):
    output = tmp_path / "selection-run"
    manifest = run_discovery(
        plan(queries), output, search=Search(records), fetcher=Fetcher(records),
        selector=selector, budget_usd=budget, target_candidates=target,
        max_concurrency=1, known_records=known,
    )
    return output, manifest


def test_target_counts_only_grounded_accepts_and_preserves_rejections(tmp_path):
    records = [vacancy(i) for i in range(3)]
    selector = Selector(["reject", "uncertain", "accept"])
    output, manifest = run(tmp_path, records, selector)
    assert selector.calls == 3
    assert manifest["stop_reason"] == "target_reached"
    assert manifest["counts"]["candidates"] == manifest["counts"]["selection_accepted"] == 1
    assert manifest["counts"]["selection_rejected"] == 1
    assert manifest["counts"]["selection_uncertain"] == 1
    assert manifest["cost_usd"] == pytest.approx(.008)
    assert len(manifest["expenses"]) == 4
    assert [row["selection"]["decision"] for row in read_jsonl(output / "selection.jsonl")] == [
        "reject", "uncertain", "accept"]
    candidates = read_jsonl(output / "candidates.jsonl")
    assert [row["job_uid"] for row in candidates] == [candidate_uid(records[2])]


def test_unknown_model_failure_is_not_accepted_and_reserved_cost_is_retained(tmp_path):
    selector = Selector([ValueError("Provider unavailable"), "accept"], reservation=.02)
    output, manifest = run(tmp_path, [vacancy(0), vacancy(1)], selector)
    assert manifest["counts"]["selection_failed"] == 1
    assert manifest["counts"]["candidates"] == 1
    assert manifest["cost_usd"] == pytest.approx(.026)
    selection_expenses = [entry for entry in manifest["expenses"] if entry["stage"] == "selection"]
    assert selection_expenses[0]["status"] == "uncertain"
    assert selection_expenses[0]["cost_usd"] == .02
    assert read_jsonl(output / "selection.jsonl")[0]["selection"]["decision"] == "uncertain"


def test_unaffordable_selection_never_calls_model_or_accepts_candidate(tmp_path):
    selector = Selector(["accept"], reservation=.03)
    output, manifest = run(tmp_path, [vacancy(0)], selector, budget=.02)
    assert selector.calls == 0
    assert manifest["counts"]["candidates"] == 0
    assert manifest["stop_reason"] == "budget_limit"
    assert manifest["cost_usd"] == .005
    assert len(manifest["expenses"]) == 1
    assert read_jsonl(output / "candidates.jsonl") == []


def test_regional_review_content_keeps_own_selection_and_source(tmp_path):
    first = vacancy(0)
    second = deepcopy(first)
    second.source_job_id = "req-regional"
    second.original_url = "https://jobs.example.com/jobs/regional"
    second.location_text = "Remote - Canada"
    second.description_clean = second.description_clean.replace("USD per year", "CAD per year")
    selector = Selector(["reject", "accept"])
    output, manifest = run(tmp_path, [first, second, vacancy(2)], selector)
    assert selector.calls == 2
    assert selector.prepared == [first.original_url, second.original_url]
    assert manifest["counts"]["review_duplicates"] == 0
    duplicates = read_jsonl(output / "review-duplicates.jsonl")
    assert duplicates == []
    candidate = read_jsonl(output / "candidates.jsonl")[0]
    assert candidate["job_uid"] == candidate_uid(second)
    assert candidate["source"] == _snapshot(second)


def test_prior_run_content_is_excluded_without_transferring_human_labels(tmp_path):
    earlier = vacancy("prior")
    repeat = deepcopy(earlier)
    repeat.original_url = "https://jobs.example.com/jobs/current-regional"
    repeat.source_job_id = "req-current"
    repeat.description_clean = repeat.description_clean.replace("USD per year", "CAD per year")
    selector = Selector(["accept"])
    output, manifest = run(tmp_path, [repeat, vacancy(1)], selector, known=[earlier])
    assert selector.prepared == [vacancy(1).original_url]
    assert manifest["counts"]["review_duplicates"] == 1
    assert manifest["counts"]["candidates"] == 1
    duplicates = read_jsonl(output / "review-duplicates.jsonl")
    assert duplicates[0]["duplicate_of"] == candidate_uid(earlier)
    prepare_labeling(output, size=1, random_size=0)
    labels = read_jsonl(output / "labels.jsonl")
    assert len(labels) == 1
    assert labels[0]["relevance"] is None and labels[0]["role_label"] is None
    assert labels[0]["gate_outcome"] is None and labels[0]["method"] is None
    store = DiscoveryLabelStore(load_app_config("config/settings.yaml"), output)
    detail = json.dumps(store.detail(labels[0]["job_uid"]), ensure_ascii=False)
    assert "fake-grounded-selection" not in detail
    assert "Grounded" not in detail


def test_no_duty_units_fails_closed_without_paid_model_request(tmp_path):
    class EmptySelector(Selector):
        def prepare(self, record, examples):
            return "No candidate duties", {}

    selector = EmptySelector(["accept"])
    output, manifest = run(tmp_path, [vacancy(0)], selector)
    assert selector.calls == 0
    assert manifest["counts"]["selection_uncertain"] == 1
    assert manifest["counts"]["candidates"] == 0
    assert manifest["cost_usd"] == .005
    assert read_jsonl(output / "selection.jsonl")[0]["selection"]["decision"] == "uncertain"


def test_labeling_legacy_run_deduplicates_review_queue_without_rewriting_candidates(tmp_path):
    first, distinct = vacancy(0), vacancy(1)
    duplicate = deepcopy(first)
    duplicate.original_url = "https://jobs.example.com/jobs/regional"
    duplicate.source_job_id = "req-regional"
    duplicate.description_clean = duplicate.description_clean.replace(
        "USD per year", "CAD per year"
    )
    output = tmp_path / "legacy-run"
    output.mkdir()
    write_json(output / "manifest.json", {"status": "completed"})
    rows = [{"job_uid": candidate_uid(record), "source": _snapshot(record),
             "source_hash": source_hash(record), "priority": 100 - index}
            for index, record in enumerate([first, duplicate, distinct])]
    write_jsonl(output / "candidates.jsonl", rows)
    original = (output / "candidates.jsonl").read_bytes()
    batch = prepare_labeling(output, size=3, random_size=0)
    assert batch["size"] == 2
    assert len(read_jsonl(output / "labels.jsonl")) == 2
    assert (output / "candidates.jsonl").read_bytes() == original
