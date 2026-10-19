from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Annotated

import typer

from job_intake.storage.repair import apply_repair, prepare_repair, rollback_repair

app = typer.Typer(help="Prepare offline database repairs, then apply an exact reviewed patch")


@app.command("prepare")
def prepare(
    output: Annotated[Path, typer.Option()],
    config: Annotated[Path, typer.Option()] = Path("config/settings.yaml"),
) -> None:
    try:
        report = prepare_repair(config, output)
    except (ValueError, OSError, sqlite3.Error) as error:
        raise typer.BadParameter(str(error)) from error
    typer.echo(
        json.dumps(
            {
                "jobs": report["jobs"],
                "transitions": len(report["transitions"]),
                "cache_recovery": report["cache_recovery"],
                "api_calls": 0,
                "report": str((output / "report.json").resolve()),
            }
        )
    )


@app.command("apply")
def apply(prepared: Annotated[Path, typer.Option()]) -> None:
    try:
        result = apply_repair(prepared)
    except (ValueError, OSError, sqlite3.Error) as error:
        raise typer.BadParameter(str(error)) from error
    typer.echo(json.dumps(result))


@app.command("rollback")
def rollback(prepared: Annotated[Path, typer.Option()]) -> None:
    try:
        result = rollback_repair(prepared)
    except (ValueError, OSError, sqlite3.Error) as error:
        raise typer.BadParameter(str(error)) from error
    typer.echo(json.dumps(result))
