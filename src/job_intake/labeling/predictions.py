"""vacancy-labels-v3 prediction rows for accuracy and frozen evaluation.

The ``rules`` variant is an honest baseline only: it maps the saved tier through an
explicit table that every report states. The pipeline never applies this table.
"""

from __future__ import annotations

from job_intake.annotation.text import source_hash
from job_intake.labeling.numeric import is_finite_score
from job_intake.labeling.schema import (
    DECISIONS,
    GATE_OUTCOMES,
    PREDICTION_SCHEMA,
    TIERS,
)
from job_intake.labeling.schema_validation import json_schema_errors
from job_intake.models.job import JobRecord

BASELINE_LABELER = "rules:tier-map-v1"
BASELINE_VARIANT = "rules"
TIER_RELEVANCE = {"A": 3, "B": 2, "C": 1}
OTHER_ROLE_RELEVANCE = 0
TIER_MAP_NOTE = (
    "Baseline relevance maps the saved tier A→3, B→2, C→1 and role other→0; "
    "a missing or stale saved evaluation abstains. Reports only, never the pipeline."
)


def baseline_relevance(saved: dict) -> int | None:
    if saved.get("role_label") == "other":
        return OTHER_ROLE_RELEVANCE
    return TIER_RELEVANCE.get(saved.get("tier"))


def baseline_predictions(rows: list[tuple[str, JobRecord]], saved: dict[str, dict]
                         ) -> list[dict]:
    """Variant 'rules': relevance from the saved tier by the explicit table above."""
    result = []
    for uid, record in rows:
        prediction = saved.get(uid) or {}
        role = prediction.get("role_label")
        score = prediction.get("fit_score")
        route = prediction.get("route")
        result.append({
            "schema": PREDICTION_SCHEMA, "job_uid": uid, "source_hash": source_hash(record),
            "labeler": BASELINE_LABELER, "variant": BASELINE_VARIANT, "status": "rules_only",
            "rubric_version": None, "prompt_version": None, "aggregation_version": None,
            "role_label": role if isinstance(role, str) and role else None,
            "relevance": baseline_relevance(prediction),
            "gate_outcome": prediction.get("gate_outcome")
            if prediction.get("gate_outcome") in GATE_OUTCOMES else None,
            "gate_reasons": [], "tags": {},
            "route": route if isinstance(route, str) and route else None,
            "decision": prediction.get("decision")
            if prediction.get("decision") in DECISIONS else None,
            "tier": prediction.get("tier") if prediction.get("tier") in TIERS else None,
            "fit_score": score if is_finite_score(score) else None,
        })
    return result


def check_prediction_rows(
    rows: list[dict], *, profile_ids: list[str] | None = None,
) -> dict[str, dict]:
    """Validate the full closed v3 schema before any row reaches evaluation."""
    if not isinstance(rows, list):
        raise ValueError("V3 predictions must be a list of records")
    allowed_roles = None if profile_ids is None else {*profile_ids, "other", None}
    result = {}
    for index, row in enumerate(rows, start=1):
        if problems := json_schema_errors(row, kind="prediction"):
            raise ValueError(f"Invalid v3 prediction at row {index}: {problems[0]['message']}")
        if allowed_roles is not None and row["role_label"] not in allowed_roles:
            raise ValueError(f"Invalid v3 prediction at row {index}: "
                             "$.role_label must be an enabled profile ID, other or null")
        if row["job_uid"] in result:
            raise ValueError(f"Invalid v3 prediction at row {index}: $.job_uid must be unique")
        result[row["job_uid"]] = row
    return result
