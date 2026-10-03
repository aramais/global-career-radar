from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from job_intake.annotation.ai import EXTRACTION_PROMPT, REVIEW_PROMPT, AnnotationModelClient
from job_intake.annotation.jev import JEV_CRITERIA
from job_intake.annotation.service import VacancyAnnotator
from job_intake.config.settings import AnnotationConfig
from job_intake.models.job import JobRecord


def record(**changes):
    return JobRecord(
        source="test", title="Product Manager", company="Example",
        original_url="https://example.com/17",
        description_clean="Working language is English. Applicants must reside in Brazil.",
        **changes,
    )


def configure(tmp_path, **changes):
    return AnnotationConfig(
        ai_enabled=True, extract_provider="openai", extract_model="gpt-6-luna",
        review_provider="jev", review_model="jev-1.13.0", cache_dir=str(tmp_path), **changes,
    )


def fake_generation(calls):
    def generate(self, model, prompt):
        calls.append((self.config.provider, model))
        if prompt.startswith(EXTRACTION_PROMPT):
            return {"claims": []}, {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12}
        payload = json.loads(prompt[len(REVIEW_PROMPT):])
        return {"reviews": [{
            "claim_id": claim["claim_id"], "review_status": "SUPPORTED",
            "review_notes": "fixture", "is_inference": False,
        } for claim in payload["claims"]]}, {}
    return generate


def jev_post(calls, probability=0.99, confidence=0.99):
    def post(url, **kwargs):
        calls.append((url, kwargs["json"]))
        answers = {}
        for key in kwargs["json"]["questions"]:
            distribution = dict.fromkeys(JEV_CRITERIA, (1 - probability) / 4)
            distribution["SUPPORTED"] = probability
            answers[key] = {"type": "choice", "choice": "SUPPORTED",
                            "probabilities": distribution, "confidence": confidence}
        return SimpleNamespace(status_code=200, text=json.dumps({
            "model": "jev-1.13.0", "answers": answers,
            "usage": {"input_tokens": 50, "output_tokens": 5},
        }))
    return post


@pytest.fixture
def keys(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai")
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-typesafe")
    monkeypatch.setenv("MISTRAL_API_KEY", "test-mistral")


def test_mixed_extractor_and_jev_review_seeds_quotes_and_persists_provenance(
    tmp_path, monkeypatch, keys
):
    generation, reviews = [], []
    monkeypatch.setattr(AnnotationModelClient, "generate", fake_generation(generation))
    monkeypatch.setattr("job_intake.annotation.jev.requests.post", jev_post(reviews))
    config = configure(tmp_path)
    first = VacancyAnnotator(config)
    job = record()
    result = first.annotate(job, use_ai=True)
    assert result["status"] == "reviewed"
    assert generation == [("openai", "gpt-6-luna")]
    assert first.stats["review_calls"] == len(reviews) == 1
    assert result["extract_provider"] == "openai"
    assert result["review_provider"] == "jev"
    assert all(claim["review_adapter"] == "jev" for claim in result["claims"])
    assert all(claim["source_snippet"] for claim in result["claims"])
    assert "test-typesafe" not in json.dumps(result)
    calls_before = len(reviews), len(generation)
    repeated = VacancyAnnotator(config).annotate(record(), use_ai=True)
    assert repeated["claims"] == result["claims"]
    assert (len(reviews), len(generation)) == calls_before


def test_missing_review_key_preserves_drafts_and_partial_status(tmp_path, monkeypatch, keys):
    monkeypatch.delenv("TYPESAFE_API_KEY")
    calls = []
    monkeypatch.setattr(AnnotationModelClient, "generate", fake_generation(calls))
    result = VacancyAnnotator(configure(tmp_path)).annotate(record(), use_ai=True)
    assert result["method"] == "two_pass"
    assert result["status"] == "partial"
    assert calls == [("openai", "gpt-6-luna")]
    assert not result["summary"]["knowns"]
    assert all(claim["review_status"] != "SUPPORTED" for claim in result["claims"])


def test_review_switch_reuses_extraction_from_disk_and_discards_old_review(
    tmp_path, monkeypatch, keys
):
    generation, reviews = [], []
    monkeypatch.setattr(AnnotationModelClient, "generate", fake_generation(generation))
    monkeypatch.setattr("job_intake.annotation.jev.requests.post", jev_post(reviews))
    config = configure(tmp_path)
    VacancyAnnotator(config).annotate(record(), use_ai=True)
    generation.clear()
    changed = replace(config, review_provider="mistral", review_model="mistral-small-2603")
    result = VacancyAnnotator(changed).annotate(record(), use_ai=True)
    assert generation == [("mistral", "mistral-small-2603")]
    assert result["status"] == "reviewed"
    assert all(claim.get("review_adapter") != "jev" for claim in result["claims"])


def test_review_switch_reuses_matching_persisted_extraction_without_files(
    tmp_path, monkeypatch, keys
):
    generation, reviews = [], []
    monkeypatch.setattr(AnnotationModelClient, "generate", fake_generation(generation))
    monkeypatch.setattr("job_intake.annotation.jev.requests.post", jev_post(reviews))
    config = configure(tmp_path)
    saved = VacancyAnnotator(config).annotate(record(), use_ai=True)
    for path in Path(tmp_path).rglob("*.json"):
        path.unlink()
    generation.clear()
    changed = replace(config, review_provider="mistral", review_model="mistral-small-2603")
    result = VacancyAnnotator(changed).annotate(record(annotation=saved), use_ai=True)
    assert generation == [("mistral", "mistral-small-2603")]
    assert result["status"] == "reviewed"


def test_jev_replay_rebuilds_status_from_probabilities_offline(tmp_path, monkeypatch, keys):
    generation, reviews = [], []
    monkeypatch.setattr(AnnotationModelClient, "generate", fake_generation(generation))
    monkeypatch.setattr(
        "job_intake.annotation.jev.requests.post", jev_post(reviews, probability=0.8)
    )
    config = configure(tmp_path)
    result = VacancyAnnotator(config).annotate(record(), use_ai=True)
    assert result["status"] == "reviewed"  # Review complete, decisions remain uncertain.
    assert not result["summary"]["knowns"]
    tampered = deepcopy(result)
    for stage in tampered["stages"].values():
        for row in stage["review"]["reviews"]:
            row["review_status"] = "SUPPORTED"
    offline = VacancyAnnotator(config).annotate(record(annotation=tampered))
    assert not offline["summary"]["knowns"]
    assert all(claim["review_status"] == "NEEDS_VERIFICATION" for claim in offline["claims"])


def test_same_model_string_at_distinct_providers_routes_by_stage(tmp_path, monkeypatch, keys):
    generation = []
    monkeypatch.setattr(AnnotationModelClient, "generate", fake_generation(generation))
    config = AnnotationConfig(
        ai_enabled=True, extract_provider="openai", extract_model="different-vendor-model",
        review_provider="mistral", review_model="different-vendor-model", cache_dir=str(tmp_path),
    )
    result = VacancyAnnotator(config).annotate(record(), use_ai=True)
    assert generation == [("openai", "different-vendor-model"),
                          ("mistral", "different-vendor-model")]
    assert result["status"] == "reviewed"
    offline = VacancyAnnotator(config).annotate(record(annotation=result))
    assert offline["status"] == "reviewed"


def test_jev_threshold_change_reuses_extraction_but_runs_review_again(
    tmp_path, monkeypatch, keys
):
    generation, reviews = [], []
    monkeypatch.setattr(AnnotationModelClient, "generate", fake_generation(generation))
    monkeypatch.setattr(
        "job_intake.annotation.jev.requests.post", jev_post(reviews, probability=0.96)
    )
    config = configure(tmp_path)
    first = VacancyAnnotator(config).annotate(record(), use_ai=True)
    assert first["summary"]["knowns"]
    generation.clear()
    second = VacancyAnnotator(replace(config, jev_min_probability=0.98)).annotate(
        record(), use_ai=True
    )
    assert generation == []
    assert len(reviews) == 2
    assert not second["summary"]["knowns"]


def test_malformed_saved_jev_review_is_partial_offline(tmp_path, monkeypatch, keys):
    generation, reviews = [], []
    monkeypatch.setattr(AnnotationModelClient, "generate", fake_generation(generation))
    monkeypatch.setattr("job_intake.annotation.jev.requests.post", jev_post(reviews))
    config = configure(tmp_path)
    result = VacancyAnnotator(config).annotate(record(), use_ai=True)
    for stage in result["stages"].values():
        stage["review"] = []
    offline = VacancyAnnotator(config).annotate(record(annotation=result))
    assert offline["status"] == "partial"
    assert offline["issues"]


def test_same_provider_model_at_distinct_endpoints_replays_offline(
    tmp_path, monkeypatch, keys
):
    calls = []
    monkeypatch.setattr(AnnotationModelClient, "generate", fake_generation(calls))
    config = AnnotationConfig(
        ai_enabled=True, provider="openai", api_key_env="OPENAI_API_KEY",
        extract_model="gpt-6-luna", review_model="gpt-6-luna",
        review_base_url="https://configured-gateway.example/v1", cache_dir=str(tmp_path),
    )
    result = VacancyAnnotator(config).annotate(record(), use_ai=True)
    assert result["status"] == "reviewed"
    offline = VacancyAnnotator(config).annotate(record(annotation=result))
    assert offline["status"] == "reviewed"
