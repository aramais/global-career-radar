"""Bounded CRM vacancy queries; full descriptions are loaded only for a detail view."""

from __future__ import annotations

import math
from datetime import UTC

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session, load_only, selectinload

from job_intake.annotation.text import source_hash
from job_intake.profiles import stale_version_reasons
from job_intake.purpose import PURPOSE_RUBRIC_VERSION, serialize_purpose
from job_intake.storage.models import JobORM, JobProfileEvaluationORM, JobPurposeAssessmentORM
from job_intake.storage.queries import classification_is_current, ranked_profile_evaluations
from job_intake.storage.source_state import record_from_saved

JOB_QUERY_FIELDS = {"profile_id", "job_archive", "query", "tier", "score_min", "score_max",
                    "sort", "offset", "limit", "purpose"}
PUBLIC_ANNOTATION_FIELDS = {
    "version", "status", "method", "summary", "claims", "issues", "extract_model", "review_model",
    "extract_provider", "review_provider", "coverage", "description_complete",
}
LIST_COLUMNS = (
    JobORM.id, JobORM.job_uid, JobORM.company, JobORM.title, JobORM.content_hash,
    JobORM.archived_at, JobORM.archive_version, JobORM.apply_url, JobORM.original_url,
    JobORM.location_text, JobORM.remote_text,
    JobORM.role_label, JobORM.role_classification,
    JobORM.version_components,
    JobORM.relevance, JobORM.labels_status, JobORM.labels_version,
)
EVALUATION_COLUMNS = (
    JobProfileEvaluationORM.job_uid, JobProfileEvaluationORM.profile_id,
    JobProfileEvaluationORM.profile_name, JobProfileEvaluationORM.profile_version,
    JobProfileEvaluationORM.content_hash, JobProfileEvaluationORM.tier,
    JobProfileEvaluationORM.decision, JobProfileEvaluationORM.fit_score,
    JobProfileEvaluationORM.classification_version,
    JobProfileEvaluationORM.classification_source_hash,
    JobProfileEvaluationORM.raw_fit_score, JobProfileEvaluationORM.score_cap_reasons,
    JobProfileEvaluationORM.eligibility_status, JobProfileEvaluationORM.eligibility_evidence_status,
    JobProfileEvaluationORM.version_components,
    JobProfileEvaluationORM.relevance, JobProfileEvaluationORM.labels_status,
    JobProfileEvaluationORM.labels_version,
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


def current_evaluations(job: JobORM, versions: dict[str, str],
                        components: dict[str, dict] | None = None) -> list[JobProfileEvaluationORM]:
    return sorted(
        (p for p in job.profile_evaluations
         if classification_is_current(job, p)
         and versions.get(p.profile_id) == p.profile_version
         and p.content_hash == job.content_hash
         and not (components and p.version_components and stale_version_reasons(
             p.version_components, components.get(p.profile_id, {}),
         ))),
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


def _public_labels(value: object) -> dict | None:
    """Public fact fields only; never publish model errors, prompts or cache internals."""
    if not isinstance(value, dict):
        return None
    ordinal = value.get("relevance")
    status, outcome = value.get("status"), value.get("gate_outcome")
    result = {
        "relevance": ordinal if type(ordinal) is int and ordinal in range(4) else None,
        "status": status if isinstance(status, str) and status in {
            "labeled", "rules_only", "gated", "error"} else "error",
        "gate_outcome": outcome if isinstance(outcome, str) and outcome in {
            "eligible", "ineligible", "unknown"} else "unknown",
        "escalated": value.get("escalated") is True,
    }
    reasons = {"hiring_region_restricted", "work_authorization_required", "office_outside_sp",
               "other_language_required", "vacancy_closed"}
    result["gate_reasons"] = [item for item in value.get("gate_reasons", [])
                              if isinstance(item, str) and item in reasons] if isinstance(
                                  value.get("gate_reasons"), list) else []
    scalar_fields = {"value", "industry", "requirement", "required_domain",
                     "domain", "office_location", "basis"}
    tags = value.get("tags")
    result["tags"] = {
        name: {key: item if key in scalar_fields else [v for v in item if isinstance(v, str)]
               for key, item in tag.items()
               if (key in scalar_fields and isinstance(item, str))
               or (key in {"values", "evidence"} and isinstance(item, list))}
        for name, tag in tags.items()
        if name in {"hiring_eligibility", "work_mode", "working_language", "function_core",
                    "domain_fit", "preferred_focus"} and isinstance(tag, dict)
    } if isinstance(tags, dict) else {}
    return result


def serialize_job(job: JobORM, versions: dict[str, str], *, profile: str | None = None,
                  detailed: bool = False, components: dict[str, dict] | None = None) -> dict:
    evaluations = current_evaluations(job, versions, components)
    classified = classification_is_current(job, next(iter(job.profile_evaluations), None))
    best = next(iter(evaluations), None)
    previous = next(iter(job.profile_evaluations), None)
    old_components = previous.version_components if previous is not None else job.version_components
    stale_reasons = stale_version_reasons(
        old_components, (components or {}).get(job.role_label, {}),
    )
    if previous is not None and previous.content_hash != job.content_hash:
        stale_reasons.append("source:content_changed")
    if previous is not None and previous.profile_version != versions.get(previous.profile_id):
        stale_reasons.append("profile_version:changed")
    if not classified:
        stale_reasons.append("classification:source_or_role_changed")
    if old_components and components and stale_version_reasons(
        old_components, components.get(job.role_label, {}), stage="classification",
    ):
        classified = False
    if profile and job.role_label != profile:
        best = None
        evaluations = []
    scored = best is not None and job.role_label != "other"
    archived_at = job.archived_at
    if archived_at is not None and archived_at.tzinfo is None:
        archived_at = archived_at.replace(tzinfo=UTC)
    purpose = serialize_purpose(job.purpose_assessment)
    if not detailed:
        purpose = {key: value for key, value in purpose.items() if key in {
            "human_p1", "human_p2", "predicted_p1", "predicted_p2", "version",
            "human_stale", "human_p1_stale", "human_p2_stale",
        }}
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
        "stale_reasons": sorted(set(stale_reasons)),
        "version_components": old_components or {},
        "purpose": purpose,
        "evaluation_state": ("unscored" if classified and job.role_label == "other"
                             else "current" if best else "needs_reevaluation"),
        "eligibility": ({"decision": best.decision,
                         "status": best.eligibility_status,
                         "evidence_status": best.eligibility_evidence_status,
                         **({"blocker_signals": best.blocker_signals, "risks": best.risks}
                            if detailed else {})} if best else None),
        "relevance": ({"id": best.profile_id, "name": best.profile_name,
                       "score": best.fit_score, "tier": best.tier, "decision": best.decision,
                       **({"fit_reason": best.fit_reason, "risks": best.risks,
                           "blocker_signals": best.blocker_signals} if detailed else {})}
                      if scored else None),
        "fit_score": best.fit_score if scored else None, "tier": best.tier if scored else None,
        "raw_fit_score": best.raw_fit_score if scored else None,
        "score_cap_reasons": best.score_cap_reasons if scored else [],
        "eligibility_status": best.eligibility_status if best else "unknown",
        "eligibility_evidence_status": best.eligibility_evidence_status if best else "unconfirmed",
        "decision": best.decision if best else None,
        "profiles": [{"id": p.profile_id, "name": p.profile_name, "score": p.fit_score,
                      "tier": p.tier, "decision": p.decision,
                      **({"fit_reason": p.fit_reason, "risks": p.risks,
                          "blocker_signals": p.blocker_signals} if detailed else {})}
                     for p in evaluations if job.role_label != "other"],
    }
    # Preserve compact legacy list payloads when no labels have been produced.
    # `relevance` above remains the legacy profile-assessment object.
    if job.labels_status is not None or job.relevance is not None:
        result.update(labels_relevance=job.relevance, labels_status=job.labels_status,
                      labels_version=job.labels_version)
    if detailed:
        raw_labels = (job.annotation or {}).get("labels_v3")
        labels_current = (isinstance(raw_labels, dict)
                          and raw_labels.get("source_hash") == source_hash(record_from_saved(job)))
        result["labels_v3"] = _public_labels(raw_labels) if labels_current else None
        if isinstance(raw_labels, dict) and not labels_current:
            result.update(labels_relevance=None, labels_status="stale", labels_stale=True)
        result["description"] = job.description_clean or job.description_raw
        result["annotation"] = {key: value for key, value in (job.annotation or {}).items()
                                if key in PUBLIC_ANNOTATION_FIELDS}
        prefilter = _public_prefilter((job.annotation or {}).get("prefilter"))
        if prefilter is not None:
            result["annotation"]["prefilter"] = prefilter
    return result


def list_job_page(session: Session, versions: dict[str, str], query: dict[str, str],
                  components: dict[str, dict] | None = None) -> dict:
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
    if sort not in {"tier-asc", "tier-desc", "score-asc", "score-desc", "review-evidence",
                    "relevance-asc", "relevance-desc"}:
        raise ValueError("Unknown vacancy sort")
    purpose = query.get("purpose") or "all"
    if purpose not in {"all", "p1", "p2", "review"}:
        raise ValueError("Unknown vacancy purpose")
    offset = _integer(query, "offset", 0)
    limit = _integer(query, "limit", 100, 500)
    minimum, maximum = _score(query, "score_min"), _score(query, "score_max")
    if minimum is not None and maximum is not None and minimum > maximum:
        raise ValueError("Minimum score must not exceed maximum score")
    ranked = ranked_profile_evaluations(versions, profile_id=profile)
    if components:
        ranked = select(ranked).where(or_(
            ranked.c.version_components == {},
            *[and_(ranked.c.profile_id == label, *[
                ranked.c.version_components[key].as_string() == value
                for key, value in expected.items() if key != "query"
            ]) for label, expected in components.items()],
        )).subquery("current_component_evaluations")
    join = and_(ranked.c.job_uid == JobORM.job_uid, ranked.c.evaluation_rank == 1)
    statement = select(JobORM).outerjoin(ranked, join)
    if purpose != "all":
        row = JobPurposeAssessmentORM
        statement = statement.outerjoin(row, row.job_uid == JobORM.job_uid)
        stale_axes = {}
        for axis in ("p1", "p2"):
            human = getattr(row, "human_" + axis)
            pin = func.coalesce(
                func.nullif(getattr(row, "human_" + axis + "_source_hash"), ""),
                row.human_source_hash, "",
            )
            rubric = func.coalesce(
                func.nullif(getattr(row, "human_" + axis + "_rubric_version"), ""),
                row.human_rubric_version, "",
            )
            stale_axes[axis] = and_(human != "unknown", or_(
                pin != row.prediction_source_hash,
                rubric != func.coalesce(func.nullif(row.rubric_version, ""),
                                        PURPOSE_RUBRIC_VERSION),
            ))
        if purpose == "review":
            statement = statement.where(or_(
                row.job_uid.is_(None), stale_axes["p1"], stale_axes["p2"],
                row.human_p1 == "unknown", row.human_p2 == "unknown",
            ))
        else:
            human = row.human_p1 if purpose == "p1" else row.human_p2
            predicted = row.predicted_p1 if purpose == "p1" else row.predicted_p2
            human_current = ~stale_axes[purpose]
            statement = statement.where(or_(
                and_(human_current, human == "suitable"),
                and_(or_(~human_current, human == "unknown"), predicted == "suitable"),
            ))
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
    if sort == "review-evidence":
        order = (case((ranked.c.decision == "review", 0), else_=1), case(
            (ranked.c.eligibility_evidence_status == "confirmed", 0),
            (ranked.c.eligibility_evidence_status == "unconfirmed", 1), else_=2,
        ), score_order, tier_order)
    if sort.startswith("relevance-"):
        relevance_order = (JobORM.relevance.asc() if sort == "relevance-asc"
                           else JobORM.relevance.desc())
        ordering = (case((JobORM.relevance.is_(None), 1), else_=0), relevance_order, *order)
    else:
        ordering = (unrated, *order)
    statement = statement.order_by(*ordering, JobORM.job_uid).offset(offset).limit(limit)
    statement = statement.options(
        load_only(*LIST_COLUMNS, raiseload=True),
        selectinload(JobORM.profile_evaluations).load_only(*EVALUATION_COLUMNS, raiseload=True),
        selectinload(JobORM.purpose_assessment),
    )
    jobs = [serialize_job(job, versions, profile=profile, components=components)
            for job in session.scalars(statement)]
    return {"jobs": jobs, "total": total, "offset": offset, "limit": limit,
            "has_more": offset + len(jobs) < total}


def job_detail(session: Session, versions: dict[str, str], uid: str,
               components: dict[str, dict] | None = None) -> dict | None:
    if not isinstance(uid, str) or not uid or len(uid) > 100:
        raise ValueError("Invalid vacancy ID")
    job = session.scalar(select(JobORM).where(JobORM.job_uid == uid)
                         .options(selectinload(JobORM.profile_evaluations),
                                  selectinload(JobORM.purpose_assessment)))
    return serialize_job(job, versions, detailed=True, components=components) if job else None
