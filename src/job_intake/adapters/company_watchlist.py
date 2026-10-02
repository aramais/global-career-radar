from __future__ import annotations

from pathlib import Path

import yaml

from job_intake.adapters.ats import AshbyAdapter, GreenhouseAdapter, LeverAdapter
from job_intake.adapters.base import JobSourceAdapter
from job_intake.adapters.html_page import HtmlPageAdapter
from job_intake.models.job import JobRecord


class CompanyWatchlistAdapter(JobSourceAdapter):
    def fetch_jobs(self) -> list[JobRecord]:
        self.errors.clear()
        watchlist_path = Path(self.params["watchlist_path"]).resolve()
        with watchlist_path.open("r", encoding="utf-8") as handle:
            companies = yaml.safe_load(handle) or {}

        jobs: list[JobRecord] = []
        for item in companies.get("companies", []):
            if not isinstance(item, dict):
                self.errors.append("Invalid company entry: expected a mapping")
                continue
            if not item.get("enabled", True):
                continue
            company_name = item.get("name", "Unknown")
            try:
                adapter = self._build_company_adapter(item)
                company_jobs = adapter.fetch_jobs()
            except Exception as exc:
                self.errors.append(f"{company_name}: {exc}")
                continue
            self.errors.extend(f"{company_name}: {error}" for error in adapter.errors)
            for job in company_jobs:
                job.company = company_name
                job.source_metadata["watchlist_bucket"] = item.get("bucket", "core")
            jobs.extend(company_jobs)
        return jobs

    def _build_company_adapter(self, item: dict) -> JobSourceAdapter:
        adapter_types = {
            "html": HtmlPageAdapter,
            "greenhouse": GreenhouseAdapter,
            "ashby": AshbyAdapter,
            "lever": LeverAdapter,
        }
        adapter_type = item.get("type", "html")
        if adapter_type not in adapter_types:
            raise ValueError(f"Unsupported company adapter type: {adapter_type}")
        # Legacy watchlists store HTML fields directly on each company. The new
        # schema uses type + params and can mix ATS boards with HTML pages.
        params = {
            key: value
            for key, value in item.items()
            if key not in {"name", "enabled", "bucket", "type", "params"}
        }
        params.update(item.get("params") or {})
        params["company"] = item["name"]
        if adapter_type == "html":
            params.setdefault("url", params.get("careers_url"))
        return adapter_types[adapter_type](f"{self.name}:{item['name']}", params)
