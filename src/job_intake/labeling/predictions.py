"""vacancy-labels-v3 prediction rows for accuracy and frozen evaluation.

The ``rules`` variant is an honest baseline only: it maps the saved tier through an
explicit table that every report states. The pipeline never applies this table.
"""

from __future__ import annotations

import math
import re

from job_intake.annotation.text import source_hash
from job_intake.labeling.schema import (
    DECISIONS,
    GATE_OUTCOMES,
    GATE_REASONS,
    PREDICTION_SCHEMA,
    RELEVANCE_VALUES,
    STATUSES,
    TAG_NAMES,
    TIERS,
)
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
            "fit_score": score if type(score) in {int, float} and math.isfinite(score) else None,
        })
    return result


def check_prediction_rows(rows: list[dict]) -> dict[str, dict]:
    """Structural check of supplied v3 prediction rows; returns them by job_uid."""
    result = {}
    for index, row in enumerate(rows, start=1):
        problem = None
        if not isinstance(row, dict) or row.get("schema") != PREDICTION_SCHEMA:
            problem = f"schema must be {PREDICTION_SCHEMA}"
        elif not isinstance(row.get("job_uid"), str) or not row["job_uid"] \
                or row["job_uid"] in result:
            problem = "job_uid must be nonempty and unique"
        elif not isinstance(row.get("source_hash"), str) \
                or not re.fullmatch(r"[0-9a-f]{64}", row["source_hash"]):
            problem = "source_hash must be a SHA-256 digest"
        elif not isinstance(row.get("labeler"), str) \
                or not re.fullmatch(r"(?:rules|llm|embedding):\S+", row["labeler"]):
            problem = "labeler must be rules:, llm: or embedding: with a name"
        elif row.get("status") not in STATUSES:
            problem = "unknown status"
        elif row.get("relevance") is not None and (
            type(row["relevance"]) is not int or row["relevance"] not in RELEVANCE_VALUES
        ):
            problem = "relevance must be an integer 0–3 or null"
        elif row.get("gate_outcome") not in (*GATE_OUTCOMES, None):
            problem = "unknown gate_outcome"
        elif not isinstance(row.get("gate_reasons"), list) \
                or any(reason not in GATE_REASONS for reason in row["gate_reasons"]):
            problem = "gate_reasons must be closed reason codes"
        elif not isinstance(row.get("tags"), dict) or set(row["tags"]) - set(TAG_NAMES):
            problem = "tags must use only the six v3 tags"
        if problem:
            raise ValueError(f"Invalid v3 prediction at row {index}: {problem}")
        result[row["job_uid"]] = row
    return result
