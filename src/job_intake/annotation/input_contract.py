"""Versioned paid-model inputs, independent of collection observations.

The public source hash remains a snapshot/provenance contract for gold and
discovery. A model cache instead follows the exact source units it consumed;
timestamps, scrape errors and transport settings do not change that input.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from typing import Any

from job_intake.annotation.text import source_identity, source_units
from job_intake.models.job import JobRecord

INPUT_CONTRACT_VERSION = "annotation-input-v1"
MODEL_KEY_VERSION = "model-input-v2"
_COMPLETENESS = ("description_complete", "description_truncated", "truncated")


def input_digest(payload: Any) -> str:
    return hashlib.sha256(json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str,
    ).encode()).hexdigest()


def source_input(source_id: str, units: list[dict], metadata: dict) -> dict:
    return {
        "version": INPUT_CONTRACT_VERSION, "source_id": source_id, "units": units,
        # These flags govern whether absence can be trusted. Missing flags have
        # their established default meanings; scrape observations are excluded.
        "completeness": {
            "description_complete": metadata.get("description_complete") is not False,
            "description_truncated": metadata.get("description_truncated") is True,
            "truncated": metadata.get("truncated") is True,
        },
    }


def annotation_input(record: JobRecord, units: list[dict] | None = None) -> dict:
    return source_input(
        source_identity(record), source_units(record) if units is None else units,
        record.source_metadata,
    )


def annotation_input_hash(record: JobRecord, units: list[dict] | None = None) -> str:
    return input_digest(annotation_input(record, units))


def model_spec(spec: Any, *, review: bool = False) -> dict:
    """Only response-affecting settings, never credentials or retry policy."""
    values = asdict(spec)
    names = ["provider", "base_url", "model", "reasoning_effort", "max_output_tokens"]
    if review and values.get("provider") == "jev":
        names += ["jev_min_probability", "jev_min_confidence", "jev_batch_size"]
    return {name: values[name] for name in names}


def exact_saved_input(saved: dict, record: JobRecord) -> bool:
    """Prove legacy compatibility from full exact units, never quotations alone."""
    current = annotation_input(record)
    if saved.get("units") != current["units"]:
        return False
    contract = saved.get("input_contract")
    if isinstance(contract, dict):
        return contract == current and saved.get("annotation_input_hash") == input_digest(current)
    # Legacy annotations persisted only description_complete. A newly truncated
    # source cannot inherit a review from that less expressive old contract.
    return (
        type(saved.get("description_complete")) is bool
        and saved["description_complete"] == current["completeness"]["description_complete"]
        and not any(current["completeness"][name] for name in _COMPLETENESS[1:])
    )
