"""Offline contract checks for the shipped four-stream personal search configuration."""

import csv
from pathlib import Path

import pytest

from job_intake.config.settings import load_yaml_mapping
from job_intake.models.job import FilterDecision, JobRecord, JobStatus, JobTier
from job_intake.profiles import SearchStream, load_streams
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
        "description_clean": "Own product discovery and roadmap.",
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
        record.description_clean or record.description_raw, item.evaluation,
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
        assert stream.rules.onsite_locations
        assert stream.context and stream.keywords


@pytest.mark.parametrize(
    ("profile_id", "title", "description"),
    [
        ("product-management", "Product Manager", "Own product discovery and roadmap."),
        ("analytics-leadership", "Head of Analytics", "Own pricing and monetization."),
        ("business-analytics", "Business Analyst Manager", "Own decision support and forecasting."),
        (
            "data-science-management", "Data Science Manager",
            "Own team leadership and experimentation.",
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


def test_unknown_english_language_stays_reviewable_with_real_rules(streams) -> None:
    record = _job(source_metadata={})
    items = [_evaluate(stream, record) for stream in streams]
    assert all(item.evaluation.decision == FilterDecision.REVIEW for item in items)
    assert all("language:working_language_unconfirmed" in item.evaluation.risks for item in items)
    product = next(item for item in items if item.profile_id == "product-management")
    assert product.evaluation.fit_score > 0
    assert product.evaluation.tier == JobTier.B


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
        assert item.evaluation.fit_score == 0


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
    items = [_evaluate(stream, record) for stream in streams]
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
            active_profile_versions={stream.id: stream.version for stream in streams},
        )
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 1
    assert rows[0]["title"] == "Operations Associate"
    assert rows[0]["tier"] == "C"
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
