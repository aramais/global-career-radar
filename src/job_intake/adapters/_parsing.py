from __future__ import annotations

import json
from datetime import UTC, datetime
from html import unescape
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from job_intake.models.job import JobRecord, JobStatus
from job_intake.utils.text import compact_text


def html_text(value: str | None) -> str:
    """Keep all description blocks, including encoded ATS HTML and later restrictions."""
    if not value:
        return ""
    decoded = str(value)
    # Greenhouse may return HTML entities wrapped in another level of entities.
    for _ in range(2):
        updated = unescape(decoded)
        if updated == decoded:
            break
        decoded = updated
    soup = BeautifulSoup(decoded, "html.parser")
    for node in soup.select("script, style, noscript"):
        node.decompose()
    return compact_text(soup.get_text(" ", strip=True))


def parse_date(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed
    except ValueError:
        return None


def same_origin_url(base_url: str, candidate_url: str) -> bool:
    """Detail/pagination links must stay on the listing origin and use HTTP(S)."""
    base = urlparse(base_url)
    candidate = urlparse(candidate_url)
    return (
        candidate.scheme in {"http", "https"}
        and candidate.scheme == base.scheme
        and candidate.netloc.casefold() == base.netloc.casefold()
        and candidate.username is None
        and candidate.password is None
    )


def fetch_detail_text(session, url: str, listing_url: str) -> str:
    """Validate redirects before following them; a listing cannot cause off-site fetches."""
    for _ in range(4):
        if not same_origin_url(listing_url, url):
            raise ValueError("Detail link or redirect outside listing origin")
        response = session.get(url, timeout=20, allow_redirects=False)
        if response.status_code in {301, 302, 303, 307, 308}:
            redirect = response.headers.get("Location")
            if not redirect:
                raise ValueError("Detail redirect has no Location header")
            url = urljoin(url, redirect)
            continue
        response.raise_for_status()
        return response.text
    raise ValueError("Too many detail redirects")


def bounded_int(params: dict, key: str, default: int, maximum: int) -> int:
    value = int(params.get(key, default))
    if value < 1 or value > maximum:
        raise ValueError(f"{key} must be between 1 and {maximum}")
    return value


def _walk_json(value: object):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_json(child)


def job_posting(soup: BeautifulSoup) -> dict | None:
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            payload = json.loads(script.string or script.get_text())
        except (ValueError, TypeError):
            continue
        for item in _walk_json(payload):
            types = item.get("@type", [])
            if isinstance(types, str):
                types = [types]
            if (
                isinstance(types, list)
                and "JobPosting" in types
                and isinstance(item.get("description"), str)
                and item["description"].strip()
            ):
                return item
    return None


def _location_names(value: object) -> list[str]:
    if isinstance(value, list):
        return [name for item in value for name in _location_names(item)]
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        address = value.get("address", value)
        if isinstance(address, dict):
            parts = [address.get(key) for key in ("addressLocality", "addressRegion")]
            country = address.get("addressCountry")
            if isinstance(country, dict):
                country = country.get("name")
            parts.append(country)
            names = [str(part) for part in parts if part]
            if names:
                return [", ".join(names)]
        if value.get("name"):
            return [str(value["name"])]
    return []


def enrich_from_detail(job: JobRecord, soup: BeautifulSoup, selector: str | None = None) -> bool:
    """Prefer structured JobPosting; never claim an arbitrary page is the full description."""
    posting = job_posting(soup)
    if posting:
        description = html_text(posting.get("description"))
        if description:
            job.description_raw = str(posting["description"])
            job.description_clean = description
            job.posted_at = parse_date(posting.get("datePosted")) or job.posted_at
            organization = posting.get("hiringOrganization")
            if isinstance(organization, dict) and organization.get("name"):
                job.company = compact_text(str(organization["name"]))
            locations = _location_names(posting.get("jobLocation"))
            eligibility = _location_names(posting.get("applicantLocationRequirements"))
            if locations or eligibility:
                job.location_text = "; ".join(dict.fromkeys(locations + eligibility))
            location_type = posting.get("jobLocationType")
            if location_type:
                job.remote_text = "Remote" if location_type == "TELECOMMUTE" else str(location_type)
            employment_type = posting.get("employmentType")
            if employment_type:
                job.employment_type = (
                    ", ".join(map(str, employment_type))
                    if isinstance(employment_type, list)
                    else str(employment_type)
                )
            salary = posting.get("baseSalary")
            if isinstance(salary, dict):
                salary_value = salary.get("value", {})
                if isinstance(salary_value, dict):
                    amount = salary_value.get("value")
                    minimum, maximum = salary_value.get("minValue"), salary_value.get("maxValue")
                    if amount is not None or minimum is not None or maximum is not None:
                        figures = str(amount) if amount is not None else f"{minimum}–{maximum}"
                        job.salary_text = compact_text(
                            f"{salary.get('currency', '')} {figures} "
                            f"{salary_value.get('unitText', '')}"
                        )
            valid_through = parse_date(posting.get("validThrough"))
            if valid_through:
                job.source_metadata["valid_through"] = valid_through.isoformat()
                if valid_through < datetime.now(UTC):
                    job.status = JobStatus.CLOSED
            job.source_metadata.update(
                description_complete=True,
                description_source="json_ld",
                applicant_location_requirements=eligibility,
            )
            return True

    selectors = (
        [selector]
        if selector
        else [
            '[itemprop="description"]',
            '[data-testid="job-description"]',
            ".job-description",
            "#job-description",
        ]
    )
    for candidate in selectors:
        node = soup.select_one(candidate)
        if node:
            description = html_text(str(node))
            if description:
                job.description_raw = str(node)
                job.description_clean = description
                job.source_metadata.update(
                    description_complete=True,
                    description_source="detail_selector",
                    detail_description_selector=candidate,
                )
                return True
    return False
