import json

import pytest
from requests import Response
from requests.adapters import BaseAdapter

from job_intake.utils.http import build_session, fetch_json, fetch_text


class RedirectAdapter(BaseAdapter):
    def __init__(self, location):
        self.locations = location if isinstance(location, list) else [location]
        self.requests = []

    def send(self, request, **kwargs):
        self.requests.append(request)
        response = Response()
        response.request = request
        response.url = request.url
        if len(self.requests) <= len(self.locations):
            response.status_code = 302
            response.headers["Location"] = self.locations[len(self.requests) - 1]
            response._content = b""
        else:
            response.status_code = 200
            response._content = json.dumps({"ok": True}).encode()
        return response

    def close(self):
        pass


@pytest.mark.parametrize("location", [
    "https://attacker.invalid/end", "http://source.invalid/end",
    "https://source.invalid:444/end",
])
@pytest.mark.parametrize("fetch", [fetch_text, fetch_json])
def test_redirect_removes_arbitrary_secret_headers_before_sending(location, fetch):
    with build_session({"X-API-Key": "synthetic-secret", "X-Custom-Credential": "secret",
                        "Authorization": "Bearer secret"}) as session:
        session.trust_env = False
        adapter = RedirectAdapter(location)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        fetch(session, "https://source.invalid/start")
    assert adapter.requests[0].headers["X-API-Key"] == "synthetic-secret"
    forwarded = adapter.requests[1].headers
    assert "X-API-Key" not in forwarded
    assert "X-Custom-Credential" not in forwarded
    assert "Authorization" not in forwarded
    assert "User-Agent" in forwarded


@pytest.mark.parametrize("location", ["/end", "https://SOURCE.invalid:443/end"])
def test_same_origin_redirect_keeps_credentials(location):
    with build_session({"X-API-Key": "synthetic-secret"}) as session:
        session.trust_env = False
        adapter = RedirectAdapter(location)
        session.mount("https://", adapter)
        fetch_text(session, "https://source.invalid/start")
    assert adapter.requests[1].headers["X-API-Key"] == "synthetic-secret"


def test_later_direct_request_does_not_reuse_source_credentials_on_other_origin():
    with build_session({"X-API-Key": "synthetic-secret"}) as session:
        session.trust_env = False
        adapter = RedirectAdapter("/end")
        session.mount("https://", adapter)
        fetch_text(session, "https://source.invalid/start")
        fetch_text(session, "https://other.invalid/end")
    assert "X-API-Key" not in adapter.requests[-1].headers


def test_explicitly_configured_standard_header_is_also_origin_bound():
    with build_session({"Accept": "synthetic-private-value"}) as session:
        session.trust_env = False
        adapter = RedirectAdapter("https://other.invalid/end")
        session.mount("https://", adapter)
        fetch_text(session, "https://source.invalid/start")
    assert adapter.requests[0].headers["Accept"] == "synthetic-private-value"
    assert "Accept" not in adapter.requests[1].headers


def test_domain_cookie_is_removed_on_every_foreign_origin_redirect():
    with build_session({"X-API-Key": "synthetic-key", "Accept": "private-value"}) as session:
        session.trust_env = False
        session.cookies.set(
            "session_token", "synthetic-secret", domain=".example.invalid", path="/", secure=True,
        )
        adapter = RedirectAdapter([
            "https://attacker.example.invalid/step", "/end",
        ])
        session.mount("https://", adapter)
        fetch_text(session, "https://source.example.invalid/start")
    assert adapter.requests[0].headers["Cookie"] == "session_token=synthetic-secret"
    assert len(adapter.requests) == 3
    for request in adapter.requests[1:]:
        assert "Cookie" not in request.headers
        assert "X-API-Key" not in request.headers
        assert "Accept" not in request.headers


def test_request_specific_headers_are_origin_bound_through_multiple_redirects():
    with build_session() as session:
        session.trust_env = False
        adapter = RedirectAdapter(["https://other.invalid/step", "/end"])
        session.mount("https://", adapter)
        session.get("https://source.invalid/start", headers={
            "Accept": "private-value", "X-API-Key": "synthetic-key",
        })
    assert adapter.requests[0].headers["Accept"] == "private-value"
    for request in adapter.requests[1:]:
        assert "Accept" not in request.headers
        assert "X-API-Key" not in request.headers
