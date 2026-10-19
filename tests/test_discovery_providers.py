from __future__ import annotations

import json
import socket

import httpx
import pytest
import requests

from job_intake.discovery.providers import PerplexitySearch, ProviderError, PublicJobFetcher
from job_intake.models.job import JobStatus


class Response:
    def __init__(self, text="", *, payload=None, status=200, headers=None):
        self.body = text.encode("utf-8")
        self.payload = payload
        self.status_code = status
        self.headers = headers or {"Content-Type": "text/html; charset=utf-8"}
        self.encoding = "utf-8"
        self.closed = False

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload

    def iter_content(self, chunk_size):
        for index in range(0, len(self.body), chunk_size):
            yield self.body[index:index + chunk_size]

    def close(self):
        self.closed = True


class Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def _request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    def get(self, url, **kwargs):
        return self._request("GET", url, **kwargs)

    def post(self, url, **kwargs):
        return self._request("POST", url, **kwargs)


@pytest.fixture(autouse=True)
def public_dns(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
    ])


def posting(**overrides):
    return {
        "@context": "https://schema.org", "@type": "JobPosting",
        "url": "https://careers.example.com/jobs/42", "title": "Analytics Lead",
        "description": "<p>Responsibilities: lead pricing analytics and influence product "
        "decisions with finance and product teams.</p><p>Requirements: experience designing "
        "experiments and managing stakeholder relationships.</p>",
        "hiringOrganization": {"@type": "Organization", "name": "Example Company"},
        "jobLocation": {"address": {"addressLocality": "São Paulo", "addressCountry": "BR"}},
        "identifier": {"value": "42"}, "datePosted": "2026-10-16",
        **overrides,
    }


def page(value):
    return "<html><script type='application/ld+json'>" + json.dumps(value) + "</script></html>"


def test_search_uses_verified_endpoint_and_single_post():
    response = httpx.Response(200, json={"id": "fixture", "results": [{
        "title": "Analytics Lead", "url": "https://careers.example.com/jobs/42",
        "snippet": "pricing analytics", "date": "2026-10-16", "ignored": "not retained",
    }]})
    client, calls = search_client([response])
    hits = client.search(" analytics pricing ", 5)
    assert hits == [{"title": "Analytics Lead", "url": "https://careers.example.com/jobs/42",
                     "snippet": "pricing analytics", "date": "2026-10-16"}]
    assert len(calls) == 1
    request = calls[0]
    assert request.method == "POST"
    assert str(request.url) == "https://api.perplexity.ai/search"
    assert json.loads(request.content) == {"query": "analytics pricing", "max_results": 5}
    assert request.headers["Authorization"] == "Bearer secret"
    assert client.client.max_retries == 0
    assert response.is_closed
    client.close()


def search_client(responses, **kwargs):
    calls = []
    remaining = iter(responses)

    def respond(request):
        calls.append(request)
        result = next(remaining)
        if isinstance(result, Exception):
            raise result
        return result

    session = httpx.Client(transport=httpx.MockTransport(respond), follow_redirects=False)
    return PerplexitySearch("secret", session=session, **kwargs), calls


@pytest.mark.parametrize("response", [
    httpx.Response(200, json={"answer": "wrong API"}),
    httpx.Response(200, json={"id": "fixture", "results": ["not an object"]}),
    httpx.Response(200, json={"id": "fixture", "results": [
        {"title": "a", "url": "https://example.com"}]}),
    httpx.Response(200, text="secret response body is not JSON"),
    httpx.Response(401), httpx.Response(429),
    httpx.Response(307, headers={"Location": "https://other.example.com"}),
    httpx.ReadTimeout("Authorization Bearer secret"),
])
def test_search_schema_errors_and_failures_are_sanitized_and_not_retried(response):
    client, calls = search_client([response])
    with pytest.raises(ProviderError) as caught:
        client.search("query")
    assert caught.value.paid_request_attempted is True
    assert "secret" not in str(caught.value)
    assert len(calls) == 1
    client.close()


def test_search_validation_happens_before_paid_request():
    client, calls = search_client([])
    with pytest.raises(ProviderError) as caught:
        client.search("", 10)
    assert caught.value.paid_request_attempted is False
    assert not calls
    with pytest.raises(ProviderError):
        client.search("query", 21)
    assert not calls
    client.close()


def test_fetch_uses_original_full_description_and_metadata_not_search_hint():
    response = Response(page(posting()))
    session = Session([response])
    job = PublicJobFetcher(session=session).fetch({
        "url": "https://careers.example.com/jobs/42", "title": "Wrong search title",
        "snippet": "A misleading search snippet", "date": "2020-01-01",
    })
    assert job.source == "discovery"
    assert job.title == "Analytics Lead"
    assert job.company == "Example Company"
    assert job.source_job_id == "42"
    assert job.posted_at.year == 2026
    assert job.location_text == "São Paulo, BR"
    assert "pricing analytics" in job.description_clean
    assert "misleading" not in job.description_clean
    assert job.source_metadata["description_complete"] is True
    assert job.source_metadata["description_source"] == "json_ld"
    assert job.status == JobStatus.UNKNOWN
    assert response.closed


def test_graph_selects_exact_posting_url_instead_of_first():
    wrong = posting(title="Unrelated role", url="https://careers.example.com/jobs/99")
    target = posting()
    session = Session([Response(page({"@graph": [wrong, target]}))])
    job = PublicJobFetcher(session=session).fetch({"url": target["url"]})
    assert job.title == "Analytics Lead"


def test_multiple_postings_without_exact_url_are_rejected():
    postings = [posting(url="https://careers.example.com/jobs/1"),
                posting(url="https://careers.example.com/jobs/2")]
    session = Session([Response(page(postings))])
    with pytest.raises(ProviderError, match="multiple vacancies"):
        PublicJobFetcher(session=session).fetch({"url": "https://careers.example.com/careers"})


def test_single_posting_for_different_url_is_not_promoted_from_list_page():
    session = Session([Response(page(posting()))])
    with pytest.raises(ProviderError, match="different vacancy"):
        PublicJobFetcher(session=session).fetch({"url": "https://careers.example.com/careers"})


def test_missing_primary_text_does_not_fall_back_to_snippet():
    session = Session([Response("<main><h1>Careers</h1>Explore our opportunities</main>")])
    with pytest.raises(ProviderError, match="vacancy detail"):
        PublicJobFetcher(session=session).fetch({
            "url": "https://example.com/careers", "snippet": posting()["description"],
        })


@pytest.mark.parametrize("url", [
    "file:///etc/passwd", "http://localhost/jobs/42", "http://127.0.0.1/jobs",
    "http://10.0.0.1/jobs", "http://169.254.169.254/latest/meta-data",
    "http://[::1]/jobs", "http://[fc00::1]/jobs", "http://224.0.0.1/jobs",
    "https://user:password@example.com/jobs", "http://example.com:8080/jobs",
    "http://intranet/jobs", "http://example.local/jobs", "http://2130706433/jobs",
])
def test_private_local_and_credential_urls_are_rejected_before_fetch(url):
    session = Session([])
    with pytest.raises(ProviderError):
        PublicJobFetcher(session=session).fetch({"url": url})
    assert not session.calls


def test_private_dns_address_is_rejected(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.1.2.3", 443)),
    ])
    session = Session([])
    with pytest.raises(ProviderError):
        PublicJobFetcher(session=session).fetch({"url": "https://example.com/jobs/42"})
    assert not session.calls


def test_mixed_public_private_dns_rejected(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443)),
    ])
    with pytest.raises(ProviderError):
        PublicJobFetcher(session=Session([])).fetch({"url": "https://example.com/jobs/42"})


def test_redirect_private_target_is_rejected_before_second_request():
    response = Response(status=302, headers={"Location": "http://127.0.0.1/admin"})
    session = Session([response])
    with pytest.raises(ProviderError):
        PublicJobFetcher(session=session).fetch({"url": "https://example.com/jobs/42"})
    assert len(session.calls) == 1
    assert session.calls[0][2]["allow_redirects"] is False
    assert response.closed


def test_default_fetcher_pins_public_address_preserving_tls_identity():
    fetcher = PublicJobFetcher()
    adapter = fetcher.session.get_adapter("https://example.com/jobs/42")
    request = requests.Request("GET", "https://example.com/jobs/42").prepare()
    host, kwargs = adapter.build_connection_pool_key_attributes(request, True)
    assert host["host"] == "93.184.216.34"
    assert request.headers["Host"] == "example.com"
    assert kwargs["assert_hostname"] == "example.com"
    assert kwargs["server_hostname"] == "example.com"
    assert fetcher.session.trust_env is False


def test_redirects_are_bounded():
    responses = [Response(status=302, headers={"Location": "/jobs/42"}) for _ in range(5)]
    session = Session(responses)
    with pytest.raises(ProviderError, match="redirect limit"):
        PublicJobFetcher(session=session).fetch({"url": "https://example.com/jobs/42"})
    assert len(session.calls) == 5
    assert all(response.closed for response in responses)


def test_response_size_is_bounded_even_without_content_length():
    response = Response("x" * 2048)
    session = Session([response])
    with pytest.raises(ProviderError, match="size limit"):
        PublicJobFetcher(session=session, max_bytes=1024).fetch({"url": "https://example.com/jobs/42"})
    assert response.closed


def test_expired_posting_is_closed_but_geography_does_not_filter():
    value = posting(validThrough="2000-01-01", applicantLocationRequirements={"name": "US"})
    session = Session([Response(page(value))])
    job = PublicJobFetcher(session=session).fetch({"url": value["url"]})
    assert job.status == JobStatus.CLOSED
    assert "US" in job.location_text


def test_generic_html_requires_duties_requirements_and_apply_action():
    html = "<h1>Analytics Lead</h1><main><h2>Responsibilities</h2><p>" + (
        "Lead pricing analytics with product and finance teams. " * 8
    ) + "</p><h2>Requirements</h2><p>Experience with SQL and experiments.</p>" \
        "<a href='/application'>Apply now</a></main><nav>irrelevant navigation</nav>"
    session = Session([Response(html)])
    job = PublicJobFetcher(session=session).fetch({"url": "https://example.com/jobs/42"})
    assert job.status == JobStatus.UNKNOWN
    assert "irrelevant navigation" not in job.description_clean
    assert job.source_metadata["description_source"] == "html_detail"
    session = Session([Response(html.replace("Apply now", "Read more"))])
    with pytest.raises(ProviderError, match="sufficient"):
        PublicJobFetcher(session=session).fetch({"url": "https://example.com/blog/42"})


def test_greenhouse_uses_only_requested_job_full_description():
    payload = {
        "id": 42, "title": "Analytics Lead", "company_name": "Example",
        "absolute_url": "https://job-boards.greenhouse.io/example/jobs/42",
        "content": posting()["description"], "first_published": "2026-10-16",
        "location": {"name": "Remote"},
    }
    session = Session([Response(json.dumps(payload), headers={"Content-Type": "application/json"})])
    job = PublicJobFetcher(session=session).fetch({"url": payload["absolute_url"]})
    assert len(session.calls) == 1
    assert session.calls[0][1] == "https://boards-api.greenhouse.io/v1/boards/example/jobs/42"
    assert job.status == JobStatus.OPEN
    assert job.title == payload["title"]
    assert job.company == "Example"
    assert job.source_metadata["description_source"] == "api"


def test_ashby_selects_requested_id_only_from_board():
    common = {"descriptionPlain": "Experience. " * 30, "location": "Remote"}
    payload = {"jobs": [
        {**common, "id": "wrong", "title": "Wrong", "jobUrl": "https://jobs.ashbyhq.com/a/wrong"},
        {**common, "id": "target", "title": "Target", "jobUrl": "https://jobs.ashbyhq.com/a/target"},
    ]}
    session = Session([Response(json.dumps(payload))])
    job = PublicJobFetcher(session=session).fetch({"url": "https://jobs.ashbyhq.com/a/target"})
    assert job.title == "Target"
    assert job.source_job_id == "target"


def test_fetch_errors_do_not_leak_request_exception_details():
    session = Session([requests.Timeout("Private full body password=secret")])
    with pytest.raises(ProviderError) as caught:
        PublicJobFetcher(session=session).fetch({"url": "https://example.com/jobs/42"})
    assert "secret" not in str(caught.value)
    assert caught.value.paid_request_attempted is False
