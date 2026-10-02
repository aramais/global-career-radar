"""Bounded, read-only OOXML reader for the historical JobCRM import.

Only cell text, cached formula values and hyperlinks are read. No formulas,
macros, external relationships or workbook instructions are executed.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile

MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
MAX_ARCHIVE_BYTES = 32 * 1024 * 1024
MAX_EXPANDED_BYTES = 64 * 1024 * 1024
MAX_XML_BYTES = 8 * 1024 * 1024
MAX_MEMBERS = 512
MAX_CELLS = 100_000
MAX_ROWS = 10_000
CELL_RE = re.compile(r"^([A-Z]{1,3})([1-9][0-9]{0,6})$")


class WorkbookError(ValueError):
    """A workbook cannot be safely read or does not match supported OOXML."""


@dataclass(frozen=True)
class Cell:
    value: str
    hyperlink: str | None = None
    formula: str | None = None


@dataclass(frozen=True)
class Sheet:
    name: str
    rows: dict[int, dict[str, Cell]]


@dataclass(frozen=True)
class Workbook:
    sheets: tuple[Sheet, ...]
    warnings: tuple[str, ...]


def _tag(name: str) -> str:
    return f"{{{MAIN_NS}}}{name}"


def _xml(archive: ZipFile, name: str) -> ET.Element:
    try:
        info = archive.getinfo(name)
    except KeyError as exc:
        raise WorkbookError(f"Missing XLSX part: {name}") from exc
    if info.file_size > MAX_XML_BYTES:
        raise WorkbookError(f"XLSX XML part exceeds {MAX_XML_BYTES} bytes: {name}")
    data = archive.read(name)
    if len(data) > MAX_XML_BYTES:
        raise WorkbookError(f"XLSX XML part is too large: {name}")
    # UTF-16/32 encodings interleave NUL bytes with XML markup. Normalize those
    # bytes for this check without changing the bytes passed to the XML parser.
    declarations = data.replace(b"\x00", b"").upper()
    if b"<!DOCTYPE" in declarations or b"<!ENTITY" in declarations:
        raise WorkbookError(f"DTD and entity declarations are unsupported: {name}")
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise WorkbookError(f"Invalid XML in XLSX part: {name}") from exc


def _part_target(owner: str, target: str) -> str:
    if "\\" in target or ":" in target:
        raise WorkbookError("Invalid internal XLSX relationship target")
    path = posixpath.normpath(
        target.lstrip("/")
        if target.startswith("/")
        else posixpath.join(posixpath.dirname(owner), target)
    )
    if path == ".." or path.startswith("../") or path.startswith("/"):
        raise WorkbookError("XLSX relationship escapes the archive")
    return path


def _relationships(archive: ZipFile, owner: str) -> dict[str, tuple[str, str, str]]:
    directory, basename = posixpath.split(owner)
    name = posixpath.join(directory, "_rels", f"{basename}.rels")
    if name not in archive.namelist():
        return {}
    result: dict[str, tuple[str, str, str]] = {}
    for item in _xml(archive, name).findall(f"{{{PACKAGE_REL_NS}}}Relationship"):
        identifier = item.get("Id", "")
        if not identifier or identifier in result:
            raise WorkbookError("Duplicate or missing XLSX relationship identifier")
        result[identifier] = (
            item.get("Target", ""),
            item.get("TargetMode", "Internal"),
            item.get("Type", ""),
        )
    return result


def _rich_text(node: ET.Element) -> str:
    # Phonetic runs and formatting must not become cell content.
    return "".join(
        child.text or ""
        for parent in [node, *node.findall(_tag("r"))]
        for child in parent.findall(_tag("t"))
    )


def _cell_value(cell: ET.Element, shared_strings: list[str]) -> str:
    value = cell.findtext(_tag("v"), "")
    kind = cell.get("t")
    if kind == "s":
        try:
            index = int(value)
            if index < 0:
                raise ValueError
            return shared_strings[index]
        except (ValueError, IndexError) as exc:
            raise WorkbookError("Invalid XLSX shared string reference") from exc
    if kind == "inlineStr":
        inline = cell.find(_tag("is"))
        return _rich_text(inline) if inline is not None else ""
    if kind == "b":
        return "TRUE" if value == "1" else "FALSE"
    return value


def _cell_coordinates(reference: str) -> tuple[str, int]:
    match = CELL_RE.fullmatch(reference)
    if not match:
        raise WorkbookError(f"Invalid XLSX cell reference: {reference}")
    column, row = match.groups()
    return column, int(row)


def _read_sheet(
    archive: ZipFile, name: str, path: str, shared_strings: list[str], warnings: list[str]
) -> Sheet:
    doc = _xml(archive, path)
    if doc.tag != _tag("worksheet"):
        raise WorkbookError(f"Unsupported worksheet format: {name}")
    rows: dict[int, dict[str, Cell]] = {}
    count = 0
    for row in doc.findall(f"{_tag('sheetData')}/{_tag('row')}"):
        for item in row.findall(_tag("c")):
            count += 1
            if count > MAX_CELLS:
                raise WorkbookError(f"Worksheet contains too many cells: {name}")
            column, number = _cell_coordinates(item.get("r", ""))
            if number > MAX_ROWS:
                raise WorkbookError(f"Worksheet row limit exceeded: {name}")
            cells = rows.setdefault(number, {})
            if column in cells:
                raise WorkbookError(f"Duplicate worksheet cell: {name}!{column}{number}")
            formula_node = item.find(_tag("f"))
            formula = formula_node.text if formula_node is not None else None
            value = _cell_value(item, shared_strings)
            if formula_node is not None and item.find(_tag("v")) is None:
                warnings.append(f"{name}!{column}{number}: formula has no cached value")
            cells[column] = Cell(value=value, formula=formula)
    relationships = _relationships(archive, path)
    for link in doc.findall(f"{_tag('hyperlinks')}/{_tag('hyperlink')}"):
        identifier = link.get(f"{{{REL_NS}}}id")
        if not identifier:
            continue  # Internal cell navigation is not a careers/contact URL.
        relation = relationships.get(identifier)
        if relation is None:
            raise WorkbookError(f"Missing hyperlink relationship: {name}")
        target, mode, kind = relation
        if mode != "External" or not kind.endswith("/hyperlink"):
            continue
        start, _, end = link.get("ref", "").partition(":")
        column, first = _cell_coordinates(start)
        last_column, last = _cell_coordinates(end or start)
        if column != last_column or last < first or last > MAX_ROWS:
            raise WorkbookError(f"Unsupported hyperlink range: {name}")
        for number in range(first, last + 1):
            cells = rows.setdefault(number, {})
            cell = cells.get(column, Cell(""))
            cells[column] = Cell(cell.value, hyperlink=target, formula=cell.formula)
    return Sheet(name=name, rows=rows)


def read_workbook(path: Path | str) -> Workbook:
    """Read an XLSX without network requests, formula evaluation or extraction."""
    path = Path(path)
    if path.suffix.lower() != ".xlsx":
        raise WorkbookError(
            "Only .xlsx files are supported; .xls and macro workbooks are unsupported"
        )
    if path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise WorkbookError(f"XLSX archive exceeds {MAX_ARCHIVE_BYTES} bytes")
    try:
        with ZipFile(path) as archive:
            members = archive.infolist()
            if len(members) > MAX_MEMBERS:
                raise WorkbookError("XLSX archive contains too many parts")
            if sum(member.file_size for member in members) > MAX_EXPANDED_BYTES:
                raise WorkbookError("Expanded XLSX archive is too large")
            names: set[str] = set()
            for member in members:
                name = member.filename
                if name in names:
                    raise WorkbookError("Duplicate XLSX archive part")
                names.add(name)
                if name.startswith("/") or "\\" in name or ".." in name.split("/"):
                    raise WorkbookError("Unsafe XLSX archive part name")
                if member.flag_bits & 1:
                    raise WorkbookError("Encrypted XLSX parts are unsupported")
                if "vba" in name.lower():
                    raise WorkbookError("Macro workbooks are unsupported")
            types = ET.tostring(_xml(archive, "[Content_Types].xml")).lower()
            if b"macroenabled" in types or b"vba" in types:
                raise WorkbookError("Macro workbooks are unsupported")
            workbook = _xml(archive, "xl/workbook.xml")
            if workbook.tag != _tag("workbook"):
                raise WorkbookError("Unsupported workbook XML namespace")
            relations = _relationships(archive, "xl/workbook.xml")
            shared_strings: list[str] = []
            for target, mode, kind in relations.values():
                if mode == "Internal" and kind.endswith("/sharedStrings"):
                    node = _xml(archive, _part_target("xl/workbook.xml", target))
                    shared_strings = [_rich_text(item) for item in node.findall(_tag("si"))]
                    if len(shared_strings) > MAX_CELLS:
                        raise WorkbookError("Too many XLSX shared strings")
                    break
            warnings: list[str] = []
            sheets: list[Sheet] = []
            sheet_names: set[str] = set()
            for item in workbook.findall(f"{_tag('sheets')}/{_tag('sheet')}"):
                name = item.get("name", "")
                if not name or name in sheet_names:
                    raise WorkbookError("Duplicate or missing worksheet name")
                sheet_names.add(name)
                relation = relations.get(item.get(f"{{{REL_NS}}}id", ""))
                if relation is None:
                    raise WorkbookError(f"Missing worksheet relationship: {name}")
                target, mode, kind = relation
                if mode != "Internal" or not kind.endswith("/worksheet"):
                    raise WorkbookError(f"Unsupported worksheet relationship: {name}")
                sheets.append(
                    _read_sheet(
                        archive,
                        name,
                        _part_target("xl/workbook.xml", target),
                        shared_strings,
                        warnings,
                    )
                )
            return Workbook(sheets=tuple(sheets), warnings=tuple(warnings))
    except (BadZipFile, RuntimeError) as exc:
        raise WorkbookError("Invalid or encrypted XLSX archive") from exc
