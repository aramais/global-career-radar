from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlsplit

import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry

LOGGER = logging.getLogger(__name__)

_PUBLIC_HEADERS = {
    "accept", "accept-encoding", "accept-language", "cache-control", "connection",
    "content-length", "content-type", "user-agent",
}


def _origin(url: str) -> tuple[str, str | None, int | None]:
    parsed = urlsplit(url)
    return parsed.scheme.lower(), parsed.hostname, parsed.port or {
        "http": 80, "https": 443,
    }.get(parsed.scheme.lower())


class OriginBoundSession(requests.Session):
    """Keep configured credentials on the origin of the initial source request."""

    def __init__(self, configured_headers: dict[str, str] | None = None) -> None:
        super().__init__()
        self._credential_origin: tuple[str, str | None, int | None] | None = None
        self._configured_headers = {name.lower() for name in configured_headers or {}}

    def _strip_private_headers(self, request: requests.PreparedRequest) -> None:
        # A secret can use any custom header name; a denylist cannot cover it.
        for name in list(request.headers):
            if name.lower() not in _PUBLIC_HEADERS or name.lower() in self._configured_headers:
                del request.headers[name]

    def prepare_request(self, request: requests.Request) -> requests.PreparedRequest:
        self._configured_headers.update(name.lower() for name in request.headers or {})
        prepared = super().prepare_request(request)
        origin = _origin(prepared.url)
        if self._credential_origin is None:
            self._credential_origin = origin
        elif origin != self._credential_origin:
            self._strip_private_headers(prepared)
        return prepared

    def rebuild_auth(self, prepared_request, response) -> None:
        super().rebuild_auth(prepared_request, response)
        # Requests calls this before sending a redirected request, after merging
        # cookies/auth. The jar can add cookies again on every hop, so always
        # compare the target to the credential origin, including scheme/port.
        if self._credential_origin is None:
            self._credential_origin = _origin(response.request.url)
        if _origin(prepared_request.url) != self._credential_origin:
            self._strip_private_headers(prepared_request)


def build_session(headers: dict[str, str] | None = None) -> requests.Session:
    session = OriginBoundSession(headers)
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=0.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update(
        {
            "User-Agent": (
                "job-intake-mvp/0.1 (+https://example.local; deterministic remote-job intake)"
            )
        }
    )
    if headers:
        session.headers.update(headers)
    return session


def fetch_text(session: requests.Session, url: str, timeout: int = 20) -> str:
    LOGGER.info("fetch_url", extra={"url": url})
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    return response.text


def fetch_json(session: requests.Session, url: str, timeout: int = 20) -> dict[str, Any]:
    LOGGER.info("fetch_json", extra={"url": url})
    response = session.get(url, timeout=timeout)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object from {url}")
    return payload
