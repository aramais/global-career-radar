from job_intake.models.job import (
    EvaluatedJob,
    FilterDecision,
    JobEvaluation,
    JobRecord,
    JobTier,
)
from job_intake.scoring.tiering import finalize_tier

THRESHOLD_A = 14.0
THRESHOLD_B = 8.0


def _job(decision: FilterDecision, fit_score: float, bridge_role: bool) -> EvaluatedJob:
    record = JobRecord(
        source="test",
        company="Example",
        title="Product Analytics Lead",
        original_url="https://example.com/job",
    )
    evaluation = JobEvaluation(
        decision=decision,
        fit_score=fit_score,
        bridge_role=bridge_role,
    )
    return EvaluatedJob(record=record, evaluation=evaluation)


def _finalize(job: EvaluatedJob) -> EvaluatedJob:
    return finalize_tier(job, THRESHOLD_A, THRESHOLD_B)


def test_reject_always_tier_c() -> None:
    job = _finalize(_job(FilterDecision.REJECT, fit_score=99.0, bridge_role=True))
    assert job.evaluation.tier == JobTier.C
    assert job.evaluation.bucket == "Bucket C"


def test_pass_high_score_with_bridge_is_a() -> None:
    job = _finalize(_job(FilterDecision.PASS, fit_score=THRESHOLD_A, bridge_role=True))
    assert job.evaluation.tier == JobTier.A
    assert job.evaluation.bucket == "Bucket A"


def test_pass_high_score_without_bridge_is_b_not_a() -> None:
    # A-tier requires bridge_role even above threshold_a.
    job = _finalize(_job(FilterDecision.PASS, fit_score=THRESHOLD_A + 10, bridge_role=False))
    assert job.evaluation.tier == JobTier.B


def test_mid_score_is_b() -> None:
    job = _finalize(_job(FilterDecision.PASS, fit_score=THRESHOLD_B, bridge_role=True))
    assert job.evaluation.tier == JobTier.B


def test_low_score_pass_is_c() -> None:
    job = _finalize(_job(FilterDecision.PASS, fit_score=THRESHOLD_B - 1, bridge_role=True))
    assert job.evaluation.tier == JobTier.C


def test_review_never_reaches_a() -> None:
    # REVIEW is not PASS, so it cannot be A regardless of score/bridge.
    job = _finalize(_job(FilterDecision.REVIEW, fit_score=THRESHOLD_A + 5, bridge_role=True))
    assert job.evaluation.tier == JobTier.B
