from __future__ import annotations

from pathlib import Path

import yaml

from job_intake.adapters.base import JobSourceAdapter
from job_intake.adapters.html_page import HtmlPageAdapter
from job_intake.models.job import JobRecord


class CompanyWatchlistAdapter(JobSourceAdapter):
    def fetch_jobs(self) -> list[JobRecord]:
        watchlist_path = Path(self.params["watchlist_path"]).resolve()
        with watchlist_path.open("r", encoding="utf-8") as handle:
            companies = yaml.safe_load(handle) or {}

        jobs: list[JobRecord] = []
        for item in companies.get("companies", []):
            if not item.get("enabled", True):
                continue
            adapter = HtmlPageAdapter(
                name=f"{self.name}:{item['name']}",
                params={
                    "url": item["careers_url"],
                    "listing_selector": item["listing_selector"],
                    "title_selector": item["title_selector"],
                    "company_selector": item.get("company_selector", item["title_selector"]),
                    "description_selector": item.get("description_selector"),
                    "apply_selector": item.get("apply_selector"),
                    "location_selector": item.get("location_selector"),
                    "salary_selector": item.get("salary_selector"),
                    "employment_type_selector": item.get("employment_type_selector"),
                    "timezone_selector": item.get("timezone_selector"),
                    "job_id_attribute": item.get("job_id_attribute", "data-job-id"),
                    "headers": item.get("headers"),
                },
            )
            company_jobs = adapter.fetch_jobs()
            for job in company_jobs:
                job.company = item["name"]
                job.source_metadata["watchlist_bucket"] = item.get("bucket", "core")
            jobs.extend(company_jobs)
        return jobs
