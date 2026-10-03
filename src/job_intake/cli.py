from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path
from typing import Annotated

import typer

from job_intake.config.settings import (
    ANNOTATION_PROVIDER_DEFAULTS,
    SourceDefinition,
    load_app_config,
    load_yaml_mapping,
)
from job_intake.crm.cli import app as crm_app
from job_intake.pipeline import JobIntakePipeline, build_pipeline
from job_intake.profiles import load_streams

app = typer.Typer(add_completion=False, help="Personal multi-profile job search")
app.add_typer(crm_app, name="crm")

_ANNOTATION_PRESETS = {
    "budget-jev": {
        "provider": "openai",
        "api_key_env": "OPENAI_API_KEY",
        "extract_provider": "openai",
        "review_provider": "jev",
        "extract_model": "gpt-6-luna",
        "review_model": "jev-1.13.0",
        "extract_reasoning_effort": "none",
        "review_reasoning_effort": "low",
    },
    "balanced": {
        "provider": "gemini",
        "api_key_env": "GEMINI_API_KEY",
        "extract_provider": "gemini",
        "review_provider": "gemini",
        "extract_model": "gemini-3.5-flash-lite",
        "review_model": "gemini-3.8-flash",
        "extract_reasoning_effort": "minimal",
        "review_reasoning_effort": "low",
    },
}


@app.command()
def run(config: str = typer.Option("config/settings.yaml", help="Path to app config YAML")) -> None:
    pipeline = build_pipeline(config)
    result = pipeline.run()
    typer.echo(
        f"Run completed: ingested={result['ingested']} persisted={result['persisted']} "
        f"evaluations={result['evaluations']} alerts={result['alerts']} "
        f"source_errors={result['source_errors']} record_errors={result['record_errors']}"
    )
    for error in result["errors"]:
        typer.echo(error, err=True)
    if result["source_errors"] or result["record_errors"] or result["alert_errors"]:
        raise typer.Exit(code=1)


@app.command("profiles")
def profiles(config: str = typer.Option("config/settings.yaml", help="App config YAML")) -> None:
    """List enabled search profiles and the queries they generate."""
    settings = load_app_config(config)
    streams = load_streams(
        load_yaml_mapping(settings.rules_path), load_yaml_mapping(settings.search_profiles_path)
    )
    for stream in streams:
        typer.echo(f"{stream.id}: {stream.name} (version {stream.version[:8]})")
        typer.echo(f"  Keywords: {', '.join(stream.keywords)}")


@app.command("import-email")
def import_email(
    directory: Annotated[
        Path, typer.Argument(help="Directory containing selected .eml job emails")
    ],
    config: str = typer.Option("config/settings.yaml", help="App config YAML"),
    max_messages: int = typer.Option(100, min=1, max=1000, help="Maximum messages per import"),
) -> None:
    """Import selected job emails offline and evaluate them in all enabled profiles."""
    _import_email_source(
        SourceDefinition(
            name="email-vacancies",
            type="email_files",
            params={
                "directory": str(directory.expanduser().absolute()),
                "max_messages": max_messages,
            },
        ),
        config,
    )


@app.command("import-gmail")
def import_gmail(
    snapshot_file: Annotated[
        Path, typer.Argument(help="JSON snapshot from connected Gmail message reads")
    ],
    config: str = typer.Option("config/settings.yaml", help="App config YAML"),
) -> None:
    """Import a Gmail connector snapshot offline into all enabled search profiles."""
    _import_email_source(
        SourceDefinition(
            name="email-vacancies",
            type="gmail_snapshot",
            params={"snapshot_file": str(snapshot_file.expanduser().absolute())},
        ),
        config,
    )


def _import_email_source(source: SourceDefinition, config: str) -> None:
    settings = load_app_config(config)
    settings = replace(
        settings,
        sources=[source],
        llm=replace(settings.llm, enabled=False),
        telegram=replace(settings.telegram, enabled=False),
    )
    result = JobIntakePipeline(settings).run()
    typer.echo(
        f"Email import completed: ingested={result['ingested']} persisted={result['persisted']} "
        f"evaluations={result['evaluations']} source_errors={result['source_errors']} "
        f"record_errors={result['record_errors']}"
    )
    for error in result["errors"]:
        typer.echo(error, err=True)
    if result["source_errors"] or result["record_errors"]:
        raise typer.Exit(code=1)


@app.command("reevaluate")
def reevaluate(config: str = typer.Option("config/settings.yaml", help="App config YAML")) -> None:
    """Re-score saved vacancies offline without fetching, AI calls or notifications."""
    result = build_pipeline(config).reevaluate_saved()
    typer.echo(
        f"Reevaluated {result['persisted']} jobs, {result['evaluations']} profile evaluations"
    )
    for error in result["errors"]:
        typer.echo(error, err=True)
    if result["record_errors"]:
        raise typer.Exit(code=1)


@app.command("annotate")
def annotate(
    config: str = typer.Option("config/settings.yaml", help="App config YAML"),
    ai: bool = typer.Option(False, "--ai", help="Extract and independently review with models"),
    limit: int | None = typer.Option(None, min=1, help="Maximum saved vacancies; default all"),
    provider: str | None = typer.Option(None, help="Legacy shared provider; default from settings"),
    preset: str | None = typer.Option(None, help="Model preset: budget-jev or balanced"),
    extract_provider: str | None = typer.Option(None, help="Extraction provider"),
    review_provider: str | None = typer.Option(None, help="Independent review provider"),
    extract_model: str | None = typer.Option(None, help="Extraction model"),
    review_model: str | None = typer.Option(None, help="Different model for source review"),
    extract_api_key_env: str | None = typer.Option(None, help="Extraction key environment name"),
    review_api_key_env: str | None = typer.Option(None, help="Review key environment name"),
    extract_base_url: str | None = typer.Option(None, help="Extraction API base URL"),
    review_base_url: str | None = typer.Option(None, help="Review API base URL"),
    extract_reasoning_effort: str | None = typer.Option(None, help="Extraction reasoning effort"),
    review_reasoning_effort: str | None = typer.Option(None, help="Review reasoning effort"),
) -> None:
    """Annotate saved source texts and rescore profiles, without fetching or sending alerts."""
    try:
        settings = load_app_config(config)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    changes = {"enabled": True, "ai_enabled": ai}
    if preset is not None:
        if preset not in _ANNOTATION_PRESETS:
            raise typer.BadParameter("preset must be budget-jev or balanced")
        if provider is not None:
            raise typer.BadParameter("Use either --provider or --preset")
        changes.update(_ANNOTATION_PRESETS[preset])
        for stage in ("extract", "review"):
            changes[f"{stage}_api_key_env"] = None
            changes[f"{stage}_base_url"] = None
    if provider is not None:
        if provider not in ANNOTATION_PROVIDER_DEFAULTS:
            raise typer.BadParameter("provider must name a supported annotation provider")
        changes["provider"] = provider
        switching = settings.annotation.provider != provider or any(
            settings.annotation.resolved_stage(stage).provider != provider
            for stage in ("extract", "review")
        )
        for stage in ("extract", "review"):
            changes[f"{stage}_provider"] = None
            changes[f"{stage}_api_key_env"] = None
            changes[f"{stage}_base_url"] = None
            changes[f"{stage}_reasoning_effort"] = None
        if provider == "openai" and switching:
            changes.update(
                api_key_env="OPENAI_API_KEY", extract_model="gpt-5-mini", review_model="gpt-5"
            )
        elif provider == "gemini" and switching:
            changes.update(
                api_key_env="GEMINI_API_KEY",
                extract_model="gemini-2.5-flash-lite",
                review_model="gemini-2.5-pro",
            )
        elif switching:
            changes["api_key_env"] = ANNOTATION_PROVIDER_DEFAULTS[provider][0]
            if extract_model is None or review_model is None:
                raise typer.BadParameter(
                    "Switching the shared provider requires --extract-model and --review-model"
                )
    overrides = {
        "extract_provider": extract_provider,
        "review_provider": review_provider,
        "extract_model": extract_model,
        "review_model": review_model,
        "extract_api_key_env": extract_api_key_env,
        "review_api_key_env": review_api_key_env,
        "extract_base_url": extract_base_url,
        "review_base_url": review_base_url,
        "extract_reasoning_effort": extract_reasoning_effort,
        "review_reasoning_effort": review_reasoning_effort,
    }
    for name, value in overrides.items():
        if value is not None:
            changes[name] = value
    try:
        annotation = replace(settings.annotation, **changes)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    if ai:
        missing_envs = dict.fromkeys(
            annotation.resolved_stage(stage).api_key_env
            for stage in ("extract", "review")
            if not os.getenv(annotation.resolved_stage(stage).api_key_env, "").strip()
        )
        if missing_envs:
            for env_name in missing_envs:
                typer.echo("Set " + env_name + " locally before running --ai.", err=True)
            raise typer.Exit(code=1)
    settings = replace(
        settings,
        annotation=annotation,
        llm=replace(settings.llm, enabled=False),
        telegram=replace(settings.telegram, enabled=False),
    )
    result = JobIntakePipeline(settings).reevaluate_saved(use_ai=ai, limit=limit)
    typer.echo(
        f"Annotated {result['persisted']} jobs, {result['evaluations']} profile evaluations. "
        f"Extraction calls={result['annotation_extraction_calls']}, "
        f"review calls={result['annotation_review_calls']}, "
        f"cache hits={result['annotation_cache_hits']}, errors={result['annotation_errors']}"
    )
    for error in result["errors"]:
        typer.echo(error, err=True)
    if result["record_errors"] or result["annotation_errors"]:
        raise typer.Exit(code=1)


@app.command()
def digest(
    config: str = typer.Option("config/settings.yaml", help="Path to app config YAML"),
    hours: int = typer.Option(24, help="Digest lookback window in hours"),
) -> None:
    pipeline = build_pipeline(config)
    typer.echo(pipeline.send_daily_digest(hours=hours))


@app.command("export-csv")
def export_csv(
    config: str = typer.Option("config/settings.yaml", help="Path to app config YAML"),
    output: str = typer.Option("data/shortlisted_jobs.csv", help="Export target CSV path"),
    profile: str | None = typer.Option(None, help="Enabled profile ID"),
    include_rejected: bool = typer.Option(False, help="Include hard rejected vacancies"),
    shortlist: bool = typer.Option(
        False, help="Export A/B tiers only; default includes low scores"
    ),
) -> None:
    pipeline = build_pipeline(config)
    path = pipeline.export_csv(
        Path(output).resolve(),
        profile_id=profile,
        include_rejected=include_rejected,
        shortlist=shortlist,
    )
    typer.echo(f"Exported vacancies to {path}")


@app.command("render-html")
def render_html(
    config: str = typer.Option("config/settings.yaml", help="Path to app config YAML"),
    output: str = typer.Option("data/review.html", help="HTML report target"),
    limit: int = typer.Option(100, help="Maximum number of recent rows to render"),
    profile: str | None = typer.Option(None, help="Initial profile filter"),
) -> None:
    pipeline = build_pipeline(config)
    path = pipeline.render_html(Path(output).resolve(), limit=limit, profile_id=profile)
    typer.echo(f"Rendered review report to {path}")


@app.command("prune")
def prune(
    config: str = typer.Option("config/settings.yaml", help="Path to app config YAML"),
    days: int = typer.Option(90, help="Delete matching jobs not seen for this many days"),
    tiers: str = typer.Option("C", help="Comma-separated tier(s) to prune; default C"),
    vacuum: bool = typer.Option(False, "--vacuum/--no-vacuum", help="Run VACUUM after pruning"),
) -> None:
    pipeline = build_pipeline(config)
    tier_tuple = tuple(t.strip() for t in tiers.split(",") if t.strip()) or ("C",)
    removed = pipeline.prune(older_than_days=days, tiers=tier_tuple, do_vacuum=vacuum)
    typer.echo(f"Pruned {removed} job(s) older than {days}d, tiers={list(tier_tuple)}")


@app.command("feedback")
def feedback(
    job_uid: str = typer.Argument(..., help="Job UID"),
    label: str = typer.Argument(..., help="feedback label such as false_positive"),
    note: str = typer.Option("", help="Optional note"),
    config: str = typer.Option("config/settings.yaml", help="Path to app config YAML"),
) -> None:
    pipeline = build_pipeline(config)
    pipeline.add_feedback(job_uid, label, note)
    typer.echo(f"Stored feedback for {job_uid}")
