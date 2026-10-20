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
    write_new_text,
)
from job_intake.annotation.eval_runner import read_saved_records, report_destinations
from job_intake.annotation.gold import validate_gold
from job_intake.annotation.text import source_hash
from job_intake.config.settings import AppConfig
from job_intake.labeling.metrics import has_relevance
from job_intake.labeling.numeric import is_finite_score
from job_intake.labeling.schema import LABELS_SCHEMA
from job_intake.models.job import JobRecord, JobStatus

BUNDLE_SCHEMA = "frozen-evaluation-v1"
RUN_SCHEMA = "frozen-evaluation-run-v1"
TASK_METRICS_VERSION = "frozen-task-metrics-v2"
LEGACY_TASK_METRICS_VERSION = "frozen-task-metrics-v1"
GATE_VERSION = "audit-quality-gates-v2"
GATE_COVERAGE_VERSION = "frozen-gate-coverage-v1"
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
    from job_intake.profiles import (
        load_streams,
        taxonomy_version,
        with_labeling_versions,
        with_other_stream,
    )

    profiles, classifier_version = None, None
    if config.rules_path.is_file() and config.search_profiles_path.is_file():
        rules = load_yaml_mapping(config.rules_path)
        streams = with_other_stream(
            load_streams(rules, load_yaml_mapping(config.search_profiles_path)),
            rules,
        )
        streams = with_labeling_versions(streams, config.labeling)
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
    validated = validate_gold(
        list(records.items()), cases, source_history=history,
        profile_ids=list(profiles) if profiles is not None else None,
    )
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
    if cases[0].get("schema") == LABELS_SCHEMA:
        # An immutable v3 bundle needs normalized v3 baseline rows as well. This
        # explicit tier-map is an evaluation baseline, never a pipeline policy.
        from job_intake.labeling.predictions import baseline_predictions

        predictions = {row["job_uid"]: row for row in baseline_predictions(
            [(uid, record) for uid, record in saved if uid in predictions], predictions,
        )}
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
    if any(row.get("label", {}).get("schema") == LABELS_SCHEMA for row in result["rows"]):
        validated = validate_gold(
            [(row["job_uid"], record_from_snapshot(row["source"])) for row in result["rows"]],
            [row["label"] for row in result["rows"]],
        )
        if not validated["valid"]:
            raise ValueError("Invalid v3 frozen labels: " + "; ".join(
                row["code"] for row in validated["errors"]
            ))
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
    cases: list[dict], predictions: dict[str, dict], *, random_seed: int = 1729,
    version: str = TASK_METRICS_VERSION,
    relevance_scale_version: str | None = None,
) -> dict:
    """Measure independent tasks; v1 only replays immutable historical runs."""
    if not isinstance(version, str) or version not in {
        TASK_METRICS_VERSION, LEGACY_TASK_METRICS_VERSION,
    }:
        raise ValueError(f"Unsupported frozen task metrics version: {version}")
    result = evaluate_vacancies(cases, predictions)
    result["task_metrics_version"] = version
    if relevance_scale_version is not None and "relevance_scale" in result:
        from job_intake.labeling.metrics import relevance_scale

        result["relevance_scale"] = relevance_scale(
            cases, predictions, version=relevance_scale_version,
        )
    labeled = [row for row in cases if type(row.get("relevance")) is int]
    relevant = [row for row in labeled if row["relevance"] >= 2]
    role_relevant = relevant if version == LEGACY_TASK_METRICS_VERSION else [
        row for row in relevant
        if isinstance(row.get("role_label"), str) and row["role_label"].strip()
    ]
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
            sum(pred(row).get("role_label") == row.get("role_label") for row in role_relevant),
            len(role_relevant),
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
        if is_finite_score(pred(row).get("fit_score"))
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


def _bundle_uses_v3_labels(bundle: dict) -> bool:
    return any(row["label"].get("schema") == LABELS_SCHEMA for row in bundle["rows"])


def _has_full_human_tags(row: dict) -> bool:
    if row.get("schema") == LABELS_SCHEMA:
        from job_intake.labeling.schema import TAG_NAMES

        return set(row.get("tags", {})) == set(TAG_NAMES)
    return len(row.get("tags", {})) == 19


def _is_v3_prediction(row: dict) -> bool:
    # These discriminators belong to v3 rows, regardless of the experiment name.
    # Legacy frozen predictions may already carry relevance, so it is not enough.
    return bool({"schema", "labeler", "variant"} & row.keys())


def _validate_v3_predictions(bundle: dict, predictions: list[dict], *, variant: str) -> None:
    from job_intake.labeling.predictions import check_prediction_rows

    if any(not isinstance(row, dict) for row in predictions):
        raise ValueError("Prediction rows must be objects")
    # A v3 bundle requires normalized v3 outputs for every experiment. Legacy
    # bundles can also receive v3 rows: their own discriminators trigger checking.
    requires_v3 = _bundle_uses_v3_labels(bundle) or variant in {"labels", "rules"}
    check_prediction_rows([
        row for row in predictions if requires_v3 or _is_v3_prediction(row)
    ])


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
    _validate_v3_predictions(bundle, predictions, variant=variant)
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
    from job_intake.profiles import (
        load_streams,
        taxonomy_version,
        with_labeling_versions,
        with_other_stream,
    )

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
        streams = with_labeling_versions(streams, config.labeling)
        versions = {stream.id: stream.version for stream in streams}
        classification_version = taxonomy_version(streams)
    predictions = read_saved_predictions(
        config.database_url,
        saved,
        profile_versions=versions,
        classification_version=classification_version,
    )
    v3 = _bundle_uses_v3_labels(bundle)
    if v3:
        from job_intake.labeling.predictions import baseline_predictions

        predictions = {row["job_uid"]: row for row in baseline_predictions(
            [(uid, record) for uid, record in saved if uid in predictions], predictions,
        )}
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
        row = {**predictions[uid], "source_hash": frozen["source_hash"]}
        if not v3:
            row["source_binding"] = {
                key: value for key, value in proof.items() if key != "source_snapshot"
            }
        output.append(row)
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
    recorded = run.get("evaluation")
    scale = recorded.get("relevance_scale") if isinstance(recorded, dict) else None
    scale_version = scale.get("version") if isinstance(scale, dict) else None
    # A recorded metrics version selects numeric replay, never a validation bypass.
    _validate_v3_predictions(bundle, list(predictions.values()), variant=run["variant"])
    computed = task_metrics(
        [row["label"] for row in bundle["rows"]], predictions,
        version=_recorded_task_metrics_version(run),
        relevance_scale_version=scale_version,
    )
    # An absent marker identifies the original formula; never upgrade saved evidence.
    if isinstance(recorded, dict) and "task_metrics_version" not in recorded:
        computed.pop("task_metrics_version")
    # Additive route coverage is independently recomputable from frozen outputs.
    # Keep old immutable v1 reports valid without changing their recorded metrics.
    if (
        isinstance(recorded, dict)
        and "routing" not in recorded
        and "route" not in recorded.get("task_prediction_coverage", {})
    ):
        computed.pop("routing")
        computed["task_prediction_coverage"].pop("route")
    # The additive relevance_scale block is equally recomputable; older runs lack it.
    if isinstance(recorded, dict) and "relevance_scale" not in recorded:
        computed.pop("relevance_scale", None)
    if recorded != computed:
        raise ValueError("Run metrics disagree with recorded outputs")


def _recorded_task_metrics_version(run: dict) -> str:
    recorded = run.get("evaluation")
    if not isinstance(recorded, dict):
        raise ValueError("Run metrics disagree with recorded outputs")
    return recorded.get("task_metrics_version", LEGACY_TASK_METRICS_VERSION)


def _gate_prediction_coverage(cases: list[dict], predictions: dict[str, dict], *,
                              version: str) -> dict[str, dict]:
    """Comparison-only coverage of each metric's own human sample and inputs.

    Historical task metrics remain immutable. Their shared accuracy coverage uses
    complete human decisions, which is narrower than several independent tasks.
    """
    from job_intake.annotation.accuracy import _NEGATIVES, _value
    from job_intake.annotation.gold import TAG_VALUES

    gates = {"eligible", "ineligible", "unknown"}
    labeled = [row for row in cases if type(row.get("relevance")) is int]
    relevant = [row for row in labeled if row["relevance"] >= 2]
    role_relevant = relevant if version == LEGACY_TASK_METRICS_VERSION else [
        row for row in relevant
        if isinstance(row.get("role_label"), str) and row["role_label"].strip()
    ]

    def pred(row: dict) -> dict:
        return predictions.get(row["job_uid"], {})

    def role(row: dict) -> bool:
        value = pred(row).get("role_label")
        return isinstance(value, str) and bool(value.strip())

    def gate(row: dict) -> bool:
        return pred(row).get("gate_outcome") in gates

    def tag(row: dict, name: str) -> bool:
        return _value(pred(row).get("tags", {}).get(name)) in TAG_VALUES[name]

    def coverage(rows: list[dict], present) -> dict:
        return proportion(sum(present(row) for row in rows), len(rows))

    result = {
        "profession_accuracy": coverage(
            [row for row in cases if isinstance(row.get("role_label"), str)], role),
        "eligibility_accuracy": coverage(
            [row for row in cases if row.get("gate_outcome") in gates], gate),
        "role_recall": coverage(role_relevant, role),
        "actionable_p1_recall": coverage(
            [row for row in relevant if row.get("gate_outcome") == "eligible"],
            lambda row: gate(row) and pred(row).get("decision") in {"pass", "review", "reject"}
            and pred(row).get("tier") in {"A", "B", "C"}),
        "blocker_recall": coverage(
            [row for row in cases if row.get("gate_outcome") == "ineligible"], gate),
        "false_block_rate": coverage(
            [row for row in cases if row.get("gate_outcome") == "eligible"], gate),
    }
    ranking = coverage(
        [row for row in labeled if row.get("role_label") != "other"],
        lambda row: pred(row).get("tier") in {"A", "B", "C"}
        and is_finite_score(pred(row).get("fit_score")),
    )
    result.update({f"ndcg@{k}": ranking for k in (5, 10)})
    for name in ("project_management", "people_interaction", "decision_influence"):
        result["scope." + name] = coverage(
            [row for row in cases if _value(row.get("tags", {}).get(name)) is not None],
            lambda row, name=name: tag(row, name),
        )
    negative_pairs = [(row, name) for row in cases for name, values in _NEGATIVES.items()
                      if _value(row.get("tags", {}).get(name)) in values]
    result["negative_restriction_recall"] = proportion(
        sum(tag(row, name) for row, name in negative_pairs), len(negative_pairs),
    )
    return result


def _repeat_prediction_coverage(cases: list[dict], predictions: dict[str, dict]) -> dict:
    """Observed human axes only; identical abstentions do not prove repeatability.

    Legacy outputs need no ordinal field, but an explicit relevance or v3 record
    must cover a known human relevance. Unlabeled tags are never required.
    """
    from job_intake.annotation.accuracy import _value
    from job_intake.annotation.gold import TAG_VALUES as LEGACY_TAG_VALUES
    from job_intake.labeling.metrics import _focus_values
    from job_intake.labeling.schema import DOMAIN_REQUIREMENTS, INDUSTRIES, TAG_VALUES

    observations = []
    for row in cases:
        predicted = predictions.get(row["job_uid"], {})
        if isinstance(row.get("role_label"), str):
            value = predicted.get("role_label")
            observations.append(isinstance(value, str) and bool(value.strip()))
        if row.get("gate_outcome") in {"eligible", "ineligible", "unknown"}:
            observations.append(predicted.get("gate_outcome") in
                                {"eligible", "ineligible", "unknown"})
        if type(row.get("relevance")) is int and (
            row.get("schema") == LABELS_SCHEMA or _is_v3_prediction(predicted)
            or "relevance" in predicted
        ):
            value = predicted.get("relevance")
            observations.append(type(value) is int and value in range(4))
        human_tags, machine_tags = row.get("tags", {}), predicted.get("tags", {})
        if row.get("schema") != LABELS_SCHEMA:
            for name, label in human_tags.items():
                if name in LEGACY_TAG_VALUES and _value(label) is not None:
                    observations.append(_value(machine_tags.get(name)) in LEGACY_TAG_VALUES[name])
            continue
        for name, values in TAG_VALUES.items():
            if _value(human_tags.get(name)) is not None:
                observations.append(_value(machine_tags.get(name)) in values)
        domain = human_tags.get("domain_fit", {})
        machine_domain = machine_tags.get("domain_fit", {})
        for field, values in (("requirement", DOMAIN_REQUIREMENTS), ("industry", INDUSTRIES)):
            if domain.get(field) is not None:
                observations.append(machine_domain.get(field) in values)
        if domain.get("requirement") in {"preferred", "required"}:
            observations.append(machine_domain.get("requirement") == "none" or (
                machine_domain.get("requirement") in {"preferred", "required"}
                and machine_domain.get("required_domain") in INDUSTRIES))
        if _focus_values(human_tags.get("preferred_focus")) is not None:
            observations.append(_focus_values(machine_tags.get("preferred_focus")) is not None)
    return proportion(sum(observations), len(observations))


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
    versions = {
        _recorded_task_metrics_version(run)
        for run in (baseline, candidate, *([repeat] if repeat else []))
    }
    if len(versions) != 1:
        raise ValueError("Cannot compare runs with different frozen task metrics versions")
    metrics_version = versions.pop()
    baseline, candidate = copy.deepcopy(baseline), copy.deepcopy(candidate)
    repeat = copy.deepcopy(repeat) if repeat else None
    for run in (baseline, candidate, *([repeat] if repeat else [])):
        run["evaluation"] = task_metrics(
            [row["label"] for row in bundle["rows"]], run["predictions"],
            version=metrics_version,
        )
    if baseline["run_id"] == candidate["run_id"]:
        raise ValueError("Baseline/candidate require separate recorded runs")
    if baseline["variant"] == candidate["variant"]:
        raise ValueError("Quality comparison requires two declared variants")
    policy = bundle["gate_policy"]
    independent = independent_holdout(bundle.get("cohort"), log=exposure_log)
    gates = []
    cases = [row["label"] for row in bundle["rows"]]
    prediction_coverage = {
        name: _gate_prediction_coverage(cases, run["predictions"], version=metrics_version)
        for name, run in (("baseline", baseline), ("candidate", candidate), ("repeat", repeat))
        if run is not None
    }

    def gate(
        name: str,
        before: dict,
        after: dict,
        *,
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
                prediction_coverage[run][name]["numerator"]
                != prediction_coverage[run][name]["denominator"]
                for run in ("baseline", "candidate")
            )
            or any(
                "required_n" in metric and (metric.get("status") != "measured"
                                            or metric.get("prediction_n") != metric["required_n"])
                for metric in (before, after)
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
                "prediction_coverage": {
                    run: prediction_coverage[run][name] for run in ("baseline", "candidate")
                },
            }
        )

    before, after = baseline["evaluation"], candidate["evaluation"]
    for name, observed, minimum in (
        ("human_decisions", after["counts"]["decision_labeled"], policy["minimum_cases"]),
        ("human_relevant", after["counts"]["relevant"], policy.get("minimum_relevant", 0)),
        (
            "human_full_tags",
            sum(_has_full_human_tags(row) for row in cases),
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
    )
    gate(
        "eligibility_accuracy",
        before["eligibility"]["accuracy"],
        after["eligibility"]["accuracy"],
    )
    for name in ("role_recall", "actionable_p1_recall", "blocker_recall", "false_block_rate"):
        gate(
            name,
            before["independent_axes"][name],
            after["independent_axes"][name],
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
            minimum=policy.get("minimum_full_tag_cases", policy["minimum_cases"]),
        )
    gate(
        "negative_restriction_recall",
        before["negative_restrictions"]["recall"],
        after["negative_restrictions"]["recall"],
        minimum=policy.get("minimum_blind_cases", policy["minimum_cases"]),
    )
    for k in (5, 10):
        name = f"ndcg@{k}"
        old, new = before["graded_ranking"][name], after["graded_ranking"][name]
        gate(
            name,
            {**old, "ideal_dcg": old["denominator"], "denominator": old["required_n"]},
            {**new, "ideal_dcg": new["denominator"], "denominator": new["required_n"]},
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
        if _bundle_uses_v3_labels(bundle) or any(
            has_relevance(run["predictions"])
            or run["variant"] in {"labels", "rules"}
            or any(_is_v3_prediction(row) for row in run["predictions"].values())
            for run in (candidate, repeat)
        ):
            from job_intake.labeling.metrics import compare_stability as labels_stability

            stability["labels_v3"] = labels_stability(candidate["predictions"],
                                                      repeat["predictions"])
        expected = {row["job_uid"] for row in bundle["rows"]}
        comparable = set(candidate["predictions"]) & set(repeat["predictions"])
        complete = comparable == expected and len(expected) >= policy["minimum_cases"]
        repeat_coverage = {
            name: _repeat_prediction_coverage(cases, run["predictions"])
            for name, run in (("candidate", candidate), ("repeat", repeat))
        }
        complete = complete and all(
            metric["numerator"] == metric["denominator"]
            for name in ("candidate", "repeat")
            for metric in (*prediction_coverage[name].values(), repeat_coverage[name])
        )
        # Legacy predictions carry no relevance, so its comparison is None == None for them.
        fields = ("role_label", "gate_outcome", "decision", "tier", "fit_score", "tags", "route",
                  "relevance")
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
        stability["human_prediction_coverage"] = repeat_coverage
    gates.append(
        {
            "name": "repeat_run_stability",
            "status": stability_status,
            "passed": stability_status == "pass",
            "minimum_cases": policy["minimum_cases"],
            "measurement": stability,
        }
    )
    scales = {
        name: _relevance_summary(run["evaluation"]["relevance_scale"])
        for name, run in (("baseline", baseline), ("candidate", candidate))
        if "relevance_scale" in run["evaluation"]
    }
    statuses = {row["status"] for row in gates}
    status = (
        "fail"
        if "fail" in statuses
        else "insufficient_evidence"
        if "insufficient_evidence" in statuses or repeat is None
        else "pass"
    )
    result = {
        "schema": "frozen-quality-comparison-v1",
        "task_metrics_version": metrics_version,
        "gate_coverage_version": GATE_COVERAGE_VERSION,
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
    if scales:
        # Descriptive only: the unified 0–3 scale is not yet a predeclared gate.
        result["relevance_scale"] = scales
    return result


def _relevance_summary(block: dict) -> dict:
    return {
        "N": block["N"],
        "coverage": block["coverage"]["value"],
        "qwk": block["qwk"]["value"],
        "exact": block["exact"]["value"],
        "within_one": block["within_one"]["value"],
        "relevant_recall": block["relevant"]["recall"]["value"],
        "relevant_precision": block["relevant"]["precision"]["value"],
        "precision_at_10": block["precision_at"]["10"]["value"],
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
    scales = comparison.get("relevance_scale") or {}
    if scales:
        lines += [
            "",
            "Relevance scale 0–3 (descriptive, not a gate): " + "; ".join(
                f"{name} QWK {cell(row['qwk'])}, exact {cell(row['exact'])}, "
                f"recall≥2 {cell(row['relevant_recall'])}, "
                f"precision≥2 {cell(row['relevant_precision'])}"
                for name, row in scales.items()
            ) + ".",
        ]
    lines += ["", "API calls by this report: 0. Missing evidence never passes a gate.", ""]
    return "\n".join(lines)


def comparison_destinations(path: Path, *, protected_paths: list[Path] | None = None
                            ) -> tuple[Path, Path]:
    """Validate both lexical report names before consuming an exposure journal."""
    if path.suffix != ".json":
        raise ValueError("Use new JSON and Markdown report destinations")
    try:
        return report_destinations([path, path.with_suffix(".md")],
                                   protected_paths=protected_paths or [])
    except ValueError as exc:
        raise ValueError(f"Use new JSON and Markdown report destinations: {exc}") from exc


def write_comparison_report(report: dict, path: Path) -> tuple[Path, Path]:
    path, markdown = comparison_destinations(path)
    write_new(path, report)
    write_new_text(markdown, comparison_markdown(report))
    return path, markdown
