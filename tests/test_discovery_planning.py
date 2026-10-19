from __future__ import annotations

import copy
import json

import pytest

from job_intake.annotation.gold import _snapshot
from job_intake.annotation.text import TEXT_VERSION, source_hash
from job_intake.discovery.planning import ALGORITHM_VERSION, build_plan, rank_candidate
from job_intake.models.job import JobRecord


def job(text=None, **overrides):
    return JobRecord(**{
        "source": "test", "company": "OriginalCorp", "title": "Unusual Original Title",
        "original_url": "https://example.test/seed",
        "description_clean": text or (
            "About us:\nWe build pricing experimentation products for customers.\n"
            "Responsibilities:\n"
            "You will lead pricing experiments and advise commercial decisions.\n"
            "Manage cross functional projects and partner with finance stakeholders.\n"
            "Develop monetization recommendations and measure product outcomes.\n"
            "Qualifications:\nExperience managing PostgreSQL databases is required.\n"
            "Benefits:\nWe offer unlimited travel and generous insurance."
        ), **overrides,
    })


def label(record, uid="seed", **overrides):
    return {
        "schema": "vacancy-tags-v2", "job_uid": uid, "source_hash": source_hash(record),
        "text_version": TEXT_VERSION, "source": _snapshot(record),
        "labeled_at": "2026-10-18", "method": "blind", "tags": {},
        "role_label": "analytics-leadership", "relevance": 3,
        "gate_outcome": "eligible", "notes": "", **overrides,
    }


def write(path, rows):
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    return path


def test_build_plan_grounded_queries_and_source_only_seeds(tmp_path):
    record = job()
    row = label(record, gate_outcome="ineligible", role_label=None)
    path = write(tmp_path / "gold.jsonl", [row])
    before = path.read_bytes()
    plan = build_plan(path, max_queries=20)
    assert path.read_bytes() == before
    assert plan["schema"] == "discovery-plan-v1"
    assert plan["algorithm_version"] == ALGORITHM_VERSION
    assert plan["seeds"][0]["source"] == row["source"]
    assert plan["seeds"][0]["role_label"] is None
    assert not any("PostgreSQL" in duty or "insurance" in duty
                   for duty in plan["seeds"][0]["duties"])
    assert len(plan["queries"]) == 20
    assert sum(query["kind"] == "explore" for query in plan["queries"]) == 4
    for entry in plan["queries"]:
        assert len(entry["query"]) <= 128
        assert "originalcorp" not in entry["query"].casefold()
        assert record.title.casefold() not in entry["query"].casefold()
        assert "postgresql" not in entry["query"].casefold()
        if entry["kind"] == "seed":
            assert entry["seed_ids"] == ["seed"]
            for phrase in entry["query"].split('"')[1::2]:
                assert any(phrase in duty for duty in plan["seeds"][0]["duties"])


def test_plan_excludes_all_known_rows_but_uses_only_human_positives(tmp_path):
    rows = []
    for uid, relevance in [("positive", 2), ("negative", 0), ("uncertain", 1), ("blank", None)]:
        record = job(original_url=f"https://example.test/{uid}?utm_source=x")
        rows.append(label(record, uid, relevance=relevance,
                          method=None if relevance is None else "blind",
                          labeled_at=None if relevance is None else "2026-10-18",
                          role_label=None if relevance is None else "analytics-leadership",
                          gate_outcome=None if relevance is None else "eligible"))
    plan = build_plan(write(tmp_path / "gold.jsonl", rows))
    assert [entry["job_uid"] for entry in plan["seeds"]] == ["positive"]
    assert [entry["job_uid"] for entry in plan["counterexamples"]] == ["negative"]
    assert plan["excluded_job_uids"] == ["blank", "negative", "positive", "uncertain"]
    assert plan["excluded_urls"] == [f"https://example.test/{uid}"
                                     for uid in ["blank", "negative", "positive", "uncertain"]]


def test_holdout_by_id_or_url_never_supplies_seeds_or_counterexamples(tmp_path):
    held = job(original_url="https://example.test/held")
    other = job(original_url="https://example.test/train")
    gold = write(tmp_path / "gold.jsonl", [label(held, "renamed-held"), label(other, "train")])
    holdout = write(tmp_path / "holdout.jsonl", [label(held, "held")])
    plan = build_plan(gold, holdout_path=holdout)
    assert [seed["job_uid"] for seed in plan["seeds"]] == ["train"]
    assert plan["holdout_job_uids"] == ["held"]
    assert plan["excluded_job_uids"] == ["held", "renamed-held", "train"]


def test_human_feedback_adds_positive_and_excludes_batch_sources(tmp_path):
    gold = write(tmp_path / "gold.jsonl", [label(job())])
    feedback_record = job(original_url="https://example.test/feedback")
    feedback = write(tmp_path / "labels.jsonl", [label(feedback_record, "new", relevance=2)])
    plan = build_plan(gold, feedback_path=feedback)
    assert {seed["job_uid"] for seed in plan["seeds"]} == {"seed", "new"}
    assert "https://example.test/feedback" in plan["excluded_urls"]


def test_negative_feedback_retains_human_explanation_and_separate_hiring_gate(tmp_path):
    gold = write(tmp_path / "gold.jsonl", [label(job())])
    negative = label(job(original_url="https://example.test/negative"), "negative",
                     relevance=0, gate_outcome="unknown",
                     notes="Predominantly financial modelling; no analytical project ownership.")
    positive = label(job(original_url="https://example.test/positive"), "positive",
                     relevance=3, gate_outcome="ineligible", notes="Relevant duties, US only.")
    feedback = write(tmp_path / "feedback.jsonl", [negative, positive])
    plan = build_plan(gold, feedback_path=feedback)
    counterexample = plan["counterexamples"][0]
    assert counterexample["notes"] == negative["notes"]
    assert counterexample["method"] == "blind"
    assert counterexample["labeled_at"] == negative["labeled_at"]
    seed = next(row for row in plan["seeds"] if row["job_uid"] == "positive")
    assert seed["gate_outcome"] == "ineligible"
    assert seed["relevance"] == 3


def test_seed_queries_keep_role_scope_before_optional_topics(tmp_path):
    plan = build_plan(write(tmp_path / "gold.jsonl", [label(job())]),
                      max_queries=20, exploration_fraction=0)
    queries = [entry["query"] for entry in plan["queries"]]
    assert "business decisions stakeholder partnership" in queries[0]
    assert "cross functional projects leadership" in queries[1]
    assert all(any(role in query for role in (
        "analytics manager", "data science manager", "business analytics manager",
        "product analytics manager", "analytics project lead", "head of analytics",
    )) for query in queries)
    assert not any("unit economics finance" in query for query in queries)


def test_product_role_is_retrieval_hint_without_requiring_economics(tmp_path):
    record = job("Responsibilities:\nOwn the analytics product roadmap.\n"
                 "Lead projects and collaborate with engineering stakeholders.",
                 title="Product Lead (Analytics & ML)")
    plan = build_plan(write(tmp_path / "gold.jsonl", [label(record)]), max_queries=10)
    assert plan["queries"][0]["query"].startswith("data product manager ")
    assert "finance" not in plan["queries"][0]["query"]


@pytest.mark.parametrize("change", [
    {"relevance": True}, {"relevance": 2.0}, {"method": "model"},
    {"method": None}, {"labeled_at": None}, {"source_hash": "0" * 64},
])
def test_rejects_machine_or_invalid_labels_and_hashes(tmp_path, change):
    with pytest.raises(ValueError):
        build_plan(write(tmp_path / "gold.jsonl", [label(job(), **change)]))


def test_rejects_modified_snapshot_even_if_machine_score_is_high(tmp_path):
    row = label(job())
    row["source"]["description_clean"] += " You will lead new projects."
    with pytest.raises(ValueError, match="source_hash"):
        build_plan(write(tmp_path / "gold.jsonl", [row]))


def test_rejects_duplicate_uids_and_conflicting_feedback_sources(tmp_path):
    row = label(job())
    with pytest.raises(ValueError, match="invalid_job_uid"):
        build_plan(write(tmp_path / "duplicate.jsonl", [row, copy.deepcopy(row)]))
    gold = write(tmp_path / "gold.jsonl", [row])
    changed = label(job("Responsibilities:\nYou will manage different projects."))
    feedback = write(tmp_path / "feedback.jsonl", [changed])
    with pytest.raises(ValueError, match="Conflicting immutable"):
        build_plan(gold, feedback_path=feedback)


def test_original_metadata_needs_readonly_record_binding(tmp_path):
    record = job()
    record.source_metadata = {"feed_position": 18, "description_complete": True}
    row = label(record)
    path = write(tmp_path / "gold.jsonl", [row])
    with pytest.raises(ValueError, match="source_records"):
        build_plan(path)
    plan = build_plan(path, source_records={"seed": record})
    assert plan["seeds"][0]["source_binding"] == "sqlite_verified"
    assert plan["seeds"][0]["source_hash"] == row["source_hash"]
    assert plan["seeds"][0]["snapshot_hash"] != row["source_hash"]
    drifted = copy.deepcopy(record)
    drifted.title = "Changed title"
    with pytest.raises(ValueError, match="snapshot mismatch"):
        build_plan(path, source_records={"seed": drifted})


def test_conflicting_human_feedback_does_not_overwrite_existing_gold(tmp_path):
    row = label(job())
    gold = write(tmp_path / "gold.jsonl", [row])
    feedback = write(tmp_path / "feedback.jsonl", [{**row, "relevance": 0}])
    with pytest.raises(ValueError, match="Conflicting human labels"):
        build_plan(gold, feedback_path=feedback)


@pytest.mark.parametrize("kwargs", [
    {"max_queries": True}, {"max_queries": 0}, {"max_queries": 81},
    {"exploration_fraction": True}, {"exploration_fraction": float("nan")},
    {"exploration_fraction": -1},
])
def test_validates_resource_parameters(tmp_path, kwargs):
    with pytest.raises(ValueError):
        build_plan(write(tmp_path / "gold.jsonl", [label(job())]), **kwargs)


def test_machine_annotation_and_title_cannot_create_positive_seed(tmp_path):
    record = job("About us:\nWe build pricing experimentation analytics products.\n"
                 "Requirements:\nExperience leading projects and advising decisions.")
    record.annotation = {"relevance": 3, "score": 100}
    with pytest.raises(ValueError, match="No validated human"):
        build_plan(write(tmp_path / "gold.jsonl", [label(record)]))


def test_title_is_optional_and_plan_is_reproducible_except_creation_time(tmp_path):
    path = write(tmp_path / "gold.jsonl", [label(job(title=""))])
    first, second = build_plan(path), build_plan(path)
    first.pop("created_at")
    second.pop("created_at")
    assert first == second
    assert len({entry["query"].casefold() for entry in first["queries"]}) == len(first["queries"])
    assert len(first["queries"]) <= 80


def test_rank_uses_literal_duties_ignores_title_company_and_hiring(tmp_path):
    plan = build_plan(write(tmp_path / "gold.jsonl", [label(job())]))
    relevant = job(
        "Responsibilities:\nYou will lead pricing experiments and advise commercial decisions.\n"
        "Manage cross functional projects and partner with finance stakeholders.",
        title="Scientist", company="Different", location_text="US only",
    )
    irrelevant = job(
        "About us:\nWe build pricing experiments and advise commercial decisions.\n"
        "Requirements:\nExperience managing cross functional projects and finance stakeholders.\n"
        "Responsibilities:\nYou will repair office equipment.",
        title="Pricing Experiments Commercial Decisions Finance Stakeholders",
        company="Pricing Experiments Commercial Decisions Finance Stakeholders",
    )
    good = rank_candidate(relevant, plan)
    bad = rank_candidate(irrelevant, plan)
    assert good["priority"] > bad["priority"]
    assert good["seed_ids"] == ["seed"]
    assert good["evidence"]
    assert all(evidence in relevant.description_clean for evidence in good["evidence"])
    assert bad == {"priority": 0.0, "seed_ids": [], "evidence": []}
    relevant.location_text = "Worldwide remote"
    assert rank_candidate(relevant, plan) == good
    assert not set(good).intersection({"relevance", "predicted_label", "gate_outcome"})


def test_company_only_and_benefits_cannot_gain_ranking_priority(tmp_path):
    plan = build_plan(write(tmp_path / "gold.jsonl", [label(job())]))
    record = job("About us:\nOur company leads pricing experiments.\n"
                 "Benefits:\nYou will lead pricing experiments and advise commercial decisions.")
    assert rank_candidate(record, plan)["priority"] == 0


def test_distinct_seed_themes_are_represented_in_queries(tmp_path):
    pricing = job()
    causal = job("Responsibilities:\nYou will design causal experiments and advise strategy.\n"
                 "Lead projects with stakeholders and develop experimental methods.",
                 original_url="https://example.test/causal")
    path = write(tmp_path / "gold.jsonl", [label(pricing, "pricing"), label(causal, "causal")])
    plan = build_plan(path, max_queries=10, exploration_fraction=.2)
    assert {uid for query in plan["queries"] if query["kind"] == "seed"
            for uid in query["seed_ids"]} == {"pricing", "causal"}
    assert len(plan["queries"]) == 10
