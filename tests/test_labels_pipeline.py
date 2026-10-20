"""P3 mode boundaries and transactional labeling with fake model results."""

from copy import deepcopy
from dataclasses import asdict, dataclass, field, replace

import pytest
from sqlalchemy import select
from test_pipeline_profiles import _config, _record

from job_intake.config.settings import LabelingConfig
from job_intake.models.job import FilterDecision, JobStatus, JobTier
from job_intake.pipeline import JobIntakePipeline
from job_intake.profiles import (
    labeling_version_components,
    load_streams,
    stale_version_reasons,
    with_labeling_versions,
)
from job_intake.purpose import (
    LEGACY_PURPOSE_RUBRIC_VERSION,
    PURPOSE_RUBRIC_VERSION,
    predict_purpose,
)
from job_intake.storage.models import JobORM, JobPurposeAssessmentORM


@dataclass
class Labels:
    schema: str = "vacancy-labels-v3"
    status: str = "labeled"
    role_label: str = "product"
    relevance: int = 3
    rubric_status: str = "confirmed"
    rubric_version: str = "test-rubric"
    prompt_version: str = "prompt-v1"
    aggregation_version: str = "aggregation-v1"
    cache_key: str = "test-key"
    gate_outcome: str = "unknown"
    gate_reasons: list = field(default_factory=list)
    tags: dict = field(default_factory=dict)
    p2_candidate: bool = False


class FakeTagger:
    def __init__(self, config, streams, **kwargs):
        self.config = config
        self.stats = dict.fromkeys(
            ("calls", "cache_hits", "facts_reuse", "escalations", "errors", "gated"), 0,
        )
        self.progress = None
        self.budget = self.ledger = None
        self.result = Labels()
        self.paid = self.offline = 0

    def set_progress(self, progress):
        self.progress = progress

    def label(self, record, evaluation, classification, *, use_ai):
        assert use_ai is True
        self.paid += 1
        self.stats["calls"] += 1
        return deepcopy(self.result)

    def relabel_offline(self, record, evaluation, classification):
        self.offline += 1
        self.stats["cache_hits"] += 1
        return deepcopy(self.result)


@pytest.fixture
def factory(tmp_path, monkeypatch):
    monkeypatch.setattr("job_intake.labeling.tagger.VacancyTagger", FakeTagger)

    def build(*, mode="shadow", enabled=True, tier_source="score", **changes):
        config = _config(tmp_path)
        labeling = LabelingConfig(
            enabled=enabled, mode=mode, ai_enabled=True, tier_source=tier_source,
            cache_dir=str(tmp_path / "labels-cache"),
            ledger_dir=str(tmp_path / "labels-ledger"),
        )
        return JobIntakePipeline(replace(config, labeling=labeling, **changes))

    return build


@pytest.mark.parametrize("mode,enabled", [("shadow", False), ("legacy", True)])
def test_disabled_and_legacy_do_not_invoke_tagger_or_change_profile_versions(
    factory, mode, enabled,
):
    pipeline = factory(mode=mode, enabled=enabled)
    assert not any("labels_prompt" in stream.version_components for stream in pipeline.streams)
    record = _record()
    items, _, _ = pipeline._evaluate_record(record, pipeline._stats(), use_llm=False)
    assert pipeline.tagger.paid == pipeline.tagger.offline == 0
    assert "labels_v3" not in record.annotation
    assert items[0].evaluation.relevance is None


def test_shadow_preserves_the_complete_old_evaluation_and_purpose(factory):
    legacy = factory(enabled=False)
    shadow = factory()
    old, _, _ = legacy._evaluate_record(_record(), legacy._stats(), use_llm=False)
    new, _, _ = shadow._evaluate_record(_record(), shadow._stats(), use_llm=False)
    old_value, new_value = asdict(old[0].evaluation), asdict(new[0].evaluation)
    for name in ("relevance", "labels_status", "labels_version"):
        old_value.pop(name)
        new_value.pop(name)
    assert old_value == new_value
    assert old[0].profile_version == new[0].profile_version
    assert old[0].version_components == new[0].version_components
    assert predict_purpose(old[0]) == predict_purpose(new[0])
    assert predict_purpose(new[0])["rubric_version"] == LEGACY_PURPOSE_RUBRIC_VERSION
    assert new[0].record.annotation["labels_v3"]["relevance"] == 3


def test_labels_avoids_all_legacy_paid_paths_and_preserves_labels_after_annotation(factory):
    pipeline = factory(mode="labels")
    pipeline.config.annotation.ai_enabled = True
    pipeline.config.prefilter.enabled = True
    pipeline.config.llm.enabled = True
    requested = []
    original = pipeline.annotator.annotate

    def local_only(record, *, use_ai):
        requested.append(use_ai)
        assert use_ai is False
        return original(record, use_ai=False)

    def forbidden(*args, **kwargs):
        pytest.fail("Legacy paid path must not run in labels mode")

    pipeline.annotator.annotate = local_only
    pipeline.prefilter.route = forbidden
    pipeline.reranker.rerank = forbidden
    pipeline.classifier.client.generate = forbidden
    # The budgeted label command is the only owner of this authorization context.
    pipeline._label_ai_authorized = True
    stats = pipeline._stats()
    items, keys, pending = pipeline._evaluate_record(_record(), stats, use_llm=True)
    assert requested == [False]
    assert pipeline.tagger.paid == 1
    assert stats["labels_calls"] == 1
    assert stats["prefilter_screen_calls"] == stats["annotation_extraction_calls"] == 0
    assert keys == {} and pending == []
    assert items[0].record.annotation["labels_v3"] == asdict(pipeline.tagger.result)


def test_labels_role_change_rebuilds_stream_and_persists_one_role(factory):
    pipeline = factory(mode="labels")
    pipeline.tagger.result.role_label = "analytics"
    stats = pipeline._stats()
    items, keys, _ = pipeline._evaluate_record(_record(), stats, use_llm=True)
    assert len(items) == 1 and items[0].profile_id == "analytics"
    assert items[0].role_classification["label"] == "analytics"
    assert items[0].role_classification["method"] == "labels_v3"
    assert items[0].evaluation.relevance == 3
    pipeline._save(items, keys, observed=True, queue_alert=False)
    with pipeline.database.session() as session:
        job = session.scalar(select(JobORM))
        assert job.role_label == "analytics"
        assert job.relevance == 3 and job.labels_status == "labeled"
        assert len(job.profile_evaluations) == 1


@pytest.mark.parametrize("status,rubric,relevance,expected", [
    ("labeled", "confirmed", 3, "relevance"),
    ("gated", "confirmed", 0, "relevance"),
    ("error", "confirmed", 1, "score"),
    ("rules_only", "confirmed", 1, "score"),
    ("labeled", "proposed", 3, "score"),
    ("labeled", "confirmed", None, "score"),
    ("labeled", "confirmed", True, "score"),
])
def test_effective_relevance_tier_requires_confirmed_success(
    factory, status, rubric, relevance, expected,
):
    pipeline = factory(mode="labels", tier_source="relevance")
    labels = asdict(replace(pipeline.tagger.result, status=status, rubric_status=rubric,
                            relevance=relevance))
    assert pipeline._effective_tier_source(labels) == expected


def test_reevaluate_uses_offline_tagger_and_restores_progress(factory):
    pipeline = factory(mode="labels")
    pipeline._label_ai_authorized = True
    items, keys, _ = pipeline._evaluate_record(_record(), pipeline._stats(), use_llm=True)
    pipeline._save(items, keys, observed=True, queue_alert=False)
    pipeline._label_ai_authorized = False
    def original(event):
        pass
    pipeline.tagger.set_progress(original)
    paid_before = pipeline.tagger.paid
    result = pipeline.reevaluate_saved(progress=lambda event: None)
    assert result["persisted"] == 1
    assert result["labels_cache_hits"] == 1
    assert pipeline.tagger.paid == paid_before
    assert pipeline.tagger.offline == 1
    assert pipeline.tagger.progress is original


def test_budget_exhaustion_keeps_already_committed_jobs(factory, monkeypatch):
    from job_intake.labeling.budget import BudgetExceeded

    pipeline = factory(mode="labels")
    for index in range(2):
        record = _record()
        record.source_job_id = str(index)
        record.original_url += str(index)
        items, keys, _ = pipeline._evaluate_record(record, pipeline._stats(), use_llm=False)
        pipeline._save(items, keys, observed=True, queue_alert=False)
    count = 0
    original = pipeline.tagger.label

    def capped(*args, **kwargs):
        nonlocal count
        count += 1
        if count == 2:
            raise BudgetExceeded("budget_exceeded")
        return original(*args, **kwargs)

    monkeypatch.setattr(pipeline.tagger, "label", capped)
    saved = []
    original_save = pipeline._save

    def capture(*args, **kwargs):
        original_save(*args, **kwargs)
        saved.append(args[0][0].record.original_url)

    monkeypatch.setattr(pipeline, "_save", capture)
    with pytest.raises(BudgetExceeded):
        pipeline.label_saved(use_ai=True, budget=object())
    assert count == 2 and len(saved) == 1
    assert pipeline._label_ai_authorized is False


def test_shadow_or_disabled_configuration_cannot_rewrite_evaluations_via_label_saved(factory):
    with pytest.raises(ValueError, match="mode=labels"):
        factory().label_saved()


@pytest.mark.parametrize("candidate,status,expected", [
    (True, "labeled", "suitable"),
    (True, "error", "suitable"),
    (False, "labeled", "unsuitable"),
    (False, "error", "unknown"),
    (None, "labeled", "unknown"),
])
def test_labels_practice_uses_candidate_and_preserves_unknown(factory, candidate, status, expected):
    pipeline = factory(mode="labels")
    pipeline.tagger.result.p2_candidate = candidate
    pipeline.tagger.result.status = status
    items, _, _ = pipeline._evaluate_record(_record(), pipeline._stats(), use_llm=False)
    value = predict_purpose(items[0])
    assert value["predicted_p2"] == value["practice_suitability"] == expected
    assert value["rubric_version"] == PURPOSE_RUBRIC_VERSION


def test_closed_vacancy_never_becomes_practice_suitable(factory):
    pipeline = factory(mode="labels")
    pipeline.tagger.result.p2_candidate = True
    record = _record()
    record.status = JobStatus.CLOSED
    items, _, _ = pipeline._evaluate_record(record, pipeline._stats(), use_llm=False)
    assert predict_purpose(items[0])["predicted_p2"] == "unsuitable"


def test_legacy_human_purpose_does_not_become_stale_when_new_labels_rubric_exists(factory):
    from job_intake.purpose import serialize_purpose, update_human_purpose

    pipeline = factory(enabled=False)
    items, keys, _ = pipeline._evaluate_record(_record(), pipeline._stats(), use_llm=False)
    pipeline._save(items, keys, observed=True, queue_alert=False)
    with pipeline.database.session() as session:
        row = session.scalar(select(JobPurposeAssessmentORM))
        assert row.rubric_version == LEGACY_PURPOSE_RUBRIC_VERSION
        update_human_purpose(session, row.job_uid, {
            "expected_version": row.version, "source_hash": row.prediction_source_hash,
            "human_p1": "suitable", "human_p2": "unsuitable",
        })
        assert not serialize_purpose(row)["human_stale"]
        assert row.human_p1_rubric_version == LEGACY_PURPOSE_RUBRIC_VERSION
        assert row.human_p2_rubric_version == LEGACY_PURPOSE_RUBRIC_VERSION


def test_only_labels_mode_has_label_contract_versions_and_explained_staleness():
    streams = load_streams({}, {"title_weights": {"analyst": 8}})
    shadow = LabelingConfig(enabled=True, mode="shadow")
    assert with_labeling_versions(streams, shadow) is streams
    labels = replace(shadow, mode="labels")
    versioned = with_labeling_versions(streams, labels)
    assert streams[0].version != versioned[0].version
    reasons = stale_version_reasons(streams[0].version_components, versioned[0].version_components)
    assert reasons == ["version:labels_aggregation:changed", "version:labels_prompt:changed",
                       "version:labels_rubric:changed"]
    assert labeling_version_components(replace(labels, enabled=False)) == {}
    assert stale_version_reasons(
        versioned[0].version_components, streams[0].version_components,
    ) == []


@pytest.mark.parametrize("relevance,tier", [
    (0, JobTier.C), (1, JobTier.C), (2, JobTier.B), (3, JobTier.A),
])
def test_relevance_tier_is_independent_of_score_and_obeys_source_cap(relevance, tier):
    from job_intake.models.job import EvaluatedJob, JobEvaluation
    from job_intake.scoring.tiering import finalize_tier

    item = EvaluatedJob(_record(), JobEvaluation(
        decision=FilterDecision.REVIEW, fit_score=0, relevance=relevance,
        fit_policy="role_fit_v1", bridge_role=False,
    ))
    finalize_tier(item, 100, 50, tier_source="relevance")
    assert item.evaluation.tier == tier
    assert item.evaluation.fit_score == 0
    item.evaluation.fit_max_tier = JobTier.C
    finalize_tier(item, 100, 50, tier_source="relevance")
    assert item.evaluation.tier == JobTier.C


@pytest.mark.parametrize("relevance", [None, True, -1, 4, 2.0])
def test_relevance_tier_rejects_unknown_or_invalid_value(relevance):
    from job_intake.models.job import EvaluatedJob, JobEvaluation
    from job_intake.scoring.tiering import finalize_tier

    item = EvaluatedJob(_record(), JobEvaluation(
        decision=FilterDecision.REVIEW, relevance=relevance,
    ))
    with pytest.raises(ValueError, match="relevance"):
        finalize_tier(item, 14, 8, tier_source="relevance")
