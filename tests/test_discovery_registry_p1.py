from __future__ import annotations

from copy import deepcopy

import httpx
import pytest
from test_discovery_providers import Response, Session, page, posting
from test_discovery_selection_service import Fetcher, Search, Selector, plan, vacancy

from job_intake.discovery import service
from job_intake.discovery.artifacts import (
    candidate_keys,
    read_json,
    read_jsonl,
    review_content_keys,
    review_queue_keys,
)
from job_intake.discovery.providers import PerplexitySearch, ProviderError, PublicJobFetcher
from job_intake.discovery.registry import DiscoveryRegistry, contract_key, selection_contract
from job_intake.discovery.service import run_discovery


def execute(tmp_path, name, records, selector=None, **options):
    search, fetcher = Search(records), Fetcher(records)
    result = run_discovery(plan(), tmp_path / name, search=search, fetcher=fetcher,
                           selector=selector, budget_usd=.1, target_candidates=1,
                           max_concurrency=1, **options)
    return result, search


def test_rerank_reservation_is_on_disk_before_provider_call(tmp_path):
    class Reranker:
        model = "offline-ranker"

        def prepare(self, record, saved_plan):
            return "rank", [{"text": "attributable duties"}]

        def reserve_cost(self, prompt):
            return .01

        def rank(self, prompt, units):
            manifest = read_json(tmp_path / "ranked" / "manifest.json")
            entry = manifest["expenses"][-1]
            assert entry["stage"] == "rerank" and entry["status"] == "pending"
            assert entry["model"] == self.model and entry["job_uid"]
            assert entry["reserved_usd"] == .01
            assert manifest["cost_usd"] >= .01
            return {"priority": 42}, {"cost_usd": .002, "usage_known": True}

    result, _ = execute(tmp_path, "ranked", [vacancy(1)], semantic=Reranker())
    assert result["expenses"][-1]["status"] == "estimated"
    assert result["cost_usd"] == pytest.approx(.007)


def test_search_fetch_gate_are_reused_across_runs_without_double_billing(tmp_path):
    first_selector, second_selector = Selector(["accept"]), Selector([])
    records = [vacancy(1)]
    first, search = execute(tmp_path, "first", records, first_selector)
    second, repeat = execute(tmp_path, "second", records, second_selector)
    assert search.calls == first_selector.calls == 1
    assert repeat.calls == second_selector.calls == 0
    assert first["cost_usd"] == pytest.approx(.006)
    assert second["cost_usd"] == 0 and second["expenses"] == []
    assert second["counts"]["candidates"] == 1
    assert second["counts"]["search_cache_hits"] == second["counts"]["fetch_cache_hits"] == 1
    audit = read_jsonl(tmp_path / "second" / "selection.jsonl")[0]
    assert audit["cache"]["origin"].endswith("first")


def test_different_filters_refresh_search_but_reuse_exact_gate(tmp_path):
    records = [vacancy(1)]
    execute(tmp_path, "first", records, Selector(["accept"]))
    search, selector = Search(records), Selector([])
    search.options = {"country": "BR"}
    result = run_discovery(plan(), tmp_path / "second", search=search, fetcher=Fetcher(records),
                           selector=selector, budget_usd=.1, target_candidates=1)
    assert search.calls == 1 and selector.calls == 0
    assert result["cost_usd"] == .005


def test_repeat_queries_refreshes_search_only_and_keeps_gate_cache(tmp_path):
    records = [vacancy(1)]
    execute(tmp_path, "first", records, Selector(["accept"]))
    result, repeat = execute(tmp_path, "second", records, Selector([]), repeat_queries=True)
    assert repeat.calls == 1 and result["cost_usd"] == .005
    assert result["counts"]["selection_attempts"] == 0


def test_new_gate_model_or_criteria_does_not_inherit_cached_reject(tmp_path):
    records = [vacancy(1)]
    first, _ = execute(tmp_path, "first", records, Selector(["reject"]))
    assert first["counts"]["candidates"] == 0
    changed = Selector(["accept"])
    changed.model = "changed-model"
    second, search = execute(tmp_path, "second", records, changed)
    assert search.calls == 0 and changed.calls == 1
    assert second["counts"]["candidates"] == 1
    assert second["cost_usd"] == pytest.approx(.001)


def test_uncertain_paid_attempt_blocks_immediate_second_payment(tmp_path):
    records = [vacancy(1)]
    first, _ = execute(tmp_path, "first", records, Selector([ValueError("network")]))
    selector = Selector([])
    second, search = execute(tmp_path, "second", records, selector)
    assert first["cost_usd"] == .015
    assert second["cost_usd"] == 0 and selector.calls == search.calls == 0
    assert second["counts"]["candidates"] == 0


def test_registry_ttl_pending_claim_and_explicit_refresh_are_safe(tmp_path):
    clock = [100.0]
    registry = DiscoveryRegistry(tmp_path / "memory.sqlite3", clock=lambda: clock[0])
    assert registry.claim("q", stage="search", origin="a", ttl=10)
    assert not registry.claim("q", stage="search", origin="b", repeat=True)
    clock[0] = 111
    assert registry.get("q") is None
    assert registry.claim("q", stage="search", origin="b")
    registry.put("q", stage="search", status="success", payload={"hits": []},
                 origin="b", ttl=20)
    assert registry.claim("q", stage="search", origin="c", repeat=True)


def test_gate_key_includes_snapshot_examples_settings_and_objective():
    selector = Selector([])
    inputs = {"prompt": "duties", "units": {"u": "duty"}, "snapshot": {"location": "BR"},
              "plan": plan(), "objective": "training"}
    original = selection_contract(selector, **inputs)
    base = contract_key("selection", original)
    for field, value in (("objective", "p2"), ("prompt", "new prompt"),
                         ("snapshot", {"location": "US"})):
        altered = {**inputs, field: value}
        assert contract_key("selection", selection_contract(selector, **altered)) != base
    selector.max_output_tokens = 10
    assert contract_key("selection", selection_contract(selector, **inputs)) != base


def test_429_retries_same_query_and_records_each_post(tmp_path, monkeypatch):
    calls, sleeps = [], []

    class RateLimitedSearch(Search):
        def search(self, query, max_results=10):
            calls.append(query)
            if len(calls) == 1:
                raise ProviderError("rate limited", paid_request_attempted=True, status_code=429,
                                    retry_after_seconds=2, billing_known_free=True)
            return super().search(query, max_results)

    monkeypatch.setattr(service.time, "sleep", sleeps.append)
    records = [vacancy(1)]
    result = run_discovery(plan(), tmp_path / "limited", search=RateLimitedSearch(records),
                           fetcher=Fetcher(records), selector=Selector(["accept"]),
                           budget_usd=.1, target_candidates=1)
    assert calls == [plan()["queries"][0]["query"]] * 2
    assert sleeps == [2]
    assert result["counts"]["search_attempts"] == 2
    assert result["expenses"][0]["deferred_same_query"] is True
    assert result["expenses"][0]["cost_usd"] == 0
    assert result["cost_usd"] == pytest.approx(.006)


def test_429_retry_limit_is_bounded_and_keeps_request_for_later(tmp_path, monkeypatch):
    calls = []

    class Limited:
        def search(self, query, max_results=10):
            calls.append(query)
            raise ProviderError("rate", status_code=429, retry_after_seconds=0,
                                paid_request_attempted=True, billing_known_free=True)

    monkeypatch.setattr(service.time, "sleep", lambda _: None)
    result = run_discovery(plan(), tmp_path / "limited", search=Limited(), fetcher=Fetcher([]),
                           budget_usd=.1, target_candidates=1)
    assert len(calls) == 2
    assert result["cost_usd"] == 0 and result["counts"]["candidates"] == 0


def test_valid_paid_search_items_survive_malformed_neighbours():
    client = PerplexitySearch("fixture", session=httpx.Client(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, json={"results": [
            {"title": "Good", "url": "https://example.com/1", "snippet": "Duties"},
            {"title": "bad", "url": []}, "bad",
            {"title": "Good 2", "url": "https://example.com/2", "snippet": "Tasks"},
        ]}))))
    assert len(client.search("analytics")) == 2
    assert client.result_errors == [{"index": 1, "code": "invalid_fields"},
                                    {"index": 2, "code": "invalid_fields"}]
    client.close()


def test_brazil_variant_survives_us_copy_without_merging_answers(tmp_path):
    us = vacancy(1, location_text="United States")
    brazil = deepcopy(us)
    brazil.source_job_id, brazil.original_url = "br", "https://jobs.example.com/br"
    brazil.location_text = "Brazil"
    assert review_content_keys(us) & review_content_keys(brazil)
    assert not review_queue_keys(us) & review_queue_keys(brazil)
    assert not candidate_keys(us) & candidate_keys(brazil)
    result, _ = execute(tmp_path, "regional", [brazil], Selector(["accept"]), known_records=[us])
    assert result["counts"]["candidates"] == 1
    assert "relevance" not in read_jsonl(tmp_path / "regional" / "candidates.jsonl")[0]


def test_rejected_audit_is_separate_from_accepted_target(tmp_path):
    records = [vacancy(1), vacancy(2)]
    result, _ = execute(tmp_path, "queue", records, Selector(["reject", "accept"]))
    assert result["counts"]["candidates"] == 1
    assert len(read_jsonl(tmp_path / "queue" / "audit-rejected.jsonl")) == 1
    assert len(read_jsonl(tmp_path / "queue" / "candidates.jsonl")) == 1


@pytest.fixture
def public_target(monkeypatch):
    monkeypatch.setattr("job_intake.discovery.providers._public_url", lambda value: value)


@pytest.mark.parametrize("text", ["This position has been filled", "Esta vaga foi encerrada"])
def test_explicit_closed_page_is_recognized_in_english_and_portuguese(public_target, text):
    record = PublicJobFetcher(session=Session([Response("<h1>Analytics</h1>" + text)])).fetch(
        {"url": "https://example.com/jobs/1"})
    assert record.status.value == "closed"


def test_404_is_not_proof_that_requisition_is_closed(public_target):
    with pytest.raises(ProviderError) as caught:
        PublicJobFetcher(session=Session([Response(status=404)])).fetch(
            {"url": "https://example.com/jobs/1"})
    assert caught.value.outcome == "not_found_unverified"


def test_robots_and_domain_denials_prevent_original_requests(public_target):
    session = Session([Response("User-agent: *\nDisallow: /jobs")])
    with pytest.raises(ProviderError) as caught:
        PublicJobFetcher(session=session, respect_robots=True).fetch(
            {"url": "https://example.com/jobs/1"})
    assert caught.value.outcome == "robots_denied"
    assert len(session.calls) == 1 and session.calls[0][1].endswith("robots.txt")
    session = Session([])
    with pytest.raises(ProviderError, match="policy"):
        PublicJobFetcher(session=session, domain_policy={"example.com": "deny"}).fetch(
            {"url": "https://example.com/jobs/1"})
    assert not session.calls


def test_ats_403_does_not_try_to_bypass_access_via_public_page(public_target):
    session = Session([Response(status=403), Response(page(posting()))])
    with pytest.raises(ProviderError) as caught:
        PublicJobFetcher(session=session).fetch(
            {"url": "https://jobs.lever.co/acme/42"})
    assert caught.value.outcome == "access_denied" and len(session.calls) == 1


def test_p1_objective_requires_explicit_configured_evaluator_before_any_write(tmp_path):
    with pytest.raises(ValueError, match="configured"):
        execute(tmp_path, "p1", [], objective="p1")
    assert not (tmp_path / "p1").exists()


def test_pending_paid_reservation_is_durable_before_search_post(tmp_path):
    from job_intake.discovery.artifacts import read_json

    class CrashSearch:
        def search(self, query, max_results=10):
            manifest = read_json(tmp_path / "crash" / "manifest.json")
            assert manifest["expenses"][0]["status"] == "pending"
            assert manifest["expenses"][0]["reserved_usd"] == .005
            registry = DiscoveryRegistry(tmp_path / ".discovery-registry.sqlite3")
            row = registry.get(manifest["expenses"][0]["request_key"])
            assert row["payload"]["reservation"]["reserved_usd"] == .005
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run_discovery(plan(), tmp_path / "crash", search=CrashSearch(), fetcher=Fetcher([]),
                      budget_usd=.1, target_candidates=1)
    manifest = read_json(tmp_path / "crash" / "manifest.json")
    assert manifest["status"] == "interrupted" and manifest["cost_usd"] == .005


def test_p2_allows_supported_analogous_tasks_without_full_p1_mandate():
    from test_discovery_selection import answer

    from job_intake.discovery.semantic import GeminiSelector

    value = answer()
    for key in ("project_ownership", "people_interaction"):
        value["criteria"][key] = {"status": "unclear", "evidence_ids": []}
    with pytest.raises(ValueError, match="Unsupported"):
        GeminiSelector._validated(value, {"duty-1": "Analyze product decisions"})
    assert GeminiSelector._validated(value, {"duty-1": "Analyze product decisions"},
                                      objective="p2")["decision"] == "accept"
