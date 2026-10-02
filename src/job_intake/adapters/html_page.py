from __future__ import annotations

from urllib.parse import urljoin

from bs4 import BeautifulSoup

from job_intake.adapters._parsing import (
    bounded_int,
    enrich_from_detail,
    fetch_detail_text,
    parse_date,
    same_origin_url,
)
from job_intake.adapters.base import JobSourceAdapter
from job_intake.models.job import JobRecord
from job_intake.utils.http import build_session, fetch_text
from job_intake.utils.text import compact_text


class HtmlPageAdapter(JobSourceAdapter):
    def fetch_jobs(self) -> list[JobRecord]:
        self.errors.clear()
        session = build_session(headers=self.params.get("headers"))
        html = fetch_text(session, self.params["url"])
        soup = BeautifulSoup(html, "html.parser")
        jobs = []
        detail_count = 0
        fetch_details = self.params.get(
            "fetch_details", bool(self.params.get("detail_description_selector"))
        )
        max_details = bounded_int(self.params, "max_detail_fetches", 100, 1000)

        for card in soup.select(self.params["listing_selector"]):
            title_node = card.select_one(self.params["title_selector"])
            company_selector = self.params.get("company_selector")
            company_node = card.select_one(company_selector) if company_selector else None
            company = self.params.get("company") or (
                compact_text(company_node.get_text(" ", strip=True)) if company_node else None
            )
            if title_node is None or not company:
                continue

            title = compact_text(title_node.get_text(" ", strip=True))
            job_url = urljoin(self.params["url"], title_node.get("href") or "")
            apply_selector = self.params.get("apply_selector")
            apply_url = None
            if apply_selector:
                apply_node = card.select_one(apply_selector)
                if apply_node and apply_node.get("href"):
                    apply_url = urljoin(self.params["url"], apply_node["href"])

            description_selector = self.params.get("description_selector")
            description = ""
            if description_selector:
                desc_node = card.select_one(description_selector)
                if desc_node:
                    description = compact_text(desc_node.get_text(" ", strip=True))

            location = self._read_optional_text(card, "location_selector")
            remote = self._read_optional_text(card, "remote_selector")
            salary = self._read_optional_text(card, "salary_selector")
            employment_type = self._read_optional_text(card, "employment_type_selector")
            timezone_text = self._read_optional_text(card, "timezone_selector")
            source_job_id = card.get(self.params.get("job_id_attribute", "data-job-id"))

            posted_at = None
            date_selector = self.params.get("posted_at_selector")
            if date_selector:
                date_node = card.select_one(date_selector)
                if date_node:
                    posted_at = parse_date(
                        date_node.get("datetime") or date_node.get_text(" ", strip=True)
                    )
            job = JobRecord(
                source=self.name,
                source_job_id=source_job_id,
                company=company,
                title=title,
                original_url=job_url,
                apply_url=apply_url or job_url,
                posted_at=posted_at,
                location_text=location,
                remote_text=remote,
                salary_text=salary,
                employment_type=employment_type,
                timezone_text=timezone_text,
                description_raw=description,
                description_clean=description,
                source_metadata={
                    "source_url": self.params["url"],
                    "description_complete": bool(
                        description and self.params.get("listing_description_complete", False)
                    ),
                    "description_source": "listing_card",
                },
            )
            if fetch_details and detail_count < max_details:
                detail_count += 1
                self._fetch_detail(session, job)
            elif fetch_details:
                job.source_metadata["detail_error"] = "Detail fetch limit reached"
            jobs.append(job)
        return jobs

    def _fetch_detail(self, session, job: JobRecord) -> None:
        if not same_origin_url(self.params["url"], job.original_url):
            job.source_metadata["detail_error"] = "Detail link outside listing origin"
            return
        try:
            soup = BeautifulSoup(
                fetch_detail_text(session, job.original_url, self.params["url"]), "html.parser"
            )
            if not enrich_from_detail(job, soup, self.params.get("detail_description_selector")):
                job.source_metadata["detail_error"] = "No JobPosting or description selector found"
        except Exception as exc:
            job.source_metadata["detail_error"] = str(exc)
            self.errors.append(f"HTML detail {job.original_url}: {exc}")

    def _read_optional_text(self, card, key: str) -> str | None:
        selector = self.params.get(key)
        if not selector:
            return None
        node = card.select_one(selector)
        if node is None:
            return None
        return compact_text(node.get_text(" ", strip=True)) or None
