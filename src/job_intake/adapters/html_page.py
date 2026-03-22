from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from job_intake.adapters.base import JobSourceAdapter
from job_intake.models.job import JobRecord
from job_intake.utils.http import build_session, fetch_text
from job_intake.utils.text import compact_text


class HtmlPageAdapter(JobSourceAdapter):
    def fetch_jobs(self) -> list[JobRecord]:
        session = build_session(headers=self.params.get("headers"))
        html = fetch_text(session, self.params["url"])
        soup = BeautifulSoup(html, "html.parser")
        jobs = []

        for card in soup.select(self.params["listing_selector"]):
            title_node = card.select_one(self.params["title_selector"])
            company_node = card.select_one(self.params["company_selector"])
            if title_node is None or company_node is None:
                continue

            title = compact_text(title_node.get_text(" ", strip=True))
            company = compact_text(company_node.get_text(" ", strip=True))
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

            jobs.append(
                JobRecord(
                    source=self.name,
                    source_job_id=source_job_id,
                    company=company,
                    title=title,
                    original_url=job_url,
                    apply_url=apply_url or job_url,
                    posted_at=datetime.now(timezone.utc),
                    location_text=location,
                    remote_text=remote,
                    salary_text=salary,
                    employment_type=employment_type,
                    timezone_text=timezone_text,
                    description_raw=description,
                    description_clean=description,
                    source_metadata={"source_url": self.params["url"]},
                )
            )
        return jobs

    def _read_optional_text(self, card, key: str) -> str | None:
        selector = self.params.get(key)
        if not selector:
            return None
        node = card.select_one(selector)
        if node is None:
            return None
        return compact_text(node.get_text(" ", strip=True)) or None
