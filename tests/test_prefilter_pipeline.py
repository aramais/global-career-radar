"""Paid routing across profiles, persistence and offline reevaluation."""

import json
from copy import deepcopy
from dataclasses import replace

import pytest
import yaml
from sqlalchemy import select
from test_annotation_integration import _config, _record
from test_annotation_service import ModelFake

from job_intake.annotation import ai
from job_intake.annotation.prefilter import SCREEN_PROMPT, VERIFY_PROMPT
from job_intake.config.settings import PrefilterConfig, load_app_config, load_yaml_mapping
from job_intake.models.job import FilterDecision, JobStatus
from job_intake.pipeline import JobIntakePipeline
from job_intake.profiles import load_streams, with_other_stream
from job_intake.storage.models import AlertOutboxORM, JobORM, JobProfileEvaluationORM


class GateFake:
    def __init__(self, *, screen="clearly_irrelevant", verify="clearly_irrelevant", fail=False):
        self.screen, self.verify, self.fail = screen, verify, fail
        self.calls = []

    def generate_stage(self, stage, model, prompt):
        self.calls.append((stage, model))
        if self.fail:
            raise RuntimeError("private-provider-response-body")
        prefix = SCREEN_PROMPT if stage == "screen" else VERIFY_PROMPT
        data = json.loads(prompt[len(prefix):])
        decision = self.screen if stage == "screen" else self.verify
        evidence = []
        if decision == "clearly_irrelevant":
            unit = next(unit for unit in data["units"] if "You will" in unit["text"])
            evidence = [{"unit_id": unit["unit_id"], "source_snippet": unit["text"]}]
        return {
            "decision": decision,
            "reason_code": "outside_core_role" if evidence else "no_clear_mismatch",
            "reason": "Actual implementation duties." if evidence else "Retain for full review.",
            "evidence": evidence,
        }, {"input_tokens": 20, "output_tokens": 10, "total_tokens": 30}


@pytest.fixture
def app_config(tmp_path, monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("Test attempted a live provider request")

    monkeypatch.setattr(ai.AnnotationModelClient, "generate", forbidden)
    monkeypatch.setenv("ANNOTATION_INTEGRATION_TEST_KEY", "fake-test-key")
    monkeypatch.setenv("PREFILTER_PIPELINE_TEST_KEY", "fake-test-key")
    config = _config(tmp_path, ai=True)
    profiles = yaml.safe_load(config.search_profiles_path.read_text())
    profiles["streams"].append({
        **deepcopy(profiles["streams"][0]), "id": "analytics",
        "rules": {"positive_title_signals": ["head of analytics"]},
    })
    config.search_profiles_path.write_text(yaml.safe_dump(profiles))
    return replace(config, prefilter=PrefilterConfig(
        enabled=True, api_key_env="PREFILTER_PIPELINE_TEST_KEY",
        cache_dir=str(tmp_path / "prefilter-cache"),
    ))


def pipeline_with_fakes(config, gate=None):
    pipeline = JobIntakePipeline(config)
    pipeline.prefilter.client = gate or GateFake()
    pipeline.annotator.client = ModelFake(config.annotation)
    return pipeline


def noisy_record(**changes):
    return _record(description_clean=(
        "Responsibilities:\nYou will implement frontend components in React.\n"
    ), **changes)


def test_confirmed_suppression_skips_full_ai_once_across_profiles_and_retains_source(app_config):
    pipeline = pipeline_with_fakes(app_config)
    record = noisy_record()
    stats = pipeline._stats()
    items, keys, pending = pipeline._evaluate_record(record, stats, use_llm=True)
    assert len(pipeline.prefilter.client.calls) == 2  # screen + independent verification
    assert pipeline.annotator.client.calls == []
    assert stats["prefilter_suppressed"] == 1
    assert stats["prefilter_screen_calls"] == stats["prefilter_verify_calls"] == 1
    assert stats["annotation_extraction_calls"] == stats["annotation_review_calls"] == 0
    assert keys == {} and pending == []
    assert len(items) == 1
    assert all(item.evaluation.decision == FilterDecision.REVIEW for item in items)
    assert all(item.evaluation.tier.value == "C" for item in items)
    assert all(item.evaluation.fit_score == 0 for item in items)
    pipeline._save(items, keys, observed=True, queue_alert=False)
    with pipeline.database.session() as session:
        stored = session.scalar(select(JobORM))
        assert stored.archived_at is None
        assert stored.status == "open"
        assert stored.description_clean == record.description_clean
        assert stored.annotation["prefilter"]["routing"] == "suppressed"
        assert len(list(session.scalars(select(JobProfileEvaluationORM)))) == 1
        assert list(session.scalars(select(AlertOutboxORM))) == []


@pytest.mark.parametrize("screen,verify", [
    ("likely_relevant", "clearly_irrelevant"),
    ("uncertain", "clearly_irrelevant"),
    ("clearly_irrelevant", "uncertain"),
    ("clearly_irrelevant", "likely_relevant"),
])
def test_eligible_or_uncertain_or_disputed_routes_receive_full_annotation(
    app_config, screen, verify,
):
    gate = GateFake(screen=screen, verify=verify)
    pipeline = pipeline_with_fakes(app_config, gate)
    record = noisy_record()
    stats = pipeline._stats()
    items, _, _ = pipeline._evaluate_record(record, stats, use_llm=True)
    assert len(items) == 1
    assert stats["prefilter_full_review"] == 1
    assert stats["prefilter_suppressed"] == 0
    assert stats["annotation_extraction_calls"] == stats["annotation_review_calls"] == 1
    assert record.annotation["status"] == "reviewed"
    assert record.annotation["prefilter"]["routing"] == "full_review"
    assert len(gate.calls) == (2 if screen == "clearly_irrelevant" else 1)


def test_prefilter_failure_does_not_stop_full_review_or_leak_private_error(app_config):
    pipeline = pipeline_with_fakes(app_config, GateFake(fail=True))
    events = []
    pipeline.prefilter.set_progress(events.append)
    stats = pipeline._stats()
    record = noisy_record()
    pipeline._evaluate_record(record, stats, use_llm=True)
    assert stats["prefilter_errors"] == 1
    assert stats["prefilter_full_review"] == 1
    assert stats["annotation_extraction_calls"] == stats["annotation_review_calls"] == 1
    assert record.annotation["status"] == "reviewed"
    assert "private-provider-response-body" not in json.dumps(events)
    assert "private-provider-response-body" not in json.dumps(record.annotation["prefilter"])


def test_hard_blockers_skip_both_prefilter_and_full_ai_before_payment(app_config):
    pipeline = pipeline_with_fakes(app_config)
    stats = pipeline._stats()
    record = noisy_record(status=JobStatus.CLOSED)
    items, _, _ = pipeline._evaluate_record(record, stats, use_llm=True)
    assert all(item.evaluation.decision == FilterDecision.REJECT for item in items)
    assert pipeline.prefilter.client.calls == pipeline.annotator.client.calls == []
    assert stats["prefilter_local_skipped"] == 1
    assert record.annotation["prefilter"]["routing"] == "local_reject"


def test_a_blocker_in_an_unselected_role_does_not_affect_selected_role(app_config):
    data = yaml.safe_load(app_config.search_profiles_path.read_text())
    data["streams"][1]["rules"]["company_blacklist"] = ["Example"]
    app_config.search_profiles_path.write_text(yaml.safe_dump(data))
    pipeline = pipeline_with_fakes(app_config, GateFake(screen="uncertain"))
    stats = pipeline._stats()
    items, _, _ = pipeline._evaluate_record(noisy_record(), stats, use_llm=True)
    assert stats["prefilter_local_skipped"] == 0
    assert stats["annotation_extraction_calls"] == 1
    assert [item.evaluation.decision for item in items] == [
        FilterDecision.PASS,
    ]


def test_cached_suppression_survives_offline_reevaluation_and_observation_dates(app_config):
    pipeline = pipeline_with_fakes(app_config)
    stats = pipeline._stats()
    items, keys, _ = pipeline._evaluate_record(noisy_record(), stats, use_llm=True)
    pipeline._save(items, keys, observed=True, queue_alert=False)
    with pipeline.database.session() as session:
        stored = session.scalar(select(JobORM))
        dates = stored.first_seen_at, stored.last_seen_at

    offline = pipeline_with_fakes(app_config)
    result = offline.reevaluate_saved()
    assert result["persisted"] == 1 and result["evaluations"] == 1
    assert result["prefilter_suppressed"] == 1
    assert offline.prefilter.client.calls == offline.annotator.client.calls == []
    with offline.database.session() as session:
        stored = session.scalar(select(JobORM))
        assert (stored.first_seen_at, stored.last_seen_at) == dates
        assert stored.tier == "C" and stored.filter_decision == "review"
        assert stored.annotation["prefilter"]["routing"] == "suppressed"


def test_changed_source_discards_saved_suppression_offline(app_config):
    pipeline = pipeline_with_fakes(app_config)
    record = noisy_record()
    pipeline._evaluate_record(record, pipeline._stats(), use_llm=True)
    changed = replace(record, description_clean=(
        "Responsibilities:\nYou will lead analytical projects with Finance.\n"
    ))
    pipeline._evaluate_record(changed, pipeline._stats(), use_llm=False)
    assert "prefilter" not in changed.annotation


def test_bypass_removes_suppression_and_allows_explicit_full_review(app_config):
    pipeline = pipeline_with_fakes(app_config)
    record = noisy_record()
    pipeline._evaluate_record(record, pipeline._stats(), use_llm=True)
    bypass = pipeline_with_fakes(replace(
        app_config, prefilter=replace(app_config.prefilter, enabled=False),
    ))
    stats = bypass._stats()
    items, _, _ = bypass._evaluate_record(record, stats, use_llm=True)
    assert stats["annotation_extraction_calls"] == 1
    assert "prefilter" not in record.annotation
    assert all(item.evaluation.tier.value == "A" for item in items)


def test_active_versions_match_shared_crm_loader_and_model_defaults(app_config):
    pipeline = pipeline_with_fakes(app_config)
    shared = with_other_stream(load_streams(
        load_yaml_mapping(app_config.rules_path),
        load_yaml_mapping(app_config.search_profiles_path),
    ), load_yaml_mapping(app_config.rules_path))
    assert pipeline.profile_versions == {stream.id: stream.version for stream in shared}
    actual = load_app_config("config/settings.yaml")
    assert actual.annotation.review_model == "gemini-3.6-flash"
    assert actual.annotation.resolved_stage("review").reasoning_effort == "medium"
    assert actual.prefilter.enabled is True
