"""Offline, predeclared experiments; ledgers describe actual attempts, never new calls."""

from __future__ import annotations

import copy
import math
from datetime import UTC, datetime

from job_intake.annotation.cohorts import digest
from job_intake.annotation.frozen_eval import compare_runs, validate_run

FACTORS = {
    "review_thinking",
    "compact_payload",
    "chunk_chars",
    "reason_priming",
    "independent_verify",
    "prefilter_enabled",
    "domain_filter",
    "freshness_days",
    "search_type",
}


def declare_experiment(
    bundle: dict,
    *,
    experiment_id: str,
    factor: str,
    variants: dict[str, dict],
    budget_usd: float,
    goals: dict,
    minimum_repeats: int = 2,
    maximum_price_age_days: int = 30,
) -> dict:
    if (
        not experiment_id.strip()
        or factor not in FACTORS
        or len(variants) != 2
        or type(budget_usd) not in {int, float}
        or not math.isfinite(budget_usd)
        or budget_usd <= 0
        or type(minimum_repeats) is not int
        or minimum_repeats < 2
        or type(maximum_price_age_days) is not int
        or maximum_price_age_days < 0
    ):
        raise ValueError(
            "Experiment requires known factor, positive budget and independent repeats"
        )
    if not isinstance(goals, dict) or not goals:
        raise ValueError("Declare goals before measuring")
    values = list(variants.values())
    if any(name not in bundle["versions"] for name in variants) or any(
        not isinstance(value, dict) for value in values
    ):
        raise ValueError("Variants must have been frozen in bundle")
    changed = {
        key for key in set(values[0]) | set(values[1]) if values[0].get(key) != values[1].get(key)
    }
    if changed != {factor}:
        raise ValueError("Change exactly one declared factor per experiment")
    result = {
        "schema": "audit-experiment-v1",
        "experiment_id": experiment_id,
        "declared_at": datetime.now(UTC).isoformat(),
        "bundle_id": bundle["bundle_id"],
        "factor": factor,
        "variants": copy.deepcopy(variants),
        "budget_usd": budget_usd,
        "goals": copy.deepcopy(goals),
        "minimum_repeats": minimum_repeats,
        "maximum_price_age_days": maximum_price_age_days,
        "gate_policy": copy.deepcopy(bundle["gate_policy"]),
    }
    result["declaration_hash"] = digest(result)
    return result


def ledger_metrics(
    ledger: list[dict],
    *,
    prices: dict,
    maximum_price_age_days: int,
    measured_at: datetime | None = None,
    run_wall_seconds: float | None = None,
) -> dict:
    """Count all POST attempts and retries once, preserving unknown billing exposure.

    output_tokens includes thinking_tokens, following provider total output usage;
    cached_input_tokens is a subset of input_tokens. Neither is added twice.
    Rebound cache rows refer to original_call_id and never create another bill.
    """
    measured_at = measured_at or datetime.now(UTC)
    if type(maximum_price_age_days) is not int or maximum_price_age_days < 0:
        raise ValueError("Maximum price age must be nonnegative")
    if run_wall_seconds is not None and (
        type(run_wall_seconds) not in {int, float}
        or not math.isfinite(run_wall_seconds)
        or run_wall_seconds < 0
    ):
        raise ValueError("Measured run wall time must be finite and nonnegative")
    if measured_at.tzinfo is None:
        raise ValueError("Measured timestamp needs timezone")
    if (
        prices.get("schema") != "eval-prices-v1"
        or prices.get("currency") != "USD"
        or not isinstance(prices.get("models"), dict)
    ):
        raise ValueError("Use a dated USD eval-prices-v1 snapshot")
    try:
        as_of = datetime.fromisoformat(prices["as_of"]).date()
    except (ValueError, TypeError, KeyError):
        raise ValueError("Price snapshot requires ISO as_of") from None
    age = (measured_at.date() - as_of).days
    fresh = 0 <= age <= maximum_price_age_days
    totals = {
        "input_tokens": 0,
        "output_tokens": 0,
        "thinking_tokens": 0,
        "cached_input_tokens": 0,
        "attempts": 0,
        "retries": 0,
        "uncertain_bills": 0,
        "cache_reuses": 0,
        "cost_usd": 0.0,
        "wall_seconds": 0.0,
    }
    seen = set()
    timestamps = []
    timestamps_complete = True
    for row in ledger:
        if (
            not isinstance(row, dict)
            or not isinstance(row.get("call_id"), str)
            or not row["call_id"]
            or row["call_id"] in seen
        ):
            raise ValueError("Each ledger attempt requires a unique call_id")
        seen.add(row["call_id"])
        if row.get("cache_reuse") is True:
            if not isinstance(row.get("original_call_id"), str) or not row["original_call_id"]:
                raise ValueError("Cache reuse requires original paid call provenance")
            totals["cache_reuses"] += 1
            continue
        totals["attempts"] += 1
        try:
            requested = datetime.fromisoformat(row["requested_at"].replace("Z", "+00:00"))
            if requested.tzinfo is None:
                raise ValueError("No timezone")
            timestamps.append(requested)
        except (KeyError, ValueError, TypeError, AttributeError):
            timestamps_complete = False
        attempt = row.get("attempt")
        if type(attempt) is not int or attempt < 1:
            raise ValueError("Ledger requires explicit 1-based attempt")
        totals["retries"] += attempt > 1
        elapsed = row.get("wall_seconds")
        if type(elapsed) not in {int, float} or not math.isfinite(elapsed) or elapsed < 0:
            raise ValueError("Ledger requires measured nonnegative wall time")
        totals["wall_seconds"] += elapsed
        usage = row.get("usage")
        price = prices["models"].get(row.get("model"))
        if not isinstance(usage, dict) or not isinstance(price, dict):
            totals["uncertain_bills"] += 1
            continue
        counts = {}
        valid = True
        for key in ("input_tokens", "output_tokens", "thinking_tokens", "cached_input_tokens"):
            counts[key] = usage.get(
                key, 0 if key in {"thinking_tokens", "cached_input_tokens"} else None
            )
            valid = valid and type(counts[key]) is int and counts[key] >= 0
        if (
            not valid
            or counts["cached_input_tokens"] > counts["input_tokens"]
            or counts["thinking_tokens"] > counts["output_tokens"]
        ):
            totals["uncertain_bills"] += 1
            continue
        for key, count in counts.items():
            totals[key] += count
        rates = {
            "input_per_million": price.get("input_per_million"),
            "output_per_million": price.get("output_per_million"),
            "cached_input_per_million": price.get("cached_input_per_million")
            if counts["cached_input_tokens"]
            else 0,
        }
        if any(
            type(value) not in {int, float} or not math.isfinite(value) or value < 0
            for value in rates.values()
        ):
            totals["uncertain_bills"] += 1
            continue
        totals["cost_usd"] += (
            (counts["input_tokens"] - counts["cached_input_tokens"]) * rates["input_per_million"]
            + counts["cached_input_tokens"] * rates["cached_input_per_million"]
            + counts["output_tokens"] * rates["output_per_million"]
        ) / 1_000_000
    totals["attempt_seconds"] = totals["wall_seconds"]
    totals["wall_seconds"] = run_wall_seconds
    return {
        **totals,
        "schema": "attempt-ledger-metrics-v1",
        "ledger_hash": digest(ledger),
        "attempt_timestamps_complete": timestamps_complete,
        "first_attempt_at": min(timestamps).isoformat() if timestamps else None,
        "last_attempt_at": max(timestamps).isoformat() if timestamps else None,
        "prices_as_of": prices["as_of"],
        "prices_hash": digest(prices),
        "prices_fresh": fresh,
        "price_age_days": age,
        "complete": totals["uncertain_bills"] == 0,
        "cost_scope": "All recorded attempts, including failed retries; provider tariff estimate.",
        "time_scope": "Measured run wall time; attempt_seconds separately includes retries.",
    }


def measure_experiment(
    bundle: dict, declaration: dict, *, runs: list[dict], exposure_log=None
) -> dict:
    if (
        declaration.get("schema") != "audit-experiment-v1"
        or declaration.get("bundle_id") != bundle["bundle_id"]
        or declaration.get("declaration_hash")
        != digest({k: v for k, v in declaration.items() if k != "declaration_hash"})
    ):
        raise ValueError("Experiment declaration changed or targets another bundle")
    by_variant = {key: [] for key in declaration["variants"]}
    seen = set()
    for run in runs:
        validate_run(bundle, run)
        if run["variant"] not in by_variant or run["run_id"] in seen:
            raise ValueError("Every experiment run must be independently identified")
        if datetime.fromisoformat(run["created_at"]) < datetime.fromisoformat(
            declaration["declared_at"]
        ):
            raise ValueError("Experiment must be declared before runs are recorded")
        seen.add(run["run_id"])
        by_variant[run["variant"]].append(run)
    variants = {}
    total_cost = 0.0
    complete = True
    for name, group in by_variant.items():
        cost = sum(run["operational"].get("cost_usd", 0) for run in group)
        times = [run["operational"].get("wall_seconds") for run in group]
        seconds = sum(times) if all(type(value) in {int, float} for value in times) else None
        usable = len(group) >= declaration["minimum_repeats"] and all(
            run["operational"].get("complete") is True
            and run["operational"].get("prices_fresh") is True
            and run["operational"].get("schema") == "attempt-ledger-metrics-v1"
            and run["operational"].get("price_age_days", math.inf)
            <= declaration["maximum_price_age_days"]
            and run["operational"].get("attempt_timestamps_complete") is True
            and isinstance(run["operational"].get("first_attempt_at"), str)
            and datetime.fromisoformat(run["operational"]["first_attempt_at"])
            >= datetime.fromisoformat(declaration["declared_at"])
            for run in group
        )
        complete &= usable
        total_cost += cost
        confirmed, actionable = 0, 0
        for run in group:
            for row in bundle["rows"]:
                answer = row["label"]
                pred = run["predictions"].get(row["job_uid"], {})
                retained = (
                    bool(pred)
                    and pred.get("decision") != "reject"
                    and pred.get("route")
                    not in {"suppressed", "OUT_OF_SCOPE", "out_of_scope", "title_out_of_family"}
                )
                relevant = type(answer.get("relevance")) is int and answer["relevance"] >= 2
                confirmed += retained and relevant
                actionable += (
                    retained
                    and relevant
                    and answer.get("gate_outcome") == "eligible"
                    and pred.get("gate_outcome") == "eligible"
                    and pred.get("decision") == "pass"
                    and pred.get("tier") in {"A", "B"}
                )
        variants[name] = {
            "runs": len(group),
            "run_ids": [run["run_id"] for run in group],
            "cost_usd": cost,
            "wall_seconds": seconds,
            "confirmed_relevant_results": confirmed,
            "confirmed_actionable_results": actionable,
            "cost_per_confirmed_relevant": cost / confirmed if confirmed else None,
            "cost_per_confirmed_actionable": cost / actionable if actionable else None,
            "seconds_per_confirmed_relevant": seconds / confirmed
            if confirmed and seconds is not None
            else None,
            "status": "measured" if usable else "insufficient_evidence",
        }
    old, new = list(by_variant.values())
    comparisons = [
        compare_runs(
            bundle,
            baseline,
            candidate,
            repeat=new[(index + 1) % len(new)] if len(new) > 1 else None,
            exposure_log=exposure_log,
        )
        for baseline in old
        for index, candidate in enumerate(new)
    ]
    comparison = comparisons[0] if comparisons else None
    comparison_status = (
        "fail"
        if any(row["status"] == "fail" for row in comparisons)
        else "insufficient_evidence"
        if any(row["status"] == "insufficient_evidence" for row in comparisons)
        else "pass"
    )
    status = (
        "budget_exceeded"
        if total_cost > declaration["budget_usd"]
        else "insufficient_evidence"
        if not complete or comparison is None
        else comparison_status
    )
    return {
        "schema": "audit-experiment-result-v1",
        "experiment_id": declaration["experiment_id"],
        "bundle_id": bundle["bundle_id"],
        "declaration_hash": declaration["declaration_hash"],
        "status": status,
        "total_cost_usd": total_cost,
        "budget_usd": declaration["budget_usd"],
        "variants": variants,
        "comparison": comparison,
        "all_run_comparisons": comparisons,
        "limitations": [
            "Model agreement is not human quality.",
            "Variant savings cannot be added without a joint experiment.",
            "Small samples retain Wilson intervals and may not pass quality gates.",
        ],
    }
