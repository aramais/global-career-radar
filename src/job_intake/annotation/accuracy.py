"""Vacancy-level saved baseline without API calls or database writes.

Unknown predictions are abstentions, not correct unspecified values. Historical
token estimates and latest-run log telemetry have separate scopes; neither is a bill.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
import unicodedata
from collections import Counter, defaultdict
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import quote

from job_intake.annotation.benchmark import read_jsonl
from job_intake.annotation.cohorts import write_new_text
from job_intake.annotation.eval_runner import (
    _object_json,
    evaluate_log,
    read_saved_records,
    report_destinations,
    sqlite_path,
)
from job_intake.annotation.schema import _language_entities
from job_intake.annotation.text import TEXT_VERSION, source_hash
from job_intake.config.settings import AppConfig, load_yaml_mapping
from job_intake.labeling.metrics import has_relevance, relevance_scale
from job_intake.labeling.numeric import is_finite_score
from job_intake.labeling.schema import LABELS_SCHEMA, PREDICTION_SCHEMA
from job_intake.models.job import JobRecord, JobStatus
from job_intake.profiles import load_streams

ACCURACY_VERSION = "vacancy-accuracy-v2"
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
_ROUTES = {"full_review", "suppressed", "OUT_OF_SCOPE", "out_of_scope", "local_reject",
           "title_out_of_family"}


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


def read_saved_predictions(
    database_url: str,
    rows: list[tuple[str, JobRecord]],
    *,
    profile_versions: dict[str, str] | None = None,
    classification_version: str | None = None,
) -> dict[str, dict]:
    """Read the classified vacancy's single current score, without migrations.

    Legacy profile matrices remain historical data. They cannot establish a role
    label and are never promoted to a current prediction by choosing a winner.
    """
    path = sqlite_path(database_url)
    connection = sqlite3.connect(f"file:{quote(str(path), safe='/')}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    result = {}
    try:
        connection.execute("PRAGMA query_only=ON")
        job_columns = {row["name"] for row in connection.execute("PRAGMA table_info(jobs)")}
        score_columns = {
            row["name"] for row in connection.execute(
                "PRAGMA table_info(job_relevance_evaluations)"
            )
        }
        fields = {"job_uid", "profile_id", "profile_version", "content_hash", "decision",
                  "tier", "fit_score"}
        optional = sorted(score_columns & {"eligibility_status", "eligibility_evidence_status",
                                           "raw_fit_score", "score_cap_reasons",
                                           "version_components"})
        for uid, record in rows:
            prediction = {"job_uid": uid, **adapt_saved_record(record)}
            metadata = connection.execute("SELECT * FROM jobs WHERE job_uid = ?", (uid,)).fetchone()
            label = metadata["role_label"] if "role_label" in job_columns else None
            classification = {}
            if "role_classification" in job_columns:
                try:
                    parsed = json.loads(metadata["role_classification"] or "{}")
                    classification = parsed if isinstance(parsed, dict) else {}
                except (TypeError, ValueError):
                    pass
            saved = [dict(row) for row in connection.execute(
                "SELECT job_uid, profile_id, profile_version, content_hash, "
                "decision, tier, fit_score" + ("," + ",".join(optional) if optional else "")
                + " FROM job_relevance_evaluations "
                "WHERE job_uid = ?", (uid,),
            )] if score_columns >= fields else []
            stale_content = [row["profile_id"] for row in saved if (
                "content_hash" not in job_columns
                or row["content_hash"] != metadata["content_hash"]
            )]
            stale_profiles = [row["profile_id"] for row in saved if profile_versions is not None
                              and row["profile_version"] != profile_versions.get(row["profile_id"])]
            source_current = (
                record.annotation.get("source_hash") == source_hash(record)
                if record.annotation.get("source_hash") else None
            )
            classification_current = (
                isinstance(label, str) and classification.get("label") == label
                and bool(classification.get("version"))
                and (classification_version is None
                     or classification.get("version") == classification_version)
                and classification.get("source_hash") == source_hash(record)
                and "content_hash" in job_columns
                and classification.get("content_hash") == metadata["content_hash"]
                and (profile_versions is None or label in profile_versions)
            )
            valid = [row for row in saved if (
                row["profile_id"] == label and classification_current
                and row["profile_id"] not in stale_content + stale_profiles
                and row.get("decision") in {"pass", "review", "reject"}
                and (label == "other" or (
                    row.get("tier") in {"A", "B", "C"}
                    and is_finite_score(row.get("fit_score"))
                ))
            )]
            current = valid[0] if len(valid) == 1 and source_current is not False else {}
            unscored = classification_current and label == "other"
            gate = current.get("eligibility_status")
            gate = gate if gate in {"eligible", "ineligible", "unknown"} else None
            raw_score = current.get("raw_fit_score")
            raw_score = raw_score if is_finite_score(raw_score) and not unscored else None
            cap_reasons, components = None, None
            try:
                parsed = json.loads(current.get("score_cap_reasons") or "null")
                if isinstance(parsed, list) and all(isinstance(item, str) for item in parsed):
                    cap_reasons = parsed
                components = _object_json(current.get("version_components"), "version_components") \
                    if "version_components" in current else None
            except (ValueError, TypeError):
                pass
            prediction.update(
                decision=current.get("decision"), tier=None if unscored else current.get("tier"),
                fit_score=None if unscored else current.get("fit_score"),
                profile_id=label if unscored else current.get("profile_id"),
                role_label=label if classification_current else None,
                gate_outcome=gate, raw_fit_score=raw_score, score_cap_reasons=cap_reasons,
                version_components=components,
                eligibility_evidence_status=current.get("eligibility_evidence_status")
                if current.get("eligibility_evidence_status") in {
                    "confirmed", "blocked", "unconfirmed",
                } else None,
                eligibility_provenance={
                    "column_present": "eligibility_status" in score_columns,
                    "source": "current_saved_evaluation" if gate is not None else
                    "missing_column" if "eligibility_status" not in score_columns else
                    "stale_or_invalid_saved_evaluation",
                },
                role_classification=classification,
                relevance_evaluation=None if unscored else current or None,
                source_current=source_current,
                stale_content_profiles=stale_content, stale_profiles=stale_profiles,
                current_eligible=bool(current) and not unscored,
                needs_reevaluation=not (bool(current) or unscored),
                evaluation_state=("unscored" if unscored else
                                  "current" if current else "needs_reevaluation"),
                decision_source=("role_unscored" if unscored else
                                 "classified_relevance" if current else "reevaluation_required"),
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
    value = case.get("relevance")
    return value if type(value) is int and value in range(4) else None


def evaluate_vacancies(cases: list[dict], predictions: dict[str, dict]) -> dict:
    """Evaluate all labels; missing outputs stay in recall and tag denominators."""
    from job_intake.annotation.gold import TAG_VALUES

    labeled = [
        case
        for case in cases
        if _relevance(case) is not None
        and isinstance(case.get("role_label"), str)
        and case.get("gate_outcome") in {"eligible", "ineligible", "unknown"}
    ]
    relevant = [case for case in labeled if _relevance(case) >= 2]
    eligible = [case for case in labeled if case.get("gate_outcome") == "eligible"]

    def prediction(case: dict) -> dict:
        return predictions.get(case["job_uid"], {})

    def excluded(case: dict) -> bool:
        row = prediction(case)
        return row.get("decision") == "reject" or isinstance(row.get("route"), str) \
            and row.get("route") in {
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
    ranking_cases = [case for case in labeled if case.get("role_label") != "other"]
    rankable = [
        case
        for case in labeled
        if prediction(case).get("tier") in {"A", "B", "C"}
        and is_finite_score(prediction(case).get("fit_score"))
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
    role_pairs = [
        (case["role_label"], prediction(case).get("role_label"))
        for case in cases if isinstance(case.get("role_label"), str)
    ]
    role_confusion = Counter((gold, predicted or _MISSING) for gold, predicted in role_pairs)
    role_metrics = {
        "accuracy": proportion(sum(gold == predicted for gold, predicted in role_pairs),
                               len(role_pairs)),
        "prediction_coverage": proportion(sum(predicted is not None for _, predicted in role_pairs),
                                          len(role_pairs)),
        "confusion": {
            gold: {predicted: count for (truth, predicted), count in sorted(role_confusion.items())
                   if truth == gold}
            for gold in sorted({gold for gold, _ in role_pairs})
        },
    }
    eligibility_values = {"eligible", "ineligible", "unknown"}
    eligibility_pairs = [
        (case["gate_outcome"], prediction(case).get("gate_outcome"))
        for case in cases if case.get("gate_outcome") in eligibility_values
    ]
    eligibility_confusion = Counter(
        (gold, predicted if predicted in eligibility_values else _MISSING)
        for gold, predicted in eligibility_pairs
    )
    eligibility_metrics = {
        "accuracy": proportion(sum(gold == predicted for gold, predicted in eligibility_pairs),
                               len(eligibility_pairs)),
        "prediction_coverage": proportion(
            sum(predicted in eligibility_values for _, predicted in eligibility_pairs),
            len(eligibility_pairs),
        ),
        "confusion": {
            gold: {predicted: count
                   for (truth, predicted), count in sorted(eligibility_confusion.items())
                   if truth == gold}
            for gold in sorted({gold for gold, _ in eligibility_pairs})
        },
        "note": ("Eligibility requires its own prediction; "
                 "role, relevance and tier do not imply it."),
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
    route_present = [case for case in labeled
                     if isinstance(prediction(case).get("route"), str)
                     and prediction(case)["route"] in _ROUTES]
    route_missing = [case["job_uid"] for case in labeled
                     if case not in route_present]
    route_coverage = proportion(len(route_present), len(labeled))
    route_status = "not_applicable" if not labeled else "unmeasured" if not route_present else \
        "measured" if len(route_present) == len(labeled) else "partial"
    result = {
        "counts": {
            "gold_rows": len(cases),
            "decision_labeled": len(labeled),
            "decision_unlabeled": len(cases) - len(labeled),
            "relevant": len(relevant),
            "eligible": len(eligible),
            "ranked": len(rankable),
        },
        "decision_prediction_coverage": proportion(complete, len(labeled)),
        "task_prediction_coverage": {
            "decision": proportion(sum(
                prediction(case).get("decision") in {"pass", "review", "reject"}
                for case in labeled
            ), len(labeled)),
            "role_classification": role_metrics["prediction_coverage"],
            "eligibility": eligibility_metrics["prediction_coverage"],
            "route": route_coverage,
            "ranking": proportion(sum(case in ranking_cases for case in rankable),
                                  len(ranking_cases)),
            **{f"tags.{name}": row["prediction_coverage"] for name, row in tag_metrics.items()},
        },
        "metrics": metrics,
        "role_classification": role_metrics,
        "eligibility": eligibility_metrics,
        "routing": {"prediction_coverage": route_coverage, "status": route_status,
                    "N": len(labeled), "predicted_N": len(route_present),
                    "missing_N": len(route_missing), "missing_job_uids": route_missing,
                    "note": "Routing needs its own saved output; decision/tier do not imply it."},
        "tags": tag_metrics,
        "negative_restrictions": {
            "recall": proportion(aggregate_negative["tp"], aggregate_negative["actual"]),
            "precision": proportion(aggregate_negative["tp"], aggregate_negative["predicted"]),
        },
        "ranking": [case["job_uid"] for case in ranked[:20]],
    }
    # Explicit v3 inputs can carry only tags/gates or abstain on relevance entirely.
    if has_relevance(predictions) or any(case.get("schema") == LABELS_SCHEMA for case in cases) \
            or any(isinstance(row, dict) and row.get("schema") == PREDICTION_SCHEMA
                   for row in predictions.values()):
        result["relevance_scale"] = relevance_scale(cases, predictions)
    return result


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
        "acceptance_status": "insufficient_evidence",
        "acceptance_passed": False,
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
    labels_ready = counts["decision_labeled"] >= 160 and counts["relevant"] >= 40
    tasks = {}
    for name, coverage in evaluation["task_prediction_coverage"].items():
        denominator, numerator = coverage["denominator"], coverage["numerator"]
        state = ("not_applicable" if not denominator else "unmeasured" if not numerator
                 else "measured" if numerator == denominator else "partial")
        tasks[name] = {"coverage": coverage, "status": state,
                       "measured": bool(numerator),
                       "prediction_complete": bool(denominator) and numerator == denominator}
    applicable = [task for task in tasks.values() if task["coverage"]["denominator"]]
    complete = bool(counts["decision_labeled"]) and bool(applicable) and all(
        task["prediction_complete"] for task in applicable
    )
    return {
        "status": ("pending_human_labels" if not labels_ready else
                   "baseline_measured" if complete else "pending_predictions"),
        "human_labels_ready": labels_ready,
        "gold_ready": labels_ready and complete,
        "minimum_labeled": 160,
        "minimum_relevant": 40,
        "labels_present": bool(counts["decision_labeled"]),
        "baseline_measured": complete,
        "prediction_complete": complete,
        "tasks": tasks,
        "acceptance_status": "insufficient_evidence",
        "acceptance_passed": False,
        "default_mode_change_authorized": False,
        "gates": [
            {"name": name, "passed": False, "status": "insufficient_evidence",
             "reason": reason}
            for name, reason in (
                ("decision_accuracy_vs_baseline", "No comparable baseline/candidate pair."),
                ("negative_restrictions", "No comparable baseline/candidate pair."),
                ("scope_vs_baseline", "No comparable baseline/candidate pair."),
                ("speed_at_concurrency_6", "No cohort-linked concurrency-6 benchmark."),
                ("current_price_cost", "No complete candidate usage at current prices."),
                ("repeat_run_stability", "No provenance-matched independent repeat run."),
            )
        ],
        "limitations": [
            "A saved baseline cannot establish comparative acceptance of a new variant.",
            "Human labels, both variants, current prices and a repeat run are required.",
        ]
        + (
            ["Gold needs at least 160 decision labels and 40 relevant vacancies."]
            if not labels_ready
            else []
        ) + (["Missing task predictions prevent a measured complete baseline."]
             if not complete else []),
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


def _accuracy_cases(gold: Path, cohort: Path | None) -> tuple[list[dict], dict | None]:
    cases = read_jsonl(gold)
    cohort_manifest = None
    if cohort is not None:
        from job_intake.annotation.cohorts import load_cohort, select_cases

        cohort_manifest = load_cohort(cohort)
        cases = select_cases(cases, cohort_manifest)
    if any(not isinstance(case.get("job_uid"), str) for case in cases):
        raise ValueError("Vacancy gold rows require job_uid")
    if not cases:
        raise ValueError("Gold is empty")
    return cases, cohort_manifest


def _accuracy_sources(
    config: AppConfig, cases: list[dict], *, allow_source_snapshot: bool,
) -> tuple[Path, list[tuple[str, JobRecord]], list[tuple[str, JobRecord]], dict]:
    """Read-only gold sources: (database, all rows, saved DB rows, source problems)."""
    uids = [case["job_uid"] for case in cases]
    if allow_source_snapshot:
        from job_intake.annotation.frozen_eval import record_from_snapshot

        database, saved_rows, problems = read_saved_records(
            config.database_url, limit=None, include_archived=True,
        )
        saved_rows = [(uid, record) for uid, record in saved_rows if uid in uids]
        records = dict(saved_rows)
        rows = list(saved_rows)
        for case in cases:
            if case["job_uid"] not in records:
                if not isinstance(case.get("source"), dict):
                    raise ValueError("Missing source snapshot for absent saved vacancy")
                rows.append((case["job_uid"], record_from_snapshot(case["source"])))
    else:
        database, rows, problems = read_saved_records(
            config.database_url, limit=None, job_uids=uids, include_archived=True,
        )
        saved_rows = rows
    return database, rows, saved_rows, problems


def _profile_versions(config: AppConfig) -> tuple[dict[str, str] | None, str | None]:
    if not (config.rules_path.is_file() and config.search_profiles_path.is_file()):
        return None, None
    from job_intake.profiles import taxonomy_version, with_other_stream

    rules = load_yaml_mapping(config.rules_path)
    streams = with_other_stream(
        load_streams(rules, load_yaml_mapping(config.search_profiles_path)), rules,
    )
    return {stream.id: stream.version for stream in streams}, taxonomy_version(streams)


def run_accuracy(
    config: AppConfig,
    *,
    gold: Path,
    variant: str = "saved",
    prices: Path | None = None,
    log: Path | None = None,
    output: Path | None = None,
    cohort: Path | None = None,
    allow_source_snapshot: bool = False,
    predictions: Path | None = None,
) -> dict:
    from job_intake.annotation.gold import validate_gold

    if variant in {"rules", "labels"}:
        if prices is not None or log is not None:
            raise ValueError("--prices and --log apply to the saved variant only")
        report = run_relevance_accuracy(
            config, gold=gold, variant=variant, predictions=predictions, cohort=cohort,
            allow_source_snapshot=allow_source_snapshot,
        )
        if output:
            write_accuracy_report(report, output, protected_paths=[
                Path(report["database"]), gold, config.rules_path, config.search_profiles_path,
                *([predictions] if predictions else []),
            ])
        return report
    if variant != "saved":
        raise ValueError("variant must be saved (v2 gold), rules or labels (v3 gold); "
                         "no AI calls are made")
    if predictions is not None:
        raise ValueError("--predictions applies to the labels variant only")
    cases, cohort_manifest = _accuracy_cases(gold, cohort)
    uids = [case["job_uid"] for case in cases]
    database, rows, saved_rows, problems = _accuracy_sources(
        config, cases, allow_source_snapshot=allow_source_snapshot,
    )
    versions, classifier_version = _profile_versions(config)
    from job_intake.annotation.source_compat import read_source_history

    validated = validate_gold(
        rows,
        cases,
        profile_ids=list(versions) if versions is not None else None,
        source_history=read_source_history(config.database_url, cases),
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
    predictions = read_saved_predictions(
        config.database_url, saved_rows, profile_versions=versions,
        classification_version=classifier_version,
    )
    for uid, _ in rows:
        if uid not in predictions:
            predictions[uid] = {"job_uid": uid, "tags": {}, "route": None,
                                "evaluation_state": "missing_saved_prediction"}
    for uid, findings in problems.items():
        if any(finding["severity"] == "error" for finding in findings):
            predictions[uid]["tags"] = dict.fromkeys(predictions[uid]["tags"], None)
            predictions[uid]["route"] = None
    price_path = prices or config.rules_path.parent / "eval_prices.yaml"
    price_config = load_prices(price_path)
    evaluation_cases = [
        {**case, "role_label": None, "relevance": None, "gate_outcome": None, "tags": {}}
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
        "selection": {"job_uids": uids, "include_archived": True,
                      "cohort": cohort_manifest, "source_snapshot_fallback": allow_source_snapshot},
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
            "Legacy profile matrices and stale decisions need classification and reevaluation.",
            "Exclusion counts observed cases only; incomplete coverage prevents acceptance.",
            "Legacy or stale saved rows without independent eligibility fields abstain."
            if any(row.get("gate_outcome") is None for row in predictions.values()) else
            "Eligibility is read independently from current saved hiring assessments.",
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
    report["readiness"]["full_tag_label_sample_ready"] = full_tags >= 60
    report["readiness"]["full_tag_sample_ready"] = full_tags >= 60 and all(
        task["prediction_complete"] for name, task in report["readiness"]["tasks"].items()
        if name.startswith("tags.")
    )
    report["readiness"]["blind_decision_labeled"] = sum(
        case.get("method") == "blind" and _relevance(case) is not None
        and case.get("gate_outcome") in {"eligible", "ineligible", "unknown"}
        for case in evaluation_cases
    )
    report["readiness"]["minimum_blind_decision_labeled"] = 30
    report["readiness"]["blind_label_sample_ready"] = (
        report["readiness"]["blind_decision_labeled"] >= 30
    )
    blind_cases = [case for case in evaluation_cases if case.get("method") == "blind"]
    blind_tasks = readiness_report(evaluate_vacancies(blind_cases, predictions))["tasks"]
    report["readiness"]["blind_tasks"] = blind_tasks
    report["readiness"]["blind_sample_ready"] = (
        report["readiness"]["blind_label_sample_ready"]
        and all(task["prediction_complete"] for task in blind_tasks.values()
                if task["coverage"]["denominator"])
    )
    if not (report["readiness"]["full_tag_label_sample_ready"]
            and report["readiness"]["blind_label_sample_ready"]):
        report["readiness"]["human_labels_ready"] = False
        report["readiness"]["gold_ready"] = False
        if report["readiness"]["status"] != "pending_human_labels":
            report["readiness"]["status"] = "pending_tag_or_blind_labels"
        report["readiness"]["limitations"].append(
            "Gold also needs 60 full-tag cases and 30 blind decision labels."
        )
    report["readiness"]["source_review_required"] = bool(drift_uids)
    if drift_uids:
        report["readiness"]["status"] = "pending_source_review"
        report["readiness"]["gold_ready"] = False
        report["readiness"]["human_labels_ready"] = False
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


def run_relevance_accuracy(
    config: AppConfig,
    *,
    gold: Path,
    variant: str,
    predictions: Path | None = None,
    cohort: Path | None = None,
    allow_source_snapshot: bool = False,
) -> dict:
    """v3 gold against the rules tier-map baseline or supplied v3 predictions, read-only."""
    from job_intake.annotation.source_compat import read_source_history
    from job_intake.labeling.gold_v3 import gold_file_schema, validate_gold_v3
    from job_intake.labeling.predictions import (
        BASELINE_LABELER,
        OTHER_ROLE_RELEVANCE,
        TIER_MAP_NOTE,
        TIER_RELEVANCE,
        baseline_predictions,
        check_prediction_rows,
    )
    from job_intake.labeling.schema import LABELS_SCHEMA

    if variant == "rules" and predictions is not None:
        raise ValueError("The rules variant derives predictions from saved tiers; "
                         "use --variant labels for a predictions file")
    if variant == "labels" and predictions is None:
        raise ValueError("--variant labels requires --predictions with v3 prediction rows")
    cases, cohort_manifest = _accuracy_cases(gold, cohort)
    if gold_file_schema(cases) != LABELS_SCHEMA:
        raise ValueError(f"--variant {variant} needs {LABELS_SCHEMA} gold; "
                         "create it with gold migrate-v3")
    database, rows, saved_rows, problems = _accuracy_sources(
        config, cases, allow_source_snapshot=allow_source_snapshot,
    )
    versions, classifier_version = _profile_versions(config)
    if variant == "labels" and versions is None:
        raise ValueError("Labels evaluation requires the current rules and search profiles "
                         "to validate $.role_label")
    validated = validate_gold_v3(
        rows, cases, profile_ids=list(versions) if versions is not None else None,
        source_history=read_source_history(config.database_url, cases),
    )
    if not validated["valid"]:
        raise ValueError(
            "Invalid vacancy gold: " + "; ".join(str(error) for error in validated["errors"])
        )
    records = dict(rows)
    parser_only = {case["job_uid"] for case in validated["rows"]
                   if _parser_only_drift(case, records[case["job_uid"]])}
    drift_uids = {warning["job_uid"] for warning in validated["warnings"]
                  if warning.get("code") == "source_hash_changed"} - parser_only
    if variant == "rules":
        saved = read_saved_predictions(
            config.database_url, saved_rows, profile_versions=versions,
            classification_version=classifier_version,
        )
        for uid, findings in problems.items():
            if uid in saved and any(finding["severity"] == "error" for finding in findings):
                saved[uid] = {"job_uid": uid, "evaluation_state": "invalid_saved_source"}
        prediction_rows = baseline_predictions(rows, saved)
    else:
        from job_intake.annotation.text import source_hash

        prediction_rows = read_jsonl(predictions)
        supplied = check_prediction_rows(
            prediction_rows, profile_ids=list(versions) if versions is not None else None,
        )
        # A cohort evaluates a subset; predictions for the rest of the gold are set aside.
        outside = sorted(uid for uid in supplied if uid not in records) if cohort else []
        for uid, row in supplied.items():
            if uid in outside:
                continue
            if uid not in records or row["source_hash"] != source_hash(records[uid]):
                raise ValueError("Predictions must bind uniquely to the gold sources: " + uid)
        prediction_rows = [row for row in prediction_rows if row["job_uid"] not in outside]
    prediction_map = {row["job_uid"]: row for row in prediction_rows}
    evaluation_cases = [
        {**case, "role_label": None, "relevance": None, "gate_outcome": None, "tags": {}}
        if case["job_uid"] in drift_uids else case
        for case in validated["rows"]
    ]
    evaluation = evaluate_vacancies(
        [{**case, "tags": {}} for case in evaluation_cases],
        {uid: {**row, "tags": {}} for uid, row in prediction_map.items()},
    )
    # Legacy v2 tag blocks do not apply to v3 gold; the v3 tags live in relevance_scale.
    evaluation.pop("tags")
    evaluation.pop("negative_restrictions")
    for name in [name for name in evaluation["task_prediction_coverage"]
                 if name.startswith("tags.")]:
        evaluation["task_prediction_coverage"].pop(name)
    evaluation["relevance_scale"] = relevance_scale(evaluation_cases, prediction_map)
    evaluation["counts"]["source_drift_withheld"] = len(drift_uids)
    evaluation["counts"]["parser_only_rebound"] = len(parser_only)
    report = {
        "version": ACCURACY_VERSION,
        "variant": variant,
        "scope": "vacancy_level_labels_v3",
        "generated_at": datetime.now(UTC).isoformat(),
        "database": str(database),
        "database_access": "read_only",
        "api_calls": 0,
        "gold": {"path": str(gold.resolve()), "schema": LABELS_SCHEMA, "validation": validated},
        "selection": {"job_uids": [case["job_uid"] for case in cases], "include_archived": True,
                      "cohort": cohort_manifest,
                      "source_snapshot_fallback": allow_source_snapshot},
        "prediction_source": {
            "kind": "saved_tier_map" if variant == "rules" else "jsonl",
            "path": str(predictions.resolve()) if predictions else None,
            "labelers": dict(Counter(row.get("labeler") for row in prediction_rows)),
            "ignored_outside_selection": len(outside) if variant == "labels" else 0,
        },
        "evaluation": evaluation,
        "predictions": prediction_rows,
        "source_findings": problems,
        "limitations": [
            "Missing predictions count as errors in coverage, exact, ±1, recall and P@k; "
            "MAE, QWK and Spearman use covered pairs only.",
            "Tag metrics use only human-labeled v3 tags.",
            "Conclusions require the holdout cohort; calibration results are not acceptance.",
        ],
    }
    if variant == "rules":
        report["baseline_tier_map"] = {
            "labeler": BASELINE_LABELER, "tier_relevance": TIER_RELEVANCE,
            "other_role_relevance": OTHER_ROLE_RELEVANCE, "note": TIER_MAP_NOTE,
        }
    if drift_uids:
        report["limitations"].append(
            "Changed source labels await human review and are withheld from metrics."
        )
    return report


def relevance_accuracy_markdown(report: dict) -> str:
    from job_intake.labeling.metrics import relevance_scale_markdown

    evaluation = report["evaluation"]
    block = evaluation["relevance_scale"]
    lines = [
        f"# Vacancy accuracy: {report['variant']} (vacancy-labels-v3)",
        "",
        f"API calls: {report['api_calls']}; database: read-only.",
        "",
        f"Human relevance labels: {block['N']}; decision labels: "
        f"{evaluation['counts']['decision_labeled']}; relevant: "
        f"{evaluation['counts']['relevant']}.",
    ]
    if "baseline_tier_map" in report:
        lines += ["", "Baseline: " + report["baseline_tier_map"]["note"]]
    lines += ["", "## Relevance scale 0–3", "", *relevance_scale_markdown(block), "",
              "## Role classification", "",
              f"Accuracy: {_formatted(evaluation['role_classification']['accuracy'])}; "
              f"coverage: {_formatted(evaluation['role_classification']['prediction_coverage'])}.",
              "", "## Eligibility", "",
              f"Accuracy: {_formatted(evaluation['eligibility']['accuracy'])}; "
              f"coverage: {_formatted(evaluation['eligibility']['prediction_coverage'])}.",
              "", "## Tags", "", "| Tag | N | Accuracy | Coverage |", "| --- | --- | --- | --- |"]
    lines.extend(
        f"| {name} | {row['N']} | {_formatted(row['accuracy'])} | "
        f"{_formatted(row['prediction_coverage'])} |"
        for name, row in block["tags"].items()
    )
    lines += [""]
    lines.extend("- " + limitation for limitation in report["limitations"])
    return "\n".join(lines) + "\n"


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
    if report.get("scope") == "vacancy_level_labels_v3":
        return relevance_accuracy_markdown(report)
    evaluation, operational = report["evaluation"], report["operational"]
    lines = [
        "# Vacancy accuracy: saved baseline",
        "",
        f"API calls: {report['api_calls']}; database: read-only.",
        "",
        f"Decision labels: {evaluation['counts']['decision_labeled']}; "
        f"relevant: {evaluation['counts']['relevant']}.",
        "",
        "## Prediction coverage by task",
        "",
        "| Task | Coverage | Status |",
        "| --- | --- | --- |",
        *[f"| {name} | {_formatted(task['coverage'])} | {task['status']} |"
          for name, task in report["readiness"]["tasks"].items()],
        "",
        "| Metric | Result |",
        "| --- | --- |",
    ]
    lines.extend(
        f"| {name} | {_formatted(metric)} |" for name, metric in evaluation["metrics"].items()
    )
    role = evaluation["role_classification"]
    eligibility = evaluation["eligibility"]
    lines += [
        "",
        "## Role classification",
        "",
        f"Accuracy: {_formatted(role['accuracy'])}; "
        f"coverage: {_formatted(role['prediction_coverage'])}.",
        "",
        "## Eligibility",
        "",
        f"Accuracy: {_formatted(eligibility['accuracy'])}; "
        f"coverage: {_formatted(eligibility['prediction_coverage'])}.",
        eligibility["note"],
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
        f"Human labels ready: {report['readiness']['human_labels_ready']}; "
        f"complete baseline measured: {report['readiness']['baseline_measured']}; "
        f"gold ready for comparison: {report['readiness']['gold_ready']}.",
        "Acceptance gates: insufficient_evidence; "
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


def accuracy_report_destinations(
    output: Path, *, protected_paths: list[Path] | None = None,
) -> tuple[Path, Path]:
    """Pin the parent, protect input aliases, and refuse both occupied final names."""
    output = output.expanduser()
    json_path, markdown = report_destinations(
        [output, output.with_suffix(".md")], protected_paths=protected_paths or [],
    )
    return json_path, markdown


def write_accuracy_report(
    report: dict, output: Path, *, protected_paths: list[Path] | None = None,
) -> tuple[Path, Path]:
    """Publish new JSON and Markdown exclusively, including the saved variant.

    Each file is complete before becoming visible; the pair is not a transaction.
    A Markdown failure can leave a complete JSON, requiring new paths for retry.
    """
    output, markdown = accuracy_report_destinations(output, protected_paths=protected_paths)
    content = accuracy_markdown(report)
    json_content = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    write_new_text(output, json_content)
    write_new_text(markdown, content)
    return output, markdown
