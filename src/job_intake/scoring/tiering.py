from __future__ import annotations

from job_intake.models.job import EvaluatedJob, FilterDecision, JobTier


def finalize_tier(
    job: EvaluatedJob,
    threshold_a: float,
    threshold_b: float,
) -> EvaluatedJob:
    """Single source of truth for tier assignment.

    Runs after both deterministic scoring and (optional) LLM reranking, so it sees
    the final ``fit_score``. Thresholds come from the search-profile config
    (``threshold_a``/``threshold_b``) — no hardcoded cutoffs. A-tier additionally
    requires a passing decision and a detected bridge-role signal.
    """
    if job.evaluation.decision == FilterDecision.REJECT:
        job.evaluation.tier = JobTier.C
        job.evaluation.bucket = "Bucket C"
        return job

    if (
        job.evaluation.decision == FilterDecision.PASS
        and job.evaluation.fit_score >= threshold_a
        and job.evaluation.bridge_role
    ):
        job.evaluation.tier = JobTier.A
        job.evaluation.bucket = "Bucket A"
    elif job.evaluation.fit_score >= threshold_b:
        job.evaluation.tier = JobTier.B
        job.evaluation.bucket = "Bucket B"
    else:
        job.evaluation.tier = JobTier.C
        job.evaluation.bucket = "Bucket C"
    return job
