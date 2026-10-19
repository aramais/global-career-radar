"""Successful full board snapshots are the only evidence for absence closure."""
from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from urllib.parse import urlparse

from sqlalchemy import select

from job_intake.models.job import JobStatus
from job_intake.storage.models import JobORM, SourceRunORM
from job_intake.storage.source_state import record_from_saved
from job_intake.utils.text import canonicalize_url


def _utc(value):
    return value.replace(tzinfo=UTC) if value is not None and value.tzinfo is None else value


def _belongs(job: JobORM, snapshot: dict) -> bool:
    providers = {"GreenhouseAdapter": ("greenhouse", "board_token"),
                 "AshbyAdapter": ("ashby", "board_name"), "LeverAdapter": ("lever", "site")}
    provider, field = providers.get(snapshot.get("adapter_type", snapshot.get("adapter")),
                                    (None, None))
    metadata = job.source_metadata or {}
    if provider and metadata.get("ats") == provider:
        if snapshot.get("api_host") and (
            urlparse(metadata.get("api_url") or "").netloc != snapshot["api_host"]
        ):
            return False
        return str(metadata.get(field)) == str(snapshot.get("board_id"))
    if provider:
        return False
    return job.source == snapshot.get("source")


def record_source_snapshot(session, snapshot: dict, *, now: datetime | None = None,
                           grace_days: float = 7, minimum_observations: int = 2) -> list:
    if grace_days < 0 or type(minimum_observations) is not int or minimum_observations < 2:
        raise ValueError("Source closure needs nonnegative grace and at least two observations")
    now = _utc(now or datetime.now(UTC))
    status = snapshot.get("status", "failed")
    complete = bool(snapshot.get("snapshot_complete")) and status == "success" and (
        snapshot.get("scope") == "board"
    )
    details = deepcopy(snapshot)
    run = SourceRunORM(source=str(snapshot["source"]), status=status,
                       snapshot_complete=complete,
                       record_count=len(snapshot.get("seen_urls", [])), details=details,
                       started_at=now, finished_at=now)
    session.add(run)
    closures = []
    if snapshot.get("scope") != "board":
        return closures
    identifiers = {str(value) for value in snapshot.get("seen_job_ids", [])}
    urls = {canonicalize_url(value) for value in snapshot.get("seen_urls", [])}
    for job in session.scalars(select(JobORM)):
        if not _belongs(job, snapshot):
            continue
        if not complete:
            # A failed interval cannot accrue proven missing time.
            job.missing_last_checked_at = None
            continue
        seen = str(job.source_job_id) in identifiers or canonicalize_url(job.original_url) in urls
        if seen:
            job.last_complete_seen_at = now
            job.missing_since = job.missing_last_checked_at = None
            job.missing_successes = 0
            job.missing_verified_seconds = 0
            if job.status == JobStatus.CLOSED.value and (
                job.source_metadata.get("closure_reason") == "missing_from_complete_snapshots"
            ):
                record = record_from_saved(job)
                record.status = JobStatus.OPEN
                record.source_metadata.pop("closure_reason", None)
                record.source_metadata.pop("closure_provenance", None)
                record.source_metadata["board_reappearance_verified"] = True
                closures.append(record)
            continue
        if job.status == JobStatus.CLOSED.value:
            continue
        job.missing_since = job.missing_since or now
        job.missing_successes += 1
        previous_check = _utc(job.missing_last_checked_at)
        if previous_check is not None:
            job.missing_verified_seconds += max(0, (now - previous_check).total_seconds())
        job.missing_last_checked_at = now
        if (job.missing_successes >= minimum_observations
                and job.missing_verified_seconds >= grace_days * 86400):
            record = record_from_saved(job)
            record.status = JobStatus.CLOSED
            record.source_metadata["closure_reason"] = "missing_from_complete_snapshots"
            record.source_metadata["closure_provenance"] = {
                "board_id": snapshot.get("board_id"), "observations": job.missing_successes,
                "verified_missing_seconds": job.missing_verified_seconds,
            }
            closures.append(record)
    details["lifecycle_candidates"] = len(closures)
    details["closure_candidates"] = sum(record.status == JobStatus.CLOSED for record in closures)
    details["reopen_candidates"] = sum(record.status == JobStatus.OPEN for record in closures)
    run.details = details
    return closures
