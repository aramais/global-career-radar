#!/usr/bin/env python3
"""Compare offline role policies against completed human gold, without DB writes.

Example (run from the repository with its installed environment)::

    PYTHONPATH=src .venv/bin/python scripts/compare_role_classifier.py \
        --gold data/local/gold/vacancy-tags-v2-labeled.jsonl \
        --baseline data/local/eval/classifier-gold30-before-2026-10-17.json \
        --output data/local/eval/classifier-gold30-after-2026-10-17.json

Use --focus scoring for relevance agreements and strong-A precision/recall.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from dataclasses import fields
from datetime import UTC, datetime
from pathlib import Path

from job_intake.annotation.accuracy import _parser_only_drift, evaluate_vacancies, proportion
from job_intake.annotation.benchmark import read_jsonl
from job_intake.annotation.eval_runner import (
    read_saved_records,
    report_destinations,
    sqlite_path,
    write_report,
    write_text_report,
)
from job_intake.annotation.gold import validate_gold
from job_intake.annotation.rules_diff import run_rules_diff
from job_intake.config.settings import AppConfig, load_app_config
from job_intake.models.job import JobRecord

VERSION = "role-classifier-comparison-v1"
REPOSITORY = Path(__file__).resolve().parents[1]


def completed_cases(cases: list[dict]) -> list[dict]:
    """Select only complete human decisions, without completing or changing labels."""
    selected = [case for case in cases if isinstance(case.get("role_label"), str)
                and type(case.get("relevance")) is int and case["relevance"] in range(4)
                and case.get("gate_outcome") in {"eligible", "ineligible", "unknown"}]
    if not selected:
        raise ValueError("Gold contains no completed human decisions")
    uids = [case.get("job_uid") for case in selected]
    if any(not isinstance(uid, str) or not uid.strip() for uid in uids):
        raise ValueError("Completed human decisions require a nonempty job_uid")
    if len(set(uids)) != len(uids):
        raise ValueError("Completed human decisions require unique job_uid values")
    return selected


def predictions_for(revision: dict) -> dict[str, dict]:
    """Adapt selected outcomes; `other` is an unranked role, not tier C / score 0."""
    if revision.get("policy") != "single-role-v1":
        raise ValueError("Comparison requires a baseline with one selected role per vacancy")
    predictions = {}
    for row in revision["evaluations"]:
        uid = row["job_uid"]
        if uid in predictions:
            raise ValueError("Comparison requires exactly one selected role per vacancy")
        other = row["profile_id"] == "other"
        predictions[uid] = {
            "job_uid": uid, "role_label": row["profile_id"],
            "decision": row["decision"], "tier": None if other else row["tier"],
            "fit_score": None if other else row["score"],
            "route": None, "tags": {},
        }
    return predictions


def _source_fingerprint(rows: list[tuple[str, JobRecord]]) -> str:
    # Match rules-diff's complete source + saved annotation snapshot. This also
    # detects source changes between gold validation and policy evaluation.
    records = []
    for uid, record in rows:
        values = {field.name: getattr(record, field.name)
                  for field in fields(JobRecord) if field.init}
        values["posted_at"] = record.posted_at.isoformat() if record.posted_at else None
        values["status"] = record.status.value
        records.append({"job_uid": uid, "record": values})
    content = json.dumps(sorted(records, key=lambda row: row["job_uid"]),
                         sort_keys=True, ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _evaluation(cases: list[dict], predictions: dict) -> dict:
    evaluation = evaluate_vacancies(cases, predictions)
    # Tags are neither re-extracted nor evaluated by this comparison.
    result = {key: evaluation[key] for key in (
        "counts", "decision_prediction_coverage", "role_classification", "metrics", "ranking",
    )}
    result["scoring_agreement"] = scoring_metrics(cases, predictions)
    return result


def coarse_grade_matches(relevance: int, tier: str | None) -> bool:
    """Compare three grades: A=3, B=2, C/unscored=0 or 1."""
    return (relevance == 3 if tier == "A" else relevance == 2 if tier == "B"
            else relevance in {0, 1})


def scoring_metrics(cases: list[dict], predictions: dict[str, dict]) -> dict:
    """Measure relevance agreement independently of occupation and hiring gates.

    The full completed cohort remains in every agreement denominator. As specified
    by this comparison contract, unscored outcomes predict the coarse low grade;
    decision_prediction_coverage separately exposes that lack of a score.
    """
    confusion = dict.fromkeys(("TP", "FP", "FN", "TN"), 0)
    coarse_correct = strong_predicted = strong_correct = strong_actual = 0
    for case in cases:
        relevance = case["relevance"]
        tier = predictions.get(case["job_uid"], {}).get("tier")
        actual = relevance >= 2
        predicted = tier in {"A", "B"}
        key = ("TP" if predicted else "FN") if actual else ("FP" if predicted else "TN")
        confusion[key] += 1
        coarse_correct += coarse_grade_matches(relevance, tier)
        strong_predicted += tier == "A"
        strong_actual += relevance == 3
        strong_correct += tier == "A" and relevance == 3
    return {
        "binary_relevance_agreement": proportion(confusion["TP"] + confusion["TN"], len(cases)),
        "binary_confusion": confusion,
        "coarse_grade_agreement": proportion(coarse_correct, len(cases)),
        "precision_A_strong": proportion(strong_correct, strong_predicted),
        "recall_strong_A": proportion(strong_correct, strong_actual),
        "definitions": {
            "binary_relevance": "A/B predicts relevance >=2; C/unscored predicts relevance 0–1.",
            "coarse_grade": "A=3; B=2; C/unscored=0 or 1, without distinguishing 0 from 1.",
            "strong_A": "Precision: relevance 3 among A; recall: A among relevance 3.",
            "gate_scope": "Hiring decision and restrictions are separate from relevance agreement.",
        },
    }


def hiring_decision_comparison(before: dict[str, dict], after: dict[str, dict]) -> dict:
    """Describe gate-decision drift without requiring gate parity in future comparisons."""
    uids = sorted(set(before) | set(after))
    changed = [uid for uid in uids
               if before.get(uid, {}).get("decision") != after.get(uid, {}).get("decision")]
    return {
        "unchanged": not changed, "compared": len(uids),
        "changed_count": len(changed), "changed_job_uids": changed,
        "before_counts": dict(Counter(before.get(uid, {}).get("decision") for uid in uids)),
        "after_counts": dict(Counter(after.get(uid, {}).get("decision") for uid in uids)),
        "note": "Decision parity is descriptive; no relevance or role correctness is inferred.",
    }


def compare(config: AppConfig, *, gold: Path, baseline: str,
            repository: Path = REPOSITORY, focus: str = "classification",
            cohort: Path | None = None) -> dict:
    if focus not in {"classification", "scoring"}:
        raise ValueError("Comparison focus must be classification or scoring")
    raw_cases = read_jsonl(gold)
    cohort_manifest = None
    if cohort:
        from job_intake.annotation.cohorts import load_cohort, select_cases

        cohort_manifest = load_cohort(cohort)
        raw_cases = select_cases(raw_cases, cohort_manifest)
    cases = completed_cases(raw_cases)
    uids = [case["job_uid"] for case in cases]
    database, saved, problems = read_saved_records(
        config.database_url, limit=None, job_uids=uids, include_archived=True,
    )
    if problems:
        raise ValueError("Comparison requires valid saved source and annotation records")
    diff = run_rules_diff(config, baseline=baseline, repository=repository,
                          limit=None, job_uids=uids, include_archived=True)
    if diff["source_snapshot"]["fingerprint"] != _source_fingerprint(saved):
        raise ValueError("Source inputs changed during comparison; retry against a stable database")
    profile_ids = list(set(diff["baseline"]["profiles"]) | set(diff["current"]["profiles"]))
    from job_intake.annotation.source_compat import read_source_history

    validation = validate_gold(saved, cases, profile_ids=profile_ids,
                               source_history=read_source_history(config.database_url, cases))
    if not validation["valid"]:
        raise ValueError("Invalid completed gold: " + "; ".join(
            error["code"] for error in validation["errors"]))
    records = dict(saved)
    drift = {warning["job_uid"] for warning in validation["warnings"]
             if warning["code"] == "source_hash_changed"}
    parser_only = {
        case["job_uid"] for case in cases
        if case["job_uid"] in drift and _parser_only_drift(case, records[case["job_uid"]])
    }
    if drift - parser_only:
        raise ValueError("Gold source content changed; revalidate human labels before comparison")
    before, after = predictions_for(diff["baseline"]), predictions_for(diff["current"])
    if set(before) != set(uids) or set(after) != set(uids):
        raise ValueError("Both policy revisions must cover the exact completed gold cohort")
    comparison = []
    for case in cases:
        uid = case["job_uid"]
        old, new = before[uid], after[uid]
        old_correct = old["role_label"] == case["role_label"]
        new_correct = new["role_label"] == case["role_label"]
        result = ("improved" if new_correct else "regressed") \
            if old_correct != new_correct else "unchanged"
        old_score_correct = coarse_grade_matches(case["relevance"], old["tier"])
        new_score_correct = coarse_grade_matches(case["relevance"], new["tier"])
        score_result = ("improved" if new_score_correct else "regressed") \
            if old_score_correct != new_score_correct else "unchanged"
        comparison.append({
            "job_uid": uid, "company": records[uid].company, "title": records[uid].title,
            "url": records[uid].original_url, "human_role": case["role_label"],
            "human_relevance": case["relevance"], "human_gate": case["gate_outcome"],
            "before": old, "after": new, "role_result": result,
            "scoring_result": score_result,
            "role_changed": old["role_label"] != new["role_label"],
            "tier_changed": old["tier"] != new["tier"],
        })
    return {
        "version": VERSION, "focus": focus, "generated_at": datetime.now(UTC).isoformat(),
        "database": str(database), "database_access": "read_only", "api_calls": 0,
        "gold": {"path": str(gold.resolve()),
                 "sha256": hashlib.sha256(gold.read_bytes()).hexdigest(),
                 "total_rows": len(raw_cases), "completed_rows": len(cases),
                 "validation": validation, "parser_only_rebound": len(parser_only)},
        "selection": {"job_uids": uids, "archived": True, "complete_decisions_only": True,
                      "cohort": cohort_manifest},
        "source_snapshot": diff["source_snapshot"], "scope": diff["scope"],
        "before": _evaluation(cases, before), "after": _evaluation(cases, after),
        "role_changes": dict(Counter(row["role_result"] for row in comparison)),
        "scoring_changes": dict(Counter(row["scoring_result"] for row in comparison)),
        "hiring_decision_comparison": hiring_decision_comparison(before, after),
        "vacancies": comparison,
        "relevant_vacancies": [row for row in comparison if row["human_relevance"] >= 2],
        "rules_diff": diff,
        "limitations": [
            "Калибровочная выборка использована для исправлений; это не независимый тест.",
            "Платная маршрутизация и семантическое ранжирование исключены; "
            "сохранённые факты аннотации одинаковы в обеих версиях.",
            "other сохраняет профессию other, но не имеет tier и score; "
            "отсутствующие ранги не приносят gain в NDCG.",
            "Колонка «Результат профессии» отражает только правильность профессии. "
            "Изменение tier само по себе не подтверждает улучшение релевантности.",
            "Результат скоринга отражает совпадение грубой шкалы A=3, B=2, C/без оценки=0–1. "
            "Согласие бинарной релевантности проверяется отдельно; 0 и 1 не различаются.",
            "Релевантность не подтверждает условия найма: decision, blockers и risks "
            "сохраняются в исходных результатах rules_diff и проверяются отдельно.",
            "95% Wilson CI описывает пропорции этой малой выборки; "
            "NDCG — отношение DCG, для него биномиальный CI не рассчитывается.",
            "Оценки и профессии в рабочей базе не обновлены; отчёт сравнивает код офлайн.",
        ],
    }


def _cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ").replace("<", "&lt;").replace(
        ">", "&gt;")


def _metric(metric: dict) -> str:
    value = metric["value"]
    numerator, denominator = metric["numerator"], metric["denominator"]
    if value is None:
        return f"нет значения ({numerator}/{denominator})"
    if metric["ci95"] is not None:
        lower, upper = metric["ci95"]
        return (f"{numerator}/{denominator} = {value:.1%}; "
                f"95% CI {lower:.1%}–{upper:.1%}")
    return f"{value:.4f} (DCG {numerator:.4f}/{denominator:.4f})"


def render_markdown(report: dict) -> str:
    before, after = report["before"], report["after"]
    scoring_focus = report.get("focus") == "scoring"
    heading = "Скоринг" if scoring_focus else "Классификатор"
    lines = [f"# {heading}: до → после", "",
             f"Полных решений: **{report['gold']['completed_rows']}** из "
             f"{report['gold']['total_rows']}; релевантных (2–3): "
             f"**{before['counts']['relevant']}**. SQLite прочитана в режиме read-only; API: 0.",
             "", "| Метрика | До | После |", "|---|---|---|"]
    metrics = [
        ("Совпадение профессии", before["role_classification"]["accuracy"],
         after["role_classification"]["accuracy"]),
        ("Покрытие профессии (включая other)", before["role_classification"]["prediction_coverage"],
         after["role_classification"]["prediction_coverage"]),
        ("Покрытие decision + tier (other без tier)", before["decision_prediction_coverage"],
         after["decision_prediction_coverage"]),
    ]
    metrics += [(label, before["metrics"][key], after["metrics"][key]) for label, key in (
        ("Recall A/B среди релевантных", "relevant_recall_AB"),
        ("Precision A/B", "precision_AB"), ("NDCG@20", "ndcg@20"),
    )]
    if scoring_focus:
        metrics += [(label, before["scoring_agreement"][key], after["scoring_agreement"][key])
                    for label, key in (
                        ("Согласие релевантности (A/B ↔ 2–3)", "binary_relevance_agreement"),
                        ("Совпадение грубой оценки (A=3, B=2, C/без оценки=0–1)",
                         "coarse_grade_agreement"),
                        ("Precision A: оценка 3 среди A", "precision_A_strong"),
                        ("Recall A: A среди оценки 3", "recall_strong_A"),
                    )]
    lines += [f"| {label} | {_metric(old)} | {_metric(new)} |" for label, old, new in metrics]
    lines += ["", "Результат классификации: " + ", ".join(
        f"{name}: {report['role_changes'].get(name, 0)}"
        for name in ("improved", "regressed", "unchanged")) + ".", ""]
    if scoring_focus:
        lines += ["Результат скоринга (грубая шкала): " + ", ".join(
            f"{name}: {report['scoring_changes'].get(name, 0)}"
            for name in ("improved", "regressed", "unchanged")) + ".", "",
            "| Бинарная релевантность | До | После |", "|---|---:|---:|"]
        for key in ("TP", "FP", "FN", "TN"):
            lines.append(f"| {key} | {before['scoring_agreement']['binary_confusion'][key]} | "
                         f"{after['scoring_agreement']['binary_confusion'][key]} |")
        lines += ["", "A/B предсказывает релевантность 2–3; C/без оценки — 0–1. "
                  "Грубая шкала отдельно различает A=3 и B=2. Условия найма "
                  "не входят в эти метрики совпадения.", ""]
    decisions = report.get("hiring_decision_comparison")
    if decisions:
        lines += [f"Решения по условиям найма (decision): совпадают "
                  f"{decisions['compared'] - decisions['changed_count']}/{decisions['compared']}; "
                  f"изменены: {decisions['changed_count']}.", ""]

    def add_table(title: str, rows: list[dict]) -> None:
        score_header = " Результат скоринга |" if scoring_focus else ""
        score_separator = "---|" if scoring_focus else ""
        lines.extend([f"## {title}", "",
                      "| Вакансия | Ваша релевантность | Ваша профессия | "
                      "До: роль / tier / score | "
                      "После: роль / tier / score | Результат профессии |" + score_header,
                      "|---|---:|---|---|---|---|" + score_separator])
        for row in rows:
            outcomes = []
            for key in ("before", "after"):
                outcome = row[key]
                score = outcome["fit_score"]
                score = "—" if score is None else f"{score:g}"
                outcomes.append(f"{outcome['role_label']} / {outcome['tier'] or '—'} / {score}")
            cells = [f"{row['company']} — {row['title']} ({row['job_uid'][:10]})",
                     row["human_relevance"], row["human_role"], *outcomes, row["role_result"]]
            if scoring_focus:
                cells.append(row["scoring_result"])
            lines.append("| " + " | ".join(_cell(value) for value in cells) + " |")
        lines.append("")

    add_table("Релевантные вакансии", report["relevant_vacancies"])
    add_table("Все завершённые решения", report["vacancies"])
    lines.extend(["## Воспроизводимость", "",
                  f"Gold SHA-256: `{report['gold']['sha256']}`.", "",
                  f"Source snapshot: `{report['source_snapshot']['fingerprint']}`.", ""])
    for label, key in (("До", "baseline"), ("После", "current")):
        revision = report["rules_diff"][key]
        lines += [f"{label}: {revision['kind']}; "
                  f"`{revision.get('ref') or revision.get('snapshot_path')}`; "
                  f"commit `{revision.get('commit', 'см. snapshot')}`; "
                  f"policy `{revision['policy']}`; text `{revision['text_version']}`.", "",
                  f"Code SHA-256: `{revision['code_fingerprint']}`.", ""]
        lines += ["Версии профилей: " + ", ".join(
            f"`{profile}` = `{version}`"
            for profile, version in sorted(revision["profiles"].items())
        ) + ".", ""]
        lines += [f"Config `{path}` SHA-256: `{digest}`."
                  for path, digest in revision["config_fingerprints"].items()]
        lines.append("")
    lines += ["## Ограничения", "", *[f"- {note}" for note in report["limitations"]], ""]
    return "\n".join(lines)


def save_comparison(
    report: dict, output: Path, *, protected_paths: list[Path],
) -> tuple[Path, Path]:
    if output.suffix != ".json":
        raise ValueError("Output must be a JSON report and must not overwrite input files")
    # Validate both destinations before writing either of them.
    output, markdown = report_destinations(
        [output, output.with_suffix(".md")], protected_paths=protected_paths,
    )
    summary = render_markdown(report)
    json_path = write_report(report, output, protected_paths=[*protected_paths, markdown])
    write_text_report(summary, markdown, protected_paths=[*protected_paths, output])
    return json_path, markdown


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--baseline", required=True, help="Git ref or rules-diff JSON snapshot")
    parser.add_argument("--output", type=Path, required=True,
                        help="Write JSON and adjacent Markdown")
    parser.add_argument("--config", type=Path, default=REPOSITORY / "config/settings.yaml")
    parser.add_argument("--cohort", type=Path, help="Strict provenance sidecar; gold v2 unchanged")
    parser.add_argument("--exposure-log", type=Path, help="Append developer analysis for holdout")
    parser.add_argument("--focus", choices=("classification", "scoring"), default="classification",
                        help="Choose classification or relevance-scoring report focus")
    args = parser.parse_args()
    try:
        config = load_app_config(args.config)
        report = compare(config, gold=args.gold, baseline=args.baseline, focus=args.focus,
                         cohort=args.cohort)
        manifest = report["selection"].get("cohort")
        if manifest and manifest["dataset_role"] == "holdout" \
                and not args.exposure_log and not manifest.get("exposure_log"):
            raise ValueError("Holdout analysis requires --exposure-log or manifest exposure_log")
        protected = [args.gold, args.config, config.rules_path, config.search_profiles_path,
                     sqlite_path(config.database_url)]
        if args.cohort:
            protected.append(args.cohort)
        if Path(args.baseline).expanduser().is_file():
            protected.append(Path(args.baseline))
        outputs = save_comparison(report, args.output, protected_paths=protected)
        if manifest:
            from job_intake.annotation.cohorts import record_report_exposure

            record_report_exposure(manifest, output=args.output, exposure_log=args.exposure_log)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Comparison failed: {exc}\n")
    print("\n".join(str(path) for path in outputs))


if __name__ == "__main__":
    main()
