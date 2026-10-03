"""Scope must survive text parsing, evidence review, filtering and final tiers."""

import pytest

from job_intake.annotation.schema import prepare_claims, review_claims, summarize_claims
from job_intake.annotation.service import VacancyAnnotator, apply_annotation_constraints
from job_intake.annotation.text import role_description, source_units
from job_intake.config.settings import AnnotationConfig
from job_intake.filtering import FilterRules, RuleEngine
from job_intake.models.job import EvaluatedJob, FilterDecision, JobRecord, JobTier
from job_intake.scoring.pre_score import DeterministicScorer, SearchProfiles
from job_intake.scoring.tiering import finalize_tier


def _engine():
    return RuleEngine(
        FilterRules.from_mapping(
            {
                "recall_first": True,
                "positive_title_signals": ["product manager"],
                "positive_description_signals": ["roadmap"],
                "required_languages": ["English"],
                "target_geographies": ["Brazil", "worldwide"],
                "onsite_locations": ["São Paulo"],
                "timezone_allowed": ["americas timezone"],
                "timezone_blockers": ["europe time zone", "work eastern time only"],
                "blocker_phrases": ["US work authorization required"],
                "closed_phrases": ["no longer accepting applications"],
            }
        )
    )


def _job(description, *, language=None):
    return JobRecord(
        source="scope-regression",
        company="Example",
        title="Product Manager",
        original_url="https://example.com/jobs/scope",
        location_text="Brazil",
        remote_text="Remote",
        description_clean=description,
        source_metadata={"working_language": language} if language else {},
    )


def _annotate(job, tmp_path):
    annotator = VacancyAnnotator(
        AnnotationConfig(
            enabled=True,
            ai_enabled=False,
            cache_dir=str(tmp_path / "cache"),
        )
    )
    return annotator.annotate(job)


def _grade(job):
    engine = _engine()
    evaluation = engine.evaluate(job)
    apply_annotation_constraints(job, evaluation, engine)
    DeterministicScorer(
        SearchProfiles.from_mapping(
            {
                "title_weights": {"product manager": 20},
                "threshold_a": 14,
                "threshold_b": 6,
            }
        )
    ).score(job.source, job.company, job.title, role_description(job), evaluation)
    return finalize_tier(EvaluatedJob(record=job, evaluation=evaluation), 14, 6).evaluation


@pytest.mark.parametrize(
    "company_clause",
    [
        "Our teams speak fluent English and operate in the americas timezone.",
        "Our company working language is English.",
    ],
)
def test_company_language_does_not_clear_real_vacancy_language_uncertainty(
    tmp_path,
    company_clause,
):
    job = _job("About us\n" + company_clause + "\nResponsibilities\nOwn the roadmap.")
    annotation = _annotate(job, tmp_path)
    evaluation = _grade(job)
    assert "working_language" in annotation["summary"]["unknowns"]
    assert evaluation.decision == FilterDecision.REVIEW
    assert "language:working_language_unconfirmed" in evaluation.risks
    assert evaluation.tier == JobTier.B  # A high title score cannot supply missing evidence.
    assert evaluation.fit_score == 17


@pytest.mark.parametrize(
    "company_clause",
    [
        "Our customers operate in the Europe time zone.",
        "Our customers have US work authorization required for their bank accounts.",
        "Our old customer portal is no longer accepting applications.",
    ],
)
def test_company_customer_constraints_do_not_reject_an_eligible_current_role(
    tmp_path,
    company_clause,
):
    job = _job(
        "About us\n" + company_clause + "\nResponsibilities\nOwn the roadmap.", language="English"
    )
    _annotate(job, tmp_path)
    evaluation = _grade(job)
    assert evaluation.blocker_signals == []
    assert evaluation.decision == FilterDecision.PASS
    assert evaluation.tier == JobTier.A


def test_supported_model_review_cannot_promote_company_quote_over_language_uncertainty(tmp_path):
    job = _job(
        "About us\nOur global team publishes all company documentation in English.\n"
        "Responsibilities\nOwn the roadmap."
    )
    annotation = _annotate(job, tmp_path)
    unit = next(unit for unit in source_units(job) if "documentation" in unit["text"])
    assert unit["section"] == "company"
    drafts, rejects = prepare_claims(
        {
            "claims": [
                {
                    "kind": "working_language",
                    "value": "English",
                    "unit_id": unit["unit_id"],
                    "source_snippet": unit["text"],
                    "requirement": "required",
                    "polarity": "affirmative",
                    "is_inference": False,
                }
            ]
        },
        annotation["source_id"],
        [unit],
        "model-chunk",
    )
    assert rejects == []
    reviewed, rejects = review_claims(
        drafts,
        {
            "reviews": [
                {
                    "claim_id": drafts[0]["claim_id"],
                    "review_status": "SUPPORTED",
                    "review_notes": "Overconfident model review",
                    "is_inference": False,
                }
            ]
        },
    )
    assert rejects == []
    assert reviewed[0]["review_status"] == "WEAKLY_SUPPORTED"
    annotation["claims"].extend(reviewed)
    annotation["summary"] = summarize_claims(annotation["claims"])
    annotation["status"] = "reviewed"
    annotation["method"] = "two_pass"
    evaluation = _grade(job)
    assert "working_language" in annotation["summary"]["unknowns"]
    assert evaluation.decision == FilterDecision.REVIEW
    assert evaluation.tier == JobTier.B
    assert evaluation.fit_score == 17


def test_optional_english_does_not_override_explicit_portuguese_working_language(tmp_path):
    job = _job(
        "Responsibilities\nOwn the roadmap.\nRequirements\n"
        "The working language is Portuguese. English is optional."
    )
    annotation = _annotate(job, tmp_path)
    assert [
        claim["value"]
        for claim in annotation["summary"]["knowns"]
        if claim["kind"] == "working_language"
    ] == ["Portuguese"]
    assert annotation["summary"]["contradictions"] == []
    evaluation = _grade(job)
    assert evaluation.decision == FilterDecision.REJECT
    assert any("language" in blocker for blocker in evaluation.blocker_signals)
    assert evaluation.fit_score == 0
    assert evaluation.tier == JobTier.C
