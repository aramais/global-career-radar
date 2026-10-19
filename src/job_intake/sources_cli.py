"""Bounded source operations; stored health and refresh previews are strictly read-only."""

from __future__ import annotations

import json
import re
import sqlite3
from collections import defaultdict, deque
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from urllib.parse import quote, quote_plus, urlparse

import requests
import typer

from job_intake.adapters.ats import AshbyAdapter, GreenhouseAdapter, LeverAdapter
from job_intake.adapters.base import SourceAuthenticationError
from job_intake.adapters.company_watchlist import CompanyWatchlistAdapter
from job_intake.adapters.dailyremote import DailyRemoteAdapter, configure_dailyremote_session
from job_intake.adapters.factory import build_adapter
from job_intake.adapters.html_page import HtmlPageAdapter
from job_intake.annotation.eval_runner import read_saved_records, sqlite_path
from job_intake.config.settings import (
    AppConfig,
    SourceDefinition,
    load_app_config,
    load_yaml_mapping,
)
from job_intake.models.job import JobRecord
from job_intake.profiles import load_streams, profile_query_order
from job_intake.utils.http import build_session

app = typer.Typer(
    help="Inspect source health and refresh saved vacancies without AI or notifications",
)


def configured_sources(settings: AppConfig) -> list[SourceDefinition]:
    streams = load_streams(
        load_yaml_mapping(settings.rules_path), load_yaml_mapping(settings.search_profiles_path),
    )
    result = []
    for source in settings.sources:
        if not source.enabled:
            continue
        params = dict(source.params)
        if source.type == "dailyremote" and params.get("use_profile_queries"):
            # Rotate through streams before query number two from any one stream.
            ordered = profile_query_order(streams)
            template = params.get(
                "search_url_template", "https://dailyremote.com/remote-jobs?search={query}",
            )
            params["search_urls"] = [template.replace("{query}", quote_plus(q)) for q in ordered]
        result.append(replace(source, params=params))
    return result


def stored_health(settings: AppConfig, *, limit: int = 50) -> dict:
    """Never initialise a database merely to report that no health runs exist."""
    path = sqlite_path(settings.database_url)
    if not path.is_file():
        return {"mode": "stored", "database_exists": False, "runs": [], "api_calls": 0}
    connection = sqlite3.connect(f"file:{quote(str(path), safe='/')}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA query_only=ON")
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(source_runs)")}
        if not columns:
            return {"mode": "stored", "database_exists": True, "runs": [],
                    "status": "no_recorded_source_runs", "api_calls": 0}
        fields = [name for name in (
            "id", "source", "status", "snapshot_complete", "record_count", "details",
            "started_at", "finished_at",
        ) if name in columns]
        query = "SELECT " + ", ".join(fields) + " FROM source_runs ORDER BY id DESC LIMIT ?"
        runs = []
        for row in connection.execute(query, (limit,)):
            item = dict(row)
            if isinstance(item.get("details"), str):
                try:
                    item["details"] = json.loads(item["details"])
                except ValueError:
                    item["details"] = {"status": "invalid_saved_details"}
            runs.append(item)
        return {"mode": "stored", "database_exists": True, "runs": runs, "api_calls": 0}
    finally:
        connection.close()


def live_health(settings: AppConfig, *, source_name: str | None = None) -> dict:
    result = []
    for source in configured_sources(settings):
        if source_name and source.name != source_name:
            continue
        params = {**source.params, "max_pages": 1, "max_detail_fetches": 1}
        if source.type == "dailyremote":
            params["search_urls"] = params.get("search_urls", [])[:1]
        adapter = build_adapter(replace(source, params=params))
        error = None
        try:
            adapter.fetch_jobs()
        except Exception as exc:
            error = type(exc).__name__
        result.append({**adapter.source_health, "snapshots": adapter.board_snapshots,
                       "error": error})
    return {"mode": "live_probe", "sources": result, "jobs_imported": 0,
            "database_writes": 0, "ai_calls": 0}


def refresh_candidates(
    settings: AppConfig, *, limit: int, stale_days: float, source_name: str | None = None,
    now: datetime | None = None,
) -> list[tuple[str, JobRecord, str]]:
    now = now or datetime.now(UTC)
    path, records, _problems = read_saved_records(settings.database_url, limit=None)
    connection = sqlite3.connect(f"file:{quote(str(path), safe='/')}?mode=ro", uri=True)
    try:
        connection.execute("PRAGMA query_only=ON")
        columns = {row[1] for row in connection.execute("PRAGMA table_info(jobs)")}
        dates = dict(connection.execute("SELECT job_uid, last_seen_at FROM jobs")) if (
            "last_seen_at" in columns
        ) else {}
    finally:
        connection.close()
    groups = defaultdict(deque)
    for uid, record in records:
        if source_name and record.source != source_name and not record.source.startswith(
            source_name + ":",
        ):
            continue
        metadata = record.source_metadata
        incomplete = metadata.get("description_complete") is False or not (
            record.description_clean or record.description_raw
        ).strip()
        observed = metadata.get("detail_fetched_at") or dates.get(uid)
        try:
            date = datetime.fromisoformat(observed.replace("Z", "+00:00"))
            date = date.replace(tzinfo=UTC) if date.tzinfo is None else date
            expired = now - date >= timedelta(days=stale_days)
        except (AttributeError, TypeError, ValueError):
            expired = True
        if not incomplete and not expired:
            continue
        reason = "incomplete" if incomplete else "expired"
        context = metadata.get("search_context") or metadata.get("listing_url") or record.source
        groups[str(context)].append((uid, record, reason))
    selected = []
    while groups and len(selected) < limit:
        for key in list(groups):
            selected.append(groups[key].popleft())
            if not groups[key]:
                del groups[key]
            if len(selected) == limit:
                break
    return selected


def _adapter_for_record(settings: AppConfig, record: JobRecord):
    for source in configured_sources(settings):
        if source.name == record.source:
            return build_adapter(source)
        if source.type == "watchlist" and record.source.startswith(source.name + ":"):
            watchlist = CompanyWatchlistAdapter(source.name, source.params)
            companies = load_yaml_mapping(source.params["watchlist_path"]).get("companies", [])
            for company in companies:
                if (isinstance(company, dict) and company.get("enabled", True)
                        and source.name + ":" + str(company.get("name")) == record.source):
                    return watchlist._build_company_adapter(company)
    # A renamed watchlist entry can still safely refresh the exact saved ATS board.
    metadata = record.source_metadata
    kind = metadata.get("ats")
    keys = {"greenhouse": "board_token", "ashby": "board_name", "lever": "site"}
    if kind in keys and metadata.get(keys[kind]):
        params = {keys[kind]: metadata[keys[kind]], "company": record.company}
        if kind == "lever" and "api.eu.lever.co" in str(metadata.get("api_url", "")):
            params["region"] = "eu"
        return build_adapter(SourceDefinition(record.source, kind, params=params))
    return None


def fetch_saved_detail(settings: AppConfig, record: JobRecord, board_cache: dict) -> JobRecord:
    adapter = _adapter_for_record(settings, record)
    refreshed = deepcopy(record)
    refreshed.source_metadata.pop("detail_outcome", None)
    refreshed.source_metadata.pop("detail_error", None)
    # A prior successful fetch is not evidence that this attempt succeeded.
    # The candidate is persisted only after the detail parser confirms this read.
    refreshed.source_metadata["description_complete"] = False
    if isinstance(adapter, DailyRemoteAdapter):
        with build_session() as session:
            configure_dailyremote_session(session, adapter.params)
            adapter._fetch_detail(session, record.original_url, refreshed)
    elif isinstance(adapter, HtmlPageAdapter):
        with build_session(headers=adapter.params.get("headers")) as session:
            adapter._fetch_detail(session, refreshed)
    elif isinstance(adapter, GreenhouseAdapter | AshbyAdapter | LeverAdapter):
        key = (type(adapter).__name__, json.dumps(adapter.params, sort_keys=True))
        if key not in board_cache:
            board_cache[key] = adapter.fetch_jobs()
        selected = next((job for job in board_cache[key] if (
            record.source_job_id and job.source_job_id == record.source_job_id
            or job.original_url.rstrip("/") == record.original_url.rstrip("/")
        )), None)
        if selected is None:
            raise ValueError("saved_requisition_not_in_board_snapshot")
        refreshed = deepcopy(selected)
        refreshed.source = record.source
    else:
        raise ValueError("saved_source_has_no_configured_detail_adapter")
    outcome = refreshed.source_metadata.get("detail_outcome")
    if outcome and outcome != "success":
        raise ValueError(str(outcome))
    if refreshed.source_metadata.get("description_complete") is not True:
        raise ValueError("detail_description_incomplete")
    refreshed.source_metadata["detail_fetched_at"] = datetime.now(UTC).isoformat()
    return refreshed


def refresh_details(
    settings: AppConfig, *, limit: int = 20, stale_days: float = 7,
    source_name: str | None = None, dry_run: bool = False,
) -> dict:
    selected = refresh_candidates(
        settings, limit=limit, stale_days=stale_days, source_name=source_name,
    )
    result = {"operation": "refresh_details", "dry_run": dry_run, "selected": len(selected),
              "persisted": 0, "ai_calls": 0, "notifications": 0, "outcomes": []}
    if dry_run:
        result["outcomes"] = [{"job_uid": uid, "reason": reason} for uid, _, reason in selected]
        return result
    if not selected:
        return result
    from job_intake.pipeline import JobIntakePipeline

    offline = replace(
        settings, sources=[], llm=replace(settings.llm, enabled=False),
        annotation=replace(settings.annotation, ai_enabled=False),
        telegram=replace(settings.telegram, enabled=False),
    )
    pipeline = JobIntakePipeline(offline)
    cache = {}
    for uid, record, reason in selected:
        outcome = {"job_uid": uid, "reason": reason}
        try:
            refreshed = fetch_saved_detail(settings, record, cache)
            refreshed._canonical_job_uid = uid
            items, keys, _pending = pipeline._evaluate_record(
                refreshed, pipeline._stats(), use_llm=False,
            )
            pipeline._save(items, keys, observed=False, queue_alert=False)
            result["persisted"] += 1
            outcome["outcome"] = "success"
        except Exception as exc:
            outcome.update(outcome="auth_required" if isinstance(exc, SourceAuthenticationError)
                           else "error", error=type(exc).__name__)
        result["outcomes"].append(outcome)
        from job_intake.storage.source_lifecycle import record_source_snapshot

        with pipeline.database.session() as session:
            record_source_snapshot(session, {
                "source": record.source, "scope": "detail", "board_id": record.source,
                "status": "success" if outcome["outcome"] == "success" else outcome["outcome"],
                "snapshot_complete": False, "seen_job_ids": [record.source_job_id],
                "seen_urls": [record.original_url], "refresh_outcome": outcome,
            })
            session.commit()
    return result


def recheck_urls(settings: AppConfig, *, limit: int = 20, source_name: str | None = None) -> dict:
    _, records, _problems = read_saved_records(settings.database_url, limit=None)
    selected = [(uid, record) for uid, record in records if not source_name or (
        record.source == source_name or record.source.startswith(source_name + ":")
    )][:limit]
    outcomes = []
    for uid, record in selected:
        outcome = {"job_uid": uid, "http_status": None, "outcome": "request_error"}
        try:
            parsed = urlparse(record.original_url)
            if parsed.scheme not in {"https", "http"} or not parsed.netloc:
                raise ValueError("invalid_saved_url")
            adapter = _adapter_for_record(settings, record)
            with build_session() as session:
                if isinstance(adapter, DailyRemoteAdapter):
                    configure_dailyremote_session(session, adapter.params)
                response = session.get(record.original_url, timeout=20, allow_redirects=False)
                try:
                    code = response.status_code
                    outcome["http_status"] = code
                    if code in {401, 403}:
                        outcome["outcome"] = "auth_required"
                    elif code in {404, 410}:
                        outcome["outcome"] = "not_found_unconfirmed"
                    elif 300 <= code < 400:
                        outcome["outcome"] = "redirect_unchecked"
                    elif code >= 400:
                        outcome["outcome"] = "http_error"
                    elif re.search(
                        r"no longer accepting applications|position has been filled|"
                        r"this job is closed|vaga encerrada|esta vaga (?:foi|está) encerrada",
                        response.text, re.I,
                    ):
                        outcome["outcome"] = "explicit_closed_page"
                    else:
                        outcome["outcome"] = "reachable"
                finally:
                    response.close()
        except SourceAuthenticationError:
            outcome["outcome"] = "auth_required"
        except requests.Timeout:
            outcome["outcome"] = "timeout"
        except Exception as exc:
            outcome["error"] = type(exc).__name__
        outcomes.append(outcome)
    return {"operation": "recheck_urls", "outcomes": outcomes, "database_writes": 0,
            "automatic_closures": 0, "ai_calls": 0, "notifications": 0}


@app.command("check")
def check_command(config: str = typer.Option("config/settings.yaml"),
                  live: bool = typer.Option(False, help="Explicit bounded external source probe"),
                  source: str | None = typer.Option(None),
                  limit: int = typer.Option(50, min=1, max=1000)) -> None:
    """Read stored source health by default; --live fetches without importing jobs."""
    try:
        settings = load_app_config(config)
        result = (live_health(settings, source_name=source) if live
                  else stored_health(settings, limit=limit))
    except (ValueError, OSError, sqlite3.Error) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(json.dumps(result, ensure_ascii=False))


@app.command("refresh-details")
def refresh_command(config: str = typer.Option("config/settings.yaml"),
                    limit: int = typer.Option(20, min=1, max=1000),
                    stale_days: float = typer.Option(7, min=0),
                    source: str | None = typer.Option(None),
                    dry_run: bool = typer.Option(
                        False, help="Read-only selection; no fetch/migration",
                    )):
    """Refresh incomplete/expired saved sources, then score offline without alerts."""
    try:
        result = refresh_details(load_app_config(config), limit=limit, stale_days=stale_days,
                                 source_name=source, dry_run=dry_run)
    except (ValueError, OSError, sqlite3.Error) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(json.dumps(result, ensure_ascii=False))


@app.command("recheck-urls")
def recheck_command(config: str = typer.Option("config/settings.yaml"),
                    limit: int = typer.Option(20, min=1, max=1000),
                    source: str | None = typer.Option(None)) -> None:
    """Emit typed reachability evidence; 404 alone never closes a vacancy."""
    try:
        result = recheck_urls(load_app_config(config), limit=limit, source_name=source)
    except (ValueError, OSError, sqlite3.Error) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(json.dumps(result, ensure_ascii=False))
