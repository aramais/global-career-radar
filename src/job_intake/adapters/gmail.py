"""Import selected Gmail message snapshots without opening a mailbox or using a token.

The connector is run explicitly outside the ordinary CLI. Only the supplied local
snapshot is read here; MIME bodies are parsed as vacancy excerpts, never executed.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import stat
from datetime import UTC, datetime
from email import policy
from email.message import EmailMessage
from email.utils import format_datetime, parsedate_to_datetime
from pathlib import Path
from typing import Any

from job_intake.adapters._email_parsing import (
    MAX_BODY_BYTES,
    MAX_EMAIL_BYTES,
    MAX_MIME_DEPTH,
    MAX_MIME_PARTS,
    parse_email_jobs,
)
from job_intake.adapters.base import JobSourceAdapter
from job_intake.models.job import JobRecord
from job_intake.utils.text import canonicalize_url

MAX_MESSAGES = 1000
MAX_SNAPSHOT_BYTES = 32 * 1024 * 1024
MAX_HEADER_CHARS = 8192
_BASE64URL = re.compile(r"[A-Za-z0-9_-]*={0,2}\Z")
_CHARSET = re.compile(r"[A-Za-z0-9._-]{1,80}\Z")


def _header(part: dict[str, Any], name: str) -> str | None:
    headers = part.get("headers") or []
    if not isinstance(headers, list):
        raise ValueError("Gmail message headers have an unsupported format")
    if len(headers) > MAX_MIME_PARTS * 2:
        raise ValueError("Gmail message exceeds the supported header limit")
    for header in headers:
        if not isinstance(header, dict) or not isinstance(header.get("name"), str):
            continue
        if header["name"].casefold() != name.casefold():
            continue
        value = header.get("value")
        if not isinstance(value, str) or len(value) > MAX_HEADER_CHARS:
            raise ValueError("Gmail message header has an unsupported format")
        if "\r" in value or "\n" in value or "\x00" in value:
            raise ValueError("Gmail message header has an unsupported format")
        return value
    return None


def _date(message: dict[str, Any], payload: dict[str, Any]) -> str | None:
    value = _header(payload, "Date") or message.get("date")
    if isinstance(value, str) and len(value) <= MAX_HEADER_CHARS:
        try:
            parsed = parsedate_to_datetime(value)
            if parsed is not None:
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=UTC)
                return format_datetime(parsed)
        except (ValueError, TypeError, OverflowError):
            pass

    value = message.get("internal_date", message.get("internalDate"))
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    try:
        if isinstance(value, int) or re.fullmatch(r"\d{1,16}", value):
            parsed = datetime.fromtimestamp(int(value) / 1000, tz=UTC)
        else:
            if len(value) > 100:
                return None
            parsed = datetime.fromisoformat(value)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
        return format_datetime(parsed)
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def _charset(part: dict[str, Any]) -> str:
    value = _header(part, "Content-Type")
    if not value:
        return "utf-8"
    header = EmailMessage(policy=policy.default)
    header["Content-Type"] = value
    charset = header.get_content_charset()
    return charset if charset and _CHARSET.fullmatch(charset) else "utf-8"


def _body(part: dict[str, Any]) -> tuple[bytes, str] | None:
    body = part.get("body") or {}
    if not isinstance(body, dict):
        raise ValueError("Gmail message body has an unsupported format")
    # The connector may return already decoded Unicode. Its original charset must
    # not be applied a second time when reconstructing the message.
    content = body.get("content")
    if content is not None:
        if not isinstance(content, str) or len(content) > MAX_BODY_BYTES:
            raise ValueError("Gmail message exceeds the supported body size limit")
        try:
            raw = content.encode("utf-8")
        except UnicodeError:
            raise ValueError("Gmail message body has an unsupported format") from None
        if len(raw) > MAX_BODY_BYTES:
            raise ValueError("Gmail message exceeds the supported body size limit")
        return raw, "utf-8"

    data = body.get("data") or body.get("base64_url_content")
    if data is None or data == "":
        return None
    if not isinstance(data, str) or len(data) > (MAX_BODY_BYTES + 2) // 3 * 4:
        raise ValueError("Gmail message exceeds the supported body size limit")
    if not _BASE64URL.fullmatch(data) or len(data) % 4 == 1:
        raise ValueError("Gmail message body encoding is invalid")
    try:
        raw = base64.b64decode(data + "=" * (-len(data) % 4), altchars=b"-_", validate=True)
    except (ValueError, binascii.Error):
        raise ValueError("Gmail message body encoding is invalid") from None
    if len(raw) > MAX_BODY_BYTES:
        raise ValueError("Gmail message exceeds the supported body size limit")
    return raw, _charset(part)


def _mime_bytes(message: dict[str, Any]) -> bytes:
    payload = message.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("Gmail message payload has an unsupported format")
    envelope = EmailMessage(policy=policy.default)
    date = _date(message, payload)
    if date:
        envelope["Date"] = date
    envelope.make_mixed()
    pending = [(payload, 0)]
    count = 0
    body_size = 0
    while pending:
        part, depth = pending.pop()
        count += 1
        if count > MAX_MIME_PARTS or depth > MAX_MIME_DEPTH:
            raise ValueError("Gmail message exceeds the supported MIME complexity limit")
        if not isinstance(part, dict):
            raise ValueError("Gmail message MIME part has an unsupported format")
        if part.get("filename"):
            continue
        content_type = _header(part, "Content-Type")
        if content_type:
            header = EmailMessage(policy=policy.default)
            header["Content-Type"] = content_type
            if header.get_filename():
                continue
        disposition = _header(part, "Content-Disposition")
        if disposition:
            header = EmailMessage(policy=policy.default)
            header["Content-Disposition"] = disposition
            if header.get_content_disposition() == "attachment" or header.get_filename():
                continue
        mime_type = part.get("mime_type", part.get("mimeType", ""))
        if not isinstance(mime_type, str):
            raise ValueError("Gmail message MIME type has an unsupported format")
        mime_type = mime_type.casefold()
        if mime_type == "message/rfc822":
            continue
        if mime_type.startswith("multipart/"):
            children = part.get("parts") or []
            if not isinstance(children, list):
                raise ValueError("Gmail message MIME children have an unsupported format")
            if count + len(pending) + len(children) > MAX_MIME_PARTS:
                raise ValueError("Gmail message exceeds the supported MIME complexity limit")
            pending.extend((child, depth + 1) for child in reversed(children))
            continue
        if mime_type not in {"text/plain", "text/html"}:
            continue
        decoded = _body(part)
        if decoded is None:
            continue
        raw, charset = decoded
        body_size += len(raw)
        if body_size > MAX_BODY_BYTES:
            raise ValueError("Gmail message exceeds the supported body size limit")
        leaf = EmailMessage(policy=policy.default)
        leaf.set_type(mime_type)
        leaf.set_param("charset", charset)
        leaf["Content-Transfer-Encoding"] = "base64"
        leaf.set_payload(base64.encodebytes(raw).decode("ascii"))
        envelope.attach(leaf)
    raw = envelope.as_bytes()
    if len(raw) > MAX_EMAIL_BYTES:
        raise ValueError("Gmail message exceeds the supported size limit")
    return raw


def parse_gmail_message(
    message: dict[str, Any],
    source: str = "email-vacancies",
    *,
    keywords: list[str] | None = None,
) -> list[JobRecord]:
    """Parse one connector or REST message; reject malformed input without its contents."""
    if not isinstance(message, dict):
        raise ValueError("Gmail message has an unsupported format")
    message_id = message.get("id")
    if (
        not isinstance(message_id, str)
        or not message_id
        or len(message_id) > 1024
        or any(ord(char) < 32 for char in message_id)
    ):
        raise ValueError("Gmail message identifier has an unsupported format")
    try:
        message_ref = hashlib.sha256(message_id.encode("utf-8")).hexdigest()
        raw = _mime_bytes(message)
        return parse_email_jobs(raw, source, message_ref, keywords=keywords)
    except (ValueError, TypeError, LookupError, RecursionError, UnicodeError):
        raise ValueError("Gmail message could not be parsed within the supported limits") from None


def _deduplicated(jobs: list[JobRecord]) -> list[JobRecord]:
    result: list[JobRecord] = []
    seen: set[str] = set()
    for job in jobs:
        url = canonicalize_url(job.apply_url or job.original_url)
        if url in seen:
            continue
        seen.add(url)
        result.append(job)
    return result


def parse_gmail_messages(
    messages: list[dict[str, Any]], source: str = "email-vacancies"
) -> list[JobRecord]:
    """Parse a bounded collection. Malformed messages raise; the adapter isolates them."""
    if not isinstance(messages, list) or len(messages) > MAX_MESSAGES:
        raise ValueError("Gmail messages must be a list with at most 1000 entries")
    return _deduplicated(
        [job for message in messages for job in parse_gmail_message(message, source)]
    )


class GmailSnapshotAdapter(JobSourceAdapter):
    """Read only the supplied selected-message snapshot, without Gmail credentials."""

    def fetch_jobs(self) -> list[JobRecord]:
        self.errors.clear()
        path, limit, keywords = self._validated_params()
        messages = self._read_snapshot(path)
        if len(messages) > limit:
            self.errors.append("Gmail message limit reached; additional messages were skipped")
        jobs: list[JobRecord] = []
        for position, message in enumerate(messages[:limit], start=1):
            try:
                jobs.extend(parse_gmail_message(message, self.name, keywords=keywords))
            except Exception:
                self.errors.append(f"Gmail message {position}: unable to parse message")
        return _deduplicated(jobs)

    def _validated_params(self) -> tuple[Path, int, list[str] | None]:
        raw_path = self.params.get("snapshot_file")
        if not isinstance(raw_path, (str, Path)) or not str(raw_path).strip():
            raise ValueError("Gmail snapshot_file is required")
        raw_limit = self.params.get("max_messages", 100)
        if isinstance(raw_limit, bool) or not isinstance(raw_limit, (int, str)):
            raise ValueError("Gmail max_messages must be between 1 and 1000")
        try:
            limit = int(raw_limit)
        except ValueError:
            raise ValueError("Gmail max_messages must be between 1 and 1000") from None
        if not 1 <= limit <= MAX_MESSAGES:
            raise ValueError("Gmail max_messages must be between 1 and 1000")
        keywords = self.params.get("keywords")
        if keywords is not None and (
            not isinstance(keywords, list)
            or any(not isinstance(word, str) or not word.strip() for word in keywords)
        ):
            raise ValueError("Gmail keywords must be a list of nonempty strings")
        return Path(raw_path), limit, keywords

    @staticmethod
    def _read_snapshot(path: Path) -> list[dict[str, Any]]:
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(descriptor, "rb") as handle:
                if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                    raise ValueError("Gmail snapshot must be a regular file")
                raw = handle.read(MAX_SNAPSHOT_BYTES + 1)
            if len(raw) > MAX_SNAPSHOT_BYTES:
                raise ValueError("Gmail snapshot exceeds the supported size limit")
            snapshot = json.loads(raw)
        except (OSError, ValueError, UnicodeError, RecursionError):
            raise ValueError(
                "Gmail snapshot could not be read within the supported limits"
            ) from None
        if not isinstance(snapshot, dict) or not isinstance(snapshot.get("messages"), list):
            raise ValueError("Gmail snapshot must contain a messages list")
        if len(snapshot["messages"]) > MAX_MESSAGES:
            raise ValueError("Gmail snapshot must contain at most 1000 messages")
        return snapshot["messages"]
