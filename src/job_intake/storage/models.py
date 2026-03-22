from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


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
    fit_reason: Mapped[str] = mapped_column(Text, default="")
    tier: Mapped[str] = mapped_column(String(4), default="C", index=True)
    bucket: Mapped[str] = mapped_column(String(32), default="Bucket C")
    risks: Mapped[list[str]] = mapped_column(JSON, default=list)
    audit_log: Mapped[list[str]] = mapped_column(JSON, default=list)
    bridge_role: Mapped[bool] = mapped_column(default=False)
    last_alerted_tier: Mapped[str | None] = mapped_column(String(4), nullable=True)
    source_metadata: Mapped[dict] = mapped_column(JSON, default=dict)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    events: Mapped[list["JobEventORM"]] = relationship(back_populates="job", cascade="all, delete")
    feedback: Mapped[list["FeedbackORM"]] = relationship(
        back_populates="job", cascade="all, delete"
    )


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
