"""Independent personal application CRM tables sharing the intake database."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import JSON, CheckConstraint, Date, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.orm import relationship as orm_relationship

from job_intake.storage.models import Base, utcnow


class ApplicationORM(Base):
    __tablename__ = "crm_applications"
    __table_args__ = (
        CheckConstraint("channel IN ('unknown','cold','referral','recruiter')"),
        CheckConstraint(
            "stage IN ('saved','contacted','applied','hr_screen','team_interview',"
            "'assessment','offer','rejected','withdrawn','archived')"
        ),
        CheckConstraint("referral_state IN ('none','to_find','requested','referred')"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Retention of an intake record never removes personal application history.
    job_uid: Mapped[str | None] = mapped_column(
        ForeignKey("jobs.job_uid", ondelete="SET NULL"), nullable=True, unique=True
    )
    company: Mapped[str] = mapped_column(String(255), index=True)
    title: Mapped[str] = mapped_column(String(500))
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    canonical_key: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    profile_id: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    channel: Mapped[str] = mapped_column(String(32), default="unknown", index=True)
    stage: Mapped[str] = mapped_column(String(32), default="saved", index=True)
    referral_state: Mapped[str] = mapped_column(String(32), default="none")
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_action: Mapped[str] = mapped_column(Text, default="")
    next_action_due: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    cv_version: Mapped[str | None] = mapped_column(String(255), nullable=True)
    original_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_key: Mapped[str | None] = mapped_column(String(500), unique=True, nullable=True)
    source_metadata: Mapped[dict] = mapped_column(JSON, default=dict)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    __mapper_args__ = {"version_id_col": version}
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    history: Mapped[list[StageEventORM]] = orm_relationship(
        back_populates="application", cascade="all, delete-orphan", order_by="StageEventORM.id"
    )
    contact_links: Mapped[list[ApplicationContactORM]] = orm_relationship(
        back_populates="application", cascade="all, delete-orphan"
    )


class StageEventORM(Base):
    __tablename__ = "crm_stage_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("crm_applications.id", ondelete="CASCADE"), index=True
    )
    from_stage: Mapped[str | None] = mapped_column(String(32), nullable=True)
    to_stage: Mapped[str] = mapped_column(String(32), index=True)
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    note: Mapped[str] = mapped_column(Text, default="")
    application: Mapped[ApplicationORM] = orm_relationship(back_populates="history")


class ContactORM(Base):
    __tablename__ = "crm_contacts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255))
    company: Mapped[str] = mapped_column(String(255), default="", index=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    linkedin_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    identity_key: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    source_key: Mapped[str | None] = mapped_column(String(500), unique=True, nullable=True)
    source_metadata: Mapped[dict] = mapped_column(JSON, default=dict)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    __mapper_args__ = {"version_id_col": version}
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    application_links: Mapped[list[ApplicationContactORM]] = orm_relationship(
        back_populates="contact"
    )


class ApplicationContactORM(Base):
    __tablename__ = "crm_application_contacts"
    __table_args__ = (
        CheckConstraint("relationship IN ('referral','recruiter','hiring_manager','other')"),
    )

    application_id: Mapped[int] = mapped_column(
        ForeignKey("crm_applications.id", ondelete="CASCADE"), primary_key=True
    )
    contact_id: Mapped[int] = mapped_column(
        ForeignKey("crm_contacts.id", ondelete="CASCADE"), primary_key=True
    )
    relationship: Mapped[str] = mapped_column(String(32), default="other", primary_key=True)
    application: Mapped[ApplicationORM] = orm_relationship(back_populates="contact_links")
    contact: Mapped[ContactORM] = orm_relationship(back_populates="application_links")


class CompanyORM(Base):
    __tablename__ = "crm_companies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255))
    normalized_name: Mapped[str] = mapped_column(String(255), unique=True)
    url: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    source_key: Mapped[str | None] = mapped_column(String(500), nullable=True)
    source_metadata: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
