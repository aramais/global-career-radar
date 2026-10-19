from __future__ import annotations

import re
from itertools import zip_longest
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from job_intake.adapters._dailyremote_auth import fetch_dailyremote_text, load_dailyremote_cookies
from job_intake.adapters._parsing import (
    bounded_int,
    enrich_from_detail,
    fetch_detail_text,
    parse_date,
    publication_metadata,
    same_origin_url,
)
from job_intake.adapters.base import JobSourceAdapter, SourceAuthenticationError, source_snapshot
from job_intake.models.job import JobRecord
from job_intake.utils.http import build_session, fetch_text
from job_intake.utils.text import compact_text


def configure_dailyremote_session(session, params: dict) -> bool:
    """Apply explicit auth policy once; never fall back after a configured auth failure."""
    cookies_file = params.get("cookies_file")
    policy = params.get(
            "auth_policy",
            "require_authenticated" if "cookies_file" in params else "allow_anonymous",
        )
    if policy not in {"require_authenticated", "allow_anonymous"}:
        raise ValueError(
            "DailyRemote auth_policy must be require_authenticated or allow_anonymous"
        )
    if policy == "require_authenticated" and not cookies_file:
        raise SourceAuthenticationError("DailyRemote authenticated session is required")
    if cookies_file:
        try:
            load_dailyremote_cookies(session, cookies_file)
        except ValueError as exc:
            raise SourceAuthenticationError(str(exc)) from None
    return bool(cookies_file)


class DailyRemoteAdapter(JobSourceAdapter):
    @source_snapshot("search")
    def fetch_jobs(self) -> list[JobRecord]:
        self.errors.clear()
        with build_session() as session:
            return self._fetch_jobs(session)

    def _fetch_jobs(self, session) -> list[JobRecord]:
        authenticated = configure_dailyremote_session(session, self.params)
        jobs: list[JobRecord] = []
        max_pages = bounded_int(self.params, "max_pages", 1, 20)
        max_details = bounded_int(self.params, "max_detail_fetches", 100, 1000)
        groups: list[list[JobRecord]] = []
        seen_jobs: set[str] = set()
        for search_url in self.params.get("search_urls", []):
            query_jobs: list[JobRecord] = []
            groups.append(query_jobs)
            url = search_url
            visited: set[str] = set()
            for page in range(max_pages):
                if url in visited:
                    break
                visited.add(url)
                try:
                    html = (
                        fetch_dailyremote_text(session, url)
                        if authenticated
                        else fetch_text(session, url)
                    )
                    soup = BeautifulSoup(html, "html.parser")
                    page_jobs = self._parse_listing_page(url, soup)
                except Exception as exc:
                    status = getattr(getattr(exc, "response", None), "status_code", None)
                    if status in {401, 403} or re.search(
                        r"DailyRemote HTTP (?:401|403)\b", str(exc),
                    ):
                        self._snapshot_auth_required = True
                    self.errors.append(f"DailyRemote listing {url}: {exc}")
                    break
                for job in page_jobs:
                    if job.original_url in seen_jobs:
                        continue
                    seen_jobs.add(job.original_url)
                    job.source_metadata["search_context"] = search_url
                    query_jobs.append(job)
                next_node = soup.select_one(
                    self.params.get(
                        "next_selector", 'a[rel="next"], a[aria-label="Go to next page"]'
                    )
                )
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
        # Establish the search frame before spending a detail quota. Every query
        # receives a turn, so the first broad stream cannot consume all detail reads.
        for row in zip_longest(*groups):
            jobs.extend(job for job in row if job is not None)
        for index, job in enumerate(jobs):
            if self.params.get("fetch_details", True) and index < max_details:
                self._fetch_detail(session, job.source_metadata["listing_url"], job)
            elif self.params.get("fetch_details", True):
                job.source_metadata["detail_error"] = "Detail fetch limit reached"
                job.source_metadata["detail_outcome"] = "quota_deferred"
        if not jobs and not self.errors:
            self.errors.append(
                "DailyRemote search returned no vacancy cards; verify source layout/access"
            )
        return jobs

    def _fetch_detail(self, session, listing_url: str, job: JobRecord) -> None:
        if not same_origin_url(listing_url, job.original_url):
            job.source_metadata["detail_error"] = "Detail link outside listing origin"
            return
        try:
            html = (
                fetch_dailyremote_text(session, job.original_url)
                if self.params.get("cookies_file")
                else fetch_detail_text(session, job.original_url, listing_url)
            )
            soup = BeautifulSoup(html, "html.parser")
            # Current anonymous pages contain dummy HTML Ipsum inside this wrapper,
            # not the vacancy body. Even a custom CSS selector must not turn the
            # placeholder or premium marketing text into a complete description.
            if soup.select_one(".dj-descwrap.dj-paygated, .dj-lock"):
                job.source_metadata.update(
                    description_complete=False,
                    detail_access="restricted",
                    detail_outcome="auth_required",
                    detail_error=(
                        "DailyRemote full description is restricted: "
                        "sign-in/premium access required"
                    ),
                )
                self.errors.append("DailyRemote detail requires sign-in/premium access")
                self._snapshot_auth_required = True
                return
            complete = enrich_from_detail(job, soup, self.params.get("detail_description_selector"))
            if not complete and not self.params.get("detail_description_selector"):
                complete = enrich_from_detail(job, soup, ".dj-descwrap:not(.dj-paygated) .dj-prose")
            if not complete:
                job.source_metadata["detail_error"] = "No JobPosting or description selector found"
                job.source_metadata["detail_outcome"] = "parse_missing_description"
                self.errors.append("DailyRemote detail has no supported vacancy description")
            else:
                job.source_metadata["detail_outcome"] = "success"
        except Exception as exc:
            job.source_metadata["detail_error"] = str(exc)
            job.source_metadata["detail_outcome"] = "request_error"
            self.errors.append(f"DailyRemote detail {job.original_url}: {exc}")

    def _parse_listing_page(self, base_url: str, soup: BeautifulSoup) -> list[JobRecord]:
        jobs = []
        for heading in soup.select("h2"):
            title_link = heading.select_one("a[href]")
            if title_link is None:
                continue

            title = compact_text(title_link.get_text(" ", strip=True))
            original_url = urljoin(base_url, title_link["href"])
            parsed_url = urlparse(original_url)
            if not title or parsed_url.scheme not in {"http", "https"} or not parsed_url.netloc:
                self.errors.append("DailyRemote listing card has no valid title/URL")
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
                    candidate = urljoin(base_url, link["href"])
                    if urlparse(candidate).scheme in {"http", "https"}:
                        apply_url = candidate
                        break

            # Use only explicit source dates. A collection time is not a posting date.
            posted_at = None
            published_label = None
            published_value = None
            for node in block:
                time_nodes = [node] if node.name == "time" else node.select("time[datetime]")
                for time_node in time_nodes:
                    posted_at = parse_date(time_node.get("datetime")) or posted_at
                    published_value = time_node.get("datetime") or published_value
                    published_label = (
                        compact_text(time_node.get_text(" ", strip=True))
                        or time_node.get("datetime") or published_label
                    )
            # Include every block, not just the first paragraph: eligibility requirements
            # can be in lists or later paragraphs in the card.
            description = block_text

            jobs.append(
                JobRecord(
                    source=self.name,
                    source_job_id=title_link.get("href"),
                    company=company or "Unknown",
                    title=title,
                    original_url=original_url,
                    apply_url=apply_url or original_url,
                    posted_at=posted_at,
                    location_text=self._extract_card_location(block),
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
                        **publication_metadata(published_value, "listing_time"),
                        "published_label": published_label,
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
            "worldwide", "global", "latam", "amer",
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
            if re.search(r"(?<!\w)" + re.escape(candidate) + r"(?!\w)", lowered):
                return candidate.title()
        return None

    @classmethod
    def _extract_card_location(cls, nodes) -> str | None:
        locations = []
        for node in nodes:
            if node.name in {"p", "ul", "ol", "a"}:
                continue
            text = compact_text(node.get_text(" ", strip=True))
            if "·" in text:
                candidates = text.split("·")[1:]
                for candidate in candidates:
                    if cls._extract_location(candidate):
                        locations.append(candidate.strip())
        return "; ".join(dict.fromkeys(locations)) or None

    @staticmethod
    def _extract_salary(text: str) -> str | None:
        salary = re.search(
            r"(?:(?:USD|CAD|BRL|EUR|GBP)\s*)?(?:R\$|\$|€|£)\s*"
            r"\d[\d.,]*(?:\s*[kKmM])?(?:\s*(?:[-–—]|to)\s*"
            r"(?:(?:USD|CAD|BRL|EUR|GBP)\s*)?(?:R\$|\$|€|£)?\s*"
            r"\d[\d.,]*(?:\s*[kKmM])?)?"
            r"(?:\s*(?:/|per\s+)?(?:year|yr|annum|month|mo|hour|hr))?", text,
        )
        return compact_text(salary[0]) if salary else None
