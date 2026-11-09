# Adapted from PPT Master 5e8856b (MIT); see assets/licenses/ppt_master.txt.
"""Native chart styling and companion text helpers."""

from __future__ import annotations

import math
import re
from typing import Any
from xml.etree import ElementTree as ET

from .marker_attributes import native_import_source, native_json_is_authoritative

from ..drawingml.context import ConvertContext, ShapeResult
from ..drawingml.utils import (
    _xml_escape,
    ctx_x,
    ctx_y,
    detect_text_lang,
    parse_font_family,
    px_to_emu,
    quantize_ooxml_alpha,
    text_has_rtl_characters,
    text_uses_rtl,
)
from .chart_data import _DEFAULT_CHART_COLORS, _category_axis_reversed, _chart_data
from .marker_common import (
    _bool_attr,
    _bounds,
    _chart_bool,
    _clean_hex,
    _compact_key,
    _fallback_concentric_circle_radii,
    _fallback_fill_candidates,
    _fallback_shape_records,
    _fallback_stroke_colors,
    _fallback_text_colors,
    _fallback_text_records,
    _first_present,
    _font_size_hpt,
    _hex_or_none,
    _inferred_chart_background,
    _local_tag,
    _maybe_number,
    _most_common_color,
    _number,
    _normalized_fallback_text,
    _powerpoint_emu,
    _powerpoint_emu_value,
    _style_attr,
    _visible_fallback_texts,
)


_THEME_FONT_TOKEN = re.compile(r"^\+(mn|mj)-(lt|ea|cs)$")


_AXIS_TITLE_KEY_GROUPS = {
    "category": (
        ("category",),
        ("category_axis_title", "categoryAxisTitle"),
    ),
    "value": (
        ("value",),
        ("value_axis_title", "valueAxisTitle"),
    ),
    "x": (
        ("x",),
        ("x_axis_title", "xAxisTitle"),
    ),
    "y": (
        ("y",),
        ("y_axis_title", "yAxisTitle"),
    ),
    "secondary_value": (
        ("secondary_value", "secondaryValue"),
        ("secondary_value_axis_title", "secondaryValueAxisTitle"),
    ),
}


_NUMBER_FORMAT_RE = re.compile(
    r'^(?:"(?P<prefix>[^"]*)")?(#,##|#|0)?(0*)(?:\.(0+))?(%?)(?:"(?P<suffix>[^"]*)")?$'
)
