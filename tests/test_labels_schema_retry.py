"""One budgeted, ledgered retry when a complete provider reply breaks the schema."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from test_labels_tagger import config as config
from test_labels_tagger import label, payload, service
from test_labels_tagger import record as record
from test_labels_tagger import streams as streams

from job_intake.labeling.ledger import LedgerWriter
from job_intake.labeling.tagger import SCHEMA_RETRIES


def test_invalid_reply_is_retried_once_and_only_the_valid_reply_is_cached(
    config, streams, record, tmp_path,
):
    config = replace(config, escalation=replace(config.escalation, enabled=False))
    path = tmp_path / "attempts.jsonl"
    ledger = LedgerWriter(path)
    events = []
    tagger = service(config, streams, [{"broken": True}, payload(record, relevance=2)],
                     ledger=ledger)
    tagger.set_progress(events.append)
    value = label(tagger, record, use_ai=True)
    ledger.close()
    assert SCHEMA_RETRIES == 1
    assert value.status == "labeled" and value.relevance == 2 and not value.errors
    assert tagger.client.calls == ["tag", "tag"]
    assert tagger.stats["schema_retries"] == 1 and tagger.stats["calls"] == 2
    assert [event["event"] for event in events].count("label_schema_retry") == 1
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(rows) == 2 and len({row["call_id"] for row in rows}) == 2
    assert all(row["usage"]["input_tokens"] == 100 for row in rows)
    # Both complete replies are billed: 2 × (100 input + 50 output) at $1 per million.
    assert abs(tagger.budget.spent - 300 / 1_000_000) < 1e-12
    assert len(list(Path(config.cache_dir).rglob("*.json"))) == 1


def test_second_invalid_reply_keeps_rules_fallback_without_cache(config, streams, record):
    tagger = service(config, streams, [{"broken": 1}, {"broken": 2}, payload(record)])
    value = label(tagger, record, use_ai=True)
    assert value.status == "error" and value.errors[0]["code"] == "schema_violation"
    assert tagger.client.calls == ["tag", "tag"]
    assert not list(Path(config.cache_dir).rglob("*.json"))


def test_escalation_stage_also_retries_its_invalid_reply(config, streams, record):
    first = payload(record, relevance=3)
    tagger = service(config, streams, [first, {"broken": True}, payload(record, relevance=3)])
    value = label(tagger, record, use_ai=True)
    assert tagger.client.calls == ["tag", "escalate", "escalate"]
    assert value.status == "labeled" and value.escalated and not value.errors
