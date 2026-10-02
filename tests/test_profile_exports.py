import csv
from dataclasses import replace

from job_intake.models.job import EvaluatedJob, FilterDecision, JobEvaluation, JobRecord, JobTier
from job_intake.storage.database import Database
from job_intake.storage.repository import JobRepository


def _item(name, profile="product", tier=JobTier.A, score=20, decision=FilterDecision.PASS):
    return EvaluatedJob(
        record=JobRecord(
            source="test",
            source_job_id=name,
            company="Acme",
            title=name,
            original_url=f"https://example.com/jobs/{name}",
            description_clean="Full saved description.",
        ),
        evaluation=JobEvaluation(decision=decision, fit_score=score, tier=tier),
        profile_id=profile,
        profile_name=profile.title(),
        profile_version="v1",
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
    assert len(_export(repo, tmp_path, wide=True, include_rejected=True)) == 2


def test_shortlist_filters_before_limit(tmp_path):
    repo = _repository()
    for index in range(3):
        repo.upsert_evaluated_job(_item(f"low-{index}", tier=JobTier.C, score=100))
    repo.upsert_evaluated_job(_item("best", tier=JobTier.A, score=20))
    rows = _export(repo, tmp_path, limit=1)
    assert [row["title"] for row in rows] == ["best"]


def test_profile_shortlist_uses_selected_tier_and_score(tmp_path):
    repo = _repository()
    product = _item("shared")
    analytics = replace(
        product,
        profile_id="analytics",
        profile_name="Analytics",
        evaluation=JobEvaluation(decision=FilterDecision.REVIEW, fit_score=0, tier=JobTier.C),
    )
    repo.upsert_evaluations([product, analytics])
    versions = {"product": "v1", "analytics": "v1"}
    assert not _export(repo, tmp_path, profile_id="analytics", active_profile_versions=versions)
    rows = _export(
        repo, tmp_path, wide=True, profile_id="analytics", active_profile_versions=versions
    )
    assert rows[0]["tier"] == "C"
    assert float(rows[0]["fit_score"]) == 0
    assert rows[0]["profile_id"] == "analytics"


def test_disabled_winner_does_not_leak_into_current_export(tmp_path):
    repo = _repository()
    product = _item("shared")
    analytics = replace(
        product,
        profile_id="analytics",
        profile_name="Analytics",
        evaluation=JobEvaluation(decision=FilterDecision.REVIEW, fit_score=2, tier=JobTier.C),
    )
    repo.upsert_evaluations([product, analytics])
    rows = _export(repo, tmp_path, wide=True, active_profile_versions={"analytics": "v1"})
    assert rows[0]["tier"] == "C"
    assert rows[0]["profile_id"] == "analytics"
    assert float(rows[0]["fit_score"]) == 2


def test_stale_reject_is_visible_for_reevaluation_without_old_verdict(tmp_path):
    repo = _repository()
    repo.upsert_evaluations([_item("old", tier=JobTier.C, decision=FilterDecision.REJECT)])
    rows = _export(repo, tmp_path, wide=True, active_profile_versions={"product": "v2"})
    assert rows[0]["evaluation_state"] == "needs_reevaluation"
    assert rows[0]["filter_decision"] == rows[0]["tier"] == rows[0]["fit_score"] == ""


def test_reenabled_profile_with_old_content_is_not_current(tmp_path):
    repo = _repository()
    product = _item("shared")
    analytics = replace(product, profile_id="analytics")
    repo.upsert_evaluations([product, analytics])
    changed = replace(
        product,
        record=replace(
            product.record, description_clean="Updated description, US residency required."
        ),
    )
    repo.upsert_evaluations([changed])
    rows = _export(repo, tmp_path, wide=True, active_profile_versions={"analytics": "v1"})
    assert rows[0]["evaluation_state"] == "needs_reevaluation"


def test_aggregation_is_independent_of_profile_order():
    repo = _repository()
    first = _item("shared", profile="a")
    second = replace(first, profile_id="b")
    repo.upsert_evaluations([second, first])
    assert repo.list_jobs()[0].best_profile_id == "a"
    repo.upsert_evaluations([first, second])
    assert repo.list_jobs()[0].best_profile_id == "a"
