# Adapted from PPT Master 5e8856b (MIT); see assets/licenses/ppt_master.txt.
"""Native chart metadata normalization."""

from __future__ import annotations

import math
from typing import Any

from language_tags import LanguageTagError, normalize_language_tag

from .marker_common import (
    _chart_bool,
    _clean_hex,
    _compact_key,
    _first_present,
    _hex_or_none,
    _number,
    _powerpoint_emu,
    _powerpoint_line_width_emu,
)


_CATEGORY_CHART_TYPES = {
    "area",
    "bar",
    "column",
    "doughnut",
    "line",
    "of_pie",
    "pie",
    "radar",
}
_XY_CHART_TYPES = {"scatter", "bubble"}
_CHARTEX_CHART_TYPES = {
    "box_whisker",
    "funnel",
    "histogram",
    "pareto",
    "sunburst",
    "treemap",
    "waterfall",
}
_DEFERRED_CHART_TYPES = {
    "bullet",
    "gantt",
    "heatmap",
    "map",
}
_UNSUPPORTED_3D_CHART_TYPES = {
    "area3d",
    "bar3d",
    "column3d",
    "line3d",
    "pie3d",
    "surface",
}
_DEFAULT_CHART_COLORS = [
    "4472C4",
    "ED7D31",
    "A5A5A5",
    "FFC000",
    "5B9BD5",
    "70AD47",
    "264478",
    "9E480E",
]

_AXIS_ROLE_DEFAULTS = {
    "category": ("text", "bottom"),
    "secondary_category": ("text", "bottom"),
    "secondary_value": ("value", "right"),
    "value": ("value", "left"),
    "x": ("value", "bottom"),
    "y": ("value", "left"),
}
