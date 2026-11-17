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



# Stable public facade; implementation is split by responsibility.
from .chart_style_inference import (
    _chart_style_value,
    _chart_style_color,
    _fallback_text_attr_values,
    _most_common_value,
    _most_common_font_size,
    _record_has_label,
    _fallback_chart_colors,
    _classic_chart_style,
    _chart_text_sizes,
)
from .chart_style_text_xml import (
    _solid_fill_xml,
    _chart_area_sp_pr_xml,
    _chart_line_sp_pr_xml,
    _major_gridlines_xml,
    _font_face_xml,
    _chart_tx_pr_xml,
    _chart_text_entry,
    _chart_title_is_bounded,
    _chart_text_entry_font_size,
    _chart_text_entry_color,
    _chart_text_entry_font_face,
    _alpha_xml,
    _axis_title_xml,
)
from .chart_style_payload import (
    _axis_titles,
    _metadata_text,
    _native_chart_chrome_errors,
    _native_chart_export_payload,
    _legend_candidate_texts,
)
from .chart_style_series_warnings import (
    _native_chart_style_color_warnings,
    _native_chart_point_color_warnings,
    _fallback_data_shapes,
    _series_palette,
    _typed_series,
    _native_chart_line_marker_warnings,
    _native_chart_area_warnings,
    _native_chart_category_order_warnings,
)
from .chart_style_geometry_warnings import (
    _fallback_bar_clusters,
    _median,
    _inferred_bar_gap_width,
    _inferred_bar_overlap,
    _inferred_cross_between,
    _native_chart_radial_warnings,
    _native_chart_tile_color_warnings,
    _chart_projection_text_variants,
)
from .chart_style_text_warnings import (
    _format_number_like_excel,
    _chart_number_formats,
    _chart_projected_texts,
    _native_chart_reverse_text_warnings,
    _native_chart_chrome_warnings,
)
from .chart_style_companions import (
    _text_box_xml,
    _chart_companion_entries,
    _chart_companion_width_only,
    _chart_companion_box,
    _validate_chart_companion_boxes,
    _chart_companion_shapes,
)
