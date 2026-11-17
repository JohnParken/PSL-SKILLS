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

def _strict_axis_bool(elem: ET.Element | None, default: bool) -> bool:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .chart_to_svg import (
        _UnsupportedChart,
    )
    if elem is None:
        return default
    raw = elem.attrib.get("val")
    if raw is None:
        return default
    key = raw.strip().lower()
    if key in {"1", "on", "true"}:
        return True
    if key in {"0", "false", "off"}:
        return False
    raise _UnsupportedChart("unsupported-chart-axis-options")


def _validate_canonical_series_order(
    plots: list[ET.Element],
    status: str,
) -> None:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .chart_to_svg import (
        C_NS,
        _UnsupportedChart,
        _element_val,
    )
    expected_index = 0
    for plot in plots:
        for series in plot.findall("c:ser", C_NS):
            expected = str(expected_index)
            for child_name in ("idx", "order"):
                child = series.find(f"c:{child_name}", C_NS)
                if (
                    child is None
                    or set(child.attrib) != {"val"}
                    or list(child)
                    or _element_val(child) != expected
                ):
                    raise _UnsupportedChart(status)
            expected_index += 1


def _plot_series_identifiers(
    plot: ET.Element,
    status: str,
) -> tuple[list[int], list[int]]:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .chart_to_svg import (
        C_NS,
        _UnsupportedChart,
        _element_val,
    )
    indices: list[int] = []
    orders: list[int] = []
    for series in plot.findall("c:ser", C_NS):
        values: list[int] = []
        for child_name in ("idx", "order"):
            child = series.find(f"c:{child_name}", C_NS)
            raw_value = _element_val(child)
            if (
                child is None
                or set(child.attrib) != {"val"}
                or list(child)
                or raw_value is None
                or re.fullmatch(r"[0-9]+", raw_value) is None
            ):
                raise _UnsupportedChart(status)
            values.append(int(raw_value))
        indices.append(values[0])
        orders.append(values[1])
    if (
        not indices
        or len(set(indices)) != len(indices)
        or len(set(orders)) != len(orders)
    ):
        raise _UnsupportedChart(status)
    return indices, orders


def _axis_number(elem: ET.Element | None) -> int | float | None:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .chart_to_svg import (
        _UnsupportedChart,
    )
    if elem is None:
        return None
    raw = elem.attrib.get("val")
    try:
        number = float(raw) if raw is not None else math.nan
    except (TypeError, ValueError, OverflowError):
        raise _UnsupportedChart("unsupported-chart-axis-options") from None
    if not math.isfinite(number):
        raise _UnsupportedChart("unsupported-chart-axis-options")
    return int(number) if number.is_integer() else number


def _axis_nodes_by_id(plot_area: ET.Element) -> dict[str, ET.Element]:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .chart_to_svg import (
        C_NS,
        _UnsupportedChart,
        _element_val,
    )
    if plot_area.find("c:serAx", C_NS) is not None:
        raise _UnsupportedChart("unsupported-chart-axis-options")
    axes: dict[str, ET.Element] = {}
    for tag in ("catAx", "dateAx", "valAx"):
        for axis in plot_area.findall(f"c:{tag}", C_NS):
            axis_id = _element_val(axis.find("c:axId", C_NS))
            if not axis_id or axis_id in axes:
                raise _UnsupportedChart("unsupported-chart-axis-options")
            axes[axis_id] = axis
    return axes


def _plot_axis_pair(
    plot: ET.Element,
    axes_by_id: dict[str, ET.Element],
) -> tuple[str, ET.Element, str, ET.Element]:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .chart_to_svg import (
        C_NS,
        _UnsupportedChart,
        _element_val,
        _local_name,
    )
    axis_ids = [_element_val(elem) for elem in plot.findall("c:axId", C_NS)]
    if len(axis_ids) != 2 or any(not axis_id for axis_id in axis_ids):
        raise _UnsupportedChart("unsupported-chart-axis-options")
    if len(set(axis_ids)) != 2:
        raise _UnsupportedChart("unsupported-chart-axis-options")
    resolved = [(axis_id, axes_by_id.get(str(axis_id))) for axis_id in axis_ids]
    if any(axis is None for _, axis in resolved):
        raise _UnsupportedChart("unsupported-chart-axis-options")
    category_axes = [
        (str(axis_id), axis)
        for axis_id, axis in resolved
        if axis is not None and _local_name(axis.tag) in {"catAx", "dateAx"}
    ]
    value_axes = [
        (str(axis_id), axis)
        for axis_id, axis in resolved
        if axis is not None and _local_name(axis.tag) == "valAx"
    ]
    if len(category_axes) != 1 or len(value_axes) != 1:
        raise _UnsupportedChart("unsupported-chart-axis-options")
    cat_id, cat_axis = category_axes[0]
    val_id, val_axis = value_axes[0]
    return cat_id, cat_axis, val_id, val_axis


def _axis_config_from_xml(
    axis: ET.Element,
    *,
    role: str,
    expected_cross_axis_id: str,
    allowed_crosses: set[str],
    expected_cross_between: str | None,
) -> dict[str, Any]:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .chart_to_svg import (
        C_NS,
        _UnsupportedChart,
        _axis_number,
        _element_val,
        _local_name,
        _strict_axis_bool,
    )
    axis_kind = _local_name(axis.tag)
    allowed_children = {
        "auto", "axId", "axPos", "baseTimeUnit", "crossAx", "crossBetween",
        "crosses", "delete", "lblAlgn", "lblOffset", "majorGridlines",
        "majorTickMark", "majorUnit", "minorTickMark", "noMultiLvlLbl",
        "numFmt", "scaling", "spPr", "tickLblPos", "title", "txPr",
    }
    if any(_local_name(child.tag) not in allowed_children for child in axis):
        raise _UnsupportedChart("unsupported-chart-axis-options")
    if axis.attrib:
        raise _UnsupportedChart("unsupported-chart-axis-options")
    for child_name in allowed_children:
        if len(axis.findall(f"c:{child_name}", C_NS)) > 1:
            raise _UnsupportedChart("unsupported-chart-axis-options")
    simple_children = {
        "auto", "axId", "axPos", "baseTimeUnit", "crossAx",
        "crossBetween", "crosses", "delete", "lblAlgn", "lblOffset",
        "majorTickMark", "majorUnit", "minorTickMark", "noMultiLvlLbl",
        "tickLblPos",
    }
    if any(
        _local_name(child.tag) in simple_children
        and (set(child.attrib) != {"val"} or list(child))
        for child in axis
    ):
        raise _UnsupportedChart("unsupported-chart-axis-options")
    if axis.find("c:minorGridlines", C_NS) is not None:
        raise _UnsupportedChart("unsupported-chart-axis-options")

    scaling = axis.find("c:scaling", C_NS)
    config: dict[str, Any] = {}
    if scaling is not None:
        if scaling.attrib or any(
            _local_name(child.tag) not in {"max", "min", "orientation"}
            for child in scaling
        ):
            raise _UnsupportedChart("unsupported-chart-axis-options")
        for child_name in ("orientation", "max", "min"):
            if len(scaling.findall(f"c:{child_name}", C_NS)) > 1:
                raise _UnsupportedChart("unsupported-chart-axis-options")
        if any(
            set(child.attrib) != {"val"} or list(child)
            for child in scaling
        ):
            raise _UnsupportedChart("unsupported-chart-axis-options")
        orientation = _element_val(scaling.find("c:orientation", C_NS)) or "minMax"
        if orientation not in {"maxMin", "minMax"}:
            raise _UnsupportedChart("unsupported-chart-axis-options")
        config["reverse"] = orientation == "maxMin"
        minimum = _axis_number(scaling.find("c:min", C_NS))
        maximum = _axis_number(scaling.find("c:max", C_NS))
        if minimum is not None:
            config["minimum"] = minimum
        if maximum is not None:
            config["maximum"] = maximum
        if minimum is not None and maximum is not None and minimum >= maximum:
            raise _UnsupportedChart("unsupported-chart-axis-options")
    else:
        config["reverse"] = False

    position_aliases = {"b": "bottom", "l": "left", "r": "right", "t": "top"}
    position = position_aliases.get(_element_val(axis.find("c:axPos", C_NS)) or "")
    if position is None:
        raise _UnsupportedChart("unsupported-chart-axis-options")
    if role in {"category", "secondary_category"}:
        if position not in {"bottom", "top"}:
            raise _UnsupportedChart("unsupported-chart-axis-options")
        config["kind"] = "date" if axis_kind == "dateAx" else "text"
    elif role == "x":
        if position not in {"bottom", "top"} or axis_kind != "valAx":
            raise _UnsupportedChart("unsupported-chart-axis-options")
        config["kind"] = "value"
    else:
        if position not in {"left", "right"} or axis_kind != "valAx":
            raise _UnsupportedChart("unsupported-chart-axis-options")
        config["kind"] = "value"
    config["position"] = position
    config["visible"] = not _strict_axis_bool(axis.find("c:delete", C_NS), False)

    tick_label_aliases = {
        "high": "high",
        "low": "low",
        "nextTo": "next_to",
        "none": "none",
    }
    tick_label_position = _element_val(axis.find("c:tickLblPos", C_NS)) or "nextTo"
    if tick_label_position not in tick_label_aliases:
        raise _UnsupportedChart("unsupported-chart-axis-options")
    config["label_position"] = tick_label_aliases[tick_label_position]
    config["major_gridlines"] = axis.find("c:majorGridlines", C_NS) is not None

    num_fmt = axis.find("c:numFmt", C_NS)
    if num_fmt is not None:
        if list(num_fmt) or not set(num_fmt.attrib).issubset({"formatCode", "sourceLinked"}):
            raise _UnsupportedChart("unsupported-chart-axis-options")
        number_format = num_fmt.attrib.get("formatCode", "")
        if not number_format.strip():
            raise _UnsupportedChart("unsupported-chart-axis-number-format")
        config["number_format"] = number_format

    major_unit = _axis_number(axis.find("c:majorUnit", C_NS))
    if major_unit is not None:
        if role not in {"value", "secondary_value", "x", "y"} or major_unit <= 0:
            raise _UnsupportedChart("unsupported-chart-axis-options")
        config["major_unit"] = major_unit

    if _element_val(axis.find("c:crossAx", C_NS)) != expected_cross_axis_id:
        raise _UnsupportedChart("unsupported-chart-axis-options")
    crosses = _element_val(axis.find("c:crosses", C_NS)) or "autoZero"
    if crosses not in allowed_crosses:
        raise _UnsupportedChart("unsupported-chart-axis-options")
    cross_between = _element_val(axis.find("c:crossBetween", C_NS))
    if cross_between != expected_cross_between:
        raise _UnsupportedChart("unsupported-chart-axis-options")
    auto = axis.find("c:auto", C_NS)
    if auto is not None:
        _strict_axis_bool(auto, True)
    major_tick = _element_val(axis.find("c:majorTickMark", C_NS))
    minor_tick = _element_val(axis.find("c:minorTickMark", C_NS))
    if major_tick not in {None, "none", "out", "in", "cross"} or minor_tick not in {None, "none"}:
        raise _UnsupportedChart("unsupported-chart-axis-options")
    label_alignment = _element_val(axis.find("c:lblAlgn", C_NS))
    label_offset = _element_val(axis.find("c:lblOffset", C_NS))
    no_multi_level = _element_val(axis.find("c:noMultiLvlLbl", C_NS))
    if role in {"category", "secondary_category"}:
        if label_alignment not in {None, "ctr"}:
            raise _UnsupportedChart("unsupported-chart-axis-options")
        if label_offset not in {None, "100"}:
            raise _UnsupportedChart("unsupported-chart-axis-options")
        if no_multi_level not in {None, "0"}:
            raise _UnsupportedChart("unsupported-chart-axis-options")
    elif any(
        value is not None
        for value in (label_alignment, label_offset, no_multi_level, auto)
    ):
        raise _UnsupportedChart("unsupported-chart-axis-options")
    if axis_kind == "dateAx":
        if _element_val(axis.find("c:baseTimeUnit", C_NS)) not in {None, "days"}:
            raise _UnsupportedChart("unsupported-chart-axis-options")
    elif axis.find("c:baseTimeUnit", C_NS) is not None:
        raise _UnsupportedChart("unsupported-chart-axis-options")
    return config


def _single_axis_contract(
    plot_area: ET.Element,
    plot: ET.Element,
    *,
    category_kind: str,
    cross_between: str,
) -> dict[str, dict[str, Any]]:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .chart_to_svg import (
        _UnsupportedChart,
        _axis_config_from_xml,
        _axis_nodes_by_id,
        _local_name,
        _plot_axis_pair,
    )
    axes_by_id = _axis_nodes_by_id(plot_area)
    cat_id, cat_axis, val_id, val_axis = _plot_axis_pair(plot, axes_by_id)
    if set(axes_by_id) != {cat_id, val_id}:
        raise _UnsupportedChart("unsupported-chart-axis-options")
    if _local_name(cat_axis.tag) != ("dateAx" if category_kind == "date" else "catAx"):
        raise _UnsupportedChart("unsupported-chart-axis-options")
    category = _axis_config_from_xml(
        cat_axis,
        role="category",
        expected_cross_axis_id=val_id,
        allowed_crosses={"autoZero"},
        expected_cross_between=None,
    )
    value = _axis_config_from_xml(
        val_axis,
        role="value",
        expected_cross_axis_id=cat_id,
        allowed_crosses={"autoZero"},
        expected_cross_between=cross_between,
    )
    return {"category": category, "value": value}


def _xy_axis_contract(
    plot_area: ET.Element,
    plot: ET.Element,
) -> dict[str, dict[str, Any]]:
    """Return the closed two-value-axis contract used by XY charts."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .chart_to_svg import (
        C_NS,
        _UnsupportedChart,
        _axis_config_from_xml,
        _axis_nodes_by_id,
        _element_val,
        _local_name,
    )
    axes_by_id = _axis_nodes_by_id(plot_area)
    plot_axis_nodes = plot.findall("c:axId", C_NS)
    if len(plot_axis_nodes) != 2 or any(
        set(node.attrib) != {"val"} or list(node)
        for node in plot_axis_nodes
    ):
        raise _UnsupportedChart("unsupported-chart-axis-options")
    axis_ids = [_element_val(node) for node in plot_axis_nodes]
    if any(not axis_id for axis_id in axis_ids) or len(set(axis_ids)) != 2:
        raise _UnsupportedChart("unsupported-chart-axis-options")
    if set(axes_by_id) != set(axis_ids):
        raise _UnsupportedChart("unsupported-chart-axis-options")

    resolved = [
        (str(axis_id), axes_by_id.get(str(axis_id)))
        for axis_id in axis_ids
    ]
    if any(
        axis is None or _local_name(axis.tag) != "valAx"
        for _, axis in resolved
    ):
        raise _UnsupportedChart("unsupported-chart-axis-options")
    horizontal = [
        (axis_id, axis)
        for axis_id, axis in resolved
        if axis is not None
        and _element_val(axis.find("c:axPos", C_NS)) in {"b", "t"}
    ]
    vertical = [
        (axis_id, axis)
        for axis_id, axis in resolved
        if axis is not None
        and _element_val(axis.find("c:axPos", C_NS)) in {"l", "r"}
    ]
    if len(horizontal) != 1 or len(vertical) != 1:
        raise _UnsupportedChart("unsupported-chart-axis-options")

    x_id, x_axis = horizontal[0]
    y_id, y_axis = vertical[0]
    x_config = _axis_config_from_xml(
        x_axis,
        role="x",
        expected_cross_axis_id=y_id,
        allowed_crosses={"autoZero"},
        expected_cross_between="midCat",
    )
    y_config = _axis_config_from_xml(
        y_axis,
        role="y",
        expected_cross_axis_id=x_id,
        allowed_crosses={"autoZero"},
        expected_cross_between="midCat",
    )
    return {"x": x_config, "y": y_config}


def _validate_legacy_axes(payload: dict[str, Any], plot_area: ET.Element) -> None:
    """Keep the original narrow axis gate for payloads without axes metadata."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .chart_to_svg import (
        C_NS,
        _UnsupportedChart,
        _element_val,
        _local_name,
    )
    grouping = payload.get("grouping")
    axes = plot_area.findall("c:catAx", C_NS) + plot_area.findall("c:valAx", C_NS)
    for axis in axes:
        axis_kind = _local_name(axis.tag)
        axis_position = _element_val(axis.find("c:axPos", C_NS))
        delete = axis.find("c:delete", C_NS)
        if delete is not None and ooxml_bool(delete.attrib.get("val"), True):
            raise _UnsupportedChart("unsupported-chart-axis-options")
        scaling = axis.find("c:scaling", C_NS)
        if scaling is not None:
            for tag in ("logBase", "min", "max"):
                if scaling.find(f"c:{tag}", C_NS) is not None:
                    raise _UnsupportedChart("unsupported-chart-axis-options")
            orientation = _element_val(scaling.find("c:orientation", C_NS))
            if orientation not in {None, "minMax"}:
                raise _UnsupportedChart("unsupported-chart-axis-options")
        for tag in (
            "majorUnit", "minorUnit", "crossesAt", "dispUnits",
            "tickLblSkip", "tickMarkSkip",
        ):
            if axis.find(f"c:{tag}", C_NS) is not None:
                raise _UnsupportedChart("unsupported-chart-axis-options")
        crosses = _element_val(axis.find("c:crosses", C_NS))
        if crosses not in {None, "autoZero"}:
            raise _UnsupportedChart("unsupported-chart-axis-options")
        auto = axis.find("c:auto", C_NS)
        if auto is not None and not ooxml_bool(auto.attrib.get("val"), True):
            raise _UnsupportedChart("unsupported-chart-axis-options")
        num_fmt = axis.find("c:numFmt", C_NS)
        if num_fmt is not None:
            format_code = num_fmt.attrib.get("formatCode", "").strip()
            allowed_formats = {"", "General"}
            if grouping == "percentStacked":
                allowed_formats.add("0%")
            if format_code not in allowed_formats:
                raise _UnsupportedChart("unsupported-chart-axis-number-format")
        tick_label_position = _element_val(axis.find("c:tickLblPos", C_NS))
        if payload["type"] in {"scatter", "bubble"} or axis_kind == "catAx":
            if tick_label_position not in {None, "nextTo"}:
                raise _UnsupportedChart("unsupported-chart-axis-options")
        elif tick_label_position == "none":
            payload["show_value_axis_labels"] = False
        elif tick_label_position not in {None, "nextTo"}:
            raise _UnsupportedChart("unsupported-chart-axis-options")

        has_major_gridlines = axis.find("c:majorGridlines", C_NS) is not None
        if payload["type"] in {"scatter", "bubble"}:
            expected_major_gridlines = axis_position in {"l", "r"}
        else:
            expected_major_gridlines = axis_kind == "valAx"
        if has_major_gridlines != expected_major_gridlines:
            raise _UnsupportedChart("unsupported-chart-axis-options")
        if axis.find("c:minorGridlines", C_NS) is not None:
            raise _UnsupportedChart("unsupported-chart-axis-options")


def _validate_normalized_axis_topology(
    payload: dict[str, Any],
    plot_area: ET.Element,
    plot: ET.Element,
) -> None:
    """Validate a category/value pair whose presentation will normalize."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .chart_to_svg import (
        C_NS,
        _UnsupportedChart,
        _axis_nodes_by_id,
        _element_val,
        _plot_axis_pair,
    )
    axes_by_id = _axis_nodes_by_id(plot_area)
    cat_id, cat_axis, val_id, val_axis = _plot_axis_pair(plot, axes_by_id)
    if set(axes_by_id) != {cat_id, val_id}:
        raise _UnsupportedChart("unsupported-chart-axis-options")

    plot_axis_nodes = plot.findall("c:axId", C_NS)
    if any(set(node.attrib) != {"val"} or list(node) for node in plot_axis_nodes):
        raise _UnsupportedChart("unsupported-chart-axis-options")
    for axis, axis_id, cross_axis_id in (
        (cat_axis, cat_id, val_id),
        (val_axis, val_id, cat_id),
    ):
        if axis.attrib:
            raise _UnsupportedChart("unsupported-chart-axis-options")
        id_nodes = axis.findall("c:axId", C_NS)
        cross_nodes = axis.findall("c:crossAx", C_NS)
        if (
            len(id_nodes) != 1
            or len(cross_nodes) != 1
            or set(id_nodes[0].attrib) != {"val"}
            or set(cross_nodes[0].attrib) != {"val"}
            or list(id_nodes[0])
            or list(cross_nodes[0])
            or _element_val(id_nodes[0]) != axis_id
            or _element_val(cross_nodes[0]) != cross_axis_id
        ):
            raise _UnsupportedChart("unsupported-chart-axis-options")

    position_nodes = (
        cat_axis.findall("c:axPos", C_NS),
        val_axis.findall("c:axPos", C_NS),
    )
    if any(
        len(nodes) != 1
        or set(nodes[0].attrib) != {"val"}
        or list(nodes[0])
        for nodes in position_nodes
    ):
        raise _UnsupportedChart("unsupported-chart-axis-options")
    category_position = _element_val(position_nodes[0][0])
    value_position = _element_val(position_nodes[1][0])
    if payload["type"] == "bar":
        valid_positions = (
            category_position in {"l", "r"}
            and value_position in {"b", "t"}
        )
    else:
        valid_positions = (
            category_position in {"b", "t"}
            and value_position in {"l", "r"}
        )
    if not valid_positions:
        raise _UnsupportedChart("unsupported-chart-axis-options")


def _validate_or_normalize_legacy_axes(
    payload: dict[str, Any],
    plot_area: ET.Element,
    plot: ET.Element,
) -> None:
    """Keep exact legacy payloads while allowing presentation normalization."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .chart_to_svg import (
        _UnsupportedChart,
        _single_axis_contract,
        _validate_legacy_axes,
        _validate_normalized_axis_topology,
    )
    try:
        _validate_legacy_axes(payload, plot_area)
    except _UnsupportedChart as exc:
        if exc.status not in {
            "unsupported-chart-axis-number-format",
            "unsupported-chart-axis-options",
        }:
            raise
        if payload["type"] in {"area", "column", "line"}:
            try:
                payload["axes"] = _single_axis_contract(
                    plot_area,
                    plot,
                    category_kind="text",
                    cross_between="between",
                )
                return
            except _UnsupportedChart:
                payload.pop("axes", None)
        _validate_normalized_axis_topology(payload, plot_area, plot)


