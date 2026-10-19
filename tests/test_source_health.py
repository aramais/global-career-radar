"""Lifecycle evidence from offline source fixtures, never inferred from a bounded search."""

from datetime import UTC, datetime

import pytest
import requests

from job_intake.adapters import ats, dailyremote
from job_intake.adapters._parsing import publication_metadata
from job_intake.adapters.ats import AshbyAdapter, GreenhouseAdapter, LeverAdapter
from job_intake.adapters.base import SourceAuthenticationError
from job_intake.adapters.dailyremote import DailyRemoteAdapter


@pytest.fixture(autouse=True)
def prohibit_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Source-health fixtures must not make live requests")
    monkeypatch.setattr(requests.Session, "request", blocked)


def greenhouse_job(job_id=1):
    return {"id": job_id, "title": "Analytics Manager",
            "absolute_url": f"https://boards.greenhouse.io/example/jobs/{job_id}",
            "content": "<p>Lead analytics projects.</p>"}


def test_successful_full_ats_snapshot_can_support_missing_evidence(monkeypatch):
    monkeypatch.setattr(ats, "fetch_json_payload", lambda *_: {"jobs": [greenhouse_job()]})
    adapter = GreenhouseAdapter("company:example", {"board_token": "example"})
    assert len(adapter.fetch_jobs()) == 1
    snapshot = adapter.board_snapshots[0]
    assert snapshot["source"] == "company:example"
    assert snapshot["board_id"] == "example" and snapshot["scope"] == "board"
    assert snapshot["snapshot_complete"] is True and snapshot["status"] == "success"
    assert snapshot["seen_job_ids"] == ["1"]
    assert snapshot["coverage"] == {"records": 1, "complete_descriptions": 1}
    assert adapter.source_health["snapshot_complete"] is True


def test_malformed_paid_board_item_keeps_valid_jobs_and_invalidates_fullness(monkeypatch):
    monkeypatch.setattr(ats, "fetch_json_payload", lambda *_: {
        "jobs": [greenhouse_job(), {"id": 2}],
    })
    adapter = GreenhouseAdapter("example", {"board_token": "example"})
    assert len(adapter.fetch_jobs()) == 1
    assert adapter.board_snapshots[0]["status"] == "partial"
    assert adapter.board_snapshots[0]["snapshot_complete"] is False


def test_failed_board_cannot_be_a_successful_empty_snapshot(monkeypatch):
    def failed(*_):
        raise requests.HTTPError("HTTP 503")
    monkeypatch.setattr(ats, "fetch_json_payload", failed)
    adapter = GreenhouseAdapter("example", {"board_token": "example"})
    with pytest.raises(requests.HTTPError):
        adapter.fetch_jobs()
    assert adapter.source_health["status"] == "failed"
    assert adapter.board_snapshots[0]["snapshot_complete"] is False


def test_ashby_filter_is_not_full_board_evidence(monkeypatch):
    item = {"id": "a", "title": "Data Science Manager",
            "jobUrl": "https://jobs.ashbyhq.com/example/a", "descriptionPlain": "Lead data."}
    monkeypatch.setattr(ats, "fetch_json_payload", lambda *_: {
        "jobs": [item, {**item, "id": "b", "isListed": False}],
    })
    adapter = AshbyAdapter("example", {"board_name": "example"})
    assert len(adapter.fetch_jobs()) == 1
    assert adapter.board_snapshots[0]["snapshot_complete"] is False
    assert adapter.board_snapshots[0]["status"] == "partial"


def test_lever_pagination_cap_cannot_close_missing_jobs(monkeypatch):
    monkeypatch.setattr(ats, "fetch_json_payload", lambda *_: [{
        "id": "one", "text": "Analytics Manager", "hostedUrl": "https://jobs.lever.co/a/one",
        "description": "Lead analytics projects.",
    }])
    adapter = LeverAdapter("example", {"site": "example", "page_size": 1, "max_pages": 1})
    assert len(adapter.fetch_jobs()) == 1
    assert adapter.board_snapshots[0]["snapshot_complete"] is False
    assert adapter.board_snapshots[0]["limits"]["max_pages"] == 1
    assert adapter.board_snapshots[0]["api_host"] == "api.lever.co"


def card(job_id, country="Brazil"):
    return (f'<h2><a href="/remote-job/{job_id}">Analytics Manager</a></h2>'
            f"<div>Example · {country} · Full Time</div><p>Lead analytical projects.</p>")


def test_dailyremote_query_frame_precedes_round_robin_detail_quota(monkeypatch):
    first = "https://dailyremote.com/remote-jobs?search=one"
    second = "https://dailyremote.com/remote-jobs?search=two"
    listings = {first: card("a") + card("b"), second: card("c") + card("d")}
    calls = []
    def listing(_session, url):
        calls.append(url)
        return listings[url]
    def detail(_session, url, _listing):
        calls.append(url)
        return '<div class="job-description">Lead pricing projects.</div>'
    monkeypatch.setattr(dailyremote, "fetch_text", listing)
    monkeypatch.setattr(dailyremote, "fetch_detail_text", detail)
    adapter = DailyRemoteAdapter("dailyremote", {
        "search_urls": [first, second], "max_detail_fetches": 2,
        "auth_policy": "allow_anonymous",
    })
    jobs = adapter.fetch_jobs()
    assert [job.original_url.rsplit("/", 1)[-1] for job in jobs] == ["a", "c", "b", "d"]
    assert calls[:2] == [first, second]
    assert calls[2:] == ["https://dailyremote.com/remote-job/a",
                         "https://dailyremote.com/remote-job/c"]
    assert [job.source_metadata["description_complete"] for job in jobs] == [
        True, True, False, False,
    ]
    snapshot = adapter.board_snapshots[0]
    assert snapshot["scope"] == "search" and snapshot["snapshot_complete"] is False
    assert snapshot["warnings"] == ["quota_deferred"]


@pytest.mark.parametrize("params", [
    {"auth_policy": "require_authenticated"}, {"cookies_file": ""},
])
def test_required_auth_without_cookie_never_falls_back_to_anonymous(params):
    adapter = DailyRemoteAdapter("dailyremote", {
        "search_urls": ["https://dailyremote.com/remote-jobs"], **params,
    })
    with pytest.raises(SourceAuthenticationError):
        adapter.fetch_jobs()
    assert adapter.source_health["status"] == "auth_required"
    assert adapter.board_snapshots[0]["snapshot_complete"] is False


def test_cookie_policy_typo_cannot_silently_disable_authentication():
    adapter = DailyRemoteAdapter("dailyremote", {"auth_policy": "authenticated"})
    with pytest.raises(ValueError, match="auth_policy"):
        adapter.fetch_jobs()
    assert adapter.source_health["status"] == "failed"


def test_paywall_is_visible_as_auth_required_and_preserves_card(monkeypatch):
    monkeypatch.setattr(dailyremote, "fetch_text", lambda *_: card("a"))
    monkeypatch.setattr(dailyremote, "fetch_detail_text", lambda *_: (
        '<div class="dj-descwrap dj-paygated"><div class="dj-lock">Sign in</div></div>'
    ))
    adapter = DailyRemoteAdapter("dailyremote", {
        "search_urls": ["https://dailyremote.com/remote-jobs"],
        "auth_policy": "allow_anonymous",
    })
    job = adapter.fetch_jobs()[0]
    assert "Lead analytical projects" in job.description_clean
    assert job.source_metadata["detail_outcome"] == "auth_required"
    assert adapter.source_health["status"] == "auth_required"
    assert not adapter.source_health["snapshot_complete"]


@pytest.mark.parametrize(("value", "precision"), [
    ("2026-10-19", "day"), ("2026-10-19T08:00:00Z", "timestamp"),
    ("2 days ago", "unknown"), (None, "unknown"),
])
def test_date_provenance_preserves_precision_without_guessing_publication(value, precision):
    metadata = publication_metadata(value, "fixture")
    assert metadata["published_label"] == value
    assert metadata["date_precision"] == precision
    assert metadata["publication_date_estimated"] is False
    assert datetime.now(UTC) != value


def test_card_duties_do_not_supply_the_location_or_arbitrary_apply_protocol():
    from bs4 import BeautifulSoup

    adapter = DailyRemoteAdapter("dailyremote", {})
    html = ('<h2><a href="/remote-job/a">Analytics Manager</a></h2>'
            '<div>Example · Full Time</div><p>Analyze Brazil market data for US clients.</p>'
            '<a href="javascript:openForm()">Apply now</a>')
    job = adapter._parse_listing_page("https://dailyremote.com/remote-jobs", BeautifulSoup(
        html, "html.parser",
    ))[0]
    assert job.location_text is None
    assert job.apply_url == job.original_url
    assert DailyRemoteAdapter._extract_salary("USD $100,000 – $150,000 per year") == (
        "USD $100,000 – $150,000 per year"
    )
