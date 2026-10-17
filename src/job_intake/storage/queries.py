"""Shared SQL selection of evaluations valid for the configured search profiles."""

from __future__ import annotations

from sqlalchemy import and_, case, false, func, or_, select

from job_intake.storage.models import JobORM, JobProfileEvaluationORM


def classification_is_current(job, evaluation=None) -> bool:
    """Check persisted provenance without loading the vacancy's full description."""
    value = getattr(job, "role_classification", None)
    if not isinstance(value, dict) or not (
        value.get("label") == getattr(job, "role_label", None)
        and bool(value.get("label"))
        and value.get("content_hash") == getattr(job, "content_hash", None)
        and isinstance(value.get("source_hash"), str) and bool(value["source_hash"])
        and isinstance(value.get("version"), str) and bool(value["version"])
    ):
        return False
    return evaluation is None or (
        evaluation.profile_id == job.role_label
        and evaluation.content_hash == job.content_hash
        and value["version"] == evaluation.classification_version
        and value["source_hash"] == evaluation.classification_source_hash
    )


def ranked_profile_evaluations(
    active_profile_versions: dict[str, str] | None, profile_id: str | None = None,
):
    """Return the one relevance evaluation for the vacancy's assigned role.

    ``None`` omits only the criteria-version filter; classification and source
    provenance are always required. An empty map means no current evaluations.
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
    ).join(JobORM, JobORM.job_uid == evaluation.job_uid).where(
        JobORM.role_label == evaluation.profile_id,
        evaluation.content_hash == JobORM.content_hash,
        JobORM.role_classification["label"].as_string() == JobORM.role_label,
        JobORM.role_classification["content_hash"].as_string() == JobORM.content_hash,
        JobORM.role_classification["version"].as_string() == evaluation.classification_version,
        JobORM.role_classification["source_hash"].as_string()
        == evaluation.classification_source_hash,
        evaluation.classification_version != "",
        evaluation.classification_source_hash != "",
    )
    if active_profile_versions is not None:
        query = query.where(
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
