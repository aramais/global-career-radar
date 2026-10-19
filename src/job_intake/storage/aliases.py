"""Stable requisition aliases without collapsing different regional postings."""
from __future__ import annotations

from urllib.parse import urlparse

from sqlalchemy import select

from job_intake.models.job import JobRecord
from job_intake.storage.models import JobSourceAliasORM
from job_intake.utils.text import canonicalize_url, stable_hash


def source_aliases(record: JobRecord) -> list[tuple[str, str, dict]]:
    meta = record.source_metadata
    result = []
    provider = meta.get("ats")
    board = {"greenhouse": meta.get("board_token"), "ashby": meta.get("board_name"),
             "lever": meta.get("site")}.get(provider)
    if provider and board and record.source_job_id:
        data = {"provider": provider, "board": board, "source_job_id": record.source_job_id,
                "api_host": urlparse(meta.get("api_url") or "").netloc.casefold()}
        key = stable_hash("ats-requisition-v1:" + "|".join(map(str, data.values())))
        result.append((key, "ats_requisition", data))
    for value in (record.original_url, record.apply_url):
        if not value:
            continue
        url = canonicalize_url(value)
        host = urlparse(url).netloc.casefold()
        # General listing/search links are not reliable vacancy identities.
        if host in {"boards.greenhouse.io", "job-boards.greenhouse.io", "jobs.ashbyhq.com",
                    "jobs.lever.co", "jobs.eu.lever.co"} and len(urlparse(url).path.split("/")) > 2:
            row = (stable_hash("ats-url-v1:" + url), "ats_url", {"url": url})
            if row not in result:
                result.append(row)
    return result


def resolve_alias(session, record: JobRecord) -> str | None:
    keys = [key for key, _, _ in source_aliases(record)]
    if not keys:
        return None
    uids = set(session.scalars(select(JobSourceAliasORM.job_uid).where(
        JobSourceAliasORM.alias_key.in_(keys),
    )))
    if len(uids) > 1:
        raise ValueError("Requisition aliases conflict; manual reconciliation required")
    return next(iter(uids), None)


def register_aliases(session, record: JobRecord, job_uid: str) -> None:
    for key, kind, provenance in source_aliases(record):
        row = next((item for item in session.new if isinstance(item, JobSourceAliasORM)
                    and item.alias_key == key), None)
        if row is None:
            row = session.get(JobSourceAliasORM, key)
        if row is not None:
            if row.job_uid != job_uid:
                raise ValueError("Requisition alias already belongs to a different vacancy")
        else:
            session.add(JobSourceAliasORM(alias_key=key, kind=kind, provenance=provenance,
                                          job_uid=job_uid))
