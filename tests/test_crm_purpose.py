import pytest
from sqlalchemy import insert, select, update
from test_crm_audit_fixes import seed_jobs
from test_crm_server import service as service
from test_crm_ui import _run_javascript

from job_intake.crm.server import RequestError
from job_intake.purpose import PURPOSE_RUBRIC_VERSION
from job_intake.storage.models import JobEventORM, JobProfileEvaluationORM, JobPurposeAssessmentORM


def purpose_rows(service, ids):
    with service.database.session() as session:
        session.execute(insert(JobPurposeAssessmentORM), [{
            "job_uid": uid, "predicted_p1": "unsuitable", "predicted_p2": "suitable",
            "prediction_source_hash": "source-v1", "prediction_reason": {"p2": ["similar_tasks"]},
        } for uid in ids])
        session.commit()


def test_purpose_prediction_human_labels_and_hiring_are_independent(service):
    uid = seed_jobs(service)[0]
    purpose_rows(service, [uid])
    with service.database.session() as session:
        session.execute(update(JobProfileEvaluationORM).where(
            JobProfileEvaluationORM.job_uid == uid,
        ).values(eligibility_status="ineligible", eligibility_evidence_status="blocked",
                 raw_fit_score=31, fit_score=5.5, score_cap_reasons=["reporting_core"]))
        session.commit()
    detail = service.job(uid)
    assert detail["raw_fit_score"] == 31 and detail["score_cap_reasons"] == ["reporting_core"]
    assert detail["eligibility_status"] == "ineligible"
    assert detail["purpose"]["human_p2"] == "unknown"
    assert detail["purpose"]["predicted_p2"] == "suitable"
    assert service.jobs({"purpose": "p2"})["total"] == 1
    assert service.jobs({"purpose": "p1"})["total"] == 0
    result = service.mutate("PATCH", f"/api/jobs/{uid}/purpose", {
        "human_p1": "unsuitable", "human_p2": "suitable", "human_note": "Practice useful",
        "expected_version": 1, "source_hash": "source-v1",
    })
    assert result["purpose"]["human_p2"] == "suitable"
    assert result["purpose"]["predicted_p1"] == "unsuitable"
    assert result["purpose"]["version"] == 2
    assert service.jobs({"purpose": "review"})["total"] == 0
    with service.database.session() as session:
        events = list(session.scalars(select(JobEventORM).where(
            JobEventORM.event_type == "human_purpose_updated",
        )))
        assert len(events) == 1


def test_purpose_write_conflict_does_not_overwrite_human_or_machine_decision(service):
    uid = seed_jobs(service)[0]
    purpose_rows(service, [uid])
    payload = {"human_p2": "unsuitable", "expected_version": 1, "source_hash": "source-v1"}
    service.mutate("PATCH", f"/api/jobs/{uid}/purpose", payload)
    with pytest.raises(RequestError) as error:
        service.mutate("PATCH", f"/api/jobs/{uid}/purpose", {**payload, "human_p2": "suitable"})
    assert error.value.status == 409
    detail = service.job(uid)
    assert detail["purpose"]["human_p2"] == "unsuitable"
    assert detail["purpose"]["predicted_p2"] == "suitable"
    assert service.jobs({"purpose": "p2"})["total"] == 0


def test_stale_p1_and_current_manual_p2_are_filtered_independently(service):
    uid = seed_jobs(service)[0]
    purpose_rows(service, [uid])
    with service.database.session() as session:
        row = session.get(JobPurposeAssessmentORM, uid)
        row.human_p1 = "suitable"
        row.human_p1_source_hash = "previous-source"
        row.human_p2 = "unsuitable"
        row.human_p2_source_hash = "source-v1"
        row.human_rubric_version = PURPOSE_RUBRIC_VERSION
        session.commit()
    assert service.jobs({"purpose": "p1"})["total"] == 0
    assert service.jobs({"purpose": "p2"})["total"] == 0
    assert service.jobs({"purpose": "review"})["total"] == 1


def test_rubric_staleness_is_independent_per_axis_and_matches_sql_queues(service):
    uid = seed_jobs(service)[0]
    purpose_rows(service, [uid])
    service.mutate("PATCH", f"/api/jobs/{uid}/purpose", {
        "human_p1": "suitable", "human_p2": "unsuitable",
        "expected_version": 1, "source_hash": "source-v1",
    })
    with service.database.session() as session:
        row = session.get(JobPurposeAssessmentORM, uid)
        row.human_p1_rubric_version = "previous-rubric"
        session.commit()
    detail = service.job(uid)
    assert detail["purpose"]["human_p1_stale"] is True
    assert detail["purpose"]["human_p2_stale"] is False
    assert service.jobs({"purpose": "p1"})["total"] == 0
    assert service.jobs({"purpose": "p2"})["total"] == 0
    assert service.jobs({"purpose": "review"})["total"] == 1
    service.mutate("PATCH", f"/api/jobs/{uid}/purpose", {
        "human_p2": "unsuitable", "human_note": "Only practice reviewed",
        "expected_version": detail["purpose"]["version"], "source_hash": "source-v1",
    })
    assert service.job(uid)["purpose"]["human_p1_stale"] is True
    assert service.jobs({"purpose": "p1"})["total"] == 0


def test_legacy_shared_rubric_is_fallback_only_until_axis_is_confirmed(service):
    uid = seed_jobs(service)[0]
    purpose_rows(service, [uid])
    with service.database.session() as session:
        row = session.get(JobPurposeAssessmentORM, uid)
        row.human_p1 = "suitable"
        row.human_p2 = "unsuitable"
        row.human_p1_source_hash = row.human_p2_source_hash = None
        row.human_source_hash = "source-v1"
        row.human_rubric_version = PURPOSE_RUBRIC_VERSION
        row.human_p1_rubric_version = row.human_p2_rubric_version = None
        session.commit()
    assert service.jobs({"purpose": "p1"})["total"] == 1
    assert service.jobs({"purpose": "p2"})["total"] == 0
    assert service.jobs({"purpose": "review"})["total"] == 0
    with service.database.session() as session:
        row = session.get(JobPurposeAssessmentORM, uid)
        row.human_p2_rubric_version = "explicit-old-axis-rubric"
        session.commit()
    assert service.jobs({"purpose": "p1"})["total"] == 1
    assert service.jobs({"purpose": "p2"})["total"] == 1
    assert service.jobs({"purpose": "review"})["total"] == 1


def test_component_mismatch_is_explained_and_not_ranked_as_current(service):
    uid = seed_jobs(service)[0]
    current = service.streams[0].version_components
    with service.database.session() as session:
        session.execute(update(JobProfileEvaluationORM).where(
            JobProfileEvaluationORM.job_uid == uid,
        ).values(version_components={**current, "annotation": "old-prompt"}))
        session.commit()
    listed = service.jobs({})["jobs"][0]
    assert listed["fit_score"] is None
    assert "version:annotation:changed" in listed["stale_reasons"]
    assert listed["classification_state"] == "current"
    assert service.jobs({"tier": "A"})["total"] == 0
    assert service.jobs({"tier": "unrated"})["total"] == 1


def test_review_evidence_sort_uses_explicit_support_not_hiring_probability(service):
    ids = seed_jobs(service, 3)
    with service.database.session() as session:
        for uid, evidence in zip(ids, ["blocked", "unconfirmed", "confirmed"], strict=True):
            session.execute(update(JobProfileEvaluationORM).where(
                JobProfileEvaluationORM.job_uid == uid,
            ).values(decision="review", eligibility_evidence_status=evidence))
        session.commit()
    assert [job["job_uid"] for job in service.jobs({"sort": "review-evidence"})["jobs"]] == (
        list(reversed(ids))
    )


def test_purpose_form_does_not_reconfirm_untouched_stale_axis():
    result = _run_javascript("purposeUpdatePayload({human_p1:'suitable',human_p2:'unknown',"
                             "version:5,prediction_source_hash:'new-source'},"
                             "'suitable','suitable','Interview practice')")
    assert result == {"human_p2": "suitable", "human_note": "Interview practice",
                      "expected_version": 5, "source_hash": "new-source"}
    confirmed = _run_javascript("purposeUpdatePayload({human_p1:'suitable',human_p2:'unknown',"
                                "version:5,prediction_source_hash:'new-source'},"
                                "'suitable','unknown','',{p1:true})")
    assert confirmed["human_p1"] == "suitable" and "human_p2" not in confirmed


def test_regional_discovery_variants_expose_only_source_facts_for_blind_review():
    result = _run_javascript("regionalVariantFacts([{title:'Analytics Manager',location:'Brazil',"
                             "url:'https://example.com/br',priority:99,decision:'accept',"
                             "human_label:3},{title:'Other',url:'javascript:alert(1)'}])")
    assert result == [{"title": "Analytics Manager", "location": "Brazil",
                       "url": "https://example.com/br"},
                      {"title": "Other", "location": "География не указана", "url": None}]
