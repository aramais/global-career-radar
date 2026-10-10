import pytest

from job_intake.config.settings import PrefilterConfig


@pytest.mark.parametrize("field,value", [
    ("enabled", "true"), ("enabled", 1), ("provider", "jev"), ("provider", "anthropic"),
    ("screen_model", "../model"), ("verify_model", "model?key=secret"),
    ("api_key_env", "KEY=secret"), ("cache_dir", ""), ("max_source_chars", True),
    ("max_source_chars", 255), ("max_output_tokens", 255), ("request_timeout", 0),
    ("max_retries", 6), ("retry_backoff", float("nan")),
    ("screen_reasoning_effort", "other"), ("verify_reasoning_effort", "other"),
    ("base_url", "https://user:secret@example.com/api"),
])
def test_invalid_prefilter_settings_fail_before_any_request(field, value):
    with pytest.raises(ValueError, match="prefilter"):
        PrefilterConfig(**{field: value})


def test_prefilter_verification_requires_an_independent_model():
    with pytest.raises(ValueError, match="different model"):
        PrefilterConfig(screen_model="same", verify_model="same")


def test_prefilter_default_specs_have_short_separate_stages():
    config = PrefilterConfig()
    assert config.enabled is False
    screen, verify = config.resolved_stage("screen"), config.resolved_stage("verify")
    assert screen.model == "gemini-3.5-flash-lite"
    assert verify.model == "gemini-3.8-flash"
    assert screen.reasoning_effort == "minimal" and verify.reasoning_effort == "low"
    assert screen.max_output_tokens == verify.max_output_tokens == 1536
    with pytest.raises(ValueError, match="screen or verify"):
        config.resolved_stage("other")
