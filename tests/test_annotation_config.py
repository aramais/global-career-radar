from __future__ import annotations

import pytest

from job_intake.config.settings import AnnotationConfig


def test_annotation_defaults_are_offline_with_distinct_extraction_and_review_models():
    config = AnnotationConfig()
    assert config.enabled is True
    assert config.ai_enabled is False
    assert config.extract_model != config.review_model
    assert config.max_retries == 2


@pytest.mark.parametrize(("field", "value"), [
    ("enabled", "false"),
    ("ai_enabled", "true"),
    ("ai_enabled", 1),
    ("extract_model", ""),
    ("review_model", None),
    ("review_model", 123),
    ("review_model", "model?key=secret"),
    ("extract_model", "../another-endpoint"),
    ("review_model", "models/model"),
    ("api_key_env", ""),
    ("api_key_env", None),
    ("api_key_env", "KEY=value"),
    ("api_key_env", "MY KEY"),
    ("api_key_env", "0KEY"),
    ("provider", "other-provider"),
    ("max_retries", -1),
    ("max_retries", 6),
    ("max_retries", True),
    ("chunk_chars", 255),
    ("max_output_tokens", 100.5),
    ("request_timeout", 0),
    ("retry_backoff", float("nan")),
])
def test_invalid_annotation_config_fails_before_any_model_request(field, value):
    with pytest.raises(ValueError):
        AnnotationConfig(**{field: value})


def test_identical_models_cannot_perform_independent_review():
    with pytest.raises(ValueError, match="different model"):
        AnnotationConfig(extract_model="same-model", review_model="same-model")
