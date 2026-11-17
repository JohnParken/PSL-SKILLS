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

def _effective_radar_style(plot: ET.Element) -> str:
    """Map Office radar marker variants to the writer's uniform style set."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .chart_to_svg import (
        C_NS,
        _UnsupportedChart,
        _element_val,
    )
    style_nodes = plot.findall("c:radarStyle", C_NS)
    if len(style_nodes) > 1:
        raise _UnsupportedChart("unsupported-chart-radar-style")
    style_node = style_nodes[0] if style_nodes else None
    if style_node is not None and (
        set(style_node.attrib) != {"val"} or list(style_node)
    ):
        raise _UnsupportedChart("unsupported-chart-radar-style")
    style = _element_val(style_node) if style_node is not None else "standard"
    if style == "filled":
        return "filled"
    if style not in {"marker", "standard"}:
        raise _UnsupportedChart("unsupported-chart-radar-style")

    default_marker = style == "marker"
    marker_states: list[bool] = []
    for series in plot.findall("c:ser", C_NS):
        symbol = _element_val(series.find("c:marker/c:symbol", C_NS))
        marker_states.append(
            default_marker if symbol is None else symbol != "none"
        )
    return "lineMarker" if any(marker_states) else "line"


def _effective_scatter_style(
    plot: ET.Element,
    visual_styles: list[SeriesVisualStyle],
) -> str:
    """Normalize plot-level line intent plus uniform series overrides."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .chart_to_svg import (
        C_NS,
        _UnsupportedChart,
        _element_val,
    )
    style_nodes = plot.findall("c:scatterStyle", C_NS)
    if len(style_nodes) > 1:
        raise _UnsupportedChart("unsupported-chart-scatter-style")
    style_node = style_nodes[0] if style_nodes else None
    if style_node is not None and (
        set(style_node.attrib) != {"val"} or list(style_node)
    ):
        raise _UnsupportedChart("unsupported-chart-scatter-style")
    plot_style = _element_val(style_node) or "marker"
    if plot_style not in {"line", "lineMarker", "marker", "smooth", "smoothMarker"}:
        raise _UnsupportedChart("unsupported-chart-scatter-style")
    plot_has_line = plot_style in {"line", "lineMarker", "smooth", "smoothMarker"}
    plot_is_smooth = plot_style in {"smooth", "smoothMarker"}

    style_by_state = {
        (False, True, False): "marker",
        (True, False, False): "line",
        (True, True, False): "lineMarker",
        (True, False, True): "smooth",
        (True, True, True): "smoothMarker",
    }
    effective_styles: set[str] = set()
    series_nodes = plot.findall("c:ser", C_NS)
    if len(series_nodes) != len(visual_styles):
        raise _UnsupportedChart("unsupported-chart-scatter-style")
    for series, visual_style in zip(series_nodes, visual_styles):
        line_node = series.find("c:spPr/a:ln", C_NS)
        has_line = plot_has_line if line_node is None else (
            visual_style.stroke is not None
            and visual_style.stroke_opacity > 0
        )
        markers = series.findall("c:marker", C_NS)
        if len(markers) != 1:
            raise _UnsupportedChart("unsupported-chart-scatter-style")
        marker = markers[0]
        symbol = marker.find("c:symbol", C_NS) if marker is not None else None
        if (
            marker is None
            or symbol is None
            or set(symbol.attrib) != {"val"}
            or list(symbol)
        ):
            raise _UnsupportedChart("unsupported-chart-scatter-style")
        symbol_value = _element_val(symbol)
        if symbol_value not in {"circle", "none"}:
            raise _UnsupportedChart("unsupported-chart-scatter-style")
        has_marker = symbol_value == "circle"

        smooth_nodes = series.findall("c:smooth", C_NS)
        if len(smooth_nodes) > 1:
            raise _UnsupportedChart("unsupported-chart-scatter-style")
        smooth = smooth_nodes[0] if smooth_nodes else None
        is_smooth = plot_is_smooth
        if smooth is not None:
            if not set(smooth.attrib).issubset({"val"}) or list(smooth):
                raise _UnsupportedChart("unsupported-chart-scatter-style")
            raw_smooth = smooth.attrib.get("val")
            key = raw_smooth.strip().lower() if raw_smooth is not None else "true"
            if key in {"1", "on", "true"}:
                is_smooth = True
            elif key in {"0", "false", "off"}:
                is_smooth = False
            else:
                raise _UnsupportedChart("unsupported-chart-scatter-style")
        if is_smooth and not has_line:
            raise _UnsupportedChart("unsupported-chart-scatter-style")

        effective_style = style_by_state.get((has_line, has_marker, is_smooth))
        if effective_style is None:
            raise _UnsupportedChart("unsupported-chart-scatter-style")
        effective_styles.add(effective_style)

    if len(effective_styles) != 1:
        raise _UnsupportedChart("unsupported-chart-scatter-style")
    return effective_styles.pop()


def _bounded_plot_integer(
    plot: ET.Element,
    name: str,
    *,
    minimum: int,
    maximum: int,
    status: str,
) -> int | None:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .chart_to_svg import (
        C_NS,
        _UnsupportedChart,
    )
    nodes = plot.findall(f"c:{name}", C_NS)
    if len(nodes) > 1:
        raise _UnsupportedChart(status)
    if not nodes:
        return None
    node = nodes[0]
    raw_value = node.attrib.get("val")
    if (
        set(node.attrib) != {"val"}
        or list(node)
        or raw_value is None
        or re.fullmatch(r"-?[0-9]+", raw_value) is None
    ):
        raise _UnsupportedChart(status)
    value = int(raw_value)
    if not minimum <= value <= maximum:
        raise _UnsupportedChart(status)
    return value


def _validate_chart_semantics(
    payload: dict[str, Any],
    plot_area: ET.Element,
    plot: ET.Element,
    *,
    palette: ColorPalette | None = None,
    validate_axes: bool = True,
) -> list[SeriesVisualStyle]:
    """Reject valid chart features the compact marker cannot reproduce."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .chart_to_svg import (
        C_NS,
        _UnsupportedChart,
        _apply_series_formatting,
        _bounded_plot_integer,
        _chart_visual_styles,
        _effective_scatter_style,
        _element_val,
        _local_name,
        _normalized_chart_visual_styles,
        _validate_or_normalize_legacy_axes,
    )
    chart_type = payload["type"]
    grouping = payload.get("grouping")
    try:
        visual_styles = _chart_visual_styles(payload, plot, palette)
    except _UnsupportedChart as exc:
        if (
            exc.status != "unsupported-chart-series-style"
            or palette is None
            or palette.strict
        ):
            raise
        palette._diagnose(
            "chart-series-style-normalized",
            "Chart series styling uses DrawingML features outside the native "
            "flat-color chart contract",
            "preserve chart data and use representative flat series colors",
        )
        visual_styles = _normalized_chart_visual_styles(
            payload,
            plot,
            palette,
        )
    for tag in (
        "trendline", "errBars", "dropLines", "hiLowLines", "upDownBars",
    ):
        if plot.find(f".//c:{tag}", C_NS) is not None:
            raise _UnsupportedChart("unsupported-chart-analysis-features")
    if plot_area.find("c:dTable", C_NS) is not None:
        raise _UnsupportedChart("unsupported-chart-data-table")
    ser_line_nodes = plot.findall("c:serLines", C_NS)
    if len(ser_line_nodes) > 1:
        raise _UnsupportedChart("unsupported-chart-analysis-features")
    if ser_line_nodes:
        ser_lines = ser_line_nodes[0]
        if (
            chart_type != "of_pie"
            or ser_lines.attrib
            or len(ser_lines) > 1
            or any(_local_name(child.tag) != "spPr" for child in ser_lines)
        ):
            raise _UnsupportedChart("unsupported-chart-analysis-features")
    if validate_axes:
        _validate_or_normalize_legacy_axes(payload, plot_area, plot)

    if chart_type == "line":
        smooth_nodes = [plot.find("c:smooth", C_NS), *plot.findall("c:ser/c:smooth", C_NS)]
        if any(
            node is not None and ooxml_bool(node.attrib.get("val"), True)
            for node in smooth_nodes
        ):
            raise _UnsupportedChart("unsupported-chart-line-style")
        plot_marker = plot.find("c:marker", C_NS)
        plot_has_markers = bool(
            plot_marker is not None
            and ooxml_bool(plot_marker.attrib.get("val"), True)
        )
        marker_states: set[bool] = set()
        for series in plot.findall("c:ser", C_NS):
            marker_node = series.find("c:marker", C_NS)
            symbol = _element_val(series.find("c:marker/c:symbol", C_NS))
            if symbol not in {None, "circle", "none"}:
                raise _UnsupportedChart("unsupported-chart-line-style")
            marker_states.add(plot_has_markers if symbol is None else symbol != "none")
        if len(marker_states) > 1:
            raise _UnsupportedChart("unsupported-chart-line-style")

    if chart_type in {"bar", "column"}:
        gap_width = _bounded_plot_integer(
            plot,
            "gapWidth",
            minimum=0,
            maximum=500,
            status="unsupported-chart-bar-options",
        )
        overlap = _bounded_plot_integer(
            plot,
            "overlap",
            minimum=-100,
            maximum=100,
            status="unsupported-chart-bar-options",
        )
        if gap_width is not None:
            payload["gap_width"] = gap_width
        if overlap is not None:
            payload["overlap"] = overlap

    if chart_type == "bubble":
        bubble_scale = _element_val(plot.find("c:bubbleScale", C_NS))
        if bubble_scale not in {None, "100"}:
            raise _UnsupportedChart("unsupported-chart-bubble-options")
        show_negative = plot.find("c:showNegBubbles", C_NS)
        if show_negative is not None and ooxml_bool(
            show_negative.attrib.get("val"),
            True,
        ):
            raise _UnsupportedChart("unsupported-chart-bubble-options")
        size_represents = _element_val(plot.find("c:sizeRepresents", C_NS))
        if size_represents not in {None, "area"}:
            raise _UnsupportedChart("unsupported-chart-bubble-options")
        bubble_3d_nodes = [
            plot.find("c:bubble3D", C_NS),
            *plot.findall("c:ser/c:bubble3D", C_NS),
        ]
        if any(
            node is not None and ooxml_bool(node.attrib.get("val"), True)
            for node in bubble_3d_nodes
        ):
            raise _UnsupportedChart("unsupported-chart-bubble-options")

    if chart_type == "scatter":
        payload["scatter_style"] = _effective_scatter_style(plot, visual_styles)

    if chart_type in {"pie", "doughnut", "of_pie"}:
        for explosion in plot.findall(".//c:explosion", C_NS):
            if _element_val(explosion) not in {None, "0"}:
                raise _UnsupportedChart("unsupported-chart-pie-options")
        first_slice = _element_val(plot.find("c:firstSliceAng", C_NS))
        if first_slice not in {None, "0"}:
            raise _UnsupportedChart("unsupported-chart-pie-options")
    if chart_type == "doughnut":
        raw_hole_size = _element_val(plot.find("c:holeSize", C_NS))
        if raw_hole_size is None or not raw_hole_size.isdigit():
            raise _UnsupportedChart("unsupported-chart-doughnut-options")
        hole_size = int(raw_hole_size)
        if not 10 <= hole_size <= 90:
            raise _UnsupportedChart("unsupported-chart-doughnut-options")
        payload["hole_size"] = hole_size
    if chart_type == "of_pie":
        for tag in ("splitType", "splitPos", "custSplit"):
            if plot.find(f"c:{tag}", C_NS) is not None:
                raise _UnsupportedChart("unsupported-chart-of-pie-options")
        gap_width = _element_val(plot.find("c:gapWidth", C_NS))
        if gap_width != "100":
            raise _UnsupportedChart("unsupported-chart-of-pie-options")
        second_size = _element_val(plot.find("c:secondPieSize", C_NS))
        if second_size not in {None, "75"}:
            raise _UnsupportedChart("unsupported-chart-of-pie-options")
    _apply_series_formatting(payload, plot, visual_styles, palette)
    return visual_styles


