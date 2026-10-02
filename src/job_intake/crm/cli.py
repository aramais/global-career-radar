from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Annotated

import typer

from job_intake.config.settings import AppConfig, load_app_config
from job_intake.crm.importer import import_into_session, parse_workbook
from job_intake.crm.repository import CRMRepository, serialize_application
from job_intake.crm.server import csv_export, serve_crm
from job_intake.storage.database import Database

app = typer.Typer(help="Локальная CRM откликов, контактов и следующих действий")
DEFAULT_EXPORT = Path("data/local/crm.csv")


def _config(path: str, database: Path | None) -> AppConfig:
    config = load_app_config(path)
    if database is not None:
        config = replace(config, database_url=f"sqlite:///{database.resolve()}")
    return config


@app.command("serve")
def serve(
    config: str = typer.Option("config/settings.yaml", help="Настройки проекта"),
    port: int = typer.Option(8765, min=1, max=65535, help="Локальный порт"),
    database: Annotated[Path | None, typer.Option(help="Отдельная локальная SQLite-база")] = None,
) -> None:
    """Открыть редактируемую CRM на http://127.0.0.1:8765."""
    settings = _config(config, database)
    typer.echo(f"CRM: http://127.0.0.1:{port} — Ctrl+C для остановки")
    serve_crm(settings, port=port)


@app.command("import-xlsx")
def import_xlsx(
    workbook: Annotated[Path, typer.Argument(exists=True, file_okay=True, dir_okay=False)],
    config: str = typer.Option("config/settings.yaml", help="Настройки проекта"),
    dry_run: bool = typer.Option(False, help="Показать состав импорта без записи в базу"),
    database: Annotated[Path | None, typer.Option(help="Отдельная локальная SQLite-база")] = None,
) -> None:
    """Перенести историю JobCRM.xlsx; исходная таблица сохраняется."""
    try:
        parsed = parse_workbook(workbook)
        if dry_run:
            summary = parsed.summary()
        else:
            db = Database(_config(config, database).database_url)
            db.create_schema()
            with db.session() as session:
                summary = import_into_session(session, parsed).as_dict()
                session.commit()
        typer.echo(json.dumps(summary, ensure_ascii=False, indent=2))
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc


@app.command("export-csv")
def export_csv(
    config: str = typer.Option("config/settings.yaml", help="Настройки проекта"),
    output: Annotated[Path, typer.Option(help="Файл экспорта CRM")] = DEFAULT_EXPORT,
    database: Annotated[Path | None, typer.Option(help="Отдельная локальная SQLite-база")] = None,
) -> None:
    """Выгрузить отклики, контакты и историю этапов в CSV."""
    db = Database(_config(config, database).database_url)
    db.create_schema()
    with db.session() as session:
        applications = [
            serialize_application(app) for app in CRMRepository(session).list_applications()
        ]
        content = csv_export(applications)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(content)
    typer.echo(f"Экспортировано откликов: {len(applications)}. Файл: {output.resolve()}")
