from __future__ import annotations

import copy
import json
from dataclasses import replace
from pathlib import Path

import pytest

from job_intake.annotation import prefilter
from job_intake.annotation.prefilter import VacancyPrefilter
from job_intake.annotation.text import source_units
from job_intake.config.settings import PrefilterConfig
from job_intake.models.job import JobRecord


@pytest.fixture
def config(tmp_path, monkeypatch):
    monkeypatch.setenv("PREFILTER_TEST_KEY", "test-only-prefilter-key")
    return PrefilterConfig(
        enabled=True, api_key_env="PREFILTER_TEST_KEY", cache_dir=str(tmp_path / "cache"),
    )


@pytest.fixture
def record():
    return JobRecord(
        source="test", source_job_id="sales-1", company="Example", title="Sales Manager",
        original_url="https://example.test/jobs/1",
        description_clean=(
            "Responsibilities\nYou will prospect and sell subscriptions to new customers."
        ),
        source_metadata={"description_complete": True},
    )


def result(record, *, decision="clearly_irrelevant", code="outside_core_role", field=None):
    units = source_units(replace(record))
    unit = next(unit for unit in units if (
        unit["field"] == field if field else "You will" in unit["text"]
    ))
    return {
        "decision": decision,
        "reason_code": code if decision == "clearly_irrelevant" else "no_clear_mismatch",
        "reason": "Primary duties are outbound selling." if decision == "clearly_irrelevant"
        else "Possible project scope needs full review.",
        "evidence": [{"unit_id": unit["unit_id"], "source_snippet": unit["text"].strip()}],
    }


class FakeClient:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []
        self.progress = None

    def set_progress(self, progress):
        self.progress = progress

    def generate_stage(self, stage, model, prompt):
        self.calls.append((stage, model, prompt))
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response, {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150}


def install(config, responses, progress=None):
    service = VacancyPrefilter(config, progress=progress)
    service.client = FakeClient(responses)
    service.set_progress(progress)
    return service


def test_two_models_must_confirm_same_positive_mismatch(config, record):
    payload = result(record)
    service = install(config, [payload, payload])
    original = copy.deepcopy(record)
    routing = service.route(record)
    assert routing["routing"] == "suppressed"
    assert routing["decision"] == "clearly_irrelevant"
    assert routing["reason_code"] == "outside_core_role"
    assert [row[0] for row in service.client.calls] == ["screen", "verify"]
    assert service.stats == {"screen_calls": 1, "verify_calls": 1, "cache_hits": 0, "errors": 0}
    assert record == original
    assert record._text_units_cache is None
    assert routing["evidence"] == payload["evidence"]
    assert set(routing["stages"]) == {"screen", "verify"}


@pytest.mark.parametrize("id_only_stages", [(True, True), (True, False), (False, True)])
def test_id_only_evidence_is_hydrated_without_model_written_quotes(
    config, record, id_only_stages,
):
    exact = result(record)
    responses = []
    for id_only in id_only_stages:
        payload = copy.deepcopy(exact)
        if id_only:
            payload["evidence"] = [{"unit_id": row["unit_id"]} for row in payload["evidence"]]
        responses.append(payload)
    service = install(config, responses)
    routing = service.route(record)
    assert routing["routing"] == "suppressed"
    assert routing["errors"] == []
    assert routing["evidence"] == exact["evidence"]
    for stage in ("screen", "verify"):
        assert routing["stages"][stage]["result"]["evidence"] == exact["evidence"]
    for _stage, _model, prompt in service.client.calls:
        assert prefilter.PREFILTER_EVIDENCE_FORMAT in prompt
        assert 'evidence: [{"unit_id": "unchanged source unit ID"}]' in prompt
        assert "do not return source_snippet" in prompt


@pytest.mark.parametrize("change,code", [
    (lambda row: row.update(unit_id="description_clean:999999"), "quote_unattributable"),
    (lambda row: row.update(unit_id="title:000001"), "not_duty"),
    (lambda row: row.update(unit_id=""), "invalid_evidence"),
    (lambda row: row.update(extra="untrusted text"), "invalid_evidence"),
])
def test_id_only_evidence_keeps_source_and_duty_guards(config, record, change, code):
    payload = result(record)
    payload["evidence"] = [{"unit_id": payload["evidence"][0]["unit_id"]}]
    change(payload["evidence"][0])
    routing = install(config, [payload]).route(record)
    assert routing["routing"] == "full_review"
    assert routing["errors"][0]["code"] == code
    assert routing["stages"] == {}


def test_id_only_hydration_keeps_whitespace_and_entire_qualified_unit(config, record):
    unit = next(unit for unit in source_units(replace(record)) if "You will" in unit["text"])
    unit = {**unit, "text": " \nYou will sell subscriptions only when authorised. \n"}
    payload = {**result(record), "evidence": [{"unit_id": unit["unit_id"]}]}
    checked = prefilter._validated(payload, [unit])
    assert checked["evidence"] == [{"unit_id": unit["unit_id"], "source_snippet": unit["text"]}]


def test_id_only_transport_preserves_paid_stage_policy_fingerprints(config, record):
    service = install(config, [])
    content_hash = prefilter.source_hash(record)
    spec = prefilter.asdict(config.resolved_stage("screen"))
    old_stage_key = prefilter._hash({
        "version": prefilter.PREFILTER_VERSION, "focus": prefilter.FOCUS_VERSION,
        "source_hash": content_hash, "stage": "screen", "spec": spec,
        "prompt": prefilter._CACHE_PROMPTS["screen"], "proposed_reason_code": None,
        "max_source_chars": config.max_source_chars,
    })
    assert service._stage_key("screen", content_hash, None) == old_stage_key
    assert prefilter.prompts_version() == prefilter._hash([
        prefilter._CACHE_PROMPTS["screen"], prefilter._CACHE_PROMPTS["verify"],
        prefilter.FOCUS_VERSION, prefilter.ROUTING_POLICY_VERSION,
    ])
    assert prefilter._CACHE_PROMPTS["screen"] != prefilter.SCREEN_PROMPT
    payload = result(record)
    record.annotation["prefilter"] = install(config, [payload, payload]).route(record)
    for path in Path(config.cache_dir).rglob("*.json"):
        path.unlink()
    assert service.cached_route(record)["routing"] == "suppressed"
    assert service.client.calls == []


@pytest.fixture
def mixed_role(record):
    record.title = "Product Lead, Infrastructure"
    record.description_clean = (
        "Responsibilities\nYou will build compute and networking primitives.\n"
        "You own the problem statement and requirements behind every platform bet.\n"
        "You define how each launch is measured before it ships.\n"
        "The metric, the threshold and the read afterwards are yours: whether a launch "
        "moved enterprise deal velocity, project economics, or platform reliability.\n"
        "You keep engineering, design and leadership aligned."
    )
    return record


def test_mixed_owned_decision_analytics_counters_two_valid_negatives(config, mixed_role):
    payload = result(mixed_role)
    original = copy.deepcopy(mixed_role)
    service = install(config, [payload, payload])
    routed = service.route(mixed_role)
    assert routed["routing"] == "full_review"
    assert routed["decision"] == "uncertain"
    assert routed["reason_code"] == "no_clear_mismatch"
    assert routed["routing_guard"] == "mixed_decision_analytics_scope"
    assert routed["routing_policy_version"] == prefilter.ROUTING_POLICY_VERSION
    assert routed["evidence"] == routed["errors"] == []
    assert service.stats["errors"] == 0
    assert {row["result"]["decision"] for row in routed["stages"].values()} == {
        "clearly_irrelevant",
    }
    units = source_units(replace(mixed_role))
    by_id = {unit["unit_id"]: unit for unit in units}
    assert len(routed["counterevidence"]) == 4
    assert all(item["source_snippet"] == by_id[item["unit_id"]]["text"]
               for item in routed["counterevidence"])
    assert mixed_role == original
    assert "claims" not in routed


def legacy_route(service, record, saved):
    saved = copy.deepcopy(saved)
    saved["cache_key"] = prefilter._hash({
        "screen": service._stage_key("screen", prefilter.source_hash(record), None),
        "verify": service._stage_key("verify", prefilter.source_hash(record), None),
    })
    for name in ("routing_policy_version", "routing_guard", "counterevidence"):
        saved.pop(name, None)
    service._apply_stages(saved, units=[])
    saved.pop("routing_guard", None)
    saved.pop("counterevidence", None)
    return saved


@pytest.mark.parametrize("mixed", [True, False])
def test_legacy_paid_stages_rebuild_current_route_without_disk_or_api(
    config, record, mixed_role, mixed, monkeypatch,
):
    if not mixed:
        record.description_clean = "Responsibilities\nYou will sell subscriptions."
    payload = result(record)
    original_service = install(config, [payload, payload])
    saved = original_service.route(record)
    record.annotation["prefilter"] = legacy_route(original_service, record, saved)
    for path in Path(config.cache_dir).rglob("*.json"):
        path.unlink()
    monkeypatch.delenv(config.api_key_env)
    service = install(config, [])
    rebuilt = service.route(record)
    assert rebuilt["routing"] == ("full_review" if mixed else "suppressed")
    assert rebuilt["routing_guard"] == ("mixed_decision_analytics_scope" if mixed else None)
    assert rebuilt["routing_policy_version"] == prefilter.ROUTING_POLICY_VERSION
    assert rebuilt["cache_key"] != record.annotation["prefilter"]["cache_key"]
    assert service.client.calls == []
    assert service.stats == {"screen_calls": 0, "verify_calls": 0, "cache_hits": 2, "errors": 0}
    assert all(row["cached"] for row in rebuilt["usage"])


def test_local_routing_revision_changes_top_and_profiles_preserving_paid_stages(
    config, record, monkeypatch,
):
    service = install(config, [])
    content_hash = prefilter.source_hash(record)
    old_key = service._result(content_hash)["cache_key"]
    old_stage_keys = {stage: service._stage_key(stage, content_hash, None)
                      for stage in ("screen", "verify")}
    old_prompt_version = prefilter.prompts_version()
    monkeypatch.setattr(prefilter, "ROUTING_POLICY_VERSION", "a-later-local-guard")
    assert service._result(content_hash)["cache_key"] != old_key
    assert prefilter.prompts_version() != old_prompt_version
    assert {stage: service._stage_key(stage, content_hash, None)
            for stage in ("screen", "verify")} == old_stage_keys


@pytest.mark.parametrize("extra", [
    "You will report adoption and revenue to internal stakeholders.\n"
    "You own partner account relationships and lead co-selling projects with sales teams.",
    "You own latency metrics and system instrumentation.\n"
    "You lead engineering delivery projects and work with engineering teams.",
    "You do not own analytics or measurement programs for business decisions.\n"
    "You manage projects with Finance teams.",
    "Requirements\nYou own analytics measurement programs for business decisions.\n"
    "You manage projects with Finance teams.",
    "Benefits\nYou own analytics measurement programs for business decisions.\n"
    "You manage projects with Finance teams.",
    "You own analytics measurement programs for business decisions.",
    "You manage projects with Finance teams.",
    "Develop and execute joint GTM plans with defined KPIs for adoption and revenue.\n"
    "Track and report on partner performance metrics.\n"
    "You own partner launch projects and work with Product and Engineering teams.",
    "You lead customer acquisition campaigns using analytics and adoption metrics.\n"
    "You own campaign projects and work with Marketing teams.",
])
def test_counterevidence_needs_owned_decision_analytics_projects_and_people(
    config, record, extra,
):
    record.title = "Analytics Product Manager"
    record.description_clean += "\n" + extra
    payload = result(record)
    routed = install(config, [payload, payload]).route(record)
    assert routed["routing"] == "suppressed"
    assert routed["counterevidence"] == []
    assert routed["routing_guard"] is None
    assert routed["errors"] == []


def test_current_guard_counterevidence_is_revalidated_on_saved_route(config, mixed_role):
    payload = result(mixed_role)
    saved = install(config, [payload, payload]).route(mixed_role)
    for path in Path(config.cache_dir).rglob("*.json"):
        path.unlink()
    mixed_role.annotation["prefilter"] = saved
    service = install(config, [])
    assert service.cached_route(mixed_role)["routing"] == "full_review"
    saved["counterevidence"][0]["source_snippet"] = "invented owned analytics mandate"
    assert service.cached_route(mixed_role) is None
    assert service.client.calls == []


@pytest.mark.parametrize("duties", [
    "Defining what success looks like for each integration and measure it honestly: "
    "adoption by the developers who use it, integration quality, and reliability in real use.\n"
    "Talking to them constantly and building the roadmap from that signal.\n"
    "Working hands-on with each Partner's product and engineering counterparts to ship.",
    "Design and maintain partner attribution models and the dashboards leadership uses "
    "to evaluate performance.\n"
    "Contribute to partner segmentation, tiering, and prioritization strategy, bringing data "
    "and operational reality to bear on where the team should invest.\n"
    "Own execution of agreed strategic direction: once a call is made, drive the operational "
    "and cross-functional work to make it real.\n"
    "Serve as the connective tissue between Partnerships, Sales, RevOps, Legal, and Finance.",
])
def test_owned_measurement_and_strategic_operations_mixed_duties_need_full_review(
    config, record, duties,
):
    record.description_clean += "\n" + duties
    payload = result(record)
    routed = install(config, [payload, payload]).route(record)
    assert routed["routing"] == "full_review"
    assert routed["decision"] == "uncertain"
    assert routed["routing_guard"] == "mixed_decision_analytics_scope"
    assert routed["errors"] == []


@pytest.mark.parametrize("decision", ["likely_relevant", "uncertain"])
def test_retained_vacancies_need_only_screening(config, record, decision):
    service = install(config, [result(record, decision=decision)])
    routing = service.route(record)
    assert routing["routing"] == "full_review"
    assert routing["decision"] == decision
    assert len(service.client.calls) == 1
    assert service.stats["verify_calls"] == 0


@pytest.mark.parametrize("decision", ["likely_relevant", "uncertain", "clearly_irrelevant"])
def test_verifier_disagreement_keeps_full_review(config, record, decision):
    payload = result(record, decision=decision, code="prolonged_isolated_analysis")
    service = install(config, [result(record), payload])
    routing = service.route(record)
    assert routing["routing"] == "full_review"
    assert routing["decision"] == "uncertain"
    assert routing["reason_code"] == "no_clear_mismatch"


def test_explicit_prolonged_isolation_can_be_confirmed(config, record):
    record.description_clean = (
        "Responsibilities\nYou will spend all working time on prolonged solo analysis "
        "without interacting with stakeholders or other people."
    )
    payload = result(record, code="prolonged_isolated_analysis")
    routing = install(config, [payload, payload]).route(record)
    assert routing["routing"] == "suppressed"
    assert routing["reason_code"] == "prolonged_isolated_analysis"


@pytest.mark.parametrize("change", [
    {"enabled": False},
    {"max_source_chars": 256},
])
def test_disabled_or_oversized_sources_do_not_call_models(config, record, change):
    config = replace(config, **change)
    if "max_source_chars" in change:
        record.description_clean *= 5
    service = install(config, [])
    routing = service.route(record)
    assert routing["routing"] == "full_review"
    assert routing["decision"] == "uncertain"
    assert service.client.calls == []


@pytest.mark.parametrize("metadata", [
    {"description_complete": False}, {"description_truncated": True}, {"truncated": True},
])
def test_incomplete_source_never_reaches_models(config, record, metadata):
    record.source_metadata = metadata
    service = install(config, [])
    assert service.route(record)["routing"] == "full_review"
    assert service.cached_route(record) is None
    assert service.client.calls == []


def test_empty_source_never_reaches_models(config, record):
    record.description_clean = record.description_raw = ""
    service = install(config, [])
    assert service.route(record)["routing"] == "full_review"
    assert service.client.calls == []


def test_missing_api_key_keeps_full_review_without_request(config, record, monkeypatch):
    monkeypatch.delenv(config.api_key_env)
    service = install(config, [])
    routing = service.route(record)
    assert routing["routing"] == "full_review"
    assert service.client.calls == []
    assert service.stats["screen_calls"] == 0
    assert service.stats["errors"] == 1


@pytest.mark.parametrize("bad", [None, [], {}, {"decision": "clearly_irrelevant"},
                                 "invalid JSON"])
def test_invalid_screening_output_never_suppresses(config, record, bad):
    service = install(config, [bad])
    routing = service.route(record)
    assert routing["routing"] == "full_review"
    assert service.stats["errors"] == 1
    assert service.stats["verify_calls"] == 0
    assert list(Path(config.cache_dir).rglob("*.json")) == []


@pytest.mark.parametrize("change,code", [
    (lambda payload: payload.update(extra="private-key/raw-source"), "invalid_envelope"),
    (lambda payload: payload.update(decision="private-key/raw-source"), "invalid_decision"),
    (lambda payload: payload["evidence"].append("private-key"), "invalid_evidence"),
    (lambda payload: payload["evidence"][0].update(source_snippet="sell subscriptions"),
     "quote_shortened"),
    (lambda payload: payload["evidence"][0].update(source_snippet="private-key/raw-source"),
     "quote_unattributable"),
    (lambda payload: payload["evidence"].clear(), "no_evidence"),
])
def test_validation_diagnostics_and_paid_usage_survive_without_invalid_output(
    config, record, change, code,
):
    payload = result(record)
    change(payload)
    routing = install(config, [payload]).route(record)
    assert routing["routing"] == "full_review"
    assert routing["stages"] == {}
    assert routing["errors"] == [{
        "stage": "screen", "code": code, "error_type": "PrefilterValidationError",
        "usage": {"input_tokens": 100, "output_tokens": 50, "total_tokens": 150},
    }]
    assert routing["usage"] == [{
        "input_tokens": 100, "output_tokens": 50, "total_tokens": 150,
        "stage": "screen", "model": config.screen_model, "provider": "gemini",
        "cached": False, "failed": True,
    }]
    assert "private-key" not in json.dumps(routing)
    assert list(Path(config.cache_dir).rglob("*.json")) == []


def test_non_duty_diagnostic_is_distinct_without_loosening_guard(config, record):
    routing = install(config, [result(record, field="title")]).route(record)
    assert routing["routing"] == "full_review"
    assert routing["errors"][0]["code"] == "not_duty"


def test_failed_validation_does_not_stick_as_a_cached_route(config, record):
    invalid = result(record)
    invalid["evidence"][0]["source_snippet"] = "sell subscriptions"
    record.annotation["prefilter"] = install(config, [invalid]).route(record)
    service = install(config, [result(record, decision="likely_relevant")])
    routing = service.route(record)
    assert len(service.client.calls) == 1
    assert routing["decision"] == "likely_relevant"
    assert routing["errors"] == []


@pytest.mark.parametrize("code", [
    "no_project_management", "no_people_interaction", "english_unknown", "no_direct_reports",
    "no_pnl", "no_causal_inference", "ic_title", "no_clear_mismatch",
])
def test_absent_required_or_preferred_dimensions_cannot_be_rejection_codes(
    config, record, code,
):
    payload = result(record)
    payload["reason_code"] = code
    service = install(config, [payload, payload])
    assert service.route(record)["routing"] == "full_review"
    assert service.stats["verify_calls"] == 0


def test_absence_only_quote_cannot_prove_mismatch(config, record):
    record.description_clean = (
        "Responsibilities\nYou will work on projects; scope is not specified."
    )
    payload = result(record)
    service = install(config, [payload, payload])
    assert service.route(record)["routing"] == "full_review"
    assert service.stats["verify_calls"] == 0


@pytest.mark.parametrize("field,description", [
    ("title", None),
    ("company", None),
    ("description_clean", "About us\nYou will see our sales team selling subscriptions."),
    ("description_clean", "Requirements\nYou will need prior experience selling subscriptions."),
    ("description_clean", "Benefits\nFree subscriptions."),
    ("description_clean", "Benefits\nYou will manage your insurance preferences."),
    ("description_clean", "How to apply\nYou will write a cover letter for our recruiter."),
])
def test_titles_company_requirements_and_benefits_do_not_prove_duties(
    config, record, field, description,
):
    if description:
        record.description_clean = description
        unit = source_units(replace(record))[-1]
        payload = result(record, field="title")
        payload["evidence"] = [{"unit_id": unit["unit_id"], "source_snippet": unit["text"]}]
    else:
        payload = result(record, field=field)
    service = install(config, [payload, payload])
    assert service.route(record)["routing"] == "full_review"
    assert service.stats["verify_calls"] == 0


@pytest.mark.parametrize("quote", ["sell subscriptions", "invented sales duty", ""])
def test_evidence_must_be_an_entire_verbatim_unit(config, record, quote):
    payload = result(record)
    payload["evidence"][0]["source_snippet"] = quote
    service = install(config, [payload, payload])
    assert service.route(record)["routing"] == "full_review"
    assert service.stats["verify_calls"] == 0


@pytest.mark.parametrize("change", [
    lambda payload: payload["evidence"].clear(),
    lambda payload: payload["evidence"][0].update(unit_id="description_clean:999999"),
    lambda payload: payload.update(extra="private source instructions"),
])
def test_unattributable_or_invalid_evidence_never_suppresses(config, record, change):
    payload = result(record)
    change(payload)
    service = install(config, [payload, payload])
    assert service.route(record)["routing"] == "full_review"
    assert service.stats["errors"] == 1


def test_both_prompts_preserve_entire_source_units_without_duplicated_text(config, record):
    record.description_clean += "\nYou will also lead analytics projects with Finance stakeholders."
    record.source_metadata["working_language"] = "English"
    record.source_metadata["raw_response"] = {"large_html": "PRIVATE UNNEEDED HTML" * 5000}
    payload = result(record)
    service = install(config, [payload, payload])
    service.route(record)
    for stage, _model, prompt in service.client.calls:
        prefix = prefilter.SCREEN_PROMPT if stage == "screen" else prefilter.VERIFY_PROMPT
        source = json.loads(prompt.removeprefix(prefix))
        assert "text" not in source
        assert "unit_ids" not in source
        assert source["units"] == source_units(replace(record))
        assert source["source_metadata"] == {"description_complete": True}
        assert "PRIVATE UNNEEDED HTML" not in prompt
        assert any(unit["field"] == "source_metadata.working_language" for unit in source["units"])
        if stage == "verify":
            assert source["proposed_reason_code"] == "outside_core_role"
            assert "evidence" not in source
        assert "direct reports are optional" in prompt
        assert "never grounds for suppression" in prompt


def test_both_prompts_share_validator_duty_boundaries(config, record):
    record.description_clean = (
        "About us\nOur sales platform serves customers globally.\n"
        "Responsibilities\nYou will prospect and sell subscriptions to new customers.\n"
        "Requirements\nYou will need experience managing sales projects.\n"
        "Benefits\nYou will manage your insurance preferences."
    )
    payload = result(record)
    service = install(config, [payload, payload])
    routing = service.route(record)
    assert routing["routing"] == "suppressed"
    assert service.stats["errors"] == 0
    units = source_units(replace(record))
    allowed = prefilter._duty_unit_ids(units)
    assert payload["evidence"][0]["unit_id"] in allowed
    for stage, _model, prompt in service.client.calls:
        prefix = prefilter.SCREEN_PROMPT if stage == "screen" else prefilter.VERIFY_PROMPT
        source = json.loads(prompt.removeprefix(prefix))
        assert source["duty_unit_ids"] == [
            unit["unit_id"] for unit in units if unit["unit_id"] in allowed
        ]
        assert all(unit["field"].startswith("description_") for unit in units
                   if unit["unit_id"] in allowed)
        assert all(unit["section"] == "role" for unit in units
                   if unit["unit_id"] in allowed)
        assert source["units"] == units
        assert "only those unit IDs may be selected" in prompt
        assert "not proof of a" in prompt


def test_absence_only_scope_is_not_offered_as_duty_evidence(config, record):
    record.description_clean = (
        "Responsibilities\nYou will work on projects; scope is not specified."
    )
    payload = result(record, decision="uncertain")
    service = install(config, [payload])
    assert service.route(record)["routing"] == "full_review"
    source = json.loads(service.client.calls[0][2].removeprefix(prefilter.SCREEN_PROMPT))
    assert payload["evidence"][0]["unit_id"] not in source["duty_unit_ids"]
    assert service.stats["errors"] == 0


@pytest.mark.parametrize("heading", ["What We Offer", "Benefits", "How to Apply"])
def test_flattened_html_boilerplate_cannot_supply_duty_quotes(config, record, heading):
    record.description_raw = (
        f"<h3><strong>{heading}</strong></h3>"
        "<p>You will manage your insurance preferences.</p>"
        "<p>We provide health insurance wherever you are.</p>"
    )
    record.description_clean = (
        f"{heading.upper()} You will manage your insurance preferences. "
        "We provide health insurance wherever you are."
    )
    units = source_units(replace(record))
    duty_ids = prefilter._duty_unit_ids(units)
    assert not duty_ids
    payload = result(record, field="title")
    unit = next(unit for unit in units if "manage your insurance" in unit["text"])
    payload["evidence"] = [{"unit_id": unit["unit_id"], "source_snippet": unit["text"]}]
    routing = install(config, [payload]).route(record)
    assert routing["routing"] == "full_review"
    assert routing["errors"][0]["code"] == "not_duty"


def test_empty_source_fragments_are_not_offered_as_duty_quotes(config, record):
    units = [{"unit_id": "description_clean:000001", "field": "description_clean",
              "section": "role", "text": " \n "}]
    assert not prefilter._duty_unit_ids(units)


def test_uppercase_flattened_duties_are_separated_from_requirements_and_benefits(
    config, record,
):
    record.description_raw = (
        "<h3><strong>About the Role</strong></h3>"
        "<p>You’ll maintain our Postgres foundation.</p>"
        "<h3><strong>What you'll own:</strong></h3>"
        "<ul><li>Build and maintain Postgres extensions in C and Rust</li></ul>"
        "<h3><strong>What you bring:</strong></h3>"
        "<ul><li>Deep expertise in Postgres internals</li></ul>"
        "<h3><strong>What We Offer</strong></h3>"
        "<p>You will manage your insurance preferences.</p>"
    )
    record.description_clean = (
        "ABOUT THE ROLE You’ll maintain our Postgres foundation. "
        "WHAT YOU'LL OWN: - Build and maintain Postgres extensions in C and Rust "
        "WHAT YOU BRING: - Deep expertise in Postgres internals "
        "WHAT WE OFFER You will manage your insurance preferences."
    )
    units = source_units(replace(record))
    duty = next(unit for unit in units if "Build and maintain" in unit["text"])
    assert "expertise" not in duty["text"]
    assert "insurance" not in duty["text"]
    assert duty["section"] == "role"
    assert "".join(unit["text"] for unit in units
                   if unit["field"] == "description_clean") == record.description_clean
    payload = {
        "decision": "clearly_irrelevant", "reason_code": "outside_core_role",
        "reason": "Primary duties maintain database extensions.",
        "evidence": [{"unit_id": duty["unit_id"], "source_snippet": duty["text"]}],
    }
    routing = install(config, [payload, payload]).route(record)
    assert routing["routing"] == "suppressed"
    assert routing["errors"] == []
    allowed = prefilter._duty_unit_ids(units)
    assert duty["unit_id"] in allowed
    assert not any(unit["unit_id"] in allowed for unit in units
                   if "expertise" in unit["text"] or "insurance" in unit["text"])


def test_unrecognized_qualification_heading_excludes_following_role_marked_units(
    config, record,
):
    record.description_raw = (
        "<h3>About the Role</h3><p>You will build authentication services.</p>"
        "<h3>You might be a good fit if you</h3>"
        "<p>Have experience building authentication systems.</p>"
        "<p>Have experience managing database migrations.</p>"
        "<h3>Responsibilities</h3><p>You will implement secure authentication features.</p>"
    )
    record.description_clean = (
        "ABOUT THE ROLE You will build authentication services. "
        "YOU MIGHT BE A GOOD FIT IF YOU Have experience building authentication systems. "
        "Have experience managing database migrations. "
        "RESPONSIBILITIES You will implement secure authentication features."
    )
    units = source_units(replace(record))
    allowed = prefilter._duty_unit_ids(units)
    qualifications = [unit for unit in units if "Have experience" in unit["text"]]
    assert len(qualifications) == 2
    assert all(unit["section"] == "role" for unit in qualifications)
    assert all(unit["unit_id"] not in allowed for unit in qualifications)
    duties = next(unit for unit in units if "implement secure" in unit["text"])
    assert duties["unit_id"] in allowed
    payload = {
        "decision": "clearly_irrelevant", "reason_code": "outside_core_role",
        "reason": "Primary duties build authentication systems.",
        "evidence": [{"unit_id": duties["unit_id"]}],
    }
    saved = install(config, [payload, payload]).route(record)
    assert saved["routing"] == "suppressed"
    for path in Path(config.cache_dir).rglob("*.json"):
        path.unlink()
    # A legacy persisted route that used candidate qualifications must fail
    # today's stricter enforcement, despite matching source and cache hashes.
    bad_quote = {"unit_id": qualifications[1]["unit_id"],
                 "source_snippet": qualifications[1]["text"]}
    saved["evidence"] = [bad_quote]
    for stage in ("screen", "verify"):
        saved["stages"][stage]["result"]["evidence"] = [bad_quote]
    record.annotation["prefilter"] = saved
    assert install(config, []).cached_route(record) is None
    invalid = {**payload, "evidence": [{"unit_id": qualifications[1]["unit_id"]}]}
    routing = install(config, [invalid]).route(record)
    assert routing["routing"] == "full_review"
    assert routing["errors"][0]["code"] == "not_duty"


@pytest.mark.parametrize("heading", [
    "YOU MIGHT BE A GOOD FIT IF YOU", "PREFERRED EXPERIENCE", "Required Qualifications",
])
def test_embedded_qualification_heading_excludes_whole_unit_and_following_lines(heading):
    texts = [
        "You will build secure services.",
        f"Lead blameless post-incident reviews {heading} Have 5+ years of engineering.",
        "Have experience building analytics systems for business decisions.",
        "Have experience managing projects and working with Finance teams.",
        "Responsibilities: You will lead delivery projects with engineers.",
    ]
    units = [{"unit_id": f"description_clean:{index:06d}", "field": "description_clean",
              "section": "role", "text": text} for index, text in enumerate(texts, start=1)]
    allowed = prefilter._duty_unit_ids(units)
    assert allowed == {units[0]["unit_id"], units[-1]["unit_id"]}
    assert prefilter._mixed_duty_counterevidence(units) == []
    for unit in units[1:-1]:
        negative = {
            "decision": "clearly_irrelevant", "reason_code": "outside_core_role",
            "reason": "Engineering is primary.", "evidence": [{"unit_id": unit["unit_id"]}],
        }
        with pytest.raises(prefilter.PrefilterValidationError) as error:
            prefilter._validated(negative, units)
        assert error.value.code == "not_duty"


def test_qualification_scope_resets_on_new_description_field():
    units = [
        {"unit_id": "description_clean:000001", "field": "description_clean",
         "section": "role", "text": "PREFERRED EXPERIENCE: database engineering."},
        {"unit_id": "description_raw:000001", "field": "description_raw",
         "section": "role", "text": "You will build secure services."},
    ]
    assert prefilter._duty_unit_ids(units) == {"description_raw:000001"}


def test_real_duty_heading_establishes_other_section_scope_until_qualifications():
    texts = [
        "WHAT YOU'LL BE RESPONSIBLE FOR - Owning partner integration success.",
        "Talking to developers constantly and building the roadmap from that signal.",
        "Working hands-on with each Partner's product and engineering counterparts to ship.",
        "Defining how to measure each integration honestly: adoption and reliability.",
        "YOU MIGHT BE A GOOD FIT IF YOU - Have shipped complex systems.",
        "Have experience owning business analytics and managing projects with Finance.",
        "WHAT YOU'LL OWN - Design and deliver integration projects.",
        "WHAT WE OFFER - We provide health insurance wherever you are.",
        "You will manage your insurance preferences.",
    ]
    units = [{"unit_id": f"description_clean:{index:06d}", "field": "description_clean",
              "section": "other", "text": text} for index, text in enumerate(texts, start=1)]
    allowed = prefilter._duty_unit_ids(units)
    assert allowed == {units[index]["unit_id"] for index in (0, 1, 2, 3, 6)}
    assert prefilter._mixed_duty_counterevidence(units)


def test_legal_fellow_payrange_cannot_be_actual_duty_evidence():
    # Actual Legal Fellow (Spring 2027), Wikimedia source unit 36. Its
    # "this position" cue used to admit the compensation paragraph as a duty.
    unit = {
        "unit_id": "description_clean:000036", "field": "description_clean",
        "section": "other",
        "text": " The anticipated annual pay range of this position for applicants based "
                "within the United States is US$37.42 /hourly to US$45.6 /hourly with "
                "multiple individualized factors, including cost of living in the location, "
                "being the determinants of the offered pay.",
    }
    negative = {
        "decision": "clearly_irrelevant", "reason_code": "outside_core_role",
        "reason": "Legal work is primary.",
        "evidence": [{"unit_id": unit["unit_id"], "source_snippet": unit["text"]}],
    }
    assert unit["unit_id"] not in prefilter._duty_unit_ids([unit])
    with pytest.raises(prefilter.PrefilterValidationError) as error:
        prefilter._validated(negative, [unit])
    assert error.value.code == "not_duty"


@pytest.mark.parametrize("text", [
    "The anticipated annual pay range of this position is US$37.42 to US$45.6 hourly.",
    "Salaries at the Wikimedia Foundation are set in a way that is competitive, equitable, "
    "and consistent with our values and culture.",
    "For applicants located outside of the US, the pay range will be adjusted to the "
    "country of hire.",
    "We neither ask for nor take into consideration the salary history of applicants.",
    "The compensation for a successful applicant will be based on their skills, "
    "experience and location.",
    "We provide health insurance and paid time off for this position.",
    "You are provided with health insurance and paid time off.",
    "This position is eligible for health insurance and paid time off.",
    "As an equal opportunity employer, the Wikimedia Foundation values having a diverse "
    "workforce and continuously strives to maintain an inclusive and equitable workplace.",
    "We do not discriminate against any person based upon their religion or age.",
    "All applicants can reach out to their recruiter to understand more about the "
    "specific pay range for their location during the interview process.",
    "If you are a qualified applicant requiring assistance or an accommodation to complete "
    "any step of the application process due to a disability, you may contact us.",
    "By submitting a job application, you confirm that you have read and agree to "
    "our Candidate Privacy Notice.",
    "More information Applicant Privacy Policy Wikimedia Foundation.",
])
def test_pure_offer_and_application_footer_are_excluded_even_in_inherited_role_scope(text):
    units = [
        {"unit_id": "description_clean:000001", "field": "description_clean",
         "section": "role", "text": "Responsibilities: You will lead delivery projects."},
        {"unit_id": "description_clean:000002", "field": "description_clean",
         "section": "role", "text": text},
        {"unit_id": "description_clean:000003", "field": "description_clean",
         "section": "role", "text": "You will manage projects with Finance teams."},
    ]
    assert prefilter._duty_unit_ids(units) == {
        "description_clean:000001", "description_clean:000003",
    }


@pytest.mark.parametrize("text", [
    "Lead compensation analytics projects and model salary budgets with Finance.",
    "You will own compensation analysis and workforce planning with business leadership.",
    "Own salary benchmarking and advise leaders on compensation strategy.",
    "Compensation Analytics: Lead analysis of pay equity and advise Finance on salary budgets.",
    "Compensation: Provide budget analysis and salary modelling with Finance stakeholders.",
    "Benefits: Lead benefits analytics projects with HR and manage delivery with stakeholders.",
    "The expected annual pay range is US$100,000 to US$150,000; you will lead compensation "
    "analytics projects and own salary budget forecasts with Finance.",
])
def test_compensation_analytics_and_mixed_offer_duties_keep_entire_exact_evidence(text):
    unit = {"unit_id": "description_clean:000001", "field": "description_clean",
            "section": "role", "text": text}
    assert prefilter._duty_unit_ids([unit]) == {unit["unit_id"]}
    negative = {
        "decision": "clearly_irrelevant", "reason_code": "outside_core_role",
        "reason": "Independent models must still assess the profession.",
        "evidence": [{"unit_id": unit["unit_id"]}],
    }
    checked = prefilter._validated(negative, [unit])
    assert checked["evidence"] == [{"unit_id": unit["unit_id"], "source_snippet": text}]


def test_analytical_compensation_heading_does_not_exclude_following_real_duties():
    units = [
        {"unit_id": "description_clean:000001", "field": "description_clean",
         "section": "other", "text": "Compensation Analytics"},
        {"unit_id": "description_clean:000002", "field": "description_clean",
         "section": "other", "text": "Lead salary analysis and budget forecasting with Finance."},
    ]
    assert "description_clean:000002" in prefilter._duty_unit_ids(units)


def test_application_instructions_are_not_real_duties_inside_a_footer_heading():
    units = [
        {"unit_id": "description_clean:000001", "field": "description_clean",
         "section": "other", "text": "How to apply: Provide your CV and cover letter."},
        {"unit_id": "description_clean:000002", "field": "description_clean",
         "section": "other", "text": "You will manage your application with a recruiter."},
    ]
    assert prefilter._duty_unit_ids(units) == set()


def test_narrow_duty_enforcement_keeps_policy_and_paid_stage_fingerprints(config, record):
    helper = install(config, [])
    content_hash = prefilter.source_hash(record)
    assert prefilter.ROUTING_POLICY_VERSION == "mixed-duty-counterevidence-v1"
    old_stage_key = prefilter._hash({
        "version": prefilter.PREFILTER_VERSION, "focus": prefilter.FOCUS_VERSION,
        "source_hash": content_hash, "stage": "screen",
        "spec": prefilter.asdict(config.resolved_stage("screen")),
        "prompt": prefilter._CACHE_PROMPTS["screen"], "proposed_reason_code": None,
        "max_source_chars": config.max_source_chars,
    })
    assert helper._stage_key("screen", content_hash, None) == old_stage_key


def test_unchanged_completed_cache_avoids_all_new_calls(config, record, monkeypatch):
    payload = result(record)
    first = install(config, [payload, payload]).route(record)
    monkeypatch.delenv(config.api_key_env)
    service = install(config, [])
    routing = service.route(record)
    assert routing["routing"] == first["routing"] == "suppressed"
    assert service.client.calls == []
    assert service.stats["cache_hits"] == 2
    assert all(row["cached"] for row in routing["usage"])


def test_saved_current_routing_remains_valid_after_disk_cache_removed(config, record):
    payload = result(record)
    record.annotation["prefilter"] = install(config, [payload, payload]).route(record)
    for file in Path(config.cache_dir).rglob("*.json"):
        file.unlink()
    service = install(config, [])
    assert service.cached_route(record)["routing"] == "suppressed"
    assert service.client.calls == []
    assert service.stats == {"screen_calls": 0, "verify_calls": 0, "cache_hits": 0, "errors": 0}


@pytest.mark.parametrize("change", [
    lambda row: row.update(routing="suppressed", decision="uncertain"),
    lambda row: row.update(cache_key="stale"),
    lambda row: row.update(source_hash="wrong-source"),
    lambda row: row.update(version="obsolete"),
    lambda row: row.update(stages={}),
    lambda row: row["stages"]["verify"]["result"]["evidence"].clear(),
])
def test_saved_routing_is_revalidated_and_never_trusted_alone(config, record, change):
    payload = result(record)
    saved = install(config, [payload, payload]).route(record)
    for file in Path(config.cache_dir).rglob("*.json"):
        file.unlink()
    saved["routing"] = "full_review"  # Deliberate top-level inconsistency is invalid.
    change(saved)
    record.annotation["prefilter"] = saved
    service = install(config, [])
    assert service.cached_route(record) is None


def test_invalid_disk_cache_never_suppresses_offline(config, record):
    payload = result(record)
    install(config, [payload, payload]).route(record)
    verify_file = next(Path(config.cache_dir).rglob("verify/*.json"))
    verify_file.write_text("not JSON", encoding="utf-8")
    service = install(config, [])
    assert service.cached_route(record) is None
    assert service.stats["cache_hits"] == 0


def test_failed_verifier_retries_only_verification(config, record):
    payload = result(record)
    first = install(config, [payload, RuntimeError("secret full source and API key")])
    partial = first.route(record)
    assert partial["routing"] == "full_review"
    assert "secret" not in json.dumps(partial)
    record.annotation["prefilter"] = partial
    second = install(config, [payload])
    assert second.cached_route(record) is None
    assert second.route(record)["routing"] == "suppressed"
    assert [row[0] for row in second.client.calls] == ["verify"]
    assert second.stats["cache_hits"] == 1


def test_invalid_verifier_payload_never_becomes_completed_cache(config, record):
    payload = result(record)
    invalid = copy.deepcopy(payload)
    invalid["evidence"][0]["source_snippet"] = "selling"
    first = install(config, [payload, invalid])
    assert first.route(record)["routing"] == "full_review"
    assert first.cached_route(record) is None
    second = install(config, [payload])
    assert second.route(record)["routing"] == "suppressed"
    assert [row[0] for row in second.client.calls] == ["verify"]


def test_verifier_model_change_reuses_screen_but_invalidates_suppression(config, record):
    payload = result(record)
    record.annotation["prefilter"] = install(config, [payload, payload]).route(record)
    changed = replace(config, verify_model="gemini-3.6-flash")
    second = install(changed, [result(record, decision="uncertain")])
    assert second.cached_route(record) is None
    assert second.route(record)["routing"] == "full_review"
    assert [row[0] for row in second.client.calls] == ["verify"]
    assert second.stats["cache_hits"] == 1


def test_source_change_invalidates_both_stages(config, record):
    payload = result(record)
    record.annotation["prefilter"] = install(config, [payload, payload]).route(record)
    record.description_clean += "\nYou will lead pricing research projects with Finance."
    service = install(config, [result(record, decision="likely_relevant")])
    assert service.cached_route(record) is None
    assert service.route(record)["routing"] == "full_review"
    assert [row[0] for row in service.client.calls] == ["screen"]
    assert service.stats["cache_hits"] == 0


def test_prompt_or_focus_change_invalidates_routing(config, record, monkeypatch):
    payload = result(record)
    record.annotation["prefilter"] = install(config, [payload, payload]).route(record)
    original = prefilter.prompts_version()
    monkeypatch.setattr(prefilter, "FOCUS_VERSION", "career-new-focus")
    service = install(config, [result(record, decision="uncertain")])
    assert prefilter.prompts_version() != original
    assert service.cached_route(record) is None
    assert service.route(record)["routing"] == "full_review"
    assert service.stats["cache_hits"] == 0


def test_error_progress_never_exposes_prompts_bodies_or_exception_messages(config, record):
    events = []
    service = install(config, [RuntimeError("private-key and raw source text")], events.append)
    routing = service.route(record)
    assert routing["routing"] == "full_review"
    assert events == [
        {"event": "prefilter_start"},
        {"event": "prefilter_stage", "stage": "screen", "provider": "gemini",
         "model": config.screen_model},
        {"event": "prefilter_error", "stage": "screen", "error_type": "RuntimeError",
         "code": "unexpected_error"},
        {"event": "prefilter_complete", "routing": "full_review", "decision": "uncertain",
         "reason_code": "no_clear_mismatch"},
    ]
    assert "private-key" not in json.dumps(events)
    assert "raw source" not in json.dumps(events)


def test_screen_and_verifier_use_independent_model_configs(config):
    service = VacancyPrefilter(config)
    assert service.client.screen.config.model == "gemini-3.5-flash-lite"
    assert service.client.screen.config.reasoning_effort == "minimal"
    assert service.client.verify.config.model == "gemini-3.8-flash"
    assert service.client.verify.config.reasoning_effort == "low"
