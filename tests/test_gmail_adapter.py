from __future__ import annotations

import base64
import json
from copy import deepcopy
from email.message import EmailMessage

import pytest

from job_intake.adapters._email_parsing import parse_email_jobs
from job_intake.adapters.gmail import (
    MAX_BODY_BYTES,
    MAX_MESSAGES,
    MAX_MIME_DEPTH,
    MAX_MIME_PARTS,
    GmailSnapshotAdapter,
    parse_gmail_message,
    parse_gmail_messages,
)

HTML = """
<article><h2><a href="https://dailyremote.com/remote-job/product-manager-42?utm_source=mail">
Product Manager at Example</a></h2><p>Remote from Brazil. English team.</p></article>
<article><h2><a href="https://jobs.lever.co/example/data-analytics-43">
Data Science Manager at Other</a></h2><p>Lead a team of analysts.</p></article>
<footer><a href="https://example.com/unsubscribe/secret">Unsubscribe</a></footer>
"""


def message(body: str = HTML, *, connector: bool = False, message_id: str = "gmail-1") -> dict:
    body_value = (
        {"content": body, "base64_url_content": None}
        if connector
        else {"data": base64.urlsafe_b64encode(body.encode()).decode().rstrip("=")}
    )
    return {
        "id": message_id,
        "internal_date": "1577836800000",
        "payload": {
            "mime_type": "text/html",
            "headers": [
                {"name": "Date", "value": "Fri, 2 Oct 2026 14:00:00 +0000"},
                {"name": "Content-Type", "value": "text/html; charset=utf-8"},
                {"name": "From", "value": "private-sender@example.com"},
                {"name": "To", "value": "private-recipient@example.com"},
                {"name": "Subject", "value": "Private mailbox subject"},
            ],
            "body": body_value,
        },
    }


def snapshot(tmp_path, messages: list) -> GmailSnapshotAdapter:
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps({"messages": messages}), encoding="utf-8")
    return GmailSnapshotAdapter("email-vacancies", {"snapshot_file": path})


@pytest.mark.parametrize("connector", [False, True])
def test_extracts_job_cards_without_retaining_mailbox_headers(connector):
    jobs = parse_gmail_messages([message(connector=connector)])

    assert [job.title for job in jobs] == ["Product Manager", "Data Science Manager"]
    assert [job.company for job in jobs] == ["Example", "Other"]
    assert jobs[0].original_url == "https://dailyremote.com/remote-job/product-manager-42"
    assert "Remote from Brazil" in jobs[0].description_clean
    for job in jobs:
        assert job.source == "email-vacancies"
        assert job.posted_at is None
        assert job.status == "unknown"
        assert job.source_metadata["email_date"] == "2026-10-02T14:00:00+00:00"
        assert job.source_metadata["description_complete"] is False
        assert len(job.source_metadata["message_ref"]) == 64
        assert job.source_metadata["message_ref"] != "gmail-1"
        serialized = json.dumps(job.source_metadata) + job.description_raw
        assert "private-sender" not in serialized
        assert "private-recipient" not in serialized
        assert "Private mailbox subject" not in serialized
        assert "Unsubscribe" not in serialized


def test_multipart_connector_prefers_html_and_deduplicates_job_urls():
    original = message(connector=True)
    html_part = original["payload"]
    original["payload"] = {
        "mime_type": "multipart/alternative",
        "headers": html_part["headers"],
        "body": {"content": None},
        "parts": [
            {
                "mime_type": "text/plain",
                "headers": [],
                "parts": None,
                "body": {
                    "content": "Product Manager at Example\n"
                    "https://dailyremote.com/remote-job/product-manager-42"
                },
            },
            html_part,
        ],
    }

    jobs = parse_gmail_messages([original, message(message_id="gmail-2")])

    assert len(jobs) == 2
    assert "Remote from Brazil" in jobs[0].description_clean


def test_standard_rest_field_names_and_plain_text_are_supported():
    original = message(
        "Product Manager at Example\nhttps://example.com/jobs/product-manager-42\nRemote role"
    )
    original["internalDate"] = original.pop("internal_date")
    payload = original["payload"]
    payload["mimeType"] = "text/plain"
    del payload["mime_type"]
    payload["headers"] = []

    jobs = parse_gmail_message(original)

    assert len(jobs) == 1
    assert jobs[0].source_metadata["email_date"] == "2020-01-01T00:00:00+00:00"
    assert "Remote role" in jobs[0].description_clean


def test_encoded_bytes_use_original_charset_but_decoded_connector_text_uses_utf8():
    text = '<a href="https://example.com/jobs/42">Product Manager at Café</a>'
    encoded = message()
    encoded["payload"]["headers"] = [
        {"name": "Content-Type", "value": "text/html; charset=windows-1252"}
    ]
    encoded["payload"]["body"] = {
        "base64_url_content": base64.urlsafe_b64encode(text.encode("cp1252")).decode()
    }
    decoded = deepcopy(encoded)
    decoded["payload"]["body"] = {"content": text, "base64_url_content": None}

    assert parse_gmail_message(encoded)[0].company == "Café"
    assert parse_gmail_message(decoded)[0].company == "Café"


@pytest.mark.parametrize("internal_date", ["2026-10-02T16:30:00Z", "1790958600000"])
def test_internal_date_fallback_does_not_change_posted_at(internal_date):
    original = message()
    original["payload"]["headers"] = [{"name": "Date", "value": "invalid date"}]
    original["internal_date"] = internal_date

    job = parse_gmail_message(original)[0]

    assert job.posted_at is None
    assert job.source_metadata["email_date"] == "2026-10-02T16:30:00+00:00"


def test_invalid_dates_are_ignored():
    original = message()
    original["payload"]["headers"] = []
    original["internal_date"] = "not a timestamp"

    assert "email_date" not in parse_gmail_message(original)[0].source_metadata


def test_gmail_and_eml_produce_the_same_source_identifier():
    raw = EmailMessage()
    raw.set_content(HTML, subtype="html")

    eml = parse_email_jobs(raw.as_bytes(), "email-vacancies", "local-message")
    gmail = parse_gmail_messages([message()])

    assert [job.source_job_id for job in gmail] == [job.source_job_id for job in eml]


@pytest.mark.parametrize(
    "extra",
    [
        {"filename": "attached.html", "mime_type": "text/html"},
        {
            "headers": [{"name": "Content-Disposition", "value": "attachment"}],
            "mime_type": "text/html",
        },
        {
            "headers": [{"name": "Content-Disposition", "value": 'inline; filename="job.html"'}],
            "mime_type": "text/html",
        },
        {
            "headers": [{"name": "Content-Type", "value": 'text/html; name="job.html"'}],
            "mime_type": "text/html",
        },
        {"mime_type": "image/png"},
        {"mime_type": "message/rfc822"},
    ],
)
def test_attachments_and_images_are_not_decoded_or_used_as_jobs(extra):
    original = message()
    ignored = {"body": {"data": "invalid SECRET PRIVATE encoding"}, **extra}
    original["payload"] = {
        "mime_type": "multipart/mixed",
        "parts": [original["payload"], ignored],
    }

    assert len(parse_gmail_message(original)) == 2


def test_adapter_keeps_successful_messages_and_sanitizes_individual_failure(tmp_path):
    broken = message(message_id="private-message-id")
    broken["payload"]["body"] = {"data": "PRIVATE SECRET INVALID BODY"}
    adapter = snapshot(tmp_path, [message(), broken, message(message_id="gmail-3")])

    jobs = adapter.fetch_jobs()

    assert len(jobs) == 2
    assert adapter.errors == ["Gmail message 2: unable to parse message"]
    assert len(adapter.fetch_jobs()) == 2
    assert len(adapter.errors) == 1


def test_adapter_limits_messages_and_filters_titles(tmp_path):
    adapter = snapshot(tmp_path, [message(), message(message_id="gmail-2")])
    adapter.params.update(max_messages=1, keywords=["data science"])

    jobs = adapter.fetch_jobs()

    assert [job.title for job in jobs] == ["Data Science Manager"]
    assert adapter.errors == ["Gmail message limit reached; additional messages were skipped"]


@pytest.mark.parametrize("payload", [None, {}, {"mime_type": "multipart/mixed", "parts": "secret"}])
def test_malformed_mime_structure_raises_without_echoing_input(payload):
    original = message()
    original["payload"] = payload

    if payload == {}:
        assert parse_gmail_message(original) == []
    else:
        with pytest.raises(ValueError, match="Gmail message could not be parsed") as error:
            parse_gmail_message(original)
        assert "secret" not in str(error.value)


def test_body_byte_limit_applies_to_sum_of_text_parts():
    original = message()
    original["payload"] = {
        "mime_type": "multipart/alternative",
        "parts": [
            {"mime_type": "text/html", "body": {"content": "x" * (MAX_BODY_BYTES // 2 + 1)}},
            {"mime_type": "text/plain", "body": {"content": "x" * (MAX_BODY_BYTES // 2 + 1)}},
        ],
    }

    with pytest.raises(ValueError, match="supported limits"):
        parse_gmail_message(original)


@pytest.mark.parametrize("kind", ["parts", "depth"])
def test_mime_complexity_is_bounded(kind):
    original = message()
    if kind == "parts":
        original["payload"] = {
            "mime_type": "multipart/mixed",
            "parts": [{"mime_type": "image/png"} for _ in range(MAX_MIME_PARTS)],
        }
    else:
        for _ in range(MAX_MIME_DEPTH + 1):
            original["payload"] = {"mime_type": "multipart/mixed", "parts": [original["payload"]]}

    with pytest.raises(ValueError, match="supported limits"):
        parse_gmail_message(original)


@pytest.mark.parametrize("params", [{}, {"snapshot_file": ""}, {"snapshot_file": False}])
def test_snapshot_path_is_required(params):
    with pytest.raises(ValueError, match="snapshot_file is required"):
        GmailSnapshotAdapter("email-vacancies", params).fetch_jobs()


@pytest.mark.parametrize("limit", [True, 0, -1, 1001, "invalid", 1.2])
def test_invalid_message_limits_are_rejected(tmp_path, limit):
    adapter = snapshot(tmp_path, [])
    adapter.params["max_messages"] = limit

    with pytest.raises(ValueError, match="max_messages must be between"):
        adapter.fetch_jobs()


def test_snapshot_cannot_follow_symlink(tmp_path):
    real = tmp_path / "private.json"
    real.write_text('{"messages": []}', encoding="utf-8")
    linked = tmp_path / "linked.json"
    linked.symlink_to(real)

    with pytest.raises(ValueError, match="snapshot could not be read"):
        GmailSnapshotAdapter("email-vacancies", {"snapshot_file": linked}).fetch_jobs()


@pytest.mark.parametrize("content", ["PRIVATE INVALID JSON", "[]", '{"messages": {}}'])
def test_invalid_snapshot_errors_do_not_echo_file_contents(tmp_path, content):
    path = tmp_path / "snapshot.json"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(ValueError) as error:
        GmailSnapshotAdapter("email-vacancies", {"snapshot_file": path}).fetch_jobs()
    assert "PRIVATE" not in str(error.value)


def test_message_collection_cannot_exceed_hard_limit(tmp_path):
    messages = [{}] * (MAX_MESSAGES + 1)
    with pytest.raises(ValueError, match="at most 1000"):
        parse_gmail_messages(messages)
    with pytest.raises(ValueError, match="at most 1000"):
        snapshot(tmp_path, messages).fetch_jobs()
