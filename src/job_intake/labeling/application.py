"""Apply accepted v3 hiring facts without coupling hiring to role relevance."""

from __future__ import annotations

from job_intake.annotation.text import source_hash
from job_intake.models.job import FilterDecision, JobEvaluation, JobRecord

_HIRING_UNKNOWN_RISKS = (
    "geography:", "language:", "authorization:",
    "annotation:work_authorization", "annotation:timezone_", "annotation:work_mode_",
    "annotation:incomplete_source_review", "annotation:conflicting_requirements",
    "labels_v3:hiring_unknown",
)
_LABEL_BLOCK_PREFIX = "annotation:hiring_labels_v3:"
_GATE_REASONS = {
    "hiring_region_restricted", "work_authorization_required", "office_outside_sp",
    "other_language_required", "vacancy_closed",
}


def apply_labels_hiring(record: JobRecord, evaluation: JobEvaluation, labels: object) -> None:
    """Bridge an effective labels-mode result to normal hiring/queue decisions.

    Callers select labels mode; shadow/legacy must not invoke this function. Only
    accepted, confirmed and current-source results apply. Existing rule blockers
    remain authoritative, and source conflicts cannot become confirmed eligibility.
    Scores, relevance and relevance-derived category are not changed here.
    """
    if (
        not isinstance(labels, dict)
        or labels.get("schema") != "vacancy-labels-v3"
        or labels.get("status") not in {"labeled", "gated"}
        or labels.get("rubric_status") != "confirmed"
        or labels.get("source_hash") != source_hash(record)
        or labels.get("gate_outcome") not in {"eligible", "ineligible", "unknown"}
    ):
        return
    # This runs after the ordinary rule refresh. It must never clear a rule-based
    # hiring blocker, even when a model reported an otherwise positive fact set.
    if evaluation.eligibility_status == "ineligible":
        evaluation.decision = FilterDecision.REJECT
        return
    outcome = labels["gate_outcome"]
    if record.source_metadata.get("source_conflicts") and outcome == "eligible":
        outcome = "unknown"
    evaluation.blocker_signals = [
        signal for signal in evaluation.blocker_signals
        if not signal.startswith(_LABEL_BLOCK_PREFIX)
    ]
    if outcome == "ineligible":
        reasons = labels.get("gate_reasons", [])
        reasons = [reason for reason in reasons
                   if isinstance(reason, str) and reason in _GATE_REASONS] \
            if isinstance(reasons, list) else []
        evaluation.blocker_signals.extend(
            _LABEL_BLOCK_PREFIX + reason for reason in dict.fromkeys(reasons or ["ineligible"])
        )
        evaluation.eligibility_status = "ineligible"
        evaluation.eligibility_evidence_status = "blocked"
        evaluation.decision = FilterDecision.REJECT
    elif outcome == "eligible":
        evaluation.risks = [risk for risk in evaluation.risks
                            if not risk.startswith(_HIRING_UNKNOWN_RISKS)]
        evaluation.eligibility_status = "eligible"
        evaluation.eligibility_evidence_status = "confirmed"
        if evaluation.decision != FilterDecision.REJECT and not evaluation.risks:
            evaluation.decision = FilterDecision.PASS
    else:
        evaluation.eligibility_status = "unknown"
        evaluation.eligibility_evidence_status = "unconfirmed"
        if "labels_v3:hiring_unknown" not in evaluation.risks:
            evaluation.risks.append("labels_v3:hiring_unknown")
        if evaluation.decision != FilterDecision.REJECT:
            evaluation.decision = FilterDecision.REVIEW
