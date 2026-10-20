"""Read-only v3 exports and narrowly scoped shadow writes, without legacy AI."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, replace
from pathlib import Path

from job_intake.annotation.benchmark import read_jsonl
from job_intake.annotation.cohorts import write_new_text
from job_intake.annotation.eval_runner import read_saved_records
from job_intake.annotation.frozen_eval import load_bundle, record_from_snapshot
from job_intake.annotation.text import source_hash
from job_intake.classification import RoleClassifier
from job_intake.config.settings import AppConfig, load_yaml_mapping
from job_intake.filtering import refresh_eligibility_status
from job_intake.labeling.application import apply_labels_hiring
from job_intake.labeling.predictions import check_prediction_rows
from job_intake.labeling.schema import PREDICTION_SCHEMA, prediction_schema
from job_intake.models.job import FilterDecision, JobTier
from job_intake.profiles import load_streams, with_other_stream
from job_intake.scoring.tiering import finalize_tier


def labeling_streams(settings: AppConfig):
    rules = load_yaml_mapping(settings.rules_path)
    return with_other_stream(
        load_streams(rules, load_yaml_mapping(settings.search_profiles_path)),
        rules,
    )


def export_records(
    settings: AppConfig,
    *,
    bundle: Path | None = None,
    gold: Path | None = None,
    job_uids: list[str] | None = None,
    limit: int | None = None,
    include_archived: bool = False,
    profile_ids: list[str] | None = None,
):
    """Bundle/gold snapshots never supply human answers to the model."""
    if bundle is not None:
        rows = load_bundle(bundle)["rows"]
        result = [(row["job_uid"], record_from_snapshot(row["source"])) for row in rows]
        if len({uid for uid, _ in result}) != len(result):
            raise ValueError("Frozen bundle contains duplicate job IDs")
        return result
    if gold is not None:
        from job_intake.labeling.gold_v3 import validate_gold_v3

        cases = read_jsonl(gold)
        if len({case.get("job_uid") for case in cases}) != len(cases):
            raise ValueError("V3 gold contains duplicate job IDs")
        records, unresolved = {}, []
        for case in cases:
            snapshot = record_from_snapshot(case["source"])
            if source_hash(snapshot) == case.get("source_hash"):
                records[case["job_uid"]] = snapshot
            else:
                unresolved.append(case)
        history = {}
        if unresolved:
            records.update(_saved_gold_sources(settings, unresolved))
            from job_intake.annotation.source_compat import read_source_history

            history = read_source_history(settings.database_url, unresolved)
        result = [(case["job_uid"], records[case["job_uid"]]) for case in cases]
        validated = validate_gold_v3(result, cases, profile_ids=profile_ids,
                                     source_history=history)
        if not validated["valid"]:
            raise ValueError("Invalid v3 gold snapshots; review gold validation before export")
        return result
    _, rows, findings = read_saved_records(
        settings.database_url,
        limit=limit,
        job_uids=job_uids,
        include_archived=include_archived,
    )
    if any(
        finding["severity"] == "error" for problems in findings.values() for finding in problems
    ):
        raise ValueError("Saved source contains invalid fields; export refused")
    return rows


def _saved_gold_sources(settings: AppConfig, cases: list[dict]) -> dict:
    """Resolve gold pins whose whitelisted snapshot cannot reproduce the full source hash.

    Gold sampling and migration keep a reduced source view, while ``source_hash``
    covers the full saved record. Such rows use the read-only saved source, exactly
    as a frozen bundle would: the pinned hash or a proven collection-only change.
    The record is rebuilt from its source snapshot, so no saved annotation is used.
    """
    from job_intake.annotation.eval_runner import sqlite_path
    from job_intake.annotation.frozen_eval import source_snapshot
    from job_intake.annotation.source_compat import (
        check_source_compatibility,
        read_source_history,
    )

    first = cases[0]["job_uid"]
    try:
        available = sqlite_path(settings.database_url).is_file()
    except ValueError:
        available = False
    if not available:
        raise ValueError(
            f"Gold snapshot for {first} does not reproduce its source_hash and no SQLite "
            "source is available; freeze a bundle from the saved sources"
        )
    _, saved, findings = read_saved_records(
        settings.database_url, limit=None, include_archived=True,
    )
    saved = dict(saved)
    history = read_source_history(settings.database_url, cases)
    result = {}
    for case in cases:
        uid = case["job_uid"]
        record = saved.get(uid)
        if record is None or any(
            finding["severity"] == "error" and finding["code"] != "invalid_annotation_json"
            for finding in findings.get(uid, [])
        ):
            raise ValueError(
                f"Gold snapshot for {uid} does not reproduce its source_hash and SQLite has "
                "no valid saved source; freeze a bundle or restore the vacancy"
            )
        if not check_source_compatibility(case, record, history=history.get(uid))["compatible"]:
            raise ValueError(
                f"Gold source_hash for {uid} differs from the saved source facts; review the "
                "labels or freeze a reviewed bundle"
            )
        result[uid] = record_from_snapshot(source_snapshot(record))
    return result


def _prediction(uid, record, result, item, stream, settings):
    values = asdict(result)
    allowed = prediction_schema()["properties"]
    row = {key: value for key, value in values.items() if key in allowed}
    if settings.labeling.mode == "labels":
        if record.source_metadata.get("source_conflicts"):
            if item.evaluation.decision != FilterDecision.REJECT:
                item.evaluation.decision = FilterDecision.REVIEW
            if item.evaluation.fit_max_tier != JobTier.C:
                item.evaluation.fit_max_tier = JobTier.B
            if "source_update_unverified" not in item.evaluation.risks:
                item.evaluation.risks.append("source_update_unverified")
        refresh_eligibility_status(item.evaluation)
        apply_labels_hiring(record, item.evaluation, values)
        tier_source = "score"
        if (
            settings.labeling.tier_source == "relevance"
            and result.rubric_status == "confirmed"
            and result.status in {"labeled", "gated"}
            and result.source_hash == source_hash(record)
            and type(result.relevance) is int and result.relevance in range(4)
        ):
            item.evaluation.relevance = result.relevance
            tier_source = "relevance"
        finalize_tier(
            item, stream.scoring.threshold_a, stream.scoring.threshold_b, tier_source=tier_source,
        )
    model = settings.labeling.escalate_model if result.escalated else settings.labeling.tag_model
    row.update(
        schema=PREDICTION_SCHEMA,
        job_uid=uid,
        source_hash=source_hash(record),
        labeler="llm:" + model if result.status == "labeled" else "rules:labels-v3",
        variant="labels",
        decision=str(item.evaluation.decision),
        tier=str(item.evaluation.tier),
        fit_score=item.evaluation.fit_score,
    )
    return row


def label_records(
    settings: AppConfig,
    rows,
    *,
    use_ai: bool = False,
    offline: bool = False,
    concurrency: int = 1,
    budget=None,
    ledger=None,
    progress=None,
    persist=None,
):
    """Keep every completed job when the shared per-attempt budget is exhausted."""
    from job_intake.labeling.budget import BudgetExceeded
    from job_intake.labeling.tagger import VacancyTagger

    streams = labeling_streams(settings)
    config = replace(settings.labeling, enabled=True)

    # One isolated tagger/classifier per task; only budget and ledger are shared.
    def label_one(pair):
        uid, record = pair
        classifier = RoleClassifier(streams, replace(settings.annotation, ai_enabled=False))
        stream, classification = classifier.classify(record, use_ai=False)
        item = stream.engine.apply(record)
        if stream.id != "other":
            stream.scorer.score(
                record.source,
                record.company,
                record.title,
                record.description_clean or record.description_raw,
                item.evaluation,
                record=record,
            )
            finalize_tier(item, stream.scoring.threshold_a, stream.scoring.threshold_b)
        else:
            item.evaluation.tier = JobTier.C
        tagger = VacancyTagger(config, streams, progress=progress, budget=budget, ledger=ledger)
        result = (
            tagger.relabel_offline(record, item.evaluation, classification)
            if offline
            else tagger.label(record, item.evaluation, classification, use_ai=use_ai)
        )
        if settings.labeling.mode == "labels" and result.role_label != stream.id:
            destination = next((entry for entry in streams if entry.id == result.role_label), None)
            if destination is not None:
                stream = destination
                item = stream.engine.apply(record)
                if stream.id != "other":
                    stream.scorer.score(
                        record.source,
                        record.company,
                        record.title,
                        record.description_clean or record.description_raw,
                        item.evaluation,
                        record=record,
                    )
                    finalize_tier(item, stream.scoring.threshold_a, stream.scoring.threshold_b)
                else:
                    item.evaluation.tier = JobTier.C
        if persist is not None:
            persist(uid, record, asdict(result))
        return _prediction(uid, record, result, item, stream, settings)

    completed = {}
    stopped = False
    if concurrency == 1:
        for pair in rows:
            try:
                completed[pair[0]] = label_one(pair)
            except BudgetExceeded:
                stopped = True
                break
    else:
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = {executor.submit(label_one, pair): pair[0] for pair in rows}
            for future in as_completed(futures):
                if future.cancelled():
                    continue
                try:
                    completed[futures[future]] = future.result()
                except BudgetExceeded:
                    stopped = True
                    for pending in futures:
                        pending.cancel()
    predictions = [completed[uid] for uid, _ in rows if uid in completed]
    check_prediction_rows(predictions, profile_ids=[stream.id for stream in streams])
    return predictions, stopped


def publish_predictions(output: Path, predictions: list[dict]) -> None:
    content = "".join(
        json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n" for row in predictions
    )
    write_new_text(output, content)


def shadow_writer(settings: AppConfig, *, include_archived: bool = False):
    """Update just v3 fields; keep legacy evaluation, CRM decisions and alerts."""
    from sqlalchemy import select

    from job_intake.storage.database import Database
    from job_intake.storage.models import JobORM
    from job_intake.storage.repository import JobRepository

    database = Database(settings.database_url)
    database.create_schema()

    def persist(uid, record, labels):
        with database.session() as session:
            repository = JobRepository(session)
            saved = session.scalar(select(JobORM).where(JobORM.job_uid == uid))
            accepted = (saved.annotation or {}).get("labels_v3", {}) if saved is not None else {}
            preserve = (
                labels.get("status") in {"error", "rules_only"}
                and accepted.get("status") == "labeled"
                and accepted.get("source_hash") == source_hash(record)
            )
            writer = repository.record_label_attempt if preserve else repository.update_labels
            updated = writer(
                uid,
                labels,
                expected_source_hash=source_hash(record),
                include_archived=include_archived,
            )
            if not updated:
                raise ValueError("Selected vacancy disappeared or was archived during labeling")
            session.commit()

    return persist
