from xml.etree import ElementTree as ET
from zipfile import ZipFile

import pytest

from job_intake.crm import xlsx_reader as reader


def _workbook(tmp_path, sheets):
    def tag(name):
        return f"{{{reader.MAIN_NS}}}{name}"

    workbook = ET.Element(tag("workbook"))
    nodes = ET.SubElement(workbook, tag("sheets"))
    relations = ET.Element(f"{{{reader.PACKAGE_REL_NS}}}Relationships")
    path = tmp_path / "limits.xlsx"
    with ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        written = set()
        for index, (part, cells, ranges) in enumerate(sheets):
            identifier = f"s{index}"
            ET.SubElement(nodes, tag("sheet"), {
                "name": f"Sheet{index}", f"{{{reader.REL_NS}}}id": identifier,
            })
            ET.SubElement(relations, f"{{{reader.PACKAGE_REL_NS}}}Relationship", {
                "Id": identifier, "Target": f"worksheets/{part}.xml",
                "Type": f"{reader.REL_NS}/worksheet",
            })
            if part in written:
                continue
            written.add(part)
            sheet = ET.Element(tag("worksheet"))
            data = ET.SubElement(sheet, tag("sheetData"))
            row = ET.SubElement(data, tag("row"))
            for reference in cells:
                cell = ET.SubElement(row, tag("c"), {"r": reference})
                ET.SubElement(cell, tag("v")).text = "value"
            links = ET.SubElement(sheet, tag("hyperlinks"))
            for reference in ranges:
                ET.SubElement(links, tag("hyperlink"), {
                    "ref": reference, f"{{{reader.REL_NS}}}id": "link",
                })
            link_rels = ET.Element(f"{{{reader.PACKAGE_REL_NS}}}Relationships")
            ET.SubElement(link_rels, f"{{{reader.PACKAGE_REL_NS}}}Relationship", {
                "Id": "link", "Target": "https://example.invalid/job",
                "TargetMode": "External", "Type": f"{reader.REL_NS}/hyperlink",
            })
            archive.writestr(f"xl/worksheets/{part}.xml", ET.tostring(sheet))
            archive.writestr(f"xl/worksheets/_rels/{part}.xml.rels", ET.tostring(link_rels))
        archive.writestr("xl/workbook.xml", ET.tostring(workbook))
        archive.writestr("xl/_rels/workbook.xml.rels", ET.tostring(relations))
    return path


def test_hyperlink_range_checks_materialization_budget_before_creating_cells(tmp_path, monkeypatch):
    monkeypatch.setattr(reader, "MAX_CELLS", 4)
    created = []
    original = reader.Cell

    def record_cell(*args, **kwargs):
        created.append(args)
        return original(*args, **kwargs)

    monkeypatch.setattr(reader, "Cell", record_cell)
    path = _workbook(tmp_path, [("one", [], ["A1:A5"])])
    with pytest.raises(reader.WorkbookError, match="materialized cells"):
        reader.read_workbook(path)
    assert created == []


def test_materialized_cell_limit_is_global_across_sheets(tmp_path, monkeypatch):
    monkeypatch.setattr(reader, "MAX_CELLS", 4)
    path = _workbook(tmp_path, [("one", ["A1", "A2", "A3"], []),
                               ("two", [], ["B1:B2"])])
    with pytest.raises(reader.WorkbookError, match="materialized cells"):
        reader.read_workbook(path)


def test_overlapping_ranges_are_bounded_even_when_no_new_cells_are_added(tmp_path, monkeypatch):
    monkeypatch.setattr(reader, "MAX_CELL_WORK", 9)
    path = _workbook(tmp_path, [("one", ["A1", "A2"], ["A1:A2", "A1:A2"])])
    with pytest.raises(reader.WorkbookError, match="processing budget"):
        reader.read_workbook(path)


def test_hyperlinks_on_existing_cells_do_not_double_count_materialization(tmp_path, monkeypatch):
    monkeypatch.setattr(reader, "MAX_CELLS", 2)
    path = _workbook(tmp_path, [("one", ["A1", "A2"], ["A1:A2"])])
    workbook = reader.read_workbook(path)
    assert workbook.sheets[0].rows[2]["A"].value == "value"
    assert workbook.sheets[0].rows[2]["A"].hyperlink == "https://example.invalid/job"


def test_duplicate_worksheet_target_is_rejected(tmp_path):
    path = _workbook(tmp_path, [("one", ["A1"], []), ("one", [], [])])
    with pytest.raises(reader.WorkbookError, match="relationship target"):
        reader.read_workbook(path)


def test_sheet_count_is_checked_before_reading_worksheets(tmp_path, monkeypatch):
    monkeypatch.setattr(reader, "MAX_SHEETS", 1)
    path = _workbook(tmp_path, [("one", [], []), ("two", [], [])])
    with pytest.raises(reader.WorkbookError, match="too many worksheets"):
        reader.read_workbook(path)
