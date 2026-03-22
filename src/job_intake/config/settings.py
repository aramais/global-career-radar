from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import re
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


@dataclass(slots=True)
class LLMConfig:
    enabled: bool = False
    provider: str = "openai"
    model: str = "gpt-5-mini"
    api_key_env: str = "OPENAI_API_KEY"
    prompt_path: str = "config/llm_prompt.txt"
    max_description_chars: int = 6000


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
    telegram = TelegramConfig(**raw.get("telegram", {}))
    llm_raw = raw.get("llm", {})
    llm = LLMConfig(
        **{
            **llm_raw,
            "prompt_path": str((config_path.parent.parent / llm_raw.get("prompt_path", "config/llm_prompt.txt")).resolve()),
        }
    )
    return AppConfig(
        database_url=raw.get("database_url", "sqlite:///data/job_intake.db"),
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
    )


def load_yaml_mapping(path: str | Path) -> dict[str, Any]:
    return _read_yaml(Path(path).resolve())
