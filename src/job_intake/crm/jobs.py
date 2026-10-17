"""Bounded CRM vacancy queries; full descriptions are loaded only for a detail view."""

from __future__ import annotations

import math
from datetime import UTC

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session, load_only, selectinload

from job_intake.storage.models import JobORM, JobProfileEvaluationORM
from job_intake.storage.queries import classification_is_current, ranked_profile_evaluations

JOB_QUERY_FIELDS = {"profile_id", "job_archive", "query", "tier", "score_min", "score_max",
                    "sort", "offset", "limit"}
PUBLIC_ANNOTATION_FIELDS = {
    "version", "status", "method", "summary", "claims", "issues", "extract_model", "review_model",
    "extract_provider", "review_provider", "coverage", "description_complete",
}
LIST_COLUMNS = (
    JobORM.id, JobORM.job_uid, JobORM.company, JobORM.title, JobORM.content_hash,
    JobORM.archived_at, JobORM.archive_version, JobORM.apply_url, JobORM.original_url,
    JobORM.location_text, JobORM.remote_text,
    JobORM.role_label, JobORM.role_classification,
)
EVALUATION_COLUMNS = (
    JobProfileEvaluationORM.job_uid, JobProfileEvaluationORM.profile_id,
    JobProfileEvaluationORM.profile_name, JobProfileEvaluationORM.profile_version,
    JobProfileEvaluationORM.content_hash, JobProfileEvaluationORM.tier,
    JobProfileEvaluationORM.decision, JobProfileEvaluationORM.fit_score,
    JobProfileEvaluationORM.classification_version,
    JobProfileEvaluationORM.classification_source_hash,
)


def _integer(query: dict, key: str, default: int, maximum: int | None = None) -> int:
    value = query.get(key, "")
    if value == "":
        return default
    if not isinstance(value, str) or not value.isascii() or not value.isdigit():
        raise ValueError(f"{key} must be a non-negative integer")
    number = int(value)
    if (key == "limit" and number < 1) or (maximum is not None and number > maximum):
        raise ValueError(f"{key} must be between 1 and {maximum}")
    return number


def _score(query: dict, key: str) -> float | None:
    value = query.get(key, "")
    if value == "":
        return None
    try:
        number = float(value)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{key} must be a finite number") from exc
    if not math.isfinite(number):
        raise ValueError(f"{key} must be a finite number")
    return number


def current_evaluations(job: JobORM, versions: dict[str, str]) -> list[JobProfileEvaluationORM]:
    return sorted(
        (p for p in job.profile_evaluations
         if classification_is_current(job, p)
         and versions.get(p.profile_id) == p.profile_version
         and p.content_hash == job.content_hash),
        key=lambda p: ({"A": 0, "B": 1, "C": 2}.get(p.tier, 3),
                       {"pass": 0, "review": 1, "reject": 2}.get(p.decision, 3),
                       -p.fit_score, p.profile_id),
    )


def _public_prefilter(value: object) -> dict | None:
    """Expose routing evidence, without API usage, cache internals or full source units."""
    if not isinstance(value, dict):
        return None
    result = {
        key: value[key] for key in ("routing", "decision", "reason_code", "reason", "version")
        if isinstance(value.get(key), str)
    }
    evidence = value.get("evidence", [])
    result["evidence"] = [
        {key: row[key] for key in ("unit_id", "source_snippet")
         if isinstance(row.get(key), str)}
        for row in evidence if isinstance(row, dict)
    ] if isinstance(evidence, list) else []
    blockers = value.get("blockers", [])
    if isinstance(blockers, list):
        result["blockers"] = [item for item in blockers if isinstance(item, str)]
    return result


def serialize_job(job: JobORM, versions: dict[str, str], *, profile: str | None = None,
                  detailed: bool = False) -> dict:
    evaluations = current_evaluations(job, versions)
    classified = classification_is_current(job, next(iter(job.profile_evaluations), None))
    best = next(iter(evaluations), None)
    if profile and job.role_label != profile:
        best = None
        evaluations = []
    scored = best is not None and job.role_label != "other"
    archived_at = job.archived_at
    if archived_at is not None and archived_at.tzinfo is None:
        archived_at = archived_at.replace(tzinfo=UTC)
    result = {
        "job_uid": job.job_uid, "company": job.company, "title": job.title,
        "archived": archived_at is not None,
        "archived_at": archived_at.isoformat() if archived_at else None,
        "archive_version": job.archive_version, "url": job.apply_url or job.original_url,
        "location": job.location_text, "remote": job.remote_text,
        "role_label": job.role_label,
        "role_name": (job.role_classification or {}).get("name"),
        "classification_reason": (job.role_classification or {}).get("reason"),
        "classification_state": "current" if classified else "needs_reclassification",
        "evaluation_state": ("unscored" if classified and job.role_label == "other"
                             else "current" if best else "needs_reevaluation"),
        "eligibility": ({"decision": best.decision,
                         **({"blocker_signals": best.blocker_signals, "risks": best.risks}
                            if detailed else {})} if best else None),
        "relevance": ({"id": best.profile_id, "name": best.profile_name,
                       "score": best.fit_score, "tier": best.tier, "decision": best.decision,
                       **({"fit_reason": best.fit_reason, "risks": best.risks,
                           "blocker_signals": best.blocker_signals} if detailed else {})}
                      if scored else None),
        "fit_score": best.fit_score if scored else None, "tier": best.tier if scored else None,
        "decision": best.decision if best else None,
        "profiles": [{"id": p.profile_id, "name": p.profile_name, "score": p.fit_score,
                      "tier": p.tier, "decision": p.decision,
                      **({"fit_reason": p.fit_reason, "risks": p.risks,
                          "blocker_signals": p.blocker_signals} if detailed else {})}
                     for p in evaluations if job.role_label != "other"],
    }
    if detailed:
        result["description"] = job.description_clean or job.description_raw
        result["annotation"] = {key: value for key, value in (job.annotation or {}).items()
                                if key in PUBLIC_ANNOTATION_FIELDS}
        prefilter = _public_prefilter((job.annotation or {}).get("prefilter"))
        if prefilter is not None:
            result["annotation"]["prefilter"] = prefilter
    return result


def list_job_page(session: Session, versions: dict[str, str], query: dict[str, str]) -> dict:
    """Apply current assessment, search and sort in SQL before offset/limit."""
    unknown = set(query) - JOB_QUERY_FIELDS
    if unknown:
        raise ValueError(f"Unknown job filters: {', '.join(sorted(unknown))}")
    archive = query.get("job_archive") or "active"
    if archive not in {"active", "archived", "all"}:
        raise ValueError("Unknown vacancy archive filter")
    profile = query.get("profile_id") or None
    tier = query.get("tier") or None
    if tier not in {None, "A", "B", "C", "unrated"}:
        raise ValueError("Unknown vacancy tier")
    sort = query.get("sort") or "tier-asc"
    if sort not in {"tier-asc", "tier-desc", "score-asc", "score-desc"}:
        raise ValueError("Unknown vacancy sort")
    offset = _integer(query, "offset", 0)
    limit = _integer(query, "limit", 100, 500)
    minimum, maximum = _score(query, "score_min"), _score(query, "score_max")
    if minimum is not None and maximum is not None and minimum > maximum:
        raise ValueError("Minimum score must not exceed maximum score")
    ranked = ranked_profile_evaluations(versions, profile_id=profile)
    join = and_(ranked.c.job_uid == JobORM.job_uid, ranked.c.evaluation_rank == 1)
    statement = select(JobORM).outerjoin(ranked, join)
    if archive != "all":
        statement = statement.where(JobORM.archived_at.is_not(None) if archive == "archived"
                                    else JobORM.archived_at.is_(None))
    if profile:
        statement = statement.where(JobORM.role_label == profile)
    if tier:
        statement = statement.where(
            or_(ranked.c.id.is_(None), JobORM.role_label == "other") if tier == "unrated"
            else and_(JobORM.role_label != "other", ranked.c.tier == tier)
        )
    if minimum is not None:
        statement = statement.where(JobORM.role_label != "other", ranked.c.fit_score >= minimum)
    if maximum is not None:
        statement = statement.where(JobORM.role_label != "other", ranked.c.fit_score <= maximum)
    search = query.get("query", "").strip()
    if len(search) > 500 or "\x00" in search:
        raise ValueError("Search text is too long or contains a null character")
    if search:
        lower = func.hr_unicode_lower if session.get_bind().dialect.name == "sqlite" else func.lower
        columns = (
            JobORM.company, JobORM.title, JobORM.location_text,
            func.coalesce(func.nullif(JobORM.description_clean, ""), JobORM.description_raw),
        )
        statement = statement.where(or_(*(
            lower(column).contains(search.lower(), autoescape=True) for column in columns
        )))
    total = session.scalar(select(func.count()).select_from(statement.subquery())) or 0
    unscored = or_(ranked.c.id.is_(None), JobORM.role_label == "other")
    unrated = case((unscored, 1), else_=0)
    tier_rank = case((ranked.c.tier == "A", 0), (ranked.c.tier == "B", 1), else_=2)
    tier_order = tier_rank.desc() if sort == "tier-desc" else tier_rank.asc()
    score = case((unscored, None), else_=ranked.c.fit_score)
    score_order = score.asc() if sort == "score-asc" else score.desc()
    order = (score_order, tier_order) if sort.startswith("score-") else (tier_order, score_order)
    statement = statement.order_by(unrated, *order, JobORM.job_uid).offset(offset).limit(limit)
    statement = statement.options(
        load_only(*LIST_COLUMNS, raiseload=True),
        selectinload(JobORM.profile_evaluations).load_only(*EVALUATION_COLUMNS, raiseload=True),
    )
    jobs = [serialize_job(job, versions, profile=profile) for job in session.scalars(statement)]
    return {"jobs": jobs, "total": total, "offset": offset, "limit": limit,
            "has_more": offset + len(jobs) < total}


def job_detail(session: Session, versions: dict[str, str], uid: str) -> dict | None:
    if not isinstance(uid, str) or not uid or len(uid) > 100:
        raise ValueError("Invalid vacancy ID")
    job = session.scalar(select(JobORM).where(JobORM.job_uid == uid)
                         .options(selectinload(JobORM.profile_evaluations)))
    return serialize_job(job, versions, detailed=True) if job else None
