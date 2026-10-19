from __future__ import annotations

import copy
import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from typer.testing import CliRunner

from job_intake.annotation.accuracy import run_accuracy
from job_intake.annotation.cohorts import (
    SCHEMA,
    adjudication_queue,
    digest,
    duplicate_group,
    independent_holdout,
    load_cohort,
    population_estimates,
    record_example_exposure,
    record_exposure,
    record_registered_exposure,
    register_cohort,
    registered_cohorts,
    resolve_adjudication,
    sample_frame,
    select_cases,
    union_search_frames,
    validate_cohort,
    validate_cohort_boundaries,
    validate_registered_seed_usage,
    validate_seed_usage,
    write_new,
)
from job_intake.annotation.evaluation_cli import app
from job_intake.annotation.experiments import declare_experiment, ledger_metrics, measure_experiment
from job_intake.annotation.frozen_eval import (
    BUNDLE_SCHEMA,
    DEFAULT_POLICY,
    build_bundle,
    compare_runs,
    create_run,
    load_bundle,
    source_snapshot,
    task_metrics,
    validate_policy,
)
from job_intake.annotation.gold import _snapshot, validate_gold
from job_intake.annotation.text import TEXT_VERSION, source_hash
from job_intake.config.settings import AppConfig, LLMConfig, TelegramConfig
from job_intake.models.job import JobRecord


def record():
    return JobRecord(
        source="test",
        company="Example",
        title="Analytics Manager",
        original_url="https://example.test/jobs/1",
        description_clean="Responsibilities:\nLead pricing decisions with stakeholders.",
    )


def label(uid="uid", *, relevance=3, gate="eligible"):
    source = record()
    return {
        "schema": "vacancy-tags-v2",
        "job_uid": uid,
        "source_hash": source_hash(source),
        "text_version": TEXT_VERSION,
        "labeled_at": "2026-10-19",
        "method": "blind",
        "role_label": "analytics-leadership",
        "relevance": relevance,
        "gate_outcome": gate,
        "tags": {},
        "notes": "",
        "source": _snapshot(source),
    }


def cohort(role="holdout", *, clean=True):
    result = {
        "schema": SCHEMA,
        "cohort_id": "independent-01",
        "dataset_role": role,
        "rubric_version": "human-tags-v2",
        "created_at": "2026-10-19T12:00:00+00:00",
        "exposure_log": "/__hr_frozen_eval_synthetic_no_writes__/exposure.jsonl",
        "members": [
            {
                "job_uid": "uid",
                "source_hash": label()["source_hash"],
                "duplicate_group": duplicate_group(_snapshot(record())),
                "label_method": "blind",
                "original_labeled_at": "2026-10-19",
                "url": "https://example.test/jobs/1",
            }
        ],
    }
    if clean:
        result["provenance_review"] = {
            "reviewed_by": "human",
            "reviewed_at": result["created_at"],
            "clean_history_confirmed": True,
            "prior_exposures": [],
        }
    return result


def bundle(n=30, *, role="holdout", gate="eligible"):
    cases = [label(f"uid-{i}", gate=gate) for i in range(n)]
    manifest = cohort(role)
    manifest["members"] = [{**manifest["members"][0], "job_uid": row["job_uid"]} for row in cases]
    result = {
        "schema": BUNDLE_SCHEMA,
        "created_at": datetime.now(UTC).isoformat(),
        "rows": [
            {
                "job_uid": row["job_uid"],
                "source_hash": row["source_hash"],
                "source": source_snapshot(record()),
                "saved_annotation": {},
                "saved_prediction": None,
                "label": row,
            }
            for row in cases
        ],
        "versions": {"baseline": "v1", "candidate": "v2"},
        "cohort": manifest,
        "gate_policy": {
            **copy.deepcopy(DEFAULT_POLICY),
            "minimum_cases": 30,
            "minimum_relevant": 0,
            "minimum_full_tag_cases": 0,
            "minimum_blind_cases": 0,
        },
    }
    result["bundle_id"] = digest(result)
    return result


def predictions(frozen, *, gate="eligible", decision="pass"):
    return [
        {
            "job_uid": row["job_uid"],
            "source_hash": row["source_hash"],
            "role_label": row["label"]["role_label"],
            "gate_outcome": gate,
            "tier": "A",
            "fit_score": 20,
            "decision": decision,
            "tags": {},
        }
        for row in frozen["rows"]
    ]


def runs(frozen, *, operational=None):
    operational = operational or {
        "complete": True,
        "prices_fresh": True,
        "uncertain_bills": 0,
        "cost_usd": 0.1,
        "wall_seconds": 2,
    }
    return [
        create_run(
            frozen,
            predictions=predictions(frozen),
            variant=variant,
            version=frozen["versions"][variant],
            run_id=uid,
            operational=operational,
        )
        for variant, uid in (("baseline", "b1"), ("candidate", "c1"), ("candidate", "c2"))
    ]


@pytest.fixture
def saved(tmp_path):
    database = tmp_path / "jobs.db"
    job = record()
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE jobs (job_uid TEXT, source TEXT, company TEXT, title TEXT, "
            "original_url TEXT, description_raw TEXT, description_clean TEXT, "
            "source_metadata TEXT, annotation TEXT, updated_at TEXT, archived_at TEXT, "
            "status TEXT)"
        )
        connection.execute(
            "INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "uid",
                job.source,
                job.company,
                job.title,
                job.original_url,
                "",
                job.description_clean,
                "{}",
                "{}",
                "2026-10-19",
                "2026-10-19",
                "open",
            ),
        )
    config = AppConfig(
        database_url=f"sqlite:///{database}",
        log_level="INFO",
        sources=[],
        rules_path=tmp_path / "rules.yaml",
        search_profiles_path=tmp_path / "profiles.yaml",
        company_watchlist_path=tmp_path / "companies.yaml",
        export_dir=tmp_path,
        telegram=TelegramConfig(),
        llm=LLMConfig(),
    )
    gold = tmp_path / "gold.jsonl"
    gold.write_text(json.dumps(label()) + "\n")
    return config, database, gold


def test_closed_sidecar_keeps_gold_v2_untouched():
    answer = label()
    before = copy.deepcopy(answer)
    assert validate_gold([("uid", record())], [answer])["valid"]
    assert select_cases([answer], cohort()) == [answer]
    assert answer == before
    assert "cohort_id" not in answer
    with pytest.raises(ValueError, match="nonclosed"):
        validate_cohort({**cohort(), "arbitrary": True})


@pytest.mark.parametrize(
    "field,value", [("source_hash", "f" * 64), ("labeled_at", "2026-10-18"), ("method", "assisted")]
)
def test_cohort_freezes_source_and_label_provenance(field, value):
    answer = {**label(), field: value}
    with pytest.raises(ValueError, match="changed|match"):
        select_cases([answer], cohort())


def test_blind_is_not_automatically_an_independent_holdout():
    assert not independent_holdout(cohort(clean=False))
    assert not independent_holdout(cohort("curated"))
    assert independent_holdout(cohort())
    unanchored = cohort()
    unanchored.pop("exposure_log")
    assert not independent_holdout(unanchored)
    exposed = cohort()
    exposed["provenance_review"]["prior_exposures"] = ["used for threshold tuning"]
    with pytest.raises(ValueError, match="Previously exposed"):
        validate_cohort(exposed)


@pytest.mark.parametrize(
    "example",
    [
        label(),
        {"job_uid": "mirror", "source_hash": label()["source_hash"]},
        {"source": _snapshot(record())},
        {"duplicate_group": cohort()["members"][0]["duplicate_group"]},
        {"url": "https://example.test/jobs/1"},
    ],
)
def test_holdout_seed_and_mirror_protection(example):
    with pytest.raises(ValueError, match="Holdout"):
        validate_seed_usage(cohort(), [example])
    validate_seed_usage(cohort(), [example], purpose="inference")


def test_duplicate_group_cannot_cross_holdout_and_development():
    development = cohort("development")
    development["cohort_id"] = "dev"
    development["members"][0]["job_uid"] = "mirror"
    development["members"][0]["source_hash"] = "b" * 64
    with pytest.raises(ValueError, match="boundary"):
        validate_cohort_boundaries([cohort(), development])
    other_holdout = cohort()
    other_holdout["cohort_id"] = "fake-fresh-cohort"
    with pytest.raises(ValueError, match="boundary"):
        validate_cohort_boundaries([cohort(), other_holdout])


def test_exposure_is_append_only_and_inference_is_allowed(tmp_path):
    manifest = cohort()
    path = tmp_path / "exposure.jsonl"
    manifest["exposure_log"] = str(path)
    original_manifest = copy.deepcopy(manifest)
    record_exposure(path, manifest, job_uids=["uid"], purpose="inference", actor="runner")
    before = path.read_bytes()
    assert independent_holdout(manifest, log=path)
    record_exposure(
        path, manifest, job_uids=["uid"], purpose="developer_analysis", actor="developer"
    )
    assert path.read_bytes().startswith(before)
    assert not independent_holdout(manifest, log=path)
    assert manifest == original_manifest
    alternate = tmp_path / "alternate.jsonl"
    with pytest.raises(ValueError, match="canonical cohort journal"):
        independent_holdout(manifest, log=alternate)
    with pytest.raises(ValueError, match="canonical cohort journal"):
        record_exposure(alternate, manifest, job_uids=["uid"], purpose="inference", actor="runner")
    assert not alternate.exists()


def test_cohort_exposure_log_resolves_relative_to_manifest(tmp_path):
    manifest = {**cohort(), "exposure_log": "events.jsonl"}
    write_new(tmp_path / "cohort.json", manifest)
    assert load_cohort(tmp_path / "cohort.json")["exposure_log"] == str(tmp_path / "events.jsonl")


def test_registered_holdout_is_global_and_cannot_be_redeclared_as_development(tmp_path):
    registry = tmp_path / "cohorts.jsonl"
    result = register_cohort(registry, cohort())
    assert result["registered"]
    before = registry.read_bytes()
    assert register_cohort(registry, cohort())["idempotent"]
    assert registry.read_bytes() == before
    assert registered_cohorts(registry) == [cohort()]
    with pytest.raises(ValueError, match="Holdout"):
        validate_registered_seed_usage([label()], registry=registry)
    with pytest.raises(ValueError, match="immutable"):
        register_cohort(registry, cohort("development"))
    exposed = cohort("development")
    exposed["cohort_id"] = "dev"
    with pytest.raises(ValueError, match="boundary"):
        register_cohort(registry, exposed)


def test_registry_tampering_is_detected(tmp_path):
    registry = tmp_path / "cohorts.jsonl"
    register_cohort(registry, cohort())
    event = json.loads(registry.read_text())
    event["manifest"]["dataset_role"] = "development"
    registry.write_text(json.dumps(event) + "\n")
    with pytest.raises(ValueError, match="registry history"):
        registered_cohorts(registry)


def test_registered_usage_records_source_mirrors_in_canonical_journal(tmp_path):
    manifest = cohort("calibration")
    manifest["exposure_log"] = str(tmp_path / "canonical.jsonl")
    registry = tmp_path / "registry.jsonl"
    register_cohort(registry, manifest)
    mirror = {**label(), "job_uid": "external-mirror"}
    events = record_registered_exposure(
        [mirror], purpose="prompt_demo", actor="selector", registry=registry
    )
    assert len(events) == 1 and events[0]["job_uids"] == ["uid"]
    assert json.loads((tmp_path / "canonical.jsonl").read_text())["purpose"] == "prompt_demo"
    assert (
        record_registered_exposure(
            [mirror],
            purpose="prompt_demo",
            actor="selector",
            registry=registry,
            exclude_cohort_ids={manifest["cohort_id"]},
        )
        == []
    )
    assert (
        record_example_exposure(
            manifest, [{"job_uid": "unrelated"}], purpose="discovery_seed", actor="planner"
        )
        is None
    )


def test_legacy_run_without_additive_route_metrics_stays_immutable_and_recomputable():
    frozen = bundle()
    before, after, repeat = runs(frozen)
    before["evaluation"].pop("routing")
    before["evaluation"]["task_prediction_coverage"].pop("route")
    before["run_hash"] = digest({key: value for key, value in before.items() if key != "run_hash"})
    original = copy.deepcopy(before)
    compare_runs(frozen, before, after, repeat=repeat)
    assert before == original


def test_stratified_sample_has_frozen_zero_inclusion_and_weights():
    frame = [
        {
            "job_uid": f"u{i}",
            "source_hash": "a" * 64,
            "stratum": "accepted" if i < 10 else "rejected",
            "rank": i,
        }
        for i in range(20)
    ]
    result = sample_frame(frame, quotas={"accepted": 3, "rejected": 2}, seed=123)
    assert result == sample_frame(frame, quotas={"accepted": 3, "rejected": 2}, seed=123)
    assert result["population_estimable"]
    assert result["strata"]["accepted"] == {"population": 10, "selected": 3}
    assert {row["inclusion_probability"] for row in result["frame"]} == {0.3, 0.2}
    assert all(row["sampling_weight"] > 1 for row in result["selected"])
    assert "selected" not in frame[0]
    partial = sample_frame(frame, quotas={"accepted": 5}, seed=123)
    assert not partial["population_estimable"]
    assert all(
        row["inclusion_probability"] == 0
        for row in partial["frame"]
        if row["stratum"] == "rejected"
    )


def test_topk_is_deterministic_and_does_not_claim_population_recall():
    frame = [
        {"job_uid": f"u{i}", "source_hash": "a" * 64, "stratum": "all", "rank": i}
        for i in range(10)
    ]
    result = sample_frame(frame, quotas={"all": 2}, seed=1, mode="top_k")
    assert [row["job_uid"] for row in result["selected"]] == ["u0", "u1"]
    assert not result["population_estimable"]


def test_weighted_selector_recall_requires_rejected_and_uncertain_coverage():
    frame = [
        {
            "job_uid": f"u{i}",
            "source_hash": "a" * 64,
            "stratum": "accepted" if i < 10 else "rejected",
        }
        for i in range(20)
    ]
    sample = sample_frame(frame, quotas={"accepted": 2, "rejected": 5}, seed=42)
    answers = [
        {"job_uid": row["job_uid"], "source_hash": row["source_hash"], "relevance": 3}
        for row in sample["selected"]
    ]
    result = population_estimates(sample, answers)
    assert result["status"] == "measured"
    assert result["precision"]["value"] == 1
    assert result["recall"]["value"] == 0.5
    assert population_estimates(sample, answers[:-1])["recall"]["value"] is None
    accepted_only = sample_frame(frame, quotas={"accepted": 2}, seed=42)
    assert population_estimates(accepted_only, answers)["recall"]["value"] is None


def test_search_experiment_union_preserves_excluded_failed_and_budget_unknowns():
    old = [
        {"job_uid": "old", "source_hash": "a" * 64, "fetch_status": "failed"},
        {"job_uid": "both", "source_hash": "b" * 64, "fetch_status": "complete"},
    ]
    new = [
        {"job_uid": "new", "source_hash": "c" * 64, "fetch_status": "budget_not_processed"},
        {"job_uid": "both", "source_hash": "b" * 64, "fetch_status": "complete"},
    ]
    result = union_search_frames(old, new)
    assert {row["stratum"] for row in result} == {"shared", "baseline_only", "candidate_only"}
    assert (
        next(row for row in result if row["job_uid"] == "new")["observations"]["candidate"][
            "fetch_status"
        ]
        == "budget_not_processed"
    )
    assert all("relevance" not in row for row in result)
    with pytest.raises(ValueError, match="different snapshots"):
        union_search_frames(old, [{**new[1], "source_hash": "d" * 64}])


def test_adjudication_keeps_original_answer_and_history():
    original = label()
    queue = adjudication_queue(
        [original], [{"job_uid": "uid", "reason": "Role disputed", "proposal": "other"}]
    )
    corrected = {**original, "role_label": "other"}
    result = resolve_adjudication(queue, job_uid="uid", answer=corrected, actor="human")
    assert queue["items"][0]["status"] == "pending"
    assert result["items"][0]["original_answer"] == original
    assert result["items"][0]["history"][0]["answer"] == corrected
    with pytest.raises(ValueError, match="snapshot"):
        resolve_adjudication(
            queue, job_uid="uid", answer={**corrected, "source_hash": "c" * 64}, actor="human"
        )


def test_bundle_includes_archive_and_is_immutable_readonly(saved, tmp_path, monkeypatch):
    config, database, gold = saved
    before = database.read_bytes(), gold.read_bytes()
    monkeypatch.setattr(
        "requests.sessions.Session.request",
        lambda *args, **kwargs: pytest.fail("No network in saved evaluation"),
    )
    result = build_bundle(config, gold=gold, versions={"baseline": "v1", "candidate": "v2"})
    assert len(result["rows"]) == 1
    assert result["rows"][0]["source_origin"] == "saved_database"
    output = tmp_path / "frozen.json"
    write_new(output, result)
    assert load_bundle(output) == result
    with pytest.raises(FileExistsError):
        write_new(output, result)
    assert (database.read_bytes(), gold.read_bytes()) == before
    changed = copy.deepcopy(result)
    changed["rows"][0]["label"]["relevance"] = 0
    output.write_text(json.dumps(changed))
    with pytest.raises(ValueError, match="changed"):
        load_bundle(output)


def test_absent_db_row_snapshot_fallback_does_not_invent_prediction(saved):
    config, database, gold = saved
    with sqlite3.connect(database) as connection:
        connection.execute("DELETE FROM jobs")
    with pytest.raises(ValueError, match="fallback"):
        build_bundle(config, gold=gold, versions={"baseline": "v1"})
    result = build_bundle(
        config, gold=gold, versions={"baseline": "v1"}, allow_source_snapshot=True
    )
    assert result["rows"][0]["saved_prediction"] is None
    assert result["rows"][0]["source_origin"] == "gold_source_snapshot"
    evaluated = create_run(result, predictions=None, variant="baseline", version="v1", run_id="s1")
    assert evaluated["evaluation"]["task_prediction_coverage"]["decision"]["numerator"] == 0


def test_missing_source_fallback_accuracy_is_explicit(saved):
    config, database, gold = saved
    with sqlite3.connect(database) as connection:
        connection.execute("DELETE FROM jobs")
    report = run_accuracy(
        config,
        gold=gold,
        allow_source_snapshot=True,
        prices=__import__("pathlib").Path("config/eval_prices.yaml"),
    )
    assert report["predictions"][0]["evaluation_state"] == "missing_saved_prediction"
    assert not report["readiness"]["baseline_measured"]


def test_hiring_reject_is_not_role_recall_loss():
    answer = label(gate="ineligible")
    result = task_metrics(
        [answer],
        {
            "uid": {
                "role_label": "analytics-leadership",
                "gate_outcome": "ineligible",
                "decision": "reject",
                "tier": "A",
                "fit_score": 20,
            }
        },
    )
    assert result["independent_axes"]["role_recall"]["value"] == 1
    assert result["independent_axes"]["blocker_recall"]["value"] == 1
    assert result["independent_axes"]["actionable_p1_recall"]["value"] is None


def test_unknown_eligibility_is_not_actionable():
    result = task_metrics(
        [label(gate="unknown")],
        {"uid": {"gate_outcome": "unknown", "decision": "pass", "tier": "A"}},
    )
    assert result["independent_axes"]["actionable_p1_precision"]["value"] is None
    assert result["independent_axes"]["unknown_eligibility"] == 1


def test_ranking_has_reproducible_random_baseline_and_other_is_na():
    cases = [label(f"u{i}", relevance=i % 4) for i in range(20)]
    preds = {row["job_uid"]: {"tier": "A", "fit_score": row["relevance"]} for row in cases}
    first = task_metrics(cases, preds)["graded_ranking"]
    assert first == task_metrics(cases, preds)["graded_ranking"]
    assert first["ndcg@5"]["value"] == 1
    assert first["ndcg@10"]["random_baseline"] < 1
    assert (
        task_metrics([{**label(), "role_label": "other"}], {})["graded_ranking"]["ndcg@5"]["status"]
        == "not_applicable"
    )


@pytest.mark.parametrize(
    "n,role",
    [(0, "holdout"), (3, "holdout"), (30, "calibration"), (30, "curated"), (30, "discovery")],
)
def test_insufficient_or_exposed_cohorts_cannot_pass(n, role):
    frozen = bundle(max(n, 1), role=role)
    before, after, repeat = runs(frozen)
    result = compare_runs(frozen, before, after, repeat=repeat)
    assert result["status"] == "insufficient_evidence"


def test_complete_independent_comparison_passes_and_regression_fails():
    frozen = bundle()
    before, after, repeat = runs(frozen)
    assert compare_runs(frozen, before, after, repeat=repeat)["status"] == "pass"
    broken = predictions(frozen)
    broken[0]["role_label"] = "other"
    regressed = create_run(
        frozen,
        predictions=broken,
        variant="candidate",
        version="v2",
        run_id="c3",
        operational=after["operational"],
    )
    result = compare_runs(frozen, before, regressed, repeat=repeat)
    assert result["status"] == "fail"
    gate = next(row for row in result["gates"] if row["name"] == "profession_accuracy")
    assert gate["N"] == 30 and gate["status"] == "fail"


def test_missing_predictions_and_missing_prices_never_pass():
    frozen = bundle()
    before, after, repeat = runs(frozen)
    missing = create_run(frozen, predictions=[], variant="candidate", version="v2", run_id="empty")
    result = compare_runs(frozen, before, missing, repeat=repeat)
    assert result["status"] == "insufficient_evidence"
    assert (
        next(row for row in result["gates"] if row["name"] == "current_price_cost")["status"]
        == "insufficient_evidence"
    )


def test_run_requires_same_bundle_version_source_and_independent_repeat():
    frozen = bundle()
    with pytest.raises(ValueError, match="declared"):
        create_run(frozen, predictions=[], variant="new", version="v3", run_id="x")
    bad = predictions(frozen)
    bad[0]["source_hash"] = "f" * 64
    with pytest.raises(ValueError, match="exact frozen source"):
        create_run(frozen, predictions=bad, variant="candidate", version="v2", run_id="x")
    before, after, _ = runs(frozen)
    with pytest.raises(ValueError, match="independent repeat"):
        compare_runs(frozen, before, after, repeat=after)
    with pytest.raises(ValueError, match="unchanged"):
        compare_runs(bundle(31), before, after)
    with pytest.raises(ValueError, match="only represent baseline"):
        create_run(frozen, predictions=None, variant="candidate", version="v2", run_id="x")
    edited = copy.deepcopy(after)
    edited["evaluation"]["role_classification"]["accuracy"]["value"] = 0
    edited["run_hash"] = digest({k: v for k, v in edited.items() if k != "run_hash"})
    with pytest.raises(ValueError, match="metrics disagree"):
        compare_runs(frozen, before, edited)


def test_policy_must_be_predeclared_versioned_and_finite():
    with pytest.raises(ValueError, match="Unsupported"):
        validate_policy({**DEFAULT_POLICY, "version": "ad-hoc"})
    with pytest.raises(ValueError, match="finite"):
        validate_policy({**DEFAULT_POLICY, "maximum_accuracy_regression": float("nan")})


def tariff(as_of="2026-10-19"):
    return {
        "schema": "eval-prices-v1",
        "currency": "USD",
        "as_of": as_of,
        "models": {
            "test": {
                "input_per_million": 1,
                "output_per_million": 2,
                "cached_input_per_million": 0.1,
            }
        },
    }


def attempts():
    return [
        {
            "call_id": "a1",
            "model": "test",
            "attempt": 1,
            "wall_seconds": 2,
            "usage": {
                "input_tokens": 1000,
                "cached_input_tokens": 200,
                "output_tokens": 500,
                "thinking_tokens": 100,
            },
        },
        {
            "call_id": "a2",
            "model": "test",
            "attempt": 2,
            "wall_seconds": 1,
            "usage": {"input_tokens": 1000, "output_tokens": 100},
        },
        {"call_id": "cache", "cache_reuse": True, "original_call_id": "a1"},
    ]


def test_ledger_counts_retries_thinking_cached_tokens_and_rebind_once():
    result = ledger_metrics(
        attempts(),
        prices=tariff(),
        maximum_price_age_days=30,
        measured_at=datetime(2026, 10, 19, tzinfo=UTC),
    )
    assert result["attempts"] == 2 and result["retries"] == 1 and result["cache_reuses"] == 1
    assert result["cost_usd"] == pytest.approx(0.00302)
    assert result["output_tokens"] == 600 and result["thinking_tokens"] == 100
    assert result["complete"] and result["prices_fresh"]


@pytest.mark.parametrize("as_of", ["2026-01-01", "2027-01-01"])
def test_stale_or_future_prices_are_not_fresh(as_of):
    assert not ledger_metrics(
        attempts(),
        prices=tariff(as_of),
        maximum_price_age_days=30,
        measured_at=datetime(2026, 10, 19, tzinfo=UTC),
    )["prices_fresh"]


def test_unknown_bill_is_preserved_not_zero_cost_success():
    result = ledger_metrics(
        [{"call_id": "timeout", "model": "test", "attempt": 1, "wall_seconds": 5, "usage": None}],
        prices=tariff(),
        maximum_price_age_days=30,
    )
    assert result["uncertain_bills"] == 1 and not result["complete"]
    with pytest.raises(ValueError, match="unique call_id"):
        ledger_metrics([attempts()[0], attempts()[0]], prices=tariff(), maximum_price_age_days=30)


def test_experiment_changes_one_factor_and_requires_independent_runs():
    frozen = bundle()
    spec = {
        "experiment_id": "thinking-01",
        "factor": "review_thinking",
        "variants": {
            "baseline": {"review_thinking": "low", "chunk_chars": 3000},
            "candidate": {"review_thinking": "none", "chunk_chars": 3000},
        },
        "budget_usd": 1,
        "goals": {"scope_accuracy": "no regression"},
    }
    declaration = declare_experiment(frozen, **spec)
    before, after, repeat = runs(
        frozen,
        operational={
            **ledger_metrics(
                attempts(),
                prices=tariff(),
                maximum_price_age_days=30,
                measured_at=datetime(2026, 10, 19, tzinfo=UTC),
            ),
            "wall_seconds": 3,
        },
    )
    result = measure_experiment(frozen, declaration, runs=[before, after, repeat])
    assert result["status"] == "insufficient_evidence"  # only one baseline repeat
    assert result["variants"]["candidate"]["cost_per_confirmed_relevant"] is not None
    with pytest.raises(ValueError, match="independently"):
        measure_experiment(frozen, declaration, runs=[before, before])
    bad = copy.deepcopy(spec)
    bad["variants"]["candidate"]["chunk_chars"] = 2000
    with pytest.raises(ValueError, match="exactly one"):
        declare_experiment(frozen, **bad)


def test_retroactive_experiment_and_tampered_run_are_rejected():
    frozen = bundle()
    before, after, repeat = runs(frozen)
    declaration = declare_experiment(
        frozen,
        experiment_id="x",
        factor="compact_payload",
        variants={"baseline": {"compact_payload": False}, "candidate": {"compact_payload": True}},
        budget_usd=1,
        goals={"quality": "no regression"},
    )
    declaration["declared_at"] = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    declaration["declaration_hash"] = digest(
        {k: v for k, v in declaration.items() if k != "declaration_hash"}
    )
    with pytest.raises(ValueError, match="before runs"):
        measure_experiment(frozen, declaration, runs=[before, after, repeat])


def test_experiment_rejected_positives_do_not_count_as_successful_results():
    frozen = bundle()
    declaration = declare_experiment(
        frozen,
        experiment_id="reject",
        factor="compact_payload",
        variants={"baseline": {"compact_payload": False}, "candidate": {"compact_payload": True}},
        budget_usd=1,
        goals={"quality": "no regression"},
    )
    metrics = ledger_metrics(
        attempts(),
        prices=tariff(),
        maximum_price_age_days=30,
        measured_at=datetime(2026, 10, 19, tzinfo=UTC),
        run_wall_seconds=3,
    )
    before = create_run(
        frozen,
        predictions=predictions(frozen),
        variant="baseline",
        version="v1",
        run_id="b1",
        operational=metrics,
    )
    after = create_run(
        frozen,
        predictions=predictions(frozen, decision="reject"),
        variant="candidate",
        version="v2",
        run_id="c1",
        operational=metrics,
    )
    report = measure_experiment(frozen, declaration, runs=[before, after])
    assert report["variants"]["candidate"]["confirmed_relevant_results"] == 0
    assert report["variants"]["candidate"]["cost_per_confirmed_relevant"] is None


def test_compare_cli_records_developer_exposure_once(tmp_path):
    frozen = bundle()
    output, log = tmp_path / "comparison.json", tmp_path / "exposure.jsonl"
    frozen["cohort"]["exposure_log"] = str(log)
    frozen["bundle_id"] = digest(
        {key: value for key, value in frozen.items() if key != "bundle_id"}
    )
    before, after, repeat = runs(frozen)
    paths = {
        name: tmp_path / (name + ".json") for name in ("bundle", "baseline", "candidate", "repeat")
    }
    for name, value in zip(paths, (frozen, before, after, repeat), strict=True):
        write_new(paths[name], value)
    args = [
        "compare",
        "--bundle",
        str(paths["bundle"]),
        "--baseline",
        str(paths["baseline"]),
        "--candidate",
        str(paths["candidate"]),
        "--repeat",
        str(paths["repeat"]),
        "--output",
        str(output),
        "--exposure-log",
        str(log),
    ]
    result = CliRunner().invoke(app, args)
    assert result.exit_code == 0, result.output
    assert json.loads(output.read_text())["status"] == "pass"
    assert not independent_holdout(frozen["cohort"], log=log)
    before_log = log.read_bytes()
    assert CliRunner().invoke(app, args).exit_code != 0
    assert log.read_bytes() == before_log
