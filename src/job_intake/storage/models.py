from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class JobORM(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_uid: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(100), index=True)
    source_job_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    company: Mapped[str] = mapped_column(String(255), index=True)
    title: Mapped[str] = mapped_column(String(255), index=True)
    original_url: Mapped[str] = mapped_column(Text)
    apply_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    location_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    remote_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    employment_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    salary_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    timezone_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    description_raw: Mapped[str] = mapped_column(Text)
    description_clean: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), index=True)
    detected_blockers: Mapped[list[str]] = mapped_column(JSON, default=list)
    matched_signals: Mapped[list[str]] = mapped_column(JSON, default=list)
    filter_decision: Mapped[str] = mapped_column(String(32), index=True)
    fit_score: Mapped[float] = mapped_column(Float, default=0.0)
    semantic_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    fit_reason: Mapped[str] = mapped_column(Text, default="")
    tier: Mapped[str] = mapped_column(String(4), default="C", index=True)
    bucket: Mapped[str] = mapped_column(String(32), default="Bucket C")
    risks: Mapped[list[str]] = mapped_column(JSON, default=list)
    audit_log: Mapped[list[str]] = mapped_column(JSON, default=list)
    bridge_role: Mapped[bool] = mapped_column(default=False)
    last_alerted_tier: Mapped[str | None] = mapped_column(String(4), nullable=True)
    last_alerted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source_metadata: Mapped[dict] = mapped_column(JSON, default=dict)
    annotation: Mapped[dict] = mapped_column(JSON, default=dict)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archive_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    best_profile_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    best_profile_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    alert_pending: Mapped[bool] = mapped_column(default=False)
    profile_evaluations: Mapped[list[JobProfileEvaluationORM]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )
    alert_outbox: Mapped[list[AlertOutboxORM]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )

    events: Mapped[list[JobEventORM]] = relationship(back_populates="job", cascade="all, delete")
    feedback: Mapped[list[FeedbackORM]] = relationship(back_populates="job", cascade="all, delete")


class JobProfileEvaluationORM(Base):
    __tablename__ = "job_profile_evaluations"
    __table_args__ = (UniqueConstraint("job_uid", "profile_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_uid: Mapped[str] = mapped_column(ForeignKey("jobs.job_uid"), index=True)
    profile_id: Mapped[str] = mapped_column(String(100), index=True)
    profile_name: Mapped[str] = mapped_column(String(255))
    profile_version: Mapped[str] = mapped_column(String(64))
    content_hash: Mapped[str] = mapped_column(String(64))
    llm_cache_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    decision: Mapped[str] = mapped_column(String(32))
    deterministic_score: Mapped[float] = mapped_column(Float)
    semantic_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    fit_score: Mapped[float] = mapped_column(Float)
    tier: Mapped[str] = mapped_column(String(4))
    bucket: Mapped[str] = mapped_column(String(32))
    matched_signals: Mapped[list[str]] = mapped_column(JSON, default=list)
    blocker_signals: Mapped[list[str]] = mapped_column(JSON, default=list)
    reasons: Mapped[list[str]] = mapped_column(JSON, default=list)
    fit_reason: Mapped[str] = mapped_column(Text)
    bridge_role: Mapped[bool] = mapped_column(default=False)
    risks: Mapped[list[str]] = mapped_column(JSON, default=list)
    audit_log: Mapped[list[str]] = mapped_column(JSON, default=list)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    job: Mapped[JobORM] = relationship(back_populates="profile_evaluations")


class AlertOutboxORM(Base):
    __tablename__ = "alert_outbox"
    __table_args__ = (UniqueConstraint("job_uid", "channel"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_uid: Mapped[str] = mapped_column(ForeignKey("jobs.job_uid"), index=True)
    channel: Mapped[str] = mapped_column(String(32), default="telegram")
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    message: Mapped[str] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    job: Mapped[JobORM] = relationship(back_populates="alert_outbox")


class JobEventORM(Base):
    __tablename__ = "job_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_uid: Mapped[str] = mapped_column(ForeignKey("jobs.job_uid"), index=True)
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    job: Mapped[JobORM] = relationship(back_populates="events")


class FeedbackORM(Base):
    __tablename__ = "job_feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_uid: Mapped[str] = mapped_column(ForeignKey("jobs.job_uid"), index=True)
    label: Mapped[str] = mapped_column(String(64), index=True)
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    job: Mapped[JobORM] = relationship(back_populates="feedback")
