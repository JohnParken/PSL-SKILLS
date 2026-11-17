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

def _canonical_srgb_color(fill: ET.Element | None) -> str:
    """Return an exporter-canonical solid RGB fill, or reject the style."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .chart_to_svg import (
        _UnsupportedChart,
        _local_name,
    )
    if fill is None or fill.attrib:
        raise _UnsupportedChart("unsupported-chart-series-style")
    children = list(fill)
    if (
        len(children) != 1
        or _local_name(children[0].tag) != "srgbClr"
        or set(children[0].attrib) != {"val"}
        or list(children[0])
    ):
        raise _UnsupportedChart("unsupported-chart-series-style")
    color = children[0].attrib["val"].strip()
    if len(color) != 6 or any(char not in "0123456789abcdefABCDEF" for char in color):
        raise _UnsupportedChart("unsupported-chart-series-style")
    return color.upper()


def _resolved_solid_fill(
    fill: ET.Element,
    palette: ColorPalette | None,
) -> tuple[str, float]:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .chart_to_svg import (
        _COLOR_MODIFIERS_WITHOUT_VALUE,
        _COLOR_MODIFIERS_WITH_VALUE,
        _UnsupportedChart,
        _local_name,
    )
    if fill.attrib or len(fill) != 1:
        raise _UnsupportedChart("unsupported-chart-series-style")
    color = find_color_elem(fill)
    if color is None or color is not fill[0]:
        raise _UnsupportedChart("unsupported-chart-series-style")
    tag = _local_name(color.tag)
    allowed_attrs = {
        "srgbClr": {"val"},
        "schemeClr": {"val"},
        "sysClr": {"val", "lastClr"},
        "prstClr": {"val"},
        "hslClr": {"hue", "sat", "lum"},
        "scrgbClr": {"r", "g", "b"},
    }.get(tag)
    if allowed_attrs is None or not set(color.attrib).issubset(allowed_attrs):
        raise _UnsupportedChart("unsupported-chart-series-style")
    required = {
        "srgbClr": {"val"}, "schemeClr": {"val"}, "prstClr": {"val"},
        "hslClr": {"hue", "sat", "lum"}, "scrgbClr": {"r", "g", "b"},
    }.get(tag, set())
    if not required.issubset(color.attrib):
        raise _UnsupportedChart("unsupported-chart-series-style")
    # ElementTree elements are iterable; pylint infers Optional incorrectly here.
    for modifier in color:  # pylint: disable=not-an-iterable
        modifier_name = _local_name(modifier.tag)
        if modifier_name in _COLOR_MODIFIERS_WITH_VALUE:
            if set(modifier.attrib) != {"val"} or list(modifier):
                raise _UnsupportedChart("unsupported-chart-series-style")
            try:
                modifier_value = float(modifier.attrib["val"])
            except (TypeError, ValueError, OverflowError):
                raise _UnsupportedChart("unsupported-chart-series-style") from None
            if not math.isfinite(modifier_value):
                raise _UnsupportedChart("unsupported-chart-series-style")
        elif modifier_name in _COLOR_MODIFIERS_WITHOUT_VALUE:
            if modifier.attrib or list(modifier):
                raise _UnsupportedChart("unsupported-chart-series-style")
        else:
            raise _UnsupportedChart("unsupported-chart-series-style")
    try:
        resolved, alpha = resolve_color(color, palette)
    except (TypeError, ValueError, OverflowError):
        raise _UnsupportedChart("unsupported-chart-series-style") from None
    if resolved is None or not math.isfinite(alpha):
        raise _UnsupportedChart("unsupported-chart-series-style")
    return resolved.upper(), max(0.0, min(1.0, alpha))


def _resolved_line(
    line: ET.Element,
    palette: ColorPalette | None,
) -> _LinePaint:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .chart_to_svg import (
        _LinePaint,
        _UnsupportedChart,
        _local_name,
        _resolved_solid_fill,
    )
    if not set(line.attrib).issubset({"w", "cap"}):
        raise _UnsupportedChart("unsupported-chart-series-style")
    raw_width = line.attrib.get("w")
    if raw_width is None:
        width = 1.5
    else:
        if re.fullmatch(r"[0-9]+", raw_width) is None:
            raise _UnsupportedChart("unsupported-chart-series-style")
        width = int(raw_width) / 9525.0
        if not 0 <= width <= 1000:
            raise _UnsupportedChart("unsupported-chart-series-style")
    cap_aliases = {None: "round", "rnd": "round", "sq": "square", "flat": "butt"}
    cap = cap_aliases.get(line.attrib.get("cap"))
    if cap is None:
        raise _UnsupportedChart("unsupported-chart-series-style")

    fill_nodes: list[ET.Element] = []
    for child in line:
        name = _local_name(child.tag)
        if name in {"solidFill", "noFill"}:
            fill_nodes.append(child)
        elif name == "prstDash":
            if child.attrib != {"val": "solid"} or list(child):
                raise _UnsupportedChart("unsupported-chart-series-style")
        elif name in {"round", "bevel"}:
            if child.attrib or list(child):
                raise _UnsupportedChart("unsupported-chart-series-style")
        elif name == "miter":
            if not set(child.attrib).issubset({"lim"}) or list(child):
                raise _UnsupportedChart("unsupported-chart-series-style")
            raw_limit = child.attrib.get("lim")
            if raw_limit is not None and re.fullmatch(r"[0-9]+", raw_limit) is None:
                raise _UnsupportedChart("unsupported-chart-series-style")
        else:
            raise _UnsupportedChart("unsupported-chart-series-style")
    if len(fill_nodes) > 1:
        raise _UnsupportedChart("unsupported-chart-series-style")
    if not fill_nodes:
        return _LinePaint(None, 1.0, width, cap, True, True)
    fill = fill_nodes[0]
    if _local_name(fill.tag) == "noFill":
        if fill.attrib or list(fill):
            raise _UnsupportedChart("unsupported-chart-series-style")
        return _LinePaint(None, 1.0, width, cap, False, False)
    color, opacity = _resolved_solid_fill(fill, palette)
    return _LinePaint(color, opacity, width, cap, True, False)


def _resolved_shape_paint(
    sp_pr: ET.Element | None,
    palette: ColorPalette | None,
) -> _ShapePaint:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .chart_to_svg import (
        _ShapePaint,
        _UnsupportedChart,
        _local_name,
        _resolved_line,
        _resolved_solid_fill,
    )
    if sp_pr is None:
        return _ShapePaint(None, 1.0, False, None)
    if sp_pr.attrib:
        raise _UnsupportedChart("unsupported-chart-series-style")
    fill_nodes: list[ET.Element] = []
    line_nodes: list[ET.Element] = []
    for child in sp_pr:
        name = _local_name(child.tag)
        if name in {"solidFill", "noFill"}:
            fill_nodes.append(child)
        elif name == "ln":
            line_nodes.append(child)
        elif name == "effectLst":
            if child.attrib or list(child):
                raise _UnsupportedChart("unsupported-chart-series-style")
        else:
            raise _UnsupportedChart("unsupported-chart-series-style")
    if len(fill_nodes) > 1 or len(line_nodes) > 1:
        raise _UnsupportedChart("unsupported-chart-series-style")
    fill_color: str | None = None
    fill_opacity = 1.0
    fill_explicit = bool(fill_nodes)
    if fill_nodes:
        fill = fill_nodes[0]
        if _local_name(fill.tag) == "noFill":
            if fill.attrib or list(fill):
                raise _UnsupportedChart("unsupported-chart-series-style")
        else:
            fill_color, fill_opacity = _resolved_solid_fill(fill, palette)
    line = _resolved_line(line_nodes[0], palette) if line_nodes else None
    return _ShapePaint(fill_color, fill_opacity, fill_explicit, line)


def _resolved_marker(
    marker: ET.Element | None,
    palette: ColorPalette | None,
) -> _MarkerPaint:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .chart_to_svg import (
        C_NS,
        _MarkerPaint,
        _UnsupportedChart,
        _element_val,
        _local_name,
        _resolved_shape_paint,
    )
    if marker is None:
        return _MarkerPaint(None, 5.0, _resolved_shape_paint(None, palette))
    if marker.attrib:
        raise _UnsupportedChart("unsupported-chart-series-style")
    allowed = {"symbol", "size", "spPr"}
    names = [_local_name(child.tag) for child in marker]
    if any(name not in allowed for name in names) or len(names) != len(set(names)):
        raise _UnsupportedChart("unsupported-chart-series-style")
    symbol_node = marker.find("c:symbol", C_NS)
    symbol = _element_val(symbol_node)
    if symbol_node is not None and (
        set(symbol_node.attrib) != {"val"} or list(symbol_node)
    ):
        raise _UnsupportedChart("unsupported-chart-series-style")
    if symbol not in {None, "circle", "none"}:
        raise _UnsupportedChart("unsupported-chart-series-style")
    size_node = marker.find("c:size", C_NS)
    size = 5.0
    if size_node is not None:
        raw_size = size_node.attrib.get("val", "")
        if set(size_node.attrib) != {"val"} or list(size_node) or re.fullmatch(r"[0-9]+", raw_size) is None:
            raise _UnsupportedChart("unsupported-chart-series-style")
        size = float(raw_size)
        if not 2 <= size <= 72:
            raise _UnsupportedChart("unsupported-chart-series-style")
    return _MarkerPaint(
        symbol,
        size,
        _resolved_shape_paint(marker.find("c:spPr", C_NS), palette),
    )


def _automatic_color(palette: ColorPalette | None, index: int) -> str:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .chart_to_svg import (
        _FALLBACK_CHART_COLORS,
    )
    if palette is not None:
        resolved = palette.resolve_scheme(f"accent{index % 6 + 1}")
        if resolved:
            return f"#{resolved.upper()}"
    return _FALLBACK_CHART_COLORS[index % len(_FALLBACK_CHART_COLORS)]


def _line_color(line: _LinePaint | None, default: str | None) -> str | None:
    if line is None:
        return default
    if not line.visible:
        return None
    return default if line.automatic else line.color


def _series_visual_style(
    shape: _ShapePaint,
    marker: _MarkerPaint,
    *,
    chart_type: str,
    auto_color: str,
) -> SeriesVisualStyle:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .chart_to_svg import (
        _line_color,
    )
    fill_default = auto_color if chart_type in {"area", "bar", "bubble", "column"} else None
    fill = shape.fill if shape.fill_explicit else fill_default
    if chart_type in {"line", "scatter"}:
        line_default = auto_color
    elif chart_type == "area":
        line_default = auto_color
    else:
        line_default = None
    stroke = _line_color(shape.line, line_default)
    marker_default = (stroke or auto_color) if chart_type in {"line", "scatter"} else auto_color
    marker_fill = marker.shape.fill if marker.shape.fill_explicit else marker_default
    marker_stroke = _line_color(marker.shape.line, marker_default)
    line = shape.line
    marker_line = marker.shape.line
    return SeriesVisualStyle(
        fill=fill,
        fill_opacity=shape.fill_opacity,
        stroke=stroke,
        stroke_opacity=line.opacity if line is not None else 1.0,
        stroke_width=line.width if line is not None else 1.5,
        line_cap=line.cap if line is not None else "round",
        marker_fill=marker_fill,
        marker_fill_opacity=marker.shape.fill_opacity,
        marker_stroke=marker_stroke,
        marker_stroke_opacity=marker_line.opacity if marker_line is not None else 1.0,
        marker_stroke_width=marker_line.width if marker_line is not None else 1.0,
        marker_size=marker.size,
    )


def _pie_visual_styles(
    payload: dict[str, Any],
    series: ET.Element,
    palette: ColorPalette | None,
) -> list[SeriesVisualStyle]:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .chart_to_svg import (
        C_NS,
        _UnsupportedChart,
        _automatic_color,
        _line_color,
        _local_name,
        _resolved_shape_paint,
    )
    chart_type = payload["type"]
    expected_count = len(payload["categories"]) + (1 if chart_type == "of_pie" else 0)
    base_shape = _resolved_shape_paint(series.find("c:spPr", C_NS), palette)
    point_nodes = series.findall("c:dPt", C_NS)
    points: dict[int, _ShapePaint] = {}
    for point in point_nodes:
        if point.attrib:
            raise _UnsupportedChart("unsupported-chart-series-style")
        allowed = {"idx", "bubble3D", "explosion", "spPr"}
        names = [_local_name(child.tag) for child in point]
        if any(name not in allowed for name in names) or len(names) != len(set(names)):
            raise _UnsupportedChart("unsupported-chart-series-style")
        idx = point.find("c:idx", C_NS)
        if idx is None or set(idx.attrib) != {"val"} or list(idx):
            raise _UnsupportedChart("unsupported-chart-series-style")
        try:
            point_index = int(idx.attrib["val"])
        except ValueError:
            raise _UnsupportedChart("unsupported-chart-series-style") from None
        bubble_3d = point.find("c:bubble3D", C_NS)
        if bubble_3d is not None and (
            not set(bubble_3d.attrib).issubset({"val"})
            or list(bubble_3d)
            or ooxml_bool(bubble_3d.attrib.get("val"), True)
        ):
            raise _UnsupportedChart("unsupported-chart-series-style")
        if point_index < 0 or point_index >= expected_count or point_index in points:
            raise _UnsupportedChart("unsupported-chart-series-style")
        points[point_index] = _resolved_shape_paint(point.find("c:spPr", C_NS), palette)
    if points and set(points) != set(range(expected_count)):
        raise _UnsupportedChart("unsupported-chart-series-style")

    styles: list[SeriesVisualStyle] = []
    for index in range(expected_count):
        auto = _automatic_color(palette, index)
        point_shape = points.get(index, base_shape)
        fill = point_shape.fill if point_shape.fill_explicit else auto
        stroke = _line_color(point_shape.line, "#FFFFFF")
        line = point_shape.line
        styles.append(
            SeriesVisualStyle(
                fill=fill,
                fill_opacity=point_shape.fill_opacity,
                stroke=stroke,
                stroke_opacity=line.opacity if line is not None else 1.0,
                stroke_width=line.width if line is not None else 1.0,
                line_cap=line.cap if line is not None else "round",
                marker_fill=fill,
                marker_stroke=stroke,
            )
        )
    return styles


def _chart_visual_styles(
    payload: dict[str, Any],
    plot: ET.Element,
    palette: ColorPalette | None,
) -> list[SeriesVisualStyle]:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .chart_to_svg import (
        C_NS,
        _UnsupportedChart,
        _automatic_color,
        _element_val,
        _pie_visual_styles,
        _resolved_marker,
        _resolved_shape_paint,
        _series_visual_style,
    )
    chart_type = payload["type"]
    series_nodes = plot.findall("c:ser", C_NS)
    if chart_type in {"pie", "doughnut", "of_pie"}:
        if len(series_nodes) != 1:
            raise _UnsupportedChart("unsupported-chart-series-style")
        styles = _pie_visual_styles(payload, series_nodes[0], palette)
    else:
        if chart_type not in {"bar", "column", "line"} and any(
            series.find("c:dPt", C_NS) is not None for series in series_nodes
        ):
            raise _UnsupportedChart("unsupported-chart-series-style")
        styles = []
        for index, series in enumerate(series_nodes):
            source_index = _element_val(series.find("c:idx", C_NS))
            auto = _automatic_color(palette, int(source_index) if source_index and source_index.isdigit() else index)
            shape = _resolved_shape_paint(series.find("c:spPr", C_NS), palette)
            marker = _resolved_marker(series.find("c:marker", C_NS), palette)
            styles.append(
                _series_visual_style(
                    shape,
                    marker,
                    chart_type=chart_type,
                    auto_color=auto,
                )
            )
    colors = [
        (style.stroke if chart_type == "line" else style.fill)
        or style.fill or style.stroke or style.marker_fill or _automatic_color(palette, index)
        for index, style in enumerate(styles)
    ]
    if colors:
        payload["style"] = {"colors": colors}
    return styles


