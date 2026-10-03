"""Source-preserving vacancy text units, evidence normalization and scoped signals.

The source is never rewritten to make it look like an extracted fact. Sections are
annotations on exact source fragments, and every character of the selected source
description remains available to extraction and review, including its final tail.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import UTC, date, datetime
from typing import Any

from bs4 import BeautifulSoup

from job_intake.models.job import JobRecord
from job_intake.utils.text import canonicalize_url, contains_any, normalize_text

TEXT_VERSION = "source-units-v1"
_FIELDS = (
    "title", "company", "location_text", "remote_text", "employment_type",
    "salary_text", "timezone_text",
)
_HEADINGS = {
    "company": (
        "about us", "about the company", "about company", "who we are", "our company",
        "our mission", "our values", "company overview", "company description",
    ),
    "role": (
        "about the role", "the role", "your role", "role overview", "responsibilities",
        "key responsibilities", "your responsibilities", "what you will do",
        "what you'll do", "what you’ll do", "what you do", "your impact", "the opportunity",
        "job description", "what you will own", "in this role", "duties",
    ),
    "requirements": (
        "requirements", "qualifications", "required qualifications", "what you bring",
        "who you are", "what we are looking for", "what we're looking for",
        "what we’re looking for", "skills and experience", "preferred qualifications",
        "nice to have", "basic qualifications", "minimum qualifications", "eligibility",
        "location requirements", "working location", "candidate requirements",
    ),
    "other": (
        "benefits", "perks and benefits", "what we offer", "compensation", "salary",
        "how to apply", "application process", "equal opportunity", "legal notice",
    ),
}
_ROLE_CUES = re.compile(
    r"\b(?:you(?: will|['’]ll| are)|your (?:role|responsibilities|experience)|"
    r"this (?:role|position)|candidates?|applicants?|must |required |requirements?|"
    r"responsibilities|we (?:are hiring|hire|cannot hire))\b", re.I,
)
_HIRING_CUES = re.compile(
    r"\b(?:you(?: will|['’]ll| are)|your (?:role|responsibilities|experience)|"
    r"this (?:role|position)|candidates?|applicants?|responsibilities|"
    r"we (?:are hiring|hire|cannot hire))\b", re.I,
)
_DIRECT_ROLE_CUES = re.compile(
    r"\b(?:you(?: will|['’]ll| are| must)|your (?:role|responsibilities|experience)|"
    r"this (?:role|position|job)|(?:the|this) vacancy|candidates?|applicants?)\b", re.I,
)
_COMPANY_LANGUAGE = re.compile(
    r"^\s*(?:(?:our|the|their) (?:company|organization|business|teams?|customers?|"
    r"clients?|offices?|platform|website|documentation)\b|we (?:speak|work|operate)\b)"
    r"[^.!?\n]*\b(?:working language|languages?|speak|spoken|fluent|proficiency)\b", re.I,
)
_FOOTPRINT = re.compile(
    r"^\s*(?:[-•]\s*)?(?:(?:our|the company['’]?s?|we have|we serve|we support|we operate)"
    r"\b.*\b(?:offices?|headquarters?|customers?|clients?|markets?|users?|employees?|"
    r"countries|globally)|"
    r"(?:headquartered|offices? (?:are )?(?:in|located)|customers? (?:in|across)|"
    r"serving customers|founded in|we are (?:a|an) (?:global|worldwide)))\b", re.I,
)
_NAVIGATION = re.compile(
    r"^\s*(?:[>#*\-]\s*)?(?:apply (?:now|today)|share (?:this )?(?:job|vacancy)|"
    r"back to (?:jobs|careers)|view all jobs|follow us(?: on .*)?|"
    r"privacy policy|cookie policy|terms (?:of use|and conditions))\s*[.!:]?\s*$", re.I,
)


def normalize_quote(text: str) -> str:
    """Normalize layout only; retain letters, punctuation, case and accents."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text)).strip()


def _heading(text: str) -> str | None:
    label = re.sub(r"^[\s#>*\-]+", "", text).strip()
    # Inline headings such as "Requirements: English required" still set scope.
    prefix = label.split(":", 1)[0].strip().strip("*_").casefold()
    for section, labels in _HEADINGS.items():
        if prefix in labels:
            return section
    return None


def _raw_heading_positions(record: JobRecord, text: str) -> dict[int, str]:
    """Recover heading scope lost by HTML-to-one-line adapters, without rewriting text."""
    raw = record.description_raw
    if "<" not in raw or ">" not in raw:
        return {}
    soup = BeautifulSoup(raw, "html.parser")
    positions: dict[int, str] = {}
    cursor = 0
    for node in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "strong", "b"]):
        label = node.get_text(" ", strip=True)
        section = _heading(label)
        if section is None or not label:
            continue
        pattern = r"\s+".join(re.escape(word) for word in label.split())
        match = re.search(pattern, text[cursor:])
        if match is not None:
            position = cursor + match.start()
            positions[position] = section
            cursor += match.end()
    return positions


def _description_units(
    text: str, field: str, heading_positions: dict[int, str] | None = None,
) -> list[dict[str, Any]]:
    units: list[dict[str, Any]] = []
    section = "other"
    heading_positions = dict(heading_positions or {})
    # Sentence boundaries preserve the boundary whitespace in the next fragment.
    # Newlines remain attached to their line, so concatenation exactly recovers input.
    for line_match in re.finditer(r"[^\r\n]*(?:\r\n|\r|\n|$)", text):
        line = line_match[0]
        if not line:
            continue
        heading = _heading(line)
        if heading is not None:
            heading_positions[line_match.start()] = heading
        starts = sorted({
            0, *[match.end() for match in re.finditer(r"[.!?](?=\s+\S)", line)],
            *[position - line_match.start() for position in heading_positions
              if line_match.start() <= position < line_match.end()],
        })
        for start, end in zip(starts, starts[1:] + [len(line)], strict=True):
            fragment = line[start:end]
            if line_match.start() + start in heading_positions:
                section = heading_positions[line_match.start() + start]
            fragment_section = section
            if (
                _COMPANY_LANGUAGE.search(fragment) and not _DIRECT_ROLE_CUES.search(fragment)
            ) or (_FOOTPRINT.search(fragment) and not _HIRING_CUES.search(fragment)):
                fragment_section = "company"
            elif section == "company" and _ROLE_CUES.search(fragment):
                # A hiring condition embedded in About us must remain a condition.
                fragment_section = "requirements"
            units.append({
                "unit_id": f"{field}:{len(units) + 1:06d}",
                "field": field,
                "text": fragment,
                "section": fragment_section,
                "offset_start": line_match.start() + start,
                "offset_end": line_match.start() + end,
            })
    return units


def source_units(record: JobRecord) -> list[dict[str, Any]]:
    """Return source units with stable local IDs and exact, attributable text."""
    units: list[dict[str, Any]] = []
    for field in _FIELDS:
        text = getattr(record, field)
        if isinstance(text, str) and text:
            units.append({
                "unit_id": f"{field}:000001", "field": field, "text": text,
                "section": "company" if field == "company" else (
                    "role" if field == "title" else "requirements"
                ),
                "offset_start": 0, "offset_end": len(text),
            })
    field = "description_clean" if record.description_clean else "description_raw"
    description = getattr(record, field)
    units.extend(_description_units(
        description, field, _raw_heading_positions(record, description)
    ))
    for key in ("applicant_location_requirements", "working_language"):
        value = record.source_metadata.get(key)
        values = value if isinstance(value, list) else [value]
        for index, text in enumerate(values, start=1):
            if not isinstance(text, str) or not text:
                continue
            field = f"source_metadata.{key}"
            units.append({
                "unit_id": f"{field}:{index:06d}", "field": field, "text": text,
                "section": "requirements", "offset_start": 0, "offset_end": len(text),
            })
    return units


def _json_default(value: Any) -> str:
    if isinstance(value, date | datetime):
        return value.isoformat()
    raise TypeError(f"Unsupported source value type: {type(value).__name__}")


def _hash(payload: Any) -> str:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      default=_json_default)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def source_identity(record: JobRecord) -> str:
    """Qualify an external source ID globally without changing job deduplication."""
    identity = (
        {"source_job_id": record.source_job_id}
        if record.source_job_id else {"url": canonicalize_url(record.original_url)}
    )
    if not record.source_job_id and not identity["url"]:
        identity = {"company": record.company, "title": record.title}
    return "source-" + _hash({"source": record.source, **identity})


def source_hash(record: JobRecord) -> str:
    """Hash original content and metadata; derived annotation is deliberately absent."""
    fields = (
        *_FIELDS, "source", "source_job_id", "original_url", "apply_url", "posted_at",
        "description_raw", "description_clean", "status", "source_metadata",
    )
    payload = {"text_version": TEXT_VERSION, **{
        field: getattr(record, field) for field in fields
    }}
    posted_at = record.posted_at
    if posted_at is not None:
        # SQLite DateTime restores UTC timestamps without tzinfo. Canonicalizing
        # to a UTC wall-clock value keeps reviewed source hashes stable on reload.
        if posted_at.tzinfo is not None:
            posted_at = posted_at.astimezone(UTC).replace(tzinfo=None)
        payload["posted_at"] = posted_at.isoformat(timespec="microseconds")
    return _hash(payload)


def chunk_source(units: list[dict[str, Any]], max_chars: int) -> list[dict[str, Any]]:
    """Bound model input without silently dropping or duplicating source characters.

    A very long unit is split into attributed fragments, each in its own chunk.
    Fragments retain the original unit ID and offsets, so evidence validation uses
    precisely the part shown to that model, rather than an unseen tail of the unit.
    """
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    chunks: list[dict[str, Any]] = []
    fragments: list[dict[str, Any]] = []
    text_parts: list[str] = []

    def flush() -> None:
        if fragments:
            chunks.append({
                "chunk_id": f"chunk-{len(chunks) + 1:06d}",
                "unit_ids": [fragment["unit_id"] for fragment in fragments],
                "fragments": list(fragments), "units": list(fragments),
                "text": "\n\n".join(text_parts),
            })
            fragments.clear()
            text_parts.clear()

    for unit in units:
        original = unit["text"]
        marker = f"[{unit['unit_id']} | {unit['field']} | {unit['section']}]\n"
        capacity = max_chars - len(marker)
        if capacity < 1:
            raise ValueError("max_chars is too small for a source unit marker")
        start = 0
        while start < len(original):
            end = min(start + capacity, len(original))
            # Prefer a whitespace boundary; slicing still preserves that whitespace.
            if end < len(original):
                boundary = max(original.rfind(" ", start, end), original.rfind("\n", start, end))
                if boundary > start + capacity // 2:
                    end = boundary + 1
            text = original[start:end]
            rendered = marker + text
            projected = len("\n\n".join([*text_parts, rendered]))
            if fragments and (projected > max_chars or any(
                fragment["unit_id"] == unit["unit_id"] for fragment in fragments
            )):
                flush()
            fragments.append({
                "unit_id": unit["unit_id"], "field": unit["field"],
                "section": unit["section"], "text": text, "start": start, "end": end,
                "offset_start": unit.get("offset_start", 0) + start,
                "offset_end": unit.get("offset_start", 0) + end,
            })
            text_parts.append(rendered)
            start = end
    flush()
    return chunks


def scoped_description_text(description: str) -> str:
    """Select actual role context for fit while leaving the original source intact."""
    return "\n".join(
        unit["text"] for unit in _description_units(description, "description_clean")
        if unit["section"] != "company" and not _NAVIGATION.fullmatch(unit["text"])
    )


def role_description(record: JobRecord) -> str:
    return "\n".join(
        unit["text"] for unit in source_units(record)
        if unit["field"].startswith("description_") and unit["section"] != "company"
        and not _NAVIGATION.fullmatch(unit["text"])
    )


def asserted_phrase_hits(text: str, phrases: list[str], *, required: bool = False) -> list[str]:
    """Ignore negated signals and optional/quoted questions as hard requirements.

    Matching remains compatible with existing configured whole-token English
    phrases, but each occurrence is checked in its own sentence/clause context.
    Ambiguity is left for manual review instead of becoming a false hard rejection.
    """
    found: set[str] = set()
    for clause in re.split(r"[.;!\n|]+|\b(?:but|however|although)\b", text, flags=re.I):
        normalized = normalize_text(clause)
        for phrase in contains_any(clause, phrases):
            normalized_phrase = normalize_text(phrase)
            for match in re.finditer(
                r"(?<![a-z0-9])" + re.escape(normalized_phrase) + r"(?![a-z0-9])",
                normalized,
            ):
                prefix = normalized[:match.start()]
                suffix = normalized[match.end():]
                negated = bool(re.search(
                    r"\b(?:no|not|never|without|don t|doesn t|isn t|aren t|won t)\b"
                    r"(?:\s+\w+){0,5}\s*$", prefix
                ) or re.match(
                    r"\s*(?:(?:is|are|will be|experience is)\s+)?"
                    r"(?:not|never|isn t|aren t)\b", suffix
                ) or re.match(
                    r"\s*(?:(?:skills|proficiency|fluency|communication|experience|"
                    r"language|knowledge|is|are|was|were|will|be)\s+){0,4}"
                    r"(?:not|isn t|aren t|wasn t|weren t)\s+"
                    r"(?:\w+\s+){0,2}(?:required|needed|necessary|mandatory|expected)\b",
                    suffix,
                ))
                if re.search(r"\bnot only\s*$", prefix):
                    negated = False
                optional = required and bool(re.search(
                    r"\b(?:optional|preferred|nice to have|a plus)"
                    r"(?:\s+\w+){0,4}\s*$", prefix,
                ) or re.match(
                    r"\s*(?:(?:is|are|will be|experience is)\s+)?"
                    r"(?:optional|preferred|nice to have|a plus)\b", suffix,
                ) or "?" in clause)
                if not negated and not optional:
                    found.add(phrase)
    return [phrase for phrase in phrases if phrase in found]


def eligibility_description(record: JobRecord) -> str:
    """Geography scope: company/customer footprint alone is not applicant eligibility."""
    return "\n".join(
        unit["text"] for unit in source_units(record)
        if unit["field"].startswith("description_")
        and unit["section"] != "company"
    )
