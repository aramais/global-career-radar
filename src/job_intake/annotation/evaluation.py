"""Offline integrity checks for persisted vacancy annotation and routing.

This checks attributable evidence and completeness, not the semantic accuracy of
the models. It never reads caches, contacts providers, or changes a JobRecord.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import replace
from typing import Any

from job_intake.annotation.ai import _ERROR_CODES, _FINISH_REASONS
from job_intake.annotation.prefilter import (
    PREFILTER_VERSION,
    VacancyPrefilter,
    _validated,
)
from job_intake.annotation.schema import (
    REVIEW_STATUSES,
    prepare_claims,
    review_claims,
    summarize_claims,
)
from job_intake.annotation.service import (
    ANNOTATION_VERSION,
    VacancyAnnotator,
    _drafts,
    _local_claims,
    _reviewed,
)
from job_intake.annotation.text import chunk_source, source_hash, source_identity, source_units
from job_intake.config.settings import AnnotationConfig, PrefilterConfig
from job_intake.models.job import JobRecord

EVALUATION_VERSION = "saved-integrity-v1"
_CLAIM_FIELDS = (
    "claim_id",
    "source_id",
    "chunk_id",
    "source_field",
    "source_section",
    "kind",
    "value",
    "unit_id",
    "source_snippet",
    "requirement",
    "polarity",
    "is_inference",
    "evidence_warnings",
    "review_status",
    "review_method",
)

_DIAGNOSTIC_CODES = set(_ERROR_CODES) | {
    "invalid_decision",
    "invalid_evidence",
    "quote_shortened",
    "quote_unattributable",
    "not_duty",
    "no_evidence",
}
_ERROR_TYPES = {
    "AnnotationModelError",
    "PrefilterValidationError",
    "ValueError",
    "TypeError",
    "KeyError",
    "OSError",
    "RuntimeError",
    "PermissionError",
    "FileNotFoundError",
    "Exception",
}
_CACHE_REBUILT_NOTICE = re.compile(
    r"(chunk-[0-9]{6}): invalid (?:extraction cache rebuilt|review cache rebuilt|"
    r"review batch checkpoint rebuilt|checkpoint ignored)"
)


def _cache_rebuilt_notices(issues: Any, chunk_ids: set[str]) -> bool:
    """Recognize exact internal notices only; validation must prove recovery later."""
    if not isinstance(issues, list) or not issues:
        return False
    for issue in issues:
        if not isinstance(issue, str):
            return False
        if issue == "Invalid cache ignored; extraction will be rebuilt.":
            continue
        match = _CACHE_REBUILT_NOTICE.fullmatch(issue)
        if match is None or match[1] not in chunk_ids:
            return False
    return True


def _diagnostics(value: Any, *, prefilter: bool = False) -> list[dict]:
    """Keep safe diagnostic enums and counters; ignore arbitrary historical strings."""
    if not isinstance(value, list):
        return []
    result = []
    for row in value:
        if not isinstance(row, dict):
            continue
        code = row.get("code")
        safe = {
            "code": code
            if isinstance(code, str) and code in _DIAGNOSTIC_CODES
            else "unexpected_error"
        }
        stage = row.get("stage")
        safe["stage"] = (
            stage
            if isinstance(stage, str)
            and stage
            in {
                "extract",
                "review",
                "screen",
                "verify",
            }
            else "prefilter"
            if prefilter
            else "annotation"
        )
        if prefilter and safe["stage"] in {"screen", "verify"}:
            safe["stage"] = "prefilter_" + safe["stage"]
        chunk = row.get("chunk_id")
        if isinstance(chunk, str) and re.fullmatch(r"chunk-\d{6}", chunk):
            safe["chunk_id"] = chunk
        batch_id = row.get("batch_id")
        if isinstance(batch_id, str) and re.fullmatch(r"batch-\d{6}", batch_id):
            safe["batch_id"] = batch_id
        error_type = row.get("error_type")
        if isinstance(error_type, str) and error_type in _ERROR_TYPES:
            safe["error_type"] = error_type
        status = row.get("http_status")
        if type(status) is int and 100 <= status <= 599:
            safe["http_status"] = status
        finish = row.get("finish_reason")
        if isinstance(finish, str) and finish in _FINISH_REASONS:
            safe["finish_reason"] = finish
        usage = row.get("usage")
        if isinstance(usage, dict):
            safe_usage = {
                name: usage[name]
                for name in (
                    "input_tokens",
                    "output_tokens",
                    "total_tokens",
                )
                if type(usage.get(name)) is int and usage[name] >= 0
            }
            if usage.get("usage_known") is False:
                safe_usage["usage_known"] = False
            if safe_usage:
                safe["usage"] = safe_usage
        result.append(safe)
    return result


def _pure_helper(cls: type, config: Any) -> Any:
    # The relevant methods only need config. Avoid even creating provider clients.
    instance = object.__new__(cls)
    instance.config = config
    return instance


def _finding(
    findings: list[dict],
    code: str,
    message: str,
    *,
    severity: str = "error",
    stage: str = "annotation",
    chunk_id: str | None = None,
    claim_id: str | None = None,
) -> None:
    row = {"severity": severity, "code": code, "stage": stage, "message": message}
    if chunk_id is not None:
        row["chunk_id"] = chunk_id
    if claim_id is not None:
        row["claim_id"] = claim_id
    findings.append(row)


def _reject_findings(rejects: list[dict], findings: list[dict], stage: str, chunk: str) -> None:
    for rejected in rejects:
        reason = rejected["reason"]
        code = (
            "duplicate_draft"
            if reason == "Duplicate claim in extraction response"
            else "duplicate_review"
            if reason == "Duplicate reviews for the same claim_id"
            else "unknown_review_claim"
            if reason == "Review claim_id does not identify an input claim"
            else "invalid_review"
            if stage == "review"
            else "invalid_draft"
        )
        _finding(
            findings,
            code,
            "Invalid model output was excluded by the evidence validator.",
            severity="warning",
            stage=stage,
            chunk_id=chunk,
        )


def _prefilter(
    saved: Any,
    record: JobRecord,
    config: PrefilterConfig,
    units: list[dict],
    findings: list[dict],
) -> tuple[str, bool]:
    if saved is None:
        return "absent", False
    if not isinstance(saved, dict):
        _finding(
            findings, "invalid_prefilter", "Saved prefilter must be an object.", stage="prefilter"
        )
        return "invalid", False
    if saved.get("source_hash") != source_hash(record):
        _finding(
            findings,
            "prefilter_source_mismatch",
            "Prefilter belongs to another source.",
            stage="prefilter",
        )
        return "invalid", False
    if saved.get("routing") == "local_reject":
        blockers = saved.get("blockers")
        if (
            saved.get("decision") != "clearly_irrelevant"
            or saved.get("reason_code") != "hard_blocker"
            or not isinstance(blockers, list)
            or not blockers
            or not all(isinstance(item, str) and item.strip() for item in blockers)
        ):
            _finding(
                findings,
                "invalid_local_reject",
                "Local rejection lacks hard blockers.",
                stage="prefilter",
            )
            return "invalid", False
        _finding(
            findings,
            "local_reject",
            "Full annotation was intentionally skipped by rules.",
            severity="info",
            stage="prefilter",
        )
        _finding(
            findings,
            "local_reject_not_revalidated",
            "Saved hard blockers are reported; active profile rules were not rerun.",
            severity="info",
            stage="prefilter",
        )
        return "local_reject", True
    helper = _pure_helper(VacancyPrefilter, config)
    expected = helper._result(source_hash(record))
    current_policy = config.enabled
    for name in ("version", "focus_version", "cache_key", "routing_policy_version"):
        if saved.get(name) != expected[name]:
            current_policy = False
            _finding(
                findings,
                "stale_prefilter_" + name,
                "Saved prefilter policy or model configuration differs from current settings.",
                severity="warning",
                stage="prefilter",
            )
    if not isinstance(saved.get("routing"), str) or saved["routing"] not in {
        "suppressed",
        "full_review",
    }:
        _finding(
            findings,
            "invalid_prefilter_routing",
            "Unknown saved routing decision.",
            stage="prefilter",
        )
        return "invalid", False
    stages = saved.get("stages")
    if not isinstance(stages, dict):
        _finding(
            findings,
            "invalid_prefilter_stages",
            "Saved prefilter stages are missing.",
            stage="prefilter",
        )
        return "invalid", False
    fallback = (
        saved.get("reason") == ("Prefilter unavailable or invalid; full review required.")
        or bool(saved.get("issues"))
        or bool(saved.get("errors"))
    )
    if fallback:
        _finding(
            findings,
            "prefilter_fallback",
            "Prefilter failed and retained the full review.",
            severity="warning",
            stage="prefilter",
        )
        if (
            saved.get("routing_guard") is not None
            or saved.get("counterevidence", []) != []
        ):
            _finding(
                findings,
                "prefilter_routing_mismatch",
                "A failed prefilter cannot claim a validated routing guard.",
                stage="prefilter",
            )
            return "invalid", False
    checked = {}
    for stage in ("screen", "verify"):
        if stage not in stages:
            continue
        row = stages[stage]
        if not isinstance(row, dict) or row.get("source_hash") != source_hash(record):
            _finding(
                findings,
                "invalid_prefilter_stage",
                "Prefilter stage source is invalid.",
                stage="prefilter_" + stage,
            )
            continue
        proposed = checked.get("screen", {}).get("result", {}).get("reason_code")
        try:
            validated = _validated(row.get("result"), units)
        except (TypeError, ValueError, KeyError):
            _finding(
                findings,
                "invalid_prefilter_evidence",
                "Prefilter stage has an invalid decision or unattributable duty quotes.",
                stage="prefilter_" + stage,
            )
            continue
        key = helper._stage_key(stage, source_hash(record), proposed if stage == "verify" else None)
        if row.get("stage") != stage or not isinstance(row.get("usage"), dict):
            _finding(
                findings,
                "invalid_prefilter_stage",
                "Stage metadata is malformed.",
                stage="prefilter_" + stage,
            )
            continue
        if row.get("cache_key") != key or row.get("version") != PREFILTER_VERSION:
            current_policy = False
            _finding(
                findings,
                "stale_prefilter_stage",
                "Stage uses an older policy or model.",
                severity="warning",
                stage="prefilter_" + stage,
            )
        spec = config.resolved_stage(stage)
        usage = row.get("usage", {})
        if isinstance(usage, dict) and any(
            usage.get(name) is not None and usage.get(name) != value
            for name, value in (("model", spec.model), ("provider", spec.provider))
        ):
            current_policy = False
            _finding(
                findings,
                "stale_prefilter_model",
                "Stage was run with another model.",
                severity="warning",
                stage="prefilter_" + stage,
            )
        checked[stage] = {"result": validated, "usage": {}}
    if set(stages) - {"screen", "verify"}:
        current_policy = False
        _finding(
            findings,
            "unknown_prefilter_stage",
            "Unknown persisted prefilter stages.",
            severity="warning",
            stage="prefilter",
        )
    screen = checked.get("screen", {}).get("result")
    if screen is None:
        if saved["routing"] == "suppressed":
            _finding(
                findings,
                "unverified_suppression",
                "Suppression has no valid screen.",
                stage="prefilter",
            )
            return "invalid", False
        if not fallback and stages:
            return "invalid", False
        if any(
            saved.get(name, [] if name == "counterevidence" else None) != expected[name]
            for name in (
                "routing",
                "decision",
                "reason_code",
                "evidence",
                "routing_guard",
                "counterevidence",
            )
        ):
            _finding(
                findings,
                "invalid_prefilter_bypass",
                "A bypass or failed stage cannot produce a positive routing verdict.",
                stage="prefilter",
            )
            return "invalid", False
        return "fallback" if fallback else "bypassed", False
    requires_verify = screen["decision"] == "clearly_irrelevant"
    if requires_verify and "verify" not in checked:
        _finding(
            findings,
            "prefilter_verify_missing",
            "Proposed rejection lacks independent valid verification.",
            severity="warning" if fallback and saved["routing"] == "full_review" else "error",
            stage="prefilter",
        )
        return "fallback" if fallback else "invalid", False
    rebuilt = helper._result(source_hash(record))
    rebuilt["stages"] = checked
    helper._apply_stages(rebuilt, units=units)
    if not fallback and any(
        saved.get(name, [] if name == "counterevidence" else None) != rebuilt[name]
        for name in (
            "routing",
            "decision",
            "reason_code",
            "evidence",
            "routing_guard",
            "counterevidence",
        )
    ):
        _finding(
            findings,
            "prefilter_routing_mismatch",
            "Saved routing disagrees with independently validated stages.",
            stage="prefilter",
        )
        return "invalid", False
    if fallback:
        if saved["routing"] == "suppressed":
            _finding(
                findings,
                "unsafe_prefilter_fallback",
                "A failed prefilter cannot suppress full annotation.",
                stage="prefilter",
            )
            return "invalid", False
        return "fallback", False
    suppressed = rebuilt["routing"] == "suppressed"
    if suppressed and not current_policy:
        _finding(
            findings,
            "suppression_not_current",
            "Suppression must match the enabled current policy and model settings.",
            stage="prefilter",
        )
        return "invalid", False
    if suppressed:
        _finding(
            findings,
            "suppressed",
            "Two grounded stages intentionally skipped full review.",
            severity="info",
            stage="prefilter",
        )
    return "suppressed" if suppressed else "full_review", suppressed


def evaluate_annotation(
    record: JobRecord,
    annotation_config: AnnotationConfig,
    prefilter_config: PrefilterConfig,
) -> dict[str, Any]:
    """Validate saved stages against source, without API calls or cache fallback."""
    record = replace(record)
    units = source_units(record)
    annotation = record.annotation
    findings: list[dict] = []
    result: dict[str, Any] = {
        "source_id": source_identity(record),
        "title": record.title,
        "company": record.company,
        "annotation_status": "unannotated",
        "method": None,
        "integrity": "not_evaluated",
        "intentional_skip": False,
        # Full review is required unless a current validated route exempts it.
        # A retained two-pass artifact then describes historical work, not a
        # claim that the current routing decision requested that work again.
        "full_review_required": True,
        "historical_annotation_status": None,
        "prefilter_status": "absent",
        "findings": findings,
        "diagnostics": [],
        "pipeline_health": {
            "status": "not_evaluated",
            "annotation_stage_errors": 0,
            "historical_annotation_stage_errors": 0,
            "prefilter_stage_errors": 0,
            "prefilter_fallback": False,
        },
        "counts": {
            "claims": 0,
            "valid_quotes": 0,
            "supported": 0,
            "inferences": 0,
            "reviewed_claims": 0,
            "expected_review_claims": 0,
        },
        "coverage": {
            "chunks": 0,
            "extracted_chunks": 0,
            "completed_chunks": 0,
            "source_chars": sum(len(unit["text"]) for unit in units),
            "extracted_source_chars": 0,
            "reviewed_source_chars": 0,
        },
        "search_focus": None,
    }
    if not annotation:
        _finding(findings, "unannotated", "No saved annotation.", severity="info")
        return result
    if not isinstance(annotation, dict):
        _finding(findings, "invalid_annotation", "Saved annotation must be an object.")
        result["integrity"] = "error"
        return result
    result["annotation_status"] = (
        annotation.get("status", "unknown")
        if isinstance(annotation.get("status", "unknown"), str)
        else "invalid"
    )
    result["method"] = (
        annotation.get("method") if isinstance(annotation.get("method"), str) else None
    )
    prefilter_status, intentional = _prefilter(
        annotation.get("prefilter"),
        record,
        prefilter_config,
        units,
        findings,
    )
    result.update(
        prefilter_status=prefilter_status,
        intentional_skip=intentional,
        full_review_required=not intentional,
        historical_annotation_status=(
            result["annotation_status"]
            if intentional and result["method"] == "two_pass" else None
        ),
    )
    result["diagnostics"] = _diagnostics(annotation.get("errors"))
    annotation_errors = len(result["diagnostics"])
    if intentional:
        for diagnostic in result["diagnostics"]:
            diagnostic["historical"] = True
    saved_prefilter = annotation.get("prefilter")
    if isinstance(saved_prefilter, dict):
        result["diagnostics"].extend(_diagnostics(saved_prefilter.get("errors"), prefilter=True))
    result["pipeline_health"] = {
        "status": "degraded" if (
            any(not row.get("historical") for row in result["diagnostics"])
            or (annotation.get("issues") and not intentional)
            or prefilter_status == "fallback"
        ) else "ok",
        "annotation_stage_errors": 0 if intentional else annotation_errors,
        "historical_annotation_stage_errors": annotation_errors if intentional else 0,
        "prefilter_stage_errors": sum(
            row["stage"].startswith("prefilter") for row in result["diagnostics"]
        ),
        "prefilter_fallback": prefilter_status == "fallback",
    }
    helper = _pure_helper(VacancyAnnotator, annotation_config)
    for name, expected in (
        ("source_id", source_identity(record)),
        ("source_hash", source_hash(record)),
    ):
        if annotation.get(name) != expected:
            _finding(
                findings,
                "annotation_" + name + "_mismatch",
                "Saved annotation belongs to a different source identity or content.",
            )
    if annotation.get("version") != ANNOTATION_VERSION:
        _finding(
            findings,
            "stale_annotation_version",
            "Annotation uses an older format.",
            severity="warning",
        )
    # --ai is enabled in memory by the CLI without rewriting YAML. Its Boolean
    # value does not change the evidence/model policy being checked here.
    enabled_helper = _pure_helper(
        VacancyAnnotator,
        replace(annotation_config, ai_enabled=not annotation_config.ai_enabled),
    )
    if annotation.get("cache_key") not in (
        helper._cache_key(record),
        enabled_helper._cache_key(record),
    ):
        _finding(
            findings,
            "stale_annotation_config",
            "Annotation settings or prompts have changed.",
            severity="warning",
        )
    if annotation.get("units") != units:
        _finding(
            findings,
            "source_units_mismatch",
            "Saved units do not cover the current source exactly.",
        )
    claims = annotation.get("claims")
    if not isinstance(claims, list):
        _finding(findings, "invalid_claims", "Saved claims must be an array.")
        claims = []
    result["counts"]["claims"] = len(claims)
    ai = annotation.get("method") == "two_pass"
    if not ai and annotation.get("method") != "deterministic":
        _finding(findings, "unknown_annotation_method", "Unknown annotation method.")
    if not ai:
        if annotation.get("status") != "local":
            _finding(
                findings, "invalid_local_status", "Deterministic annotation must have local status."
            )
        elif not intentional:
            _finding(
                findings,
                "local_only",
                "Only deterministic evidence is saved; AI was not evaluated.",
                severity="info",
            )
    chunk_size = annotation.get("chunk_chars", annotation_config.chunk_chars)
    if type(chunk_size) is not int or chunk_size < 256:
        _finding(findings, "invalid_chunk_size", "Saved chunk size is invalid.")
        chunk_size = annotation_config.chunk_chars
    chunks = chunk_source(units, chunk_size) if ai else []
    chunk_map = {chunk["chunk_id"]: chunk for chunk in chunks}
    complete = False
    verified: dict[str, dict] = {}
    if ai:
        result["coverage"]["chunks"] = len(chunks)
        if chunk_size != annotation_config.chunk_chars:
            _finding(
                findings,
                "stale_chunk_size",
                "Saved chunk size differs from current settings.",
                severity="warning",
            )
        for stage_name in ("extract", "review"):
            spec = annotation_config.resolved_stage(stage_name)
            if (
                annotation.get(stage_name + "_model") != spec.model
                or annotation.get(stage_name + "_provider") != spec.provider
            ):
                _finding(
                    findings,
                    "stale_annotation_model",
                    "Saved stage uses another model.",
                    severity="warning",
                    stage=stage_name,
                )
        stages = annotation.get("stages")
        if not isinstance(stages, dict):
            _finding(findings, "invalid_annotation_stages", "Saved chunk stages must be an object.")
            stages = {}
        if set(stages) - set(chunk_map):
            _finding(
                findings, "unknown_annotation_chunk", "Persisted stages include unknown chunks."
            )
        for chunk_id, chunk in chunk_map.items():
            chunk_units = [unit for unit in chunk["units"] if unit["text"].strip()]
            # The AI path seeds attributable source fields before extraction.
            # A failed extraction leaves those drafts unreviewed, not invented.
            seeded, _ = _drafts({"claims": []}, result["source_id"], chunk_units, chunk_id)
            verified.update({claim["claim_id"]: claim for claim in seeded})
            stage = stages.get(chunk_id)
            if not isinstance(stage, dict) or "extraction" not in stage:
                result["counts"]["expected_review_claims"] += len(seeded)
                _finding(
                    findings,
                    "extraction_missing",
                    "Source chunk has no saved extraction.",
                    severity="info" if intentional else "error",
                    stage="extract",
                    chunk_id=chunk_id,
                )
                continue
            try:
                drafts, _ = _drafts(stage["extraction"], result["source_id"], chunk_units, chunk_id)
                # Local seeds can conservatively fail their own heuristics (for
                # example a short heading). Only model drafts are output defects.
                _, rejected = prepare_claims(
                    stage["extraction"],
                    result["source_id"],
                    chunk_units,
                    chunk_id,
                )
            except (TypeError, ValueError, KeyError):
                result["counts"]["expected_review_claims"] += len(seeded)
                _finding(
                    findings,
                    "invalid_extraction",
                    "Extraction response envelope is invalid.",
                    stage="extract",
                    chunk_id=chunk_id,
                )
                continue
            _reject_findings(rejected, findings, "extract", chunk_id)
            result["coverage"]["extracted_chunks"] += 1
            chars = sum(len(unit["text"]) for unit in chunk["units"])
            result["coverage"]["extracted_source_chars"] += chars
            result["counts"]["expected_review_claims"] += len(drafts)
            extraction_key = helper._extraction_key(record, chunk)
            if stage.get("extraction_key") != extraction_key:
                _finding(
                    findings,
                    "stale_extraction_config",
                    "Extraction stage settings have changed.",
                    severity="warning",
                    stage="extract",
                    chunk_id=chunk_id,
                )
            if stage.get("review_key") not in (None, helper._review_key(extraction_key, drafts)):
                _finding(
                    findings,
                    "stale_review_config",
                    "Review stage settings have changed.",
                    severity="warning",
                    stage="review",
                    chunk_id=chunk_id,
                )
            payload = stage.get("review", {"reviews": []})
            try:
                provider = annotation.get("review_provider", "")
                options = helper._jev_options(annotation.get("review_model", ""))
                reviewed, rejected = _reviewed(drafts, payload, provider, options)
                complete = helper._review_complete(drafts, payload, provider, options)
            except (TypeError, ValueError, KeyError):
                _finding(
                    findings,
                    "invalid_review",
                    "Review response envelope is invalid.",
                    stage="review",
                    chunk_id=chunk_id,
                )
                reviewed, _ = _reviewed(drafts, {"reviews": []})
                complete, rejected = False, []
            _reject_findings(rejected, findings, "review", chunk_id)
            result["counts"]["reviewed_claims"] += sum(
                claim.get("review_method") == "model" for claim in reviewed
            )
            verified.update({claim["claim_id"]: claim for claim in reviewed})
            if complete:
                result["coverage"]["completed_chunks"] += 1
                result["coverage"]["reviewed_source_chars"] += chars
            else:
                _finding(
                    findings,
                    "review_incomplete",
                    "Not every seeded or extracted claim has a valid review.",
                    severity="info" if intentional else "error",
                    stage="review",
                    chunk_id=chunk_id,
                )
        complete = result["coverage"]["completed_chunks"] == len(chunks)
        expected_status = "reviewed" if complete else "partial"
        if annotation.get("status") != expected_status:
            _finding(
                findings,
                "annotation_status_mismatch",
                "Saved status disagrees with chunk review coverage.",
            )
        if annotation.get("coverage") != {
            "chunks": len(chunks),
            "completed_chunks": result["coverage"]["completed_chunks"],
        }:
            _finding(
                findings,
                "annotation_coverage_mismatch",
                "Saved coverage disagrees with validated stages.",
            )
    else:
        local, _ = prepare_claims(
            {"claims": _local_claims(units)},
            result["source_id"],
            [unit for unit in units if unit["text"].strip()],
            "local",
        )
        local, _ = review_claims(
            local,
            {
                "reviews": [
                    {
                        "claim_id": claim["claim_id"],
                        "review_status": "SUPPORTED",
                        "is_inference": False,
                        "review_notes": "Direct source field or explicit clause; local extraction.",
                    }
                    for claim in local
                ]
            },
        )
        for claim in local:
            claim["review_method"] = "local"
        verified = {claim["claim_id"]: claim for claim in local}
    seen = set()
    for claim in claims:
        if not isinstance(claim, dict):
            _finding(findings, "invalid_saved_claim", "Saved claim must be an object.")
            continue
        claim_id = claim.get("claim_id")
        # IDs in findings always come from validated expected evidence, not model strings.
        safe_id = claim_id if isinstance(claim_id, str) and claim_id in verified else None
        if not isinstance(claim_id, str) or claim_id in seen:
            _finding(
                findings,
                "duplicate_saved_claim",
                "Saved claim IDs must be unique strings.",
                claim_id=safe_id,
            )
        if isinstance(claim_id, str):
            seen.add(claim_id)
        own_chunk = claim.get("chunk_id") if isinstance(claim.get("chunk_id"), str) else None
        claim_units = chunk_map.get(own_chunk, {}).get("units", []) if ai else units
        claim_units = [unit for unit in claim_units if unit["text"].strip()]
        try:
            prepared, rejected = prepare_claims(
                {"claims": [claim]}, result["source_id"], claim_units, own_chunk
            )
        except (TypeError, ValueError, KeyError):
            prepared, rejected = [], [{}]
        if rejected or not prepared:
            _finding(
                findings,
                "invalid_saved_evidence",
                "Claim has invalid source units, identity or quotes.",
                claim_id=safe_id,
            )
        else:
            result["counts"]["valid_quotes"] += 1
            rebuilt = prepared[0]
            expected_claim = verified.get(claim_id) if isinstance(claim_id, str) else None
            # Review can add an inference flag and conservatively downgrade its
            # status. Evidence warnings belong to the original validated draft;
            # rerunning their heuristic on the final reviewed flag would invent
            # a new warning and falsely call that valid downgrade tampering.
            warnings = (
                expected_claim["evidence_warnings"]
                if expected_claim is not None else rebuilt["evidence_warnings"]
            )
            if any(
                claim.get(name) != rebuilt[name]
                for name in (
                    "claim_id",
                    "source_id",
                    "chunk_id",
                    "source_field",
                    "source_section",
                )
            ) or claim.get("evidence_warnings") != warnings:
                _finding(
                    findings,
                    "saved_claim_provenance_mismatch",
                    "Saved claim identity or evidence warnings disagree with source.",
                    claim_id=safe_id,
                )
        status = claim.get("review_status")
        if not isinstance(status, str) or status not in REVIEW_STATUSES | {"UNREVIEWED"}:
            _finding(
                findings,
                "invalid_saved_review_status",
                "Saved review status is invalid.",
                claim_id=safe_id,
            )
        result["counts"]["supported"] += status == "SUPPORTED"
        result["counts"]["inferences"] += claim.get("is_inference") is True
        if status == "SUPPORTED" and (claim.get("is_inference") or claim.get("evidence_warnings")):
            _finding(
                findings,
                "unsafe_supported_claim",
                "Inference or warned evidence cannot be SUPPORTED.",
                claim_id=safe_id,
            )
        expected_claim = verified.get(claim_id) if isinstance(claim_id, str) else None
        if expected_claim is None:
            _finding(
                findings,
                "unattributable_saved_claim",
                "Claim is absent from validated saved stages.",
            )
        elif any(
            claim.get(name) != expected_claim.get(name)
            and not (
                expected_claim.get("review_method") == "unreviewed"
                and (
                    (name == "review_status" and claim.get(name) == "UNREVIEWED")
                    or (name == "review_method" and claim.get(name) is None)
                )
            )
            for name in _CLAIM_FIELDS
        ):
            _finding(
                findings,
                "saved_claim_review_mismatch",
                "Claim differs from independently rebuilt review.",
                claim_id=safe_id,
            )
    if set(verified) - seen:
        _finding(
            findings,
            "saved_claims_missing",
            "Validated stage claims are missing from the final annotation.",
        )
    summary = summarize_claims(list(verified.values()))
    result["search_focus"] = summary["search_focus"]
    if annotation.get("summary") != summary:
        _finding(
            findings,
            "summary_mismatch",
            "Summary or search focus differs from verified source claims.",
        )
    cache_rebuilt = (
        ai
        and complete
        and bool(chunks)
        and annotation.get("errors", []) == []
        and _cache_rebuilt_notices(annotation.get("issues"), set(chunk_map))
        and not any(finding["severity"] == "error" for finding in findings)
    )
    if cache_rebuilt:
        _finding(
            findings,
            "annotation_cache_rebuilt",
            "Recorded cache recovery was followed by independently validated complete review.",
            severity="info",
        )
        result["pipeline_health"]["status"] = (
            "degraded" if any(not row.get("historical") for row in result["diagnostics"])
            or prefilter_status == "fallback" else "ok"
        )
    elif ai and (annotation.get("issues") or annotation.get("errors")):
        _finding(
            findings,
            "historical_annotation_issues" if complete else "annotation_stage_failed",
            "Annotation contains recorded issues; review coverage was checked independently.",
            severity="info" if intentional else "warning" if complete else "error",
        )
    if (
        ((annotation.get("issues") and not cache_rebuilt) or prefilter_status == "fallback")
        and not result["diagnostics"]
    ):
        _finding(
            findings,
            "failure_cause_unavailable",
            "Historical errors did not record a specific cause; rerunning may add diagnostics.",
            severity="info",
        )
    severities = {finding["severity"] for finding in findings}
    result["integrity"] = (
        "error" if "error" in severities else ("warning" if "warning" in severities else "ok")
    )
    return result


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def evaluate_records(
    rows: list[tuple[str, JobRecord]],
    annotation_config: AnnotationConfig,
    prefilter_config: PrefilterConfig,
) -> dict[str, Any]:
    """Aggregate saved-integrity evidence, explicitly leaving accuracy unmeasured."""
    jobs = [
        dict(evaluate_annotation(record, annotation_config, prefilter_config), job_uid=uid)
        for uid, record in rows
    ]
    statuses = Counter(job["annotation_status"] for job in jobs)
    counts = {
        "jobs": len(jobs),
        "annotated": sum(job["method"] is not None for job in jobs),
        "reviewed": statuses["reviewed"],
        "partial": statuses["partial"],
        "local": statuses["local"],
        "unannotated": statuses["unannotated"],
        "suppressed": sum(job["prefilter_status"] == "suppressed" for job in jobs),
        "full_review_required": sum(job["full_review_required"] for job in jobs),
        "historical_partial": sum(
            job["historical_annotation_status"] == "partial" for job in jobs
        ),
        "local_rejected": sum(job["prefilter_status"] == "local_reject" for job in jobs),
        "prefilter_fallback": sum(job["prefilter_status"] == "fallback" for job in jobs),
        "jobs_with_errors": sum(job["integrity"] == "error" for job in jobs),
        "jobs_with_warnings": sum(job["integrity"] == "warning" for job in jobs),
    }
    totals = Counter()
    coverage = Counter()
    for job in jobs:
        totals.update(job["counts"])
        coverage.update(job["coverage"])
    codes = Counter(finding["code"] for job in jobs for finding in job["findings"])
    diagnostics = Counter(row["code"] for job in jobs for row in job["diagnostics"])
    return {
        "version": EVALUATION_VERSION,
        "scope": "saved_annotation_integrity",
        "semantic_accuracy": None,
        "accuracy_note": (
            "Source integrity is not semantic accuracy; a labelled reference set is required."
        ),
        "counts": counts,
        "claim_counts": dict(totals),
        "coverage": dict(coverage),
        "metrics": {
            "valid_quote_rate": _ratio(totals["valid_quotes"], totals["claims"]),
            "review_completeness": _ratio(
                totals["reviewed_claims"], totals["expected_review_claims"]
            ),
            "completed_chunk_rate": _ratio(coverage["completed_chunks"], coverage["chunks"]),
            "reviewed_source_coverage": _ratio(
                coverage["reviewed_source_chars"],
                sum(job["coverage"]["source_chars"] for job in jobs if job["method"] == "two_pass"),
            ),
        },
        "finding_counts": dict(sorted(codes.items())),
        "diagnostic_counts": dict(sorted(diagnostics.items())),
        "pipeline_health": {
            "jobs_degraded": sum(job["pipeline_health"]["status"] == "degraded" for job in jobs),
            "annotation_stage_errors": sum(
                job["pipeline_health"]["annotation_stage_errors"] for job in jobs
            ),
            "historical_annotation_stage_errors": sum(
                job["pipeline_health"]["historical_annotation_stage_errors"] for job in jobs
            ),
            "prefilter_stage_errors": sum(
                job["pipeline_health"]["prefilter_stage_errors"] for job in jobs
            ),
            "prefilter_fallback": counts["prefilter_fallback"],
        },
        "jobs": jobs,
    }
