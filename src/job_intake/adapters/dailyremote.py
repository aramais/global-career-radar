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


class DailyRemoteAdapter(JobSourceAdapter):
    def fetch_jobs(self) -> list[JobRecord]:
        self.errors.clear()
        session = build_session()
        jobs: list[JobRecord] = []
        max_pages = bounded_int(self.params, "max_pages", 1, 20)
        max_details = bounded_int(self.params, "max_detail_fetches", 100, 1000)
        detail_count = 0
        seen_jobs: set[str] = set()
        for search_url in self.params.get("search_urls", []):
            url = search_url
            visited: set[str] = set()
            for page in range(max_pages):
                if url in visited:
                    break
                visited.add(url)
                try:
                    html = fetch_text(session, url)
                    soup = BeautifulSoup(html, "html.parser")
                    page_jobs = self._parse_listing_page(url, soup)
                except Exception as exc:
                    self.errors.append(f"DailyRemote listing {url}: {exc}")
                    break
                for job in page_jobs:
                    if job.original_url in seen_jobs:
                        continue
                    seen_jobs.add(job.original_url)
                    if self.params.get("fetch_details", True) and detail_count < max_details:
                        detail_count += 1
                        self._fetch_detail(session, url, job)
                    elif self.params.get("fetch_details", True):
                        job.source_metadata["detail_error"] = "Detail fetch limit reached"
                    jobs.append(job)
                next_node = soup.select_one(self.params.get("next_selector", 'a[rel="next"]'))
                if next_node is None or not next_node.get("href"):
                    break
                next_url = urljoin(url, next_node["href"])
                if not same_origin_url(search_url, next_url):
                    self.errors.append(
                        f"DailyRemote pagination link outside listing origin: {next_url}"
                    )
                    break
                if page == max_pages - 1:
                    self.errors.append(
                        "DailyRemote pagination limit reached; collection is incomplete"
                    )
                url = next_url
        return jobs

    def _fetch_detail(self, session, listing_url: str, job: JobRecord) -> None:
        if not same_origin_url(listing_url, job.original_url):
            job.source_metadata["detail_error"] = "Detail link outside listing origin"
            return
        try:
            soup = BeautifulSoup(
                fetch_detail_text(session, job.original_url, listing_url), "html.parser"
            )
            # Current anonymous pages contain dummy HTML Ipsum inside this wrapper,
            # not the vacancy body. Even a custom CSS selector must not turn the
            # placeholder or premium marketing text into a complete description.
            if soup.select_one(".dj-descwrap.dj-paygated, .dj-lock"):
                job.source_metadata.update(
                    description_complete=False,
                    detail_access="restricted",
                    detail_error=(
                        "DailyRemote full description is restricted: "
                        "sign-in/premium access required"
                    ),
                )
                return
            complete = enrich_from_detail(job, soup, self.params.get("detail_description_selector"))
            if not complete and not self.params.get("detail_description_selector"):
                complete = enrich_from_detail(job, soup, ".dj-descwrap:not(.dj-paygated) .dj-prose")
            if not complete:
                job.source_metadata["detail_error"] = "No JobPosting or description selector found"
        except Exception as exc:
            job.source_metadata["detail_error"] = str(exc)
            self.errors.append(f"DailyRemote detail {job.original_url}: {exc}")

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
            paragraphs = [
                compact_text(node.get_text(" ", strip=True)) for node in block if node.name == "p"
            ]
            links = [node for node in block if node.name == "a" and node.get("href")]
            company = self._extract_company(block)
            apply_url = None
            for link in links:
                label = compact_text(link.get_text(" ", strip=True)).lower()
                if "apply" in label:
                    apply_url = urljoin(base_url, link["href"])
                    break

            # Use only explicit source dates. A collection time is not a posting date.
            posted_at = None
            for node in block:
                time_nodes = [node] if node.name == "time" else node.select("time[datetime]")
                for time_node in time_nodes:
                    posted_at = parse_date(time_node.get("datetime")) or posted_at
            # Include every block, not just the first paragraph: eligibility requirements
            # can be in lists or later paragraphs in the card.
            description = block_text

            jobs.append(
                JobRecord(
                    source=self.name,
                    source_job_id=title_link.get("href"),
                    company=company or "Unknown",
                    title=title,
                    original_url=urljoin(base_url, title_link["href"]),
                    apply_url=apply_url or urljoin(base_url, title_link["href"]),
                    posted_at=posted_at,
                    location_text=self._extract_location(block_text),
                    remote_text="Remote",
                    salary_text=self._extract_salary(block_text),
                    employment_type="Full Time" if "full time" in block_text.casefold() else None,
                    description_raw=description,
                    description_clean=description,
                    source_metadata={
                        "listing_url": base_url,
                        "description_complete": False,
                        "description_source": "listing_card",
                        "card_paragraph_count": len(paragraphs),
                    },
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
