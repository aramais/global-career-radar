from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from job_intake.config.settings import ANNOTATION_PROVIDER_DEFAULTS, AnnotationConfig


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


def test_legacy_config_resolves_both_stages_with_custom_shared_key():
    config = AnnotationConfig(api_key_env="PERSONAL_GEMINI_KEY", reasoning_effort="medium")
    extract = config.resolved_stage("extract")
    review = config.resolved_stage("review")
    assert extract.provider == review.provider == "gemini"
    assert extract.api_key_env == review.api_key_env == "PERSONAL_GEMINI_KEY"
    assert extract.base_url == "https://generativelanguage.googleapis.com/v1beta"
    assert extract.reasoning_effort == review.reasoning_effort == "medium"
    assert extract.model == config.extract_model
    assert review.model == config.review_model
    assert extract.max_retries == config.max_retries
    with pytest.raises(FrozenInstanceError):
        extract.model = "another-model"


@pytest.mark.parametrize("provider", ANNOTATION_PROVIDER_DEFAULTS.keys() - {"openai_compatible"})
def test_explicit_stage_provider_uses_its_default_key_and_endpoint(provider):
    config = AnnotationConfig(
        api_key_env="CUSTOM_SHARED_KEY", review_provider=provider,
        review_model="jev-1.13.0" if provider == "jev" else "reviewer-v1",
    )
    review = config.resolved_stage("review")
    expected_env, expected_url = ANNOTATION_PROVIDER_DEFAULTS[provider]
    assert review.api_key_env == ("CUSTOM_SHARED_KEY" if provider == "gemini" else expected_env)
    assert review.base_url == expected_url
    assert config.resolved_stage("extract").api_key_env == "CUSTOM_SHARED_KEY"


def test_per_stage_overrides_and_common_limits_are_in_resolved_spec():
    config = AnnotationConfig(
        extract_provider="openai", review_provider="jev",
        extract_model="gpt-6-luna", review_model="jev-1.13.0",
        extract_api_key_env="PERSONAL_EXTRACT_KEY", review_api_key_env="PERSONAL_REVIEW_KEY",
        extract_reasoning_effort="none", review_reasoning_effort="high",
        request_timeout=13, max_output_tokens=2048, max_retries=1, retry_backoff=0.2,
        jev_min_probability=0.97, jev_min_confidence=0.9, jev_batch_size=8,
    )
    extract, review = config.resolved_stage("extract"), config.resolved_stage("review")
    assert extract.api_key_env == "PERSONAL_EXTRACT_KEY"
    assert review.api_key_env == "PERSONAL_REVIEW_KEY"
    assert extract.reasoning_effort == "none"
    assert review.reasoning_effort == "high"
    assert (review.request_timeout, review.max_output_tokens, review.max_retries) == (13, 2048, 1)
    assert review.retry_backoff == 0.2
    assert (review.jev_min_probability, review.jev_min_confidence, review.jev_batch_size) == (
        0.97, 0.9, 8,
    )


@pytest.mark.parametrize("field", ["extract_provider", "review_provider"])
@pytest.mark.parametrize("value", ["", "unknown", False, []])
def test_invalid_stage_provider_is_rejected(field, value):
    with pytest.raises(ValueError):
        AnnotationConfig(**{field: value})


@pytest.mark.parametrize("field", ["extract_api_key_env", "review_api_key_env"])
@pytest.mark.parametrize("value", ["", "API_KEY=secret", "lower-key", 123])
def test_invalid_stage_key_environment_name_is_rejected(field, value):
    with pytest.raises(ValueError):
        AnnotationConfig(**{field: value})


@pytest.mark.parametrize("provider", ["openai", "openrouter", "qwen", "anthropic"])
def test_vendor_namespaced_models_are_allowed_outside_gemini(provider):
    config = AnnotationConfig(
        review_provider=provider, review_model="vendor/model-v1:free"
    )
    assert config.resolved_stage("review").model == "vendor/model-v1:free"


@pytest.mark.parametrize("model", ["../model", "vendor/../model", "vendor//model", "model?x=1"])
def test_namespaced_models_cannot_contain_url_or_traversal_payloads(model):
    with pytest.raises(ValueError):
        AnnotationConfig(review_provider="openrouter", review_model=model)


def test_same_model_name_is_independent_across_providers_or_distinct_endpoints():
    AnnotationConfig(
        extract_provider="openai", review_provider="mistral",
        extract_model="shared-name", review_model="shared-name",
    )
    AnnotationConfig(
        extract_provider="openai_compatible", review_provider="openai_compatible",
        extract_model="shared-name", review_model="shared-name",
        extract_base_url="https://extract.example/v1", review_base_url="https://review.example/v1",
    )


def test_endpoint_spelling_does_not_make_identical_models_independent():
    with pytest.raises(ValueError, match="different model"):
        AnnotationConfig(
            provider="openai", extract_model="same", review_model="same",
            extract_base_url="https://API.OPENAI.COM:443/v1/",
            review_base_url="https://api.openai.com/v1",
        )


def test_generic_provider_requires_explicit_endpoint_and_gets_generic_key_default():
    with pytest.raises(ValueError, match="base_url"):
        AnnotationConfig(review_provider="openai_compatible", review_model="reviewer")
    config = AnnotationConfig(
        review_provider="openai_compatible", review_model="reviewer",
        review_base_url="https://llm.example/custom/api/",
    )
    review = config.resolved_stage("review")
    assert review.api_key_env == "LLM_API_KEY"
    assert review.base_url == "https://llm.example/custom/api"


@pytest.mark.parametrize("base_url", [
    "https://user:secret@api.example/v1", "https://api.example/v1?key=secret",
    "https://api.example/v1?", "https://api.example/v1#fragment", "http://api.example/v1",
    "http://localhost.attacker.example/v1", "http://0.0.0.0:8080/v1",
    "file:///tmp/model", "https://", "https://api.example:bad/v1",
    "https://api.example\\@attacker.example/v1", " https://api.example/v1",
    "https://api.example/v1\n", "https://api.example/%20 path", 123,
])
def test_unsafe_api_endpoints_are_rejected_without_echoing_the_endpoint(base_url):
    with pytest.raises(ValueError) as error:
        AnnotationConfig(review_base_url=base_url)
    assert "secret" not in str(error.value)
    assert "attacker.example" not in str(error.value)


@pytest.mark.parametrize("base_url", [
    "http://localhost:8080/v1", "http://127.0.0.1:1234/v1", "http://[::1]:8080/v1",
])
def test_http_local_loopback_endpoints_are_allowed(base_url):
    config = AnnotationConfig(
        review_provider="openai_compatible", review_model="local-reviewer",
        review_base_url=base_url,
    )
    assert config.resolved_stage("review").base_url == base_url


def test_jev_is_review_only():
    with pytest.raises(ValueError, match="only for review"):
        AnnotationConfig(extract_provider="jev", extract_model="jev-1.13.0")
    review = AnnotationConfig(
        review_provider="jev", review_model="jev-1.13.0"
    ).resolved_stage("review")
    assert review.base_url == "https://api.typesafe.ai/v1"
    assert review.api_key_env == "TYPESAFE_API_KEY"


@pytest.mark.parametrize("model", ["gpt-6-luna", "jev", "jev-", "jev-1.13", "jev-arbitrary"])
def test_jev_rejects_mismatched_or_ambiguous_model_names_before_any_client(model):
    with pytest.raises(ValueError, match="Jev"):
        AnnotationConfig(review_provider="jev", review_model=model)


@pytest.mark.parametrize("model", ["jev-1.13.0", "jev-2.0.1", "jev-latest", "jev-preview"])
def test_jev_allows_pinned_versions_and_documented_aliases(model):
    config = AnnotationConfig(review_provider="jev", review_model=model)
    assert config.resolved_stage("review").model == model


@pytest.mark.parametrize("endpoint", [
    "https://api.jev.ai/v1", "https://typesafe.ai/v1", "https://api.typesafe.ai.other/v1",
    "https://api.typesafe.ai/v2", "https://api.typesafe.ai/v1/systemone",
    "http://127.0.0.1:8000/v1",
])
def test_jev_key_cannot_be_routed_to_a_nonofficial_endpoint(endpoint):
    with pytest.raises(ValueError, match="official TypeSafe") as error:
        AnnotationConfig(
            review_provider="jev", review_model="jev-1.13.0", review_base_url=endpoint,
        )
    assert endpoint not in str(error.value)


def test_jev_endpoint_validation_accepts_equivalent_canonical_spelling():
    config = AnnotationConfig(
        review_provider="jev", review_model="jev-1.13.0",
        review_base_url="https://API.TYPESAFE.AI:443/v1/",
    )
    assert config.resolved_stage("review").base_url == "https://api.typesafe.ai/v1"


@pytest.mark.parametrize(("field", "value"), [
    ("jev_min_probability", -0.01), ("jev_min_probability", 1.01),
    ("jev_min_probability", True), ("jev_min_confidence", float("inf")),
    ("jev_min_confidence", float("nan")), ("jev_min_confidence", "0.8"),
    ("jev_batch_size", 0), ("jev_batch_size", 65), ("jev_batch_size", 1.5),
    ("jev_batch_size", True),
    ("extract_reasoning_effort", ""), ("review_reasoning_effort", "unbounded"),
    ("reasoning_effort", False),
])
def test_invalid_stage_reasoning_and_jev_controls_are_rejected(field, value):
    with pytest.raises(ValueError):
        AnnotationConfig(**{field: value})


def test_unknown_stage_is_rejected():
    with pytest.raises(ValueError, match="extract or review"):
        AnnotationConfig().resolved_stage("another")
