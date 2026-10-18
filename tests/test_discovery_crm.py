"""Discovery review uses saved source facts and accounts only committed human work."""

from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Thread

import pytest
from test_crm_server import _request

from job_intake.annotation import gold_ui
from job_intake.annotation.gold import _snapshot
from job_intake.annotation.gold_ui import GoldUIError
from job_intake.annotation.text import TEXT_VERSION, source_hash
from job_intake.config.settings import AppConfig, LLMConfig, TelegramConfig
from job_intake.crm.server import CRMHTTPServer, CRMService
from job_intake.discovery.review import DiscoveryLabelStore
from job_intake.models.job import JobRecord


def write_rows(path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


@pytest.fixture
def discovery(tmp_path, monkeypatch):
    monkeypatch.setattr(gold_ui, "configured_roles", lambda _: [
        {"id": "analytics-leadership", "name": "Analytics Leadership"},
    ])
    monkeypatch.setattr(gold_ui, "read_saved_records", lambda *args, **kwargs: pytest.fail(
        "Discovery source validation must never read the jobs database"))
    run = tmp_path / "run"
    run.mkdir()
    rules, profiles = tmp_path / "rules.yaml", tmp_path / "profiles.yaml"
    rules.write_text("{}")
    profiles.write_text("streams:\n  - id: analytics-leadership\n    name: Analytics Leadership\n")
    config = AppConfig(
        database_url=f"sqlite:///{tmp_path / 'missing-jobs.db'}", log_level="INFO", sources=[],
        rules_path=rules, search_profiles_path=profiles,
        company_watchlist_path=tmp_path / "companies.yaml", export_dir=tmp_path,
        telegram=TelegramConfig(), llm=LLMConfig(),
    )
    candidates, labels = [], []
    for index in range(2):
        record = JobRecord(source="web", company="Acme", title="Analytics Manager",
                           original_url=f"https://example.com/{index}",
                           description_clean=f"Lead pricing experiments and projects {index}.")
        candidate = {"job_uid": f"uid-{index}", "source_hash": source_hash(record),
                     "source": _snapshot(record), "priority": 999999,
                     "seed_ids": ["PRIVATE_SEED"], "evidence": ["MODEL_RANKING"],
                     "observations": [{"provider": "PRIVATE_PROVIDER"}]}
        candidates.append(candidate)
        labels.append({"schema": "vacancy-tags-v2", "job_uid": candidate["job_uid"],
                       "source_hash": candidate["source_hash"], "source": candidate["source"],
                       "text_version": TEXT_VERSION, "labeled_at": None, "method": None,
                       "label_scope": "blind_decisions", "tags": {}, "relevance": None,
                       "gate_outcome": None, "role_label": None, "notes": ""})
    write_rows(run / "candidates.jsonl", candidates)
    write_rows(run / "labels.jsonl", labels)
    return config, run, candidates, labels


def save_payload(store, uid="uid-0", **changes):
    return {"expected_version": store.detail(uid)["version"], "review_seconds": 24.5,
            "role_label": "analytics-leadership", "relevance": 3, "gate_outcome": "unknown",
            **changes}


def test_source_only_discovery_review_and_immutable_sources(discovery):
    config, run, _, _ = discovery
    store = DiscoveryLabelStore(config, run)
    sources_before = (run / "candidates.jsonl").read_bytes()
    detail = store.detail("uid-0")
    assert detail["can_save"] and detail["mode"] == "discovery"
    result = store.save("uid-0", save_payload(store))
    assert result["complete"] and result["labels"]["relevance"] == 3
    exposed = json.dumps([store.list_cases({}), result])
    for secret in ("999999", "PRIVATE_SEED", "PRIVATE_PROVIDER", "MODEL_RANKING"):
        assert secret not in exposed
    labels = [json.loads(line) for line in (run / "labels.jsonl").read_text().splitlines()]
    assert "review_seconds" not in labels[0]
    assert labels[0]["method"] == "blind"
    assert (run / "candidates.jsonl").read_bytes() == sources_before
    assert not (run.parent / "missing-jobs.db").exists()


@pytest.mark.parametrize("change", ["missing", "description", "company", "hash", "label"])
def test_candidate_source_drift_and_missing_prevent_saves(discovery, change):
    config, run, candidates, labels = discovery
    store = DiscoveryLabelStore(config, run)
    payload = save_payload(store)
    if change == "missing":
        (run / "candidates.jsonl").unlink()
    elif change == "label":
        labels[0]["source"]["company"] = "Altered label source"
        write_rows(run / "labels.jsonl", labels)
        payload["expected_version"] = store.detail("uid-0")["version"]
    else:
        if change == "description":
            candidates[0]["source"]["description_clean"] = "Different description"
        elif change == "company":
            candidates[0]["source"]["company"] = "Another company"
        else:
            candidates[0]["source_hash"] = "0" * 64
        write_rows(run / "candidates.jsonl", candidates)
    before = (run / "labels.jsonl").read_bytes()
    assert store.detail("uid-0")["can_save"] is False
    with pytest.raises(GoldUIError):
        store.save("uid-0", payload)
    assert (run / "labels.jsonl").read_bytes() == before
    assert not (run / "review-events.jsonl").exists()


def test_frozen_batch_digest_rejects_any_candidate_file_edit(discovery):
    config, run, candidates, _ = discovery
    digest = hashlib.sha256((run / "candidates.jsonl").read_bytes()).hexdigest()
    (run / "batch.json").write_text(json.dumps({"candidates_sha256": digest}))
    store = DiscoveryLabelStore(config, run)
    payload = save_payload(store)
    candidates[0]["priority"] = 1  # Even non-source edits violate the frozen pool.
    write_rows(run / "candidates.jsonl", candidates)
    assert store.detail("uid-0")["can_save"] is False
    with pytest.raises(GoldUIError):
        store.save("uid-0", payload)


def test_repeat_requests_no_change_and_concurrent_retries_do_not_double_time(discovery):
    config, run, _, _ = discovery
    store = DiscoveryLabelStore(config, run)
    payload = save_payload(store)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: DiscoveryLabelStore(config, run).save("uid-0", payload),
                                range(2)))
    assert sum(result["changed"] for result in results) == 1
    assert sum(bool(result.get("replayed")) for result in results) == 1
    assert store.save("uid-0", save_payload(store, review_seconds=30))["changed"] is False
    events = [json.loads(line) for line in (run / "review-events.jsonl").read_text().splitlines()]
    assert len(events) == 1 and events[0]["seconds"] == 24.5
    assert events[0]["version"] == store.detail("uid-0")["version"]


def test_concurrent_different_rows_merge_labels_and_time(discovery):
    config, run, _, _ = discovery
    store = DiscoveryLabelStore(config, run)
    payloads = [save_payload(store, f"uid-{index}", review_seconds=index + 1) for index in range(2)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda index: DiscoveryLabelStore(config, run).save(
            f"uid-{index}", payloads[index]), range(2)))
    assert all(row["complete"] for row in results)
    assert store.list_cases({})["stats"]["relevant"] == 2
    assert sum(json.loads(line)["seconds"] for line in (
        run / "review-events.jsonl").read_text().splitlines()) == 3


@pytest.mark.parametrize("seconds", [-1, 1801, True, None, "50", float("nan"), float("inf")])
def test_review_seconds_are_finite_bounded_numbers(discovery, seconds):
    config, run, _, _ = discovery
    store = DiscoveryLabelStore(config, run)
    with pytest.raises(GoldUIError):
        store.save("uid-0", save_payload(store, review_seconds=seconds))
    assert not (run / "review-events.jsonl").exists()


def test_saved_label_event_is_recovered_after_interrupted_event_append(discovery, monkeypatch):
    config, run, _, _ = discovery
    store = DiscoveryLabelStore(config, run)
    payload = save_payload(store)
    original = store._append_event
    monkeypatch.setattr(store, "_append_event", lambda _: (_ for _ in ()).throw(OSError("disk")))
    with pytest.raises(GoldUIError):
        store.save("uid-0", payload)
    assert store.detail("uid-0")["labels"]["relevance"] == 3
    assert (run / ".review-pending.json").exists()
    monkeypatch.setattr(store, "_append_event", original)
    assert store.save("uid-0", payload)["replayed"] is True
    assert len((run / "review-events.jsonl").read_text().splitlines()) == 1
    assert not (run / ".review-pending.json").exists()


def test_crm_discovery_binding_and_gold_exclusion(discovery):
    config, run, _, _ = discovery
    with pytest.raises(ValueError, match="одновременно"):
        CRMService(config, gold_path=run / "labels.jsonl", discovery_path=run)
    crm_config = replace(config, database_url=f"sqlite:///{run.parent / 'crm-test.db'}")
    service = CRMService(crm_config, discovery_path=run)
    assert service.state({})["labeling_mode"] == "discovery"
    assert service.gold_case("uid-0")["mode"] == "discovery"
    assert service.mutate("PATCH", "/api/gold/uid-0", save_payload(service.gold_store))["complete"]


def test_discovery_http_blind_labels_and_report(discovery, monkeypatch):
    from job_intake.discovery import report

    config, run, _, _ = discovery
    service = CRMService(config, discovery_path=run)
    aggregate = {"counts": {"relevant": 1}, "cost": {"total_usd": 0.5}, "time": {}}
    monkeypatch.setattr(report, "build_report", lambda path: aggregate if path == run else None)
    server = CRMHTTPServer(service, port=0)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, detail = _request(server, "GET", "/api/gold/uid-0")
        assert status == 200 and detail["mode"] == "discovery"
        assert "PRIVATE_SEED" not in json.dumps(detail)
        payload = save_payload(service.gold_store)
        assert _request(server, "PATCH", "/api/gold/uid-0", payload)[0] == 403
        assert _request(server, "PATCH", "/api/gold/uid-0", payload,
                        **{"X-CSRF-Token": service.csrf_token, "Origin": server.origin})[0] == 200
        assert _request(server, "GET", "/api/discovery/report") == (200, aggregate)
        assert _request(server, "GET", "/api/discovery/report?bad=1")[0] == 400
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
