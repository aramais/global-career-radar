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


def _search_options(country=None, domain=None, language=None, context_size=None,
                    max_tokens=None, max_tokens_per_page=None, after=None, before=None,
                    updated_after=None, updated_before=None, recency=None) -> dict:
    return {key: value for key, value in {
        "country": country, "search_domain_filter": domain, "search_language_filter": language,
        "search_context_size": context_size, "max_tokens": max_tokens,
        "max_tokens_per_page": max_tokens_per_page, "search_after_date_filter": after,
        "search_before_date_filter": before, "last_updated_after_filter": updated_after,
        "last_updated_before_filter": updated_before, "search_recency_filter": recency,
    }.items() if value is not None}


@app.command("search")
def search_command(
    query: Annotated[list[str], typer.Option("--query", "-q", help="1–5 поисковых фраз")],
    max_results: int = typer.Option(10, min=1, max=20),
    country: str | None = typer.Option(None, help="ISO alpha-2, например BR"),
    domain: Annotated[
        list[str] | None, typer.Option(help="Домен/путь; -домен для исключения")
    ] = None,
    language: Annotated[list[str] | None, typer.Option(help="ISO 639-1, например en")] = None,
    context_size: str | None = typer.Option(None, help="low / medium / high"),
    max_tokens: int | None = typer.Option(None, min=1, max=1_000_000),
    max_tokens_per_page: int | None = typer.Option(None, min=1, max=1_000_000),
    after: str | None = typer.Option(None, help="Дата публикации после MM/DD/YYYY"),
    before: str | None = typer.Option(None, help="Дата публикации до MM/DD/YYYY"),
    updated_after: str | None = typer.Option(None, help="Изменение после MM/DD/YYYY"),
    updated_before: str | None = typer.Option(None, help="Изменение до MM/DD/YYYY"),
    recency: str | None = typer.Option(None, help="hour/day/week/month/year"),
    smoke: bool = typer.Option(False, help="Печатать только HTTP-статус и форму ответа"),
    config: str = typer.Option("config/settings.yaml"),
) -> None:
    """Один реальный Search POST; не меняет базу, gold и результаты discovery."""
    search = None
    try:
        load_app_config(config)  # Existing dotenv/environment conventions, no Database.
        options = _search_options(country, domain, language, context_size, max_tokens,
                                  max_tokens_per_page, after, before, updated_after,
                                  updated_before, recency)
        search = PerplexitySearch(options=options)
        results = search.search(query[0] if len(query) == 1 else query, max_results=max_results)
        output = {"http_status": search.last_status_code,
                  "shape": {"results": "array", "result_fields": sorted({
                      key for item in results for key in item})}, "result_count": len(results)} \
            if smoke else {"results": results}
        typer.echo(json.dumps(output, ensure_ascii=False, allow_nan=False))
    except (ProviderError, ValueError, OSError, TypeError) as error:
        if isinstance(error, ProviderError) and error.retry_after_seconds is not None:
            typer.echo(f"Retry-After: {error.retry_after_seconds:.1f} seconds", err=True)
        _fail(error)
    finally:
        if search is not None:
            search.close()


@app.command("plan")
def plan_command(
    gold: Annotated[Path, typer.Option(exists=True, dir_okay=False)],
    output: Annotated[Path, typer.Option(help="Новый JSON-план (не заменяет существующий)")],
    holdout: Annotated[Path | None, typer.Option(exists=True, dir_okay=False)] = None,
    feedback: Annotated[list[Path] | None, typer.Option(exists=True, dir_okay=False)] = None,
    cohort: Annotated[Path | None, typer.Option(exists=True, dir_okay=False)] = None,
    exposure_log: Annotated[Path | None, typer.Option()] = None,
    objective: str = typer.Option("training", help="training / p1 / p2"),
    max_queries: int = typer.Option(80, min=1, max=80),
    exploration_fraction: float = typer.Option(.2, min=0, max=1),
    config: str = typer.Option("config/settings.yaml"),
    database: Annotated[Path | None, typer.Option(exists=True, dir_okay=False)] = None,
) -> None:
    """Создать план из human-positive примеров без обращения к API; SQLite только читается."""
    try:
        if output.exists():
            raise ValueError("Plan already exists; choose a new output file")
        from job_intake.annotation.cohorts import load_cohort
        from job_intake.discovery.provenance import record_plan_exposure
        manifest = load_cohort(cohort) if cohort else None
        if manifest is not None and exposure_log is not None and not manifest.get("exposure_log"):
            manifest["exposure_log"] = str(exposure_log.resolve())
        result = build_plan(gold, holdout_path=holdout, feedback_paths=feedback, cohort=manifest,
                            objective=objective,
                            max_queries=max_queries, exploration_fraction=exploration_fraction,
                            source_records=_records(config, database))
        record_plan_exposure(result, exposure_log=exposure_log, actor="discovery-plan",
                             stage="planner", context={"plan": str(output.resolve())})
        write_json(output, result, exclusive=True)
        typer.echo(f"План: {output.resolve()}; примеров={len(result['seeds'])}; "
                   f"запросов={len(result['queries'])}. API: 0 USD.")
    except (ValueError, OSError, KeyError, TypeError) as error:
        _fail(error)


@app.command("run")
def run_command(
    plan: Annotated[Path, typer.Option(exists=True, dir_okay=False)],
    output: Annotated[Path, typer.Option(help="Новая папка запуска")],
    budget_usd: Annotated[
        float, typer.Option(min=.001, help="Потолок API-расходов USD; расходовать целиком не нужно")
    ],
    max_concurrency: int = typer.Option(4, min=1, max=16),
    max_details: int = typer.Option(200, min=1, max=1000),
    target_candidates: int | None = typer.Option(
        None, min=1, max=1000, help="Остановиться после N новых полных вакансий для разметки"
    ),
    max_results: int = typer.Option(10, min=1, max=20),
    max_seconds: float = typer.Option(3600, min=1, max=86400),
    search_price_usd: float = typer.Option(.005, min=.000001,
                                         help="USD за один web Search POST; сохраняется в отчёте"),
    semantic_model: Annotated[
        str | None, typer.Option(help="Gemini для смыслового ранжирования")
    ] = None,
    selection_model: Annotated[
        str | None, typer.Option(help="Gemini для отбора по human-примерам до включения в цель")
    ] = None,
    input_per_million: Annotated[float | None, typer.Option(min=0)] = None,
    output_per_million: Annotated[float | None, typer.Option(min=0)] = None,
    config: str = typer.Option("config/settings.yaml"),
    database: Annotated[Path | None, typer.Option(exists=True, dir_okay=False)] = None,
    known_run: Annotated[
        list[Path] | None, typer.Option(help="Прежняя discovery-папка для дедупликации")
    ] = None,
    query_batch_size: int = typer.Option(1, min=1, max=5, help="Фраз в одном Search POST"),
    country: str | None = typer.Option(None, help="Регион выдачи ISO alpha-2"),
    domain: Annotated[list[str] | None, typer.Option(help="Домен/путь поиска")] = None,
    language: Annotated[list[str] | None, typer.Option(help="Язык исходников ISO 639-1")] = None,
    context_size: str | None = typer.Option(None, help="low / medium / high"),
    max_tokens: int | None = typer.Option(None, min=1, max=1_000_000),
    max_tokens_per_page: int | None = typer.Option(None, min=1, max=1_000_000),
    after: str | None = typer.Option(None, help="Дата публикации после MM/DD/YYYY"),
    before: str | None = typer.Option(None, help="Дата публикации до MM/DD/YYYY"),
    updated_after: str | None = typer.Option(None, help="Изменение после MM/DD/YYYY"),
    updated_before: str | None = typer.Option(None, help="Изменение до MM/DD/YYYY"),
    recency: str | None = typer.Option(None, help="hour/day/week/month/year"),
    objective: str | None = typer.Option(None, help="training / p1 / p2; default from plan"),
    registry: Annotated[Path | None, typer.Option(help="Реестр между запусками SQLite")] = None,
    repeat_queries: bool = typer.Option(False, help="Обновить успешные Search ответы до TTL"),
    search_ttl_seconds: float = typer.Option(86400, min=1),
    fetch_ttl_seconds: float = typer.Option(21600, min=1),
    domain_policy: Annotated[Path | None, typer.Option(exists=True, dir_okay=False)] = None,
) -> None:
    """Запустить платный поиск Perplexity; PERPLEXITY_API_KEY берётся из окружения."""
    search, semantic, selector = None, None, None
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
        options = _search_options(country, domain, language, context_size, max_tokens,
                                  max_tokens_per_page, after, before, updated_after,
                                  updated_before, recency)
        search = PerplexitySearch(os.environ.get("PERPLEXITY_API_KEY", ""), options=options) \
            if options else PerplexitySearch(os.environ.get("PERPLEXITY_API_KEY", ""))
        if semantic_model or selection_model:
            from job_intake.discovery.semantic import GeminiReranker, GeminiSelector
            if input_per_million is None or output_per_million is None:
                raise ValueError("Supply --input-per-million and --output-per-million tariffs")
            for model, client_type in ((semantic_model, GeminiReranker),
                                       (selection_model, GeminiSelector)):
                if model:
                    client = client_type(os.environ.get("GEMINI_API_KEY", ""), model,
                                         input_per_million=input_per_million,
                                         output_per_million=output_per_million)
                    if client_type is GeminiSelector:
                        selector = client
                    else:
                        semantic = client
        fetcher = PublicJobFetcher(domain_policy=read_json(domain_policy)) \
            if domain_policy else PublicJobFetcher()
        purpose_evaluator = None
        if (objective or read_json(plan).get("objective", "training")) != "training":
            from job_intake.config.settings import load_yaml_mapping
            from job_intake.filtering import FilterRules, RuleEngine
            settings = load_app_config(config)
            purpose_evaluator = RuleEngine(FilterRules.from_mapping(
                load_yaml_mapping(settings.rules_path))).apply
        result = run_discovery(read_json(plan), output, search=search, fetcher=fetcher,
                               budget_usd=budget_usd, max_concurrency=max_concurrency,
                               max_details=max_details, max_results=max_results,
                               max_seconds=max_seconds, search_price_usd=search_price_usd,
                               semantic=semantic, known_records=known,
                               query_batch_size=query_batch_size,
                               target_candidates=target_candidates, selector=selector,
                               registry_path=registry, repeat_queries=repeat_queries,
                               search_ttl_seconds=search_ttl_seconds,
                               fetch_ttl_seconds=fetch_ttl_seconds, objective=objective,
                               purpose_evaluator=purpose_evaluator)
        goal = f"/{target_candidates}" if target_candidates is not None else ""
        typer.echo(f"Запуск: {output.resolve()}; "
                   f"кандидатов={result['counts']['candidates']}{goal}; "
                   f"API по тарифу={result['cost_usd']:.4f} USD; "
                   f"время={result['duration_seconds']:.1f} с. "
                   f"остановка: {result['stop_reason']}.")
    except (ValueError, OSError, KeyError, TypeError, ProviderError) as error:
        _fail(error)
    finally:
        for client in (search, semantic, selector):
            if client is not None and callable(getattr(client, "close", None)):
                client.close()


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


@app.command("promote")
def promote_command(
    run: Annotated[Path, typer.Option(exists=True, file_okay=False)],
    objective: str = typer.Option("p1", help="p1 / p2; human confirmation required"),
    job_uid: Annotated[list[str] | None, typer.Option()] = None,
    apply: bool = typer.Option(False, help="Записать импорт; по умолчанию read-only dry-run"),
    config: str = typer.Option("config/settings.yaml"),
) -> None:
    """Перенести подтверждённые источники в CRM, сохранив ссылки на human-разметку."""
    try:
        from job_intake.config.settings import load_yaml_mapping
        from job_intake.discovery.promote import promotion_drafts
        from job_intake.profiles import load_streams
        from job_intake.storage.promote import promote_drafts
        settings = load_app_config(config)
        streams = load_streams(load_yaml_mapping(settings.rules_path),
                               load_yaml_mapping(settings.search_profiles_path))
        drafts = promotion_drafts(run, objective=objective, job_uids=job_uid,
                                  profile_ids=[stream.id for stream in streams])
        result = promote_drafts(config, drafts, dry_run=not apply)
        typer.echo(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
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
