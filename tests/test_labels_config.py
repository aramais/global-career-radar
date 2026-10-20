from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from job_intake.config.settings import (
    ANNOTATION_PROVIDER_DEFAULTS,
    LabelingConfig,
    LabelingEscalationConfig,
    load_app_config,
)


def test_default_labeling_is_disabled_and_models_resolve_independently():
    config = LabelingConfig()
    assert not config.enabled and not config.ai_enabled
    assert (config.mode, config.tier_source) == ("shadow", "score")
    assert config.resolved_stage("tag").model == "gemini-3.5-flash-lite"
    assert config.resolved_stage("escalate").model == "gemini-3.8-flash"


@pytest.mark.parametrize("provider", ANNOTATION_PROVIDER_DEFAULTS)
def test_labeling_reuses_all_annotation_provider_validations(provider):
    # Jev's existing adapter is a review-only verifier, not a generative tagger.
    tag_provider = "gemini" if provider == "jev" else provider
    kwargs = {"provider": tag_provider, "escalate_provider": provider,
              "api_key_env": "TEST_API_KEY"}
    if provider == "jev":
        kwargs["escalate_model"] = "jev-1.13.0"
    if provider == "openai_compatible":
        kwargs.update(
            base_url="https://example.test/v1", escalate_base_url="https://example.test/v1"
        )
    config = LabelingConfig(**kwargs)
    assert config.resolved_stage("tag").provider == tag_provider
    assert config.resolved_stage("escalate").provider == provider
    assert config.resolved_stage("tag").api_key_env == "TEST_API_KEY"


@pytest.mark.parametrize(
    "changes",
    [
        {"enabled": 1},
        {"ai_enabled": "false"},
        {"refresh_stale": 1},
        {"mode": "unknown"},
        {"tier_source": "llm"},
        {"final_policy": "mean"},
        {"max_source_chars": True},
        {"max_source_chars": 255},
        {"provider": "invalid"},
        {"tag_model": "same", "escalate_model": "same"},
        {"api_key_env": "secret-value"},
        {"base_url": "https://user:pass@example.test"},
        {"cache_dir": " "},
        {"max_retries": -1},
        {"max_output_tokens": 0},
        {"request_timeout": float("inf")},
        {"escalation": {"enabled": 1}},
        {"escalation": {"max_share_warning": float("nan")}},
    ],
)
def test_invalid_labeling_config_refuses_before_runtime(changes):
    with pytest.raises(ValueError):
        LabelingConfig(**changes)


def test_custom_stage_endpoints_keys_and_nested_replace():
    config = LabelingConfig(
        provider="openai",
        tag_model="small",
        escalate_provider="anthropic",
        escalate_model="large",
        api_key_env="FIRST_KEY",
        escalate_api_key_env="SECOND_KEY",
        base_url="https://first.test/v1",
        escalate_base_url="https://second.test/v1",
        escalation={"enabled": False, "max_share_warning": 0.2},
    )
    assert config.resolved_stage("tag").base_url == "https://first.test/v1"
    assert config.resolved_stage("escalate").api_key_env == "SECOND_KEY"
    assert not config.escalation.enabled
    assert replace(config, enabled=True).escalation == LabelingEscalationConfig(False, 0.2)


def test_yaml_paths_resolve_at_project_root_and_old_configs_stay_disabled(tmp_path):
    folder = tmp_path / "config"
    folder.mkdir()
    path = folder / "settings.yaml"
    path.write_text("{}")
    config = load_app_config(path)
    assert not config.labeling.enabled
    assert Path(config.labeling.rubric_path) == tmp_path / "config/labeling_rubric.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "labeling": {
                    "enabled": True,
                    "mode": "labels",
                    "cache_dir": "scratch/cache",
                    "escalation": {"enabled": False},
                }
            }
        )
    )
    config = load_app_config(path)
    assert config.labeling.enabled and config.labeling.mode == "labels"
    assert Path(config.labeling.cache_dir) == tmp_path / "scratch/cache"
    assert not config.labeling.escalation.enabled
