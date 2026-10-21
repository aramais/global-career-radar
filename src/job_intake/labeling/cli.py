"""Opt-in labels-v3 commands; export avoids pipeline construction and DB writes."""

from __future__ import annotations

import json
import math
import re
import sqlite3
import sys
from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import Annotated
from uuid import uuid4

import typer

from job_intake.annotation.accuracy import load_prices
from job_intake.annotation.eval_runner import report_destinations, sqlite_input_paths
from job_intake.config.settings import load_app_config
from job_intake.labeling.runner import (
    export_records,
    label_records,
    labeling_streams,
    publish_predictions,
    shadow_writer,
)
from job_intake.storage.repository import validate_job_uids


def _progress(event: dict) -> None:
    # Known enums/counters only: never provider bodies, exception text or sources.
    name = event.get("event")
    if name == "label_escalation_share_warning":
        typer.echo(f"Labels escalation share {event['share']:.1%} exceeds "
                   f"{event['threshold']:.1%} of completed labeled vacancies.")
        return
    if name in {
        "label_start",
        "label_cache_hit",
        "label_facts_reuse",
        "label_gated",
        "label_complete",
        "label_error",
        "label_escalate",
        "label_schema_retry",
    }:
        typer.echo(str(name))


def label(
    config: str = typer.Option("config/settings.yaml", help="App config YAML"),
    export: Annotated[
        Path | None, typer.Option("--export", help="New prediction JSONL; SQLite read-only")
    ] = None,
    bundle: Annotated[Path | None, typer.Option(help="Frozen bundle source snapshots")] = None,
    gold: Annotated[
        Path | None, typer.Option(help="V3 gold source snapshots; human answers stay blind")
    ] = None,
    job_uid: Annotated[list[str] | None, typer.Option("--job-uid")] = None,
    limit: Annotated[int | None, typer.Option(min=1)] = None,
    include_archived: bool = typer.Option(
        False, "--include-archived", help="Include archived vacancies without unarchiving"
    ),
    ai: bool = typer.Option(False, "--ai", help="Explicit paid labels calls; requires --max-cost"),
    max_cost: Annotated[
        float | None, typer.Option(help="Shared USD limit, including retries")
    ] = None,
    prices: Annotated[Path | None, typer.Option(help="Versioned eval-prices-v1 tariffs")] = None,
    offline: bool = typer.Option(
        False, "--offline", help="Reuse cached facts and recompute aggregation without API"
    ),
    concurrency: int = typer.Option(1, min=1, max=8, help="Export workers; writes require 1"),
    run_id: Annotated[
        str | None, typer.Option(help="Ledger run ID; defaults to a new UUID")
    ] = None,
    refresh_stale: bool = typer.Option(
        False, "--refresh-stale", help="With --ai, refresh facts from a changed prompt rubric"
    ),
    tag_model: Annotated[str | None, typer.Option()] = None,
    escalate_model: Annotated[str | None, typer.Option()] = None,
    no_escalation: bool = typer.Option(
        False, "--no-escalation", help="Disable the second labels model"
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Label saved vacancies; export predictions or explicitly enabled DB updates."""
    from job_intake.labeling.budget import BudgetExceeded, BudgetGuard
    from job_intake.labeling.ledger import LedgerWriter

    ledger = None
    try:
        selected = validate_job_uids(job_uid)
        if selected and limit is not None:
            raise ValueError("Use either --job-uid or --limit, not both")
        if sum((bundle is not None, gold is not None, selected is not None)) > 1:
            raise ValueError("Use only one of --bundle, --gold or --job-uid")
        if (bundle or gold) and (export is None or limit is not None):
            raise ValueError("--bundle/--gold require --export and cannot be limited")
        if export is None and concurrency != 1:
            raise ValueError("Database labeling requires --concurrency 1")
        if ai and offline:
            raise ValueError("--offline cannot be combined with --ai")
        if refresh_stale and not ai:
            raise ValueError("--refresh-stale requires --ai")
        if ai and max_cost is None:
            raise ValueError("--ai requires --max-cost")
        if max_cost is not None and (not math.isfinite(max_cost) or max_cost <= 0):
            raise ValueError("--max-cost must be positive and finite")
        if max_cost is not None and max_cost > 5:
            if not sys.stdin.isatty():
                raise ValueError("--max-cost above $5 requires interactive confirmation")
            if not typer.confirm(f"Allow labels-v3 calls costing up to ${max_cost:.2f}?"):
                raise typer.Abort()
        run_id = run_id or uuid4().hex
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", run_id):
            raise ValueError("--run-id must be a plain identifier up to 64 characters")
        settings = load_app_config(config)
        updates = {"ai_enabled": ai, "refresh_stale": refresh_stale}
        if tag_model is not None:
            updates["tag_model"] = tag_model
        if escalate_model is not None:
            updates["escalate_model"] = escalate_model
        if no_escalation:
            updates["escalation"] = replace(settings.labeling.escalation, enabled=False)
        settings = replace(settings, labeling=replace(settings.labeling, **updates))
        if export is None and (not settings.labeling.enabled or settings.labeling.mode == "legacy"):
            raise ValueError(
                "Database labeling requires labeling.enabled=true and mode shadow or labels"
            )
        price_path = prices or settings.rules_path.parent / "eval_prices.yaml"
        ledger_path = (
            export.with_suffix(".ledger.jsonl")
            if export is not None
            else Path(settings.labeling.ledger_dir) / (run_id + ".jsonl")
        )
        protected = [
            Path(config),
            settings.rules_path,
            settings.search_profiles_path,
            settings.company_watchlist_path,
            *sqlite_input_paths(settings.database_url),
            Path(settings.labeling.rubric_path),
            Path(settings.labeling.precedents_path),
            price_path,
            *([bundle] if bundle else []),
            *([gold] if gold else []),
        ]
        outputs = ([export] if export is not None else []) + [ledger_path]
        report_destinations(outputs, protected_paths=protected)
        budget = BudgetGuard(max_cost, load_prices(price_path)) if ai else None
        ledger = LedgerWriter(ledger_path, run_id=run_id)
        progress = _progress if verbose else None
        if export is None and settings.labeling.mode == "labels":
            from job_intake.pipeline import JobIntakePipeline

            pipeline = JobIntakePipeline(settings)
            try:
                result = pipeline.label_saved(
                    use_ai=ai,
                    offline=offline,
                    limit=limit,
                    job_uids=selected,
                    include_archived=include_archived,
                    budget=budget,
                    ledger=ledger,
                    progress=progress,
                )
            except BudgetExceeded:
                typer.echo("Budget exhausted; completed vacancies remain saved.")
                raise typer.Exit(1) from None
            typer.echo(
                json.dumps(
                    {
                        "persisted": result["persisted"],
                        "mode": "labels",
                        "record_errors": result["record_errors"],
                    }
                )
            )
            if result["record_errors"] or result.get("labels_errors"):
                raise typer.Exit(1)
            return
        streams = labeling_streams(settings)
        rows = export_records(
            settings,
            bundle=bundle,
            gold=gold,
            job_uids=selected,
            limit=limit,
            include_archived=include_archived,
            profile_ids=[stream.id for stream in streams],
        )
        persist = (
            shadow_writer(settings, include_archived=include_archived) if export is None else None
        )
        predictions, stopped = label_records(
            settings,
            rows,
            use_ai=ai,
            offline=offline,
            concurrency=concurrency,
            budget=budget,
            ledger=ledger,
            progress=progress,
            persist=persist,
        )
        if export is not None:
            publish_predictions(export, predictions)
        typer.echo(
            json.dumps(
                {
                    "selected": len(rows),
                    "completed": len(predictions),
                    "database_access": "read_only" if export is not None else "write",
                    "statuses": dict(Counter(row["status"] for row in predictions)),
                    "budget_exhausted": stopped,
                    "spent_usd": budget.spent if budget is not None else 0.0,
                }
            )
        )
        if stopped or any(row["status"] == "error" for row in predictions):
            raise typer.Exit(1)
    except (ValueError, TypeError, KeyError, OSError, sqlite3.Error) as exc:
        raise typer.BadParameter(str(exc)) from None
    finally:
        if ledger is not None:
            ledger.close()
