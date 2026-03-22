from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from job_intake.models.job import EvaluatedJob, FilterDecision, JobEvaluation, JobRecord, JobTier
from job_intake.utils.text import compact_text, contains_any, normalize_text


TARGET_GEO_TOKENS = [
    "worldwide",
    "global remote",
    "globally remote",
    "remote anywhere",
    "anywhere",
    "americas",
    "latam",
    "latin america",
    "south america",
    "chile",
    "argentina",
    "brazil",
    "colombia",
    "mexico",
    "canada",
]


@dataclass(slots=True)
class FilterRules:
    positive_title_signals: list[str]
    positive_description_signals: list[str]
    negative_title_signals: list[str]
    negative_description_signals: list[str]
    blocker_phrases: list[str]
    review_phrases: list[str]
    allowlist_phrases: list[str]
    company_blacklist: list[str]
    company_whitelist: list[str]
    target_geographies: list[str]
    geography_blockers: list[str]
    timezone_allowed: list[str]
    timezone_blockers: list[str]
    closed_phrases: list[str]

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "FilterRules":
        return cls(
            positive_title_signals=data.get("positive_title_signals", []),
            positive_description_signals=data.get("positive_description_signals", []),
            negative_title_signals=data.get("negative_title_signals", []),
            negative_description_signals=data.get("negative_description_signals", []),
            blocker_phrases=data.get("blocker_phrases", []),
            review_phrases=data.get("review_phrases", []),
            allowlist_phrases=data.get("allowlist_phrases", []),
            company_blacklist=data.get("company_blacklist", []),
            company_whitelist=data.get("company_whitelist", []),
            target_geographies=data.get("target_geographies", TARGET_GEO_TOKENS),
            geography_blockers=data.get("geography_blockers", []),
            timezone_allowed=data.get("timezone_allowed", []),
            timezone_blockers=data.get("timezone_blockers", []),
            closed_phrases=data.get("closed_phrases", []),
        )


class RuleEngine:
    def __init__(self, rules: FilterRules) -> None:
        self.rules = rules

    def evaluate(self, job: JobRecord) -> JobEvaluation:
        text_blob = self._build_text_blob(job)
        title_text = normalize_text(job.title)
        company_text = normalize_text(job.company)
        matched = []
        blockers = []
        reasons = []
        risks = []
        audit = []

        if job.status.value == "closed":
            blockers.append("status:closed")
            reasons.append("Job is marked closed by the source.")

        closed_hits = contains_any(text_blob, self.rules.closed_phrases)
        if closed_hits:
            blockers.extend([f"status_phrase:{hit}" for hit in closed_hits])
            reasons.append("Job description indicates the role is no longer open.")

        if company_text in [normalize_text(name) for name in self.rules.company_blacklist]:
            blockers.append(f"company_blacklist:{job.company}")
            reasons.append("Company is on the configured blacklist.")

        if self._has_explicit_geo_blocker(text_blob):
            geo_hits = contains_any(text_blob, self.rules.geography_blockers)
            blockers.extend([f"geo_blocker:{hit}" for hit in geo_hits])
            reasons.append("Role restricts hiring geography outside the target LATAM/Americas scope.")

        timezone_hits = contains_any(text_blob, self.rules.timezone_blockers)
        if timezone_hits:
            blockers.extend([f"timezone_blocker:{hit}" for hit in timezone_hits])
            reasons.append("Timezone requirement looks incompatible with Chile/LATAM/Americas coverage.")

        negative_title_hits = contains_any(title_text, self.rules.negative_title_signals)
        if negative_title_hits:
            blockers.extend([f"title_blocker:{hit}" for hit in negative_title_hits])
            reasons.append("Title maps to an excluded role family.")

        negative_desc_hits = contains_any(text_blob, self.rules.negative_description_signals)
        if negative_desc_hits:
            blockers.extend([f"desc_blocker:{hit}" for hit in negative_desc_hits])
            reasons.append("Description contains excluded role-family signals.")

        blocker_hits = contains_any(text_blob, self.rules.blocker_phrases)
        if blocker_hits:
            blockers.extend([f"phrase_blocker:{hit}" for hit in blocker_hits])
            reasons.append("Description contains explicit hard blockers.")

        positive_title_hits = contains_any(title_text, self.rules.positive_title_signals)
        positive_desc_hits = contains_any(text_blob, self.rules.positive_description_signals)
        matched.extend([f"title_signal:{hit}" for hit in positive_title_hits])
        matched.extend([f"description_signal:{hit}" for hit in positive_desc_hits])

        allow_hits = contains_any(text_blob, self.rules.allowlist_phrases)
        matched.extend([f"allowlist:{hit}" for hit in allow_hits])

        if normalize_text(job.company) in [normalize_text(name) for name in self.rules.company_whitelist]:
            matched.append(f"company_whitelist:{job.company}")

        timezone_allow_hits = contains_any(text_blob, self.rules.timezone_allowed)
        matched.extend([f"timezone_signal:{hit}" for hit in timezone_allow_hits])

        if not blockers and not positive_title_hits and not positive_desc_hits:
            reasons.append("No strong target-family signals found; send to manual review.")
            decision = FilterDecision.REVIEW
        elif blockers:
            decision = FilterDecision.REJECT
        else:
            decision = FilterDecision.PASS

        review_hits = contains_any(text_blob, self.rules.review_phrases)
        if decision == FilterDecision.PASS and review_hits:
            risks.extend([f"review_flag:{hit}" for hit in review_hits])
            reasons.append("Role passed hard filters but contains ambiguity that merits review.")

        bridge_role = bool(positive_title_hits or positive_desc_hits)
        fit_reason = self._build_fit_reason(decision, matched, blockers, risks)
        tier = JobTier.C if decision == FilterDecision.REJECT else JobTier.B
        bucket = "Bucket C" if decision == FilterDecision.REJECT else "Bucket B"

        audit.extend(reasons)
        return JobEvaluation(
            decision=decision,
            matched_signals=sorted(set(matched)),
            blocker_signals=sorted(set(blockers)),
            reasons=reasons,
            fit_reason=fit_reason,
            bridge_role=bridge_role,
            tier=tier,
            bucket=bucket,
            risks=sorted(set(risks)),
            audit_log=audit,
        )

    def apply(self, job: JobRecord) -> EvaluatedJob:
        return EvaluatedJob(record=job, evaluation=self.evaluate(job))

    def _build_text_blob(self, job: JobRecord) -> str:
        return " | ".join(
            compact_text(value)
            for value in [
                job.title,
                job.company,
                job.location_text,
                job.remote_text,
                job.timezone_text,
                job.employment_type,
                job.description_clean or job.description_raw,
            ]
            if value
        )

    def _has_explicit_geo_blocker(self, text_blob: str) -> bool:
        geo_blockers = contains_any(text_blob, self.rules.geography_blockers)
        if not geo_blockers:
            return False
        allow_hits = contains_any(text_blob, self.rules.allowlist_phrases + self.rules.target_geographies)
        return not allow_hits

    @staticmethod
    def _build_fit_reason(
        decision: FilterDecision,
        matched: list[str],
        blockers: list[str],
        risks: list[str],
    ) -> str:
        if decision == FilterDecision.REJECT:
            return f"Rejected due to: {', '.join(blockers[:4])}"
        if decision == FilterDecision.REVIEW:
            return "Manual review: passed blockers but lacked enough high-confidence bridge-role signals."
        detail = ", ".join(matched[:4]) or "deterministic match"
        if risks:
            detail += f"; risks: {', '.join(risks[:2])}"
        return f"Passed hard filters with signals: {detail}"
