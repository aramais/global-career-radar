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
from job_intake.scoring.llm import (
    BatchReranker,
    OpenAIReranker,
    _extract_output_text,
    should_skip_llm,
)

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


# --- P3-2 boilerplate strip ---------------------------------------------------


def test_boilerplate_stripped_from_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = json.dumps({"semantic_score": 1.0, "bridge_role": False, "risks": []})
    client = _install_fake_openai(monkeypatch, payload)
    job = _passing_job(
        "Lead pricing experimentation for the marketplace.\n"
        "We are an equal opportunity employer.\n"
        "How to apply: click apply now."
    )

    _reranker().rerank(job)

    sent = client.responses.last_prompt
    assert "Lead pricing experimentation" in sent
    assert "equal opportunity" not in sent
    assert "click apply" not in sent


# --- P3-4 heuristic gating ----------------------------------------------------


def _evaluation(score: float, bridge: bool) -> JobEvaluation:
    return JobEvaluation(
        decision=FilterDecision.PASS,
        deterministic_score=score,
        fit_score=score,
        bridge_role=bridge,
    )


def test_should_skip_llm_clear_a() -> None:
    # score well above threshold_a (14) + margin (4) and a bridge role -> already A.
    assert should_skip_llm(_evaluation(20.0, True), 14.0, 8.0, 4.0) is True


def test_should_skip_llm_clear_low() -> None:
    # score well below threshold_b (8) - margin (4) and no bridge -> already low.
    assert should_skip_llm(_evaluation(2.0, False), 14.0, 8.0, 4.0) is True


def test_should_not_skip_llm_borderline() -> None:
    assert should_skip_llm(_evaluation(12.0, True), 14.0, 8.0, 4.0) is False


# --- P3-1 batch reranking -----------------------------------------------------


def test_extract_output_text_both_shapes() -> None:
    assert _extract_output_text({"output_text": "hi"}) == "hi"
    walked = {"output": [{"content": [{"type": "output_text", "text": "deep"}]}]}
    assert _extract_output_text(walked) == "deep"
    assert _extract_output_text({}) == ""


class _FakeFiles:
    def __init__(self, output_text: str) -> None:
        self._output_text = output_text

    def create(self, *, file, purpose):
        return type("F", (), {"id": "file-in"})()

    def content(self, file_id: str):
        return type("C", (), {"text": self._output_text})()


class _FakeBatches:
    def create(self, *, input_file_id, endpoint, completion_window):
        return type("B", (), {"id": "batch-1", "status": "validating"})()

    def retrieve(self, batch_id: str):
        attrs = {"id": batch_id, "status": "completed", "output_file_id": "file-out"}
        return type("B", (), attrs)()


class _FakeBatchClient:
    def __init__(self, output_text: str) -> None:
        self.files = _FakeFiles(output_text)
        self.batches = _FakeBatches()


def _install_fake_batch_openai(monkeypatch: pytest.MonkeyPatch, output_lines: list[dict]) -> None:
    output_text = "\n".join(json.dumps(line) for line in output_lines)
    client = _FakeBatchClient(output_text)

    class _FakeOpenAI:
        def __new__(cls, *args, **kwargs):
            return client

    monkeypatch.setattr("openai.OpenAI", _FakeOpenAI, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")


def test_batch_reranker_applies_scores(monkeypatch: pytest.MonkeyPatch) -> None:
    jobs = [_passing_job("Role one."), _passing_job("Role two.")]
    output_lines = [
        {
            "custom_id": "0",
            "response": {"body": {"output_text": json.dumps({"semantic_score": 3.0})}},
        },
        {
            "custom_id": "1",
            "response": {"body": {"output_text": json.dumps({"semantic_score": 5.0})}},
        },
    ]
    _install_fake_batch_openai(monkeypatch, output_lines)

    reranker = BatchReranker(LLMConfig(enabled=True, batch_enabled=True, prompt_path=PROMPT_PATH))
    result = reranker.rerank_many(jobs)

    assert result[0].evaluation.semantic_score == 3.0
    assert result[1].evaluation.semantic_score == 5.0
    assert result[0].evaluation.fit_score == 13.0


def test_batch_reranker_missing_key_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    jobs = [_passing_job("Role one.")]

    reranker = BatchReranker(LLMConfig(enabled=True, batch_enabled=True, prompt_path=PROMPT_PATH))
    result = reranker.rerank_many(jobs)

    assert result[0].evaluation.semantic_score is None
    assert any("skipped" in entry.lower() for entry in result[0].evaluation.audit_log)
