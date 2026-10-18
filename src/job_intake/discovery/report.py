"""Read-only effectiveness report, joined exclusively to source-bound human answers."""
from __future__ import annotations

import hashlib
import math
from pathlib import Path

from job_intake.annotation.gold import GOLD_SCHEMA
from job_intake.discovery.artifacts import load_candidates, read_json, read_jsonl


def _number(value, name: str) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise ValueError("Invalid nonnegative report value: " + name)
    return float(value)


def _ratio(numerator: float, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def build_report(run_dir: Path) -> dict:
    run_dir = Path(run_dir)
    manifest = read_json(run_dir / "manifest.json")
    if manifest.get("schema") != "discovery-run-v1":
        raise ValueError("Unsupported discovery run")
    candidates = {row["job_uid"]: row for row in load_candidates(run_dir)}
    labels_path = run_dir / "labels.jsonl"
    labels = read_jsonl(labels_path) if labels_path.exists() else []
    batch_path = run_dir / "batch.json"
    batch = read_json(batch_path) if batch_path.exists() else {}
    digest = batch.get("candidates_sha256")
    if digest and digest != hashlib.sha256((run_dir / "candidates.jsonl").read_bytes()).hexdigest():
        raise ValueError("Candidate sources changed after batch preparation")
    human, seen, complete = {}, set(), 0
    label_versions = {}
    if labels_path.exists():
        raw_lines = [line for line in labels_path.read_bytes().splitlines(keepends=True)
                     if line.strip()]
        label_versions = {row["job_uid"]: hashlib.sha256(line).hexdigest()
                          for row, line in zip(labels, raw_lines, strict=True)}
    for label in labels:
        uid = label.get("job_uid")
        if uid in seen or uid not in candidates:
            raise ValueError("Duplicate or foreign discovery human answer")
        seen.add(uid)
        source = candidates[uid]
        if label.get("schema") != GOLD_SCHEMA or label.get("source_hash") != source["source_hash"] \
                or label.get("source") != source["source"]:
            raise ValueError("Human answer no longer matches discovery source")
        relevance = label.get("relevance")
        if relevance is None:
            continue
        if type(relevance) is not int or relevance not in range(4) \
                or label.get("method") not in ("blind", "assisted") \
                or not isinstance(label.get("labeled_at"), str) or not label["labeled_at"]:
            raise ValueError("Report requires explicit dated human relevance")
        human[uid] = label
        complete += bool(label.get("role_label") and label.get("gate_outcome"))
    relevant = {uid for uid, label in human.items() if label["relevance"] >= 2}
    strong = {uid for uid, label in human.items() if label["relevance"] == 3}
    expense_rows = manifest.get("expenses")
    if not isinstance(expense_rows, list):
        raise ValueError("Discovery expense ledger is required")
    total = sum(_number(row.get("cost_usd"), "expense") for row in expense_rows)
    declared = _number(manifest.get("cost_usd"), "run cost")
    if abs(total - declared) > 1e-8:
        raise ValueError("Discovery expense total does not match ledger")
    elapsed = _number(manifest.get("duration_seconds"), "duration")
    active, event_ids, timed_ids = 0.0, set(), set()
    events_path = run_dir / "review-events.jsonl"
    events = read_jsonl(events_path) if events_path.exists() else []
    pending_path = run_dir / ".review-pending.json"
    if pending_path.exists():
        pending = read_json(pending_path)
        current = label_versions.get(pending.get("job_uid"))
        if current == pending.get("version"):
            events.append(pending)  # Committed label; timing journal awaits durable append.
        elif current != pending.get("before_version"):
            raise ValueError("Unresolved review transaction does not match human labels")
    if events:
        for event in events:
            event_id, uid = event.get("event_id"), event.get("job_uid")
            if not isinstance(event_id, str) or not event_id or uid not in seen:
                raise ValueError("Invalid discovery review event")
            if event_id in event_ids:
                continue
            if event.get("source_hash") != candidates[uid]["source_hash"]:
                raise ValueError("Review timing source mismatch")
            event_ids.add(event_id)
            active += _number(event.get("seconds"), "review seconds")
            timed_ids.add(uid)

    def cohort(ids):
        pool = set(ids)
        reviewed = pool & human.keys()
        positives = pool & relevant
        return {"queued": len(pool), "reviewed": len(reviewed), "relevant": len(positives),
                "precision": _ratio(len(positives), len(reviewed))}

    return {
        "schema": "discovery-report-v1", "run_id": manifest["run_id"],
        "run_status": manifest["status"],
        "counts": {"candidates": len(candidates), "queued": len(labels),
                   "reviewed": len(human), "relevant": len(relevant), "strong": len(strong),
                   "complete": complete, "unlabeled": len(labels) - len(human),
                   "complete_relevant": sum(bool(human[uid].get("role_label")
                                                 and human[uid].get("gate_outcome"))
                                            for uid in relevant)},
        "cost": {"total_usd": total, "usd_per_relevant": _ratio(total, len(relevant)),
                 "basis": "tariff_estimate", "uncertain_requests": sum(
                     row.get("status") in ("uncertain", "pending") for row in expense_rows),
                 "includes": "All recorded search and model attempts, including failures",
                 "excludes": "Local compute, subscriptions, engineering and human hourly rate"},
        "time": {"machine_wall_seconds": elapsed, "human_active_seconds": active,
                 "human_seconds_per_relevant": _ratio(active, len(relevant))
                 if len(timed_ids & human.keys()) == len(human) else None,
                 "machine_seconds_per_relevant": _ratio(elapsed, len(relevant)),
                 "reviewed_with_timing": len(timed_ids & human.keys()),
                 "relevant_per_machine_hour": _ratio(3600 * len(relevant), elapsed)},
        "precision": _ratio(len(relevant), len(human)),
        "cohorts": {"top": cohort(batch.get("top_ids", [])),
                    "random": cohort(batch.get("random_ids", []))},
        "hiring_among_relevant": {name: sum(human[uid].get("gate_outcome") == gate
                                             for uid in relevant)
                                  for name, gate in (("eligible", "eligible"),
                                                     ("unknown", "unknown"),
                                                     ("ineligible", "ineligible"),
                                                     ("unanswered", None))},
        "limitations": [
            "Priority and model scores never count as human relevance.",
            "No positives means unit costs/times are undefined, not zero.",
            "Top-ranked and random cohorts have separate precision; neither is market recall.",
            "Human time covers saved active review intervals; external/unsaved work is unmeasured.",
            "Tariff estimates are not provider invoices; uncertain requests retain reserved costs.",
        ],
    }


def report_markdown(report: dict) -> str:
    counts, cost, timing = report["counts"], report["cost"], report["time"]

    def value(number, suffix=""):
        return "не определено" if number is None else f"{number:.4f}{suffix}"

    return (
        f"# Результаты поиска {report['run_id']}\n\n"
        f"Кандидатов: {counts['candidates']}; в партии: {counts['queued']}; "
        f"просмотрено: {counts['reviewed']}.\n\n"
        f"Подтверждено релевантных (≥2): **{counts['relevant']}**; "
        f"сильных совпадений (3): **{counts['strong']}**.\n\n"
        "| Показатель | Значение |\n|---|---:|\n"
        f"| Расходы API по тарифу | ${cost['total_usd']:.4f} |\n"
        f"| На подтверждённую релевантную вакансию | {value(cost['usd_per_relevant'], ' USD')} |\n"
        f"| Время выполнения поиска | {timing['machine_wall_seconds']:.1f} с |\n"
        f"| Учтённое активное время разметки | {timing['human_active_seconds']:.1f} с |\n"
        f"| Время разметки на релевантную | {value(timing['human_seconds_per_relevant'], ' с')} |\n"
        f"| Время поиска на релевантную | "
        f"{value(timing['machine_seconds_per_relevant'], ' с')} |\n\n"
        f"Просмотренных с учётом времени: {timing['reviewed_with_timing']} "
        f"из {counts['reviewed']}. Запросов с неопределённым списанием: "
        f"{cost['uncertain_requests']}.\n\n"
        "Стоимость — оценка по журналу тарифов, включая неудачные попытки; "
        "инфраструктура, подписки, разработка и стоимость часа человека не включены. "
        "Человеческая метка и допустимость найма учитываются отдельно.\n"
    )
