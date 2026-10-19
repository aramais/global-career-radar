"""Source-bound promotion drafts; human labels remain in their original journal."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from job_intake.annotation.gold import validate_gold
from job_intake.discovery.artifacts import (
    load_candidates,
    read_json,
    read_jsonl,
    record_from_snapshot,
)


def promotion_drafts(run_dir: Path, *, objective: str = "p1",
                     job_uids: list[str] | None = None,
                     profile_ids: list[str] | None = None) -> list[dict]:
    if objective not in {"p1", "p2"}:
        raise ValueError("Promotion requires an explicit p1 or p2 objective")
    run_dir = Path(run_dir).resolve()
    candidates = {row["job_uid"]: row for row in load_candidates(run_dir)}
    batch_path = run_dir / "batch.json"
    if batch_path.exists():
        digest = read_json(batch_path).get("candidates_sha256")
        actual = hashlib.sha256((run_dir / "candidates.jsonl").read_bytes()).hexdigest()
        if digest and digest != actual:
            raise ValueError("Candidates changed after human labeling")
    labels_path = run_dir / "labels.jsonl"
    labels = read_jsonl(labels_path)
    selected = set(job_uids) if job_uids is not None else None
    if selected is not None and not selected <= candidates.keys():
        raise ValueError("Promotion contains unknown candidate IDs")
    drafts, seen = [], set()
    for label in labels:
        uid = label.get("job_uid")
        if uid in seen:
            raise ValueError("Duplicate human answer in promotion")
        seen.add(uid)
        candidate = candidates.get(uid)
        if candidate is None or any(label.get(field) != candidate[field]
                                    for field in ("source", "source_hash")):
            raise ValueError("Promotion human answer no longer matches immutable source")
        validation = validate_gold([(uid, record_from_snapshot(candidate["source"]))], [label],
                                   profile_ids=profile_ids)
        if not validation["valid"]:
            raise ValueError("Invalid source-bound human answer for promotion")
        if selected is not None and uid not in selected:
            continue
        confirmed = (type(label.get("relevance")) is int and label["relevance"] in {2, 3}
                     and isinstance(label.get("role_label"), str)
                     and label.get("gate_outcome") in {"eligible", "ineligible", "unknown"}
                     and label.get("method") in {"blind", "assisted"}
                     and bool(label.get("labeled_at")))
        if not confirmed or (objective == "p1" and label.get("gate_outcome") != "eligible"):
            if selected is not None:
                raise ValueError("Selected candidate lacks human confirmation for " + objective)
            continue
        reference = {"run": str(run_dir), "labels": str(labels_path), "job_uid": uid,
                     "source_hash": label["source_hash"], "labeled_at": label["labeled_at"],
                     "method": label["method"], "relevance": label["relevance"],
                     "role_label": label["role_label"],
                     "label_sha256": hashlib.sha256(json.dumps(
                         label, sort_keys=True, ensure_ascii=False,
                         separators=(",", ":"), allow_nan=False).encode()).hexdigest(),
                     "gate_outcome": label.get("gate_outcome")}
        identity = uid + ":" + label["source_hash"] + ":" + objective
        key = hashlib.sha256(identity.encode()).hexdigest()
        drafts.append({"schema": "discovery-promotion-v1", "idempotency_key": key,
                       "job_uid": uid, "source": candidate["source"],
                       "source_hash": candidate["source_hash"], "objective": objective,
                       "human_reference": reference})
    if selected is not None and not selected <= seen:
        raise ValueError("Selected candidate has no human answer")
    return drafts
