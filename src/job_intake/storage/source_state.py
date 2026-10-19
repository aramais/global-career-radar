"""Reconcile a fresh observation before making decisions about its source.

An incomplete card is an observation of a vacancy, not a replacement for its
previously fetched details. Conflicting new excerpt text remains visible to the
rules and requires review until a complete detail refresh resolves it.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import fields
from typing import Any

from job_intake.annotation.text import source_units
from job_intake.models.job import JobRecord, JobStatus

OBSERVATION_FIELDS = frozenset({
    "listing_observed_at", "collection_id", "published_label", "detail_error",
    "updated_at", "feed_position", "listing_url", "fetched_at", "collected_at",
    "detail_fetched_at", "detail_outcome", "search_context", "date_precision", "date_source",
    "publication_date_estimated",
})
_DETAIL_FIELDS = (
    "apply_url", "posted_at", "location_text", "remote_text", "employment_type",
    "salary_text", "timezone_text",
)


def record_from_saved(saved: Any) -> JobRecord:
    """Copy only source fields, avoiding ORM state and mutable JSON aliases."""
    values = {
        item.name: deepcopy(getattr(saved, item.name))
        for item in fields(JobRecord) if item.init
    }
    values["status"] = JobStatus(values["status"])
    return JobRecord(**values)


def source_snapshot(record: JobRecord) -> dict:
    result = {
        item.name: deepcopy(getattr(record, item.name))
        for item in fields(JobRecord) if item.init and item.name != "annotation"
    }
    result["status"] = record.status.value
    if record.posted_at is not None:
        result["posted_at"] = record.posted_at.isoformat()
    return result


def incomplete_source(record: JobRecord) -> bool:
    meta = record.source_metadata
    return (
        not (record.description_clean or record.description_raw).strip()
        or meta.get("description_complete") is False
        or meta.get("description_truncated") is True
        or meta.get("truncated") is True
        or (meta.get("description_complete") is not True and meta.get("description_source")
            in {"listing_card", "email_excerpt"})
    )


def _unchanged_excerpt(incoming: JobRecord, previous: JobRecord) -> bool:
    # A substring can reverse the assertion: "authorization required" is also
    # contained in "No authorization required". Compare whole attributable units.
    def assertions(record):
        return [" ".join(unit["text"].split()).casefold() for unit in source_units(record)
                if unit["field"].startswith("description") and unit["text"].strip()]

    before = set(assertions(previous))
    return all(assertion in before for assertion in assertions(incoming))


def _fact_metadata(metadata: dict) -> dict:
    return {key: value for key, value in metadata.items()
            if key not in OBSERVATION_FIELDS and key != "collection_observations"}


def reconcile_source(incoming: JobRecord, saved: Any | None) -> JobRecord:
    """Mutate the incoming transport record before classification and scoring."""
    incoming._source_reconciled = True
    incoming._expected_source_revision = saved.source_revision if saved is not None else None
    if saved is None:
        return incoming
    previous = record_from_saved(saved)
    if not incoming.annotation:
        incoming.annotation = deepcopy(previous.annotation)
    if not incomplete_source(incoming) or not (
        previous.description_clean or previous.description_raw
    ).strip():
        return incoming

    excerpt = incoming.description_clean or incoming.description_raw
    body = previous.description_clean or previous.description_raw
    unchanged_excerpt = _unchanged_excerpt(incoming, previous)
    conflicts = deepcopy(previous.source_metadata.get("source_conflicts", []))
    metadata = deepcopy(previous.source_metadata)
    fresh_metadata = deepcopy(incoming.source_metadata)
    observations = dict(metadata.pop("collection_observations", {}) or {})
    for key in OBSERVATION_FIELDS:
        if key in metadata:
            observations[key] = metadata.pop(key)
        if key in fresh_metadata:
            observations[key] = fresh_metadata.pop(key)
    observations.update(fresh_metadata.pop("collection_observations", {}) or {})
    for key in ("description_complete", "description_source", "description_truncated", "truncated"):
        if key in fresh_metadata:
            observations["incoming_" + key] = fresh_metadata.pop(key)
    for key in ("working_language", "applicant_location_requirements"):
        old, fresh = metadata.get(key), fresh_metadata.get(key)
        if old and fresh and old != fresh:
            if any(conflict.get("field") == "source_metadata." + key
                   and conflict.get("incoming") == fresh for conflict in conflicts):
                fresh_metadata[key] = old
                continue
            conflicts.append({"field": "source_metadata." + key, "previous": old,
                              "incoming": fresh, "reason": "incomplete_fact_conflict"})
            values = old if isinstance(old, list) else [old]
            values += fresh if isinstance(fresh, list) else [fresh]
            fresh_metadata[key] = list(dict.fromkeys(values))
    metadata.update(fresh_metadata)
    if fresh_metadata.get("board_reappearance_verified") is True and (
        previous.source_metadata.get("closure_reason") == "missing_from_complete_snapshots"
    ):
        metadata.pop("closure_reason", None)
        metadata.pop("closure_provenance", None)
    metadata["collection_observations"] = observations
    incoming.description_raw = previous.description_raw
    incoming.description_clean = previous.description_clean
    for name in _DETAIL_FIELDS:
        if getattr(incoming, name) in (None, ""):
            setattr(incoming, name, getattr(previous, name))
        elif name not in {"posted_at", "apply_url"} and getattr(previous, name) and (
            getattr(incoming, name) != getattr(previous, name)
        ):
            old, fresh = getattr(previous, name), getattr(incoming, name)
            if any(conflict.get("field") == name and conflict.get("incoming") == fresh
                   for conflict in conflicts):
                setattr(incoming, name, old)
                continue
            conflicts.append({"field": name, "previous": old, "incoming": fresh,
                              "reason": "incomplete_fact_conflict"})
            setattr(incoming, name,
                    f"Previous complete detail: {old}\nLatest incomplete listing: {fresh}")
    if incoming.status == JobStatus.UNKNOWN:
        incoming.status = previous.status
    elif previous.status == JobStatus.CLOSED and incoming.status == JobStatus.OPEN and not (
        incoming.source_metadata.get("board_reappearance_verified") is True
        and previous.source_metadata.get("closure_reason") == "missing_from_complete_snapshots"
    ):
        observations["incoming_status"] = "open_unverified"
        incoming.status = previous.status

    if excerpt.strip() and not unchanged_excerpt:
        # Keep both attributable versions: a new explicit restriction must reach
        # the rules even when fetching its complete detail page failed.
        suffix = "\n\nLatest incomplete listing update:\n" + excerpt
        incoming.description_clean = body + suffix
        if incoming.description_raw:
            incoming.description_raw += suffix
        conflicts.append({
            "field": "description", "reason": "new_incomplete_excerpt",
            "incoming_excerpt": excerpt,
        })
    if conflicts:
        metadata["source_conflicts"] = conflicts
        metadata["description_complete"] = False
    incoming.source_metadata = metadata
    incoming._text_units_cache = None
    return incoming


def would_discard_source(incoming: JobRecord, saved: Any) -> bool:
    """Repository guard: callers must score the reconciled source, not a card."""
    if incoming._source_reconciled:
        return False
    candidate = record_from_saved(incoming)
    reconcile_source(candidate, saved)
    candidate_snapshot, incoming_snapshot = source_snapshot(candidate), source_snapshot(incoming)
    candidate_snapshot["source_metadata"] = _fact_metadata(candidate.source_metadata)
    incoming_snapshot["source_metadata"] = _fact_metadata(incoming.source_metadata)
    return candidate_snapshot != incoming_snapshot
