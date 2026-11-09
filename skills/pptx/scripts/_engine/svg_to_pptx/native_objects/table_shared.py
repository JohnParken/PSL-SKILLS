# Adapted from PPT Master 5e8856b (MIT); see assets/licenses/ppt_master.txt.
"""Native PowerPoint table conversion."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from xml.etree import ElementTree as ET

from semantic_table import expand_semantic_table_payload

from .marker_attributes import native_import_source

from ..drawingml.context import ConvertContext, ShapeResult
from ..drawingml.theme_colors import ThemeColorSpec, color_node_xml
from ..drawingml.utils import (
    _xml_escape,
    detect_text_lang,
    font_px_to_hpt,
    px_to_emu,
    text_has_rtl_characters,
    text_uses_rtl,
)
from .chart_style import _fallback_text_attr_values, _font_face_xml, _most_common_value
from .marker_common import (
    TABLE_URI,
    _FallbackTextRecord,
    _bool_attr,
    _bounds,
    _clean_hex,
    _fallback_shape_records,
    _fallback_text_records,
    _first_present,
    _font_size_hpt,
    _hex_or_none,
    _maybe_number,
    _normalized_fallback_text,
    _number,
    _powerpoint_emu,
    _powerpoint_line_width_emu,
    _visible_fallback_texts,
)


_TABLE_CANONICAL_SPAN_KEYS = {
    "col_span",
    "row_span",
}
_TABLE_UNSUPPORTED_SPAN_KEYS = {
    "colSpan",
    "grid_span",
    "gridSpan",
    "hMerge",
    "merge",
    "merged",
    "rowSpan",
    "vMerge",
}
_TABLE_TOP_LEVEL_SPAN_KEYS = {
    "merge_cells",
    "merged_cells",
    "merges",
    "spans",
}
_TABLE_MAX_ROWS = 1000
_TABLE_MAX_COLUMNS = 1000


@dataclass(frozen=True)
class _TableMergeRegion:
    row: int
    col: int
    row_span: int
    col_span: int


@dataclass(frozen=True)
class _TableBorderSpec:
    style: str
    color: str | None = None
    width: float | None = None


@dataclass(frozen=True)
class _TableRun:
    text: str
    bold: bool | None = None
    italic: bool | None = None
    underline: bool | None = None
    strike: bool | None = None
    color: str | None = None
    font_size: int | None = None
    font_family: str | None = None
    lang: str | None = None
    alt_lang: str | None = None
    baseline: int | None = None
    outline: _TableBorderSpec | None = None


@dataclass(frozen=True)
class _TableParagraph:
    text: str
    align: str | None = None
    runs: tuple[_TableRun, ...] | None = None
    line_spacing_percent: float | None = None


_TEXT_LINE_FACTOR = 1.4  # PowerPoint line box per em, covering CJK faces' tall metrics


_TABLE_BORDER_SIDES = (
    "left",
    "right",
    "top",
    "bottom",
    "diagonal_down",
    "diagonal_up",
)
_TABLE_BORDER_TAGS = {
    "left": "lnL",
    "right": "lnR",
    "top": "lnT",
    "bottom": "lnB",
    "diagonal_down": "lnTlToBr",
    "diagonal_up": "lnBlToTr",
}
