from __future__ import annotations

import logging
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util import Retry


LOGGER = logging.getLogger(__name__)


def build_session(headers: dict[str, str] | None = None) -> requests.Session:
    session = requests.Session()
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
