from __future__ import annotations

from copy import deepcopy

import pytest

from job_intake.discovery.artifacts import candidate_keys, candidate_uid, review_content_keys
from job_intake.models.job import JobRecord


def vacancy(**changes) -> JobRecord:
    values = {
        "source": "discovery",
        "company": "Acme Analytics",
        "title": "Analytics Lead",
        "original_url": "https://jobs.example.com/acme/jobs/1",
        "source_job_id": "req-1",
        "description_clean": (
            "Lead the analytics team and partner with Product on business decisions. "
            "Manage the experiment roadmap and coach analysts while coordinating stakeholders. "
            "Own the forecasting framework and communicate recommendations to senior leadership. "
            "Define clear success metrics and improve pricing through careful research. "
            "Develop the team, align priorities and set the analytics strategy for the business. "
            "This position requires authorization to work in the advertised country. "
            "The expected salary range for this position is $130,000 – $150,000 USD per year, "
            "based on experience, skills, and qualifications."
        ),
    }
    values.update(changes)
    return JobRecord(**values)


def regional_copy(record: JobRecord) -> JobRecord:
    other = deepcopy(record)
    other.source_job_id = "req-2"
    other.original_url = "https://jobs.example.com/acme/jobs/2"
    other.location_text = "Remote - Canada"
    return other


def test_review_duplicates_preserve_distinct_requisition_identities():
    first = vacancy(location_text="Remote - United States")
    second = regional_copy(first)
    assert not candidate_keys(first) & candidate_keys(second)
    assert candidate_uid(first) != candidate_uid(second)
    assert review_content_keys(first) & review_content_keys(second)
    assert first.source_job_id == "req-1" and second.source_job_id == "req-2"


def test_same_long_responsibilities_with_regional_salary_currency_share_review_key():
    first = vacancy(location_text="Remote - United States")
    second = regional_copy(first)
    second.description_clean = second.description_clean.replace("USD per year", "CAD per year")
    assert not candidate_keys(first) & candidate_keys(second)
    overlap = review_content_keys(first) & review_content_keys(second)
    assert len(overlap) == 1
    assert next(iter(overlap)).startswith("review-salary-currency-v1:")


@pytest.mark.parametrize("old,new", [
    ("$150,000", "$180,000"),
    ("requires authorization", "does not require authorization"),
    ("Lead the analytics team", "Operate analytics pipelines"),
    ("per year", "per month"),
])
def test_material_body_changes_are_never_removed(old, new):
    first = vacancy()
    second = regional_copy(first)
    second.description_clean = second.description_clean.replace(old, new)
    assert not review_content_keys(first) & review_content_keys(second)


@pytest.mark.parametrize("field,value", [
    ("company", "Another Company"), ("title", "Finance Lead"),
])
def test_review_content_never_crosses_company_or_title(field, value):
    first = vacancy()
    second = regional_copy(first)
    setattr(second, field, value)
    assert not review_content_keys(first) & review_content_keys(second)


def test_currency_in_responsibilities_is_kept():
    first = vacancy()
    first.description_clean = first.description_clean.replace(
        "Own the forecasting framework", "Own USD currency forecasting"
    )
    second = regional_copy(first)
    second.description_clean = second.description_clean.replace("Own USD", "Own CAD")
    assert not review_content_keys(first) & review_content_keys(second)


def test_unicode_and_html_presentation_normalization_does_not_erase_content():
    first = vacancy(company="Équipe Аналитика", title="Руководитель аналитики")
    second = regional_copy(first)
    second.company = "ÉQUIPE\u00a0АНАЛИТИКА"
    second.description_clean = "\n" + first.description_clean.replace(" and ", " &amp; ") + "\n"
    first.description_clean = first.description_clean.replace(" and ", " & ")
    assert review_content_keys(first) & review_content_keys(second)
    second.company = "Équipe Разработка"
    assert not review_content_keys(first) & review_content_keys(second)


@pytest.mark.parametrize("field", ["company", "title", "description_clean"])
def test_missing_review_context_has_no_content_key(field):
    assert review_content_keys(vacancy(**{field: ""})) == set()


def test_short_salary_excerpt_is_not_enough_for_currency_neutral_grouping():
    first = vacancy(description_clean="The salary range is $100 USD per hour.")
    second = regional_copy(first)
    second.description_clean = first.description_clean.replace("USD", "CAD")
    assert not review_content_keys(first) & review_content_keys(second)


def test_raw_description_is_used_only_when_clean_description_is_absent():
    first = vacancy()
    second = regional_copy(first)
    second.description_raw = second.description_clean
    second.description_clean = ""
    assert review_content_keys(first) & review_content_keys(second)
