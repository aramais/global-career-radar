from __future__ import annotations

import math
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

from job_intake.annotation.gold import GOLD_SCHEMA, _snapshot
from job_intake.annotation.text import TEXT_VERSION, source_hash
from job_intake.discovery.artifacts import (
    candidate_keys,
    candidate_uid,
    load_candidates,
    read_json,
    record_from_snapshot,
    review_content_keys,
    review_queue_keys,
    url_identity,
    write_json,
    write_jsonl,
)
from job_intake.discovery.registry import DiscoveryRegistry, contract_key, selection_contract
from job_intake.models.job import JobStatus


class Budget:
    def __init__(self, limit: float):
        if not math.isfinite(limit) or limit <= 0:
            raise ValueError("A finite positive --budget-usd is required")
        self.limit, self.spent = limit, 0.0
        self.lock = threading.Lock()
        self.entries = []

    def reserve(self, amount: float) -> dict | None:
        if not math.isfinite(amount) or amount < 0:
            raise ValueError("Invalid request tariff")
        with self.lock:
            if self.spent + amount > self.limit + 1e-12:
                return None
            self.spent += amount
            entry = {"cost_usd": amount, "reserved_usd": amount, "status": "pending"}
            self.entries.append(entry)
            return entry

    def settle(self, entry: dict, *, actual: float | None = None, **fields) -> None:
        with self.lock:
            if actual is not None:
                if not math.isfinite(actual) or actual < 0:
                    raise ValueError("Invalid usage cost")
                self.spent += actual - entry["cost_usd"]
                entry["cost_usd"] = actual
            entry.update(fields)


def _validate_plan(plan: dict) -> None:
    if not isinstance(plan, dict) or plan.get("schema") != "discovery-plan-v1" \
            or not isinstance(plan.get("seeds"), list) or not plan["seeds"] \
            or not isinstance(plan.get("queries"), list) or not plan["queries"]:
        raise ValueError("Invalid or empty discovery plan")
    query_ids = set()
    for query in plan["queries"]:
        if not isinstance(query, dict) or not isinstance(query.get("query"), str) \
                or not 1 <= len(query["query"]) <= 128 or not query.get("id") \
                or query["id"] in query_ids:
            raise ValueError("Invalid/duplicate discovery query")
        query_ids.add(query["id"])
    for seed in [*plan["seeds"], *plan.get("counterexamples", [])]:
        record = record_from_snapshot(seed["source"])
        if type(seed.get("relevance")) is not int or seed["relevance"] not in (0, 2, 3) \
                or seed.get("snapshot_hash", seed.get("source_hash")) != source_hash(record):
            raise ValueError("Plan examples require source-bound human labels")
    if any(seed["relevance"] not in (2, 3) for seed in plan["seeds"]):
        raise ValueError("Plan seeds require source-bound human positive labels")
    from job_intake.discovery.provenance import validate_plan_examples
    validate_plan_examples(plan)


def run_discovery(plan: dict, output: Path, *, search, fetcher, budget_usd: float,
                  max_concurrency: int = 4, max_details: int = 200, max_results: int = 10,
                  search_price_usd: float = .005, max_seconds: float = 3600,
                  semantic=None, known_records: list | None = None,
                  query_batch_size: int = 1, target_candidates: int | None = None,
                  selector=None, registry_path: Path | None = None,
                  repeat_queries: bool = False, search_ttl_seconds: float = 86400,
                  fetch_ttl_seconds: float = 21600, max_search_attempts: int = 2,
                  objective: str | None = None, audit_rejected_size: int = 20,
                  purpose_evaluator=None) -> dict:
    """Bounded parallel retrieval; every paid attempt and partial result is retained."""
    from job_intake.discovery.planning import rank_candidate

    _validate_plan(plan)
    objective = objective or plan.get("objective", "training")
    if objective not in {"training", "p1", "p2"}:
        raise ValueError("Discovery objective must be training, p1 or p2")
    if objective == "p1" and purpose_evaluator is None:
        raise ValueError("P1 discovery requires the configured role and hiring evaluator")
    if selector is not None:
        selector.objective = objective
    if not 1 <= max_search_attempts <= 5 or search_ttl_seconds <= 0 or fetch_ttl_seconds <= 0:
        raise ValueError("Invalid registry TTL or retry limit")
    if type(max_concurrency) is not int or not 1 <= max_concurrency <= 16 \
            or type(max_details) is not int or not 1 <= max_details <= 1000 \
            or type(max_results) is not int or not 1 <= max_results <= 20 \
            or type(query_batch_size) is not int or not 1 <= query_batch_size <= 5 \
            or not math.isfinite(max_seconds) or max_seconds <= 0:
        raise ValueError("Invalid discovery limits")
    if target_candidates is not None and (
        type(target_candidates) is not int or not 1 <= target_candidates <= max_details
    ):
        raise ValueError("--target-candidates must be an integer between 1 and --max-details")
    ledger = Budget(budget_usd)
    if not math.isfinite(search_price_usd) or search_price_usd <= 0:
        raise ValueError("Search request price must be positive and finite")
    from job_intake.discovery.provenance import record_local_exposure, record_plan_exposure
    exposure_events = record_plan_exposure(
        plan, actor="discovery-run", stage="query_plan", context={"run": str(output.resolve())})
    # Output directories are never reused: a retry cannot erase labels or an earlier run.
    output.mkdir(parents=True, exist_ok=False)
    record_local_exposure(output / "example-exposure.jsonl", plan, actor="discovery-run",
                          stage="query_plan", context={"run": str(output.resolve())})
    registry = DiscoveryRegistry(registry_path or output.parent / ".discovery-registry.sqlite3")
    started = time.monotonic()
    manifest = {"schema": "discovery-run-v1", "run_id": output.name,
                "started_at": datetime.now(UTC).isoformat(), "status": "running",
                "budget_usd": budget_usd, "search_price_usd": search_price_usd,
                "objective": objective,
                "example_exposure_events": exposure_events,
                "example_exposure_journal": "example-exposure.jsonl",
                "registry": {"path": str(registry.path), "repeat_queries": repeat_queries,
                             "search_ttl_seconds": search_ttl_seconds,
                             "fetch_ttl_seconds": fetch_ttl_seconds},
                "price_source": "https://docs.perplexity.ai/docs/getting-started/pricing",
                "cost_basis": "tariff_estimate", "expenses": [], "errors": [],
                "duration_seconds": 0.0, "cost_usd": 0.0,
                "counts": {"search_attempts": 0, "search_hits": 0, "fetch_attempts": 0,
                           "fetch_original_attempts": 0, "fetch_cache_hits": 0,
                           "search_cache_hits": 0, "missing_company": 0,
                           "duplicates": 0, "known": 0, "closed": 0, "missing_text": 0,
                           "review_duplicates": 0, "selection_accepted": 0,
                           "selection_rejected": 0, "selection_uncertain": 0,
                           "selection_failed": 0},
                "limits": {"max_details": max_details, "max_results": max_results,
                           "max_concurrency": max_concurrency, "max_seconds": max_seconds,
                           "query_batch_size": query_batch_size,
                           "target_candidates": target_candidates},
                "search_options": getattr(search, "options", {}),
                "selection": {"enabled": selector is not None,
                              "model": getattr(selector, "model", None),
                              "positives": len(plan["seeds"]),
                              "negatives": len(plan.get("counterexamples", []))},
                "ranking": "gemini-grounded-v1" if semantic else (
                    "gemini-positive-negative-selection-v1" if selector
                    else "seed-duty-similarity-v1")}
    write_json(output / "plan.json", plan, exclusive=True)
    candidates, discoveries, selections, review_duplicates, rejected = [], [], [], [], []
    write_jsonl(output / "candidates.jsonl", [])
    known_keys = {"url:" + url_identity(url) for url in plan.get("excluded_urls", [])}
    reviewed_content = {}
    for seed in [*plan["seeds"], *plan.get("counterexamples", [])]:
        record = record_from_snapshot(seed["source"])
        known_keys.update(candidate_keys(record))
        reviewed_content.update(dict.fromkeys(review_queue_keys(record), seed["job_uid"]))
    for record in known_records or []:
        known_keys.update(candidate_keys(record))
        reviewed_content.update(dict.fromkeys(review_queue_keys(record), candidate_uid(record)))
    manifest["known_snapshot_size"] = len(known_records or [])
    known_uids = set(plan.get("excluded_job_uids", []))
    key_to_candidate = {}
    uid_to_candidate = {}
    url_groups = {}
    authentication_failed = threading.Event()
    queries = []
    for index in range(0, len(plan["queries"]), query_batch_size):
        group = plan["queries"][index:index + query_batch_size]
        queries.append({"id": ",".join(item["id"] for item in group),
                        "query": group[0]["query"] if len(group) == 1 else [
                            item["query"] for item in group],
                        "query_ids": [item["id"] for item in group],
                        "seed_ids": sorted({uid for item in group for uid in item["seed_ids"]})})

    def checkpoint():
        manifest["duration_seconds"] = time.monotonic() - started
        manifest["cost_usd"] = ledger.spent
        manifest["expenses"] = ledger.entries
        manifest["counts"]["candidates"] = len(candidates)
        manifest["counts"]["search_attempts"] = sum(
            row.get("stage") == "search" for row in ledger.entries)
        manifest["counts"]["selection_attempts"] = sum(
            row.get("stage") == "selection" for row in ledger.entries)
        manifest["counts"]["fetch_http_attempts"] = getattr(fetcher, "http_attempts", None)
        manifest["query_registry"] = [{key: value for key, value in query.items()
                                       if key not in {"seed_ids"}} for query in queries]
        write_jsonl(output / "candidates.jsonl", candidates)
        write_jsonl(output / "discoveries.jsonl", discoveries)
        write_jsonl(output / "selection.jsonl", selections)
        write_jsonl(output / "review-duplicates.jsonl", review_duplicates)
        write_jsonl(output / "audit-rejected.jsonl", rejected[:audit_rejected_size])
        write_json(output / "manifest.json", manifest)

    checkpoint()

    def do_search(query):
        request_key = contract_key("search", {
            "query": [" ".join(item.split()) for item in (
                [query["query"]] if isinstance(query["query"], str) else query["query"])],
            "max_results": max_results, "options": getattr(search, "options", {}),
            "endpoint": "https://api.perplexity.ai/search", "contract": "search-results-v1",
        })
        cached = registry.get(request_key)
        if cached and cached["status"] == "success" and not repeat_queries:
            manifest["counts"]["search_cache_hits"] += 1
            query["cache"] = {"key": request_key, "origin": cached["origin"]}
            return query, cached["payload"]["hits"], None
        if cached and cached["status"] in {"pending", "uncertain", "transient"}:
            return query, [], "registry_" + cached["status"]
        if authentication_failed.is_set():
            return query, [], "authentication_error"
        if time.monotonic() - started >= max_seconds:
            return query, [], "time_limit"
        expense = ledger.reserve(search_price_usd)
        if expense is None:
            return query, [], "budget_limit"
        expense.update(stage="search", request_key=request_key, query_id=query["id"],
                       query_ids=query["query_ids"], query_units=len(query["query_ids"]), attempt=1)
        if not registry.claim(request_key, stage="search", origin=str(output),
                              repeat=repeat_queries, reservation=dict(expense)):
            ledger.settle(expense, actual=0, stage="search_cache", status="concurrent_skip")
            return query, [], "registry_pending"
        checkpoint()  # A crash after POST must retain the paid reservation on disk.
        return search_attempt(query, request_key, expense, 1)

    def search_attempt(query, request_key, expense, attempt):
        at = time.monotonic()
        try:
            hits = search.search(query["query"], max_results=max_results)
            ledger.settle(expense, stage="search", query_id=query["id"], status="estimated",
                          query_ids=query["query_ids"], query_units=len(query["query_ids"]),
                          seconds=time.monotonic() - at)
            registry.put(request_key, stage="search", status="success",
                         payload={"hits": hits,
                                  "result_errors": getattr(search, "result_errors", [])},
                         origin=str(output), ttl=search_ttl_seconds)
            query["request_key"] = request_key
            query["result_errors"] = getattr(search, "result_errors", [])
            return query, hits, None
        except Exception as error:
            # Retain unknown charges; documented unbilled errors release the reservation.
            attempted = getattr(error, "paid_request_attempted", True)
            free = not attempted or getattr(error, "billing_known_free", False)
            status = getattr(error, "status_code", None)
            if status == 401:
                authentication_failed.set()
            ledger.settle(expense, actual=0.0 if free else None, stage="search",
                          query_id=query["id"], query_ids=query["query_ids"],
                          query_units=len(query["query_ids"]),
                          status="unbilled" if free else "uncertain", http_status=status,
                          retry_after_seconds=getattr(error, "retry_after_seconds", None),
                          attempt=attempt, request_key=request_key,
                          seconds=time.monotonic() - at)
            delay = getattr(error, "retry_after_seconds", None)
            if status == 429 and attempt < max_search_attempts and isinstance(delay, (int, float)) \
                    and math.isfinite(delay) and 0 <= delay <= 60 \
                    and time.monotonic() - started + delay < max_seconds:
                expense["deferred_same_query"] = True
                time.sleep(delay)
                retry = ledger.reserve(search_price_usd)
                if retry is not None:
                    retry.update(stage="search", request_key=request_key, query_id=query["id"],
                                 query_ids=query["query_ids"], attempt=attempt + 1)
                    checkpoint()
                    return search_attempt(query, request_key, retry, attempt + 1)
            registry.put(request_key, stage="search", status="transient" if free else "uncertain",
                         payload={"http_status": status, "retry_after_seconds": delay},
                         origin=str(output), ttl=max(60, delay or 0) if free else 3600)
            return query, [], "authentication_error" if status == 401 else "search_failed"

    def observation(item):
        return {key: item[key] for key in ("query_id", "query_ids", "url", "seed_ids")}

    def fetch(group):
        key = contract_key("fetch", {"url": url_identity(group[0]["hit"]["url"]),
                                     "contract": "public-full-description-v3",
                                     "domain_policy": getattr(fetcher, "domain_policy", {}),
                                     "robots": getattr(fetcher, "respect_robots", None)})
        cached = registry.get(key)
        if cached:
            if cached["status"] == "success":
                group[0]["fetch_cache"] = {"key": key, "origin": cached["origin"]}
                group[0]["company_provenance"] = cached["payload"].get("company_provenance")
                return record_from_snapshot(cached["payload"]["source"]), None
            return None, cached["payload"].get("code", "registry_pending")
        try:
            group[0]["original_fetch_attempted"] = True
            record = fetcher.fetch(group[0]["hit"])
            group[0]["company_provenance"] = record.source_metadata.get("company_provenance")
            registry.put(key, stage="fetch", status="success",
                         payload={"source": _snapshot(record), "company_provenance":
                                  record.source_metadata.get("company_provenance")},
                         origin=str(output), ttl=fetch_ttl_seconds)
            return record, None
        except Exception as error:
            code = getattr(error, "outcome", "fetch_failed")
            group[0]["http_status"] = getattr(error, "status_code", None)
            registry.put(key, stage="fetch", status="transient", payload={"code": code},
                         origin=str(output), ttl=300)
            return None, code

    def select(row):
        """Only grounded accepts enter the queue; every attempt shares the run budget."""
        audit = {key: row[key] for key in ("job_uid", "source_hash", "source", "observations")}
        audit["selection"] = {"decision": "uncertain", "reason": "No supported decision"}
        selections.append(audit)
        try:
            prompt, units = selector.prepare(record_from_snapshot(row["source"]), plan)
        except Exception:
            manifest["counts"]["selection_failed"] += 1
            manifest["errors"].append({"stage": "selection", "code": "prepare_failed"})
            return None
        if not units:
            audit["selection"]["reason"] = "No attributable candidate duties"
            manifest["counts"]["selection_uncertain"] += 1
            return None
        key = contract_key("selection", selection_contract(
            selector, prompt=prompt, units=units, snapshot=row["source"], plan=plan,
            objective=objective))
        cached = registry.get(key)
        if cached:
            audit["cache"] = {"key": key, "origin": cached["origin"]}
            if cached["status"] == "success":
                audit["selection"] = cached["payload"]["decision"]
                outcome = audit["selection"].get("decision")
                field = "selection_accepted" if outcome == "accept" else (
                    "selection_rejected" if outcome == "reject" else "selection_uncertain")
                manifest["counts"][field] += 1
                if outcome == "reject":
                    rejected.append(audit)
                return audit["selection"] if outcome == "accept" else None
            audit["selection"]["reason"] = "Prior attempt remains uncertain; wait for TTL"
            manifest["counts"]["selection_uncertain"] += 1
            return None
        entry = ledger.reserve(selector.reserve_cost(prompt))
        if entry is None:
            audit["selection"]["reason"] = "Insufficient remaining selection budget"
            manifest["stop_reason"] = "budget_limit"
            return None
        try:
            record_plan_exposure(plan, actor="discovery-selector", stage="selector", context={
                "run": str(output.resolve()), "job_uid": row["job_uid"],
                "model": getattr(selector, "model", None)},
                local_path=output / "example-exposure.jsonl")
        except BaseException:
            ledger.settle(entry, actual=0, stage="selection_blocked", status="unbilled")
            raise
        entry.update(stage="selection", job_uid=row["job_uid"], request_key=key)
        if not registry.claim(key, stage="selection", origin=str(output), reservation=dict(entry)):
            ledger.settle(entry, actual=0, stage="selection_cache", status="concurrent_skip")
            return None
        checkpoint()
        audit["expense_index"] = len(ledger.entries) - 1
        at = time.monotonic()
        try:
            decision, usage = selector.select(prompt, units)
            ledger.settle(entry, actual=usage.get("cost_usd"), stage="selection",
                          job_uid=row["job_uid"], usage=usage,
                          status="estimated" if usage.get("usage_known") else "uncertain",
                          seconds=time.monotonic() - at)
            audit["selection"] = decision
            registry.put(key, stage="selection", status="success",
                         payload={"decision": decision, "usage": usage}, origin=str(output),
                         ttl=86400 if decision.get("decision") != "uncertain" else 300)
            outcome = decision.get("decision")
            if outcome == "accept":
                manifest["counts"]["selection_accepted"] += 1
                return decision
            field = "selection_rejected" if outcome == "reject" else "selection_uncertain"
            manifest["counts"][field] += 1
            if outcome == "reject":
                rejected.append(audit)
        except Exception as error:
            usage = getattr(error, "usage", {})
            ledger.settle(entry, actual=usage.get("cost_usd"), stage="selection",
                          job_uid=row["job_uid"], usage=usage,
                          status="estimated" if usage.get("usage_known") else "uncertain",
                          seconds=time.monotonic() - at)
            audit["selection"]["reason"] = "Selection failed; not accepted"
            registry.put(key, stage="selection", status="uncertain", payload={"usage": usage},
                         origin=str(output), ttl=3600)
            manifest["counts"]["selection_failed"] += 1
            manifest["errors"].append({"stage": "selection", "code": "selection_failed"})
            if getattr(error, "status_code", None) in (401, 403):
                manifest["stop_reason"] = "selection_authentication_error"
        return None

    def retrieve(items, executor):
        # Retain cross-query memberships while fetching each discovered URL only once.
        pending = []
        for item in items:
            key = url_identity(item["url"])
            if "url:" + key in known_keys:
                manifest["counts"]["known"] += 1
                item["outcome"] = "known"
                continue
            if key not in url_groups:
                url_groups[key] = []
                pending.append(url_groups[key])
            group = url_groups[key]
            if group and "outcome" in group[0]:
                previous = group[0]
                item["outcome"] = previous["outcome"]
                if previous.get("job_uid") in uid_to_candidate:
                    item["job_uid"] = previous["job_uid"]
                    uid_to_candidate[item["job_uid"]]["observations"].append(observation(item))
            group.append(item)

        cursor = 0
        while cursor < len(pending):
            if time.monotonic() - started >= max_seconds:
                manifest["stop_reason"] = "time_limit"
                break
            remaining_details = max_details - manifest["counts"]["fetch_attempts"]
            if remaining_details <= 0:
                if target_candidates is not None:
                    manifest["stop_reason"] = "detail_limit"
                break
            width = min(max_concurrency, remaining_details)
            if target_candidates is not None:
                width = min(width, target_candidates - len(candidates))
            groups = pending[cursor:cursor + width]
            results = list(executor.map(fetch, groups))
            cursor += len(groups)
            manifest["counts"]["fetch_attempts"] += len(groups)
            hits = sum(bool(group[0].get("fetch_cache")) for group in groups)
            manifest["counts"]["fetch_cache_hits"] += hits
            manifest["counts"]["fetch_original_attempts"] += sum(
                bool(group[0].get("original_fetch_attempted")) for group in groups)
            for group, (record, error) in zip(groups, results, strict=True):
                if error:
                    for item in group:
                        item["outcome"] = error
                    manifest["errors"].append({"stage": "fetch", "code": error})
                    continue
                if record.status == JobStatus.CLOSED:
                    manifest["counts"]["closed"] += 1
                    for item in group:
                        item["outcome"] = "closed"
                    continue
                if not record.source_metadata.get("description_complete") \
                        or not (record.description_clean or record.description_raw).strip():
                    manifest["counts"]["missing_text"] += 1
                    for item in group:
                        item["outcome"] = "missing_text"
                    continue
                if not record.company.strip():
                    manifest["counts"]["missing_company"] += 1
                    group[0]["company_outcome"] = "missing_company"
                keys = candidate_keys(record)
                uid = candidate_uid(record)
                if keys & known_keys or uid in known_uids:
                    manifest["counts"]["known"] += 1
                    for item in group:
                        item["outcome"] = "known"
                    continue
                observations = [observation(item) for item in group]
                existing = next((key_to_candidate[key] for key in sorted(keys)
                                 if key in key_to_candidate), None)
                if existing is not None:
                    existing["observations"].extend(observations)
                    for key in keys:
                        key_to_candidate[key] = existing
                    manifest["counts"]["duplicates"] += 1
                    for item in group:
                        item.update(outcome="duplicate", job_uid=existing["job_uid"])
                    continue
                # Hash exactly the whitelisted source snapshot shown in review.
                record = record_from_snapshot(_snapshot(record))
                review_keys = review_queue_keys(record)
                duplicate_of = next((reviewed_content[key] for key in sorted(review_keys)
                                     if key in reviewed_content), None)
                if duplicate_of is not None:
                    manifest["counts"]["review_duplicates"] += 1
                    review_duplicates.append({"job_uid": uid, "source_hash": source_hash(record),
                                              "source": _snapshot(record),
                                              "duplicate_of": duplicate_of,
                                              "observations": observations})
                    for item in group:
                        item.update(outcome="review_duplicate", job_uid=uid,
                                    duplicate_of=duplicate_of)
                    continue
                reviewed_content.update(dict.fromkeys(review_keys, uid))
                rank = rank_candidate(record, plan)
                row = {"job_uid": uid, "source_hash": source_hash(record),
                       "source": _snapshot(record), **rank,
                       "observations": observations,
                       "ranking_method": "seed-duty-similarity-v1", "objective": objective,
                       "content_cluster_keys": sorted(review_content_keys(record))}
                if objective != "training":
                    from job_intake.models.job import EvaluatedJob, FilterDecision, JobEvaluation
                    from job_intake.purpose import predict_purpose
                    evaluated = purpose_evaluator(record) if purpose_evaluator else EvaluatedJob(
                        record, JobEvaluation(FilterDecision.REVIEW))
                    purpose = predict_purpose(evaluated)
                    row["purpose_prediction"] = purpose
                    if purpose["predicted_" + objective] != "suitable":
                        row["defer_reason"] = purpose["prediction_reason"][objective]
                        selections.append({**row, "selection": {
                            "decision": "uncertain", "reason": "Objective is not confirmed"}})
                        for item in group:
                            item.update(outcome="objective_unconfirmed", job_uid=uid)
                        continue
                if selector is not None:
                    if time.monotonic() - started >= max_seconds:
                        manifest["stop_reason"] = "time_limit"
                    if "stop_reason" in manifest:
                        for item in group:
                            item["outcome"] = manifest["stop_reason"]
                        continue
                    selected = select(row)
                    if selected is None:
                        for item in group:
                            item.update(outcome="not_selected", job_uid=uid)
                        continue
                    row.update(priority=selected["priority"], evidence=selected["evidence"],
                               selection=selected, ranking_method=selected["selection_method"])
                candidates.append(row)
                uid_to_candidate[uid] = row
                for key in keys:
                    key_to_candidate[key] = row
                for item in group:
                    item.update(outcome="candidate", job_uid=uid)
            if target_candidates is not None and len(candidates) >= target_candidates:
                manifest["stop_reason"] = "target_reached"
            checkpoint()
            if "stop_reason" in manifest:
                break
        for group in pending[cursor:]:
            for item in group:
                item["outcome"] = manifest.get("stop_reason", "detail_limit")
        if target_candidates is not None and "stop_reason" not in manifest \
                and manifest["counts"]["fetch_attempts"] >= max_details:
            manifest["stop_reason"] = "detail_limit"

    try:
        # Targeted runs inspect one Search POST before deciding whether another is needed.
        # Executor.map keeps both search and retrieval results in deterministic plan order.
        with ThreadPoolExecutor(max_workers=max_concurrency) as executor:
            search_width = 1 if target_candidates is not None else max_concurrency
            for start in range(0, len(queries), search_width):
                batch = queries[start:start + search_width]
                new_discoveries = []
                # Search reservations follow plan order. Public detail downloads
                # remain parallel; no scheduler race may decide which paid query
                # gets the final budget slot or reorder the expense ledger.
                for query, hits, error in map(do_search, batch):
                    if error:
                        manifest["errors"].append({"stage": "search", "query_id": query["id"],
                                                   "code": error})
                    for hit in hits:
                        if not isinstance(hit, dict) or not isinstance(hit.get("url"), str):
                            continue
                        new_discoveries.append({"query_id": query["id"],
                                                "seed_ids": query["seed_ids"],
                                                "query_ids": query["query_ids"],
                                                "url": hit["url"], "hit": hit})
                    manifest["counts"]["search_hits"] += len(hits)
                discoveries.extend(new_discoveries)
                if authentication_failed.is_set():
                    manifest["stop_reason"] = "authentication_error"
                if target_candidates is not None:
                    retrieve(new_discoveries, executor)
                checkpoint()
                if "stop_reason" in manifest or time.monotonic() - started >= max_seconds:
                    break
            if target_candidates is None:
                retrieve(discoveries, executor)
        # Optional semantic ranking is budgeted separately; fallback never creates human labels.
        if semantic:
            for row in sorted(candidates, key=lambda r: -r["priority"]):
                if time.monotonic() - started >= max_seconds:
                    break
                prompt, units = semantic.prepare(record_from_snapshot(row["source"]), plan)
                if not units:
                    continue
                entry = ledger.reserve(semantic.reserve_cost(prompt))
                if entry is None:
                    break
                try:
                    record_plan_exposure({**plan, "counterexamples": []},
                                         actor="discovery-rerank", stage="rerank", context={
                                             "run": str(output.resolve()),
                                             "job_uid": row["job_uid"],
                                             "model": getattr(semantic, "model", None)},
                                         local_path=output / "example-exposure.jsonl")
                except BaseException:
                    ledger.settle(entry, actual=0, stage="rerank_blocked", status="unbilled")
                    raise
                entry.update(stage="rerank", job_uid=row["job_uid"],
                             model=getattr(semantic, "model", None))
                checkpoint()
                at = time.monotonic()
                try:
                    rank, usage = semantic.rank(prompt, units)
                    ledger.settle(entry, actual=usage.get("cost_usd"), stage="rerank",
                                  status="estimated" if usage["usage_known"] else "uncertain",
                                  seconds=time.monotonic() - at, usage=usage)
                    row.update(rank)
                except Exception:
                    ledger.settle(entry, stage="rerank", status="uncertain",
                                  seconds=time.monotonic() - at)
                    manifest["errors"].append({"stage": "rerank", "code": "rerank_failed"})
                checkpoint()
        if "stop_reason" not in manifest:
            manifest["stop_reason"] = "time_limit" if time.monotonic() - started >= max_seconds \
                else ("budget_limit" if ledger.spent + search_price_usd > budget_usd
                      else ("plan_exhausted" if target_candidates is not None else "completed"))
        manifest["status"] = "completed"
    except BaseException:
        manifest["status"] = "interrupted"
        raise
    finally:
        manifest["finished_at"] = datetime.now(UTC).isoformat()
        manifest["counts"]["candidates"] = len(candidates)
        checkpoint()
    return manifest


def prepare_labeling(run_dir: Path, *, size: int = 50, random_size: int = 10,
                     seed: int = 42) -> dict:
    if type(size) is not int or type(random_size) is not int or not 1 <= size <= 1000 \
            or not 0 <= random_size <= size:
        raise ValueError("Invalid labeling batch sizes")
    if (run_dir / "labels.jsonl").exists() or (run_dir / "batch.json").exists():
        raise ValueError("Labeling batch already exists; never overwrite human answers")
    manifest = read_json(run_dir / "manifest.json")
    if manifest.get("status") == "running":
        raise ValueError("Finish discovery before preparing immutable labeling sources")
    rows = sorted(load_candidates(run_dir), key=lambda row: (-row["priority"], row["job_uid"]))
    if not rows:
        raise ValueError("No complete candidates to label")
    distinct, seen_content, duplicate_ids = [], set(), []
    for row in rows:
        keys = review_queue_keys(record_from_snapshot(row["source"]))
        if keys & seen_content:
            duplicate_ids.append(row["job_uid"])
            continue
        distinct.append(row)
        seen_content.update(keys)
    rows = distinct
    rng = random.Random(seed)
    top = rows[:max(0, min(len(rows), size - random_size))]
    top_ids = {row["job_uid"] for row in top}
    rest = [row for row in rows if row["job_uid"] not in top_ids]
    random_part = rng.sample(rest, min(random_size, len(rest)))
    selected = [*top, *random_part]
    rng.shuffle(selected)
    labels = [{"schema": GOLD_SCHEMA, "job_uid": row["job_uid"],
               "source_hash": row["source_hash"], "text_version": TEXT_VERSION,
               "source": row["source"], "label_scope": "blind_decisions",
               "role_label": None, "relevance": None, "gate_outcome": None,
               "labeled_at": None, "method": None, "tags": {}, "notes": ""}
              for row in selected]
    batch = {"schema": "discovery-batch-v1", "seed": seed,
             "created_at": datetime.now(UTC).isoformat(),
             "top_ids": [row["job_uid"] for row in top],
             "random_ids": [row["job_uid"] for row in random_part], "size": len(labels),
             "excluded_review_duplicate_ids": duplicate_ids}
    # A complete candidate snapshot digest binds the queue to the immutable run.
    import hashlib
    batch["candidates_sha256"] = hashlib.sha256(
        (run_dir / "candidates.jsonl").read_bytes()
    ).hexdigest()
    write_json(run_dir / "batch.json", batch, exclusive=True)
    write_jsonl(run_dir / "labels.jsonl", labels, exclusive=True)
    return batch
