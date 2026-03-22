from __future__ import annotations

import hashlib
import re
from urllib.parse import parse_qsl, urlparse, urlunparse


WHITESPACE_RE = re.compile(r"\s+")
PUNCT_STRIP_RE = re.compile(r"[^a-z0-9\s:/+-]")


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    lowered = value.casefold()
    lowered = PUNCT_STRIP_RE.sub(" ", lowered)
    return WHITESPACE_RE.sub(" ", lowered).strip()


def compact_text(value: str | None) -> str:
    if not value:
        return ""
    return WHITESPACE_RE.sub(" ", value).strip()


def canonicalize_url(url: str | None) -> str:
    if not url:
        return ""
    parsed = urlparse(url.strip())
    query = [(k, v) for k, v in parse_qsl(parsed.query) if not k.startswith("utm_")]
    return urlunparse(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            parsed.path.rstrip("/"),
            "",
            "&".join(f"{k}={v}" for k, v in query),
            "",
        )
    )


def stable_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def contains_any(text: str, phrases: list[str]) -> list[str]:
    normalized = normalize_text(text)
    return [phrase for phrase in phrases if normalize_text(phrase) in normalized]
