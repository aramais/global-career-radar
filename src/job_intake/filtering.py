from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from job_intake.annotation.text import (
    asserted_phrase_hits,
    assertion_clauses,
    eligibility_description,
    role_description,
)
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

_CANDIDATE_EXCLUSIONS = (
    re.compile(
        r"\b(?:candidates?|applicants?|applications?|residents?)\s+"
        r"(?:who (?:are|live)\s+)?(?:in|from|of|based in|located in|residing in|living in)\s+"
        r"(?P<location>[^.;!?\n|]+?)\s+"
        r"(?:(?:will|shall|can|may) not be (?:considered|hired|accepted|eligible|allowed)|"
        r"(?:are|is) (?:not (?:eligible|accepted|allowed|considered)|ineligible)|"
        r"(?:cannot|can'?t) (?:apply|be (?:hired|considered|accepted)))\b", re.I,
    ),
    re.compile(
        r"\b(?P<location>[\w -]+?)-based (?:candidates?|applicants?|residents?)\s+"
        r"(?:(?:will|shall|can) not be (?:considered|hired|accepted)|"
        r"(?:are|is) (?:not eligible|ineligible))\b", re.I,
    ),
    re.compile(
        r"\b(?:do not|don'?t|cannot|can'?t|will not) (?:consider|accept|hire)\s+"
        r"(?:candidates?|applicants?|applications?)\s+(?:in|from|based in)\s+"
        r"(?P<location>[^.;!?\n|]+)", re.I,
    ),
)
_REGION_EXCLUSION = re.compile(
    r"\b(?:except(?:\s+for)?|excluding|other than|with the exception of|"
    r"not including|but not)\s+(?P<location>[^.;!?\n|]+)", re.I,
)


def _candidate_exclusion_matches(clause: str) -> list[re.Match[str]]:
    return [match for pattern in _CANDIDATE_EXCLUSIONS for match in pattern.finditer(clause)]


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
    recall_first: bool = False
    onsite_locations: list[str] | None = None
    required_languages: list[str] | None = None
    required_scope_title_signals: list[str] | None = None
    required_scope_description_signals: list[str] | None = None
    required_annotation_focus: list[str] | None = None

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> FilterRules:
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
            recall_first=data.get("recall_first", False),
            onsite_locations=data.get("onsite_locations", []),
            required_languages=data.get("required_languages", []),
            required_scope_title_signals=data.get("required_scope_title_signals", []),
            required_scope_description_signals=data.get("required_scope_description_signals", []),
            required_annotation_focus=data.get("required_annotation_focus", []),
        )


class RuleEngine:
    def __init__(self, rules: FilterRules) -> None:
        self.rules = rules

    def evaluate(self, job: JobRecord) -> JobEvaluation:
        text_blob = self._build_text_blob(job)
        role_text = role_description(job)
        title_text = normalize_text(job.title)
        company_text = normalize_text(job.company)
        matched = []
        blockers = []
        reasons = []
        risks = []
        audit = []

        if self.rules.recall_first and job.source_metadata.get("description_complete") is False:
            risks.append("incomplete_description")
            reasons.append(
                "The source did not provide the full description; verify omitted conditions."
            )

        if job.status.value == "closed":
            blockers.append("status:closed")
            reasons.append("Job is marked closed by the source.")

        closed_hits = asserted_phrase_hits(text_blob, self.rules.closed_phrases, required=True)
        if closed_hits:
            blockers.extend([f"status_phrase:{hit}" for hit in closed_hits])
            reasons.append("Job description indicates the role is no longer open.")

        if company_text in [normalize_text(name) for name in self.rules.company_blacklist]:
            blockers.append(f"company_blacklist:{job.company}")
            reasons.append("Company is on the configured blacklist.")

        geo_hits, geo_risks = self._geography_signals(job, text_blob)
        if geo_hits:
            blockers.extend([f"geo_blocker:{hit}" for hit in geo_hits])
            reasons.append("Role explicitly requires a hiring location outside the search profile.")
        risks.extend(geo_risks)

        timezone_hits = asserted_phrase_hits(text_blob, self.rules.timezone_blockers, required=True)
        if timezone_hits:
            blockers.extend([f"timezone_blocker:{hit}" for hit in timezone_hits])
            reasons.append(
                "Timezone requirement is incompatible with the configured search profile."
            )

        negative_title_hits = contains_any(title_text, self.rules.negative_title_signals)
        if negative_title_hits:
            target = risks if self.rules.recall_first else blockers
            target.extend([f"title_blocker:{hit}" for hit in negative_title_hits])
            reasons.append("Title contains a role-family mismatch.")

        negative_desc_hits = asserted_phrase_hits(
            role_text, self.rules.negative_description_signals
        )
        if negative_desc_hits:
            target = risks if self.rules.recall_first else blockers
            target.extend([f"desc_blocker:{hit}" for hit in negative_desc_hits])
            reasons.append("Description contains role-family mismatch signals.")

        blocker_hits = asserted_phrase_hits(text_blob, self.rules.blocker_phrases, required=True)
        if self._allowed_onsite(job):
            generic_onsite = {"on-site required", "on site required", "onsite required"}
            blocker_hits = [
                hit for hit in blocker_hits
                if self._geo_text(hit) not in generic_onsite
            ]
        if blocker_hits:
            blockers.extend([f"phrase_blocker:{hit}" for hit in blocker_hits])
            reasons.append("Description contains explicit hard blockers.")

        language_blockers, language_risks = self._language_signals(job, text_blob)
        blockers.extend(language_blockers)
        risks.extend(language_risks)

        positive_title_hits = contains_any(title_text, self.rules.positive_title_signals)
        positive_desc_hits = asserted_phrase_hits(
            role_text, self.rules.positive_description_signals
        )
        matched.extend([f"title_signal:{hit}" for hit in positive_title_hits])
        matched.extend([f"description_signal:{hit}" for hit in positive_desc_hits])

        allow_hits = contains_any(text_blob, self.rules.allowlist_phrases)
        matched.extend([f"allowlist:{hit}" for hit in allow_hits])

        whitelist = [normalize_text(name) for name in self.rules.company_whitelist]
        if company_text in whitelist:
            matched.append(f"company_whitelist:{job.company}")

        timezone_allow_hits = contains_any(text_blob, self.rules.timezone_allowed)
        matched.extend([f"timezone_signal:{hit}" for hit in timezone_allow_hits])

        review_hits = contains_any(text_blob, self.rules.review_phrases)
        risks.extend([f"review_flag:{hit}" for hit in review_hits])

        scope_title_signals = self.rules.required_scope_title_signals or []
        scope_description_signals = self.rules.required_scope_description_signals or []
        if scope_title_signals or scope_description_signals:
            scope_title_hits = asserted_phrase_hits(job.title, scope_title_signals, required=True)
            scope_description_hits = asserted_phrase_hits(
                role_description(job, exclude_boilerplate=True),
                scope_description_signals, required=True,
            )
            matched.extend([f"scope_title:{hit}" for hit in scope_title_hits])
            matched.extend([f"scope_description:{hit}" for hit in scope_description_hits])
            if not scope_title_hits and not scope_description_hits:
                risks.append("role:required_scope_unconfirmed")
                reasons.append("Required role scope is not confirmed by the title or description.")

        if blockers:
            decision = FilterDecision.REJECT
        elif risks and (
            self.rules.recall_first or "role:required_scope_unconfirmed" in risks
        ):
            reasons.append("Keep this opportunity for manual review of the flagged conditions.")
            decision = FilterDecision.REVIEW
        elif not positive_title_hits and not positive_desc_hits:
            reasons.append("No strong target-family signals found; send to manual review.")
            decision = FilterDecision.REVIEW
        else:
            decision = FilterDecision.PASS

        if decision == FilterDecision.PASS and review_hits:
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
            value.strip()
            for value in [
                job.title,
                job.location_text,
                job.remote_text,
                job.timezone_text,
                job.employment_type,
                eligibility_description(job),
            ]
            if value
        )

    @staticmethod
    def _geo_text(value: str) -> str:
        # City names must match with and without accents (São Paulo / Sao Paulo).
        ascii_text = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
        return "\n".join(compact_text(line) for line in ascii_text.casefold().splitlines())

    def _target_location(self, text: str) -> bool:
        # A broad positive region cannot override an explicit target exclusion.
        if any(
            self._target_residency(match["location"])
            for match in _REGION_EXCLUSION.finditer(text)
        ):
            return False
        return bool(asserted_phrase_hits(
            self._geo_text(text), [
                self._geo_text(place)
                for place in self.rules.target_geographies + (self.rules.onsite_locations or [])
            ], required=True,
        ))

    def _target_residency(self, text: str) -> bool:
        # These describe remote policy, not a place of residence.
        remote_policies = {
            "worldwide", "global remote", "globally remote", "remote anywhere", "anywhere",
            "work from anywhere",
        }
        places = [
            self._geo_text(place)
            for place in self.rules.target_geographies + (self.rules.onsite_locations or [])
            if self._geo_text(place) not in remote_policies
        ]
        return bool(contains_any(self._geo_text(text), places))

    def _onsite(self, job: JobRecord) -> bool:
        text = self._geo_text(" | ".join(filter(None, [job.remote_text, job.location_text])))
        return any(self._asserted_office_modes(clause) for clause in assertion_clauses(text))

    @staticmethod
    def _asserted_office_modes(clause: str) -> list[str]:
        office = r"(?:on[ -]?site|hybrid|in[ -]office|office[ -]based)"
        # An employer offering a remote alternative does not require an office.
        if re.search(
            rf"\b(?:remote|remotely)\b[^.;!?]{{0,30}}(?:\bor\b|/)[^.;!?]{{0,30}}"
            rf"\b{office}\b|\b{office}\b[^.;!?]{{0,30}}(?:\bor\b|/)"
            rf"[^.;!?]{{0,30}}\b(?:remote|remotely)\b", clause,
        ):
            return []
        modes = [match[0] for match in re.finditer(rf"\b{office}\b", clause)]
        return asserted_phrase_hits(clause, modes, required=True)

    def _mandatory_office_locations(self, job: JobRecord) -> list[str | None]:
        """Read explicit office obligations from actual vacancy prose, not footprint."""
        locations = []
        office = r"(?:on[ -]?site|hybrid|in[ -]office|office[ -]based)"
        obligation = (
            rf"\b(?:requires?|required|mandatory|must(?:\s+(?:work|be|come))?)\s+"
            rf"(?:a\s+|an\s+|regular\s+)?{office}\b|"
            rf"\b{office}\b(?:\s+(?:work|working|attendance|schedule|arrangement|"
            rf"presence|model|role|position|setup|office|days))?\s+"
            rf"(?:(?:is|will be)\s+)?(?:required|mandatory)\b"
        )
        for clause in assertion_clauses(self._geo_text(eligibility_description(job))):
            if not re.search(obligation, clause) or not self._asserted_office_modes(clause):
                continue
            # General employee policy is not direct evidence of where this vacancy
            # requires its successful applicant to work.
            if re.search(
                r"\b(?:our|the company s) (?:employees|staff|offices|teams)\b", clause,
            ) and not re.search(
                r"\b(?:this role|this position|this job|you|candidates|applicants)\b", clause,
            ):
                continue
            # A candidate location before the obligation is not the office
            # location ("candidates in Brazil must work onsite in London").
            office_match = re.search(rf"\b{office}\b", clause)
            office_context = clause[office_match.end():] if office_match else clause
            location = re.search(
                r"\b(?:from|at|in|based in)\s+(?:(?:our|the|an?)\s+)?([^.;!?|]+)",
                office_context,
            )
            locations.append(location[1].strip() if location else job.location_text)
        return locations

    def _allowed_onsite(self, job: JobRecord) -> bool:
        location = self._geo_text(job.location_text or "")
        return self._onsite(job) and bool(contains_any(
            location, [self._geo_text(city) for city in self.rules.onsite_locations or []]
        ))

    def _onsite_location_unknown(self, location: str) -> bool:
        normalized = normalize_text(self._geo_text(location))
        normalized = re.sub(
            r"\b(?:on[ -]?site|hybrid|in[ -]office|office[ -]based|remote)\b", "", normalized
        ).strip(" ,-/")
        normalized = re.sub(r"^(?:our|the|an?)\s+", "", normalized)
        unknown = {
            "", "office", "offices", "headquarters", "hq", "tbd", "multiple locations",
            "location not specified", "person",
        }
        # A permitted country/region does not confirm the specific office city.
        unknown.update(
            normalize_text(self._geo_text(place)) for place in self.rules.target_geographies
        )
        if normalized in unknown:
            return True
        # Prepositions introduce time and generic office references too. Neither
        # states a city that can safely support a hard geography rejection.
        return bool(re.search(
            r"^(?:(?:later|earlier|future|another|scheduled|specified)\s+(?:date|time)|"
            r"(?:(?:assigned|designated|local|company|corporate|nearest)\s+)?"
            r"(?:office[s]?|headquarters|hq|location|site)"
            r"(?:$|\s+(?:for|to|when|on|at|not specified)\b))",
            normalized,
        ))

    def _geography_signals(self, job: JobRecord, text_blob: str) -> tuple[list[str], list[str]]:
        blockers: list[str] = []
        risks: list[str] = []
        # Keep punctuation here: an unrelated "global" later in a posting cannot
        # cancel a mandatory residency clause in an earlier sentence.
        geography_text = " | ".join(filter(None, [
            job.title, job.location_text, job.remote_text, job.timezone_text,
            eligibility_description(job),
        ]))
        text = self._geo_text(geography_text)
        location_requirements = job.source_metadata.get("applicant_location_requirements")
        structured_eligibility = False
        if isinstance(location_requirements, list) and location_requirements:
            if all(isinstance(place, str) and place.strip() for place in location_requirements):
                structured_eligibility = any(
                    self._target_location(place) for place in location_requirements
                )
                if not structured_eligibility:
                    blockers.append("applicant_locations:" + ", ".join(location_requirements))
            elif self.rules.recall_first:
                risks.append("geography:applicant_locations_unconfirmed")
        residency = re.compile(
            r"\b(?:must (?:be (?:based|located)|reside|live)|"
            r"(?:current residence|residency)(?: required)?|"
            r"(?:applicants|candidates) (?:must )?(?:be based|reside|live)) in\s+"
            r"([^;!?\n|]+)"
        )
        # Extract from sentence-aware clauses: a dot inside U.S./U.K. must not
        # truncate an allowed alternative such as "the U.S. or Brazil".
        # Split before case-folding so "U.S. Brazil ..." remains two sentences.
        residency_matches = [
            match for clause in assertion_clauses(geography_text)
            for match in residency.finditer(self._geo_text(clause))
        ]
        asserted_residency = []
        for match in residency_matches:
            constraint = re.split(
                r"\b(?:but|however|although)\b", match[0]
            )[0].strip().rstrip(".;!? ")
            if not asserted_phrase_hits(text, [constraint], required=True):
                continue
            asserted_residency.append(match)
            location = re.split(
                r"\b(?:and (?:have|be|work)|with|for this role|as|where|because|but|however|"
                r"although|while|contractor|async|distributed|remote|remotely|we|our)\b", match[1]
            )[0]
            if self.rules.recall_first and re.search(
                r"\b(?:eligible|approved|supported|listed|certain|these|office|one of|"
                r"the country|a country|our country)\b", location
            ) and not self._target_residency(location):
                risks.append("geography:residency_location_unconfirmed")
            elif not self._target_residency(location):
                blockers.append(match[0].strip().rstrip(".;!? "))

        for match in re.finditer(
            r"\b(?:cannot hire|can not hire|unable to hire|not hiring|not available|"
            r"not open to)\s+(?:candidates |applicants |residents )?(?:in|from)\s+([^.;!|]+)",
            text,
        ):
            if self._target_residency(match[1]):
                if asserted_phrase_hits(text, [match[0]], required=True):
                    blockers.append(match[0].strip())
                else:
                    risks.append("geography:candidate_exclusion_unconfirmed")

        for clause in assertion_clauses(text):
            for match in _REGION_EXCLUSION.finditer(clause):
                if self._target_residency(match["location"]):
                    if re.search(
                        r"\?|\b(?:if|unless|might|may|could|possibly|sponsorship|"
                        r"citizenship|passport|visa|work (?:authorization|permit))\b", clause,
                    ):
                        risks.append("geography:candidate_exclusion_unconfirmed")
                    else:
                        blockers.append(match[0].rstrip(".;!? "))
            for match in _candidate_exclusion_matches(clause):
                if not self._target_residency(match["location"]):
                    continue
                if re.search(
                    r"\?|\b(?:unless|if|without|except|may|might|could|sponsorship|"
                    r"benefits?|relocation|bonus|insurance|discount)\b", clause,
                ):
                    risks.append("geography:candidate_exclusion_unconfirmed")
                else:
                    blockers.append(match[0].strip())

        generic_residency = {
            "must be based in", "must reside in", "must live in", "current residence in",
        }
        for phrase in self.rules.geography_blockers:
            normalized = self._geo_text(phrase)
            if normalized in generic_residency:
                # The complete clause above determines whether residency is allowed.
                continue
            if asserted_phrase_hits(text, [normalized], required=True):
                blockers.append(phrase)

        # On-site eligibility is based on the actual source location, not a
        # Brazil/remote/contractor mention elsewhere in the description.
        if self.rules.onsite_locations and self._onsite(job):
            if self._onsite_location_unknown(job.location_text or ""):
                risks.append("geography:onsite_location_unknown")
            elif not self._allowed_onsite(job):
                blockers.append(f"onsite:{job.location_text}")

        if self.rules.onsite_locations:
            for location in self._mandatory_office_locations(job):
                if self._onsite_location_unknown(location or ""):
                    risks.append("geography:onsite_location_unknown")
                elif not contains_any(
                    self._geo_text(location or ""),
                    [self._geo_text(city) for city in self.rules.onsite_locations],
                ):
                    blockers.append(f"onsite:{location}")

        if self.rules.recall_first and not blockers:
            location_info = " ".join(filter(None, [job.location_text, job.remote_text]))
            permitted = structured_eligibility or self._target_location(location_info) or any(
                self._target_location(match[1]) for match in asserted_residency
            )
            # Positive region/remote statements are evidence of eligibility;
            # contract type or an async team alone are not.
            permitted = permitted or self._description_confirms_eligibility(job)
            if not permitted:
                risks.append("geography:eligibility_unconfirmed")
        return sorted(set(blockers)), risks

    def _description_confirms_eligibility(self, job: JobRecord) -> bool:
        for clause in assertion_clauses(eligibility_description(job)):
            if _candidate_exclusion_matches(clause):
                continue
            if any(
                self._target_residency(match["location"])
                for match in _REGION_EXCLUSION.finditer(clause)
            ):
                continue
            if re.search(
                r"\b(?:citizenship|citizens?|nationality|passport|visa|sponsorship|"
                r"work (?:authorization|permit)|right to work)\b", clause, re.I,
            ):
                # A nationality or permission mentioned in a role clause does
                # not state where the employer is able to hire its applicants.
                continue
            if re.search(
                r"\b(?:ineligible|excluded|not eligible|not considered|not accepted|"
                r"not permitted|not allowed)\b", clause, re.I,
            ):
                continue
            if not re.search(
                r"\b(?:remote|remotely|work from|work anywhere|hire|hiring|eligible|"
                r"eligibility|applicants?|candidates?|this role|role|position|contractor|"
                r"location|based|reside|live)\b", clause, re.I,
            ):
                continue
            targets = self.rules.target_geographies + (self.rules.onsite_locations or [])
            if asserted_phrase_hits(
                self._geo_text(clause), [self._geo_text(place) for place in targets]
            ):
                return True
        return False

    def _language_signals(self, job: JobRecord, text_blob: str) -> tuple[list[str], list[str]]:
        languages = self.rules.required_languages or []
        if not languages:
            return [], []
        # A structured language comes from the source, never from language
        # detection on a page: an English advert can describe another work language.
        working_language = job.source_metadata.get("working_language")
        if isinstance(working_language, list):
            working_language = ", ".join(
                language for language in working_language if isinstance(language, str)
            )
        if isinstance(working_language, str) and working_language.strip():
            if asserted_phrase_hits(working_language, languages, required=True):
                return [], []
            if contains_any(working_language, languages) or contains_any(
                working_language, ["no", "not", "unspecified", "unknown", "tbd", "optional",
                                   "unconfirmed", "uncertain", "might", "may", "could",
                                   "possibly", "whether"]
            ) or "?" in working_language:
                return [], ["language:working_language_unconfirmed"]
            return [f"language:working_language:{working_language}"], []
        text = self._geo_text(text_blob)
        clauses = assertion_clauses(text)
        for clause in clauses:
            explicit_work_language = re.search(
                r"\bworking language\s*(?:(?:is|will be)\s*|:\s*)([^.;!?|]+)", clause
            )
            if explicit_work_language and not contains_any(
                explicit_work_language[1], languages + [
                    "no", "not", "optional", "preferred", "unspecified", "unknown", "tbd",
                ]
            ) and asserted_phrase_hits(clause, [explicit_work_language[1]], required=True):
                return [f"language:working_language:{explicit_work_language[1].strip()}"], []
        for language in languages:
            token = re.escape(self._geo_text(language))
            if any(re.search(
                rf"\b(?:fluen\w*|proficien\w*|working language|business|native|"
                rf"professional|spoken|written)\b[^.;|]{{0,30}}\b{token}\b|"
                rf"\b{token}\b[^.;|]{{0,30}}\b(?:required|fluen\w*|proficien\w*|"
                rf"communication|skills|working language)\b", clause
            ) and asserted_phrase_hits(clause, [language], required=True) for clause in clauses):
                return [], []
        return [], ["language:working_language_unconfirmed"] if self.rules.recall_first else []

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
            detail = ", ".join(risks[:3]) or "insufficient high-confidence profile signals"
            return f"Manual review: {detail}."
        detail = ", ".join(matched[:4]) or "deterministic match"
        if risks:
            detail += f"; risks: {', '.join(risks[:2])}"
        return f"Passed hard filters with signals: {detail}"
