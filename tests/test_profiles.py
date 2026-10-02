import pytest

from job_intake.profiles import load_streams


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
