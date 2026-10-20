"""Conservative witnesses for operational hiring facts, not candidate preferences.

Models may select a real source ID while assigning an unrelated or contradictory
value. These checks establish the particular hiring predicate. Unrecognized or
ambiguous wording abstains; it never becomes a new hard exclusion.
"""

from __future__ import annotations

import re
import unicodedata

from job_intake.labeling.gold_v3 import migrate_tags


def _text(value: str) -> str:
    return " ".join(
        "".join(
            char for char in unicodedata.normalize("NFKD", value) if not unicodedata.combining(char)
        )
        .casefold()
        .split()
    )


# Alias recognition is a parser capability. It does not prefer one foreign
# country over another. Unknown names abstain instead of being invented places.
_FOREIGN_NAMES = (
    "united states",
    "united kingdom",
    "canada",
    "germany",
    "france",
    "spain",
    "portugal",
    "ireland",
    "netherlands",
    "italy",
    "switzerland",
    "sweden",
    "norway",
    "denmark",
    "poland",
    "finland",
    "belgium",
    "austria",
    "australia",
    "new zealand",
    "mexico",
    "argentina",
    "chile",
    "colombia",
    "peru",
    "uruguay",
    "india",
    "singapore",
    "japan",
    "china",
    "south korea",
    "israel",
    "united arab emirates",
    "south africa",
    "namibia",
    "europe",
    "european union",
    "north america",
    "emea",
    "apac",
    "asia",
    "africa",
    "middle east",
    "oceania",
)
_FOREIGN_CODES = re.compile(r"\b(?:US|USA|UK|EU|EEA)\b|\b(?i:u\.s\.?(?:a\.?)?)(?!\w)")
_CANDIDATE_PREFIX = (
    r"(?:(?:remote\s+)?(?:candidates?|applicants?|people|workers?|residents?|employees?|"
    r"talent|professionals?|team\s+members?)\s+)?"
    r"(?:(?:based|located|living|residing)\s+)?"
    r"(?:(?:in|from|of|within|across|throughout|anywhere\s+in)\s+)?"
)
# An opening's own audience: "open/available to X" lists who may apply, like
# "restricted/limited to X". Hiring in a country without such a scope does not.
_CLOSED_POLICIES = frozenset({"restricted", "limited", "open", "available"})
_ROLE_SUFFIX = r"(?:\s+(?:for|to\s+apply\s+for)\s+(?:this|the)\s+(?:role|position|opening|job))?"
_CANDIDATE_SCOPES = tuple(re.compile(pattern, re.I) for pattern in (
    r"(?:we\s+(?:are\s+|can\s+|will\s+)?(?:only\s+)?)?(?:hire|hires|hiring|recruit|recruiting)\s+"
    r"(?:(?:only|exclusively)\s+)?" + _CANDIDATE_PREFIX + r"(?P<places>.+?)",
    r"(?:hiring|recruitment|(?:this|the)\s+(?:opening|role|position))\s+"
    r"(?:is\s+)?(?:(?:fully\s+)?remote\s+and\s+)?(?:(?:strictly|currently)\s+)?"
    r"(?P<policy>restricted|limited|open|available)\s+(?:only\s+)?to\s+"
    + _CANDIDATE_PREFIX + r"(?P<places>.+?)",
    r"(?:candidates?|applicants?|you)(?:\s+for\s+(?:this|the)\s+role)?\s+must\s+"
    r"(?:live\s+in|reside\s+in|be\s+based\s+in|be\s+located\s+in|be\s+residents?\s+of)\s+"
    r"(?P<places>.+?)",
    r"(?:candidates?|applicants?|residents?)\s+"
    r"(?:(?:based|located|living|residing)\s+)?(?:in|from|of)\s+"
    r"(?P<places>.+?)\s+(?:are\s+)?(?:eligible|can\s+apply|welcome)",
    # Advert headline form, e.g. "Open to candidates in LATAM".
    r"(?P<policy>open|available)\s+(?:only\s+)?to\s+" + _CANDIDATE_PREFIX + r"(?P<places>.+?)",
    # A work-location permission naming the allowed region.
    r"(?:you\s+can\s+)?work\s+(?:remotely\s+)?from\s+anywhere\s+in\s+(?P<places>.+?)",
))
_NEGATIVE_CANDIDATE_SCOPES = tuple(re.compile(pattern, re.I) for pattern in (
    r"(?:we\s+)?(?:do\s+not|does\s+not|cannot|can\s+not|can't|will\s+not)\s+"
    r"(?:hire|accept|consider)\s+" + _CANDIDATE_PREFIX + r"(?P<places>.+?)",
    r"(?:candidates?|applicants?|residents?)\s+"
    r"(?:(?:based|located|living|residing)\s+)?(?:in|from|of)\s+"
    r"(?P<places>.+?)\s+(?:are\s+)?(?:not\s+eligible|ineligible|not\s+accepted|cannot\s+apply)"
    + _ROLE_SUFFIX,
    r"(?P<places>brazil|brasil)(?:ian)?\s+(?:candidates?|applicants?|residents?)\s+"
    r"(?:are\s+)?(?:not\s+eligible|ineligible|not\s+accepted|cannot\s+apply)" + _ROLE_SUFFIX,
    r"no\s+(?:candidates?|applicants?|residents?)\s+(?:from|in|of)\s+(?P<places>.+?)",
    r"(?:this|the)\s+(?:role|position|opening|job)\s+is\s+not\s+(?:open|available)\s+to\s+"
    + _CANDIDATE_PREFIX + r"(?P<places>.+?)",
    r"(?:we\s+are\s+)?not\s+(?:hiring|recruiting)\s+" + _CANDIDATE_PREFIX + r"(?P<places>.+?)",
))
_AUTHORIZATION_SCOPES = tuple(re.compile(pattern, re.I) for pattern in (
    r"(?P<places>.+?)\s+(?:work\s+authori[sz]ation|citizenship|work\s+permit|residency)\s+"
    r"(?:(?:is|are)\s+)?(?:required|mandatory|needed)",
    r"(?:you|candidates?|applicants?)\s+must\s+(?:have|hold|possess)\s+(?P<places>.+?)\s+"
    r"(?:work\s+authori[sz]ation|citizenship|work\s+permit|residency)",
    r"(?:you|candidates?|applicants?)\s+must\s+be\s+authori[sz]ed\s+to\s+work\s+in\s+"
    r"(?P<places>.+?)(?:\s+to\s+apply\s+for\s+(?:this|the)\s+role)?",
    r"(?:work\s+authori[sz]ation|citizenship|work\s+permit|right\s+to\s+work)\s+"
    r"(?:in|for|of)\s+(?P<places>.+?)\s+(?:(?:is|are)\s+)?(?:required|mandatory|needed)",
    r"(?:this|the)\s+(?:[\w-]+\s+){0,3}(?:role|position|opening)\s+requires?\s+"
    r"(?P<places>.+?)\s+(?:work\s+authori[sz]ation|citizenship|work\s+permit|residency)",
))
_REMOTE = re.compile(
    r"\b(?:fully remote|remote (?:role|position|job|work|working)|work(?:ing)? remotely|"
    r"work from home|work from anywhere|home office|(?:role|position|job) "
    r"(?:is |will be )?remote)\b"
)
_OFFICE = re.compile(r"\b(?:office|onsite|on site|on-site|hybrid|presencial|hibrid[oa])\b")
_OFFICE_COMMITMENT = re.compile(
    r"\b(?:hybrid|onsite|on site|on-site|presencial|hibrid[oa]|office-based|in-office)\b|"
    r"\b(?:work(?:ing)? (?:in|from|at)|based in|located in)"
    r"(?:\W+\w+){0,6}\W+office\b|"
    r"\boffice(?:\W+\w+){0,5}\W+(?:required|mandatory)\b"
)
_OFFICE_PLACE = re.compile(
    r"\b(?:office|onsite|on site|on-site|hybrid|presencial|hibrid[oa])\b"
    r"[^.;!?]{0,65}?\b(?:in|at|em|based in|located in)\s+([^.;!?]+)"
)
_OFFICE_LABEL = re.compile(
    r"\b(?:office|onsite|on site|on-site|hybrid|presencial|hibrid[oa])"
    r"(?: location)?\s*:\s*([^.;!?]+)"
)
# A captured office phrase keeps only its place: "our Sao Paulo office three days a
# week" -> "sao paulo". Metropolitan/state wording and city lists still abstain.
_OFFICE_DETERMINER = re.compile(r"^(?:our|the|an?|nosso|nossa|o|a)\s+")
_OFFICE_TRAILER = re.compile(
    r"\s+(?:offices?|hub|headquarters|hq|campus|escritorio)\b.*$|"
    r"\s+(?:\d+|one|two|three|four|five)\s+days?\b.*$|"
    r"\s+(?:on|every|each|per)\s+.*$"
)
_METRO_OR_STATE = re.compile(r"\b(?:state|estado|metropolitan|metropolitana|metro|greater)\b")
_ENGLISH = re.compile(r"\b(?:english|ingles|anglais|английск\w*)\b")
_PORTUGUESE = re.compile(r"\b(?:portuguese|portugues|portugais|португальск\w*)\b")
_OTHER_LANGUAGE = re.compile(
    r"\b(?:spanish|espanol|espanhol|french|francais|frances|german|deutsch|alemao|"
    r"italian|italiano|japanese|japones|chinese|mandarin|russian|arabic|korean|"
    r"swedish|danish|finnish|dutch|norwegian|icelandic|polish|czech|hungarian|greek|"
    r"romanian|ukrainian|turkish|hebrew|hindi|bengali|urdu|indonesian|vietnamese|"
    r"thai|malay|tagalog|filipino|swahili|somali|"
    r"испанск\w*|французск\w*|немецк\w*)\b"
)
_COMPANY_INTERNATIONAL = re.compile(
    r"\b(?:we\s+(?:are|operate\s+as)|(?:our|this|the)\s+company\s+is)\s+"
    r"(?:(?:an?|the|leading|truly)\s+){0,3}(?:international|global|worldwide)\s+"
    r"(?:company|business|organization|organisation|employer)\b|"
    r"\b(?:(?:our|this|the)\s+company|we)\s+operates?\s+(?:internationally|globally|worldwide)\b"
)
_COMPANY_DOMESTIC = re.compile(
    r"\b(?:brazil|brasil)[ -]only\s+(?:company|business|employer)\b|"
    r"\b(?:(?:our|this|the)\s+(?:company|business|organization|organisation|employer)|we)\s+"
    r"(?:(?:only|exclusively)\s+operates?\s+in|"
    r"operates?\s+(?:only|exclusively)\s+in)\s+(?:brazil|brasil)\b|"
    r"\b(?:(?:our|this|the)\s+(?:company|business)|we)\s+operates?\s+in\s+"
    r"(?:brazil|brasil)\s+(?:only|exclusively)\b"
)
_ENGLISH_ADVERT = re.compile(
    r"\b(?:you\s+(?:will|are)|your\s+role|this\s+(?:role|position)|"
    r"responsibilities\s+include|we\s+(?:are\s+looking\s+for|seek)|the\s+candidate\s+will)\b"
)
_ENGLISH_DENIED = re.compile(
    r"\benglish\s+(?:(?:(?:is|will(?:\s+be)?)\s+)?(?:not|never)|"
    r"isn't|isn’t|won't|won’t|cannot|can't|can’t)\s+(?:be\s+)?"
    r"(?:used|spoken|our\s+working\s+language|the\s+working\s+language)\b|"
    r"\bno\s+english\s+(?:(?:is|will\s+be)\s+)?(?:used|spoken)\b|"
    r"\b(?:we|you|this\s+role)\s+(?:never|do\s+not|does\s+not|don't|don’t|doesn't|doesn’t)\s+"
    r"(?:use|speak|work\s+in|communicate\s+in)\s+english\b|"
    r"\benglish\s+(?:is\s+)?(?:forbidden|prohibited|excluded)\b|"
    r"\bworking\s+languages?\s+(?:is|are)\s+(?:not|never)\s+english\b"
)
_NEGATED_LANGUAGE_PREDICATE = re.compile(
    r"\b(?:not|never|without|don't|don’t|doesn't|doesn’t|can't|can’t|cannot|won't|won’t)\s+"
    r"(?:work|working|communicate|communication|collaborate|speak|use|require|required|need|"
    r"fluent|proficient)(?:\s+(?:in|to|fluency|proficiency|speak)){0,3}\s*$"
)
_OPTIONAL_LANGUAGE_PREDICATE = re.compile(
    r"\b(?:preferred|optional|bonus|nice to have|desirable)"
    r"(?:\s+(?:fluency|proficiency|in|knowledge|of|speaking|ability|to|speak)){0,4}\s*:?\s*$"
)
_OPTIONAL_LANGUAGE_OBJECT = re.compile(
    r"^(?:(?:proficiency|fluency|language\s+skills?)\s+)?(?:(?:is|are)\s+)?"
    r"(?:not\s+(?:an?\s+)?(?:required|mandatory|needed|essential|working\s+language)|"
    r"never\s+required|(?:isn't|isn’t|aren't|aren’t)\s+(?:required|mandatory|needed)|"
    r"optional|preferred|desirable|a\s+(?:bonus|plus)|nice\s+to\s+have)\b"
)
_INCIDENTAL_LANGUAGE_OBJECT = re.compile(
    r"^(?:[- ]?speaking\s+)?(?:customers?|clients?|company|companies|law|laws|"
    r"work\s+authori[sz]ation|citizenship|work\s+permit|residency)\b|"
    r"^(?:proficiency|fluency)\s+(?:trends?|metrics?|data|assessments?)\b"
)
_LANGUAGE_OBJECT_END = re.compile(
    r"^(?:only\s*)?(?:(?:for|in)\s+(?:this|the)\s+(?:role|position))?\s*$"
)
_LANGUAGE_USAGE_END = re.compile(
    r"^(?:only\s*)?(?:with\s+(?:(?:your|our|the)\s+)?"
    r"(?:team|colleagues|clients|customers)\s*)?"
    r"(?:(?:for|in)\s+(?:this|the)\s+(?:role|position))?\s*$"
)
_ROLE_LANGUAGE_DECLARATIONS = tuple(re.compile(pattern) for pattern in (
    r"(?:(?:our|the|this\s+role's)\s+)?working\s+languages?\s+(?:(?:is|are)\s+|:\s*)"
    r"(?P<language>.+)",
    r"(?:all\s+|our\s+|the\s+)?(?:meetings|communication|collaboration)"
    r"(?:\s+for\s+(?:this|the)\s+role)?\s+(?:is|are|will\s+be)\s+"
    r"(?:conducted|held|done)\s+in\s+(?P<language>.+)",
    r"(?:this|the)\s+(?:[\w-]+\s+){0,3}role\s+(?:is|will\s+be)\s+"
    r"(?:conducted|performed|done)\s+in\s+(?P<language>.+)",
    r"(?:all\s+|our\s+|the\s+)?(?:daily\s+)?work\s+(?:is|will\s+be)\s+"
    r"(?:conducted|performed|done)\s+in\s+(?P<language>.+)",
    r"(?:required|mandatory|essential)\s+languages?\s*:\s*(?P<language>.+)",
    r"languages?\s+(?:required|mandatory|essential)\s*:\s*(?P<language>.+)",
))
_LANGUAGE_REQUIREMENT_LISTS = tuple(re.compile(pattern) for pattern in (
    r"(?P<language>.+?)\s+(?:(?:proficiency|fluency|language\s+skills?)\s+)?"
    r"(?:(?:is|are|will\s+be|must\s+be)\s+)?(?:an?\s+)?"
    r"(?:required|mandatory|needed|essential)(?:\s+requirement)?"
    r"(?:\s+(?:for|in)\s+(?:this|the)\s+(?:role|position))?",
    r"(?:required|mandatory|essential)\s*:?\s+(?P<language>.+)",
    r"(?:we|(?:this|the)\s+(?:role|position))\s+requires?\s+(?P<language>.+)",
    r"(?:you|candidates?|applicants?)\s+(?:must|will|need\s+to|are\s+required\s+to)\s+"
    r"(?:speak|use|work\s+in|communicate\s+in|collaborate\s+in|"
    r"be\s+fluent\s+in|have\s+proficiency\s+in)\s+(?P<language>.+)",
))


def _clauses(contexts: list[dict]) -> list[tuple[str, str]]:
    return [
        (part, _text(part))
        for unit in contexts
        for part in re.split(r"[;!?]|\n|\.\s+(?=[A-Z])", unit["text"])
        if part.strip()
    ]


def explicit_brazil_exclusion(tag: dict, contexts: list[dict]) -> bool:
    """A negated hiring predicate can positively establish exclusion of Brazil."""
    if tag.get("value") != "restricted_elsewhere" or tag.get("basis") != "explicit":
        return False
    for original, _ in _clauses(contexts):
        for pattern in _NEGATIVE_CANDIDATE_SCOPES:
            match = pattern.fullmatch(original.strip().rstrip("."))
            if match and (places := _places(match["places"])) is not None and "brazil" in places:
                return True
    return False


def _places(value: str) -> set[str] | None:
    """Parse the entire allowed region, never a convenient foreign substring.

    Country aliases are parser capabilities, not preferences. An unknown list
    element or a non-geographic qualifier causes abstention for the whole fact.
    """
    value = re.sub(r"\s+(?:only|exclusively)$", "", value.strip().rstrip("."), flags=re.I)
    result = set()
    for original in re.split(r"\s*(?:,|/|&|\band\b|\bor\b)\s*", value, flags=re.I):
        # "LATAM, including Brazil" names an element of the same allowed region.
        original = re.sub(r"^(?:including|incl\.?|such\s+as)\s+", "", original.strip(),
                          flags=re.I)
        original = re.sub(r"^the\s+", "", original.strip(), flags=re.I)
        place = _text(original).rstrip(".")
        if place in {"brazil", "brasil", "latam", "latin america", "south america", "americas",
                     "worldwide", "globally", "anywhere in the world"}:
            result.add("brazil")
        elif place in _FOREIGN_NAMES or _FOREIGN_CODES.fullmatch(original):
            result.add(place)
        else:
            return None
    return result or None


def _hiring_claim(original: str) -> bool | None:
    """True includes Brazil, False excludes it, None has no complete scoped proof."""
    clause = original.strip().rstrip(".")
    if explicit_brazil_exclusion({"value": "restricted_elsewhere", "basis": "explicit"},
                                 [{"text": original}]):
        return False
    for index, pattern in enumerate(_CANDIDATE_SCOPES):
        if match := pattern.fullmatch(clause):
            target = match["places"]
            excluded = re.fullmatch(r"(.+?)\s+(?:except|excluding|other than)\s+(brazil|brasil)",
                                    target, flags=re.I)
            if excluded and _places(excluded[1]) is not None:
                return False
            places = _places(target)
            if places is not None:
                if "brazil" in places:
                    return True
                # Hiring in a foreign country does not say that Brazil is
                # excluded. Only an exclusive list or a candidate obligation does.
                exclusive = re.search(r"\b(?:only|exclusively)\b", clause, re.I)
                policy = match.groupdict().get("policy") or ""
                mandatory = index == 2 or policy.lower() in _CLOSED_POLICIES
                if exclusive or mandatory:
                    return False
    for pattern in _AUTHORIZATION_SCOPES:
        if match := pattern.fullmatch(clause):
            places = _places(match["places"])
            # A foreign authorization requirement can exclude the candidate;
            # Brazilian authorization alone does not prove an opening accepts them.
            if places is not None and "brazil" not in places:
                return False
    return None


def _structured_candidate_location(unit: dict) -> bool:
    return bool(re.search(r"(?:applicant|candidate|hiring).*location", unit.get("field", "")))


def _hiring_claims(contexts: list[dict], *, source: list[dict]) -> list[bool | None]:
    claims = [_hiring_claim(original) for original, _ in _clauses(
        [unit for unit in contexts if not _structured_candidate_location(unit)]
    )]
    fields = {unit["field"] for unit in contexts if _structured_candidate_location(unit)}
    for field in fields:
        # A structured allowed-location array is a single union. A citation to
        # Canada cannot erase a later Brazil item; unknown members invalidate the
        # complete set rather than turning the recognized subset into a limit.
        units = [unit for unit in source if unit.get("field") == field]
        places = _places(", ".join(unit["text"] for unit in units))
        claims.append("brazil" in places if places is not None else (
            _hiring_claim(units[0]["text"]) if len(units) == 1 else None
        ))
    return claims


def _hiring_supported(tag: dict, contexts: list[dict], source: list[dict]) -> bool:
    wanted = tag["value"] == "open_to_brazil"
    cited = _hiring_claims(contexts, source=source)
    if wanted not in cited:
        return False
    # Neither a convenient company/global citation nor two matching model replies
    # may hide a contradictory candidate restriction elsewhere in the advert.
    claims = _hiring_claims(source, source=source)
    return not any(claim is not wanted for claim in claims if claim is not None)


def _office_place(value: str) -> str:
    place = _OFFICE_DETERMINER.sub("", value.strip(" ,:"))
    return _OFFICE_TRAILER.sub("", place).strip(" ,:")


def _office_kind(location: str) -> str | None:
    if _METRO_OR_STATE.search(_text(location)):
        return None
    tags, _ = migrate_tags(
        {
            "work_mode": {
                "value": "hybrid",
                "quote": "",
                "office_location": location,
            }
        }
    )
    return tags.get("work_mode", {}).get("value")


def _office_city(location: str) -> str:
    name = _text(location)
    name = re.sub(r"^(?:(?:city|municipality) of |(?:cidade|municipio) de )", "", name)
    name = re.split(r"\s*,\s*|\s+[–-]\s+", name)[0]
    return re.sub(r"(?: city| capital| \(city\)|\s*\(sp\))$", "", name).strip()


def _office_supported(tag: dict, contexts: list[dict]) -> str | None:
    text = " ".join(_text(unit["text"]) for unit in contexts)
    if not _OFFICE.search(text) or not _OFFICE_COMMITMENT.search(text):
        return "unsupported_evidence"
    locations = []
    for unit in contexts:
        normalized = _text(unit["text"])
        locations.extend(_office_place(match[1]) for match in _OFFICE_PLACE.finditer(normalized))
        # "Hybrid: you will work in Sao Paulo" holds a sentence, not a place; its own
        # "in/at" phrase is captured above. Bare labels such as "Hybrid: A or B" stay.
        locations.extend(_office_place(match[1]) for match in _OFFICE_LABEL.finditer(normalized)
                         if not re.search(r"\b(?:in|at|em)\s+", match[1]))
        if "location" in unit.get("field", "") and not re.search(
            r"(?:company|hiring|applicant|candidate)", unit["field"]
        ):
            locations.append(normalized)
    declared = tag.get("office_location", "").strip()
    declared_kind = _office_kind(declared) if declared else None
    if declared and declared_kind != tag["value"]:
        return "location_mismatch"
    if not locations and declared and _text(declared) in text:
        locations = [declared]
    kinds = {_office_kind(location) for location in locations}
    if not kinds or None in kinds or kinds != {tag["value"]}:
        return "location_mismatch"
    if declared and {_office_city(location) for location in locations} != {_office_city(declared)}:
        return "location_mismatch"
    return None


def _language_names(value: str) -> tuple[list[str], bool] | None:
    """Retain AND/OR semantics; mixed expressions have no safe simple reading."""
    value = re.sub(r"^only\s+|\s+only$", "", value.strip())
    value = re.sub(r"\s+(?:fluently|proficiently)$", "", value)
    value = re.sub(r"\s+with\s+(?:(?:your|our|the)\s+)?"
                   r"(?:team|colleagues|clients|customers)$", "", value)
    alternative = bool(re.search(r"\bor\b", value))
    if alternative and re.search(r",|/|&|\band\b", value):
        return None
    names = [part.strip() for part in re.split(r"\s*(?:,|/|&|\band\b|\bor\b)\s*", value)]
    return (names, alternative) if names and all(names) else None


def _asserted_language_list(pattern: re.Pattern, clause: str, *, alternatives: bool) -> bool | None:
    for declaration in (*_ROLE_LANGUAGE_DECLARATIONS, *_LANGUAGE_REQUIREMENT_LISTS):
        if not (match := declaration.fullmatch(clause.strip())):
            continue
        parsed = _language_names(match["language"])
        if parsed is None:
            continue
        names, alternative = parsed
        if not all(any(alias.fullmatch(name) for alias in (_ENGLISH, _PORTUGUESE, _OTHER_LANGUAGE))
                   for name in names):
            continue
        matches = [bool(pattern.fullmatch(name)) for name in names]
        # A conjunction requires each language. A disjunction only requires a
        # foreign language if every alternative does; English may be an option.
        return any(matches) if not alternative or alternatives else all(matches)
    return None


def _language_asserted(pattern: re.Pattern, contexts: list[dict], *,
                       alternatives: bool = False) -> bool:
    for unit in contexts:
        text = _text(unit["text"])
        if re.search(r"(?:^|\.)(?:working_languages?|language_requirements)$", unit["field"]):
            text = "working language is " + text
        for clause in re.split(r"[.;!?]", text):
            asserted = _asserted_language_list(pattern, clause, alternatives=alternatives)
            if asserted is not None:
                if asserted:
                    return True
                continue
            for match in pattern.finditer(clause):
                before, after = clause[: match.start()].rstrip(), clause[match.end() :].lstrip()
                if re.search(r"\b(?:not|never|without|no)\s*$", before):
                    continue
                if _NEGATED_LANGUAGE_PREDICATE.search(before) \
                        or _OPTIONAL_LANGUAGE_PREDICATE.search(before):
                    continue
                if re.match(
                    r"^[\s-]*(?:only\s+)?(?:(?:is|are|will be|must be)\s+"
                    r"(?:(?:our|the)\s+)?(?:only\s+)?)?"
                    r"(?:an?\s+)?(?:required|mandatory|needed|essential|"
                    r"(?:speaking|spoken)\s+(?:role|position|working language)|"
                    r"business level|professional level|working proficiency|working language)\b",
                    after,
                ):
                    return True
                if re.match(r"^(?:proficiency|fluency|language\s+skills?)\s+"
                            r"(?:(?:is|are)\s+)?(?:required|mandatory|needed|essential)\b", after):
                    return True
                if _LANGUAGE_USAGE_END.fullmatch(after) and re.search(
                    r"\b(?:work|working|communicate|communication|collaborate)\s+in\s*$", before,
                ):
                    return True
                if _LANGUAGE_OBJECT_END.fullmatch(after) and re.search(
                    r"(?:fluent|fluency|proficient|proficiency|working language|"
                    r"must speak|need to speak|required to speak|work in|working in|"
                    r"communicate in|communication in|collaborate in)"
                    r"(?:\s+in)?\s*:?\s*$",
                    before,
                ):
                    return True
                if _LANGUAGE_OBJECT_END.fullmatch(after) and re.search(
                    r"(?:this\s+role|the\s+role|this\s+position|we)\s+requires?\s*$|"
                    r"(?:our\s+|the\s+)?working\s+languages?\s+(?:is|are)\s*$|"
                    r"(?:all\s+|our\s+|the\s+)?(?:meetings|communication|collaboration)"
                    r"(?:\s+for\s+(?:this|the)\s+role)?\s+(?:is|are|will\s+be)\s+"
                    r"(?:conducted|held|done)\s+in\s*$", before,
                ):
                    return True
    return False


def _language_source(source: list[dict]) -> list[dict]:
    return [unit for unit in source if unit.get("section") in {"requirements", "role"}
            or unit.get("field", "").startswith("description")
            or re.search(r"source_metadata\.(?:working_languages?|language_requirements)$",
                         unit.get("field", ""))]


def _international_company(contexts: list[dict]) -> bool:
    return any(_COMPANY_INTERNATIONAL.search(_text(unit["text"])) for unit in contexts)


def _english_advert(source: list[dict]) -> bool:
    return any(
        unit.get("field", "").startswith("description")
        and unit.get("section") in {"role", "requirements"}
        and _ENGLISH_ADVERT.search(_text(unit["text"]))
        for unit in source
    )


def _other_declared_language(source: list[dict], *, allowed: frozenset[str]) -> bool:
    """Unknown named working languages veto inference without inventing a blocker.

    A typed working-language/meeting declaration differs from a skill requirement:
    Python or SQL being mandatory is not a statement about spoken communication.
    """
    for unit in source:
        texts = [_text(original).rstrip(".") for original, _ in _clauses([unit])]
        if re.search(r"source_metadata\.(?:working_languages?|language_requirements)$",
                     unit.get("field", "")):
            texts = ["working language is " + _text(unit["text"])]
        for text in texts:
            for pattern in (*_ROLE_LANGUAGE_DECLARATIONS, *_LANGUAGE_REQUIREMENT_LISTS):
                match = pattern.fullmatch(text)
                if match:
                    if re.match(r"^(?:not|never)\b", match["language"]):
                        continue
                    parsed = _language_names(match["language"])
                    if parsed is None:
                        if pattern in _ROLE_LANGUAGE_DECLARATIONS:
                            return True
                        continue
                    names, alternative = parsed
                    # Untyped mandatory skills (SQL/Python) do not declare a
                    # spoken language. Typed declarations may safely abstain on
                    # an unknown name; ordinary skill predicates may not.
                    if pattern not in _ROLE_LANGUAGE_DECLARATIONS and not all(
                        any(alias.fullmatch(name) for alias in (_ENGLISH, _PORTUGUESE,
                                                               _OTHER_LANGUAGE))
                        for name in names
                    ):
                        continue
                    if not (any(name in allowed for name in names) if alternative
                            else set(names) <= allowed):
                        return True
    return False


def _unresolved_language_relation(source: list[dict], patterns: tuple[re.Pattern, ...]) -> bool:
    """A mentioned language with an unknown relation cannot prove compatibility.

    Required-language recognition is deliberately finite. Failure to parse a
    natural-language mention does not prove the absence of a requirement. Only
    resolved optional/negated facts, language alternatives or clear non-language
    noun objects permit positive English inference. Unresolved mentions abstain;
    they do not create an other-language hard exclusion.
    """
    for unit in source:
        text = _text(unit["text"])
        if re.search(r"(?:^|\.)(?:working_languages?|language_requirements)$", unit["field"]):
            text = "working language is " + text
        for clause in re.split(r"[.;!?]", text):
            for pattern in patterns:
                if _asserted_language_list(pattern, clause, alternatives=False) is not None:
                    continue
                if _language_asserted(pattern, [{**unit, "text": clause}]):
                    continue  # The caller separately checks required-language incompatibility.
                for match in pattern.finditer(clause):
                    before = clause[:match.start()].rstrip()
                    after = clause[match.end():].lstrip()
                    if re.search(r"\b(?:not|never|without|no)\s*$", before) \
                            or _NEGATED_LANGUAGE_PREDICATE.search(before) \
                            or _OPTIONAL_LANGUAGE_PREDICATE.search(before) \
                            or _OPTIONAL_LANGUAGE_OBJECT.match(after) \
                            or _INCIDENTAL_LANGUAGE_OBJECT.match(after):
                        continue
                    return True
    return False


def hiring_evidence_warning(name: str, tag: dict, contexts: list[dict], *,
                            source: list[dict] | None = None) -> str | None:
    """Return a closed diagnostic code when a nonempty hiring value lacks proof."""
    value = tag.get("value")
    if value == "unspecified" or name not in {
        "hiring_eligibility",
        "work_mode",
        "working_language",
    }:
        return None
    source = contexts if source is None else source
    if name == "hiring_eligibility":
        supported = _hiring_supported(tag, contexts, source)
    elif name == "work_mode":
        if value != "remote":
            return _office_supported(tag, contexts)
        supported = any(_REMOTE.search(text) for _, text in _clauses(contexts)) or any(
            unit.get("field") == "remote_text" and _text(unit["text"]) in {"remote", "remotely"}
            for unit in contexts
        )
    else:
        language_source = _language_source(source)
        foreign_required = _language_asserted(_OTHER_LANGUAGE, language_source)
        portuguese_required = _language_asserted(_PORTUGUESE, language_source)
        denied_english = any(_ENGLISH_DENIED.search(_text(unit["text"]))
                             for unit in language_source)
        domestic = any(_COMPANY_DOMESTIC.search(_text(unit["text"])) for unit in source)
        other_declared = _other_declared_language(language_source, allowed=frozenset({"english"}))
        if value == "english":
            unresolved = _unresolved_language_relation(
                language_source, (_PORTUGUESE, _OTHER_LANGUAGE),
            )
            supported = not (foreign_required or portuguese_required or denied_english
                             or other_declared or unresolved) \
                and _language_asserted(_ENGLISH, contexts, alternatives=True)
            if tag["basis"] == "implied":
                supported = supported or bool(
                    not (foreign_required or portuguese_required or denied_english or domestic
                         or other_declared or unresolved)
                    and _international_company(contexts) and _english_advert(source)
                )
        elif value == "portuguese_with_english":
            supported = bool(
                _language_asserted(_PORTUGUESE, contexts)
                and _language_asserted(_ENGLISH, contexts, alternatives=True)
                and _international_company(contexts)
                and not (foreign_required or denied_english or domestic)
                and not _unresolved_language_relation(language_source, (_OTHER_LANGUAGE,))
                and not _other_declared_language(
                    language_source, allowed=frozenset({"portuguese", "english"}),
                )
            )
        elif value == "portuguese_only":
            supported = bool(
                _language_asserted(_PORTUGUESE, contexts)
                and not _language_asserted(_ENGLISH, language_source)
            )
        else:
            supported = _language_asserted(_OTHER_LANGUAGE, contexts)
    return None if supported else "unsupported_evidence"
