"""Offline contract checks for the shipped four-stream personal search configuration."""

import csv
from pathlib import Path

import pytest

from job_intake.classification import RoleClassifier
from job_intake.config.settings import load_yaml_mapping
from job_intake.models.job import FilterDecision, JobRecord, JobStatus, JobTier
from job_intake.profiles import SearchStream, load_streams, with_other_stream
from job_intake.scoring.tiering import finalize_tier
from job_intake.storage.database import Database
from job_intake.storage.repository import JobRepository

CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"


@pytest.fixture
def streams() -> list[SearchStream]:
    # Read YAML directly: no .env, network, production database or AI client.
    return load_streams(
        load_yaml_mapping(CONFIG_DIR / "rules.yaml"),
        load_yaml_mapping(CONFIG_DIR / "search_profiles.yaml"),
    )


def _job(**changes) -> JobRecord:
    fields = {
        "source": "dailyremote",
        "company": "Example",
        "title": "Product Manager",
        "original_url": "https://example.com/job",
        "location_text": "Brazil",
        "remote_text": "Remote",
        "description_clean": "Own pricing, monetization and experimentation with business teams.",
        "source_metadata": {"working_language": "English"},
    }
    return JobRecord(**{**fields, **changes})


def _evaluate(stream: SearchStream, record: JobRecord):
    item = stream.engine.apply(record)
    item.profile_id = stream.id
    item.profile_name = stream.name
    item.profile_version = stream.version
    item.profile_context = stream.context
    stream.scorer.score(
        record.source, record.company, record.title,
        record.description_clean or record.description_raw, item.evaluation, record=record,
    )
    return finalize_tier(item, stream.scoring.threshold_a, stream.scoring.threshold_b)


def test_all_four_real_streams_share_broad_personal_constraints(streams) -> None:
    assert {stream.id for stream in streams} == {
        "product-management", "analytics-leadership", "business-analytics",
        "data-science-management",
    }
    for stream in streams:
        assert stream.rules.recall_first is True
        assert stream.rules.required_languages == ["English"]
        assert stream.rules.required_annotation_focus == [
            "project_management", "people_interaction", "decision_influence", "working_english",
        ]
        assert stream.rules.onsite_locations
        assert stream.context and stream.keywords


@pytest.mark.parametrize(
    ("profile_id", "title", "description"),
    [
        (
            "product-management", "Product Manager",
            "Lead pricing, monetization and experimentation projects with business teams.",
        ),
        (
            "analytics-leadership", "Head of Analytics",
            "Lead pricing and monetization projects with the general manager and Finance.",
        ),
        (
            "business-analytics", "Business Analyst Manager",
            "Lead decision support projects, budget planning and forecasting with Finance.",
        ),
        (
            "data-science-management", "Data Science Manager",
            "Lead experimentation and unit economics projects for business decisions.",
        ),
    ],
)
def test_target_roles_are_ranked_in_their_own_stream(
    streams, profile_id, title, description,
) -> None:
    items = [
        _evaluate(stream, _job(title=title, description_clean=description)) for stream in streams
    ]
    target = next(item for item in items if item.profile_id == profile_id)
    assert target.evaluation.decision == FilterDecision.PASS
    assert target.evaluation.bridge_role is True
    assert target.evaluation.tier == JobTier.A
    assert target.evaluation.fit_score == max(item.evaluation.fit_score for item in items)


def test_economic_product_decisions_outrank_generic_product_management(streams) -> None:
    product = next(stream for stream in streams if stream.id == "product-management")
    generic = _evaluate(
        product, _job(description_clean="Own product discovery, user research and roadmap."),
    )
    economic = _evaluate(
        product,
        _job(description_clean=(
            "Lead pricing and monetization projects. Work with the general manager and "
            "Finance on P&L and business decisions."
        )),
    )
    assert economic.evaluation.tier == JobTier.A
    assert generic.evaluation.tier != JobTier.A
    assert economic.evaluation.fit_score > generic.evaluation.fit_score


def test_decision_science_outranks_technical_model_development(streams) -> None:
    science = next(stream for stream in streams if stream.id == "data-science-management")
    technical = _evaluate(
        science,
        _job(
            title="Data Science Manager",
            description_clean="Lead model development, infrastructure and model deployment.",
        ),
    )
    decision = _evaluate(
        science,
        _job(
            title="Data Science Manager",
            description_clean=(
                "Lead causal inference and experimentation projects with Finance to "
                "inform pricing and business decisions."
            ),
        ),
    )
    assert decision.evaluation.tier == JobTier.A
    assert technical.evaluation.tier != JobTier.A
    assert decision.evaluation.fit_score > technical.evaluation.fit_score


def test_reports_only_role_remains_reviewable_below_a(streams) -> None:
    record = _job(
        title="Head of Analytics",
        description_clean=(
            "Reporting only. Produce reports on pricing, monetization and experimentation."
        ),
    )
    for stream in streams:
        item = _evaluate(stream, record)
        assert item.evaluation.decision == FilterDecision.REVIEW
        assert "desc_blocker:reporting only" in item.evaluation.risks
        assert item.evaluation.tier != JobTier.A
        assert item.evaluation.blocker_signals == []


def test_project_leadership_without_direct_reports_remains_a_target(streams) -> None:
    analytics = next(stream for stream in streams if stream.id == "analytics-leadership")
    record = _job(
        title="Product Analytics Lead",
        description_clean=(
            "Lead pricing and monetization projects with the general manager and Finance. "
            "Manage cross-functional stakeholders to inform business decisions. "
            "This role has no direct reports."
        ),
    )
    item = _evaluate(analytics, record)
    assert item.evaluation.decision == FilterDecision.PASS
    assert item.evaluation.tier == JobTier.A
    assert item.evaluation.blocker_signals == []
    assert item.evaluation.risks == []


def test_individual_analyst_needs_evidence_of_project_leadership(streams) -> None:
    description = (
        "Apply causal inference and experimentation to pricing, monetization and unit "
        "economics to inform business decisions."
    )
    individual = _job(title="Staff Data Scientist", description_clean=description)
    for stream in streams:
        item = _evaluate(stream, individual)
        assert item.evaluation.decision == FilterDecision.REVIEW
        assert "role:required_scope_unconfirmed" in item.evaluation.risks
        assert item.evaluation.tier != JobTier.A

    science = next(stream for stream in streams if stream.id == "data-science-management")
    project_lead = _evaluate(
        science,
        _job(
            title="Staff Data Scientist",
            description_clean=(
                f"{description} Lead cross-functional projects and manage stakeholders. "
                "This role has no direct reports."
            ),
        ),
    )
    assert project_lead.evaluation.decision == FilterDecision.PASS
    assert project_lead.evaluation.tier == JobTier.A
    assert "role:required_scope_unconfirmed" not in project_lead.evaluation.risks


@pytest.mark.parametrize(
    "solo_scope",
    ["solo analysis only", "individual analysis only", "without stakeholder interaction"],
)
def test_prolonged_solo_analysis_cannot_be_a_target_despite_manager_title(
    streams, solo_scope,
) -> None:
    record = _job(
        title="Analytics Manager",
        description_clean=(
            f"The role requires {solo_scope}. Analyze pricing, monetization and "
            "experimentation to inform business decisions."
        ),
    )
    for stream in streams:
        item = _evaluate(stream, record)
        assert item.evaluation.decision == FilterDecision.REVIEW
        assert f"desc_blocker:{solo_scope}" in item.evaluation.risks
        assert item.evaluation.tier != JobTier.A


def test_confirmed_other_working_language_rejects_in_every_stream(streams) -> None:
    record = _job(source_metadata={"working_language": "Portuguese"})
    for stream in streams:
        item = _evaluate(stream, record)
        assert item.evaluation.decision == FilterDecision.REJECT
        assert "language:working_language:Portuguese" in item.evaluation.blocker_signals
        same_role = _evaluate(stream, _job())
        assert item.evaluation.fit_score == same_role.evaluation.fit_score
        assert item.evaluation.tier == same_role.evaluation.tier


def test_unknown_english_language_stays_reviewable_with_real_rules(streams) -> None:
    record = _job(source_metadata={})
    items = [_evaluate(stream, record) for stream in streams]
    assert all(item.evaluation.decision == FilterDecision.REVIEW for item in items)
    assert all("language:working_language_unconfirmed" in item.evaluation.risks for item in items)
    product = next(item for item in items if item.profile_id == "product-management")
    assert product.evaluation.fit_score > 0
    passing = _evaluate(
        next(stream for stream in streams if stream.id == "product-management"), _job(),
    )
    assert product.evaluation.fit_score == passing.evaluation.fit_score
    assert product.evaluation.tier == passing.evaluation.tier


@pytest.mark.parametrize("country", ["Brazil", "Brasil", "BR"])
def test_remote_brazil_aliases_are_eligible(streams, country) -> None:
    product = next(stream for stream in streams if stream.id == "product-management")
    item = _evaluate(product, _job(location_text=country))
    assert item.evaluation.decision == FilterDecision.PASS
    assert item.evaluation.blocker_signals == []


@pytest.mark.parametrize("city", ["São Paulo, Brazil", "Sao Paulo, Brazil"])
@pytest.mark.parametrize("mode", ["On-site", "Hybrid"])
def test_sao_paulo_office_roles_are_eligible(streams, city, mode) -> None:
    product = next(stream for stream in streams if stream.id == "product-management")
    item = _evaluate(product, _job(location_text=city, remote_text=mode))
    assert item.evaluation.decision == FilterDecision.PASS
    assert item.evaluation.blocker_signals == []


@pytest.mark.parametrize(
    "requirement",
    ["Must reside in Brazil.", "Must be based in Brazil.", "Must live in Brazil."],
)
def test_mandatory_brazil_residence_is_compatible(streams, requirement) -> None:
    product = next(stream for stream in streams if stream.id == "product-management")
    item = _evaluate(product, _job(description_clean=f"{requirement} Own product discovery."))
    assert item.evaluation.decision == FilterDecision.PASS
    assert item.evaluation.blocker_signals == []


@pytest.mark.parametrize(
    "requirement",
    [
        "Must reside in Germany.", "Must be based in the United States.",
        "Must live in Canada.", "US only.", "EU only.",
        "US work authorization required.",
    ],
)
def test_explicit_foreign_constraints_reject_in_every_stream(streams, requirement) -> None:
    record = _job(
        location_text="Worldwide", remote_text="Global remote",
        description_clean=f"{requirement} Contractor in an async distributed team.",
    )
    for stream in streams:
        item = _evaluate(stream, record)
        assert item.evaluation.decision == FilterDecision.REJECT
        assert item.evaluation.tier == JobTier.C
        same_role = _evaluate(stream, _job(
            location_text="Worldwide", remote_text="Global remote",
            description_clean="Contractor in an async distributed team.",
        ))
        assert item.evaluation.fit_score == same_role.evaluation.fit_score


def test_explicit_other_office_city_is_rejected_in_every_stream(streams) -> None:
    record = _job(location_text="Rio de Janeiro, Brazil", remote_text="Hybrid")
    assert all(
        _evaluate(stream, record).evaluation.decision == FilterDecision.REJECT
        for stream in streams
    )


def test_unknown_office_city_and_portuguese_requirement_remain_reviewable(streams) -> None:
    record = _job(
        location_text="Brazil", remote_text="On-site", source_metadata={},
        description_clean="Own product discovery. Portuguese required.",
    )
    for stream in streams:
        item = _evaluate(stream, record)
        assert item.evaluation.decision == FilterDecision.REVIEW
        assert "geography:onsite_location_unknown" in item.evaluation.risks
        assert "review_flag:portuguese required" in item.evaluation.risks
        assert item.evaluation.blocker_signals == []


def test_low_fit_unknown_language_is_visible_in_wide_export(streams, tmp_path) -> None:
    record = _job(
        title="Operations Associate", description_clean="General business coordination.",
        source_metadata={},
    )
    stream, classification = RoleClassifier(with_other_stream(
        streams, load_yaml_mapping(CONFIG_DIR / "rules.yaml"),
    )).classify(record)
    item = _evaluate(stream, record)
    item.role_classification = classification
    items = [item]
    assert all(item.evaluation.tier == JobTier.C for item in items)
    assert all(item.evaluation.decision == FilterDecision.REVIEW for item in items)
    database = Database("sqlite:///:memory:")
    database.create_schema()
    with database.session() as session:
        repository = JobRepository(session)
        repository.upsert_evaluations(items)
        session.commit()
        path = repository.export_shortlisted_csv(
            tmp_path / "wide-search.csv", wide=True,
            active_profile_versions={stream.id: stream.version},
        )
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1
    assert rows[0]["title"] == "Operations Associate"
    assert rows[0]["role_label"] == "other"
    assert rows[0]["evaluation_state"] == "unscored"
    assert rows[0]["tier"] == rows[0]["fit_score"] == ""
    assert rows[0]["filter_decision"] == "review"
    assert "language:working_language_unconfirmed" in rows[0]["risks"]


def test_closed_vacancy_remains_a_hard_rejection(streams) -> None:
    record = _job(status=JobStatus.CLOSED)
    assert all(
        _evaluate(stream, record).evaluation.decision == FilterDecision.REJECT
        for stream in streams
    )


def test_explicitly_incomplete_description_cannot_receive_pass_or_a(streams) -> None:
    product = next(stream for stream in streams if stream.id == "product-management")
    record = _job(
        location_text="Worldwide", remote_text="Global remote",
        source_metadata={"working_language": "English", "description_complete": False},
    )
    item = _evaluate(product, record)
    assert item.evaluation.decision == FilterDecision.REVIEW
    assert item.evaluation.tier == JobTier.B
    assert item.evaluation.fit_score > 0
    assert "incomplete_description" in item.evaluation.risks
    assert item.evaluation.blocker_signals == []


def test_explicitly_complete_description_remains_passing(streams) -> None:
    product = next(stream for stream in streams if stream.id == "product-management")
    record = _job(
        location_text="Worldwide", remote_text="Global remote",
        source_metadata={"working_language": "English", "description_complete": True},
    )
    item = _evaluate(product, record)
    assert item.evaluation.decision == FilterDecision.PASS
    assert item.evaluation.tier == JobTier.A
    assert "incomplete_description" not in item.evaluation.risks


def test_missing_description_completeness_metadata_keeps_legacy_behavior(streams) -> None:
    product = next(stream for stream in streams if stream.id == "product-management")
    item = _evaluate(product, _job())
    assert item.evaluation.decision == FilterDecision.PASS
    assert "incomplete_description" not in item.evaluation.risks
