from __future__ import annotations

from dataclasses import dataclass

from job_intake.models.job import JobRecord
from job_intake.utils.text import canonicalize_url, normalize_text, stable_hash


@dataclass(slots=True)
class DedupIdentity:
    job_uid: str
    content_hash: str
    fingerprint: str


class JobDeduplicator:
    def build_identity(self, job: JobRecord) -> DedupIdentity:
        source_id_key = ""
        if job.source_job_id:
            source_id_key = f"{job.source}:{normalize_text(job.source_job_id)}"

        canonical_url = canonicalize_url(job.apply_url or job.original_url)
        url_key = canonical_url or canonicalize_url(job.original_url)
        body_basis = "|".join(
            [
                normalize_text(job.company),
                normalize_text(job.title),
                normalize_text(job.location_text),
                normalize_text(job.remote_text),
                normalize_text(job.description_clean[:800]),
            ]
        )
        fingerprint = stable_hash(body_basis)
        primary_key = source_id_key or url_key or fingerprint
        content_hash = stable_hash(
            "|".join(
                [
                    body_basis,
                    normalize_text(job.salary_text),
                    normalize_text(job.timezone_text),
                    normalize_text(job.employment_type),
                    normalize_text(job.status.value),
                ]
            )
        )
        return DedupIdentity(
            job_uid=stable_hash(primary_key),
            content_hash=content_hash,
            fingerprint=fingerprint,
        )
