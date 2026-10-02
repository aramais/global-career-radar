"""Cookie-file authentication stays within DailyRemote's vacancy-reading surface."""

from dataclasses import dataclass, field
from urllib.parse import quote

import pytest
import requests

from job_intake.adapters import dailyremote
from job_intake.adapters._dailyremote_auth import (
    fetch_dailyremote_text,
    load_dailyremote_cookies,
)

LISTING_URL = "https://dailyremote.com/?search=analytics"
DETAIL_URL = "https://dailyremote.com/remote-job/head-analytics-123"
UNICODE_DETAIL_PATH = (
    "/remote-job/data-analytics-manager-remote-latam-"
    "performance-marketing-for-universities-📈-🎓-5687043"
)


@dataclass
class Response:
    status_code: int = 200
    text: str = "<p>Vacancy description</p>"
    headers: dict = field(default_factory=dict)
    closed: bool = False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError("HTTP failure containing sensitive-test-value")

    def close(self):
        self.closed = True


class RecordingSession(requests.Session):
    def __init__(self, responses=()):
        super().__init__()
        self.responses = iter(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs, self.prepare_request(requests.Request("GET", url))))
        result = next(self.responses)
        if isinstance(result, Exception):
            raise result
        return result


def cookie_file(tmp_path, content):
    path = tmp_path / "dailyremote.cookies.txt"
    path.write_text(content, encoding="utf-8")
    return path


@pytest.mark.parametrize("prefix", ["", "Cookie: "])
def test_cookie_header_loads_opaque_values_as_secure_domain_cookies(tmp_path, prefix):
    session = RecordingSession([Response()])
    path = cookie_file(tmp_path, prefix + "session_token=opaque=base64==; preference=remote\n")

    load_dailyremote_cookies(session, path)

    assert "Cookie" not in session.headers
    assert session.cookies.get("session_token", domain="dailyremote.com", path="/") == (
        "opaque=base64=="
    )
    assert session.cookies.get("preference", domain="dailyremote.com", path="/") == "remote"
    assert all(cookie.secure and cookie.path == "/" for cookie in session.cookies)
    assert fetch_dailyremote_text(session, LISTING_URL) == "<p>Vacancy description</p>"
    prepared = session.calls[0][2]
    assert prepared.headers["Cookie"] == "session_token=opaque=base64==; preference=remote"
    for url in ["https://outside.example/", "http://dailyremote.com/"]:
        outside = session.prepare_request(requests.Request("GET", url))
        assert "Cookie" not in outside.headers


@pytest.mark.parametrize(
    "content",
    [
        "",
        " \n",
        "Cookie: \n",
        "sensitive-test-value",
        "=sensitive-test-value",
        "invalid name=sensitive-test-value",
        "session=sensitive-test-value\nother=injected",
        "session=sensitive-test-value\r\nAuthorization: injected",
        "session=sensitive-test-value\x00",
        "session=sensitive-test-value; malformed",
        "session=sensitive-test-value; session=duplicate",
    ],
)
def test_cookie_file_rejects_malformed_content_without_disclosing_values(tmp_path, content, caplog):
    session = RecordingSession()
    path = cookie_file(tmp_path, content)

    with pytest.raises(ValueError) as error:
        load_dailyremote_cookies(session, path)

    assert "sensitive-test-value" not in str(error.value)
    assert "sensitive-test-value" not in caplog.text
    assert not session.cookies
    assert not session.calls


def test_missing_cookie_file_fails_without_request_or_cookie(tmp_path):
    session = RecordingSession()

    with pytest.raises(ValueError):
        load_dailyremote_cookies(session, tmp_path / "missing.cookies.txt")

    assert not session.cookies
    assert not session.calls


@pytest.mark.parametrize(
    "url",
    [
        "http://dailyremote.com/",
        "https://outside.example/",
        "https://dailyremote.com.evil.example/",
        "https://subdomain.dailyremote.com/",
        "https://www.dailyremote.com/",
        "https://user:password@dailyremote.com/",
        "https://dailyremote.com:8443/",
        "https://dailyremote.com:invalid/",
        "//dailyremote.com/",
        "https://dailyremote.com/sign_in",
        "https://dailyremote.com/logout",
        "https://dailyremote.com/account",
        "https://dailyremote.com/remote-jobs/logout",
        "https://dailyremote.com/remote-job/",
        "https://dailyremote.com/remote-job/slug/extra",
        "https://dailyremote.com/remote-job/../../logout",
        "https://dailyremote.com/remote-job/slug%2Fextra",
        "https://dailyremote.com/remote-job/slug%2F",
        "https://dailyremote.com/remote-job/slug%5Cextra",
        "https://dailyremote.com/remote-job/slug%3Bunexpected-parameter",
        "https://dailyremote.com/remote-job/%2E%2E/logout",
        "https://dailyremote.com/remote-job/%2E%2E%2Flogout",
        "https://dailyremote.com/remote-job/slug%252Fextra",
        "https://dailyremote.com/remote-job/slug%255Cextra",
        "https://dailyremote.com/remote-job/slug%253Bparameter",
        "https://dailyremote.com/remote-job/slug%252E%252E",
        "https://dailyremote.com/remote-job/slug%00",
        "https://dailyremote.com/remote-job/slug%20extra",
        "https://dailyremote.com/remote-job/slug%C2%85extra",
        "https://dailyremote.com/remote-job/slug%C2%A0extra",
        "https://dailyremote.com/remote-job/slug%FF",
        "https://dailyremote.com/remote-job/slug%ED%A0%80",
        "https://dailyremote.com/remote-job/slug%2",
        "https://dailyremote.com/remote-job/slug;unexpected-parameter",
    ],
)
def test_disallowed_origin_or_nonvacancy_path_is_rejected_before_request(url):
    session = RecordingSession()

    with pytest.raises(ValueError):
        fetch_dailyremote_text(session, url)

    assert not session.calls


@pytest.mark.parametrize(
    "url",
    [
        LISTING_URL,
        DETAIL_URL,
        "https://dailyremote.com",
        "https://dailyremote.com:443/",
        "https://dailyremote.com/remote-jobs?search=analytics",
        "https://dailyremote.com/remote-jobs/",
        "https://dailyremote.com/remote-job/head_analytics-123/",
        "https://dailyremote.com" + UNICODE_DETAIL_PATH,
        "https://dailyremote.com" + quote(UNICODE_DETAIL_PATH),
    ],
)
def test_allowed_pages_use_get_without_automatic_redirects_and_close_response(url):
    response = Response()
    session = RecordingSession([response])

    assert fetch_dailyremote_text(session, url) == response.text

    assert len(session.calls) == 1
    assert session.calls[0][1] == {"timeout": 20, "allow_redirects": False}
    assert response.closed


@pytest.mark.parametrize(
    "location",
    [
        "https://outside.example/remote-job/123",
        "http://dailyremote.com/remote-job/123",
        "https://subdomain.dailyremote.com/remote-job/123",
        "https://dailyremote.com:8443/remote-job/123",
        "/logout",
        "/remote-job/slug%2F",
        "/remote-job/slug%5Cextra",
        "/remote-job/slug%252Fextra",
        "/remote-job/%2E%2E%2Flogout",
    ],
)
def test_redirect_is_rejected_before_following_disallowed_address(location):
    response = Response(status_code=302, headers={"Location": location})
    session = RecordingSession([response])

    with pytest.raises(ValueError):
        fetch_dailyremote_text(session, LISTING_URL)

    assert [call[0] for call in session.calls] == [LISTING_URL]
    assert response.closed


@pytest.mark.parametrize("status_code", [301, 302, 303, 307, 308])
def test_relative_vacancy_redirect_preserves_cookie_and_closes_responses(tmp_path, status_code):
    redirect = Response(status_code=status_code, headers={"Location": "/remote-job/123"})
    terminal = Response(text="Full vacancy")
    session = RecordingSession([redirect, terminal])
    load_dailyremote_cookies(session, cookie_file(tmp_path, "session=synthetic-token"))

    assert fetch_dailyremote_text(session, LISTING_URL) == "Full vacancy"

    assert [call[0] for call in session.calls] == [
        LISTING_URL,
        "https://dailyremote.com/remote-job/123",
    ]
    assert all(call[1]["allow_redirects"] is False for call in session.calls)
    assert all(call[2].headers["Cookie"] == "session=synthetic-token" for call in session.calls)
    assert redirect.closed and terminal.closed


def test_unicode_vacancy_redirect_accepts_percent_encoded_utf8_and_preserves_cookie(tmp_path):
    encoded_path = quote(UNICODE_DETAIL_PATH)
    redirect = Response(status_code=301, headers={"Location": encoded_path})
    terminal = Response(text="Full LATAM vacancy")
    session = RecordingSession([redirect, terminal])
    load_dailyremote_cookies(session, cookie_file(tmp_path, "session=synthetic-token"))

    assert fetch_dailyremote_text(session, "https://dailyremote.com" + UNICODE_DETAIL_PATH) == (
        terminal.text
    )

    assert [call[0] for call in session.calls] == [
        "https://dailyremote.com" + UNICODE_DETAIL_PATH,
        "https://dailyremote.com" + encoded_path,
    ]
    assert all(call[2].headers["Cookie"] == "session=synthetic-token" for call in session.calls)
    assert redirect.closed and terminal.closed


def test_root_listing_redirect_preserves_credentials_on_remote_jobs_page(tmp_path):
    redirect = Response(status_code=301, headers={"Location": "/remote-jobs?search=analytics"})
    terminal = Response(text="Authenticated vacancy listing")
    session = RecordingSession([redirect, terminal])
    load_dailyremote_cookies(session, cookie_file(tmp_path, "session=synthetic-token"))

    assert fetch_dailyremote_text(session, LISTING_URL) == "Authenticated vacancy listing"

    assert [call[0] for call in session.calls] == [
        LISTING_URL,
        "https://dailyremote.com/remote-jobs?search=analytics",
    ]
    assert all(call[2].headers["Cookie"] == "session=synthetic-token" for call in session.calls)
    assert redirect.closed and terminal.closed


def test_redirect_without_location_fails_and_closes_response():
    response = Response(status_code=302)
    session = RecordingSession([response])

    with pytest.raises(ValueError):
        fetch_dailyremote_text(session, LISTING_URL)

    assert len(session.calls) == 1
    assert response.closed


def test_redirect_loop_is_bounded_and_closes_all_responses():
    responses = [Response(status_code=302, headers={"Location": "/"}) for _ in range(4)]
    session = RecordingSession(responses)

    with pytest.raises(ValueError):
        fetch_dailyremote_text(session, LISTING_URL)

    assert len(session.calls) == 4
    assert all(response.closed for response in responses)


@pytest.mark.parametrize(
    "result",
    [
        requests.ConnectionError("request contained sensitive-test-value"),
        requests.Timeout("Cookie: sensitive-test-value"),
        Response(status_code=403),
    ],
)
def test_request_failures_are_sanitized_and_response_is_closed(result, caplog):
    session = RecordingSession([result])

    with pytest.raises(ValueError) as error:
        fetch_dailyremote_text(session, LISTING_URL)

    assert "sensitive-test-value" not in str(error.value)
    assert "sensitive-test-value" not in caplog.text
    if isinstance(result, Response):
        assert result.closed


def test_adapter_reads_authenticated_listing_and_full_visible_description(tmp_path, monkeypatch):
    listing = Response(
        text='<h2><a href="/remote-job/head-analytics-123">Head of Analytics</a></h2>'
        "<div>Acme · Brazil</div><p>Short summary.</p>"
    )
    detail = Response(
        text='<div class="dj-descwrap"><div class="dj-prose">'
        "<p>Lead analytics.</p><p>Must reside in Brazil.</p></div></div>"
    )
    session = RecordingSession([listing, detail])
    monkeypatch.setattr(dailyremote, "build_session", lambda: session)
    adapter = dailyremote.DailyRemoteAdapter(
        "dailyremote",
        {
            "search_urls": [LISTING_URL],
            "cookies_file": str(cookie_file(tmp_path, "session=synthetic-token")),
        },
    )

    jobs = adapter.fetch_jobs()

    assert len(jobs) == 1
    assert jobs[0].description_clean == "Lead analytics. Must reside in Brazil."
    assert jobs[0].source_metadata["description_complete"] is True
    assert not adapter.errors
    assert [call[0] for call in session.calls] == [LISTING_URL, DETAIL_URL]
    assert all(call[2].headers["Cookie"] == "session=synthetic-token" for call in session.calls)


@pytest.mark.parametrize("missing_file", [False, True])
def test_adapter_invalid_cookie_file_cannot_fall_back_to_anonymous_fetch(
    tmp_path, monkeypatch, missing_file
):
    session = RecordingSession()
    monkeypatch.setattr(dailyremote, "build_session", lambda: session)
    path = (
        tmp_path / "missing.cookies.txt" if missing_file else cookie_file(tmp_path, "not-a-cookie")
    )
    adapter = dailyremote.DailyRemoteAdapter(
        "dailyremote",
        {
            "search_urls": [LISTING_URL],
            "cookies_file": str(path),
        },
    )

    with pytest.raises(ValueError):
        adapter.fetch_jobs()

    assert not session.calls


def test_adapter_cookie_mode_rejects_wrong_initial_host_before_request(tmp_path, monkeypatch):
    session = RecordingSession()
    monkeypatch.setattr(dailyremote, "build_session", lambda: session)
    adapter = dailyremote.DailyRemoteAdapter(
        "dailyremote",
        {
            "search_urls": ["https://outside.example/"],
            "cookies_file": str(cookie_file(tmp_path, "session=synthetic-token")),
        },
    )

    assert adapter.fetch_jobs() == []

    assert len(adapter.errors) == 1
    assert "synthetic-token" not in adapter.errors[0]
    assert not session.calls


def test_adapter_expired_cookie_preserves_incomplete_card_without_disclosing_cookie(
    tmp_path, monkeypatch
):
    listing = Response(
        text='<h2><a href="/remote-job/head-analytics-123">Head of Analytics</a></h2>'
        "<div>Acme · Brazil</div><p>Useful listing summary.</p>"
    )
    detail = Response(
        text='<div class="dj-descwrap dj-paygated"><div class="dj-prose">'
        '<p>HTML Ipsum filler.</p></div><div class="dj-lock">Unlock this job</div></div>'
    )
    session = RecordingSession([listing, detail])
    monkeypatch.setattr(dailyremote, "build_session", lambda: session)
    adapter = dailyremote.DailyRemoteAdapter(
        "dailyremote",
        {
            "search_urls": [LISTING_URL],
            "cookies_file": str(cookie_file(tmp_path, "session=synthetic-private-value")),
        },
    )

    jobs = adapter.fetch_jobs()

    assert len(jobs) == 1
    assert "Useful listing summary" in jobs[0].description_clean
    assert "HTML Ipsum" not in jobs[0].description_clean
    assert jobs[0].source_metadata["description_complete"] is False
    assert jobs[0].source_metadata["detail_access"] == "restricted"
    assert "synthetic-private-value" not in repr(jobs[0].source_metadata)
    assert "synthetic-private-value" not in repr(adapter.errors)
    assert [call[0] for call in session.calls] == [LISTING_URL, DETAIL_URL]
