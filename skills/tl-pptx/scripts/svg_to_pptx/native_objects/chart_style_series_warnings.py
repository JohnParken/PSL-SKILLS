"""Native chart styling and companion text helpers."""

from __future__ import annotations

from typing import Any
from xml.etree import ElementTree as ET


from .chart_data import (
    _DEFAULT_CHART_COLORS,
    _category_axis_reversed,
)
from .marker_common import (
    _clean_hex,
    _fallback_shape_records,
    _fallback_text_records,
    _first_present,
    _hex_or_none,
    _normalized_fallback_text,
)



# Adapted from chart_style.py; upstream MIT, Hugo He, 2025-2026.
def _native_chart_style_color_warnings(
    elem: ET.Element,
    payload: dict[str, Any],
) -> list[str]:
    # Resolve shared symbols at call time; retain the public module contract.
    from .chart_style import (
        _chart_style_value,
        _fallback_chart_colors,
    )
    inferred = _fallback_chart_colors(elem)
    fields = {
        "text_color": (
            "text_color", "textColor", "label_color", "labelColor",
            "font_color", "fontColor",
        ),
        "axis_color": (
            "axis_color", "axisColor", "axis_line_color", "axisLineColor",
        ),
        "grid_color": (
            "grid_color", "gridColor", "gridline_color", "gridlineColor",
        ),
    }
    warnings: list[str] = []
    for field_name, aliases in fields.items():
        raw = _chart_style_value(payload, *aliases)
        if raw is None:
            continue
        payload_color = _hex_or_none(raw)
        fallback_color = inferred[field_name]
        if (
            payload_color is None
            or fallback_color is None
            or payload_color == fallback_color
        ):
            continue
        message = (
            f"Native PPTX chart style.{field_name} #{payload_color} differs "
            f"from fallback dominant {field_name} #{fallback_color}; set "
            f"style.{field_name} to #{fallback_color} or repaint the fallback "
            f"so #{payload_color} is its dominant {field_name}"
        )
        label_needle = {"axis_color": "axis", "grid_color": "grid"}.get(field_name)
        if label_needle and not inferred.get(f"{label_needle}_labeled"):
            message += (
                f" (no stroke carries an id or class naming '{label_needle}', so "
                f"the marker's dominant stroke was read instead — label the "
                f"{label_needle} lines to read their own color)"
            )
        warnings.append(message)
    return warnings


def _native_chart_point_color_warnings(
    elem: ET.Element,
    payload: dict[str, Any],
    chart_data: dict[str, Any],
) -> list[str]:
    # Resolve shared symbols at call time; retain the public module contract.
    from .chart_style import (
        _record_has_label,
    )
    if chart_data.get("kind") != "category" or chart_data.get("type") not in {
        "bar", "column",
    }:
        return []
    series = chart_data.get("series")
    if not isinstance(series, list) or not series:
        return []
    style = payload.get("style") if isinstance(payload.get("style"), dict) else {}
    raw_colors = _first_present(style.get("colors"), payload.get("colors"))
    palette = (
        [_clean_hex(color, "#4472C4") for color in raw_colors]
        if isinstance(raw_colors, list) and raw_colors
        else list(_DEFAULT_CHART_COLORS)
    )
    series_colors = {
        palette[idx % len(palette)]
        for idx in range(len(series))
    }
    point_colors = {
        color
        for item in series
        if isinstance(item, dict)
        for color in item.get("point_colors", [])
    }
    deviations = sorted({
        record.fill
        for record in _fallback_shape_records(elem)
        if (
            record.tag == "rect"
            and record.fill is not None
            and _record_has_label(record, "bar", "column")
            and not _record_has_label(record, "legend")
            and record.fill not in series_colors
            and record.fill not in point_colors
        )
    })
    return [
        "Native PPTX chart data mark fill "
        f"#{color} deviates from its series color, but series point_colors is absent"
        for color in deviations
    ]


def _fallback_data_shapes(elem: ET.Element) -> list[Any]:
    """Painted fallback geometry that is not axis, grid, or legend chrome."""
    # Resolve shared symbols at call time; retain the public module contract.
    from .chart_style import (
        _record_has_label,
    )
    return [
        record
        for record in _fallback_shape_records(elem)
        if not _record_has_label(record, "axis", "grid", "legend")
    ]


def _series_palette(payload: dict[str, Any], series_count: int) -> set[str]:
    style = payload.get("style") if isinstance(payload.get("style"), dict) else {}
    raw_colors = _first_present(style.get("colors"), payload.get("colors"))
    palette = (
        [_clean_hex(color, "#4472C4") for color in raw_colors]
        if isinstance(raw_colors, list) and raw_colors
        else list(_DEFAULT_CHART_COLORS)
    )
    return {palette[idx % len(palette)] for idx in range(series_count)}


def _typed_series(chart_data: dict[str, Any], chart_type: str) -> list[dict[str, Any]]:
    """Series of one plot type from a category chart or the matching combo plots."""
    if chart_data.get("kind") == "category" and chart_data.get("type") == chart_type:
        return list(chart_data.get("series") or [])
    if chart_data.get("kind") == "combo":
        return [
            item
            for plot in chart_data.get("plots") or []
            if plot.get("type") == chart_type
            for item in plot.get("series") or []
        ]
    return []


def _native_chart_line_marker_warnings(
    elem: ET.Element,
    payload: dict[str, Any],
    chart_data: dict[str, Any],
) -> list[str]:
    # Resolve shared symbols at call time; retain the public module contract.
    line_series = _typed_series(chart_data, "line")
    if not line_series:
        return []
    dots = [
        record
        for record in _fallback_data_shapes(elem)
        if record.tag in {"circle", "ellipse"} and record.fill is not None
    ]
    if not dots:
        return []
    if chart_data.get("kind") == "category":
        marker_styles = [chart_data.get("line_style")]
    else:
        marker_styles = [
            plot.get("line_style")
            for plot in chart_data.get("plots") or []
            if plot.get("type") == "line"
        ]
    has_markers = "lineMarker" in marker_styles or any(
        item.get("point_colors") for item in line_series
    )
    warnings: list[str] = []
    if not has_markers:
        warnings.append(
            f"Native PPTX line chart fallback draws {len(dots)} point marker(s) "
            "but the payload has none; set chart-root line_style \"lineMarker\" "
            "(combo: per plot; marker_size in px) for every point, or series "
            "point_colors with a colour per marked point "
            "and null elsewhere"
        )
    series_count = (
        len(chart_data.get("series") or [])
        if chart_data.get("kind") == "category"
        else sum(len(plot.get("series") or []) for plot in chart_data.get("plots") or [])
    )
    known = _series_palette(payload, series_count) | {
        color
        for item in line_series
        for color in item.get("point_colors", [])
        if color is not None
    }
    deviations = sorted({
        record.fill for record in dots if record.fill not in known
    })
    warnings.extend(
        f"Native PPTX line marker fill #{color} deviates from its series color, "
        "but series point_colors is absent"
        for color in deviations
    )
    return warnings


def _native_chart_area_warnings(
    elem: ET.Element,
    payload: dict[str, Any],
    chart_data: dict[str, Any],
) -> list[str]:
    # Resolve shared symbols at call time; retain the public module contract.
    area_series = _typed_series(chart_data, "area")
    if not area_series:
        return []
    shapes = _fallback_data_shapes(elem)
    regions = [
        record
        for record in shapes
        if record.tag in {"path", "polygon"} and record.fill is not None
    ]
    warnings: list[str] = []
    translucent = sorted({
        record.fill_opacity
        for record in regions
        if record.fill_opacity is not None and record.fill_opacity < 1
    })
    if translucent and not any(item.get("fill_opacity") is not None for item in area_series):
        sample = ", ".join(f"{value:g}" for value in translucent[:3])
        warnings.append(
            f"Native PPTX area chart fallback fills are translucent (fill-opacity {sample}) "
            "but no area series sets fill_opacity; native areas export opaque"
        )
    if chart_data.get("kind") != "category":
        return warnings
    strokes = [
        record
        for record in shapes
        if record.tag in {"path", "polyline"} and record.fill is None and record.stroke is not None
    ]
    dots = [
        record
        for record in shapes
        if record.tag in {"circle", "ellipse"} and record.fill is not None
    ]
    outlined = any(item.get("line_width") is not None for item in area_series)
    if (strokes and not outlined) or dots:
        warnings.append(
            "Native PPTX area chart fallback draws a line"
            + (" and point markers" if dots else "")
            + " over the filled region, but an area series exports as a fill without "
            "either; give the series line_width for a same-colour outline, or use type "
            "\"combo\" with an area plot (fill_opacity) under a line plot (line_width, "
            "line_style \"lineMarker\")"
        )
    return warnings


def _native_chart_category_order_warnings(
    elem: ET.Element,
    payload: dict[str, Any],
    chart_data: dict[str, Any],
) -> list[str]:
    # Resolve shared symbols at call time; retain the public module contract.
    from .chart_style import (
        _record_has_label,
    )
    chart_type = chart_data.get("type")
    if chart_data.get("kind") != "category" or chart_type not in {"bar", "column"}:
        return []
    categories = [str(item) for item in chart_data.get("categories") or []]
    if len(categories) < 2:
        return []
    first_text = _normalized_fallback_text(categories[0])
    last_text = _normalized_fallback_text(categories[-1])
    records = [
        record
        for record in _fallback_text_records(elem)
        if not _record_has_label(record, "legend")
        and record.x is not None
        and record.y is not None
    ]
    firsts = [record for record in records if record.text == first_text]
    lasts = [record for record in records if record.text == last_text]
    if len(firsts) != 1 or len(lasts) != 1 or first_text == last_text:
        return []
    first, last = firsts[0], lasts[0]
    axes = chart_data.get("axes") if isinstance(chart_data.get("axes"), dict) else {}
    category = axes.get("category") if isinstance(axes.get("category"), dict) else {}
    resolved = _category_axis_reversed(category, chart_type)
    if chart_type == "bar":
        expected = first.y < last.y
        drawn = "top-down" if expected else "bottom-up"
    else:
        expected = first.x > last.x
        drawn = "right-to-left" if expected else "left-to-right"
    if expected == resolved:
        return []
    return [
        f"Native PPTX {chart_type} chart fallback lists categories {drawn} "
        f"({categories[0]!r} before {categories[-1]!r}) but the payload resolves "
        f"axes.category.reverse to {str(resolved).lower()}; set axes.category.reverse: "
        f"{str(expected).lower()}"
    ]
