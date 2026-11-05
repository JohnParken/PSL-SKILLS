"""Bounded native-object readback and explicit creation-content comparisons."""

from __future__ import annotations

import json
import re
from xml.etree import ElementTree as ET


A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
M_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"
SVG_NS = "http://www.w3.org/2000/svg"
MAX_TABLE_ROWS = 100
MAX_TABLE_COLUMNS = 50
MAX_TABLE_CELLS = 5000
MAX_DETAIL_BYTES = 8 * 1024 * 1024
MAX_DETAIL_OBJECTS = 10000


class ContentError(ValueError):
    """An explicit content mismatch or unsupported semantic inspection input."""

    def __init__(self, message: str, code: str = "content_validation_failed", **details: object) -> None:
        super().__init__(message)
        self.code = code
        self.details = details


def _positive(value: str | None, maximum: int, label: str, default: int | None = None) -> int:
    if value is None and default is not None:
        return default
    if value is None or not re.fullmatch(r"[0-9]{1,12}", value):
        raise ContentError(f"Invalid {label}", field_path=label)
    number = int(value)
    if not 1 <= number <= maximum:
        raise ContentError(f"Out-of-range {label}", field_path=label)
    return number


def _flag(value: str | None, label: str) -> bool:
    if value in (None, "0", "false"):
        return False
    if value in ("1", "true"):
        return True
    raise ContentError(f"Invalid {label} boolean", field_path=label)


def read_table(root: ET.Element, paragraph_reader) -> dict:
    """Read a physical DrawingML cell grid, validating merge continuation cells."""
    grids = root.findall(f"{{{A_NS}}}tblGrid")
    rows = root.findall(f"{{{A_NS}}}tr")
    if len(grids) != 1:
        raise ContentError("A table must contain one column grid")
    columns = list(grids[0])
    if any(column.tag != f"{{{A_NS}}}gridCol" for column in columns):
        raise ContentError("Unsupported table grid child")
    if not 1 <= len(rows) <= MAX_TABLE_ROWS or not 1 <= len(columns) <= MAX_TABLE_COLUMNS:
        raise ContentError("Table details exceed 100 rows or 50 columns", "resource_limit")
    if len(rows) * len(columns) > MAX_TABLE_CELLS:
        raise ContentError("Table details exceed 5000 cells", "resource_limit")
    widths = [_positive(column.get("w"), 2147483647, "table.column_width") for column in columns]
    heights = [_positive(row.get("h"), 2147483647, "table.row_height") for row in rows]
    cells = [row.findall(f"{{{A_NS}}}tc") for row in rows]
    if any(len(row) != len(columns) for row in cells):
        raise ContentError("Table cell rows must match the physical column grid")
    owners: dict[tuple[int, int], dict] = {}
    merges = []
    output = []
    for row_number, row in enumerate(cells):
        output_row = []
        for column_number, cell in enumerate(row):
            row_span = _positive(cell.get("rowSpan"), len(rows), "table.row_span", 1)
            column_span = _positive(cell.get("gridSpan"), len(columns), "table.column_span", 1)
            horizontal = _flag(cell.get("hMerge"), "table.hMerge")
            vertical = _flag(cell.get("vMerge"), "table.vMerge")
            position = (row_number, column_number)
            owner = owners.get(position)
            if owner is None:
                if horizontal or vertical:
                    raise ContentError("Table continuation has no merge anchor", row=row_number + 1, column=column_number + 1)
                if row_number + row_span > len(rows) or column_number + column_span > len(columns):
                    raise ContentError("Table merge extends beyond its grid", row=row_number + 1, column=column_number + 1)
                if row_span > 1 or column_span > 1:
                    owner = {"row": row_number + 1, "column": column_number + 1, "row_span": row_span, "column_span": column_span}
                    merges.append(owner)
                    for other_row in range(row_number, row_number + row_span):
                        for other_column in range(column_number, column_number + column_span):
                            key = (other_row, other_column)
                            if key in owners:
                                raise ContentError("Overlapping table merge regions")
                            owners[key] = owner
            if owner is not None:
                anchor_row, anchor_column = owner["row"] - 1, owner["column"] - 1
                wanted = (
                    owner["row_span"] if row_number == anchor_row else 1,
                    owner["column_span"] if column_number == anchor_column else 1,
                    column_number > anchor_column,
                    row_number > anchor_row,
                )
                if (row_span, column_span, horizontal, vertical) != wanted:
                    raise ContentError("Table merge continuation disagrees with its anchor", row=row_number + 1, column=column_number + 1)
            bodies = cell.findall(f"{{{A_NS}}}txBody")
            if len(bodies) != 1:
                raise ContentError("A table cell must contain one text body", row=row_number + 1, column=column_number + 1)
            paragraphs = paragraph_reader(bodies[0])
            if (horizontal or vertical) and any(paragraphs):
                raise ContentError("A merged continuation cell contains independent text", row=row_number + 1, column=column_number + 1)
            output_row.append({
                "row": row_number + 1, "column": column_number + 1,
                "paragraphs": paragraphs, "text": "\n".join(paragraphs),
                "merge_anchor": {"row": owner["row"], "column": owner["column"]} if owner is not None else None,
            })
        output.append(output_row)
    return {"row_count": len(rows), "column_count": len(columns), "column_widths_emu": widths,
            "row_heights_emu": heights, "rows": output, "merges": merges}


def _cell_paragraphs(cell: object) -> list[str]:
    def xml_text(value: str) -> str:
        return value.replace("\r\n", "\n").replace("\r", "\n")

    if not isinstance(cell, dict):
        return [xml_text("" if cell is None else str(cell))]
    if "paragraphs" not in cell:
        return [xml_text("" if cell.get("text") is None else str(cell["text"]))]
    result = []
    for paragraph in cell["paragraphs"]:
        if isinstance(paragraph, str):
            result.append(paragraph)
        elif "text" in paragraph:
            result.append(paragraph["text"])
        else:
            result.append("".join(run if isinstance(run, str) else run["text"] for run in paragraph["runs"]))
    return [xml_text(value) for value in result]


def expected_table(payload: dict) -> dict:
    """Normalize source cell content without using the table XML writer."""
    from semantic_table import expand_semantic_table_payload

    expanded = expand_semantic_table_payload(payload)
    rows = ([expanded["columns"]] if expanded.get("columns") else []) + expanded.get("rows", [])
    width = max(len(row) for row in rows)
    merged = {}
    merges = []
    result = []
    for row_number, row in enumerate(rows):
        output = []
        for column_number in range(width):
            cell = row[column_number] if column_number < len(row) else ""
            position = (row_number, column_number)
            covered = merged.get(position)
            if covered is not None and position != covered:
                output.append([""])
                continue
            row_span = cell.get("row_span", 1) if isinstance(cell, dict) else 1
            column_span = cell.get("col_span", 1) if isinstance(cell, dict) else 1
            if row_span > 1 or column_span > 1:
                merges.append({"row": row_number + 1, "column": column_number + 1, "row_span": row_span, "column_span": column_span})
                for other_row in range(row_number, row_number + row_span):
                    for other_column in range(column_number, column_number + column_span):
                        merged[(other_row, other_column)] = position
            output.append(_cell_paragraphs(cell))
        result.append(output)
    return {"row_count": len(rows), "column_count": width, "paragraphs": result, "merges": merges}


def compare_table_input(payload: dict, actual: dict) -> None:
    expected = expected_table(payload)
    observed = {"row_count": actual["row_count"], "column_count": actual["column_count"],
                "paragraphs": [[cell["paragraphs"] for cell in row] for row in actual["rows"]], "merges": actual["merges"]}
    for key, value in expected.items():
        if value != observed[key]:
            raise ContentError("Generated table content differs from its source", field_path=f"table.{key}")


def read_formula(root: ET.Element) -> dict:
    """Return supported OMML structure, retaining math properties and text."""
    from svg_to_pptx.native_objects.formula_omml import validate_omml_fragment

    if root.tag != f"{{{M_NS}}}oMath":
        raise ContentError("Formula inspection expects an Office Math expression")
    try:
        validate_omml_fragment(ET.tostring(root, encoding="unicode"))
    except (ValueError, RuntimeError) as error:
        raise ContentError("Formula is outside the supported OMML inspection profile", "unsupported_content", field_path="formula") from error

    def visit(element: ET.Element) -> dict | None:
        if element.tag == f"{{{M_NS}}}ctrlPr" or element.tag.startswith("{" + A_NS + "}"):
            return None
        if not element.tag.startswith("{" + M_NS + "}"):
            raise ContentError("Unsupported namespace inside a mathematical expression", "unsupported_content")
        tag = element.tag.split("}", 1)[1]
        children = [value for child in element if (value := visit(child)) is not None]
        attributes = {key.rsplit("}", 1)[-1]: value for key, value in sorted(element.attrib.items()) if key.startswith("{" + M_NS + "}")}
        if tag.endswith("Pr") and not children and not attributes:
            return None
        result = {"tag": tag}
        if attributes:
            result["attributes"] = attributes
        if tag == "t":
            result["text"] = element.text or ""
        if children:
            result["children"] = children
        return result

    return {"representation": "omml", "structure": visit(root)}


def expected_formula(latex: str, *, inline: bool) -> dict:
    from svg_to_pptx.native_objects.formula_compiler import compile_latex_to_inline_omml, compile_latex_to_omml

    xml = compile_latex_to_inline_omml(latex) if inline else compile_latex_to_omml(latex)
    # This is compiler-produced XML; external package XML is parsed by safe_xml.
    root = ET.fromstring(xml)
    expression = root if root.tag == f"{{{M_NS}}}oMath" else root.find(f"{{{M_NS}}}oMath")
    if expression is None:
        raise ContentError("Formula compiler did not produce an Office Math expression")
    return read_formula(expression)


def expected_slide(svg: str, notes: str) -> dict:
    """Extract explicit native inputs; declarations and fallback drawings are excluded."""
    from svg_to_pptx.drawingml.utils import svg_hidden_reason
    from .validation import safe_xml

    root = safe_xml(svg.encode("utf-8"), "Prepared SVG content")
    parents = {id(child): parent for parent in root.iter() for child in parent}
    expected = {"charts": [], "tables": [], "formulas": [],
                "notes": notes.replace("\r\n", "\n").replace("\r", "\n").split("\n") if notes else []}

    def visit(element: ET.Element) -> None:
        if element.tag in {f"{{{SVG_NS}}}{name}" for name in ("defs", "clipPath", "marker", "symbol")}:
            return
        hidden = svg_hidden_reason(element, parents)
        if hidden == "display:none" or hidden and element.tag not in {f"{{{SVG_NS}}}g", f"{{{SVG_NS}}}a"}:
            return
        kind = element.get("data-pptx-replace-with")
        if kind is not None and hidden is None:
            raw = element.get("data-pptx-json", element.get("data-pptx-data"))
            if raw is None:
                raw = element.findtext(f"{{{SVG_NS}}}metadata", "")
            payload = json.loads(raw)
            entry = {"element_id": element.get("id"), "payload": payload}
            if kind == "formula":
                entry["expected"] = expected_formula(payload["latex"], inline=False)
            expected[{"chart": "charts", "table": "tables", "formula": "formulas"}[kind]].append(entry)
            return
        if "data-pptx-inline-formula" in element.attrib:
            expected["formulas"].append({"element_id": element.get("id"),
                                         "expected": expected_formula(element.get("data-pptx-inline-formula"), inline=True)})
            return
        for child in element:
            visit(child)

    visit(root)
    return expected


def compare_slide_input(expected: dict, objects: list[dict], notes: list[str], slide_number: int) -> None:
    if expected["notes"] != notes:
        raise ContentError("Generated notes differ from their source", slide_number=slide_number, field_path="notes")
    for kind, key in (("chart", "charts"), ("table", "tables"), ("formula", "formulas")):
        actual = [item for item in objects if item["kind"] == kind]
        sources = expected[key]
        if len(sources) != len(actual):
            raise ContentError("Generated native object count differs from its source", slide_number=slide_number, field_path=key)
        for number, (source, item) in enumerate(zip(sources, actual), 1):
            try:
                if kind == "chart":
                    from .chart_read import compare_chart_input

                    compare_chart_input(source["payload"], item["data"])
                elif kind == "table":
                    compare_table_input(source["payload"], item["data"])
                elif source["expected"] != item["data"]:
                    raise ContentError("Generated formula structure differs from its source", field_path="formula.structure")
            except ValueError as error:
                details = dict(getattr(error, "details", {}))
                details.update(slide_number=slide_number, object_kind=kind, object_number=number)
                if source.get("element_id"):
                    details["element_id"] = source["element_id"]
                raise ContentError(str(error), getattr(error, "code", "content_validation_failed"), **details) from error


class DetailBudget:
    """Reserve response capacity incrementally before retaining each object."""

    def __init__(self) -> None:
        self.count = 0
        self.bytes = 0

    def consume(self, item: dict) -> None:
        self.count += 1
        self.bytes += len(json.dumps(item, ensure_ascii=False, allow_nan=False).encode("utf-8")) + 2
        if self.count > MAX_DETAIL_OBJECTS or self.bytes > MAX_DETAIL_BYTES:
            raise ContentError("Native object details exceed 10000 objects or 8 MiB", "resource_limit")
