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


def matches_phrase(normalized_text: str, phrase: str) -> bool:
    """Whole-token match of ``phrase`` inside already-normalized text.

    Uses non-alphanumeric boundaries instead of a bare substring test, so ``ml`` no
    longer matches inside ``html`` and ``pricing`` no longer matches ``repricing``.
    Punctuation inside multi-token phrases (``a/b testing``, ``booking.com``) is
    handled because ``normalize_text`` is applied to the phrase too.
    """
    norm_phrase = normalize_text(phrase)
    if not norm_phrase:
        return False
    pattern = r"(?<![a-z0-9])" + re.escape(norm_phrase) + r"(?![a-z0-9])"
    return re.search(pattern, normalized_text) is not None


def contains_any(text: str, phrases: list[str]) -> list[str]:
    normalized = normalize_text(text)
    return [phrase for phrase in phrases if matches_phrase(normalized, phrase)]
