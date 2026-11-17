"""Deterministic normalized SVG fallback for parsed classic charts.

The renderer is intentionally independent from the native-chart OOXML
emitter.  It visualizes only data and semantics that ``chart_to_svg`` has
already accepted, so a rendering failure can never invalidate an otherwise
valid editable-chart payload.
"""

from __future__ import annotations

import html
import math
import re
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Any


_DEFAULT_COLORS = (
    "#4472C4",
    "#ED7D31",
    "#A5A5A5",
    "#FFC000",
    "#5B9BD5",
    "#70AD47",
    "#264478",
    "#9E480E",
)


@dataclass(frozen=True)
class SeriesVisualStyle:
    """Resolved paint used only by the normalized SVG fallback."""

    fill: str | None
    fill_opacity: float = 1.0
    stroke: str | None = None
    stroke_opacity: float = 1.0
    stroke_width: float = 1.5
    line_cap: str = "round"
    marker_fill: str | None = None
    marker_fill_opacity: float = 1.0
    marker_stroke: str | None = None
    marker_stroke_opacity: float = 1.0
    marker_stroke_width: float = 1.0
    marker_size: float = 5.0


@dataclass(frozen=True)
class _Rect:
    x: float
    y: float
    w: float
    h: float



# Stable upstream API facade, split by conversion responsibility.
from .normalized_chart_svg_layout import (
    render_normalized_chart_svg,
    _outer_layout,
    _render_category,
    _render_combo,
    _category_labels,
    _excel_1900_date_parts,
    _category_segments,
)
from .normalized_chart_svg_plots import (
    _render_bars,
    _render_lines_or_areas,
    _render_pie,
    _render_xy,
)
from .normalized_chart_svg_axes import (
    _vertical_grid_and_ticks,
    _horizontal_grid_and_ticks,
    _column_category_labels,
    _category_point_x,
    _bar_category_labels,
    _xy_grid_and_ticks,
    _axis_titles,
    _xy_axis_titles,
    _draw_axis_titles,
    _legend_entries,
    _render_legend,
)
from .normalized_chart_svg_labels import (
    _data_label,
    _point_data_labels,
    _label_font_size,
    _format_data_label_value,
    _nice_scale,
    _sector_path,
    _full_ring_path,
    _complete_styles,
    _entry_text,
    _entry_value,
    _entry_color,
    _font_size,
    _text,
    _map,
    _points,
    _format_number,
    _format_percent,
    _contrast_color,
    _fmt,
)

