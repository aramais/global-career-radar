"""The search gate needs advertised duties, not titles or candidate qualifications."""

import pytest

from job_intake.annotation.ai import _anthropic_schema
from job_intake.annotation.schema import (
    FOCUS_KINDS,
    prepare_claims,
    review_claims,
    summarize_claims,
)


def evidence(rows):
    units, drafts = [], []
    for index, row in enumerate(rows):
        kind, text, *options = row
        extra = options[0] if options else {}
        unit_id = f"unit-{index}"
        units.append({
            "unit_id": unit_id, "field": extra.get("field", "description_clean"),
            "section": extra.get("section", "role"), "text": text,
        })
        drafts.append({
            "kind": kind, "value": extra.get("value", text), "unit_id": unit_id,
            "source_snippet": text, "requirement": extra.get("requirement", "informational"),
            "polarity": extra.get("polarity", "affirmative"),
            "is_inference": extra.get("is_inference", False),
        })
    claims, rejects = prepare_claims({"claims": drafts}, "vacancy", units, "chunk")
    assert rejects == []
    reviewed, rejects = review_claims(claims, {"reviews": [{
        "claim_id": claim["claim_id"], "review_status": "SUPPORTED", "is_inference": False,
    } for claim in claims]})
    assert rejects == []
    return reviewed, summarize_claims(reviewed)["search_focus"]


def required_rows():
    return [
        ("project_management", "You will lead cross-functional pricing projects."),
        ("people_interaction", "You will coordinate delivery with Finance and product teams."),
        ("decision_influence", "You will shape pricing decisions using analytical findings."),
        ("working_language", "Working language for this role is English.", {"value": "English"}),
    ]


def test_project_leadership_with_no_reports_and_some_analysis_confirms_required_scope():
    claims, focus = evidence(required_rows() + [
        ("direct_reports", "This role has no direct reports.", {"polarity": "negative"}),
        ("hands_on_analysis", "You will spend part of your time analyzing experiments."),
    ])
    assert focus["required_confirmed"] is True
    assert focus["unconfirmed_required"] == focus["explicit_mismatches"] == []
    assert focus["dimensions"]["direct_reports"]["status"] == "explicitly_absent"
    assert focus["dimensions"]["hands_on_analysis"]["status"] == "confirmed"
    assert focus["dimensions"]["business_economics"]["status"] == "unknown"
    assert focus["dimensions"]["project_management"]["claim_ids"] == [claims[0]["claim_id"]]


def test_staff_title_collaboration_and_decision_influence_do_not_prove_project_management():
    _, focus = evidence([
        ("role", "Staff Product Analyst", {"field": "title"}),
        *required_rows()[1:],
    ])
    assert focus["required_confirmed"] is False
    assert focus["unconfirmed_required"] == ["project_management"]
    assert focus["dimensions"]["direct_reports"]["status"] == "unknown"


def test_title_candidate_history_company_context_and_conditional_scope_remain_unresolved():
    rows = [
        ("project_management", "Analytics Manager", {"field": "title"}),
        ("project_management", "Experience managing analytics projects is required.",
         {"section": "requirements", "requirement": "required"}),
        ("people_interaction", "Strong communication skills are required.",
         {"section": "requirements", "requirement": "required"}),
        ("decision_influence", "Our company uses data to guide strategic decisions.",
         {"section": "company"}),
        ("project_management", "You may lead pricing projects if business needs change."),
    ]
    claims, focus = evidence(rows)
    assert all(claim["review_status"] == "WEAKLY_SUPPORTED" for claim in claims)
    assert all(focus["dimensions"][kind]["status"] == "unknown" for kind in (
        "project_management", "people_interaction", "decision_influence",
    ))
    assert focus["dimensions"]["project_management"]["unresolved_claim_ids"] == [
        claims[index]["claim_id"] for index in (0, 1, 4)
    ]


def test_actual_role_duty_embedded_in_qualification_section_is_not_past_experience():
    _, focus = evidence([
        ("project_management", "You will manage analytics projects using your prior experience.",
         {"section": "requirements"}),
    ])
    assert focus["dimensions"]["project_management"]["status"] == "confirmed"


@pytest.mark.parametrize("text", [
    "This role requires experience managing analytical projects.",
    "You must have experience managing analytical projects.",
    "You will bring prior experience leading analytical projects.",
    "You must be able to manage analytical projects.",
])
def test_role_or_you_subject_does_not_convert_candidate_qualification_into_duty(text):
    _, focus = evidence([
        ("project_management", text, {"section": "requirements", "requirement": "required"}),
    ])
    assert focus["dimensions"]["project_management"]["status"] == "unknown"


def test_optional_english_does_not_confirm_working_english():
    _, focus = evidence([
        *required_rows()[:3],
        ("working_language", "English is preferred, but not required.",
         {"value": "English", "requirement": "preferred"}),
    ])
    assert focus["unconfirmed_required"] == ["working_english"]
    assert focus["explicit_mismatches"] == []


def test_only_portuguese_working_language_is_explicit_mismatch():
    _, focus = evidence([
        *required_rows()[:3],
        ("working_language", "Portuguese is the only working language.", {"value": "Portuguese"}),
    ])
    assert focus["dimensions"]["working_english"]["status"] == "explicitly_absent"
    assert focus["explicit_mismatches"] == ["working_english"]


def test_english_and_portuguese_only_does_not_invent_exclusion_of_english():
    _, focus = evidence([
        *required_rows()[:3],
        ("working_language", "We work in English and Portuguese only.", {"value": "English"}),
        ("working_language", "We work in English and Portuguese only.", {"value": "Portuguese"}),
    ])
    assert focus["required_confirmed"] is True


def test_explicitly_isolated_analysis_marks_mismatch_without_guessing_from_hands_on_work():
    _, focus = evidence(required_rows() + [
        ("isolated_analysis", "You will do prolonged solo analysis without people interaction."),
    ])
    assert focus["explicit_mismatches"] == ["isolated_analysis"]
    assert focus["required_confirmed"] is False


def test_required_scope_conflict_never_becomes_confirmation():
    _, focus = evidence(required_rows() + [
        ("project_management", "You will not manage projects.", {"polarity": "negative"}),
    ])
    assert focus["dimensions"]["project_management"]["status"] == "conflicting"
    assert focus["required_confirmed"] is False


def test_economics_finance_and_causal_duties_are_preferences_not_missing_hard_filters():
    _, focus = evidence(required_rows() + [
        ("business_economics", "You will analyze the unit's P&L and pricing economics."),
        ("executive_finance_partnership", "You will partner with the GM and Finance on budgets."),
        ("causal_inference", "You will apply causal inference to monetization experiments."),
    ])
    assert focus["required_confirmed"] is True
    assert focus["confirmed_preferences"] == [
        "business_economics", "executive_finance_partnership", "causal_inference",
    ]
    assert focus["dimensions"]["causal_inference"]["priority"] == "preferred"


def test_preferred_causal_skill_does_not_invent_research_responsibility():
    _, focus = evidence(required_rows() + [
        ("skill", "Causal inference experience is a nice-to-have.", {"requirement": "preferred"}),
    ])
    assert focus["required_confirmed"] is True
    assert focus["dimensions"]["causal_inference"]["status"] == "unknown"


def test_native_extraction_schema_accepts_source_grounded_focus_kinds():
    item = _anthropic_schema("extract")["properties"]["claims"]["items"]
    enum = item["properties"]["kind"]["enum"]
    assert set(FOCUS_KINDS) <= set(enum)
