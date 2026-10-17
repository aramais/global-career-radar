"""Conservative, source-attributed evidence for deterministic role-fit scoring.

These patterns describe advertised duties, never candidate ability or hiring
eligibility. Missing evidence is unknown: the booleans are ranking inputs, not
grounds for rejecting an application. Weights and thresholds stay in config.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from job_intake.annotation.text import (
    asserted_phrase_hits,
    assertion_clauses,
    source_units,
)
from job_intake.models.job import JobRecord
from job_intake.utils.text import normalize_text

_ACTION = (
    r"(?:lead(?:s|ing)?|manag(?:e[sd]?|ing)|own(?:s|ing)?|oversee(?:s|ing)?|"
    r"direct(?:s|ing)?|design(?:s|ing)?|build(?:s|ing)?|develop(?:s|ing)?|"
    r"deliver(?:s|ing)?|conduct(?:s|ing)?|run(?:s|ning)?|guid(?:e[sd]?|ing)|"
    r"set(?:s|ting)?|defin(?:e[sd]?|ing)|establish(?:es|ing)?|champion(?:s|ing)?|"
    r"driv(?:e[sd]?|ing)|translat(?:e[sd]?|ing)|recommend(?:s|ing)?|"
    r"partner(?:s|ing)?|collaborat(?:e[sd]?|ing)|influenc(?:e[sd]?|ing)|"
    r"inform(?:s|ing)?|support(?:s|ing)?|analy[sz](?:e[sd]?|ing)|"
    r"measur(?:e[sd]?|ing)|creat(?:e[sd]?|ing)|optimi[sz](?:e[sd]?|ing)|"
    r"execut(?:e[sd]?|ing)|coach(?:es|ing)?|hir(?:e[sd]?|ing)|mentor(?:s|ing)?|"
    r"turn(?:s|ing)?|shap(?:e[sd]?|ing)|prioriti[sz](?:e[sd]?|ing)|"
    r"align(?:s|ing)?|communicat(?:e[sd]?|ing)|present(?:s|ing)?|"
    r"monitor(?:s|ing)?|foster(?:s|ing)?|train(?:s|ing)?|ensur(?:e[sd]?|ing)|"
    r"improv(?:e[sd]?|ing)|coordinat(?:e[sd]?|ing)|contribut(?:e[sd]?|ing)|"
    r"review(?:s|ing)?|scal(?:e[sd]?|ing)|identify|identifies|identifying|"
    r"bring(?:s|ing)?|agree(?:s|ing)?|maintain(?:s|ing)?|appl(?:y|ies|ying)|"
    r"test(?:s|ing)?|generat(?:e[sd]?|ing)|quantif(?:y|ies|ying)|"
    r"model(?:s|ing|ling)?|synthesi[sz](?:e[sd]?|ing)|interpret(?:s|ing)?)"
)
_ACTION_RE = re.compile(rf"\b{_ACTION}\b", re.I)
_IMPERATIVE = re.compile(rf"(?:^|:\s*[-•]?\s*){_ACTION}\b", re.I)
_ROLE_SUBJECT = re.compile(
    r"\b(?:you(?: will|['’]ll| are)?|your (?:role|responsibilities)|"
    r"(?:the|this) (?:role|position|manager|head|director)|responsible for|"
    r"responsibilities|accountabilities)\b", re.I,
)
_HISTORY = re.compile(
    r"\b(?:experience (?:in|with|of|leading|managing)|years (?:of )?experience|"
    r"track record|previous(?:ly)?|prior experience|background in|"
    r"(?:ability|able) to|proven ability|skills?|proficiency|familiarity|"
    r"knowledge of|expertise|experienced (?:in|with|at))\b", re.I,
)
_HISTORY_PREFIX = re.compile(
    r"^\s*(?:[^:.;!?]{1,60}:\s*)?(?:(?:extensive|strong|demonstrated|relevant|"
    r"hands-on|deep|prior|previous|proven|professional|direct|significant|"
    r"substantial|successful|quantitative|industry)\s+){0,4}"
    r"(?:experience|track record|background|proficiency|familiarity|"
    r"you (?:have|bring|possess|demonstrate)|you['’]ve)\b", re.I,
)
_EXPLICIT_DUTY = re.compile(
    r"\b(?:you will|you['’]ll|responsible for|your responsibilities|"
    r"(?:the|this) (?:role|position) (?:owns|leads|manages|involves|includes))\b", re.I,
)
_COMPANY_SUBJECT = re.compile(
    r"^\s*(?:our (?:company|mission|vision|business|platform|products?|customers?|"
    r"teams?)|we (?:are|have|build|serve|believe|offer|provide|operate)|"
    r"about (?:us|the company|the team))\b", re.I,
)
_UNCERTAIN = re.compile(
    r"\b(?:might|could|possibly|potentially|optional|preferred|nice to have|"
    r"not confirmed|unconfirmed|whether)\b|\bmay\s+(?:be\s+)?\w+|"
    r"\bif\s+(?:needed|required|requested|you)\b", re.I,
)
_NEGATIVE_DUTY = re.compile(
    rf"\b(?:you (?:will |do )?not|you won['’]t|do not|don['’]t|"
    rf"not responsible for|no need to|not expected to)\s+(?:\w+\s+){{0,3}}{_ACTION}\b",
    re.I,
)
_BLOCK_HEADING = re.compile(
    r"^\s*(?:[-•#>*]\s*)?(?:education\s*(?:&|and)\s*experience|"
    r"skills\s*(?:&|and)\s*(?:certifications|experience)|desired skills|"
    r"education and qualifications|"
    r"required skills|preferred skills|what (?:we offer|you bring|you will get)|"
    r"we['’]ll have a match if you|will be an advantage|"
    r"who you are|requirements|qualifications|benefits|compensation|salary|"
    r"how to apply|your application|equal opportunity|about (?:us|the company|the team))\b",
    re.I,
)
_DUTY_HEADING = re.compile(
    r"^\s*(?:[-•#>*]\s*)?(?:responsibilities|(?:key |your )?accountabilities|"
    r"your tasks and responsibilities|what you(?: will|['’]ll) (?:do|own)|"
    r"about (?:the|your) role|role (?:description|overview))\b", re.I,
)
_NAVIGATION = re.compile(
    r"^\s*(?:apply (?:now|today)|share (?:this )?(?:job|vacancy)|back to jobs|"
    r"view all jobs|follow us|privacy policy|cookie policy|terms (?:of use|and conditions))\b",
    re.I,
)
_LEADERSHIP = re.compile(
    r"\b(?:lead(?:s|ing)?|manag(?:e[sd]?|ing)|own(?:s|ing)?|oversee(?:s|ing)?|"
    r"direct(?:s|ing)?|coordinat(?:e[sd]?|ing)|set(?:s|ting)?|defin(?:e[sd]?|ing))\b"
    r"(?:\s+[\w'-]+|\s*[,/&–—-]\s*){0,12}\s+"
    r"(?:projects?|programs?|teams?|org(?:anization)?s?|functions?|workstreams?|"
    r"goals?|objectives?|roadmaps?)\b", re.I,
)
_FUNCTION_LEADERSHIP = re.compile(
    r"\b(?:lead(?:s|ing)?|manag(?:e[sd]?|ing)|own(?:s|ing)?)\b"
    r"(?:\s+[\w'-]+|\s*[,/&–—-]\s*){0,5}\s+"
    r"(?:decision support|budget planning|commercial analytics|pricing|monetization|"
    r"experimentation|measurement|forecasting)\b", re.I,
)
_COLLABORATOR = re.compile(
    r"\b(?:partner|collaborate|work|coordinate|interact|interface|liaise)\w*\s+"
    r"(?:closely\s+)?with\b", re.I,
)
_REPORT_OUTPUT = re.compile(r"\b(?:reports?|reporting|dashboards?)\b", re.I)
_REPORT_OWNERSHIP = re.compile(
    r"\b(?:own(?:s|ing)?|build(?:s|ing)?|deliver(?:s|ing)?|"
    r"develop(?:s|ing)?|design(?:s|ing)?|lead(?:s|ing)?)\b"
    r"(?:\s+[\w'-]+|\s*[,/&–—-]\s*){0,10}\s+"
    r"(?:reports?|reporting|dashboards?)\b", re.I,
)
_ANALYTICAL_WORK = re.compile(
    r"\b(?:analy[sz](?:e[sd]?|ing)|analytics|analysis|analytical work|"
    r"experiments?|experimentation|experiment design|causal inference|"
    r"algorithms?|(?:predictive |statistical |machine learning |ltv )?models?|"
    r"pricing|monetization|unit economics|profit and loss|p&l|measurement|"
    r"decision support|budget planning|forecasting)\b", re.I,
)
_ANALYSIS_OWNERSHIP = re.compile(
    r"\b(?:own(?:s|ing)?|lead(?:s|ing)?|driv(?:e[sd]?|ing)|"
    r"design(?:s|ing)?|conduct(?:s|ing)?|run(?:s|ning)?|"
    r"build(?:s|ing)?|develop(?:s|ing)?|creat(?:e[sd]?|ing)|"
    r"appl(?:y|ies|ying)|analy[sz](?:e[sd]?|ing))\b",
    re.I,
)
_DECISION = re.compile(
    r"\b(?:business|commercial|strategic|product|budget|operational)\s+"
    r"decision(?:s|[ -]making)?\b", re.I,
)
_DECISION_ACTION = re.compile(
    r"\b(?:driv(?:e[sd]?|ing)|shap(?:e[sd]?|ing)|influenc(?:e[sd]?|ing)|"
    r"recommend(?:s|ing)?|own(?:s|ing)?|mak(?:e[sd]?|ing)|inform(?:s|ing)?|"
    r"support(?:s|ing)?|translat(?:e[sd]?|ing)|guid(?:e[sd]?|ing)|"
    r"participat(?:e[sd]?|ing))\b", re.I,
)
_PRODUCT_ACTION = re.compile(
    r"\b(?:shap(?:e[sd]?|ing)|defin(?:e[sd]?|ing)|driv(?:e[sd]?|ing)|"
    r"own(?:s|ing)?|influenc(?:e[sd]?|ing)|recommend(?:s|ing)?)\b(?:\s+\w+){0,8}\s+"
    r"(?:product strategy|product vision|business strategy|commercial strategy|roadmaps?)\b",
    re.I,
)
_EXPERIMENT = re.compile(r"\b(?:experimentation|experiments?|experiment design)\b", re.I)
_ECONOMIC_WORK = re.compile(
    r"\b(?:pricing|monetization|unit economics|profit and loss|p&l|budget planning|"
    r"budget allocations?|budget reallocations?)\b", re.I,
)
_TECHNICAL_OUTPUT = re.compile(
    r"\b(?:data pipelines?|data architecture|databases?|infrastructure|data platforms?)\b",
    re.I,
)
_INDEPENDENT_ANALYSIS_OBJECT = re.compile(
    r"\b(?:experiments?|experimentation|experiment design|causal inference|"
    r"(?:predictive |statistical |machine learning |ltv )models?|algorithms?|"
    r"pricing|monetization|unit economics|budget planning|forecasting)\b", re.I,
)
_SHARED_ECONOMIC_DUTY = re.compile(
    r"\bwork(?:s|ing)?\s+(?:closely\s+)?with\s+(?:the\s+)?"
    r"(?:general manager|finance|(?:unit|business|executive) leadership)\b"
    r"(?:\s+[\w&'-]+){0,6}\s+on\s+(?:[\w&'-]+\s+){0,5}"
    r"(?:p&l|profit and loss|pricing|monetization|unit economics|budget planning|"
    r"(?:business|commercial|strategic|product|budget|operational) decisions?)\b",
    re.I,
)


def _asserted(text: str, match: re.Match[str]) -> bool:
    """Check local negation without treating 'without direct reports' as no scope."""
    prefix, suffix = text[:match.start()], text[match.end():]
    negated = re.search(
        r"\b(?:no|not|never|without|don't|doesn't|isn't|aren't|won't)\b"
        r"(?:\s+\w+){0,5}\s*$", prefix, re.I,
    )
    if re.search(r"\bnot (?:only|just)\s*$", prefix, re.I):
        negated = False
    return not (negated or re.match(
        r"\s*(?:(?:is|are|will be)\s+)?(?:not|never|isn't|aren't)\b", suffix, re.I,
    ))


def _own_action(text: str, match: re.Match[str]) -> bool:
    """An action inside a collaborator's title/duties is not the vacancy's scope."""
    prefix = text[:match.start()]
    if re.search(r"\b(?:our|your|their|its|his|her|my)\s+$", prefix, re.I):
        # "our own tools" is not the action "own tools".
        return False
    collaborator = _COLLABORATOR.search(prefix)
    if collaborator:
        following = prefix[collaborator.end():]
        if re.search(r"\b(?:who|whose|that)\b", following, re.I):
            return False
        if not re.search(r"\bto\b|\band\s*$", following, re.I):
            return False
    return _asserted(text, match)


def _shared_economics(text: str) -> bool:
    return any(
        _asserted(text, match)
        and not re.search(r"\b(?:who|whose|that)\b", match.group(), re.I)
        for match in _SHARED_ECONOMIC_DUTY.finditer(text)
    )


def _independent_analysis(text: str) -> bool:
    """A separate experiment/economic task survives reports in the same sentence."""
    actions = [match for match in _ANALYSIS_OWNERSHIP.finditer(text) if _own_action(text, match)]
    return any(
        _asserted(text, target)
        and any(
            0 <= target.start() - action.end() <= 100
            and not _REPORT_OUTPUT.search(text[action.start():target.end()])
            for action in actions
        )
        for target in _INDEPENDENT_ANALYSIS_OBJECT.finditer(text)
    )


def _duty_units(record: JobRecord) -> list[tuple[str, str]]:
    duties: list[tuple[str, str]] = []
    blocked = False
    local_role_override = False
    for unit in source_units(record):
        if not unit["field"].startswith("description_"):
            continue
        text, section = unit["text"], unit["section"]
        if _BLOCK_HEADING.search(text):
            blocked = True
            local_role_override = False
        elif _DUTY_HEADING.search(text):
            blocked = False
            local_role_override = section in {"company", "requirements"}
        # Some sources flatten an unrecognised "You will:" heading into the
        # preceding company paragraph. Preserve its original units and recover
        # only the following explicit responsibilities, until the next heading.
        inline_duties = re.search(r"\byou will\s*:\s*", text, re.I)
        if inline_duties:
            text = text[inline_duties.end():]
            blocked = False
            local_role_override = True
        if local_role_override:
            section = "role"
        if blocked or section in {"company", "requirements"}:
            continue
        for raw_clause in assertion_clauses(text):
            clause = raw_clause.strip().lstrip("•*- ")
            if not clause or "?" in clause or _NAVIGATION.search(clause):
                continue
            if _UNCERTAIN.search(clause) or _NEGATIVE_DUTY.search(clause):
                continue
            if _COMPANY_SUBJECT.search(clause) and not _EXPLICIT_DUTY.search(clause):
                continue
            if (_HISTORY.search(clause) or _HISTORY_PREFIX.search(clause)) and not (
                _EXPLICIT_DUTY.search(clause)
            ):
                continue
            shared_economics = _shared_economics(clause)
            if not _ACTION_RE.search(clause) and not shared_economics:
                continue
            if section != "role" and not (
                _IMPERATIVE.search(clause) or _ROLE_SUBJECT.search(clause) or shared_economics
            ):
                continue
            duties.append((unit["unit_id"], raw_clause.strip()))
    return duties


@dataclass(slots=True)
class FitEvidence:
    duties_text: str
    project_leadership: bool = False
    analytical_mandate: bool = False
    substantive_decision: bool = False
    reporting_core: bool = False
    signals: list[dict[str, Any]] = field(default_factory=list)
    _units: list[tuple[str, str]] = field(default_factory=list, repr=False)

    def _signal(self, unit_id: str, quote: str, concept: str) -> None:
        signal = {"unit_id": unit_id, "quote": quote, "concept": concept}
        if signal not in self.signals:
            self.signals.append(signal)

    def matched_weights(self, weights: dict[str, float]) -> list[tuple[str, float]]:
        """Match each configured key once; aliases never introduce new weights."""
        matches = []
        for phrase, weight in weights.items():
            normalized = normalize_text(phrase)
            concept = {
                "experimentation": "experimentation",
                "business decisions": "business decisions",
                "project leadership": "project leadership",
            }.get(normalized)
            if concept is not None:
                hits = [signal for signal in self.signals if signal["concept"] == concept]
            else:
                hits = []
                for unit_id, quote in self._units:
                    if not asserted_phrase_hits(quote, [phrase], required=True):
                        continue
                    if normalized == "decision science" and _COLLABORATOR.search(quote):
                        continue
                    # Reporting's generic decision language does not establish
                    # an independent decision-support mandate.
                    if normalized in {"strategic decisions", "decision support"} and (
                        _REPORT_OUTPUT.search(quote) and self.reporting_core
                    ):
                        continue
                    hits.append({"unit_id": unit_id, "quote": quote, "concept": phrase})
            if hits:
                matches.append((phrase, float(weight)))
                for hit in hits:
                    self._signal(hit["unit_id"], hit["quote"], phrase)
        return matches


def description_evidence(record: JobRecord) -> FitEvidence:
    """Extract a bounded set of positive duty concepts with original-source quotes."""
    units = _duty_units(record)
    evidence = FitEvidence(duties_text="\n".join(text for _, text in units), _units=units)
    reporting: list[tuple[str, str]] = []
    standalone_analysis = False
    for unit_id, quote in units:
        for match in (*_LEADERSHIP.finditer(quote), *_FUNCTION_LEADERSHIP.finditer(quote)):
            if _own_action(quote, match):
                evidence.project_leadership = True
                evidence._signal(unit_id, quote, "project leadership")
        report = any(_own_action(quote, match) for match in _REPORT_OWNERSHIP.finditer(quote))
        if report:
            reporting.append((unit_id, quote))
        independent = not _REPORT_OUTPUT.search(quote) or _independent_analysis(quote)
        analytical = any(
            _asserted(quote, match) for match in _ANALYTICAL_WORK.finditer(quote)
        )
        owned_analysis = any(
            _own_action(quote, match) for match in _ANALYSIS_OWNERSHIP.finditer(quote)
        )
        if analytical and owned_analysis and independent:
            standalone_analysis = True
            evidence.analytical_mandate = True
            evidence._signal(unit_id, quote, "analytical mandate")
        experiment_matches = list(_EXPERIMENT.finditer(quote))
        if independent and any(_asserted(quote, match) for match in experiment_matches):
            evidence._signal(unit_id, quote, "experimentation")
        decisions = [match for match in _DECISION.finditer(quote) if _asserted(quote, match)]
        decision_actions = [
            match for match in _DECISION_ACTION.finditer(quote) if _own_action(quote, match)
        ]
        product_actions = [
            match for match in _PRODUCT_ACTION.finditer(quote) if _own_action(quote, match)
        ]
        shared_economics = _shared_economics(quote)
        if independent and (
            (decisions and decision_actions) or product_actions or shared_economics
        ):
            evidence.substantive_decision = True
            evidence._signal(unit_id, quote, "substantive decision")
            if decisions:
                evidence._signal(unit_id, quote, "business decisions")
        if (
            independent and owned_analysis and not _TECHNICAL_OUTPUT.search(quote)
            and any(_asserted(quote, match) for match in _ECONOMIC_WORK.finditer(quote))
        ):
            evidence.substantive_decision = True
            evidence._signal(unit_id, quote, "substantive decision")
    evidence.reporting_core = bool(reporting) and not (
        standalone_analysis or evidence.substantive_decision
    )
    if evidence.reporting_core:
        for unit_id, quote in reporting:
            evidence._signal(unit_id, quote, "reporting core")
    return evidence
