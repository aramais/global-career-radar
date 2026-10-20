"""Dataset provenance beside closed gold-v2 rows; no inferred human answers."""

from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import os
import random
import re
import tempfile
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

SCHEMA = "evaluation-cohort-v1"
DEFAULT_REGISTRY = Path("data/local/evaluation/cohorts.jsonl")
ROLES = {"development", "calibration", "curated", "discovery", "seed_exposed", "holdout"}
_HASH = re.compile(r"[0-9a-f]{64}")
_EXPOSURE = {
    "discovery_seed",
    "negative_example",
    "prompt_demo",
    "calibration",
    "threshold_tuning",
    "developer_analysis",
    "inference",
}


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode()
    ).hexdigest()


def _object(pairs: list[tuple]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def read_object(path: Path) -> dict:
    def invalid(value: str) -> None:
        raise ValueError("Nonfinite JSON number: " + value)

    result = json.loads(
        path.read_text(encoding="utf-8"), object_pairs_hook=_object, parse_constant=invalid
    )
    if not isinstance(result, dict):
        raise ValueError("Expected a JSON object")
    return result


def write_new_text(path: Path, content: str) -> Path:
    """Publish complete UTF-8 text exclusively; pre-link failures leave no output.

    The sibling staging file is flushed, fsynced and closed before its hard link
    becomes visible. Existing lexical paths, including dangling symlinks, and
    concurrent publications are refused without replacement. Directory entries
    are not fsynced: this promises atomic visibility, not power-loss durability.
    A cleanup failure after linking can leave a complete output and its staging
    link; an existing output is never removed to roll back a reported error.
    Staging is removed only with verified ownership; unavailable inode identity
    can leave an orphan rather than risk deleting another process's file.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    identity = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=path.name + ".",
            suffix=".tmp", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            info = os.fstat(stream.fileno())
            identity = info.st_dev, info.st_ino
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
    finally:
        if temporary is not None and identity is not None:
            try:
                info = temporary.lstat()
                if (info.st_dev, info.st_ino) == identity:
                    temporary.unlink()
            except FileNotFoundError:
                pass
    return path


def write_new(path: Path, value: dict) -> Path:
    """Serialize JSON before creating artifacts, then publish it exclusively."""
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
    return write_new_text(path, payload + "\n")


def validate_cohort(manifest: dict) -> dict:
    required = {"schema", "cohort_id", "dataset_role", "rubric_version", "created_at", "members"}
    if (
        not isinstance(manifest, dict)
        or set(manifest) - (required | {"exposure_log", "notes", "provenance_review"})
        or not required <= set(manifest)
        or manifest.get("schema") != SCHEMA
    ):
        raise ValueError("Unsupported or nonclosed cohort sidecar")
    if manifest["dataset_role"] not in ROLES:
        raise ValueError("Unknown dataset_role")
    for field in ("cohort_id", "rubric_version", "created_at"):
        if not isinstance(manifest[field], str) or not manifest[field].strip():
            raise ValueError("Missing cohort provenance: " + field)
    datetime.fromisoformat(manifest["created_at"].replace("Z", "+00:00"))
    review = manifest.get("provenance_review")
    if review is not None:
        if (
            not isinstance(review, dict)
            or set(review)
            != {"reviewed_at", "reviewed_by", "prior_exposures", "clean_history_confirmed"}
            or type(review["clean_history_confirmed"]) is not bool
            or not isinstance(review["prior_exposures"], list)
            or not isinstance(review["reviewed_by"], str)
            or not review["reviewed_by"].strip()
        ):
            raise ValueError("Invalid historical exposure review")
        datetime.fromisoformat(review["reviewed_at"].replace("Z", "+00:00"))
        if review["prior_exposures"] and review["clean_history_confirmed"]:
            raise ValueError("Previously exposed examples cannot be declared a clean holdout")
    if not isinstance(manifest["members"], list) or not manifest["members"]:
        raise ValueError("Cohort members must be nonempty")
    seen = set()
    for row in manifest["members"]:
        if (
            not isinstance(row, dict)
            or not {"job_uid", "source_hash", "duplicate_group"} <= row.keys()
            or set(row)
            - {
                "job_uid",
                "source_hash",
                "duplicate_group",
                "original_labeled_at",
                "label_method",
                "selection",
                "url",
            }
        ):
            raise ValueError("Invalid cohort member fields")
        if (
            any(
                not isinstance(row[k], str) or not row[k].strip()
                for k in ("job_uid", "source_hash", "duplicate_group")
            )
            or not _HASH.fullmatch(row["source_hash"])
            or row["job_uid"] in seen
        ):
            raise ValueError("Invalid or duplicate cohort member identity")
        seen.add(row["job_uid"])
    return copy.deepcopy(manifest)


def load_cohort(path: Path) -> dict:
    result = validate_cohort(read_object(path))
    if result.get("exposure_log"):
        exposure = Path(result["exposure_log"])
        if not exposure.is_absolute():
            exposure = path.parent / exposure
        result["exposure_log"] = str(exposure.resolve())
    return result


def select_cases(cases: list[dict], manifest: dict) -> list[dict]:
    manifest = validate_cohort(manifest)
    members = {row["job_uid"]: row for row in manifest["members"]}
    selected = [row for row in cases if row.get("job_uid") in members]
    if {row["job_uid"] for row in selected} != set(members):
        raise ValueError("Cohort members are missing from gold")
    for row in selected:
        member = members[row["job_uid"]]
        if row.get("source_hash") != member["source_hash"]:
            raise ValueError("Cohort source hash does not match gold")
        for field, target in (("original_labeled_at", "labeled_at"), ("label_method", "method")):
            expected = member.get(field)
            if field == "label_method" and row.get("schema") == "vacancy-labels-v3":
                from job_intake.labeling.gold_v3 import METHOD_MAP

                # gold migrate-v3 renames v2 "assisted" to "curated"; provenance is unchanged.
                expected = METHOD_MAP.get(expected, expected)
            if field in member and expected != row.get(target):
                raise ValueError("Cohort label provenance changed; create a new cohort")
    return selected


def _matches(example: dict, members: list[dict]) -> bool:
    for member in members:
        if any(
            example.get(key) and example.get(key) == member.get(key)
            for key in ("job_uid", "source_hash", "duplicate_group", "url")
        ):
            return True
        source = example.get("source", {})
        if (
            isinstance(source, dict)
            and source.get("description_clean")
            and duplicate_group(source) == member.get("duplicate_group")
        ):
            return True
        if (
            isinstance(source, dict)
            and member.get("url") == source.get("original_url")
            and member.get("url")
        ):
            return True
    return False


def duplicate_group(source: dict) -> str:
    return digest(
        {
            field: " ".join(str(source.get(field) or "").casefold().split())
            for field in ("company", "title", "description_clean")
        }
    )


def validate_cohort_boundaries(manifests: list[dict]) -> None:
    """Reject holdout mirrors even when their source IDs/metadata differ."""
    for manifest in manifests:
        validate_cohort(manifest)
    for index, left in enumerate(manifests):
        for right in manifests[index + 1 :]:
            if left["dataset_role"] != "holdout" and right["dataset_role"] != "holdout":
                continue
            if any(_matches(member, right["members"]) for member in left["members"]):
                raise ValueError("Duplicate/source mirror crosses independent cohort boundary")


def registered_cohorts(path: Path = DEFAULT_REGISTRY) -> list[dict]:
    if not path.exists():
        return []
    result, previous = [], None
    for line in path.read_text(encoding="utf-8").splitlines():
        event = json.loads(line, object_pairs_hook=_object)
        if (
            event.get("schema") != "cohort-registration-v1"
            or event.get("previous_hash") != previous
            or event.get("event_hash")
            != digest({key: value for key, value in event.items() if key != "event_hash"})
        ):
            raise ValueError("Invalid cohort registry history")
        result.append(validate_cohort(event["manifest"]))
        previous = event["event_hash"]
    validate_cohort_boundaries(result)
    return result


def register_cohort(path: Path, manifest: dict) -> dict:
    """Explicit append-only global protection; an exposed duplicate cannot become holdout later."""
    manifest = validate_cohort(manifest)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        current = registered_cohorts(path)
        old = next((row for row in current if row["cohort_id"] == manifest["cohort_id"]), None)
        if old is not None:
            if old != manifest:
                raise ValueError("Registered cohort identity is immutable; create a new cohort")
            return {"registered": False, "cohort_id": manifest["cohort_id"], "idempotent": True}
        validate_cohort_boundaries([*current, manifest])
        stream.seek(0)
        lines = stream.read().splitlines()
        previous = json.loads(lines[-1])["event_hash"] if lines else None
        event = {
            "schema": "cohort-registration-v1",
            "previous_hash": previous,
            "manifest": manifest,
            "registered_at": datetime.now(UTC).isoformat(),
        }
        event["event_hash"] = digest(event)
        stream.seek(0, 2)
        stream.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
        stream.flush()
        return {
            "registered": True,
            "cohort_id": manifest["cohort_id"],
            "event_hash": event["event_hash"],
        }


def validate_registered_seed_usage(
    examples: list[dict], *, registry: Path = DEFAULT_REGISTRY, purpose: str = "discovery_seed"
) -> None:
    for manifest in registered_cohorts(registry):
        validate_seed_usage(manifest, examples, purpose=purpose)


def validate_seed_usage(
    manifest: dict, examples: list[dict], *, purpose: str = "discovery_seed"
) -> None:
    manifest = validate_cohort(manifest)
    if purpose not in _EXPOSURE:
        raise ValueError("Unknown exposure purpose")
    if (
        manifest["dataset_role"] == "holdout"
        and purpose != "inference"
        and any(_matches(row, manifest["members"]) for row in examples)
    ):
        raise ValueError("Holdout and its duplicate mirrors cannot be training examples")


def exposure_events(path: Path | None, manifest: dict) -> list[dict]:
    canonical = Path(manifest["exposure_log"]).resolve() if manifest.get("exposure_log") else None
    if canonical is not None and path is not None and path.resolve() != canonical:
        raise ValueError("Exposure journal override must match canonical cohort journal")
    path = canonical or path
    if path is None or not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        event = json.loads(line, object_pairs_hook=_object)
        if (
            not isinstance(event, dict)
            or event.get("schema") != "cohort-exposure-v1"
            or event.get("purpose") not in _EXPOSURE
        ):
            raise ValueError("Invalid exposure journal")
        if event.get("cohort_id") == manifest["cohort_id"]:
            rows.append(event)
    return rows


def independent_holdout(manifest: dict | None, *, log: Path | None = None) -> bool:
    if manifest is None or manifest.get("dataset_role") != "holdout":
        return False
    validate_cohort(manifest)
    review = manifest.get("provenance_review", {})
    if (
        not manifest.get("exposure_log")
        or review.get("clean_history_confirmed") is not True
        or review.get("prior_exposures")
        or any(row.get("label_method") != "blind" for row in manifest["members"])
    ):
        return False
    return not any(row["purpose"] != "inference" for row in exposure_events(log, manifest))


def record_exposure(
    path: Path,
    manifest: dict,
    *,
    job_uids: list[str],
    purpose: str,
    actor: str,
    context: dict | None = None,
) -> dict:
    """Append explicit usage, including developer analysis. Inference is not training."""
    manifest = validate_cohort(manifest)
    if manifest.get("exposure_log") and path.resolve() != Path(manifest["exposure_log"]).resolve():
        raise ValueError("Exposure journal override must match canonical cohort journal")
    if purpose not in _EXPOSURE or not isinstance(actor, str) or not actor.strip():
        raise ValueError("Exposure requires known purpose and actor")
    members = {row["job_uid"]: row for row in manifest["members"]}
    if not job_uids or set(job_uids) - set(members):
        raise ValueError("Exposure IDs must belong to cohort")
    event = {
        "schema": "cohort-exposure-v1",
        "cohort_id": manifest["cohort_id"],
        "manifest_hash": digest(manifest),
        "recorded_at": datetime.now(UTC).isoformat(),
        "purpose": purpose,
        "actor": actor,
        "job_uids": sorted(set(job_uids)),
        "context": copy.deepcopy(context or {}),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        stream.seek(0)
        for line in stream.read().splitlines():
            old = json.loads(line, object_pairs_hook=_object)
            if (
                not isinstance(old, dict)
                or old.get("schema") != "cohort-exposure-v1"
                or old.get("purpose") not in _EXPOSURE
            ):
                raise ValueError("Exposure destination contains other data; refusing to append")
        stream.seek(0, 2)
        stream.write(json.dumps(event, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n")
        stream.flush()
    return event


def record_report_exposure(
    manifest: dict | None,
    *,
    output: Path,
    exposure_log: Path | None = None,
    actor: str = "local-user",
) -> dict | None:
    """CLI hook after successful report persistence; DB and gold remain read-only."""
    if manifest is None:
        return None
    target = exposure_log or (
        Path(manifest["exposure_log"]) if manifest.get("exposure_log") else None
    )
    if target is None:
        if manifest["dataset_role"] == "holdout":
            raise ValueError("Holdout analysis requires an exposure journal")
        return None
    return record_exposure(
        target,
        manifest,
        job_uids=[row["job_uid"] for row in manifest["members"]],
        purpose="developer_analysis",
        actor=actor,
        context={"report": str(output.resolve())},
    )


def record_example_exposure(
    manifest: dict,
    examples: list[dict],
    *,
    purpose: str,
    actor: str,
    context: dict | None = None,
    exposure_log: Path | None = None,
) -> dict | None:
    """Record the cohort member identities represented by examples, including mirrors."""
    manifest = validate_cohort(manifest)
    matches = [
        member["job_uid"]
        for member in manifest["members"]
        if any(_matches(example, [member]) for example in examples)
    ]
    if not matches:
        return None
    target = exposure_log or (
        Path(manifest["exposure_log"]) if manifest.get("exposure_log") else None
    )
    if target is None:
        raise ValueError("Example use requires the cohort's canonical exposure journal")
    validate_seed_usage(manifest, examples, purpose=purpose)
    return record_exposure(
        target,
        manifest,
        job_uids=matches,
        purpose=purpose,
        actor=actor,
        context={
            **copy.deepcopy(context or {}),
            "example_job_uids": sorted(
                {row["job_uid"] for row in examples if isinstance(row.get("job_uid"), str)}
            ),
        },
    )


def record_registered_exposure(
    examples: list[dict],
    *,
    purpose: str,
    actor: str,
    context: dict | None = None,
    registry: Path = DEFAULT_REGISTRY,
    exclude_cohort_ids: set[str] | None = None,
) -> list[dict]:
    events = []
    for manifest in registered_cohorts(registry):
        if manifest["cohort_id"] in (exclude_cohort_ids or set()):
            continue
        event = record_example_exposure(
            manifest, examples, purpose=purpose, actor=actor, context=context
        )
        if event:
            events.append(event)
    return events


def sample_frame(
    frame: list[dict], *, quotas: dict[str, int], seed: int, mode: str = "audit"
) -> dict:
    """Freeze the complete frame, including zero-probability and unprocessed strata."""
    if mode not in {"audit", "top_k"} or type(seed) is not int:
        raise ValueError("Unknown sampling mode or noninteger seed")
    if not quotas or any(
        not isinstance(k, str) or type(v) is not int or v < 0 for k, v in quotas.items()
    ):
        raise ValueError("Quotas must be nonnegative stratum counts")
    rows = sorted(copy.deepcopy(frame), key=lambda row: row["job_uid"])
    if not rows or len({row["job_uid"] for row in rows}) != len(rows):
        raise ValueError("Frame identities must be nonempty and unique")
    groups = defaultdict(list)
    for row in rows:
        if not isinstance(row.get("stratum"), str) or not _HASH.fullmatch(
            row.get("source_hash", "")
        ):
            raise ValueError("Frame requires source hash and explicit stratum")
        groups[row["stratum"]].append(row)
    if set(quotas) - set(groups):
        raise ValueError("Quota refers to unknown stratum")
    rng = random.Random(seed)
    selected, strata = [], {}
    for name, population in sorted(groups.items()):
        n = min(quotas.get(name, 0), len(population))
        if mode == "audit":
            chosen = rng.sample(population, n)
        else:
            if any(type(row.get("rank")) is not int for row in population):
                raise ValueError("Top-k requires frozen integer rank")
            chosen = sorted(population, key=lambda row: (row["rank"], row["job_uid"]))[:n]
        chosen_ids = {row["job_uid"] for row in chosen}
        for row in population:
            row["selected"] = row["job_uid"] in chosen_ids
            row["inclusion_probability"] = (
                n / len(population) if mode == "audit" else float(row["selected"])
            )
            row["sampling_weight"] = (
                1 / row["inclusion_probability"] if row["inclusion_probability"] else None
            )
            if row["selected"]:
                selected.append(copy.deepcopy(row))
        strata[name] = {"population": len(population), "selected": n}
    return {
        "schema": "frozen-sample-v1",
        "mode": mode,
        "seed": seed,
        "frame_hash": digest(frame),
        "frame": rows,
        "strata": strata,
        "selected": sorted(selected, key=lambda row: row["job_uid"]),
        "population_estimable": all(row["inclusion_probability"] > 0 for row in rows),
        "note": "Accepted-only precision does not measure selector or market recall.",
    }


def population_estimates(sample: dict, labels: list[dict]) -> dict:
    """Design-weighted selector estimates for the declared finite frame, never a market claim."""
    if sample.get("schema") != "frozen-sample-v1":
        raise ValueError("Population estimates require a frozen sample")
    answers = {row["job_uid"]: row for row in labels}
    if len(answers) != len(labels):
        raise ValueError("Duplicate human answers")
    selected = sample["selected"]
    missing = []
    positive = accepted_positive = accepted = 0.0
    for row in selected:
        answer = answers.get(row["job_uid"], {})
        if (
            answer.get("source_hash") != row["source_hash"]
            or type(answer.get("relevance")) is not int
            or answer["relevance"] not in range(4)
        ):
            missing.append(row["job_uid"])
            continue
        weight = row["sampling_weight"]
        is_positive = answer["relevance"] >= 2
        is_accepted = row.get("outcome", row["stratum"]) == "accepted"
        positive += weight * is_positive
        accepted_positive += weight * is_positive * is_accepted
        accepted += weight * is_accepted
    estimable = sample["population_estimable"] and not missing
    return {
        "schema": "design-weighted-selector-v1",
        "frame_hash": sample["frame_hash"],
        "sample_N": len(selected),
        "missing_human_answers": missing,
        "population_frame_N": len(sample["frame"]),
        "status": "measured" if estimable else "insufficient_evidence",
        "precision": {
            "value": accepted_positive / accepted if accepted and not missing else None,
            "numerator": accepted_positive,
            "denominator": accepted,
        },
        "recall": {
            "value": accepted_positive / positive if estimable and positive else None,
            "numerator": accepted_positive,
            "denominator": positive,
        },
        "ci95": None,
        "note": "Horvitz-Thompson totals/ratio on this frame; zero inclusion prevents recall.",
    }


def union_search_frames(baseline: list[dict], candidate: list[dict]) -> list[dict]:
    """Freeze the union before auditing domain/freshness/search variants.

    Fetch failures and budget omissions remain frame rows with human labels absent;
    the sampling layer must assign them a nonzero probability before population recall.
    """
    combined = {}
    for name, rows in (("baseline", baseline), ("candidate", candidate)):
        seen = set()
        for row in rows:
            uid = row.get("job_uid")
            if (
                not isinstance(uid, str)
                or not uid
                or uid in seen
                or not isinstance(row.get("source_hash"), str)
                or not _HASH.fullmatch(row["source_hash"])
            ):
                raise ValueError("Search frame requires unique frozen source identities")
            seen.add(uid)
            current = combined.get(uid)
            if current and current["source_hash"] != row["source_hash"]:
                raise ValueError(
                    "Search variants observed different snapshots; reconcile before freezing"
                )
            if current is None:
                current = {"job_uid": uid, "source_hash": row["source_hash"], "observations": {}}
                combined[uid] = current
            current["observations"][name] = copy.deepcopy(row)
    for row in combined.values():
        membership = set(row["observations"])
        row["stratum"] = "shared" if len(membership) == 2 else next(iter(membership)) + "_only"
    return [combined[uid] for uid in sorted(combined)]


def adjudication_queue(cases: list[dict], disputes: list[dict]) -> dict:
    originals = {row["job_uid"]: row for row in cases}
    items = []
    for dispute in disputes:
        if (
            not isinstance(dispute, dict)
            or not {"job_uid", "reason"} <= dispute.keys()
            or dispute["job_uid"] not in originals
            or not str(dispute["reason"]).strip()
        ):
            raise ValueError("Adjudication requires an existing answer and reason")
        original = copy.deepcopy(originals[dispute["job_uid"]])
        items.append(
            {
                "job_uid": dispute["job_uid"],
                "original_answer": original,
                "original_hash": digest(original),
                "reason": dispute["reason"],
                "proposal": copy.deepcopy(dispute.get("proposal")),
                "status": "pending",
                "history": [],
            }
        )
    return {
        "schema": "gold-adjudication-v1",
        "created_at": datetime.now(UTC).isoformat(),
        "items": items,
    }


def resolve_adjudication(queue: dict, *, job_uid: str, answer: dict, actor: str) -> dict:
    result = copy.deepcopy(queue)
    if queue.get("schema") != "gold-adjudication-v1" or not actor.strip():
        raise ValueError("Invalid adjudication queue or actor")
    item = next((row for row in result["items"] if row["job_uid"] == job_uid), None)
    if (
        item is None
        or answer.get("job_uid") != job_uid
        or answer.get("source_hash") != item["original_answer"]["source_hash"]
    ):
        raise ValueError("Adjudication cannot move a label to another snapshot")
    original = item["original_answer"]
    if original.get("source"):
        from job_intake.annotation.frozen_eval import record_from_snapshot
        from job_intake.annotation.gold import validate_gold

        validation = validate_gold([(job_uid, record_from_snapshot(original["source"]))], [answer])
        if not validation["valid"]:
            raise ValueError("Adjudication answer must satisfy the closed human gold schema")
    item["history"].append(
        {"at": datetime.now(UTC).isoformat(), "actor": actor, "answer": copy.deepcopy(answer)}
    )
    item["status"] = "resolved"
    return result
