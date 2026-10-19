import pytest

from job_intake import profiles
from job_intake.profiles import (
    load_streams,
    profile_query_order,
    stale_version_reasons,
    taxonomy_version,
)


def test_original_single_profile_configuration_is_supported():
    streams = load_streams({}, {"title_weights": {"analyst": 8}})
    assert len(streams) == 1
    assert streams[0].id == "default"
    assert streams[0].scoring.title_weights == {"analyst": 8}


def test_profile_version_changes_when_criteria_change_but_not_query_order():
    config = {
        "streams": [
            {
                "id": "product",
                "keywords": ["A", "B"],
                "context": "Product",
                "scoring": {"title_weights": {"manager": 8}},
            }
        ]
    }
    original = load_streams({}, config)[0].version
    config["streams"][0]["keywords"].reverse()
    assert load_streams({}, config)[0].version == original
    config["streams"][0]["scoring"]["title_weights"]["manager"] = 9
    assert load_streams({}, config)[0].version != original


@pytest.mark.parametrize("streams", [[], [{"id": "x"}, {"id": "x"}], [{"id": "bad id"}]])
def test_invalid_profiles_fail_before_ingestion(streams):
    with pytest.raises(ValueError):
        load_streams({}, {"streams": streams})


def explicit_config():
    return {"streams": [{
        "id": "analytics", "search_queries": ["Analytics Manager remote Brazil"],
        "titles": ["Analytics Manager"], "context": "Confirmed projects and decisions",
        "scoring": {"title_weights": {"Analytics Manager": 8}},
    }]}


def test_query_only_change_does_not_invalidate_role_or_evaluation():
    config = explicit_config()
    old = load_streams({}, config)[0]
    config["streams"][0]["search_queries"].append("Analytics Manager LATAM")
    new = load_streams({}, config)[0]
    assert old.query_version != new.query_version
    assert old.version == new.version
    assert taxonomy_version([old]) == taxonomy_version([new])
    assert stale_version_reasons(old.version_components, new.version_components) == []


def test_classification_prompt_changes_only_prompt_component(monkeypatch):
    config = explicit_config()
    old = load_streams({}, config)[0]
    monkeypatch.setattr(profiles, "ROLE_CLASSIFICATION_PROMPT", "New classifier instructions")
    new = load_streams({}, config)[0]
    assert old.version == new.version
    assert stale_version_reasons(old.version_components, new.version_components) == [
        "version:classification_prompt:changed",
    ]
    assert stale_version_reasons(
        old.version_components, new.version_components, stage="scoring",
    ) == []


def test_scoring_change_has_explicit_reason_without_staling_classification():
    config = explicit_config()
    old = load_streams({}, config)[0]
    config["streams"][0]["scoring"]["title_weights"]["Analytics Manager"] = 9
    new = load_streams({}, config)[0]
    assert old.version != new.version
    assert stale_version_reasons(old.version_components, new.version_components) == [
        "version:scoring:changed",
    ]
    assert stale_version_reasons(
        old.version_components, new.version_components, stage="classification",
    ) == []
    assert stale_version_reasons(None, new.version_components) == [
        "version_components:legacy_unknown",
    ]


@pytest.mark.parametrize("field", ["search_queries", "titles"])
def test_explicit_profile_fields_reject_invalid_values(field):
    config = explicit_config()
    config["streams"][0][field] = "not a list"
    with pytest.raises(ValueError):
        load_streams({}, config)


def test_query_quota_order_round_robins_streams_without_duplicate_requests():
    streams = load_streams({}, {"streams": [
        {"id": "one", "search_queries": ["first", "shared", "third"]},
        {"id": "two", "search_queries": ["second", "shared", "fourth"]},
    ]})
    assert profile_query_order(streams) == ["first", "second", "shared", "third", "fourth"]
