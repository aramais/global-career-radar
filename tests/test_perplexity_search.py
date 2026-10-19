from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx
import pytest
from typer.testing import CliRunner

from job_intake.cli import app
from job_intake.discovery import cli, providers
from job_intake.discovery.providers import PerplexitySearch, ProviderError


def client_for(handler, **options):
    return PerplexitySearch("test-only-key", session=httpx.Client(
        transport=httpx.MockTransport(handler), follow_redirects=False), options=options)


def response(results=None):
    return httpx.Response(200, json={"id": "fixture", "results": results or []})


def test_multi_query_results_are_url_deduplicated_and_preserve_optional_fields():
    calls = []

    def handle(request):
        calls.append(request)
        return response([
            {"title": "First", "url": "https://example.com/job?utm_source=search",
             "snippet": "duties", "date": "2026-10-18", "last_updated": "2026-10-18"},
            {"title": "Mirror", "url": "https://example.com/job", "snippet": "duplicate"},
            {"title": "Second", "url": "https://example.com/other", "snippet": "other duties"},
        ])

    client = client_for(handle, country="br", search_domain_filter=["example.com/jobs"],
                        search_language_filter=["en"], search_context_size="low",
                        search_after_date_filter="10/01/2026", search_recency_filter="month")
    hits = client.search([" analytics lead ", "decision science"], max_results=2)
    assert len(calls) == 1
    assert json.loads(calls[0].content) == {
        "query": ["analytics lead", "decision science"], "max_results": 2, "country": "BR",
        "search_domain_filter": ["example.com/jobs"], "search_language_filter": ["en"],
        "search_context_size": "low", "search_after_date_filter": "10/01/2026",
        "search_recency_filter": "month",
    }
    assert [hit["title"] for hit in hits] == ["First", "Second"]
    assert hits[0]["date"] == hits[0]["last_updated"] == "2026-10-18"
    assert "date" not in hits[1]
    client.close()


@pytest.mark.parametrize("query", [[], ["q"] * 6, ["q", ""], ["q", 1], True])
def test_invalid_queries_fail_before_network(query):
    client = client_for(lambda _: pytest.fail("Invalid request must not reach HTTP"))
    with pytest.raises(ProviderError) as error:
        client.search(query)
    assert not error.value.paid_request_attempted
    client.close()


@pytest.mark.parametrize("options", [
    {"country": "BRA"}, {"max_tokens": True}, {"max_tokens_per_page": 1_000_001},
    {"search_domain_filter": ["example.com", "-other.com"]},
    {"search_domain_filter": ["https://example.com"]},
    {"search_domain_filter": ["a.com"] * 21}, {"search_language_filter": ["english"]},
    {"search_after_date_filter": "2026-10-18"}, {"last_updated_after_filter": "02/30/2026"},
    {"search_context_size": "very-high"}, {"search_context_size": "low", "max_tokens": 10},
    {"search_recency_filter": "decade"}, {"guessed_parameter": True},
])
def test_invalid_filters_fail_locally(options):
    with pytest.raises(ProviderError):
        client_for(lambda _: pytest.fail("No HTTP for invalid filters"), **options)


def test_env_key_resolution_and_missing_key_message(monkeypatch):
    monkeypatch.delenv("PERPLEXITY_API_KEY", raising=False)
    with pytest.raises(ProviderError, match="console.perplexity.ai"):
        PerplexitySearch()
    monkeypatch.setenv("PERPLEXITY_API_KEY", "environment-test-only")
    seen = []
    client = PerplexitySearch(session=httpx.Client(transport=httpx.MockTransport(
        lambda request: seen.append(request.headers["Authorization"]) or response())))
    client.search("query")
    assert seen == ["Bearer environment-test-only"]
    client.close()


def test_retry_after_cooldown_is_shared_without_hidden_paid_retries(monkeypatch):
    clock, sleeps, calls = [100.0], [], []
    monkeypatch.setattr(providers.time, "monotonic", lambda: clock[0])

    def sleep(delay):
        sleeps.append(delay)
        clock[0] += delay

    monkeypatch.setattr(providers.time, "sleep", sleep)

    def handle(request):
        calls.append(request)
        return httpx.Response(429, headers={"Retry-After": "3"}) if len(calls) == 1 else response()

    client = client_for(handle)
    with pytest.raises(ProviderError) as caught:
        client.search("first")
    assert caught.value.status_code == 429 and caught.value.retry_after_seconds == 3
    assert caught.value.billing_known_free
    assert len(calls) == 1
    assert client.search("second") == []
    assert sleeps == [3] and len(calls) == 2
    client.close()


def test_long_retry_after_never_sends_request_early():
    calls = []
    client = client_for(lambda request: calls.append(request) or httpx.Response(
        429, headers={"Retry-After": "300"}))
    with pytest.raises(ProviderError):
        client.search("one")
    with pytest.raises(ProviderError) as caught:
        client.search("two")
    assert len(calls) == 1
    assert caught.value.retry_after_seconds > 290
    assert not caught.value.paid_request_attempted
    client.close()


def test_retry_after_accepts_http_date_and_rejects_nonfinite():
    date = format_datetime(datetime.now(UTC) + timedelta(seconds=10), usegmt=True)
    assert 8 <= PerplexitySearch._retry_after(httpx.Headers({"Retry-After": date})) <= 10
    assert PerplexitySearch._retry_after(httpx.Headers({"Retry-After": "NaN"})) == 1
    assert PerplexitySearch._retry_after(httpx.Headers({"retry-after-ms": "500"})) == .5


def test_cli_search_smoke_reveals_only_status_and_shape(monkeypatch):
    monkeypatch.setattr(cli, "load_app_config", lambda _: None)
    client = client_for(lambda _: response([
        {"title": "PRIVATE_TITLE", "url": "https://example.com/private", "snippet": "PRIVATE_TEXT"},
    ]))
    monkeypatch.setattr(cli, "PerplexitySearch", lambda **_: client)
    result = CliRunner().invoke(app, ["discovery", "search", "--query", "one",
                                      "--query", "two", "--max-results", "1", "--smoke"])
    assert result.exit_code == 0, result.output
    value = json.loads(result.output)
    assert value["http_status"] == 200
    assert value["shape"]["result_fields"] == ["snippet", "title", "url"]
    assert "PRIVATE" not in result.output and "test-only-key" not in result.output


def test_cli_401_is_actionable_without_key_or_response_body(monkeypatch):
    monkeypatch.setattr(cli, "load_app_config", lambda _: None)
    client = client_for(lambda _: httpx.Response(401, json={"detail": "test-only-key"}))
    monkeypatch.setattr(cli, "PerplexitySearch", lambda **_: client)
    result = CliRunner().invoke(app, ["discovery", "search", "--query", "one", "--smoke"])
    assert result.exit_code == 1
    assert "401" in result.output and "console.perplexity.ai" in result.output
    assert "test-only-key" not in result.output
