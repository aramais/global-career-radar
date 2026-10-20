"""Shared, conservative source-scope boundaries for annotation and v3 labels."""

from __future__ import annotations

import re

_DUTY_CUE = re.compile(
    r"\b(?:you(?: will|['’]ll| are| must)|your (?:role|responsibilities)|"
    r"this (?:role|position|job)|responsibilities|duties)\b|"
    r"^\s*(?:[-•]\s*)?(?:build|develop|implement|maintain|write|sell|prospect|"
    r"recruit|answer|resolve|manage|lead|own|design|deliver|perform|conduct)\b",
    re.I,
)
_ABSENCE_ONLY = re.compile(
    r"\b(?:not (?:mentioned|specified|stated|listed|described|confirmed)|"
    r"(?:no|without|lack of|missing) (?:information|details|evidence|mention)|"
    r"(?:duties|responsibilities|scope) (?:are |is )?(?:unknown|unclear|unspecified))\b",
    re.I,
)
_NON_DUTY_HEADING = re.compile(
    r"^\s*[#>*\-]*\s*(?:benefits|perks and benefits|what we offer|compensation|"
    r"salary|how to apply|application process|equal opportunity|legal notice)\b",
    re.I,
)
_QUALIFICATION_HEADING = re.compile(
    r"\b(?:you (?:might|may) be (?:a )?(?:good |great )?fit"
    r"(?: if(?: you)?)?|(?:preferred|required) (?:experience|qualifications|skills))\b|"
    r"^\s*[#>*\-]*\s*your (?:profile|skills|experience)\b",
    re.I,
)
_DUTY_SECTION_HEADING = re.compile(
    r"^\s*[#>*\-]*\s*(?:(?:about )?the role|your role|role overview|"
    r"(?:key |your )?responsibilities|duties|your impact|the opportunity|"
    r"job description|in this role|what you(?: will|['’]ll)? "
    r"(?:do|own|be responsible for))\b",
    re.I,
)
_OFFER_OR_FOOTER_PARAGRAPH = re.compile(
    r"^\s*(?:[#>*\-•]\s*)?(?:"
    r"(?:the )?(?:anticipated|expected|annual|base|starting)\b.{0,55}"
    r"\b(?:pay|salary|compensation)\b|"
    r"(?:the |our )?(?:salaries|salary|pay|compensation)\b.{0,100}"
    r"\b(?:range|competitive|equitable|offered|successful applicant|salary history)\b|"
    r"for applicants\b.{0,100}\b(?:pay|salary|compensation)\b|"
    r"we neither ask\b.{0,100}\bsalary history\b|"
    r"(?:we (?:offer|provide)|you (?:will receive|receive|get|are provided(?: with)?)|"
    r"this (?:role|position) (?:offers|provides|is eligible for))\b.{0,100}"
    r"\b(?:health insurance|medical insurance|paid time off|benefits|salary|compensation)\b|"
    r"(?:as an? |we are an? )equal opportunity employer\b|"
    r"we do not discriminate\b|"
    r"all applicants can reach out to their recruiter\b|"
    r"if you are a qualified applicant requiring (?:assistance|an accommodation)\b|"
    r"by submitting a job application\b|"
    r"more information applicant privacy policy\b)",
    re.I,
)
_DUTY_ACTION = (
    r"(?:manag(?:e|ing)|lead(?:ing)?|own(?:ing)?|design(?:ing)?|analy[sz](?:e|ing)|"
    r"develop(?:ing)?|build(?:ing)?|maintain(?:ing)?|deliver(?:ing)?|perform(?:ing)?|"
    r"conduct(?:ing)?|drive|driving|defin(?:e|ing)|forecast(?:ing)?|monitor(?:ing)?|"
    r"optimi[sz](?:e|ing)|evaluat(?:e|ing)|advise|advising|provide)"
)
_REAL_DUTY_STATEMENT = re.compile(
    r"^\s*(?:[-•]\s*)?" + _DUTY_ACTION + r"\b|"
    r"\byou(?: will|['’]ll| must| are (?:responsible for|expected to))\s+" + _DUTY_ACTION + r"\b",
    re.I,
)
_ANALYTICAL_COMPENSATION_HEADING = re.compile(
    r"^\s*[#>*\-]*\s*(?:compensation|salary) "
    r"(?:analytics|analysis|strategy|budgeting|management|planning|operations|benchmarking)\b",
    re.I,
)
_PERSONAL_BENEFIT_ACTIVITY = re.compile(
    r"\bmanag(?:e|ing)\b.{0,25}\byour\b.{0,30}\b(?:insurance|benefits?)\b|"
    r"\byour (?:resume|résumé|cv|cover letter|application|salary history)\b",
    re.I,
)
_CLAUSE_BOUNDARY = re.compile(r"[.!?;]\s+|\n|(?:^|\s)[-•]\s+")

OPTIONAL = re.compile(
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

NEGATED_LANGUAGE = re.compile(
    rf"\b(?:not|no)\s+{_LANGUAGE_PATTERN}\b"
    rf"|\b{_LANGUAGE_PATTERN}\s+(?:(?:is|will\s+be|are)\s+)?"
    rf"not\b(?!\s+only\b)",
    re.IGNORECASE,
)

UNCONFIRMED = re.compile(
    r"\bnot\s+(?:yet\s+)?confirmed\b"
    r"|\b(?:unconfirmed|unclear|undetermined|unknown|might|possibly)\b"
    r"|\bmay\s+(?:be|change|vary|require|hire)\b",
    re.IGNORECASE,
)

CANDIDATE_HISTORY = re.compile(
    r"\b(?:experience (?:in|with|of|leading|managing)|track record|previous(?:ly)?|"
    r"prior experience|background in|(?:ability|able) to|proven ability|"
    r"skills?|skillset|competenc(?:e|ies)|experienced (?:in|with|at)|skilled at)\b",
    re.IGNORECASE,
)

ROLE_SCOPE = re.compile(
    r"\b(?:you (?:will|must)(?!\s+(?:have|bring|possess|demonstrate|be\s+able))|"
    r"you['’]ll(?!\s+(?:have|bring|possess|demonstrate|be\s+able))|your responsibilities|"
    r"(?:this|the|your) (?:role|position) (?:will|involves|owns|includes|leads|manages)|"
    r"responsible for|responsibilities include)\b",
    re.IGNORECASE,
)

CONDITIONAL_SCOPE = re.compile(
    r"\b(?:may|might|could)\s+(?:be\s+)?(?:lead|manage|own|coordinate|collaborate|"
    r"partner|work|apply|analy[sz]e|perform|influence|participate|conduct|do|spend)\b",
    re.IGNORECASE,
)


def _has_real_duty_statement(text: str) -> bool:
    """Do not discard mixed offer/duty units or compensation analytics duties."""
    for clause in _CLAUSE_BOUNDARY.split(text):
        # A label may precede an imperative within the same exact source unit.
        body = clause.split(":", 1)[-1]
        if _REAL_DUTY_STATEMENT.search(body) and not _PERSONAL_BENEFIT_ACTIVITY.search(body):
            return True
    return False


def duty_unit_ids(units: list[dict]) -> set[str]:
    """Share conservative source-scope eligibility between the prompt and validator.

    These IDs only delimit permissible evidence. The two independent models
    still have to establish actual primary duties and a positive mismatch.
    """
    duty_ids = set()
    previous_section, previous_field, excluded, role_context = "other", None, False, False
    for unit in units:
        if not unit["field"].startswith("description_"):
            continue
        if unit["field"] != previous_field:
            previous_section, excluded, role_context = "other", False, False
        section = unit["section"]
        real_duty = _has_real_duty_statement(unit["text"])
        non_duty_heading = (
            _NON_DUTY_HEADING.search(unit["text"])
            and not real_duty
            and not _ANALYTICAL_COMPENSATION_HEADING.search(unit["text"])
        )
        pure_offer_or_footer = _OFFER_OR_FOOTER_PARAGRAPH.search(unit["text"]) and not real_duty
        if (
            _QUALIFICATION_HEADING.search(unit["text"])
            or non_duty_heading
            or section in {"company", "requirements"}
        ):
            excluded, role_context = True, False
        elif _DUTY_SECTION_HEADING.search(unit["text"]):
            excluded, role_context = False, True
        elif section == "other" and previous_section != "other":
            excluded, role_context = True, False
        # A qualification heading may be unrecognized by the text parser, which
        # can leave its following lines marked role. Preserve the exclusion
        # until a real duty/role heading or a separate source field starts.
        if (
            not excluded
            and unit["text"].strip()
            and (section == "role" or role_context or _DUTY_CUE.search(unit["text"]))
            and not _ABSENCE_ONLY.search(unit["text"])
            and not pure_offer_or_footer
        ):
            duty_ids.add(unit["unit_id"])
        previous_section = section
        previous_field = unit["field"]
    return duty_ids
