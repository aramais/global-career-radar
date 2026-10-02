"""Private session cookies and bounded vacancy reads for DailyRemote only."""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

COOKIE_NAME = re.compile(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+")
COOKIE_VALUE = re.compile(r"[\x21\x23-\x2b\x2d-\x3a\x3c-\x5b\x5d-\x7e]*")
JOB_PATH = re.compile(r"/remote-job/[A-Za-z0-9_\-\u0080-\U0010ffff]+/?")
MAX_COOKIE_FILE_CHARS = 65536


def load_dailyremote_cookies(session, path: str | Path) -> None:
    """Read a browser's Cookie request value without exposing it in errors/logs."""
    try:
        with Path(path).expanduser().open(encoding="utf-8-sig") as handle:
            text = handle.read(MAX_COOKIE_FILE_CHARS + 1)
    except (OSError, UnicodeError):
        raise ValueError("Cannot read DailyRemote cookies file") from None
    if len(text) > MAX_COOKIE_FILE_CHARS:
        raise ValueError("DailyRemote cookies file is too large")
    text = text.strip()
    if text.lower().startswith("cookie:"):
        text = text[len("cookie:") :].strip()
    if not text or any(ord(char) < 32 or ord(char) == 127 for char in text):
        raise ValueError("DailyRemote cookies file must contain one Cookie header value")

    # Validate the entire file before mutating the jar. Values may contain '='.
    cookies: dict[str, str] = {}
    for pair in text.split(";"):
        name, separator, value = pair.strip().partition("=")
        unquoted = value[1:-1] if value.startswith('"') and value.endswith('"') else value
        if (
            not separator
            or not COOKIE_NAME.fullmatch(name)
            or not COOKIE_VALUE.fullmatch(unquoted)
            or name in cookies
        ):
            raise ValueError("Invalid or duplicate cookie in DailyRemote cookies file")
        cookies[name] = value
    for name, value in cookies.items():
        session.cookies.set(name, value, domain="dailyremote.com", path="/", secure=True)


def _validate_dailyremote_url(url: str) -> None:
    try:
        parsed = urlparse(url)
        decoded_path = unquote(parsed.path, errors="strict")
        valid = (
            not any(ord(char) < 32 or ord(char) == 127 for char in url)
            and not parsed.params
            # Decode Unicode slugs once, without accepting encoded separators,
            # nested escapes, whitespace, or Unicode control/surrogate characters.
            and decoded_path.count("/") == parsed.path.count("/")
            and "%" not in decoded_path
            and not any(
                char.isspace() or unicodedata.category(char) in {"Cc", "Cs"}
                for char in decoded_path
            )
            and parsed.scheme == "https"
            and parsed.netloc.casefold() in {"dailyremote.com", "dailyremote.com:443"}
            and parsed.username is None
            and parsed.password is None
            and (
                decoded_path in {"", "/", "/remote-jobs", "/remote-jobs/"}
                or JOB_PATH.fullmatch(decoded_path)
            )
        )
    except (ValueError, TypeError, AttributeError):
        valid = False
    if not valid:
        raise ValueError("Authenticated DailyRemote requests require an HTTPS vacancy URL")


def fetch_dailyremote_text(session, url: str) -> str:
    """Only GET listings/vacancies; validate every redirect before sending cookies."""
    for _ in range(4):
        _validate_dailyremote_url(url)
        try:
            response = session.get(url, timeout=20, allow_redirects=False)
        except Exception as exc:
            # Requests errors can contain headers; never propagate their contents.
            raise ValueError(f"DailyRemote request failed ({type(exc).__name__})") from None
        try:
            if response.status_code in {301, 302, 303, 307, 308}:
                redirect = response.headers.get("Location")
                if not redirect:
                    raise ValueError("DailyRemote redirect has no Location header")
                url = urljoin(url, redirect)
                continue
            if response.status_code >= 400:
                raise ValueError(f"DailyRemote HTTP {response.status_code}")
            return response.text
        finally:
            response.close()
    raise ValueError("Too many DailyRemote redirects")
