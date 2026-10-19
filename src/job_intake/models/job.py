from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class FilterDecision(StrEnum):
    PASS = "pass"
    REJECT = "reject"
    REVIEW = "review"


class JobTier(StrEnum):
    A = "A"
    B = "B"
    C = "C"


class JobStatus(StrEnum):
    OPEN = "open"
    CLOSED = "closed"
    UNKNOWN = "unknown"


@dataclass(slots=True)
class JobRecord:
    source: str
    company: str
    title: str
    original_url: str
    apply_url: str | None = None
    source_job_id: str | None = None
    posted_at: datetime | None = None
    location_text: str | None = None
    remote_text: str | None = None
    employment_type: str | None = None
    salary_text: str | None = None
    timezone_text: str | None = None
    description_raw: str = ""
    description_clean: str = ""
    status: JobStatus = JobStatus.OPEN
    source_metadata: dict[str, Any] = field(default_factory=dict)
    annotation: dict[str, Any] = field(default_factory=dict)
    # Derived text units are local to this record, never persisted or hashed as source.
    # The parser validates its immutable input key before reusing this cache.
    _text_units_cache: tuple[Any, ...] | None = field(
        default=None, init=False, repr=False, compare=False,
    )
    # Optimistic source revision captured before any classification/model work.
    # These are transport guards, never source facts or public snapshot fields.
    _source_reconciled: bool = field(default=False, init=False, repr=False, compare=False)
    _expected_source_revision: int | None = field(
        default=None, init=False, repr=False, compare=False,
    )
    _canonical_job_uid: str | None = field(default=None, init=False, repr=False, compare=False)


@dataclass(slots=True)
class JobEvaluation:
    decision: FilterDecision
    matched_signals: list[str] = field(default_factory=list)
    blocker_signals: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    deterministic_score: float = 0.0
    semantic_score: float | None = None
    fit_score: float = 0.0
    fit_reason: str = ""
    bridge_role: bool = False
    bucket: str = "Bucket C"
    tier: JobTier = JobTier.C
    risks: list[str] = field(default_factory=list)
    audit_log: list[str] = field(default_factory=list)
    # Relevance policy is independent of the separately persisted hiring decision.
    fit_policy: str = "legacy"
    fit_max_tier: JobTier | None = None
    # Unknown for legacy rows: a capped score cannot reconstruct the original rank.
    raw_fit_score: float | None = None
    score_cap_reasons: list[str] = field(default_factory=list)
    # Hiring evidence is independent of occupation, role fit and overall routing.
    eligibility_status: str = "unknown"
    eligibility_evidence_status: str = "unconfirmed"


@dataclass(slots=True)
class EvaluatedJob:
    record: JobRecord
    evaluation: JobEvaluation
    profile_id: str = "default"
    profile_name: str = ""
    profile_version: str = ""
    profile_context: str = ""
    # Occupation classification is distinct from source facts and relevance.
    role_classification: dict[str, Any] = field(default_factory=dict)
    version_components: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class JobAlert:
    job_uid: str
    channel: str
    tier: JobTier
    message: str
    sent_at: datetime
