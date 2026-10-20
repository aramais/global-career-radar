"""Independent machine and human suitability for work and interview practice."""

from __future__ import annotations

from sqlalchemy import update

from job_intake.annotation.input_contract import annotation_input_hash, input_digest
from job_intake.annotation.text import asserted_phrase_hits, source_units
from job_intake.models.job import EvaluatedJob, JobStatus
from job_intake.scoring.evidence import description_evidence
from job_intake.storage.models import JobEventORM, JobPurposeAssessmentORM

LEGACY_PURPOSE_RUBRIC_VERSION = "data-interview-practice-2026-10-19-v1"
PURPOSE_RUBRIC_VERSION = "data-interview-practice-2026-10-20-v2"
SUITABILITY_VALUES = {"suitable", "unsuitable", "unknown"}


class PurposeConflictError(ValueError):
    pass


def purpose_source_hash(record) -> str:
    return input_digest({"source_input": annotation_input_hash(record), "status": record.status})


def predict_purpose(item: EvaluatedJob) -> dict:
    """Use attributable tasks, never tier or score, and keep unknown explicit."""
    evidence = description_evidence(item.record)
    people = []
    for unit in source_units(item.record):
        if (
            unit["field"].startswith("description")
            and unit["section"]
            not in {
                "company",
                "requirements",
            }
            and asserted_phrase_hits(
                unit["text"],
                [
                    "partner with",
                    "collaborate",
                    "cross-functional",
                    "stakeholders",
                    "lead a team",
                    "manage a team",
                    "work with",
                    "direct reports",
                ],
            )
        ):
            people.append({"unit_id": unit["unit_id"], "quote": unit["text"]})
    analytical = evidence.analytical_mandate
    similar = analytical and (
        evidence.project_leadership
        or bool(people)
        or any(signal["concept"] == "experimentation" for signal in evidence.signals)
    )
    complete = item.record.source_metadata.get("description_complete") is not False
    explicit_mismatch = "explicit_role_mismatch" in item.evaluation.score_cap_reasons
    p2 = "suitable" if similar else "unsuitable" if complete and explicit_mismatch else "unknown"
    p2_reasons = ["similar_analytics_data_tasks" if similar else "task_similarity_unconfirmed"]
    if item.record.status == JobStatus.CLOSED:
        p2, p2_reasons = "unsuitable", ["vacancy_closed"]
    labels = item.record.annotation.get("labels_v3", {})
    labels_active = "labels_prompt" in item.version_components and isinstance(labels, dict)
    if labels_active and item.record.status != JobStatus.CLOSED:
        candidate = labels.get("p2_candidate")
        if candidate is True:
            p2, p2_reasons = "suitable", ["labels_v3_analytics_practice"]
        elif candidate is False and labels.get("status") == "labeled":
            p2, p2_reasons = "unsuitable", ["labels_v3_not_practice_candidate"]
    p1 = "unknown"
    reasons = []
    if item.record.status == JobStatus.CLOSED:
        p1, reasons = "unsuitable", ["vacancy_closed"]
    elif item.evaluation.eligibility_status == "ineligible":
        p1, reasons = "unsuitable", ["hiring_ineligible"]
    elif explicit_mismatch or evidence.reporting_core:
        p1, reasons = "unsuitable", ["role_mismatch"]
    elif (
        analytical
        and evidence.substantive_decision
        and evidence.project_leadership
        and people
        and item.evaluation.eligibility_status == "eligible"
        and not item.record.source_metadata.get("source_conflicts")
    ):
        p1, reasons = "suitable", ["role_tasks_and_hiring_supported"]
    else:
        reasons = ["role_tasks_or_hiring_unconfirmed"]
    return {
        "predicted_p1": p1,
        "predicted_p2": p2,
        "practice_suitability": p2,
        "rubric_version": (
            PURPOSE_RUBRIC_VERSION if labels_active else LEGACY_PURPOSE_RUBRIC_VERSION
        ),
        "prediction_source_hash": purpose_source_hash(item.record),
        "prediction_reason": {
            "p1": reasons,
            "p2": p2_reasons,
            "evidence": evidence.signals,
            "people_evidence": people,
        },
    }


def save_prediction(session, job_uid: str, item: EvaluatedJob) -> None:
    value = predict_purpose(item)
    row = session.get(JobPurposeAssessmentORM, job_uid)
    if row is None:
        session.add(JobPurposeAssessmentORM(job_uid=job_uid, **value))
    elif any(getattr(row, key) != val for key, val in value.items()):
        for key, val in value.items():
            setattr(row, key, val)
        row.version += 1


def serialize_purpose(row: JobPurposeAssessmentORM | None) -> dict:
    if row is None:
        return {
            "human_p1": "unknown",
            "human_p2": "unknown",
            "predicted_p1": "unknown",
            "predicted_p2": "unknown",
            "practice_suitability": "unknown",
            "version": 0,
            "rubric_version": PURPOSE_RUBRIC_VERSION,
            "human_stale": False,
        }
    names = (
        "human_p1",
        "human_p2",
        "predicted_p1",
        "predicted_p2",
        "practice_suitability",
        "rubric_version",
        "human_rubric_version",
        "human_note",
        "version",
        "prediction_reason",
        "prediction_source_hash",
    )
    stale = {
        name + "_stale": bool(
            getattr(row, name) != "unknown"
            and (
                (getattr(row, name + "_source_hash") or row.human_source_hash)
                != row.prediction_source_hash
                or (getattr(row, name + "_rubric_version") or row.human_rubric_version)
                != (row.rubric_version or PURPOSE_RUBRIC_VERSION)
            )
        )
        for name in ("human_p1", "human_p2")
    }
    return {
        **{name: getattr(row, name) for name in names},
        **stale,
        "human_stale": any(stale.values()),
    }


def update_human_purpose(session, job_uid: str, payload: dict) -> dict:
    allowed = {"human_p1", "human_p2", "human_note", "expected_version", "source_hash"}
    if set(payload) - allowed or not {"expected_version", "source_hash"} <= set(payload):
        raise ValueError("Purpose update requires source_hash and expected_version")
    version = payload["expected_version"]
    if type(version) is not int or version < 1:
        raise ValueError("Invalid purpose version; refresh the vacancy")
    row = session.get(JobPurposeAssessmentORM, job_uid)
    if (
        row is None
        or row.version != version
        or (payload["source_hash"] != row.prediction_source_hash)
    ):
        raise PurposeConflictError("Purpose or vacancy changed; refresh before saving")
    changes = {name: payload[name] for name in ("human_p1", "human_p2") if name in payload}
    if any(value not in SUITABILITY_VALUES for value in changes.values()):
        raise ValueError("Suitability must be suitable, unsuitable or unknown")
    note = payload.get("human_note", row.human_note)
    if not isinstance(note, str) or len(note) > 20000:
        raise ValueError("Invalid purpose note")
    before = serialize_purpose(row)
    values = {
        **changes,
        "human_note": note,
        "version": version + 1,
    }
    for name in changes:
        values[name + "_source_hash"] = row.prediction_source_hash
        values[name + "_rubric_version"] = row.rubric_version or PURPOSE_RUBRIC_VERSION
    if {"human_p1", "human_p2"} <= changes.keys():
        values["human_source_hash"] = row.prediction_source_hash
        values["human_rubric_version"] = row.rubric_version or PURPOSE_RUBRIC_VERSION
    result = session.execute(
        update(JobPurposeAssessmentORM)
        .where(
            JobPurposeAssessmentORM.job_uid == job_uid,
            JobPurposeAssessmentORM.version == version,
            JobPurposeAssessmentORM.prediction_source_hash == payload["source_hash"],
        )
        .values(**values),
        execution_options={"synchronize_session": False},
    )
    if result.rowcount != 1:
        raise PurposeConflictError("Purpose changed while saving")
    session.expire(row)
    session.add(
        JobEventORM(
            job_uid=job_uid,
            event_type="human_purpose_updated",
            payload={"previous": before, "current": serialize_purpose(row)},
        )
    )
    session.flush()
    return serialize_purpose(row)
