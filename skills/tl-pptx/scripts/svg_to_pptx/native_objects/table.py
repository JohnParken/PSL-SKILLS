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



# Stable public facade; implementation is split by responsibility.
from .table_text_xml import (
    _table_text_run,
    _table_paragraph_properties,
    _cell_payload,
)
from .table_content import (
    _table_rows,
    _table_cell_paragraphs,
    _table_run,
)
from .table_validation import (
    _table_span_value,
    _merge_covered_cell_is_blank,
    _resolve_table_merge_layout,
    _grid_is_strict,
    _table_bool,
    _table_header_rows,
    _validate_table_lengths,
    _validate_table_cell_formatting,
    _validate_table_payload,
)
from .table_fallback_grid import (
    _table_cell_text_parts,
    _native_table_metadata_texts,
    _table_fallback_frame,
    _dedupe_table_edges,
    _table_fallback_grid,
    _table_cell_at,
    _table_cell_texts,
)
from .table_parity import (
    _table_cell_parity_text_style,
    _table_cell_parity_style_gaps,
    _table_parity_style_gaps,
    _table_text_cells,
    _fallback_table_alignment,
)
from .table_appearance_warnings import (
    _native_table_header_warnings,
    _native_table_fill_warnings,
    _native_table_body_text_warnings,
    _native_table_first_column_warnings,
    _native_table_inset_graphic_warnings,
)
from .table_topology_warnings import (
    _native_table_border_topology_warnings,
    _native_table_warnings,
)
from .table_layout import (
    _weighted_lengths,
    _table_weights,
    _table_padding_value,
    _table_text_height_emu,
    _table_padding_attrs,
    _table_anchor,
    _table_cell_extra_attrs,
    _table_fill_opacity,
    _table_border_width,
)
from .table_format_xml import (
    _strict_table_border_color,
    _table_border_override,
    _table_border_specs,
    _table_border_xml,
    _table_fill_xml,
    _table_merge_attrs,
)
from .table_conversion import (
    _build_native_table,
)
