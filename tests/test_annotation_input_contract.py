from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from job_intake.annotation import ai
from job_intake.annotation.input_contract import annotation_input_hash
from job_intake.annotation.prefilter import VacancyPrefilter
from job_intake.annotation.service import VacancyAnnotator
from job_intake.annotation.text import chunk_source, source_hash, source_identity, source_units
from job_intake.config.settings import AnnotationConfig, PrefilterConfig
from job_intake.models.job import JobRecord


def record(**changes):
    fields = {
        "source": "fixture", "source_job_id": "17", "company": "Example",
        "title": "Analytics Lead", "original_url": "https://example.test/jobs/17",
        "description_clean": "Responsibilities:\nYou will lead analytics projects with teams.",
        "source_metadata": {"description_complete": True, "listing_observed_at": "before"},
    }
    fields.update(changes)
    return JobRecord(**fields)


class FakeModels:
    def __init__(self):
        self.calls = []

    def generate(self, model, prompt):
        extraction = prompt.startswith(ai.EXTRACTION_PROMPT)
        prefix = ai.EXTRACTION_PROMPT if extraction else ai.REVIEW_PROMPT
        payload = json.loads(prompt[len(prefix):])
        self.calls.append("extract" if extraction else "review")
        if extraction:
            result = {"claims": []}
        else:
            result = {"reviews": [{
                "claim_id": row["claim_id"], "review_status": "SUPPORTED",
                "review_notes": "Exact evidence checked.", "is_inference": False,
            } for row in payload["claims"]]}
        return result, {"input_tokens": 11, "output_tokens": 7, "total_tokens": 18}


def annotator(config):
    result = VacancyAnnotator(config)
    result.client = FakeModels()
    return result


@pytest.fixture
def config(tmp_path, monkeypatch):
    monkeypatch.setenv("CACHE_CONTRACT_FIXTURE_KEY", "fixture-key")
    return AnnotationConfig(ai_enabled=True, api_key_env="CACHE_CONTRACT_FIXTURE_KEY",
                            cache_dir=str(tmp_path / "annotation"))


@pytest.mark.parametrize("name,value", [
    ("listing_observed_at", "later"), ("collection_id", "next"),
    ("detail_error", "temporary"), ("updated_at", "later"),
    ("published_label", "today"),
])
def test_observation_rebind_preserves_paid_review_and_usage(config, name, value):
    original = record()
    first = annotator(config)
    paid = deepcopy(first.annotate(original, use_ai=True))
    changed = record(source_metadata={**original.source_metadata, name: value}, annotation=paid)
    assert source_hash(changed) != source_hash(original)
    assert annotation_input_hash(changed) == annotation_input_hash(original)
    second = annotator(replace(config, request_timeout=123, max_retries=0, retry_backoff=4))
    reused = second.annotate(changed, use_ai=True)
    assert second.client.calls == []
    assert reused["status"] == "reviewed"
    assert reused["source_hash"] == source_hash(changed)
    assert reused["claims"] == paid["claims"]
    assert reused["usage"] == paid["usage"]
    assert reused["cache_reuse"][0]["cache_key"] == paid["cache_key"]
    assert len(reused["cache_reuse"][0]["usage_refs"]) == len(paid["usage"])
    # Applying the same rebind twice neither duplicates usage nor origins.
    again = second.annotate(changed, use_ai=True)
    assert again["usage"] == reused["usage"]
    assert again["cache_reuse"] == reused["cache_reuse"]


def test_disk_only_review_survives_observation_change_and_timeout(config):
    first = annotator(config)
    old = record()
    paid = first.annotate(old, use_ai=True)
    fresh = record(source_metadata={**old.source_metadata, "collection_id": "second"})
    retry = annotator(replace(config, request_timeout=121))
    reused = retry.annotate(fresh, use_ai=True)
    assert retry.client.calls == []
    assert reused["claims"] == paid["claims"]
    assert reused["source_hash"] == source_hash(fresh)
    assert reused["cache_reuse"][0]["source_hash"] == source_hash(old)


@pytest.mark.parametrize("metadata", [
    {"working_language": "Portuguese"},
    {"applicant_location_requirements": ["United States"]},
    {"description_complete": False}, {"description_truncated": True}, {"truncated": True},
])
def test_significant_source_facts_change_paid_input(config, metadata):
    first = annotator(config)
    old = record()
    first.annotate(old, use_ai=True)
    fresh = record(source_metadata={**old.source_metadata, **metadata},
                   annotation=deepcopy(old.annotation))
    assert annotation_input_hash(fresh) != annotation_input_hash(old)
    retry = annotator(config)
    retry.annotate(fresh, use_ai=True)
    assert "extract" in retry.client.calls


def test_corrected_sections_cannot_inherit_review_from_identical_public_hash(config):
    old = record()
    paid = annotator(config).annotate(old, use_ai=True)
    corrupted = deepcopy(paid)
    corrupted["units"][-1]["section"] = "company"
    fresh = record(annotation=corrupted)
    retry = annotator(config)
    # Remove disk caches so the stale persisted units are the only candidate.
    retry.config = replace(config, cache_dir=str(Path(config.cache_dir) / "empty"))
    result = retry.annotate(fresh)
    assert result["method"] == "deterministic"
    assert result["claims"] != corrupted["claims"] or result["status"] == "local"
    assert retry.client.calls == []


def test_legacy_review_without_units_cannot_reuse_after_parser_change(config, monkeypatch):
    from job_intake.annotation import input_contract, service

    old = record()
    paid = deepcopy(annotator(config).annotate(old, use_ai=True))
    snapshot_hash = source_hash(old)
    paid.pop("units")
    paid.pop("input_contract")
    paid.pop("annotation_input_hash")
    current_units = source_units(old)
    current_units[-1]["section"] = "company"
    monkeypatch.setattr(service, "source_units", lambda _record: deepcopy(current_units))
    monkeypatch.setattr(input_contract, "source_units", lambda _record: deepcopy(current_units))
    fresh = record(annotation=paid)
    assert source_hash(fresh) == snapshot_hash
    retry = annotator(config)
    assert retry._saved_review(paid, fresh) is None
    result = retry.annotate(fresh)
    assert result["status"] != "reviewed"
    assert not any(claim.get("review_method") == "model" for claim in result["claims"])
    assert retry.client.calls == []


def test_legacy_file_rebind_requires_exact_units_and_known_legacy_configuration(config):
    initial = record()
    first = annotator(config)
    paid = deepcopy(first.annotate(initial, use_ai=True))
    old_hash = "legacy-text-version-source-hash"
    legacy_key = first._legacy_cache_key(old_hash)
    paid.update(source_hash=old_hash, cache_key=legacy_key)
    paid.pop("input_contract")
    paid.pop("annotation_input_hash")
    for chunk in chunk_source(source_units(initial), config.chunk_chars):
        paid["stages"][chunk["chunk_id"]]["extraction_key"] = first._legacy_extraction_key(
            old_hash, chunk,
        )
    directory = Path(config.cache_dir) / source_identity(initial)
    for path in directory.glob("*.json"):
        path.unlink()
    legacy_path = directory / (legacy_key + ".json")
    original_bytes = json.dumps(paid).encode()
    legacy_path.write_bytes(original_bytes)
    fresh = record()
    retry = annotator(config)
    recovered = retry.annotate(fresh, use_ai=True)
    assert retry.client.calls == []
    assert recovered["status"] == "reviewed"
    assert recovered["cache_reuse"][0]["cache_key"] == legacy_key
    assert recovered["usage"] == paid["usage"]
    assert legacy_path.read_bytes() == original_bytes
    assert recovered["cache_key"] == retry._cache_key(fresh)


def test_repeated_observation_rebinds_keep_original_legacy_paid_usage_refs(config):
    old = record()
    service = annotator(config)
    paid = deepcopy(service.annotate(old, use_ai=True))
    legacy_hash = "historical-source-hash"
    legacy_key = service._legacy_cache_key(legacy_hash)
    paid.update(source_hash=legacy_hash, cache_key=legacy_key)
    paid.pop("annotation_input_hash")
    paid.pop("input_contract")
    for chunk in chunk_source(source_units(old), config.chunk_chars):
        paid["stages"][chunk["chunk_id"]]["extraction_key"] = service._legacy_extraction_key(
            legacy_hash, chunk,
        )
    pristine = deepcopy(paid)
    first = service._saved_review(paid, old)
    assert paid == pristine  # Rekeying must not mutate the saved nested stages.
    expected_refs = first["paid_usage_refs"]
    assert expected_refs
    for index in range(3):
        prior = deepcopy(first)
        changed = record(source_metadata={**old.source_metadata, "collection_id": str(index)})
        saved = first
        first = service._saved_review(saved, changed)
        assert saved == prior
        assert first["paid_usage_refs"] == expected_refs
        assert all(origin["usage_refs"] == expected_refs for origin in first["cache_reuse"])
        assert first["usage"] == pristine["usage"]
        # Mutating the returned copy cannot affect a persisted input envelope.
        marker = f"copy-only-{index}"
        first["stages"][next(iter(first["stages"]))][marker] = True
        assert marker not in saved["stages"][next(iter(saved["stages"]))]
    assert service.client.calls == ["extract", "review"]


def test_recovery_inspection_needs_no_key_and_never_writes_or_calls_models(config, monkeypatch):
    old = record()
    paid = annotator(config).annotate(old, use_ai=True)
    paths_before = {
        path: path.read_bytes() for path in Path(config.cache_dir).rglob("*") if path.is_file()
    }
    monkeypatch.delenv("CACHE_CONTRACT_FIXTURE_KEY")
    source = record(source_metadata={**old.source_metadata, "collection_id": "inspection"})
    original = deepcopy(source)
    inspector = annotator(config)
    monkeypatch.setattr(inspector, "_write", lambda *_: pytest.fail("Recovery inspection wrote"))
    inspected = inspector.inspect_cached_review(source)
    assert inspected["annotation"]["status"] == "reviewed"
    assert inspected["annotation"]["usage"] == paid["usage"]
    assert source == original and source._text_units_cache is None
    assert inspector.client.calls == []
    assert inspector.stats == {"extraction_calls": 0, "review_calls": 0,
                               "cache_hits": 0, "errors": 0}
    assert {
        path: path.read_bytes() for path in Path(config.cache_dir).rglob("*") if path.is_file()
    } == paths_before


def test_prefilter_uses_exact_units_and_transport_independent_spec(tmp_path, monkeypatch):
    monkeypatch.setenv("CACHE_CONTRACT_FIXTURE_KEY", "fixture-key")
    config = PrefilterConfig(enabled=True, api_key_env="CACHE_CONTRACT_FIXTURE_KEY",
                             cache_dir=str(tmp_path / "routing"))
    job = record()
    unit = next(unit for unit in source_units(job) if "You will" in unit["text"])

    class Client:
        calls = 0

        def generate_stage(self, stage, model, prompt):
            self.calls += 1
            return {"decision": "likely_relevant", "reason_code": "no_clear_mismatch",
                    "reason": "Needs full review.", "evidence": [{"unit_id": unit["unit_id"]}]}, {
                        "input_tokens": 12, "output_tokens": 5, "total_tokens": 17,
                    }

    first = VacancyPrefilter(config)
    first.client = Client()
    route = first.route(job)
    assert first.client.calls == 1
    changed = record(source_metadata={**job.source_metadata, "listing_observed_at": "later"})
    second = VacancyPrefilter(replace(config, request_timeout=123, max_retries=0))
    second.client = Client()
    reused = second.route(changed)
    assert second.client.calls == 0
    assert reused["routing"] == route["routing"]
    assert reused["annotation_input_hash"] == route["annotation_input_hash"]
    assert reused["source_hash"] == source_hash(changed)
    assert reused["usage"][0]["cached"] is True
