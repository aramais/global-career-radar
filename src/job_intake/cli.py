from __future__ import annotations

from pathlib import Path

import typer

from job_intake.pipeline import build_pipeline


app = typer.Typer(add_completion=False, help="Deterministic-first job intake pipeline")


@app.command()
def run(config: str = typer.Option("config/settings.yaml", help="Path to app config YAML")) -> None:
    pipeline = build_pipeline(config)
    result = pipeline.run()
    typer.echo(
        f"Run completed: ingested={result['ingested']} persisted={result['persisted']} alerts={result['alerts']}"
    )


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
) -> None:
    pipeline = build_pipeline(config)
    path = pipeline.export_csv(Path(output).resolve())
    typer.echo(f"Exported shortlist to {path}")


@app.command("render-html")
def render_html(
    config: str = typer.Option("config/settings.yaml", help="Path to app config YAML"),
    output: str = typer.Option("data/review.html", help="HTML report target"),
    limit: int = typer.Option(100, help="Maximum number of recent rows to render"),
) -> None:
    pipeline = build_pipeline(config)
    path = pipeline.render_html(Path(output).resolve(), limit=limit)
    typer.echo(f"Rendered review report to {path}")


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
