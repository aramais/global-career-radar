"""Immutable source/label bundles and comparable, evidence-limited quality gates."""

from __future__ import annotations

import copy
import math
import random
from datetime import UTC, datetime
from pathlib import Path

from job_intake.annotation.accuracy import evaluate_vacancies, proportion, read_saved_predictions
from job_intake.annotation.benchmark import read_jsonl
from job_intake.annotation.cohorts import (
    digest,
    independent_holdout,
    load_cohort,
    read_object,
    select_cases,
    write_new,
)
from job_intake.annotation.eval_runner import read_saved_records
from job_intake.annotation.gold import validate_gold
from job_intake.annotation.text import source_hash
from job_intake.config.settings import AppConfig
from job_intake.models.job import JobRecord, JobStatus

BUNDLE_SCHEMA = "frozen-evaluation-v1"
RUN_SCHEMA = "frozen-evaluation-run-v1"
GATE_VERSION = "audit-quality-gates-v2"
DEFAULT_POLICY = {
    "version": GATE_VERSION,
    "minimum_cases": 160,
    "minimum_relevant": 40,
    "minimum_full_tag_cases": 60,
    "minimum_blind_cases": 30,
    "maximum_accuracy_regression": 0.0,
    "maximum_false_block_increase": 0.0,
    "maximum_cost_ratio": 1.0,
    "maximum_time_ratio": 1.0,
}
_SOURCE_FIELDS = (
    "source",
    "company",
    "title",
    "original_url",
    "apply_url",
    "source_job_id",
    "posted_at",
    "location_text",
    "remote_text",
    "employment_type",
    "salary_text",
    "timezone_text",
    "description_raw",
    "description_clean",
    "status",
    "source_metadata",
)


def source_snapshot(record: JobRecord) -> dict:
    result = {}
    for key in _SOURCE_FIELDS:
        value = getattr(record, key)
        result[key] = value.isoformat() if isinstance(value, datetime) else copy.deepcopy(value)
    return result


def record_from_snapshot(snapshot: dict) -> JobRecord:
    if (
        not isinstance(snapshot, dict)
        or set(snapshot) - set(_SOURCE_FIELDS)
        or not {"source", "company", "title", "original_url"} <= snapshot.keys()
    ):
        raise ValueError("Invalid source snapshot")
    values = copy.deepcopy(snapshot)
    if values.get("posted_at"):
        values["posted_at"] = datetime.fromisoformat(values["posted_at"])
    values["status"] = JobStatus(values.get("status", "unknown"))
    return JobRecord(**values)


def build_bundle(
    config: AppConfig,
    *,
    gold: Path,
    versions: dict[str, str],
    cohort: Path | None = None,
    allow_source_snapshot: bool = False,
    policy: dict | None = None,
) -> dict:
    """Archive-inclusive SELECT only; missing saved predictions remain missing."""
    if not versions or any(
        not isinstance(k, str) or not isinstance(v, str) or not v.strip()
        for k, v in versions.items()
    ):
        raise ValueError("Declare baseline/candidate versions before freezing")
    cases = read_jsonl(gold)
    manifest = load_cohort(cohort) if cohort else None
    if manifest:
        cases = select_cases(cases, manifest)
    if not cases or len({case.get("job_uid") for case in cases}) != len(cases):
        raise ValueError("Bundle needs unique gold IDs")
    # Read all rows so an explicit snapshot fallback can distinguish missing IDs
    # from a malformed/inaccessible DB. Never suppress SQLite or schema failures.
    database, saved, problems = read_saved_records(
        config.database_url, limit=None, include_archived=True
    )
    wanted = {case["job_uid"] for case in cases}
    saved = [(uid, record) for uid, record in saved if uid in wanted]
    from job_intake.config.settings import load_yaml_mapping
    from job_intake.profiles import load_streams, taxonomy_version, with_other_stream

    profiles, classifier_version = None, None
    if config.rules_path.is_file() and config.search_profiles_path.is_file():
        rules = load_yaml_mapping(config.rules_path)
        streams = with_other_stream(
            load_streams(rules, load_yaml_mapping(config.search_profiles_path)),
            rules,
        )
        profiles = {stream.id: stream.version for stream in streams}
        classifier_version = taxonomy_version(streams)
    predictions = read_saved_predictions(
        config.database_url,
        saved,
        profile_versions=profiles,
        classification_version=classifier_version,
    )
    records = dict(saved)
    origins = dict.fromkeys(records, "saved_database")
    for case in cases:
        uid = case["job_uid"]
        if uid not in records:
            if not allow_source_snapshot or not isinstance(case.get("source"), dict):
                raise ValueError("Missing DB source; explicitly enable source snapshot fallback")
            records[uid] = record_from_snapshot(case["source"])
            origins[uid] = "gold_source_snapshot"
    from job_intake.annotation.source_compat import check_source_compatibility, read_source_history

    history = read_source_history(config.database_url, cases)
    validated = validate_gold(list(records.items()), cases, source_history=history)
    if not validated["valid"]:
        raise ValueError("Invalid gold: " + "; ".join(row["code"] for row in validated["errors"]))
    proofs = {
        case["job_uid"]: check_source_compatibility(
            case,
            records[case["job_uid"]],
            history=history.get(case["job_uid"]),
        )
        for case in cases
    }
    if any(not proof["compatible"] for proof in proofs.values()):
        raise ValueError("Source drift: create reviewed labels/cohort before freezing")
    gate_policy = copy.deepcopy(policy or DEFAULT_POLICY)
    validate_policy(gate_policy)
    rows = [
        {
            "job_uid": case["job_uid"],
            "source_hash": source_hash(records[case["job_uid"]]),
            "source_compatibility": proofs[case["job_uid"]],
            "source": source_snapshot(records[case["job_uid"]]),
            "source_origin": origins[case["job_uid"]],
            "saved_annotation": copy.deepcopy(records[case["job_uid"]].annotation),
            "saved_prediction": copy.deepcopy(predictions.get(case["job_uid"])),
            "label": copy.deepcopy(case),
            "source_findings": problems.get(case["job_uid"], []),
        }
        for case in sorted(cases, key=lambda row: row["job_uid"])
    ]
    result = {
        "schema": BUNDLE_SCHEMA,
        "created_at": datetime.now(UTC).isoformat(),
        "database": str(database),
        "database_access": "read_only",
        "api_calls": 0,
        "gold_sha256": digest(cases),
        "versions": copy.deepcopy(versions),
        "cohort": manifest,
        "gate_policy": gate_policy,
        "rows": rows,
    }
    result["bundle_id"] = digest(result)
    return result


def validate_policy(policy: dict) -> None:
    legacy_keys = set(DEFAULT_POLICY) - {
        "minimum_relevant",
        "minimum_full_tag_cases",
        "minimum_blind_cases",
    }
    if (
        not isinstance(policy, dict)
        or not (
            (set(policy) == set(DEFAULT_POLICY) and policy.get("version") == GATE_VERSION)
            or (set(policy) == legacy_keys and policy.get("version") == "audit-quality-gates-v1")
        )
        or type(policy.get("minimum_cases")) is not int
        or policy["minimum_cases"] < 1
    ):
        raise ValueError("Unsupported predeclared quality gate policy")
    for key in set(policy) - {"version", "minimum_cases"}:
        if (
            type(policy[key]) not in {int, float}
            or not math.isfinite(policy[key])
            or policy[key] < 0
        ):
            raise ValueError("Gate tolerances must be finite and nonnegative")
        if key.startswith("minimum_") and type(policy[key]) is not int:
            raise ValueError("Minimum evidence counts must be integers")


def load_bundle(path: Path) -> dict:
    result = read_object(path)
    if result.get("schema") != BUNDLE_SCHEMA or result.get("bundle_id") != digest(
        {k: v for k, v in result.items() if k != "bundle_id"}
    ):
        raise ValueError("Frozen bundle changed or has unsupported schema")
    validate_policy(result["gate_policy"])
    for row in result["rows"]:
        from job_intake.annotation.source_compat import check_source_compatibility

        record = record_from_snapshot(row["source"])
        proof = row.get("source_compatibility", {})
        history = [proof] if proof.get("source_snapshot") else None
        if (
            source_hash(record) != row["source_hash"]
            or not check_source_compatibility(
                row["label"],
                record,
                history=history,
            )["compatible"]
        ):
            raise ValueError("Frozen source/label hash mismatch")
    return result


def task_metrics(
    cases: list[dict], predictions: dict[str, dict], *, random_seed: int = 1729
) -> dict:
    """Profession and hiring are orthogonal; rejecting inaccessible jobs is not role loss."""
    result = evaluate_vacancies(cases, predictions)
    labeled = [row for row in cases if type(row.get("relevance")) is int]
    relevant = [row for row in labeled if row["relevance"] >= 2]
    available = [row for row in relevant if row.get("gate_outcome") == "eligible"]
    unavailable = [row for row in cases if row.get("gate_outcome") == "ineligible"]
    eligible = [row for row in cases if row.get("gate_outcome") == "eligible"]

    def pred(row: dict) -> dict:
        return predictions.get(row["job_uid"], {})

    def actionable(row: dict) -> bool:
        return (
            pred(row).get("gate_outcome") == "eligible"
            and pred(row).get("decision") == "pass"
            and pred(row).get("tier") in {"A", "B"}
        )

    selected = [row for row in labeled if actionable(row)]
    result["independent_axes"] = {
        "role_recall": proportion(
            sum(pred(row).get("role_label") == row.get("role_label") for row in relevant),
            len(relevant),
        ),
        "actionable_p1_recall": proportion(sum(map(actionable, available)), len(available)),
        "actionable_p1_precision": proportion(
            sum(
                row["relevance"] >= 2 and row.get("gate_outcome") == "eligible" for row in selected
            ),
            len(selected),
        ),
        "blocker_recall": proportion(
            sum(pred(row).get("gate_outcome") == "ineligible" for row in unavailable),
            len(unavailable),
        ),
        "false_block_rate": proportion(
            sum(pred(row).get("gate_outcome") == "ineligible" for row in eligible), len(eligible)
        ),
        "unknown_eligibility": sum(row.get("gate_outcome") == "unknown" for row in cases),
    }
    rank_cases = [row for row in labeled if row.get("role_label") != "other"]
    ranked = [
        row
        for row in rank_cases
        if type(pred(row).get("fit_score")) in {int, float}
        and math.isfinite(pred(row)["fit_score"])
        and pred(row).get("tier") in {"A", "B", "C"}
    ]
    ranked.sort(
        key=lambda row: (
            {"A": 0, "B": 1, "C": 2}[pred(row)["tier"]],
            -pred(row)["fit_score"],
            row["job_uid"],
        )
    )
    ideal = sorted([row["relevance"] for row in rank_cases], reverse=True)
    rng = random.Random(random_seed)
    baselines = [rng.sample(rank_cases, len(rank_cases)) for _ in range(256)]
    result["graded_ranking"] = {"random_seed": random_seed, "random_repeats": 256}
    for k in (5, 10):

        def dcg(values: list[int], k: int = k) -> float:
            return sum((2**value - 1) / math.log2(i + 2) for i, value in enumerate(values[:k]))

        denominator = dcg(ideal)
        numerator = dcg([row["relevance"] for row in ranked])
        random_values = [dcg([row["relevance"] for row in sample]) for sample in baselines]
        result["graded_ranking"][f"ndcg@{k}"] = {
            "value": numerator / denominator if denominator else None,
            "numerator": numerator,
            "denominator": denominator,
            "prediction_n": len(ranked),
            "required_n": len(rank_cases),
            "random_baseline": sum(random_values) / len(random_values) / denominator
            if denominator
            else None,
            "status": "not_applicable"
            if not rank_cases
            else "measured"
            if len(ranked) == len(rank_cases)
            else "partial",
        }
    return result


def create_run(
    bundle: dict,
    *,
    predictions: list[dict] | None,
    variant: str,
    version: str,
    run_id: str,
    operational: dict | None = None,
) -> dict:
    if bundle["versions"].get(variant) != version or not run_id.strip():
        raise ValueError("Run variant/version must have been declared in frozen bundle")
    expected = {row["job_uid"]: row["source_hash"] for row in bundle["rows"]}
    if predictions is None:
        if variant != "baseline":
            raise ValueError(
                "Frozen saved predictions only represent baseline; supply candidate outputs"
            )
        predictions = [
            {**copy.deepcopy(row["saved_prediction"]), "source_hash": row["source_hash"]}
            for row in bundle["rows"]
            if row["saved_prediction"] is not None
        ]
    if not isinstance(predictions, list):
        raise ValueError("Predictions must be an array")
    seen = set()
    for row in predictions:
        uid = row.get("job_uid")
        if uid not in expected or uid in seen or row.get("source_hash") != expected[uid]:
            raise ValueError("Predictions must bind uniquely to this exact frozen source")
        seen.add(uid)
    prediction_map = {row["job_uid"]: copy.deepcopy(row) for row in predictions}
    result = {
        "schema": RUN_SCHEMA,
        "bundle_id": bundle["bundle_id"],
        "variant": variant,
        "version": version,
        "run_id": run_id,
        "created_at": datetime.now(UTC).isoformat(),
        "predictions": prediction_map,
        "operational": copy.deepcopy(operational or {}),
        "evaluation": task_metrics([row["label"] for row in bundle["rows"]], prediction_map),
    }
    result["run_hash"] = digest(result)
    return result


def saved_predictions_for_bundle(config: AppConfig, bundle: dict) -> list[dict]:
    """Read current saved policy outputs only when the exact frozen source facts still match."""
    from job_intake.annotation.gold import _snapshot
    from job_intake.annotation.source_compat import check_source_compatibility
    from job_intake.config.settings import load_yaml_mapping
    from job_intake.profiles import load_streams, taxonomy_version, with_other_stream

    _, saved, _ = read_saved_records(config.database_url, limit=None, include_archived=True)
    selected = {row["job_uid"]: row for row in bundle["rows"]}
    saved = [(uid, record) for uid, record in saved if uid in selected]
    versions, classification_version = None, None
    if config.rules_path.is_file() and config.search_profiles_path.is_file():
        rules = load_yaml_mapping(config.rules_path)
        streams = with_other_stream(
            load_streams(rules, load_yaml_mapping(config.search_profiles_path)),
            rules,
        )
        versions = {stream.id: stream.version for stream in streams}
        classification_version = taxonomy_version(streams)
    predictions = read_saved_predictions(
        config.database_url,
        saved,
        profile_versions=versions,
        classification_version=classification_version,
    )
    output = []
    for uid, record in saved:
        frozen = selected[uid]
        historical = record_from_snapshot(frozen["source"])
        proof = check_source_compatibility(
            {"source_hash": frozen["source_hash"], "source": _snapshot(historical)},
            record,
            history=[
                {"method": "hash_verified_frozen_bundle_facts", "source_snapshot": frozen["source"]}
            ],
        )
        if not proof["compatible"]:
            raise ValueError("Current source facts changed since bundle: " + uid)
        output.append(
            {
                **predictions[uid],
                "source_hash": frozen["source_hash"],
                "source_binding": {
                    key: value for key, value in proof.items() if key != "source_snapshot"
                },
            }
        )
    return output


def validate_run(bundle: dict, run: dict) -> None:
    if (
        run.get("schema") != RUN_SCHEMA
        or run.get("bundle_id") != bundle["bundle_id"]
        or run.get("run_hash") != digest({k: v for k, v in run.items() if k != "run_hash"})
        or bundle["versions"].get(run.get("variant")) != run.get("version")
    ):
        raise ValueError("Run is not an unchanged member of this frozen bundle")
    expected = {row["job_uid"]: row["source_hash"] for row in bundle["rows"]}
    predictions = run.get("predictions")
    if not isinstance(predictions, dict) or any(
        uid not in expected
        or not isinstance(row, dict)
        or row.get("job_uid") != uid
        or row.get("source_hash") != expected[uid]
        for uid, row in predictions.items()
    ):
        raise ValueError("Run predictions do not bind to frozen sources")
    computed = task_metrics([row["label"] for row in bundle["rows"]], predictions)
    recorded = run.get("evaluation")
    # Additive route coverage is independently recomputable from frozen outputs.
    # Keep old immutable v1 reports valid without changing their recorded metrics.
    if (
        isinstance(recorded, dict)
        and "routing" not in recorded
        and "route" not in recorded.get("task_prediction_coverage", {})
    ):
        computed.pop("routing")
        computed["task_prediction_coverage"].pop("route")
    if recorded != computed:
        raise ValueError("Run metrics disagree with recorded outputs")


def compare_runs(
    bundle: dict,
    baseline: dict,
    candidate: dict,
    *,
    repeat: dict | None = None,
    exposure_log: Path | None = None,
) -> dict:
    for run in (baseline, candidate, *([repeat] if repeat else [])):
        validate_run(bundle, run)
    baseline, candidate = copy.deepcopy(baseline), copy.deepcopy(candidate)
    repeat = copy.deepcopy(repeat) if repeat else None
    for run in (baseline, candidate, *([repeat] if repeat else [])):
        run["evaluation"] = task_metrics(
            [row["label"] for row in bundle["rows"]], run["predictions"]
        )
    if baseline["run_id"] == candidate["run_id"]:
        raise ValueError("Baseline/candidate require separate recorded runs")
    if baseline["variant"] == candidate["variant"]:
        raise ValueError("Quality comparison requires two declared variants")
    policy = bundle["gate_policy"]
    independent = independent_holdout(bundle.get("cohort"), log=exposure_log)
    gates = []

    def gate(
        name: str,
        before: dict,
        after: dict,
        *,
        coverage: list[str],
        lower: bool = False,
        tolerance: float | None = None,
        minimum: int | None = None,
    ) -> None:
        n = after.get("denominator", 0)
        required = policy["minimum_cases"] if minimum is None else minimum
        if not n:
            status = "not_applicable"
        elif (
            not independent
            or n < required
            or len(bundle["rows"]) < policy["minimum_cases"]
            or before.get("value") is None
            or after.get("value") is None
            or any(
                run["evaluation"]["task_prediction_coverage"][task]["numerator"]
                != run["evaluation"]["task_prediction_coverage"][task]["denominator"]
                for run in (baseline, candidate)
                for task in coverage
            )
        ):
            status = "insufficient_evidence"
        else:
            allowed = policy["maximum_accuracy_regression"] if tolerance is None else tolerance
            passed = (
                after["value"] <= before["value"] + allowed
                if lower
                else after["value"] >= before["value"] - allowed
            )
            status = "pass" if passed else "fail"
        gates.append(
            {
                "name": name,
                "status": status,
                "passed": status == "pass",
                "baseline": before,
                "candidate": after,
                "N": n,
                "minimum_cases": required,
            }
        )

    before, after = baseline["evaluation"], candidate["evaluation"]
    cases = [row["label"] for row in bundle["rows"]]
    for name, observed, minimum in (
        ("human_decisions", after["counts"]["decision_labeled"], policy["minimum_cases"]),
        ("human_relevant", after["counts"]["relevant"], policy.get("minimum_relevant", 0)),
        (
            "human_full_tags",
            sum(len(row.get("tags", {})) == 19 for row in cases),
            policy.get("minimum_full_tag_cases", 0),
        ),
        (
            "human_blind_decisions",
            sum(
                row.get("method") == "blind"
                and type(row.get("relevance")) is int
                and row.get("role_label") is not None
                and row.get("gate_outcome") in {"eligible", "ineligible", "unknown"}
                for row in cases
            ),
            policy.get("minimum_blind_cases", 0),
        ),
    ):
        passed = independent and observed >= minimum
        gates.append(
            {
                "name": name,
                "status": "pass" if passed else "insufficient_evidence",
                "passed": passed,
                "N": observed,
                "minimum_cases": minimum,
                "numerator": observed,
                "denominator": minimum,
                "policy_status": "experimental_predeclared_protocol",
            }
        )
    gate(
        "profession_accuracy",
        before["role_classification"]["accuracy"],
        after["role_classification"]["accuracy"],
        coverage=["role_classification"],
    )
    gate(
        "eligibility_accuracy",
        before["eligibility"]["accuracy"],
        after["eligibility"]["accuracy"],
        coverage=["eligibility"],
    )
    for name in ("role_recall", "actionable_p1_recall", "blocker_recall", "false_block_rate"):
        gate(
            name,
            before["independent_axes"][name],
            after["independent_axes"][name],
            coverage=["role_classification"]
            if name == "role_recall"
            else ["eligibility", "decision", "ranking"]
            if name == "actionable_p1_recall"
            else ["eligibility", "decision"],
            lower=name == "false_block_rate",
            tolerance=policy["maximum_false_block_increase"]
            if name == "false_block_rate"
            else None,
            minimum=policy.get("minimum_relevant", policy["minimum_cases"])
            if name == "role_recall"
            else policy.get("minimum_blind_cases", policy["minimum_cases"]),
        )
    for tag in ("project_management", "people_interaction", "decision_influence"):
        gate(
            "scope." + tag,
            before["tags"][tag]["accuracy"],
            after["tags"][tag]["accuracy"],
            coverage=["tags." + tag],
            minimum=policy.get("minimum_full_tag_cases", policy["minimum_cases"]),
        )
    gate(
        "negative_restriction_recall",
        before["negative_restrictions"]["recall"],
        after["negative_restrictions"]["recall"],
        coverage=[
            "tags." + tag
            for tag in (
                "hiring_geography",
                "work_mode",
                "working_language",
                "work_authorization",
                "vacancy_closed",
            )
        ],
        minimum=policy.get("minimum_blind_cases", policy["minimum_cases"]),
    )
    for k in (5, 10):
        name = f"ndcg@{k}"
        old, new = before["graded_ranking"][name], after["graded_ranking"][name]
        gate(
            name,
            {**old, "ideal_dcg": old["denominator"], "denominator": old["required_n"]},
            {**new, "ideal_dcg": new["denominator"], "denominator": new["required_n"]},
            coverage=["ranking"],
            minimum=policy.get("minimum_blind_cases", policy["minimum_cases"]),
        )
    for name, field, ratio in (
        ("current_price_cost", "cost_usd", "maximum_cost_ratio"),
        ("wall_time", "wall_seconds", "maximum_time_ratio"),
    ):
        old, new = baseline["operational"], candidate["operational"]
        valid = all(
            type(run.get(field)) in {int, float}
            and math.isfinite(run[field])
            and run[field] >= 0
            and run.get("complete") is True
            for run in (old, new)
        )
        valid = valid and (
            name != "current_price_cost"
            or all(
                run.get("prices_fresh") is True and run.get("uncertain_bills") == 0
                for run in (old, new)
            )
        )
        status = (
            "insufficient_evidence"
            if not valid
            else "pass"
            if new[field] <= old[field] * policy[ratio]
            else "fail"
        )
        gates.append(
            {
                "name": name,
                "status": status,
                "passed": status == "pass",
                "baseline": old.get(field),
                "candidate": new.get(field),
                "numerator": new.get(field),
                "denominator": old.get(field),
            }
        )
    stability = None
    stability_status = "insufficient_evidence"
    if repeat:
        if (
            repeat["run_id"] in {baseline["run_id"], candidate["run_id"]}
            or repeat["variant"] != candidate["variant"]
            or repeat["version"] != candidate["version"]
        ):
            raise ValueError("Stability requires an independent repeat of candidate")
        from job_intake.annotation.accuracy import compare_stability

        stability = compare_stability(candidate["predictions"], repeat["predictions"])
        expected = {row["job_uid"] for row in bundle["rows"]}
        comparable = set(candidate["predictions"]) & set(repeat["predictions"])
        complete = comparable == expected and len(expected) >= policy["minimum_cases"]
        fields = ("role_label", "gate_outcome", "decision", "tier", "fit_score", "tags", "route")
        unchanged = sum(
            all(
                candidate["predictions"][uid].get(field) == repeat["predictions"][uid].get(field)
                for field in fields
            )
            for uid in comparable
        )
        stability_status = (
            "pass"
            if complete and unchanged == len(expected)
            else "fail"
            if complete
            else "insufficient_evidence"
        )
        stability["exact_repeat_agreement"] = proportion(unchanged, len(expected))
    gates.append(
        {
            "name": "repeat_run_stability",
            "status": stability_status,
            "passed": stability_status == "pass",
            "minimum_cases": policy["minimum_cases"],
            "measurement": stability,
        }
    )
    statuses = {row["status"] for row in gates}
    status = (
        "fail"
        if "fail" in statuses
        else "insufficient_evidence"
        if "insufficient_evidence" in statuses or repeat is None
        else "pass"
    )
    return {
        "schema": "frozen-quality-comparison-v1",
        "bundle_id": bundle["bundle_id"],
        "gate_policy": policy,
        "baseline_run": baseline["run_hash"],
        "candidate_run": candidate["run_hash"],
        "independent_holdout": independent,
        "status": status,
        "gates": gates,
        "stability": stability,
        "limitations": [
            "Insufficient evidence never authorizes a default policy change.",
            "Unknown hiring eligibility is a separate stratum.",
            "Developer inspection consumes holdout independence for later releases.",
        ],
    }


def save_bundle(bundle: dict, path: Path) -> Path:
    return write_new(path, bundle)


def comparison_markdown(report: dict) -> str:
    comparison = report.get("comparison") or report
    lines = [
        "# Frozen evaluation",
        "",
        f"Bundle: `{report['bundle_id']}`",
        "",
        f"Status: **{report['status']}**",
        "",
        "| Gate | Status | N | Baseline | Candidate |",
        "|---|---|---:|---|---|",
    ]

    def cell(value: object) -> str:
        if isinstance(value, dict):
            return str(value.get("value")) if value.get("value") is not None else "not measured"
        return str(value) if value is not None else "not measured"

    for row in comparison.get("gates", []):
        lines.append(
            f"| {row['name']} | {row['status']} | {row.get('N', '—')} | "
            f"{cell(row.get('baseline'))} | {cell(row.get('candidate'))} |"
        )
    if report.get("variants"):
        lines += [
            "",
            "| Variant | Runs | Cost USD | Cost / human relevant |",
            "|---|---:|---:|---:|",
        ]
        for name, row in report["variants"].items():
            safe_name = name.replace("|", "\\|").replace("\n", " ")
            lines.append(
                f"| {safe_name} | {row['runs']} | {row['cost_usd']} | "
                f"{cell(row.get('cost_per_confirmed_relevant'))} |"
            )
    lines += ["", "API calls by this report: 0. Missing evidence never passes a gate.", ""]
    return "\n".join(lines)


def write_comparison_report(report: dict, path: Path) -> tuple[Path, Path]:
    markdown = path.with_suffix(".md")
    if path.suffix != ".json" or path.exists() or markdown.exists():
        raise ValueError("Use new JSON and Markdown report destinations")
    write_new(path, report)
    with markdown.open("x", encoding="utf-8") as stream:
        stream.write(comparison_markdown(report))
    return path, markdown
