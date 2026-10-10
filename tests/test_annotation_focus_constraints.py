"""Reviewed job responsibilities gate the personal search independently of titles."""

from dataclasses import replace

import pytest

from job_intake.annotation.schema import prepare_claims, review_claims, summarize_claims
from job_intake.annotation.service import apply_annotation_constraints
from job_intake.filtering import FilterRules, RuleEngine
from job_intake.models.job import FilterDecision, JobRecord

REQUIRED = ["project_management", "people_interaction", "decision_influence", "working_english"]
DUTIES = [
    ("project_management", "You will oversee analytical initiatives from planning to delivery."),
    ("people_interaction", "You will coordinate delivery with Finance and product teams."),
    ("decision_influence", "You will shape pricing decisions using analytical findings."),
    ("working_language", "Working language for this role is English."),
]


def annotated_job(rows, *, title="Analytics Manager", status="reviewed", review="SUPPORTED"):
    units, drafts = [], []
    for index, row in enumerate([*rows, ("hiring_location", "Applicants may work from Brazil.")]):
        kind, text, *options = row
        extra = options[0] if options else {}
        unit_id = str(index)
        units.append({"unit_id": unit_id, "field": "description_clean", "section": "role",
                      "text": text})
        drafts.append({
            "kind": kind, "value": "English" if kind == "working_language" else text,
            "unit_id": unit_id, "source_snippet": text,
            "requirement": "informational", "polarity": extra.get("polarity", "affirmative"),
            "is_inference": False,
        })
    claims, bad = prepare_claims({"claims": drafts}, "job", units, "chunk")
    assert not bad
    claims, bad = review_claims(claims, {"reviews": [{
        "claim_id": claim["claim_id"], "review_status": review, "is_inference": False,
    } for claim in claims]})
    assert not bad
    return JobRecord(
        source="test", source_job_id="1", company="Example", title=title,
        original_url="https://example.com/job", remote_text="Remote",
        description_clean="\n".join(row[1] for row in rows),
        source_metadata={"working_language": "English",
                         "applicant_location_requirements": ["Brazil"]},
        annotation={"method": "two_pass", "status": status, "summary": summarize_claims(claims)},
    )


def engine():
    return RuleEngine(FilterRules.from_mapping({
        "recall_first": True, "target_geographies": ["Brazil"],
        "required_languages": ["English"],
        "positive_title_signals": ["analytics", "data scientist"],
        "required_scope_title_signals": ["manager", "lead"],
        "required_scope_description_signals": ["lead projects"],
        "required_annotation_focus": REQUIRED,
    }))


@pytest.mark.parametrize("missing", REQUIRED)
def test_manager_title_cannot_bypass_missing_reviewed_required_dimension(missing):
    job = annotated_job([row for row in DUTIES if row[0] != (
        "working_language" if missing == "working_english" else missing
    )])
    rules = engine()
    evaluation = rules.evaluate(job)
    assert evaluation.decision == FilterDecision.PASS  # Lexical signal is preliminary.
    apply_annotation_constraints(job, evaluation, rules)
    assert evaluation.decision == FilterDecision.REVIEW
    assert f"annotation:search_focus:{missing}:unknown" in evaluation.risks


def test_actual_project_lead_without_reports_can_resolve_scope_without_lexical_hit():
    job = annotated_job(DUTIES + [
        ("direct_reports", "This role has no direct reports.", {"polarity": "negative"}),
        ("hands_on_analysis", "You will spend part of your time analyzing experiments."),
    ], title="Staff Data Scientist")
    rules = engine()
    evaluation = rules.evaluate(job)
    assert evaluation.decision == FilterDecision.REVIEW
    assert "role:required_scope_unconfirmed" in evaluation.risks
    apply_annotation_constraints(job, evaluation, rules)
    assert evaluation.decision == FilterDecision.PASS
    assert evaluation.risks == []
    assert {"grounded_focus:" + name for name in REQUIRED} <= set(evaluation.matched_signals)
    assert job.annotation["summary"]["search_focus"]["confirmed_preferences"] == []


@pytest.mark.parametrize("status,review", [
    ("partial", "SUPPORTED"), ("reviewed", "WEAKLY_SUPPORTED"),
    ("reviewed", "UNSUPPORTED"), ("reviewed", "NEEDS_VERIFICATION"),
])
def test_incomplete_or_weak_review_cannot_resolve_mandatory_scope(status, review):
    job = annotated_job(DUTIES, title="Staff Data Scientist", status=status, review=review)
    rules = engine()
    evaluation = rules.evaluate(job)
    apply_annotation_constraints(job, evaluation, rules)
    assert evaluation.decision == FilterDecision.REVIEW
    assert "role:required_scope_unconfirmed" in evaluation.risks


def test_explicit_prolonged_solo_analysis_without_people_remains_reviewable():
    job = annotated_job(DUTIES + [
        ("isolated_analysis", "You will do prolonged solo analysis without people interaction."),
    ])
    rules = engine()
    evaluation = rules.evaluate(job)
    apply_annotation_constraints(job, evaluation, rules)
    assert evaluation.decision == FilterDecision.REVIEW
    assert "annotation:search_focus:isolated_analysis" in evaluation.risks


def test_focus_rule_is_opt_in_and_deterministic_annotation_keeps_lexical_rules():
    job = annotated_job([])
    rules = engine()
    rules.rules = replace(rules.rules, required_annotation_focus=[])
    evaluation = rules.evaluate(job)
    apply_annotation_constraints(job, evaluation, rules)
    assert not any(risk.startswith("annotation:search_focus:") for risk in evaluation.risks)
    job.annotation["method"] = "deterministic"
    rules = engine()
    evaluation = rules.evaluate(job)
    apply_annotation_constraints(job, evaluation, rules)
    assert evaluation.decision == FilterDecision.PASS
    assert evaluation.risks == []


def test_reviewed_focus_cannot_override_existing_language_hard_rejection():
    job = annotated_job(DUTIES)
    job.source_metadata["working_language"] = "Portuguese"
    rules = engine()
    evaluation = rules.evaluate(job)
    assert evaluation.decision == FilterDecision.REJECT
    blockers = evaluation.blocker_signals.copy()
    apply_annotation_constraints(job, evaluation, rules)
    assert evaluation.decision == FilterDecision.REJECT
    assert evaluation.blocker_signals == blockers
