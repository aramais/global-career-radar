"""Paid search and bounded retrieval of public vacancy originals.

Search snippets are discovery hints only. A candidate always needs a separately
retrieved description from a JobPosting, an ATS API, or a marked job detail page.
"""
from __future__ import annotations

import ipaddress
import json
import math
import os
import re
import socket
import threading
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx
import requests
from bs4 import BeautifulSoup
from perplexity import APIError, APIStatusError, Perplexity
from pydantic import ValidationError

from job_intake.adapters._parsing import enrich_from_detail, html_text, parse_date
from job_intake.adapters.ats import AshbyAdapter, GreenhouseAdapter, LeverAdapter
from job_intake.models.job import JobRecord, JobStatus
from job_intake.utils.http import OriginBoundSession
from job_intake.utils.text import canonicalize_url, compact_text

_SEARCH_ENDPOINT = "https://api.perplexity.ai/search"
_REDIRECTS = {301, 302, 303, 307, 308}
_MAX_BYTES = 2 * 1024 * 1024
_CLOSED = re.compile(
    r"this (?:job|position|vacancy) (?:is no longer (?:available|accepting)|"
    r"has (?:been filled|expired|closed))|no longer accepting applications|"
    r"(?:esta |essa )?vaga (?:foi (?:encerrada|preenchida)|encerrada|n[aã]o est[aá] mais "
    r"dispon[ií]vel)|n[aã]o (?:estamos |est[aá] )?(?:mais )?aceitando candidaturas",
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
        retry_after_seconds: float | None = None,
        billing_known_free: bool = False,
        outcome: str | None = None,
    ) -> None:
        super().__init__(message)
        self.paid_request_attempted = paid_request_attempted
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds
        self.billing_known_free = billing_known_free
        self.outcome = outcome or ("http_error" if status_code else "parse_schema")


class PerplexitySearch:
    """Official SDK, one visible POST per call, shared Retry-After cooldown.

    Endpoint/schema: https://docs.perplexity.ai/api-reference/search-post
    (verified 2026-10-18). The default web search tariff belongs in run config.
    """

    def __init__(self, api_key: str | None = None, timeout: float = 30, *,
                 session: Any = None, options: dict | None = None) -> None:
        api_key = os.getenv("PERPLEXITY_API_KEY", "") if api_key is None else api_key
        if not isinstance(api_key, str) or not api_key.strip():
            raise ProviderError("PERPLEXITY_API_KEY is required. Create a key at "
                                "https://console.perplexity.ai and export it in your terminal.")
        if type(timeout) not in (int, float) or not math.isfinite(timeout) \
                or not 0 < timeout <= 120:
            raise ProviderError("Search timeout must be between 0 and 120 seconds")
        self.timeout = timeout
        self.options = self._options(options or {})
        self._cooldown_lock = threading.Lock()
        self._not_before = 0.0
        self._local = threading.local()
        self.client = Perplexity(
            api_key=api_key.strip(), base_url="https://api.perplexity.ai", max_retries=0,
            timeout=timeout, _strict_response_validation=True,
            http_client=session if session is not None else httpx.Client(
                timeout=timeout, follow_redirects=False, trust_env=False),
        )

    @staticmethod
    def _options(options: dict) -> dict:
        allowed = {"country", "search_context_size", "max_tokens", "max_tokens_per_page",
                   "search_domain_filter", "search_language_filter", "search_after_date_filter",
                   "search_before_date_filter", "last_updated_after_filter",
                   "last_updated_before_filter", "search_recency_filter"}
        if not isinstance(options, dict) or set(options) - allowed:
            raise ProviderError("Unsupported Search API options")
        values = {key: value for key, value in options.items() if value is not None}
        for key, value in values.items():
            if key == "country":
                if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z]{2}", value):
                    raise ProviderError("country requires an ISO 3166-1 alpha-2 code")
                values[key] = value.upper()
            elif key in {"max_tokens", "max_tokens_per_page"}:
                if type(value) is not int or not 1 <= value <= 1_000_000:
                    raise ProviderError(key + " must be an integer from 1 to 1000000")
            elif key in {"search_domain_filter", "search_language_filter"}:
                if not isinstance(value, list) or not 1 <= len(value) <= 20 \
                        or any(not isinstance(item, str) or not item.strip() for item in value):
                    raise ProviderError(key + " requires 1–20 nonempty strings")
                if key == "search_language_filter" and any(
                    not re.fullmatch(r"[a-z]{2}", item) for item in value
                ):
                    raise ProviderError("Languages require ISO 639-1 two-letter codes")
                if key == "search_domain_filter" and (
                    any("://" in item or len(item) > 253 or any(c.isspace() for c in item)
                        for item in value) or len({item.startswith("-") for item in value}) > 1
                ):
                    raise ProviderError("Use domains/paths without protocols; "
                                        "do not mix allowlist and denylist")
            elif key.endswith("date_filter") or key.startswith("last_updated_"):
                try:
                    if not isinstance(value, str) or not re.fullmatch(r"\d{2}/\d{2}/\d{4}", value):
                        raise ValueError
                    datetime.strptime(value, "%m/%d/%Y")
                except ValueError:
                    raise ProviderError(key + " must be a valid MM/DD/YYYY date") from None
            elif key == "search_context_size" and value not in ("low", "medium", "high"):
                raise ProviderError("search_context_size must be low, medium or high")
            elif key == "search_recency_filter" and value not in (
                "hour", "day", "week", "month", "year"
            ):
                raise ProviderError("Invalid search_recency_filter")
        if "search_context_size" in values \
                and {"max_tokens", "max_tokens_per_page"} & values.keys():
            raise ProviderError("Use search_context_size OR explicit token limits")
        return values

    @staticmethod
    def _retry_after(headers) -> float:
        try:
            if headers.get("retry-after-ms") is not None:
                delay = float(headers["retry-after-ms"]) / 1000
            else:
                delay = float(headers.get("Retry-After", "1"))
        except (ValueError, TypeError):
            try:
                moment = parsedate_to_datetime(headers["Retry-After"])
                if moment.tzinfo is None:
                    moment = moment.replace(tzinfo=UTC)
                delay = (moment - datetime.now(UTC)).total_seconds()
            except (KeyError, ValueError, TypeError, OverflowError):
                delay = 1.0
        return max(0.0, delay) if math.isfinite(delay) else 1.0

    def _wait(self):
        while True:
            with self._cooldown_lock:
                remaining = self._not_before - time.monotonic()
            if remaining <= 0:
                return
            if remaining > self.timeout:
                raise ProviderError("Perplexity rate limit cooldown is still active",
                                    status_code=429, retry_after_seconds=remaining,
                                    billing_known_free=True)
            time.sleep(remaining)

    @property
    def last_status_code(self) -> int | None:
        return getattr(self._local, "status", None)

    @property
    def result_errors(self) -> list[dict]:
        return getattr(self._local, "result_errors", [])

    def close(self):
        self.client.close()

    def search(self, query: str | list[str], max_results: int = 10, **options) -> list[dict]:
        queries = [query] if isinstance(query, str) else query
        if not isinstance(queries, list) or not 1 <= len(queries) <= 5 or any(
            not isinstance(item, str) or not item.strip() for item in queries
        ):
            raise ProviderError("query must be a nonempty string or 1–5 nonempty queries")
        if type(max_results) is not int or not 1 <= max_results <= 20:
            raise ProviderError("Search max_results must be between 1 and 20")
        normalized = [item.strip() for item in queries]
        request = {"query": normalized[0] if isinstance(query, str) else normalized,
                   "max_results": max_results, **self._options({**self.options, **options})}
        self._wait()
        response = None
        try:
            response = self.client.search.with_raw_response.create(**request)
            self._local.status = response.status_code
            if response.status_code != 200:
                raise ProviderError(
                    "Perplexity Search returned an unexpected HTTP status",
                    paid_request_attempted=True, status_code=response.status_code,
                )
            # Decode the envelope before SDK model validation. A malformed item
            # must not discard valid neighbours in an already-paid response.
            payload = response.http_response.json()
            if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
                raise ProviderError("Perplexity Search response must contain a results array",
                                    paid_request_attempted=True)
            hits, seen = [], set()
            self._local.result_errors = []
            for index, item in enumerate(payload["results"]):
                if not isinstance(item, dict) or any(
                    not isinstance(item.get(key), str) for key in ("title", "url", "snippet")
                ):
                    self._local.result_errors.append({"index": index, "code": "invalid_fields"})
                    continue
                try:
                    parsed = urlsplit(item["url"])
                    host = parsed.hostname
                except ValueError:
                    self._local.result_errors.append({"index": index, "code": "invalid_url"})
                    continue
                if parsed.scheme not in {"http", "https"} or not host \
                        or parsed.username is not None or parsed.password is not None:
                    self._local.result_errors.append({"index": index, "code": "invalid_url"})
                    continue
                identity = canonicalize_url(item["url"])
                if identity in seen:
                    continue
                seen.add(identity)
                hit = {key: item[key] for key in ("title", "url", "snippet")}
                for key in ("date", "last_updated"):
                    if isinstance(item.get(key), str):
                        hit[key] = item[key]
                hits.append(hit)
            if payload["results"] and not hits:
                raise ProviderError("No valid Search results in response",
                                    paid_request_attempted=True)
            return hits
        except APIStatusError as error:
            status = error.status_code
            self._local.status = status
            retry_after = None
            if status == 429:
                retry_after = self._retry_after(error.response.headers)
                with self._cooldown_lock:
                    self._not_before = max(self._not_before, time.monotonic() + retry_after)
            message = ("Perplexity authentication failed (401); check the API key in your "
                       "terminal or https://console.perplexity.ai" if status == 401 else
                       f"Perplexity Search request failed (HTTP {status})")
            raise ProviderError(message, paid_request_attempted=True, status_code=status,
                                retry_after_seconds=retry_after, billing_known_free=True) from None
        except ProviderError:
            raise
        except (APIError, httpx.HTTPError, ValidationError, ValueError, TypeError, KeyError):
            raise ProviderError(
                "Perplexity Search could not return valid results", paid_request_attempted=True
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
        raise ProviderError("Vacancy URL must resolve only to public HTTP(S) addresses",
                            outcome="unsafe_url") from None


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
        self, timeout: float = 20, *, session: Any = None, max_bytes: int = _MAX_BYTES,
        respect_robots: bool | None = None, domain_policy: dict[str, str] | None = None,
    ) -> None:
        if not isinstance(timeout, (int, float)) or not 0 < timeout <= 120:
            raise ProviderError("Fetch timeout must be between 0 and 120 seconds")
        if not isinstance(max_bytes, int) or not 1024 <= max_bytes <= 10 * _MAX_BYTES:
            raise ProviderError("Invalid vacancy response size limit")
        self.timeout = timeout
        self.max_bytes = max_bytes
        self.respect_robots = session is None if respect_robots is None else respect_robots
        self.domain_policy = domain_policy or {}
        if any(value not in {"allow", "deny"} for value in self.domain_policy.values()):
            raise ProviderError("Domain policy requires allow or deny")
        self._robots = {}
        self._robots_lock = threading.RLock()
        self.http_attempts = 0
        self.session = session if session is not None else OriginBoundSession()
        if session is None:
            self.session.trust_env = False
            self.session.mount("http://", _PublicAddressAdapter(max_retries=0))
            self.session.mount("https://", _PublicAddressAdapter(max_retries=0))

    def _check_policy(self, url: str) -> None:
        parsed = urlsplit(url)
        if any((parsed.hostname == domain or parsed.hostname.endswith("." + domain))
               and rule == "deny" for domain, rule in self.domain_policy.items()):
            raise ProviderError("Domain retrieval is prohibited by local policy",
                                outcome="domain_policy_denied")
        if not self.respect_robots:
            return
        origin = f"{parsed.scheme}://{parsed.netloc}"
        with self._robots_lock:
            if origin not in self._robots:
                parser = RobotFileParser(origin + "/robots.txt")
                try:
                    text, _ = self._get(origin + "/robots.txt", check_policy=False)
                    parser.parse(text.splitlines())
                except ProviderError as error:
                    if error.status_code in {404, 410}:
                        parser.parse([])
                    else:
                        raise ProviderError("Robots policy is unavailable or denies access",
                                            outcome="robots_unavailable") from None
                self._robots[origin] = parser
            if not self._robots[origin].can_fetch("job-intake-discovery", url):
                raise ProviderError("Robots policy prohibits vacancy retrieval",
                                    outcome="robots_denied")

    def _get(self, url: str, *, check_policy: bool = True) -> tuple[str, str]:
        for _ in range(5):
            url = _public_url(url)
            if check_policy:
                self._check_policy(url)
            elif any((urlsplit(url).hostname == domain or
                      urlsplit(url).hostname.endswith("." + domain)) and rule == "deny"
                     for domain, rule in self.domain_policy.items()):
                raise ProviderError("Domain retrieval is prohibited by local policy",
                                    outcome="domain_policy_denied")
            response = None
            try:
                with self._robots_lock:
                    self.http_attempts += 1
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
                        "Vacancy source returned an HTTP error", status_code=response.status_code,
                        outcome=("access_denied" if response.status_code in {401, 403} else
                                 "not_found_unverified" if response.status_code in {404, 410}
                                 else "http_error"),
                    )
                mime = response.headers.get("Content-Type", "").split(";", 1)[0].lower()
                if mime and mime not in {
                    "text/html", "application/xhtml+xml", "application/json", "text/plain",
                }:
                    raise ProviderError("Vacancy source is not an HTML or JSON document",
                                        outcome="unsupported_content_type")
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
            except requests.Timeout:
                raise ProviderError("Vacancy retrieval timed out", outcome="timeout") from None
            except (requests.RequestException, UnicodeError, LookupError):
                raise ProviderError("Could not retrieve the vacancy original",
                                    outcome="network_error") from None
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
                company = payload.get("company_name")
                if not isinstance(company, str) or not company.strip():
                    company = next(iter(adapter.params.values()), "")
                    company_source = "ats_board_identity"
                else:
                    company_source = "ats_company_name"
                job.company = compact_text(str(company or ""))
                job.source_metadata["company_provenance"] = company_source
                job.original_url = _public_url(job.original_url)
                job.apply_url = _public_url(job.apply_url or job.original_url)
                if not job.title or len(job.description_clean) < 120:
                    raise ValueError
                job.status = JobStatus.CLOSED if _CLOSED.search(job.description_clean) \
                    or payload.get("isListed") is False or payload.get("active") is False \
                    else JobStatus.OPEN
                job.source_metadata["description_complete"] = True
                return job
            except ProviderError as error:
                if error.status_code in {401, 403} or error.outcome in {
                    "domain_policy_denied", "robots_denied", "robots_unavailable",
                }:
                    raise
                # Public original fallback is allowed for a stale API route,
                # never to circumvent access or domain restrictions.
            except (ValueError, TypeError, KeyError, AttributeError, StopIteration):
                # A stale API route or schema must not turn its JSON into a description.
                # A valid structured original may still be present on the public page.
                pass
        html, final_url = self._get(url)
        soup = BeautifulSoup(html, "html.parser")
        if _CLOSED.search(soup.get_text(" ", strip=True)):
            heading = soup.select_one("h1")
            return JobRecord(
                source="discovery", company="", title=compact_text(
                    heading.get_text(" ", strip=True) if heading else "Closed vacancy"),
                original_url=final_url, status=JobStatus.CLOSED,
                description_raw=html, description_clean=html_text(html),
                source_metadata={"description_complete": False,
                                 "closed_evidence": "explicit_page_statement"},
            )
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
        job.source_metadata["company_provenance"] = "json_ld_hiring_organization" \
            if job.company else "missing"
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
            raise ProviderError("Page is not an identifiable vacancy detail", outcome="non_vacancy")
        container = soup.select_one(
            '[itemprop="description"], [data-testid="job-description"], '
            '.job-description, #job-description, main, article'
        )
        if container is None:
            raise ProviderError("Vacancy page has no full description container",
                                outcome="missing_text")
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
            raise ProviderError("Page lacks sufficient full vacancy detail signals",
                                outcome="missing_text")
        organization = soup.select_one('[itemprop="hiringOrganization"]')
        company = compact_text(organization.get_text(" ", strip=True)) if organization else ""
        return JobRecord(
            source="discovery", company=company, title=title, original_url=url, apply_url=url,
            description_raw=str(container), description_clean=description,
            status=JobStatus.CLOSED if _CLOSED.search(description) else JobStatus.UNKNOWN,
            source_metadata={"description_complete": True, "description_source": "html_detail",
                             "company_provenance": "html_hiring_organization" if company
                             else "missing"},
        )
