import json
from pathlib import Path

import pytest

from job_intake.config.settings import LLMConfig
from job_intake.models.job import (
    EvaluatedJob,
    FilterDecision,
    JobEvaluation,
    JobRecord,
)
from job_intake.scoring.llm import OpenAIReranker

PROMPT_PATH = str(Path(__file__).resolve().parents[1] / "config" / "llm_prompt.txt")


class _FakeUsage:
    input_tokens = 100
    output_tokens = 20


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self.output_text = text
        self.usage = _FakeUsage()


class _FakeResponses:
    def __init__(self, text: str) -> None:
        self._text = text
        self.last_prompt: str | None = None

    def create(self, *, model, input, **kwargs):  # noqa: A002 - mirror SDK signature
        self.last_prompt = input
        return _FakeResponse(self._text)


class _FakeClient:
    def __init__(self, text: str) -> None:
        self.responses = _FakeResponses(text)


def _install_fake_openai(monkeypatch: pytest.MonkeyPatch, response_text: str) -> _FakeClient:
    client = _FakeClient(response_text)

    class _FakeOpenAI:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __new__(cls, *args, **kwargs):
            return client

    monkeypatch.setattr("openai.OpenAI", _FakeOpenAI, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    return client


def _passing_job(description: str) -> EvaluatedJob:
    record = JobRecord(
        source="test",
        company="Example",
        title="Product Analytics Lead",
        original_url="https://example.com/job",
        description_clean=description,
    )
    evaluation = JobEvaluation(decision=FilterDecision.PASS, fit_score=10.0)
    return EvaluatedJob(record=record, evaluation=evaluation)


def _reranker() -> OpenAIReranker:
    return OpenAIReranker(LLMConfig(enabled=True, prompt_path=PROMPT_PATH))


def test_description_with_braces_does_not_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = json.dumps(
        {"semantic_score": 2.0, "bridge_role": True, "fit_reason": "ok", "risks": []}
    )
    client = _install_fake_openai(monkeypatch, payload)
    job = _passing_job("Own pricing config like {reward: 100} and {experiment}.")

    result = _reranker().rerank(job)

    assert result.evaluation.semantic_score == 2.0
    assert "{reward: 100}" in client.responses.last_prompt


def test_malformed_json_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_openai(monkeypatch, "sorry, here is prose not json")
    job = _passing_job("Standard analytics leadership role.")

    result = _reranker().rerank(job)

    assert result.evaluation.semantic_score is None
    assert result.evaluation.fit_score == 10.0  # unchanged
    assert any("failed" in entry.lower() for entry in result.evaluation.audit_log)


def test_semantic_score_is_clamped(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = json.dumps(
        {"semantic_score": 100.0, "bridge_role": False, "fit_reason": "", "risks": []}
    )
    _install_fake_openai(monkeypatch, payload)
    job = _passing_job("Standard analytics leadership role.")

    result = _reranker().rerank(job)

    assert result.evaluation.semantic_score == 6.0  # clamped to semantic_score_max
    assert result.evaluation.fit_score == 16.0


def test_markdown_fenced_json_is_parsed(monkeypatch: pytest.MonkeyPatch) -> None:
    body = json.dumps({"semantic_score": 1.5, "bridge_role": False, "risks": []})
    payload = f"```json\n{body}\n```"
    _install_fake_openai(monkeypatch, payload)
    job = _passing_job("Standard analytics leadership role.")

    result = _reranker().rerank(job)

    assert result.evaluation.semantic_score == 1.5
