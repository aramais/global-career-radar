import json
from copy import deepcopy

import pytest

from job_intake.annotation.benchmark import evaluate_reviews, main, read_jsonl


def gold_case():
    return {
        "case_id": "negation-and-restriction",
        "units": [
            {"unit_id": "u1", "text": "English is not required."},
            {"unit_id": "u2", "text": "Candidates must reside in Germany."},
            {"unit_id": "u3", "text": "Our customers include banks in Brazil."},
        ],
        "drafts": [
            {
                "claim_id": "language",
                "kind": "working_language",
                "value": "English",
                "unit_id": "u1",
                "source_snippet": "English is not required.",
                "requirement": "required",
                "polarity": "affirmative",
                "is_inference": False,
                "expected_review_status": "UNSUPPORTED",
            },
            {
                "claim_id": "residence",
                "kind": "hiring_location",
                "value": "Germany",
                "unit_id": "u2",
                "source_snippet": "Candidates must reside in Germany.",
                "requirement": "required",
                "polarity": "affirmative",
                "is_inference": False,
                "expected_review_status": "SUPPORTED",
                "restriction": True,
            },
            {
                "claim_id": "footprint",
                "kind": "hiring_location",
                "value": "Brazil",
                "unit_id": "u3",
                "source_snippet": "Our customers include banks in Brazil.",
                "requirement": "informational",
                "polarity": "affirmative",
                "is_inference": False,
                "expected_review_status": "UNSUPPORTED",
            },
        ],
    }


def prediction(*reviews):
    return [{"case_id": "negation-and-restriction", "reviews": list(reviews)}]


def test_correct_reviewer_recognizes_negation_company_scope_and_required_residence():
    gold = gold_case()
    predictions = prediction(
        *(
            {"claim_id": draft["claim_id"], "review_status": draft["expected_review_status"]}
            for draft in gold["drafts"]
        )
    )
    report = evaluate_reviews([gold], predictions)
    assert report["metrics"] == {
        "review_coverage": 1.0,
        "review_status_accuracy": 1.0,
        "supported_precision": 1.0,
        "supported_quote_exact_rate": 1.0,
        "restriction_recall": 1.0,
        "false_promotion_rate": 0.0,
    }
    assert report["failures"] == []


def test_source_quote_alone_cannot_hide_false_promotion_or_missing_restriction():
    report = evaluate_reviews(
        [gold_case()],
        prediction(
            {"claim_id": "language", "review_status": "SUPPORTED"},
            {"claim_id": "residence", "review_status": "NEEDS_VERIFICATION"},
        ),
    )
    assert report["metrics"]["supported_quote_exact_rate"] == 1.0
    assert report["metrics"]["supported_precision"] == 0.0
    assert report["metrics"]["restriction_recall"] == 0.0
    assert report["metrics"]["false_promotion_rate"] == 1.0
    assert report["metrics"]["review_coverage"] == pytest.approx(2 / 3)
    assert report["counts"]["false_promotions"] == 1


@pytest.mark.parametrize(
    "bad_reviews",
    [
        [{"claim_id": "residence", "review_status": "SUPPORTED"}] * 2,
        [{"claim_id": "residence", "review_status": "SUPPORTED", "value": "Brazil"}],
        [{"claim_id": "residence", "review_status": "SUPPORTED", "source_snippet": "Germany"}],
        [{"claim_id": "residence", "review_status": "SUPPORTED", "is_inference": "false"}],
    ],
)
def test_duplicate_and_mutated_reviews_do_not_receive_credit(bad_reviews):
    report = evaluate_reviews([gold_case()], prediction(*bad_reviews))
    assert report["metrics"]["review_coverage"] == 0.0
    assert report["metrics"]["restriction_recall"] == 0.0
    assert report["metrics"]["supported_precision"] == 0.0
    assert report["counts"]["invalid_reviews"] >= 1


def test_unknown_supported_claim_is_counted_as_false_promotion():
    report = evaluate_reviews(
        [gold_case()], prediction({"claim_id": "invented", "review_status": "SUPPORTED"})
    )
    assert report["counts"]["false_promotions"] == 1
    assert report["metrics"]["supported_quote_exact_rate"] == 0.0
    assert report["metrics"]["review_coverage"] == 0.0


def test_malformed_status_or_supported_answer_without_id_cannot_hide_in_metrics():
    report = evaluate_reviews(
        [gold_case()],
        prediction(
            {"claim_id": "residence", "review_status": ["SUPPORTED"]},
            {"review_status": "SUPPORTED"},
        ),
    )
    assert report["counts"]["invalid_reviews"] == 2
    assert report["counts"]["false_promotions"] == 1
    assert report["metrics"]["supported_precision"] == 0.0
    assert report["metrics"]["review_coverage"] == 0.0


def test_all_missing_predictions_have_zero_coverage_and_undefined_support_precision():
    report = evaluate_reviews([gold_case()], [])
    assert report["metrics"]["review_coverage"] == 0.0
    assert report["metrics"]["supported_precision"] is None
    assert report["metrics"]["false_promotion_rate"] is None


def test_supported_gold_must_have_quote_from_its_own_unit_and_cannot_be_inferred():
    for changes in (
        {"source_snippet": "Candidates must reside in Brazil."},
        {"is_inference": True},
    ):
        gold = gold_case()
        gold["drafts"][1].update(changes)
        with pytest.raises(ValueError, match="direct exact source quote"):
            evaluate_reviews([gold], [])


def test_unicode_and_whitespace_alignment_preserve_case_sensitive_evidence():
    gold = gold_case()
    draft = gold["drafts"][1]
    gold["units"][1]["text"] = "Candidates\n must reside in Sa\u0303o Paulo."
    draft.update(value="São Paulo", source_snippet="Candidates must reside in São Paulo.")
    report = evaluate_reviews(
        [gold], prediction({"claim_id": "residence", "review_status": "SUPPORTED"})
    )
    assert report["metrics"]["supported_precision"] == 1.0
    draft["source_snippet"] = "candidates must reside in São Paulo."
    with pytest.raises(ValueError, match="direct exact source quote"):
        evaluate_reviews([gold], [])


def test_invalid_gold_and_misaligned_cases_fail_explicitly():
    for gold in ([], [gold_case(), gold_case()]):
        with pytest.raises(ValueError):
            evaluate_reviews(gold, [])
    mutated = deepcopy(gold_case())
    mutated["drafts"][0]["restriction"] = True
    with pytest.raises(ValueError, match="supported required condition"):
        evaluate_reviews([mutated], [])
    with pytest.raises(ValueError, match="unknown or duplicated"):
        evaluate_reviews([gold_case()], [{"case_id": "other", "reviews": []}])


def test_offline_cli_writes_report_without_loading_model_or_database(tmp_path):
    gold = tmp_path / "gold.jsonl"
    predictions = tmp_path / "predictions.jsonl"
    output = tmp_path / "metrics.json"
    gold.write_text(json.dumps(gold_case()) + "\n", encoding="utf-8")
    predictions.write_text("", encoding="utf-8")
    result = main(["--gold", str(gold), "--predictions", str(predictions), "--output", str(output)])
    assert result == 0
    assert json.loads(output.read_text())["metrics"]["review_coverage"] == 0.0
    assert sorted(path.name for path in tmp_path.iterdir()) == [
        "gold.jsonl",
        "metrics.json",
        "predictions.jsonl",
    ]


def test_jsonl_rejects_ambiguous_keys_and_cli_cannot_overwrite_inputs(tmp_path):
    path = tmp_path / "gold.jsonl"
    path.write_text('{"case_id":"first","case_id":"second"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="line 1"):
        read_jsonl(path)
    with pytest.raises(SystemExit) as error:
        main(["--gold", str(path), "--predictions", str(path), "--output", str(path)])
    assert error.value.code == 2
    assert "first" in path.read_text()
