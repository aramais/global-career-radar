from job_intake.filtering import FilterRules, RuleEngine
from job_intake.models.job import FilterDecision, JobRecord


def build_rules() -> FilterRules:
    return FilterRules.from_mapping(
        {
            "positive_title_signals": ["product analytics lead", "staff data scientist"],
            "positive_description_signals": ["experimentation", "pricing", "marketplace"],
            "negative_title_signals": ["ml engineer", "data engineer"],
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
    assert any("phrase_blocker:us work authorization required" == item for item in result.blocker_signals)


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
