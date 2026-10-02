from __future__ import annotations

from collections import Counter
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from sqlalchemy import select

from job_intake.crm.importer import import_into_session, parse_workbook
from job_intake.crm.xlsx_reader import MAIN_NS, PACKAGE_REL_NS, REL_NS, WorkbookError
from job_intake.storage.database import Database


def _xml(element: ET.Element) -> bytes:
    return ET.tostring(element, encoding="utf-8", xml_declaration=True)


def _fixture(
    tmp_path: Path,
    *,
    statuses: list[str] | None = None,
    company_count: int = 63,
    wrong_headers: bool = False,
    unsafe_url: bool = False,
    formula: bool = False,
) -> Path:
    """Small synthetic workbook with the real layout and no personal history."""
    if statuses is None:
        statuses = [
            *["Application Sent"] * 9,
            "Написал рекрутеру",
            *["Rejected"] * 3,
            *["Too difficult"] * 4,
            "HR Screening",
            "Not Remote",
            *["Duplicate"] * 2,
            *["Not relevant"] * 2,
            "",
            "",
            "",
        ]
    path = tmp_path / "JobCRM.xlsx"
    tag = lambda name: f"{{{MAIN_NS}}}{name}"  # noqa: E731
    shared = ET.Element(tag("sst"))
    shared_values: list[str] = []

    def add_cell(row: ET.Element, ref: str, value: str, kind: str = "inlineStr") -> ET.Element:
        cell = ET.SubElement(row, tag("c"), {"r": ref, "t": kind})
        if kind == "s":
            shared_values.append(value)
            item = ET.SubElement(shared, tag("si"))
            ET.SubElement(item, tag("t")).text = value
            ET.SubElement(cell, tag("v")).text = str(len(shared_values) - 1)
        else:
            item = ET.SubElement(cell, tag("is"))
            ET.SubElement(item, tag("t")).text = value
        return cell

    applications = ET.Element(tag("worksheet"))
    data = ET.SubElement(applications, tag("sheetData"))
    header = ET.SubElement(data, tag("row"), {"r": "1"})
    for column, value in zip(
        "ABCDE",
        [
            "Wrong" if wrong_headers else "Position",
            "Company",
            "Link",
            "Status",
            "Recruiter",
        ],
        strict=True,
    ):
        add_cell(header, f"{column}1", value)
    links = ET.SubElement(applications, tag("hyperlinks"))
    app_rels = ET.Element(f"{{{PACKAGE_REL_NS}}}Relationships")
    for index, status in enumerate(statuses, 2):
        row = ET.SubElement(data, tag("row"), {"r": str(index)})
        add_cell(row, f"A{index}", f"Position {index}", "s")
        add_cell(row, f"B{index}", f"Company {index}")
        link = add_cell(row, f"C{index}", "Vacancy link")
        if formula and index == 2:
            link.attrib["t"] = "str"
            link.remove(link.find(tag("is")))
            ET.SubElement(link, tag("f")).text = '"Cached"'
            ET.SubElement(link, tag("v")).text = "Cached vacancy label"
        add_cell(row, f"D{index}", status, "s")
        raw_recruiter = ""
        if index <= 13:
            raw_recruiter = (
                "https://www.linkedin.com/in/person-a/\nhttps://www.linkedin.com/in/person-b/"
                if index == 2
                else f"Person {index} — https://www.linkedin.com/in/person-{index}/"
            )
        add_cell(row, f"E{index}", raw_recruiter)
        identifier = f"job{index}"
        ET.SubElement(links, tag("hyperlink"), {"ref": f"C{index}", f"{{{REL_NS}}}id": identifier})
        ET.SubElement(
            app_rels,
            f"{{{PACKAGE_REL_NS}}}Relationship",
            {
                "Id": identifier,
                "Type": f"{REL_NS}/hyperlink",
                "TargetMode": "External",
                "Target": "javascript:alert(1)"
                if unsafe_url and index == 2
                else f"https://example.com/jobs/{index}",
            },
        )
    # Styled blank trailing rows/cells should not inflate import counts.
    blank = ET.SubElement(data, tag("row"), {"r": "500"})
    add_cell(blank, "A500", "")
    companies = ET.Element(tag("worksheet"))
    company_data = ET.SubElement(companies, tag("sheetData"))
    header = ET.SubElement(company_data, tag("row"), {"r": "1"})
    add_cell(header, "A1", "Название компании")
    add_cell(header, "B1", "Индустрия")
    for index in range(2, company_count + 2):
        row = ET.SubElement(company_data, tag("row"), {"r": str(index)})
        add_cell(row, f"A{index}", f"Company {index}")
        add_cell(row, f"B{index}", "Technology")
    workbook = ET.Element(tag("workbook"))
    sheets = ET.SubElement(workbook, tag("sheets"))
    for index, name in enumerate(["Applications", "Companies"], 1):
        ET.SubElement(
            sheets,
            tag("sheet"),
            {
                "name": name,
                "sheetId": str(index),
                f"{{{REL_NS}}}id": f"sheet{index}",
            },
        )
    relations = ET.Element(f"{{{PACKAGE_REL_NS}}}Relationships")
    for index in range(1, 3):
        ET.SubElement(
            relations,
            f"{{{PACKAGE_REL_NS}}}Relationship",
            {
                "Id": f"sheet{index}",
                "Type": f"{REL_NS}/worksheet",
                "Target": f"worksheets/sheet{index}.xml",
            },
        )
    ET.SubElement(
        relations,
        f"{{{PACKAGE_REL_NS}}}Relationship",
        {
            "Id": "shared",
            "Type": f"{REL_NS}/sharedStrings",
            "Target": "sharedStrings.xml",
        },
    )
    with ZipFile(path, "w", ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", '<Types xmlns="urn:test"/>')
        archive.writestr("xl/workbook.xml", _xml(workbook))
        archive.writestr("xl/_rels/workbook.xml.rels", _xml(relations))
        archive.writestr("xl/sharedStrings.xml", _xml(shared))
        archive.writestr("xl/worksheets/sheet1.xml", _xml(applications))
        archive.writestr("xl/worksheets/sheet2.xml", _xml(companies))
        archive.writestr("xl/worksheets/_rels/sheet1.xml.rels", _xml(app_rels))
    return path


def _database(tmp_path: Path) -> Database:
    database = Database(f"sqlite:///{tmp_path / 'crm.db'}")
    database.create_schema()
    return database


def test_parses_real_layout_counts_hyperlinks_and_raw_statuses(tmp_path):
    parsed = parse_workbook(_fixture(tmp_path))
    assert parsed.summary()["applications"] == 26
    assert parsed.summary()["companies"] == 63
    assert parsed.summary()["contacts"] == 12
    assert Counter(row.stage for row in parsed.applications) == {
        "applied": 9,
        "contacted": 1,
        "rejected": 3,
        "archived": 9,
        "hr_screen": 1,
        "saved": 3,
    }
    first = parsed.applications[0]
    assert first.job_url == "https://example.com/jobs/2"
    assert first.raw_values["link"] == "Vacancy link"
    assert first.recruiter_raw.count("https://") == 2
    assert len(first.recruiter_urls) == 2
    assert parsed.companies[0].careers_url is None
    duplicates = [row for row in parsed.applications if row.raw_status == "Duplicate"]
    assert len(duplicates) == 2
    assert all(row.disposition_reason == "Duplicate" for row in duplicates)


def test_cached_formulas_are_read_without_execution(tmp_path):
    parsed = parse_workbook(_fixture(tmp_path, statuses=[""], formula=True))
    assert parsed.applications[0].raw_values["link"] == "Cached vacancy label"
    assert parsed.applications[0].job_url == "https://example.com/jobs/2"


def test_unknown_status_and_unsafe_link_preserved_in_provenance(tmp_path):
    parsed = parse_workbook(_fixture(tmp_path, statuses=["Unrecognized"], unsafe_url=True))
    row = parsed.applications[0]
    assert row.stage == "saved"
    assert row.raw_status == "Unrecognized"
    assert row.job_url is None
    assert row.raw_hyperlinks["link"] == "javascript:alert(1)"
    assert len(parsed.warnings) == 2


def test_rejects_missing_headers_before_import(tmp_path):
    with pytest.raises(WorkbookError, match="headers missing"):
        parse_workbook(_fixture(tmp_path, wrong_headers=True))


@pytest.mark.parametrize(
    "part,content,match",
    [
        ("xl/vbaProject.bin", b"macro", "Macro"),
        ("../outside.xml", b"escape", "Unsafe"),
        ("xl/workbook.xml", b'<!DOCTYPE workbook [<!ENTITY x "a">]><workbook/>', "DTD"),
        (
            "xl/workbook.xml",
            '<!DOCTYPE workbook [<!ENTITY x "a">]><workbook/>'.encode("utf-16"),
            "DTD",
        ),
    ],
)
def test_rejects_macros_zip_traversal_and_xml_entities(tmp_path, part, content, match):
    path = _fixture(tmp_path, statuses=[""], company_count=1)
    with ZipFile(path) as original:
        parts = {name: original.read(name) for name in original.namelist()}
    parts[part] = content
    with ZipFile(path, "w") as archive:
        for name, value in parts.items():
            archive.writestr(name, value)
    with pytest.raises(WorkbookError, match=match):
        parse_workbook(path)


def test_rejects_archive_expansion_limit(tmp_path, monkeypatch):
    from job_intake.crm import xlsx_reader

    path = _fixture(tmp_path, statuses=[""], company_count=1)
    monkeypatch.setattr(xlsx_reader, "MAX_EXPANDED_BYTES", 32)
    with pytest.raises(WorkbookError, match="Expanded"):
        parse_workbook(path)


def test_dry_run_does_not_write_and_source_name_hides_upload_path(tmp_path):
    path = _fixture(tmp_path)
    parsed = parse_workbook(path, source_name="JobCRM.xlsx")
    assert parsed.filename == "JobCRM.xlsx"
    assert parsed.summary()["stage_counts"]["applied"] == 9
    assert not (tmp_path / "crm.db").exists()


def test_duplicate_marked_rows_are_preserved_even_with_same_url(tmp_path):
    from dataclasses import replace

    from job_intake.crm.models import ApplicationORM

    parsed = parse_workbook(
        _fixture(tmp_path, statuses=["Duplicate", "Duplicate"], company_count=1)
    )
    parsed = replace(
        parsed,
        applications=(
            parsed.applications[0],
            replace(parsed.applications[1], job_url=parsed.applications[0].job_url),
        ),
    )
    database = _database(tmp_path)
    with database.session() as session:
        summary = import_into_session(session, parsed)
        session.commit()
        applications = list(session.scalars(select(ApplicationORM)))
        assert len(applications) == 2
        assert summary.applications_created == 2
        assert all(item.stage == "archived" for item in applications)
        assert all(item.original_status == "Duplicate" for item in applications)


def test_contact_counts_distinguish_shared_identity_from_source_rows(tmp_path):
    from dataclasses import replace

    from job_intake.crm.models import ContactORM

    parsed = parse_workbook(_fixture(tmp_path, statuses=["", ""], company_count=1))
    shared_url = "https://www.linkedin.com/in/same-person/"
    parsed = replace(
        parsed,
        applications=tuple(
            replace(row, recruiter_urls=(shared_url,)) for row in parsed.applications
        ),
    )
    database = _database(tmp_path)
    with database.session() as session:
        summary = import_into_session(session, parsed)
        session.commit()
        assert summary.contacts_created == 1
        assert summary.contacts_existing == 1
        assert summary.contacts_linked == 2
        assert len(list(session.scalars(select(ContactORM)))) == 1


def test_import_preserves_unknown_dates_channels_and_raw_contact(tmp_path):
    from job_intake.crm.models import ApplicationORM, ContactORM, StageEventORM

    parsed = parse_workbook(_fixture(tmp_path))
    database = _database(tmp_path)
    with database.session() as session:
        summary = import_into_session(session, parsed)
        assert summary.applications_created == 26
        assert summary.companies_created == 63
        assert summary.contacts_created == 12
        session.commit()
    with database.session() as session:
        applications = list(session.scalars(select(ApplicationORM)))
        assert len(applications) == 26
        assert all(application.channel == "unknown" for application in applications)
        assert all(application.applied_at is None for application in applications)
        assert all(event.occurred_at is None for event in session.scalars(select(StageEventORM)))
        first = next(app for app in applications if app.source_metadata["row"] == 2)
        assert first.original_status == "Application Sent"
        assert first.source_metadata["recruiter_raw"].count("https://") == 2
        contacts = list(session.scalars(select(ContactORM)))
        assert len(contacts) == 12
        assert contacts[0].source_metadata["raw_text"].count("https://") == 2


def test_reimport_renamed_copy_is_idempotent_and_preserves_user_edits(tmp_path):
    from job_intake.crm.models import ApplicationORM

    path = _fixture(tmp_path, statuses=["Application Sent"], company_count=1)
    parsed = parse_workbook(path)
    database = _database(tmp_path)
    with database.session() as session:
        import_into_session(session, parsed)
        session.commit()
        application = session.scalar(select(ApplicationORM))
        application.stage = "team_interview"
        application.channel = "referral"
        application.notes = "User notes"
        session.commit()
    copy = tmp_path / "uploaded-another-name.xlsx"
    copy.write_bytes(path.read_bytes())
    with database.session() as session:
        summary = import_into_session(session, parse_workbook(copy))
        session.commit()
        assert summary.applications_created == 0
        assert summary.applications_skipped == 1
        assert summary.contacts_created == 0
        applications = list(session.scalars(select(ApplicationORM)))
        assert len(applications) == 1
        assert applications[0].stage == "team_interview"
        assert applications[0].channel == "referral"
        assert applications[0].notes == "User notes"


def test_successful_import_is_not_committed_by_savepoint(tmp_path):
    from job_intake.crm.models import ApplicationORM

    database = _database(tmp_path)
    with database.session() as session:
        import_into_session(session, parse_workbook(_fixture(tmp_path)))
        session.rollback()
    with database.session() as session:
        assert list(session.scalars(select(ApplicationORM))) == []


def test_sorting_and_inserting_rows_imports_only_new_vacancy_and_preserves_manual_edits(tmp_path):
    from dataclasses import replace

    from job_intake.crm.models import ApplicationORM

    parsed = parse_workbook(_fixture(tmp_path, statuses=["Application Sent", "Rejected"]))
    database = _database(tmp_path)
    with database.session() as session:
        import_into_session(session, parsed)
        session.commit()
        first = session.scalar(select(ApplicationORM).where(ApplicationORM.title == "Position 2"))
        first_id, original_key = first.id, first.source_key
        first.stage = "team_interview"
        first.notes = "Keep user notes after Excel sort"
        first.title = "Manually edited display title"
        session.commit()
    reordered = replace(
        parsed,
        applications=tuple(
            replace(row, row=index) for index, row in enumerate(reversed(parsed.applications), 2)
        ),
    )
    with database.session() as session:
        result = import_into_session(session, reordered)
        session.commit()
        assert result.applications_created == 0
        assert result.applications_skipped == 2
        assert session.get(ApplicationORM, first_id).source_key == original_key
    inserted = replace(
        parsed.applications[0],
        row=2,
        title="Newly inserted vacancy",
        job_url="https://example.com/jobs/new",
        raw_status="",
        stage="saved",
        raw_values={**parsed.applications[0].raw_values, "position": "Newly inserted vacancy"},
        raw_hyperlinks={"link": "https://example.com/jobs/new"},
    )
    expanded = replace(
        parsed,
        applications=(
            inserted,
            *tuple(replace(row, row=index) for index, row in enumerate(reordered.applications, 3)),
        ),
    )
    with database.session() as session:
        result = import_into_session(session, expanded)
        session.commit()
        assert result.applications_created == 1
        assert result.applications_skipped == 2
        applications = list(session.scalars(select(ApplicationORM)))
        assert len(applications) == 3
        assert any(item.title == "Newly inserted vacancy" for item in applications)
        first = session.get(ApplicationORM, first_id)
        assert first.title == "Manually edited display title"
        assert first.stage == "team_interview"
        assert first.notes == "Keep user notes after Excel sort"


def test_status_and_recruiter_edits_do_not_create_new_application(tmp_path):
    from dataclasses import replace

    from job_intake.crm.models import ApplicationORM

    parsed = parse_workbook(_fixture(tmp_path, statuses=["Application Sent"], company_count=1))
    database = _database(tmp_path)
    with database.session() as session:
        import_into_session(session, parsed)
        session.commit()
    changed = replace(
        parsed,
        applications=(
            replace(
                parsed.applications[0],
                raw_status="Rejected",
                stage="rejected",
                recruiter_raw="Another recruiter",
                recruiter_urls=(),
            ),
        ),
    )
    with database.session() as session:
        result = import_into_session(session, changed)
        session.commit()
        assert result.applications_created == 0
        assert result.applications_skipped == 1
        assert session.scalar(select(ApplicationORM)).stage == "applied"


def test_identical_duplicate_rows_keep_distinct_stable_ordinals_after_insert(tmp_path):
    from dataclasses import replace

    from job_intake.crm.models import ApplicationORM

    parsed = parse_workbook(
        _fixture(tmp_path, statuses=["Duplicate", "Duplicate"], company_count=1)
    )
    first = parsed.applications[0]
    parsed = replace(parsed, applications=(first, replace(first, row=3)))
    database = _database(tmp_path)
    with database.session() as session:
        import_into_session(session, parsed)
        session.commit()
        assert len(list(session.scalars(select(ApplicationORM)))) == 2
    inserted = replace(first, row=2, title="New vacancy", job_url="https://example.com/new")
    changed = replace(
        parsed,
        applications=(
            inserted,
            replace(first, row=3),
            replace(first, row=4),
        ),
    )
    with database.session() as session:
        result = import_into_session(session, changed)
        session.commit()
        assert result.applications_created == 1
        assert result.applications_skipped == 2
        imported_duplicates = list(
            session.scalars(
                select(ApplicationORM).where(
                    ApplicationORM.title == first.title,
                )
            )
        )
        assert len(imported_duplicates) == 2
        assert {item.source_metadata["duplicate_ordinal"] for item in imported_duplicates} == {1, 2}


def test_early_row_keys_upgrade_from_original_provenance_without_resetting_user_data(tmp_path):
    import hashlib
    from dataclasses import replace

    from job_intake.crm.models import ApplicationORM

    parsed = parse_workbook(_fixture(tmp_path, statuses=["Application Sent", "Rejected"]))
    database = _database(tmp_path)
    with database.session() as session:
        import_into_session(session, parsed)
        session.commit()
        for item in session.scalars(select(ApplicationORM)):
            prefix = hashlib.sha256(b"JobCRM").hexdigest()[:20]
            item.source_key = f"xlsx:{prefix}:Applications:{item.source_metadata['row']}"
            item.notes = "Existing manual notes"
        session.commit()
    changed = replace(
        parsed,
        applications=tuple(
            replace(row, row=index) for index, row in enumerate(reversed(parsed.applications), 2)
        ),
    )
    with database.session() as session:
        result = import_into_session(session, changed)
        session.commit()
        assert result.applications_created == 0
        assert result.applications_skipped == 2
        applications = list(session.scalars(select(ApplicationORM)))
        assert len(applications) == 2
        assert all(item.notes == "Existing manual notes" for item in applications)
        assert all(
            item.source_metadata["source_identity"] in item.source_key for item in applications
        )


def test_import_failure_rolls_back_entire_import(tmp_path, monkeypatch):
    from job_intake.crm.models import ApplicationORM, CompanyORM, ContactORM
    from job_intake.crm.repository import CRMRepository

    database = _database(tmp_path)
    parsed = parse_workbook(_fixture(tmp_path, statuses=["Application Sent", "Rejected"]))
    original = CRMRepository.create_application

    def fail_second(self, **fields):
        if fields["title"] == "Position 3":
            raise ValueError("Simulated invalid application")
        return original(self, **fields)

    monkeypatch.setattr(CRMRepository, "create_application", fail_second)
    with database.session() as session:
        with pytest.raises(ValueError, match="Simulated"):
            import_into_session(session, parsed)
        session.commit()
    with database.session() as session:
        assert list(session.scalars(select(ApplicationORM))) == []
        assert list(session.scalars(select(CompanyORM))) == []
        assert list(session.scalars(select(ContactORM))) == []
