from __future__ import annotations

import json
import logging
import math
import os
import time
from pathlib import Path
from typing import Any

from job_intake.config.settings import LLMConfig
from job_intake.models.job import EvaluatedJob, FilterDecision, JobEvaluation
from job_intake.utils.text import strip_boilerplate

LOGGER = logging.getLogger(__name__)


def _strip_json_fence(text: str) -> str:
    """Remove an optional ```json ... ``` markdown fence around a JSON payload."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[-1] if "\n" in stripped else stripped
        if stripped.endswith("```"):
            stripped = stripped[: -3]
    return stripped.strip()


def should_skip_llm(
    evaluation: JobEvaluation,
    threshold_a: float,
    threshold_b: float,
    margin: float,
) -> bool:
    """Skip the LLM when the deterministic outcome is already unambiguous.

    Avoids paying for reranking on jobs the semantic pass cannot realistically move:
    a clear A (well above ``threshold_a`` and a bridge role) or a clear non-bridge
    reject/low-fit (well below ``threshold_b`` and no bridge signal).
    """
    score = evaluation.deterministic_score
    clear_a = score >= threshold_a + margin and evaluation.bridge_role
    clear_low = score < threshold_b - margin and not evaluation.bridge_role
    return clear_a or clear_low


def _apply_semantic_payload(
    evaluation: JobEvaluation,
    payload: dict[str, Any],
    config: LLMConfig,
) -> float:
    """Apply a parsed rerank payload to an evaluation (shared by sync and batch).

    Clamps ``semantic_score`` to the advertised range so a misbehaving model cannot
    skew tiers, then folds it into the fit score and merges bridge/reason/risks.
    """
    if not isinstance(payload, dict):
        raise ValueError("Semantic payload must be a JSON object.")
    raw_score = payload.get("semantic_score", 0.0)
    if isinstance(raw_score, bool) or not isinstance(raw_score, (int, float)):
        raise ValueError("semantic_score must be a finite number.")
    semantic_score = float(raw_score)
    if not math.isfinite(semantic_score):
        raise ValueError("semantic_score must be a finite number.")
    bridge_role = payload.get("bridge_role", False)
    if not isinstance(bridge_role, bool):
        raise ValueError("bridge_role must be a JSON boolean.")
    explanation = payload.get("fit_reason", "")
    if not isinstance(explanation, str):
        raise ValueError("fit_reason must be a string.")
    risks = payload.get("risks", [])
    if not isinstance(risks, list) or any(not isinstance(item, str) for item in risks):
        raise ValueError("risks must be an array of strings.")
    # Validate the entire payload before changing any deterministic field.
    semantic_score = max(
        min(semantic_score, config.semantic_score_max),
        config.semantic_score_min,
    )
    explanation = explanation.strip()

    evaluation.semantic_score = semantic_score
    evaluation.fit_score += semantic_score
    evaluation.bridge_role = evaluation.bridge_role or bridge_role
    if explanation:
        evaluation.fit_reason = explanation
    evaluation.risks = sorted(set(evaluation.risks + risks))
    evaluation.audit_log.append(f"Semantic rerank added {semantic_score:.2f} points.")
    return semantic_score


def _extract_output_text(body: dict[str, Any]) -> str:
    """Pull the model text out of a raw Responses API body (batch output line).

    Prefers the SDK convenience ``output_text`` when present, otherwise walks the
    ``output[].content[]`` array for the first ``output_text`` chunk.
    """
    if not isinstance(body, dict):
        return ""
    if body.get("output_text"):
        return str(body["output_text"])
    for item in body.get("output", []) or []:
        for chunk in item.get("content", []) or []:
            if chunk.get("type") in {"output_text", "text"} and chunk.get("text"):
                return str(chunk["text"])
    return ""


def render_prompt(template: str, config: LLMConfig, job: EvaluatedJob) -> str:
    """Render the rerank prompt via literal replacement.

    ``str.format`` would raise on job descriptions containing literal ``{``/``}``
    (common in engineering postings), so substitute known placeholders directly.
    Optionally strips boilerplate first to save tokens.
    """
    description = job.record.description_clean or job.record.description_raw
    if config.strip_boilerplate:
        description = strip_boilerplate(description)
    description = description[: config.max_description_chars]
    LOGGER.debug("llm_prompt_description_est_tokens=%s", len(description) // 4)
    replacements = {
        "{title}": job.record.title,
        "{company}": job.record.company,
        "{location}": job.record.location_text or "",
        "{remote_text}": job.record.remote_text or "",
        "{employment_type}": job.record.employment_type or "",
        "{timezone_text}": job.record.timezone_text or "",
        "{description}": description,
        "{profile_id}": getattr(job, "profile_id", "default"),
        "{profile_name}": getattr(job, "profile_name", "") or "Default profile",
        "{profile_version}": getattr(job, "profile_version", ""),
        "{profile_context}": getattr(job, "profile_context", "") or (
            "Assess fit to the configured role-family signals. Do not infer a candidate profile."
        ),
    }
    prompt = template
    for placeholder, value in replacements.items():
        prompt = prompt.replace(placeholder, value)
    return prompt


class SemanticReranker:
    def rerank(self, job: EvaluatedJob) -> EvaluatedJob:
        raise NotImplementedError


class NullReranker(SemanticReranker):
    def rerank(self, job: EvaluatedJob) -> EvaluatedJob:
        job.evaluation.audit_log.append("Semantic rerank skipped.")
        return job


class OpenAIReranker(SemanticReranker):
    def __init__(self, config: LLMConfig) -> None:
        self.config = config
        self.prompt_template = Path(config.prompt_path).read_text(encoding="utf-8")

    def _render_prompt(self, job: EvaluatedJob) -> str:
        return render_prompt(self.prompt_template, self.config, job)

    def rerank(self, job: EvaluatedJob) -> EvaluatedJob:
        if job.evaluation.decision == FilterDecision.REJECT:
            job.evaluation.audit_log.append("Semantic rerank not allowed for hard-rejected job.")
            return job

        api_key = os.getenv(self.config.api_key_env)
        if not api_key:
            job.evaluation.audit_log.append("Semantic rerank skipped because API key is missing.")
            return job

        try:
            from openai import OpenAI

            client = OpenAI(api_key=api_key, timeout=self.config.request_timeout)
            prompt = self._render_prompt(job)
            response = client.responses.create(
                model=self.config.model,
                input=prompt,
                reasoning={"effort": self.config.reasoning_effort},
                max_output_tokens=self.config.max_output_tokens,
                text={"format": {"type": "json_object"}},
            )
            usage = getattr(response, "usage", None)
            if usage is not None:
                LOGGER.info(
                    "llm_rerank_usage input_tokens=%s output_tokens=%s",
                    getattr(usage, "input_tokens", None),
                    getattr(usage, "output_tokens", None),
                )
            payload = json.loads(_strip_json_fence(response.output_text))
            _apply_semantic_payload(job.evaluation, payload, self.config)
        except Exception as exc:  # noqa: BLE001 - any failure must fall back deterministically
            LOGGER.warning("llm_rerank_failed error=%s", exc)
            job.evaluation.audit_log.append(
                f"Semantic rerank failed, kept deterministic result: {exc}"
            )
            return job

        return job


class BatchReranker:
    """Rerank a batch of non-rejected jobs in one OpenAI Batch API job (≈50% cheaper).

    Opt-in and asynchronous by nature: it submits all requests together, polls until
    the batch completes, then applies the parsed scores. Suited to the nightly digest
    path rather than instant alerts — it blocks up to ``batch_max_wait`` seconds. On
    any error or timeout the jobs keep their deterministic result (``semantic_score``
    stays ``None``) so a later run retries them.
    """

    def __init__(self, config: LLMConfig) -> None:
        self.config = config
        self.prompt_template = Path(config.prompt_path).read_text(encoding="utf-8")

    def _build_request_line(self, custom_id: str, job: EvaluatedJob) -> dict[str, Any]:
        return {
            "custom_id": custom_id,
            "method": "POST",
            "url": "/v1/responses",
            "body": {
                "model": self.config.model,
                "input": render_prompt(self.prompt_template, self.config, job),
                "reasoning": {"effort": self.config.reasoning_effort},
                "max_output_tokens": self.config.max_output_tokens,
                "text": {"format": {"type": "json_object"}},
            },
        }

    def rerank_many(self, jobs: list[EvaluatedJob]) -> list[EvaluatedJob]:
        passing = [job for job in jobs if job.evaluation.decision != FilterDecision.REJECT]
        if not passing:
            return jobs

        api_key = os.getenv(self.config.api_key_env)
        if not api_key:
            for job in passing:
                job.evaluation.audit_log.append("Batch rerank skipped because API key is missing.")
            return jobs

        try:
            from openai import OpenAI

            client = OpenAI(api_key=api_key, timeout=self.config.request_timeout)
            index = {str(pos): job for pos, job in enumerate(passing)}
            jsonl = "\n".join(
                json.dumps(self._build_request_line(cid, job)) for cid, job in index.items()
            )
            upload = client.files.create(
                file=("batch_rerank.jsonl", jsonl.encode("utf-8")),
                purpose="batch",
            )
            batch = client.batches.create(
                input_file_id=upload.id,
                endpoint="/v1/responses",
                completion_window=self.config.batch_completion_window,
            )
            batch = self._poll(client, batch.id)
            if getattr(batch, "status", None) != "completed":
                raise RuntimeError(f"batch ended with status={getattr(batch, 'status', None)}")

            results = self._parse_output(client, batch.output_file_id)
        except Exception as exc:  # noqa: BLE001 - fall back deterministically for the whole batch
            LOGGER.warning("batch_rerank_failed error=%s", exc)
            for job in passing:
                job.evaluation.audit_log.append(
                    f"Batch rerank failed, kept deterministic result: {exc}"
                )
            return jobs

        applied = 0
        for custom_id, payload in results.items():
            job = index.get(custom_id)
            if job is not None and payload is not None:
                try:
                    _apply_semantic_payload(job.evaluation, payload, self.config)
                    applied += 1
                except (TypeError, ValueError) as exc:
                    LOGGER.warning(
                        "batch_rerank_payload_invalid custom_id=%s error=%s", custom_id, exc
                    )
                    job.evaluation.audit_log.append(
                        f"Batch rerank failed, kept deterministic result: {exc}"
                    )
        LOGGER.info("batch_rerank_applied count=%s", applied)
        return jobs

    def _poll(self, client, batch_id: str):
        deadline = time.monotonic() + self.config.batch_max_wait
        batch = client.batches.retrieve(batch_id)
        while getattr(batch, "status", None) in {"validating", "in_progress", "finalizing"}:
            if time.monotonic() >= deadline:
                raise TimeoutError("batch polling exceeded batch_max_wait")
            time.sleep(self.config.batch_poll_interval)
            batch = client.batches.retrieve(batch_id)
        return batch

    def _parse_output(self, client, output_file_id: str) -> dict[str, Any]:
        content = client.files.content(output_file_id)
        text = getattr(content, "text", None) or content.read().decode("utf-8")
        results: dict[str, Any] = {}
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
                custom_id = record.get("custom_id")
                body = (record.get("response") or {}).get("body", {})
                output_text = _extract_output_text(body)
                parsed = json.loads(_strip_json_fence(output_text)) if output_text else None
            except (json.JSONDecodeError, TypeError, AttributeError) as exc:
                LOGGER.warning("batch_rerank_output_invalid error=%s", exc)
                # Keep partial successes when a single output line is malformed.
                continue
            if not isinstance(custom_id, str):
                continue
            results[custom_id] = parsed
        return results


def build_reranker(config: LLMConfig) -> SemanticReranker:
    if not config.enabled:
        return NullReranker()
    return OpenAIReranker(config)
