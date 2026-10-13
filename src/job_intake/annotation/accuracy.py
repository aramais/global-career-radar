"""Vacancy-level saved baseline without API calls or database writes.

Unknown predictions are abstentions, not correct unspecified values. Historical
token estimates and latest-run log telemetry have separate scopes; neither is a bill.
"""

from __future__ import annotations

import math
import os
import re
import sqlite3
import tempfile
import unicodedata
from collections import Counter, defaultdict
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import quote

from job_intake.annotation.benchmark import read_jsonl
from job_intake.annotation.eval_runner import (
    evaluate_log,
    read_saved_records,
    sqlite_path,
    write_report,
)
from job_intake.annotation.schema import _language_entities
from job_intake.annotation.text import TEXT_VERSION, source_hash
from job_intake.config.settings import AppConfig, load_yaml_mapping
from job_intake.models.job import JobRecord, JobStatus
from job_intake.profiles import load_streams

ACCURACY_VERSION = "vacancy-accuracy-v1"
_SCOPE_MAP = {
    "project_management": "project_management",
    "people_interaction": "people_interaction",
    "decision_influence": "decision_influence",
    "business_economics": "business_economics",
    "executive_finance_partnership": "finance_gm_partnership",
    "causal_inference": "causal_experimentation",
}
_NEGATIVES = {
    "hiring_geography": {"excludes_brazil", "restricted_elsewhere"},
    "working_language": {"other_language_required"},
    "work_authorization": {"country_required"},
    "work_mode": {"onsite", "hybrid"},
    "vacancy_closed": {"true"},
}
_NUMBER = r"[0-9]{1,12}(?:\.[0-9]{1,6})?"
_MISSING = "__abstained__"


def proportion(numerator: int, denominator: int) -> dict:
    """A binomial proportion with a two-sided 95% Wilson interval."""
    if not 0 <= numerator <= denominator:
        raise ValueError("Invalid proportion counts")
    if not denominator:
        return {"value": None, "numerator": numerator, "denominator": denominator, "ci95": None}
    z = 1.959963984540054
    value = numerator / denominator
    divisor = 1 + z * z / denominator
    centre = (value + z * z / (2 * denominator)) / divisor
    half = z * math.sqrt(value * (1 - value) / denominator + z * z / (4 * denominator**2))
    half /= divisor
    return {
        "value": round(value, 6),
        "numerator": numerator,
        "denominator": denominator,
        "ci95": [round(max(0, centre - half), 6), round(min(1, centre + half), 6)],
    }


def _usable_claim(row: object) -> bool:
    return (
        isinstance(row, dict)
        and row.get("review_status") == "SUPPORTED"
        and not row.get("is_inference")
        and not row.get("evidence_warnings")
        and isinstance(row.get("value"), str)
    )


def adapt_saved_record(record: JobRecord) -> dict:
    """Loss-aware map of saved legacy facts; never rerun policy or infer from titles."""
    from job_intake.annotation.gold import TAG_VALUES

    annotation = record.annotation
    tags = dict.fromkeys(TAG_VALUES, None)
    summary = annotation.get("summary", {})
    summary = summary if isinstance(summary, dict) else {}
    focus = summary.get("search_focus", {})
    focus = focus if isinstance(focus, dict) else {}
    dimensions = focus.get("dimensions", {})
    dimensions = dimensions if isinstance(dimensions, dict) else {}
    candidates: dict[str, set[str]] = defaultdict(set)
    unknowns = summary.get("unknowns", [])
    if isinstance(unknowns, list):
        for legacy, name in (
            ("hiring_location", "hiring_geography"),
            ("work_mode", "work_mode"),
            ("working_language", "working_language"),
            ("employment", "employment_type"),
            ("work_authorization", "work_authorization"),
        ):
            if legacy in unknowns:
                tags[name] = "unspecified"
    for legacy, name in _SCOPE_MAP.items():
        dimension = dimensions.get(legacy, {})
        status = dimension.get("status") if isinstance(dimension, dict) else None
        value = {
            "confirmed": "duty",
            "explicitly_absent": "explicitly_absent",
            "unknown": "unspecified",
        }.get(status)
        if value in TAG_VALUES[name]:
            tags[name] = value
            candidates[name].add(value)
    for legacy, name, mapping in (
        ("working_english", "working_language", {"confirmed": "english_required"}),
        (
            "direct_reports",
            "direct_reports",
            {"confirmed": "yes", "explicitly_absent": "explicitly_none"},
        ),
        (
            "isolated_analysis",
            "isolated_analysis",
            {"confirmed": "explicit", "explicitly_absent": "not_indicated"},
        ),
    ):
        dimension = dimensions.get(legacy, {})
        value = mapping.get(dimension.get("status")) if isinstance(dimension, dict) else None
        if value:
            candidates[name].add(value)
    claims = annotation.get("claims", summary.get("knowns", []))
    if not isinstance(claims, list):
        claims = []
    for claim in filter(_usable_claim, claims):
        kind, text = claim.get("kind"), claim["value"].casefold()
        negative = claim.get("polarity") == "negative"
        requirement = claim.get("requirement")
        if kind in _SCOPE_MAP and not negative and claim.get("source_section") == "requirements":
            candidates[_SCOPE_MAP[kind]].discard("unspecified")
            candidates[_SCOPE_MAP[kind]].add("skill_only")
        if kind == "hiring_location":
            brazil = bool(re.search(r"\b(?:brazil|brasil)\b", text))
            if negative and brazil:
                candidates["hiring_geography"].add("excludes_brazil")
            elif not negative and brazil and requirement in {"required", "informational"}:
                candidates["hiring_geography"].add("brazil_explicit")
            elif (
                not negative
                and requirement in {"required", "informational"}
                and re.search(
                    r"\b(?:latam|latin america|south america|worldwide|global)\b",
                    text,
                )
            ):
                candidates["hiring_geography"].add("includes_brazil_region")
            elif (
                not negative
                and requirement == "required"
                and re.search(
                    r"\b(?:united states|u\.?s\.?|usa|canada|united kingdom|uk|europe|"
                    r"european union|eu)\b",
                    text,
                )
            ):
                candidates["hiring_geography"].add("restricted_elsewhere")
        elif kind == "working_language" and not negative:
            languages = _language_entities(text)
            if "english" in languages and requirement in {
                "required",
                "informational",
                "preferred",
                "not_required",
            }:
                candidates["working_language"].add(
                    "english_optional"
                    if requirement in {"preferred", "not_required"}
                    else "english_required"
                )
            if languages - {"english"} and requirement == "required":
                candidates["working_language"].add("other_language_required")
        elif kind == "work_mode" and not negative and requirement in {"required", "informational"}:
            remote = bool(re.search(r"\bremote(?:ly)?\b", text))
            hybrid = bool(re.search(r"\bhybrid\b", text))
            onsite = bool(re.search(r"\bon[ -]?site\b|\bin[ -]office\b", text))
            if remote or hybrid or onsite:
                candidates["work_mode"].add(
                    "remote_or_hybrid"
                    if remote and hybrid
                    else "remote"
                    if remote
                    else "hybrid"
                    if hybrid
                    else "onsite"
                )
        elif kind == "work_authorization":
            if requirement == "not_required":
                candidates["work_authorization"].add("not_required")
            elif not negative and requirement == "required":
                candidates["work_authorization"].add("country_required")
        elif kind == "employment" and not negative:
            for pattern, value in (
                (r"\bfull[ -]?time\b", "full_time"),
                (r"\bpart[ -]?time\b", "part_time"),
                (r"\bcontract(?:or)?\b", "contract"),
            ):
                if re.search(pattern, text):
                    candidates["employment_type"].add(value)
        elif (
            kind == "status"
            and not negative
            and re.search(
                r"\bclosed\b|\bfilled\b|no longer accepting|no longer available",
                text,
            )
        ):
            candidates["vacancy_closed"].add("true")
        elif (
            kind in {"role", "responsibility"}
            and not negative
            and claim.get("source_section") == "role"
        ):
            for pattern, value in (
                (r"\bproduct\b", "product"),
                (r"\banalytics\b", "analytics"),
                (r"\bdata science\b", "data_science"),
                (r"\b(?:engineer|engineering|developer)\b", "engineering"),
                (r"\bdesign(?:er)?\b", "design"),
            ):
                if re.search(pattern, text):
                    candidates["role_family"].add(value)
    if record.status == JobStatus.CLOSED:
        candidates["vacancy_closed"].add("true")
    elif record.status == JobStatus.OPEN:
        candidates["vacancy_closed"].add("false")
    conflicts = []
    for name, values in candidates.items():
        values.discard("unspecified")
        if "duty" in values:
            values.discard("skill_only")
        if (
            name == "hiring_geography"
            and values <= {"brazil_explicit", "includes_brazil_region"}
            and "brazil_explicit" in values
        ):
            values = {"brazil_explicit"}
        if (
            name == "work_mode"
            and values <= {"remote", "hybrid", "remote_or_hybrid"}
            and ("remote_or_hybrid" in values or {"remote", "hybrid"} <= values)
        ):
            values = {"remote_or_hybrid"}
        if len(values) == 1 and next(iter(values)) in TAG_VALUES[name]:
            tags[name] = next(iter(values))
        elif len(values) > 1:
            tags[name] = None
            conflicts.append(name)
    prefilter = annotation.get("prefilter", {})
    prefilter = prefilter if isinstance(prefilter, dict) else {}
    route = prefilter.get("routing")
    return {
        "tags": tags,
        "route": "OUT_OF_SCOPE" if route == "suppressed" else route,
        "adapter_conflicts": sorted(conflicts),
        "unmapped_tags": sorted(name for name, value in tags.items() if value is None),
    }


def _decision_key(row: dict) -> tuple:
    return (
        {"A": 0, "B": 1, "C": 2}.get(row.get("tier"), 3),
        {"pass": 0, "review": 1, "reject": 2}.get(row.get("decision"), 3),
        -row["fit_score"],
        row.get("profile_id") or "",
    )


def read_saved_predictions(
    database_url: str,
    rows: list[tuple[str, JobRecord]],
    *,
    profile_versions: dict[str, str] | None = None,
) -> dict[str, dict]:
    """Read decisions by SELECT on the existing DB; never initialize an ORM service."""
    path = sqlite_path(database_url)
    connection = sqlite3.connect(f"file:{quote(str(path), safe='/')}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    result = {}
    try:
        connection.execute("PRAGMA query_only=ON")
        job_columns = {row["name"] for row in connection.execute("PRAGMA table_info(jobs)")}
        profile_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(job_profile_evaluations)")
        }
        fields = {
            "job_uid",
            "profile_id",
            "profile_version",
            "content_hash",
            "decision",
            "tier",
            "fit_score",
        }
        for uid, record in rows:
            prediction = {"job_uid": uid, **adapt_saved_record(record)}
            metadata = connection.execute("SELECT * FROM jobs WHERE job_uid = ?", (uid,)).fetchone()
            saved = []
            if profile_columns >= fields:
                saved = [
                    dict(row)
                    for row in connection.execute(
                        "SELECT job_uid, profile_id, profile_version, content_hash, "
                        "decision, tier, fit_score FROM job_profile_evaluations "
                        "WHERE job_uid = ? ORDER BY profile_id",
                        (uid,),
                    )
                ]
                if profile_versions is not None:
                    saved = [row for row in saved if row["profile_id"] in profile_versions]
            elif {"filter_decision", "tier", "fit_score"} <= job_columns:
                saved = [
                    {
                        "decision": metadata["filter_decision"],
                        "tier": metadata["tier"],
                        "fit_score": metadata["fit_score"],
                        "profile_id": None,
                    }
                ]
                prediction["decision_source"] = "legacy_jobs_fallback"
            saved = [
                row
                for row in saved
                if row.get("decision") in {"pass", "review", "reject"}
                and row.get("tier") in {"A", "B", "C"}
                and type(row.get("fit_score")) in {int, float}
                and math.isfinite(row["fit_score"])
            ]
            stale_content = [
                row["profile_id"]
                for row in saved
                if "content_hash" in job_columns
                and row.get("content_hash") is not None
                and row["content_hash"] != metadata["content_hash"]
            ]
            stale_profiles = [
                row["profile_id"]
                for row in saved
                if profile_versions is not None
                and row.get("profile_version") is not None
                and row["profile_version"] != profile_versions.get(row["profile_id"])
            ]
            saved = [row for row in saved if row.get("profile_id") not in stale_content]
            best = min(saved, key=_decision_key) if saved else {}
            source_current = (
                record.annotation.get("source_hash") == source_hash(record)
                if record.annotation.get("source_hash")
                else None
            )
            prediction.update(
                decision=best.get("decision"),
                tier=best.get("tier"),
                fit_score=best.get("fit_score"),
                profile_id=best.get("profile_id"),
                profile_evaluations=saved,
                source_current=source_current,
                stale_content_profiles=stale_content,
                stale_profiles=stale_profiles,
                current_eligible=(
                    bool(best)
                    and not stale_profiles
                    and not stale_content
                    and source_current is not False
                )
                if profile_versions is not None and "content_hash" in job_columns
                else None,
                decision_source=prediction.get("decision_source", "saved_profiles"),
            )
            if source_current is False:
                prediction["tags"] = dict.fromkeys(prediction["tags"], None)
                prediction["route"] = None
            result[uid] = prediction
    finally:
        connection.close()
    return result


def _value(tag: object) -> str | None:
    value = tag.get("value") if isinstance(tag, dict) else tag
    return value if isinstance(value, str) else None


def _relevance(case: dict) -> int | None:
    values = case.get("relevance")
    if (
        not isinstance(values, dict)
        or not values
        or any(type(v) is not int for v in values.values())
    ):
        return None
    return max(values.values())


def evaluate_vacancies(cases: list[dict], predictions: dict[str, dict]) -> dict:
    """Evaluate all labels; missing outputs stay in recall and tag denominators."""
    from job_intake.annotation.gold import TAG_VALUES

    labeled = [
        case
        for case in cases
        if _relevance(case) is not None
        and case.get("gate_outcome") in {"eligible", "ineligible", "unknown"}
    ]
    relevant = [case for case in labeled if _relevance(case) >= 2]
    eligible = [case for case in labeled if case.get("gate_outcome") == "eligible"]

    def prediction(case: dict) -> dict:
        return predictions.get(case["job_uid"], {})

    def excluded(case: dict) -> bool:
        row = prediction(case)
        return row.get("decision") == "reject" or row.get("route") in {
            "suppressed",
            "OUT_OF_SCOPE",
            "out_of_scope",
            "title_out_of_family",
        }

    def shortlisted(case: dict, tiers: set[str]) -> bool:
        return prediction(case).get("tier") in tiers

    metrics = {
        "false_exclusion_rate": proportion(sum(map(excluded, relevant)), len(relevant)),
        "relevant_recall_AB": proportion(
            sum(shortlisted(case, {"A", "B"}) for case in relevant),
            len(relevant),
        ),
        "wrong_block_rate": proportion(
            sum(prediction(case).get("decision") == "reject" for case in eligible),
            len(eligible),
        ),
    }
    for name in ("false_exclusion_rate", "wrong_block_rate"):
        metrics[name]["note"] = (
            "Observed exclusion lower bound if decision/route predictions are missing; "
            "consult prediction coverage before interpreting."
        )
    for name, tiers in (("precision_A", {"A"}), ("precision_AB", {"A", "B"})):
        selected = [case for case in labeled if shortlisted(case, tiers)]
        metrics[name] = proportion(sum(_relevance(case) >= 2 for case in selected), len(selected))
    rankable = [
        case
        for case in labeled
        if prediction(case).get("tier") in {"A", "B", "C"}
        and type(prediction(case).get("fit_score")) in {int, float}
    ]
    ranked = sorted(
        rankable,
        key=lambda case: (
            {"A": 0, "B": 1, "C": 2}[prediction(case)["tier"]],
            -prediction(case)["fit_score"],
            case["job_uid"],
        ),
    )

    def dcg(values: list[int]) -> float:
        return sum((2**value - 1) / math.log2(index + 2) for index, value in enumerate(values[:20]))

    actual = dcg([_relevance(case) for case in ranked])
    ideal = dcg(sorted((_relevance(case) for case in labeled), reverse=True))
    metrics["ndcg@20"] = {
        "value": round(actual / ideal, 6) if ideal else None,
        "numerator": actual,
        "denominator": ideal,
        "ci95": None,
        "note": "Graded ranking ratio, not binomial; missing ranks contribute no gain.",
    }
    tag_metrics = {}
    aggregate_negative = Counter()
    for name in TAG_VALUES:
        pairs = [
            (
                _value(case.get("tags", {}).get(name)),
                _value(prediction(case).get("tags", {}).get(name)),
            )
            for case in cases
        ]
        pairs = [(gold, predicted) for gold, predicted in pairs if gold is not None]
        confusion = Counter(
            (gold, predicted if predicted is not None else _MISSING) for gold, predicted in pairs
        )
        classes = sorted(
            {gold for gold, _ in pairs}
            | {predicted for _, predicted in pairs if predicted is not None}
        )
        scores = {}
        for value in classes:
            tp = sum(gold == value and predicted == value for gold, predicted in pairs)
            fp = sum(gold != value and predicted == value for gold, predicted in pairs)
            fn = sum(gold == value and predicted != value for gold, predicted in pairs)
            score = proportion(2 * tp, 2 * tp + fp + fn)
            score["note"] = (
                "F1 derived counts; Wilson interval is descriptive, not independent-trials CI."
            )
            scores[value] = score
        f1s = [score["value"] for score in scores.values() if score["value"] is not None]
        row = {
            "accuracy": proportion(sum(gold == predicted for gold, predicted in pairs), len(pairs)),
            "prediction_coverage": proportion(
                sum(predicted is not None for _, predicted in pairs), len(pairs)
            ),
            "macro_f1": {
                "value": round(sum(f1s) / len(f1s), 6) if f1s else None,
                "numerator": None,
                "denominator": None,
                "ci95": None,
                "classes": len(f1s),
                "note": "Mean per-class F1; not a binomial proportion.",
            },
            "class_f1": scores,
            "confusion": {
                gold: {
                    predicted: count
                    for (truth, predicted), count in sorted(confusion.items())
                    if truth == gold
                }
                for gold in sorted({gold for gold, _ in pairs})
            },
        }
        if name in _NEGATIVES:
            negatives = _NEGATIVES[name]
            tp = sum(gold in negatives and predicted in negatives for gold, predicted in pairs)
            actual_negative = sum(gold in negatives for gold, _ in pairs)
            predicted_negative = sum(predicted in negatives for _, predicted in pairs)
            row["negative_recall"] = proportion(tp, actual_negative)
            row["negative_precision"] = proportion(tp, predicted_negative)
            if name == "work_mode":
                row["negative_note"] = (
                    "Onsite/hybrid are restriction signals; tag accuracy does not determine "
                    "office compatibility. São Paulo may remain eligible."
                )
            aggregate_negative.update(tp=tp, actual=actual_negative, predicted=predicted_negative)
        tag_metrics[name] = row
    complete = sum(
        prediction(case).get("decision") in {"pass", "review", "reject"}
        and prediction(case).get("tier") in {"A", "B", "C"}
        for case in labeled
    )
    return {
        "counts": {
            "gold_rows": len(cases),
            "decision_labeled": len(labeled),
            "decision_unlabeled": len(cases) - len(labeled),
            "relevant": len(relevant),
            "eligible": len(eligible),
            "ranked": len(rankable),
        },
        "decision_prediction_coverage": proportion(complete, len(labeled)),
        "metrics": metrics,
        "tags": tag_metrics,
        "negative_restrictions": {
            "recall": proportion(aggregate_negative["tp"], aggregate_negative["actual"]),
            "precision": proportion(aggregate_negative["tp"], aggregate_negative["predicted"]),
        },
        "ranking": [case["job_uid"] for case in ranked[:20]],
    }


def compare_stability(first: dict[str, dict], second: dict[str, dict]) -> dict:
    """Cohen's kappa for comparable nonmissing tags across two prediction maps.

    A report caller must establish independent runs and matching source provenance.
    Single-category agreement has undefined kappa and cannot pass a stability gate.
    """
    from job_intake.annotation.gold import TAG_VALUES

    uids = sorted(set(first) | set(second))
    results = {}
    for name in TAG_VALUES:
        pairs = [
            (
                _value(first.get(uid, {}).get("tags", {}).get(name)),
                _value(second.get(uid, {}).get("tags", {}).get(name)),
            )
            for uid in uids
        ]
        paired = [(a, b) for a, b in pairs if a is not None and b is not None]
        total = len(paired)
        agreement = proportion(sum(a == b for a, b in paired), total)
        left = Counter(a for a, _ in paired)
        right = Counter(b for _, b in paired)
        expected = sum(left[value] * right[value] for value in set(left) | set(right))
        expected = expected / total**2 if total else None
        observed = sum(a == b for a, b in paired) / total if total else None
        kappa = (
            (observed - expected) / (1 - expected)
            if expected is not None and expected < 1
            else None
        )
        results[name] = {
            "kappa": round(kappa, 6) if kappa is not None else None,
            "paired": total,
            "missing": len(uids) - total,
            "agreement": agreement,
            "sample_ready": total >= 30,
        }
    return {
        "scope": "paired_prediction_tags",
        "jobs": len(uids),
        "tags": results,
        "independent_runs_confirmed": None,
        "source_provenance_confirmed": None,
        "acceptance_passed": None,
        "limitations": [
            "At least 30 comparable vacancies and independent runs are required.",
            "Saved report comparisons alone do not establish model repeatability.",
        ],
    }


def load_prices(path: Path) -> dict:
    data = load_yaml_mapping(path)
    if data.get("schema") != "eval-prices-v1" or data.get("currency") != "USD":
        raise ValueError("Unsupported eval price schema or currency")
    try:
        date.fromisoformat(data["as_of"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("Eval prices require an ISO as_of date") from None
    models = data.get("models")
    if not isinstance(models, dict):
        raise ValueError("Eval prices require a model table")
    for model, price in models.items():
        if (
            not isinstance(model, str)
            or not isinstance(price, dict)
            or any(
                type(price.get(key)) not in {int, float}
                or not math.isfinite(price[key])
                or price[key] < 0
                for key in ("input_per_million", "output_per_million")
            )
        ):
            raise ValueError("Invalid model tariff")
    return data


def _usage_rows(record: JobRecord) -> tuple[list[dict], bool]:
    containers = [record.annotation]
    if isinstance(record.annotation.get("prefilter"), dict):
        containers.append(record.annotation["prefilter"])
    values, known = [], True
    for container in containers:
        usage = container.get("usage")
        if isinstance(usage, list):
            known &= all(isinstance(row, dict) for row in usage)
            values.extend(row for row in usage if isinstance(row, dict))
        else:
            known = False
    return values, known


def _distribution(values: list[float]) -> dict:
    values = sorted(values)

    def percentile(p: float) -> float | None:
        if not values:
            return None
        position = (len(values) - 1) * p
        lo, hi = math.floor(position), math.ceil(position)
        return round(values[lo] + (values[hi] - values[lo]) * (position - lo), 6)

    return {"p50": percentile(0.5), "p95": percentile(0.95), "samples": len(values)}


def operational_metrics(
    rows: list[tuple[str, JobRecord]], prices: dict, *, log: Path | None = None
) -> dict:
    totals = Counter()
    costs, stage_latency, vacancy_latency = [], defaultdict(list), []
    for stage in ("extract", "review", "screen", "verify"):
        stage_latency[stage] = []
    known_cost_total = 0.0
    unknown_models = set()
    cached = observed_cache = 0
    for _, record in rows:
        usage, known = _usage_rows(record)
        totals["jobs_with_usage"] += known
        cost, cost_known = 0.0, known and bool(usage)
        for event in usage:
            totals["usage_records"] += 1
            price = prices["models"].get(event.get("model"))
            complete = event.get("usage_known") is not False and all(
                type(event.get(key)) is int and event[key] >= 0
                for key in ("input_tokens", "output_tokens")
            )
            if complete and price:
                event_cost = (
                    event["input_tokens"] * price["input_per_million"]
                    + event["output_tokens"] * price["output_per_million"]
                ) / 1_000_000
                cost += event_cost
                known_cost_total += event_cost
                totals["priced_usage_records"] += 1
            else:
                cost_known = False
                if not price:
                    unknown_models.add(str(event.get("model", "unknown")))
            if type(event.get("cached")) is bool:
                observed_cache += 1
                cached += event["cached"]
            elapsed = event.get("latency_ms")
            if type(elapsed) in {int, float} and math.isfinite(elapsed) and elapsed >= 0:
                stage_latency[str(event.get("stage", "unknown"))].append(elapsed / 1000)
        containers = [record.annotation, record.annotation.get("prefilter", {})]
        unknown_errors = any(
            isinstance(container, dict)
            and isinstance(container.get("errors"), list)
            and any(
                not isinstance(error, dict) or "usage" not in error for error in container["errors"]
            )
            for container in containers
        )
        cost_known &= not unknown_errors
        if cost_known:
            costs.append(cost)
        elapsed = record.annotation.get("latency_ms")
        if type(elapsed) in {int, float} and math.isfinite(elapsed) and elapsed >= 0:
            vacancy_latency.append(elapsed / 1000)
    result = {
        "scope": "saved_usage_historical_estimate",
        "cost_completeness_scope": "saved_usage_rows_only",
        "provider_bill_complete": None,
        "currency": "USD",
        "price_as_of": prices["as_of"],
        "price_source": prices.get("source"),
        "cost_per_vacancy": round(sum(costs) / len(costs), 8) if costs else None,
        "cost_known_jobs": len(costs),
        "cost_unknown_jobs": len(rows) - len(costs),
        "known_cost_total": round(known_cost_total, 8) if totals["priced_usage_records"] else None,
        "usage_records": totals["usage_records"],
        "priced_usage_records": totals["priced_usage_records"],
        "unknown_models": sorted(unknown_models),
        "vacancy_latency_seconds": _distribution(vacancy_latency),
        "stage_latency_seconds": {
            stage: _distribution(values) for stage, values in sorted(stage_latency.items())
        },
        "requests_per_vacancy": None,
        "error_rate": proportion(0, 0),
        "timeout_rate": proportion(0, 0),
        "cache_hit_rate": proportion(cached, observed_cache),
        "cache_telemetry_coverage": proportion(observed_cache, totals["usage_records"]),
        "s4_verification_rate": proportion(0, 0),
        "throughput_vacancies_per_minute": None,
        "limitations": [
            "Saved usage does not establish fresh API attempts, retries, cache hits or wall time.",
            "Dated Standard prices omit cache/Batch discounts, gateways and taxes.",
            "Failed-attempt usage can be unknown; known cost is not a provider bill.",
        ],
    }
    if log:
        result["latest_run"] = _log_operational(log)
    return result


def _log_operational(path: Path) -> dict:
    """Only bounded numeric metadata from the latest run; never expose raw log content."""
    summary = evaluate_log(path)
    content = path.read_text(encoding="utf-8")
    starts = list(re.finditer(r"^.*Starting annotation \(", content, re.MULTILINE))
    latest = content[starts[-1].start() :] if starts else content
    vacancy_latency, stages, times = [], defaultdict(list), []
    for stage in ("extract", "review", "prefilter screen", "prefilter verify"):
        stages[stage] = []
    attempt_codes = Counter()
    attempts = 0
    for line in latest.splitlines():
        timed = re.match(rf"^\[({_NUMBER})s\] ", line)
        if timed:
            times.append(float(timed[1]))
        clean = re.sub(rf"^\[({_NUMBER})s\] ", "", line)
        match = re.fullmatch(
            rf"Job [0-9]+/[0-9]+: saved [0-9]+ profile evaluations in ({_NUMBER})s; "
            r"annotation errors=[0-9]+(?:, prefilter errors=[0-9]+)?\.",
            clean,
        )
        if match:
            vacancy_latency.append(float(match[1]))
        match = re.search(
            rf": (extract|review|prefilter screen|prefilter verify|model) request "
            rf"(?:completed|failed) in ({_NUMBER})s[;.]",
            clean,
        )
        if match:
            stages[match[1]].append(float(match[2]))
        if re.search(
            r"(?:^|: )(?:extract|review|prefilter screen|prefilter verify|model) "
            r"request attempt [0-9]+/[0-9]+ ",
            clean,
        ):
            attempts += 1
        if " request failed " in clean:
            code = re.search(r"code=([A-Za-z_]+)", clean)
            attempt_codes[code[1] if code else "unknown"] += 1
    jobs = summary["progress"]["jobs_started"]
    completed = summary.get("summary", {})
    cache_hits = sum(
        completed.get(name, 0) for name in ("annotation_cache_hits", "prefilter_cache_hits")
    )
    fresh_calls = sum(
        completed.get(name, 0)
        for name in ("extraction_calls", "review_calls", "screen_calls", "verify_calls")
    )
    failed = sum(attempt_codes.values())
    request_events_complete = attempts > 0 and (
        summary["request_counts"]["completed"] + failed == attempts
    )
    result = {
        "scope": "latest_annotation_run_log",
        "complete": summary["complete"],
        "requests": attempts if attempts else None,
        "requests_per_vacancy": round(attempts / jobs, 6) if attempts and jobs else None,
        "error_rate": proportion(failed, attempts) if request_events_complete else proportion(0, 0),
        "timeout_rate": proportion(
            attempt_codes["request_timeout"] + attempt_codes["timeout"],
            attempts,
        )
        if request_events_complete
        else proportion(0, 0),
        "cache_hit_rate": proportion(cache_hits, cache_hits + fresh_calls)
        if completed
        else proportion(0, 0),
        "vacancy_latency_seconds": _distribution(vacancy_latency),
        "stage_latency_seconds": {
            stage: _distribution(values) for stage, values in sorted(stages.items())
        },
        "throughput_vacancies_per_minute": round(
            summary["progress"]["jobs_saved"] * 60 / (max(times) - min(times)),
            6,
        )
        if summary["complete"] and len(times) >= 2 and max(times) > min(times)
        else None,
        "concurrency": None,
        "usage": summary,
        "limitations": [
            "Log job indices are not job_uid; run telemetry is not joined to gold.",
            "Concurrency is not recorded; throughput cannot prove the concurrency-6 gate.",
            "Cache ratio is a stage-level estimate; final-run cache summary counts vary by stage.",
        ],
    }
    if attempt_codes.get("unknown"):
        result["timeout_rate"] = proportion(0, 0)
        result["limitations"].append("Some failed attempts lack codes; timeout share is unknown.")
    return result


def readiness_report(evaluation: dict) -> dict:
    counts = evaluation["counts"]
    ready = counts["decision_labeled"] >= 160 and counts["relevant"] >= 40
    coverage = evaluation["decision_prediction_coverage"]
    return {
        "status": "baseline_measured" if ready else "pending_human_labels",
        "gold_ready": ready,
        "minimum_labeled": 160,
        "minimum_relevant": 40,
        "baseline_measured": bool(counts["decision_labeled"]),
        "prediction_complete": coverage["denominator"] > 0
        and coverage["numerator"] == coverage["denominator"],
        "acceptance_passed": None,
        "default_mode_change_authorized": False,
        "gates": [
            {"name": name, "passed": None}
            for name in (
                "decision_accuracy_vs_baseline",
                "negative_restrictions",
                "scope_vs_baseline",
                "speed_at_concurrency_6",
                "current_price_cost",
                "repeat_run_stability",
            )
        ],
        "limitations": [
            "A saved baseline cannot establish comparative acceptance of a new variant.",
            "Human labels, both variants, current prices and a repeat run are required.",
        ]
        + (
            ["Gold needs at least 160 decision labels and 40 relevant vacancies."]
            if not ready
            else []
        ),
    }


def _parser_only_drift(case: dict, record: JobRecord) -> bool:
    """Require a complete source snapshot before trusting version/layout-only drift."""
    from job_intake.annotation.gold import _snapshot

    if case.get("text_version") == TEXT_VERSION:
        return False
    snapshot = case.get("source")
    current = _snapshot(record)
    if not isinstance(snapshot, dict) or set(snapshot) != set(current):
        return False
    if any(snapshot[field] != current[field] for field in current if field != "description_clean"):
        return False
    if current["description_raw"]:
        # Unchanged original HTML is the source of truth; clean layout is derived.
        return True

    def layout_text(value: str) -> str:
        return " ".join(unicodedata.normalize("NFC", value).replace("•", " ").split())

    return layout_text(snapshot.get("description_clean") or "") == layout_text(
        current["description_clean"] or ""
    )


def run_accuracy(
    config: AppConfig,
    *,
    gold: Path,
    variant: str = "saved",
    prices: Path | None = None,
    log: Path | None = None,
    output: Path | None = None,
) -> dict:
    from job_intake.annotation.gold import validate_gold

    if variant != "saved":
        raise ValueError(
            "Only the saved accuracy variant is implemented in Phase 0; no AI calls are made"
        )
    cases = read_jsonl(gold)
    if any(not isinstance(case.get("job_uid"), str) for case in cases):
        raise ValueError("Vacancy gold rows require job_uid")
    uids = [case["job_uid"] for case in cases]
    if not uids:
        raise ValueError("Gold is empty")
    database, rows, problems = read_saved_records(config.database_url, limit=None, job_uids=uids)
    versions = None
    if config.rules_path.is_file() and config.search_profiles_path.is_file():
        versions = {
            stream.id: stream.version
            for stream in load_streams(
                load_yaml_mapping(config.rules_path), load_yaml_mapping(config.search_profiles_path)
            )
        }
    validated = validate_gold(
        rows,
        cases,
        profile_ids=list(versions) if versions is not None else None,
    )
    if not validated["valid"]:
        raise ValueError(
            "Invalid vacancy gold: " + "; ".join(str(error) for error in validated["errors"])
        )
    records_by_uid = dict(rows)
    parser_only = {
        case["job_uid"]
        for case in validated["rows"]
        if _parser_only_drift(case, records_by_uid[case["job_uid"]])
    }
    drift_uids = {
        warning["job_uid"]
        for warning in validated["warnings"]
        if warning.get("code") == "source_hash_changed"
    } - parser_only
    predictions = read_saved_predictions(config.database_url, rows, profile_versions=versions)
    for uid, findings in problems.items():
        if any(finding["severity"] == "error" for finding in findings):
            predictions[uid]["tags"] = dict.fromkeys(predictions[uid]["tags"], None)
            predictions[uid]["route"] = None
    price_path = prices or config.rules_path.parent / "eval_prices.yaml"
    price_config = load_prices(price_path)
    evaluation_cases = [
        {**case, "relevance": None, "gate_outcome": None, "tags": {}}
        if case["job_uid"] in drift_uids
        else case
        for case in validated["rows"]
    ]
    evaluation = evaluate_vacancies(evaluation_cases, predictions)
    evaluation["counts"]["source_drift_withheld"] = len(drift_uids)
    evaluation["counts"]["parser_only_rebound"] = len(parser_only)
    report = {
        "version": ACCURACY_VERSION,
        "variant": variant,
        "scope": "vacancy_level_saved_baseline",
        "generated_at": datetime.now(UTC).isoformat(),
        "database": str(database),
        "database_access": "read_only",
        "api_calls": 0,
        "gold": {"path": str(gold.resolve()), "validation": validated},
        "selection": {"job_uids": uids, "archived": False},
        "evaluation": evaluation,
        "operational": operational_metrics(rows, price_config, log=log),
        "readiness": readiness_report(evaluation),
        "stability": {
            "measured": False,
            "kappa": None,
            "note": "A second independent run on at least 30 vacancies is required.",
        },
        "predictions": list(predictions.values()),
        "source_findings": problems,
        "limitations": [
            "Legacy mapping is loss-aware; unmapped tags count as abstention errors.",
            "Profile-stale decisions are historical; current CRM eligibility is unconfirmed.",
            "Exclusion counts observed cases only; incomplete coverage prevents acceptance.",
            "NDCG uses tier, score descending, job_uid in this gold cohort, not the full queue.",
        ],
    }
    full_tags = sum(
        len(case.get("tags", {})) == 19
        and all(_value(tag) is not None for tag in case.get("tags", {}).values())
        for case in evaluation_cases
    )
    report["readiness"]["full_tag_labeled"] = full_tags
    report["readiness"]["minimum_full_tag_labeled"] = 60
    report["readiness"]["full_tag_sample_ready"] = full_tags >= 60
    report["readiness"]["blind_decision_labeled"] = sum(
        case.get("method") == "blind" and _relevance(case) is not None
        and case.get("gate_outcome") in {"eligible", "ineligible", "unknown"}
        for case in evaluation_cases
    )
    report["readiness"]["minimum_blind_decision_labeled"] = 30
    report["readiness"]["blind_sample_ready"] = (
        report["readiness"]["blind_decision_labeled"] >= 30
    )
    if not (report["readiness"]["full_tag_sample_ready"]
            and report["readiness"]["blind_sample_ready"]):
        report["readiness"]["gold_ready"] = False
        if report["readiness"]["status"] == "baseline_measured":
            report["readiness"]["status"] = "pending_tag_or_blind_labels"
        report["readiness"]["limitations"].append(
            "Gold also needs 60 full-tag cases and 30 blind decision labels."
        )
    report["readiness"]["source_review_required"] = bool(drift_uids)
    if drift_uids:
        report["readiness"]["status"] = "pending_source_review"
        report["readiness"]["gold_ready"] = False
        report["readiness"]["limitations"].append(
            "Changed source labels await human review; quote rebinding alone is insufficient."
        )
    if output:
        write_accuracy_report(
            report,
            output,
            protected_paths=[
                database,
                gold,
                price_path,
                config.rules_path,
                config.search_profiles_path,
                *([log] if log else []),
            ],
        )
    return report


def _formatted(metric: dict) -> str:
    value = metric.get("value")
    if value is None:
        return "unknown"
    counts = (
        f" ({metric['numerator']}/{metric['denominator']})"
        if type(metric.get("numerator")) is int
        else ""
    )
    ci = metric.get("ci95")
    interval = f"; 95% CI [{ci[0]:.3f}, {ci[1]:.3f}]" if ci else ""
    return f"{value:.3f}{counts}{interval}"


def accuracy_markdown(report: dict) -> str:
    evaluation, operational = report["evaluation"], report["operational"]
    lines = [
        "# Vacancy accuracy: saved baseline",
        "",
        f"API calls: {report['api_calls']}; database: read-only.",
        "",
        f"Decision labels: {evaluation['counts']['decision_labeled']}; "
        f"relevant: {evaluation['counts']['relevant']}.",
        "",
        "| Metric | Result |",
        "| --- | --- |",
    ]
    lines.extend(
        f"| {name} | {_formatted(metric)} |" for name, metric in evaluation["metrics"].items()
    )
    lines += [
        "",
        "## Tags",
        "",
        "| Tag | Accuracy | Macro-F1 | Coverage |",
        "| --- | --- | --- | --- |",
    ]
    lines.extend(
        f"| {name} | {_formatted(metrics['accuracy'])} | {_formatted(metrics['macro_f1'])} | "
        f"{_formatted(metrics['prediction_coverage'])} |"
        for name, metrics in evaluation["tags"].items()
    )
    cost = operational["cost_per_vacancy"]
    latency = operational["vacancy_latency_seconds"]
    lines += [
        "",
        "## Operations",
        "",
        f"Historical estimated $/vacancy: {cost if cost is not None else 'unknown'} "
        f"(prices {operational['price_as_of']}).",
        f"Known cost jobs: {operational['cost_known_jobs']}; "
        f"unknown: {operational['cost_unknown_jobs']}.",
        f"Saved vacancy p50/p95 seconds: {latency['p50']} / {latency['p95']}.",
    ]
    if "latest_run" in operational:
        run = operational["latest_run"]
        latency = run["vacancy_latency_seconds"]
        lines += [
            f"Latest log run p50/p95 seconds: {latency['p50']} / {latency['p95']}.",
            f"Requests/vacancy: {run['requests_per_vacancy']}; "
            f"errors: {_formatted(run['error_rate'])}; "
            f"timeouts: {_formatted(run['timeout_rate'])}.",
            f"Cache hits: {_formatted(run['cache_hit_rate'])}.",
        ]
        lines += [
            "",
            "| Log stage | p50 seconds | p95 seconds | Samples |",
            "| --- | --- | --- | --- |",
        ]
        lines.extend(
            f"| {stage} | {stats['p50']} | {stats['p95']} | {stats['samples']} |"
            for stage, stats in run["stage_latency_seconds"].items()
        )
    else:
        lines += [
            f"Requests/vacancy: {operational['requests_per_vacancy']}; "
            f"errors: {_formatted(operational['error_rate'])}; "
            f"timeouts: {_formatted(operational['timeout_rate'])}.",
            f"Cache hits: {_formatted(operational['cache_hit_rate'])}.",
        ]
    negatives = evaluation["negative_restrictions"]
    lines += [
        "",
        "Negative restrictions: "
        f"recall {_formatted(negatives['recall'])}; "
        f"precision {_formatted(negatives['precision'])}.",
    ]
    lines += [
        "",
        "## Readiness",
        "",
        f"Gold ready: {report['readiness']['gold_ready']}. Acceptance gates: unmeasured; "
        "default mode unchanged.",
        "",
    ]
    lines.extend(
        "- " + limitation
        for limitation in report["readiness"]["limitations"]
        + report["limitations"]
        + operational["limitations"]
    )
    return "\n".join(lines) + "\n"


def write_accuracy_report(
    report: dict, output: Path, *, protected_paths: list[Path] | None = None
) -> tuple[Path, Path]:
    """Write atomic JSON and Markdown siblings, validating both destinations first."""
    output = output.expanduser().resolve()
    markdown = output.with_suffix(".md")
    protected = {path.expanduser().resolve() for path in protected_paths or []}
    if output == markdown or output in protected or markdown in protected:
        raise ValueError("Accuracy outputs must be distinct and must not overwrite inputs")
    content = accuracy_markdown(report)
    json_path = write_report(report, output, protected_paths=list(protected))
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=markdown.parent,
            prefix=markdown.name + ".",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(markdown)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)
    return json_path, markdown
