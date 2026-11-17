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

def _apply_series_formatting(
    payload: dict[str, Any],
    plot: ET.Element,
    visual_styles: list[SeriesVisualStyle],
    palette: ColorPalette | None,
) -> None:
    """Project the series paint and marker options the classic writer supports."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .chart_to_svg import (
        C_NS,
        _UnsupportedChart,
        _element_val,
        _normalized_marker_paint,
        _normalized_shape_paint,
        _resolved_marker,
        _resolved_shape_paint,
        _round_payload_number,
    )
    chart_type = payload["type"]
    if chart_type not in {"area", "bar", "column", "line"}:
        return
    for series, item, visual in zip(plot.findall("c:ser", C_NS), payload["series"], visual_styles):
        if chart_type == "area":
            item["fill_opacity"] = visual.fill_opacity
        if chart_type in {"area", "line"} and visual.stroke and visual.stroke_width > 0:
            item["line_width"] = visual.stroke_width
        if chart_type == "line" and series.find("c:marker/c:size", C_NS) is not None:
            item["marker_size"] = _round_payload_number(visual.marker_size / 0.75)

        base_color = visual.fill
        if chart_type == "line":
            base_color = visual.marker_fill or visual.stroke
            if payload.get("line_style") == "lineMarker" and base_color != visual.stroke:
                item["point_colors"] = [base_color] * len(item["values"])
        points = series.findall("c:dPt", C_NS)
        if not points or chart_type == "area":
            continue
        colors = [base_color] * len(item["values"])
        sizes = [visual.marker_size] * len(colors)
        if chart_type == "line" and payload.get("line_style") != "lineMarker":
            colors = [None] * len(colors)
            sizes = [None] * len(colors)
        seen: set[int] = set()
        for point in points:
            raw_index = _element_val(point.find("c:idx", C_NS))
            if raw_index is None or not raw_index.isdigit():
                raise _UnsupportedChart("unsupported-chart-series-style")
            index = int(raw_index)
            if index >= len(colors) or index in seen:
                raise _UnsupportedChart("unsupported-chart-series-style")
            seen.add(index)
            if chart_type == "line":
                marker_node = point.find("c:marker", C_NS)
                marker = (
                    _normalized_marker_paint(marker_node, palette)
                    if palette is not None and not palette.strict
                    else _resolved_marker(marker_node, palette)
                )
                if marker.symbol == "none":
                    colors[index] = None
                    sizes[index] = None
                elif marker.symbol == "circle" or (
                    marker.symbol is None and payload.get("line_style") == "lineMarker"
                ):
                    colors[index] = marker.shape.fill or base_color
                    if marker_node is not None and marker_node.find("c:size", C_NS) is not None:
                        sizes[index] = marker.size
            else:
                shape = point.find("c:spPr", C_NS)
                paint = (
                    _normalized_shape_paint(shape, palette)
                    if palette is not None and not palette.strict
                    else _resolved_shape_paint(shape, palette)
                )
                colors[index] = paint.fill or base_color
        vary_colors = ooxml_bool(_element_val(plot.find("c:varyColors", C_NS)), False)
        series_color = visual.stroke if chart_type == "line" else visual.fill
        if (chart_type == "line" or all(colors)) and (vary_colors or any(color != series_color for color in colors)):
            item["point_colors"] = colors
        else:
            item.pop("point_colors", None)
        if chart_type == "line":
            visible_sizes = {size for size in sizes if size is not None}
            if len(visible_sizes) == 1:
                item["marker_size"] = _round_payload_number(visible_sizes.pop() / 0.75)


def _apply_plot_data_labels(
    payload: dict[str, Any],
    plot: ET.Element,
    *,
    palette: ColorPalette | None = None,
) -> None:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .chart_to_svg import (
        C_NS,
        _UnsupportedChart,
        _data_labels_payload,
    )
    chart_type = str(payload.get("type") or "")
    series_nodes = plot.findall("c:ser", C_NS)
    series_payloads = payload.get("series")
    if not isinstance(series_payloads, list) or len(series_payloads) != len(series_nodes):
        raise _UnsupportedChart("unsupported-chart-series-data-labels")

    for series_node, series_payload in zip(series_nodes, series_payloads):
        dlabels = series_node.find("c:dLbls", C_NS)
        if dlabels is None:
            continue
        if chart_type not in {"area", "bar", "column", "line"}:
            if palette is None or palette.strict:
                raise _UnsupportedChart("unsupported-chart-series-data-labels")
            palette._diagnose(
                "chart-series-data-labels-omitted",
                "Per-series chart data labels are outside the native contract "
                f"for {chart_type} charts",
                "keep the chart data and any supported plot-level labels",
            )
            continue
        point_count = len(series_payload.get("values") or [])
        try:
            series_labels = _data_labels_payload(
                dlabels,
                palette=palette,
                point_count=point_count,
                preserve_source=True,
            )
            if not series_labels:
                continue
            validate_data_label_position(
                series_labels.get("position"),
                chart_type,
                payload.get("grouping"),
            )
        except (_UnsupportedChart, RuntimeError):
            if palette is None or palette.strict:
                raise _UnsupportedChart("unsupported-chart-series-data-labels") from None
            palette._diagnose(
                "chart-series-data-labels-omitted",
                "Source per-series chart data labels could not be normalized",
                "keep the chart data and any supported plot-level labels",
            )
            continue
        series_payload["data_labels"] = series_labels

    data_labels = _data_labels_payload(
        plot.find("c:dLbls", C_NS),
        palette=palette,
    )
    if not data_labels:
        return
    if payload["type"] not in {"area", "bar", "column", "line"}:
        if palette is None or palette.strict:
            raise _UnsupportedChart("unsupported-chart-data-labels")
        palette._diagnose(
            "chart-data-labels-normalized",
            "Source data-label options are outside the native contract for "
            f'{payload["type"]} charts',
            "use the normalized SVG chart label layout",
        )
        return
    try:
        validate_data_label_position(
            data_labels.get("position"),
            payload["type"],
            payload.get("grouping"),
        )
    except RuntimeError:
        raise _UnsupportedChart("unsupported-chart-data-labels") from None
    payload["data_labels"] = data_labels


def _apply_chart_metadata(
    payload: dict[str, Any],
    chart_root: ET.Element,
    plot_area: ET.Element,
    plot: ET.Element,
    *,
    palette: ColorPalette | None = None,
    include_plot_labels: bool = True,
) -> None:
    """Copy visible classic-chart chrome supported by the native schema."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .chart_to_svg import (
        C_NS,
        _UnsupportedChart,
        _apply_chart_formatting,
        _apply_plot_data_labels,
        _canonical_title_entries,
        _chart_text,
        _element_val,
        _source_chart_text_style,
        _styled_chart_title,
    )
    chart = chart_root.find("c:chart", C_NS)
    if chart is None:
        return

    language = _element_val(chart_root.find("c:lang", C_NS))
    if language:
        payload["lang"] = language
    _apply_chart_formatting(payload, chart_root, plot_area, palette)

    title_element = chart.find("c:title", C_NS)
    title_entries = _canonical_title_entries(title_element)
    title = _chart_text(title_element)
    if title:
        if title_entries is not None:
            payload["title"] = title_entries[0]
            if len(title_entries) == 2:
                payload["subtitle"] = title_entries[1]
        else:
            payload["title"] = _styled_chart_title(title_element, palette)

    legend = chart.find("c:legend", C_NS)
    if legend is not None:
        delete = legend.find("c:delete", C_NS)
        if delete is None or not ooxml_bool(delete.attrib.get("val"), True):
            position = _element_val(legend.find("c:legendPos", C_NS)) or "r"
            if position not in {"b", "l", "r", "t"}:
                raise _UnsupportedChart("unsupported-chart-legend-position")
            payload["show_legend"] = True
            payload["legend_position"] = position
            legend_style = _source_chart_text_style(legend.find("c:txPr", C_NS), palette)
            if "font_size" in legend_style:
                payload["legend_font_size"] = legend_style["font_size"]
            if palette is not None and any(
                "font_size" in _source_chart_text_style(entry.find("c:txPr", C_NS), palette)
                for entry in legend.findall("c:legendEntry", C_NS)
            ):
                palette._diagnose(
                    "chart-legend-entry-font-size-omitted",
                    "Legend entry font-size overrides are outside the native chart JSON contract",
                    "keep source entry typography on unchanged or data-only round-trip edits",
                )

    if include_plot_labels:
        _apply_plot_data_labels(payload, plot, palette=palette)

    axis_titles: dict[str, Any] = {}
    category_axis_nodes = (
        plot_area.findall("c:catAx", C_NS)
        + plot_area.findall("c:dateAx", C_NS)
    )
    category_titles = [
        axis.find("c:title", C_NS)
        for axis in category_axis_nodes
        if _chart_text(axis.find("c:title", C_NS))
    ]
    value_titles = [
        axis.find("c:title", C_NS)
        for axis in plot_area.findall("c:valAx", C_NS)
        if _chart_text(axis.find("c:title", C_NS))
    ]
    if payload["type"] == "combo":
        if len({_chart_text(title) for title in category_titles}) > 1:
            raise _UnsupportedChart("unsupported-chart-axis-titles")
        if category_titles:
            axis_titles["category"] = _styled_chart_title(category_titles[0], palette)
        for axis in plot_area.findall("c:valAx", C_NS):
            text = _chart_text(axis.find("c:title", C_NS))
            if not text:
                continue
            position = _element_val(axis.find("c:axPos", C_NS))
            key = "secondary_value" if position == "r" else "value"
            if key in axis_titles:
                raise _UnsupportedChart("unsupported-chart-axis-titles")
            axis_titles[key] = _styled_chart_title(axis.find("c:title", C_NS), palette)
    elif payload["type"] in {"scatter", "bubble"}:
        if category_titles:
            raise _UnsupportedChart("unsupported-chart-axis-titles")
        titled_value_axes = [
            (
                _element_val(axis.find("c:axPos", C_NS)),
                axis.find("c:title", C_NS),
            )
            for axis in plot_area.findall("c:valAx", C_NS)
        ]
        for position, title in titled_value_axes:
            if not _chart_text(title):
                continue
            key = "x" if position in {"b", "t"} else "y"
            if key in axis_titles:
                raise _UnsupportedChart("unsupported-chart-axis-titles")
            axis_titles[key] = _styled_chart_title(title, palette)
    else:
        if len(category_titles) > 1 or len(value_titles) > 1:
            raise _UnsupportedChart("unsupported-chart-axis-titles")
        if category_titles:
            axis_titles["category"] = _styled_chart_title(category_titles[0], palette)
        if value_titles:
            axis_titles["value"] = _styled_chart_title(value_titles[0], palette)
    if axis_titles:
        payload["axis_titles"] = axis_titles


def _styled_chart_title(title: ET.Element, palette: ColorPalette | None) -> str | dict[str, Any]:
    """Keep uniform title typography through the existing text-entry contract."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .chart_to_svg import (
        C_NS,
        _chart_text,
        _source_chart_text_style,
    )
    style = _source_chart_text_style(title.find("c:txPr", C_NS), palette)
    style.update(_source_chart_text_style(title.find("c:tx/c:rich", C_NS), palette))
    style.pop("bold", None)
    text = _chart_text(title)
    return {"text": text, **style} if style else text


def _chart_fill_color(owner: ET.Element | None, palette: ColorPalette | None) -> str | None:
    """Resolve a schema-supported flat fill, including theme color transforms."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .chart_to_svg import (
        C_NS,
    )
    if owner is None:
        return None
    if owner.find("a:noFill", C_NS) is not None:
        return "none"
    color_node = find_color_elem(owner.find("a:solidFill", C_NS))
    if color_node is None:
        return None
    color, alpha = resolve_color(color_node, palette)
    return color if alpha > 0 else "none"


def _apply_chart_formatting(
    payload: dict[str, Any],
    chart_root: ET.Element,
    plot_area: ET.Element,
    palette: ColorPalette | None,
) -> None:
    """Project only formatting with a writer in native-data.md's chart schema."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .chart_to_svg import (
        C_NS,
        _axis_number,
        _chart_fill_color,
        _element_val,
        _local_name,
        _source_chart_text_style,
        _strict_axis_bool,
    )
    style = payload.setdefault("style", {})
    base_text = _source_chart_text_style(chart_root.find("c:txPr", C_NS), palette)
    for source, target in (("font_family", "font_family"), ("font_size", "font_size"),
                           ("color", "text_color")):
        if source in base_text:
            style[target] = base_text[source]
    for owner, key in ((chart_root, "chart_area_fill"), (plot_area, "plot_area_fill")):
        color = _chart_fill_color(owner.find("c:spPr", C_NS), palette)
        if color is not None:
            style[key] = color

    layout = plot_area.find("c:layout/c:manualLayout", C_NS)
    if layout is not None:
        modes = {name: _element_val(layout.find(f"c:{name}", C_NS)) for name in (
            "layoutTarget", "xMode", "yMode", "wMode", "hMode",
        )}
        factors = [_axis_number(layout.find(f"c:{name}", C_NS)) for name in ("x", "y", "w", "h")]
        if (
            modes["layoutTarget"] == "inner"
            and modes["xMode"] == modes["yMode"] == "edge"
            and modes["wMode"] in {None, "factor"} and modes["hMode"] in {None, "factor"}
            and all(value is not None for value in factors)
            and factors[0] >= 0 and factors[1] >= 0 and factors[2] > 0 and factors[3] > 0
            and factors[0] + factors[2] <= 1 and factors[1] + factors[3] <= 1
        ):
            x, y, width, height = factors
            payload["plot_area"] = {
                "x": payload["x"] + x * payload["width"],
                "y": payload["y"] + y * payload["height"],
                "width": width * payload["width"], "height": height * payload["height"],
            }
        elif palette is not None:
            palette._diagnose(
                "chart-plot-layout-normalized",
                "Chart manual layout is outside the absolute inner plot-area contract",
                "keep chart data with automatic plot layout",
            )

    secondary_ids: set[str] = set()
    if payload["type"] == "combo":
        plots = [node for node in plot_area if _local_name(node.tag).endswith("Chart")]
        for plot, config in zip(plots, payload["plots"]):
            if config["axis"] == "secondary":
                secondary_ids.update(node.get("val") for node in plot.findall("c:axId", C_NS))
    for axis in plot_area:
        tag = _local_name(axis.tag)
        if tag not in {"catAx", "dateAx", "valAx"}:
            continue
        position = _element_val(axis.find("c:axPos", C_NS))
        if payload["type"] in {"scatter", "bubble"}:
            role = "x" if position in {"b", "t"} else "y"
        else:
            role = "value" if tag == "valAx" else "category"
            if _element_val(axis.find("c:axId", C_NS)) in secondary_ids:
                role = f"secondary_{role}"
        config = payload.setdefault("axes", {}).setdefault(role, {})
        if position in {"b", "t", "l", "r"}:
            config["position"] = {"b": "bottom", "t": "top", "l": "left", "r": "right"}[position]
        config["visible"] = not _strict_axis_bool(axis.find("c:delete", C_NS), False)
        config["reverse"] = _element_val(axis.find("c:scaling/c:orientation", C_NS)) == "maxMin"
        config["major_gridlines"] = axis.find("c:majorGridlines", C_NS) is not None
        for tag_name, field, aliases in (
            ("tickLblPos", "label_position", {"nextTo": "next_to", "none": "none", "low": "low", "high": "high"}),
            ("majorTickMark", "tick_marks", {value: value for value in ("none", "in", "out", "cross")}),
        ):
            value = _element_val(axis.find(f"c:{tag_name}", C_NS))
            if value in aliases:
                config[field] = aliases[value]
        if role in {"value", "secondary_value"}:
            crossing = _element_val(axis.find("c:crossBetween", C_NS))
            if crossing in {"between", "midCat"}:
                config["cross_between"] = "mid_category" if crossing == "midCat" else crossing
        number_format = axis.find("c:numFmt", C_NS)
        if number_format is not None and number_format.get("formatCode"):
            config["number_format"] = number_format.get("formatCode")
        for path, field in (("scaling/c:min", "minimum"), ("scaling/c:max", "maximum"),
                            ("majorUnit", "major_unit")):
            value = _axis_number(axis.find(f"c:{path}", C_NS))
            if value is not None and (field != "major_unit" or tag == "valAx"):
                config[field] = value
        text_style = _source_chart_text_style(axis.find("c:txPr", C_NS), palette)
        config.update({key: value for key, value in text_style.items() if key != "bold"})
        for path, key in (("c:spPr/a:ln", "axis_color"), ("c:majorGridlines/c:spPr/a:ln", "grid_color")):
            color = _chart_fill_color(axis.find(path, C_NS), palette)
            if color is not None:
                style.setdefault(key, color)


