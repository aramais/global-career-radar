from dataclasses import replace

import pytest

from job_intake.filtering import FilterRules, RuleEngine
from job_intake.models.job import FilterDecision, JobRecord


def build_rules() -> FilterRules:
    return FilterRules.from_mapping(
        {
            "positive_title_signals": ["product analytics lead", "staff data scientist"],
            "positive_description_signals": ["experimentation", "pricing", "marketplace"],
            "negative_title_signals": ["ml engineer", "data engineer", "machine learning engineer"],
            "negative_description_signals": ["mlops", "feature store"],
            "blocker_phrases": ["us work authorization required", "relocation required"],
            "review_phrases": ["preferred in"],
            "allowlist_phrases": ["worldwide", "americas", "latam", "contractor", "eor"],
            "company_blacklist": ["BadCo"],
            "company_whitelist": ["GreatCo"],
            "target_geographies": ["americas", "latam", "worldwide"],
            "geography_blockers": ["eu only", "us only", "must reside in"],
            "timezone_allowed": ["americas timezone", "async"],
            "timezone_blockers": ["europe time zone", "work eastern time only"],
            "closed_phrases": ["no longer accepting applications"],
        }
    )


def test_rejects_country_work_auth_blocker() -> None:
    engine = RuleEngine(build_rules())
    job = JobRecord(
        source="test",
        company="Example",
        title="Senior Product Analytics Lead",
        original_url="https://example.com/job",
        description_clean=(
            "Lead experimentation and pricing. US work authorization required. "
            "Role is remote in americas."
        ),
    )

    result = engine.evaluate(job)
    assert result.decision == FilterDecision.REJECT
    assert "phrase_blocker:us work authorization required" in result.blocker_signals


def test_passes_bridge_role_with_allowed_geo() -> None:
    engine = RuleEngine(build_rules())
    job = JobRecord(
        source="test",
        company="GreatCo",
        title="Product Analytics Lead",
        original_url="https://example.com/job",
        description_clean=(
            "Worldwide contractor role leading experimentation, pricing, and marketplace decisions "
            "for a distributed async team."
        ),
    )

    result = engine.evaluate(job)
    assert result.decision == FilterDecision.PASS
    assert result.bridge_role is True
    assert any(item.startswith("allowlist:worldwide") for item in result.matched_signals)


def test_rejects_non_target_ml_role_family() -> None:
    engine = RuleEngine(build_rules())
    job = JobRecord(
        source="test",
        company="Example",
        title="Machine Learning Engineer",
        original_url="https://example.com/job",
        description_clean="Own the feature store, platform and mlops roadmap.",
    )

    result = engine.evaluate(job)
    assert result.decision == FilterDecision.REJECT
    assert any(item.startswith("title_blocker") for item in result.blocker_signals)


def test_sends_weak_signal_roles_to_review() -> None:
    engine = RuleEngine(build_rules())
    job = JobRecord(
        source="test",
        company="Example",
        title="Analytics Manager",
        original_url="https://example.com/job",
        description_clean="General analytics stakeholder support for internal reporting.",
    )

    result = engine.evaluate(job)
    assert result.decision == FilterDecision.REVIEW


def broad_rules() -> FilterRules:
    return replace(
        build_rules(),
        recall_first=True,
        target_geographies=["brazil", "latam", "americas", "worldwide"],
        onsite_locations=["São Paulo"],
        required_languages=["English"],
    )


def test_broad_search_preserves_role_family_mismatches() -> None:
    job = JobRecord(
        source="test", company="Example", title="Machine Learning Engineer",
        original_url="https://example.com/job", location_text="Remote, Brazil",
        description_clean="Own the feature store and mlops. English proficiency required.",
    )
    result = RuleEngine(broad_rules()).evaluate(job)
    assert result.decision == FilterDecision.REVIEW
    assert result.blocker_signals == []
    assert "title_blocker:machine learning engineer" in result.risks
    assert "desc_blocker:mlops" in result.risks
    assert RuleEngine(build_rules()).evaluate(job).decision == FilterDecision.REJECT


@pytest.mark.parametrize("allowance", ["Contractor", "Async distributed team", "Worldwide remote"])
def test_mandatory_foreign_residence_is_not_cancelled_by_general_allowance(allowance: str) -> None:
    job = JobRecord(
        source="test", company="Example", title="Product Analytics Lead",
        original_url="https://example.com/job",
        description_clean=f"Must reside in Germany. {allowance}. English proficiency required.",
    )
    result = RuleEngine(broad_rules()).evaluate(job)
    assert result.decision == FilterDecision.REJECT
    assert "geo_blocker:must reside in germany" in result.blocker_signals


def test_mandatory_brazil_residence_is_allowed() -> None:
    job = JobRecord(
        source="test", company="Example", title="Product Analytics Lead",
        original_url="https://example.com/job", remote_text="Remote",
        description_clean="Must be based in Brazil. Fluency in English required.",
    )
    result = RuleEngine(broad_rules()).evaluate(job)
    assert result.decision == FilterDecision.PASS
    assert result.blocker_signals == []


@pytest.mark.parametrize("location", ["São Paulo, Brazil", "Sao Paulo, Brazil"])
def test_configured_onsite_city_is_allowed(location: str) -> None:
    rules = replace(broad_rules(), blocker_phrases=["on-site required"])
    job = JobRecord(
        source="test", company="Example", title="Product Analytics Lead",
        original_url="https://example.com/job", location_text=location,
        remote_text="On-site", description_clean="On-site required. Fluent English required.",
    )
    result = RuleEngine(rules).evaluate(job)
    assert result.decision == FilterDecision.PASS


def test_structured_onsite_other_city_is_rejected() -> None:
    job = JobRecord(
        source="test", company="Example", title="Product Analytics Lead",
        original_url="https://example.com/job", location_text="Rio de Janeiro, Brazil",
        remote_text="Hybrid", description_clean="Fluent English required.",
    )
    result = RuleEngine(broad_rules()).evaluate(job)
    assert result.decision == FilterDecision.REJECT
    assert "geo_blocker:onsite:Rio de Janeiro, Brazil" in result.blocker_signals


def test_unknown_eligibility_and_working_language_remain_reviewable() -> None:
    job = JobRecord(
        source="test", company="Example", title="Product Analytics Lead",
        original_url="https://example.com/job", remote_text="Remote",
        description_clean="Lead pricing and experimentation for a distributed async team.",
    )
    result = RuleEngine(broad_rules()).evaluate(job)
    assert result.decision == FilterDecision.REVIEW
    assert "geography:eligibility_unconfirmed" in result.risks
    assert "language:working_language_unconfirmed" in result.risks
    assert result.blocker_signals == []


def test_english_advert_is_not_proof_of_english_working_language() -> None:
    job = JobRecord(
        source="test", company="Example", title="Product Analytics Lead",
        original_url="https://example.com/job", location_text="Remote, Brazil",
        description_clean="Lead product strategy and experimentation.",
    )
    result = RuleEngine(broad_rules()).evaluate(job)
    assert result.decision == FilterDecision.REVIEW
    assert result.risks == ["language:working_language_unconfirmed"]


def test_known_other_working_language_is_explicit_blocker() -> None:
    job = JobRecord(
        source="test", company="Example", title="Product Analytics Lead",
        original_url="https://example.com/job", location_text="Remote, Brazil",
        source_metadata={"working_language": "Portuguese"},
    )
    result = RuleEngine(broad_rules()).evaluate(job)
    assert result.decision == FilterDecision.REJECT
    assert result.blocker_signals == ["language:working_language:Portuguese"]


def test_onsite_country_without_city_is_reviewable() -> None:
    job = JobRecord(
        source="test", company="Example", title="Product Analytics Lead",
        original_url="https://example.com/job", location_text="Brazil", remote_text="On-site",
        description_clean="Fluent English required.",
    )
    result = RuleEngine(broad_rules()).evaluate(job)
    assert result.decision == FilterDecision.REVIEW
    assert "geography:onsite_location_unknown" in result.risks
    assert result.blocker_signals == []


def test_unspecified_mandatory_residence_is_reviewable() -> None:
    job = JobRecord(
        source="test", company="Example", title="Product Analytics Lead",
        original_url="https://example.com/job",
        description_clean=(
            "Must be based in one of our supported countries. Fluent English required."
        ),
    )
    result = RuleEngine(broad_rules()).evaluate(job)
    assert result.decision == FilterDecision.REVIEW
    assert "geography:residency_location_unconfirmed" in result.risks
    assert result.blocker_signals == []


def test_target_country_explicit_exclusion_is_not_an_allow_signal() -> None:
    job = JobRecord(
        source="test", company="Example", title="Product Analytics Lead",
        original_url="https://example.com/job",
        description_clean="Worldwide remote. We cannot hire in Brazil. Fluent English required.",
    )
    result = RuleEngine(broad_rules()).evaluate(job)
    assert result.decision == FilterDecision.REJECT
    assert "geo_blocker:cannot hire in brazil" in result.blocker_signals


def test_explicit_other_working_language_in_description_is_blocker() -> None:
    job = JobRecord(
        source="test", company="Example", title="Product Analytics Lead",
        original_url="https://example.com/job", location_text="Remote, Brazil",
        description_clean="The working language is Portuguese. Lead experimentation.",
    )
    result = RuleEngine(broad_rules()).evaluate(job)
    assert result.decision == FilterDecision.REJECT
    assert "language:working_language:portuguese" in result.blocker_signals


@pytest.mark.parametrize("eligible_locations", [["Brazil"], ["Worldwide"], ["LATAM"]])
def test_structured_applicant_locations_can_confirm_eligibility(
    eligible_locations: list[str],
) -> None:
    job = JobRecord(
        source="test", company="Example", title="Product Analytics Lead",
        original_url="https://example.com/job", remote_text="Remote",
        source_metadata={
            "applicant_location_requirements": eligible_locations,
            "working_language": ["English", "Portuguese"],
        },
    )
    result = RuleEngine(broad_rules()).evaluate(job)
    assert result.decision == FilterDecision.PASS
    assert result.risks == []


def test_office_country_is_not_applicant_eligibility() -> None:
    job = JobRecord(
        source="test", company="Example", title="Product Analytics Lead",
        original_url="https://example.com/job", remote_text="Remote",
        source_metadata={"country": "Brazil", "working_language": "English"},
    )
    result = RuleEngine(broad_rules()).evaluate(job)
    assert result.decision == FilterDecision.REVIEW
    assert result.risks == ["geography:eligibility_unconfirmed"]


def test_explicit_applicant_country_allowlist_outside_target_rejects() -> None:
    job = JobRecord(
        source="test", company="Example", title="Product Analytics Lead",
        original_url="https://example.com/job", remote_text="Worldwide remote",
        source_metadata={"applicant_location_requirements": ["United States", "Canada"]},
    )
    result = RuleEngine(broad_rules()).evaluate(job)
    assert result.decision == FilterDecision.REJECT
    assert "geo_blocker:applicant_locations:United States, Canada" in result.blocker_signals


def test_same_sentence_contractor_geography_does_not_override_mandatory_residence() -> None:
    job = JobRecord(
        source="test", company="Example", title="Product Analytics Lead",
        original_url="https://example.com/job",
        description_clean="Must reside in Germany, but we hire contractors across LATAM.",
    )
    assert RuleEngine(broad_rules()).evaluate(job).decision == FilterDecision.REJECT
