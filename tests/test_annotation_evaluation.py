from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from job_intake.annotation import ai
from job_intake.annotation.evaluation import evaluate_annotation, evaluate_records
from job_intake.annotation.prefilter import VacancyPrefilter
from job_intake.annotation.service import VacancyAnnotator
from job_intake.annotation.text import source_hash, source_units
from job_intake.config.settings import AnnotationConfig, PrefilterConfig
from job_intake.models.job import JobRecord


class Models:
    def __init__(self, fail_review=False):
        self.fail_review = fail_review

    def generate_stage(self, stage, model, prompt):
        prefix = ai.EXTRACTION_PROMPT if stage == "extract" else ai.REVIEW_PROMPT
        payload = json.loads(prompt[len(prefix) :])
        if stage == "extract":
            unit = next(
                (unit for unit in payload["units"] if "manage analytics projects" in unit["text"]),
                None,
            )
            result = {
                "claims": []
                if unit is None
                else [
                    {
                        "kind": "project_management",
                        "value": "Manage analytics projects",
                        "unit_id": unit["unit_id"],
                        "source_snippet": unit["text"].strip(),
                        "requirement": "informational",
                        "polarity": "affirmative",
                        "is_inference": False,
                    }
                ]
            }
        elif self.fail_review:
            raise ValueError("private body with provider key must not be copied")
        else:
            result = {
                "reviews": [
                    {
                        "claim_id": claim["claim_id"],
                        "review_status": "SUPPORTED",
                        "is_inference": False,
                        "review_notes": "Checked against source.",
                    }
                    for claim in payload["claims"]
                ]
            }
        return result, {"input_tokens": 100, "output_tokens": 100, "total_tokens": 200}


@pytest.fixture
def settings(tmp_path, monkeypatch):
    monkeypatch.setenv("ANNOTATION_EVAL_TEST_KEY", "fake-only-test-key")
    return (
        AnnotationConfig(
            ai_enabled=True,
            api_key_env="ANNOTATION_EVAL_TEST_KEY",
            cache_dir=str(tmp_path / "annotations"),
        ),
        PrefilterConfig(
            enabled=True,
            api_key_env="ANNOTATION_EVAL_TEST_KEY",
            cache_dir=str(tmp_path / "prefilter"),
        ),
    )


@pytest.fixture
def job():
    return JobRecord(
        source="test",
        source_job_id="17",
        company="Example",
        title="Analytics Manager",
        original_url="https://example.test/jobs/17",
        description_clean=(
            "Responsibilities\nYou will manage analytics projects with product teams.\n"
            "Working language is English."
        ),
    )


def annotate(job, config, *, fail_review=False, use_ai=True):
    service = VacancyAnnotator(config)
    service.client = Models(fail_review)
    service.annotate(job, use_ai=use_ai)
    return job


def codes(result):
    return {row["code"] for row in result["findings"]}


def route(job, config, *, suppressed=True):
    # Build a saved route from independent stage records without running a model.
    helper = object.__new__(VacancyPrefilter)
    helper.config = config
    value = helper._result(source_hash(job))
    units = source_units(replace(job))
    unit = next(unit for unit in units if "You will" in unit["text"])
    result = {
        "decision": "clearly_irrelevant" if suppressed else "uncertain",
        "reason_code": "outside_core_role" if suppressed else "no_clear_mismatch",
        "reason": "Selling subscriptions is the primary duty.",
        "evidence": [{"unit_id": unit["unit_id"], "source_snippet": unit["text"].strip()}],
    }
    for stage in ("screen", "verify") if suppressed else ("screen",):
        value["stages"][stage] = {
            "cache_key": helper._stage_key(
                stage, source_hash(job), "outside_core_role" if stage == "verify" else None
            ),
            "source_hash": source_hash(job),
            "version": value["version"],
            "stage": stage,
            "result": deepcopy(result),
            "usage": {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30},
        }
    helper._apply_stages(value, units=units)
    return value


def test_completed_annotation_is_grounded_without_claiming_semantic_accuracy(settings, job):
    cfg, prefilter = settings
    annotate(job, cfg)
    report = evaluate_records([("job-17", job)], cfg, prefilter)
    assert report["counts"]["reviewed"] == 1
    assert report["counts"]["jobs_with_errors"] == 0
    assert report["metrics"]["review_completeness"] == 1
    assert report["metrics"]["reviewed_source_coverage"] == 1
    assert report["semantic_accuracy"] is None
    assert report["jobs"][0]["job_uid"] == "job-17"
    assert (
        report["jobs"][0]["search_focus"]["dimensions"]["project_management"]["status"]
        == "confirmed"
    )


def test_partial_reviews_preserve_quotes_but_cannot_be_complete(settings, job):
    cfg, prefilter = settings
    annotate(job, cfg, fail_review=True)
    result = evaluate_annotation(job, cfg, prefilter)
    assert result["integrity"] == "error"
    assert result["counts"]["valid_quotes"] == result["counts"]["claims"]
    assert result["coverage"]["completed_chunks"] == 0
    assert result["counts"]["expected_review_claims"] > 0
    assert {"review_incomplete", "annotation_stage_failed"} <= codes(result)
    assert "private body" not in json.dumps(result)


def test_current_suppression_keeps_partial_history_without_requiring_full_review(settings, job):
    cfg, prefilter = settings
    job.description_clean = "Responsibilities\nYou will sell subscriptions to new customers."
    annotate(job, cfg, fail_review=True)
    job.annotation["prefilter"] = route(job, prefilter)
    before = deepcopy(job)
    report = evaluate_records([("job-17", job)], cfg, prefilter)
    result = report["jobs"][0]
    assert result["integrity"] == "ok"
    assert result["annotation_status"] == result["historical_annotation_status"] == "partial"
    assert result["intentional_skip"] is True
    assert result["full_review_required"] is False
    assert result["coverage"]["completed_chunks"] == 0
    assert all(claim["review_status"] != "SUPPORTED" for claim in job.annotation["claims"])
    assert all(finding["severity"] == "info" for finding in result["findings"])
    assert result["pipeline_health"] == {
        "status": "ok", "annotation_stage_errors": 0,
        "historical_annotation_stage_errors": 1,
        "prefilter_stage_errors": 0, "prefilter_fallback": False,
    }
    assert result["diagnostics"][0]["historical"] is True
    assert report["counts"]["historical_partial"] == 1
    assert report["counts"]["full_review_required"] == 0
    assert report["pipeline_health"]["historical_annotation_stage_errors"] == 1
    assert report["counts"]["jobs_with_errors"] == 0
    assert job == before
    # All persisted paid stages remain available for a later full-review resume.
    assert job.annotation["stages"] == before.annotation["stages"]


def test_current_suppression_can_retain_failed_extraction_as_history(settings, job):
    cfg, prefilter = settings
    service = VacancyAnnotator(cfg)
    models = Models()

    def fail_extraction(stage, model, prompt):
        raise ai.AnnotationModelError("Invalid result.", code="invalid_json")

    models.generate_stage = fail_extraction
    service.client = models
    service.annotate(job, use_ai=True)
    job.annotation["prefilter"] = route(job, prefilter)
    result = evaluate_annotation(job, cfg, prefilter)
    assert result["integrity"] == "ok"
    assert {"extraction_missing", "annotation_stage_failed"} <= codes(result)
    assert result["historical_annotation_status"] == "partial"
    assert result["pipeline_health"]["historical_annotation_stage_errors"] == 1
    assert result["pipeline_health"]["annotation_stage_errors"] == 0
    assert result["counts"]["supported"] == 0


@pytest.mark.parametrize("corruption", [
    "verify_missing", "policy", "cache_key", "stage_key", "stage_version", "stage_model",
    "disabled", "quote", "unknown_stage",
])
def test_unverified_or_stale_suppression_cannot_exempt_partial_reviews(
    settings, job, corruption,
):
    cfg, prefilter = settings
    annotate(job, cfg, fail_review=True)
    saved = route(job, prefilter)
    job.annotation["prefilter"] = saved
    if corruption == "verify_missing":
        saved["stages"].pop("verify")
    elif corruption == "policy":
        saved["focus_version"] = "old-policy"
    elif corruption == "cache_key":
        saved["cache_key"] = "old-config"
    elif corruption == "stage_key":
        saved["stages"]["verify"]["cache_key"] = "old-model"
    elif corruption == "stage_version":
        saved["stages"]["verify"]["version"] = "old-stage"
    elif corruption == "stage_model":
        saved["stages"]["verify"]["usage"]["model"] = "old-model"
    elif corruption == "disabled":
        prefilter = replace(prefilter, enabled=False)
    elif corruption == "quote":
        saved["stages"]["verify"]["result"]["evidence"][0]["source_snippet"] = "invented"
    else:
        saved["stages"]["other"] = {}
    result = evaluate_annotation(job, cfg, prefilter)
    assert result["integrity"] == "error"
    assert result["intentional_skip"] is False
    assert result["full_review_required"] is True
    assert result["historical_annotation_status"] is None
    incomplete = next(row for row in result["findings"] if row["code"] == "review_incomplete")
    assert incomplete["severity"] == "error"
    assert result["pipeline_health"]["annotation_stage_errors"] == 1
    assert result["pipeline_health"]["historical_annotation_stage_errors"] == 0


@pytest.mark.parametrize("corruption, expected", [
    ("source", "annotation_source_hash_mismatch"),
    ("quote", "invalid_saved_evidence"),
    ("promote", "saved_claim_review_mismatch"),
    ("status", "annotation_status_mismatch"),
    ("coverage", "annotation_coverage_mismatch"),
    ("extraction", "invalid_extraction"),
    ("review", "invalid_review"),
])
def test_valid_suppression_does_not_hide_artifact_corruption(
    settings, job, corruption, expected,
):
    cfg, prefilter = settings
    annotate(job, cfg, fail_review=True)
    job.annotation["prefilter"] = route(job, prefilter)
    stage = next(iter(job.annotation["stages"].values()))
    if corruption == "source":
        job.annotation["source_hash"] = "wrong-source"
    elif corruption == "quote":
        job.annotation["claims"][0]["source_snippet"] = "Invented quote"
    elif corruption == "promote":
        job.annotation["claims"][0]["review_status"] = "SUPPORTED"
    elif corruption == "status":
        job.annotation["status"] = "reviewed"
    elif corruption == "coverage":
        job.annotation["coverage"]["completed_chunks"] = 1
    elif corruption == "extraction":
        stage["extraction"] = {"claims": "not-an-array"}
    else:
        stage["review"] = {"reviews": "not-an-array"}
    result = evaluate_annotation(job, cfg, prefilter)
    assert result["integrity"] == "error"
    finding = next(row for row in result["findings"] if row["code"] == expected)
    assert finding["severity"] == "error"


def test_seeded_source_claims_also_need_reviews(settings, job):
    cfg, prefilter = settings
    annotate(job, cfg)
    stage = next(iter(job.annotation["stages"].values()))
    stage["review"]["reviews"] = [
        row
        for row in stage["review"]["reviews"]
        if row["claim_id"] == stage["extraction"]["claims"][0].get("claim_id", "no-external-id")
    ]
    result = evaluate_annotation(job, cfg, prefilter)
    assert "review_incomplete" in codes(result)
    assert "annotation_status_mismatch" in codes(result)


@pytest.mark.parametrize(
    "corruption, expected",
    [
        ("quote", "invalid_saved_evidence"),
        ("id", "saved_claim_provenance_mismatch"),
        ("summary", "summary_mismatch"),
        ("inference", "unsafe_supported_claim"),
        ("duplicate", "duplicate_saved_claim"),
        ("source", "annotation_source_hash_mismatch"),
    ],
)
def test_detects_tampered_saved_facts(settings, job, corruption, expected):
    cfg, prefilter = settings
    annotate(job, cfg)
    if corruption == "quote":
        job.annotation["claims"][0]["source_snippet"] = "Invented quote"
    elif corruption == "id":
        job.annotation["claims"][0]["claim_id"] = "invented"
    elif corruption == "summary":
        job.annotation["summary"]["search_focus"]["required_confirmed"] = True
    elif corruption == "inference":
        job.annotation["claims"][0]["is_inference"] = True
    elif corruption == "duplicate":
        job.annotation["claims"].append(deepcopy(job.annotation["claims"][0]))
    else:
        job.annotation["source_hash"] = "older"
    assert expected in codes(evaluate_annotation(job, cfg, prefilter))


def test_unknown_duplicate_invalid_reviews_and_unknown_chunk_are_detected(settings, job):
    cfg, prefilter = settings
    annotate(job, cfg)
    stage = next(iter(job.annotation["stages"].values()))
    stage["review"]["reviews"].append(deepcopy(stage["review"]["reviews"][0]))
    stage["review"]["reviews"].append({"claim_id": "invented", "review_status": "SUPPORTED"})
    job.annotation["stages"]["unknown-chunk"] = {}
    result = evaluate_annotation(job, cfg, prefilter)
    assert {
        "duplicate_review",
        "unknown_review_claim",
        "unknown_annotation_chunk",
        "review_incomplete",
    } <= codes(result)


def test_model_switch_is_staleness_not_broken_historical_evidence(settings, job):
    cfg, prefilter = settings
    annotate(job, cfg)
    newer = replace(cfg, review_model="gemini-future-review")
    result = evaluate_annotation(job, newer, prefilter)
    assert result["integrity"] == "warning"
    assert "stale_annotation_model" in codes(result)
    assert result["coverage"]["completed_chunks"] == result["coverage"]["chunks"]


def test_local_annotation_and_empty_report_do_not_fabricate_review_accuracy(settings, job):
    cfg, prefilter = settings
    annotate(job, cfg, use_ai=False)
    result = evaluate_annotation(job, cfg, prefilter)
    assert result["integrity"] == "ok"
    assert "local_only" in codes(result)
    assert result["coverage"]["chunks"] == 0
    assert evaluate_records([], cfg, prefilter)["metrics"]["review_completeness"] is None
    assert (
        evaluate_records([("empty", replace(job, annotation={}))], cfg, prefilter)["counts"][
            "unannotated"
        ]
        == 1
    )


def test_two_stage_suppression_is_intentional_and_requires_exact_duty_quotes(settings, job):
    cfg, prefilter = settings
    job.description_clean = "Responsibilities\nYou will sell subscriptions to new customers."
    annotate(job, cfg, use_ai=False)
    job.annotation["prefilter"] = route(job, prefilter)
    result = evaluate_annotation(job, cfg, prefilter)
    assert result["integrity"] == "ok"
    assert result["intentional_skip"] is True
    assert result["prefilter_status"] == "suppressed"
    job.annotation["prefilter"]["stages"]["verify"]["result"]["evidence"][0]["source_snippet"] = (
        "sell"
    )
    invalid = evaluate_annotation(job, cfg, prefilter)
    assert invalid["intentional_skip"] is False
    assert "invalid_prefilter_evidence" in codes(invalid)
    assert "prefilter_verify_missing" in codes(invalid)


def test_fallback_diagnostics_are_safe_and_disk_cache_never_hides_invalid_route(
    settings, job, monkeypatch
):
    cfg, prefilter = settings
    annotate(job, cfg, fail_review=True)
    job.annotation["prefilter"] = route(job, prefilter, suppressed=False)
    job.annotation["prefilter"]["stages"] = {}
    job.annotation["prefilter"]["evidence"] = []
    job.annotation["prefilter"]["reason"] = (
        "Prefilter unavailable or invalid; full review required."
    )
    job.annotation["prefilter"]["errors"] = [
        {
            "stage": "screen",
            "code": "quote_shortened",
            "error_type": "PrefilterValidationError",
            "private": "secret body",
            "http_status": 200,
        }
    ]
    job.annotation["errors"] = [
        {
            "stage": "review",
            "code": "output_truncated",
            "finish_reason": "MAX_TOKENS",
            "chunk_id": "chunk-000001",
        }
    ]
    before = deepcopy(job)
    monkeypatch.setattr(Path, "read_text", lambda *_args, **_kwargs: pytest.fail("cache read"))
    monkeypatch.setattr(
        ai.AnnotationModelClient,
        "__init__",
        lambda *_args, **_kwargs: pytest.fail("client created"),
    )
    report = evaluate_records([("job-17", job)], cfg, prefilter)
    assert job == before
    assert report["counts"]["prefilter_fallback"] == 1
    assert report["diagnostic_counts"] == {"output_truncated": 1, "quote_shortened": 1}
    assert "secret body" not in json.dumps(report)


def test_local_reject_is_not_a_missing_model_review(settings, job):
    cfg, prefilter = settings
    annotate(job, cfg, use_ai=False)
    job.annotation["prefilter"] = {
        "routing": "local_reject",
        "decision": "clearly_irrelevant",
        "reason_code": "hard_blocker",
        "source_hash": source_hash(job),
        "blockers": ["closed"],
    }
    result = evaluate_annotation(job, cfg, prefilter)
    assert result["intentional_skip"] is True
    assert result["integrity"] == "ok"
    assert "review_incomplete" not in codes(result)


def test_evaluator_survives_malformed_saved_claim_fields(settings, job):
    cfg, prefilter = settings
    annotate(job, cfg)
    job.annotation["claims"][0].update(chunk_id={}, review_status=[], claim_id=[])
    result = evaluate_annotation(job, cfg, prefilter)
    assert result["integrity"] == "error"
    assert "invalid_saved_evidence" in codes(result)


def test_runtime_ai_switch_does_not_change_grounding_policy(settings, job):
    cfg, prefilter = settings
    annotate(job, cfg)
    from_yaml = replace(cfg, ai_enabled=False)
    result = evaluate_annotation(job, from_yaml, prefilter)
    assert result["integrity"] == "ok"
    assert "stale_annotation_config" not in codes(result)


def test_partial_failure_raw_drafts_are_safe_unreviewed_state(settings, job):
    cfg, prefilter = settings
    annotate(job, cfg, fail_review=True)
    assert any(row["review_status"] == "UNREVIEWED" for row in job.annotation["claims"])
    result = evaluate_annotation(job, cfg, prefilter)
    assert "review_incomplete" in codes(result)
    assert "saved_claim_review_mismatch" not in codes(result)
    assert "summary_mismatch" not in codes(result)


def test_review_added_inference_is_a_valid_downgrade_not_provenance_corruption(settings, job):
    cfg, prefilter = settings
    models = Models()
    original = models.generate_stage

    def mark_inference(stage, model, prompt):
        payload, usage = original(stage, model, prompt)
        if stage == "review":
            for row in payload["reviews"]:
                row["is_inference"] = True
        return payload, usage

    models.generate_stage = mark_inference
    service = VacancyAnnotator(cfg)
    service.client = models
    service.annotate(job, use_ai=True)
    assert all(claim["is_inference"] for claim in job.annotation["claims"])
    assert all(claim["review_status"] == "WEAKLY_SUPPORTED" for claim in job.annotation["claims"])
    # Warnings were calculated from original non-inference drafts. Review's
    # inference flag is checked against its saved verdict separately.
    assert all(claim["evidence_warnings"] == [] for claim in job.annotation["claims"])
    result = evaluate_annotation(job, cfg, prefilter)
    assert result["integrity"] == "ok"
    assert "saved_claim_provenance_mismatch" not in codes(result)
    job.annotation["claims"][0].update(is_inference=False, review_status="SUPPORTED")
    tampered = evaluate_annotation(job, cfg, prefilter)
    assert tampered["integrity"] == "error"
    assert "saved_claim_review_mismatch" in codes(tampered)


@pytest.mark.parametrize("change", ["add", "remove"])
def test_saved_warning_tampering_is_not_hidden_by_review_inference_check(settings, job, change):
    cfg, prefilter = settings
    models = Models()
    original = models.generate_stage

    def inferred_extraction(stage, model, prompt):
        payload, usage = original(stage, model, prompt)
        if stage == "extract" and change == "remove":
            for claim in payload["claims"]:
                claim["is_inference"] = True
        return payload, usage

    models.generate_stage = inferred_extraction
    service = VacancyAnnotator(cfg)
    service.client = models
    service.annotate(job, use_ai=True)
    assert evaluate_annotation(job, cfg, prefilter)["integrity"] == "ok"
    claim = next(row for row in job.annotation["claims"] if row["kind"] == "project_management")
    if change == "remove":
        assert claim["evidence_warnings"] == ["inference_requires_verification"]
    claim["evidence_warnings"] = ["invented_warning"] if change == "add" else []
    result = evaluate_annotation(job, cfg, prefilter)
    assert result["integrity"] == "error"
    assert "saved_claim_provenance_mismatch" in codes(result)


def test_failed_extraction_keeps_local_source_seeds_attributable_and_unreviewed(settings, job):
    cfg, prefilter = settings
    cfg = replace(cfg, chunk_chars=256)
    job.description_clean = "Responsibilities\n" + "\n".join(
        f"You will manage analytics projects with product teams in initiative {index}."
        for index in range(10)
    )
    models = Models()
    original = models.generate_stage
    extraction_failed = False

    def fail_first_extraction(stage, model, prompt):
        nonlocal extraction_failed
        if stage == "extract" and not extraction_failed:
            extraction_failed = True
            raise ai.AnnotationModelError("Invalid result.", code="invalid_json")
        return original(stage, model, prompt)

    models.generate_stage = fail_first_extraction
    service = VacancyAnnotator(cfg)
    service.client = models
    service.annotate(job, use_ai=True)
    assert job.annotation["coverage"]["chunks"] > 1
    unreviewed = [
        claim for claim in job.annotation["claims"] if claim["review_status"] == "UNREVIEWED"
    ]
    assert unreviewed
    result = evaluate_annotation(job, cfg, prefilter)
    assert result["integrity"] == "error"
    assert "extraction_missing" in codes(result)
    assert "unattributable_saved_claim" not in codes(result)
    assert "saved_claim_review_mismatch" not in codes(result)
    assert "summary_mismatch" not in codes(result)
    assert result["counts"]["expected_review_claims"] == len(job.annotation["claims"])
    assert result["counts"]["reviewed_claims"] < result["counts"]["expected_review_claims"]
    assert result["pipeline_health"]["annotation_stage_errors"] == 1
    # A failed stage cannot justify arbitrary new claims or promoted source seeds.
    unreviewed[0]["review_status"] = "SUPPORTED"
    tampered = evaluate_annotation(job, cfg, prefilter)
    assert "saved_claim_review_mismatch" in codes(tampered)


def failed_prefilter(job, config):
    helper = object.__new__(VacancyPrefilter)
    helper.config = config
    value = helper._result(source_hash(job))
    value["reason"] = "Prefilter unavailable or invalid; full review required."
    value["errors"] = [{
        "stage": "screen", "code": "quote_shortened", "error_type": "PrefilterValidationError",
    }]
    return value


def test_prefilter_failure_with_completed_annotation_is_warning_and_pipeline_failure(settings, job):
    cfg, prefilter = settings
    annotate(job, cfg)
    job.annotation["prefilter"] = failed_prefilter(job, prefilter)
    report = evaluate_records([("job-17", job)], cfg, prefilter)
    result = report["jobs"][0]
    assert result["integrity"] == "warning"
    assert result["annotation_status"] == "reviewed"
    assert result["prefilter_status"] == "fallback"
    assert result["coverage"]["completed_chunks"] == result["coverage"]["chunks"]
    assert result["pipeline_health"] == {
        "status": "degraded", "annotation_stage_errors": 0,
        "historical_annotation_stage_errors": 0,
        "prefilter_stage_errors": 1, "prefilter_fallback": True,
    }
    assert report["pipeline_health"] == {
        "jobs_degraded": 1, "annotation_stage_errors": 0,
        "historical_annotation_stage_errors": 0,
        "prefilter_stage_errors": 1, "prefilter_fallback": 1,
    }
    assert report["counts"]["jobs_with_errors"] == 0
    assert report["counts"]["jobs_with_warnings"] == 1
    assert report["diagnostic_counts"] == {"quote_shortened": 1}


def test_safe_verify_failure_warns_but_failed_prefilter_cannot_suppress(settings, job):
    cfg, prefilter = settings
    job.description_clean = "Responsibilities\nYou will sell subscriptions to new customers."
    annotate(job, cfg)
    negative = route(job, prefilter)
    fallback = failed_prefilter(job, prefilter)
    fallback["stages"]["screen"] = negative["stages"]["screen"]
    fallback["errors"][0]["stage"] = "verify"
    job.annotation["prefilter"] = fallback
    result = evaluate_annotation(job, cfg, prefilter)
    assert result["integrity"] == "warning"
    assert "prefilter_verify_missing" in codes(result)
    fallback["routing"] = "suppressed"
    unsafe = evaluate_annotation(job, cfg, prefilter)
    assert unsafe["integrity"] == "error"
    assert unsafe["intentional_skip"] is False


def test_old_saved_annotation_never_claims_current_source_review_is_complete(settings, job):
    cfg, prefilter = settings
    annotate(job, cfg, use_ai=False)
    job.description_clean += "\nYou will coordinate a new roadmap with finance leaders."
    result = evaluate_annotation(job, cfg, prefilter)
    assert result["annotation_status"] == "local"
    assert result["integrity"] == "error"
    assert "annotation_source_hash_mismatch" in codes(result)
    assert "source_units_mismatch" in codes(result)
    assert result["coverage"]["completed_chunks"] == 0


def mixed_infrastructure_job(job):
    job.description_clean = (
        "Responsibilities\n"
        "You will build compute and networking primitives.\n"
        "You own the problem statement and requirements behind every platform bet.\n"
        "You define how each launch is measured before it ships.\n"
        "The metric, the threshold and the read afterwards are yours: whether a launch "
        "moved enterprise deal velocity, project economics, or platform reliability.\n"
        "You keep engineering, design and leadership aligned."
    )
    return job


def test_current_mixed_duty_guard_requires_full_review_without_promoting_focus(settings, job):
    cfg, prefilter = settings
    mixed_infrastructure_job(job)
    annotate(job, cfg)
    saved = route(job, prefilter)
    assert saved["routing"] == "full_review"
    assert saved["decision"] == "uncertain"
    assert saved["routing_guard"] == "mixed_decision_analytics_scope"
    assert saved["counterevidence"]
    units = {unit["unit_id"]: unit["text"] for unit in source_units(job)}
    assert all(item["source_snippet"] == units[item["unit_id"]]
               for item in saved["counterevidence"])
    job.annotation["prefilter"] = saved
    before = deepcopy(job)
    result = evaluate_annotation(job, cfg, prefilter)
    assert result["integrity"] == "ok"
    assert result["prefilter_status"] == "full_review"
    assert result["intentional_skip"] is False
    assert result["full_review_required"] is True
    assert result["historical_annotation_status"] is None
    assert result["search_focus"]["required_confirmed"] is False
    assert job == before


@pytest.mark.parametrize("corruption", [
    "guard_missing", "guard_code", "counterevidence_missing", "counterevidence_quote",
    "counterevidence_unit", "positive_promotion",
])
def test_mixed_guard_metadata_must_match_rebuilt_current_duty_evidence(settings, job, corruption):
    cfg, prefilter = settings
    mixed_infrastructure_job(job)
    annotate(job, cfg)
    saved = route(job, prefilter)
    assert saved["routing_guard"] == "mixed_decision_analytics_scope"
    if corruption == "guard_missing":
        saved.pop("routing_guard")
    elif corruption == "guard_code":
        saved["routing_guard"] = "invented_guard"
    elif corruption == "counterevidence_missing":
        saved.pop("counterevidence")
    elif corruption == "counterevidence_quote":
        saved["counterevidence"][0]["source_snippet"] = "Invented positive evidence."
    elif corruption == "counterevidence_unit":
        saved["counterevidence"][0]["unit_id"] = "description_clean:999999"
    else:
        saved["decision"] = "likely_relevant"
    job.annotation["prefilter"] = saved
    result = evaluate_annotation(job, cfg, prefilter)
    assert result["integrity"] == "error"
    assert "prefilter_routing_mismatch" in codes(result)
    assert result["intentional_skip"] is False
    assert result["full_review_required"] is True


def test_unsafe_old_suppression_of_mixed_source_cannot_exempt_full_review(settings, job):
    cfg, prefilter = settings
    mixed_infrastructure_job(job)
    annotate(job, cfg, fail_review=True)
    saved = route(job, prefilter)
    # Older routing ignored the contradictory duties despite grounded model stages.
    saved.update(deepcopy(saved["stages"]["verify"]["result"]), routing="suppressed")
    for name in ("routing_policy_version", "routing_guard", "counterevidence"):
        saved.pop(name)
    job.annotation["prefilter"] = saved
    before = deepcopy(job)
    result = evaluate_annotation(job, cfg, prefilter)
    assert result["integrity"] == "error"
    assert {"stale_prefilter_routing_policy_version", "prefilter_routing_mismatch",
            "review_incomplete"} <= codes(result)
    assert result["intentional_skip"] is False
    assert result["full_review_required"] is True
    assert result["historical_annotation_status"] is None
    assert job == before


def test_stale_guard_revision_never_exempts_suppressed_annotation(settings, job):
    cfg, prefilter = settings
    job.description_clean = "Responsibilities\nYou will sell subscriptions to new customers."
    annotate(job, cfg)
    saved = route(job, prefilter)
    saved["routing_policy_version"] = "old-routing-policy"
    job.annotation["prefilter"] = saved
    result = evaluate_annotation(job, cfg, prefilter)
    assert result["integrity"] == "error"
    assert {"stale_prefilter_routing_policy_version", "suppression_not_current"} <= codes(result)
    warning = next(row for row in result["findings"]
                   if row["code"] == "stale_prefilter_routing_policy_version")
    assert warning["severity"] == "warning"
    assert result["intentional_skip"] is False
    assert result["full_review_required"] is True


def test_missing_legacy_empty_guard_fields_do_not_invent_counterevidence(settings, job):
    cfg, prefilter = settings
    job.description_clean = "Responsibilities\nYou will sell subscriptions to new customers."
    annotate(job, cfg)
    saved = route(job, prefilter)
    assert saved["routing_guard"] is None
    assert saved["counterevidence"] == []
    saved.pop("routing_guard")
    saved.pop("counterevidence")
    job.annotation["prefilter"] = saved
    result = evaluate_annotation(job, cfg, prefilter)
    assert result["integrity"] == "ok"
    assert result["intentional_skip"] is True


def test_failed_prefilter_cannot_claim_mixed_guard_without_valid_stages(settings, job):
    cfg, prefilter = settings
    annotate(job, cfg)
    saved = failed_prefilter(job, prefilter)
    saved["routing_guard"] = "mixed_decision_analytics_scope"
    saved["counterevidence"] = [{"unit_id": "fake", "source_snippet": "fake"}]
    job.annotation["prefilter"] = saved
    result = evaluate_annotation(job, cfg, prefilter)
    assert result["integrity"] == "error"
    assert "prefilter_routing_mismatch" in codes(result)
    assert result["intentional_skip"] is False
    assert result["full_review_required"] is True


def test_whitespace_source_units_are_preserved_but_not_used_as_claim_units(settings, job):
    cfg, prefilter = settings
    job.description_clean += "\n\n"
    annotate(job, cfg)
    assert any(not unit["text"].strip() for unit in job.annotation["units"])
    result = evaluate_annotation(job, cfg, prefilter)
    assert result["integrity"] == "ok"
    assert result["coverage"]["reviewed_source_chars"] == result["coverage"]["source_chars"]


def test_rebuilt_completed_cache_notes_are_warnings_not_stage_failure(settings, job):
    cfg, prefilter = settings
    annotate(job, cfg)
    job.annotation["issues"] = ["Invalid review cache ignored and rebuilt."]
    result = evaluate_annotation(job, cfg, prefilter)
    assert result["integrity"] == "warning"
    assert "historical_annotation_issues" in codes(result)
    assert "annotation_stage_failed" not in codes(result)


@pytest.mark.parametrize("notice", [
    "chunk-000001: invalid review cache rebuilt",
    "chunk-000001: invalid extraction cache rebuilt",
    "chunk-000001: invalid review batch checkpoint rebuilt",
    "chunk-000001: invalid checkpoint ignored",
    "Invalid cache ignored; extraction will be rebuilt.",
])
def test_exact_cache_recovery_notice_is_info_only_after_complete_validated_review(
    settings, job, notice,
):
    cfg, prefilter = settings
    annotate(job, cfg)
    job.annotation["issues"] = [notice]
    assert job.annotation["errors"] == []
    before = deepcopy(job)
    result = evaluate_annotation(job, cfg, prefilter)
    assert result["integrity"] == "ok"
    assert "annotation_cache_rebuilt" in codes(result)
    assert {"historical_annotation_issues", "failure_cause_unavailable",
            "annotation_stage_failed"}.isdisjoint(codes(result))
    finding = next(row for row in result["findings"] if row["code"] == "annotation_cache_rebuilt")
    assert finding["severity"] == "info"
    assert result["pipeline_health"]["status"] == "ok"
    assert result["coverage"]["completed_chunks"] == result["coverage"]["chunks"]
    assert job == before


@pytest.mark.parametrize("additional", [
    "Unknown issue after cache recovery.",
    "chunk-999999: invalid review cache rebuilt",
    "chunk-000001: invalid review cache rebuilt but still incomplete",
    {"message": "chunk-000001: invalid review cache rebuilt"},
])
def test_cache_recovery_allowlist_does_not_hide_unknown_or_unattributable_notices(
    settings, job, additional,
):
    cfg, prefilter = settings
    annotate(job, cfg)
    job.annotation["issues"] = ["chunk-000001: invalid review cache rebuilt", additional]
    result = evaluate_annotation(job, cfg, prefilter)
    assert result["integrity"] == "warning"
    assert "annotation_cache_rebuilt" not in codes(result)
    assert {"historical_annotation_issues", "failure_cause_unavailable"} <= codes(result)
    assert result["pipeline_health"]["status"] == "degraded"


def test_cache_recovery_notice_does_not_hide_current_error_diagnostics(settings, job):
    cfg, prefilter = settings
    annotate(job, cfg)
    job.annotation["issues"] = ["chunk-000001: invalid review cache rebuilt"]
    job.annotation["errors"] = [{
        "stage": "review", "chunk_id": "chunk-000001", "code": "http_error",
        "error_type": "AnnotationModelError", "http_status": 400,
    }]
    result = evaluate_annotation(job, cfg, prefilter)
    assert result["integrity"] == "warning"
    assert "historical_annotation_issues" in codes(result)
    assert "annotation_cache_rebuilt" not in codes(result)
    assert result["pipeline_health"]["status"] == "degraded"
    assert result["pipeline_health"]["annotation_stage_errors"] == 1


def test_cache_recovery_notice_does_not_hide_incomplete_review(settings, job):
    cfg, prefilter = settings
    annotate(job, cfg, fail_review=True)
    job.annotation["issues"] = ["chunk-000001: invalid review cache rebuilt"]
    job.annotation["errors"] = []
    result = evaluate_annotation(job, cfg, prefilter)
    assert result["integrity"] == "error"
    assert {"review_incomplete", "annotation_stage_failed"} <= codes(result)
    assert "annotation_cache_rebuilt" not in codes(result)
    assert result["pipeline_health"]["status"] == "degraded"


def test_cache_recovery_notice_does_not_hide_invalid_current_quotes(settings, job):
    cfg, prefilter = settings
    annotate(job, cfg)
    job.annotation["issues"] = ["chunk-000001: invalid review cache rebuilt"]
    job.annotation["claims"][0]["source_snippet"] = "Invented positive evidence."
    result = evaluate_annotation(job, cfg, prefilter)
    assert result["integrity"] == "error"
    assert "invalid_saved_evidence" in codes(result)
    assert "annotation_cache_rebuilt" not in codes(result)
    assert result["pipeline_health"]["status"] == "degraded"


def test_cache_recovery_notice_retains_other_staleness_warnings(settings, job):
    cfg, prefilter = settings
    annotate(job, cfg)
    job.annotation["issues"] = ["chunk-000001: invalid review cache rebuilt"]
    job.annotation["version"] = "older-annotation-format"
    result = evaluate_annotation(job, cfg, prefilter)
    assert result["integrity"] == "warning"
    assert {"annotation_cache_rebuilt", "stale_annotation_version"} <= codes(result)
    assert "historical_annotation_issues" not in codes(result)


def test_short_rejected_local_seed_is_not_invalid_model_output(settings, job):
    cfg, prefilter = settings
    job.description_clean = "Your responsibilities\nYou will manage analytics projects."
    annotate(job, cfg)
    result = evaluate_annotation(job, cfg, prefilter)
    assert "invalid_draft" not in codes(result)
    assert result["integrity"] == "ok"


@pytest.mark.parametrize("routing", [[], {}])
def test_malformed_prefilter_routing_produces_findings(settings, job, routing):
    cfg, prefilter = settings
    annotate(job, cfg, use_ai=False)
    job.annotation["prefilter"] = route(job, prefilter, suppressed=False)
    job.annotation["prefilter"]["routing"] = routing
    result = evaluate_annotation(job, cfg, prefilter)
    assert result["integrity"] == "error"
    assert "invalid_prefilter_routing" in codes(result)
