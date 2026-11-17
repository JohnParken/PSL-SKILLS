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

def _category_payload(
    chart: ET.Element,
    chart_type: str,
    xfrm: Xfrm,
    *,
    category_kind: str = "text",
) -> dict[str, Any]:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .chart_to_svg import (
        C_NS,
        _UnsupportedChart,
        _bounds_payload,
        _category_values,
        _element_val,
        _line_style,
        _numeric_values,
        _series_name,
    )
    series_nodes = chart.findall("c:ser", C_NS)
    if not series_nodes:
        raise _UnsupportedChart("unsupported-chart-cache")

    category_reader = (
        _numeric_values
        if category_kind in {"date", "numeric"}
        else _category_values
    )
    categories = category_reader(series_nodes[0].find("c:cat", C_NS))
    if not categories:
        raise _UnsupportedChart("unsupported-chart-cache")

    series: list[dict[str, Any]] = []
    for idx, ser in enumerate(series_nodes, start=1):
        if category_reader(ser.find("c:cat", C_NS)) != categories:
            raise _UnsupportedChart("unsupported-chart-cache")
        values = _numeric_values(ser.find("c:val", C_NS))
        if not values or len(values) != len(categories):
            raise _UnsupportedChart("unsupported-chart-cache")
        series.append({
            "name": _series_name(ser, idx),
            "values": values,
        })

    payload: dict[str, Any] = {
        **_bounds_payload(xfrm),
        "categories": categories,
        "series": series,
        "type": chart_type,
    }
    grouping = _element_val(chart.find("c:grouping", C_NS))
    if grouping and chart_type in {"area", "bar", "column", "line"}:
        payload["grouping"] = grouping
    if chart_type == "line":
        payload["line_style"] = _line_style(chart, series_nodes)
    if chart_type == "of_pie":
        payload["of_pie_type"] = _element_val(chart.find("c:ofPieType", C_NS)) or "pie"
    return payload


def _xy_payload(chart: ET.Element, chart_type: str, xfrm: Xfrm) -> dict[str, Any]:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .chart_to_svg import (
        C_NS,
        _UnsupportedChart,
        _bounds_payload,
        _numeric_values,
        _series_name,
    )
    series_nodes = chart.findall("c:ser", C_NS)
    if not series_nodes:
        raise _UnsupportedChart("unsupported-chart-cache")

    series: list[dict[str, Any]] = []
    for idx, ser in enumerate(series_nodes, start=1):
        x_values = _numeric_values(ser.find("c:xVal", C_NS))
        y_values = _numeric_values(ser.find("c:yVal", C_NS))
        if not x_values or len(x_values) != len(y_values):
            raise _UnsupportedChart("unsupported-chart-cache")
        item: dict[str, Any] = {
            "name": _series_name(ser, idx),
            "x": x_values,
            "y": y_values,
        }
        if chart_type == "bubble":
            sizes = _numeric_values(ser.find("c:bubbleSize", C_NS))
            if len(sizes) != len(x_values):
                raise _UnsupportedChart("unsupported-chart-cache")
            item["sizes"] = sizes
        series.append(item)

    payload: dict[str, Any] = {
        **_bounds_payload(xfrm),
        "series": series,
        "type": chart_type,
    }
    return payload


def _bar_chart_type(chart: ET.Element) -> str:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .chart_to_svg import (
        C_NS,
        _element_val,
    )
    return "bar" if _element_val(chart.find("c:barDir", C_NS)) == "bar" else "column"


def _line_style(chart: ET.Element, series_nodes: list[ET.Element]) -> str:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .chart_to_svg import (
        C_NS,
        _element_val,
    )
    symbols = [
        _element_val(ser.find("c:marker/c:symbol", C_NS))
        for ser in series_nodes
    ]
    if symbols and all(symbol == "none" for symbol in symbols):
        return "line"
    if any(symbol not in {None, "none"} for symbol in symbols):
        return "lineMarker"
    marker = chart.find("c:marker", C_NS)
    if marker is not None and ooxml_bool(marker.attrib.get("val"), True):
        return "lineMarker"
    return "line"


def _category_values(cat: ET.Element | None) -> list[str]:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .chart_to_svg import (
        C_NS,
        _UnsupportedChart,
        _cache_point_values,
        _first_cache,
        _numeric_cache_values,
    )
    cache = _first_cache(cat, ("strCache", "strLit"))
    if cache is not None:
        return [str(value) if value is not None else "" for value in _cache_point_values(cache)]
    cache = _first_cache(cat, ("numCache", "numLit"))
    if cache is None:
        return []
    format_code = cache.findtext("c:formatCode", default="", namespaces=C_NS).strip()
    if format_code and format_code.lower() != "general":
        raise _UnsupportedChart("unsupported-formatted-category-cache")
    numbers = _numeric_cache_values(cache)
    return [str(value) if value is not None else "" for value in numbers]


def _category_cache_is_numeric(chart: ET.Element) -> bool:
    """Return the cache representation shared by every series in one plot."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .chart_to_svg import (
        C_NS,
        _UnsupportedChart,
        _first_cache,
    )
    result: bool | None = None
    for series in chart.findall("c:ser", C_NS):
        category = series.find("c:cat", C_NS)
        has_text = _first_cache(category, ("strCache", "strLit")) is not None
        has_number = _first_cache(category, ("numCache", "numLit")) is not None
        if has_text == has_number:
            raise _UnsupportedChart("unsupported-combo-category-layout")
        current = has_number
        if result is not None and current != result:
            raise _UnsupportedChart("unsupported-combo-category-layout")
        result = current
    if result is None:
        raise _UnsupportedChart("unsupported-combo-category-layout")
    return result


def _numeric_category_cache_format(chart: ET.Element) -> str:
    """Return one exact numeric category-cache format shared by the plot."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .chart_to_svg import (
        C_NS,
        _UnsupportedChart,
        _first_cache,
    )
    formats: set[str] = set()
    for series in chart.findall("c:ser", C_NS):
        cache = _first_cache(
            series.find("c:cat", C_NS),
            ("numCache", "numLit"),
        )
        if cache is None:
            raise _UnsupportedChart("unsupported-chart-category-format")
        number_format = cache.findtext(
            "c:formatCode",
            default="",
            namespaces=C_NS,
        )
        if not number_format.strip():
            raise _UnsupportedChart("unsupported-chart-category-format")
        formats.add(number_format)
    if len(formats) != 1:
        raise _UnsupportedChart("unsupported-chart-category-format")
    return next(iter(formats))


def _series_name(ser: ET.Element, index: int) -> str:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .chart_to_svg import (
        C_NS,
        _text_cache_values,
    )
    tx = ser.find("c:tx", C_NS)
    values = _text_cache_values(tx)
    if values:
        return values[0]
    direct = tx.findtext("c:v", default="", namespaces=C_NS) if tx is not None else ""
    return direct or f"Series {index}"


def _text_cache_values(parent: ET.Element | None) -> list[str]:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .chart_to_svg import (
        _cache_point_values,
        _first_cache,
    )
    cache = _first_cache(parent, ("strCache", "strLit"))
    if cache is not None:
        return [str(value) if value is not None else "" for value in _cache_point_values(cache)]
    cache = _first_cache(parent, ("numCache", "numLit"))
    return [str(value) if value is not None else "" for value in _cache_point_values(cache)]


def _numeric_values(parent: ET.Element | None) -> list[int | float | None]:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .chart_to_svg import (
        _first_cache,
        _numeric_cache_values,
    )
    cache = _first_cache(parent, ("numCache", "numLit"))
    if cache is None:
        return []
    return _numeric_cache_values(cache)


def _numeric_cache_values(cache: ET.Element) -> list[int | float | None]:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .chart_to_svg import (
        _UnsupportedChart,
        _cache_point_values,
    )
    values: list[int | float | None] = []
    for value in _cache_point_values(cache):
        if value is None:
            values.append(None)
            continue
        number = float(value)
        if not math.isfinite(number):
            raise _UnsupportedChart("unsupported-chart-cache")
        values.append(int(number) if number.is_integer() else number)
    return values


def _first_cache(parent: ET.Element | None, names: tuple[str, ...]) -> ET.Element | None:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .chart_to_svg import (
        C_NS,
    )
    if parent is None:
        return None
    for name in names:
        found = parent.find(f".//c:{name}", C_NS)
        if found is not None:
            return found
    return None


def _cache_point_values(cache: ET.Element | None) -> list[str | None]:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .chart_to_svg import (
        C_NS,
        _UnsupportedChart,
    )
    if cache is None:
        return []
    points: dict[int, str] = {}
    for idx, point in enumerate(cache.findall("c:pt", C_NS)):
        raw_idx = point.attrib.get("idx")
        try:
            point_idx = int(raw_idx) if raw_idx is not None else idx
        except ValueError:
            raise _UnsupportedChart("unsupported-chart-cache")
        if point_idx < 0 or point_idx in points:
            raise _UnsupportedChart("unsupported-chart-cache")
        value = point.findtext("c:v", default="", namespaces=C_NS)
        points[point_idx] = value

    count_elem = cache.find("c:ptCount", C_NS)
    if count_elem is not None:
        try:
            point_count = int(count_elem.attrib.get("val", ""))
        except ValueError:
            raise _UnsupportedChart("unsupported-chart-cache")
    else:
        point_count = len(points)
    if (
        point_count < 0
        or any(idx >= point_count for idx in points)
    ):
        raise _UnsupportedChart("unsupported-chart-cache")
    return [points.get(idx) for idx in range(point_count)]


def _element_val(elem: ET.Element | None) -> str | None:
    if elem is None:
        return None
    return elem.attrib.get("val")


def _bounds_payload(xfrm: Xfrm) -> dict[str, int | float]:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .chart_to_svg import (
        _round_payload_number,
    )
    return {
        "height": _round_payload_number(xfrm.h),
        "width": _round_payload_number(xfrm.w),
        "x": _round_payload_number(xfrm.x),
        "y": _round_payload_number(xfrm.y),
    }


def _round_payload_number(value: float) -> int | float:
    rounded = round(float(value), 3)
    return int(rounded) if rounded.is_integer() else rounded


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag
