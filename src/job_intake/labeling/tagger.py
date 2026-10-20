"""Source-grounded v3 labeling, conservative escalation and reusable fact caches."""

from __future__ import annotations

import copy
import json
import os
import tempfile
import threading
import time
import uuid
from contextvars import ContextVar
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from job_intake.annotation.ai import AnnotationModelClient, error_diagnostic
from job_intake.annotation.input_contract import annotation_input_hash, input_digest, model_spec
from job_intake.annotation.progress import emit_progress
from job_intake.annotation.text import source_hash, source_identity, source_units
from job_intake.labeling.aggregate import (
    AGGREGATION_VERSION,
    escalation_triggers,
    final_relevance,
    gate_outcome,
    p2_candidate,
    rules_relevance,
)
from job_intake.labeling.budget import BudgetExceeded
from job_intake.labeling.gates import free_gates
from job_intake.labeling.prompt import build_prompt, prompt_version
from job_intake.labeling.rubric import load_precedents, load_rubric, precedents_hash
from job_intake.labeling.schema import LABELS_SCHEMA, LabelResult
from job_intake.labeling.validate import (
    LabelValidationError,
    is_validation_warning,
    unspecified_tags,
    validate_tags,
)
from job_intake.profiles import role_taxonomy, taxonomy_version

_ACTIVE = ContextVar("labeling_attempt", default=None)


class TaggerStageClients:
    def __init__(self, config):
        self.tag = AnnotationModelClient(config.resolved_stage("tag"))
        self.escalate = AnnotationModelClient(config.resolved_stage("escalate"))

    def set_progress(self, progress):
        for stage in ("tag", "escalate"):
            getattr(self, stage).progress = lambda event, stage=stage: progress(
                {**event, "stage": stage}
            )

    def generate_stage(self, stage, model, prompt):
        return getattr(self, stage).generate(model, prompt)


def _raw(normalized):
    tags = copy.deepcopy(normalized["tags"])
    tags["work_mode"].setdefault("office_location", "")
    tags["domain_fit"].setdefault("domain", "")
    tags["domain_fit"].setdefault("required_domain", None)
    return {
        **tags,
        "reason": "",
        **{key: normalized[key] for key in ("role_label", "relevance", "confidence")},
    }


def _block_values(tags):
    return {
        name: tag.get("value")
        for name, tag in tags.items()
        if tag.get("basis") == "explicit"
        and (
            name == "hiring_eligibility"
            and tag.get("value") == "restricted_elsewhere"
            or name == "work_mode"
            and tag.get("value") == "office_elsewhere"
            or name == "working_language"
            and tag.get("value") in {"portuguese_only", "other_language_required"}
        )
    }


class VacancyTagger:
    def __init__(self, config, streams, *, progress=None, ledger=None, budget=None):
        self.config, self.streams = config, streams
        self.ledger, self.budget = ledger, budget
        self.client = TaggerStageClients(config)
        self.stats = dict.fromkeys(
            ("calls", "cache_hits", "facts_reuse", "escalations", "errors", "gated"), 0
        )
        self._lock = threading.Lock()
        self._labels_observed = 0
        self._labels_escalated = 0
        self.set_progress(progress)

    @property
    def rubric(self):
        return load_rubric(self.config.rubric_path)

    def set_progress(self, progress):
        self.progress = progress
        if hasattr(self.client, "set_progress"):
            self.client.set_progress(self._on_event)

    def _inc(self, name):
        with self._lock:
            self.stats[name] += 1

    def _report_escalation_share(self, result):
        if result.status != "labeled":
            return
        if self.ledger is not None and hasattr(self.ledger, "observe_label"):
            totals = self.ledger.observe_label(result.escalated)
        else:
            with self._lock:
                self._labels_observed += 1
                self._labels_escalated += bool(result.escalated)
                totals = {
                    "labeled": self._labels_observed,
                    "escalated": self._labels_escalated,
                    "share": self._labels_escalated / self._labels_observed,
                }
        threshold = self.config.escalation.max_share_warning
        if totals["share"] > threshold:
            emit_progress(
                self.progress, "label_escalation_share_warning", threshold=threshold, **totals
            )

    def _on_event(self, event):
        active = _ACTIVE.get()
        if active is not None and event.get("event") == "request_start":
            if active["started"]:
                active["reservation"] = (
                    self.budget.reserve(active["prompt"], active["spec"]) if self.budget else None
                )
            active["started"] = True
            active["finished"] = False
            active["call_id"] = uuid.uuid4().hex
            self._inc("calls")
        if active is not None:
            event = {**event, "call_id": active["call_id"]}
            if event.get("event") in {"request_complete", "request_failed"}:
                if self.budget:
                    self.budget.complete(active["reservation"], event)
                active["reservation"] = None
                active["finished"] = True
        if self.ledger:
            self.ledger(event)
        if self.progress:
            self.progress(event)

    def _keys(self, record, units, stage, rubric, examples):
        facts = input_digest(
            {
                "schema": LABELS_SCHEMA,
                "prompt_version": prompt_version(),
                "annotation_input_hash": annotation_input_hash(record, units),
                "stage": stage,
                "spec": model_spec(self.config.resolved_stage(stage)),
                "taxonomy": taxonomy_version(self.streams),
            }
        )
        full = input_digest(
            {
                "facts_key": facts,
                "prompt_rubric_hash": rubric.prompt_rubric_hash,
                "precedents_hash": precedents_hash(examples),
            }
        )
        directory = Path(self.config.cache_dir) / source_identity(record) / stage / facts
        return facts, full, directory

    def _cached(self, directory, facts, full, record, units, stage, role_ids, *, allow_reuse):
        paths = [directory / (full + ".json")]
        if allow_reuse and directory.is_dir():
            paths += [path for path in sorted(directory.glob("*.json")) if path not in paths]
        for path in paths:
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
                if (
                    value["facts_key"] != facts
                    or value["cache_key"] != path.stem
                    or value["annotation_input_hash"] != annotation_input_hash(record, units)
                    or value["stage"] != stage
                ):
                    continue
                checked, warnings = validate_tags(_raw(value["result"]), units, role_ids)
                old_warnings = value.get("warnings", [])
                # Never replay arbitrary tag names, codes or malformed containers.
                old_warnings = (
                    [w for w in old_warnings if is_validation_warning(w)]
                    if isinstance(old_warnings, list)
                    else []
                )
                stale = path.stem != full
                self._inc("facts_reuse" if stale else "cache_hits")
                emit_progress(
                    self.progress, "label_facts_reuse" if stale else "label_cache_hit", stage=stage
                )
                if self.ledger:
                    self.ledger.record_cache(
                        value.get("call_id"),
                        stage=stage,
                        model=self.config.resolved_stage(stage).model,
                    )
                usage = {
                    "stage": stage,
                    "model": self.config.resolved_stage(stage).model,
                    "cached": True,
                }
                return (
                    checked,
                    list(dict.fromkeys(old_warnings + warnings)),
                    usage,
                    stale,
                    path.stem,
                )
            except (OSError, ValueError, TypeError, KeyError, AttributeError):
                continue
        return None

    @staticmethod
    def _write(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        name = None
        try:
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", dir=path.parent, prefix=".labels-", delete=False
            ) as stream:
                name = stream.name
                json.dump(value, stream, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, path)
            name = None
        finally:
            if name is not None:
                Path(name).unlink(missing_ok=True)

    def _expose(self, examples):
        if not examples:
            return
        from job_intake.annotation.cohorts import (
            record_example_exposure,
            registered_cohorts,
        )

        for manifest in registered_cohorts():
            record_example_exposure(manifest, examples, purpose="prompt_demo", actor="labels_v3")

    def _stage(self, record, units, stage, rubric, examples, role_ids, use_ai):
        facts, full, directory = self._keys(record, units, stage, rubric, examples)
        cached = self._cached(
            directory,
            facts,
            full,
            record,
            units,
            stage,
            role_ids,
            allow_reuse=not (self.config.refresh_stale and use_ai),
        )
        if cached:
            return cached
        if not use_ai:
            return None
        if self.budget is None:
            raise BudgetExceeded("budget_required")
        prompt, _schema = build_prompt(record, units, role_taxonomy(self.streams), rubric, examples)
        spec = self.config.resolved_stage(stage)
        reservation = self.budget.reserve(prompt, spec)
        active = {
            "reservation": reservation,
            "prompt": prompt,
            "spec": spec,
            "started": False,
            "finished": False,
            "call_id": uuid.uuid4().hex,
        }
        token = _ACTIVE.set(active)
        emit_progress(self.progress, "label_stage", stage=stage, model=spec.model)
        try:
            self._expose(examples)
            raw, usage = self.client.generate_stage(stage, spec.model, prompt)
            if not active["started"]:
                # Fake clients used by tests may omit transport progress events.
                self._on_event(
                    {"event": "request_start", "stage": stage, "model": spec.model, "attempt": 1}
                )
            if not active["finished"]:
                self._on_event(
                    {
                        "event": "request_complete",
                        "stage": stage,
                        "model": spec.model,
                        "elapsed": 0.0,
                        **usage,
                    }
                )
            checked, warnings = validate_tags(raw, units, role_ids)
            safe_usage = {
                key: value
                for key, value in usage.items()
                if key
                in {
                    "input_tokens",
                    "output_tokens",
                    "total_tokens",
                    "thinking_tokens",
                    "cached_input_tokens",
                }
                and type(value) is int
                and value >= 0
            }
            safe_usage.update(stage=stage, model=spec.model, cached=False)
            self._write(
                directory / (full + ".json"),
                {
                    "cache_key": full,
                    "facts_key": facts,
                    "annotation_input_hash": annotation_input_hash(record, units),
                    "stage": stage,
                    "result": checked,
                    "warnings": warnings,
                    "usage": safe_usage,
                    "call_id": active["call_id"],
                    "created_at": datetime.now(UTC).isoformat(),
                },
            )
            return checked, warnings, safe_usage, False, full
        except BaseException as exc:
            # Native clients reject missing keys/configuration before request_start.
            # A foreign/fake client with no transport events cannot prove no POST;
            # retain conservative billing exposure and an unknown-usage attempt.
            if not active["started"] and getattr(exc, "code", None) not in {
                "missing_api_key",
                "invalid_configuration",
            }:
                self._on_event(
                    {"event": "request_start", "stage": stage, "model": spec.model, "attempt": 1}
                )
            if active["started"] and not active["finished"]:
                self._on_event(
                    {
                        "event": "request_failed",
                        "stage": stage,
                        "model": spec.model,
                        "elapsed": 0.0,
                        "usage_known": False,
                    }
                )
            raise
        finally:
            if active["reservation"] is not None:
                self.budget.complete(active["reservation"], posted=active["started"])
            _ACTIVE.reset(token)

    def label(self, record, evaluation, classification, *, use_ai):
        started = time.monotonic()
        rubric = self.rubric
        source = replace(record)
        units = source_units(source)
        role_ids = [role["id"] for role in role_taxonomy(self.streams)]
        classifier_role = classification.get("label")
        if classifier_role not in {*role_ids, "other"}:
            classifier_role = "other"
        result = LabelResult(
            role_label=classifier_role,
            tags=unspecified_tags(),
            rubric_version=rubric.version,
            rubric_status=rubric.rubric_status,
            prompt_version=prompt_version(),
            aggregation_version=AGGREGATION_VERSION,
            annotation_input_hash=annotation_input_hash(source, units),
            source_hash=source_hash(source),
        )
        emit_progress(self.progress, "label_start")
        gates = free_gates(source, evaluation, classification, self.config, streams=self.streams)
        result.gate_outcome, result.gate_reasons = gates.gate_outcome, gates.gate_reasons
        if gates.gate_outcome == "ineligible" and not gates.gate_reasons:
            result.validation_warnings.append("gate:rule_reason_unmapped")
        if gates.stop:
            self._inc("gated")
            result.status, result.route, result.relevance = (
                gates.status,
                gates.route,
                gates.relevance,
            )
            result.latency_ms = int((time.monotonic() - started) * 1000)
            emit_progress(self.progress, "label_gated", route=gates.route)
            return result
        if rubric.rubric_status != "confirmed":
            # Proposed anchors/aggregation cannot authorize a candidate decision,
            # even when old confirmed facts exist under the same source identity.
            result.route = "rubric_unconfirmed"
            result.p2_candidate = None
            return result
        examples = []
        try:
            if Path(self.config.precedents_path).is_file():
                examples = load_precedents(Path(self.config.precedents_path), role_ids=role_ids)
            first_stage = self._stage(source, units, "tag", rubric, examples, role_ids, use_ai)
            if first_stage is not None:
                first, warnings, usage, stale, key = first_stage
                result.status, result.cache_key = "labeled", key
                result.usage.append(usage)
                result.validation_warnings.extend(warnings)
                rules_rel = rules_relevance(
                    first["role_label"], first["tags"], rubric, company=source.company
                )
                _outcome, _reasons, llm_block = gate_outcome(first["tags"], evaluation, gates)
                result.escalation_triggers = escalation_triggers(
                    first, rules_rel, classification, warnings, llm_block
                )
                if stale:
                    result.escalation_triggers.append("stale_rubric")
                selected = first
                second = None
                if result.escalation_triggers and self.config.escalation.enabled:
                    emit_progress(
                        self.progress, "label_escalate", triggers=result.escalation_triggers
                    )
                    try:
                        second_stage = self._stage(
                            source,
                            units,
                            "escalate",
                            rubric,
                            examples,
                            role_ids,
                            use_ai and not stale,
                        )
                        if second_stage is not None:
                            second, new_warnings, new_usage, new_stale, new_key = second_stage
                            self._inc("escalations")
                            result.escalated = True
                            selected = second
                            result.cache_key = new_key
                            result.usage.append(new_usage)
                            result.validation_warnings.extend(new_warnings)
                            stale = new_stale
                    except BudgetExceeded:
                        raise
                    except Exception as exc:
                        self._inc("errors")
                        result.errors.append(
                            {**error_diagnostic(exc, stage="escalate"), "code": "escalation_failed"}
                        )
                llm_role = selected["role_label"]
                if classifier_role == "other" or classifier_role == llm_role or second is not None:
                    result.role_label = llm_role
                result.tags = selected["tags"]
                result.llm_relevance = selected["relevance"]
                result.confidence = selected["confidence"]
                result.llm_relevance_stale = stale
                if stale and "stale_rubric" not in result.escalation_triggers:
                    result.escalation_triggers.append("stale_rubric")
                outcome, reasons, model_only = gate_outcome(result.tags, evaluation, gates)
                if model_only:
                    confirmed_values = (
                        dict(
                            _block_values(first["tags"]).items()
                            & _block_values(second["tags"]).items()
                        )
                        if second is not None
                        else {}
                    )
                    if not confirmed_values:
                        outcome, reasons = "unknown", []
                        result.validation_warnings.append("gate:unconfirmed_model_block")
                    else:
                        confirmed_reasons = {
                            "hiring_eligibility": "hiring_region_restricted",
                            "work_mode": "office_outside_sp",
                            "working_language": "other_language_required",
                        }
                        reasons = list(
                            dict.fromkeys(confirmed_reasons[name] for name in confirmed_values)
                        )
                        if len(confirmed_values) < len(_block_values(result.tags)):
                            result.validation_warnings.append("gate:unconfirmed_model_block")
                result.gate_outcome, result.gate_reasons = outcome, reasons
        except BudgetExceeded:
            raise
        except Exception as exc:
            self._inc("errors")
            result.status = "error"
            diagnostic = error_diagnostic(exc, stage="tag")
            if isinstance(exc, LabelValidationError):
                diagnostic["code"] = "schema_violation"
            result.errors.append(diagnostic)
            emit_progress(self.progress, "label_error", code=diagnostic["code"])
        result.rules_relevance = rules_relevance(
            result.role_label, result.tags, rubric, company=source.company
        )
        result.relevance_by_policy = {
            policy: final_relevance(result.llm_relevance, result.rules_relevance, policy)
            for policy in ("llm", "rules", "max", "min")
        }
        result.relevance = result.relevance_by_policy[self.config.final_policy]
        if result.llm_relevance_stale and self.config.final_policy == "llm":
            result.relevance = result.rules_relevance
        result.p2_candidate = (
            p2_candidate(result.tags["function_core"]["value"], result.relevance,
                         result.gate_outcome)
            if rubric.p2_rule is not None else None
        )
        result.latency_ms = int((time.monotonic() - started) * 1000)
        self._report_escalation_share(result)
        emit_progress(
            self.progress,
            "label_complete",
            relevance=result.relevance,
            gate_outcome=result.gate_outcome,
            escalated=result.escalated,
        )
        return result

    def relabel_offline(self, record, evaluation, classification):
        return self.label(record, evaluation, classification, use_ai=False)
