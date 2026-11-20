"""Validation and normalization helpers for native table specs in tl-docx creation."""

from __future__ import annotations

import math
import re
import unicodedata

from tl_create_styles import validate_format


_TABLE_FORMAT_KEYS = {
    "preset",
    "column_sizing",
    "band_fill",
    "align",
    "vertical_align",
    "border_style",
    "border_width_pt",
    "border_color",
    "cell_fill",
    "cell_margin_top_mm",
    "cell_margin_bottom_mm",
    "cell_margin_left_mm",
    "cell_margin_right_mm",
    "repeat_header",
    "allow_row_split",
    "borders",
}
_EDGE_ORDER = ("top", "left", "bottom", "right", "insideH", "insideV")
_EDGE_NAMES = set(_EDGE_ORDER)
_CELL_EDGE_NAMES = {"top", "left", "bottom", "right"}
_TABLE_KEYS = {
    "type",
    "rows",
    "header",
    "header_rows",
    "repeat_header",
    "col_widths_cm",
    "widths_mm",
    "merges",
    "format",
    "caption",
    "body_format",
    "header_format",
}
_IMAGE_KEYS = {"path", "width_mm", "height_mm", "alt"}
_COLOR = re.compile(r"[0-9A-Fa-f]{6}\Z")
_DEFAULT_EDGE = {"style": "single", "width_pt": 0.5, "color": "000000"}


def _expect_object(value, path: str, allowed: set[str], required: set[str] = frozenset()) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{path} must be an object")
    if any(not isinstance(key, str) for key in value):
        raise ValueError(f"{path} keys must be strings")
    unknown = set(value) - allowed
    missing = required - set(value)
    if unknown:
        raise ValueError(f"{path} has unsupported keys: {', '.join(sorted(unknown))}")
    if missing:
        raise ValueError(f"{path} is missing required keys: {', '.join(sorted(missing))}")
    return value


def _text(value, path: str, *, allow_empty: bool = True) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{path} must be a string")
    if not allow_empty and not value:
        raise ValueError(f"{path} must not be empty")
    for character in value:
        codepoint = ord(character)
        if unicodedata.category(character) == "Cc" or 0xD800 <= codepoint <= 0xDFFF:
            raise ValueError(f"{path} must not contain control characters")
        if codepoint in (0xFFFE, 0xFFFF):
            raise ValueError(f"{path} contains a character unsupported by XML")
    return value


def _number(value, path: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{path} must be a number")
    number = float(value)
    if not math.isfinite(number) or number < minimum or number > maximum:
        raise ValueError(f"{path} must be between {minimum:g} and {maximum:g}")
    return number


def _integer(value, path: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{path} must be an integer")
    if value < minimum or value > maximum:
        raise ValueError(f"{path} must be between {minimum} and {maximum}")
    return value


def _boolean(value, path: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{path} must be a boolean")
    return value


def _validate_rgb(value, path: str, *, allow_none: bool = False) -> str:
    color = _text(value, path, allow_empty=False)
    if allow_none and color == "none":
        return color
    if _COLOR.fullmatch(color) is None:
        suffix = " or none" if allow_none else ""
        raise ValueError(f"{path} must be a six-digit RGB value{suffix}")
    return color.upper()


def _validate_edge(value, path: str) -> None:
    edge = _expect_object(value, path, {"style", "width_pt", "color"})
    if "style" in edge and (
        not isinstance(edge["style"], str) or edge["style"] not in {"single", "none"}
    ):
        raise ValueError(f"{path}.style must be single or none")
    if "width_pt" in edge:
        _number(edge["width_pt"], f"{path}.width_pt", 0.25, 12)
    if "color" in edge:
        _validate_rgb(edge["color"], f"{path}.color")


def _validate_borders(value, path: str, allowed_edges: set[str]) -> None:
    borders = _expect_object(value, path, allowed_edges)
    for name, edge in borders.items():
        _validate_edge(edge, f"{path}.{name}")


def validate_table_format(value, path: str) -> None:
    """Validate a global or per-table layout format object."""
    options = _expect_object(value, path, _TABLE_FORMAT_KEYS)
    if "preset" in options and (
        not isinstance(options["preset"], str)
        or options["preset"] not in {"grid", "three_line", "zebra"}
    ):
        raise ValueError(f"{path}.preset must be grid, three_line, or zebra")
    if "column_sizing" in options and (
        not isinstance(options["column_sizing"], str)
        or options["column_sizing"] not in {"equal", "content"}
    ):
        raise ValueError(f"{path}.column_sizing must be equal or content")
    if "band_fill" in options:
        _validate_rgb(options["band_fill"], f"{path}.band_fill", allow_none=True)
    if "align" in options and (
        not isinstance(options["align"], str)
        or options["align"] not in {"left", "center", "right"}
    ):
        raise ValueError(f"{path}.align must be left, center, or right")
    if "vertical_align" in options and (
        not isinstance(options["vertical_align"], str)
        or options["vertical_align"] not in {"top", "center", "bottom"}
    ):
        raise ValueError(f"{path}.vertical_align must be top, center, or bottom")
    if "border_style" in options and (
        not isinstance(options["border_style"], str)
        or options["border_style"] not in {"single", "none"}
    ):
        raise ValueError(f"{path}.border_style must be single or none")
    if "border_width_pt" in options:
        _number(options["border_width_pt"], f"{path}.border_width_pt", 0.25, 12)
    if "border_color" in options:
        _validate_rgb(options["border_color"], f"{path}.border_color")
    if "cell_fill" in options:
        _validate_rgb(options["cell_fill"], f"{path}.cell_fill", allow_none=True)
    for side in (
        "top",
        "bottom",
        "left",
        "right",
    ):
        key = f"cell_margin_{side}_mm"
        if key in options:
            _number(options[key], f"{path}.{key}", 0, 50)
    if "repeat_header" in options:
        _boolean(options["repeat_header"], f"{path}.repeat_header")
    if "allow_row_split" in options:
        _boolean(options["allow_row_split"], f"{path}.allow_row_split")
    if "borders" in options:
        _validate_borders(options["borders"], f"{path}.borders", _EDGE_NAMES)


def _validate_image(value, path: str) -> None:
    image = _expect_object(value, path, _IMAGE_KEYS, _IMAGE_KEYS)
    _text(image["path"], f"{path}.path", allow_empty=False)
    _number(image["width_mm"], f"{path}.width_mm", 0.01, 558.8)
    _number(image["height_mm"], f"{path}.height_mm", 0.01, 558.8)
    _text(image["alt"], f"{path}.alt")


def _validate_cell(value, path: str) -> None:
    if value is None:
        return
    if isinstance(value, str):
        _text(value, path)
        return
    cell = _expect_object(
        value,
        path,
        {"text", "image", "borders", "row_span", "col_span"},
    )
    if "text" not in cell and "image" not in cell:
        raise ValueError(f"{path} must contain text or image")
    if "text" in cell:
        _text(cell["text"], f"{path}.text")
    if "image" in cell:
        _validate_image(cell["image"], f"{path}.image")
    for key in ("row_span", "col_span"):
        if key in cell:
            _integer(cell[key], f"{path}.{key}", 1, 256)
    if "borders" in cell:
        _validate_borders(cell["borders"], f"{path}.borders", _CELL_EDGE_NAMES)


def _validate_merge(value, path: str, row_count: int, column_count: int) -> dict:
    merge = _expect_object(value, path, {"row", "col", "rowspan", "colspan"}, {"row", "col", "rowspan", "colspan"})
    row = _integer(merge["row"], f"{path}.row", 0, row_count - 1)
    column = _integer(merge["col"], f"{path}.col", 0, column_count - 1)
    rowspan = _integer(merge["rowspan"], f"{path}.rowspan", 1, row_count)
    colspan = _integer(merge["colspan"], f"{path}.colspan", 1, column_count)
    if rowspan == 1 and colspan == 1:
        raise ValueError(f"{path} must cover more than one cell")
    if row + rowspan > row_count or column + colspan > column_count:
        raise ValueError(f"{path} extends beyond table dimensions")
    return {"row": row, "col": column, "rowspan": rowspan, "colspan": colspan, "strict_null": False}


def _merge_coordinates(merge: dict) -> set[tuple[int, int]]:
    return {
        (row_index, column_index)
        for row_index in range(merge["row"], merge["row"] + merge["rowspan"])
        for column_index in range(merge["col"], merge["col"] + merge["colspan"])
    }


def _normalized_table(block: dict, path: str) -> dict:
    rows = block["rows"]
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"{path}.rows must be a non-empty array")
    if any(not isinstance(row, list) or not row for row in rows):
        raise ValueError(f"{path}.rows must contain non-empty arrays")
    column_count = len(rows[0])
    if any(len(row) != column_count for row in rows):
        raise ValueError(f"{path}.rows must have a consistent number of columns")

    for row_index, row in enumerate(rows):
        for column_index, cell in enumerate(row):
            _validate_cell(cell, f"{path}.rows[{row_index}][{column_index}]")

    header = block.get("header")
    if "header" in block:
        _boolean(header, f"{path}.header")
    legacy_header_rows = 1 if header else 0
    if "header_rows" in block:
        header_rows = _integer(block["header_rows"], f"{path}.header_rows", 0, len(rows))
        if "header" in block and header_rows != legacy_header_rows:
            raise ValueError(f"{path}.header and header_rows must agree")
    else:
        header_rows = legacy_header_rows

    merges = []
    occupied: set[tuple[int, int]] = set()
    for row_index, row in enumerate(rows):
        for column_index, cell in enumerate(row):
            if not isinstance(cell, dict):
                continue
            rowspan = cell.get("row_span", 1)
            colspan = cell.get("col_span", 1)
            if rowspan == 1 and colspan == 1:
                continue
            if row_index + rowspan > len(rows) or column_index + colspan > column_count:
                raise ValueError(f"{path}.rows[{row_index}][{column_index}] span extends beyond table dimensions")
            merge = {
                "row": row_index,
                "col": column_index,
                "rowspan": rowspan,
                "colspan": colspan,
                "strict_null": True,
            }
            coordinates = _merge_coordinates(merge)
            if occupied.intersection(coordinates):
                raise ValueError(f"{path} contains overlapping cell spans")
            occupied.update(coordinates)
            merges.append(merge)

    if "merges" in block:
        if not isinstance(block["merges"], list):
            raise ValueError(f"{path}.merges must be an array")
        for index, raw_merge in enumerate(block["merges"]):
            merge = _validate_merge(raw_merge, f"{path}.merges[{index}]", len(rows), column_count)
            coordinates = _merge_coordinates(merge)
            if occupied.intersection(coordinates):
                raise ValueError(f"{path}.merges[{index}] overlaps another merge or cell span")
            occupied.update(coordinates)
            merges.append(merge)

    covered: set[tuple[int, int]] = set()
    for merge in merges:
        coordinates = _merge_coordinates(merge)
        if merge["row"] < header_rows < merge["row"] + merge["rowspan"]:
            raise ValueError(f"{path} vertical merge crosses the header/data boundary")
        anchor = rows[merge["row"]][merge["col"]]
        if anchor is None:
            raise ValueError(f"{path} merge anchor cell must contain text or image")
        for row_index, column_index in coordinates - {(merge["row"], merge["col"])}:
            cell_value = rows[row_index][column_index]
            if merge["strict_null"]:
                if cell_value is not None:
                    raise ValueError(f"{path} cell spans require null in every covered cell")
            elif cell_value not in {None, ""}:
                raise ValueError(f"{path} merge covers a non-empty cell")
        covered.update(coordinates)

    for row_index, row in enumerate(rows):
        for column_index, value in enumerate(row):
            if value is None and (row_index, column_index) not in covered:
                raise ValueError(f"{path}.rows[{row_index}][{column_index}] contains null outside a declared merge")

    if "repeat_header" in block:
        _boolean(block["repeat_header"], f"{path}.repeat_header")
        if block["repeat_header"] and header_rows == 0:
            raise ValueError(f"{path}.repeat_header requires header or header_rows")
    if "col_widths_cm" in block:
        widths = block["col_widths_cm"]
        if not isinstance(widths, list) or len(widths) != column_count:
            raise ValueError(f"{path}.col_widths_cm must have one width per column")
        for index, width in enumerate(widths):
            _number(width, f"{path}.col_widths_cm[{index}]", 0.1, 50)
    if "widths_mm" in block:
        widths = block["widths_mm"]
        if not isinstance(widths, list) or len(widths) != column_count:
            raise ValueError(f"{path}.widths_mm must have one width per column")
        for index, width in enumerate(widths):
            _number(width, f"{path}.widths_mm[{index}]", 0.1, 558.8)
    if "col_widths_cm" in block and "widths_mm" in block:
        old_widths = [float(value) * 10 for value in block["col_widths_cm"]]
        if any(abs(old - float(new)) > 0.01 for old, new in zip(old_widths, block["widths_mm"])):
            raise ValueError(f"{path}.col_widths_cm and widths_mm must agree when both are present")
    if "format" in block:
        validate_table_format(block["format"], f"{path}.format")
    for text_format in ("body_format", "header_format"):
        if text_format in block:
            validate_format(block[text_format], f"{path}.{text_format}")
    if "caption" in block:
        _text(block["caption"], f"{path}.caption", allow_empty=False)

    return {
        "rows": rows,
        "row_count": len(rows),
        "column_count": column_count,
        "header_rows": header_rows,
        "merges": merges,
        "covered": covered,
    }


def validate_table(block: dict, path: str) -> None:
    """Validate all table geometry and block-level settings."""
    _expect_object(block, path, _TABLE_KEYS, {"type", "rows"})
    _normalized_table(block, path)


__all__ = [
    "_DEFAULT_EDGE",
    "_EDGE_NAMES",
    "_EDGE_ORDER",
    "_normalized_table",
    "validate_table",
    "validate_table_format",
]
