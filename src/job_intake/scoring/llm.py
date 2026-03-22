from __future__ import annotations

import json
import os
from pathlib import Path

from job_intake.config.settings import LLMConfig
from job_intake.models.job import EvaluatedJob, FilterDecision


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

    def rerank(self, job: EvaluatedJob) -> EvaluatedJob:
        if job.evaluation.decision != FilterDecision.PASS:
            job.evaluation.audit_log.append("Semantic rerank not allowed for non-passing job.")
            return job

        api_key = os.getenv(self.config.api_key_env)
        if not api_key:
            job.evaluation.audit_log.append("Semantic rerank skipped because API key is missing.")
            return job

        from openai import OpenAI

        client = OpenAI(api_key=api_key)
        prompt = self.prompt_template.format(
            title=job.record.title,
            company=job.record.company,
            location=job.record.location_text or "",
            remote_text=job.record.remote_text or "",
            employment_type=job.record.employment_type or "",
            timezone_text=job.record.timezone_text or "",
            description=(job.record.description_clean or job.record.description_raw)[
                : self.config.max_description_chars
            ],
        )
        response = client.responses.create(
            model=self.config.model,
            input=prompt,
        )
        text = response.output_text
        payload = json.loads(text)

        semantic_score = float(payload.get("semantic_score", 0.0))
        bridge_role = bool(payload.get("bridge_role", False))
        explanation = str(payload.get("fit_reason", "")).strip()
        risks = [str(item) for item in payload.get("risks", [])]

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
