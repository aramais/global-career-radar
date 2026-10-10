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


def test_interrupted_review_resumes_extraction_from_small_chunk_checkpoint(config):
    first, fake = annotator_with_fake(config)
    job = record()
    original = fake.generate

    def interrupted(model, prompt):
        if model == config.review_model:
            raise KeyboardInterrupt("simulated process interruption")
        return original(model, prompt)

    fake.generate = interrupted
    with pytest.raises(KeyboardInterrupt):
        first.annotate(job, use_ai=True)
    assert not cache_path(first, job).exists()
    second, retried = annotator_with_fake(config)
    result = second.annotate(record(), use_ai=True)
    assert result["status"] == "reviewed"
    assert [stage for stage, _ in retried.calls] == ["review"]
    assert {row["stage"] for row in result["usage"]} == {"extract", "review"}


def test_failed_review_saves_safe_diagnostics_and_tokens_then_retries(config):
    annotator, fake = annotator_with_fake(config)
    job = record()
    original = fake.generate

    def truncated(model, prompt):
        if model == config.review_model:
            raise ai.AnnotationModelError(
                "Annotation provider returned an incomplete result.", code="output_truncated",
                finish_reason="MAX_TOKENS",
                usage={"input_tokens": 100, "output_tokens": 4096, "total_tokens": 4196},
            )
        return original(model, prompt)

    fake.generate = truncated
    result = annotator.annotate(job, use_ai=True)
    assert result["status"] == "partial"
    assert len(result["errors"]) == 1
    diagnostic = result["errors"][0]
    assert diagnostic["stage"] == "review"
    assert diagnostic["code"] == "output_truncated"
    assert diagnostic["finish_reason"] == "MAX_TOKENS"
    assert diagnostic["chunk_id"] in result["stages"]
    assert diagnostic["usage"]["output_tokens"] == 4096
    failed = [row for row in result["usage"] if row.get("failed")]
    assert len(failed) == 1
    assert failed[0]["stage"] == "review"
    assert failed[0]["output_tokens"] == 4096
    saved = json.loads(cache_path(annotator, job).read_text())
    assert saved["errors"] == result["errors"]
    resumed, resumed_fake = annotator_with_fake(config)
    result = resumed.annotate(job, use_ai=True)
    assert result["status"] == "reviewed"
    assert result["errors"] == []
    assert [stage for stage, _ in resumed_fake.calls] == ["review"]


def test_invalid_extraction_envelope_has_stage_diagnostic_and_is_not_cached(config):
    annotator, fake = annotator_with_fake(config)

    def invalid(_model, _prompt):
        return {"private-response": "private-key/raw-source"}, {
            "input_tokens": 5, "output_tokens": 7, "total_tokens": 12,
        }

    fake.generate = invalid
    job = record()
    result = annotator.annotate(job, use_ai=True)
    assert result["status"] == "partial"
    assert result["errors"][0]["code"] == "invalid_extraction"
    assert result["errors"][0]["stage"] == "extract"
    assert result["usage"][0]["failed"] is True
    assert "private-key" not in json.dumps(result)
    assert not any(stage.get("extraction") for stage in result["stages"].values())


def test_checkpoint_write_volume_is_bounded_by_source_size(config, monkeypatch):
    annotator, _ = annotator_with_fake(config, extractor=lambda _: [])
    sizes = []
    full_writes = []

    def capture(path, payload):
        sizes.append(len(json.dumps(payload).encode()))
        if "units" in payload:
            full_writes.append(path)

    monkeypatch.setattr(annotator, "_write", capture)
    description = "Requirements:\n" + "An explicit source sentence.\n" * 1500
    result = annotator.annotate(record(description_clean=description), use_ai=True)
    assert result["coverage"]["chunks"] > 20
    assert result["status"] == "reviewed"
    assert len(full_writes) == 1
    # Complete annotation plus small chunk checkpoints, not N copies of the source.
    assert sum(sizes) < len(json.dumps(result).encode()) * 4


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


def many_claim_record():
    return record(
        remote_text="",
        description_clean="Responsibilities:\n" + "\n".join(
            f"You will coordinate project {index} with customers and business leaders."
            for index in range(24)
        ),
    )


def extract_responsibilities(data):
    return [{
        "kind": "responsibility", "value": unit["text"],
        "unit_id": unit["unit_id"], "source_snippet": unit["text"],
        "requirement": "informational", "polarity": "affirmative", "is_inference": False,
    } for unit in data["units"] if unit["field"].startswith("description")
            and "You will" in unit["text"]]


def test_large_review_batches_preserve_full_source_and_cover_every_claim_once(config):
    config = replace(config, chunk_chars=20000, review_batch_size=8)
    annotator, fake = annotator_with_fake(config, extractor=extract_responsibilities)
    result = annotator.annotate(many_claim_record(), use_ai=True)
    extracted = next(data for stage, data in fake.calls if stage == "extract")
    reviewed = [data for stage, data in fake.calls if stage == "review"]
    assert len(reviewed) >= 3
    assert all(data["units"] == extracted["units"] for data in reviewed)
    assert all(data["chunk_id"] == extracted["chunk_id"] for data in reviewed)
    assert all(1 <= len(data["claims"]) <= 8 for data in reviewed)
    reviewed_ids = [claim["claim_id"] for data in reviewed for claim in data["claims"]]
    assert len(reviewed_ids) == len(set(reviewed_ids))
    assert set(reviewed_ids) == {claim["claim_id"] for claim in result["claims"]}
    assert result["status"] == "reviewed"
    assert annotator.stats["review_calls"] == len(reviewed)
    stage = result["stages"][extracted["chunk_id"]]
    assert len(stage["review_batches"]) == len(reviewed)
    assert len(stage["review"]["reviews"]) == len(reviewed_ids)


def test_failed_later_batch_preserves_completed_review_and_resumes_only_missing_batches(config):
    config = replace(config, chunk_chars=20000, review_batch_size=8)
    first, fake = annotator_with_fake(config, extractor=extract_responsibilities)
    original = fake.generate
    requests = 0

    def fail_second_batch(model, prompt):
        nonlocal requests
        if model == config.review_model:
            requests += 1
            if requests == 2:
                raise ai.AnnotationModelError(
                    "Annotation provider returned an incomplete result.",
                    code="output_truncated", finish_reason="MAX_TOKENS",
                    usage={"input_tokens": 100, "output_tokens": 8192, "total_tokens": 8292},
                )
        return original(model, prompt)

    fake.generate = fail_second_batch
    job = many_claim_record()
    partial = first.annotate(job, use_ai=True)
    assert partial["status"] == "partial"
    assert partial["coverage"]["completed_chunks"] == 0
    assert partial["errors"][0]["batch_id"] == "batch-000002"
    first_review = next(data for stage, data in fake.calls if stage == "review")
    completed_ids = {claim["claim_id"] for claim in first_review["claims"]}
    assert {
        claim["claim_id"] for claim in partial["claims"] if claim["review_method"] == "model"
    } == completed_ids
    assert all(
        claim["review_status"] != "SUPPORTED" for claim in partial["claims"]
        if claim["claim_id"] not in completed_ids
    )
    failed = [usage for usage in partial["usage"] if usage.get("failed")]
    assert len(failed) == 1 and failed[0]["batch_id"] == "batch-000002"
    cache_path(first, job).unlink()
    resumed, retry = annotator_with_fake(config, extractor=extract_responsibilities)
    result = resumed.annotate(job, use_ai=True)
    assert result["status"] == "reviewed"
    assert result["errors"] == []
    assert all(stage == "review" for stage, _ in retry.calls)
    assert not completed_ids.intersection(
        claim["claim_id"] for _, data in retry.calls for claim in data["claims"]
    )
    assert {
        claim["claim_id"] for claim in result["claims"] if claim["review_method"] == "model"
    } == {claim["claim_id"] for claim in result["claims"]}


def test_interrupted_later_batch_resumes_completed_batch_checkpoint(config):
    config = replace(config, chunk_chars=20000, review_batch_size=8)
    first, fake = annotator_with_fake(config, extractor=extract_responsibilities)
    original = fake.generate
    requests = 0

    def interrupt_second_batch(model, prompt):
        nonlocal requests
        if model == config.review_model:
            requests += 1
            if requests == 2:
                raise KeyboardInterrupt("simulated process interruption")
        return original(model, prompt)

    fake.generate = interrupt_second_batch
    job = many_claim_record()
    with pytest.raises(KeyboardInterrupt):
        first.annotate(job, use_ai=True)
    assert not cache_path(first, job).exists()
    completed_ids = {
        claim["claim_id"] for stage, data in fake.calls if stage == "review"
        for claim in data["claims"]
    }
    resumed, retry = annotator_with_fake(config, extractor=extract_responsibilities)
    result = resumed.annotate(many_claim_record(), use_ai=True)
    assert result["status"] == "reviewed"
    assert all(stage == "review" for stage, _ in retry.calls)
    assert not completed_ids.intersection(
        claim["claim_id"] for _, data in retry.calls for claim in data["claims"]
    )


def test_review_cannot_use_foreign_id_to_promote_another_batch(config):
    config = replace(config, chunk_chars=20000, review_batch_size=8)
    annotator, fake = annotator_with_fake(config, extractor=extract_responsibilities)
    original = fake.generate
    job = many_claim_record()
    all_drafts = []

    def inject_foreign_review(model, prompt):
        if model == config.extract_model:
            payload, usage = original(model, prompt)
            from job_intake.annotation.service import _drafts

            chunk = chunk_source(source_units(job), config.chunk_chars)[0]
            all_drafts.extend(_drafts(
                payload, source_identity(job), chunk["units"], chunk["chunk_id"]
            )[0])
            return payload, usage
        payload, usage = original(model, prompt)
        assert len(all_drafts) > config.review_batch_size
        payload["reviews"].append({
            "claim_id": all_drafts[config.review_batch_size]["claim_id"],
            "review_status": "SUPPORTED", "review_notes": "Foreign claim", "is_inference": False,
        })
        return payload, usage

    fake.generate = inject_foreign_review
    partial = annotator.annotate(job, use_ai=True)
    assert partial["status"] == "partial"
    foreign = next(
        claim for claim in partial["claims"]
        if claim["claim_id"] == all_drafts[config.review_batch_size]["claim_id"]
    )
    assert foreign["review_method"] == "unreviewed"
    assert foreign["review_status"] != "SUPPORTED"
    assert any("does not identify" in row["reason"] for row in partial["rejected"])
    assert partial["errors"][0]["code"] == "invalid_review"
    assert partial["errors"][0]["batch_id"] == "batch-000001"


def test_review_batch_size_change_reuses_extraction_but_rebuilds_review(config):
    config = replace(config, chunk_chars=20000, review_batch_size=8)
    first, _ = annotator_with_fake(config, extractor=extract_responsibilities)
    job = many_claim_record()
    initial = first.annotate(job, use_ai=True)
    changed = replace(config, review_batch_size=4)
    second, fake = annotator_with_fake(changed, extractor=extract_responsibilities)
    result = second.annotate(job, use_ai=True)
    assert initial["cache_key"] != result["cache_key"]
    assert result["status"] == "reviewed"
    assert all(stage == "review" for stage, _ in fake.calls)
    assert all(len(data["claims"]) <= 4 for _, data in fake.calls)


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


@pytest.mark.parametrize("corrupt_extraction", [False, True])
def test_offline_partial_retains_source_seeds_when_extraction_failed(
    config, monkeypatch, corrupt_extraction,
):
    config = replace(config, chunk_chars=256)
    first, fake = annotator_with_fake(config)
    original = fake.generate
    failed = False

    def fail_first_extraction(model, prompt):
        nonlocal failed
        if model == config.extract_model and not failed:
            failed = True
            raise ai.AnnotationModelError(
                "Annotation returned invalid JSON.", code="invalid_json",
                usage={"input_tokens": 30, "output_tokens": 20, "total_tokens": 50},
            )
        return original(model, prompt)

    fake.generate = fail_first_extraction
    job = record(description_clean="Responsibilities:\n" + "\n".join(
        f"You will coordinate project {index} with customers and business leaders."
        for index in range(10)
    ))
    partial = first.annotate(job, use_ai=True)
    assert partial["coverage"]["chunks"] > 1
    assert partial["status"] == "partial"
    failed_chunk = partial["errors"][0]["chunk_id"]
    retained_ids = {
        claim["claim_id"] for claim in partial["claims"] if claim["chunk_id"] == failed_chunk
    }
    assert retained_ids
    if corrupt_extraction:
        partial["stages"][failed_chunk]["extraction"] = {"invalid_envelope": []}
    cache_path(first, job).unlink()
    offline_job = replace(job, annotation=deepcopy(partial))
    offline_annotator = VacancyAnnotator(config)
    with monkeypatch.context() as offline_patch:
        offline_patch.setattr(
            Path, "read_text", lambda *_args, **_kwargs: pytest.fail("Offline cache read")
        )
        offline = offline_annotator.annotate(offline_job)
    assert {claim["claim_id"] for claim in offline["claims"]} == {
        claim["claim_id"] for claim in partial["claims"]
    }
    assert all(
        claim["review_status"] == "UNREVIEWED" and claim.get("review_method") != "local"
        for claim in offline["claims"] if claim["claim_id"] in retained_ids
    )
    assert offline["status"] == "partial"
    assert offline["method"] == "two_pass"
    assert offline["coverage"] == partial["coverage"]
    assert offline["errors"] == partial["errors"]
    assert offline["issues"] == partial["issues"]
    assert offline_annotator.stats["extraction_calls"] == 0
    assert offline_annotator.stats["review_calls"] == 0
    from job_intake.annotation.evaluation import evaluate_annotation
    from job_intake.config.settings import PrefilterConfig

    result = evaluate_annotation(offline_job, config, PrefilterConfig())
    findings = {finding["code"] for finding in result["findings"]}
    assert result["integrity"] == "error"  # The original failed stage still needs review.
    assert not findings.intersection({
        "saved_claims_missing", "unattributable_saved_claim",
        "saved_claim_review_mismatch", "summary_mismatch",
    })
    evaluation = JobEvaluation(decision=FilterDecision.PASS, bridge_role=True)
    apply_annotation_constraints(offline_job, evaluation, engine())
    assert evaluation.decision == FilterDecision.REVIEW
    assert "annotation:incomplete_source_review" in evaluation.risks
    # Offline reconstruction leaves the valid paid stages reusable: only the
    # original failed extraction and its review are requested on an AI resume.
    resumed, retry = annotator_with_fake(config)
    result = resumed.annotate(offline_job, use_ai=True)
    assert result["status"] == "reviewed"
    assert result["errors"] == []
    assert [stage for stage, _ in retry.calls] == ["extract", "review"]
    assert retained_ids <= {claim["claim_id"] for claim in result["claims"]}


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
