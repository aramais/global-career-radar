"""Transactional CRM operations. Callers own commits and rollbacks."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import UTC, date, datetime, time
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from job_intake.crm.models import (
    ApplicationContactORM,
    ApplicationORM,
    CompanyORM,
    ContactORM,
    StageEventORM,
)
from job_intake.crm.schemas import (
    CHANNELS,
    CONTACT_RELATIONSHIPS,
    REFERRAL_STATES,
    STAGES,
    TERMINAL_STAGES,
    CRMConflictError,
    CRMValidationError,
    canonical_url_key,
    date_value,
    datetime_value,
    email_value,
    enum_value,
    safe_url,
    text_value,
)
from job_intake.storage.models import JobORM, utcnow


def _iso(value: datetime | date | None) -> str | None:
    if isinstance(value, datetime) and value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat() if value is not None else None


def _metadata(value: object) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise CRMValidationError("source_metadata must be an object")
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (ValueError, TypeError) as exc:
        raise CRMValidationError("source_metadata must contain JSON values") from exc
    if len(encoded) > 100000:
        raise CRMValidationError("source_metadata is too large")
    return json.loads(encoded)


def _optional_text(value: object, field: str, maximum: int = 500) -> str | None:
    return None if value is None else text_value(value, field, maximum=maximum) or None


def _check_version(item: ApplicationORM | ContactORM, expected_version: int | None) -> None:
    if expected_version is not None:
        if isinstance(expected_version, bool) or not isinstance(expected_version, int):
            raise CRMValidationError("expected_version must be an integer")
        if item.version != expected_version:
            raise CRMConflictError("Record changed. Reload before saving your changes.")


def serialize_contact(contact: ContactORM) -> dict[str, Any]:
    return {
        "id": contact.id,
        "name": contact.name,
        "company": contact.company,
        "email": contact.email,
        "linkedin_url": contact.linkedin_url,
        "notes": contact.notes,
        "version": contact.version,
        "created_at": _iso(contact.created_at),
        "updated_at": _iso(contact.updated_at),
        "source_key": contact.source_key,
        "source_metadata": contact.source_metadata,
    }


def serialize_company(company: CompanyORM) -> dict[str, Any]:
    return {
        "id": company.id,
        "name": company.name,
        "url": company.url,
        "notes": company.notes,
        "source_key": company.source_key,
        "source_metadata": company.source_metadata,
        "created_at": _iso(company.created_at),
        "updated_at": _iso(company.updated_at),
    }


def serialize_application(application: ApplicationORM) -> dict[str, Any]:
    return {
        "id": application.id,
        "job_uid": application.job_uid,
        "company": application.company,
        "title": application.title,
        "url": application.url,
        "profile_id": application.profile_id,
        "channel": application.channel,
        "stage": application.stage,
        "referral_state": application.referral_state,
        "applied_at": _iso(application.applied_at),
        "next_action": application.next_action,
        "next_action_due": _iso(application.next_action_due),
        "notes": application.notes,
        "cv_version": application.cv_version,
        "original_status": application.original_status,
        "source_key": application.source_key,
        "source_metadata": application.source_metadata,
        "version": application.version,
        "created_at": _iso(application.created_at),
        "updated_at": _iso(application.updated_at),
        "history": [
            {
                "id": event.id,
                "from_stage": event.from_stage,
                "stage": event.to_stage,
                "occurred_at": _iso(event.occurred_at),
                "recorded_at": _iso(event.recorded_at),
                "note": event.note,
            }
            for event in application.history
        ],
        "contacts": [
            {**serialize_contact(link.contact), "relationship": link.relationship}
            for link in application.contact_links
        ],
    }


class CRMRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def _flush(self) -> None:
        try:
            self.session.flush()
        except StaleDataError as exc:
            raise CRMConflictError("Record changed. Reload before saving your changes.") from exc

    def get_application(self, application_id: int) -> ApplicationORM | None:
        return self.session.get(ApplicationORM, application_id)

    def _application(self, application_id: int) -> ApplicationORM:
        item = self.get_application(application_id)
        if item is None:
            raise CRMValidationError("Application not found")
        return item

    def find_application_by_source_key(self, source_key: str) -> ApplicationORM | None:
        return self.session.scalar(
            select(ApplicationORM).where(ApplicationORM.source_key == source_key)
        )

    def create_application(
        self,
        *,
        company: str,
        title: str,
        url: str | None = None,
        job_uid: str | None = None,
        profile_id: str | None = None,
        channel: str = "unknown",
        stage: str = "saved",
        referral_state: str = "none",
        applied_at: object = None,
        next_action: str = "",
        next_action_due: object = None,
        notes: str = "",
        cv_version: str | None = None,
        original_status: str | None = None,
        source_key: str | None = None,
        source_metadata: dict | None = None,
        stage_occurred_at: object = None,
    ) -> ApplicationORM:
        values = self._application_values(
            {
                "company": company,
                "title": title,
                "url": url,
                "job_uid": job_uid,
                "profile_id": profile_id,
                "channel": channel,
                "stage": stage,
                "referral_state": referral_state,
                "applied_at": applied_at,
                "next_action": next_action,
                "next_action_due": next_action_due,
                "notes": notes,
                "cv_version": cv_version,
                "original_status": original_status,
                "source_key": source_key,
                "source_metadata": source_metadata,
            },
            creating=True,
        )
        source_key = values["source_key"]
        job_uid = values["job_uid"]
        event_date = datetime_value(stage_occurred_at, "stage_occurred_at")
        if source_key:
            existing = self.find_application_by_source_key(source_key)
            if existing is not None:
                return existing
        if job_uid:
            existing = self.session.scalar(
                select(ApplicationORM).where(ApplicationORM.job_uid == job_uid)
            )
            if existing is not None:
                return existing
        item = ApplicationORM(**values)
        # Import rows preserve distinct source rows, including explicitly labelled duplicates.
        if not source_key:
            item.canonical_key = canonical_url_key(values["url"])
            if item.canonical_key:
                existing = self.session.scalar(
                    select(ApplicationORM).where(ApplicationORM.canonical_key == item.canonical_key)
                )
                if existing is not None:
                    return existing
        self.session.add(item)
        if item.applied_at is not None and stage != "applied":
            item.history.append(
                StageEventORM(
                    from_stage=None,
                    to_stage="applied",
                    occurred_at=item.applied_at,
                    note="Application date explicitly recorded",
                )
            )
        if stage == "applied" and event_date is None:
            event_date = item.applied_at
        if stage == "applied" and item.applied_at is None and event_date is not None:
            item.applied_at = event_date
        item.history.append(
            StageEventORM(
                from_stage=None,
                to_stage=stage,
                occurred_at=event_date,
                note="Created",
            )
        )
        self._flush()
        return item

    def _application_values(self, fields: dict, *, creating: bool = False) -> dict:
        allowed = {
            "company",
            "title",
            "url",
            "job_uid",
            "profile_id",
            "channel",
            "stage",
            "referral_state",
            "applied_at",
            "next_action",
            "next_action_due",
            "notes",
            "cv_version",
            "original_status",
            "source_key",
            "source_metadata",
        }
        if set(fields) - allowed:
            raise CRMValidationError(f"Unknown application fields: {sorted(set(fields) - allowed)}")
        if not creating and {
            "stage",
            "job_uid",
            "source_key",
            "source_metadata",
            "original_status",
        } & set(fields):
            raise CRMValidationError("Use mark_stage for stages; source provenance is immutable")
        values = {}
        for name, value in fields.items():
            if name in {"company", "title"}:
                values[name] = text_value(
                    value, name, required=True, maximum=500 if name == "title" else 255
                )
            elif name == "url":
                values[name] = safe_url(value)
            elif name == "channel":
                values[name] = enum_value(value, name, CHANNELS)
            elif name == "stage":
                values[name] = enum_value(value, name, STAGES)
            elif name == "referral_state":
                values[name] = enum_value(value, name, REFERRAL_STATES)
            elif name == "applied_at":
                values[name] = datetime_value(value, name)
            elif name == "next_action_due":
                values[name] = date_value(value, name)
            elif name in {"next_action", "notes"}:
                values[name] = text_value(value, name)
            elif name == "source_metadata":
                values[name] = _metadata(value)
            else:
                maximum = {"job_uid": 64, "profile_id": 100, "cv_version": 255}.get(name, 500)
                values[name] = _optional_text(value, name, maximum)
        if (
            creating
            and values.get("job_uid")
            and self.session.scalar(
                select(JobORM.job_uid).where(JobORM.job_uid == values["job_uid"])
            )
            is None
        ):
            raise CRMValidationError("Saved job not found")
        return values

    def update_application(
        self,
        application_id: int,
        *,
        expected_version: int | None = None,
        **fields: Any,
    ) -> ApplicationORM:
        item = self._application(application_id)
        _check_version(item, expected_version)
        values = self._application_values(fields)
        if "url" in values:
            key = canonical_url_key(values["url"])
            if key:
                duplicate = self.session.scalar(
                    select(ApplicationORM.id).where(
                        ApplicationORM.canonical_key == key,
                        ApplicationORM.id != application_id,
                    )
                )
                if duplicate is not None:
                    raise CRMValidationError("This vacancy URL already has an application")
            item.canonical_key = key
        for name, value in values.items():
            setattr(item, name, value)
        self._flush()
        return item

    def mark_stage(
        self,
        application_id: int,
        stage: str,
        *,
        occurred_at: object = None,
        note: str = "",
        expected_version: int | None = None,
    ) -> ApplicationORM:
        item = self._application(application_id)
        _check_version(item, expected_version)
        stage = enum_value(stage, "stage", STAGES)
        occurred_at = datetime_value(occurred_at, "occurred_at")
        note = text_value(note, "note")
        if item.stage == stage:
            return item
        item.history.append(
            StageEventORM(
                from_stage=item.stage,
                to_stage=stage,
                occurred_at=occurred_at,
                note=note,
            )
        )
        item.stage = stage
        if stage == "applied" and item.applied_at is None and occurred_at is not None:
            item.applied_at = occurred_at
        self._flush()
        return item

    def list_applications(
        self,
        *,
        stage: str | None = None,
        profile_id: str | None = None,
        channel: str | None = None,
        query: str | None = None,
        due_before: object = None,
    ) -> list[ApplicationORM]:
        statement = select(ApplicationORM)
        if stage:
            statement = statement.where(ApplicationORM.stage == enum_value(stage, "stage", STAGES))
        if channel:
            statement = statement.where(
                ApplicationORM.channel == enum_value(channel, "channel", CHANNELS)
            )
        if profile_id:
            statement = statement.where(ApplicationORM.profile_id == profile_id)
        if query:
            literal = text_value(query, "query", maximum=500)
            statement = statement.where(
                or_(
                    ApplicationORM.company.icontains(literal, autoescape=True),
                    ApplicationORM.title.icontains(literal, autoescape=True),
                    ApplicationORM.notes.icontains(literal, autoescape=True),
                )
            )
        if due_before is not None:
            statement = statement.where(
                ApplicationORM.next_action_due <= date_value(due_before, "due_before")
            )
        return list(
            self.session.scalars(
                statement.order_by(ApplicationORM.updated_at.desc(), ApplicationORM.id.desc())
            )
        )

    def create_from_job(self, job_uid: str, profile_id: str | None = None) -> ApplicationORM:
        job = self.session.scalar(select(JobORM).where(JobORM.job_uid == job_uid))
        if job is None:
            raise CRMValidationError("Saved job not found")
        existing = self.session.scalar(
            select(ApplicationORM).where(ApplicationORM.job_uid == job_uid)
        )
        if existing is not None:
            return existing
        url = job.apply_url or job.original_url
        key = canonical_url_key(safe_url(url))
        if key:
            # An imported application may already refer to this vacancy.
            for item in self.session.scalars(
                select(ApplicationORM).where(ApplicationORM.url.is_not(None))
            ):
                if canonical_url_key(item.url) == key:
                    item.job_uid = job_uid
                    if item.profile_id is None and profile_id:
                        item.profile_id = profile_id
                    self._flush()
                    return item
        return self.create_application(
            company=job.company,
            title=job.title,
            url=url,
            job_uid=job_uid,
            profile_id=profile_id,
            stage_occurred_at=utcnow(),
            source_metadata={"intake_source": job.source, "source_job_id": job.source_job_id},
        )

    def get_contact(self, contact_id: int) -> ContactORM | None:
        return self.session.get(ContactORM, contact_id)

    def upsert_contact(
        self,
        name: str,
        company: str = "",
        *,
        email: str | None = None,
        linkedin_url: str | None = None,
        notes: str = "",
        source_key: str | None = None,
        source_metadata: dict | None = None,
    ) -> ContactORM:
        values = self._contact_values(
            {
                "name": name,
                "company": company,
                "email": email,
                "linkedin_url": linkedin_url,
                "notes": notes,
            }
        )
        source_key = _optional_text(source_key, "source_key")
        provenance = _metadata(source_metadata)
        identity = values["email"].casefold() if values["email"] else values["linkedin_url"]
        identity_key = hashlib.sha256(identity.encode()).hexdigest() if identity else None
        existing = None
        if source_key:
            existing = self.session.scalar(
                select(ContactORM).where(ContactORM.source_key == source_key)
            )
        if existing is None and identity_key:
            existing = self.session.scalar(
                select(ContactORM).where(ContactORM.identity_key == identity_key)
            )
        if existing is not None:
            return existing
        item = ContactORM(
            **values,
            identity_key=identity_key,
            source_key=source_key,
            source_metadata=provenance,
        )
        self.session.add(item)
        self._flush()
        return item

    @staticmethod
    def _contact_values(fields: dict) -> dict:
        if set(fields) - {"name", "company", "email", "linkedin_url", "notes"}:
            raise CRMValidationError("Unknown contact fields")
        values = {}
        for name, value in fields.items():
            if name == "email":
                values[name] = email_value(value)
            elif name == "linkedin_url":
                values[name] = safe_url(value, name)
            else:
                values[name] = text_value(
                    value,
                    name,
                    required=name == "name",
                    maximum=20000 if name == "notes" else 255,
                )
        return values

    def update_contact(
        self,
        contact_id: int,
        *,
        expected_version: int | None = None,
        **fields: Any,
    ) -> ContactORM:
        item = self.get_contact(contact_id)
        if item is None:
            raise CRMValidationError("Contact not found")
        _check_version(item, expected_version)
        values = self._contact_values(fields)
        updated_email = values.get("email", item.email)
        updated_linkedin = values.get("linkedin_url", item.linkedin_url)
        identity = updated_email.casefold() if updated_email else updated_linkedin
        identity_key = hashlib.sha256(identity.encode()).hexdigest() if identity else None
        if identity_key:
            duplicate = self.session.scalar(
                select(ContactORM.id).where(
                    ContactORM.identity_key == identity_key,
                    ContactORM.id != contact_id,
                )
            )
            if duplicate is not None:
                raise CRMValidationError("This contact identity already exists")
        for name, value in values.items():
            setattr(item, name, value)
        item.identity_key = identity_key
        self._flush()
        return item

    def list_contacts(self, *, query: str | None = None) -> list[ContactORM]:
        statement = select(ContactORM)
        if query:
            literal = text_value(query, "query", maximum=500)
            statement = statement.where(
                or_(
                    ContactORM.name.icontains(literal, autoescape=True),
                    ContactORM.company.icontains(literal, autoescape=True),
                )
            )
        return list(self.session.scalars(statement.order_by(ContactORM.name, ContactORM.id)))

    def link_contact(
        self,
        application_id: int,
        contact_id: int,
        relationship: str = "referral",
        *,
        expected_version: int | None = None,
    ) -> ApplicationContactORM:
        item = self._application(application_id)
        _check_version(item, expected_version)
        if self.get_contact(contact_id) is None:
            raise CRMValidationError("Contact not found")
        relationship = enum_value(relationship, "relationship", CONTACT_RELATIONSHIPS)
        existing = self.session.get(
            ApplicationContactORM, (application_id, contact_id, relationship)
        )
        if existing is not None:
            return existing
        link = ApplicationContactORM(contact_id=contact_id, relationship=relationship)
        item.contact_links.append(link)
        item.version += 1
        self._flush()
        return link

    def unlink_contact(
        self,
        application_id: int,
        contact_id: int,
        relationship: str,
        *,
        expected_version: int | None = None,
    ) -> ApplicationORM:
        item = self._application(application_id)
        _check_version(item, expected_version)
        relationship = enum_value(relationship, "relationship", CONTACT_RELATIONSHIPS)
        link = self.session.get(ApplicationContactORM, (application_id, contact_id, relationship))
        if link is not None:
            item.contact_links.remove(link)
            item.version += 1
            self._flush()
        return item

    def find_company(self, name: str) -> CompanyORM | None:
        normalized = " ".join(
            text_value(name, "name", required=True, maximum=255).casefold().split()
        )
        return self.session.scalar(
            select(CompanyORM).where(CompanyORM.normalized_name == normalized)
        )

    def upsert_company(
        self,
        name: str,
        *,
        url: str | None = None,
        notes: str = "",
        source_key: str | None = None,
        source_metadata: dict | None = None,
    ) -> CompanyORM:
        name = text_value(name, "name", required=True, maximum=255)
        url = safe_url(url)
        notes = text_value(notes, "notes")
        source_key = _optional_text(source_key, "source_key")
        metadata = _metadata(source_metadata)
        existing = self.find_company(name)
        if existing is not None:
            if not existing.url and url:
                existing.url = url
            if not existing.notes and notes:
                existing.notes = notes
            # Additional source evidence is retained without replacing existing manual fields.
            if source_key and source_key != existing.source_key:
                current = dict(existing.source_metadata)
                sources = list(current.get("additional_sources", []))
                if not any(entry.get("source_key") == source_key for entry in sources):
                    sources.append({"source_key": source_key, "metadata": metadata})
                    current["additional_sources"] = sources
                    existing.source_metadata = current
            self._flush()
            return existing
        item = CompanyORM(
            name=name,
            normalized_name=" ".join(name.casefold().split()),
            url=url,
            notes=notes,
            source_key=source_key,
            source_metadata=metadata,
        )
        self.session.add(item)
        self._flush()
        return item

    def update_company(self, company_id: int, **fields: Any) -> CompanyORM:
        item = self.session.get(CompanyORM, company_id)
        if item is None:
            raise CRMValidationError("Company not found")
        if set(fields) - {"name", "url", "notes"}:
            raise CRMValidationError("Unknown company fields")
        for name, value in fields.items():
            if name == "url":
                item.url = safe_url(value)
            elif name == "name":
                value = text_value(value, "name", required=True, maximum=255)
                other = self.find_company(value)
                if other is not None and other.id != company_id:
                    raise CRMValidationError("Company already exists")
                item.name = value
                item.normalized_name = " ".join(value.casefold().split())
            else:
                item.notes = text_value(value, "notes")
        self._flush()
        return item

    def list_companies(self, *, query: str | None = None) -> list[CompanyORM]:
        statement = select(CompanyORM)
        if query:
            statement = statement.where(
                CompanyORM.name.icontains(
                    text_value(query, "query", maximum=500),
                    autoescape=True,
                )
            )
        return list(self.session.scalars(statement.order_by(CompanyORM.name, CompanyORM.id)))

    def due_actions(self, as_of: object = None) -> list[ApplicationORM]:
        deadline = date.today() if as_of is None else date_value(as_of, "as_of")
        return list(
            self.session.scalars(
                select(ApplicationORM)
                .where(
                    ApplicationORM.next_action_due <= deadline,
                    ApplicationORM.next_action != "",
                    ApplicationORM.stage.not_in(TERMINAL_STAGES),
                )
                .order_by(ApplicationORM.next_action_due, ApplicationORM.id)
            )
        )

    def funnel_metrics(
        self,
        *,
        profile_id: str | None = None,
        channel: str | None = None,
        since: object = None,
        until: object = None,
    ) -> dict[str, Any]:
        """Count stage events in the period and cohort conversion by its end.

        Channel cohorts use the explicit application date, or the earliest known
        application event when the field is empty. Their outcomes include known
        team interviews on or before ``until``, regardless of ``since``.
        """
        lower = datetime_value(since, "since")
        upper = datetime_value(until, "until")
        if isinstance(until, str) and len(until) == 10 and upper is not None:
            upper = datetime.combine(upper.date(), time.max, tzinfo=UTC)
        if lower and upper and lower > upper:
            raise CRMValidationError("since must be before until")
        applications = self.list_applications(profile_id=profile_id, channel=channel)
        reached = {stage: set() for stage in STAGES}
        dated = {stage: set() for stage in STAGES}
        unknown = {stage: set() for stage in STAGES}
        known = {stage: set() for stage in STAGES}
        application_dates: dict[int, datetime] = {}
        team_by_period_end: set[int] = set()
        channels: dict[str, dict[str, Any]] = {}
        for item in applications:
            known_application_events = []
            for event in item.history:
                if event.to_stage not in reached:
                    continue
                reached[event.to_stage].add(item.id)
                when = datetime_value(event.occurred_at, "occurred_at")
                if when is None:
                    unknown[event.to_stage].add(item.id)
                else:
                    known[event.to_stage].add(item.id)
                    if event.to_stage == "applied":
                        known_application_events.append(when)
                    elif event.to_stage == "team_interview" and (upper is None or when <= upper):
                        team_by_period_end.add(item.id)
                    if (
                        event.to_stage != "applied"
                        and (lower is None or when >= lower)
                        and (upper is None or when <= upper)
                    ):
                        dated[event.to_stage].add(item.id)
            # An explicitly entered application date is evidence even when the
            # application was imported at a later stage or edited after creation.
            applied_date = datetime_value(item.applied_at, "applied_at")
            if applied_date is None and known_application_events:
                applied_date = min(known_application_events)
            if applied_date is not None:
                application_dates[item.id] = applied_date
                known["applied"].add(item.id)
                reached["applied"].add(item.id)
                if (lower is None or applied_date >= lower) and (
                    upper is None or applied_date <= upper
                ):
                    dated["applied"].add(item.id)
        for stage in STAGES:
            # A sparse imported event does not make the whole application undated
            # once another event supplies a known date for that same stage.
            unknown[stage].difference_update(known[stage])
        for channel_name in CHANNELS:
            cohort = [item for item in applications if item.channel == channel_name]
            ids = {item.id for item in cohort}
            known_applied_ids = {
                item.id
                for item in cohort
                if item.id in application_dates
                and (lower is None or application_dates[item.id] >= lower)
                and (upper is None or application_dates[item.id] <= upper)
            }
            # Dated, explicitly attributable cohorts only. Undated imported data never
            # produces an apparently precise conversion rate.
            team_known_ids = team_by_period_end & known_applied_ids
            unknown_applications = (reached["applied"] & ids) - known["applied"]
            unknown_team = unknown["team_interview"] & known_applied_ids
            rate = None
            if (
                known_applied_ids
                and not unknown_applications
                and not unknown_team
                and channel_name != "unknown"
            ):
                rate = len(team_known_ids) / len(known_applied_ids)
            channels[channel_name] = {
                "applications": len(cohort),
                "applied": len(reached["applied"] & ids),
                "team_interview": len(reached["team_interview"] & ids),
                "dated_applied": len(known_applied_ids),
                "dated_team_interview": len(team_known_ids),
                "unknown_date_applications": len(unknown_applications),
                "unknown_date_team_interviews": len(unknown_team),
                "team_from_applied_rate": rate,
            }
        current = Counter(item.stage for item in applications)
        return {
            "total": len(applications),
            "by_stage": {stage: current[stage] for stage in STAGES},
            "reached_stages": {stage: len(ids) for stage, ids in reached.items()},
            "dated_reached_stages": {stage: len(ids) for stage, ids in dated.items()},
            "unknown_date_reached_stages": {stage: len(ids) for stage, ids in unknown.items()},
            "by_channel": channels,
            "team_interview_goal": 4,
            "period": {"since": _iso(lower), "until": _iso(upper)},
        }
