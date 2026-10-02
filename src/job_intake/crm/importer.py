"""Import historical JobCRM data while retaining its meaning and provenance."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from job_intake.crm.xlsx_reader import Cell, Sheet, WorkbookError, read_workbook

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
STATUS_MAP = {
    "application sent": "applied",
    "написал рекрутеру": "contacted",
    "rejected": "rejected",
    "hr screening": "hr_screen",
    "not remote": "archived",
    "not relevant": "archived",
    "too difficult": "archived",
    "duplicate": "archived",
    "": "saved",
}


@dataclass(frozen=True)
class ApplicationRow:
    sheet: str
    row: int
    company: str
    title: str
    job_url: str | None
    recruiter_raw: str
    recruiter_urls: tuple[str, ...]
    raw_status: str
    stage: str
    disposition_reason: str | None
    raw_values: dict[str, str]
    raw_hyperlinks: dict[str, str]


@dataclass(frozen=True)
class CompanyRow:
    sheet: str
    row: int
    name: str
    careers_url: str | None
    raw_values: dict[str, str]
    raw_hyperlinks: dict[str, str]


@dataclass(frozen=True)
class ParsedWorkbook:
    filename: str
    fingerprint: str
    applications: tuple[ApplicationRow, ...]
    companies: tuple[CompanyRow, ...]
    sheet_names: tuple[str, ...]
    warnings: tuple[str, ...]

    def summary(self) -> dict:
        """Return counts only, suitable for dry-run output without personal rows."""
        return {
            "filename": self.filename,
            "fingerprint": self.fingerprint,
            "applications": len(self.applications),
            "companies": len(self.companies),
            "contacts": sum(bool(item.recruiter_raw.strip()) for item in self.applications),
            "stage_counts": dict(sorted(Counter(item.stage for item in self.applications).items())),
            "warnings": list(self.warnings),
        }


@dataclass
class ImportSummary:
    applications_total: int
    companies_total: int
    applications_created: int = 0
    applications_skipped: int = 0
    companies_created: int = 0
    companies_existing: int = 0
    contacts_created: int = 0
    contacts_existing: int = 0
    contacts_linked: int = 0
    stage_counts: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


def _normalized(text: str) -> str:
    return " ".join(text.split()).casefold()


def _url(value: str | None) -> str | None:
    if not value:
        return None
    value = value.strip()
    if len(value) > 4000 or any(char.isspace() or ord(char) < 32 for char in value):
        return None
    try:
        parsed = urlsplit(value)
        port = parsed.port
        if (
            parsed.scheme.lower() in {"http", "https"}
            and parsed.hostname
            and parsed.username is None
            and parsed.password is None
            and (port is None or 1 <= port <= 65535)
        ):
            return value
    except ValueError:
        pass
    return None


def _headers(sheet: Sheet) -> tuple[int, dict[str, str]]:
    for number, row in sorted(sheet.rows.items()):
        if any(cell.value.strip() for cell in row.values()):
            headers: dict[str, str] = {}
            for column, cell in row.items():
                header = _normalized(cell.value)
                if not header:
                    continue
                if header in headers:
                    raise WorkbookError(f"Duplicate header in worksheet: {sheet.name}")
                headers[header] = column
            return number, headers
    raise WorkbookError(f"Empty worksheet: {sheet.name}")


def _source_values(row: dict[str, Cell], headers: dict[str, str]) -> dict[str, str]:
    return {header: row.get(column, Cell("")).value for header, column in headers.items()}


def _source_hyperlinks(row: dict[str, Cell], headers: dict[str, str]) -> dict[str, str]:
    return {
        header: cell.hyperlink
        for header, column in headers.items()
        if (cell := row.get(column)) is not None and cell.hyperlink is not None
    }


def _urls_in_recruiter(cell: Cell) -> tuple[str, ...]:
    candidates = [cell.hyperlink, *URL_RE.findall(cell.value)]
    result: list[str] = []
    for candidate in candidates:
        valid = _url(candidate)
        if valid is not None and valid not in result:
            result.append(valid)
    return tuple(result)


def _parse_applications(sheet: Sheet, warnings: list[str]) -> tuple[ApplicationRow, ...]:
    header_row, headers = _headers(sheet)
    required = {"position", "company", "link", "status", "recruiter"}
    missing = required - headers.keys()
    if missing:
        raise WorkbookError(f"Applications headers missing: {', '.join(sorted(missing))}")
    result: list[ApplicationRow] = []
    for number, row in sorted(sheet.rows.items()):
        if number <= header_row:
            continue
        raw = _source_values(row, headers)
        links = _source_hyperlinks(row, headers)
        if not any(value.strip() for value in raw.values()) and not links:
            continue
        company, title = raw["company"].strip(), raw["position"].strip()
        if not company or not title:
            raise WorkbookError(f"Applications row {number}: company and position are required")
        status = raw["status"]
        normalized_status = _normalized(status)
        stage = STATUS_MAP.get(normalized_status, "saved")
        if normalized_status not in STATUS_MAP:
            warnings.append(f"Applications row {number}: unrecognized status kept as saved")
        link_cell = row.get(headers["link"], Cell(""))
        job_url = _url(link_cell.hyperlink) or _url(link_cell.value)
        if not job_url:
            warnings.append(f"Applications row {number}: no usable HTTP(S) vacancy URL")
        recruiter = row.get(headers["recruiter"], Cell(""))
        result.append(
            ApplicationRow(
                sheet=sheet.name,
                row=number,
                company=company,
                title=title,
                job_url=job_url,
                recruiter_raw=recruiter.value,
                recruiter_urls=_urls_in_recruiter(recruiter),
                raw_status=status,
                stage=stage,
                disposition_reason=status if stage == "archived" else None,
                raw_values=raw,
                raw_hyperlinks=links,
            )
        )
    return tuple(result)


def _parse_companies(sheet: Sheet, warnings: list[str]) -> tuple[CompanyRow, ...]:
    header_row, headers = _headers(sheet)
    name_header = next(
        (key for key in ("название компании", "company", "company name", "name") if key in headers),
        None,
    )
    if name_header is None:
        raise WorkbookError("Companies worksheet is missing its company name header")
    url_header = next(
        (key for key in ("careers", "careers url", "career url", "url", "link") if key in headers),
        None,
    )
    result: list[CompanyRow] = []
    for number, row in sorted(sheet.rows.items()):
        if number <= header_row:
            continue
        raw = _source_values(row, headers)
        links = _source_hyperlinks(row, headers)
        if not any(value.strip() for value in raw.values()) and not links:
            continue
        name = raw[name_header].strip()
        if not name:
            raise WorkbookError(f"Companies row {number}: company name is required")
        cell = row.get(headers[url_header or name_header], Cell(""))
        careers_url = _url(cell.hyperlink) or (_url(cell.value) if url_header else None)
        if (cell.hyperlink or (url_header and cell.value.strip())) and careers_url is None:
            warnings.append(f"Companies row {number}: unusable careers URL retained in provenance")
        result.append(
            CompanyRow(
                sheet=sheet.name,
                row=number,
                name=name,
                careers_url=careers_url,
                raw_values=raw,
                raw_hyperlinks=links,
            )
        )
    return tuple(result)


def parse_workbook(path: Path | str, *, source_name: str | None = None) -> ParsedWorkbook:
    """Validate and parse completely before any database writes can begin."""
    path = Path(path)
    workbook = read_workbook(path)
    sheets = {_normalized(sheet.name): sheet for sheet in workbook.sheets}
    if len(sheets) != len(workbook.sheets):
        raise WorkbookError("Worksheet names must be distinct ignoring case and whitespace")
    if "applications" not in sheets:
        raise WorkbookError("Required Applications worksheet is missing")
    warnings = list(workbook.warnings)
    applications = _parse_applications(sheets["applications"], warnings)
    companies = _parse_companies(sheets["companies"], warnings) if "companies" in sheets else ()
    if "companies" not in sheets:
        warnings.append("Companies worksheet is absent")
    ignored = [
        sheet.name
        for sheet in workbook.sheets
        if _normalized(sheet.name)
        not in {
            "applications",
            "companies",
        }
    ]
    if ignored:
        warnings.append(f"Other worksheets are not imported: {', '.join(ignored)}")
    return ParsedWorkbook(
        filename=source_name.replace("\\", "/").rsplit("/", 1)[-1] if source_name else path.name,
        fingerprint=hashlib.sha256(path.read_bytes()).hexdigest(),
        applications=applications,
        companies=companies,
        sheet_names=tuple(sheet.name for sheet in workbook.sheets),
        warnings=tuple(warnings),
    )


def _source_key(source_id: str, sheet: str, identity: str, occurrence: int = 1) -> str:
    digest = hashlib.sha256(source_id.strip().encode("utf-8")).hexdigest()[:20]
    return f"xlsx:{digest}:{_normalized(sheet)}:{identity}:{occurrence}"


def _application_identity(company: str, title: str, url: str | None) -> str:
    from job_intake.crm.schemas import canonical_url_key

    payload = [_normalized(company), _normalized(title), canonical_url_key(url)]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode("utf-8")).hexdigest()


def _company_identity(name: str) -> str:
    return hashlib.sha256(_normalized(name).encode("utf-8")).hexdigest()


def _migrate_row_keys(session: Session, source_id: str) -> None:
    """Upgrade early row-based imports using source values, never edited fields."""
    from sqlalchemy import select

    from job_intake.crm.models import ApplicationORM

    digest = hashlib.sha256(source_id.strip().encode("utf-8")).hexdigest()[:20]
    prefix = f"xlsx:{digest}:"
    imported = list(
        session.scalars(
            select(ApplicationORM).where(
                ApplicationORM.source_key.startswith(prefix, autoescape=True),
            )
        )
    )
    legacy = []
    for application in imported:
        metadata = application.source_metadata or {}
        sheet, row = metadata.get("sheet"), metadata.get("row")
        if metadata.get("import_format") != "jobcrm_xlsx" or not isinstance(row, int):
            continue
        if application.source_key != f"{prefix}{sheet}:{row}":
            continue
        values, links = metadata.get("values", {}), metadata.get("hyperlinks", {})
        if not isinstance(values, dict) or not isinstance(links, dict):
            raise WorkbookError("Existing row-based import lacks valid source provenance")
        company, title = values.get("company"), values.get("position")
        if not isinstance(company, str) or not isinstance(title, str):
            raise WorkbookError("Existing row-based import lacks vacancy identity provenance")
        url = _url(links.get("link")) or _url(values.get("link"))
        identity = _application_identity(company, title, url)
        legacy.append((str(sheet), row, application.id, identity, application))
    occurrences: Counter[tuple[str, str]] = Counter()
    for sheet, _row, _id, identity, application in sorted(legacy):
        group = (_normalized(sheet), identity)
        occurrences[group] += 1
        application.source_key = _source_key(source_id, sheet, identity, occurrences[group])
        application.source_metadata = {
            **application.source_metadata,
            "source_identity": identity,
            "duplicate_ordinal": occurrences[group],
        }
    session.flush()


def _provenance(parsed: ParsedWorkbook, row: ApplicationRow | CompanyRow) -> dict:
    raw_payload = {"values": row.raw_values, "hyperlinks": row.raw_hyperlinks}
    return {
        "import_format": "jobcrm_xlsx",
        "filename": parsed.filename,
        "workbook_fingerprint": parsed.fingerprint,
        "sheet": row.sheet,
        "row": row.row,
        "row_fingerprint": hashlib.sha256(
            json.dumps(
                raw_payload,
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest(),
        **raw_payload,
    }


def import_into_session(
    session: Session, parsed: ParsedWorkbook, *, source_id: str = "JobCRM"
) -> ImportSummary:
    """Import atomically; caller commits. Existing source rows retain user edits.

    Keys use source vacancy identity plus an ordinal for repeated identical
    vacancies. Sorting and inserting rows do not replace existing source rows.
    The logical source ID is stable when a workbook is renamed or recopied; a
    different independent tracker must use its own source ID.
    Historical application/stage dates and acquisition channels remain unknown.
    """
    from sqlalchemy import func, select

    from job_intake.crm.models import ContactORM
    from job_intake.crm.repository import CRMRepository

    if not source_id.strip():
        raise ValueError("Import source ID must not be empty")
    repository = CRMRepository(session)
    summary = ImportSummary(
        applications_total=len(parsed.applications),
        companies_total=len(parsed.companies),
        stage_counts=dict(sorted(Counter(item.stage for item in parsed.applications).items())),
        warnings=list(parsed.warnings),
    )
    # sqlite3's legacy transaction mode does not start a transaction for a
    # SAVEPOINT. Ensure releasing our savepoint cannot commit before the caller.
    connection = session.connection()
    if connection.dialect.name == "sqlite":
        driver = connection.connection.driver_connection
        if not driver.in_transaction:
            connection.exec_driver_sql("BEGIN")
    with session.begin_nested():
        _migrate_row_keys(session, source_id)
        contacts_before = session.scalar(select(func.count()).select_from(ContactORM)) or 0
        for row in parsed.companies:
            existing = repository.find_company(row.name)
            repository.upsert_company(
                name=row.name,
                url=row.careers_url,
                source_key=_source_key(source_id, row.sheet, _company_identity(row.name)),
                source_metadata={
                    **_provenance(parsed, row),
                    "source_identity": _company_identity(row.name),
                },
            )
            if existing is None:
                summary.companies_created += 1
            else:
                summary.companies_existing += 1
        occurrences: Counter[tuple[str, str]] = Counter()
        for row in parsed.applications:
            identity = _application_identity(row.company, row.title, row.job_url)
            group = (_normalized(row.sheet), identity)
            occurrences[group] += 1
            key = _source_key(source_id, row.sheet, identity, occurrences[group])
            if repository.find_application_by_source_key(key) is not None:
                summary.applications_skipped += 1
                continue
            metadata = _provenance(parsed, row)
            metadata.update(
                {
                    "disposition_reason": row.disposition_reason,
                    "recruiter_raw": row.recruiter_raw,
                    "recruiter_urls": list(row.recruiter_urls),
                    "historical_dates_known": False,
                    "channel_known": False,
                    "source_identity": identity,
                    "duplicate_ordinal": occurrences[group],
                }
            )
            application = repository.create_application(
                company=row.company,
                title=row.title,
                url=row.job_url,
                channel="unknown",
                stage=row.stage,
                applied_at=None,
                original_status=row.raw_status,
                source_key=key,
                source_metadata=metadata,
                stage_occurred_at=None,
            )
            summary.applications_created += 1
            if row.recruiter_raw.strip():
                linkedin = (
                    next(
                        (
                            url
                            for url in row.recruiter_urls
                            if (urlsplit(url).hostname or "").lower() == "linkedin.com"
                            or (urlsplit(url).hostname or "").lower().endswith(".linkedin.com")
                        ),
                        None,
                    )
                    if len(row.recruiter_urls) == 1
                    else None
                )
                contact = repository.upsert_contact(
                    name=row.recruiter_raw.strip()[:255],
                    company=row.company,
                    linkedin_url=linkedin,
                    source_key=f"{key}:recruiter",
                    source_metadata={**metadata, "raw_text": row.recruiter_raw},
                )
                repository.link_contact(application.id, contact.id, relationship="recruiter")
                summary.contacts_linked += 1
        session.flush()
        contacts_after = session.scalar(select(func.count()).select_from(ContactORM)) or 0
        summary.contacts_created = contacts_after - contacts_before
        summary.contacts_existing = summary.contacts_linked - summary.contacts_created
    return summary
