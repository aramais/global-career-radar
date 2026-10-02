"""Small boundary validators; text remains plain text and UI must escape it."""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, date, datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

STAGES = (
    "saved",
    "contacted",
    "applied",
    "hr_screen",
    "team_interview",
    "assessment",
    "offer",
    "rejected",
    "withdrawn",
    "archived",
)
CHANNELS = ("unknown", "cold", "referral", "recruiter")
REFERRAL_STATES = ("none", "to_find", "requested", "referred")
CONTACT_RELATIONSHIPS = ("referral", "recruiter", "hiring_manager", "other")
TERMINAL_STAGES = ("rejected", "withdrawn", "archived")


class CRMValidationError(ValueError):
    """An input failed CRM boundary validation."""


class CRMConflictError(ValueError):
    """Another write changed the application after the displayed version."""


def text_value(value: object, field: str, *, required: bool = False, maximum: int = 20000) -> str:
    if not isinstance(value, str):
        raise CRMValidationError(f"{field} must be text")
    cleaned = value.strip()
    if required and not cleaned:
        raise CRMValidationError(f"{field} is required")
    if len(cleaned) > maximum or "\x00" in cleaned:
        raise CRMValidationError(f"{field} is too long or contains a null character")
    return cleaned


def enum_value(value: object, field: str, choices: tuple[str, ...]) -> str:
    if not isinstance(value, str) or value not in choices:
        raise CRMValidationError(f"{field} must be one of: {', '.join(choices)}")
    return value


def safe_url(value: object, field: str = "url") -> str | None:
    if value is None or value == "":
        return None
    result = text_value(value, field, maximum=4000)
    if not result:
        return None
    try:
        parts = urlsplit(result)
        port = parts.port
    except ValueError as exc:
        raise CRMValidationError(f"{field} is not a valid URL") from exc
    if (
        parts.scheme.lower() not in {"http", "https"}
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or any(c.isspace() or ord(c) < 32 for c in result)
        or (port is not None and not 1 <= port <= 65535)
    ):
        raise CRMValidationError(f"{field} must be a public HTTP(S) URL without credentials")
    return result


def canonical_url_key(value: str | None) -> str | None:
    if not value:
        return None
    parts = urlsplit(value)
    query = [
        (key, item)
        for key, item in parse_qsl(parts.query, keep_blank_values=True)
        if not key.lower().startswith("utm_")
    ]
    canonical = urlunsplit(
        (
            parts.scheme.lower(),
            parts.netloc.lower(),
            parts.path.rstrip("/"),
            urlencode(sorted(query)),
            "",
        )
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def email_value(value: object) -> str | None:
    if value is None or value == "":
        return None
    result = text_value(value, "email", maximum=320)
    if not re.fullmatch(r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+", result):
        raise CRMValidationError("email is not a valid email address")
    return result


def datetime_value(value: object, field: str) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise CRMValidationError(f"{field} must be an ISO date/time") from exc
    if not isinstance(value, datetime):
        raise CRMValidationError(f"{field} must be an ISO date/time")
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def date_value(value: object, field: str) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, str):
        try:
            value = date.fromisoformat(value)
        except ValueError as exc:
            raise CRMValidationError(f"{field} must be an ISO date") from exc
    if isinstance(value, datetime) or not isinstance(value, date):
        raise CRMValidationError(f"{field} must be an ISO date")
    return value
