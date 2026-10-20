"""Free routing gates; hiring blockers do not suppress relevance labeling."""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace

from job_intake.annotation.text import source_units
from job_intake.labeling.rubric import load_rubric
from job_intake.models.job import JobEvaluation, JobRecord, JobStatus

OUTSIDE_TITLE = re.compile(
    r"\b(?:engineer|developer|designer|account executive|sales|recruiter|"
    r"customer support|technical support|counsel|legal)\b",
    re.I,
)


@dataclass(slots=True)
class GateResult:
    stop: bool = False
    status: str = "continue"
    relevance: int | None = None
    gate_outcome: str = "unknown"
    gate_reasons: list[str] = field(default_factory=list)
    route: str | None = None
    evidence: list[dict] = field(default_factory=list)


def rule_gate_reasons(evaluation: JobEvaluation) -> list[str]:
    """Translate known operational blockers without fabricating a geography fact."""
    result = []
    for signal in evaluation.blocker_signals:
        text = str(signal).casefold()
        if text.startswith(("status:", "status_phrase:", "annotation:status:")):
            reason = "vacancy_closed"
        elif text.startswith("annotation:hiring_labels_v3:"):
            reason = text.removeprefix("annotation:hiring_labels_v3:")
            if reason not in {
                "hiring_region_restricted", "work_authorization_required", "office_outside_sp",
                "other_language_required", "vacancy_closed",
            }:
                continue
        elif (text == "work_authorization_required" or text.startswith("phrase_blocker:")
              and any(word in text for word in ("authoriz", "citizen", "visa", "work permit"))):
            reason = "work_authorization_required"
        elif text.startswith(("geo_blocker:", "phrase_blocker:")) and any(
            word in text for word in ("office", "onsite", "on-site", "on site", "hybrid")
        ):
            reason = "office_outside_sp"
        elif text.startswith(("language:", "annotation:working_language:")):
            reason = "other_language_required"
        elif text.startswith(("geo_blocker:", "annotation:hiring_exclusion:",
                              "annotation:hiring_location:")):
            reason = "hiring_region_restricted"
        else:
            # Company, occupation, timezone and arbitrary phrase policies have no
            # equivalent in the closed v3 reason enum. Preserve ineligibility,
            # but do not misrepresent it as a proven geographic restriction.
            continue
        if reason not in result:
            result.append(reason)
    return result


def free_gates(
    record: JobRecord, evaluation: JobEvaluation, classification: dict, config, *, streams=()
) -> GateResult:
    result = GateResult()
    if evaluation.eligibility_status == "ineligible":
        result.gate_outcome = "ineligible"
        result.gate_reasons = rule_gate_reasons(evaluation)
    if record.status == JobStatus.CLOSED:
        return GateResult(True, "gated", None, "ineligible", ["vacancy_closed"], "closed")
    metadata = record.source_metadata
    if metadata.get("description_complete") is False or any(
        metadata.get(key) is True for key in ("description_truncated", "truncated")
    ):
        result.stop, result.status, result.route = True, "rules_only", "needs_fetch"
        return result
    units = source_units(replace(record))
    if sum(len(unit["text"]) for unit in units) > config.max_source_chars:
        result.stop, result.status, result.route = True, "rules_only", "too_long"
        return result
    positive = any(
        signal.casefold() in record.title.casefold()
        for stream in streams
        for signal in stream.rules.positive_title_signals
    )
    if (
        classification.get("label") == "other"
        and classification.get("method") != "model"
        and OUTSIDE_TITLE.search(record.title)
        and not positive
        and load_rubric(config.rubric_path).aggregation is not None
    ):
        result.stop, result.status, result.relevance, result.route = (
            True,
            "gated",
            0,
            "title_out_of_family",
        )
        result.evidence = [
            {"unit_id": unit["unit_id"], "rule": "title_out_of_family"}
            for unit in units
            if unit["field"] == "title"
        ]
    return result
