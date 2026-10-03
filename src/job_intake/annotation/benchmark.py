"""Offline comparison of reviewers against manually labelled, identical drafts.

This measures review decisions, not extraction recall or production vacancy grades.
No model, configuration, database, or credential is loaded by this module.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from job_intake.annotation.schema import CLAIM_KINDS, POLARITIES, REQUIREMENTS, REVIEW_STATUSES
from job_intake.annotation.text import normalize_quote


def _identifier(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _member(value: Any, allowed: frozenset[str]) -> bool:
    return isinstance(value, str) and value in allowed


def _quote_matches(draft: dict, units: dict[str, str]) -> bool:
    quote = normalize_quote(draft["source_snippet"])
    return bool(quote) and quote in normalize_quote(units.get(draft["unit_id"], ""))


def _gold_cases(rows: list[dict]) -> dict[str, dict]:
    cases = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Gold cases must be objects")
        case_id = row.get("case_id")
        if not _identifier(case_id) or case_id in cases:
            raise ValueError("Gold case_id must be nonempty and unique")
        units = {}
        if not isinstance(row.get("units"), list) or not isinstance(row.get("drafts"), list):
            raise ValueError("Gold requires units[] and drafts[]")
        for unit in row["units"]:
            if (
                not isinstance(unit, dict)
                or not _identifier(unit.get("unit_id"))
                or unit["unit_id"] in units
                or not isinstance(unit.get("text"), str)
            ):
                raise ValueError("Gold units require unique unit_id and original text")
            units[unit["unit_id"]] = unit["text"]
        drafts = {}
        for draft in row["drafts"]:
            if (
                not isinstance(draft, dict)
                or not _identifier(draft.get("claim_id"))
                or draft["claim_id"] in drafts
                or not _member(draft.get("kind"), CLAIM_KINDS)
                or not _identifier(draft.get("value"))
                or not _identifier(draft.get("unit_id"))
                or draft["unit_id"] not in units
                or not isinstance(draft.get("source_snippet"), str)
                or not _member(draft.get("requirement"), REQUIREMENTS)
                or not _member(draft.get("polarity"), POLARITIES)
                or type(draft.get("is_inference")) is not bool
                or not _member(draft.get("expected_review_status"), REVIEW_STATUSES)
                or type(draft.get("restriction", False)) is not bool
            ):
                raise ValueError("Gold draft is invalid or its claim_id is duplicated")
            if draft["expected_review_status"] == "SUPPORTED" and (
                not _quote_matches(draft, units) or draft["is_inference"]
            ):
                raise ValueError("Gold SUPPORTED drafts require a direct exact source quote")
            if draft.get("restriction") and (
                draft["expected_review_status"] != "SUPPORTED" or draft["requirement"] != "required"
            ):
                raise ValueError("Gold restriction must be a supported required condition")
            drafts[draft["claim_id"]] = draft
        cases[case_id] = {"units": units, "drafts": drafts}
    if not cases or not any(case["drafts"] for case in cases.values()):
        raise ValueError("Gold must contain at least one manually labelled draft")
    return cases


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def evaluate_reviews(gold: list[dict], predictions: list[dict]) -> dict:
    """Compare exact-ID reviews; missing, duplicate, and mutated reviews cannot pass."""
    cases = _gold_cases(gold)
    predicted = {}
    for row in predictions:
        if not isinstance(row, dict):
            raise ValueError("Prediction cases must be objects")
        case_id = row.get("case_id")
        if not _identifier(case_id) or case_id not in cases or case_id in predicted:
            raise ValueError("Prediction case_id is unknown or duplicated")
        if not isinstance(row.get("reviews"), list):
            raise ValueError("Predictions require reviews[]")
        predicted[case_id] = row["reviews"]
    counts = {
        "cases": len(cases),
        "claims": sum(len(case["drafts"]) for case in cases.values()),
        "reviewed_claims": 0,
        "correct_statuses": 0,
        "supported_predictions": 0,
        "correct_supported": 0,
        "exact_supported_quotes": 0,
        "false_promotions": 0,
        "restrictions": 0,
        "supported_restrictions": 0,
        "invalid_reviews": 0,
    }
    failures = []
    for case_id, case in cases.items():
        groups = defaultdict(list)
        for review in predicted.get(case_id, []):
            if not isinstance(review, dict) or not _identifier(review.get("claim_id")):
                counts["invalid_reviews"] += 1
                if isinstance(review, dict) and review.get("review_status") == "SUPPORTED":
                    counts["supported_predictions"] += 1
                    counts["false_promotions"] += 1
                continue
            groups[review["claim_id"]].append(review)
        for claim_id in groups.keys() - case["drafts"].keys():
            rows = groups[claim_id]
            counts["invalid_reviews"] += len(rows)
            if any(row.get("review_status") == "SUPPORTED" for row in rows):
                counts["supported_predictions"] += 1
                counts["false_promotions"] += 1
            failures.append({"case_id": case_id, "claim_id": claim_id, "reason": "unknown_claim"})
        for claim_id, draft in case["drafts"].items():
            rows = groups.get(claim_id, [])
            status = "MISSING"
            if rows:
                row = rows[0]
                valid = len(rows) == 1 and _member(row.get("review_status"), REVIEW_STATUSES)
                # A reviewer must assess the fixed draft, never replace it with an easier claim.
                valid = valid and all(
                    field not in row or row[field] == draft[field]
                    for field in (
                        "kind",
                        "value",
                        "unit_id",
                        "source_snippet",
                        "requirement",
                        "polarity",
                    )
                )
                valid = valid and type(row.get("is_inference", False)) is bool
                status = row["review_status"] if valid else "INVALID"
                counts["reviewed_claims"] += int(valid)
                counts["invalid_reviews"] += 0 if valid else len(rows)
            expected = draft["expected_review_status"]
            counts["correct_statuses"] += int(status == expected)
            quoted = _quote_matches(draft, case["units"])
            raw_support = any(row.get("review_status") == "SUPPORTED" for row in rows)
            correct_support = (
                (
                    status == expected == "SUPPORTED"
                    and quoted
                    and not row.get("is_inference", False)
                    and not draft["is_inference"]
                )
                if rows
                else False
            )
            if raw_support:
                counts["supported_predictions"] += 1
                counts["exact_supported_quotes"] += int(quoted)
                counts["correct_supported"] += int(correct_support)
                counts["false_promotions"] += int(not correct_support)
            if draft.get("restriction"):
                counts["restrictions"] += 1
                counts["supported_restrictions"] += int(correct_support)
            if status != expected or (raw_support and not correct_support):
                failures.append(
                    {
                        "case_id": case_id,
                        "claim_id": claim_id,
                        "expected": expected,
                        "predicted": status,
                        "quote_exact": quoted,
                    }
                )
    return {
        "scope": "reviewer_fixed_drafts",
        "counts": counts,
        "metrics": {
            "review_coverage": _ratio(counts["reviewed_claims"], counts["claims"]),
            "review_status_accuracy": _ratio(counts["correct_statuses"], counts["claims"]),
            "supported_precision": _ratio(
                counts["correct_supported"], counts["supported_predictions"]
            ),
            "supported_quote_exact_rate": _ratio(
                counts["exact_supported_quotes"], counts["supported_predictions"]
            ),
            "restriction_recall": _ratio(counts["supported_restrictions"], counts["restrictions"]),
            "false_promotion_rate": _ratio(
                counts["false_promotions"], counts["supported_predictions"]
            ),
        },
        "failures": failures,
    }


def _unique_object(pairs: list[tuple[str, Any]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def read_jsonl(path: Path) -> list[dict]:
    rows = []
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line, object_pairs_hook=_unique_object)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"Invalid JSONL at line {index}") from exc
        if not isinstance(row, dict):
            raise ValueError(f"JSONL line {index} must be an object")
        rows.append(row)
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--output", type=Path, help="Optional JSON report; defaults to stdout")
    args = parser.parse_args(argv)
    try:
        if args.output and args.output.resolve() in {
            args.gold.resolve(),
            args.predictions.resolve(),
        }:
            raise ValueError("Output must not overwrite benchmark inputs")
        report = evaluate_reviews(read_jsonl(args.gold), read_jsonl(args.predictions))
        text = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(text, encoding="utf-8")
        else:
            print(text, end="")
    except (OSError, ValueError, TypeError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
