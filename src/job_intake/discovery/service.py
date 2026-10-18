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
    url_identity,
    write_json,
    write_jsonl,
)
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
    for seed in plan["seeds"]:
        record = record_from_snapshot(seed["source"])
        if type(seed.get("relevance")) is not int or seed["relevance"] not in (2, 3) \
                or seed.get("snapshot_hash", seed.get("source_hash")) != source_hash(record):
            raise ValueError("Plan seeds require source-bound human positive labels")


def run_discovery(plan: dict, output: Path, *, search, fetcher, budget_usd: float,
                  max_concurrency: int = 4, max_details: int = 200, max_results: int = 10,
                  search_price_usd: float = .005, max_seconds: float = 3600,
                  semantic=None, known_records: list | None = None) -> dict:
    """Bounded parallel retrieval; every paid attempt and partial result is retained."""
    from job_intake.discovery.planning import rank_candidate

    _validate_plan(plan)
    if type(max_concurrency) is not int or not 1 <= max_concurrency <= 16 \
            or type(max_details) is not int or not 1 <= max_details <= 1000 \
            or type(max_results) is not int or not 1 <= max_results <= 20 \
            or not math.isfinite(max_seconds) or max_seconds <= 0:
        raise ValueError("Invalid discovery limits")
    ledger = Budget(budget_usd)
    if not math.isfinite(search_price_usd) or search_price_usd <= 0:
        raise ValueError("Search request price must be positive and finite")
    # Output directories are never reused: a retry cannot erase labels or an earlier run.
    output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    manifest = {"schema": "discovery-run-v1", "run_id": output.name,
                "started_at": datetime.now(UTC).isoformat(), "status": "running",
                "budget_usd": budget_usd, "search_price_usd": search_price_usd,
                "price_source": "https://docs.perplexity.ai/docs/getting-started/pricing",
                "cost_basis": "tariff_estimate", "expenses": [], "errors": [],
                "duration_seconds": 0.0, "cost_usd": 0.0,
                "counts": {"search_attempts": 0, "search_hits": 0, "fetch_attempts": 0,
                           "duplicates": 0, "known": 0, "closed": 0, "missing_text": 0},
                "limits": {"max_details": max_details, "max_results": max_results,
                           "max_concurrency": max_concurrency, "max_seconds": max_seconds},
                "ranking": "gemini-grounded-v1" if semantic else "seed-duty-similarity-v1"}
    write_json(output / "plan.json", plan, exclusive=True)
    candidates, discoveries = [], []
    write_jsonl(output / "candidates.jsonl", [])
    known_keys = {"url:" + url_identity(url) for url in plan.get("excluded_urls", [])}
    for seed in plan["seeds"]:
        known_keys.update(candidate_keys(record_from_snapshot(seed["source"])))
    for record in known_records or []:
        known_keys.update(candidate_keys(record))
    manifest["known_snapshot_size"] = len(known_records or [])
    known_uids = set(plan.get("excluded_job_uids", []))
    key_to_candidate = {}

    def checkpoint():
        manifest["duration_seconds"] = time.monotonic() - started
        manifest["cost_usd"] = ledger.spent
        manifest["expenses"] = ledger.entries
        write_jsonl(output / "candidates.jsonl", candidates)
        write_jsonl(output / "discoveries.jsonl", discoveries)
        write_json(output / "manifest.json", manifest)

    checkpoint()

    def do_search(query):
        if time.monotonic() - started >= max_seconds:
            return query, [], "time_limit"
        expense = ledger.reserve(search_price_usd)
        if expense is None:
            return query, [], "budget_limit"
        at = time.monotonic()
        try:
            hits = search.search(query["query"], max_results=max_results)
            ledger.settle(expense, stage="search", query_id=query["id"], status="estimated",
                          seconds=time.monotonic() - at)
            return query, hits, None
        except Exception as error:
            # An HTTP failure may still have incurred a charge. Keep its reservation.
            attempted = getattr(error, "paid_request_attempted", True)
            ledger.settle(expense, actual=None if attempted else 0.0, stage="search",
                          query_id=query["id"], status="uncertain" if attempted else "unbilled",
                          seconds=time.monotonic() - at)
            return query, [], "search_failed"

    try:
        # Executor.map consumes results in plan order, so fixture runs remain reproducible.
        with ThreadPoolExecutor(max_workers=max_concurrency) as executor:
            futures = []
            # Bounded batches avoid eagerly launching the entire plan beyond time/budget limits.
            for start in range(0, len(plan["queries"]), max_concurrency):
                batch = plan["queries"][start:start + max_concurrency]
                futures = list(executor.map(do_search, batch))
                for query, hits, error in futures:
                    if error:
                        manifest["errors"].append({"stage": "search", "query_id": query["id"],
                                                   "code": error})
                    for hit in hits:
                        if not isinstance(hit, dict) or not isinstance(hit.get("url"), str):
                            continue
                        discoveries.append({"query_id": query["id"], "seed_ids": query["seed_ids"],
                                            "url": hit["url"], "hit": hit})
                    manifest["counts"]["search_hits"] += len(hits)
                checkpoint()
                if ledger.spent + search_price_usd > budget_usd \
                        or time.monotonic() - started >= max_seconds:
                    break
            manifest["counts"]["search_attempts"] = len(ledger.entries)
            # Retain all query memberships even when fetching a URL only once.
            grouped = {}
            for item in discoveries:
                key = url_identity(item["url"])
                if "url:" + key in known_keys:
                    manifest["counts"]["known"] += 1
                    item["outcome"] = "known"
                    continue
                grouped.setdefault(key, []).append(item)
            pending = list(grouped.values())[:max_details]
            for group in list(grouped.values())[max_details:]:
                for item in group:
                    item["outcome"] = "detail_limit"

            def fetch(group):
                try:
                    return fetcher.fetch(group[0]["hit"]), None
                except Exception:
                    return None, "fetch_failed"

            for start in range(0, len(pending), max_concurrency):
                if time.monotonic() - started >= max_seconds:
                    manifest["stop_reason"] = "time_limit"
                    break
                groups = pending[start:start + max_concurrency]
                results = list(executor.map(fetch, groups))
                manifest["counts"]["fetch_attempts"] += len(groups)
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
                    keys = candidate_keys(record)
                    uid = candidate_uid(record)
                    if keys & known_keys or uid in known_uids:
                        manifest["counts"]["known"] += 1
                        for item in group:
                            item["outcome"] = "known"
                        continue
                    observations = [{"query_id": item["query_id"], "url": item["url"],
                                     "seed_ids": item["seed_ids"]} for item in group]
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
                    # The immutable review contract retains whitelisted source facts only.
                    # source_hash includes metadata, so hash this exact stored snapshot.
                    record = record_from_snapshot(_snapshot(record))
                    rank = rank_candidate(record, plan)
                    row = {"job_uid": uid, "source_hash": source_hash(record),
                           "source": _snapshot(record), **rank,
                           "observations": observations,
                           "ranking_method": "seed-duty-similarity-v1"}
                    candidates.append(row)
                    for key in keys:
                        key_to_candidate[key] = row
                    for item in group:
                        item.update(outcome="candidate", job_uid=uid)
                checkpoint()
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
                      else "completed")
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
             "random_ids": [row["job_uid"] for row in random_part], "size": len(labels)}
    # A complete candidate snapshot digest binds the queue to the immutable run.
    import hashlib
    batch["candidates_sha256"] = hashlib.sha256(
        (run_dir / "candidates.jsonl").read_bytes()
    ).hexdigest()
    write_json(run_dir / "batch.json", batch, exclusive=True)
    write_jsonl(run_dir / "labels.jsonl", labels, exclusive=True)
    return batch
