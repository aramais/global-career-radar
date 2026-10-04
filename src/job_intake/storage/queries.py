"""Shared SQL selection of evaluations valid for the configured search profiles."""

from __future__ import annotations

from sqlalchemy import and_, case, false, func, or_, select

from job_intake.storage.models import JobORM, JobProfileEvaluationORM


def ranked_profile_evaluations(
    active_profile_versions: dict[str, str] | None, profile_id: str | None = None,
):
    """Return all eligible evaluations, ranked within each vacancy.

    ``None`` preserves legacy consumers' unrestricted selection. A supplied map
    requires both current criteria and current source content, including an
    empty map (no current evaluations). Filter a requested profile before rank.
    """
    evaluation = JobProfileEvaluationORM
    query = select(
        evaluation,
        func.row_number().over(
            partition_by=evaluation.job_uid,
            order_by=(
                case({"A": 0, "B": 1, "C": 2}, value=evaluation.tier, else_=3),
                case({"pass": 0, "review": 1, "reject": 2}, value=evaluation.decision, else_=3),
                evaluation.fit_score.desc(),
                evaluation.profile_id,
            ),
        ).label("evaluation_rank"),
    )
    if active_profile_versions is not None:
        query = query.join(JobORM, JobORM.job_uid == evaluation.job_uid).where(
            evaluation.content_hash == JobORM.content_hash,
            or_(
                *(
                    and_(evaluation.profile_id == profile, evaluation.profile_version == version)
                    for profile, version in active_profile_versions.items()
                )
            ) if active_profile_versions else false(),
        )
    if profile_id is not None:
        query = query.where(evaluation.profile_id == profile_id)
    return query.subquery("ranked_profile_evaluations")
