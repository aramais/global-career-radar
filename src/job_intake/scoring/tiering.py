from __future__ import annotations

from job_intake.models.job import EvaluatedJob, FilterDecision, JobTier


def finalize_tier(
    job: EvaluatedJob,
    threshold_a: float,
    threshold_b: float,
    *,
    tier_source: str = "score",
) -> EvaluatedJob:
    """Single source of truth for tier assignment.

    Runs after both deterministic scoring and (optional) LLM reranking, so it sees
    the final ``fit_score``. Thresholds come from the search-profile config
    (``threshold_a``/``threshold_b``). A requires a detected decision mandate.
    ``role_fit_v1`` keeps hiring decisions separate and enforces source scope caps;
    the legacy policy additionally requires PASS for A and assigns REJECT to C.
    """
    if tier_source not in {"score", "relevance"}:
        raise ValueError("tier_source must be score or relevance")
    relevance = job.evaluation.relevance
    if tier_source == "relevance" and (type(relevance) is not int or relevance not in range(4)):
        raise ValueError("relevance tiering requires a relevance value from 0 to 3")
    role_fit = job.evaluation.fit_policy == "role_fit_v1"
    if (tier_source == "score" and job.evaluation.decision == FilterDecision.REJECT
            and not role_fit):
        job.evaluation.tier = JobTier.C
        job.evaluation.bucket = "Bucket C"
        return job

    if tier_source == "relevance":
        job.evaluation.tier = {3: JobTier.A, 2: JobTier.B, 1: JobTier.C, 0: JobTier.C}[relevance]
        job.evaluation.bucket = "Bucket " + job.evaluation.tier.value
    elif (
        (role_fit or job.evaluation.decision == FilterDecision.PASS)
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
    if job.evaluation.fit_max_tier is not None:
        cap = JobTier(job.evaluation.fit_max_tier)
        order = {JobTier.A: 0, JobTier.B: 1, JobTier.C: 2}
        if order[job.evaluation.tier] < order[cap]:
            job.evaluation.tier = cap
            job.evaluation.bucket = "Bucket " + job.evaluation.tier.value
    return job
