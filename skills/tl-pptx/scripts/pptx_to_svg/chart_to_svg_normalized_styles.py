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



# Upstream MIT, Hugo He 2025-2026; algorithms retained during module split.
from .chart_to_svg import (
    _LinePaint,
    _MarkerPaint,
    _ShapePaint,
)

def _representative_gradient_color(
    gradient: ET.Element,
    palette: ColorPalette | None,
) -> tuple[str, float] | None:
    """Collapse a chart gradient to one representative tolerant-mode color."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .chart_to_svg import (
        C_NS,
    )
    resolved: list[tuple[str, float]] = []
    for stop in gradient.findall("a:gsLst/a:gs", C_NS):
        try:
            color, opacity = resolve_color(
                find_color_elem(stop),
                palette,
                strict=False,
            )
        except (TypeError, ValueError, OverflowError):
            continue
        if color is not None:
            resolved.append((color, opacity))
    if not resolved:
        return None
    channels = [
        tuple(int(color[offset:offset + 2], 16) for offset in (1, 3, 5))
        for color, _opacity in resolved
    ]
    averaged = tuple(
        round(sum(channel[index] for channel in channels) / len(channels))
        for index in range(3)
    )
    color = "#" + "".join(f"{value:02X}" for value in averaged)
    opacity = sum(value for _color, value in resolved) / len(resolved)
    return color, max(0.0, min(1.0, opacity))


def _representative_fill(
    container: ET.Element | None,
    palette: ColorPalette | None,
) -> tuple[str | None, float, bool]:
    """Return a flat color approximation for one chart fill container."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .chart_to_svg import (
        C_NS,
        _representative_gradient_color,
    )
    if container is None:
        return None, 1.0, False
    if container.find("a:noFill", C_NS) is not None:
        return None, 1.0, True
    solid = container.find("a:solidFill", C_NS)
    if solid is not None:
        try:
            color, opacity = resolve_color(
                find_color_elem(solid),
                palette,
                strict=False,
            )
        except (TypeError, ValueError, OverflowError):
            color = None
            opacity = 1.0
        if color is not None:
            return color.upper(), max(0.0, min(1.0, opacity)), True
    gradient = container.find("a:gradFill", C_NS)
    if gradient is not None:
        representative = _representative_gradient_color(gradient, palette)
        if representative is not None:
            return representative[0].upper(), representative[1], True
    return None, 1.0, False


def _normalized_line_paint(
    line: ET.Element | None,
    palette: ColorPalette | None,
) -> _LinePaint | None:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .chart_to_svg import (
        _LinePaint,
        _representative_fill,
    )
    if line is None:
        return None
    color, opacity, explicit = _representative_fill(line, palette)
    raw_width = line.attrib.get("w", "")
    width = int(raw_width) / 9525.0 if raw_width.isdigit() else 1.5
    width = max(0.0, min(1000.0, width))
    cap = {
        "rnd": "round",
        "sq": "square",
        "flat": "butt",
    }.get(line.attrib.get("cap"), "round")
    return _LinePaint(
        color,
        opacity,
        width,
        cap,
        not (explicit and color is None),
        not explicit,
    )


def _normalized_shape_paint(
    sp_pr: ET.Element | None,
    palette: ColorPalette | None,
) -> _ShapePaint:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .chart_to_svg import (
        C_NS,
        _ShapePaint,
        _normalized_line_paint,
        _representative_fill,
    )
    fill, opacity, explicit = _representative_fill(sp_pr, palette)
    line = (
        _normalized_line_paint(sp_pr.find("a:ln", C_NS), palette)
        if sp_pr is not None
        else None
    )
    return _ShapePaint(fill, opacity, explicit, line)


def _normalized_marker_paint(
    marker: ET.Element | None,
    palette: ColorPalette | None,
) -> _MarkerPaint:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .chart_to_svg import (
        C_NS,
        _MarkerPaint,
        _element_val,
        _normalized_shape_paint,
    )
    if marker is None:
        return _MarkerPaint(None, 5.0, _normalized_shape_paint(None, palette))
    symbol = _element_val(marker.find("c:symbol", C_NS))
    if symbol not in {None, "circle", "none"}:
        symbol = "circle"
    raw_size = _element_val(marker.find("c:size", C_NS))
    try:
        size = float(raw_size) if raw_size is not None else 5.0
    except (TypeError, ValueError, OverflowError):
        size = 5.0
    return _MarkerPaint(
        symbol,
        max(2.0, min(72.0, size)),
        _normalized_shape_paint(marker.find("c:spPr", C_NS), palette),
    )


def _normalized_chart_visual_styles(
    payload: dict[str, Any],
    plot: ET.Element,
    palette: ColorPalette | None,
) -> list[SeriesVisualStyle]:
    """Keep chart data visible when source-only styling is not portable."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .chart_to_svg import (
        C_NS,
        _ShapePaint,
        _automatic_color,
        _element_val,
        _line_color,
        _normalized_marker_paint,
        _normalized_shape_paint,
        _series_visual_style,
    )
    chart_type = payload["type"]
    series_nodes = plot.findall("c:ser", C_NS)
    styles: list[SeriesVisualStyle] = []
    if chart_type in {"pie", "doughnut", "of_pie"} and series_nodes:
        expected_count = len(payload["categories"])
        if chart_type == "of_pie":
            expected_count += 1
        series = series_nodes[0]
        base_shape = _normalized_shape_paint(
            series.find("c:spPr", C_NS),
            palette,
        )
        points: dict[int, _ShapePaint] = {}
        for point in series.findall("c:dPt", C_NS):
            raw_index = _element_val(point.find("c:idx", C_NS))
            if raw_index is None or not raw_index.isdigit():
                continue
            point_index = int(raw_index)
            if 0 <= point_index < expected_count:
                points[point_index] = _normalized_shape_paint(
                    point.find("c:spPr", C_NS),
                    palette,
                )
        for index in range(expected_count):
            auto = _automatic_color(palette, index)
            shape = points.get(index, base_shape)
            fill = shape.fill if shape.fill_explicit else auto
            line = shape.line
            stroke = _line_color(line, "#FFFFFF")
            styles.append(
                SeriesVisualStyle(
                    fill=fill,
                    fill_opacity=shape.fill_opacity,
                    stroke=stroke,
                    stroke_opacity=line.opacity if line is not None else 1.0,
                    stroke_width=line.width if line is not None else 1.0,
                    line_cap=line.cap if line is not None else "round",
                    marker_fill=fill,
                    marker_stroke=stroke,
                )
            )
    else:
        for index, series in enumerate(series_nodes):
            shape = _normalized_shape_paint(
                series.find("c:spPr", C_NS),
                palette,
            )
            if not shape.fill_explicit:
                first_point = series.find("c:dPt/c:spPr", C_NS)
                point_shape = _normalized_shape_paint(first_point, palette)
                if point_shape.fill_explicit:
                    shape = _ShapePaint(
                        point_shape.fill,
                        point_shape.fill_opacity,
                        True,
                        shape.line,
                    )
            styles.append(
                _series_visual_style(
                    shape,
                    _normalized_marker_paint(
                        series.find("c:marker", C_NS),
                        palette,
                    ),
                    chart_type=chart_type,
                    auto_color=_automatic_color(
                        palette, int(_element_val(series.find("c:idx", C_NS)) or index),
                    ),
                )
            )
    colors = [
        (style.stroke if chart_type == "line" else style.fill)
        or style.fill
        or style.stroke
        or style.marker_fill
        or _automatic_color(palette, index)
        for index, style in enumerate(styles)
    ]
    if colors:
        payload["style"] = {"colors": colors}
    return styles


