from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone

import pytest

from job_intake.annotation.text import (
    asserted_phrase_hits,
    chunk_source,
    normalize_quote,
    role_description,
    scoped_description_text,
    source_hash,
    source_identity,
    source_units,
)
from job_intake.models.job import FilterDecision, JobEvaluation, JobRecord
from job_intake.scoring.pre_score import DeterministicScorer, SearchProfiles


def _job(**changes) -> JobRecord:
    return JobRecord(**{
        "source": "test", "source_job_id": "42", "company": "Example",
        "title": "Product Manager", "original_url": "https://example.com/job/42",
        "description_clean": "Own the roadmap.", **changes,
    })


def test_source_units_preserve_exact_unicode_whitespace_and_final_tail() -> None:
    description = (
        "## About us\r\n"
        "Nossa empresa atua em São Paulo e na Rússia.\n\n"
        "Responsibilities:\n  • Развивать продукт.  • Own pricing!\n"
        "Requirements: English required.\n"
        "FINAL UNPUNCTUATED ELIGIBILITY CONDITION"
    )
    units = source_units(_job(description_clean=description))
    fragments = [unit for unit in units if unit["field"] == "description_clean"]
    assert "".join(unit["text"] for unit in fragments) == description
    assert len({unit["unit_id"] for unit in units}) == len(units)
    for unit in fragments:
        assert description[unit["offset_start"]:unit["offset_end"]] == unit["text"]
    assert fragments[-1]["text"] == "FINAL UNPUNCTUATED ELIGIBILITY CONDITION"
    assert fragments[-1]["section"] == "requirements"


def test_source_units_use_raw_description_when_no_clean_description_exists() -> None:
    job = _job(description_clean="", description_raw="Original сырой текст\nTail")
    units = source_units(job)
    assert "".join(unit["text"] for unit in units if unit["field"] == "description_raw") == (
        job.description_raw
    )


def test_explicit_metadata_has_individual_original_units() -> None:
    units = source_units(_job(source_metadata={
        "applicant_location_requirements": ["Brazil", "LATAM"],
        "working_language": "English",
    }))
    metadata = [unit for unit in units if unit["field"].startswith("source_metadata.")]
    assert [unit["text"] for unit in metadata] == ["Brazil", "LATAM", "English"]
    assert all(unit["section"] == "requirements" for unit in metadata)


def test_quote_normalization_keeps_unicode_case_and_punctuation() -> None:
    assert normalize_quote("  Sa\u0303o\n Паулу\t— English?  ") == "São Паулу — English?"
    assert normalize_quote("English") != normalize_quote("english")
    assert normalize_quote("No English required.") != normalize_quote("English required.")


def test_source_identity_is_qualified_by_source_not_bare_external_job_id() -> None:
    first = _job()
    assert source_identity(first) == source_identity(replace(first, company="New name"))
    assert source_identity(first) != source_identity(replace(first, source="another-source"))
    assert source_identity(first) != source_identity(replace(first, source_job_id="43"))


def test_url_identity_ignores_tracking_parameters_and_fragment() -> None:
    job = _job(source_job_id=None)
    assert source_identity(job) == source_identity(replace(
        job, original_url="https://example.com/job/42/?utm_source=mail#apply"
    ))


def test_source_hash_changes_for_eligibility_metadata_and_exact_original_text() -> None:
    job = _job()
    assert source_hash(job) == source_hash(replace(job))
    assert source_hash(job) != source_hash(replace(job, description_clean="Own the Roadmap."))
    assert source_hash(job) != source_hash(replace(
        job, source_metadata={"working_language": "Portuguese"}
    ))
    assert source_hash(job) != source_hash(replace(job, description_raw="Another source body"))


def test_source_hash_publication_date_is_stable_across_sqlite_utc_round_trip() -> None:
    aware = _job(posted_at=datetime(2026, 9, 15, 12, 30, 10, 123456, tzinfo=UTC))
    naive = replace(aware, posted_at=aware.posted_at.replace(tzinfo=None))
    offset = replace(aware, posted_at=aware.posted_at.astimezone(timezone(timedelta(hours=-3))))
    changed = replace(aware, posted_at=aware.posted_at + timedelta(minutes=1))
    assert source_hash(aware) == source_hash(naive) == source_hash(offset)
    assert source_hash(aware) != source_hash(changed)
    assert source_hash(aware) != source_hash(replace(aware, posted_at=None))


def test_chunking_recovers_all_long_unit_fragments_with_bound_context() -> None:
    job = _job(description_clean=("слово with accents São Paulo " * 300) + "IMPORTANT TAIL")
    units = source_units(job)
    chunks = chunk_source(units, max_chars=256)
    assert len(chunks) > 10
    assert len({chunk["chunk_id"] for chunk in chunks}) == len(chunks)
    assert all(len(chunk["text"]) <= 256 for chunk in chunks)
    for chunk in chunks:
        assert len(chunk["unit_ids"]) == len(set(chunk["unit_ids"]))
        for fragment in chunk["fragments"]:
            assert fragment["text"] in chunk["text"]
            assert f"[{fragment['unit_id']} |" in chunk["text"]
    for unit in units:
        fragments = [
            fragment for chunk in chunks for fragment in chunk["fragments"]
            if fragment["unit_id"] == unit["unit_id"]
        ]
        assert "".join(fragment["text"] for fragment in fragments) == unit["text"]
        assert fragments[0]["start"] == 0
        assert fragments[-1]["end"] == len(unit["text"])
    assert chunks[-1]["text"].endswith("IMPORTANT TAIL")
    assert chunks == chunk_source(units, max_chars=256)


@pytest.mark.parametrize("max_chars", [0, -1, 10])
def test_chunk_size_cannot_be_silently_exceeded_for_context_markers(max_chars) -> None:
    with pytest.raises(ValueError):
        chunk_source(source_units(_job()), max_chars=max_chars)


def test_company_profile_is_excluded_from_fit_but_original_remains_available() -> None:
    description = (
        "About us\nWe build marketplace pricing and experimentation software.\n"
        "Responsibilities\nOwn internal payroll reporting.\nApply now"
    )
    job = _job(description_clean=description)
    scoped = role_description(job)
    assert "marketplace" not in scoped
    assert "pricing" not in scoped
    assert "experimentation" not in scoped
    assert "Own internal payroll reporting." in scoped
    assert "Apply now" not in scoped
    assert "".join(unit["text"] for unit in source_units(job) if (
        unit["field"] == "description_clean"
    )) == description


def test_original_html_heading_scope_survives_flattened_clean_description() -> None:
    job = _job(
        description_raw=(
            "<h2>About us</h2><p>We create experimentation and pricing software.</p>"
            "<h2>Responsibilities</h2><ul><li>Own payroll reporting.</li></ul>"
            "<strong>Requirements</strong><p>English required.</p>"
        ),
        description_clean=(
            "About us We create experimentation and pricing software. "
            "Responsibilities Own payroll reporting. Requirements English required."
        ),
    )
    units = [unit for unit in source_units(job) if unit["field"] == "description_clean"]
    assert "".join(unit["text"] for unit in units) == job.description_clean
    scoped = role_description(job)
    assert "experimentation" not in scoped
    assert "pricing" not in scoped
    assert "Own payroll reporting" in scoped
    assert "English required" in scoped
    assert any(unit["section"] == "role" and "payroll" in unit["text"] for unit in units)
    assert any(unit["section"] == "requirements" and "English" in unit["text"] for unit in units)


def test_role_requirements_inside_company_section_are_kept() -> None:
    description = (
        "About us\nWe build marketplaces.\n"
        "You will own pricing.\nCandidates must reside in Germany."
    )
    scoped = scoped_description_text(description)
    assert "We build marketplaces" not in scoped
    assert "You will own pricing" in scoped
    assert "Candidates must reside in Germany" in scoped


@pytest.mark.parametrize("company_statement", [
    "Our company working language is English.",
    "Our customers must speak fluent English.",
])
def test_company_language_is_not_reclassified_as_a_vacancy_requirement(company_statement) -> None:
    units = source_units(_job(description_clean=(
        "About us\n" + company_statement + "\nResponsibilities\nOwn the roadmap."
    )))
    unit = next(unit for unit in units if company_statement in unit["text"])
    assert unit["section"] == "company"
    assert company_statement not in role_description(_job(description_clean=company_statement))


def test_explicit_working_language_for_actual_role_survives_company_section() -> None:
    statement = "The working language for this role is English."
    job = _job(description_clean="About us\n" + statement)
    unit = next(unit for unit in source_units(job) if statement in unit["text"])
    assert unit["section"] == "requirements"
    assert statement in role_description(job)


def test_company_customer_and_office_footprint_without_headings_is_scoped() -> None:
    description = (
        "Our offices are in Brazil. Our customers require experimentation and pricing. "
        "You will own the payroll reporting roadmap."
    )
    scoped = scoped_description_text(description)
    assert "offices" not in scoped
    assert "customers" not in scoped
    assert "You will own" in scoped


@pytest.mark.parametrize("text", [
    "No US work authorization required.",
    "US work authorization required is optional.",
    "Is US work authorization required?",
    "No relocation required.",
])
def test_negated_optional_and_question_blockers_are_not_assertions(text) -> None:
    assert asserted_phrase_hits(
        text, ["us work authorization required", "relocation required"], required=True
    ) == []


@pytest.mark.parametrize("text", [
    "English is not required.", "No English proficiency required.",
    "English skills aren't required.", "Professional English is not required.",
])
def test_negated_language_does_not_confirm_working_language(text) -> None:
    assert asserted_phrase_hits(text, ["English"], required=True) == []


def test_negation_of_one_sentence_does_not_suppress_another_real_signal() -> None:
    assert asserted_phrase_hits(
        "No experimentation required. You will own pricing.", ["experimentation", "pricing"]
    ) == ["pricing"]
    assert asserted_phrase_hits(
        "Own not only experimentation but pricing.", ["experimentation", "pricing"]
    ) == ["experimentation", "pricing"]
    assert asserted_phrase_hits(
        "US work authorization required, relocation not required.",
        ["US work authorization required"], required=True,
    ) == ["US work authorization required"]


def test_deterministic_scoring_uses_role_context_and_ignores_negated_role_signals() -> None:
    scorer = DeterministicScorer(SearchProfiles.from_mapping({
        "description_weights": {"experimentation": 5, "pricing": 3, "roadmap": 2},
        "bucket_a_signals": ["marketplace"],
    }))
    evaluation = JobEvaluation(decision=FilterDecision.PASS)
    scorer.score("test", "Example", "Analyst", (
        "About us\nWe offer marketplace experimentation and pricing software.\n"
        "Responsibilities\nOwn the roadmap. No experimentation required."
    ), evaluation)
    assert evaluation.deterministic_score == 2
    assert evaluation.matched_signals == ["description_weight:roadmap"]
