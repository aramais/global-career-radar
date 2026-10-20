"""Audit-10 regressions: hiring gates, observation lifecycle and policy versions."""

from __future__ import annotations

import copy
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import inspect, select
from test_labels_failure_preservation import budget
from test_labels_tagger import FakeClient, payload
from test_pipeline_profiles import _config, _record

from job_intake.annotation.text import source_hash, source_units
from job_intake.config.settings import LabelingConfig, SourceDefinition
from job_intake.labeling.application import apply_labels_hiring
from job_intake.labeling.schema import LabelResult
from job_intake.models.job import FilterDecision, JobEvaluation
from job_intake.pipeline import JobIntakePipeline
from job_intake.profiles import (
    labeling_version_components,
    stale_version_reasons,
    with_labeling_versions,
)
from job_intake.storage.models import AlertOutboxORM, JobORM, JobPurposeAssessmentORM

ROOT = Path(__file__).parents[1]


def hiring_record():
    return _record(
        "Responsibilities\nYou will lead analytics projects and pricing experiments "
        "for business decisions.\nYou will partner with leadership and stakeholders "
        "to own pricing decisions.\nRequirements\nEnglish required.\n"
        "This opening is restricted to residents of Namibia."
    )


def model_result(record):
    value = payload(record, role_label="product", relevance=3)
    units = source_units(record)
    restricted = next(unit["unit_id"] for unit in units if "Namibia" in unit["text"])
    duty = next(unit["unit_id"] for unit in units if "You will lead" in unit["text"])
    value["hiring_eligibility"].update(
        value="restricted_elsewhere", basis="explicit", evidence=[restricted],
    )
    value["domain_fit"].update(industry="other", evidence=[])
    value["preferred_focus"].update(values=["pricing_monetization"], evidence=[duty])
    return value


@pytest.fixture
def build(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Audit regressions must not call an external service")

    monkeypatch.setattr("requests.sessions.Session.request", forbidden)
    pipelines = []

    def factory(*, mode="labels", tier_source="relevance", notify=False, source_conflicts=False):
        folder = tmp_path / str(len(pipelines))
        folder.mkdir()
        config = _config(folder, telegram=notify)
        labeling = LabelingConfig(
            enabled=True, mode=mode, ai_enabled=True, tier_source=tier_source,
            rubric_path=str(ROOT / "config/labeling_rubric.yaml"),
            cache_dir=str(folder / "cache"), ledger_dir=str(folder / "ledger"),
            precedents_path=str(folder / "no-precedents"),
        )
        pipeline = JobIntakePipeline(replace(config, labeling=labeling))
        record = hiring_record()
        if source_conflicts:
            record.source_metadata["source_conflicts"] = [{"field": "location_text"}]
        answer = model_result(record)
        pipeline.tagger.client = FakeClient([copy.deepcopy(answer), copy.deepcopy(answer)])
        pipeline.tagger.budget = budget(labeling)
        pipeline._label_ai_authorized = True
        items, keys, _ = pipeline._evaluate_record(record, pipeline._stats(), use_llm=True)
        pipeline._label_ai_authorized = False
        pipelines.append(pipeline)
        return pipeline, record, items, keys

    yield factory
    for pipeline in pipelines:
        pipeline.database.engine.dispose()


@pytest.mark.parametrize("tier_source", ["score", "relevance"])
def test_confirmed_hiring_block_reaches_storage_p1_and_alerts_without_changing_relevance(
    build, tier_source,
):
    pipeline, record, items, keys = build(tier_source=tier_source, notify=True)
    labels = record.annotation["labels_v3"]
    assert labels["status"] == "labeled" and labels["escalated"]
    assert labels["gate_outcome"] == "ineligible" and labels["relevance"] == 3
    assert labels["gate_reasons"] == ["hiring_region_restricted"]
    assert pipeline.tagger.client.calls == ["tag", "escalate"]
    pipeline._save(items, keys, observed=True, queue_alert=True)
    with pipeline.database.session() as session:
        job = session.scalar(select(JobORM))
        purpose = session.scalar(select(JobPurposeAssessmentORM))
        assert job.filter_decision == "reject"
        assert job.eligibility_status == "ineligible"
        assert job.eligibility_evidence_status == "blocked"
        assert job.relevance == 3
        assert purpose.predicted_p1 == "unsuitable" and purpose.predicted_p2 == "suitable"
        assert session.scalars(select(AlertOutboxORM)).all() == []
        assert job.tier == ("A" if tier_source == "relevance" else "C")


def test_shadow_keeps_legacy_hiring_and_purpose_even_with_a_confirmed_model_block(build):
    pipeline, record, items, keys = build(mode="shadow")
    assert record.annotation["labels_v3"]["gate_outcome"] == "ineligible"
    pipeline._save(items, keys, observed=True, queue_alert=False)
    with pipeline.database.session() as session:
        job = session.scalar(select(JobORM))
        purpose = session.scalar(select(JobPurposeAssessmentORM))
        assert job.filter_decision == "pass" and job.eligibility_status == "eligible"
        assert purpose.predicted_p1 == "suitable"


def accepted(record, *, outcome="eligible", **changes):
    return asdict(LabelResult(
        status="labeled", role_label="product", relevance=3, rubric_status="confirmed",
        source_hash=source_hash(record), gate_outcome=outcome,
        gate_reasons=["hiring_region_restricted"] if outcome == "ineligible" else [],
        **changes,
    ))


def test_positive_gate_resolves_soft_hiring_unknown_but_never_a_rule_blocker():
    record = hiring_record()
    soft = JobEvaluation(FilterDecision.REVIEW, eligibility_status="unknown",
                         risks=["geography:eligibility_unconfirmed", "language:unconfirmed"])
    apply_labels_hiring(record, soft, accepted(record))
    assert soft.eligibility_status == "eligible" and soft.decision == FilterDecision.PASS
    assert soft.eligibility_evidence_status == "confirmed" and soft.risks == []
    blocked = JobEvaluation(FilterDecision.REJECT, eligibility_status="ineligible",
                            blocker_signals=["language:German"])
    apply_labels_hiring(record, blocked, accepted(record))
    assert blocked.decision == FilterDecision.REJECT and blocked.eligibility_status == "ineligible"
    assert blocked.blocker_signals == ["language:German"]


def test_unknown_gate_cannot_leave_legacy_eligibility_confirmed():
    record = hiring_record()
    evaluation = JobEvaluation(FilterDecision.PASS, eligibility_status="eligible")
    apply_labels_hiring(record, evaluation, accepted(record, outcome="unknown"))
    assert evaluation.decision == FilterDecision.REVIEW
    assert evaluation.eligibility_status == "unknown"
    assert evaluation.eligibility_evidence_status == "unconfirmed"


@pytest.mark.parametrize("change", ["stale_source", "proposed", "error", "rules_only"])
def test_stale_unconfirmed_or_failed_labels_cannot_change_hiring(change):
    record = hiring_record()
    labels = accepted(record, outcome="ineligible")
    if change == "stale_source":
        labels["source_hash"] = "0" * 64
    elif change == "proposed":
        labels["rubric_status"] = "proposed"
    else:
        labels["status"] = change
    evaluation = JobEvaluation(FilterDecision.PASS, eligibility_status="eligible")
    before = copy.deepcopy(evaluation)
    apply_labels_hiring(record, evaluation, labels)
    assert evaluation == before


def test_source_conflicts_cannot_be_promoted_to_confirmed_eligibility():
    record = hiring_record()
    record.source_metadata["source_conflicts"] = [{"field": "location_text"}]
    evaluation = JobEvaluation(FilterDecision.REVIEW, eligibility_status="unknown")
    apply_labels_hiring(record, evaluation, accepted(record))
    assert evaluation.eligibility_status == "unknown"
    assert evaluation.decision == FilterDecision.REVIEW


def row_values(row):
    return {column.key: copy.deepcopy(getattr(row, column.key))
            for column in inspect(type(row)).columns}


@pytest.mark.parametrize("observed", [False, True])
def test_failed_labels_preserve_the_assessment_but_keep_genuine_observations(build, observed):
    pipeline, record, items, keys = build()
    pipeline._save(items, keys, observed=True, queue_alert=False)
    old = datetime(2020, 1, 1)
    with pipeline.database.session() as session:
        job = session.scalar(select(JobORM))
        job.last_seen_at = old
        session.commit()
        before = row_values(job)
        purpose_before = row_values(session.scalar(select(JobPurposeAssessmentORM)))
    pipeline.tagger.config = replace(pipeline.tagger.config,
                                     cache_dir=pipeline.tagger.config.cache_dir + "-empty")
    items, keys, _ = pipeline._evaluate_record(record, pipeline._stats(), use_llm=False)
    assert items[0].record.annotation["labels_v3"]["status"] == "rules_only"
    pipeline._save(items, keys, observed=observed, queue_alert=False)
    with pipeline.database.session() as session:
        job = session.scalar(select(JobORM))
        after = row_values(job)
        assert (after["last_seen_at"] > old) is observed
        for field in ("last_seen_at", "updated_at", "annotation"):
            before.pop(field)
            after.pop(field)
        assert after == before
        assert row_values(session.scalar(select(JobPurposeAssessmentORM))) == purpose_before
        assert job.annotation["labels_v3_attempt"]["status"] == "rules_only"
        assert job.annotation["labels_v3"]["status"] == "labeled"


@pytest.mark.parametrize("archived", [False, True])
def test_observed_fallback_keeps_complete_board_lifecycle_bookkeeping(build, monkeypatch, archived):
    pipeline, record, items, keys = build()
    pipeline._save(items, keys, observed=True, queue_alert=False)
    with pipeline.database.session() as session:
        job = session.scalar(select(JobORM))
        job.last_seen_at = datetime(2020, 1, 1)
        job.missing_since = job.missing_last_checked_at = datetime(2020, 1, 1)
        job.missing_successes = 3
        job.missing_verified_seconds = 1000
        if archived:
            job.archived_at = datetime(2020, 1, 1)
        session.commit()
    pipeline.tagger.config = replace(pipeline.tagger.config,
                                     cache_dir=pipeline.tagger.config.cache_dir + "-empty")
    pipeline.config.sources = [SourceDefinition("good", "html")]

    class Adapter:
        errors = []
        board_snapshots = [{"source": "good", "scope": "board", "status": "success",
                            "snapshot_complete": True, "seen_job_ids": ["42"], "seen_urls": []}]

        def fetch_jobs(self):
            return [copy.deepcopy(record)]

    monkeypatch.setattr("job_intake.pipeline.build_adapter", lambda _: Adapter())
    stats = pipeline.run()
    assert stats["persisted"] == 1 and stats["record_errors"] == 0
    with pipeline.database.session() as session:
        job = session.scalar(select(JobORM))
        assert job.last_seen_at > datetime(2020, 1, 1)
        assert job.missing_since is job.missing_last_checked_at is None
        assert job.missing_successes == job.missing_verified_seconds == 0
        assert job.last_complete_seen_at is not None
        assert job.relevance == 3 and job.tier == "A"
        assert (job.archived_at is not None) is archived


@pytest.mark.parametrize("field,value", [("final_policy", "rules"), ("tier_source", "score")])
def test_policy_changes_stale_labels_evaluation_without_invalidating_fact_cache(
    build, field, value,
):
    pipeline, record, _, _ = build()
    old = pipeline.config.labeling
    new = replace(old, **{field: value})
    before = labeling_version_components(old)
    after = labeling_version_components(new)
    assert before["labels_prompt"] == after["labels_prompt"]
    assert before["labels_rubric"] == after["labels_rubric"]
    assert stale_version_reasons(before, after) == ["version:labels_aggregation:changed"]
    assert with_labeling_versions(pipeline.streams, old)[0].version \
        != with_labeling_versions(pipeline.streams, new)[0].version
    calls = list(pipeline.tagger.client.calls)
    pipeline.tagger.config = new
    result = pipeline.tagger.label(record, pipeline.streams[0].engine.apply(record).evaluation,
                                   {"label": "product"}, use_ai=True)
    assert result.status == "labeled" and pipeline.tagger.client.calls == calls


@pytest.mark.parametrize("enabled,mode", [(False, "labels"), (True, "legacy"), (True, "shadow")])
def test_policy_changes_do_not_invalidate_disabled_legacy_or_shadow_versions(build, enabled, mode):
    pipeline, _, _, _ = build()
    config = replace(pipeline.config.labeling, enabled=enabled, mode=mode)
    assert labeling_version_components(config) == {}
    changed = replace(config, final_policy="rules", tier_source="score")
    assert labeling_version_components(changed) == {}
    assert with_labeling_versions(pipeline.streams, config) == pipeline.streams
    assert with_labeling_versions(pipeline.streams, changed) == pipeline.streams
