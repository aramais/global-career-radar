from __future__ import annotations

from collections import defaultdict

from job_intake.storage.models import JobORM


def format_job_line(job: JobORM) -> str:
    risks = f" | Risks: {', '.join(job.risks[:2])}" if job.risks else ""
    why = ", ".join(job.matched_signals[:3]) or job.fit_reason
    url = job.apply_url or job.original_url
    return f"- [{job.tier}] {job.title} @ {job.company} | Why: {why}{risks} | Link: {url}"


def build_daily_digest(jobs: list[JobORM]) -> str:
    grouped: dict[str, list[JobORM]] = defaultdict(list)
    for job in jobs:
        grouped[job.tier].append(job)

    lines = ["Daily job digest", ""]
    for tier in ["A", "B"]:
        tier_jobs = grouped.get(tier, [])
        if not tier_jobs:
            continue
        lines.append(f"{tier}-tier roles ({len(tier_jobs)})")
        for job in tier_jobs[:10]:
            lines.append(format_job_line(job))
        lines.append("")
    if len(lines) == 2:
        lines.append("No A/B-tier roles in the digest window.")
    return "\n".join(lines).strip()


def build_instant_alert(job: JobORM) -> str:
    return "\n".join(
        [
            "A-tier job alert",
            f"{job.title} @ {job.company}",
            f"Why: {job.fit_reason}",
            f"Signals: {', '.join(job.matched_signals[:4]) or 'deterministic fit'}",
            f"Risks: {', '.join(job.risks[:2]) or 'none detected'}",
            f"Apply: {job.apply_url or job.original_url}",
        ]
    )
