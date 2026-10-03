from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from sqlalchemy import select

from job_intake.annotation import ai
from job_intake.annotation.service import (
    ANNOTATION_VERSION,
    VacancyAnnotator,
    apply_annotation_constraints,
)
from job_intake.annotation.text import chunk_source, source_hash, source_identity, source_units
from job_intake.config.settings import (
    AnnotationConfig,
    AppConfig,
    LLMConfig,
    SourceDefinition,
    TelegramConfig,
)
from job_intake.filtering import FilterRules, RuleEngine
from job_intake.models.job import FilterDecision, JobEvaluation, JobRecord
from job_intake.pipeline import JobIntakePipeline
from job_intake.storage.models import JobORM


@pytest.fixture(autouse=True)
def forbid_live_model(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("Annotation service test attempted a live model request")

    monkeypatch.setattr(ai.AnnotationModelClient, "generate", forbidden)


@pytest.fixture
def config(tmp_path, monkeypatch):
    monkeypatch.setenv("ANNOTATION_SERVICE_TEST_KEY", "fake-test-key")
    return AnnotationConfig(
        ai_enabled=True,
        api_key_env="ANNOTATION_SERVICE_TEST_KEY",
        cache_dir=str(tmp_path / "annotation-cache"),
        chunk_chars=1000,
    )


def record(**changes):
    data = {
        "source": "test-source",
        "source_job_id": "17",
        "title": "Product Manager",
        "company": "Example",
        "original_url": "https://example.com/jobs/17",
        "remote_text": "Remote",
        "description_clean": (
            "Requirements:\n"
            "Applicants must demonstrate English proficiency.\n"
            "Applicants may work remotely from Brazil.\n"
        ),
    }
    data.update(changes)
    return JobRecord(**data)


def language_claim(unit):
    return {
        "kind": "working_language",
        "value": "English",
        "unit_id": unit["unit_id"],
        "source_snippet": unit["text"].strip(),
        "requirement": "required",
        "polarity": "affirmative",
        "is_inference": False,
    }


def factual_claims(data):
    claims = []
    for unit in data["units"]:
        if "demonstrate English proficiency" in unit["text"]:
            claims.append(language_claim(unit))
        elif "remotely from Brazil" in unit["text"]:
            claims.append({
                **language_claim(unit), "kind": "hiring_location", "value": "Brazil",
            })
    return claims


class ModelFake:
    def __init__(self, config, extractor=factual_claims, review_status="SUPPORTED"):
        self.config = config
        self.extractor = extractor
        self.review_status = review_status
        self.calls = []
        self.fail_reviews = 0

    def generate(self, model, prompt):
        extraction = model == self.config.extract_model
        prefix = ai.EXTRACTION_PROMPT if extraction else ai.REVIEW_PROMPT
        data = json.loads(prompt[len(prefix):])
        self.calls.append(("extract" if extraction else "review", data))
        if extraction:
            result = {"claims": self.extractor(data)}
        else:
            assert model == self.config.review_model
            if self.fail_reviews:
                self.fail_reviews -= 1
                raise RuntimeError("simulated provider failure")
            result = {"reviews": [{
                "claim_id": claim["claim_id"], "review_status": self.review_status,
                "review_notes": "Checked against the full supplied source unit.",
                "is_inference": False,
            } for claim in data["claims"]]}
        return result, {"input_tokens": 5, "output_tokens": 7, "total_tokens": 12}


def annotator_with_fake(config, **kwargs):
    annotator = VacancyAnnotator(config)
    fake = ModelFake(config, **kwargs)
    annotator.client = fake
    return annotator, fake


def cache_path(annotator, job):
    return Path(annotator.config.cache_dir) / source_identity(job) / (
        annotator._cache_key(job) + ".json"
    )


def write_cache(annotator, job, stages, **changes):
    data = {
        "version": ANNOTATION_VERSION,
        "source_id": source_identity(job),
        "source_hash": source_hash(job),
        "cache_key": annotator._cache_key(job),
        "stages": stages,
        "usage": [],
    }
    data.update(changes)
    path = cache_path(annotator, job)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_default_annotation_never_calls_ai_even_when_key_and_ai_setting_are_present(config):
    annotator = VacancyAnnotator(config)
    job = record()
    result = annotator.annotate(job)
    assert result["method"] == "deterministic"
    assert result["status"] == "local"
    assert annotator.stats["extraction_calls"] == 0
    assert annotator.stats["review_calls"] == 0
    assert "working_language" in result["summary"]["unknowns"]
    assert not Path(config.cache_dir).exists()


def test_disabled_ai_setting_prevents_explicit_ai_calls(config):
    annotator = VacancyAnnotator(replace(config, ai_enabled=False))
    result = annotator.annotate(record(), use_ai=True)
    assert result["method"] == "deterministic"
    assert annotator.stats["extraction_calls"] == 0


def test_missing_process_key_retains_local_facts_without_network(config, monkeypatch):
    monkeypatch.delenv(config.api_key_env)
    result = VacancyAnnotator(config).annotate(record(), use_ai=True)
    assert result["status"] == "local"
    assert "AI unavailable" in result["issues"][0]
    assert any(claim["kind"] == "role" for claim in result["summary"]["knowns"])
    assert "working_language" in result["summary"]["unknowns"]


@pytest.mark.parametrize("description", [
    "Is the working language: English?",
    "It is not confirmed that the working language is English.",
])
def test_local_language_question_or_unconfirmed_statement_does_not_confirm_requirement(
    config, description
):
    job = record(description_clean=description)
    result = VacancyAnnotator(config).annotate(job)
    assert "working_language" in result["summary"]["unknowns"]
    assert not any(claim["kind"] == "working_language" for claim in result["summary"]["knowns"])


def test_exact_source_evidence_is_independently_reviewed_and_saved(config):
    annotator, fake = annotator_with_fake(config)
    job = record()
    result = annotator.annotate(job, use_ai=True)
    assert result is job.annotation
    assert result["status"] == "reviewed"
    assert [stage for stage, _ in fake.calls] == ["extract", "review"]
    extracted_source = fake.calls[0][1]
    reviewed_source = fake.calls[1][1]
    assert reviewed_source["units"] == extracted_source["units"]
    units = {unit["unit_id"]: unit for unit in source_units(job)}
    language = next(claim for claim in result["claims"] if claim["kind"] == "working_language")
    assert language["source_snippet"] in units[language["unit_id"]]["text"]
    assert language["review_status"] == "SUPPORTED"
    assert language["claim_id"] in {
        claim["claim_id"] for claim in reviewed_source["claims"]
    }
    assert {"English", "Brazil"}.issubset({
        claim["value"] for claim in result["summary"]["knowns"]
    })
    assert json.loads(cache_path(annotator, job).read_text())["source_hash"] == source_hash(job)


def test_independent_reviewer_receives_local_seeds_even_when_extractor_returns_no_claims(config):
    annotator, fake = annotator_with_fake(config, extractor=lambda _data: [])
    job = record(location_text="Brazil", source_metadata={"working_language": "English"})
    result = annotator.annotate(job, use_ai=True)
    assert [stage for stage, _ in fake.calls] == ["extract", "review"]
    reviewed_drafts = fake.calls[-1][1]["claims"]
    assert {claim["source_field"] for claim in reviewed_drafts} == {
        "title", "location_text", "remote_text", "source_metadata.working_language",
    }
    assert {claim["claim_id"] for claim in result["claims"]} == {
        claim["claim_id"] for claim in reviewed_drafts
    }
    assert all(claim["review_method"] == "model" for claim in result["claims"])
    assert result["status"] == "reviewed"


@pytest.mark.parametrize("status", ["WEAKLY_SUPPORTED", "UNSUPPORTED"])
def test_omitted_local_conditions_are_not_promoted_past_independent_reviewer(config, status):
    annotator, _ = annotator_with_fake(
        config, extractor=lambda _data: [], review_status=status,
    )
    job = record(location_text="Brazil", source_metadata={"working_language": "English"})
    result = annotator.annotate(job, use_ai=True)
    assert "working_language" in result["summary"]["unknowns"]
    assert "hiring_location" in result["summary"]["unknowns"]
    assert not any(
        claim["kind"] in {"working_language", "hiring_location"}
        for claim in result["summary"]["knowns"]
    )
    rules = engine()
    evaluation = rules.evaluate(job)
    apply_annotation_constraints(job, evaluation, rules)
    assert evaluation.decision == FilterDecision.REVIEW


@pytest.mark.parametrize("model_value", ["English", "English proficiency"])
def test_different_requirement_or_value_cannot_leave_an_unreviewed_local_supported_copy(
    config, model_value
):
    def extract(data):
        unit = next(unit for unit in data["units"] if "working language is English" in unit["text"])
        return [{**language_claim(unit), "value": model_value, "requirement": "required"}]

    annotator, fake = annotator_with_fake(config, extractor=extract, review_status="UNSUPPORTED")
    job = record(description_clean="The working language is English.", location_text="Brazil")
    result = annotator.annotate(job, use_ai=True)
    language_drafts = [
        claim for claim in fake.calls[-1][1]["claims"] if claim["kind"] == "working_language"
    ]
    assert {claim["requirement"] for claim in language_drafts} == {"required", "informational"}
    assert {claim["value"] for claim in language_drafts} == {"English", model_value}
    assert not any(claim["kind"] == "working_language" for claim in result["summary"]["knowns"])
    rules = engine()
    evaluation = rules.evaluate(job)
    apply_annotation_constraints(job, evaluation, rules)
    assert evaluation.decision == FilterDecision.REVIEW


def test_offline_claims_identify_local_review_instead_of_independent_model_review(config):
    result = VacancyAnnotator(config).annotate(record())
    assert result["method"] == "deterministic"
    assert result["claims"]
    assert all(claim["review_method"] == "local" for claim in result["claims"])


def test_completed_cache_reuses_both_stages_for_fresh_record(config):
    annotator, first = annotator_with_fake(config)
    result = annotator.annotate(record(), use_ai=True)
    assert len(first.calls) == 2
    second, fake = annotator_with_fake(config)
    cached = second.annotate(record(), use_ai=True)
    assert fake.calls == []
    assert cached["status"] == "reviewed"
    assert cached["claims"] == result["claims"]
    assert second.stats["cache_hits"] >= 1


def test_partial_resume_only_retries_missing_review(config):
    annotator, fake = annotator_with_fake(config)
    fake.fail_reviews = 1
    job = record()
    result = annotator.annotate(job, use_ai=True)
    assert result["status"] == "partial"
    language = next(claim for claim in result["claims"] if claim["kind"] == "working_language")
    assert language["review_status"] == "UNREVIEWED"
    assert language["claim_id"] not in result["summary"]["supported_claim_ids"]
    assert [stage for stage, _ in fake.calls] == ["extract", "review"]
    second, retried = annotator_with_fake(config)
    resumed = second.annotate(record(), use_ai=True)
    assert [stage for stage, _ in retried.calls] == ["review"]
    assert resumed["status"] == "reviewed"
    assert resumed["coverage"] == {"chunks": 1, "completed_chunks": 1}


def test_all_chunks_and_final_tail_are_present_in_both_passes(config):
    config = replace(config, chunk_chars=256)
    description = "Responsibilities:\n" + "".join(
        f"You will lead experiment number {index} with careful measurement.\n"
        for index in range(15)
    ) + "Applicants must demonstrate English proficiency.\n"
    job = record(description_clean=description)

    def extract_every_chunk(data):
        unit = next(unit for unit in data["units"] if (
            unit["field"] == "title"
            or (unit["field"].startswith("description") and len(unit["text"].split()) > 1)
        ))
        return [{
            **language_claim(unit),
            "kind": "role" if unit["field"] == "title" else "responsibility",
            "value": unit["text"].strip(),
            "requirement": "informational",
        }]

    annotator, fake = annotator_with_fake(config, extractor=extract_every_chunk)
    result = annotator.annotate(job, use_ai=True)
    expected = chunk_source(source_units(job), config.chunk_chars)
    extracted = [data for stage, data in fake.calls if stage == "extract"]
    reviewed = [data for stage, data in fake.calls if stage == "review"]
    assert len(extracted) == len(reviewed) == len(expected)
    assert result["coverage"] == {"chunks": len(expected), "completed_chunks": len(expected)}
    description_fragments = [
        unit["text"] for data in extracted for unit in data["units"]
        if unit["field"].startswith("description")
    ]
    assert "".join(description_fragments) == description
    assert "demonstrate English proficiency" in extracted[-1]["text"]
    assert [data["chunk_id"] for data in reviewed] == [data["chunk_id"] for data in extracted]


@pytest.mark.parametrize("change", ["source_text", "metadata", "source", "model", "prompts"])
def test_cache_is_invalidated_by_source_model_or_prompt_changes(config, monkeypatch, change):
    first, fake = annotator_with_fake(config)
    job = record()
    first.annotate(job, use_ai=True)
    assert len(fake.calls) == 2
    changed = record()
    if change == "source_text":
        changed.description_clean += "Additional hiring note.\n"
    elif change == "metadata":
        changed.source_metadata = {"description_complete": False}
    elif change == "source":
        changed.source = "other-source"
    elif change == "model":
        config = replace(config, review_model="gemini-distinct-review")
    elif change == "prompts":
        monkeypatch.setattr(ai, "REVIEW_PROMPT", ai.REVIEW_PROMPT + "Additional instruction.\n")
    second, retry = annotator_with_fake(config)
    result = second.annotate(changed, use_ai=True)
    expected = ["review"] if change in {"model", "prompts"} else ["extract", "review"]
    assert [stage for stage, _ in retry.calls] == expected
    assert result["status"] == "reviewed"


@pytest.mark.parametrize("cache_change", [
    {"stages": []},
    {"stages": {"chunk-000001": []}},
    {"stages": {"chunk-000001": {"extraction": {"unexpected": []}}}},
    {"stages": {"chunk-000001": {"review": {"unexpected": []}}}},
    {"stages": {}, "usage": "invalid-usage"},
])
def test_malformed_persisted_cache_is_rebuilt_safely(config, cache_change):
    annotator, fake = annotator_with_fake(config)
    job = record()
    changes = dict(cache_change)
    write_cache(annotator, job, changes.pop("stages"), **changes)
    result = annotator.annotate(job, use_ai=True)
    assert result["status"] == "reviewed"
    assert result["summary"]["coverage"]["working_language"]
    assert [stage for stage, _ in fake.calls] == ["extract", "review"]
    assert isinstance(result["usage"], list)


def test_non_json_cache_is_ignored_without_losing_vacancy(config):
    annotator, fake = annotator_with_fake(config)
    job = record()
    path = cache_path(annotator, job)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not-json", encoding="utf-8")
    result = annotator.annotate(job, use_ai=True)
    assert result["status"] == "reviewed"
    assert len(fake.calls) == 2
    assert any("cache" in issue.lower() for issue in result["issues"])


def test_missing_review_stays_unverified_and_does_not_promote(config):
    annotator, fake = annotator_with_fake(config)
    original_generate = fake.generate

    def omit_reviews(model, prompt):
        if model == config.review_model:
            return {"reviews": []}, {}
        return original_generate(model, prompt)

    fake.generate = omit_reviews
    result = annotator.annotate(record(), use_ai=True)
    language = next(claim for claim in result["claims"] if claim["kind"] == "working_language")
    assert language["review_status"] == "NEEDS_VERIFICATION"
    assert "working_language" in result["summary"]["unknowns"]


def test_omitted_reviews_are_resumed_without_paying_for_extraction_again(config):
    annotator, fake = annotator_with_fake(config)
    original_generate = fake.generate

    def omit_reviews(model, prompt):
        if model == config.review_model:
            return {"reviews": []}, {}
        return original_generate(model, prompt)

    fake.generate = omit_reviews
    initial = annotator.annotate(record(), use_ai=True)
    assert initial["status"] == "partial"
    second, retry = annotator_with_fake(config)
    result = second.annotate(record(), use_ai=True)
    assert [stage for stage, _ in retry.calls] == ["review"]
    assert result["status"] == "reviewed"
    assert result["summary"]["coverage"]["working_language"]


def test_invalid_quotes_are_rejected_before_reviewer_and_retained_for_audit(config):
    def invented_quote(data):
        unit = next(unit for unit in data["units"] if unit["field"].startswith("description"))
        return [{**language_claim(unit), "source_snippet": "English is never required here."}]

    annotator, fake = annotator_with_fake(config, extractor=invented_quote)
    result = annotator.annotate(record(), use_ai=True)
    assert [stage for stage, _ in fake.calls] == ["extract", "review"]
    assert all(
        claim["source_snippet"] != "English is never required here."
        for claim in fake.calls[-1][1]["claims"]
    )
    assert result["rejected"]
    assert "not verbatim" in result["rejected"][-1]["reason"]
    assert "working_language" in result["summary"]["unknowns"]


def engine():
    return RuleEngine(FilterRules.from_mapping({
        "required_languages": ["English"],
        "positive_title_signals": ["product manager"],
        "positive_description_signals": ["experimentation"],
        "recall_first": True,
        "target_geographies": ["Brazil"],
    }))


@pytest.mark.parametrize("review_status", ["UNSUPPORTED", "NEEDS_VERIFICATION", "CONFLICTING"])
def test_unsupported_review_cannot_clear_uncertainty_or_promote(config, review_status):
    annotator, _ = annotator_with_fake(config, review_status=review_status)
    job = record()
    annotator.annotate(job, use_ai=True)
    evaluation = JobEvaluation(
        decision=FilterDecision.REVIEW,
        bridge_role=True,
        risks=["language:working_language_unconfirmed", "geography:eligibility_unconfirmed"],
    )
    apply_annotation_constraints(job, evaluation, engine())
    assert evaluation.decision == FilterDecision.REVIEW
    assert "language:working_language_unconfirmed" in evaluation.risks
    assert "geography:eligibility_unconfirmed" in evaluation.risks


def test_supported_exact_claims_can_resolve_only_matching_uncertainties(config):
    annotator, _ = annotator_with_fake(config)
    job = record()
    annotator.annotate(job, use_ai=True)
    evaluation = JobEvaluation(
        decision=FilterDecision.REVIEW,
        bridge_role=True,
        risks=["language:working_language_unconfirmed", "geography:eligibility_unconfirmed"],
    )
    apply_annotation_constraints(job, evaluation, engine())
    assert evaluation.decision == FilterDecision.PASS
    assert evaluation.risks == []


def test_supported_claims_do_not_override_existing_hard_rejection(config):
    annotator, _ = annotator_with_fake(config)
    job = record()
    annotator.annotate(job, use_ai=True)
    evaluation = JobEvaluation(
        decision=FilterDecision.REJECT, blocker_signals=["work_auth:foreign_only"],
        bridge_role=False,
    )
    before = deepcopy(evaluation)
    apply_annotation_constraints(job, evaluation, engine())
    assert evaluation == before


def test_partial_review_retains_language_and_geography_uncertainties(config):
    annotator, fake = annotator_with_fake(config)
    fake.fail_reviews = 1
    job = record()
    annotator.annotate(job, use_ai=True)
    evaluation = JobEvaluation(
        decision=FilterDecision.REVIEW, bridge_role=True,
        risks=["language:working_language_unconfirmed", "geography:eligibility_unconfirmed"],
    )
    apply_annotation_constraints(job, evaluation, engine())
    assert evaluation.decision == FilterDecision.REVIEW
    assert "annotation:incomplete_source_review" in evaluation.risks
    assert "language:working_language_unconfirmed" in evaluation.risks


def single_fact(config, kind, value, sentence, **changes):
    def extract(data):
        unit = next(
            unit for unit in data["units"]
            if unit["field"].startswith("description") and sentence in unit["text"]
        )
        return [{**language_claim(unit), "kind": kind, "value": value, **changes}]

    annotator, fake = annotator_with_fake(config, extractor=extract)
    job = record(description_clean=sentence)
    annotator.annotate(job, use_ai=True)
    return job, fake


def test_supported_negative_hiring_location_excludes_candidate_in_target_country(config):
    job, _ = single_fact(
        config, "hiring_location", "Brazil",
        "This vacancy is unavailable to applicants residing in Brazil.",
        polarity="negative",
    )
    evaluation = JobEvaluation(decision=FilterDecision.PASS, bridge_role=True)
    apply_annotation_constraints(job, evaluation, engine())
    assert evaluation.decision == FilterDecision.REJECT
    assert "annotation:hiring_exclusion:Brazil" in evaluation.blocker_signals


@pytest.mark.parametrize(("kind", "value", "sentence", "risk"), [
    (
        "work_authorization", "Employer sponsorship eligibility",
        "Applicants must establish eligibility for employer sponsorship.",
        "annotation:work_authorization_unconfirmed",
    ),
    (
        "timezone", "UTC 02:00-06:00 overlap",
        "Applicants must maintain an overlap between 02:00 and 06:00 UTC.",
        "annotation:timezone_compatibility_unconfirmed",
    ),
    (
        "work_mode", "On-site",
        "This position requires on-site attendance at the employer office.",
        "annotation:work_mode_compatibility_unconfirmed",
    ),
])
def test_supported_requirements_with_unknown_candidate_compatibility_force_review(
    config, kind, value, sentence, risk
):
    job, _ = single_fact(config, kind, value, sentence)
    evaluation = JobEvaluation(decision=FilterDecision.PASS, bridge_role=True)
    apply_annotation_constraints(job, evaluation, engine())
    assert evaluation.decision == FilterDecision.REVIEW
    assert risk in evaluation.risks
    assert not evaluation.blocker_signals


def test_matching_timezone_requirement_does_not_create_unknown_compatibility_risk(config):
    job, _ = single_fact(
        config, "timezone", "Americas time zone",
        "Applicants must work in Americas time zone.",
    )
    matching = engine()
    matching.rules.timezone_allowed = ["Americas time zone"]
    evaluation = JobEvaluation(decision=FilterDecision.PASS, bridge_role=True)
    apply_annotation_constraints(job, evaluation, matching)
    # Timezone compatibility does not establish missing hiring/language conditions.
    assert evaluation.decision == FilterDecision.REVIEW
    assert "annotation:timezone_compatibility_unconfirmed" not in evaluation.risks


@pytest.mark.parametrize("closed", ["closed", "filled", "no longer accepting applications"])
def test_supported_closed_vacancy_is_rejected_even_if_raw_status_was_open(config, closed):
    job, _ = single_fact(
        config, "status", closed, f"This vacancy is {closed}.", requirement="informational",
    )
    evaluation = JobEvaluation(decision=FilterDecision.PASS, bridge_role=True)
    apply_annotation_constraints(job, evaluation, engine())
    assert evaluation.decision == FilterDecision.REJECT
    assert "annotation:status:" + closed in evaluation.blocker_signals


def test_offline_partial_preserves_unfinished_review_and_never_promotes(config):
    first, fake = annotator_with_fake(config)
    fake.fail_reviews = 1
    initial_job = record()
    partial = first.annotate(initial_job, use_ai=True)
    cache_path(first, initial_job).unlink()
    offline_job = record(annotation=deepcopy(partial))
    offline = VacancyAnnotator(config).annotate(offline_job)
    assert offline["method"] == "two_pass"
    assert offline["status"] == "partial"
    assert "working_language" in offline["summary"]["unknowns"]
    assert offline["coverage"]["completed_chunks"] == 0
    evaluation = JobEvaluation(decision=FilterDecision.PASS, bridge_role=True)
    apply_annotation_constraints(offline_job, evaluation, engine())
    assert evaluation.decision == FilterDecision.REVIEW
    assert "annotation:incomplete_source_review" in evaluation.risks


def test_partial_record_resumes_from_persisted_stages_when_cache_file_is_missing(config):
    first, fake = annotator_with_fake(config)
    fake.fail_reviews = 1
    initial_job = record()
    partial = first.annotate(initial_job, use_ai=True)
    cache_path(first, initial_job).unlink()
    second, resumed = annotator_with_fake(config)
    result = second.annotate(record(annotation=deepcopy(partial)), use_ai=True)
    assert [stage for stage, _ in resumed.calls] == ["review"]
    assert result["status"] == "reviewed"
    assert result["summary"]["coverage"]["working_language"]


@pytest.mark.parametrize("invalid_model", ["", "  ", None, 123, ["review-model"]])
def test_invalid_saved_review_model_cannot_reuse_reviewed_claims_offline(config, invalid_model):
    first, _ = annotator_with_fake(config)
    initial_job = record()
    saved = first.annotate(initial_job, use_ai=True)
    saved["review_model"] = invalid_model
    cache_path(first, initial_job).unlink()
    job = record(annotation=saved)
    result = VacancyAnnotator(config).annotate(job)
    assert result["method"] == "two_pass"
    assert result["status"] == "partial"
    assert "working_language" in result["summary"]["unknowns"]
    assert result["issues"]
    evaluation = JobEvaluation(decision=FilterDecision.PASS, bridge_role=True)
    apply_annotation_constraints(job, evaluation, engine())
    assert evaluation.decision == FilterDecision.REVIEW
    assert "annotation:incomplete_source_review" in evaluation.risks


@pytest.mark.parametrize("usage", [None, "bad usage", {"input_tokens": 5}, [False]])
def test_malformed_usage_in_saved_partial_is_reset_without_losing_extraction(config, usage):
    first, fake = annotator_with_fake(config)
    fake.fail_reviews = 1
    initial_job = record()
    partial = first.annotate(initial_job, use_ai=True)
    partial["usage"] = usage
    cache_path(first, initial_job).unlink()
    second, resumed = annotator_with_fake(config)
    result = second.annotate(record(annotation=partial), use_ai=True)
    assert result["status"] == "reviewed"
    assert [stage for stage, _ in resumed.calls] == ["review"]
    assert isinstance(result["usage"], list)
    assert all(isinstance(row, dict) for row in result["usage"])


def test_fabricated_persisted_summary_does_not_become_trusted_evidence(config):
    annotator, _ = annotator_with_fake(config)
    saved = annotator.annotate(record(), use_ai=True)
    tampered = deepcopy(saved)
    claim = next(claim for claim in tampered["claims"] if claim["kind"] == "working_language")
    claim["source_snippet"] = "A nonexistent requirement fabricated in a saved annotation."
    tampered["summary"]["knowns"] = [claim]
    job = record(annotation=tampered)
    second, fake = annotator_with_fake(config)
    result = second.annotate(job, use_ai=True)
    assert all(
        known["source_snippet"] != claim["source_snippet"]
        for known in result["summary"]["knowns"]
    )
    assert all(
        known["source_snippet"] in unit["text"]
        for known in result["summary"]["knowns"]
        for unit in source_units(job) if unit["unit_id"] == known["unit_id"]
    )
    assert fake.calls == []  # Valid on-disk stage evidence remains reusable.


def pipeline_config(tmp_path, annotation_config):
    rules = tmp_path / "rules.yaml"
    profiles = tmp_path / "profiles.yaml"
    rules.write_text(yaml.safe_dump({
        "recall_first": True,
        "required_languages": ["English"],
        "target_geographies": ["Brazil", "Worldwide"],
    }), encoding="utf-8")
    profiles.write_text(yaml.safe_dump({"streams": [{
        "id": profile_id, "name": profile_id, "context": profile_id,
        "keywords": ["Product Manager"],
        "rules": {"positive_title_signals": ["product manager"]},
        "scoring": {
            "title_weights": {"product manager": weight},
            "threshold_a": 10, "threshold_b": 5,
        },
    } for profile_id, weight in [("product", 15), ("analytics", 7)]]}), encoding="utf-8")
    return AppConfig(
        database_url=f"sqlite:///{tmp_path / 'pipeline.db'}",
        log_level="WARNING",
        rules_path=rules,
        search_profiles_path=profiles,
        company_watchlist_path=tmp_path / "watchlist.yaml",
        export_dir=tmp_path,
        sources=[SourceDefinition(name="test-source", type="html")],
        telegram=TelegramConfig(enabled=False),
        llm=LLMConfig(enabled=False),
        annotation=annotation_config,
    )


def test_pipeline_annotates_once_for_all_profiles_and_persists_exact_evidence(
    config, tmp_path, monkeypatch
):
    app_config = pipeline_config(tmp_path, config)
    monkeypatch.setattr("job_intake.pipeline.build_adapter", lambda _source: SimpleNamespace(
        errors=[], fetch_jobs=lambda: [record()],
    ))
    pipeline = JobIntakePipeline(app_config)
    fake = ModelFake(config)
    pipeline.annotator.client = fake
    result = pipeline.run()
    assert result["record_errors"] == 0
    assert result["persisted"] == 1
    assert result["evaluations"] == 2
    assert result["annotation_extraction_calls"] == 1
    assert result["annotation_review_calls"] == 1
    assert result["llm_calls"] == 0
    assert len(fake.calls) == 2
    with pipeline.database.session() as session:
        stored = session.scalar(select(JobORM))
        assert stored.annotation["status"] == "reviewed"
        assert stored.annotation["source_hash"] == source_hash(record())
        assert {p.profile_id for p in stored.profile_evaluations} == {"product", "analytics"}
        assert all(p.semantic_score is None for p in stored.profile_evaluations)
        assert {p.profile_id: p.fit_score for p in stored.profile_evaluations} == {
            "product": 15, "analytics": 7,
        }
    second = JobIntakePipeline(app_config)
    repeated = ModelFake(config)
    second.annotator.client = repeated
    again = second.run()
    assert again["evaluations"] == 2
    assert again["annotation_extraction_calls"] == 0
    assert again["annotation_review_calls"] == 0
    assert repeated.calls == []


def test_pipeline_offline_reevaluation_keeps_reviewed_evidence_without_api_or_notifications(
    config, tmp_path, monkeypatch
):
    app_config = pipeline_config(tmp_path, config)
    monkeypatch.setattr("job_intake.pipeline.build_adapter", lambda _source: SimpleNamespace(
        errors=[], fetch_jobs=lambda: [record()],
    ))
    pipeline = JobIntakePipeline(app_config)
    pipeline.annotator.client = ModelFake(config)
    assert pipeline.run()["persisted"] == 1
    with pipeline.database.session() as session:
        stored = session.scalar(select(JobORM))
        before = stored.first_seen_at, stored.last_seen_at, deepcopy(stored.annotation)

    def forbidden(*_args, **_kwargs):
        pytest.fail("Offline reevaluation attempted an external side effect")

    monkeypatch.setattr("job_intake.pipeline.build_adapter", forbidden)
    pipeline.telegram.send = forbidden
    result = pipeline.reevaluate_saved()
    assert result["persisted"] == 1
    assert result["evaluations"] == 2
    assert result["annotation_extraction_calls"] == 0
    assert result["annotation_review_calls"] == 0
    with pipeline.database.session() as session:
        stored = session.scalar(select(JobORM))
        assert (stored.first_seen_at, stored.last_seen_at) == before[:2]
        assert stored.annotation["summary"] == before[2]["summary"]
        assert stored.annotation["status"] == "reviewed"


@pytest.mark.parametrize("timestamp", [
    "2026-10-03T18:10:20.123456-03:00",
    "2026-10-04T01:10:20.654321+09:00",
])
def test_non_utc_source_timestamp_roundtrip_preserves_reviewed_annotation_cache(
    config, tmp_path, monkeypatch, timestamp
):
    posted_at = datetime.fromisoformat(timestamp)
    job = record(posted_at=posted_at)
    app_config = pipeline_config(tmp_path, config)
    monkeypatch.setattr("job_intake.pipeline.build_adapter", lambda _source: SimpleNamespace(
        errors=[], fetch_jobs=lambda: [record(posted_at=posted_at)],
    ))
    first = JobIntakePipeline(app_config)
    first_fake = ModelFake(config)
    first.annotator.client = first_fake
    assert first.run()["persisted"] == 1
    assert len(first_fake.calls) == 2
    with first.database.session() as session:
        stored = session.scalar(select(JobORM))
        assert stored.posted_at == posted_at.astimezone(UTC).replace(tzinfo=None)
        initial_hash = stored.annotation["source_hash"]
        initial_summary = deepcopy(stored.annotation["summary"])
        assert initial_hash == source_hash(job)
        assert initial_hash == source_hash(record(posted_at=stored.posted_at))

    # Reingestion checks the update path; a reload with opt-in AI checks UTC-naive reuse.
    second = JobIntakePipeline(app_config)
    repeated = ModelFake(config)
    second.annotator.client = repeated
    assert second.run()["persisted"] == 1
    assert repeated.calls == []
    reevaluated = second.reevaluate_saved(use_ai=True)
    assert reevaluated["record_errors"] == 0
    assert reevaluated["persisted"] == 1
    assert reevaluated["annotation_cache_hits"] == 1
    assert repeated.calls == []
    with second.database.session() as session:
        stored = session.scalar(select(JobORM))
        assert stored.posted_at == posted_at.astimezone(UTC).replace(tzinfo=None)
        assert stored.annotation["status"] == "reviewed"
        assert stored.annotation["source_hash"] == initial_hash
        assert stored.annotation["summary"] == initial_summary
