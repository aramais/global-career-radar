from copy import deepcopy

import pytest

from job_intake.annotation.schema import prepare_claims, review_claims, summarize_claims


def _unit(text="Fluent English is required for all daily communication.", **changes):
    return {
        "unit_id": "unit-1",
        "field": "description",
        "text": text,
        "section": "requirements",
        **changes,
    }


def _claim(**changes):
    return {
        "kind": "working_language",
        "value": "English",
        "unit_id": "unit-1",
        "source_snippet": "Fluent English is required for all daily communication.",
        "requirement": "required",
        "polarity": "affirmative",
        "is_inference": False,
        **changes,
    }


def _prepare(claim=None, unit=None, source_id="job-1", chunk_id="chunk-1"):
    return prepare_claims({"claims": [claim or _claim()]}, source_id, [unit or _unit()], chunk_id)


def _support(claims, **changes):
    return review_claims(
        claims,
        {
            "reviews": [
                {"claim_id": claim["claim_id"], "review_status": "SUPPORTED", **changes}
                for claim in claims
            ]
        },
    )[0]


def test_qualified_id_is_stable_and_depends_on_source_and_evidence():
    claims, rejects = _prepare()
    assert rejects == []
    assert claims[0]["claim_id"].startswith("job-1:claim:")
    assert claims[0]["claim_id"] == _prepare(chunk_id="chunk-2")[0][0]["claim_id"]
    assert claims[0]["claim_id"] != _prepare(source_id="job-2")[0][0]["claim_id"]
    assert claims[0]["claim_id"] != _prepare(_claim(requirement="informational"))[0][0]["claim_id"]


def test_unicode_and_quote_whitespace_are_preserved_without_losing_nfc_grounding():
    snippet = "Работа в Sa\u0303o\tPaulo и 日本語 без ограничений."
    unit = _unit("Работа в São Paulo и 日本語 без ограничений.")
    claim = _claim(kind="hiring_location", value="São Paulo 日本語", source_snippet=snippet)
    claims, rejects = _prepare(claim, unit)
    assert rejects == []
    assert claims[0]["source_snippet"] == snippet
    assert claims[0]["value"] == "São Paulo 日本語"


def test_quote_must_be_case_sensitive_verbatim_in_exact_own_unit():
    units = [_unit(), _unit("Remote work is permitted anywhere in Brazil.", unit_id="unit-2")]
    claims, rejects = prepare_claims(
        {
            "claims": [
                _claim(source_snippet="fluent English is required for all daily communication."),
                _claim(source_snippet="Remote work is permitted anywhere in Brazil."),
                _claim(unit_id="invented-unit"),
            ]
        },
        "job-1",
        units,
        "chunk-1",
    )
    assert claims == []
    assert len(rejects) == 3
    assert all("verbatim" in row["reason"] for row in rejects[:2])
    assert "own chunk" in rejects[2]["reason"]


@pytest.mark.parametrize("payload", [None, [], {"claims": {}}, {}, {"claims": True}])
def test_invalid_extraction_envelope_fails_whole_response(payload):
    with pytest.raises(ValueError, match=r"claims\[\]"):
        prepare_claims(payload, "job-1", [_unit()], "chunk-1")


@pytest.mark.parametrize(
    "changes",
    [
        {"value": True},
        {"value": 9},
        {"value": " "},
        {"kind": ["role"]},
        {"requirement": "mandatory"},
        {"polarity": None},
        {"is_inference": 0},
        {"source_snippet": ""},
        {"source_id": "other-source"},
        {"chunk_id": "other-chunk"},
    ],
)
def test_malformed_claim_is_preserved_with_rejection_reason(changes):
    row = _claim(**changes)
    claims, rejects = _prepare(row)
    assert claims == []
    assert rejects[0]["record"] == row
    assert rejects[0]["reason"]


def test_non_object_claims_are_not_silently_discarded():
    claims, rejects = prepare_claims(
        {"claims": [None, "English", True, _claim()]}, "job-1", [_unit()], "chunk-1"
    )
    assert len(claims) == 1
    assert [row["record"] for row in rejects] == [None, "English", True]


@pytest.mark.parametrize("field", ["description", "description_clean", "description_raw"])
def test_short_keyword_from_prose_cannot_establish_requirement(field):
    claims, rejects = _prepare(_claim(source_snippet="English"), _unit(field=field))
    assert claims == []
    assert "generic" in rejects[0]["reason"]


@pytest.mark.parametrize(
    ("field", "kind", "value"),
    [
        ("location_text", "hiring_location", "BR"),
        ("remote_text", "work_mode", "Remote"),
        ("source_metadata.working_language", "working_language", "English"),
        ("title", "role", "Analyst"),
    ],
)
def test_short_structured_value_is_valid_direct_evidence(field, kind, value):
    claims, rejects = _prepare(
        _claim(kind=kind, value=value, source_snippet=value, requirement="informational"),
        _unit(value, field=field, section="other"),
    )
    assert not rejects
    assert _support(claims)[0]["review_status"] == "SUPPORTED"


def test_model_cannot_invent_trusted_provenance_or_review_status():
    claims, _ = _prepare(
        _claim(
            claim_id="fake-id",
            source_field="title",
            source_section="role",
            review_status="SUPPORTED",
        )
    )
    assert claims[0]["claim_id"] != "fake-id"
    assert claims[0]["source_field"] == "description"
    assert claims[0]["source_section"] == "requirements"
    assert claims[0]["review_status"] == "UNREVIEWED"


def test_duplicate_extraction_is_retained_as_reject_not_extra_support():
    claims, rejects = prepare_claims(
        {"claims": [_claim(), _claim()]}, "job-1", [_unit()], "chunk-1"
    )
    assert len(claims) == len(rejects) == 1
    assert "Duplicate" in rejects[0]["reason"]


@pytest.mark.parametrize("payload", [None, [], {}, {"reviews": {}}, {"claims": []}])
def test_invalid_review_envelope_fails_atomically_without_mutation(payload):
    claims = _prepare()[0]
    original = deepcopy(claims)
    with pytest.raises(ValueError, match=r"reviews\[\]"):
        review_claims(claims, payload)
    assert claims == original


def test_review_cannot_rewrite_claim_or_evidence_or_clear_inference():
    claims = _prepare(_claim(is_inference=True))[0]
    reviewed = _support(
        claims,
        is_inference=False,
        value="Spanish",
        source_snippet="Fabricated quote",
        source_field="title",
    )
    assert reviewed[0]["value"] == "English"
    assert reviewed[0]["source_snippet"] == claims[0]["source_snippet"]
    assert reviewed[0]["source_field"] == "description"
    assert reviewed[0]["is_inference"] is True
    assert reviewed[0]["review_status"] == "WEAKLY_SUPPORTED"
    assert summarize_claims(reviewed)["knowns"] == []


def test_reviewer_can_add_inference_but_cannot_then_support_it():
    reviewed = _support(_prepare()[0], is_inference=True)
    assert reviewed[0]["review_status"] == "WEAKLY_SUPPORTED"


def test_duplicate_invalid_and_unknown_reviews_are_retained_and_fail_closed():
    claims = _prepare()[0]
    identity = claims[0]["claim_id"]
    rows = [
        {"claim_id": identity, "review_status": "SUPPORTED"},
        {"claim_id": identity, "review_status": "UNSUPPORTED"},
        {"claim_id": "invented-id", "review_status": "SUPPORTED"},
        None,
    ]
    reviewed, rejects = review_claims(claims, {"reviews": rows})
    assert reviewed[0]["review_status"] == "NEEDS_VERIFICATION"
    assert len(rejects) == len(rows)
    assert [reject["record"] for reject in rejects] == rows


@pytest.mark.parametrize(
    "changes",
    [
        {"review_status": "APPROVED"},
        {"review_status": True},
        {"is_inference": "false"},
        {"review_notes": False},
    ],
)
def test_invalid_review_for_known_claim_does_not_grant_support(changes):
    claims = _prepare()[0]
    reviewed, rejects = review_claims(
        claims,
        {"reviews": [{"claim_id": claims[0]["claim_id"], "review_status": "SUPPORTED", **changes}]},
    )
    assert rejects
    assert reviewed[0]["review_status"] == "NEEDS_VERIFICATION"


def test_missing_review_defaults_to_verification():
    reviewed, rejects = review_claims(_prepare()[0], {"reviews": []})
    assert not rejects
    assert reviewed[0]["review_status"] == "NEEDS_VERIFICATION"


@pytest.mark.parametrize("kind", ["role", "responsibility", "skill", "hiring_location"])
def test_company_facts_are_preserved_but_cannot_become_role_fit(kind):
    unit = _unit("Our company builds data products across Brazil.", section="company")
    claims = _prepare(_claim(kind=kind, source_snippet=unit["text"]), unit)[0]
    reviewed = _support(claims)
    assert reviewed[0]["source_snippet"] == unit["text"]
    assert reviewed[0]["review_status"] == "WEAKLY_SUPPORTED"
    assert summarize_claims(reviewed)["knowns"] == []


@pytest.mark.parametrize(
    "text",
    [
        "English is not required for this position.",
        "English is optional for this position.",
        "English is nice-to-have for this position.",
        "No English required for this position.",
    ],
)
def test_optional_or_negated_english_cannot_confirm_working_language(text):
    claims = _prepare(_claim(source_snippet=text), _unit(text))[0]
    reviewed = _support(claims)
    assert reviewed[0]["review_status"] == "WEAKLY_SUPPORTED"
    assert "working_language" in summarize_claims(reviewed)["unknowns"]


def test_exact_quote_cannot_remove_negation_from_surrounding_clause():
    unit = _unit("No English required for this position.")
    claims = _prepare(_claim(source_snippet="English required"), unit)[0]
    assert _support(claims)[0]["review_status"] == "WEAKLY_SUPPORTED"


@pytest.mark.parametrize(
    "text",
    [
        "English is required for communication; French is preferred.",
        "English is required for communication, French is optional.",
        "English is not only required but also used for all communication.",
    ],
)
def test_unrelated_preferences_and_not_only_do_not_downgrade_required_english(text):
    claims = _prepare(_claim(source_snippet=text), _unit(text, field="description_clean"))[0]
    reviewed = _support(claims)
    assert reviewed[0]["review_status"] == "SUPPORTED"
    assert summarize_claims(reviewed)["coverage"]["working_language"] is True


@pytest.mark.parametrize(
    "text",
    [
        "The working language is not English.",
        "English is not our working language.",
        "No English is used for daily communication.",
    ],
)
def test_explicit_language_negation_cannot_become_affirmative_confirmation(text):
    claims = _prepare(
        _claim(source_snippet=text, requirement="informational"),
        _unit(text, field="description_raw"),
    )[0]
    reviewed = _support(claims)
    assert reviewed[0]["review_status"] == "WEAKLY_SUPPORTED"
    assert summarize_claims(reviewed)["coverage"]["working_language"] is False


@pytest.mark.parametrize(
    "text",
    [
        "Is the working language: English?",
        "It is not confirmed that the working language is English.",
        "The working language might be English.",
        "The working language is currently unknown, possibly English.",
    ],
)
def test_question_or_unconfirmed_language_cannot_be_supported_as_direct_fact(text):
    claims = _prepare(
        _claim(source_snippet=text, requirement="informational"),
        _unit(text, field="description_clean"),
    )[0]
    reviewed = _support(claims)
    assert reviewed[0]["review_status"] == "WEAKLY_SUPPORTED"
    assert "working_language" in summarize_claims(reviewed)["unknowns"]


def test_shortened_quote_cannot_hide_question_mark_in_its_source_unit():
    text = "Is the working language: English?"
    claims = _prepare(
        _claim(source_snippet="working language: English"), _unit(text, field="description_raw")
    )[0]
    assert _support(claims)[0]["review_status"] == "WEAKLY_SUPPORTED"


def test_unrelated_unconfirmed_salary_does_not_erase_explicit_working_language():
    text = "The working language is English; salary is currently unknown."
    claims = _prepare(
        _claim(source_snippet=text, requirement="informational"),
        _unit(text, field="description_clean"),
    )[0]
    assert _support(claims)[0]["review_status"] == "SUPPORTED"


def test_not_required_language_fact_is_known_without_language_confirmation():
    unit = _unit("English is not required for this position.")
    claims = _prepare(
        _claim(source_snippet=unit["text"], requirement="not_required", polarity="negative"), unit
    )[0]
    summary = summarize_claims(_support(claims))
    assert len(summary["knowns"]) == 1
    assert summary["coverage"]["working_language"] is False
    assert "working_language" in summary["unknowns"]


@pytest.mark.parametrize(
    "changes",
    [
        {"requirement": "not_required"},
        {"polarity": "negative"},
    ],
)
def test_conflicting_supported_requirements_are_never_silently_resolved(changes):
    # Independent claims can be supported by different source units while
    # contradicting one another. The summary exposes both and excludes support.
    claim1 = _prepare()[0][0]
    unit2 = _unit("English is not required for this position.", unit_id="unit-2")
    claim2 = _prepare(
        _claim(
            **{
                "unit_id": "unit-2",
                "source_snippet": unit2["text"],
                "requirement": "not_required",
                "polarity": "negative",
                **changes,
            }
        ),
        unit2,
    )[0][0]
    summary = summarize_claims(_support([claim1, claim2]))
    assert summary["knowns"] == []
    assert summary["coverage"]["working_language"] is False
    assert len(summary["contradictions"]) == 1
    assert set(summary["contradictions"][0]["claim_ids"]) == {
        claim1["claim_id"],
        claim2["claim_id"],
    }


def test_reviewed_conflict_is_reported_even_without_a_pair():
    reviewed = _support(
        _prepare()[0], review_status="CONFLICTING", review_notes="Two clauses differ"
    )
    summary = summarize_claims(reviewed)
    assert summary["knowns"] == []
    assert summary["contradictions"][0]["reason"] == "Two clauses differ"


def test_only_supported_direct_facts_fill_coverage_and_unknowns_remain_explicit():
    supported = _support(_prepare()[0])[0]
    summary = summarize_claims([supported])
    assert summary["coverage"]["working_language"] is True
    assert "working_language" not in summary["unknowns"]
    assert set(summary["unknowns"]) == {
        "hiring_location",
        "work_mode",
        "timezone",
        "salary",
        "employment",
        "work_authorization",
    }
    assert summary["supported_claim_ids"] == [supported["claim_id"]]
    for status in ("UNREVIEWED", "NEEDS_VERIFICATION", "UNSUPPORTED", "WEAKLY_SUPPORTED"):
        summary = summarize_claims([{**supported, "review_status": status}])
        assert summary["knowns"] == []
        assert summary["coverage"]["working_language"] is False


def _direct_fact(kind, value, text, unit_id, field="description_clean"):
    return _prepare(
        _claim(
            kind=kind,
            value=value,
            unit_id=unit_id,
            source_snippet=text,
            requirement="informational",
        ),
        _unit(text, field=field, unit_id=unit_id),
    )[0][0]


@pytest.mark.parametrize("kind", ["working_language", "hiring_location", "skill"])
def test_distinct_ordinary_values_are_compatible_not_automatically_conflicting(kind):
    if kind == "working_language":
        claims = [
            _direct_fact(kind, value, value, str(index), "source_metadata.working_language")
            for index, value in enumerate(("English", "Portuguese"))
        ]
    elif kind == "hiring_location":
        claims = [
            _direct_fact(kind, value, value, str(index), "location_text")
            for index, value in enumerate(("Brazil", "Germany"))
        ]
    else:
        claims = [
            _direct_fact(kind, value, "We use " + value + " for this role.", str(index))
            for index, value in enumerate(("Python", "SQL"))
        ]
    summary = summarize_claims(_support(claims))
    assert len(summary["knowns"]) == 2
    assert summary["contradictions"] == []


@pytest.mark.parametrize(
    "text",
    [
        "The only working language is Portuguese.",
        "Portuguese is the only working language.",
        "The working language is Portuguese only.",
        "We communicate exclusively in Portuguese.",
    ],
)
def test_exclusive_working_language_conflicts_with_supported_other_language(text):
    claims = [
        _direct_fact("working_language", "Portuguese", text, "pt"),
        _direct_fact(
            "working_language", "English", "English", "en", "source_metadata.working_language"
        ),
    ]
    summary = summarize_claims(_support(claims))
    assert summary["knowns"] == []
    assert "working_language" in summary["unknowns"]
    assert len(summary["contradictions"]) == 1
    assert set(summary["contradictions"][0]["values"]) == {"Portuguese", "English"}


@pytest.mark.parametrize(
    "text",
    [
        "We work not only in Portuguese but also English.",
        "Our working languages are English and Portuguese only.",
        "We use Portuguese only during the weekly Portuguese course.",
    ],
)
def test_inclusive_languages_and_activity_specific_only_do_not_create_false_conflict(text):
    claims = [
        _direct_fact("working_language", "Portuguese", text, "pt"),
        _direct_fact(
            "working_language", "English", "English", "en", "source_metadata.working_language"
        ),
    ]
    summary = summarize_claims(_support(claims))
    assert summary["contradictions"] == []
    assert len(summary["knowns"]) == 2


@pytest.mark.parametrize(
    "text",
    [
        "We hire in Germany only.",
        "We hire only in Germany.",
        "Only candidates in Germany may apply.",
        "Only German residents may apply.",
    ],
)
def test_explicit_restricted_hiring_conflicts_with_supported_worldwide(text):
    claims = [
        _direct_fact("hiring_location", "Germany", text, "de"),
        _direct_fact("hiring_location", "Worldwide", "Worldwide", "all", "location_text"),
    ]
    summary = summarize_claims(_support(claims))
    assert summary["knowns"] == []
    assert "hiring_location" in summary["unknowns"]
    assert len(summary["contradictions"]) == 1


def test_inclusive_not_only_country_does_not_conflict_with_worldwide():
    claims = [
        _direct_fact("hiring_location", "Germany", "We hire not only in Germany.", "de"),
        _direct_fact("hiring_location", "Worldwide", "Worldwide", "all", "location_text"),
    ]
    summary = summarize_claims(_support(claims))
    assert summary["contradictions"] == []
    assert len(summary["knowns"]) == 2


def test_exclusive_country_does_not_guess_region_membership_from_different_names():
    claims = [
        _direct_fact("hiring_location", "Europe", "We hire only in Europe.", "eu"),
        _direct_fact("hiring_location", "Germany", "Germany", "de", "location_text"),
    ]
    summary = summarize_claims(_support(claims))
    assert summary["contradictions"] == []
    assert len(summary["knowns"]) == 2
