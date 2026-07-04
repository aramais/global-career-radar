from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from job_intake.config.settings import LLMConfig
from job_intake.models.job import EvaluatedJob, FilterDecision

LOGGER = logging.getLogger(__name__)


def _strip_json_fence(text: str) -> str:
    """Remove an optional ```json ... ``` markdown fence around a JSON payload."""
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[-1] if "\n" in stripped else stripped
        if stripped.endswith("```"):
            stripped = stripped[: -3]
    return stripped.strip()


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
        """Render the prompt via literal replacement.

        ``str.format`` would raise on job descriptions containing literal ``{``/``}``
        (common in engineering postings), so substitute known placeholders directly.
        """
        description = (job.record.description_clean or job.record.description_raw)[
            : self.config.max_description_chars
        ]
        replacements = {
            "{title}": job.record.title,
            "{company}": job.record.company,
            "{location}": job.record.location_text or "",
            "{remote_text}": job.record.remote_text or "",
            "{employment_type}": job.record.employment_type or "",
            "{timezone_text}": job.record.timezone_text or "",
            "{description}": description,
        }
        prompt = self.prompt_template
        for placeholder, value in replacements.items():
            prompt = prompt.replace(placeholder, value)
        return prompt

    def rerank(self, job: EvaluatedJob) -> EvaluatedJob:
        if job.evaluation.decision != FilterDecision.PASS:
            job.evaluation.audit_log.append("Semantic rerank not allowed for non-passing job.")
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

            semantic_score = float(payload.get("semantic_score", 0.0))
            bridge_role = bool(payload.get("bridge_role", False))
            explanation = str(payload.get("fit_reason", "")).strip()
            risks = [str(item) for item in payload.get("risks", [])]
        except Exception as exc:  # noqa: BLE001 - any failure must fall back deterministically
            LOGGER.warning("llm_rerank_failed error=%s", exc)
            job.evaluation.audit_log.append(
                f"Semantic rerank failed, kept deterministic result: {exc}"
            )
            return job

        # Clamp to the range the prompt advertises so a misbehaving model cannot skew tiers.
        semantic_score = max(
            min(semantic_score, self.config.semantic_score_max),
            self.config.semantic_score_min,
        )

        job.evaluation.semantic_score = semantic_score
        job.evaluation.fit_score += semantic_score
        job.evaluation.bridge_role = job.evaluation.bridge_role or bridge_role
        if explanation:
            job.evaluation.fit_reason = explanation
        job.evaluation.risks = sorted(set(job.evaluation.risks + risks))
        job.evaluation.audit_log.append(f"Semantic rerank added {semantic_score:.2f} points.")
        return job


def build_reranker(config: LLMConfig) -> SemanticReranker:
    if not config.enabled:
        return NullReranker()
    return OpenAIReranker(config)
