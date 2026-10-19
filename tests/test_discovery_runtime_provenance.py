from __future__ import annotations

from copy import deepcopy

import pytest
from test_discovery_planning import job, label, write
from test_discovery_selection_service import Fetcher, Search, Selector, plan, vacancy
from typer.testing import CliRunner

from job_intake.annotation import cohorts
from job_intake.cli import app
from job_intake.discovery.artifacts import read_jsonl, write_json
from job_intake.discovery.service import run_discovery


def cohort_for(examples, path, *, role="development"):
    return {
        "schema": cohorts.SCHEMA, "cohort_id": "cohort-" + role, "dataset_role": role,
        "rubric_version": "test-v1", "created_at": "2026-10-19T00:00:00Z",
        "exposure_log": str(path),
        "members": [{"job_uid": row["job_uid"], "source_hash": row["source_hash"],
                     "duplicate_group": cohorts.duplicate_group(row["source"]),
                     "url": row["source"]["original_url"]} for row in examples],
    }


@pytest.fixture
def registry(tmp_path, monkeypatch):
    path = tmp_path / "registered.jsonl"
    monkeypatch.setattr(cohorts, "DEFAULT_REGISTRY", path)
    return path


@pytest.mark.parametrize("mirror", [False, True])
def test_ready_plan_cannot_bypass_newly_registered_holdout(tmp_path, registry, mirror):
    ready = plan()
    registered = deepcopy(ready["seeds"])
    if mirror:
        registered[0]["job_uid"] = "different-heldout-id"
        registered[0]["source_hash"] = "f" * 64
    protected = cohort_for(registered, tmp_path / "holdout-events.jsonl", role="holdout")
    cohorts.register_cohort(registry, protected)
    # Passing a different explicit development sidecar must not mask global protection.
    ready["cohort"] = cohort_for(ready["counterexamples"], tmp_path / "development.jsonl")
    search = Search([vacancy(1)])
    selector = Selector(["accept"])
    with pytest.raises(ValueError, match="Holdout"):
        run_discovery(ready, tmp_path / "blocked", search=search, fetcher=Fetcher([]),
                      selector=selector, budget_usd=.1, target_candidates=1)
    assert search.calls == selector.calls == 0
    assert not (tmp_path / "blocked").exists()
    assert not (tmp_path / "holdout-events.jsonl").exists()


def test_planner_records_canonical_journal_without_explicit_flag(tmp_path, registry, monkeypatch):
    from job_intake.discovery import cli

    monkeypatch.setattr(cli, "_records", lambda *_: {})
    example = label(job())
    gold = write(tmp_path / "human.jsonl", [example])
    journal = tmp_path / "canonical-events.jsonl"
    manifest = cohort_for([example], journal)
    sidecar = tmp_path / "cohort.json"
    write_json(sidecar, manifest)
    cohorts.register_cohort(registry, manifest)
    result = CliRunner().invoke(app, ["discovery", "plan", "--gold", str(gold),
                                     "--cohort", str(sidecar), "--output",
                                     str(tmp_path / "plan.json"),
                                     "--max-queries", "3"])
    assert result.exit_code == 0, result.output
    events = read_jsonl(journal)
    assert len(events) == 1  # Explicit and registered cohort are deduplicated.
    assert events[0]["purpose"] == "discovery_seed"
    assert events[0]["actor"] == "discovery-plan"
    assert events[0]["job_uids"] == ["seed"]


def test_registered_canonical_journals_receive_runtime_selector_usage(tmp_path, registry):
    ready = plan()
    journal = tmp_path / "canonical-events.jsonl"
    manifest = cohort_for([*ready["seeds"], *ready["counterexamples"]], journal)
    cohorts.register_cohort(registry, manifest)

    class AuditedSelector(Selector):
        def select(self, prompt, units):
            before_post = read_jsonl(journal)
            assert any(event["purpose"] == "prompt_demo" and
                       event["actor"] == "discovery-selector" for event in before_post)
            assert any(event["purpose"] == "negative_example" and
                       event["actor"] == "discovery-selector" for event in before_post)
            return super().select(prompt, units)

    records = [vacancy(1)]
    result = run_discovery(ready, tmp_path / "run", search=Search(records),
                           fetcher=Fetcher(records), selector=AuditedSelector(["accept"]),
                           budget_usd=.1, target_candidates=1)
    assert result["counts"]["candidates"] == 1
    assert len(result["example_exposure_events"]) == 2
    assert len(read_jsonl(journal)) == 4


def test_missing_canonical_journal_refuses_runtime_before_payments(tmp_path, registry):
    ready = plan()
    manifest = cohort_for(ready["seeds"], tmp_path / "unused.jsonl")
    del manifest["exposure_log"]
    cohorts.register_cohort(registry, manifest)
    search = Search([vacancy(1)])
    with pytest.raises(ValueError, match="journal"):
        run_discovery(ready, tmp_path / "blocked", search=search, fetcher=Fetcher([]),
                      budget_usd=.1, target_candidates=1)
    assert search.calls == 0 and not (tmp_path / "blocked").exists()


def test_plan_declared_holdout_ids_are_checked_at_runtime(tmp_path, registry):
    ready = plan()
    ready["holdout_job_uids"] = ["seed"]
    with pytest.raises(ValueError, match="Holdout"):
        run_discovery(ready, tmp_path / "blocked", search=Search([]), fetcher=Fetcher([]),
                      budget_usd=.1, target_candidates=1)


def test_embedded_sidecar_cannot_redirect_registered_canonical_journal(tmp_path, registry):
    ready = plan()
    manifest = cohort_for(ready["seeds"], tmp_path / "canonical.jsonl")
    cohorts.register_cohort(registry, manifest)
    ready["cohort"] = {**manifest, "exposure_log": str(tmp_path / "bypass.jsonl")}
    search = Search([vacancy(1)])
    with pytest.raises(ValueError, match="immutable registered"):
        run_discovery(ready, tmp_path / "blocked", search=search, fetcher=Fetcher([]),
                      budget_usd=.1, target_candidates=1)
    assert search.calls == 0 and not (tmp_path / "bypass.jsonl").exists()


@pytest.mark.parametrize("stage", ["selector", "rerank"])
def test_legacy_plan_records_local_prompt_inputs_before_model_call(tmp_path, registry, stage):
    ready = plan()
    records = [vacancy(1)]
    output = tmp_path / "legacy"

    def check_journal():
        events = read_jsonl(output / "example-exposure.jsonl")
        event = next(row for row in events if row["stage"] == stage
                     and row["purpose"] == "prompt_demo")
        assert event["examples"][0]["job_uid"] == ready["seeds"][0]["job_uid"]
        assert event["examples"][0]["source_hash"] == ready["seeds"][0]["source_hash"]
        assert not registry.exists()  # No implicit cohort registration.

    class LocalSelector(Selector):
        def select(self, prompt, units):
            check_journal()
            return super().select(prompt, units)

    class LocalReranker:
        model = "local-fixture"

        def prepare(self, record, examples):
            return "fixture prompt", {"duty": record.description_clean}

        def reserve_cost(self, prompt):
            return .001

        def rank(self, prompt, units):
            check_journal()
            return {"priority": 99}, {"usage_known": True, "cost_usd": .001}

    run_discovery(ready, output, search=Search(records), fetcher=Fetcher(records),
                  selector=LocalSelector(["accept"]) if stage == "selector" else None,
                  semantic=LocalReranker() if stage == "rerank" else None,
                  budget_usd=.1, target_candidates=1)
