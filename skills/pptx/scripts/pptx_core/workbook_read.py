"""Bounded chart references into already validated embedded XLSX packages.

This module does not open archives, parse XML bytes, calculate formulas, or
render spreadsheet number formats. Only cells reached by chart references are
read as values. Numeric values retain their XML decimal spelling.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from xml.etree import ElementTree as ET


S_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"
REL_BASE = R_NS + "/"
MAX_POINTS = 2000
MAX_CELLS = 100000
MAX_NUMBER_CHARS = 1024
MAX_REFERENCE_CHARS = 2048
_ADDRESS = r"\$?([A-Za-z]{1,3})\$?([1-9][0-9]{0,6})"
_REFERENCE = re.compile(
    r"(?:'((?:[^']|'')+)'|([A-Za-z_\u0080-\uffff][A-Za-z0-9_.\u0080-\uffff]*))!"
    + _ADDRESS + r"(?::" + _ADDRESS + r")?"
)
_NUMBER = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE]([+-]?[0-9]+))?")
_BUILTIN_FORMATS = {
    0: "General", 1: "0", 2: "0.00", 3: "#,##0", 4: "#,##0.00", 9: "0%", 10: "0.00%",
    11: "0.00E+00", 12: "# ?/?", 13: "# ??/??", 14: "mm-dd-yy", 15: "d-mmm-yy",
    16: "d-mmm", 17: "mmm-yy", 18: "h:mm AM/PM", 19: "h:mm:ss AM/PM", 20: "h:mm",
    21: "h:mm:ss", 22: "m/d/yy h:mm", 45: "mm:ss", 46: "[h]:mm:ss", 47: "mmss.0",
    48: "##0.0E+0", 49: "@",
}


class ChartReadError(ValueError):
    """A content inconsistency, bounded-profile limit, or unsupported reference."""

    def __init__(self, message: str, code: str = "content_validation_failed", **details: object) -> None:
        super().__init__(message)
        self.code = code
        self.details = details


def unsigned(value: str | None, maximum: int, label: str) -> int:
    if value is None or not re.fullmatch(r"[0-9]{1,12}", value):
        raise ChartReadError(f"Invalid {label}", field_path=label)
    number = int(value)
    if number > maximum:
        raise ChartReadError(f"{label} exceeds the supported limit", "resource_limit", field_path=label, limit=maximum)
    return number


def decimal_text(value: str, label: str) -> str:
    """Validate a finite decimal without converting through binary floating point."""
    value = value.strip()
    if len(value) > MAX_NUMBER_CHARS:
        raise ChartReadError(f"{label} numeric spelling is too long", "resource_limit", field_path=label)
    match = _NUMBER.fullmatch(value)
    if match is None:
        raise ChartReadError(f"{label} must be a finite decimal number", field_path=label)
    exponent = match.group(1)
    if exponent is not None and (len(exponent.lstrip("+-")) > 5 or abs(int(exponent)) > 10000):
        raise ChartReadError(f"{label} decimal exponent exceeds 10000", "resource_limit", field_path=label)
    try:
        number = Decimal(value)
    except InvalidOperation as error:
        raise ChartReadError(f"Invalid {label} decimal number", field_path=label) from error
    if not number.is_finite():
        raise ChartReadError(f"{label} must be finite", field_path=label)
    return value


def values_equal(left: str | None, right: str | None, numeric: bool) -> bool:
    if left is None or right is None:
        return left is right
    return Decimal(left) == Decimal(right) if numeric else left == right


def semantic_root(package, part: str) -> ET.Element | None:
    """Use the validator's selected MCE semantics without an import-time cycle."""
    from .validation import _semantic_tree

    root = package.xml.get(part)
    return _semantic_tree(root, package.understood_choices) if root is not None else None


def _column_number(letters: str) -> int:
    number = 0
    for letter in letters.upper():
        number = number * 26 + ord(letter) - ord("A") + 1
    if not 1 <= number <= 16384:
        raise ChartReadError("Cell column is outside the XLSX grid", "unsupported_content")
    return number


def _column_letters(number: int) -> str:
    result = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        result = chr(ord("A") + remainder) + result
    return result


def _address(value: str) -> tuple[int, int]:
    match = re.fullmatch(_ADDRESS, value)
    if match is None:
        raise ChartReadError("Only explicit A1 cell addresses are supported", "unsupported_content", cell=value)
    row = int(match[2])
    if row > 1048576:
        raise ChartReadError("Cell row is outside the XLSX grid", "unsupported_content", cell=value)
    return row, _column_number(match[1])


def parse_reference(formula: str) -> tuple[str, list[str]]:
    """Accept one in-workbook, forward, one-dimensional A1 range only."""
    if len(formula) > MAX_REFERENCE_CHARS:
        raise ChartReadError("Chart reference exceeds 2048 characters", "resource_limit")
    match = _REFERENCE.fullmatch(formula)
    if match is None or "[" in formula or "]" in formula:
        raise ChartReadError("Only a worksheet-qualified A1 range is supported", "unsupported_content", reference=formula)
    sheet = match[1].replace("''", "'") if match[1] is not None else match[2]
    start = _address(match[3] + match[4])
    end = _address(match[5] + match[6]) if match[5] is not None else start
    if start[0] > end[0] or start[1] > end[1] or (start[0] != end[0] and start[1] != end[1]):
        raise ChartReadError("Chart references must be forward one-dimensional ranges", "unsupported_content", reference=formula)
    count = max(end[0] - start[0], end[1] - start[1]) + 1
    if count > MAX_POINTS:
        raise ChartReadError("Chart reference exceeds 2000 cells", "resource_limit", reference=formula)
    cells = [
        _column_letters(start[1] + (index if start[1] != end[1] else 0))
        + str(start[0] + (index if start[0] != end[0] else 0))
        for index in range(count)
    ]
    return sheet, cells


def _one_child(parent: ET.Element, tag: str, label: str, *, required: bool = True) -> ET.Element | None:
    elements = parent.findall(f"{{{S_NS}}}{tag}")
    if len(elements) > 1 or (required and not elements):
        raise ChartReadError(f"Expected one {label}", field_path=label)
    return elements[0] if elements else None


def _rich_text(root: ET.Element) -> str:
    pieces = []
    for child in root:
        if child.tag == f"{{{S_NS}}}t":
            pieces.append(child.text or "")
        elif child.tag == f"{{{S_NS}}}r":
            text = _one_child(child, "t", "workbook.string.run")
            pieces.append(text.text or "")
        elif child.tag not in {f"{{{S_NS}}}rPh", f"{{{S_NS}}}phoneticPr"}:
            raise ChartReadError("Unsupported spreadsheet string content", "unsupported_content")
    return "".join(pieces)


class WorkbookReader:
    """Value reader whose package and every XML tree have already been validated."""

    def __init__(self, package) -> None:
        self.package = package
        roots = [item for item in package.relationships.get("", {}).values() if item.kind == REL_BASE + "officeDocument"]
        if len(roots) != 1 or roots[0].external:
            raise ChartReadError("Missing embedded workbook root", "unsupported_content")
        self.main = roots[0].target
        root = semantic_root(package, self.main)
        if root is None or root.tag != f"{{{S_NS}}}workbook":
            raise ChartReadError("Unsupported embedded workbook root", "unsupported_content")
        self.relations = package.relationships.get(self.main, {})
        self.sheets = {}
        for sheet in root.findall(f"{{{S_NS}}}sheets/{{{S_NS}}}sheet"):
            name = sheet.get("name", "")
            relation = self.relations.get(sheet.get(f"{{{R_NS}}}id", ""))
            if not name or name.casefold() in self.sheets or relation is None or relation.external or relation.kind != REL_BASE + "worksheet":
                raise ChartReadError("Invalid embedded worksheet identity", "unsupported_content")
            self.sheets[name.casefold()] = (name, relation.target)
        self.cell_indexes: dict[str, dict[str, ET.Element]] = {}
        self.strings: list[ET.Element] | None = None
        self.styles: list[ET.Element] | None = None
        self.custom_formats: dict[int, str] = {}
        self._styles_loaded = False
        self.roots = {self.main: root}

    def _root(self, part: str) -> ET.Element | None:
        if part not in self.roots:
            self.roots[part] = semantic_root(self.package, part)
        return self.roots[part]

    def _related_root(self, kind: str) -> ET.Element | None:
        relations = [item for item in self.relations.values() if item.kind == REL_BASE + kind]
        if len(relations) > 1 or any(item.external for item in relations):
            raise ChartReadError(f"Invalid workbook {kind} relationship", "unsupported_content")
        if not relations:
            return None
        root = self._root(relations[0].target)
        if root is None:
            raise ChartReadError(f"Missing workbook {kind} XML", "unsupported_content")
        return root

    def _index(self, part: str) -> dict[str, ET.Element]:
        if part in self.cell_indexes:
            return self.cell_indexes[part]
        root = self._root(part)
        if root is None or root.tag != f"{{{S_NS}}}worksheet":
            raise ChartReadError("Missing referenced worksheet", "unsupported_content", part=part)
        sheet_data = _one_child(root, "sheetData", "worksheet.sheetData")
        cells = {}
        for row in sheet_data:
            if row.tag != f"{{{S_NS}}}row":
                raise ChartReadError("Unsupported worksheet row content", "unsupported_content", part=part)
            row_number = unsigned(row.get("r"), 1048576, "worksheet.row") if row.get("r") is not None else None
            for cell in row:
                if cell.tag != f"{{{S_NS}}}c":
                    raise ChartReadError("Unsupported worksheet cell content", "unsupported_content", part=part)
                address = cell.get("r", "")
                coordinate = _address(address)
                normalized = _column_letters(coordinate[1]) + str(coordinate[0])
                if normalized in cells or (row_number is not None and row_number != coordinate[0]):
                    raise ChartReadError("Duplicate or inconsistent worksheet cell address", cell=address, part=part)
                cells[normalized] = cell
                if len(cells) > MAX_CELLS:
                    raise ChartReadError("Worksheet index exceeds 100000 cells", "resource_limit", part=part)
        self.cell_indexes[part] = cells
        return cells

    def _format(self, cell: ET.Element | None) -> dict:
        if not self._styles_loaded:
            root = self._related_root("styles")
            if root is not None:
                if root.tag != f"{{{S_NS}}}styleSheet":
                    raise ChartReadError("Unsupported workbook styles", "unsupported_content")
                xfs = _one_child(root, "cellXfs", "workbook.cellXfs")
                self.styles = xfs.findall(f"{{{S_NS}}}xf")
                for item in root.findall(f"{{{S_NS}}}numFmts/{{{S_NS}}}numFmt"):
                    identifier = unsigned(item.get("numFmtId"), 4294967295, "workbook.numFmtId")
                    if identifier in self.custom_formats or item.get("formatCode") is None:
                        raise ChartReadError("Invalid custom workbook number format")
                    self.custom_formats[identifier] = item.get("formatCode")
            self._styles_loaded = True
        style_index = unsigned(cell.get("s", "0"), 100000, "workbook.cell.style") if cell is not None else 0
        if self.styles is None:
            if style_index:
                raise ChartReadError("Referenced cell style is unavailable", "unsupported_content", style=style_index)
            identifier = 0
        else:
            if style_index >= len(self.styles):
                raise ChartReadError("Referenced cell style index is invalid", style=style_index)
            identifier = unsigned(self.styles[style_index].get("numFmtId", "0"), 4294967295, "workbook.numFmtId")
        if identifier >= 164 and identifier not in self.custom_formats:
            raise ChartReadError("Referenced custom number format is missing", "unsupported_content", number_format_id=identifier)
        return {"number_format_id": identifier, "number_format_code": self.custom_formats.get(identifier, _BUILTIN_FORMATS.get(identifier))}

    def _cell(self, cell: ET.Element | None, address: str, numeric: bool) -> tuple[str | None, dict]:
        metadata = {"cell": address, "type": "missing" if cell is None else cell.get("t", "n"), **self._format(cell)}
        if cell is None:
            return None, metadata
        if any(item.tag.startswith("{" + MC_NS + "}") for item in cell.iter()):
            raise ChartReadError("Alternate spreadsheet cell content is unsupported", "unsupported_content", cell=address)
        if cell.findall(f"{{{S_NS}}}f"):
            raise ChartReadError("Formula cells are outside chart content verification", "unsupported_content", cell=address)
        value = _one_child(cell, "v", "workbook.cell.value", required=False)
        inline = _one_child(cell, "is", "workbook.cell.inlineString", required=False)
        if value is not None and len(value):
            raise ChartReadError("Workbook cell value must be scalar", "unsupported_content", cell=address)
        kind = metadata["type"]
        if kind == "n":
            if inline is not None:
                raise ChartReadError("Numeric workbook cell has inline string content", cell=address)
            if value is None or not (value.text or "").strip():
                return None, metadata
            if not numeric:
                raise ChartReadError("Numeric-to-text formatting is outside chart content verification", "unsupported_content", cell=address)
            return decimal_text(value.text, "workbook.cell.value"), metadata
        if kind not in {"s", "inlineStr", "str"}:
            raise ChartReadError(f"Unsupported referenced cell type: {kind}", "unsupported_content", cell=address)
        if numeric:
            raise ChartReadError("Numeric chart reference points to a text cell", "unsupported_content", cell=address)
        if kind == "inlineStr":
            if inline is None or value is not None:
                raise ChartReadError("Invalid inline workbook string", cell=address)
            return _rich_text(inline), metadata
        if inline is not None:
            raise ChartReadError("Unexpected inline workbook string", cell=address)
        if kind == "str":
            return (value.text or "") if value is not None else None, metadata
        if value is None:
            raise ChartReadError("Missing shared string index", cell=address)
        if self.strings is None:
            root = self._related_root("sharedStrings")
            if root is None or root.tag != f"{{{S_NS}}}sst":
                raise ChartReadError("Shared string table is unavailable", "unsupported_content", cell=address)
            self.strings = root.findall(f"{{{S_NS}}}si")
        index = unsigned(value.text, MAX_CELLS, "workbook.sharedString.index")
        if index >= len(self.strings):
            raise ChartReadError("Shared string index is out of range", cell=address)
        return _rich_text(self.strings[index]), metadata

    def read(self, reference: str, numeric: bool) -> tuple[list[str | None], dict]:
        sheet, addresses = parse_reference(reference)
        resolved = self.sheets.get(sheet.casefold())
        if resolved is None:
            raise ChartReadError("Chart references an unavailable worksheet", "unsupported_content", reference=reference)
        name, part = resolved
        index = self._index(part)
        values = []
        metadata = []
        for address in addresses:
            try:
                value, details = self._cell(index.get(address), address, numeric)
            except ChartReadError as error:
                error.details.setdefault("reference", reference)
                error.details.setdefault("part", part)
                raise
            values.append(value)
            metadata.append(details)
        return values, {"workbook_sheet": name, "workbook_cells": metadata}
