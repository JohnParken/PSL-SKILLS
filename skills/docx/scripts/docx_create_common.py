"""Shared validation primitives and formatting defaults for DOCX generation."""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import math
import os
import re
import shutil
import stat
import struct
import sys
import tempfile
import unicodedata
import zipfile
import zlib
from pathlib import Path
from xml.sax.saxutils import quoteattr
from typing import Any
from xml.etree import ElementTree as ET

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from artifact_files import (_check_delivery_path, _prepare_delivery_directory,
                            _paths_alias, _archive_output_path, _copy_main_to_archive)

from office import xmlio
from office.runtime import check_runtime
from office.formatting.profiles import apply_profile
from office.formatting.sections import parse_section, SECTION_KEYS, BREAK_TYPES
from office.formatting.tables import normalize_merged_grid
from office.formatting.paragraphs import (
    PARAGRAPH_FORMAT_FIELDS, append_paragraph_properties,
)
from output_location import OutputLocation, resolve_output_location, read_router_config_from_environment

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"
WP = "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
PIC = "http://schemas.openxmlformats.org/drawingml/2006/picture"
XML = "http://www.w3.org/XML/1998/namespace"

ET.register_namespace("w", W)
ET.register_namespace("r", R)
ET.register_namespace("wp", WP)
ET.register_namespace("a", A)
ET.register_namespace("pic", PIC)
ET.register_namespace("", CT)

_CONTENT_TYPES = {
    "document": "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml",
    "styles": "application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml",
    "numbering": "application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml",
    "settings": "application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml",
    "header": "application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml",
    "footer": "application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml",
}
_REL_TYPES = {
    "officeDocument": f"{R}/officeDocument",
    "styles": f"{R}/styles",
    "numbering": f"{R}/numbering",
    "settings": f"{R}/settings",
    "header": f"{R}/header",
    "footer": f"{R}/footer",
    "image": f"{R}/image",
}
_MAX_PAGE_MM = 558.8  # 22 inches, the Word page-size limit.
_MAX_MARGIN_MM = 250.0
_MAX_IMAGE_BYTES = 32 * 1024 * 1024
_MAX_XML_BYTES = 16 * 1024 * 1024
_MAX_PACKAGE_BYTES = 128 * 1024 * 1024
_MAX_PACKAGE_PARTS = 4096
_COLOR_RE = re.compile(r"^[0-9A-Fa-f]{6}$")
_ALIGNMENTS = {"left", "center", "right", "justify"}
_STYLE_FIELDS = {
    "font_east_asia", "font_latin", "size_pt", "bold", "align",
    "first_line_chars", "line_spacing_pt", "space_before_pt", "space_after_pt",
    "line_spacing_multiple", "color",
} | PARAGRAPH_FORMAT_FIELDS
_STYLE_DEFAULTS = {
    "body": {
        "font_east_asia": "仿宋", "font_latin": "Times New Roman", "size_pt": 16,
        "bold": False, "align": "left", "first_line_chars": 2,
        "line_spacing_pt": 28, "space_before_pt": 0, "space_after_pt": 0,
    },
    "heading1": {
        "font_east_asia": "黑体", "font_latin": "Times New Roman", "size_pt": 16,
        "bold": False, "align": "left", "first_line_chars": 0,
        "space_before_pt": 0, "space_after_pt": 0,
    },
    "heading2": {
        "font_east_asia": "楷体", "font_latin": "Times New Roman", "size_pt": 16,
        "bold": False, "align": "left", "first_line_chars": 0,
        "space_before_pt": 0, "space_after_pt": 0,
    },
    "heading3": {
        "font_east_asia": "仿宋", "font_latin": "Times New Roman", "size_pt": 16,
        "bold": False, "align": "left", "first_line_chars": 0,
        "space_before_pt": 0, "space_after_pt": 0,
    },
    "heading4": {
        "font_east_asia": "仿宋", "font_latin": "Times New Roman", "size_pt": 16,
        "bold": False, "align": "left", "first_line_chars": 0,
        "space_before_pt": 0, "space_after_pt": 0,
    },
}
_STYLE_DEFAULTS.update({
    "title": {**_STYLE_DEFAULTS["heading1"], "font_east_asia": "方正小标宋简体", "align": "center", "size_pt": 22, "space_after_pt": 12},
    "subtitle": {**_STYLE_DEFAULTS["heading1"], "font_east_asia": "方正小标宋简体", "align": "center", "size_pt": 14, "space_after_pt": 8},
})
_TABLE_STYLE_DEFAULTS = {
    "table_body": {
        "font_east_asia": "仿宋", "font_latin": "Times New Roman", "size_pt": 16,
        "color": "000000", "bold": False, "align": "left", "first_line_chars": 0,
        "line_spacing_multiple": 1.15, "space_before_pt": 0, "space_after_pt": 0,
    },
    "table_header": {
        "font_east_asia": "仿宋", "font_latin": "Times New Roman", "size_pt": 16,
        "color": "000000", "bold": True, "align": "center", "first_line_chars": 0,
        "line_spacing_multiple": 1.15, "space_before_pt": 0, "space_after_pt": 0,
    },
}
_TABLE_LAYOUT_DEFAULTS = {
    "align": "left", "vertical_align": "center", "border_style": "single",
    "border_width_pt": 0.5, "border_color": "000000", "cell_fill": "none",
    "cell_margin_top_mm": 1, "cell_margin_bottom_mm": 1,
    "cell_margin_left_mm": 1.5, "cell_margin_right_mm": 1.5,
    "repeat_header": True, "allow_row_split": False,
    "preset": "grid", "column_sizing": "equal", "band_fill": "none",
}
_TABLE_BORDER_EDGES = {"top", "bottom", "left", "right", "insideH", "insideV"}


def _q(namespace: str, local: str) -> str:
    return f"{{{namespace}}}{local}"


def _object(value: Any, label: str, allowed: set[str], required: set[str] = frozenset()) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    unknown = set(value) - allowed
    if unknown:
        hints = []
        for key in sorted(unknown, key=str):
            matches = difflib.get_close_matches(str(key), sorted(allowed), n=1, cutoff=0.7)
            if matches:
                hints.append(f"{key!r}: did you mean {matches[0]!r}?")
        suffix = " (" + "; ".join(hints) + ")" if hints else ""
        raise ValueError(f"{label} has unknown field(s): {', '.join(map(str, sorted(unknown, key=str)))}{suffix}")
    missing = required - set(value)
    if missing:
        raise ValueError(f"{label} is missing field(s): {', '.join(sorted(missing))}")
    return value


def _number(value: Any, label: str, low: float, high: float, *, allow_zero: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be a number")
    try:
        result = float(value)
    except (OverflowError, ValueError):
        raise ValueError(f"{label} must be a finite number") from None
    if not math.isfinite(result) or result > high or result < low or (not allow_zero and result == 0):
        raise ValueError(f"{label} must be between {low:g} and {high:g}")
    return result


def _single_line_text(value: Any, label: str) -> str:
    text = _text(value, label, allow_empty=False)
    if len(text) > 64:
        raise ValueError(f"{label} must be at most 64 characters")
    if any(char in "\t\n\r\u0085\u2028\u2029" for char in text):
        raise ValueError(f"{label} must be a single-line string")
    return text


def _validate_style_fields(value: Any, label: str) -> dict:
    fields = _object(value, label, _STYLE_FIELDS)
    if "line_spacing_pt" in fields and "line_spacing_multiple" in fields:
        raise ValueError(f"{label} cannot set both line_spacing_pt and line_spacing_multiple")
    if len(set(fields) & {"first_line_chars", "first_line_indent_pt", "hanging_indent_pt"}) > 1:
        raise ValueError(f"{label} cannot mix first-line character, first-line point, and hanging indents")
    result: dict[str, Any] = {}
    for key, field_value in fields.items():
        field_label = f"{label}.{key}"
        if key in ("font_east_asia", "font_latin"):
            result[key] = _single_line_text(field_value, field_label)
        elif key == "size_pt":
            result[key] = _number(field_value, field_label, 1, 200)
        elif key in {"bold", "keep_with_next", "keep_together", "widow_control", "page_break_before"}:
            if not isinstance(field_value, bool):
                raise ValueError(f"{field_label} must be a boolean")
            result[key] = field_value
        elif key == "align":
            if not isinstance(field_value, str) or field_value not in _ALIGNMENTS:
                raise ValueError(f"{field_label} must be left, center, right, or justify")
            result[key] = field_value
        elif key == "first_line_chars":
            result[key] = _number(field_value, field_label, 0, 20, allow_zero=True)
        elif key == "line_spacing_pt":
            result[key] = _number(field_value, field_label, 1, 400)
        elif key == "line_spacing_multiple":
            result[key] = _number(field_value, field_label, 0.5, 10)
        elif key == "color":
            color = _text(field_value, field_label, allow_empty=False)
            if not _COLOR_RE.fullmatch(color):
                raise ValueError(f"{field_label} must be six hexadecimal RGB characters without #")
            result[key] = color.upper()
        elif key in ("space_before_pt", "space_after_pt",
                     "left_indent_pt", "right_indent_pt", "first_line_indent_pt", "hanging_indent_pt"):
            result[key] = _number(field_value, field_label, 0, 400, allow_zero=True)
        elif key == "tab_stops":
            if not isinstance(field_value, list) or len(field_value) > 32:
                raise ValueError(f"{field_label} must be a list of at most 32 tab stops")
            stops = []
            previous = -1.0
            for index, stop in enumerate(field_value):
                prefix = f"{field_label}[{index}]"
                stop = _object(stop, prefix, {"position_pt", "align"}, {"position_pt", "align"})
                position = _number(stop["position_pt"], prefix + ".position_pt", 0, 1000, allow_zero=True)
                if position <= previous:
                    raise ValueError(f"{field_label} positions must be strictly increasing")
                align = stop["align"]
                if not isinstance(align, str) or align not in {"left", "right", "center", "decimal"}:
                    raise ValueError(f"{prefix}.align must be left, right, center or decimal")
                previous = position
                stops.append({"position_pt": position, "align": align})
            result[key] = stops
    return result


def _merge_table_text_format(base: dict, layer: dict) -> dict:
    result = base.copy()
    if "line_spacing_pt" in layer:
        result.pop("line_spacing_multiple", None)
    elif "line_spacing_multiple" in layer:
        result.pop("line_spacing_pt", None)
    for key in ("first_line_chars", "first_line_indent_pt", "hanging_indent_pt"):
        if key in layer:
            for previous in ("first_line_chars", "first_line_indent_pt", "hanging_indent_pt"):
                if previous != key:
                    result.pop(previous, None)
    result.update(layer)
    return result


def _validate_table_text_fields(value: Any, label: str) -> dict:
    return _validate_style_fields(value, label)


def _validate_table_layout(value: Any, label: str) -> dict:
    fields = _object(value, label, set(_TABLE_LAYOUT_DEFAULTS) | {"borders"})
    result: dict[str, Any] = {}
    for key, field_value in fields.items():
        field_label = f"{label}.{key}"
        if key == "borders":
            result[key] = _validate_borders(field_value, field_label)
        elif key in {"preset", "column_sizing"}:
            choices = {"grid", "three_line", "zebra"} if key == "preset" else {"equal", "content"}
            if not isinstance(field_value, str) or field_value not in choices:
                raise ValueError(f"{field_label} must be one of {', '.join(sorted(choices))}")
            result[key] = field_value
        elif key == "align":
            if not isinstance(field_value, str) or field_value not in {"left", "center", "right"}:
                raise ValueError(f"{field_label} must be left, center, or right")
            result[key] = field_value
        elif key == "vertical_align":
            if not isinstance(field_value, str) or field_value not in {"top", "center", "bottom"}:
                raise ValueError(f"{field_label} must be top, center, or bottom")
            result[key] = field_value
        elif key == "border_style":
            if not isinstance(field_value, str) or field_value not in {"single", "none"}:
                raise ValueError(f"{field_label} must be single or none")
            result[key] = field_value
        elif key == "border_width_pt":
            result[key] = _number(field_value, field_label, 0.25, 12)
        elif key in {"border_color", "cell_fill", "band_fill"}:
            text = _text(field_value, field_label, allow_empty=False)
            if key != "border_color" and text == "none":
                result[key] = text
            elif _COLOR_RE.fullmatch(text):
                result[key] = text.upper()
            else:
                allowed = "six hexadecimal RGB characters without #" if key == "border_color" \
                    else "six hexadecimal RGB characters or 'none'"
                raise ValueError(f"{field_label} must be {allowed}")
        elif key.startswith("cell_margin_"):
            result[key] = _number(field_value, field_label, 0, 50, allow_zero=True)
        elif key in {"repeat_header", "allow_row_split"}:
            if not isinstance(field_value, bool):
                raise ValueError(f"{field_label} must be a boolean")
            result[key] = field_value
    if "preset" in result:
        preset = result["preset"]
        defaults = {"border_style": "single" if preset == "grid" else "none",
                    "band_fill": "F2F4F7" if preset == "zebra" else "none",
                    "borders": {edge: {"style": "single" if preset == "grid" else "none", "width_pt": 0.5}
                                for edge in _TABLE_BORDER_EDGES}}
        if preset == "three_line":
            for edge in ("top", "bottom"):
                defaults["borders"][edge] = {"style": "single", "width_pt": 1.5}
        for fields in defaults["borders"].values():
            if "border_style" in result:
                fields["style"] = result["border_style"]
            if "border_width_pt" in result:
                fields["width_pt"] = result["border_width_pt"]
        result = _merge_table_layout(defaults, result)
    return result


def _validate_borders(value: Any, label: str) -> dict:
    edges = _object(value, label, _TABLE_BORDER_EDGES)
    result = {}
    for edge, value in edges.items():
        fields = _object(value, f"{label}.{edge}", {"style", "width_pt", "color"})
        border = {}
        if "style" in fields:
            style = fields["style"]
            if not isinstance(style, str) or style not in {"single", "none"}:
                raise ValueError(f"{label}.{edge}.style must be single or none")
            border["style"] = style
        if "width_pt" in fields:
            border["width_pt"] = _number(fields["width_pt"], f"{label}.{edge}.width_pt", 0.25, 12)
        if "color" in fields:
            color = _text(fields["color"], f"{label}.{edge}.color", allow_empty=False)
            if not _COLOR_RE.fullmatch(color):
                raise ValueError(f"{label}.{edge}.color must be six hexadecimal RGB characters")
            border["color"] = color.upper()
        result[edge] = border
    return result


def _merge_table_layout(base: dict, layer: dict) -> dict:
    result = {key: value for key, value in base.items() if key != "borders"}
    result.update({key: value for key, value in layer.items() if key != "borders"})
    if "borders" in base or "borders" in layer:
        borders = {edge: fields.copy() for edge, fields in base.get("borders", {}).items()}
        for edge, fields in layer.get("borders", {}).items():
            borders.setdefault(edge, {}).update(fields)
        result["borders"] = borders
    return result


def _resolved_border(layout: dict, edge: str) -> dict:
    result = {"style": layout["border_style"], "width_pt": layout["border_width_pt"],
              "color": layout["border_color"]}
    result.update(layout.get("borders", {}).get(edge, {}))
    return result


def _border_attributes(border: dict) -> dict:
    if border["style"] == "none":
        return {_q(W, "val"): "nil"}
    return {_q(W, "val"): "single", _q(W, "sz"): str(int(border["width_pt"] * 8 + 0.5)),
            _q(W, "space"): "0", _q(W, "color"): border["color"]}


def _cell_borders(rows: list, layout: dict, label: str) -> None:
    """Resolve explicit cell edges and make shared boundaries unambiguous.

    Mirror a single explicit override to its neighbour. Two explicit overrides
    must encode the same border; never rely on a reader's border conflict rules.
    Cells without any explicit or mirrored override keep the old XML output.
    """
    row_count, column_count = len(rows), len(rows[0])
    resolved = {}
    for row_index, row in enumerate(rows):
        for column, cell in enumerate(row):
            if not isinstance(cell, dict):
                continue
            for edge, fields in cell.get("borders", {}).items():
                table_edge = edge
                if edge == "top" and row_index > 0 or edge == "bottom" and row_index < row_count - 1:
                    table_edge = "insideH"
                elif edge == "left" and column > 0 or edge == "right" and column < column_count - 1:
                    table_edge = "insideV"
                border = _resolved_border(layout, table_edge)
                border.update(fields)
                resolved[row_index, column, edge] = border
    for (row_index, column, edge), border in list(resolved.items()):
        adjacent = {"top": (row_index - 1, column, "bottom"),
                    "bottom": (row_index + 1, column, "top"),
                    "left": (row_index, column - 1, "right"),
                    "right": (row_index, column + 1, "left")}[edge]
        other_row, other_column, _ = adjacent
        if not (0 <= other_row < row_count and 0 <= other_column < column_count):
            continue
        if adjacent in resolved and _border_attributes(resolved[adjacent]) != _border_attributes(border):
            raise ValueError(f"{label}.rows[{row_index}][{column}].borders.{edge} conflicts with the adjacent cell border")
        resolved[adjacent] = border.copy()
    for (row_index, column, edge), border in resolved.items():
        cell = rows[row_index][column]
        if isinstance(cell, str):
            cell = {"text": cell, "image": None}
            rows[row_index][column] = cell
        cell.setdefault("borders", {})[edge] = border


def _theme_base_format(theme: str) -> dict:
    """Share non-official theme typography across body and related parts."""
    return {"font_east_asia": "微软雅黑" if theme == "business" else "宋体",
            "font_latin": "Calibri" if theme == "business" else "Times New Roman",
            "size_pt": 10.5, "line_spacing_multiple": 1.25}


def _validated_styles(value: Any, theme: str = "official") -> dict[str, dict]:
    if value is None:
        raise ValueError("styles must be an object")
    entries = _object(value, "styles", set(_STYLE_DEFAULTS) | set(_TABLE_STYLE_DEFAULTS))
    result = {name: defaults.copy() for name, defaults in _STYLE_DEFAULTS.items()}
    result.update({name: defaults.copy() for name, defaults in _TABLE_STYLE_DEFAULTS.items()})
    if theme != "official":
        for name, defaults in result.items():
            defaults.pop("line_spacing_pt", None)
            defaults.update({**_theme_base_format(theme), "space_after_pt": 4, "first_line_chars": 0})
            if name in {"title", "subtitle"} or name.startswith("heading"):
                defaults.update({"font_east_asia": "黑体", "bold": True,
                                 "size_pt": {"title": 22, "subtitle": 12, "heading1": 16,
                                             "heading2": 14, "heading3": 12, "heading4": 11}[name],
                                 "color": "1A365D" if theme == "business" else "000000"})
                defaults["align"] = "center" if name in {"title", "subtitle"} else "left"
            elif name == "body" and theme == "academic":
                defaults["first_line_chars"] = 2
    for name, fields in entries.items():
        if name in _TABLE_STYLE_DEFAULTS:
            result[name] = _merge_table_text_format(
                result[name], _validate_table_text_fields(fields, f"styles.{name}"),
            )
        else:
            result[name] = _merge_table_text_format(
                result[name], _validate_style_fields(fields, f"styles.{name}"))
    return result


def _normalize_run_fonts(run: dict) -> dict:
    normalized = {key: value for key, value in run.items() if key != "font"}
    if "font" in run:
        normalized["font_east_asia"] = run["font"]
        normalized["font_latin"] = run["font"]
    if "font_east_asia" in run:
        normalized["font_east_asia"] = run["font_east_asia"]
    if "font_latin" in run:
        normalized["font_latin"] = run["font_latin"]
    return normalized


def _text(value: Any, label: str, *, allow_empty: bool = True) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    if not allow_empty and not value:
        raise ValueError(f"{label} must not be empty")
    for index, char in enumerate(value):
        point = ord(char)
        if unicodedata.category(char) == "Cc" or not (
            point in (0x9, 0xA, 0xD)
            or 0x20 <= point <= 0xD7FF
            or 0xE000 <= point <= 0xFFFD
            or 0x10000 <= point <= 0x10FFFF
        ):
            hint = "; split multiline content into paragraph blocks" if char in "\t\r\n" else ""
            raise ValueError(f"{label} contains an unsupported control or XML character "
                             f"U+{point:04X} at character {index + 1}{hint}")
    return value


def _content_widths(rows: list, total: int, padding: int, label: str) -> list[int]:
    """Estimate widths without font metrics; keep a usable minimum per column."""
    columns = len(rows[0])
    minimum = padding + _twips(8)
    if columns * minimum > total:
        raise ValueError(f"{label}: content column sizing needs fewer columns or smaller cell margins")
    weights = []
    for column in range(columns):
        longest = 1
        for row in rows:
            cell = row[column]
            text = cell if isinstance(cell, str) else cell.get("text", "") if isinstance(cell, dict) else ""
            if isinstance(text, str):
                longest = max(longest, sum(2 if unicodedata.east_asian_width(char) in {"W", "F"}
                                           else 1 for char in text))
        weights.append(math.sqrt(longest))
    remaining = total - minimum * columns
    weight_total = sum(weights)
    widths = [minimum + int(remaining * weight / weight_total) for weight in weights]
    for index in range(total - sum(widths)):
        widths[index % columns] += 1
    return widths


def _twips(mm: float) -> int:
    return int(mm * 1440.0 / 25.4 + 0.5)


def _emu(mm: float) -> int:
    return int(mm * 36000.0 + 0.5)
