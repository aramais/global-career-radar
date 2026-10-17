from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

from job_intake.annotation.eval_runner import read_saved_records
from job_intake.annotation.gold import _snapshot
from job_intake.annotation.text import TEXT_VERSION, source_hash
from job_intake.config.settings import AppConfig, LLMConfig, TelegramConfig

_SPEC = importlib.util.spec_from_file_location(
    "classifier_comparison", Path(__file__).resolve().parents[1]
    / "scripts/compare_role_classifier.py",
)
comparison = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(comparison)


def revision(outcomes):
    return {
        "policy": "single-role-v1", "profiles": {"analytics-leadership": "v1", "other": "v1"},
        "kind": "snapshot", "snapshot_path": "baseline.json", "text_version": TEXT_VERSION,
        "code_fingerprint": "code", "config_fingerprints": {"rules.yaml": "rules"},
        "evaluations": [{"job_uid": uid, "profile_id": role, "tier": tier, "score": score,
                         "decision": "review"} for uid, role, tier, score in outcomes],
    }


@pytest.fixture
def cohort(tmp_path, monkeypatch):
    database = tmp_path / "jobs.db"
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE jobs (job_uid TEXT, source TEXT, company TEXT, title TEXT, "
            "original_url TEXT, description_raw TEXT, description_clean TEXT, "
            "source_metadata TEXT, annotation TEXT, updated_at TEXT, status TEXT)"
        )
        for uid in ("relevant", "irrelevant", "unlabeled"):
            connection.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                               (uid, "test", "Example", "Analytics Manager", "https://test/job",
                                "Lead analytics", "Lead analytics", "{}", "{}", "2026-10-17",
                                "open"))
    config = AppConfig(
        database_url=f"sqlite:///{database}", log_level="INFO", sources=[],
        rules_path=tmp_path / "rules.yaml", search_profiles_path=tmp_path / "profiles.yaml",
        company_watchlist_path=tmp_path / "watchlist.yaml", export_dir=tmp_path,
        telegram=TelegramConfig(), llm=LLMConfig(),
    )
    _, saved, _ = read_saved_records(config.database_url, limit=None)
    cases = []
    for uid, record in saved:
        labeled = uid != "unlabeled"
        cases.append({
            "job_uid": uid, "source_hash": source_hash(record), "text_version": TEXT_VERSION,
            "source": _snapshot(record), "labeled_at": "2026-10-17" if labeled else None,
            "method": "blind" if labeled else None, "tags": {}, "notes": "",
            "relevance": (3 if uid == "relevant" else 0) if labeled else None,
            "role_label": ("analytics-leadership" if uid == "relevant" else "other")
            if labeled else None,
            "gate_outcome": "eligible" if labeled else None,
        })
    gold = tmp_path / "gold.jsonl"
    gold.write_text("\n".join(json.dumps(case) for case in cases) + "\n")
    selected = [(uid, record) for uid, record in saved if uid != "unlabeled"]
    diff = {
        "source_snapshot": {"fingerprint": comparison._source_fingerprint(selected), "jobs": 2},
        "baseline": revision([("relevant", "other", "C", 0),
                              ("irrelevant", "analytics-leadership", "B", 10)]),
        "current": revision([("relevant", "analytics-leadership", "B", 10),
                             ("irrelevant", "other", "C", 0)]),
        "scope": {"paid_prefilter": "excluded", "semantic_reranking": "excluded"},
    }

    def fake_diff(config_arg, **kwargs):
        assert config_arg is config
        assert set(kwargs["job_uids"]) == {"relevant", "irrelevant"}
        return diff

    monkeypatch.setattr(comparison, "run_rules_diff", fake_diff)
    return config, gold, diff, database


def test_other_is_an_explicit_unranked_role_and_routes_are_not_reused():
    predictions = comparison.predictions_for(revision([("job", "other", "C", 0)]))
    assert predictions["job"] == {
        "job_uid": "job", "role_label": "other", "decision": "review", "tier": None,
        "fit_score": None, "route": None, "tags": {},
    }


def test_duplicate_completed_gold_ids_are_rejected_before_policy_run():
    case = {"job_uid": "duplicate", "role_label": "other", "relevance": 0,
            "gate_outcome": "unknown"}
    with pytest.raises(ValueError, match="unique job_uid"):
        comparison.completed_cases([case, case.copy()])


def test_comparison_selects_complete_decisions_and_keeps_inputs_unchanged(cohort):
    config, gold, _, database = cohort
    before = {path: path.read_bytes() for path in (database, gold)}
    report = comparison.compare(config, gold=gold, baseline="baseline.json")
    assert report["gold"]["total_rows"] == 3
    assert report["gold"]["completed_rows"] == 2
    assert report["before"]["role_classification"]["accuracy"]["numerator"] == 0
    assert report["after"]["role_classification"]["accuracy"]["numerator"] == 2
    assert report["after"]["role_classification"]["accuracy"]["denominator"] == 2
    assert report["before"]["metrics"]["relevant_recall_AB"]["value"] == 0
    assert report["after"]["metrics"]["relevant_recall_AB"]["value"] == 1
    assert report["after"]["metrics"]["precision_AB"]["value"] == 1
    assert report["role_changes"] == {"improved": 2}
    assert len(report["relevant_vacancies"]) == 1
    assert report["before"]["counts"]["ranked"] == 1
    assert report["database_access"] == "read_only" and report["api_calls"] == 0
    assert {path: path.read_bytes() for path in before} == before


def test_report_retains_denominators_intervals_and_all_rows(cohort, tmp_path):
    config, gold, _, _ = cohort
    report = comparison.compare(config, gold=gold, baseline="baseline.json")
    output = tmp_path / "comparison.json"
    json_path, markdown = comparison.save_comparison(report, output, protected_paths=[gold])
    assert json.loads(json_path.read_text())["version"] == comparison.VERSION
    text = markdown.read_text()
    assert "2/2 = 100.0%" in text and "95% CI" in text
    assert "Калибровочная выборка" in text and "Source snapshot:" in text
    assert "## Релевантные вакансии" in text and "## Все завершённые решения" in text
    assert text.count("Example — Analytics Manager (relevant)") == 2


def test_same_source_snapshot_is_required(cohort):
    config, gold, diff, _ = cohort
    diff["source_snapshot"]["fingerprint"] = "different"
    with pytest.raises(ValueError, match="Source inputs changed"):
        comparison.compare(config, gold=gold, baseline="baseline.json")


def test_genuine_gold_source_drift_is_rejected(cohort):
    config, gold, _, _ = cohort
    cases = [json.loads(line) for line in gold.read_text().splitlines()]
    for case in cases:
        if case["job_uid"] == "relevant":
            case["source_hash"] = "0" * 64
            case["source"]["description_raw"] = "Old source body"
    gold.write_text("\n".join(json.dumps(case) for case in cases) + "\n")
    with pytest.raises(ValueError, match="Gold source content changed"):
        comparison.compare(config, gold=gold, baseline="baseline.json")


@pytest.mark.parametrize("policy", ["multi-profile-legacy", None])
def test_legacy_multiple_role_policies_are_not_silently_collapsed(policy):
    value = revision([("job", "other", "C", 0)])
    value["policy"] = policy
    with pytest.raises(ValueError, match="one selected role"):
        comparison.predictions_for(value)


def test_both_output_paths_are_checked_before_writing(cohort):
    config, gold, _, _ = cohort
    report = comparison.compare(config, gold=gold, baseline="baseline.json")
    markdown = gold.with_suffix(".md")
    markdown.write_text("Protected source")
    old_gold = gold.read_bytes()
    with pytest.raises(ValueError, match="must not overwrite"):
        comparison.save_comparison(report, gold.with_suffix(".json"), protected_paths=[markdown])
    assert not gold.with_suffix(".json").exists()
    assert gold.read_bytes() == old_gold
    assert markdown.read_text() == "Protected source"


def test_relevance_agreement_keeps_binary_coarse_and_strong_a_separate():
    grades = [(3, "A"), (3, "B"), (3, "C"), (2, "B"), (2, "A"),
              (1, "B"), (0, "C"), (1, None)]
    cases = [{"job_uid": str(index), "relevance": relevance}
             for index, (relevance, _) in enumerate(grades)]
    predictions = {str(index): {"tier": tier, "decision": "reject", "role_label": "other"}
                   for index, (_, tier) in enumerate(grades)}
    metrics = comparison.scoring_metrics(cases, predictions)
    assert metrics["binary_confusion"] == {"TP": 4, "FP": 1, "FN": 1, "TN": 2}
    assert metrics["binary_relevance_agreement"] == comparison.proportion(6, 8)
    assert metrics["coarse_grade_agreement"] == comparison.proportion(4, 8)
    assert metrics["precision_A_strong"] == comparison.proportion(1, 2)
    assert metrics["recall_strong_A"] == comparison.proportion(1, 3)
    assert metrics["coarse_grade_agreement"]["ci95"] is not None


@pytest.mark.parametrize("relevance", [0, 1])
@pytest.mark.parametrize("tier", ["C", None])
def test_low_coarse_grade_does_not_distinguish_zero_one_or_unscored(relevance, tier):
    assert comparison.coarse_grade_matches(relevance, tier)
    assert not comparison.coarse_grade_matches(2, tier)
    assert not comparison.coarse_grade_matches(3, tier)


def test_scoring_agreement_has_explicit_zero_denominators():
    metrics = comparison.scoring_metrics([], {})
    assert metrics["binary_confusion"] == {"TP": 0, "FP": 0, "FN": 0, "TN": 0}
    for name in ("binary_relevance_agreement", "coarse_grade_agreement",
                 "precision_A_strong", "recall_strong_A"):
        assert metrics[name] == comparison.proportion(0, 0)


def test_scoring_focus_renders_separate_role_and_scoring_results(cohort):
    config, gold, _, database = cohort
    original = database.read_bytes()
    report = comparison.compare(config, gold=gold, baseline="baseline.json", focus="scoring")
    text = comparison.render_markdown(report)
    assert report["focus"] == "scoring"
    assert text.startswith("# Скоринг: до → после")
    assert "Согласие релевантности" in text and "Совпадение профессии" in text
    assert "Precision A: оценка 3 среди A" in text and "Recall A: A среди оценки 3" in text
    assert "Результат профессии | Результат скоринга |" in text
    assert "| TP | 0 | 1 |" in text
    assert report["role_changes"] == {"improved": 2}
    assert report["scoring_changes"] == {"improved": 1, "unchanged": 1}
    assert report["hiring_decision_comparison"]["unchanged"] is True
    assert report["hiring_decision_comparison"]["compared"] == 2
    assert "Версии профилей:" in text and "Config `rules.yaml` SHA-256:" in text
    assert database.read_bytes() == original


def test_hiring_decision_drift_is_described_without_claiming_score_agreement():
    before = {"stable": {"decision": "review"}, "changed": {"decision": "pass"}}
    after = {"stable": {"decision": "review"}, "changed": {"decision": "reject"}}
    result = comparison.hiring_decision_comparison(before, after)
    assert result["unchanged"] is False
    assert result["changed_job_uids"] == ["changed"]
    assert result["changed_count"] == 1 and result["compared"] == 2
    assert result["before_counts"] == {"review": 1, "pass": 1}
    assert result["after_counts"] == {"review": 1, "reject": 1}


def test_focus_defaults_to_classification_and_rejects_unknown_focus(cohort):
    config, gold, _, _ = cohort
    report = comparison.compare(config, gold=gold, baseline="baseline.json")
    assert report["focus"] == "classification"
    assert comparison.render_markdown(report).startswith("# Классификатор: до → после")
    with pytest.raises(ValueError, match="focus"):
        comparison.compare(config, gold=gold, baseline="baseline.json", focus="unknown")


def test_cli_accepts_scoring_focus_and_saves_reports(cohort, tmp_path, monkeypatch):
    config, gold, _, _ = cohort
    output = tmp_path / "scoring.json"
    monkeypatch.setattr(comparison, "load_app_config", lambda path: config)
    monkeypatch.setattr(sys, "argv", ["compare_role_classifier.py", "--gold", str(gold),
                                     "--baseline", "baseline.json", "--output", str(output),
                                     "--focus", "scoring"])
    comparison.main()
    assert json.loads(output.read_text())["focus"] == "scoring"
    assert output.with_suffix(".md").read_text().startswith("# Скоринг: до → после")
