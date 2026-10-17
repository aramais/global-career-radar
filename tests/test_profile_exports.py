import csv
from dataclasses import replace

import pytest

from job_intake.annotation.text import source_hash
from job_intake.models.job import EvaluatedJob, FilterDecision, JobEvaluation, JobRecord, JobTier
from job_intake.storage.database import Database
from job_intake.storage.repository import JobRepository


def _item(name, profile="product", tier=JobTier.A, score=20, decision=FilterDecision.PASS):
    return EvaluatedJob(
        record=JobRecord(
            source="test", source_job_id=name, company="Acme", title=name,
            original_url=f"https://example.com/jobs/{name}",
            description_clean="Full saved description.",
        ),
        evaluation=JobEvaluation(decision=decision, fit_score=score, tier=tier),
        profile_id=profile, profile_name=profile.title(), profile_version="v1",
    )


def _repository():
    db = Database("sqlite:///:memory:")
    db.create_schema()
    return JobRepository(db.session())


def _export(repo, tmp_path, **kwargs):
    repo.session.flush()
    path = repo.export_shortlisted_csv(tmp_path / "jobs.csv", **kwargs)
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def test_wide_feed_keeps_low_scores_and_separates_hard_rejects(tmp_path):
    repo = _repository()
    repo.upsert_evaluations([_item("low", tier=JobTier.C, score=0)])
    repo.upsert_evaluations([_item("blocked", tier=JobTier.C, decision=FilterDecision.REJECT)])
    rows = _export(repo, tmp_path, wide=True, active_profile_versions={"product": "v1"})
    assert [row["title"] for row in rows] == ["low"]
    assert rows[0]["description"] == "Full saved description."
    assert rows[0]["role_label"] == "product"
    assert "profile_scores" not in rows[0]
    assert len(_export(repo, tmp_path, wide=True, include_rejected=True)) == 2


def test_shortlist_filters_before_limit(tmp_path):
    repo = _repository()
    for index in range(3):
        repo.upsert_evaluations([_item(f"low-{index}", tier=JobTier.C, score=100)])
    repo.upsert_evaluations([_item("best", tier=JobTier.A, score=20)])
    assert [row["title"] for row in _export(repo, tmp_path, limit=1)] == ["best"]


def test_role_filter_does_not_switch_to_unrelated_assessment(tmp_path):
    repo = _repository()
    item = _item("Data Science Manager", profile="science", score=18)
    repo.upsert_evaluations([item])
    versions = {"product": "v1", "science": "v1"}
    assert not _export(repo, tmp_path, wide=True, profile_id="product",
                       active_profile_versions=versions)
    rows = _export(repo, tmp_path, profile_id="science", active_profile_versions=versions)
    assert rows[0]["role_label"] == "science"
    assert float(rows[0]["fit_score"]) == 18


def test_disabled_role_is_stale_instead_of_using_another_profile(tmp_path):
    repo = _repository()
    repo.upsert_evaluations([_item("shared", profile="analytics")])
    rows = _export(repo, tmp_path, wide=True, active_profile_versions={"product": "v1"})
    assert rows[0]["role_label"] == "analytics"
    assert rows[0]["evaluation_state"] == "needs_reevaluation"
    assert rows[0]["fit_score"] == ""


def test_stale_reject_is_visible_for_reevaluation_without_old_verdict(tmp_path):
    repo = _repository()
    repo.upsert_evaluations([_item("old", tier=JobTier.C, decision=FilterDecision.REJECT)])
    rows = _export(repo, tmp_path, wide=True, active_profile_versions={"product": "v2"})
    assert rows[0]["evaluation_state"] == "needs_reevaluation"
    assert rows[0]["filter_decision"] == rows[0]["tier"] == rows[0]["fit_score"] == ""


def test_role_switch_replaces_the_only_evaluation(tmp_path):
    repo = _repository()
    original = _item("shared", profile="product")
    repo.upsert_evaluations([original])
    repo.upsert_evaluations([replace(original, profile_id="analytics", profile_name="Analytics")])
    job = repo.list_jobs()[0]
    assert job.role_label == "analytics"
    assert [(row.profile_id, row.fit_score) for row in job.profile_evaluations] == [
        ("analytics", 20),
    ]
    assert _export(repo, tmp_path, profile_id="product") == []


def test_multiple_role_evaluations_are_rejected_before_any_write():
    repo = _repository()
    first = _item("shared", profile="a")
    with pytest.raises(ValueError, match="Exactly one"):
        repo.upsert_evaluations([first, replace(first, profile_id="b")])
    assert repo.list_jobs() == []


@pytest.mark.parametrize("field,value", [("label", "different"), ("content_hash", "old"),
                                         ("source_hash", "old")])
def test_classification_must_match_role_and_source(field, value):
    repo = _repository()
    item = _item("classified")
    classification = {"label": item.profile_id, "name": item.profile_name, "version": "v1",
                      "content_hash": repo.deduplicator.build_identity(item.record).content_hash,
                      "source_hash": source_hash(item.record), "method": "local", "reason": "Title"}
    classification[field] = value
    with pytest.raises(ValueError, match="classification must match"):
        repo.upsert_evaluations([replace(item, role_classification=classification)])
    assert repo.list_jobs() == []


def test_default_readers_do_not_fall_back_to_legacy_aggregate(tmp_path):
    repo = _repository()
    item = _item("legacy")
    repo.upsert_evaluated_job(item)
    job = repo.list_jobs()[0]
    job.role_label = None
    job.role_classification = {}
    repo.session.flush()
    assert repo.recent_jobs_for_digest() == []
    assert _export(repo, tmp_path) == []
    rows = _export(repo, tmp_path, wide=True)
    assert rows[0]["evaluation_state"] == "needs_reevaluation"
    assert rows[0]["role_label"] == rows[0]["fit_score"] == ""


@pytest.mark.parametrize("field,value", [("label", "different"), ("content_hash", "old"),
                                         ("source_hash", "tampered"), ("version", "tampered")])
def test_tampered_classification_cannot_publish_current_relevance(tmp_path, field, value):
    from job_intake.crm.jobs import job_detail, list_job_page

    repo = _repository()
    result = repo.upsert_evaluations([_item("tampered")])
    job = repo.list_jobs()[0]
    job.role_classification = {**job.role_classification, field: value}
    repo.session.flush()
    detail = job_detail(repo.session, {"product": "v1"}, result.job_uid)
    assert detail["classification_state"] == "needs_reclassification"
    assert detail["relevance"] is None
    assert list_job_page(repo.session, {"product": "v1"}, {"score_min": "0"})["total"] == 0
    assert _export(repo, tmp_path) == []
    row = _export(repo, tmp_path, wide=True)[0]
    assert row["classification_state"] == "needs_reclassification"
    assert row["fit_score"] == ""
    assert repo.recent_jobs_for_digest() == []


def test_other_is_unscored_and_preserves_mandatory_gate(tmp_path):
    from job_intake.crm.jobs import job_detail, list_job_page

    repo = _repository()
    result = repo.upsert_evaluations([
        _item("Outside role", profile="other", score=0, tier=JobTier.C),
    ])
    detail = job_detail(repo.session, {"other": "v1"}, result.job_uid)
    assert detail["role_label"] == "other"
    assert detail["evaluation_state"] == "unscored"
    assert detail["relevance"] is detail["fit_score"] is detail["tier"] is None
    assert detail["eligibility"]["decision"] == "pass"
    assert list_job_page(repo.session, {"other": "v1"}, {"tier": "unrated"})["total"] == 1
    assert list_job_page(repo.session, {"other": "v1"}, {"score_min": "0"})["total"] == 0
    row = _export(repo, tmp_path, wide=True)[0]
    assert row["role_label"] == "other"
    assert row["evaluation_state"] == "unscored"
    assert row["fit_score"] == row["tier"] == ""
    blocked = _item("Closed outside role", profile="other", decision=FilterDecision.REJECT,
                    score=0, tier=JobTier.C)
    blocked.evaluation.blocker_signals = ["status:closed"]
    blocked_result = repo.upsert_evaluations([blocked])
    assert len(_export(repo, tmp_path, wide=True)) == 1
    blocked_detail = job_detail(repo.session, {"other": "v1"}, blocked_result.job_uid)
    assert blocked_detail["relevance"] is None
    assert blocked_detail["eligibility"] == {"decision": "reject",
                                              "blocker_signals": ["status:closed"], "risks": []}
    assert len(_export(repo, tmp_path, wide=True, include_rejected=True)) == 2
