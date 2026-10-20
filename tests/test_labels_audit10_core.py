"""Audit 10: source-specific hiring proof, confirmed policy and interrupted bills."""

from __future__ import annotations

import copy
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
import test_labels_tagger as support
import yaml
from test_labels_tagger import label, payload, service

from job_intake.annotation.experiments import ledger_metrics
from job_intake.annotation.text import source_units
from job_intake.filtering import FilterRules, RuleEngine
from job_intake.labeling.budget import BudgetGuard
from job_intake.labeling.gates import rule_gate_reasons
from job_intake.labeling.ledger import LedgerWriter
from job_intake.labeling.rubric import load_rubric, parse_rubric
from job_intake.labeling.tagger import VacancyTagger
from job_intake.labeling.validate import validate_tags
from job_intake.models.job import FilterDecision, JobEvaluation

config = support.config
record = support.record
streams = support.streams


def _check_fact(record, tag, values, text, *, field="description_clean"):
    raw = payload(record)
    raw[tag].update(**values, evidence=["unit:proof"])
    units = [{"unit_id": "unit:proof", "field": field, "section": "requirements", "text": text}]
    return validate_tags(raw, units, ["analytics-leadership"])


@pytest.mark.parametrize(
    "tag,values",
    [
        ("hiring_eligibility", {"value": "restricted_elsewhere"}),
        ("hiring_eligibility", {"value": "open_to_brazil"}),
        ("work_mode", {"value": "remote"}),
        ("work_mode", {"value": "office_elsewhere", "office_location": "London"}),
        ("work_mode", {"value": "office_sao_paulo", "office_location": "São Paulo"}),
        ("working_language", {"value": "other_language_required"}),
        ("working_language", {"value": "portuguese_only"}),
    ],
)
def test_unrelated_english_requirement_does_not_prove_other_hiring_facts(record, tag, values):
    checked, warnings = _check_fact(record, tag, values, "English required.")
    assert checked["tags"][tag]["value"] == "unspecified"
    assert f"tag:{tag}:unsupported_evidence" in warnings


@pytest.mark.parametrize(
    "text,value,location,expected",
    [
        (
            "This is a hybrid role in São Paulo, SP, Brazil.",
            "office_sao_paulo",
            "São Paulo",
            "office_sao_paulo",
        ),
        (
            "This is a hybrid role in Barueri, São Paulo, Brazil.",
            "office_sao_paulo",
            "Barueri",
            "unspecified",
        ),
        (
            "This is a hybrid role in Barueri, São Paulo, Brazil.",
            "office_sao_paulo",
            "São Paulo",
            "unspecified",
        ),
        (
            "This is a hybrid role in São Paulo state.",
            "office_sao_paulo",
            "São Paulo state",
            "unspecified",
        ),
        (
            "This is a hybrid role in Greater São Paulo.",
            "office_sao_paulo",
            "Greater São Paulo",
            "unspecified",
        ),
        (
            "This is a hybrid role in Santos, SP, Brazil.",
            "office_elsewhere",
            "Santos",
            "office_elsewhere",
        ),
        ("This is a hybrid role in London, UK.", "office_elsewhere", "Barueri", "unspecified"),
        (
            "This is a hybrid role in London / São Paulo.",
            "office_elsewhere",
            "London",
            "unspecified",
        ),
        (
            "You will collaborate with our London office.",
            "office_elsewhere",
            "London",
            "unspecified",
        ),
    ],
)
def test_office_value_requires_the_actual_unambiguous_city(record, text, value, location, expected):
    checked, warnings = _check_fact(
        record, "work_mode", {"value": value, "office_location": location}, text
    )
    assert checked["tags"]["work_mode"]["value"] == expected
    if expected == "unspecified":
        assert any(w.startswith("tag:work_mode:") for w in warnings)
    else:
        assert not any(w.startswith("tag:work_mode:") for w in warnings)


@pytest.mark.parametrize(
    "text,value",
    [
        ("This opening is restricted to residents of Namibia.", "restricted_elsewhere"),
        ("Hiring is limited to candidates in the US.", "restricted_elsewhere"),
        ("US work authorization is required.", "restricted_elsewhere"),
        ("U.S. work authorization is required.", "restricted_elsewhere"),
        ("You must be authorized to work in the United States.", "restricted_elsewhere"),
        ("We do not hire applicants in Brazil.", "restricted_elsewhere"),
        ("Candidates based in Brazil are not eligible.", "restricted_elsewhere"),
        ("No applicants from Brazil.", "restricted_elsewhere"),
        ("We hire worldwide except Brazil.", "restricted_elsewhere"),
        ("Candidates located in Brazil are eligible.", "open_to_brazil"),
        ("We are hiring candidates from Latin America.", "open_to_brazil"),
    ],
)
def test_supported_explicit_geography_keeps_its_value_including_negative_brazil_exclusions(
    record,
    text,
    value,
):
    checked, warnings = _check_fact(record, "hiring_eligibility", {"value": value}, text)
    assert checked["tags"]["hiring_eligibility"]["value"] == value
    assert not any(w.startswith("tag:hiring_eligibility:") for w in warnings)


@pytest.mark.parametrize(
    "text",
    [
        "Our company has no Brazil office.",
        "Our company has offices everywhere except Brazil.",
        "Hiring is restricted to residents of Location TBD.",
        "Hiring is restricted to residents of Wonderland.",
        "Hiring is limited to Brazil and Canada.",
        "Hiring is limited to Brazil.",
        "Hiring does not exclude Brazil.",
        "We do not hire only in Brazil.",
        "We do not only hire applicants in Brazil.",
        "We do not hire outside Brazil.",
        "Candidates outside Brazil are not eligible.",
        "Brazil candidates are not ineligible.",
        "It is not true that Brazil candidates are ineligible.",
        "No applicants from Brazil are excluded.",
    ],
)
def test_company_presence_unknown_places_and_brazil_inclusive_lists_do_not_prove_exclusion(
    record,
    text,
):
    checked, _ = _check_fact(record, "hiring_eligibility", {"value": "restricted_elsewhere"}, text)
    assert checked["tags"]["hiring_eligibility"]["value"] == "unspecified"


@pytest.mark.parametrize(
    "value,text,expected",
    [
        ("english", "Working language: English.", "english"),
        ("english", "English is our working language.", "english"),
        ("other_language_required", "French required.", "other_language_required"),
        ("other_language_required", "Fluency in German.", "other_language_required"),
        ("other_language_required", "English required to serve French customers.", "unspecified"),
        ("other_language_required", "We work with German customers.", "unspecified"),
        ("portuguese_only", "Portuguese is required.", "portuguese_only"),
    ],
)
def test_language_requirement_must_apply_to_the_named_language(record, value, text, expected):
    checked, _ = _check_fact(record, "working_language", {"value": value}, text)
    assert checked["tags"]["working_language"]["value"] == expected


def test_false_barueri_city_fact_cannot_make_real_tagger_eligible(config, streams, record):
    record.description_clean += (
        "\nCandidates located in Brazil are eligible."
        "\nThis is a hybrid role in Barueri, São Paulo, Brazil."
    )
    raw = payload(record)
    units = source_units(replace(record))
    raw["hiring_eligibility"].update(
        value="open_to_brazil",
        evidence=[next(unit["unit_id"] for unit in units if "Candidates located" in unit["text"])],
    )
    raw["work_mode"].update(
        value="office_sao_paulo",
        office_location="Barueri",
        evidence=[next(unit["unit_id"] for unit in units if "Barueri" in unit["text"])],
    )
    result = label(service(config, streams, [raw, raw]), record, use_ai=True)
    assert result.gate_outcome == "unknown"
    assert result.tags["work_mode"]["value"] == "unspecified"
    assert "tag:work_mode:location_mismatch" in result.validation_warnings


def test_model_agreement_cannot_confirm_an_unrelated_geography_quote(config, streams, record):
    raw = payload(record)
    raw["hiring_eligibility"].update(
        value="restricted_elsewhere", evidence=raw["working_language"]["evidence"]
    )
    result = label(service(config, streams, [raw, raw]), record, use_ai=True)
    assert result.gate_outcome == "unknown" and result.gate_reasons == []
    assert "tag:hiring_eligibility:unsupported_evidence" in result.validation_warnings


def test_proposed_p2_rule_is_not_converted_to_a_machine_candidate(
    config, streams, record, tmp_path
):
    path = tmp_path / "rubric.yaml"
    data = yaml.safe_load(Path(config.rubric_path).read_text())
    data["p2"]["status"] = "proposed"
    path.write_text(yaml.safe_dump(data))
    raw = payload(record)
    raw["function_core"]["value"] = "analytics_reporting"
    raw["relevance"] = 1
    result = label(
        service(replace(config, rubric_path=str(path)), streams, [raw]), record, use_ai=True
    )
    assert result.relevance == 1 and result.status == "labeled"
    assert result.p2_candidate is None


def test_proposed_root_changes_effective_hashes_and_never_replays_confirmed_decisions(
    config,
    streams,
    record,
    tmp_path,
):
    path = tmp_path / "rubric.yaml"
    data = yaml.safe_load(Path(config.rubric_path).read_text())
    path.write_text(yaml.safe_dump(data))
    config = replace(config, rubric_path=str(path))
    tagger = service(config, streams, [payload(record)])
    first = label(tagger, record, use_ai=True)
    confirmed = load_rubric(path)
    assert (
        confirmed.prompt_rubric_hash
        == "8970cd265579fbc293ccc0ed3e78f09990cf37f6bfea17bc5d4168bbb7d395d8"
    )
    assert (
        confirmed.aggregation_hash
        == "9bd2773daa962e8bb8879b5a49594b6de2d86ce7d7b27888f98450edd648899f"
    )
    data["status"] = "proposed"
    path.write_text(yaml.safe_dump(data))
    proposed = load_rubric(path)
    assert proposed.prompt_rubric_hash != confirmed.prompt_rubric_hash
    assert proposed.aggregation_hash != confirmed.aggregation_hash
    assert proposed.aggregation is None and proposed.p2_rule is None
    assert proposed.domain_preferences is None and proposed.strong_company_names == ()
    changed_draft = copy.deepcopy(data)
    changed_draft["anchors"]["3"] = "UNCONFIRMED_DRAFT"
    changed_draft["aggregation"]["other_role_max"] = 0
    changed_draft["domain_preferences"]["liked_industries"] = []
    assert parse_rubric(changed_draft).prompt_rubric_hash == proposed.prompt_rubric_hash
    assert parse_rubric(changed_draft).aggregation_hash == proposed.aggregation_hash
    for use_ai in (False, True):
        result = label(tagger, record, use_ai=use_ai)
        assert result.status == "rules_only" and result.route == "rubric_unconfirmed"
        assert result.relevance is result.llm_relevance is result.rules_relevance is None
        assert result.p2_candidate is None and result.cache_key is None
    assert first.status == "labeled" and tagger.client.calls == ["tag"]


@pytest.mark.parametrize("interrupt", [KeyboardInterrupt, SystemExit])
def test_interrupted_native_attempt_remains_an_uncertain_ledger_bill(
    config,
    streams,
    record,
    tmp_path,
    monkeypatch,
    interrupt,
):
    monkeypatch.setenv(config.api_key_env, "fake-test-key")
    prices = {"models": {config.tag_model: {"input_per_million": 1, "output_per_million": 1}}}
    ledger = LedgerWriter(tmp_path / "interrupt.jsonl")
    budget = BudgetGuard(1, prices)
    tagger = VacancyTagger(config, streams, ledger=ledger, budget=budget)

    def interrupted(*args):
        raise interrupt()

    monkeypatch.setattr(tagger.client.tag, "_gemini", interrupted)
    with pytest.raises(interrupt):
        label(tagger, record, use_ai=True)
    ledger.close()
    rows = [json.loads(line) for line in ledger.path.read_text().splitlines()]
    assert len(rows) == 1 and rows[0]["usage"] == {} and rows[0]["status"] == "failed"
    assert budget.spent > 0


def test_ledger_close_retains_pending_attempt_once_as_unknown_bill(tmp_path):
    ledger = LedgerWriter(tmp_path / "pending.jsonl")
    ledger({"event": "request_start", "stage": "tag", "model": "fake", "attempt": 1})
    ledger.close()
    ledger.close()
    rows = [json.loads(line) for line in ledger.path.read_text().splitlines()]
    assert len(rows) == 1 and rows[0]["status"] == "interrupted" and rows[0]["usage"] == {}
    assert "_started" not in rows[0]
    prices = {
        "schema": "eval-prices-v1",
        "currency": "USD",
        "as_of": "2026-10-20",
        "models": {"fake": {"input_per_million": 1, "output_per_million": 1}},
    }
    measured = ledger_metrics(
        rows,
        prices=prices,
        maximum_price_age_days=0,
        measured_at=datetime(2026, 10, 20, tzinfo=UTC),
    )
    assert measured["attempts"] == measured["uncertain_bills"] == 1
    assert measured["complete"] is False


def test_rule_reasons_do_not_fabricate_geography_from_company_or_role_blocks(record):
    item = RuleEngine(
        FilterRules.from_mapping(
            {
                "company_blacklist": [record.company],
                "negative_title_signals": ["analytics"],
            }
        )
    ).apply(record)
    assert item.evaluation.eligibility_status == "ineligible"
    assert rule_gate_reasons(item.evaluation) == []
    evaluation = JobEvaluation(
        FilterDecision.REJECT,
        eligibility_status="ineligible",
        blocker_signals=["title_blocker:Engineer", "status_phrase:closed"],
    )
    assert rule_gate_reasons(evaluation) == ["vacancy_closed"]
