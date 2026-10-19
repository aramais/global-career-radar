"""Runtime cohort checks and canonical provenance for prepared discovery plans."""
from __future__ import annotations

import fcntl
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from job_intake.annotation import cohorts


def validate_plan_examples(plan: dict) -> None:
    examples = [*plan["seeds"], *plan.get("counterexamples", [])]
    if set(plan.get("holdout_job_uids", [])) & {row["job_uid"] for row in examples}:
        raise ValueError("Holdout cannot be a discovery seed or counterexample")
    manifest = plan.get("cohort")
    if manifest is not None:
        cohorts.validate_seed_usage(manifest, examples, purpose="discovery_seed")
        registered = cohorts.registered_cohorts(cohorts.DEFAULT_REGISTRY)
        if any(row["cohort_id"] == manifest["cohort_id"] and row != manifest for row in registered):
            raise ValueError("Prepared cohort differs from its immutable registered manifest")
    # Always consult the live registry. A plan created before registration cannot
    # freeze an obsolete assertion that its examples are outside the holdout.
    cohorts.validate_registered_seed_usage(examples, registry=cohorts.DEFAULT_REGISTRY,
                                          purpose="discovery_seed")


def record_plan_exposure(plan: dict, *, actor: str, stage: str,
                         exposure_log: Path | None = None,
                         context: dict | None = None,
                         local_path: Path | None = None) -> list[dict]:
    validate_plan_examples(plan)
    manifest = plan.get("cohort")
    if exposure_log is not None and manifest is None:
        raise ValueError("An exposure journal override requires an explicit cohort")
    events = []
    common = {"stage": stage, "plan_hash": cohorts.digest(plan), **(context or {})}
    for examples, purpose in (
        (plan["seeds"], "prompt_demo" if stage in {"selector", "rerank"} else "discovery_seed"),
        (plan.get("counterexamples", []), "negative_example"),
    ):
        if not examples:
            continue
        if manifest is not None:
            event = cohorts.record_example_exposure(
                manifest, examples, purpose=purpose, actor=actor, context=common,
                exposure_log=exposure_log)
            if event is not None:
                events.append(event)
        events.extend(cohorts.record_registered_exposure(
            examples, purpose=purpose, actor=actor, context=common,
            registry=cohorts.DEFAULT_REGISTRY,
            exclude_cohort_ids={manifest["cohort_id"]} if manifest is not None else None))
    if local_path is not None:
        record_local_exposure(local_path, plan, actor=actor, stage=stage, context=context)
    return events


def record_local_exposure(path: Path, plan: dict, *, actor: str, stage: str,
                          context: dict | None = None) -> None:
    """Run-local trace covers legacy/unregistered examples without registering a cohort."""
    events = []
    for field, purpose in (
        ("seeds", "prompt_demo" if stage in {"selector", "rerank"} else "discovery_seed"),
        ("counterexamples", "negative_example"),
    ):
        examples = plan.get(field, [])
        if not examples:
            continue
        events.append({
            "schema": "discovery-example-exposure-v1", "actor": actor, "stage": stage,
            "purpose": purpose, "phase": "before_provider_call",
            "recorded_at": datetime.now(UTC).isoformat(), "plan_hash": cohorts.digest(plan),
            "examples": [{key: row.get(key) for key in (
                "job_uid", "source_hash", "snapshot_hash")} for row in examples],
            "context": context or {},
        })
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        os.chmod(path, 0o600)
        fcntl.flock(handle, fcntl.LOCK_EX)
        for event in events:
            handle.write(json.dumps(event, sort_keys=True, ensure_ascii=False, allow_nan=False)
                         + "\n")
        handle.flush()
        os.fsync(handle.fileno())
