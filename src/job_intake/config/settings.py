from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass, field
from ipaddress import ip_address
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

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


ANNOTATION_PROVIDER_DEFAULTS = {
    "gemini": ("GEMINI_API_KEY", "https://generativelanguage.googleapis.com/v1beta"),
    "openai": ("OPENAI_API_KEY", "https://api.openai.com/v1"),
    "jev": ("TYPESAFE_API_KEY", "https://api.typesafe.ai/v1"),
    "mistral": ("MISTRAL_API_KEY", "https://api.mistral.ai/v1"),
    "deepseek": ("DEEPSEEK_API_KEY", "https://api.deepseek.com/v1"),
    "qwen": ("DASHSCOPE_API_KEY", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"),
    "zai": ("ZAI_API_KEY", "https://api.z.ai/api/paas/v4"),
    "openrouter": ("OPENROUTER_API_KEY", "https://openrouter.ai/api/v1"),
    "anthropic": ("ANTHROPIC_API_KEY", "https://api.anthropic.com/v1"),
    "openai_compatible": ("LLM_API_KEY", None),
}
ANNOTATION_REASONING_EFFORTS = {"none", "minimal", "low", "medium", "high", "xhigh", "max"}


def _annotation_base_url(value: Any, name: str) -> str:
    message = f"annotation.{name} must be an API URL without credentials, query or fragment"
    if (
        not isinstance(value, str)
        or not value
        or any(char.isspace() or ord(char) < 32 for char in value)
        or any(char in value for char in "\\?#")
    ):
        raise ValueError(message)
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        raise ValueError(message) from None
    if (
        parsed.scheme not in {"https", "http"}
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or "%" in hostname
    ):
        raise ValueError(message)
    if parsed.scheme == "http":
        try:
            is_loopback = ip_address(hostname).is_loopback
        except ValueError:
            is_loopback = hostname.lower() == "localhost"
        if not is_loopback:
            raise ValueError(f"annotation.{name} requires HTTPS except on loopback hosts")
    host = f"[{hostname.lower()}]" if ":" in hostname else hostname.lower()
    if port is not None and port != {"https": 443, "http": 80}[parsed.scheme]:
        host += f":{port}"
    return urlunsplit((parsed.scheme, host, parsed.path.rstrip("/"), "", ""))


@dataclass(frozen=True, slots=True)
class AnnotationStageSpec:
    provider: str
    model: str
    api_key_env: str
    base_url: str
    reasoning_effort: str
    request_timeout: float
    max_output_tokens: int
    max_retries: int
    retry_backoff: float
    jev_min_probability: float
    jev_min_confidence: float
    jev_batch_size: int


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
    extract_provider: str | None = None
    review_provider: str | None = None
    extract_api_key_env: str | None = None
    review_api_key_env: str | None = None
    extract_base_url: str | None = None
    review_base_url: str | None = None
    extract_reasoning_effort: str | None = None
    review_reasoning_effort: str | None = None
    jev_min_probability: float = 0.95
    jev_min_confidence: float = 0.8
    jev_batch_size: int = 16

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool or type(self.ai_enabled) is not bool:
            raise ValueError("annotation.enabled and ai_enabled must be Boolean")
        for name in ("provider", "extract_provider", "review_provider"):
            value = getattr(self, name)
            if value is None and name != "provider":
                continue
            if not isinstance(value, str) or value not in ANNOTATION_PROVIDER_DEFAULTS:
                raise ValueError(f"annotation.{name} must name a supported annotation provider")
        for name in ("api_key_env", "extract_api_key_env", "review_api_key_env"):
            value = getattr(self, name)
            if value is None and name != "api_key_env":
                continue
            if not isinstance(value, str) or not re.fullmatch(r"[A-Z_][A-Z0-9_]*", value):
                raise ValueError(f"annotation.{name} must name an environment variable")
        for name in ("reasoning_effort", "extract_reasoning_effort", "review_reasoning_effort"):
            value = getattr(self, name)
            if value is None and name != "reasoning_effort":
                continue
            if not isinstance(value, str) or value not in ANNOTATION_REASONING_EFFORTS:
                raise ValueError(f"annotation.{name} must name a supported reasoning effort")
        for name in ("extract_base_url", "review_base_url"):
            value = getattr(self, name)
            if value is not None:
                _annotation_base_url(value, name)
        extract = self.resolved_stage("extract")
        review = self.resolved_stage("review")
        for stage, spec in (("extract", extract), ("review", review)):
            pattern = (
                r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}"
                if spec.provider == "gemini"
                else r"[A-Za-z0-9][A-Za-z0-9._:-]*(?:/[A-Za-z0-9][A-Za-z0-9._:-]*)*"
            )
            if (
                not isinstance(spec.model, str)
                or len(spec.model) > 255
                or not re.fullmatch(pattern, spec.model)
            ):
                raise ValueError(f"annotation.{stage}_model must name a safe model ID")
        if extract.provider == "jev":
            raise ValueError("annotation Jev is supported only for review")
        if review.provider == "jev":
            if review.base_url != ANNOTATION_PROVIDER_DEFAULTS["jev"][1]:
                raise ValueError(
                    "annotation Jev review requires the official TypeSafe API endpoint"
                )
            if not re.fullmatch(r"jev-(?:\d+\.\d+\.\d+|latest|preview)", review.model):
                raise ValueError("annotation Jev review_model must name a Jev version or alias")
        if (extract.provider, extract.base_url, extract.model) == (
            review.provider, review.base_url, review.model
        ):
            raise ValueError("annotation review must use a different model from extraction")
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
        for name in ("jev_min_probability", "jev_min_confidence"):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or not 0 <= value <= 1
            ):
                raise ValueError(f"annotation.{name} must be finite and between 0 and 1")
        if (
            isinstance(self.jev_batch_size, bool)
            or not isinstance(self.jev_batch_size, int)
            or not 1 <= self.jev_batch_size <= 64
        ):
            raise ValueError("annotation.jev_batch_size must be an integer from 1 to 64")

    def resolved_stage(self, stage: str) -> AnnotationStageSpec:
        if stage not in {"extract", "review"}:
            raise ValueError("annotation stage must be extract or review")
        provider = getattr(self, f"{stage}_provider") or self.provider
        default_env, default_url = ANNOTATION_PROVIDER_DEFAULTS[provider]
        api_key_env = getattr(self, f"{stage}_api_key_env")
        if api_key_env is None:
            api_key_env = self.api_key_env if provider == self.provider else default_env
        base_url = getattr(self, f"{stage}_base_url")
        if base_url is None:
            base_url = default_url
        base_url = _annotation_base_url(base_url, f"{stage}_base_url")
        return AnnotationStageSpec(
            provider=provider,
            model=getattr(self, f"{stage}_model"),
            api_key_env=api_key_env,
            base_url=base_url,
            reasoning_effort=getattr(self, f"{stage}_reasoning_effort") or self.reasoning_effort,
            request_timeout=self.request_timeout,
            max_output_tokens=self.max_output_tokens,
            max_retries=self.max_retries,
            retry_backoff=self.retry_backoff,
            jev_min_probability=self.jev_min_probability,
            jev_min_confidence=self.jev_min_confidence,
            jev_batch_size=self.jev_batch_size,
        )


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
