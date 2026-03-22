from __future__ import annotations

from job_intake.models.job import EvaluatedJob, FilterDecision, JobTier


def finalize_tier(job: EvaluatedJob) -> EvaluatedJob:
    if job.evaluation.decision == FilterDecision.REJECT:
        job.evaluation.tier = JobTier.C
        job.evaluation.bucket = "Bucket C"
        return job

    if job.evaluation.fit_score >= 18 and job.evaluation.bridge_role:
        job.evaluation.tier = JobTier.A
        job.evaluation.bucket = "Bucket A"
    elif job.evaluation.fit_score >= 8:
        job.evaluation.tier = JobTier.B
        job.evaluation.bucket = "Bucket B"
    else:
        job.evaluation.tier = JobTier.C
        job.evaluation.bucket = "Bucket C"
    return job
