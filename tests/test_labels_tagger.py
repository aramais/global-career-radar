"""P2 uses fake clients exclusively; source binding and caches are real."""

from __future__ import annotations

import copy
import json
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from job_intake.annotation.ai import AnnotationModelError
from job_intake.annotation.text import source_units
from job_intake.config.settings import LabelingConfig, load_yaml_mapping
from job_intake.labeling.budget import BudgetExceeded, BudgetGuard
from job_intake.labeling.ledger import LedgerWriter
from job_intake.labeling.tagger import VacancyTagger
from job_intake.models.job import FilterDecision, JobEvaluation, JobRecord
from job_intake.profiles import load_streams

ROOT = Path(__file__).parents[1]


@pytest.fixture
def streams():
    return load_streams(
        load_yaml_mapping(ROOT / "config/rules.yaml"),
        load_yaml_mapping(ROOT / "config/search_profiles.yaml"),
    )


@pytest.fixture
def config(tmp_path):
    return LabelingConfig(
        enabled=True,
        rubric_path=str(ROOT / "config/labeling_rubric.yaml"),
        precedents_path=str(tmp_path / "none.jsonl"),
        cache_dir=str(tmp_path / "cache"),
        ledger_dir=str(tmp_path / "ledger"),
    )


@pytest.fixture
def record():
    return JobRecord(
        source="test",
        company="Example",
        title="Analytics Manager",
        original_url="https://example.test/jobs/one",
        description_clean="Responsibilities\nYou will lead analytics for business decisions.\n"
        "You will partner with leadership on projects.\nRequirements\nEnglish required.",
        source_metadata={"description_complete": True},
    )


def payload(record, **changes):
    units = source_units(replace(record))
    duty = next(u["unit_id"] for u in units if "You will lead" in u["text"])
    req = next(u["unit_id"] for u in units if "English required" in u["text"])
    raw = {
        name: {"value": "unspecified", "evidence": [], "basis": "explicit"}
        for name in ("hiring_eligibility", "work_mode", "working_language", "function_core")
    }
    raw["work_mode"]["office_location"] = ""
    raw["function_core"].update(value="analytics_for_decisions", evidence=[duty])
    raw["working_language"].update(value="english", evidence=[req])
    raw.update(
        domain_fit={
            "industry": "fintech",
            "requirement": "none",
            "required_domain": None,
            "domain": "",
            "evidence": [duty],
            "basis": "explicit",
        },
        preferred_focus={"values": [], "evidence": []},
        reason="provider private explanation",
        role_label="analytics-leadership",
        relevance=2,
        confidence="high",
    )
    raw.update(changes)
    return raw


class FakeClient:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.calls = []

    def set_progress(self, progress):
        self.progress = progress

    def generate_stage(self, stage, model, prompt):
        self.calls.append(stage)
        value = next(self.replies)
        if isinstance(value, Exception):
            raise value
        return value, {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150}


def service(config, streams, replies, **kwargs):
    prices = {
        "models": {
            config.tag_model: {"input_per_million": 1, "output_per_million": 1},
            config.escalate_model: {"input_per_million": 1, "output_per_million": 1},
        }
    }
    tagger = VacancyTagger(config, streams, budget=BudgetGuard(1, prices), **kwargs)
    tagger.client = FakeClient(replies)
    tagger.set_progress(None)
    return tagger


def label(tagger, record, **kwargs):
    return tagger.label(
        record,
        JobEvaluation(FilterDecision.REVIEW),
        {"label": "analytics-leadership", "method": "rules"},
        **kwargs,
    )


def test_complete_label_is_bound_to_source_and_does_not_mutate_record(config, streams, record):
    before = copy.deepcopy(record)
    tagger = service(config, streams, [payload(record)])
    value = label(tagger, record, use_ai=True)
    assert value.status == "labeled" and value.relevance == value.rules_relevance == 2
    assert value.gate_outcome == "unknown" and value.role_label == "analytics-leadership"
    assert value.tags["function_core"]["value"] == "analytics_for_decisions"
    assert value.annotation_input_hash and value.source_hash and value.cache_key
    assert record == before
    assert "provider private explanation" not in "".join(
        p.read_text() for p in Path(config.cache_dir).rglob("*.json")
    )


def test_exact_cache_hit_never_calls_client_twice(config, streams, record):
    tagger = service(config, streams, [payload(record)])
    first = label(tagger, record, use_ai=True)
    second = label(tagger, record, use_ai=True)
    assert tagger.client.calls == ["tag"]
    assert second.relevance == first.relevance and second.usage[0]["cached"]
    assert tagger.stats["cache_hits"] == 1


def test_offline_has_no_paid_calls_and_reuses_facts_after_rubric_edit(
    config, streams, record, tmp_path
):
    path = tmp_path / "rubric.yaml"
    source = Path(config.rubric_path).read_text()
    path.write_text(source)
    config = replace(config, rubric_path=str(path))
    tagger = service(config, streams, [payload(record)])
    label(tagger, record, use_ai=True)
    data = yaml.safe_load(source)
    data["anchors"]["2"] += " Clarified."
    path.write_text(yaml.safe_dump(data, allow_unicode=True))
    value = tagger.relabel_offline(
        record, JobEvaluation(FilterDecision.REVIEW), {"label": "analytics-leadership"}
    )
    assert tagger.client.calls == ["tag"] and value.llm_relevance_stale
    assert value.relevance == value.rules_relevance and "stale_rubric" in value.escalation_triggers
    assert tagger.stats["facts_reuse"] == 1


def test_aggregation_edit_recomputes_without_invalidating_model_cache(
    config, streams, record, tmp_path
):
    path = tmp_path / "rubric.yaml"
    data = yaml.safe_load(Path(config.rubric_path).read_text())
    path.write_text(yaml.safe_dump(data))
    config = replace(config, rubric_path=str(path), final_policy="rules")
    tagger = service(config, streams, [payload(record)])
    label(tagger, record, use_ai=True)
    data["aggregation"]["base_by_function_core"]["analytics_for_decisions"] = 1
    path.write_text(yaml.safe_dump(data))
    value = label(tagger, record, use_ai=False)
    assert tagger.client.calls == ["tag"] and value.rules_relevance == value.relevance == 1
    assert not value.llm_relevance_stale and tagger.stats["cache_hits"] == 1


def test_refresh_stale_requests_a_new_model_answer(config, streams, record, tmp_path):
    path = tmp_path / "rubric.yaml"
    data = yaml.safe_load(Path(config.rubric_path).read_text())
    path.write_text(yaml.safe_dump(data))
    config = replace(config, rubric_path=str(path))
    tagger = service(config, streams, [payload(record), payload(record)])
    label(tagger, record, use_ai=True)
    data["anchors"]["2"] += " Revised."
    path.write_text(yaml.safe_dump(data))
    tagger.config = replace(config, refresh_stale=True)
    value = label(tagger, record, use_ai=True)
    assert tagger.client.calls == ["tag", "tag"] and not value.llm_relevance_stale


def test_invalid_response_never_caches_or_leaks_provider_text(config, streams, record):
    tagger = service(config, streams, [{"secret": "API-SECRET"}])
    value = label(tagger, record, use_ai=True)
    assert value.status == "error" and value.relevance == 1
    assert value.errors[0]["code"] == "schema_violation"
    assert not list(Path(config.cache_dir).rglob("*.json"))
    assert "API-SECRET" not in str(value)


def test_failed_escalation_preserves_first_valid_result(config, streams, record):
    tagger = service(
        config, streams, [payload(record, relevance=3), AnnotationModelError("API-SECRET")]
    )
    value = label(tagger, record, use_ai=True)
    assert value.status == "labeled" and value.relevance == 3 and not value.escalated
    assert value.errors[0]["code"] == "escalation_failed" and "API-SECRET" not in str(value)
    assert tagger.client.calls == ["tag", "escalate"]


def test_model_only_block_requires_same_value_in_second_stage(config, streams, record):
    record.description_clean += "\nThis opening is restricted to residents of Namibia."
    first = payload(record)
    req = [next(unit["unit_id"] for unit in source_units(replace(record))
                if "Namibia" in unit["text"])]
    first["hiring_eligibility"].update(value="restricted_elsewhere", evidence=req)
    second = copy.deepcopy(first)
    tagger = service(config, streams, [first, second])
    value = label(tagger, record, use_ai=True)
    assert value.escalated and value.gate_outcome == "ineligible"
    second["hiring_eligibility"]["value"] = "unspecified"
    config = replace(config, cache_dir=config.cache_dir + "-different")
    tagger = service(config, streams, [first, second])
    value = label(tagger, record, use_ai=True)
    assert value.gate_outcome == "unknown"


def test_no_escalation_does_not_accept_model_only_hiring_block(config, streams, record):
    record.description_clean += "\nThis opening is restricted to residents of Namibia."
    raw = payload(record)
    raw["hiring_eligibility"].update(
        value="restricted_elsewhere", evidence=[next(
            unit["unit_id"] for unit in source_units(replace(record)) if "Namibia" in unit["text"]
        )]
    )
    config = replace(config, escalation=replace(config.escalation, enabled=False))
    value = label(service(config, streams, [raw]), record, use_ai=True)
    assert (
        value.gate_outcome == "unknown"
        and "gate:unconfirmed_model_block" in value.validation_warnings
    )


def test_role_disagreement_without_confirmation_keeps_classifier(config, streams, record):
    config = replace(config, escalation=replace(config.escalation, enabled=False))
    value = label(
        service(config, streams, [payload(record, role_label="business-analytics")]),
        record,
        use_ai=True,
    )
    assert value.role_label == "analytics-leadership"


def test_source_change_invalidates_cache(config, streams, record):
    tagger = service(config, streams, [payload(record), payload(record)])
    first = label(tagger, record, use_ai=True)
    record.source_metadata["description_complete"] = False
    gated = label(tagger, record, use_ai=True)
    assert (
        gated.route == "needs_fetch" and gated.annotation_input_hash != first.annotation_input_hash
    )
    assert tagger.client.calls == ["tag"]


def test_budget_required_before_any_paid_request(config, streams, record):
    tagger = VacancyTagger(config, streams)
    tagger.client = FakeClient([payload(record)])
    with pytest.raises(BudgetExceeded, match="budget_required"):
        label(tagger, record, use_ai=True)
    assert not tagger.client.calls


def test_attempt_ledger_and_cache_reuse_are_compatible_with_eval(config, streams, record, tmp_path):
    path = tmp_path / "attempts.jsonl"
    ledger = LedgerWriter(path)
    tagger = service(config, streams, [payload(record)], ledger=ledger)
    label(tagger, record, use_ai=True)
    label(tagger, record, use_ai=False)
    ledger.close()
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(rows) == 2 and rows[0]["attempt"] == 1 and rows[0]["usage"]["input_tokens"] == 100
    assert rows[1]["cache_reuse"] and rows[1]["original_call_id"] == rows[0]["call_id"]
    assert "provider private explanation" not in path.read_text()


def test_escalation_cannot_attach_new_unconfirmed_block_reasons(config, streams, record):
    record.description_clean += (
        "\nThis opening is restricted to residents of Namibia.\nFrench required."
    )
    first = payload(record)
    units = source_units(replace(record))
    evidence = [next(unit["unit_id"] for unit in units if "Namibia" in unit["text"])]
    first["hiring_eligibility"].update(value="restricted_elsewhere", evidence=evidence)
    second = copy.deepcopy(first)
    second["working_language"].update(value="other_language_required", evidence=[next(
        unit["unit_id"] for unit in units if "French required" in unit["text"]
    )])
    value = label(service(config, streams, [first, second]), record, use_ai=True)
    assert value.gate_outcome == "ineligible"
    assert value.gate_reasons == ["hiring_region_restricted"]
    assert "gate:unconfirmed_model_block" in value.validation_warnings


@pytest.mark.parametrize(
    "cached_warnings",
    [
        [
            "tag:PRIVATE_SOURCE:API_SECRET",
            "tag:function_core:API_SECRET",
            "tag:API_SECRET:no_evidence",
            "tag:function_core:no_evidence:API_SECRET",
            "tag:function_core:no_evidence",
            42,
            None,
        ],
        "tag:function_core:API_SECRET",
        {"tag:function_core:no_evidence": "API_SECRET"},
        None,
    ],
)
def test_cached_warnings_replay_only_closed_tag_code_pairs(
    config, streams, record, cached_warnings
):
    tagger = service(config, streams, [payload(record)])
    label(tagger, record, use_ai=True)
    path = next(Path(config.cache_dir).rglob("*.json"))
    cached = json.loads(path.read_text())
    cached["warnings"] = cached_warnings
    path.write_text(json.dumps(cached))
    result = label(tagger, record, use_ai=False)
    assert "API_SECRET" not in str(result)
    expected = ["tag:function_core:no_evidence"] if isinstance(cached_warnings, list) else []
    assert result.validation_warnings == expected
    assert tagger.client.calls == ["tag"]


def test_escalation_share_warning_uses_shared_run_counts_without_changing_stats(
    config,
    streams,
    record,
    tmp_path,
):
    ledger = LedgerWriter(tmp_path / "share.jsonl")
    events = []
    first = service(config, streams, [payload(record)], ledger=ledger)
    first.set_progress(events.append)
    label(first, record, use_ai=True)
    second_record = replace(record, original_url="https://example.test/jobs/two")
    second = service(config, streams, [payload(second_record, relevance=3)] * 2, ledger=ledger)
    second.set_progress(events.append)
    label(second, second_record, use_ai=True)
    ledger.close()
    warnings = [event for event in events if event["event"] == "label_escalation_share_warning"]
    assert warnings == [
        {
            "event": "label_escalation_share_warning",
            "threshold": 0.4,
            "labeled": 2,
            "escalated": 1,
            "share": 0.5,
        }
    ]
    expected = {"calls", "cache_hits", "facts_reuse", "escalations", "errors", "gated"}
    assert set(first.stats) == set(second.stats) == expected
    assert not any("provider private explanation" in str(event) for event in events)


def test_escalation_share_warning_threshold_is_strict_and_gated_results_do_not_count(
    config,
    streams,
    record,
):
    config = replace(config, escalation=replace(config.escalation, max_share_warning=1.0))
    events = []
    tagger = service(config, streams, [payload(record, relevance=3)] * 2)
    tagger.set_progress(events.append)
    label(tagger, record, use_ai=True)
    assert not any(e["event"] == "label_escalation_share_warning" for e in events)
    assert tagger._labels_observed == 1 and tagger._labels_escalated == 1
    label(tagger, replace(record, source_metadata={"truncated": True}), use_ai=True)
    assert tagger._labels_observed == 1
