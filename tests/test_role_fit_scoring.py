from dataclasses import replace

import pytest

from job_intake.models.job import (
    EvaluatedJob,
    FilterDecision,
    JobEvaluation,
    JobRecord,
    JobTier,
)
from job_intake.scoring.pre_score import DeterministicScorer, SearchProfiles
from job_intake.scoring.tiering import finalize_tier


def profiles(policy="role_fit_v1"):
    return SearchProfiles.from_mapping({
        "scoring_policy": policy, "threshold_a": 14, "threshold_b": 6,
        "title_weights": {"analytics manager": 8, "product analytics lead": 8,
                          "product analytics": 2, "head of data": 4},
        "description_weights": {"experimentation": 3, "business decisions": 3,
                                "decision support": 3, "project leadership": 3},
        "bucket_a_signals": ["experimentation", "business decisions"],
        "source_weights": {"test": 1},
    })


def record(title="Analytics Manager", description=None):
    return JobRecord(
        source="test", company="Example", title=title,
        original_url="https://example.test/job",
        description_clean=description or (
            "Responsibilities:\nLead analytics projects and manage a team. "
            "Design pricing experiments to inform business decisions."
        ),
    )


def score(job, decision=FilterDecision.REVIEW, *, config=None):
    config = config or profiles()
    evaluation = JobEvaluation(decision=decision, bridge_role=True)
    DeterministicScorer(config, role_id="analytics-leadership").score(
        job.source, job.company, job.title, job.description_clean, evaluation, record=job,
    )
    item = EvaluatedJob(job, evaluation)
    finalize_tier(item, config.threshold_a, config.threshold_b)
    return evaluation


def test_role_fit_does_not_change_with_hiring_decision_or_mutate_that_decision():
    outputs = [score(record(), decision) for decision in FilterDecision]
    assert len({output.fit_score for output in outputs}) == 1
    assert all(output.tier == JobTier.A for output in outputs)
    assert [output.decision for output in outputs] == list(FilterDecision)


@pytest.mark.parametrize("prefix", ["title_blocker:", "desc_blocker:"])
def test_explicit_occupation_blocker_caps_fit_even_when_stored_as_hard_reject(prefix):
    job = record()
    config = profiles()
    evaluation = JobEvaluation(
        decision=FilterDecision.REJECT, blocker_signals=[prefix + "unrelated role"],
    )
    DeterministicScorer(config, role_id="analytics-leadership").score(
        job.source, job.company, job.title, job.description_clean, evaluation, record=job,
    )
    finalize_tier(EvaluatedJob(job, evaluation), config.threshold_a, config.threshold_b)
    assert evaluation.tier == JobTier.C
    assert evaluation.fit_max_tier == JobTier.C
    assert evaluation.decision == FilterDecision.REJECT
    assert "fit:explicit_role_mismatch" in evaluation.risks


def test_reordered_title_receives_same_existing_bonus():
    outputs = [score(record(title=title)) for title in (
        "Analytics Manager", "Manager, Analytics", "Manager of Analytics",
    )]
    assert len({output.fit_score for output in outputs}) == 1
    assert all("title_weight:analytics manager" in output.matched_signals for output in outputs)


def test_nested_title_bonus_is_not_counted_twice():
    config = profiles()
    job = record(title="Product Analytics Lead")
    full = score(job, config=config)
    single = score(job, config=replace(config, title_weights={"product analytics lead": 8}))
    assert full.fit_score == single.fit_score
    assert [signal for signal in full.matched_signals if signal.startswith("title_weight:")] == [
        "title_weight:product analytics lead",
    ]


def test_synonyms_of_one_decision_concept_do_not_accumulate():
    job = record(description=(
        "Responsibilities:\nLead analytics projects. "
        "Provide decision support and use analysis to inform business decisions."
    ))
    config = profiles()
    full = score(job, config=config)
    weights = {key: value for key, value in config.description_weights.items()
               if key != "decision support"}
    single = score(job, config=replace(config, description_weights=weights))
    assert full.fit_score == single.fit_score


def test_requirements_and_company_words_do_not_raise_content_fit():
    job = record(description=(
        "Responsibilities:\nBuild dashboards.\nRequirements:\n"
        "Experience leading pricing experiments and business decisions.\n"
        "About the company:\nWe lead experimentation projects worldwide."
    ))
    result = score(job)
    assert result.tier == JobTier.C
    assert not any(signal.startswith("description_weight:") for signal in result.matched_signals)


def test_reporting_core_is_low_fit_even_if_the_title_is_senior():
    job = record(description=(
        "Responsibilities:\nLead business reporting initiatives. "
        "Build and deliver reports to guide business decisions."
    ))
    result = score(job)
    assert result.tier == JobTier.C
    assert "fit:reporting_core" in result.risks
    assert result.decision == FilterDecision.REVIEW
    # A later reranker cannot silently undo a source-grounded scope cap.
    result.fit_score = 99
    finalize_tier(EvaluatedJob(job, result), 14, 6)
    assert result.tier == JobTier.C


def test_reports_do_not_penalize_an_independent_experiment_and_decision_mandate():
    job = record(description=(
        "Responsibilities:\nLead pricing experimentation projects with business teams. "
        "Recommend pricing changes to inform business decisions. "
        "Build reports to communicate the results."
    ))
    result = score(job)
    assert result.tier == JobTier.A
    assert "fit:reporting_core" not in result.risks


def test_individual_contributor_with_project_responsibility_can_be_a():
    result = score(record(title="Product Analytics Lead", description=(
        "Responsibilities:\nLead cross-functional pricing experimentation projects. "
        "Recommend actions to inform business decisions. This role has no direct reports."
    )))
    assert result.tier == JobTier.A


def test_unconfirmed_project_scope_caps_a_without_rejecting_the_role():
    result = score(record(description=(
        "Responsibilities:\nAnalyze pricing experiments and inform business decisions."
    )))
    assert result.tier != JobTier.A
    assert result.decision == FilterDecision.REVIEW
    assert "fit:project_leadership_unconfirmed" in result.risks


def test_legacy_policy_preserves_gate_penalty_and_reject_score():
    config = profiles("legacy")
    passing = score(record(), FilterDecision.PASS, config=config)
    review = score(record(), config=config)
    rejected = score(record(), FilterDecision.REJECT, config=config)
    assert passing.fit_score - review.fit_score == 3
    assert review.tier != JobTier.A
    assert rejected.fit_score == 0 and rejected.tier == JobTier.C


def test_unknown_scoring_policy_is_not_silently_ignored():
    with pytest.raises(ValueError, match="scoring_policy"):
        SearchProfiles.from_mapping({"scoring_policy": "typo"})


def test_scope_cap_survives_a_serialized_batch_evaluation():
    import json
    from dataclasses import asdict

    job = record(description="Responsibilities:\nBuild and deliver business reports.")
    result = score(job)
    restored = JobEvaluation(**json.loads(json.dumps(asdict(result))))
    restored.fit_score = 99
    finalize_tier(EvaluatedJob(job, restored), 14, 6)
    assert restored.tier == JobTier.C


def test_raw_score_preserves_uncapped_order_for_reporting_mismatch():
    job = record(description="Responsibilities:\nBuild reports and dashboards.")
    result = score(job)
    assert result.raw_fit_score == 9  # existing configured title + source; no invented bonus
    assert result.fit_score == 5.5
    assert result.score_cap_reasons == ["reporting_core"]
    assert result.tier == JobTier.C


def test_incomplete_unknown_mandate_caps_b_without_inventing_a_fit_floor():
    job = record(description="Details are not available from this source.")
    job.source_metadata["description_complete"] = False
    result = score(job)
    assert result.raw_fit_score == result.fit_score == 9
    assert result.fit_max_tier == JobTier.B and result.tier == JobTier.B
    assert result.score_cap_reasons == ["incomplete_description"]
    assert "fit:analytical_mandate_unconfirmed" not in result.risks


def test_incomplete_source_never_promotes_explicit_reporting_mismatch():
    job = record(description="Responsibilities:\nBuild reports and dashboards.")
    job.source_metadata["description_complete"] = False
    result = score(job)
    assert result.tier == JobTier.C and result.fit_max_tier == JobTier.C
    assert result.score_cap_reasons == ["reporting_core"]
