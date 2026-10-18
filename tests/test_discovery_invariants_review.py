"""Independent checks of unit-cost accounting and source-grounded model output."""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from typer.testing import CliRunner

from job_intake.annotation.gold import _snapshot
from job_intake.annotation.text import TEXT_VERSION, source_hash
from job_intake.discovery import cli
from job_intake.discovery.artifacts import write_json, write_jsonl
from job_intake.discovery.report import build_report
from job_intake.discovery.semantic import GeminiReranker
from job_intake.discovery.service import Budget
from job_intake.models.job import JobRecord


class SemanticSession:
    def __init__(self, body):
        self.body = body
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        body = self.body

        class Response:
            status_code = 200

            def json(self):
                return body

        return Response()


def semantic_body(result=None, *, finish="STOP", usage=None):
    return {
        "candidates": [{"finishReason": finish, "content": {"parts": [
            {"text": json.dumps(result or {"priority": 80, "evidence_ids": ["duty-1"]})},
        ]}}],
        "usageMetadata": usage or {},
    }


def reranker(session):
    return GeminiReranker("private-key", "gemini-3.5-flash", input_per_million=1,
                          output_per_million=2, session=session)


def test_concurrent_reservations_never_exceed_shared_budget():
    budget = Budget(.05)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: budget.reserve(.01), range(100)))
    assert sum(entry is not None for entry in results) == 5
    assert budget.spent == pytest.approx(.05)
    assert sum(entry["cost_usd"] for entry in budget.entries) == pytest.approx(.05)


def test_nonbillable_validation_refunds_reservation():
    budget = Budget(.005)
    entry = budget.reserve(.005)
    budget.settle(entry, actual=0, status="unbilled")
    assert budget.reserve(.005) is not None
    assert budget.spent == pytest.approx(.005)


def test_semantic_cost_includes_thinking_tokens_without_creating_human_fields():
    session = SemanticSession(semantic_body(usage={
        "promptTokenCount": 100, "candidatesTokenCount": 20, "thoughtsTokenCount": 80,
    }))
    engine = reranker(session)
    rank, usage = engine.rank("source prompt", {"duty-1": "Lead pricing experiments."})
    assert usage["usage_known"] is True
    assert usage["output_tokens"] == 100
    assert usage["cost_usd"] == pytest.approx(.0003)
    assert not {"relevance", "role_label", "gate_outcome", "method"}.intersection(rank)
    assert rank["evidence"] == ["Lead pricing experiments."]
    generation = session.calls[0][1]["json"]["generationConfig"]
    assert generation["maxOutputTokens"] == engine.max_output_tokens
    assert session.calls[0][1]["allow_redirects"] is False


def test_missing_provider_usage_stays_uncertain_instead_of_zero_cost():
    engine = reranker(SemanticSession(semantic_body()))
    _, usage = engine.rank("source prompt", {"duty-1": "Lead pricing experiments."})
    assert usage["usage_known"] is False
    assert "cost_usd" not in usage
    assert engine.reserve_cost("source prompt") > 0


@pytest.mark.parametrize("result", [
    {"priority": 95, "evidence_ids": ["invented"]},
    {"priority": 95, "evidence_ids": []},
    {"priority": True, "evidence_ids": ["duty-1"]},
])
def test_semantic_priority_requires_attributable_candidate_evidence(result):
    engine = reranker(SemanticSession(semantic_body(result)))
    with pytest.raises(ValueError, match="reserved cost is retained"):
        engine.rank("source prompt", {"duty-1": "Lead pricing experiments."})


def test_truncated_semantic_answer_is_not_used_and_paid_call_is_not_repeated():
    session = SemanticSession(semantic_body(finish="MAX_TOKENS"))
    with pytest.raises(ValueError):
        reranker(session).rank("source prompt", {"duty-1": "Lead pricing experiments."})
    assert len(session.calls) == 1


def test_positive_with_unmeasured_human_work_has_unknown_human_unit_time(tmp_path):
    record = JobRecord(source="discovery", company="", title="Analytics Lead",
                       original_url="https://example.com/jobs/42",
                       description_clean="Lead pricing experiments and product decisions.")
    row = {"job_uid": "candidate-42", "source_hash": source_hash(record),
           "source": _snapshot(record), "priority": 80}
    write_jsonl(tmp_path / "candidates.jsonl", [row])
    write_jsonl(tmp_path / "labels.jsonl", [{
        "schema": "vacancy-tags-v2", "job_uid": row["job_uid"],
        "source_hash": row["source_hash"], "source": row["source"],
        "text_version": TEXT_VERSION, "relevance": 3,
        "method": "blind", "labeled_at": "2026-10-18",
        "role_label": "analytics-leadership", "gate_outcome": "unknown", "tags": {},
        "notes": "", "label_scope": "blind_decisions",
    }])
    write_json(tmp_path / "manifest.json", {
        "schema": "discovery-run-v1", "run_id": "test", "status": "completed",
        "duration_seconds": 10, "cost_usd": .005,
        "expenses": [{"cost_usd": .005, "status": "estimated"}],
    })
    report = build_report(tmp_path)
    assert report["counts"]["relevant"] == 1
    assert report["cost"]["usd_per_relevant"] == .005
    assert report["time"]["machine_seconds_per_relevant"] == 10
    assert report["time"]["reviewed_with_timing"] == 0
    assert report["time"]["human_seconds_per_relevant"] is None


def test_missing_search_key_is_clear_cli_error_before_network_and_artifacts(tmp_path, monkeypatch):
    monkeypatch.delenv("PERPLEXITY_API_KEY", raising=False)
    monkeypatch.setattr(cli, "_records", lambda *args: {})
    monkeypatch.setattr(cli, "run_discovery", lambda *args, **kwargs: pytest.fail(
        "Missing key must stop before any provider request or run output"))
    plan_path = tmp_path / "plan.json"
    plan_path.write_text("{}")
    output = tmp_path / "new-run"
    result = CliRunner().invoke(cli.app, [
        "run", "--plan", str(plan_path), "--output", str(output), "--budget-usd", "1",
    ])
    assert result.exit_code == 1
    assert "PERPLEXITY_API_KEY is required" in result.output
    assert "Traceback" not in result.output
    assert not output.exists()
