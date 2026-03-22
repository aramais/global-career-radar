from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from job_intake.adapters.base import JobSourceAdapter
from job_intake.models.job import JobRecord
from job_intake.utils.http import build_session, fetch_text
from job_intake.utils.text import compact_text


class DailyRemoteAdapter(JobSourceAdapter):
    def fetch_jobs(self) -> list[JobRecord]:
        session = build_session()
        jobs: list[JobRecord] = []
        for url in self.params.get("search_urls", []):
            html = fetch_text(session, url)
            soup = BeautifulSoup(html, "html.parser")
            jobs.extend(self._parse_listing_page(url, soup))
        return jobs

    def _parse_listing_page(self, base_url: str, soup: BeautifulSoup) -> list[JobRecord]:
        jobs = []
        for heading in soup.select("h2"):
            title_link = heading.select_one("a[href]")
            if title_link is None:
                continue

            title = compact_text(title_link.get_text(" ", strip=True))
            if not title:
                continue

            block = self._collect_block_siblings(heading)
            block_text = compact_text(" ".join(node.get_text(" ", strip=True) for node in block))
            paragraphs = [compact_text(node.get_text(" ", strip=True)) for node in block if node.name == "p"]
            links = [node for node in block if node.name == "a" and node.get("href")]
            company = self._extract_company(block)
            apply_url = None
            for link in links:
                label = compact_text(link.get_text(" ", strip=True)).lower()
                if "apply" in label:
                    apply_url = urljoin(base_url, link["href"])
                    break

            jobs.append(
                JobRecord(
                    source=self.name,
                    source_job_id=title_link.get("href"),
                    company=company or "Unknown",
                    title=title,
                    original_url=urljoin(base_url, title_link["href"]),
                    apply_url=apply_url or urljoin(base_url, title_link["href"]),
                    posted_at=datetime.now(timezone.utc),
                    location_text=self._extract_location(block_text),
                    remote_text="Remote",
                    salary_text=self._extract_salary(block_text),
                    employment_type="Full Time" if "full time" in block_text.casefold() else None,
                    description_raw=paragraphs[0] if paragraphs else block_text,
                    description_clean=paragraphs[0] if paragraphs else block_text,
                    source_metadata={"listing_url": base_url},
                )
            )
        return jobs

    @staticmethod
    def _collect_block_siblings(heading):
        nodes = []
        current = heading.find_next_sibling()
        while current is not None and current.name != "h2":
            nodes.append(current)
            current = current.find_next_sibling()
        return nodes

    @staticmethod
    def _extract_company(nodes) -> str | None:
        for node in nodes:
            text = compact_text(node.get_text(" ", strip=True))
            if "·" in text:
                return compact_text(text.split("·", 1)[0])
        return None

    @staticmethod
    def _extract_location(text: str) -> str | None:
        marker_candidates = [
            "worldwide",
            "united states",
            "canada",
            "argentina",
            "panama",
            "brazil",
            "europe",
            "americas",
            "latin america",
        ]
        lowered = text.casefold()
        for candidate in marker_candidates:
            if candidate in lowered:
                return candidate.title()
        return None

    @staticmethod
    def _extract_salary(text: str) -> str | None:
        if "$" not in text:
            return None
        parts = [part for part in text.split() if "$" in part]
        return " ".join(parts[:4]) or None
