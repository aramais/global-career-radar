from __future__ import annotations

import json
import os
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any

from job_intake.annotation.ai import (
    AnnotationModelClient,
    build_extraction_prompt,
    build_review_prompt,
    prompts_version,
)
from job_intake.annotation.schema import prepare_claims, review_claims, summarize_claims
from job_intake.annotation.text import (
    asserted_phrase_hits,
    chunk_source,
    source_hash,
    source_identity,
    source_units,
)
from job_intake.config.settings import AnnotationConfig
from job_intake.models.job import FilterDecision, JobEvaluation, JobRecord
from job_intake.utils.text import stable_hash

ANNOTATION_VERSION = "grounded-v1"


def _local_claims(units: list[dict]) -> list[dict]:
    """Direct field evidence and explicit clauses; no inference from advert language."""
    kinds = {
        "title": "role",
        "location_text": "hiring_location",
        "remote_text": "work_mode",
        "employment_type": "employment",
        "salary_text": "salary",
        "timezone_text": "timezone",
        "source_metadata.working_language": "working_language",
        "source_metadata.applicant_location_requirements": "hiring_location",
    }
    claims = []
    for unit in units:
        text, field = unit["text"], unit["field"]
        if not text.strip():
            continue
        kind = kinds.get(field)
        value = text.strip()
        if field.startswith("description") and unit.get("section") != "company":
            language = re.search(
                r"\b(?:the )?working language\s*(?:is\s+|will be\s+|:\s*)"
                r"(English|Portuguese|Spanish|French|German)\b",
                text,
                re.IGNORECASE,
            )
            uncertain = re.search(
                r"\?|\b(?:not confirmed|unconfirmed|uncertain|unknown|whether|"
                r"not specified|not established|might|may be|could be)\b",
                text,
                re.IGNORECASE,
            )
            if language and not uncertain:
                kind, value = "working_language", language[1]
            elif unit.get("section") == "role" and re.search(
                r"\b(?:you will|you'll|responsibilities|own|lead|manage|develop|drive)\b",
                text,
                re.IGNORECASE,
            ):
                kind = "responsibility"
        if not kind:
            continue
        claims.append(
            {
                "kind": kind,
                "value": value,
                "unit_id": unit["unit_id"],
                "source_snippet": text,
                "requirement": "informational",
                "polarity": "affirmative",
                "is_inference": False,
            }
        )
    return claims


def _drafts(payload: dict, source_id: str, units: list[dict], chunk_id: str) -> tuple[list, list]:
    """Every direct field also needs independent review when AI is enabled."""
    seeded, local_rejects = prepare_claims(
        {"claims": _local_claims(units)}, source_id, units, chunk_id
    )
    extracted, rejects = prepare_claims(payload, source_id, units, chunk_id)
    by_id = {claim["claim_id"]: claim for claim in seeded}
    by_id.update({claim["claim_id"]: claim for claim in extracted})
    return list(by_id.values()), local_rejects + rejects


def _reviewed(drafts: list[dict], payload: dict) -> tuple[list, list]:
    claims, rejects = review_claims(drafts, payload)
    invalid = {row["index"] for row in rejects}
    supplied = {
        row["claim_id"] for index, row in enumerate(payload["reviews"]) if index not in invalid
    }
    for claim in claims:
        claim["review_method"] = "model" if claim["claim_id"] in supplied else "unreviewed"
    return claims, rejects


class VacancyAnnotator:
    def __init__(self, config: AnnotationConfig) -> None:
        self.config = config
        self.client = AnnotationModelClient(config)
        self.stats = {"extraction_calls": 0, "review_calls": 0, "cache_hits": 0, "errors": 0}

    def _cache_key(self, record: JobRecord) -> str:
        config = asdict(self.config)
        config.pop("cache_dir")
        return stable_hash(
            json.dumps(
                {
                    "version": ANNOTATION_VERSION,
                    "source_hash": source_hash(record),
                    "config": config,
                    "prompts": prompts_version(),
                },
                sort_keys=True,
                ensure_ascii=False,
            )
        )

    @staticmethod
    def _write(path: Path, annotation: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(annotation, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)

    @staticmethod
    def _matches(annotation: object, record: JobRecord) -> bool:
        return isinstance(annotation, dict) and (
            annotation.get("version") == ANNOTATION_VERSION
            and annotation.get("source_id") == source_identity(record)
            and annotation.get("source_hash") == source_hash(record)
        )

    @staticmethod
    def _stages(cached: dict) -> dict:
        stages = cached.get("stages", {})
        if not isinstance(stages, dict):
            raise ValueError("Invalid annotation stages")
        if not all(
            isinstance(key, str) and isinstance(value, dict) for key, value in stages.items()
        ):
            raise ValueError("Invalid annotation chunk stages")
        return stages

    @staticmethod
    def _review_complete(drafts: list[dict], payload: dict) -> bool:
        _, rejects = review_claims(drafts, payload)
        invalid = {row["index"] for row in rejects}
        supplied = {
            row["claim_id"] for index, row in enumerate(payload["reviews"]) if index not in invalid
        }
        return all(claim["claim_id"] in supplied for claim in drafts)

    def _saved_review(self, saved: dict, record: JobRecord) -> dict | None:
        """Rebuild persisted facts from validated source evidence and exact-ID reviews."""
        if not self._matches(saved, record) or saved.get("method") != "two_pass":
            return None
        if saved.get("status") not in {"reviewed", "partial"}:
            return None
        size = saved.get("chunk_chars", self.config.chunk_chars)
        if type(size) is not int or size < 256:
            return None
        if not all(
            isinstance(saved.get(name), str) and saved[name].strip()
            for name in ("extract_model", "review_model")
        ):
            return None
        if saved["extract_model"] == saved["review_model"]:
            return None
        by_id = {}
        rejected = []
        completed = 0
        chunks = chunk_source(source_units(record), size)
        try:
            stages = self._stages(saved)
        except ValueError:
            stages = {}
        for chunk in chunks:
            try:
                stage = stages.get(chunk["chunk_id"], {})
                units = [unit for unit in chunk["units"] if unit["text"].strip()]
                drafts, bad = _drafts(
                    stage["extraction"], source_identity(record), units, chunk["chunk_id"]
                )
                rejected.extend(bad)
                review = stage.get("review", {"reviews": []})
                claims, bad = _reviewed(drafts, review)
                if self._review_complete(drafts, review):
                    completed += 1
                rejected.extend(bad)
                by_id.update({claim["claim_id"]: claim for claim in claims})
            except (KeyError, TypeError, ValueError):
                continue
        # A corrupt completed cache is rebuilt. An unfinished review stays unfinished offline.
        if saved["status"] == "reviewed" and completed != len(chunks):
            return None
        verified = {**saved, "claims": list(by_id.values()), "rejected": rejected}
        verified["stages"] = stages
        usage = saved.get("usage", [])
        verified["usage"] = (
            usage if isinstance(usage, list) and all(isinstance(row, dict) for row in usage) else []
        )
        verified["status"] = "reviewed" if completed == len(chunks) else "partial"
        verified["summary"] = summarize_claims(verified["claims"])
        verified["units"] = source_units(record)
        verified["coverage"] = {"chunks": len(chunks), "completed_chunks": completed}
        return verified

    def annotate(self, record: JobRecord, *, use_ai: bool = False) -> dict[str, Any]:
        if not self.config.enabled:
            record.annotation = {}
            return {}
        units = source_units(record)
        source_id, content_hash = source_identity(record), source_hash(record)
        key = self._cache_key(record)
        requested_ai = use_ai and self.config.ai_enabled
        available_ai = requested_ai and bool(os.getenv(self.config.api_key_env))
        saved = record.annotation
        annotation = {
            "version": ANNOTATION_VERSION,
            "source_id": source_id,
            "source_hash": content_hash,
            "cache_key": key,
            "source_url": record.original_url,
            "units": units,
            "status": "local",
            "method": "deterministic",
            "claims": [],
            "rejected": [],
            "issues": [],
            "stages": {},
            "usage": [],
            "extract_model": self.config.extract_model,
            "review_model": self.config.review_model,
            "chunk_chars": self.config.chunk_chars,
            "description_complete": record.source_metadata.get("description_complete") is not False,
        }
        semantic_units = [unit for unit in units if unit["text"].strip()]
        local, rejects = prepare_claims(
            {"claims": _local_claims(units)}, source_id, semantic_units, "local"
        )
        local, review_rejects = review_claims(
            local,
            {
                "reviews": [
                    {
                        "claim_id": claim["claim_id"],
                        "review_status": "SUPPORTED",
                        "review_notes": "Direct source field or explicit clause; local extraction.",
                        "is_inference": False,
                    }
                    for claim in local
                ]
            },
        )
        for claim in local:
            claim["review_method"] = "local"
        annotation["claims"] = local
        annotation["rejected"] = rejects + review_rejects
        if isinstance(saved, dict) and (not requested_ai or saved.get("cache_key") == key):
            verified = self._saved_review(saved, record)
            if verified is not None and (not available_ai or verified["status"] == "reviewed"):
                self.stats["cache_hits"] += 1
                record.annotation = verified
                return verified
            if verified is not None:
                annotation["stages"] = verified["stages"]
                annotation["usage"] = verified["usage"]
        if not available_ai:
            if self._matches(saved, record) and saved.get("method") == "two_pass":
                annotation["status"] = "partial"
                annotation["method"] = "two_pass"
                annotation["issues"].append(
                    "Previous independent review is incomplete or invalid; rerun with --ai."
                )
            if requested_ai:
                annotation["issues"].append("AI unavailable: set " + self.config.api_key_env)
            annotation["summary"] = summarize_claims(local)
            record.annotation = annotation
            return annotation

        path = Path(self.config.cache_dir) / source_id / (key + ".json")
        if path.is_file():
            try:
                cached = json.loads(path.read_text(encoding="utf-8"))
                if self._matches(cached, record) and cached.get("cache_key") == key:
                    annotation["stages"] = self._stages(cached)
                    usage = cached.get("usage", [])
                    if not isinstance(usage, list) or not all(
                        isinstance(row, dict) for row in usage
                    ):
                        raise ValueError("Invalid annotation usage")
                    annotation["usage"] = usage
            except (OSError, TypeError, ValueError):
                annotation["stages"] = {}
                annotation["usage"] = []
                annotation["issues"].append("Invalid cache ignored; extraction will be rebuilt.")
        annotation["method"] = "two_pass"
        claims, rejected = [], []
        chunks = chunk_source(units, self.config.chunk_chars)
        completed = 0
        for chunk in chunks:
            chunk_id = chunk["chunk_id"]
            chunk_units = [unit for unit in chunk["units"] if unit["text"].strip()]
            stage = annotation["stages"].setdefault(chunk_id, {})
            seeded, _ = _drafts({"claims": []}, source_id, chunk_units, chunk_id)
            claims.extend(seeded)
            try:
                if "extraction" in stage:
                    try:
                        prepare_claims(stage["extraction"], source_id, chunk_units, chunk_id)
                    except (TypeError, ValueError):
                        stage.clear()
                        annotation["issues"].append(chunk_id + ": invalid extraction cache rebuilt")
                if "extraction" not in stage:
                    self.stats["extraction_calls"] += 1
                    payload, usage = self.client.generate(
                        self.config.extract_model, build_extraction_prompt(source_id, chunk)
                    )
                    # An invalid envelope is never cached as a completed extraction.
                    prepare_claims(payload, source_id, chunk_units, chunk_id)
                    stage["extraction"] = payload
                    annotation["usage"].append(
                        {
                            "stage": "extract",
                            "chunk_id": chunk_id,
                            "model": self.config.extract_model,
                            **usage,
                        }
                    )
                    self._write(path, annotation)
                else:
                    self.stats["cache_hits"] += 1
                draft, bad = _drafts(stage["extraction"], source_id, chunk_units, chunk_id)
                rejected.extend(bad)
                if "review" in stage:
                    try:
                        if not self._review_complete(draft, stage["review"]):
                            raise ValueError("Missing exact-ID reviews")
                    except (TypeError, ValueError):
                        stage.pop("review")
                        annotation["issues"].append(chunk_id + ": invalid review cache rebuilt")
                if "review" not in stage:
                    if draft:
                        self.stats["review_calls"] += 1
                        payload, usage = self.client.generate(
                            self.config.review_model, build_review_prompt(source_id, chunk, draft)
                        )
                        review_claims(draft, payload)
                        stage["review"] = payload
                        annotation["usage"].append(
                            {
                                "stage": "review",
                                "chunk_id": chunk_id,
                                "model": self.config.review_model,
                                **usage,
                            }
                        )
                    else:
                        stage["review"] = {"reviews": []}
                    self._write(path, annotation)
                reviewed, bad = _reviewed(draft, stage["review"])
                rejected.extend(bad)
                claims.extend(reviewed)
                if not self._review_complete(draft, stage["review"]):
                    raise ValueError("Missing exact-ID reviews")
                completed += 1
            except Exception as exc:  # Any model failure must leave the vacancy reviewable.
                self.stats["errors"] += 1
                annotation["issues"].append(chunk_id + ": " + type(exc).__name__)
                # Preserve valid drafts even when the reviewer is unavailable.
                if "extraction" in stage:
                    try:
                        draft, bad = _drafts(stage["extraction"], source_id, chunk_units, chunk_id)
                        if "review" in stage:
                            draft, review_bad = _reviewed(draft, stage["review"])
                            rejected.extend(review_bad)
                        claims.extend(draft)
                        rejected.extend(bad)
                    except (TypeError, ValueError):
                        stage.clear()
                self._write(path, annotation)
        # A local field cannot bypass the independent reviewer in the AI path.
        by_id = {claim["claim_id"]: claim for claim in claims}
        annotation["claims"] = list(by_id.values())
        annotation["rejected"].extend(rejected)
        annotation["coverage"] = {"chunks": len(chunks), "completed_chunks": completed}
        annotation["status"] = "reviewed" if completed == len(chunks) else "partial"
        annotation["summary"] = summarize_claims(annotation["claims"])
        self._write(path, annotation)
        record.annotation = annotation
        return annotation


def apply_annotation_constraints(record: JobRecord, evaluation: JobEvaluation, engine) -> None:
    """Only supported direct statements can resolve uncertainty; drafts never affect tiers."""
    annotation = record.annotation
    if not annotation or evaluation.decision == FilterDecision.REJECT:
        return
    summary = annotation.get("summary", {})
    knowns = summary.get("knowns", [])
    confirmed_language = confirmed_geography = False
    required_locations = []
    required_languages = []
    for claim in knowns:
        if claim.get("requirement") not in {
            "required",
            "informational",
        }:
            continue
        value, kind = claim["value"], claim["kind"]
        if claim.get("polarity") == "negative":
            if kind == "hiring_location" and engine._target_residency(value):
                evaluation.blocker_signals.append("annotation:hiring_exclusion:" + value)
            elif kind in {"working_language", "work_mode", "status", "work_authorization"}:
                evaluation.risks.append("annotation:" + kind + "_exclusion_unconfirmed")
            continue
        if kind == "working_language" and asserted_phrase_hits(
            value, engine.rules.required_languages or [], required=True
        ):
            confirmed_language = True
        if kind == "hiring_location" and asserted_phrase_hits(
            engine._geo_text(value),
            [
                engine._geo_text(place)
                for place in (
                    engine.rules.target_geographies + (engine.rules.onsite_locations or [])
                )
            ],
            required=True,
        ):
            confirmed_geography = True
        if claim.get("requirement") == "required":
            if kind == "hiring_location":
                required_locations.append(value)
            if kind == "working_language":
                required_languages.append(value)
            if kind == "work_authorization":
                evaluation.risks.append("annotation:work_authorization_unconfirmed")
            if kind == "timezone" and not asserted_phrase_hits(
                value, engine.rules.timezone_allowed
            ):
                evaluation.risks.append("annotation:timezone_compatibility_unconfirmed")
            if (
                kind == "work_mode"
                and not asserted_phrase_hits(value, ["remote", "remotely"])
                and not engine._allowed_onsite(record)
            ):
                evaluation.risks.append("annotation:work_mode_compatibility_unconfirmed")
        if kind == "status" and asserted_phrase_hits(
            value, ["closed", "filled", "no longer accepting", "no longer available"]
        ):
            evaluation.blocker_signals.append("annotation:status:" + value)
        if kind in {"role", "responsibility"} and asserted_phrase_hits(
            value, engine.rules.positive_title_signals + engine.rules.positive_description_signals
        ):
            evaluation.bridge_role = True
            evaluation.matched_signals.append("grounded_role:" + value[:160])
    # Failed chunks can contain additional restrictions; never promote incomplete work to PASS.
    if annotation.get("status") == "partial" or not annotation.get("description_complete", True):
        evaluation.risks.append("annotation:incomplete_source_review")
    else:
        resolved = set()
        if confirmed_language:
            resolved.add("language:working_language_unconfirmed")
        if confirmed_geography:
            resolved.add("geography:eligibility_unconfirmed")
        evaluation.risks = [risk for risk in evaluation.risks if risk not in resolved]
    if annotation.get("method") == "two_pass":
        if engine.rules.required_languages and not confirmed_language:
            evaluation.risks.append("language:working_language_unconfirmed")
        if engine.rules.target_geographies and not confirmed_geography:
            evaluation.risks.append("geography:eligibility_unconfirmed")
    if summary.get("contradictions"):
        evaluation.risks.append("annotation:conflicting_requirements")
    if required_locations and not any(
        engine._target_location(value) for value in required_locations
    ):
        evaluation.blocker_signals.append(
            "annotation:hiring_location:" + ", ".join(required_locations)
        )
    languages = engine.rules.required_languages or []
    if (
        languages
        and required_languages
        and not any(
            asserted_phrase_hits(value, languages, required=True) for value in required_languages
        )
    ):
        evaluation.blocker_signals.append(
            "annotation:working_language:" + ", ".join(required_languages)
        )
    evaluation.risks = sorted(set(evaluation.risks))
    if evaluation.blocker_signals:
        evaluation.decision = FilterDecision.REJECT
        evaluation.reasons.append("Reviewed source evidence confirms a mandatory restriction.")
    elif evaluation.risks and (
        engine.rules.recall_first
        or any(risk.startswith("annotation:") for risk in evaluation.risks)
    ):
        evaluation.decision = FilterDecision.REVIEW
    elif (
        evaluation.decision == FilterDecision.REVIEW
        and evaluation.bridge_role
        and not evaluation.risks
    ):
        evaluation.decision = FilterDecision.PASS
    evaluation.fit_reason = engine._build_fit_reason(
        evaluation.decision,
        evaluation.matched_signals,
        evaluation.blocker_signals,
        evaluation.risks,
    )
    evaluation.audit_log.append(
        "Source-grounded annotation: " + annotation.get("method", "unknown")
    )
