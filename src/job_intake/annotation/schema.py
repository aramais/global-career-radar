"""Validate source-grounded vacancy claims before they can affect classification.

Model output is a draft. A matching quote proves provenance, not its meaning;
semantic review and the conservative gates below remain separate requirements.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections import Counter, defaultdict
from copy import deepcopy
from typing import Any

CLAIM_KINDS = frozenset(
    {
        "role",
        "responsibility",
        "skill",
        "hiring_location",
        "work_mode",
        "working_language",
        "employment",
        "timezone",
        "salary",
        "status",
        "work_authorization",
    }
)
REQUIREMENTS = frozenset({"required", "preferred", "not_required", "informational"})
POLARITIES = frozenset({"affirmative", "negative"})
REVIEW_STATUSES = frozenset(
    {
        "SUPPORTED",
        "WEAKLY_SUPPORTED",
        "ANECDOTAL",
        "CONFLICTING",
        "UNSUPPORTED",
        "NEEDS_VERIFICATION",
    }
)
COVERAGE_KINDS = (
    "hiring_location",
    "working_language",
    "work_mode",
    "timezone",
    "salary",
    "employment",
    "work_authorization",
)
_COMPANY_SECTIONS = frozenset(
    {"company", "company_context", "about_company", "company_overview", "about_us"}
)
_OPTIONAL = re.compile(
    r"\b(?:not|no)\b(?!\W+only\b)(?:\W+\w+){0,4}\W+(?:required|necessary|needed)\b"
    r"|\b(?:optional|preferred)\b|\bnice[ -]to[ -]have\b",
    re.IGNORECASE,
)
_LANGUAGE_NAMES = {
    "english": ("English", "inglês", "inglés", "anglais", "английский"),
    "portuguese": ("Portuguese", "português", "portugués", "portugais", "португальский"),
    "spanish": ("Spanish", "español", "espanhol", "испанский"),
    "french": ("French", "français", "francês", "французский"),
    "german": ("German", "Deutsch", "alemão", "немецкий"),
    "italian": ("Italian", "italiano", "итальянский"),
    "japanese": ("Japanese", "日本語", "японский"),
    "chinese": ("Chinese", "中文", "китайский"),
    "russian": ("Russian", "русский"),
    "arabic": ("Arabic", "العربية", "арабский"),
    "korean": ("Korean", "한국어", "корейский"),
}
_LANGUAGE_PATTERN = (
    "(?:" + "|".join(re.escape(name) for names in _LANGUAGE_NAMES.values() for name in names) + ")"
)
_EXCLUSIVE_LANGUAGE = re.compile(
    rf"\b{_LANGUAGE_PATTERN}\s+only(?=\s*[.!;,)]|\s*$)"
    rf"|\bonly\s+{_LANGUAGE_PATTERN}(?=\s*[.!;,)]|\s*$)"
    rf"|\b{_LANGUAGE_PATTERN}\s+is\s+(?:the\s+)?only\s+working\s+language\b"
    rf"|\bthe\s+only\s+working\s+language\s+is\s+{_LANGUAGE_PATTERN}\b"
    rf"|\b(?:work|communicate|communication)\w*\s+exclusively\s+in\s+"
    rf"{_LANGUAGE_PATTERN}\b",
    re.IGNORECASE,
)
_NEGATED_LANGUAGE = re.compile(
    rf"\b(?:not|no)\s+{_LANGUAGE_PATTERN}\b"
    rf"|\b{_LANGUAGE_PATTERN}\s+(?:(?:is|will\s+be|are)\s+)?"
    rf"not\b(?!\s+only\b)",
    re.IGNORECASE,
)
_UNCONFIRMED = re.compile(
    r"\bnot\s+(?:yet\s+)?confirmed\b"
    r"|\b(?:unconfirmed|unclear|undetermined|unknown|might|possibly)\b"
    r"|\bmay\s+(?:be|change|vary|require|hire)\b",
    re.IGNORECASE,
)
_WORLDWIDE = re.compile(
    r"\b(?:worldwide|world-wide|globally|anywhere|any country|all countries|any location)\b",
    re.IGNORECASE,
)
_EXCLUSIVE_LOCATION = re.compile(
    r"\bonly\s+(?:in|from|within)\b"
    r"|\bonly(?:\W+\w+){0,3}\W+(?:residents|candidates|applicants)\b"
    r"|\b[^\W\d_][\w'-]*\s+only(?=\s*[.!;,)]|\s*$)",
    re.IGNORECASE,
)


def _normalize_quote(value: str) -> str:
    # Case and punctuation are evidence, and must never be discarded.
    return " ".join(unicodedata.normalize("NFC", value).split())


def _envelope(payload: Any, key: str) -> list[Any]:
    if not isinstance(payload, dict) or not isinstance(payload.get(key), list):
        raise ValueError(f"Annotation response must be an object containing {key}[]")
    return payload[key]


def _nonblank(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _reject(index: int, record: Any, reason: str) -> dict[str, Any]:
    return {"index": index, "record": deepcopy(record), "reason": reason}


def _unit_index(units: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for unit in units:
        if not isinstance(unit, dict) or not all(
            _nonblank(unit.get(key)) for key in ("unit_id", "field", "text")
        ):
            raise ValueError("Trusted annotation units require unit_id, field and text")
        if unit["unit_id"] in indexed:
            raise ValueError("Trusted annotation unit IDs must be unique within a chunk")
        if not isinstance(unit.get("section", "other"), str):
            raise ValueError("Trusted annotation unit section must be a string")
        indexed[unit["unit_id"]] = unit
    return indexed


def _claim_problem(claim: Any, units: dict[str, dict[str, Any]]) -> str | None:
    if not isinstance(claim, dict):
        return "Claim must be an object"
    for key, allowed in (
        ("kind", CLAIM_KINDS),
        ("requirement", REQUIREMENTS),
        ("polarity", POLARITIES),
    ):
        if not isinstance(claim.get(key), str) or claim[key] not in allowed:
            return f"Invalid claim {key}"
    for key in ("value", "unit_id", "source_snippet"):
        if not _nonblank(claim.get(key)):
            return f"Claim {key} must be a nonblank string"
    if type(claim.get("is_inference")) is not bool:
        return "Claim is_inference must be a Boolean"
    if claim["unit_id"] not in units:
        return "Claim unit_id is outside its own chunk"
    unit = units[claim["unit_id"]]
    snippet = _normalize_quote(claim["source_snippet"])
    if snippet not in _normalize_quote(unit["text"]):
        return "Claim source_snippet is not verbatim in its referenced unit"
    # Short values in structured fields are real evidence (e.g. location=BR).
    # An isolated keyword from prose does not establish a job requirement.
    if unit["field"].startswith("description") and (len(snippet) < 8 or len(snippet.split()) < 2):
        return "Claim source_snippet is too short or generic to establish a prose fact"
    return None


def _evidence_warnings(claim: dict[str, Any], unit: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    if claim["is_inference"]:
        warnings.append("inference_requires_verification")
    if unit.get("section", "other").strip().casefold() in _COMPANY_SECTIONS:
        warnings.append("company_context_is_not_a_vacancy_requirement")
    snippet = _normalize_quote(claim["source_snippet"])
    # A model may quote "English required" out of "No English required".
    # Keep a nearby clause prefix, so exact substring matching cannot erase "no".
    source_text = _normalize_quote(unit["text"])
    snippet_start = source_text.find(snippet)
    prefix = re.split(r"[.;!?]", source_text[:snippet_start])[-1][-80:]
    context = prefix + snippet
    subjects = _language_entities(claim["value"]) if claim["kind"] == "working_language" else set()
    clauses = re.split(r"[,;.!?]|\b(?:but|whereas|while)\b", context, flags=re.IGNORECASE)
    # An optional French skill elsewhere in a quote is not an optional English
    # working language. Do not let unrelated clauses block a supported fact.
    relevant = [clause for clause in clauses if subjects & _language_entities(clause)]
    if unit["field"].startswith("description"):
        if source_text.rstrip().endswith("?") or snippet.rstrip().endswith("?"):
            warnings.append("question_is_not_a_direct_assertion")
        if (
            claim["kind"] in {"working_language", "hiring_location", "work_mode"}
            and claim["polarity"] == "affirmative"
            and any(_UNCONFIRMED.search(clause) for clause in (relevant or [context]))
        ):
            warnings.append("unconfirmed_or_conditional_evidence_requires_verification")
    optional = any(_OPTIONAL.search(clause) for clause in (relevant or [context]))
    if claim["requirement"] == "required" and optional:
        warnings.append("required_claim_conflicts_with_optional_or_negated_evidence")
    if (
        claim["kind"] == "working_language"
        and claim["polarity"] == "affirmative"
        and claim["requirement"] != "not_required"
        and (
            optional or any(_NEGATED_LANGUAGE.search(clause) for clause in (relevant or [context]))
        )
    ):
        warnings.append("optional_or_negated_language_is_not_working_language_confirmation")
    return warnings


def prepare_claims(
    payload: dict[str, Any], source_id: str, units: list[dict[str, Any]], chunk_id: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Validate each draft against the exact trusted units supplied to its call.

    An invalid response envelope fails atomically. Invalid individual claims are
    preserved in rejects, including duplicate drafts, rather than dropped.
    """
    rows = _envelope(payload, "claims")
    if not _nonblank(source_id) or not _nonblank(chunk_id):
        raise ValueError("Trusted source_id and chunk_id must be nonblank strings")
    indexed = _unit_index(units)
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, row in enumerate(rows):
        problem = _claim_problem(row, indexed)
        if not problem and (
            ("source_id" in row and row["source_id"] != source_id)
            or ("chunk_id" in row and row["chunk_id"] != chunk_id)
        ):
            problem = "Claim invents a source_id or chunk_id"
        if problem:
            rejected.append(_reject(index, row, problem))
            continue
        unit = indexed[row["unit_id"]]
        identity = [
            source_id,
            *(
                _normalize_quote(row[key])
                for key in ("kind", "value", "unit_id", "source_snippet", "requirement", "polarity")
            ),
        ]
        digest = hashlib.sha256(
            json.dumps(identity, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        claim_id = f"{source_id}:claim:{digest}"
        if claim_id in seen:
            rejected.append(_reject(index, row, "Duplicate claim in extraction response"))
            continue
        seen.add(claim_id)
        claim = {
            key: deepcopy(row[key])
            for key in (
                "kind",
                "value",
                "unit_id",
                "source_snippet",
                "requirement",
                "polarity",
                "is_inference",
            )
        }
        claim.update(
            claim_id=claim_id,
            source_id=source_id,
            chunk_id=chunk_id,
            source_field=unit["field"],
            source_section=unit.get("section", "other"),
            review_status="UNREVIEWED",
            evidence_warnings=_evidence_warnings(claim, unit),
        )
        accepted.append(claim)
    return accepted, rejected


def review_claims(
    claims: list[dict[str, Any]], payload: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Attach exact-ID reviews without allowing reviewers to rewrite evidence.

    Missing, malformed and duplicate reviews fail closed for their claim. A
    reviewer can downgrade a draft, but cannot remove its original inference or
    provenance warnings to promote it to accepted support.
    """
    rows = _envelope(payload, "reviews")
    known = {claim.get("claim_id"): claim for claim in claims}
    if len(known) != len(claims) or not all(_nonblank(key) for key in known):
        raise ValueError("Review input claims require unique nonblank claim IDs")
    counts = Counter(
        row["claim_id"] for row in rows if isinstance(row, dict) and _nonblank(row.get("claim_id"))
    )
    reviews: dict[str, dict[str, Any]] = {}
    rejected: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        problem: str | None = None
        if not isinstance(row, dict) or not _nonblank(row.get("claim_id")):
            problem = "Review must identify a claim with a nonblank claim_id"
        elif row["claim_id"] not in known:
            problem = "Review claim_id does not identify an input claim"
        elif counts[row["claim_id"]] != 1:
            problem = "Duplicate reviews for the same claim_id"
        elif (
            not isinstance(row.get("review_status"), str)
            or row["review_status"] not in REVIEW_STATUSES
        ):
            problem = "Invalid review_status"
        elif "is_inference" in row and type(row["is_inference"]) is not bool:
            problem = "Review is_inference must be a Boolean"
        elif "review_notes" in row and not isinstance(row["review_notes"], str):
            problem = "Review review_notes must be a string"
        if problem:
            rejected.append(_reject(index, row, problem))
        else:
            reviews[row["claim_id"]] = row
    reviewed: list[dict[str, Any]] = []
    for original in claims:
        claim = deepcopy(original)
        review = reviews.get(claim["claim_id"])
        if review is None:
            claim.update(
                review_status="NEEDS_VERIFICATION",
                review_notes="No unique valid review was supplied for this claim",
            )
        else:
            inference = bool(original.get("is_inference")) or review.get("is_inference", False)
            status = review["review_status"]
            if status == "SUPPORTED" and (inference or claim.get("evidence_warnings")):
                status = "WEAKLY_SUPPORTED"
            claim.update(
                review_status=status,
                review_notes=review.get("review_notes", ""),
                is_inference=inference,
            )
        reviewed.append(claim)
    return reviewed, rejected


def _language_entities(value: str) -> set[str]:
    value = _normalize_quote(value)
    return {
        language
        for language, names in _LANGUAGE_NAMES.items()
        if any(re.search(rf"\b{re.escape(name)}\b", value, re.IGNORECASE) for name in names)
    }


def _exclusive_conflict(first: dict[str, Any], second: dict[str, Any]) -> bool:
    """Detect only explicit, narrowly grounded exclusions of positive facts.

    Multiple languages, skills and locations are ordinarily compatible. The
    special cases here require an actual exclusive clause, and avoid guessing
    geographical hierarchies or declaring arbitrary distinct values conflicts.
    """
    if first["kind"] != second["kind"] or any(
        claim["polarity"] != "affirmative"
        or claim["requirement"] not in {"required", "informational"}
        for claim in (first, second)
    ):
        return False
    for restricted, other in ((first, second), (second, first)):
        snippet = _normalize_quote(restricted["source_snippet"])
        # "Not only Portuguese but English" is expressly inclusive.
        snippet = re.sub(r"\bnot\s+only\b", "", snippet, flags=re.IGNORECASE)
        if first["kind"] == "working_language" and _EXCLUSIVE_LANGUAGE.search(snippet):
            # Preserve inclusive lists such as "English and Portuguese only"
            # even when an extractor emits one atomic claim for each language.
            allowed = _language_entities(restricted["value"]) | _language_entities(snippet)
            additional = _language_entities(other["value"])
            if allowed and additional - allowed:
                return True
        if (
            first["kind"] == "hiring_location"
            and _EXCLUSIVE_LOCATION.search(snippet)
            and not _WORLDWIDE.search(restricted["value"])
            and _WORLDWIDE.search(other["value"])
        ):
            return True
    return False


def summarize_claims(claims: list[dict[str, Any]]) -> dict[str, Any]:
    """Expose supported facts, absent coverage and unresolved contradictions."""
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    kinds: dict[str, list[dict[str, Any]]] = defaultdict(list)
    contradictions: list[dict[str, Any]] = []
    conflicted_ids: set[str] = set()
    for claim in claims:
        if claim.get("review_status") == "CONFLICTING":
            conflicted_ids.add(claim["claim_id"])
            contradictions.append(
                {
                    "kind": claim["kind"],
                    "value": claim["value"],
                    "claim_ids": [claim["claim_id"]],
                    "reason": claim.get("review_notes") or "Reviewer found conflicting evidence",
                }
            )
        if (
            claim.get("review_status") == "SUPPORTED"
            and not claim.get("is_inference")
            and not claim.get("evidence_warnings")
        ):
            groups[(claim["kind"], _normalize_quote(claim["value"]).casefold())].append(claim)
            kinds[claim["kind"]].append(claim)
    for (kind, _), group in groups.items():
        for index, first in enumerate(group):
            for second in group[index + 1 :]:
                requirement_conflict = {first["requirement"], second["requirement"]} == {
                    "required",
                    "not_required",
                }
                polarity_conflict = first["polarity"] != second["polarity"]
                if not requirement_conflict and not polarity_conflict:
                    continue
                ids = [first["claim_id"], second["claim_id"]]
                conflicted_ids.update(ids)
                contradictions.append(
                    {
                        "kind": kind,
                        "value": first["value"],
                        "claim_ids": ids,
                        "reason": "Opposite requirements or polarities in source evidence",
                    }
                )
    for kind in ("working_language", "hiring_location"):
        group = kinds[kind]
        for index, first in enumerate(group):
            for second in group[index + 1 :]:
                if _normalize_quote(first["value"]).casefold() == _normalize_quote(
                    second["value"]
                ).casefold() or not _exclusive_conflict(first, second):
                    continue
                ids = [first["claim_id"], second["claim_id"]]
                conflicted_ids.update(ids)
                contradictions.append(
                    {
                        "kind": kind,
                        "value": first["value"],
                        "values": [first["value"], second["value"]],
                        "claim_ids": ids,
                        "reason": "Exclusive source restriction conflicts with another fact",
                    }
                )
    knowns = [
        deepcopy(claim)
        for claim in claims
        if claim.get("review_status") == "SUPPORTED"
        and not claim.get("is_inference")
        and not claim.get("evidence_warnings")
        and claim["claim_id"] not in conflicted_ids
    ]
    coverage = {
        kind: any(
            claim["kind"] == kind
            and (
                kind != "working_language"
                or (
                    claim["polarity"] == "affirmative"
                    and claim["requirement"] in {"required", "informational"}
                )
            )
            for claim in knowns
        )
        for kind in COVERAGE_KINDS
    }
    return {
        "knowns": knowns,
        "unknowns": [kind for kind in COVERAGE_KINDS if not coverage[kind]],
        "contradictions": contradictions,
        "coverage": coverage,
        "supported_claim_ids": [claim["claim_id"] for claim in knowns],
    }
