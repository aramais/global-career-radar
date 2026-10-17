from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from job_intake.annotation.accuracy import (
    adapt_saved_record,
    compare_stability,
    evaluate_vacancies,
    load_prices,
    operational_metrics,
    proportion,
    read_saved_predictions,
    run_accuracy,
    write_accuracy_report,
)
from job_intake.annotation.gold import _snapshot
from job_intake.annotation.text import TEXT_VERSION, source_hash
from job_intake.config.settings import AppConfig, LLMConfig, TelegramConfig
from job_intake.models.job import JobRecord


def job(annotation=None):
    return JobRecord(
        source="test",
        company="Example",
        title="Analytics Manager",
        original_url="https://example.test/job",
        description_clean="You will lead pricing experiments.",
        annotation=annotation or {},
    )


def case(uid, relevance, *, gate="eligible", tags=None):
    return {
        "job_uid": uid,
        "role_label": "analytics-leadership", "relevance": relevance,
        "gate_outcome": gate,
        "tags": tags or {},
    }


def prediction(*, tier="B", decision="review", score=10, tags=None, route=None):
    return {
        "tier": tier,
        "decision": decision,
        "fit_score": score,
        "tags": tags or {},
        "route": route,
    }


def prices():
    return load_prices(Path(__file__).parents[1] / "config/eval_prices.yaml")


def test_wilson_interval_and_zero_denominator_are_explicit():
    assert proportion(1, 2) == {
        "value": 0.5,
        "numerator": 1,
        "denominator": 2,
        "ci95": [0.094531, 0.905469],
    }
    assert proportion(0, 0)["value"] is None
    assert proportion(0, 0)["ci95"] is None
    assert proportion(0, 40)["ci95"][1] > 0
    with pytest.raises(ValueError):
        proportion(3, 2)


def test_saved_adapter_maps_focus_restrictions_and_prefilter_without_title_guessing():
    record = job(
        {
            "summary": {
                "search_focus": {
                    "dimensions": {
                        "project_management": {"status": "confirmed"},
                        "people_interaction": {"status": "explicitly_absent"},
                        "decision_influence": {"status": "unknown"},
                    }
                },
                "unknowns": ["work_authorization"],
            },
            "claims": [
                {
                    "kind": "hiring_location",
                    "value": "Brazil",
                    "polarity": "negative",
                    "requirement": "required",
                    "review_status": "SUPPORTED",
                },
                {
                    "kind": "work_mode",
                    "value": "Remote or hybrid",
                    "polarity": "affirmative",
                    "requirement": "informational",
                    "review_status": "SUPPORTED",
                },
                {
                    "kind": "working_language",
                    "value": "English",
                    "polarity": "affirmative",
                    "requirement": "required",
                    "review_status": "UNSUPPORTED",
                },
            ],
            "prefilter": {"routing": "suppressed"},
        }
    )
    mapped = adapt_saved_record(record)
    assert mapped["tags"]["project_management"] == "duty"
    assert mapped["tags"]["people_interaction"] == "explicitly_absent"
    assert mapped["tags"]["decision_influence"] == "unspecified"
    assert mapped["tags"]["hiring_geography"] == "excludes_brazil"
    assert mapped["tags"]["work_mode"] == "remote_or_hybrid"
    assert mapped["tags"]["work_authorization"] == "unspecified"
    assert mapped["tags"]["working_language"] is None
    assert mapped["tags"]["role_family"] is None
    assert mapped["route"] == "OUT_OF_SCOPE"


def test_absent_annotation_is_abstention_and_duty_trumps_skill_only():
    assert adapt_saved_record(job())["tags"]["analytics_intensity"] is None
    mapped = adapt_saved_record(
        job(
            {
                "summary": {
                    "search_focus": {
                        "dimensions": {
                            "project_management": {"status": "confirmed"},
                        }
                    }
                },
                "claims": [
                    {
                        "kind": "project_management",
                        "value": "project management experience",
                        "source_section": "requirements",
                        "polarity": "affirmative",
                        "review_status": "SUPPORTED",
                        "requirement": "required",
                    }
                ],
            }
        )
    )
    assert mapped["tags"]["project_management"] == "duty"
    assert "project_management" not in mapped["adapter_conflicts"]


def test_metrics_measure_real_exclusions_and_missing_outputs_reduce_recall():
    gold = [
        case("a", 3),
        case("b", 2),
        case("c", 2),
        case("d", 2),
        case("e", 0),
    ]
    predictions = {
        "a": prediction(tier="A", decision="pass"),
        "b": prediction(tier="C", route="OUT_OF_SCOPE"),
        "c": prediction(tier="C", decision="reject"),
        "e": prediction(tier="A", decision="pass"),
    }
    report = evaluate_vacancies(gold, predictions)
    assert report["metrics"]["false_exclusion_rate"]["numerator"] == 2
    assert report["metrics"]["false_exclusion_rate"]["denominator"] == 4
    assert report["metrics"]["relevant_recall_AB"]["value"] == 0.25
    assert report["metrics"]["precision_A"]["value"] == 0.5
    assert report["metrics"]["precision_AB"]["value"] == 0.5
    assert report["metrics"]["wrong_block_rate"]["value"] == 0.2
    assert report["decision_prediction_coverage"]["value"] == 0.8


def test_rank_order_is_tier_then_score_then_uid_and_ndcg_uses_graded_relevance():
    gold = [case("z", 3), case("b", 1), case("a", 2)]
    predictions = {
        "z": prediction(tier="B", score=1000),
        "b": prediction(tier="A", score=10),
        "a": prediction(tier="A", score=10),
    }
    report = evaluate_vacancies(gold, predictions)
    assert report["ranking"] == ["a", "b", "z"]
    assert 0 < report["metrics"]["ndcg@20"]["value"] < 1
    assert report["metrics"]["ndcg@20"]["ci95"] is None


def test_abstentions_count_as_tag_errors_and_negative_restrictions_have_counts():
    gold = [
        case(
            "a",
            2,
            tags={
                "project_management": {"value": "duty"},
                "hiring_geography": {"value": "excludes_brazil"},
            },
        ),
        case(
            "b",
            2,
            tags={
                "project_management": {"value": "skill_only"},
                "hiring_geography": {"value": "restricted_elsewhere"},
            },
        ),
        case(
            "c",
            0,
            tags={
                "project_management": {"value": "unspecified"},
                "hiring_geography": {"value": "unspecified"},
            },
        ),
    ]
    predictions = {
        "a": prediction(tags={"project_management": "duty", "hiring_geography": "excludes_brazil"}),
        "b": prediction(tags={"project_management": "duty"}),
        "c": prediction(tags={"hiring_geography": "excludes_brazil"}),
    }
    report = evaluate_vacancies(gold, predictions)
    scope = report["tags"]["project_management"]
    assert scope["accuracy"]["numerator"] == 1
    assert scope["accuracy"]["denominator"] == 3
    assert scope["prediction_coverage"]["value"] == pytest.approx(2 / 3, abs=1e-6)
    assert scope["confusion"]["unspecified"]["__abstained__"] == 1
    assert scope["macro_f1"]["value"] == pytest.approx((2 / 3) / 3, abs=1e-6)
    geography = report["tags"]["hiring_geography"]
    assert geography["negative_recall"]["value"] == 0.5
    assert geography["negative_precision"]["value"] == 0.5


def test_unlabeled_templates_produce_null_accuracy_not_zero():
    report = evaluate_vacancies([case("a", None, gate=None)], {})
    assert report["counts"]["decision_labeled"] == 0
    assert all(metric["value"] is None for metric in report["metrics"].values())
    assert report["tags"]["project_management"]["accuracy"]["value"] is None


def test_operational_unknowns_stay_null_and_partial_usage_is_visible():
    report = operational_metrics(
        [
            ("a", job()),
            (
                "b",
                job(
                    {
                        "usage": [
                            {
                                "model": "gemini-3.8-flash",
                                "input_tokens": 1_000_000,
                                "output_tokens": 0,
                            },
                            {"model": "unknown-model", "input_tokens": 0, "output_tokens": 10},
                        ]
                    }
                ),
            ),
        ],
        prices(),
    )
    assert report["cost_per_vacancy"] is None
    assert report["cost_unknown_jobs"] == 2
    assert report["known_cost_total"] == 0.75
    assert report["requests_per_vacancy"] is None
    assert report["timeout_rate"]["value"] is None
    assert report["error_rate"]["value"] is None
    assert report["cache_hit_rate"]["value"] is None
    assert report["vacancy_latency_seconds"] == {"p50": None, "p95": None, "samples": 0}
    assert report["unknown_models"] == ["unknown-model"]


def test_operational_price_and_latency_use_dated_explicit_telemetry():
    report = operational_metrics(
        [
            (
                "a",
                job(
                    {
                        "usage": [
                            {
                                "model": "gemini-3.8-flash",
                                "input_tokens": 1_000_000,
                                "output_tokens": 1_000_000,
                                "latency_ms": 5000,
                                "stage": "extract",
                            },
                            {
                                "model": "gemini-3.6-flash",
                                "input_tokens": 1_000_000,
                                "output_tokens": 0,
                                "latency_ms": 15000,
                                "stage": "review",
                                "cached": False,
                            },
                        ],
                        "latency_ms": 20000,
                    }
                ),
            )
        ],
        prices(),
    )
    assert report["cost_per_vacancy"] == 5.25
    assert report["price_as_of"] == "2026-10-10"
    assert report["vacancy_latency_seconds"]["p95"] == 20
    assert report["stage_latency_seconds"]["extract"]["p50"] == 5
    assert report["cache_hit_rate"]["denominator"] == 1


def test_latest_log_latencies_requests_and_timeouts_are_safe_metadata(tmp_path):
    log = tmp_path / "progress.log"
    log.write_text(
        "[0.0s] Starting annotation (AI); loading saved jobs.\n"
        "[0.0s] Loaded 1 vacancies.\n"
        "[0.0s] Job 1/1: Secret Company — Confidential vacancy\n"
        "[0.1s] Job 1/1 chunk 1/1: extract request attempt 1/2 (timeout 60s).\n"
        "[60.1s] Job 1/1 chunk 1/1: extract request failed in 60.0s; code=request_timeout; "
        "usage unavailable.\n"
        "[61.0s] Job 1/1 chunk 1/1: extract request attempt 2/2 (timeout 60s).\n"
        "[66.0s] Job 1/1 chunk 1/1: extract request completed in 5.0s; "
        "tokens input=100, output=200.\n"
        "[66.0s] Job 1/1: saved 4 profile evaluations in 66.0s; annotation errors=0.\n",
        encoding="utf-8",
    )
    run = operational_metrics([], prices(), log=log)["latest_run"]
    assert run["requests_per_vacancy"] == 2
    assert run["error_rate"]["value"] == 0.5
    assert run["timeout_rate"]["value"] == 0.5
    assert run["vacancy_latency_seconds"]["p95"] == 66
    assert run["stage_latency_seconds"]["extract"]["p50"] == 32.5
    assert "Secret Company" not in json.dumps(run)
    assert run["cache_hit_rate"]["value"] is None


def test_unfinished_log_request_does_not_claim_zero_errors(tmp_path):
    log = tmp_path / "unfinished.log"
    log.write_text(
        "[0.0s] Starting annotation (AI); loading saved jobs.\n"
        "[0.0s] Job 1/1: Example — Analytics Manager\n"
        "[0.1s] Job 1/1 chunk 1/1: extract request attempt 1/1 (timeout 60s).\n"
    )
    run = operational_metrics([], prices(), log=log)["latest_run"]
    assert run["requests"] == 1
    assert run["error_rate"]["value"] is None
    assert run["timeout_rate"]["value"] is None


def test_stability_kappa_uses_pairs_and_explicit_missingness():
    first = {
        "a": prediction(tags={"project_management": "duty"}),
        "b": prediction(tags={"project_management": "skill_only"}),
        "c": prediction(tags={"project_management": "duty"}),
    }
    second = {
        "a": prediction(tags={"project_management": "duty"}),
        "b": prediction(tags={"project_management": "skill_only"}),
    }
    report = compare_stability(first, second)
    scope = report["tags"]["project_management"]
    assert scope["kappa"] == 1
    assert scope["paired"] == 2
    assert scope["missing"] == 1
    assert scope["sample_ready"] is False
    assert report["tags"]["working_language"]["kappa"] is None
    assert report["acceptance_passed"] is None


def test_stability_single_category_kappa_is_undefined():
    values = {str(i): prediction(tags={"project_management": "duty"}) for i in range(30)}
    scope = compare_stability(values, values)["tags"]["project_management"]
    assert scope["sample_ready"] is True
    assert scope["kappa"] is None
    assert scope["agreement"]["value"] == 1


@pytest.fixture
def stored(tmp_path):
    path = tmp_path / "jobs.db"
    record = job()
    record.annotation["source_hash"] = source_hash(record)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE jobs (job_uid TEXT, source TEXT, company TEXT, title TEXT, "
            "original_url TEXT, description_raw TEXT, description_clean TEXT, "
            "source_metadata TEXT, annotation TEXT, updated_at TEXT, content_hash TEXT, "
            "status TEXT, role_label TEXT, role_classification TEXT)"
        )
        connection.execute(
            "INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "uid",
                record.source,
                record.company,
                record.title,
                record.original_url,
                record.description_raw,
                record.description_clean,
                "{}",
                json.dumps(record.annotation),
                "2026-10-10",
                "content",
                "open",
                "analytics-leadership",
                json.dumps({"label": "analytics-leadership", "version": "test",
                            "source_hash": source_hash(record), "content_hash": "content"}),
            ),
        )
        connection.execute(
            "CREATE TABLE job_relevance_evaluations (job_uid TEXT PRIMARY KEY, profile_id TEXT, "
            "profile_version TEXT, content_hash TEXT, decision TEXT, tier TEXT, fit_score REAL)"
        )
        connection.executemany(
            "INSERT INTO job_relevance_evaluations VALUES (?,?,?,?,?,?,?)",
            [
                ("uid", "analytics-leadership", "current", "content", "review", "B", 10),
            ],
        )
    settings = AppConfig(
        database_url=f"sqlite:///{path}",
        log_level="INFO",
        sources=[],
        rules_path=tmp_path / "rules.yaml",
        search_profiles_path=tmp_path / "profiles.yaml",
        company_watchlist_path=tmp_path / "companies.yaml",
        export_dir=tmp_path,
        telegram=TelegramConfig(),
        llm=LLMConfig(),
    )
    return path, settings, record


def test_saved_relevance_with_stale_version_is_withheld(stored):
    path, settings, record = stored
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE job_relevance_evaluations SET profile_version='old'")
    before = path.read_bytes()
    predictions = read_saved_predictions(
        settings.database_url,
        [("uid", record)],
        profile_versions={
            "analytics-leadership": "new",
            "product-management": "current",
        },
    )
    assert predictions["uid"]["tier"] is None
    assert predictions["uid"]["decision"] is None
    assert predictions["uid"]["stale_profiles"] == ["analytics-leadership"]
    assert predictions["uid"]["current_eligible"] is False
    assert path.read_bytes() == before


def test_complete_decision_labels_without_tags_or_blind_sample_are_not_ready(stored, tmp_path):
    path, settings, record = stored
    with sqlite3.connect(path) as connection:
        for index in range(1, 160):
            connection.execute(
                "INSERT INTO jobs SELECT ?, source, company, title, original_url, description_raw, "
                "description_clean, source_metadata, annotation, updated_at, content_hash, status, "
                "role_label, role_classification "
                "FROM jobs WHERE job_uid='uid'", (f"uid-{index}",),
            )
    gold = tmp_path / "decision-labels.jsonl"
    labels = [{
        "job_uid": "uid" if index == 0 else f"uid-{index}",
        "source_hash": source_hash(record), "text_version": TEXT_VERSION,
        "labeled_at": "2026-10-13", "method": "assisted", "tags": {},
        "role_label": "analytics-leadership", "relevance": 2,
        "gate_outcome": "unknown", "notes": "",
    } for index in range(160)]
    gold.write_text("".join(json.dumps(row) + "\n" for row in labels))
    report = run_accuracy(
        settings, gold=gold, prices=Path(__file__).parents[1] / "config/eval_prices.yaml",
    )
    assert report["evaluation"]["counts"]["decision_labeled"] == 160
    assert report["readiness"]["gold_ready"] is False
    assert report["readiness"]["status"] == "pending_tag_or_blind_labels"
    assert report["readiness"]["acceptance_passed"] is None


def test_run_accuracy_on_templates_writes_readonly_json_and_markdown(stored, tmp_path, monkeypatch):
    path, settings, record = stored
    gold = tmp_path / "gold.jsonl"
    gold.write_text(
        json.dumps(
            {
                "job_uid": "uid",
                "source_hash": source_hash(record),
                "text_version": TEXT_VERSION,
                "labeled_at": None,
                "method": None,
                "tags": {},
                "role_label": None, "relevance": None,
                "gate_outcome": None,
                "notes": "",
            }
        )
        + "\n"
    )
    before = path.read_bytes()

    def forbidden(*args, **kwargs):
        raise AssertionError("Accuracy must not make API calls")

    monkeypatch.setattr("requests.sessions.Session.request", forbidden)
    report = run_accuracy(
        settings, gold=gold, prices=Path(__file__).parents[1] / "config/eval_prices.yaml"
    )
    output = tmp_path / "baseline.json"
    outputs = write_accuracy_report(report, output, protected_paths=[path, gold])
    assert outputs == (output.resolve(), output.with_suffix(".md").resolve())
    assert report["api_calls"] == 0
    assert report["readiness"]["status"] == "pending_human_labels"
    assert report["evaluation"]["counts"]["decision_labeled"] == 0
    assert json.loads(output.read_text())["evaluation"]["metrics"]["precision_A"]["value"] is None
    assert "unknown" in output.with_suffix(".md").read_text()
    assert path.read_bytes() == before


def test_outputs_protect_both_sibling_inputs_before_any_write(stored, tmp_path):
    _, settings, record = stored
    gold = tmp_path / "gold.jsonl"
    gold.write_text(
        json.dumps(
            {
                "job_uid": "uid",
                "source_hash": source_hash(record),
                "text_version": TEXT_VERSION,
                "labeled_at": None,
                "method": None,
                "tags": {},
                "role_label": None, "relevance": None,
                "gate_outcome": None,
                "notes": "",
            }
        )
        + "\n"
    )
    report = run_accuracy(
        settings, gold=gold, prices=Path(__file__).parents[1] / "config/eval_prices.yaml"
    )
    protected_md = tmp_path / "protected.md"
    protected_md.write_text("keep")
    with pytest.raises(ValueError, match="overwrite inputs"):
        write_accuracy_report(report, tmp_path / "protected.json", protected_paths=[protected_md])
    assert protected_md.read_text() == "keep"
    assert not (tmp_path / "protected.json").exists()


def test_source_drift_withholds_old_labels_pending_human_review(stored, tmp_path):
    _, settings, _ = stored
    gold = tmp_path / "old-gold.jsonl"
    gold.write_text(
        json.dumps(
            {
                "job_uid": "uid",
                "source_hash": "0" * 64,
                "text_version": TEXT_VERSION,
                "labeled_at": "2026-10-10",
                "method": "blind",
                "tags": {},
                "role_label": "analytics-leadership", "relevance": 2,
                "gate_outcome": "eligible",
                "notes": "",
            }
        )
        + "\n"
    )
    report = run_accuracy(
        settings,
        gold=gold,
        prices=Path(__file__).parents[1] / "config/eval_prices.yaml",
    )
    assert report["evaluation"]["counts"]["source_drift_withheld"] == 1
    assert report["evaluation"]["counts"]["decision_labeled"] == 0
    assert report["evaluation"]["metrics"]["relevant_recall_AB"]["value"] is None
    assert report["readiness"]["source_review_required"] is True
    assert report["readiness"]["status"] == "pending_source_review"


def test_gold_survives_parser_version_change_with_unchanged_source_snapshot(stored, tmp_path):
    _, settings, record = stored
    gold = tmp_path / "portable-gold.jsonl"
    snapshot = _snapshot(record)
    snapshot["description_clean"] = "  • You will lead pricing experiments.\n"
    gold.write_text(
        json.dumps(
            {
                "job_uid": "uid",
                "source_hash": "0" * 64,
                "text_version": "source-units-v2",
                "labeled_at": "2026-10-10",
                "method": "blind",
                "tags": {},
                "source": snapshot,
                "role_label": "analytics-leadership", "relevance": 2,
                "gate_outcome": "eligible",
                "notes": "",
            }
        )
        + "\n"
    )
    report = run_accuracy(
        settings,
        gold=gold,
        prices=Path(__file__).parents[1] / "config/eval_prices.yaml",
    )
    assert report["evaluation"]["counts"]["source_drift_withheld"] == 0
    assert report["evaluation"]["counts"]["parser_only_rebound"] == 1
    assert report["evaluation"]["counts"]["decision_labeled"] == 1
    assert report["evaluation"]["metrics"]["relevant_recall_AB"]["value"] == 1


def test_parser_version_change_cannot_hide_semantic_raw_source_drift(stored, tmp_path):
    _, settings, record = stored
    gold = tmp_path / "changed-raw-gold.jsonl"
    snapshot = _snapshot(record)
    snapshot["description_raw"] = "<p>Another vacancy's original text.</p>"
    gold.write_text(
        json.dumps(
            {
                "job_uid": "uid",
                "source_hash": "0" * 64,
                "text_version": "source-units-v2",
                "labeled_at": "2026-10-10",
                "method": "blind",
                "tags": {},
                "source": snapshot,
                "role_label": "analytics-leadership", "relevance": 2,
                "gate_outcome": "eligible",
                "notes": "",
            }
        )
        + "\n"
    )
    report = run_accuracy(
        settings,
        gold=gold,
        prices=Path(__file__).parents[1] / "config/eval_prices.yaml",
    )
    assert report["evaluation"]["counts"]["source_drift_withheld"] == 1
    assert report["evaluation"]["counts"]["parser_only_rebound"] == 0
    assert report["readiness"]["status"] == "pending_source_review"


def test_compatible_geography_and_workmode_claims_choose_representable_values():
    claims = [
        {
            "kind": kind,
            "value": value,
            "review_status": "SUPPORTED",
            "polarity": "affirmative",
            "requirement": "informational",
        }
        for kind, value in (
            ("hiring_location", "Brazil"),
            ("hiring_location", "LATAM"),
            ("work_mode", "remote"),
            ("work_mode", "hybrid"),
        )
    ]
    mapped = adapt_saved_record(job({"claims": claims}))
    assert mapped["tags"]["hiring_geography"] == "brazil_explicit"
    assert mapped["tags"]["work_mode"] == "remote_or_hybrid"
    assert not mapped["adapter_conflicts"]


def test_role_classification_accuracy_is_separate_from_relevance_ranking():
    cases = [case("correct", 3), case("wrong", 2), case("missing", 0)]
    predictions = {
        "correct": {"role_label": "analytics-leadership", "decision": "pass",
                    "tier": "A", "fit_score": 20},
        "wrong": {"role_label": "product-management", "decision": "pass",
                  "tier": "A", "fit_score": 15},
    }
    result = evaluate_vacancies(cases, predictions)
    roles = result["role_classification"]
    assert roles["accuracy"]["value"] == pytest.approx(1 / 3, abs=1e-6)
    assert roles["prediction_coverage"]["value"] == pytest.approx(2 / 3, abs=1e-6)
    assert roles["confusion"]["analytics-leadership"]["product-management"] == 1
    assert sum(roles["confusion"]["analytics-leadership"].values()) == 3
    assert result["metrics"]["precision_A"]["value"] == 1


def test_legacy_matrix_is_not_promoted_to_current_prediction(stored):
    path, settings, record = stored
    with sqlite3.connect(path) as connection:
        connection.execute(
            "ALTER TABLE job_relevance_evaluations RENAME TO job_profile_evaluations"
        )
        connection.execute("UPDATE jobs SET role_label=NULL, role_classification='{}'")
    before = path.read_bytes()
    prediction = read_saved_predictions(settings.database_url, [("uid", record)])["uid"]
    assert prediction["role_label"] is None
    assert prediction["fit_score"] is None
    assert prediction["decision_source"] == "reevaluation_required"
    assert prediction["needs_reevaluation"] is True
    assert path.read_bytes() == before


@pytest.mark.parametrize("field,value", [
    ("label", "product-management"), ("source_hash", "0" * 64),
    ("content_hash", "old"), ("version", "old"),
])
def test_saved_classification_must_match_current_source_and_taxonomy(stored, field, value):
    path, settings, record = stored
    with sqlite3.connect(path) as connection:
        saved = connection.execute("SELECT role_classification FROM jobs").fetchone()[0]
        payload = json.loads(saved)
        payload[field] = value
        connection.execute("UPDATE jobs SET role_classification=?", (json.dumps(payload),))
    prediction = read_saved_predictions(
        settings.database_url, [("uid", record)], classification_version="test",
    )["uid"]
    assert prediction["role_label"] is None
    assert prediction["decision"] is None


@pytest.mark.parametrize("decision", ["review", "reject"])
def test_other_classification_has_no_relevance_score_or_ranking(stored, decision):
    path, settings, record = stored
    with sqlite3.connect(path) as connection:
        saved = connection.execute("SELECT role_classification FROM jobs").fetchone()[0]
        payload = {**json.loads(saved), "label": "other"}
        connection.execute("UPDATE jobs SET role_label='other', role_classification=?",
                           (json.dumps(payload),))
        connection.execute(
            "UPDATE job_relevance_evaluations SET profile_id='other', fit_score=0, "
            "tier='C', decision=?", (decision,),
        )
    before = path.read_bytes()
    predictions = read_saved_predictions(
        settings.database_url, [("uid", record)], profile_versions={"other": "current"},
        classification_version="test",
    )
    prediction = predictions["uid"]
    assert prediction["role_label"] == "other"
    assert prediction["fit_score"] is None and prediction["tier"] is None
    assert prediction["relevance_evaluation"] is None
    assert prediction["evaluation_state"] == "unscored"
    assert prediction["decision_source"] == "role_unscored"
    assert prediction["needs_reevaluation"] is False
    assert prediction["current_eligible"] is False
    assert prediction["decision"] == decision  # Explicit gates remain observable.
    evaluation = evaluate_vacancies([{**case("uid", 0), "role_label": "other"}], predictions)
    assert evaluation["role_classification"]["accuracy"]["value"] == 1
    assert evaluation["ranking"] == []
    assert evaluation["counts"]["ranked"] == 0
    assert evaluation["decision_prediction_coverage"]["value"] == 0
    assert path.read_bytes() == before
