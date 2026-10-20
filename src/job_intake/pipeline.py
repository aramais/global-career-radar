from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

from sqlalchemy import select

from job_intake.adapters.factory import build_adapter
from job_intake.alerts.digest import build_daily_digest, build_instant_alert
from job_intake.alerts.telegram import TelegramNotifier
from job_intake.annotation.prefilter import VacancyPrefilter
from job_intake.annotation.progress import ProgressCallback, emit_progress
from job_intake.annotation.service import VacancyAnnotator, apply_annotation_constraints
from job_intake.annotation.text import role_description, source_hash
from job_intake.classification import RoleClassifier
from job_intake.config.settings import (
    AppConfig,
    SourceDefinition,
    load_app_config,
    load_yaml_mapping,
)
from job_intake.filtering import refresh_eligibility_status
from job_intake.labeling.application import apply_labels_hiring
from job_intake.models.job import EvaluatedJob, FilterDecision, JobRecord, JobStatus, JobTier
from job_intake.profiles import (
    SearchStream,
    load_streams,
    profile_query_order,
    with_labeling_versions,
    with_other_stream,
)
from job_intake.scoring.llm import BatchReranker, build_reranker, should_skip_llm
from job_intake.scoring.tiering import finalize_tier
from job_intake.storage.database import Database
from job_intake.storage.dedup import JobDeduplicator
from job_intake.storage.models import AlertOutboxORM
from job_intake.storage.queries import classification_is_current
from job_intake.storage.repository import (
    JobRepository,
    JobSourceConflictError,
    validate_job_uids,
)
from job_intake.storage.source_lifecycle import record_source_snapshot
from job_intake.storage.source_state import reconcile_source, record_from_saved
from job_intake.utils.logging import configure_logging
from job_intake.utils.text import stable_hash

LOGGER = logging.getLogger(__name__)


class JobIntakePipeline:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        common_rules = load_yaml_mapping(config.rules_path)
        self.streams = with_other_stream(
            load_streams(common_rules, load_yaml_mapping(config.search_profiles_path)),
            common_rules,
        )
        self.streams = with_labeling_versions(self.streams, config.labeling)
        self.classifier = RoleClassifier(self.streams, config.annotation)
        self.database = Database(config.database_url)
        self.database.create_schema()
        self.reranker = build_reranker(config.llm)
        self.batch_reranker = (
            BatchReranker(config.llm) if config.llm.enabled and config.llm.batch_enabled else None
        )
        self.telegram = TelegramNotifier(config.telegram)
        self.deduplicator = JobDeduplicator()
        self.annotator = VacancyAnnotator(config.annotation)
        self.prefilter = VacancyPrefilter(config.prefilter)
        from job_intake.labeling.tagger import VacancyTagger

        self.tagger = VacancyTagger(config.labeling, self.streams)
        self._label_ai_authorized = False
        self._label_include_archived = False

    @property
    def profile_versions(self) -> dict[str, str]:
        return {stream.id: stream.version for stream in self.streams}

    def _source_with_queries(self, source: SourceDefinition) -> SourceDefinition:
        params = dict(source.params)
        if source.type == "dailyremote" and params.get("use_profile_queries"):
            template = params.get(
                "search_url_template", "https://dailyremote.com/remote-jobs?search={query}"
            )
            keywords = profile_query_order(self.streams)
            params["search_urls"] = [template.replace("{query}", quote_plus(k)) for k in keywords]
        return replace(source, params=params)

    @staticmethod
    def _stats() -> dict[str, Any]:
        return {
            "ingested": 0,
            "persisted": 0,
            "evaluations": 0,
            "alerts": 0,
            "source_errors": 0,
            "source_closures": 0,
            "source_reopens": 0,
            "record_errors": 0,
            "alert_errors": 0,
            "llm_calls": 0,
            "llm_cache_hits": 0,
            "llm_skipped": 0,
            "llm_batched": 0,
            "llm_batch_pending": 0,
            "llm_batch_errors": 0,
            "annotation_extraction_calls": 0,
            "annotation_review_calls": 0,
            "annotation_cache_hits": 0,
            "annotation_errors": 0,
            "classification_calls": 0,
            "classification_cache_hits": 0,
            "classification_errors": 0,
            "prefilter_screen_calls": 0,
            "prefilter_verify_calls": 0,
            "prefilter_cache_hits": 0,
            "prefilter_errors": 0,
            "prefilter_suppressed": 0,
            "prefilter_full_review": 0,
            "prefilter_local_skipped": 0,
            "labels_calls": 0,
            "labels_cache_hits": 0,
            "labels_facts_reuse": 0,
            "labels_escalations": 0,
            "labels_errors": 0,
            "labels_gated": 0,
            "errors": [],
        }

    @staticmethod
    def _error(stats: dict[str, Any], kind: str, label: str, exc: Any) -> None:
        stats[kind] += 1
        message = f"{label}: {exc}"
        stats["errors"].append(message)
        LOGGER.warning("%s %s", kind, message)

    def run(self) -> dict[str, Any]:
        stats = self._stats()
        if self.config.llm.enabled:
            prompt = Path(self.config.llm.prompt_path).read_text(encoding="utf-8")
            self.reranker.prompt_template = prompt
            if self.batch_reranker is not None:
                self.batch_reranker.prompt_template = prompt
        pending_groups: dict[
            str, tuple[list[EvaluatedJob], dict[str, str], list[EvaluatedJob]]
        ] = {}
        for source in self.config.sources:
            if not source.enabled:
                continue
            adapter = None
            try:
                adapter = build_adapter(self._source_with_queries(source))
                records = adapter.fetch_jobs()
            except Exception as exc:
                self._error(stats, "source_errors", source.name, exc)
                self._record_source_health(adapter, source, stats, failed=True)
                continue
            for error in getattr(adapter, "errors", []):
                self._error(stats, "source_errors", source.name, error)
            stats["ingested"] += len(records)
            for record in records:
                try:
                    items, keys, pending = self._evaluate_record(record, stats, use_llm=True)
                    # Commit before any optional batch wait or notification.
                    self._save(items, keys, observed=True, queue_alert=not pending)
                    stats["persisted"] += 1
                    stats["evaluations"] += len(items)
                    job_uid = self.deduplicator.build_identity(record).job_uid
                    if pending:
                        pending_groups[job_uid] = (items, keys, pending)
                    else:
                        pending_groups.pop(job_uid, None)
                except Exception as exc:
                    from job_intake.labeling.budget import BudgetExceeded

                    if isinstance(exc, BudgetExceeded):
                        raise
                    self._error(stats, "record_errors", record.original_url or record.title, exc)
            self._record_source_health(adapter, source, stats)
        self._finish_batches(pending_groups, stats, queue_alert=True)
        self._dispatch_alerts(stats)
        LOGGER.info("run_complete %s", json.dumps(stats, ensure_ascii=False))
        return stats

    def _record_source_health(self, adapter, source, stats, *, failed=False):
        snapshots = list(getattr(adapter, "board_snapshots", []) or [])
        if not snapshots:
            snapshots = [{"source": source.name, "scope": "unknown",
                          "status": "failed" if failed else "partial" if getattr(
                              adapter, "errors", [],
                          ) else "success",
                          "snapshot_complete": False, "seen_urls": [], "seen_job_ids": []}]
        closures = []
        with self.database.session() as session:
            for snapshot in snapshots:
                closures.extend(record_source_snapshot(
                    session, snapshot, grace_days=float(source.params.get("missing_grace_days", 7)),
                    minimum_observations=int(source.params.get("missing_min_observations", 2)),
                ))
            session.commit()
        for record in closures:
            try:
                items, keys, _ = self._evaluate_record(record, stats, use_llm=False)
                self._save(items, keys, observed=False, queue_alert=False)
                counter = (
                    "source_closures" if record.status == JobStatus.CLOSED else "source_reopens"
                )
                stats[counter] += 1
            except Exception as exc:
                self._error(stats, "record_errors", "source closure", exc)

    def _finish_batches(
        self,
        pending_groups: dict[str, tuple[list[EvaluatedJob], dict[str, str], list[EvaluatedJob]]],
        stats: dict[str, Any],
        *,
        queue_alert: bool,
    ) -> None:
        """Resolve pending evaluations identically for intake and saved-data runs."""
        pending_llm = [item for _, _, pending in pending_groups.values() for item in pending]
        if pending_llm:
            try:
                self.batch_reranker.rerank_many(pending_llm)
                stats["llm_batched"] += len(pending_llm)
                batch_stats = getattr(self.batch_reranker, "last_stats", {})
                stats["llm_batch_pending"] += batch_stats.get("pending", 0)
                stats["llm_batch_errors"] += batch_stats.get("errors", 0)
            except Exception as exc:
                self._error(stats, "record_errors", "batch reranking", exc)
            for items, keys, _ in pending_groups.values():
                try:
                    self._finalize(items)
                    self._save(items, keys, observed=False, queue_alert=queue_alert)
                except Exception as exc:
                    self._error(stats, "record_errors", items[0].record.title, exc)

    def _cache_key(self, record: JobRecord, stream: SearchStream) -> str:
        prompt = self.reranker.prompt_template
        return stable_hash(
            json.dumps(
                {
                    "content": self.deduplicator.build_identity(record).content_hash,
                    "metadata": record.source_metadata,
                    "profile": stream.version,
                    "classification": self.classifier.version,
                    "role_label": stream.id,
                    "llm": asdict(self.config.llm),
                    "prompt": prompt,
                },
                sort_keys=True,
                ensure_ascii=False,
            )
        )

    def _evaluate_record(
        self, record: JobRecord, stats: dict[str, Any], *, use_llm: bool
    ) -> tuple[list[EvaluatedJob], dict[str, str], list[EvaluatedJob]]:
        items = []
        keys = {}
        pending = []
        with self.database.session() as session:
            repository = JobRepository(session)
            saved = repository.find_by_record(record)
            reconcile_source(record, saved)
            cached_evaluations = (
                repository.profile_evaluations_for_job(saved.job_uid)
                if saved is not None and use_llm and self.config.llm.enabled else {}
            )
            # Choose occupation before evaluating relevance or per-role conditions.
            before_classification = dict(self.classifier.stats)
            stream, classification = self.classifier.classify(
                record,
                use_ai=False,
                previous=saved.role_classification if saved is not None else None,
            )
            engine = stream.engine
            local_item = engine.apply(record)
            requested_ai = (
                use_llm and self.config.annotation.enabled and self.config.annotation.ai_enabled
                and not self._labels_mode()
            )
            if (
                stream.id == "other" and requested_ai
                and classification["method"] != "model"
                and local_item.evaluation.decision != FilterDecision.REJECT
            ):
                stream, classification = self.classifier.classify(record, use_ai=True)
                engine = stream.engine
                local_item = engine.apply(record)
            for name, value in self.classifier.stats.items():
                stats["classification_" + name] += value - before_classification[name]
            labels = None
            if self.config.labeling.enabled and self.config.labeling.mode != "legacy":
                before_labels = dict(self.tagger.stats)
                if (use_llm and self.config.labeling.ai_enabled
                        and self._label_ai_authorized):
                    labels = self.tagger.label(
                        record, local_item.evaluation, classification, use_ai=True,
                    )
                else:
                    labels = self.tagger.relabel_offline(
                        record, local_item.evaluation, classification,
                    )
                for name, value in self.tagger.stats.items():
                    stats["labels_" + name] += value - before_labels[name]
                if self._labels_mode() and labels.role_label and labels.role_label != stream.id:
                    stream = self._stream_by_id(labels.role_label)
                    engine = stream.engine
                    local_item = engine.apply(record)
                    classification = {
                        **classification, "label": stream.id, "method": "labels_v3",
                        "labels_cache_key": labels.cache_key,
                    }
            skip_two_pass = labels is not None and self._labels_mode()
            route = None
            if requested_ai and local_item.evaluation.decision == FilterDecision.REJECT:
                blockers = sorted(set(local_item.evaluation.blocker_signals))
                route = {
                    "routing": "local_reject", "decision": "clearly_irrelevant",
                    "reason_code": "hard_blocker",
                    "reason": "The classified role has explicit hard blockers.",
                    "blockers": blockers, "source_hash": source_hash(record),
                }
                stats["prefilter_local_skipped"] += 1
                emit_progress(
                    self.annotator.progress, "prefilter_local_skip", routing="local_reject",
                    reason_code="hard_blocker", blockers=len(blockers),
                )
            elif self.config.prefilter.enabled and not skip_two_pass:
                if requested_ai:
                    before = dict(self.prefilter.stats)
                    route = self.prefilter.route(record)
                    for name, value in self.prefilter.stats.items():
                        stats["prefilter_" + name] += value - before[name]
                else:
                    # Offline re-evaluation retains only a revalidated current route.
                    route = self.prefilter.cached_route(record)
                if route is not None:
                    counter = (
                        "prefilter_suppressed" if route["routing"] == "suppressed"
                        else "prefilter_full_review"
                    )
                    stats[counter] += 1
            suppressed = route is not None and route["routing"] == "suppressed"
            before = dict(self.annotator.stats)
            self.annotator.annotate(
                record, use_ai=use_llm and not skip_two_pass and not suppressed
                and not (route is not None and route["routing"] == "local_reject"),
            )
            # Routing is deliberately outside source claims and their independent review.
            record.annotation.pop("prefilter", None)
            if labels is not None:
                # The legacy annotator replaces its object; preserve the independent result.
                record.annotation["labels_v3"] = asdict(labels)
            if route is not None:
                record.annotation["prefilter"] = route
            for name, value in self.annotator.stats.items():
                stats["annotation_" + name] += value - before[name]
            item = local_item
            apply_annotation_constraints(record, item.evaluation, engine)
            item.profile_id, item.profile_name = stream.id, stream.name
            item.profile_version, item.profile_context = stream.version, stream.context
            item.version_components = dict(stream.version_components)
            item.role_classification = classification
            if labels is not None:
                item.evaluation.relevance = labels.relevance
                item.evaluation.labels_status = labels.status
                item.evaluation.labels_version = (
                    labels.prompt_version + ":" + labels.aggregation_version
                )
            if stream.id == "other":
                # No relevance estimate against an unrelated occupation.
                item.evaluation.fit_score = item.evaluation.deterministic_score = 0.0
                item.evaluation.bridge_role = False
                item.evaluation.fit_reason = classification["reason"]
                if item.evaluation.decision != FilterDecision.REJECT:
                    item.evaluation.decision = FilterDecision.REVIEW
                item.evaluation.risks.append("role_classification:needs_review")
                item.evaluation.audit_log.append("Outside role taxonomy; relevance unscored.")
                items.append(item)
                self._finalize(items)
                return items, keys, pending
            stream.scorer.score(
                record.source,
                record.company,
                record.title,
                role_description(record),
                item.evaluation,
                record=record,
            )
            if suppressed and item.evaluation.decision != FilterDecision.REJECT:
                # Retain the source and CRM linkage; a routing verdict isn't an
                # independently reviewed final rejection or a hiring fact.
                item.evaluation.decision = FilterDecision.REVIEW
                item.evaluation.risks = sorted(set(
                    item.evaluation.risks + ["prefilter:" + route["reason_code"]]
                ))
                item.evaluation.bridge_role = False
                item.evaluation.fit_score = 0.0
                item.evaluation.deterministic_score = 0.0
                item.evaluation.fit_reason = "Preliminary filter: " + route["reason_code"]
                item.evaluation.audit_log.append(
                    "Independent preliminary checks agree on a mismatch; "
                    "full AI review skipped. Source retained for manual review."
                )
            if (
                use_llm
                and not skip_two_pass
                and self.config.llm.enabled
                and not suppressed
                and not (self.config.annotation.enabled and self.config.annotation.ai_enabled)
                and item.evaluation.decision != FilterDecision.REJECT
            ):
                key = self._cache_key(record, stream)
                keys[stream.id] = key
                cached = cached_evaluations.get(stream.id)
                if (
                    cached is not None
                    and cached.semantic_score is not None
                    and cached.llm_cache_key == key
                ):
                    item.evaluation.semantic_score = cached.semantic_score
                    item.evaluation.fit_score += cached.semantic_score
                    if item.evaluation.raw_fit_score is not None:
                        item.evaluation.raw_fit_score += cached.semantic_score
                    item.evaluation.bridge_role |= cached.bridge_role
                    item.evaluation.fit_reason = cached.fit_reason
                    item.evaluation.risks = sorted(set(item.evaluation.risks + cached.risks))
                    item.evaluation.audit_log.append("Semantic score reused for this profile.")
                    stats["llm_cache_hits"] += 1
                elif self.config.llm.skip_high_confidence and should_skip_llm(
                    item.evaluation,
                    stream.scoring.threshold_a,
                    stream.scoring.threshold_b,
                    self.config.llm.high_confidence_margin,
                ):
                    stats["llm_skipped"] += 1
                    item.evaluation.audit_log.append("LLM skipped: deterministic confidence.")
                elif self.batch_reranker is not None:
                    pending.append(item)
                else:
                    if os.getenv(self.config.llm.api_key_env):
                        stats["llm_calls"] += 1
                    item = self.reranker.rerank(item)
            else:
                item.evaluation.audit_log.append("Deterministic evaluation; no AI request.")
            items.append(item)
        self._finalize(items)
        return items, keys, pending

    def _finalize(self, items: list[EvaluatedJob]) -> None:
        by_id = {s.id: s for s in self.streams}
        for item in items:
            if item.record.source_metadata.get("source_conflicts"):
                if item.evaluation.decision != FilterDecision.REJECT:
                    item.evaluation.decision = FilterDecision.REVIEW
                if item.evaluation.fit_max_tier != JobTier.C:
                    item.evaluation.fit_max_tier = JobTier.B
                if "source_update_unverified" not in item.evaluation.risks:
                    item.evaluation.risks.append("source_update_unverified")
                    item.evaluation.audit_log.append(
                        "Incomplete update differs from saved details; both retained for review."
                    )
            scoring = by_id[item.profile_id].scoring
            refresh_eligibility_status(item.evaluation)
            if self._labels_mode():
                apply_labels_hiring(
                    item.record, item.evaluation, item.record.annotation.get("labels_v3"),
                )
            finalize_tier(
                item, scoring.threshold_a, scoring.threshold_b,
                tier_source=self._effective_tier_source(item.record.annotation.get("labels_v3")),
            )

    def _labels_mode(self) -> bool:
        return self.config.labeling.enabled and self.config.labeling.mode == "labels"

    def _stream_by_id(self, role_id: str) -> SearchStream:
        for stream in self.streams:
            if stream.id == role_id:
                return stream
        raise ValueError("Labels returned an unknown or disabled role")

    def _effective_tier_source(self, labels) -> str:
        if not isinstance(labels, dict) and labels is not None:
            labels = asdict(labels)
        if (
            self._labels_mode() and self.config.labeling.tier_source == "relevance"
            and isinstance(labels, dict) and labels.get("rubric_status") == "confirmed"
            and labels.get("status") in {"labeled", "gated"}
            and type(labels.get("relevance")) is int and labels["relevance"] in range(4)
        ):
            return "relevance"
        return "score"

    def _save(
        self, items: list[EvaluatedJob], keys: dict[str, str], *, observed: bool, queue_alert: bool
    ) -> None:
        with self.database.session() as session:
            repository = JobRepository(
                session, alert_dedup_hours=self.config.telegram.alert_dedup_hours
            )
            record = items[0].record
            labels = record.annotation.get("labels_v3")
            if (self._labels_mode() and isinstance(labels, dict)
                    and labels.get("status") in {"error", "rules_only"}):
                saved = repository.find_by_record(record)
                if saved is not None:
                    if (record._source_reconciled
                            and record._expected_source_revision != saved.source_revision):
                        raise JobSourceConflictError(
                            "Source changed after reconciliation; evaluate it again"
                        )
                    current_hash = source_hash(record_from_saved(saved))
                    if current_hash == labels.get("source_hash") == source_hash(record):
                        revision = saved.source_revision
                        if not repository.record_label_attempt(
                            saved.job_uid, labels, expected_source_hash=current_hash,
                            expected_source_revision=revision,
                            include_archived=self._label_include_archived or observed,
                            observed=observed,
                        ):
                            raise ValueError("Selected vacancy disappeared or was archived")
                        if observed:
                            from job_intake.storage.aliases import register_aliases

                            register_aliases(session, record, saved.job_uid)
                        session.commit()
                        record._expected_source_revision = revision
                        return
                    if repository.record_collection_label_attempt(
                        saved.job_uid, record, labels,
                        expected_source_revision=saved.source_revision,
                        include_archived=self._label_include_archived or observed,
                        observed=observed,
                    ):
                        session.commit()
                        record._expected_source_revision = saved.source_revision
                        return
            repository.upsert_evaluations(
                items,
                keys,
                observed=observed,
                retain_labels_on_fallback=(
                    self.config.labeling.enabled and self.config.labeling.mode == "shadow"
                ),
                queue_alert=(
                    queue_alert
                    and self.config.telegram.enabled
                    and self.config.telegram.instant_a_tier
                ),
            )
            session.flush()
            saved = repository.find_by_record(items[0].record)
            revision = saved.source_revision
            session.commit()
            items[0].record._expected_source_revision = revision

    def _dispatch_alerts(self, stats: dict[str, Any]) -> None:
        if not self.config.telegram.enabled or not self.config.telegram.instant_a_tier:
            return
        with self.database.session() as session:
            ids = list(
                session.scalars(select(AlertOutboxORM.id).where(AlertOutboxORM.status == "pending"))
            )
        for outbox_id in ids:
            try:
                with self.database.session() as session:
                    outbox = session.get(AlertOutboxORM, outbox_id)
                    if outbox is None or outbox.status != "pending":
                        continue
                    job = outbox.job
                    if (
                        job.archived_at is not None
                        or job.tier != "A"
                        or job.filter_decision == "reject"
                        or job.best_profile_id not in self.profile_versions
                        or not any(
                            p.profile_id == job.best_profile_id
                            and p.profile_version == self.profile_versions[p.profile_id]
                            and p.content_hash == job.content_hash
                            and classification_is_current(job, p)
                            for p in job.profile_evaluations
                        )
                    ):
                        outbox.status = "cancelled"
                        session.commit()
                        continue
                    outbox.attempts += 1
                    outbox.message = build_instant_alert(job)
                    session.commit()
                    # Vacancy and outbox have already been committed.
                    try:
                        sent = self.telegram.send(outbox.message)
                    except Exception as exc:
                        sent = False
                        outbox.last_error = str(exc)
                    if sent:
                        JobRepository(session).mark_alert_sent(
                            job.job_uid, JobTier.A, outbox.channel, outbox.message
                        )
                        outbox.status = "sent"
                        outbox.last_error = None
                        stats["alerts"] += 1
                    else:
                        outbox.last_error = outbox.last_error or "Delivery unavailable"
                        self._error(stats, "alert_errors", job.job_uid, outbox.last_error)
                    session.commit()
            except Exception as exc:
                self._error(stats, "alert_errors", str(outbox_id), exc)

    def reevaluate_saved(
        self, *, use_ai: bool = False, limit: int | None = None,
        progress: ProgressCallback | None = None,
        job_uids: list[str] | None = None,
        include_archived: bool = False,
    ) -> dict[str, Any]:
        """Apply profiles offline; progress does not change evaluations or cache keys."""
        selection = validate_job_uids(job_uids)
        if selection is not None and limit is not None:
            raise ValueError("Use either selected job UIDs or limit, not both")
        previous_progress = self.annotator.progress
        previous_prefilter_progress = self.prefilter.progress
        previous_classifier_progress = self.classifier.client.progress
        previous_tagger_progress = self.tagger.progress
        previous_include_archived = self._label_include_archived
        self.annotator.set_progress(progress)
        self.prefilter.set_progress(progress)
        self.classifier.client.progress = progress
        self.tagger.set_progress(progress)
        self._label_include_archived = include_archived
        started = time.monotonic()
        emit_progress(progress, "run_start")
        try:
            selected = {"job_uids": selection} if selection is not None else {}
            if include_archived:
                selected["include_archived"] = True
            result = self._reevaluate_saved(
                use_ai=use_ai, limit=limit, progress=progress, **selected,
            )
            emit_progress(
                progress, "run_complete", total=result["persisted"] + result["record_errors"],
                persisted=result["persisted"], elapsed=time.monotonic() - started,
                **{key: value for key, value in result.items()
                   if key.startswith(("prefilter_", "labels_"))},
            )
            return result
        finally:
            self.annotator.set_progress(previous_progress)
            self.prefilter.set_progress(previous_prefilter_progress)
            self.classifier.client.progress = previous_classifier_progress
            self.tagger.set_progress(previous_tagger_progress)
            self._label_include_archived = previous_include_archived

    def _reevaluate_saved(
        self, *, use_ai: bool, limit: int | None, progress: ProgressCallback | None,
        job_uids: list[str] | None = None,
        include_archived: bool = False,
    ) -> dict[str, Any]:
        """Apply profiles to saved data; AI is explicit opt-in, notifications stay disabled."""
        stats = self._stats()
        pending_groups = {}
        with self.database.session() as session:
            selected = {"job_uids": job_uids} if job_uids is not None else {}
            jobs = JobRepository(session).list_jobs(
                limit=limit, archived=None if include_archived else False, **selected,
            )
            if job_uids is not None:
                by_uid = {job.job_uid: job for job in jobs}
                if set(job_uids) - set(by_uid):
                    raise ValueError(
                        "Requested jobs were not selected; check IDs and archive state"
                    )
                # A resumed run follows the frozen selection, even after saves reorder updated_at.
                jobs = [by_uid[uid] for uid in job_uids]
            records = [
                JobRecord(
                    source=j.source,
                    company=j.company,
                    title=j.title,
                    original_url=j.original_url,
                    apply_url=j.apply_url,
                    source_job_id=j.source_job_id,
                    posted_at=j.posted_at,
                    location_text=j.location_text,
                    remote_text=j.remote_text,
                    employment_type=j.employment_type,
                    salary_text=j.salary_text,
                    timezone_text=j.timezone_text,
                    description_raw=j.description_raw,
                    description_clean=j.description_clean,
                    status=JobStatus(j.status),
                    source_metadata=j.source_metadata,
                    annotation=j.annotation or {},
                )
                for j in jobs
            ]
        emit_progress(progress, "jobs_loaded", total=len(records))
        for index, record in enumerate(records, 1):
            started = time.monotonic()
            annotation_errors_before = stats["annotation_errors"]
            prefilter_errors_before = stats["prefilter_errors"]
            labels_errors_before = stats["labels_errors"]
            emit_progress(
                progress, "job_start", index=index, total=len(records),
                company=record.company, title=record.title,
            )
            try:
                items, keys, pending = self._evaluate_record(record, stats, use_llm=use_ai)
                self._save(items, keys, observed=False, queue_alert=False)
                stats["persisted"] += 1
                stats["evaluations"] += len(items)
                emit_progress(
                    progress, "job_complete", evaluations=len(items),
                    elapsed=time.monotonic() - started,
                    errors=(stats["annotation_errors"] - annotation_errors_before
                            + stats["prefilter_errors"] - prefilter_errors_before
                            + stats["labels_errors"] - labels_errors_before),
                    annotation_errors=stats["annotation_errors"] - annotation_errors_before,
                    prefilter_errors=stats["prefilter_errors"] - prefilter_errors_before,
                    labels_errors=stats["labels_errors"] - labels_errors_before,
                )
                if pending:
                    job_uid = self.deduplicator.build_identity(record).job_uid
                    pending_groups[job_uid] = (items, keys, pending)
            except Exception as exc:
                from job_intake.labeling.budget import BudgetExceeded

                if isinstance(exc, BudgetExceeded):
                    raise
                emit_progress(progress, "job_error", error_type=type(exc).__name__)
                self._error(stats, "record_errors", record.title, exc)
        self._finish_batches(pending_groups, stats, queue_alert=False)
        return stats

    def label_saved(
        self, *, use_ai: bool = False, limit: int | None = None,
        job_uids: list[str] | None = None, offline: bool = False,
        include_archived: bool = False, budget=None, ledger=None,
        progress: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        """Run the labels-mode path once, committing each completed vacancy.

        Shadow writes have a dedicated repository operation and should not call
        this method: their legacy evaluation must remain exactly as saved.
        """
        if not self._labels_mode():
            raise ValueError("label_saved requires labeling.enabled and mode=labels")
        if offline and use_ai:
            raise ValueError("offline labeling cannot use AI")
        if use_ai and budget is None:
            raise ValueError("AI labels require an explicit budget")
        previous_budget, previous_ledger = self.tagger.budget, self.tagger.ledger
        previous_config = self.config.labeling.ai_enabled
        previous_authorized = self._label_ai_authorized
        self.tagger.budget, self.tagger.ledger = budget, ledger
        self.config.labeling.ai_enabled = use_ai and not offline
        self._label_ai_authorized = use_ai and budget is not None
        try:
            return self.reevaluate_saved(
                use_ai=use_ai and not offline, limit=limit, job_uids=job_uids,
                include_archived=include_archived, progress=progress,
            )
        finally:
            self.tagger.budget, self.tagger.ledger = previous_budget, previous_ledger
            self.config.labeling.ai_enabled = previous_config
            self._label_ai_authorized = previous_authorized

    def send_daily_digest(self, hours: int = 24) -> str:
        with self.database.session() as session:
            jobs = JobRepository(session).recent_jobs_for_digest(
                hours=hours, active_profile_versions=self.profile_versions
            )
            message = build_daily_digest(jobs)
        if self.config.telegram.daily_digest_enabled:
            self.telegram.send(message)
        return message

    def export_csv(
        self,
        output_path: Path,
        *,
        profile_id: str | None = None,
        include_rejected: bool = False,
        shortlist: bool = False,
    ) -> Path:
        self._validate_profile(profile_id)
        with self.database.session() as session:
            return JobRepository(session).export_shortlisted_csv(
                output_path,
                wide=not shortlist,
                include_rejected=include_rejected,
                profile_id=profile_id,
                active_profile_versions=self.profile_versions,
            )

    def render_html(
        self, output_path: Path, limit: int = 100, profile_id: str | None = None
    ) -> Path:
        from job_intake.review.report import render_html_report

        self._validate_profile(profile_id)
        with self.database.session() as session:
            jobs = JobRepository(session).list_jobs(limit=limit, archived=False)
            return render_html_report(
                jobs,
                output_path,
                profile_id=profile_id,
                active_profile_ids=list(self.profile_versions),
                active_profile_versions=self.profile_versions,
                active_profile_names={s.id: s.name for s in self.streams},
            )

    def _validate_profile(self, profile_id: str | None) -> None:
        if profile_id is not None and profile_id not in self.profile_versions:
            raise ValueError(f"Unknown or disabled profile: {profile_id}")

    def prune(
        self, older_than_days: int, tiers: tuple[str, ...] = ("C",), do_vacuum: bool = False
    ) -> int:
        with self.database.session() as session:
            removed = JobRepository(session).prune_low_tier(older_than_days, tiers)
            session.commit()
        if do_vacuum:
            self.database.vacuum()
        return removed

    def add_feedback(self, job_uid: str, label: str, note: str = "") -> None:
        with self.database.session() as session:
            JobRepository(session).add_feedback(job_uid, label, note)
            session.commit()


def build_pipeline(config_path: str | Path) -> JobIntakePipeline:
    config = load_app_config(config_path)
    configure_logging(config.log_level)
    return JobIntakePipeline(config)
