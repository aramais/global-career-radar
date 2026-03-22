from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from job_intake.models.job import FilterDecision, JobEvaluation, JobTier
from job_intake.utils.text import contains_any, normalize_text


@dataclass(slots=True)
class SearchProfiles:
    bucket_a_signals: list[str]
    bucket_b_signals: list[str]
    bucket_c_signals: list[str]
    company_priority: list[str]
    source_weights: dict[str, float]
    title_weights: dict[str, float]
    description_weights: dict[str, float]
    threshold_a: float
    threshold_b: float

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "SearchProfiles":
        return cls(
            bucket_a_signals=data.get("bucket_a_signals", []),
            bucket_b_signals=data.get("bucket_b_signals", []),
            bucket_c_signals=data.get("bucket_c_signals", []),
            company_priority=data.get("company_priority", []),
            source_weights=data.get("source_weights", {}),
            title_weights=data.get("title_weights", {}),
            description_weights=data.get("description_weights", {}),
            threshold_a=float(data.get("threshold_a", 14)),
            threshold_b=float(data.get("threshold_b", 8)),
        )


class DeterministicScorer:
    def __init__(self, profiles: SearchProfiles) -> None:
        self.profiles = profiles

    def score(
        self,
        source: str,
        company: str,
        title: str,
        description: str,
        evaluation: JobEvaluation,
    ) -> JobEvaluation:
        if evaluation.decision == FilterDecision.REJECT:
            evaluation.fit_score = 0.0
            evaluation.bucket = "Bucket C"
            evaluation.tier = JobTier.C
            evaluation.audit_log.append("Scoring skipped because job was hard rejected.")
            return evaluation

        score = 0.0
        title_text = normalize_text(title)
        description_text = normalize_text(description)

        for phrase, weight in self.profiles.title_weights.items():
            if normalize_text(phrase) in title_text:
                score += float(weight)
                evaluation.matched_signals.append(f"title_weight:{phrase}")

        for phrase, weight in self.profiles.description_weights.items():
            if normalize_text(phrase) in description_text:
                score += float(weight)
                evaluation.matched_signals.append(f"description_weight:{phrase}")

        score += self._source_weight(source)

        if normalize_text(company) in [normalize_text(name) for name in self.profiles.company_priority]:
            score += 2.0
            evaluation.matched_signals.append(f"company_priority:{company}")

        if contains_any(title_text, self.profiles.bucket_c_signals):
            score -= 8.0
        if contains_any(description_text, self.profiles.bucket_a_signals):
            score += 3.0
        if contains_any(description_text, self.profiles.bucket_b_signals):
            score += 1.5

        if evaluation.decision == FilterDecision.REVIEW:
            score = max(score - 3.0, 0.0)

        evaluation.deterministic_score = score
        evaluation.fit_score = score

        if score >= self.profiles.threshold_a and evaluation.decision == FilterDecision.PASS:
            evaluation.tier = JobTier.A
            evaluation.bucket = "Bucket A"
        elif score >= self.profiles.threshold_b:
            evaluation.tier = JobTier.B
            evaluation.bucket = "Bucket B"
        else:
            evaluation.tier = JobTier.C if evaluation.decision == FilterDecision.REJECT else JobTier.B
            evaluation.bucket = "Bucket C" if evaluation.tier == JobTier.C else "Bucket B"

        evaluation.audit_log.append(f"Deterministic score computed as {score:.2f}.")
        return evaluation

    def _source_weight(self, source: str) -> float:
        if source in self.profiles.source_weights:
            return float(self.profiles.source_weights[source])
        prefix = source.split(":", 1)[0]
        return float(self.profiles.source_weights.get(prefix, 0.0))
