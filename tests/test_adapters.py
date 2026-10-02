"""Offline source fixtures: preserve eligibility, source dates, and partial results."""

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
import requests
import yaml
from bs4 import BeautifulSoup

from job_intake.adapters import ats, dailyremote, html_page
from job_intake.adapters._parsing import fetch_detail_text
from job_intake.adapters.ats import AshbyAdapter, GreenhouseAdapter, LeverAdapter
from job_intake.adapters.company_watchlist import CompanyWatchlistAdapter
from job_intake.adapters.dailyremote import DailyRemoteAdapter
from job_intake.adapters.factory import build_adapter
from job_intake.adapters.html_page import HtmlPageAdapter
from job_intake.config.settings import SourceDefinition
from job_intake.models.job import JobStatus

LISTING_URL = "https://dailyremote.com/?search=analytics"
LISTING = """
<h2><a href="/remote-job/head-analytics-123">Head of Analytics</a></h2>
<div>Acme · Worldwide · Full Time</div>
<p>Lead experimentation and analytics.</p>
<p>Must reside in Germany. Contractor role.</p>
<ul><li>English proficiency is required.</li></ul>
<a href="/remote-job/head-analytics-123">Apply now</a>
"""


@pytest.fixture(autouse=True)
def prohibit_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Adapter tests must not make live network requests")

    monkeypatch.setattr(requests.Session, "request", blocked)


def mock_pages(monkeypatch, module, listings, details=None):
    calls = []

    def fetch(session, url):
        calls.append(url)
        result = listings[url]
        if isinstance(result, Exception):
            raise result
        return result

    def detail(session, url, listing_url):
        calls.append(url)
        result = (details or {})[url]
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(module, "fetch_text", fetch)
    monkeypatch.setattr(module, "fetch_detail_text", detail)
    return calls


def test_dailyremote_keeps_later_paragraphs_and_lists_without_inventing_date():
    adapter = DailyRemoteAdapter("dailyremote", {})
    jobs = adapter._parse_listing_page(LISTING_URL, BeautifulSoup(LISTING, "html.parser"))
    assert len(jobs) == 1
    job = jobs[0]
    assert "Must reside in Germany" in job.description_clean
    assert "English proficiency" in job.description_clean
    assert job.company == "Acme"
    assert job.posted_at is None
    assert job.source_metadata["description_complete"] is False
    assert job.source_metadata["card_paragraph_count"] == 2


def test_dailyremote_enriches_nested_jsonld_full_description_and_source_date(monkeypatch):
    posting = {
        "@type": ["JobPosting"],
        "description": "<p>Lead analytics.</p><p>Must reside in Brazil.</p>",
        "datePosted": "2026-09-28T12:00:00Z",
        "hiringOrganization": {"@type": "Organization", "name": "Acme Full Name"},
        "jobLocationType": "TELECOMMUTE",
        "applicantLocationRequirements": {"@type": "Country", "name": "Brazil"},
        "employmentType": ["FULL_TIME"],
        "baseSalary": {
            "currency": "USD",
            "value": {"minValue": 100000, "maxValue": 150000, "unitText": "YEAR"},
        },
    }
    detail = '<script type="application/ld+json">' + json.dumps({"@graph": [posting]}) + "</script>"
    mock_pages(
        monkeypatch,
        dailyremote,
        {LISTING_URL: LISTING},
        {"https://dailyremote.com/remote-job/head-analytics-123": detail},
    )
    job = DailyRemoteAdapter("dailyremote", {"search_urls": [LISTING_URL]}).fetch_jobs()[0]
    assert "Must reside in Brazil" in job.description_clean
    assert job.company == "Acme Full Name"
    assert job.posted_at == datetime(2026, 9, 28, 12, tzinfo=UTC)
    assert job.remote_text == "Remote"
    assert job.location_text == "Brazil"
    assert job.source_metadata["applicant_location_requirements"] == ["Brazil"]
    assert job.source_metadata["description_complete"] is True
    assert job.salary_text == "USD 100000–150000 YEAR"


def test_dailyremote_expired_jsonld_is_closed(monkeypatch):
    detail = (
        '<script type="application/ld+json">'
        + json.dumps(
            {
                "@type": "JobPosting",
                "description": "Lead analytics.",
                "validThrough": "2020-01-01T00:00:00Z",
            }
        )
        + "</script>"
    )
    mock_pages(
        monkeypatch,
        dailyremote,
        {LISTING_URL: LISTING},
        {
            "https://dailyremote.com/remote-job/head-analytics-123": detail,
        },
    )
    job = DailyRemoteAdapter("dailyremote", {"search_urls": [LISTING_URL]}).fetch_jobs()[0]
    assert job.status == JobStatus.CLOSED
    assert job.posted_at is None


def test_dailyremote_detail_failure_preserves_card_and_reports_error(monkeypatch):
    mock_pages(
        monkeypatch,
        dailyremote,
        {LISTING_URL: LISTING},
        {
            "https://dailyremote.com/remote-job/head-analytics-123": requests.HTTPError("HTTP 503"),
        },
    )
    adapter = DailyRemoteAdapter("dailyremote", {"search_urls": [LISTING_URL]})
    job = adapter.fetch_jobs()[0]
    assert "Must reside in Germany" in job.description_clean
    assert job.source_metadata["description_complete"] is False
    assert "503" in job.source_metadata["detail_error"]
    assert len(adapter.errors) == 1


def test_dailyremote_listing_failure_is_isolated_between_searches(monkeypatch):
    failed = "https://dailyremote.com/?search=product"
    mock_pages(
        monkeypatch, dailyremote, {failed: requests.HTTPError("HTTP 500"), LISTING_URL: LISTING}
    )
    adapter = DailyRemoteAdapter(
        "dailyremote",
        {
            "search_urls": [failed, LISTING_URL],
            "fetch_details": False,
        },
    )
    assert len(adapter.fetch_jobs()) == 1
    assert len(adapter.errors) == 1
    assert "500" in adapter.errors[0]


def test_dailyremote_pagination_is_bounded_and_duplicates_are_not_refetched(monkeypatch):
    page2 = "https://dailyremote.com/?search=analytics&page=2"
    page3 = "https://dailyremote.com/?search=analytics&page=3"
    calls = mock_pages(
        monkeypatch,
        dailyremote,
        {
            LISTING_URL: LISTING + f'<a rel="next" href="{page2}">Next</a>',
            page2: LISTING + f'<a rel="next" href="{page3}">Next</a>',
        },
    )
    adapter = DailyRemoteAdapter(
        "dailyremote",
        {
            "search_urls": [LISTING_URL],
            "fetch_details": False,
            "max_pages": 2,
        },
    )
    assert len(adapter.fetch_jobs()) == 1
    assert calls == [LISTING_URL, page2]
    assert "pagination limit" in adapter.errors[0]


def test_dailyremote_does_not_fetch_offsite_links_or_blog_body(monkeypatch):
    outside = LISTING.replace("/remote-job/head-analytics-123", "https://outside.example/job")
    calls = mock_pages(monkeypatch, dailyremote, {LISTING_URL: outside})
    adapter = DailyRemoteAdapter("dailyremote", {"search_urls": [LISTING_URL]})
    job = adapter.fetch_jobs()[0]
    assert calls == [LISTING_URL]
    assert "outside listing origin" in job.source_metadata["detail_error"]

    mock_pages(
        monkeypatch,
        dailyremote,
        {LISTING_URL: LISTING},
        {
            "https://dailyremote.com/remote-job/head-analytics-123": (
                "<main><p>Job unavailable</p></main>"
            ),
        },
    )
    job = adapter.fetch_jobs()[0]
    assert "Must reside in Germany" in job.description_clean
    assert job.source_metadata["description_complete"] is False
    assert "No JobPosting" in job.source_metadata["detail_error"]


def test_dailyremote_detail_limit_keeps_remaining_cards(monkeypatch):
    second = LISTING.replace("head-analytics-123", "head-analytics-124")
    calls = mock_pages(
        monkeypatch,
        dailyremote,
        {LISTING_URL: LISTING + second},
        {
            "https://dailyremote.com/remote-job/head-analytics-123": (
                '<section class="job-description"><p>Full description</p></section>'
            ),
        },
    )
    jobs = DailyRemoteAdapter(
        "dailyremote",
        {
            "search_urls": [LISTING_URL],
            "max_detail_fetches": 1,
        },
    ).fetch_jobs()
    assert len(jobs) == 2
    assert len(calls) == 2
    assert jobs[0].source_metadata["description_complete"] is True
    assert jobs[1].source_metadata["description_complete"] is False
    assert jobs[1].source_metadata["detail_error"] == "Detail fetch limit reached"


def test_dailyremote_current_paywall_preserves_summary_even_with_explicit_prose_selector(
    monkeypatch,
):
    # Structure recorded in the anonymous October 2026 response. The real body
    # and company are withheld; the prose wrapper contains only dummy filler.
    detail = """
    <title>Head of Analytics at [Hidden Company]</title>
    <script type="application/ld+json">
      {"@type":"BreadcrumbList","itemListElement":[]}
    </script>
    <div class="dj-ai-summary"><span>AI Summary</span><p>Lead analytics.</p></div>
    <div class="dj-descwrap dj-paygated">
      <div class="dj-prose"><h1>HTML Ipsum Presents</h1>
        <p>Pellentesque habitant morbi tristique.</p></div>
      <div class="dj-lock"><h2 class="dj-lock__title">Unlock this job</h2>
        <a href="/get-started">Get Started</a>
        <p>Trusted by remote workers worldwide</p><a href="/sign_in">Sign in</a></div>
    </div>
    """
    mock_pages(
        monkeypatch,
        dailyremote,
        {LISTING_URL: LISTING},
        {
            "https://dailyremote.com/remote-job/head-analytics-123": detail,
        },
    )
    job = DailyRemoteAdapter(
        "dailyremote",
        {
            "search_urls": [LISTING_URL],
            "detail_description_selector": ".dj-prose",
        },
    ).fetch_jobs()[0]
    assert "Must reside in Germany" in job.description_clean
    assert "HTML Ipsum" not in job.description_clean
    assert "Pellentesque" not in job.description_clean
    assert "Trusted by" not in job.description_clean
    assert job.source_metadata["description_complete"] is False
    assert job.source_metadata["detail_access"] == "restricted"
    assert "access required" in job.source_metadata["detail_error"]
    assert job.posted_at is None


def test_dailyremote_visible_prose_excludes_summary_and_promotion(monkeypatch):
    detail = """
    <div class="dj-ai-summary"><p>AI short summary.</p></div>
    <div class="dj-descwrap"><div class="dj-prose">
      <p>Lead analytics for product teams.</p><ul><li>Must live in Brazil.</li></ul>
    </div></div>
    <aside><p>Our service finds remote roles worldwide.</p></aside>
    """
    mock_pages(
        monkeypatch,
        dailyremote,
        {LISTING_URL: LISTING},
        {
            "https://dailyremote.com/remote-job/head-analytics-123": detail,
        },
    )
    job = DailyRemoteAdapter("dailyremote", {"search_urls": [LISTING_URL]}).fetch_jobs()[0]
    assert job.description_clean == "Lead analytics for product teams. Must live in Brazil."
    assert job.source_metadata["description_complete"] is True
    assert "AI short summary" not in job.description_clean
    assert "worldwide" not in job.description_clean


def html_params():
    return {
        "url": "https://example.com/careers",
        "company": "Example",
        "listing_selector": "article",
        "title_selector": "a",
        "description_selector": "p",
        "detail_description_selector": "section.details",
        "posted_at_selector": "time",
    }


def test_html_full_detail_and_failed_detail_are_independent(monkeypatch):
    listing = """
    <article data-job-id="1"><a href="/jobs/1">Product Manager</a><p>Summary</p>
      <time datetime="2026-09-29T00:00:00Z">Yesterday</time></article>
    <article data-job-id="2"><a href="/jobs/2">Analytics Manager</a><p>Second summary</p></article>
    """
    mock_pages(
        monkeypatch,
        html_page,
        {"https://example.com/careers": listing},
        {
            "https://example.com/jobs/1": '<section class="details"><p>Full description</p>'
            "<p>Applicants must live in Brazil.</p></section>",
            "https://example.com/jobs/2": requests.HTTPError("HTTP 404"),
        },
    )
    adapter = HtmlPageAdapter("html", html_params())
    jobs = adapter.fetch_jobs()
    assert len(jobs) == 2
    assert jobs[0].company == "Example"
    assert "Applicants must live in Brazil" in jobs[0].description_clean
    assert jobs[0].source_metadata["description_complete"] is True
    assert jobs[0].posted_at == datetime(2026, 9, 29, tzinfo=UTC)
    assert jobs[1].description_clean == "Second summary"
    assert jobs[1].posted_at is None
    assert jobs[1].source_metadata["description_complete"] is False
    assert "404" in adapter.errors[0]


def test_greenhouse_decodes_full_body_and_does_not_use_edit_date_as_posting_date(monkeypatch):
    urls = []

    def payload(session, url):
        urls.append(url)
        return {
            "jobs": [
                {
                    "id": 123,
                    "title": "Head of Analytics",
                    "absolute_url": "https://gh.example/123",
                    "updated_at": "2026-09-29T00:00:00Z",
                    "location": {"name": "Remote - Brazil"},
                    "content": "&amp;lt;p&amp;gt;Lead analytics.&amp;lt;/p&amp;gt;"
                    "&amp;lt;p&amp;gt;Must reside in Brazil.&amp;lt;/p&amp;gt;",
                    "language": "en",
                },
                {"id": 456, "title": "Broken listing"},
            ]
        }

    monkeypatch.setattr(ats, "fetch_json_payload", payload)
    adapter = GreenhouseAdapter("gh", {"board_token": "example", "company": "Example"})
    jobs = adapter.fetch_jobs()
    assert len(jobs) == 1
    job = jobs[0]
    assert urls == ["https://boards-api.greenhouse.io/v1/boards/example/jobs?content=true"]
    assert job.description_clean == "Lead analytics. Must reside in Brazil."
    assert job.company == "Example"
    assert job.posted_at is None
    assert job.source_metadata["updated_at"] == "2026-09-29T00:00:00Z"
    assert job.source_metadata["description_complete"] is True
    assert job.source_metadata["language"] == "en"
    assert job.remote_text == "Remote"
    assert job.status == JobStatus.OPEN
    assert len(adapter.errors) == 1


def test_ashby_keeps_multiple_locations_compensation_and_publication_date(monkeypatch):
    item = {
        "title": "Product Manager",
        "jobUrl": "https://jobs.ashbyhq.com/example/posting-1",
        "applyUrl": "https://jobs.ashbyhq.com/example/posting-1/apply",
        "isListed": True,
        "descriptionHtml": "<p>Own the roadmap.</p><p>English required.</p>",
        "publishedAt": "2026-09-25T12:30:00+00:00",
        "location": "São Paulo, Brazil",
        "secondaryLocations": [{"location": "Remote, Brazil"}],
        "workplaceType": "Hybrid",
        "employmentType": "FullTime",
        "compensation": {"scrapeableCompensationSalarySummary": "$100K – $150K"},
    }
    monkeypatch.setattr(
        ats,
        "fetch_json_payload",
        lambda session, url: {
            "jobs": [item, {**item, "isListed": False}],
        },
    )
    jobs = AshbyAdapter("ashby", {"board_name": "example", "company": "Example"}).fetch_jobs()
    assert len(jobs) == 1
    job = jobs[0]
    assert job.source_job_id == "posting-1"
    assert job.location_text == "São Paulo, Brazil; Remote, Brazil"
    assert job.remote_text == "Hybrid"
    assert job.posted_at == datetime(2026, 9, 25, 12, 30, tzinfo=UTC)
    assert job.description_clean == "Own the roadmap. English required."
    assert job.salary_text == "$100K – $150K"
    assert job.apply_url.endswith("/apply")


def lever_item(job_id="posting-1"):
    return {
        "id": job_id,
        "text": "Data Science Manager",
        "hostedUrl": f"https://jobs.lever.co/{job_id}",
        "applyUrl": f"https://jobs.lever.co/{job_id}/apply",
        "description": "<p>Lead DS.</p>",
        "lists": [{"text": "Requirements", "content": "<li>English required.</li>"}],
        "additional": "<p>Must reside in Brazil.</p>",
        "workplaceType": "remote",
        "country": "BR",
        "categories": {
            "location": "Brazil",
            "allLocations": ["Brazil", "São Paulo"],
            "commitment": "Full-time",
        },
        "salaryRange": {"currency": "USD", "min": 100000, "max": 150000, "interval": "year"},
    }


def test_lever_full_description_includes_requirements_and_closing_restrictions(monkeypatch):
    urls = []

    def payload(session, url):
        urls.append(url)
        return [lever_item()]

    monkeypatch.setattr(ats, "fetch_json_payload", payload)
    job = LeverAdapter(
        "lever", {"site": "example", "company": "Example", "region": "eu"}
    ).fetch_jobs()[0]
    assert urls == ["https://api.eu.lever.co/v0/postings/example?mode=json&skip=0&limit=100"]
    assert job.description_clean == "Lead DS. Requirements English required. Must reside in Brazil."
    assert job.location_text == "Brazil; São Paulo"
    assert job.salary_text == "USD 100000–150000 year"
    assert job.posted_at is None
    assert job.source_metadata["country"] == "BR"


def test_lever_failed_later_page_preserves_successful_records(monkeypatch):
    def payload(session, url):
        if "skip=1" in url:
            raise requests.HTTPError("HTTP 503")
        return [lever_item()]

    monkeypatch.setattr(ats, "fetch_json_payload", payload)
    adapter = LeverAdapter("lever", {"site": "example", "page_size": 1})
    assert len(adapter.fetch_jobs()) == 1
    assert "Lever page 2" in adapter.errors[0]
    assert "503" in adapter.errors[0]


@pytest.mark.parametrize(
    "adapter_type,adapter_class",
    [
        ("greenhouse", GreenhouseAdapter),
        ("ashby", AshbyAdapter),
        ("lever", LeverAdapter),
    ],
)
def test_factory_builds_public_ats_adapters(adapter_type, adapter_class):
    assert isinstance(
        build_adapter(SourceDefinition(name="example", type=adapter_type)), adapter_class
    )


def test_watchlist_mixes_ats_and_legacy_html_and_isolates_company_failures(tmp_path, monkeypatch):
    watchlist = {
        "companies": [
            {"name": "Broken", "type": "greenhouse", "params": {"board_token": "broken"}},
            {
                "name": "Healthy",
                "type": "ashby",
                "bucket": "extended",
                "params": {"board_name": "healthy"},
            },
            {
                "name": "Legacy",
                "careers_url": "https://example.com/careers",
                "listing_selector": "article",
                "title_selector": "a",
                "description_selector": "p",
            },
            {"name": "Disabled", "type": "lever", "enabled": False},
        ]
    }
    path = tmp_path / "watchlist.yaml"
    path.write_text(yaml.safe_dump(watchlist), encoding="utf-8")

    def payload(session, url):
        if "greenhouse" in url:
            raise requests.HTTPError("HTTP 500")
        return {
            "jobs": [
                {
                    "title": "Product Manager",
                    "jobUrl": "https://ashby.example/1",
                    "descriptionPlain": "Own the roadmap.",
                }
            ]
        }

    monkeypatch.setattr(ats, "fetch_json_payload", payload)
    monkeypatch.setattr(
        html_page,
        "fetch_text",
        lambda session, url: (
            '<article><a href="/jobs/2">Analytics Manager</a><p>Own analytics.</p></article>'
        ),
    )
    adapter = CompanyWatchlistAdapter("watchlist", {"watchlist_path": str(path)})
    jobs = adapter.fetch_jobs()
    assert [job.company for job in jobs] == ["Healthy", "Legacy"]
    assert jobs[0].source == "watchlist:Healthy"
    assert jobs[0].source_metadata["watchlist_bucket"] == "extended"
    assert jobs[1].source_metadata["description_complete"] is False
    assert len(adapter.errors) == 1
    assert "Broken" in adapter.errors[0]
    assert "500" in adapter.errors[0]


def test_detail_redirect_is_validated_before_following_offsite_url():
    calls = []

    def get(url, **kwargs):
        calls.append(url)
        assert kwargs["allow_redirects"] is False
        return SimpleNamespace(status_code=302, headers={"Location": "http://127.0.0.1/private"})

    with pytest.raises(ValueError, match="outside listing origin"):
        fetch_detail_text(
            SimpleNamespace(get=get), "https://example.com/jobs/1", "https://example.com/careers"
        )
    assert calls == ["https://example.com/jobs/1"]
