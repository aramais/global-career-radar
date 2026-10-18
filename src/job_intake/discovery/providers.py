"""Paid search and bounded retrieval of public vacancy originals.

Search snippets are discovery hints only. A candidate always needs a separately
retrieved description from a JobPosting, an ATS API, or a marked job detail page.
"""
from __future__ import annotations

import ipaddress
import json
import re
import socket
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup

from job_intake.adapters._parsing import enrich_from_detail, html_text, parse_date
from job_intake.adapters.ats import AshbyAdapter, GreenhouseAdapter, LeverAdapter
from job_intake.models.job import JobRecord, JobStatus
from job_intake.utils.http import OriginBoundSession
from job_intake.utils.text import compact_text

_SEARCH_ENDPOINT = "https://api.perplexity.ai/search"
_REDIRECTS = {301, 302, 303, 307, 308}
_MAX_BYTES = 2 * 1024 * 1024
_CLOSED = re.compile(
    r"this (?:job|position|vacancy) (?:is no longer (?:available|accepting)|"
    r"has (?:been filled|expired|closed))|no longer accepting applications",
    re.I,
)


class ProviderError(RuntimeError):
    """Sanitized provider error; an attempted paid request may still be billed."""

    def __init__(
        self,
        message: str,
        *,
        paid_request_attempted: bool = False,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.paid_request_attempted = paid_request_attempted
        self.status_code = status_code


class PerplexitySearch:
    """One HTTP POST per search; never retry a potentially billable operation.

    Endpoint/schema: https://docs.perplexity.ai/api-reference/search-post
    (verified 2026-10-18). The default web search tariff belongs in run config.
    """

    def __init__(
        self, api_key: str, timeout: float = 30, *, session: Any = None
    ) -> None:
        if not isinstance(api_key, str) or not api_key.strip():
            raise ProviderError("PERPLEXITY_API_KEY is required")
        if not isinstance(timeout, (int, float)) or not 0 < timeout <= 120:
            raise ProviderError("Search timeout must be between 0 and 120 seconds")
        self.timeout = timeout
        self._headers = {"Authorization": f"Bearer {api_key.strip()}"}
        self.session = session if session is not None else OriginBoundSession(self._headers)
        # Do not send a configured key through environment proxies or .netrc.
        if session is None:
            self.session.trust_env = False

    def search(self, query: str, max_results: int = 10) -> list[dict]:
        if not isinstance(query, str) or not query.strip():
            raise ProviderError("Search query must be nonempty")
        if isinstance(max_results, bool) or not isinstance(max_results, int) \
                or not 1 <= max_results <= 20:
            raise ProviderError("Search max_results must be between 1 and 20")
        response = None
        try:
            response = self.session.post(
                _SEARCH_ENDPOINT,
                headers=self._headers,
                json={"query": query.strip(), "max_results": max_results},
                timeout=self.timeout,
                allow_redirects=False,
            )
            if not 200 <= response.status_code < 300:
                raise ProviderError(
                    "Perplexity Search request failed",
                    paid_request_attempted=True,
                    status_code=response.status_code,
                )
            try:
                payload = response.json()
            except (ValueError, TypeError):
                raise ProviderError(
                    "Perplexity Search returned invalid JSON", paid_request_attempted=True
                ) from None
            if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
                raise ProviderError(
                    "Perplexity Search response must contain a results array",
                    paid_request_attempted=True,
                )
            hits = []
            for item in payload["results"]:
                if not isinstance(item, dict) or any(
                    not isinstance(item.get(key), str) for key in ("title", "url", "snippet")
                ):
                    raise ProviderError(
                        "Perplexity Search result has invalid title, URL or snippet",
                        paid_request_attempted=True,
                    )
                parsed = urlsplit(item["url"])
                if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                    raise ProviderError(
                        "Perplexity Search result has invalid URL", paid_request_attempted=True
                    )
                hit = {key: item[key] for key in ("title", "url", "snippet")}
                for key in ("date", "last_updated"):
                    if isinstance(item.get(key), str):
                        hit[key] = item[key]
                hits.append(hit)
            return hits[:max_results]
        except ProviderError:
            raise
        except (requests.RequestException, ValueError, TypeError):
            # Request exception strings can contain URLs, headers or response bodies.
            raise ProviderError(
                "Perplexity Search could not complete the request", paid_request_attempted=True
            ) from None
        finally:
            if response is not None:
                response.close()


def _public_target(url: str) -> tuple[str, str]:
    """Reject private literals, local names and DNS results before each request."""
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        if (
            parsed.scheme not in {"http", "https"}
            or not host
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port not in {None, 80, 443}
            or any(char.isspace() for char in url)
            or "\\" in url
        ):
            raise ValueError
        host = host.rstrip(".").lower()
        if host == "localhost" or "." not in host and ":" not in host \
                or host.endswith((".localhost", ".local", ".internal", ".test", ".invalid")):
            raise ValueError
        try:
            addresses = [ipaddress.ip_address(host)]
        except ValueError:
            addresses = [
                ipaddress.ip_address(result[4][0])
                for result in socket.getaddrinfo(
                    host, parsed.port or (443 if parsed.scheme == "https" else 80),
                    type=socket.SOCK_STREAM,
                )
            ]
        if not addresses or any(not address.is_global or address.is_multicast
                                or address.is_reserved for address in addresses):
            raise ValueError
        return (urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, "")),
                str(addresses[0]))
    except (ValueError, OSError, UnicodeError):
        raise ProviderError("Vacancy URL must resolve only to public HTTP(S) addresses") from None


def _public_url(url: str) -> str:
    return _public_target(url)[0]


class _PublicAddressAdapter(requests.adapters.HTTPAdapter):
    """Pin each connection to a validated IP, preserving TLS name verification.

    A DNS pre-check alone leaves a rebinding window when requests resolves the
    hostname again. The pool connects to the selected public literal instead.
    """

    def build_connection_pool_key_attributes(self, request, verify, cert=None):
        host_params, pool_kwargs = super().build_connection_pool_key_attributes(
            request, verify, cert
        )
        _, address = _public_target(request.url)
        parsed = urlsplit(request.url)
        host_params["host"] = address
        request.headers["Host"] = parsed.netloc
        if parsed.scheme == "https":
            pool_kwargs["assert_hostname"] = parsed.hostname
            pool_kwargs["server_hostname"] = parsed.hostname
        return host_params, pool_kwargs


def _identity(url: str) -> tuple[str, str, str]:
    parsed = urlsplit(url)
    query = parse_qs(parsed.query)
    # Retain meaningful IDs while ignoring common tracking parameters.
    keys = sorted(key for key in query if not key.lower().startswith("utm_")
                  and key.lower() not in {"source", "ref", "gh_src", "fbclid", "gclid"})
    return (parsed.netloc.lower(), unquote(parsed.path).rstrip("/"),
            "&".join(f"{key}={','.join(query[key])}" for key in keys))


def _walk(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _postings(soup: BeautifulSoup) -> list[dict]:
    postings = []
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            payload = json.loads(script.string or script.get_text())
        except (ValueError, TypeError, RecursionError):
            continue
        try:
            for item in _walk(payload):
                kinds = item.get("@type", [])
                kinds = [kinds] if isinstance(kinds, str) else kinds
                if isinstance(kinds, list) and "JobPosting" in kinds:
                    postings.append(item)
        except RecursionError:
            continue
    return postings


def _posting_urls(posting: dict, base: str) -> list[str]:
    urls = []
    for value in (posting.get("url"), posting.get("@id"), posting.get("mainEntityOfPage")):
        if isinstance(value, dict):
            value = value.get("@id") or value.get("url")
        if isinstance(value, str):
            urls.append(urljoin(base, value))
    return urls


class PublicJobFetcher:
    """Retrieve a public full description with bounded bytes and redirects.

    No links from the retrieved document are followed. Known ATS API routes are
    constructed from the requested host and job ID, never from page instructions.
    Sessions are injectable for deterministic offline verification.
    """

    def __init__(
        self, timeout: float = 20, *, session: Any = None, max_bytes: int = _MAX_BYTES
    ) -> None:
        if not isinstance(timeout, (int, float)) or not 0 < timeout <= 120:
            raise ProviderError("Fetch timeout must be between 0 and 120 seconds")
        if not isinstance(max_bytes, int) or not 1024 <= max_bytes <= 10 * _MAX_BYTES:
            raise ProviderError("Invalid vacancy response size limit")
        self.timeout = timeout
        self.max_bytes = max_bytes
        self.session = session if session is not None else OriginBoundSession()
        if session is None:
            self.session.trust_env = False
            self.session.mount("http://", _PublicAddressAdapter(max_retries=0))
            self.session.mount("https://", _PublicAddressAdapter(max_retries=0))

    def _get(self, url: str) -> tuple[str, str]:
        for _ in range(5):
            url = _public_url(url)
            response = None
            try:
                response = self.session.get(
                    url, timeout=self.timeout, allow_redirects=False, stream=True,
                    headers={"User-Agent": "job-intake-discovery/0.1"},
                )
                if response.status_code in _REDIRECTS:
                    location = response.headers.get("Location")
                    if not location:
                        raise ProviderError("Vacancy redirect is missing its target")
                    url = urljoin(url, location)
                    continue
                if not 200 <= response.status_code < 300:
                    raise ProviderError(
                        "Vacancy source returned an HTTP error", status_code=response.status_code
                    )
                mime = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
                if mime and mime not in {
                    "text/html", "application/xhtml+xml", "application/json", "text/plain",
                }:
                    raise ProviderError("Vacancy source is not an HTML or JSON document")
                length = response.headers.get("Content-Length")
                if length and length.isdigit() and int(length) > self.max_bytes:
                    raise ProviderError("Vacancy source exceeds the response size limit")
                chunks = []
                size = 0
                for chunk in response.iter_content(chunk_size=65536):
                    size += len(chunk)
                    if size > self.max_bytes:
                        raise ProviderError("Vacancy source exceeds the response size limit")
                    chunks.append(chunk)
                encoding = response.encoding or "utf-8"
                if encoding.lower() == "iso-8859-1":
                    encoding = "utf-8"
                return b"".join(chunks).decode(encoding, errors="replace"), url
            except ProviderError:
                raise
            except (requests.RequestException, UnicodeError, LookupError):
                raise ProviderError("Could not retrieve the vacancy original") from None
            finally:
                if response is not None:
                    response.close()
        raise ProviderError("Vacancy source exceeded the redirect limit")

    def fetch(self, hit: dict) -> JobRecord:
        if not isinstance(hit, dict) or not isinstance(hit.get("url"), str):
            raise ProviderError("Search hit must contain a vacancy URL")
        url = _public_url(hit["url"])
        # These endpoints expose the complete description of the requested job.
        ats = self._ats_route(url)
        if ats:
            api_url, adapter, job_id = ats
            try:
                text, final_url = self._get(api_url)
                payload = json.loads(text)
                if isinstance(adapter, AshbyAdapter):
                    if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
                        raise ValueError
                    payload = next((item for item in payload["jobs"] if isinstance(item, dict)
                                    and str(item.get("id")) == job_id), None)
                    if payload is None:
                        raise ValueError
                if not isinstance(payload, dict):
                    raise ValueError
                job = adapter._parse_job(payload, final_url)
                if job.source_job_id != job_id:
                    raise ValueError
                job.source = "discovery"
                job.company = compact_text(str(payload.get("company_name") or ""))
                job.original_url = _public_url(job.original_url)
                job.apply_url = _public_url(job.apply_url or job.original_url)
                if not job.title or len(job.description_clean) < 120:
                    raise ValueError
                job.status = JobStatus.OPEN
                job.source_metadata["description_complete"] = True
                return job
            except (ProviderError, ValueError, TypeError, KeyError, AttributeError, StopIteration):
                # A stale API route or schema must not turn its JSON into a description.
                # A valid structured original may still be present on the public page.
                pass
        html, final_url = self._get(url)
        soup = BeautifulSoup(html, "html.parser")
        postings = _postings(soup)
        if postings:
            exact = [posting for posting in postings if any(
                _identity(candidate) == _identity(final_url)
                or _identity(candidate) == _identity(url)
                for candidate in _posting_urls(posting, final_url)
            )]
            if len(exact) == 1:
                posting = exact[0]
            elif len(postings) == 1:
                posting = postings[0]
                targets = _posting_urls(posting, final_url)
                if targets and not exact:
                    raise ProviderError("JobPosting belongs to a different vacancy URL")
            else:
                raise ProviderError("Page contains multiple vacancies without an exact URL match")
            return self._structured(posting, final_url)
        return self._html(soup, final_url)

    @staticmethod
    def _ats_route(url: str):
        parsed = urlsplit(url)
        parts = [unquote(part) for part in parsed.path.strip("/").split("/")]
        if any(not re.fullmatch(r"[A-Za-z0-9._-]+", part) for part in parts):
            return None
        host = parsed.hostname
        if host in {"boards.greenhouse.io", "job-boards.greenhouse.io"} \
                and len(parts) == 3 and parts[1] == "jobs" and parts[2].isdigit():
            board, job_id = parts[0], parts[2]
            return (
                f"https://boards-api.greenhouse.io/v1/boards/{quote(board)}/jobs/{job_id}",
                GreenhouseAdapter("discovery", {"board_token": board}), job_id,
            )
        if host in {"jobs.lever.co", "jobs.eu.lever.co"} and len(parts) in {2, 3} \
                and (len(parts) == 2 or parts[2] == "apply"):
            site, job_id = parts[:2]
            api_host = "api.eu.lever.co" if host == "jobs.eu.lever.co" else "api.lever.co"
            return (
                f"https://{api_host}/v0/postings/{quote(site)}/{quote(job_id)}",
                LeverAdapter("discovery", {"site": site}), job_id,
            )
        if host == "jobs.ashbyhq.com" and len(parts) in {2, 3} \
                and (len(parts) == 2 or parts[2] == "application"):
            board, job_id = parts[:2]
            return (
                f"https://api.ashbyhq.com/posting-api/job-board/{quote(board)}",
                AshbyAdapter("discovery", {"board_name": board}), job_id,
            )
        return None

    @staticmethod
    def _structured(posting: dict, url: str) -> JobRecord:
        title = compact_text(posting.get("title") if isinstance(posting.get("title"), str) else "")
        raw = posting.get("description")
        if not title or not isinstance(raw, str) or len(html_text(raw)) < 120:
            raise ProviderError("JobPosting has no complete vacancy title and description")
        job = JobRecord(
            source="discovery", company="", title=title, original_url=url, apply_url=url,
            status=JobStatus.UNKNOWN,
        )
        # Reuse metadata parsing with ONLY the selected posting to avoid selecting
        # another vacancy from a graph or list page.
        selected = BeautifulSoup("<script type='application/ld+json'></script>", "html.parser")
        selected.script.string = json.dumps(posting)
        enrich_from_detail(job, selected)
        identifier = posting.get("identifier")
        if isinstance(identifier, dict):
            identifier = identifier.get("value")
        if isinstance(identifier, (str, int)):
            job.source_job_id = str(identifier)
        valid_through = parse_date(posting.get("validThrough"))
        if (valid_through and valid_through < datetime.now(UTC)) or _CLOSED.search(
            job.description_clean
        ):
            job.status = JobStatus.CLOSED
        job.source_metadata["description_source"] = "json_ld"
        return job

    @staticmethod
    def _html(soup: BeautifulSoup, url: str) -> JobRecord:
        apply = any(re.search(r"apply|application|candidat", a.get_text(" ", strip=True), re.I)
                    for a in soup.select("a[href], button"))
        for node in soup.select("script, style, noscript, nav, footer, header, aside, form"):
            node.decompose()
        title_node = soup.select_one("h1")
        title = compact_text(title_node.get_text(" ", strip=True)) if title_node else ""
        if not title or len(title) > 250 or re.fullmatch(
            r"(?:careers?|jobs?|open (?:roles|positions)|job opportunities|join (?:us|our team))",
            title, re.I,
        ):
            raise ProviderError("Page is not an identifiable vacancy detail")
        container = soup.select_one(
            '[itemprop="description"], [data-testid="job-description"], '
            '.job-description, #job-description, main, article'
        )
        if container is None:
            raise ProviderError("Vacancy page has no full description container")
        description = html_text(str(container))
        # Require distinct duty and candidate sections plus an application action.
        # Long careers pages and blog prose must not become false full descriptions.
        duties = re.search(
            r"responsibilit|what you(?:[’']ll| will) do|your (?:role|impact)|"
            r"about (?:the|this) role|o que voc[eê] vai fazer|suas atividades", description, re.I,
        )
        requirements = re.search(
            r"qualifications?|requirements?|what you(?:[’']ll| will) bring|"
            r"who you are|what we(?:[’']re| are) looking for|requisitos|experi[eê]ncia",
            description, re.I,
        )
        if len(description) < 500 or not duties or not requirements or not apply:
            raise ProviderError("Page lacks sufficient full vacancy detail signals")
        organization = soup.select_one('[itemprop="hiringOrganization"]')
        company = compact_text(organization.get_text(" ", strip=True)) if organization else ""
        return JobRecord(
            source="discovery", company=company, title=title, original_url=url, apply_url=url,
            description_raw=str(container), description_clean=description,
            status=JobStatus.CLOSED if _CLOSED.search(description) else JobStatus.UNKNOWN,
            source_metadata={"description_complete": True, "description_source": "html_detail"},
        )
