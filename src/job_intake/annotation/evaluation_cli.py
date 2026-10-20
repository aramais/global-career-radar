"""Explicit offline evaluation actions, registered under ``job-intake eval frozen``."""

from __future__ import annotations

import copy
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer

from job_intake.annotation.benchmark import read_jsonl
from job_intake.annotation.cohorts import (
    DEFAULT_REGISTRY,
    SCHEMA,
    adjudication_queue,
    duplicate_group,
    load_cohort,
    population_estimates,
    read_object,
    record_exposure,
    register_cohort,
    sample_frame,
    union_search_frames,
    validate_cohort,
    write_new,
    write_new_text,
)
from job_intake.annotation.experiments import (
    declare_experiment,
    ledger_metrics,
    measure_experiment,
)
from job_intake.annotation.frozen_eval import (
    build_bundle,
    compare_runs,
    create_run,
    load_bundle,
    save_bundle,
    saved_predictions_for_bundle,
    write_comparison_report,
)
from job_intake.config.settings import load_app_config

app = typer.Typer(help="Frozen offline evaluation, independent cohorts and recorded experiments")


def _error(exc: Exception) -> None:
    raise typer.BadParameter(str(exc)) from exc


@app.command("cohort-register")
def cohort_register(
    cohort: Annotated[Path, typer.Option()],
    registry: Annotated[Path, typer.Option()] = DEFAULT_REGISTRY,
) -> None:
    """Register a frozen cohort globally before using discovery examples."""
    try:
        result = register_cohort(registry, load_cohort(cohort))
    except (OSError, ValueError, TypeError) as exc:
        _error(exc)
    typer.echo(f"Cohort {result['cohort_id']} registered; original labels unchanged")


@app.command("freeze")
def freeze(
    gold: Annotated[Path, typer.Option()],
    versions: Annotated[Path, typer.Option(help="JSON object variant -> predeclared version")],
    output: Annotated[Path, typer.Option()],
    config: str = "config/settings.yaml",
    cohort: Annotated[Path | None, typer.Option()] = None,
    policy: Annotated[Path | None, typer.Option(help="Predeclared versioned tolerances")] = None,
    source_snapshot: bool = False,
) -> None:
    """Create new immutable source/label/annotation bundle; archives included."""
    try:
        bundle = build_bundle(
            load_app_config(config),
            gold=gold,
            versions=read_object(versions),
            cohort=cohort,
            allow_source_snapshot=source_snapshot,
            policy=read_object(policy) if policy else None,
        )
        save_bundle(bundle, output)
    except (OSError, ValueError, TypeError, sqlite3.Error) as exc:
        _error(exc)
    typer.echo(f"Frozen {len(bundle['rows'])} sources: {bundle['bundle_id']}; API calls=0")


@app.command("run")
def run(
    bundle: Annotated[Path, typer.Option()],
    variant: Annotated[str, typer.Option()],
    version: Annotated[str, typer.Option()],
    run_id: Annotated[str, typer.Option()],
    output: Annotated[Path, typer.Option()],
    predictions: Annotated[
        Path | None, typer.Option(help="JSONL bound by job_uid/source_hash")
    ] = None,
    ledger: Annotated[Path | None, typer.Option(help="Actual attempts JSONL")] = None,
    prices: Annotated[Path | None, typer.Option(help="Dated eval-prices-v1 JSON")] = None,
    maximum_price_age_days: int = 30,
    wall_seconds: Annotated[
        float | None,
        typer.Option(
            help="Measured full run wall time; attempt durations are accounted separately"
        ),
    ] = None,
    saved_config: Annotated[
        Path | None,
        typer.Option(
            help="Read current saved outputs bound to unchanged frozen facts, no reevaluation"
        ),
    ] = None,
) -> None:
    """Evaluate recorded predictions, or the frozen saved baseline, without API calls."""
    try:
        if bool(ledger) != bool(prices):
            raise ValueError("Recorded ledger and price snapshot must be supplied together")
        if predictions is not None and saved_config is not None:
            raise ValueError("Choose predictions JSONL or current saved-config")
        operational = (
            ledger_metrics(
                read_jsonl(ledger),
                prices=read_object(prices),
                maximum_price_age_days=maximum_price_age_days,
                run_wall_seconds=wall_seconds,
            )
            if ledger
            else None
        )
        frozen = load_bundle(bundle)
        outputs = (
            saved_predictions_for_bundle(load_app_config(saved_config), frozen)
            if saved_config
            else read_jsonl(predictions)
            if predictions
            else None
        )
        result = create_run(
            frozen,
            predictions=outputs,
            variant=variant,
            version=version,
            run_id=run_id,
            operational=operational,
        )
        write_new(output, result)
    except (OSError, ValueError, TypeError) as exc:
        _error(exc)
    typer.echo(f"Recorded run {result['run_id']}; API calls=0")


@app.command("compare")
def compare(
    bundle: Annotated[Path, typer.Option()],
    baseline: Annotated[Path, typer.Option()],
    candidate: Annotated[Path, typer.Option()],
    output: Annotated[Path, typer.Option()],
    repeat: Annotated[Path | None, typer.Option()] = None,
    cohort: Annotated[Path | None, typer.Option()] = None,
    exposure_log: Annotated[Path | None, typer.Option()] = None,
    actor: str = "local-user",
) -> None:
    """Compare exact bundle runs; holdout analysis is recorded as exposure."""
    try:
        frozen = load_bundle(bundle)
        if cohort is not None and load_cohort(cohort) != frozen.get("cohort"):
            raise ValueError("--cohort must be identical to the frozen sidecar")
        manifest = frozen.get("cohort")
        log = exposure_log or (
            Path(manifest["exposure_log"]) if manifest and manifest.get("exposure_log") else None
        )
        if manifest and manifest["dataset_role"] == "holdout" and log is None:
            raise ValueError("Holdout comparison requires an exposure journal")
        result = compare_runs(
            frozen,
            read_object(baseline),
            read_object(candidate),
            repeat=read_object(repeat) if repeat else None,
            exposure_log=log,
        )
        # Exclusive destination is checked before consuming the holdout; no gold writes.
        if output.suffix != ".json" or output.exists() or output.with_suffix(".md").exists():
            raise ValueError("Output exists; immutable reports cannot be replaced")
        if manifest and log:
            event = record_exposure(
                log,
                manifest,
                job_uids=[row["job_uid"] for row in frozen["rows"]],
                purpose="developer_analysis",
                actor=actor,
                context={"bundle_id": frozen["bundle_id"], "output": str(output)},
            )
            result["exposure_event"] = event
        write_comparison_report(result, output)
    except (OSError, ValueError, TypeError) as exc:
        _error(exc)
    typer.echo(f"Quality status: {result['status']}; API calls=0")


@app.command("cohort-create")
def cohort_create(
    gold: Annotated[Path, typer.Option()],
    cohort_id: Annotated[str, typer.Option()],
    dataset_role: Annotated[str, typer.Option()],
    rubric_version: Annotated[str, typer.Option()],
    output: Annotated[Path, typer.Option()],
    exposure_log: Annotated[Path | None, typer.Option()] = None,
    provenance_review: Annotated[
        Path | None,
        typer.Option(help="Historical exposure review; required before independent holdout gates"),
    ] = None,
) -> None:
    """Create a sidecar; blind labels alone do not establish independence."""
    try:
        members = []
        for row in read_jsonl(gold):
            source = row.get("source", {})
            if not source.get("description_clean"):
                raise ValueError("Cohort creation needs source snapshots for duplicate protection")
            duplicate = duplicate_group(source)
            members.append(
                {
                    "job_uid": row["job_uid"],
                    "source_hash": row["source_hash"],
                    "duplicate_group": duplicate,
                    "url": source.get("original_url"),
                    "original_labeled_at": row.get("labeled_at"),
                    "label_method": row.get("method"),
                }
            )
        result = {
            "schema": SCHEMA,
            "cohort_id": cohort_id,
            "dataset_role": dataset_role,
            "rubric_version": rubric_version,
            "created_at": datetime.now(UTC).isoformat(),
            "members": members,
        }
        if exposure_log:
            result["exposure_log"] = str(exposure_log.resolve())
        if provenance_review:
            result["provenance_review"] = read_object(provenance_review)
        write_new(output, validate_cohort(result))
    except (OSError, ValueError, TypeError, KeyError) as exc:
        _error(exc)
    typer.echo("Cohort sidecar created; original human answers unchanged")


@app.command("sample")
def sample(
    frame: Annotated[Path, typer.Option(help="JSONL: job_uid,source_hash,stratum,optional rank")],
    quotas: Annotated[Path, typer.Option(help="JSON object stratum -> n")],
    output: Annotated[Path, typer.Option()],
    seed: int = 1729,
    mode: str = "audit",
) -> None:
    try:
        write_new(
            output,
            sample_frame(read_jsonl(frame), quotas=read_object(quotas), seed=seed, mode=mode),
        )
    except (OSError, ValueError, TypeError, KeyError) as exc:
        _error(exc)
    typer.echo("Frozen sampling frame created with inclusion probabilities")


@app.command("sample-report")
def sample_report(
    sample: Annotated[Path, typer.Option()],
    gold: Annotated[Path, typer.Option()],
    output: Annotated[Path, typer.Option()],
) -> None:
    try:
        write_new(output, population_estimates(read_object(sample), read_jsonl(gold)))
    except (OSError, ValueError, TypeError, KeyError) as exc:
        _error(exc)
    typer.echo("Weighted finite-frame estimates written; missing labels remain unknown")


@app.command("union-frame")
def union_frame(
    baseline: Annotated[Path, typer.Option()],
    candidate: Annotated[Path, typer.Option()],
    output: Annotated[Path, typer.Option()],
) -> None:
    try:
        result = union_search_frames(read_jsonl(baseline), read_jsonl(candidate))
        import json

        write_new_text(output, "".join(json.dumps(row, ensure_ascii=False) + "\n"
                                       for row in result))
    except (OSError, ValueError, TypeError, KeyError) as exc:
        _error(exc)
    typer.echo("Union frame written; baseline-only and unprocessed results retained")


@app.command("adjudicate")
def adjudicate(
    gold: Annotated[Path, typer.Option()],
    disputes: Annotated[Path, typer.Option(help="JSONL job_uid/reason/optional proposal")],
    output: Annotated[Path, typer.Option()],
) -> None:
    try:
        write_new(output, adjudication_queue(read_jsonl(gold), read_jsonl(disputes)))
    except (OSError, ValueError, TypeError, KeyError) as exc:
        _error(exc)
    typer.echo("Adjudication queue created; no human answers replaced")


@app.command("experiment-declare")
def experiment_declare(
    bundle: Annotated[Path, typer.Option()],
    specification: Annotated[
        Path, typer.Option(help="experiment_id,factor,variants,budget_usd,goals")
    ],
    output: Annotated[Path, typer.Option()],
) -> None:
    try:
        write_new(output, declare_experiment(load_bundle(bundle), **read_object(specification)))
    except (OSError, ValueError, TypeError) as exc:
        _error(exc)
    typer.echo("Experiment declared; no API calls")


@app.command("experiment-report")
def experiment_report(
    bundle: Annotated[Path, typer.Option()],
    declaration: Annotated[Path, typer.Option()],
    runs: Annotated[list[Path], typer.Option(help="Repeat for every recorded run")],
    output: Annotated[Path, typer.Option()],
    exposure_log: Annotated[Path | None, typer.Option()] = None,
) -> None:
    try:
        frozen = load_bundle(bundle)
        manifest = frozen.get("cohort")
        if manifest and manifest["dataset_role"] == "holdout" and exposure_log is None:
            raise ValueError("Holdout experiment analysis requires --exposure-log")
        result = measure_experiment(
            frozen,
            read_object(declaration),
            runs=[read_object(path) for path in runs],
            exposure_log=exposure_log,
        )
        if output.suffix != ".json" or output.exists() or output.with_suffix(".md").exists():
            raise ValueError("Output exists; immutable reports cannot be replaced")
        if manifest and exposure_log:
            result["exposure_event"] = record_exposure(
                exposure_log,
                manifest,
                job_uids=[row["job_uid"] for row in frozen["rows"]],
                purpose="developer_analysis",
                actor="local-user",
                context={"bundle_id": frozen["bundle_id"], "output": str(output)},
            )
        write_comparison_report(copy.deepcopy(result), output)
    except (OSError, ValueError, TypeError) as exc:
        _error(exc)
    typer.echo(f"Experiment status: {result['status']}; API calls=0")
