from __future__ import annotations

import calendar
import csv
import io
import json
import logging
import secrets
from datetime import UTC, date, datetime, time, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import parse_qs, unquote, urlsplit
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError

from job_intake.annotation.gold_ui import GoldLabelStore, GoldUIError
from job_intake.config.settings import AppConfig, load_yaml_mapping
from job_intake.crm.jobs import JOB_QUERY_FIELDS, job_detail, list_job_page
from job_intake.crm.repository import CRMRepository, serialize_application
from job_intake.crm.schemas import CRMConflictError
from job_intake.profiles import load_streams, with_other_stream
from job_intake.purpose import PurposeConflictError, update_human_purpose
from job_intake.storage.database import Database
from job_intake.storage.models import JobORM
from job_intake.storage.repository import JobArchiveConflictError, JobRepository
from job_intake.utils.csv import spreadsheet_safe_value

LOGGER = logging.getLogger(__name__)
LOCAL_ZONE = ZoneInfo("America/Sao_Paulo")
OPTIONS = {
    "stages": [
        {"value": value, "label": label}
        for value, label in [
            ("saved", "Сохранена"),
            ("contacted", "Контакт установлен"),
            ("applied", "Отклик отправлен"),
            ("hr_screen", "Разговор с HR"),
            ("team_interview", "Интервью с командой"),
            ("assessment", "Задание"),
            ("offer", "Оффер"),
            ("rejected", "Отказ"),
            ("withdrawn", "Снял заявку"),
            ("archived", "Архив"),
        ]
    ],
    "channels": [
        {"value": value, "label": label}
        for value, label in [
            ("unknown", "Неизвестно"),
            ("cold", "Холодный отклик"),
            ("referral", "Реферал"),
            ("recruiter", "Через рекрутера"),
        ]
    ],
    "referral_states": [
        {"value": value, "label": label}
        for value, label in [
            ("none", "Не планируется"),
            ("to_find", "Найти контакт"),
            ("requested", "Реферал запрошен"),
            ("referred", "Рекомендация передана"),
        ]
    ],
    "relationships": [
        {"value": value, "label": label}
        for value, label in [
            ("recruiter", "Рекрутер"),
            ("referral", "Реферальный контакт"),
            ("hiring_manager", "Руководитель команды"),
            ("other", "Другой контакт"),
        ]
    ],
}
APPLICATION_FIELDS = {
    "company",
    "title",
    "url",
    "profile_id",
    "channel",
    "referral_state",
    "applied_at",
    "next_action",
    "next_action_due",
    "notes",
    "cv_version",
}
CONTACT_FIELDS = {"name", "company", "email", "linkedin_url", "notes"}


class RequestError(ValueError):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def _datetime(value: object, *, end_of_day: bool = False) -> datetime | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise RequestError("Дата должна быть строкой ISO или пустым значением")
    try:
        if len(value) == 10:
            moment = datetime.combine(date.fromisoformat(value), time.min, LOCAL_ZONE)
            if end_of_day:
                moment += timedelta(days=1, microseconds=-1)
            return moment.astimezone(UTC)
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=LOCAL_ZONE)
        return moment.astimezone(UTC)
    except ValueError as exc:
        raise RequestError("Некорректная дата") from exc


def _fields(payload: dict, allowed: set[str]) -> dict:
    unknown = set(payload) - allowed
    if unknown:
        raise RequestError(f"Неизвестные поля: {', '.join(sorted(unknown))}")
    return dict(payload)


def _version(payload: dict) -> int:
    value = payload.pop("expected_version", None)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise RequestError("Обновите карточку перед сохранением")
    return value


def _job_archive(job: JobORM) -> dict:
    archived_at = job.archived_at
    if archived_at is not None and archived_at.tzinfo is None:
        archived_at = archived_at.replace(tzinfo=UTC)
    return {
        "job_uid": job.job_uid,
        "archived": archived_at is not None,
        "archived_at": archived_at.isoformat() if archived_at else None,
        "archive_version": job.archive_version,
    }


def _archive_batch(payload: dict) -> tuple[bool, list[tuple[str, int]]]:
    data = _fields(payload, {"archived", "jobs"})
    if type(data.get("archived")) is not bool:
        raise RequestError("Состояние архива должно быть true или false")
    jobs = data.get("jobs")
    if not isinstance(jobs, list) or not 1 <= len(jobs) <= 1000:
        raise RequestError("Выберите от 1 до 1000 вакансий")
    requests = []
    seen = set()
    for item in jobs:
        if not isinstance(item, dict):
            raise RequestError("Некорректный список вакансий")
        item = dict(item)
        version = _version(item)
        uid = _fields(item, {"job_uid"}).get("job_uid")
        if not isinstance(uid, str) or not uid.strip() or len(uid) > 100:
            raise RequestError("Некорректный ID вакансии")
        if uid in seen:
            raise RequestError("Вакансия повторяется в списке")
        seen.add(uid)
        requests.append((uid, version))
    return data["archived"], requests


def _identifier(value: object) -> int:
    if isinstance(value, str) and value.isdigit():
        value = int(value)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise RequestError("Некорректный ID записи")
    return value


def _reject_constant(_value: str) -> None:
    raise ValueError("JSON constants must be finite")


def _contact(row) -> dict:
    return {
        key: getattr(row, key, None)
        for key in ("id", "name", "company", "email", "linkedin_url", "notes", "version")
    }


def _company(row) -> dict:
    return {
        key: getattr(row, key, None) for key in ("id", "name", "url", "notes", "version")
    }


def _application_summary(row) -> dict:
    """Fields used by list cards; history/provenance are fetched on opening a card."""
    result = {key: getattr(row, key) for key in (
        "id", "job_uid", "company", "title", "url", "profile_id", "channel", "stage",
        "referral_state", "next_action", "notes", "cv_version", "version", "original_status",
    )}
    for key in ("applied_at", "next_action_due", "created_at", "updated_at"):
        value = getattr(row, key)
        if isinstance(value, datetime) and value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        result[key] = value.isoformat() if value else None
    result["contacts"] = [{"id": link.contact_id, "name": link.contact.name,
                           "relationship": link.relationship} for link in row.contact_links]
    return result


def csv_export(applications: list[dict]) -> bytes:
    fields = [
        "id",
        "company",
        "title",
        "url",
        "profile_id",
        "channel",
        "stage",
        "referral_state",
        "applied_at",
        "cv_version",
        "next_action",
        "next_action_due",
        "notes",
        "original_status",
        "contacts",
        "history",
    ]
    handle = io.StringIO(newline="")
    writer = csv.DictWriter(handle, fieldnames=fields)
    writer.writeheader()
    for app in applications:
        row = {key: app.get(key) for key in fields}
        row["contacts"] = json.dumps(app.get("contacts", []), ensure_ascii=False)
        row["history"] = json.dumps(app.get("history", []), ensure_ascii=False)
        row = {key: spreadsheet_safe_value(value) for key, value in row.items()}
        writer.writerow(row)
    return ("\ufeff" + handle.getvalue()).encode("utf-8")


class CRMService:
    def __init__(self, config: AppConfig, *, gold_path: Path | None = None,
                 discovery_path: Path | None = None) -> None:
        if gold_path is not None and discovery_path is not None:
            raise ValueError("--gold и --discovery нельзя использовать одновременно")
        self.config = config
        rules = load_yaml_mapping(config.rules_path)
        self.streams = with_other_stream(
            load_streams(rules, load_yaml_mapping(config.search_profiles_path)), rules,
        )
        self.database = Database(config.database_url)
        self.database.create_schema()
        self.csrf_token = secrets.token_urlsafe(32)
        self.discovery_path = discovery_path.expanduser().resolve() if discovery_path else None
        if self.discovery_path is not None:
            from job_intake.discovery.review import DiscoveryLabelStore

            self.gold_store = DiscoveryLabelStore(config, self.discovery_path)
        else:
            self.gold_store = GoldLabelStore(config, path=gold_path)

    def validate_profile(self, value: object, *, existing: str | None = None) -> str | None:
        if value in (None, ""):
            return None
        if value == existing or value in {s.id for s in self.streams}:
            return str(value)
        raise RequestError("Выберите активный профиль поиска")

    def state(self, query: dict[str, str]) -> dict:
        _fields(query, {"profile_id", "channel", "since", "until"} | JOB_QUERY_FIELDS
                | {"include_jobs", "include_entities"})
        archive_filter = query.get("job_archive") or "active"
        if archive_filter not in {"active", "archived", "all"}:
            raise RequestError("Неизвестный фильтр архива вакансий")
        today = datetime.now(LOCAL_ZONE).date()
        first = today.replace(day=1)
        last = today.replace(day=calendar.monthrange(today.year, today.month)[1])
        since_text = query.get("since") or first.isoformat()
        until_text = query.get("until") or last.isoformat()
        since, until = _datetime(since_text), _datetime(until_text, end_of_day=True)
        if since is None or until is None or since >= until:
            raise RequestError("Начало периода должно быть не позже конца")
        profile = self.validate_profile(query.get("profile_id"))
        channel = query.get("channel") or None
        if channel and channel not in {item["value"] for item in OPTIONS["channels"]}:
            raise RequestError("Неизвестный канал отклика")
        versions = {stream.id: stream.version for stream in self.streams}
        include_entities = query.get("include_entities", "true") == "true"
        if query.get("include_entities", "true") not in {"true", "false"}:
            raise RequestError("include_entities must be true or false")
        with self.database.session() as session:
            repo = CRMRepository(session)
            apps = (
                repo.list_applications(profile_id=profile, channel=channel)
                if include_entities else []
            )
            applications = [_application_summary(app) for app in apps]
            metrics = repo.funnel_metrics(
                profile_id=profile, channel=channel, since=since, until=until
            )
            page = {"jobs": [], "total": 0, "offset": 0, "limit": 100, "has_more": False}
            if query.get("include_jobs", "true") not in {"true", "false"}:
                raise RequestError("include_jobs must be true or false")
            if query.get("include_jobs", "true") == "true":
                page = list_job_page(session, versions, {
                    key: value for key, value in query.items() if key in JOB_QUERY_FIELDS
                }, {stream.id: stream.version_components for stream in self.streams})
            raw = metrics
            metrics = {
                **raw,
                "applications": raw["total"],
                "applied": raw["dated_reached_stages"].get("applied", 0),
                "hr_screen": raw["dated_reached_stages"].get("hr_screen", 0),
                "team_interview": raw["dated_reached_stages"].get("team_interview", 0),
                "offer": raw["dated_reached_stages"].get("offer", 0),
                "unknown_dates": raw["unknown_date_reached_stages"].get("team_interview", 0),
                "unknown_by_stage": raw["unknown_date_reached_stages"],
                "goal": self.config.crm.team_interview_goal,
                "weekly_hours": self.config.crm.weekly_time_budget_hours,
            }
            return {
                "applications": applications,
                "contacts": [_contact(c) for c in repo.list_contacts()] if include_entities else [],
                "companies": (
                    [_company(c) for c in repo.list_companies()] if include_entities else []
                ),
                "jobs": page["jobs"],
                "job_page": {key: value for key, value in page.items() if key != "jobs"},
                "profiles": [
                    {
                        "id": s.id,
                        "name": s.name,
                        "threshold_a": s.scoring.threshold_a,
                        "threshold_b": s.scoring.threshold_b,
                    }
                    for s in self.streams
                ],
                "metrics": metrics,
                "actions": [
                    _application_summary(a)
                    for a in repo.due_actions(as_of=today)
                    if (not profile or a.profile_id == profile)
                    and (not channel or a.channel == channel)
                ],
                "as_of": today.isoformat(),
                "period": {"since": since_text, "until": until_text},
                "options": OPTIONS,
                "csrf_token": self.csrf_token,
                "labeling_mode": "discovery" if self.discovery_path is not None else "gold",
            }

    def jobs(self, query: dict[str, str]) -> dict:
        _fields(query, JOB_QUERY_FIELDS)
        if query.get("profile_id"):
            self.validate_profile(query["profile_id"])
        with self.database.session() as session:
            return list_job_page(session, {s.id: s.version for s in self.streams}, query,
                                 {s.id: s.version_components for s in self.streams})

    def gold_cases(self, query: dict[str, str]) -> dict:
        return self.gold_store.list_cases(query)

    def gold_case(self, uid: str) -> dict:
        return self.gold_store.detail(uid)

    def discovery_report(self) -> dict:
        if self.discovery_path is None:
            raise RequestError("CRM запущена без --discovery", 404)
        from job_intake.discovery.report import build_report

        return build_report(self.discovery_path)

    def metrics(self, query: dict[str, str]) -> dict:
        _fields(query, {"profile_id", "channel", "since", "until"})
        result = self.state({**query, "include_jobs": "false", "include_entities": "false"})
        return {key: result[key] for key in ("metrics", "actions", "as_of", "period")}

    def job(self, uid: str) -> dict:
        with self.database.session() as session:
            result = job_detail(session, {s.id: s.version for s in self.streams}, uid,
                                {s.id: s.version_components for s in self.streams})
            if result is None:
                raise RequestError("Вакансия не найдена", 404)
            return result

    def application(self, application_id: int) -> dict:
        with self.database.session() as session:
            row = CRMRepository(session).get_application(application_id)
            if row is None:
                raise RequestError("Отклик не найден", 404)
            return serialize_application(row)

    def export_applications(self, query: dict[str, str]) -> list[dict]:
        _fields(query, {"profile_id", "channel", "since", "until", "job_archive"})
        profile = self.validate_profile(query.get("profile_id"))
        with self.database.session() as session:
            return [serialize_application(a) for a in CRMRepository(session).list_applications(
                profile_id=profile, channel=query.get("channel") or None,
            )]

    def mutate(self, method: str, path: str, payload: dict) -> dict:
        if not isinstance(payload, dict):
            raise RequestError("Ожидается JSON-объект")
        payload = dict(payload)
        parts = path.strip("/").split("/")
        if method == "PATCH" and len(parts) == 3 and parts[:2] == ["api", "gold"]:
            return self.gold_store.save(unquote(parts[2]), payload)
        with self.database.session() as session:
            repo = CRMRepository(session)
            if (method == "PATCH" and len(parts) == 4 and parts[:2] == ["api", "jobs"]
                    and parts[3] == "purpose"):
                uid = unquote(parts[2])
                if not uid or len(uid) > 100:
                    raise RequestError("Некорректный ID вакансии")
                if session.scalar(select(JobORM.id).where(JobORM.job_uid == uid)) is None:
                    raise RequestError("Вакансия не найдена", 404)
                try:
                    purpose = update_human_purpose(session, uid, payload)
                except PurposeConflictError as exc:
                    raise RequestError(
                        "Назначение или исходник изменён. Обновите карточку перед сохранением", 409,
                    ) from exc
                except ValueError as exc:
                    raise RequestError(str(exc)) from exc
                result = {"job_uid": uid, "purpose": purpose}
            elif method == "PATCH" and path == "/api/jobs/archive":
                archived, requests = _archive_batch(payload)
                jobs = JobRepository(session)
                results = []
                for uid, version in requests:
                    try:
                        job = jobs.set_archived(uid, archived, expected_version=version)
                    except JobArchiveConflictError as exc:
                        raise RequestError(
                            "Архив вакансии изменён. Обновите список; группа не изменена", 409
                        ) from exc
                    if job is None:
                        raise RequestError("Вакансия не найдена; группа не изменена", 404)
                    results.append(_job_archive(job))
                result = {"jobs": results, "count": len(results)}
            elif (
                method == "PATCH" and len(parts) == 4 and parts[:2] == ["api", "jobs"]
                and parts[3] == "archive"
            ):
                version = _version(payload)
                data = _fields(payload, {"archived"})
                if type(data.get("archived")) is not bool:
                    raise RequestError("Состояние архива должно быть true или false")
                if not parts[2] or len(parts[2]) > 100:
                    raise RequestError("Некорректный ID вакансии")
                try:
                    job = JobRepository(session).set_archived(
                        parts[2], data["archived"], expected_version=version
                    )
                except JobArchiveConflictError as exc:
                    raise RequestError("Архив вакансии изменён. Обновите список", 409) from exc
                if job is None:
                    raise RequestError("Вакансия не найдена", 404)
                result = _job_archive(job)
            elif method == "POST" and path == "/api/applications/from-job":
                data = _fields(payload, {"job_uid", "profile_id"})
                if (
                    not isinstance(data.get("job_uid"), str)
                    or not data["job_uid"]
                    or len(data["job_uid"]) > 100
                ):
                    raise RequestError("Некорректный ID вакансии")
                profile = self.validate_profile(data.get("profile_id"))
                job = session.scalar(select(JobORM).where(JobORM.job_uid == data["job_uid"]))
                if job is None:
                    raise RequestError("Вакансия не найдена", 404)
                if not job.role_label:
                    raise RequestError("Сначала определите метку вакансии")
                if profile is not None and profile != job.role_label:
                    raise RequestError("Профиль отклика должен совпадать с меткой вакансии")
                profile = job.role_label
                row = repo.create_from_job(data.get("job_uid"), profile_id=profile)
                result = serialize_application(row)
            elif method == "POST" and path == "/api/applications":
                data = _fields(payload, APPLICATION_FIELDS | {"stage"})
                if data.get("stage", "saved") != "saved":
                    raise RequestError("Сначала создайте карточку, затем добавьте этап в историю")
                data["profile_id"] = self.validate_profile(data.get("profile_id"))
                if "applied_at" in data:
                    data["applied_at"] = _datetime(data["applied_at"])
                data["stage_occurred_at"] = datetime.now(UTC)
                row = repo.create_application(**data)
                result = serialize_application(row)
            elif len(parts) >= 3 and parts[:2] == ["api", "applications"]:
                try:
                    application_id = int(parts[2])
                except ValueError as exc:
                    raise RequestError("Некорректный ID отклика", 404) from exc
                row = repo.get_application(application_id)
                if row is None:
                    raise RequestError("Отклик не найден", 404)
                if method == "PATCH" and len(parts) == 3:
                    version = _version(payload)
                    data = _fields(payload, APPLICATION_FIELDS)
                    if "profile_id" in data:
                        data["profile_id"] = self.validate_profile(
                            data["profile_id"], existing=row.profile_id
                        )
                    if "applied_at" in data:
                        data["applied_at"] = _datetime(data["applied_at"])
                    row = repo.update_application(application_id, expected_version=version, **data)
                elif method == "POST" and len(parts) == 4 and parts[3] == "stage":
                    version = _version(payload)
                    data = _fields(payload, {"stage", "occurred_at", "note"})
                    if "occurred_at" in data:
                        data["occurred_at"] = _datetime(data["occurred_at"])
                    row = repo.mark_stage(
                        application_id,
                        data.pop("stage", None),
                        expected_version=version,
                        **data,
                    )
                elif method in {"POST", "DELETE"} and len(parts) == 4 and parts[3] == "contacts":
                    data = _fields(payload, {"contact_id", "relationship", "expected_version"})
                    if _version(data) != row.version:
                        raise RequestError("Карточка изменена. Обновите её перед сохранением", 409)
                    operation = repo.link_contact if method == "POST" else repo.unlink_contact
                    operation(
                        application_id,
                        _identifier(data.get("contact_id")),
                        relationship=data.get("relationship", "other"),
                    )
                else:
                    raise RequestError("Маршрут не найден", 404)
                session.flush()
                result = serialize_application(row)
            elif method == "POST" and path == "/api/contacts":
                result = _contact(repo.upsert_contact(**_fields(payload, CONTACT_FIELDS)))
            elif method == "PATCH" and len(parts) == 3 and parts[:2] == ["api", "contacts"]:
                version = _version(payload)
                row = repo.update_contact(
                    int(parts[2]), expected_version=version, **_fields(payload, CONTACT_FIELDS)
                )
                result = _contact(row)
            elif method == "POST" and path == "/api/companies":
                result = _company(repo.upsert_company(**_fields(payload, {"name", "url", "notes"})))
            elif method == "PATCH" and len(parts) == 3 and parts[:2] == ["api", "companies"]:
                version = _version(payload)
                result = _company(repo.update_company(
                    _identifier(parts[2]), expected_version=version,
                    **_fields(payload, {"name", "url", "notes"}),
                ))
            else:
                raise RequestError("Маршрут не найден", 404)
            session.commit()
            return result

    def import_xlsx(self, content: bytes, name: str, *, dry_run: bool) -> dict:
        from job_intake.crm.importer import import_into_session, parse_workbook

        if not name.lower().endswith(".xlsx"):
            raise RequestError("Выберите файл .xlsx")
        with TemporaryDirectory(prefix="jobcrm-import-") as directory:
            source = Path(directory) / "source.xlsx"
            source.write_bytes(content)
            parsed = parse_workbook(source, source_name=Path(name).name)
        if dry_run:
            return parsed.summary()
        with self.database.session() as session:
            result = import_into_session(session, parsed).as_dict()
            session.commit()
            return result


class CRMHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, service: CRMService, port: int = 8765) -> None:
        self.service = service
        super().__init__(("127.0.0.1", port), CRMRequestHandler)
        self.origin = f"http://127.0.0.1:{self.server_port}"


class CRMRequestHandler(BaseHTTPRequestHandler):
    server: CRMHTTPServer

    def log_message(self, _format, *_args) -> None:
        # Personal notes, contact details and URL query strings do not belong in logs.
        pass

    def _respond(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; "
            "frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
        )
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload: object) -> None:
        self._respond(
            status,
            json.dumps(payload, ensure_ascii=False, allow_nan=False).encode(),
            "application/json; charset=utf-8",
        )

    def _dispatch(self, method: str) -> None:
        try:
            parsed_url = urlsplit(self.path)
            if self.headers.get("Host") != f"127.0.0.1:{self.server.server_port}":
                raise RequestError("Недопустимый адрес сервера", 403)
            if method != "GET":
                origin = self.headers.get("Origin")
                if origin is not None and origin != self.server.origin:
                    raise RequestError("Запрос разрешён только из локального интерфейса", 403)
                if self.headers.get("Sec-Fetch-Site") in ("cross-site", "same-site"):
                    raise RequestError("Откройте локальный интерфейс CRM", 403)
                token = self.headers.get("X-CSRF-Token", "")
                if not secrets.compare_digest(token, self.server.service.csrf_token):
                    raise RequestError("Обновите страницу перед сохранением", 403)
            if method == "GET" and parsed_url.path == "/":
                from job_intake.crm.ui import render_crm_page

                self._respond(200, render_crm_page().encode(), "text/html; charset=utf-8")
                return
            query_lists = parse_qs(parsed_url.query, keep_blank_values=True)
            if any(len(values) != 1 for values in query_lists.values()):
                raise RequestError("Повторяющиеся параметры запроса")
            query = {key: values[0] for key, values in query_lists.items()}
            if method == "GET" and parsed_url.path == "/api/state":
                self._json(200, self.server.service.state(query))
                return
            if method == "GET" and parsed_url.path == "/api/metrics":
                self._json(200, self.server.service.metrics(query))
                return
            if method == "GET" and parsed_url.path == "/api/jobs":
                self._json(200, self.server.service.jobs(query))
                return
            if method == "GET" and parsed_url.path == "/api/gold":
                self._json(200, self.server.service.gold_cases(query))
                return
            if method == "GET" and parsed_url.path == "/api/discovery/report":
                _fields(query, set())
                self._json(200, self.server.service.discovery_report())
                return
            get_parts = parsed_url.path.strip("/").split("/")
            if method == "GET" and len(get_parts) == 3:
                if get_parts[:2] == ["api", "gold"]:
                    _fields(query, set())
                    self._json(200, self.server.service.gold_case(unquote(get_parts[2])))
                    return
                if get_parts[:2] == ["api", "jobs"]:
                    _fields(query, set())
                    self._json(200, self.server.service.job(unquote(get_parts[2])))
                    return
                if get_parts[:2] == ["api", "applications"]:
                    _fields(query, set())
                    self._json(200, self.server.service.application(_identifier(get_parts[2])))
                    return
            if method == "GET" and parsed_url.path == "/api/export.csv":
                applications = self.server.service.export_applications(query)
                self._respond(200, csv_export(applications), "text/csv; charset=utf-8")
                return
            if method not in {"POST", "PATCH", "DELETE"}:
                raise RequestError("Маршрут не найден", 404)
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError as exc:
                raise RequestError("Некорректная длина запроса") from exc
            is_import = method == "POST" and parsed_url.path == "/api/import-xlsx"
            maximum = 25 * 1024 * 1024 if is_import else 256 * 1024
            if length < 1 or length > maximum:
                raise RequestError("Файл или запрос слишком большой", 413)
            content = self.rfile.read(length)
            if len(content) != length:
                raise RequestError("Запрос получен не полностью")
            if is_import:
                _fields(query, {"dry_run"})
                name = unquote(self.headers.get("X-File-Name", "JobCRM.xlsx"))
                result = self.server.service.import_xlsx(
                    content, name, dry_run=query.get("dry_run", "false") == "true"
                )
            else:
                if self.headers.get_content_type() != "application/json":
                    raise RequestError("Ожидается application/json", 415)
                try:
                    payload = json.loads(content, parse_constant=_reject_constant)
                except (ValueError, UnicodeError) as exc:
                    raise RequestError("Некорректный JSON") from exc
                result = self.server.service.mutate(method, parsed_url.path, payload)
            self._json(200, result)
        except (RequestError, GoldUIError) as exc:
            self._json(exc.status, {"error": str(exc)})
        except CRMConflictError:
            self._json(409, {"error": "Карточка изменена. Обновите её перед сохранением"})
        except (StaleDataError, IntegrityError) as exc:
            LOGGER.info("crm_write_conflict type=%s", type(exc).__name__)
            self._json(409, {"error": "Данные изменились. Обновите карточку перед сохранением"})
        except (ValueError, TypeError, KeyError, LookupError) as exc:
            status = 409 if "version" in str(exc).casefold() else 400
            self._json(status, {"error": str(exc)})
        except Exception:
            LOGGER.exception("crm_request_failed")
            self._json(500, {"error": "Не удалось выполнить действие. Данные не изменены"})

    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def do_PATCH(self) -> None:
        self._dispatch("PATCH")

    def do_DELETE(self) -> None:
        self._dispatch("DELETE")


def serve_crm(config: AppConfig, *, port: int = 8765, gold_path: Path | None = None,
              discovery_path: Path | None = None) -> None:
    server = CRMHTTPServer(CRMService(config, gold_path=gold_path,
                                    discovery_path=discovery_path), port)
    LOGGER.info("crm_started url=%s", server.origin)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
