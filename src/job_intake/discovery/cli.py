from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path
from typing import Annotated

import typer

from job_intake.annotation.eval_runner import read_saved_records, sqlite_path
from job_intake.config.settings import load_app_config
from job_intake.discovery.artifacts import (
    load_candidates,
    read_json,
    record_from_snapshot,
    write_json,
    write_text,
)
from job_intake.discovery.planning import build_plan
from job_intake.discovery.providers import PerplexitySearch, ProviderError, PublicJobFetcher
from job_intake.discovery.report import build_report, report_markdown
from job_intake.discovery.service import prepare_labeling, run_discovery

app = typer.Typer(help="Поиск по положительным человеческим примерам и эффективность разметки")


def _fail(error):
    typer.echo(str(error), err=True)
    raise typer.Exit(code=1) from None


def _records(config: str, database: Path | None):
    settings = load_app_config(config)
    if database:
        settings = replace(settings, database_url=f"sqlite:///{database.resolve()}")
    path = sqlite_path(settings.database_url)
    if not path.is_file():
        return {}
    # The established reader validates the actual schema, mode=ro and query_only.
    return dict(read_saved_records(settings.database_url, limit=None, include_archived=True)[1])


@app.command("plan")
def plan_command(
    gold: Annotated[Path, typer.Option(exists=True, dir_okay=False)],
    output: Annotated[Path, typer.Option(help="Новый JSON-план (не заменяет существующий)")],
    holdout: Annotated[Path | None, typer.Option(exists=True, dir_okay=False)] = None,
    feedback: Annotated[Path | None, typer.Option(exists=True, dir_okay=False)] = None,
    max_queries: int = typer.Option(80, min=1, max=500),
    exploration_fraction: float = typer.Option(.2, min=0, max=1),
    config: str = typer.Option("config/settings.yaml"),
    database: Annotated[Path | None, typer.Option(exists=True, dir_okay=False)] = None,
) -> None:
    """Создать план из human-positive примеров без обращения к API; SQLite только читается."""
    try:
        if output.exists():
            raise ValueError("Plan already exists; choose a new output file")
        result = build_plan(gold, holdout_path=holdout, feedback_path=feedback,
                            max_queries=max_queries, exploration_fraction=exploration_fraction,
                            source_records=_records(config, database))
        write_json(output, result, exclusive=True)
        typer.echo(f"План: {output.resolve()}; примеров={len(result['seeds'])}; "
                   f"запросов={len(result['queries'])}. API: 0 USD.")
    except (ValueError, OSError, KeyError, TypeError) as error:
        _fail(error)


@app.command("run")
def run_command(
    plan: Annotated[Path, typer.Option(exists=True, dir_okay=False)],
    output: Annotated[Path, typer.Option(help="Новая папка запуска")],
    budget_usd: Annotated[float, typer.Option(min=.001, help="Общий лимит API-расходов USD")],
    max_concurrency: int = typer.Option(4, min=1, max=16),
    max_details: int = typer.Option(200, min=1, max=1000),
    max_results: int = typer.Option(10, min=1, max=20),
    max_seconds: float = typer.Option(3600, min=1, max=86400),
    search_price_usd: float = typer.Option(.005, min=.000001,
                                         help="USD за один web Search POST; сохраняется в отчёте"),
    semantic_model: Annotated[
        str | None, typer.Option(help="Gemini для смыслового ранжирования")
    ] = None,
    input_per_million: Annotated[float | None, typer.Option(min=0)] = None,
    output_per_million: Annotated[float | None, typer.Option(min=0)] = None,
    config: str = typer.Option("config/settings.yaml"),
    database: Annotated[Path | None, typer.Option(exists=True, dir_okay=False)] = None,
    known_run: Annotated[
        list[Path] | None, typer.Option(help="Прежняя discovery-папка для дедупликации")
    ] = None,
) -> None:
    """Запустить платный поиск Perplexity; PERPLEXITY_API_KEY берётся из окружения."""
    try:
        # Load dotenv through the existing settings reader, without printing credentials.
        known = list(_records(config, database).values())
        previous_runs = set(known_run or [])
        # Sibling runs are the default persistent known-vacancy registry. No DB import needed.
        if output.parent.is_dir():
            previous_runs.update(path for path in output.parent.iterdir()
                                 if path.is_dir() and path != output
                                 and (path / "manifest.json").is_file()
                                 and (path / "candidates.jsonl").is_file())
        for previous in sorted(previous_runs):
            known.extend(record_from_snapshot(row["source"]) for row in load_candidates(previous))
        search = PerplexitySearch(os.environ.get("PERPLEXITY_API_KEY", ""))
        semantic = None
        if semantic_model:
            from job_intake.discovery.semantic import GeminiReranker
            if input_per_million is None or output_per_million is None:
                raise ValueError("Supply --input-per-million and --output-per-million tariffs")
            semantic = GeminiReranker(os.environ.get("GEMINI_API_KEY", ""), semantic_model,
                                      input_per_million=input_per_million,
                                      output_per_million=output_per_million)
        result = run_discovery(read_json(plan), output, search=search, fetcher=PublicJobFetcher(),
                               budget_usd=budget_usd, max_concurrency=max_concurrency,
                               max_details=max_details, max_results=max_results,
                               max_seconds=max_seconds, search_price_usd=search_price_usd,
                               semantic=semantic, known_records=known)
        typer.echo(f"Запуск: {output.resolve()}; кандидатов={result['counts']['candidates']}; "
                   f"API по тарифу={result['cost_usd']:.4f} USD; "
                   f"время={result['duration_seconds']:.1f} с.")
    except (ValueError, OSError, KeyError, TypeError, ProviderError) as error:
        _fail(error)


@app.command("prepare-labeling")
def batch_command(
    run: Annotated[Path, typer.Option(exists=True, file_okay=False)],
    size: int = typer.Option(50, min=1, max=1000),
    random_size: int = typer.Option(10, min=0, max=1000),
    seed: int = typer.Option(42),
) -> None:
    """Создать слепую human-партию, сохранив top/random когорты вне экрана разметки."""
    try:
        result = prepare_labeling(run, size=size, random_size=random_size, seed=seed)
        typer.echo(f"Партия: {run.resolve() / 'labels.jsonl'}; вакансий={result['size']}. "
                   f"Открыть: crm serve --discovery {run.resolve()}")
    except (ValueError, OSError, KeyError, TypeError) as error:
        _fail(error)


@app.command("report")
def report_command(
    run: Annotated[Path, typer.Option(exists=True, file_okay=False)],
    output: Annotated[Path | None, typer.Option(help="Новый .md или .json отчёт")] = None,
) -> None:
    """Отчёт по человеческим positive-меткам; только чтение данных поиска и разметки."""
    try:
        report = build_report(run)
        if output:
            content = report_markdown(report) if output.suffix == ".md" \
                else json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
            write_text(output, content, exclusive=True)
            typer.echo(f"Отчёт: {output.resolve()}")
        else:
            typer.echo(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False))
    except (ValueError, OSError, KeyError, TypeError) as error:
        _fail(error)
