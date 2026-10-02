from __future__ import annotations

from urllib.parse import quote, urlencode, urlparse

from job_intake.adapters._parsing import bounded_int, html_text, parse_date
from job_intake.adapters.base import JobSourceAdapter
from job_intake.models.job import JobRecord, JobStatus
from job_intake.utils.http import build_session
from job_intake.utils.text import compact_text


def fetch_json_payload(session, url: str):
    # Lever returns a JSON array, unlike the existing dict-only HTTP helper.
    response = session.get(url, timeout=20)
    response.raise_for_status()
    return response.json()


def _token(params: dict, key: str) -> str:
    raw = params.get(key)
    if not isinstance(raw, str):
        raise ValueError(f"{key} must be a nonempty ATS board/site name")
    value = raw.strip()
    if not value or "/" in value or value in {".", ".."}:
        raise ValueError(f"{key} must be a nonempty ATS board/site name")
    return quote(value, safe="")


def _job_url(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("job URL must be an HTTP(S) URL")
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("job URL must be an HTTP(S) URL")
    return value


def _remote(location: str | None) -> str | None:
    return "Remote" if location and "remote" in location.casefold() else None


class GreenhouseAdapter(JobSourceAdapter):
    """Public Job Board API: one list call includes the complete HTML description."""

    def fetch_jobs(self) -> list[JobRecord]:
        self.errors.clear()
        token = _token(self.params, "board_token")
        url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"
        payload = fetch_json_payload(build_session(headers=self.params.get("headers")), url)
        if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
            raise ValueError("Greenhouse response must contain a jobs array")
        jobs = []
        for item in payload["jobs"]:
            try:
                jobs.append(self._parse_job(item, url))
            except (AttributeError, KeyError, TypeError, ValueError) as exc:
                self.errors.append(f"Greenhouse job skipped: {exc}")
        return jobs

    def _parse_job(self, item: dict, api_url: str) -> JobRecord:
        job_id = str(item["id"])
        title = compact_text(item["title"])
        url = _job_url(item["absolute_url"])
        if not title or not url:
            raise ValueError("job title and absolute_url are required")
        raw = str(item.get("content") or "")
        description = html_text(raw)
        location = item.get("location") or {}
        location_name = location.get("name") if isinstance(location, dict) else str(location)
        return JobRecord(
            source=self.name,
            source_job_id=job_id,
            company=str(self.params.get("company") or item.get("company_name") or self.name),
            title=title,
            original_url=url,
            apply_url=url,
            # updated_at is an edit date, not the date this vacancy was published.
            posted_at=parse_date(item.get("first_published")),
            location_text=location_name,
            remote_text=_remote(location_name),
            description_raw=raw,
            description_clean=description,
            status=JobStatus.OPEN,
            source_metadata={
                "ats": "greenhouse",
                "api_url": api_url,
                "board_token": self.params["board_token"],
                "description_complete": bool(description),
                "description_source": "api",
                "updated_at": item.get("updated_at"),
                "language": item.get("language"),
                "departments": item.get("departments", []),
                "offices": item.get("offices", []),
                "custom_fields": item.get("metadata"),
            },
        )


class AshbyAdapter(JobSourceAdapter):
    """Public Job Posting API includes full descriptions and optional compensation."""

    def fetch_jobs(self) -> list[JobRecord]:
        self.errors.clear()
        board = _token(self.params, "board_name")
        url = f"https://api.ashbyhq.com/posting-api/job-board/{board}?includeCompensation=true"
        payload = fetch_json_payload(build_session(headers=self.params.get("headers")), url)
        if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
            raise ValueError("Ashby response must contain a jobs array")
        jobs = []
        for item in payload["jobs"]:
            try:
                # Unlisted postings are direct-link postings, not public search results.
                if item.get("isListed") is False and not self.params.get("include_unlisted", False):
                    continue
                jobs.append(self._parse_job(item, url))
            except (AttributeError, KeyError, TypeError, ValueError) as exc:
                self.errors.append(f"Ashby job skipped: {exc}")
        return jobs

    def _parse_job(self, item: dict, api_url: str) -> JobRecord:
        title = compact_text(item["title"])
        url = _job_url(item["jobUrl"])
        if not title or not url:
            raise ValueError("job title and jobUrl are required")
        raw = str(item.get("descriptionHtml") or item.get("descriptionPlain") or "")
        description = compact_text(item.get("descriptionPlain")) or html_text(raw)
        locations = [item.get("location")]
        for location in item.get("secondaryLocations") or []:
            if isinstance(location, dict):
                locations.append(location.get("location"))
        location_text = "; ".join(dict.fromkeys(str(value) for value in locations if value)) or None
        compensation = item.get("compensation") or {}
        salary = None
        if isinstance(compensation, dict):
            salary = compensation.get("scrapeableCompensationSalarySummary") or compensation.get(
                "compensationTierSummary"
            )
        return JobRecord(
            source=self.name,
            source_job_id=str(item.get("id") or urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]),
            company=str(self.params.get("company") or self.name),
            title=title,
            original_url=url,
            apply_url=item.get("applyUrl") or url,
            posted_at=parse_date(item.get("publishedAt")),
            location_text=location_text,
            remote_text=item.get("workplaceType")
            or ("Remote" if item.get("isRemote") is True else None),
            employment_type=item.get("employmentType"),
            salary_text=salary,
            description_raw=raw,
            description_clean=description,
            status=JobStatus.OPEN,
            source_metadata={
                "ats": "ashby",
                "api_url": api_url,
                "board_name": self.params["board_name"],
                "description_complete": bool(description),
                "description_source": "api",
                "department": item.get("department"),
                "team": item.get("team"),
                "address": item.get("address"),
                "secondary_locations": item.get("secondaryLocations", []),
                "compensation": compensation,
            },
        )


class LeverAdapter(JobSourceAdapter):
    """Public Postings API; bounded pagination and all body/list/closing blocks."""

    def fetch_jobs(self) -> list[JobRecord]:
        self.errors.clear()
        site = _token(self.params, "site")
        region = self.params.get("region", "global")
        if region not in {"global", "eu"}:
            raise ValueError("Lever region must be global or eu")
        host = "api.eu.lever.co" if region == "eu" else "api.lever.co"
        page_size = bounded_int(self.params, "page_size", 100, 1000)
        max_pages = bounded_int(self.params, "max_pages", 10, 50)
        session = build_session(headers=self.params.get("headers"))
        jobs = []
        seen_ids = set()
        for page in range(max_pages):
            query = urlencode({"mode": "json", "skip": page * page_size, "limit": page_size})
            url = f"https://{host}/v0/postings/{site}?{query}"
            try:
                payload = fetch_json_payload(session, url)
                if not isinstance(payload, list):
                    raise ValueError("Lever response must be a JSON array")
            except Exception as exc:
                if not jobs:
                    raise
                self.errors.append(f"Lever page {page + 1}: {exc}")
                break
            unseen = 0
            for item in payload:
                try:
                    job = self._parse_job(item, url)
                    if job.source_job_id in seen_ids:
                        continue
                    seen_ids.add(job.source_job_id)
                    jobs.append(job)
                    unseen += 1
                except (AttributeError, KeyError, TypeError, ValueError) as exc:
                    self.errors.append(f"Lever job skipped: {exc}")
            if len(payload) < page_size or not unseen:
                break
            if page == max_pages - 1:
                self.errors.append(
                    "Lever pagination limit reached; this collection may be incomplete"
                )
        return jobs

    def _parse_job(self, item: dict, api_url: str) -> JobRecord:
        title = compact_text(item["text"])
        url = _job_url(item["hostedUrl"])
        job_id = str(item["id"])
        if not title or not url:
            raise ValueError("job text and hostedUrl are required")
        raw_parts = [str(item.get("description") or item.get("descriptionPlain") or "")]
        for block in item.get("lists") or []:
            raw_parts.extend([str(block.get("text") or ""), str(block.get("content") or "")])
        raw_parts.extend(
            [
                str(item.get("additional") or item.get("additionalPlain") or ""),
                str(item.get("salaryDescription") or item.get("salaryDescriptionPlain") or ""),
            ]
        )
        raw = "\n".join(part for part in raw_parts if part)
        description = html_text(raw)
        categories = item.get("categories") or {}
        locations = categories.get("allLocations") or [categories.get("location")]
        if categories.get("location") and categories["location"] not in locations:
            locations = [categories["location"], *locations]
        location_text = "; ".join(dict.fromkeys(str(value) for value in locations if value)) or None
        salary = item.get("salaryRange")
        salary_text = None
        if isinstance(salary, dict):
            salary_text = compact_text(
                f"{salary.get('currency', '')} {salary.get('min', '')}–{salary.get('max', '')} "
                f"{salary.get('interval', '')}"
            )
        return JobRecord(
            source=self.name,
            source_job_id=job_id,
            company=str(self.params.get("company") or self.name),
            title=title,
            original_url=url,
            apply_url=item.get("applyUrl") or url,
            # The public Lever API does not document a publication timestamp.
            posted_at=None,
            location_text=location_text,
            remote_text=item.get("workplaceType") or _remote(location_text),
            employment_type=categories.get("commitment"),
            salary_text=salary_text,
            description_raw=raw,
            description_clean=description,
            status=JobStatus.OPEN,
            source_metadata={
                "ats": "lever",
                "api_url": api_url,
                "site": self.params["site"],
                "description_complete": bool(description),
                "description_source": "api",
                "country": item.get("country"),
                "team": categories.get("team"),
                "department": categories.get("department"),
                "salary_range": salary,
            },
        )
