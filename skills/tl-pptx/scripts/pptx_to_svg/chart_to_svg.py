"""Extract native Chart replacement metadata from PPTX chart parts.

The visual chart preview still comes from the existing graphicFrame fallback.
This module only builds a conservative ``data-pptx-replace-with="chart"``
payload when the chart XML cache can be mapped to the current chart schema.
"""

from __future__ import annotations

import base64
import hashlib
import math
import re
from dataclasses import dataclass
from typing import Any
from xml.etree import ElementTree as ET

from svg_to_pptx.drawingml.utils import parse_font_family
from svg_to_pptx.native_objects.chart_data import (
    validate_chart_payload,
    validate_data_label_position,
)

from .chartex_to_svg import UnsupportedChartEx, extract_native_chartex_payload
from .color_resolver import ColorPalette, find_color_elem, resolve_color
from .emu_units import NS, Xfrm, ooxml_bool
from .normalized_chart_svg import SeriesVisualStyle, render_normalized_chart_svg
from .ooxml_loader import OoxmlPackage, PartRef


CHART_URI = "http://schemas.openxmlformats.org/drawingml/2006/chart"
CHARTEX_URI = "http://schemas.microsoft.com/office/drawing/2014/chartex"

C_NS = {
    **NS,
    "c": "http://schemas.openxmlformats.org/drawingml/2006/chart",
    "cx": CHARTEX_URI,
}


@dataclass
class ChartResult:
    """Native chart marker payload or a transparent unsupported status."""

    native_payload: dict[str, Any] | None = None
    native_status: str | None = None
    normalized_svg: str | None = None


class _UnsupportedChart(RuntimeError):
    """Raised when a chart should keep its visual fallback only."""

    def __init__(self, status: str) -> None:
        super().__init__(status)
        self.status = status


@dataclass(frozen=True)
class _LinePaint:
    color: str | None
    opacity: float
    width: float
    cap: str
    visible: bool
    automatic: bool


@dataclass(frozen=True)
class _ShapePaint:
    fill: str | None
    fill_opacity: float
    fill_explicit: bool
    line: _LinePaint | None


@dataclass(frozen=True)
class _MarkerPaint:
    symbol: str | None
    size: float
    shape: _ShapePaint


_FALLBACK_CHART_COLORS = (
    "#4472C4", "#ED7D31", "#A5A5A5", "#FFC000",
    "#5B9BD5", "#70AD47", "#264478", "#9E480E",
)
_COLOR_MODIFIERS_WITH_VALUE = {
    "alpha", "alphaMod", "alphaOff", "hueMod", "hueOff", "lumMod",
    "lumOff", "satMod", "satOff", "shade", "tint",
}
_COLOR_MODIFIERS_WITHOUT_VALUE = {"comp", "gray", "inv"}


_DATA_LABEL_POSITION_ALIASES = {
    "bestFit": "best_fit",
    "ctr": "center",
    "inBase": "inside_base",
    "inEnd": "inside_end",
    "outEnd": "outside_end",
    "t": "above",
}



# Stable upstream API facade, split by conversion responsibility.
from .chart_to_svg_payload import (
    extract_native_chart_payload,
    _payload_from_chart_xml,
    _combo_payload,
    _validate_stock_semantics,
    _stock_payload,
)
from .chart_to_svg_paint import (
    _canonical_srgb_color,
    _resolved_solid_fill,
    _resolved_line,
    _resolved_shape_paint,
    _resolved_marker,
    _automatic_color,
    _line_color,
    _series_visual_style,
    _pie_visual_styles,
    _chart_visual_styles,
)
from .chart_to_svg_normalized_styles import (
    _representative_gradient_color,
    _representative_fill,
    _normalized_line_paint,
    _normalized_shape_paint,
    _normalized_marker_paint,
    _normalized_chart_visual_styles,
)
from .chart_to_svg_axes import (
    _strict_axis_bool,
    _validate_canonical_series_order,
    _plot_series_identifiers,
    _axis_number,
    _axis_nodes_by_id,
    _plot_axis_pair,
    _axis_config_from_xml,
    _single_axis_contract,
    _xy_axis_contract,
    _validate_legacy_axes,
    _validate_normalized_axis_topology,
    _validate_or_normalize_legacy_axes,
)
from .chart_to_svg_semantics import (
    _effective_radar_style,
    _effective_scatter_style,
    _bounded_plot_integer,
    _validate_chart_semantics,
)
from .chart_to_svg_formatting import (
    _apply_series_formatting,
    _apply_plot_data_labels,
    _apply_chart_metadata,
    _styled_chart_title,
    _chart_fill_color,
    _apply_chart_formatting,
)
from .chart_to_svg_text_labels import (
    _chart_text,
    _canonical_title_paragraph,
    _canonical_title_entries,
    _data_label_text_style,
    _data_label_position_payload,
    _data_label_flag_payload,
)
from .chart_to_svg_data_labels import (
    _source_chart_text_style,
    _point_data_label_payload,
    _source_data_labels_ooxml,
    _data_labels_payload,
)
from .chart_to_svg_cache import (
    _category_payload,
    _xy_payload,
    _bar_chart_type,
    _line_style,
    _category_values,
    _category_cache_is_numeric,
    _numeric_category_cache_format,
    _series_name,
    _text_cache_values,
    _numeric_values,
    _numeric_cache_values,
    _first_cache,
    _cache_point_values,
    _element_val,
    _bounds_payload,
    _round_payload_number,
    _local_name,
)

