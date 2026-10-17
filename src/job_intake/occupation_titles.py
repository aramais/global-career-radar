"""Shared occupation-title grammar for classification and configured score signals.

Title syntax is independent of candidate preferences and numeric scoring policy.
Equivalent titles reuse only a weight explicitly configured for the active role.
"""

from __future__ import annotations

import re
from typing import Any

from job_intake.utils.text import normalize_text

# Occupation syntax, not search preferences. Bounded domain modifiers allow
# "Manager, Commercial Analytics" without accepting arbitrary gaps or job duties.
_LEADER = r"(?:head|manager|director|lead|vp|vice president)(?: of)?"
_DOMAIN = (
    r"(?:product|commercial|marketing|business|performance|customer|financial|finance|"
    r"revenue|growth|operational|operations|sales|risk|clinical|real world|cybersecurity|"
    r"digital|enterprise|advanced|applied|global|regional|strategic|security|fraud|credit)"
)
_MODIFIERS = rf"(?:{_DOMAIN}\s+){{0,3}}"
_ANALYTICS = r"(?:reporting and )?analytics(?: and (?:insights|reporting))?"
_SCIENCE = r"data science(?: and analytics)?"
_TITLE_PATTERNS = {
    "analytics-leadership": [
        re.compile(rf"\b{_LEADER}\s+{_MODIFIERS}{_ANALYTICS}\b"),
        re.compile(rf"\b{_MODIFIERS}{_ANALYTICS}\s+(?:(?:chapter|team|project) )?{_LEADER}\b"),
    ],
    "data-science-management": [
        re.compile(rf"\b{_LEADER}\s+{_MODIFIERS}{_SCIENCE}\b"),
        re.compile(rf"\b{_MODIFIERS}{_SCIENCE}\s+{_LEADER}\b"),
    ],
    "business-analytics": [
        re.compile(
            rf"\b(?:{_LEADER}\s+)?business (?:performance )?analytics(?:\s+{_LEADER})?\b"
        ),
    ],
}


def _title_text(value: str) -> str:
    return normalize_text(value.replace("&", " and ").replace("-", " ").replace(":", " "))


def _occupation_match(title: str, match: re.Match[str], label: str) -> bool:
    """Reject a generic prefix belonging to a different advertised occupation.

    Check each occurrence: an independent Product Manager title is still usable
    in "Head of Product Design / Product Manager".
    """
    phrase, following = match.group(), title[match.end():].lstrip()
    if (
        label in {"analytics-leadership", "business-analytics", "data-science-management"}
        and re.match(rf"{_LEADER}\b", phrase)
        and re.search(
            r"\b(?:product|engineering|design|program|project|account|sales|marketing)\s+$",
            title[:match.start()],
        )
    ):
        # In "Product Manager, Analytics", manager belongs to Product Manager.
        return False
    if (
        label == "product-management" and re.search(r"\b(?:product|product lead)$", phrase)
        and re.match(
            r"(?:and )?(?:design(?:er)?|engineering|engineer|marketing|operations|"
            r"development|analytics|data science)\b", following,
        )
    ):
        return False
    if label == "analytics-leadership":
        if "data science" in phrase:
            return False
        if phrase.endswith("data") and re.match(
            r"(?:products?|engineering|engineer|platform|infrastructure|architecture|"
            r"governance|science)\b", following,
        ):
            return False
    if label in {"analytics-leadership", "business-analytics"} and (
        re.search(r"\b(?:analytics|insights)$", phrase) and re.match(
            r"(?:engineering|engineer|platform|products?|infrastructure)\b", following,
        )
    ):
        return False
    return not (label == "data-science-management" and re.match(
        r"(?:production engineering|engineering|engineer|platform|infrastructure)\b", following,
    ))

def _title_matches(value: str, taxonomy: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Return the classifier's span-aware title evidence for configured occupations."""
    title = _title_text(value)
    evidence = {}
    for row in taxonomy:
        patterns = [
            re.compile(r"(?<![a-z0-9])" + re.escape(_title_text(phrase)) + r"(?![a-z0-9])")
            for phrase in row["titles"] if _title_text(phrase)
        ] + _TITLE_PATTERNS.get(row["id"], [])
        evidence[row["id"]] = [
            match for pattern in patterns for match in pattern.finditer(title)
            if _occupation_match(title, match, row["id"])
        ]
    # Only discard a generic occurrence inside a more specific occupation.
    # A separate Analytics Manager / Business Analytics Manager stays hybrid.
    matches = {}
    for label, candidates in evidence.items():
        matches[label] = sorted({
            match.group() for match in candidates if not any(
                other.start() <= match.start() and other.end() >= match.end()
                and (match.span() != other.span() or (
                    label == "analytics-leadership" and other_label == "business-analytics"
                ))
                for other_label, others in evidence.items() if other_label != label
                for other in others
            )
        })
    return matches


_LEADER_WORD = r"(?:head|manager|director|lead|vp|vice president)"
_SCORE_CORES = {
    "analytics-leadership": _ANALYTICS,
    "business-analytics": _ANALYTICS,
    "data-science-management": _SCIENCE,
    "product-management": "product",
}
_PRODUCT_TITLE_PATTERNS = [
    re.compile(rf"\b{_LEADER}\s+product\b"),
    re.compile(rf"\bproduct\s+{_LEADER}\b"),
]


def _leadership_parts(value: str, role_id: str) -> tuple[str, tuple[str, ...]] | None:
    """Read one leadership title without equating different leadership levels.

    Core grammar is selected by the active occupation. Domain modifiers may make
    a configured generic title more specific, but never disappear from a weighted
    specific title: Product Analytics Lead does not mean Commercial Analytics Lead.
    """
    core = _SCORE_CORES[role_id]
    modifiers = _MODIFIERS if role_id != "product-management" else ""
    patterns = (
        rf"(?P<leader>{_LEADER_WORD})(?: of)?\s+"
        rf"(?P<modifiers>{modifiers}){core}",
        rf"(?P<modifiers>{modifiers}){core}\s+"
        rf"(?:(?:chapter|team|project) )?(?P<leader>{_LEADER_WORD})(?: of)?",
    )
    for pattern in patterns:
        if match := re.fullmatch(pattern, value):
            return match["leader"], tuple(re.findall(_DOMAIN, match["modifiers"]))
    return None


def title_weight_matches(
    title: str, weights: dict[str, float], role_id: str | None,
) -> list[tuple[str, float]]:
    """Return configured title-weight candidates, without adding or summing weights.

    Literal matches remain available to custom roles. Known occupations additionally
    share the classifier's bounded title grammar, preserving the leadership term
    and any domain required by the configured phrase. Each configured key appears
    once, even when the title contains multiple equivalent occurrences. The scorer
    chooses how to aggregate these candidates.
    """
    normalized_title = _title_text(title)
    label = role_id or ""
    candidates = []
    if role_id in _SCORE_CORES:
        patterns = _TITLE_PATTERNS.get(role_id, _PRODUCT_TITLE_PATTERNS)
        candidates = [
            parts for pattern in patterns for match in pattern.finditer(normalized_title)
            if _occupation_match(normalized_title, match, role_id)
            if (parts := _leadership_parts(match.group(), role_id)) is not None
        ]
    matches = []
    for phrase, weight in weights.items():
        normalized_phrase = _title_text(phrase)
        if not normalized_phrase:
            continue
        literal = re.compile(
            r"(?<![a-z0-9])" + re.escape(normalized_phrase) + r"(?![a-z0-9])"
        )
        direct_match = any(
            _occupation_match(normalized_title, match, label)
            for match in literal.finditer(normalized_title)
        )
        equivalent_match = False
        if role_id in _SCORE_CORES and (
            parts := _leadership_parts(normalized_phrase, role_id)
        ):
            leader, required_domains = parts
            equivalent_match = any(
                candidate_leader == leader and set(required_domains).issubset(domains)
                for candidate_leader, domains in candidates
            )
        if direct_match or equivalent_match:
            matches.append((phrase, float(weight)))
    return matches
