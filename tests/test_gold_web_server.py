"""Gold editing through the actual CRM HTTP boundary, isolated from personal data."""

from __future__ import annotations

import hashlib
import json

from test_crm_audit_fixes import seed_jobs
from test_crm_server import _request
from test_crm_server import running_server as running_server
from test_crm_server import service as service

from job_intake.annotation.eval_runner import read_saved_records, sqlite_path
from job_intake.annotation.gold import _snapshot
from job_intake.annotation.text import TEXT_VERSION, source_hash


def seed_gold(service):
    uids = seed_jobs(service, count=2, description="Responsibilities: Lead analytics projects.")
    _, records, problems = read_saved_records(
        service.config.database_url, limit=None, job_uids=uids,
    )
    assert not problems
    rows = [{
        "schema": "vacancy-tags-v2", "job_uid": uid, "source_hash": source_hash(record),
        "text_version": TEXT_VERSION, "source": _snapshot(record), "role_label": None,
        "relevance": 3 if uid == uids[0] else None, "gate_outcome": None,
        "method": "assisted" if uid == uids[0] else None,
        "labeled_at": "2026-10-17" if uid == uids[0] else None, "tags": {}, "notes": "",
    } for uid, record in records]
    path = sqlite_path(service.config.database_url).parent / "gold/vacancy-tags-v2-labeled.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    return path, uids


def headers(server):
    status, state = _request(server, "GET", "/api/state?include_jobs=false")
    assert status == 200
    return {"X-CSRF-Token": state["csrf_token"], "Origin": server.origin}


def test_http_gold_partial_edit_persists_without_changing_sqlite(running_server):
    server = running_server
    path, uids = seed_gold(server.service)
    before = path.read_bytes()
    db = sqlite_path(server.service.config.database_url)
    db_hash = hashlib.sha256(db.read_bytes()).hexdigest()
    status, listing = _request(server, "GET", "/api/gold?status=partial")
    assert status == 200
    assert listing["stats"]["total"] == 2
    assert listing["stats"]["partial"] == 1
    assert listing["stats"]["labeled"] == 0
    assert [row["job_uid"] for row in listing["cases"]] == [uids[0]]
    route = f"/api/gold/{uids[0]}"
    status, detail = _request(server, "GET", route)
    assert status == 200
    assert detail["labels"]["relevance"] == 3
    assert detail["can_save"]
    assert not {"annotation", "tier", "score", "role_classification"} & set(detail["source"])
    status, saved = _request(server, "PATCH", route, {
        "expected_version": detail["version"], "role_label": "product",
        "gate_outcome": "unknown", "notes": "Human decision",
    }, **headers(server))
    assert status == 200
    assert saved["complete"]
    assert saved["labels"]["relevance"] == 3
    assert saved["labels"]["notes"] == "Human decision"
    assert saved["version"] != detail["version"]
    assert _request(server, "GET", route)[1]["labels"] == saved["labels"]
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    updated = next(row for row in rows if row["job_uid"] == uids[0])
    assert updated["method"] == "assisted"
    assert path.read_bytes() != before
    assert hashlib.sha256(db.read_bytes()).hexdigest() == db_hash


def test_http_gold_requires_csrf_and_preserves_file_on_invalid_or_stale_save(running_server):
    server = running_server
    path, uids = seed_gold(server.service)
    route = f"/api/gold/{uids[1]}"
    detail = _request(server, "GET", route)[1]
    payload = {"expected_version": detail["version"], "relevance": 0}
    before = path.read_bytes()
    assert _request(server, "PATCH", route, payload)[0] == 403
    auth = headers(server)
    assert _request(server, "PATCH", route, payload,
                    **{**auth, "Origin": "https://other.example"})[0] == 403
    for extra in ({"relevance": True}, {"tier": "A"}, {"role_label": "missing"}):
        assert _request(server, "PATCH", route, {**payload, **extra}, **auth)[0] == 400
        assert path.read_bytes() == before
    status, saved = _request(server, "PATCH", route, payload, **auth)
    assert status == 200
    assert saved["labels"]["relevance"] == 0
    after = path.read_bytes()
    assert _request(server, "PATCH", route, {**payload, "relevance": 3}, **auth)[0] == 409
    assert path.read_bytes() == after


def test_missing_gold_does_not_prevent_normal_crm_use(running_server):
    assert _request(running_server, "GET", "/api/state?include_jobs=false")[0] == 200
    status, result = _request(running_server, "GET", "/api/gold")
    assert status == 404
    assert result["error"]


def test_http_gold_rejects_extra_parameters_and_wrong_route_method(running_server):
    _, uids = seed_gold(running_server.service)
    assert _request(running_server, "GET", "/api/gold?path=/etc/passwd")[0] == 400
    assert _request(running_server, "GET", f"/api/gold/{uids[0]}?x=1")[0] == 400
    assert _request(running_server, "GET", "/api/gold?status=complete&status=all")[0] == 400
    assert _request(running_server, "POST", f"/api/gold/{uids[0]}", {},
                    **headers(running_server))[0] == 404
