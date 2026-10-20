"""Unified 0–3 relevance metrics for human gold and any machine labeler.

Only cases with an integer human relevance enter. A missing prediction stays in the
denominator of coverage, exact/±1 agreement, the confusion matrix and recall; the
ordinal statistics (MAE, QWK, Spearman) are computed on covered pairs and report
their own N. All shares use ``accuracy.proportion`` with a Wilson interval.
"""

from __future__ import annotations

import math
import random
from collections import Counter

from job_intake.labeling.schema import (
    CONFIDENCE,
    DOMAIN_REQUIREMENTS,
    INDUSTRIES,
    LABELS_SCHEMA,
    PREFERRED_FOCUS,
    RELEVANCE_VALUES,
    TAG_VALUES,
)

METRICS_VERSION = "relevance-scale-v1"
_GATE_ORDER = {"eligible": 0, "unknown": 1, "ineligible": 2}
_CONFIDENCE_ORDER = {value: index for index, value in enumerate(CONFIDENCE)}
_RANDOM_SEED = 1729
_RANDOM_REPEATS = 256


def _relevance(row: dict) -> int | None:
    value = row.get("relevance") if isinstance(row, dict) else None
    return value if type(value) is int and value in RELEVANCE_VALUES else None


def has_relevance(predictions: dict[str, dict]) -> bool:
    """True when at least one prediction carries an integer relevance."""
    return any(_relevance(row) is not None for row in predictions.values())


def _ratio(value: float | None, n: int, note: str) -> dict:
    return {"value": round(value, 6) if value is not None else None, "numerator": None,
            "denominator": n, "ci95": None, "note": note}


def quadratic_weighted_kappa(pairs: list[tuple[int, int]]) -> float | None:
    """QWK over the fixed 0–3 scale; undefined for one human class or no disagreement room."""
    if not pairs or len({gold for gold, _ in pairs}) < 2:
        return None
    k = len(RELEVANCE_VALUES)
    observed = Counter(pairs)
    gold = Counter(value for value, _ in pairs)
    predicted = Counter(value for _, value in pairs)
    n = len(pairs)

    def weight(i: int, j: int) -> float:
        return (i - j) ** 2 / (k - 1) ** 2

    disagreement = sum(weight(i, j) * observed[(i, j)] for i in range(k) for j in range(k))
    expected = sum(weight(i, j) * gold[i] * predicted[j] / n for i in range(k) for j in range(k))
    return None if not expected else 1 - disagreement / expected


def _ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=values.__getitem__)
    ranks = [0.0] * len(values)
    start = 0
    while start < len(order):
        end = start
        while end + 1 < len(order) and values[order[end + 1]] == values[order[start]]:
            end += 1
        for index in order[start:end + 1]:
            ranks[index] = (start + end) / 2 + 1
        start = end + 1
    return ranks


def spearman(pairs: list[tuple[int, int]]) -> float | None:
    if len(pairs) < 2:
        return None
    left = _ranks([gold for gold, _ in pairs])
    right = _ranks([value for _, value in pairs])
    mean_left, mean_right = sum(left) / len(left), sum(right) / len(right)
    covariance = sum((a - mean_left) * (b - mean_right) for a, b in zip(left, right, strict=True))
    spread = math.sqrt(sum((a - mean_left) ** 2 for a in left)
                       * sum((b - mean_right) ** 2 for b in right))
    return covariance / spread if spread else None


def queue_order(rows: list[tuple[str, dict]]) -> list[str]:
    """Review queue: relevance ↓, gate eligible → unknown → ineligible, confidence ↓,
    fit_score ↓, job_uid. Rows without an integer relevance are not queued."""
    def score(row: dict) -> float:
        value = row.get("fit_score")
        return -value if type(value) in {int, float} and math.isfinite(value) else math.inf

    queued = [(uid, row) for uid, row in rows if _relevance(row) is not None]
    queued.sort(key=lambda item: (
        -item[1]["relevance"], _GATE_ORDER.get(item[1].get("gate_outcome"), len(_GATE_ORDER)),
        _CONFIDENCE_ORDER.get(item[1].get("confidence"), len(_CONFIDENCE_ORDER)),
        score(item[1]), item[0],
    ))
    return [uid for uid, _ in queued]


def _class_metrics(pairs: list[tuple[str, str | None]], classes: list[str]) -> dict:
    from job_intake.annotation.accuracy import proportion

    scores = {}
    for value in classes:
        tp = sum(gold == value and predicted == value for gold, predicted in pairs)
        fp = sum(gold != value and predicted == value for gold, predicted in pairs)
        fn = sum(gold == value and predicted != value for gold, predicted in pairs)
        if tp + fp + fn:
            scores[value] = proportion(2 * tp, 2 * tp + fp + fn)
    f1s = [score["value"] for score in scores.values()]
    return {
        "accuracy": proportion(sum(gold == predicted for gold, predicted in pairs), len(pairs)),
        "prediction_coverage": proportion(sum(predicted is not None for _, predicted in pairs),
                                          len(pairs)),
        "macro_f1": {**_ratio(sum(f1s) / len(f1s) if f1s else None, len(pairs),
                              "Mean per-class F1; not a binomial proportion."),
                     "classes": len(f1s)},
        "class_f1": scores,
    }


def _tag_field(row: dict, tag: str, field: str) -> str | None:
    label = row.get("tags", {}).get(tag) if isinstance(row.get("tags"), dict) else None
    value = label.get(field) if isinstance(label, dict) else None
    return value if isinstance(value, str) else None


def _tag_metrics(cases: list[dict], predictions: dict[str, dict]) -> dict:
    """Human-labeled v3 tags only; other gold schemas use different tag enums."""
    from job_intake.annotation.accuracy import proportion

    v3 = [case for case in cases if case.get("schema", LABELS_SCHEMA) == LABELS_SCHEMA]
    fields = [(tag, tag, "value", list(values)) for tag, values in TAG_VALUES.items()] + [
        ("domain_fit.requirement", "domain_fit", "requirement", list(DOMAIN_REQUIREMENTS)),
        ("domain_fit.industry", "domain_fit", "industry", list(INDUSTRIES)),
    ]
    result = {}
    for name, tag, field, classes in fields:
        pairs = [
            (gold, _tag_field(predictions.get(case["job_uid"], {}), tag, field))
            for case in v3 if (gold := _tag_field(case, tag, field)) is not None
        ]
        result[name] = {"N": len(pairs), **_class_metrics(pairs, classes)}
    tp = fp = fn = exact = covered = n = 0
    for case in v3:
        label = case.get("tags", {}).get("preferred_focus")
        if not isinstance(label, dict) or not isinstance(label.get("values"), list):
            continue
        n += 1
        gold = set(label["values"])
        predicted_label = predictions.get(case["job_uid"], {}).get("tags", {}).get(
            "preferred_focus")
        predicted = predicted_label.get("values") if isinstance(predicted_label, dict) else None
        covered += isinstance(predicted, list)
        predicted = set(predicted) if isinstance(predicted, list) else set()
        exact += isinstance(predicted_label, dict) and gold == predicted
        tp += len(gold & predicted)
        fp += len(predicted - gold)
        fn += len(gold - predicted)
    result["preferred_focus"] = {
        "N": n,
        "accuracy": proportion(exact, n),
        "prediction_coverage": proportion(covered, n),
        "micro_f1": {**proportion(2 * tp, 2 * tp + fp + fn),
                     "note": "Micro-F1 over set members of " + ", ".join(PREFERRED_FOCUS)},
    }
    return result


def _ndcg(cases: list[dict], ranked: list[dict], k: int, baselines: list[list[dict]]) -> dict:
    def dcg(values: list[int]) -> float:
        return sum((2**value - 1) / math.log2(i + 2) for i, value in enumerate(values[:k]))

    ideal = dcg(sorted((case["relevance"] for case in cases), reverse=True))
    actual = dcg([case["relevance"] for case in ranked])
    random_values = [dcg([case["relevance"] for case in sample]) for sample in baselines]
    return {
        "value": actual / ideal if ideal else None, "numerator": actual, "denominator": ideal,
        "random_baseline": (sum(random_values) / len(random_values) / ideal
                            if ideal and random_values else None),
        "prediction_n": len(ranked), "required_n": len(cases),
        "status": "not_applicable" if not cases else
        "measured" if len(ranked) == len(cases) else "partial",
    }


def relevance_scale(cases: list[dict], predictions: dict[str, dict]) -> dict:
    """Only cases with int relevance; a missing prediction is an error in the denominator."""
    from job_intake.annotation.accuracy import proportion

    labeled = [case for case in cases
               if isinstance(case, dict) and isinstance(case.get("job_uid"), str)
               and _relevance(case) is not None]
    n = len(labeled)

    def predicted(case: dict) -> int | None:
        return _relevance(predictions.get(case["job_uid"], {}))

    pairs = [(case["relevance"], value) for case in labeled
             if (value := predicted(case)) is not None]
    qwk = quadratic_weighted_kappa(pairs)
    matrix = {str(gold): {str(value): 0 for value in RELEVANCE_VALUES}
              for gold in RELEVANCE_VALUES}
    missing = {str(gold): 0 for gold in RELEVANCE_VALUES}
    for case in labeled:
        value = predicted(case)
        if value is None:
            missing[str(case["relevance"])] += 1
        else:
            matrix[str(case["relevance"])][str(value)] += 1
    relevant = [case for case in labeled if case["relevance"] >= 2]
    selected = [case for case in labeled if (predicted(case) or 0) >= 2]
    tp = sum(case["relevance"] >= 2 for case in selected)
    order = queue_order([(case["job_uid"], predictions.get(case["job_uid"], {}))
                         for case in labeled])
    by_uid = {case["job_uid"]: case for case in labeled}
    ranked = [by_uid[uid] for uid in order]
    rng = random.Random(_RANDOM_SEED)
    baselines = [rng.sample(labeled, n) for _ in range(_RANDOM_REPEATS)] if n else []
    precision_at, ndcg_at = {}, {}
    for k in (5, 10):
        top = ranked[:k]
        precision_at[str(k)] = {
            **proportion(sum(case["relevance"] >= 2 for case in top), len(top)), "k": k,
            "status": "not_applicable" if not top else "measured" if len(top) == k else "partial",
        }
        ndcg_at[str(k)] = _ndcg(labeled, ranked, k, baselines)
    gated = [case for case in labeled if case.get("gate_outcome") in _GATE_ORDER]

    def gate(case: dict) -> object:
        return predictions.get(case["job_uid"], {}).get("gate_outcome")

    blockers = [case for case in gated if case["gate_outcome"] == "ineligible"]
    eligible = [case for case in gated if case["gate_outcome"] == "eligible"]
    return {
        "version": METRICS_VERSION,
        "N": n,
        "coverage": proportion(len(pairs), n),
        "exact": proportion(sum(gold == value for gold, value in pairs), n),
        "within_one": proportion(sum(abs(gold - value) <= 1 for gold, value in pairs), n),
        "mae": _ratio(sum(abs(gold - value) for gold, value in pairs) / len(pairs)
                      if pairs else None, len(pairs),
                      "Mean absolute error over covered pairs; see coverage."),
        "qwk": {**_ratio(qwk, len(pairs), "Quadratic weighted kappa over covered pairs."),
                **({"note": "Undefined: fewer than two human relevance classes or no "
                            "expected disagreement."} if qwk is None else {})},
        "spearman": _ratio(spearman(pairs), len(pairs),
                           "Rank correlation over covered pairs; undefined without variance."),
        "confusion": {"rows": "human", "columns": "predicted", "matrix": matrix,
                      "missing": missing},
        "relevant": {
            "threshold": 2,
            "recall": proportion(tp, len(relevant)),
            "precision": proportion(tp, len(selected)),
            "f1": {**proportion(2 * tp, len(relevant) + len(selected)),
                   "note": "F1 derived counts; Wilson interval is descriptive."},
            "note": "Thresholded on predicted relevance, not on tier.",
        },
        "precision_at": precision_at,
        "ndcg_at": {**ndcg_at, "random_seed": _RANDOM_SEED, "random_repeats": _RANDOM_REPEATS},
        "queue_order": ("relevance desc, gate eligible>unknown>ineligible, confidence desc, "
                        "fit_score desc, job_uid"),
        "tags": _tag_metrics(labeled, predictions),
        "gate": {
            "N": len(gated),
            "blocker_recall": proportion(sum(gate(case) == "ineligible" for case in blockers),
                                         len(blockers)),
            "false_block_rate": proportion(sum(gate(case) == "ineligible" for case in eligible),
                                           len(eligible)),
            "unknown_rate": proportion(sum(gate(case) == "unknown" for case in gated),
                                       len(gated)),
        },
    }


def _stability_value(row: dict, field: str) -> str | int | None:
    if field == "relevance":
        return _relevance(row)
    tag, _, part = field.partition(".")
    label = row.get("tags", {}).get(tag) if isinstance(row.get("tags"), dict) else None
    if not isinstance(label, dict):
        return None
    if tag == "preferred_focus":
        values = label.get("values")
        return ",".join(sorted(map(str, values))) if isinstance(values, list) else None
    value = label.get(part or "value")
    return value if isinstance(value, str) else None


def compare_stability(run_a: dict[str, dict], run_b: dict[str, dict]) -> dict:
    """Cohen's kappa between two v3 prediction runs: relevance and each of the 6 tags.

    ``domain_fit`` is compared by requirement and industry; ``preferred_focus`` by the
    exact set. Only pairs present in both runs count; a single category has undefined
    kappa. Callers establish that the runs are independent and source-matched.
    """
    from job_intake.annotation.accuracy import proportion

    uids = sorted(set(run_a) | set(run_b))
    fields = ["relevance", *TAG_VALUES, "domain_fit.requirement", "domain_fit.industry",
              "preferred_focus"]
    results = {}
    for field in fields:
        pairs = [(_stability_value(run_a.get(uid, {}), field),
                  _stability_value(run_b.get(uid, {}), field)) for uid in uids]
        paired = [(a, b) for a, b in pairs if a is not None and b is not None]
        total = len(paired)
        left = Counter(a for a, _ in paired)
        right = Counter(b for _, b in paired)
        expected = (sum(left[value] * right[value] for value in set(left) | set(right))
                    / total**2 if total else None)
        observed = sum(a == b for a, b in paired) / total if total else None
        kappa = ((observed - expected) / (1 - expected)
                 if expected is not None and expected < 1 else None)
        results[field] = {
            "kappa": round(kappa, 6) if kappa is not None else None,
            "paired": total, "missing": len(uids) - total,
            "agreement": proportion(sum(a == b for a, b in paired), total),
            "sample_ready": total >= 30,
        }
    return {
        "scope": "paired_v3_predictions", "version": METRICS_VERSION, "jobs": len(uids),
        "fields": results, "independent_runs_confirmed": None,
        "acceptance_status": "insufficient_evidence", "acceptance_passed": False,
        "limitations": [
            "At least 30 comparable vacancies and independent runs are required.",
            "Kappa is undefined when both runs use a single category.",
        ],
    }


def _formatted(metric: dict) -> str:
    value = metric.get("value")
    if value is None:
        return "unknown"
    counts = (f" ({metric['numerator']}/{metric['denominator']})"
              if type(metric.get("numerator")) is int else f" (N={metric.get('denominator')})")
    interval = f"; 95% CI [{metric['ci95'][0]:.3f}, {metric['ci95'][1]:.3f}]" \
        if metric.get("ci95") else ""
    return f"{value:.3f}{counts}{interval}"


def _ndcg_text(metric: dict) -> str:
    if metric["value"] is None:
        return "unknown"
    baseline = metric["random_baseline"]
    return (f"{metric['value']:.3f} (ranked {metric['prediction_n']}/{metric['required_n']}); "
            f"random {baseline:.3f}" if baseline is not None else f"{metric['value']:.3f}")


def relevance_scale_markdown(block: dict) -> list[str]:
    """Markdown lines for one relevance_scale block."""
    lines = [
        "| Metric | Result |", "| --- | --- |",
        *(f"| {name} | {_formatted(block[name])} |"
          for name in ("coverage", "exact", "within_one", "mae", "qwk", "spearman")),
        f"| recall (relevance ≥2) | {_formatted(block['relevant']['recall'])} |",
        f"| precision (relevance ≥2) | {_formatted(block['relevant']['precision'])} |",
        *(f"| P@{k} | {_formatted(block['precision_at'][str(k)])} |" for k in (5, 10)),
        *(f"| NDCG@{k} | {_ndcg_text(block['ndcg_at'][str(k)])} |" for k in (5, 10)),
        f"| gate blocker recall | {_formatted(block['gate']['blocker_recall'])} |",
        f"| gate false block rate | {_formatted(block['gate']['false_block_rate'])} |",
        f"| gate unknown rate | {_formatted(block['gate']['unknown_rate'])} |",
        "",
        "| Human \\ predicted | 0 | 1 | 2 | 3 | missing |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    matrix, missing = block["confusion"]["matrix"], block["confusion"]["missing"]
    lines.extend(
        f"| {gold} | " + " | ".join(str(matrix[gold][value]) for value in matrix[gold])
        + f" | {missing[gold]} |" for gold in matrix
    )
    return lines
