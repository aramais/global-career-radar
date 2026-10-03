from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


@dataclass(slots=True)
class SourceDefinition:
    name: str
    type: str
    enabled: bool = True
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class TelegramConfig:
    enabled: bool = False
    bot_token_env: str = "TELEGRAM_BOT_TOKEN"
    chat_id_env: str = "TELEGRAM_CHAT_ID"
    instant_a_tier: bool = True
    daily_digest_enabled: bool = True
    alert_dedup_hours: float = 24.0


@dataclass(slots=True)
class LLMConfig:
    enabled: bool = False
    provider: str = "openai"
    model: str = "gpt-5-mini"
    api_key_env: str = "OPENAI_API_KEY"
    prompt_path: str = "config/llm_prompt.txt"
    max_description_chars: int = 6000
    reasoning_effort: str = "minimal"
    max_output_tokens: int = 400
    request_timeout: float = 30.0
    semantic_score_min: float = -3.0
    semantic_score_max: float = 6.0
    strip_boilerplate: bool = True
    skip_high_confidence: bool = False
    high_confidence_margin: float = 4.0
    batch_enabled: bool = False
    batch_completion_window: str = "24h"
    batch_poll_interval: float = 30.0
    batch_max_wait: float = 86400.0


@dataclass(slots=True)
class CRMConfig:
    team_interview_goal: int = 4
    weekly_time_budget_hours: float = 10.0

    def __post_init__(self) -> None:
        if (
            isinstance(self.team_interview_goal, bool)
            or not isinstance(self.team_interview_goal, int)
            or self.team_interview_goal < 1
        ):
            raise ValueError("crm.team_interview_goal must be a positive integer")
        if (
            isinstance(self.weekly_time_budget_hours, bool)
            or not isinstance(self.weekly_time_budget_hours, (int, float))
            or not math.isfinite(self.weekly_time_budget_hours)
            or self.weekly_time_budget_hours <= 0
        ):
            raise ValueError("crm.weekly_time_budget_hours must be positive")


@dataclass(slots=True)
class AnnotationConfig:
    enabled: bool = True
    ai_enabled: bool = False
    provider: str = "gemini"
    extract_model: str = "gemini-2.5-flash-lite"
    review_model: str = "gemini-2.5-pro"
    api_key_env: str = "GEMINI_API_KEY"
    cache_dir: str = "data/local/annotations"
    chunk_chars: int = 6000
    max_output_tokens: int = 4096
    request_timeout: float = 60.0
    max_retries: int = 2
    retry_backoff: float = 1.0
    reasoning_effort: str = "low"

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool or type(self.ai_enabled) is not bool:
            raise ValueError("annotation.enabled and ai_enabled must be Boolean")
        if self.provider not in {"gemini", "openai"}:
            raise ValueError("annotation.provider must be gemini or openai")
        if not all(
            isinstance(model, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", model)
            for model in (self.extract_model, self.review_model)
        ):
            raise ValueError("annotation extraction and review models must be specified")
        if self.extract_model.strip() == self.review_model.strip():
            raise ValueError("annotation review must use a different model from extraction")
        if not isinstance(self.api_key_env, str) or not re.fullmatch(
            r"[A-Z_][A-Z0-9_]*", self.api_key_env
        ):
            raise ValueError("annotation.api_key_env must name an environment variable")
        for name in ("chunk_chars", "max_output_tokens"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 256:
                raise ValueError(f"annotation.{name} must be an integer >= 256")
        if (
            isinstance(self.max_retries, bool)
            or not isinstance(self.max_retries, int)
            or not 0 <= self.max_retries <= 5
        ):
            raise ValueError("annotation.max_retries must be an integer from 0 to 5")
        for name in ("request_timeout", "retry_backoff"):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value <= 0
            ):
                raise ValueError(f"annotation.{name} must be positive and finite")


@dataclass(slots=True)
class AppConfig:
    database_url: str
    log_level: str
    rules_path: Path
    search_profiles_path: Path
    company_watchlist_path: Path
    export_dir: Path
    sources: list[SourceDefinition]
    telegram: TelegramConfig
    llm: LLMConfig
    crm: CRMConfig = field(default_factory=CRMConfig)
    annotation: AnnotationConfig = field(default_factory=AnnotationConfig)


def _read_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Expected mapping in {path}")
    return data


ENV_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)(?::-([^}]+))?\}")


def _expand_env(value: Any) -> Any:
    if isinstance(value, str):

        def replace(match: re.Match[str]) -> str:
            name = match.group(1)
            default = match.group(2) or ""
            return os.getenv(name, default)

        return ENV_PATTERN.sub(replace, value)
    if isinstance(value, list):
        return [_expand_env(item) for item in value]
    if isinstance(value, dict):
        return {key: _expand_env(val) for key, val in value.items()}
    return value


def load_app_config(path: str | Path) -> AppConfig:
    config_path = Path(path).resolve()
    load_dotenv(config_path.parent.parent / ".env")
    raw = _expand_env(_read_yaml(config_path))

    sources = [
        SourceDefinition(
            name=item["name"],
            type=item["type"],
            enabled=item.get("enabled", True),
            params=item.get("params", {}),
        )
        for item in raw.get("sources", [])
    ]
    for source in sources:
        if source.type == "dailyremote" and source.params.get("cookies_file"):
            source.params["cookies_file"] = str(
                (
                    config_path.parent.parent / Path(source.params["cookies_file"]).expanduser()
                ).resolve()
            )
        if source.type == "email_files" and source.params.get("directory"):
            source.params["directory"] = str(
                (
                    config_path.parent.parent / Path(source.params["directory"]).expanduser()
                ).absolute()
            )
        if source.type == "gmail_snapshot" and source.params.get("snapshot_file"):
            source.params["snapshot_file"] = str(
                (
                    config_path.parent.parent / Path(source.params["snapshot_file"]).expanduser()
                ).absolute()
            )
        if source.type == "watchlist" and "watchlist_path" in source.params:
            source.params["watchlist_path"] = str(
                (config_path.parent.parent / source.params["watchlist_path"]).resolve()
            )
    telegram = TelegramConfig(**raw.get("telegram", {}))
    llm_raw = raw.get("llm", {})
    llm = LLMConfig(
        **{
            **llm_raw,
            "prompt_path": str(
                (
                    config_path.parent.parent / llm_raw.get("prompt_path", "config/llm_prompt.txt")
                ).resolve()
            ),
        }
    )
    annotation_raw = raw.get("annotation", {})
    annotation = AnnotationConfig(
        **{
            **annotation_raw,
            "cache_dir": str(
                (
                    config_path.parent.parent
                    / annotation_raw.get("cache_dir", "data/local/annotations")
                ).resolve()
            ),
        }
    )
    return AppConfig(
        database_url=raw.get("database_url", "sqlite:///data/local/job_intake.db"),
        log_level=raw.get("log_level", "INFO"),
        rules_path=(config_path.parent / raw.get("rules_path", "rules.yaml")).resolve(),
        search_profiles_path=(
            config_path.parent / raw.get("search_profiles_path", "search_profiles.yaml")
        ).resolve(),
        company_watchlist_path=(
            config_path.parent / raw.get("company_watchlist_path", "company_watchlist.yaml")
        ).resolve(),
        export_dir=(config_path.parent.parent / raw.get("export_dir", "data")).resolve(),
        sources=sources,
        telegram=telegram,
        llm=llm,
        crm=CRMConfig(**raw.get("crm", {})),
        annotation=annotation,
    )


def load_yaml_mapping(path: str | Path) -> dict[str, Any]:
    return _read_yaml(Path(path).resolve())
