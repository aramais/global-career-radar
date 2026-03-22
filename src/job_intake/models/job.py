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


@dataclass(slots=True)
class EvaluatedJob:
    record: JobRecord
    evaluation: JobEvaluation


@dataclass(slots=True)
class JobAlert:
    job_uid: str
    channel: str
    tier: JobTier
    message: str
    sent_at: datetime
