"""Audit 11 hard-value regressions exercise source relations and final gates."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from test_labels_tagger import config as config
from test_labels_tagger import label, payload, service
from test_labels_tagger import record as record
from test_labels_tagger import streams as streams

from job_intake.annotation.text import source_units
from job_intake.labeling.validate import validate_tags


def response(record):
    template = replace(record)
    template.description_clean += "\nEnglish required."
    raw = payload(template)
    units = source_units(replace(record))
    duty = next(unit["unit_id"] for unit in units if "You will lead" in unit["text"])
    raw["function_core"]["evidence"] = raw["domain_fit"]["evidence"] = [duty]
    raw["working_language"].update(value="unspecified", evidence=[])
    return raw, units


def choose(raw, units, name, text, **values):
    raw[name].update(values, evidence=[next(unit["unit_id"] for unit in units
                                          if text in unit["text"])])


@pytest.mark.parametrize("text", [
    "We hire worldwide with only two hours of overlap with the US team.",
    "We hire globally and work only with US clients.",
    "We hire candidates from North America and South America only.",
    "The US team handles work authorization for our worldwide hires.",
    "Hiring is limited to candidates in Canada and South America.",
    "Hiring is restricted to candidates from the US and LATAM.",
    "We hire globally and our product is limited to US customers.",
    "Our US team only supports candidates hired worldwide.",
    "Hiring is limited to candidates in the US and Location TBD.",
    "We hire worldwide and support clients everywhere except Brazil.",
    "We hire candidates in the US.",
    "We hire in Canada and the United States.",
    "Candidates located in the US are eligible.",
])
def test_geographic_restriction_requires_candidate_scope_and_entire_foreign_region(
    config, streams, record, text,
):
    record.description_clean += "\n" + text
    raw, units = response(record)
    choose(raw, units, "hiring_eligibility", text, value="restricted_elsewhere")
    tagger = service(config, streams, [raw, raw])
    result = label(tagger, record, use_ai=True)
    assert result.tags["hiring_eligibility"]["value"] == "unspecified"
    assert result.gate_outcome == "unknown" and result.gate_reasons == []
    assert "tag:hiring_eligibility:unsupported_evidence" in result.validation_warnings


@pytest.mark.parametrize("text", [
    "We are a worldwide company and hire only candidates in the US.",
    "We hire US candidates for a product launched worldwide.",
    "Hiring is open to candidates in Brazil except Brazil.",
    "We hire candidates in Brazil to work with clients worldwide, except Brazil.",
])
def test_positive_hiring_requires_candidate_scope_not_company_or_product_geography(record, text):
    record.description_clean += "\n" + text
    raw, units = response(record)
    choose(raw, units, "hiring_eligibility", text, value="open_to_brazil")
    checked, warnings = validate_tags(raw, units, ["analytics-leadership"])
    assert checked["tags"]["hiring_eligibility"]["value"] == "unspecified"
    assert "tag:hiring_eligibility:unsupported_evidence" in warnings


def test_uncited_candidate_authorization_cannot_be_hidden_by_positive_brazil_citation(record):
    record.description_clean += (
        "\nCandidates located in Brazil are eligible.\n"
        "This analytics role requires US residency."
    )
    raw, units = response(record)
    choose(raw, units, "hiring_eligibility", "Candidates located", value="open_to_brazil")
    checked, warnings = validate_tags(raw, units, ["analytics-leadership"])
    assert checked["tags"]["hiring_eligibility"]["value"] == "unspecified"
    assert "tag:hiring_eligibility:unsupported_evidence" in warnings


@pytest.mark.parametrize("text,locations,value", [
    ("Candidates located in Brazil are eligible.", "United States", "open_to_brazil"),
    ("Hiring is limited to candidates in the US.", "Brazil", "restricted_elsewhere"),
    ("Hiring is limited to candidates in the US.", ["Canada", "Brazil"], "restricted_elsewhere"),
])
def test_uncited_structured_candidate_locations_cannot_hide_opposing_hiring_fact(
    config, streams, record, text, locations, value,
):
    record.description_clean += "\nThis role is fully remote.\n" + text
    record.source_metadata["applicant_location_requirements"] = locations
    raw, units = response(record)
    choose(raw, units, "hiring_eligibility", text, value=value)
    choose(raw, units, "work_mode", "fully remote", value="remote")
    choose(raw, units, "working_language", "English required", value="english")
    tagger = service(config, streams, [raw, raw])
    result = label(tagger, record, use_ai=True)
    assert result.tags["hiring_eligibility"]["value"] == "unspecified"
    assert result.gate_outcome == "unknown" and result.gate_reasons == []


@pytest.mark.parametrize("locations,value,expected", [
    (["Canada", "Brazil"], "restricted_elsewhere", "unspecified"),
    (["Canada", "Brazil"], "open_to_brazil", "open_to_brazil"),
    (["United States", "Canada"], "restricted_elsewhere", "restricted_elsewhere"),
    (["United States", "Location TBD"], "restricted_elsewhere", "unspecified"),
    (["Brazil", "Location TBD"], "open_to_brazil", "unspecified"),
])
def test_structured_location_list_is_one_complete_allowed_set(record, locations, value, expected):
    record.source_metadata["applicant_location_requirements"] = locations
    raw, units = response(record)
    # The provider is allowed to cite a single source item; it must not turn a
    # subset of an allowed list into a different restriction.
    unit = next(unit for unit in units
                if unit["field"] == "source_metadata.applicant_location_requirements")
    raw["hiring_eligibility"].update(value=value, evidence=[unit["unit_id"]])
    checked, _ = validate_tags(raw, units, ["analytics-leadership"])
    assert checked["tags"]["hiring_eligibility"]["value"] == expected


@pytest.mark.parametrize("text,value", [
    ("This opening is restricted to residents of Namibia.", "restricted_elsewhere"),
    ("Candidates for this role must reside in Namibia.", "restricted_elsewhere"),
    ("Hiring is limited to candidates in the US.", "restricted_elsewhere"),
    ("We hire only candidates in Canada and the United States.", "restricted_elsewhere"),
    ("US work authorization is required.", "restricted_elsewhere"),
    ("U.S. work authorization is required.", "restricted_elsewhere"),
    ("You must be authorized to work in the United States.", "restricted_elsewhere"),
    ("You must be authorized to work in the U.S. to apply for this role.", "restricted_elsewhere"),
    ("Candidates must have US work authorization.", "restricted_elsewhere"),
    ("We do not hire applicants in Brazil.", "restricted_elsewhere"),
    ("Candidates based in Brazil are not eligible.", "restricted_elsewhere"),
    ("No applicants from Brazil.", "restricted_elsewhere"),
    ("We hire worldwide except Brazil.", "restricted_elsewhere"),
    ("Candidates located in Brazil are eligible.", "open_to_brazil"),
    ("We are hiring candidates from Latin America.", "open_to_brazil"),
    ("We hire worldwide.", "open_to_brazil"),
    ("Hiring is limited to candidates in Canada and Brazil.", "open_to_brazil"),
])
def test_known_candidate_scope_remains_supported(record, text, value):
    record.description_clean += "\n" + text
    raw, units = response(record)
    choose(raw, units, "hiring_eligibility", text, value=value)
    checked, warnings = validate_tags(raw, units, ["analytics-leadership"])
    assert checked["tags"]["hiring_eligibility"]["value"] == value
    assert not any(warning.startswith("tag:hiring_eligibility:") for warning in warnings)


@pytest.mark.parametrize("requirement", [
    "We are an international company and this role requires Portuguese only.",
    "Portuguese is the only working language for this role.",
    "Only Portuguese is required for this role.",
    "French is required.",
    "This role requires fluency in German.",
    "Portuguese required.",
    "Portuguese only is required for this role.",
    "English is not used in this role.",
    "English isn't used in this role.",
    "English won't be used in this role.",
    "English will not be used in this role.",
    "We don't communicate in English in this role.",
    "Our working language is Portuguese.",
    "All meetings for this role are conducted in French.",
    "Our working language is Swedish.",
    "Swedish is mandatory for this role.",
    "Our working language is UnrecognizedLanguage.",
    "All meetings for this role are conducted in UnrecognizedLanguage.",
    "This role is conducted in UnrecognizedLanguage.",
    "Portuguese proficiency is required.",
    "Portuguese is a mandatory requirement.",
    "English preferred, Portuguese required.",
    "French preferred but Portuguese is mandatory.",
    "You must communicate in Portuguese with your team.",
    "All daily work is performed in Portuguese.",
    "No English will be used for this role.",
    "We never use English at work.",
    "We only operate in Brazil.",
    "Our business operates exclusively in Brazil.",
    "Our working language is not English.",
    "French and English are required.",
    "You must speak French and English.",
    "Required languages: English and French.",
    "Mandatory French.",
    "You must speak English and French.",
    "Required languages: English and Portuguese.",
    "Portuguese and English are mandatory.",
    "Required languages: English and UnrecognizedLanguage.",
    "Portuguese or French are required.",
    "You must speak English and French with your team.",
    "You must speak French and English fluently.",
    "We need French.",
    "You need to know French.",
    "Candidates must know French.",
    "French fluency is a must.",
    "French capability matters for this role.",
])
@pytest.mark.parametrize("cited", [True, False])
def test_implied_english_cannot_override_an_explicit_language_requirement(
    config, streams, record, requirement, cited,
):
    record.description_clean = (
        "Responsibilities\nYou will lead analytics for business decisions.\n"
        "You will partner with leadership on projects.\nRequirements\n"
        "Candidates located in Brazil are eligible.\nThis role is fully remote.\n"
        "We are an international company.\n" + requirement
    )
    raw, units = response(record)
    choose(raw, units, "hiring_eligibility", "Candidates located", value="open_to_brazil")
    choose(raw, units, "work_mode", "fully remote", value="remote")
    choose(raw, units, "working_language", requirement if cited else "international company.",
           value="english", basis="implied")
    tagger = service(config, streams, [raw, raw])
    result = label(tagger, record, use_ai=True)
    assert result.tags["working_language"]["value"] == "unspecified"
    assert result.gate_outcome == "unknown" and result.gate_reasons == []


@pytest.mark.parametrize("text", [
    "You will work with international customers.",
    "We serve global clients and you will collaborate with them.",
    "Our worldwide product and your role involve analytics.",
])
def test_implied_english_requires_company_international_not_its_customers(record, text):
    record.description_clean = record.description_clean.replace("English required.", text)
    raw, units = response(record)
    choose(raw, units, "working_language", text, value="english", basis="implied")
    checked, warnings = validate_tags(raw, units, ["analytics-leadership"])
    assert checked["tags"]["working_language"]["value"] == "unspecified"
    assert "tag:working_language:unsupported_evidence" in warnings


@pytest.mark.parametrize("text,value", [
    ("This role requires Portuguese customers.", "portuguese_only"),
    ("This role requires French work authorization.", "other_language_required"),
    ("You will serve French-speaking customers.", "other_language_required"),
    ("Our working language is French work authorization.", "other_language_required"),
])
def test_language_named_in_non_language_object_does_not_prove_requirement(record, text, value):
    record.description_clean = record.description_clean.replace("English required.", text)
    raw, units = response(record)
    choose(raw, units, "working_language", text, value=value, basis="explicit")
    checked, warnings = validate_tags(raw, units, ["analytics-leadership"])
    assert checked["tags"]["working_language"]["value"] == "unspecified"
    assert "tag:working_language:unsupported_evidence" in warnings


@pytest.mark.parametrize("company", [
    "We are an international company.",
    "Our company operates globally.",
    "We are a global business.",
])
def test_english_advert_with_international_company_retains_soft_confirmation(record, company):
    record.description_clean = record.description_clean.replace("English required.", company)
    raw, units = response(record)
    choose(raw, units, "working_language", company, value="english", basis="implied")
    checked, warnings = validate_tags(raw, units, ["analytics-leadership"])
    assert checked["tags"]["working_language"]["value"] == "english"
    assert not any(warning.startswith("tag:working_language:") for warning in warnings)


def test_portuguese_and_role_english_in_international_company_remain_allowed(record):
    record.description_clean = record.description_clean.replace(
        "English required.",
        "Portuguese is required. You will collaborate in English. We are an international company.",
    )
    raw, units = response(record)
    raw["working_language"].update(
        value="portuguese_with_english", basis="explicit",
        evidence=[unit["unit_id"] for unit in units if any(text in unit["text"] for text in
                  ("Portuguese is required", "collaborate in English", "international company"))],
    )
    checked, warnings = validate_tags(raw, units, ["analytics-leadership"])
    assert checked["tags"]["working_language"]["value"] == "portuguese_with_english"
    assert not any(warning.startswith("tag:working_language:") for warning in warnings)


def test_combined_working_language_declaration_remains_a_valid_portuguese_english_fact(record):
    record.description_clean = record.description_clean.replace(
        "English required.",
        "Our working languages are Portuguese and English.\nWe are an international company.",
    )
    raw, units = response(record)
    raw["working_language"].update(value="portuguese_with_english", basis="explicit", evidence=[
        unit["unit_id"] for unit in units if any(text in unit["text"] for text in
                                                ("working languages", "international company"))
    ])
    checked, warnings = validate_tags(raw, units, ["analytics-leadership"])
    assert checked["tags"]["working_language"]["value"] == "portuguese_with_english"
    assert not any(warning.startswith("tag:working_language:") for warning in warnings)


@pytest.mark.parametrize("language", ["Danish", "UnrecognizedLanguage"])
def test_combined_portuguese_english_cannot_hide_another_declared_working_language(
    record, language,
):
    record.description_clean = record.description_clean.replace(
        "English required.",
        "Portuguese is required. You will collaborate in English. "
        "We are an international company.\n"
        "Our working language is " + language + ".",
    )
    raw, units = response(record)
    raw["working_language"].update(value="portuguese_with_english", basis="explicit", evidence=[
        unit["unit_id"] for unit in units if any(text in unit["text"] for text in
                  ("Portuguese is required", "collaborate in English", "international company"))
    ])
    checked, warnings = validate_tags(raw, units, ["analytics-leadership"])
    assert checked["tags"]["working_language"]["value"] == "unspecified"
    assert "tag:working_language:unsupported_evidence" in warnings


def test_explicit_english_cannot_hide_required_portuguese_in_a_domestic_company(record):
    record.description_clean += "\nPortuguese is mandatory.\nWe are a Brazil-only company."
    raw, units = response(record)
    choose(raw, units, "working_language", "English required", value="english", basis="explicit")
    checked, _ = validate_tags(raw, units, ["analytics-leadership"])
    assert checked["tags"]["working_language"]["value"] == "unspecified"


@pytest.mark.parametrize("skill", ["SQL", "Python", "C++"])
def test_technical_skill_requirements_are_not_foreign_working_languages(record, skill):
    record.description_clean = record.description_clean.replace(
        "English required.", "We are an international company.\n" + skill + " is mandatory.",
    )
    raw, units = response(record)
    choose(raw, units, "working_language", "international company.", value="english",
           basis="implied")
    checked, warnings = validate_tags(raw, units, ["analytics-leadership"])
    assert checked["tags"]["working_language"]["value"] == "english"
    assert not any(warning.startswith("tag:working_language:") for warning in warnings)


@pytest.mark.parametrize("optional", [
    "Preferred fluency in French.",
    "Fluency in French is preferred.",
    "Portuguese proficiency is optional.",
    "Portuguese proficiency is not required.",
    "French is not a mandatory requirement.",
    "You must not communicate in Portuguese with your team.",
    "We don't work in French.",
    "We never communicate in Portuguese.",
    "Our working language is not Portuguese.",
    "You will analyze French proficiency trends.",
    "You will serve Portuguese customers.",
    "This role requires French work authorization.",
    "French or English are required.",
    "English or French are required.",
    "Required languages: English or French.",
    "Required languages: French or English.",
    "We serve French-speaking customers.",
    "You will serve French speaking clients.",
    "This role requires Portuguese customers.",
    "No French proficiency is required.",
    "French proficiency isn't required.",
    "We analyze French customer data.",
    "This role requires knowledge of French law.",
    "We are a French company.",
])
def test_optional_foreign_language_does_not_veto_english_soft_fact(record, optional):
    record.description_clean = record.description_clean.replace(
        "English required.", "We are an international company.\n" + optional,
    )
    raw, units = response(record)
    choose(raw, units, "working_language", "international company.", value="english",
           basis="implied")
    checked, warnings = validate_tags(raw, units, ["analytics-leadership"])
    assert checked["tags"]["working_language"]["value"] == "english"
    assert not any(warning.startswith("tag:working_language:") for warning in warnings)


def test_previous_cache_language_fact_is_revalidated_against_uncited_contradiction(
    config, streams, record,
):
    record.description_clean = record.description_clean.replace(
        "English required.",
        "Candidates located in Brazil are eligible.\nThis role is fully remote.\n"
        "We are an international company.\nPortuguese only is required for this role.",
    )
    raw, units = response(record)
    choose(raw, units, "hiring_eligibility", "Candidates located", value="open_to_brazil")
    choose(raw, units, "work_mode", "fully remote", value="remote")
    choose(raw, units, "working_language", "international company.", value="english",
           basis="implied")
    tagger = service(config, streams, [raw])
    label(tagger, record, use_ai=True)
    cache = next(Path(config.cache_dir).rglob("*.json"))
    old = json.loads(cache.read_text())
    old["result"]["tags"]["working_language"] = raw["working_language"]
    old["warnings"] = []
    cache.write_text(json.dumps(old))
    result = label(tagger, record, use_ai=False)
    assert result.tags["working_language"]["value"] == "unspecified"
    assert result.gate_outcome == "unknown"
    assert result.usage[0]["cached"] and tagger.client.calls == ["tag"]
