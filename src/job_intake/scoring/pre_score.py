from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from job_intake.annotation.text import asserted_phrase_hits, scoped_description_text
from job_intake.models.job import FilterDecision, JobEvaluation, JobRecord, JobTier
from job_intake.occupation_titles import title_weight_matches
from job_intake.utils.text import contains_any, matches_phrase, normalize_text


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
    scoring_policy: str = "legacy"

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> SearchProfiles:
        policy = data.get("scoring_policy", "legacy")
        if policy not in ("legacy", "role_fit_v1"):
            raise ValueError("scoring_policy must be legacy or role_fit_v1")
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
            scoring_policy=policy,
        )


class DeterministicScorer:
    def __init__(self, profiles: SearchProfiles, *, role_id: str | None = None) -> None:
        self.profiles = profiles
        self.role_id = role_id

    def score(
        self,
        source: str,
        company: str,
        title: str,
        description: str,
        evaluation: JobEvaluation,
        *,
        record: JobRecord | None = None,
    ) -> JobEvaluation:
        if self.profiles.scoring_policy == "role_fit_v1":
            return self._role_fit(source, company, title, description, evaluation, record)
        if evaluation.decision == FilterDecision.REJECT:
            evaluation.raw_fit_score = None
            evaluation.score_cap_reasons = ["legacy:hard_reject"]
            evaluation.fit_score = 0.0
            evaluation.bucket = "Bucket C"
            evaluation.tier = JobTier.C
            evaluation.audit_log.append("Scoring skipped because job was hard rejected.")
            return evaluation

        score = 0.0
        title_text = normalize_text(title)
        description_text = scoped_description_text(description)

        for phrase, weight in self.profiles.title_weights.items():
            if matches_phrase(title_text, phrase):
                score += float(weight)
                evaluation.matched_signals.append(f"title_weight:{phrase}")

        for phrase, weight in self.profiles.description_weights.items():
            if asserted_phrase_hits(description_text, [phrase]):
                score += float(weight)
                evaluation.matched_signals.append(f"description_weight:{phrase}")

        score += self._source_weight(source)

        if normalize_text(company) in [
            normalize_text(name) for name in self.profiles.company_priority
        ]:
            score += 2.0
            evaluation.matched_signals.append(f"company_priority:{company}")

        if contains_any(title_text, self.profiles.bucket_c_signals):
            score -= 8.0
        if asserted_phrase_hits(description_text, self.profiles.bucket_a_signals):
            score += 3.0
        if asserted_phrase_hits(description_text, self.profiles.bucket_b_signals):
            score += 1.5

        evaluation.raw_fit_score = score
        evaluation.score_cap_reasons = []
        if evaluation.decision == FilterDecision.REVIEW:
            score = max(score - 3.0, 0.0)
            evaluation.score_cap_reasons.append("legacy:review_penalty")

        evaluation.deterministic_score = score
        evaluation.fit_score = score

        # Final tier/bucket are decided by finalize_tier() after optional LLM
        # reranking, using the config thresholds — do not assign them here.
        evaluation.audit_log.append(f"Deterministic score computed as {score:.2f}.")
        return evaluation

    def _role_fit(
        self, source: str, company: str, title: str, description: str,
        evaluation: JobEvaluation, record: JobRecord | None,
    ) -> JobEvaluation:
        from job_intake.scoring.evidence import description_evidence

        record = record or JobRecord(
            source=source, company=company, title=title, original_url="",
            description_clean=description,
        )
        evidence = description_evidence(record)
        evaluation.fit_policy = "role_fit_v1"
        evaluation.fit_max_tier = None
        evaluation.score_cap_reasons = []
        score = self._source_weight(source)
        titles = title_weight_matches(title, self.profiles.title_weights, self.role_id)
        if titles:
            phrase, weight = max(titles, key=lambda item: (item[1], -len(item[0]), item[0]))
            score += weight
            evaluation.matched_signals.append(f"title_weight:{phrase}")

        # Synonyms of one concept do not become independent reasons for a higher fit.
        groups = {
            "decision science": "decision", "decision support": "decision",
            "strategic decisions": "decision", "business decisions": "decision",
            "p&l": "pnl", "profit and loss": "pnl",
        }
        concepts: dict[str, tuple[str, float]] = {}
        for phrase, weight in evidence.matched_weights(self.profiles.description_weights):
            group = groups.get(normalize_text(phrase), normalize_text(phrase))
            if group not in concepts or weight > concepts[group][1]:
                concepts[group] = (phrase, weight)
        for phrase, weight in concepts.values():
            score += weight
            evaluation.matched_signals.append(f"description_weight:{phrase}")

        bucket_a = evidence.matched_weights(dict.fromkeys(self.profiles.bucket_a_signals, 1.0))
        bucket_b = evidence.matched_weights(dict.fromkeys(self.profiles.bucket_b_signals, 1.0))
        if bucket_a:
            score += 3.0
        if bucket_b:
            score += 1.5
        if normalize_text(company) in {
            normalize_text(name) for name in self.profiles.company_priority
        }:
            score += 2.0
            evaluation.matched_signals.append(f"company_priority:{company}")
        if contains_any(normalize_text(title), self.profiles.bucket_c_signals):
            score -= 8.0

        evaluation.raw_fit_score = score

        evaluation.bridge_role = bool(bucket_a) and evidence.substantive_decision
        role_blocked = any(
            signal.startswith(("desc_blocker:", "title_blocker:"))
            for signal in (*evaluation.risks, *evaluation.blocker_signals)
        )
        incomplete = record.source_metadata.get("description_complete") is False
        if role_blocked or evidence.reporting_core or (
            not evidence.analytical_mandate and not incomplete
        ):
            evaluation.fit_max_tier = JobTier.C
            score = min(score, max(0.0, self.profiles.threshold_b - 0.5))
            reason = (
                "reporting_core" if evidence.reporting_core else
                "explicit_role_mismatch" if role_blocked else "analytical_mandate_unconfirmed"
            )
            evaluation.risks.append("fit:" + reason)
            evaluation.score_cap_reasons.append(reason)
        elif incomplete:
            evaluation.fit_max_tier = JobTier.B
            evaluation.risks.append("fit:incomplete_description")
            evaluation.score_cap_reasons.append("incomplete_description")
        elif not evidence.project_leadership:
            evaluation.fit_max_tier = JobTier.B
            evaluation.risks.append("fit:project_leadership_unconfirmed")
            evaluation.score_cap_reasons.append("project_leadership_unconfirmed")
        evaluation.deterministic_score = evaluation.fit_score = max(score, 0.0)
        for signal in evidence.signals:
            evaluation.audit_log.append(
                f"Role fit evidence [{signal['concept']}; {signal['unit_id']}]: {signal['quote']}"
            )
        evaluation.risks = sorted(set(evaluation.risks))
        evaluation.fit_reason = (
            f"Role fit {evaluation.fit_score:g}; hiring conditions: {evaluation.decision.value}."
        )
        evaluation.audit_log.append(
            "role_fit_v1: strongest occupation title + duty concepts; hiring decision unchanged."
        )
        return evaluation

    def _source_weight(self, source: str) -> float:
        if source in self.profiles.source_weights:
            return float(self.profiles.source_weights[source])
        prefix = source.split(":", 1)[0]
        return float(self.profiles.source_weights.get(prefix, 0.0))
