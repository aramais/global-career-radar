from job_intake.models.job import JobRecord
from job_intake.storage.dedup import JobDeduplicator


def test_dedup_uses_source_job_id_when_present() -> None:
    dedup = JobDeduplicator()
    job1 = JobRecord(
        source="dailyremote",
        source_job_id="123",
        company="Acme",
        title="Product Analytics Lead",
        original_url="https://example.com/job?utm_source=test",
        description_clean="Role one",
    )
    job2 = JobRecord(
        source="dailyremote",
        source_job_id="123",
        company="Acme",
        title="Product Analytics Lead",
        original_url="https://example.com/job?utm_source=other",
        description_clean="Role one updated",
    )

    assert dedup.build_identity(job1).job_uid == dedup.build_identity(job2).job_uid


def test_dedup_falls_back_to_canonical_url() -> None:
    dedup = JobDeduplicator()
    job1 = JobRecord(
        source="html",
        company="Acme",
        title="Lead Product Analyst",
        original_url="https://example.com/jobs/42?utm_campaign=abc",
        description_clean="Same job",
    )
    job2 = JobRecord(
        source="html",
        company="Acme",
        title="Lead Product Analyst",
        original_url="https://example.com/jobs/42?utm_campaign=xyz",
        description_clean="Same job",
    )

    assert dedup.build_identity(job1).job_uid == dedup.build_identity(job2).job_uid


def test_dedup_uses_fingerprint_without_ids_or_urls() -> None:
    dedup = JobDeduplicator()
    job1 = JobRecord(
        source="html",
        company="Acme",
        title="Lead Product Analyst",
        original_url="",
        description_clean="Pricing and experimentation leadership for marketplace team.",
        location_text="Worldwide",
    )
    job2 = JobRecord(
        source="other",
        company="Acme",
        title="Lead Product Analyst",
        original_url="",
        description_clean="Pricing and experimentation leadership for marketplace team.",
        location_text="Worldwide",
    )

    assert dedup.build_identity(job1).job_uid == dedup.build_identity(job2).job_uid
