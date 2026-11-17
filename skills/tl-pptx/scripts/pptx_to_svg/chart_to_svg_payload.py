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
    ChartResult,
)

def extract_native_chart_payload(
    graphic_data: ET.Element | None,
    xfrm: Xfrm,
    slide_part: PartRef,
    pkg: OoxmlPackage,
    palette: ColorPalette | None = None,
) -> ChartResult:
    """Return native chart metadata for a supported classic or ChartEx chart."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .chart_to_svg import (
        CHARTEX_URI,
        CHART_URI,
        C_NS,
        ChartResult,
        _UnsupportedChart,
        _payload_from_chart_xml,
    )
    if graphic_data is None:
        return ChartResult(native_status="unsupported-chart-reference")

    uri = graphic_data.attrib.get("uri", "")
    if uri == CHARTEX_URI or graphic_data.find("cx:chart", C_NS) is not None:
        try:
            payload = extract_native_chartex_payload(
                graphic_data,
                xfrm,
                slide_part,
                pkg,
                palette,
            )
        except UnsupportedChartEx as exc:
            return ChartResult(native_status=exc.status)
        except RuntimeError:
            return ChartResult(native_status="unsupported-chartex-parse")
        return ChartResult(native_payload=payload)
    if uri != CHART_URI:
        return ChartResult(native_status="unsupported-chart-uri")
    if xfrm.rot or xfrm.flip_h or xfrm.flip_v:
        return ChartResult(native_status="unsupported-native-transform")

    chart_ref = graphic_data.find("c:chart", C_NS)
    if chart_ref is None:
        return ChartResult(native_status="unsupported-chart-reference")
    rid = chart_ref.attrib.get(f"{{{NS['r']}}}id")
    if not rid:
        return ChartResult(native_status="unsupported-chart-reference")

    chart_path = slide_part.resolve_rel(rid)
    if not chart_path:
        return ChartResult(native_status="unsupported-chart-relationship")
    chart_part = pkg.load_part(chart_path)
    if chart_part is None:
        return ChartResult(native_status="unsupported-chart-part")

    try:
        payload, visual_styles = _payload_from_chart_xml(
            chart_part.xml,
            xfrm,
            palette=palette,
        )
        validate_chart_payload(payload)
    except _UnsupportedChart as exc:
        return ChartResult(native_status=exc.status)
    except RuntimeError:
        return ChartResult(native_status="unsupported-chart-schema")
    except (TypeError, ValueError, AttributeError):
        return ChartResult(native_status="unsupported-chart-parse")
    # The visual fallback is deliberately best-effort and isolated from the
    # active native payload. A renderer defect must never downgrade a chart
    # that the native schema has already validated.
    try:
        normalized_svg = render_normalized_chart_svg(payload, visual_styles)
    except (KeyError, TypeError, ValueError, OverflowError, ArithmeticError):
        normalized_svg = None
    return ChartResult(native_payload=payload, normalized_svg=normalized_svg)


def _payload_from_chart_xml(
    chart_root: ET.Element,
    xfrm: Xfrm,
    *,
    palette: ColorPalette | None = None,
) -> tuple[dict[str, Any], list[SeriesVisualStyle]]:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .chart_to_svg import (
        C_NS,
        _UnsupportedChart,
        _apply_chart_metadata,
        _bar_chart_type,
        _category_cache_is_numeric,
        _category_payload,
        _combo_payload,
        _effective_radar_style,
        _local_name,
        _numeric_category_cache_format,
        _single_axis_contract,
        _stock_payload,
        _strict_axis_bool,
        _validate_canonical_series_order,
        _validate_chart_semantics,
        _xy_axis_contract,
        _xy_payload,
    )
    plot_area = chart_root.find(".//c:plotArea", C_NS)
    if plot_area is None:
        raise _UnsupportedChart("unsupported-chart-plot")

    chart_nodes = [
        child
        for child in list(plot_area)
        if _local_name(child.tag).endswith("Chart")
    ]
    if not chart_nodes:
        raise _UnsupportedChart("unsupported-chart-plot")
    date_system = chart_root.find("c:date1904", C_NS)
    if date_system is not None and (
        not set(date_system.attrib).issubset({"val"}) or list(date_system)
    ):
        raise _UnsupportedChart("unsupported-date-system")
    uses_1904_dates = _strict_axis_bool(
        date_system,
        date_system is not None,
    )
    if len(chart_nodes) > 1:
        if uses_1904_dates and any(
            _category_cache_is_numeric(chart) for chart in chart_nodes
        ):
            raise _UnsupportedChart("unsupported-date-system")
        payload, visual_styles = _combo_payload(
            chart_root,
            plot_area,
            chart_nodes,
            xfrm,
            palette=palette,
        )
        return payload, visual_styles

    chart = chart_nodes[0]
    chart_tag = _local_name(chart.tag)
    has_date_axis = plot_area.find("c:dateAx", C_NS) is not None
    if uses_1904_dates and has_date_axis:
        raise _UnsupportedChart("unsupported-date-system")
    if has_date_axis:
        _validate_canonical_series_order(
            [chart],
            "unsupported-chart-series-order",
        )
    if chart_tag in {"area3DChart", "bar3DChart", "line3DChart", "pie3DChart", "surface3DChart"}:
        raise _UnsupportedChart("unsupported-3d-chart")
    if has_date_axis and chart_tag not in {"areaChart", "stockChart"}:
        raise _UnsupportedChart("unsupported-date-axis")
    if chart_tag == "barChart":
        payload = _category_payload(chart, _bar_chart_type(chart), xfrm)
    elif chart_tag in {
        "areaChart",
        "doughnutChart",
        "lineChart",
        "ofPieChart",
        "pieChart",
        "radarChart",
    }:
        chart_type = {
            "areaChart": "area",
            "doughnutChart": "doughnut",
            "lineChart": "line",
            "ofPieChart": "of_pie",
            "pieChart": "pie",
            "radarChart": "radar",
        }[chart_tag]
        category_kind = "date" if has_date_axis else "text"
        if chart_tag == "radarChart" and _category_cache_is_numeric(chart):
            category_kind = "numeric"
        payload = _category_payload(
            chart,
            chart_type,
            xfrm,
            category_kind=category_kind,
        )
        if chart_tag == "radarChart":
            if category_kind == "numeric":
                payload["categories"] = [
                    str(value) for value in payload["categories"]
                ]
            payload["radar_style"] = _effective_radar_style(chart)
    elif chart_tag == "scatterChart":
        payload = _xy_payload(chart, "scatter", xfrm)
        payload["axes"] = _xy_axis_contract(plot_area, chart)
    elif chart_tag == "bubbleChart":
        payload = _xy_payload(chart, "bubble", xfrm)
        payload["axes"] = _xy_axis_contract(plot_area, chart)
    elif chart_tag == "stockChart":
        payload, visual_styles = _stock_payload(
            chart_root,
            plot_area,
            chart,
            xfrm,
            palette=palette,
        )
        return payload, visual_styles
    else:
        raise _UnsupportedChart("unsupported-chart-type")

    if has_date_axis:
        payload["axes"] = _single_axis_contract(
            plot_area,
            chart,
            category_kind="date",
            cross_between="midCat",
        )
        expected_category_format = payload["axes"]["category"].get(
            "number_format",
            "m/d/yyyy",
        )
        if _numeric_category_cache_format(chart) != expected_category_format:
            raise _UnsupportedChart("unsupported-chart-category-format")

    visual_styles = _validate_chart_semantics(
        payload,
        plot_area,
        chart,
        palette=palette,
        validate_axes=not (
            has_date_axis or chart_tag in {"bubbleChart", "scatterChart"}
        ),
    )
    _apply_chart_metadata(
        payload,
        chart_root,
        plot_area,
        chart,
        palette=palette,
    )
    return payload, visual_styles


def _combo_payload(
    chart_root: ET.Element,
    plot_area: ET.Element,
    chart_nodes: list[ET.Element],
    xfrm: Xfrm,
    *,
    palette: ColorPalette | None,
) -> tuple[dict[str, Any], list[SeriesVisualStyle]]:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .chart_to_svg import (
        C_NS,
        _UnsupportedChart,
        _apply_chart_metadata,
        _apply_plot_data_labels,
        _axis_config_from_xml,
        _axis_nodes_by_id,
        _bar_chart_type,
        _bounds_payload,
        _category_cache_is_numeric,
        _category_payload,
        _element_val,
        _local_name,
        _numeric_category_cache_format,
        _plot_axis_pair,
        _plot_series_identifiers,
        _validate_chart_semantics,
    )
    series_identifiers_by_plot = [
        _plot_series_identifiers(chart, "unsupported-combo-series-order")
        for chart in chart_nodes
    ]
    source_indices_by_plot = [
        identifiers[0] for identifiers in series_identifiers_by_plot
    ]
    source_orders_by_plot = [
        identifiers[1] for identifiers in series_identifiers_by_plot
    ]
    flat_source_indices = [
        index
        for indices in source_indices_by_plot
        for index in indices
    ]
    flat_source_orders = [
        order
        for orders in source_orders_by_plot
        for order in orders
    ]
    expected_series_order = list(range(len(flat_source_orders)))
    canonical_indices = (
        flat_source_indices == flat_source_orders
        and sorted(flat_source_indices) == expected_series_order
    )
    normalizable_indices = (
        len(set(flat_source_indices)) == len(flat_source_indices)
        and sorted(flat_source_orders) == expected_series_order
    )
    if canonical_indices:
        series_indices_by_plot = source_indices_by_plot
    elif normalizable_indices and palette is not None and not palette.strict:
        palette._diagnose(
            "combo-series-indices-normalized",
            "Combo chart series idx values do not match their contiguous "
            "display order",
            "reindex series by their unique contiguous order values",
        )
        series_indices_by_plot = source_orders_by_plot
    else:
        raise _UnsupportedChart("unsupported-combo-series-order")
    axes_by_id = _axis_nodes_by_id(plot_area)
    if not axes_by_id:
        raise _UnsupportedChart("unsupported-combo-chart")
    axes: dict[str, dict[str, Any]] = {}
    axis_pairs: dict[str, tuple[str, str]] = {}
    primary_categories: list[Any] | None = None
    primary_categories_are_numeric = False
    plots: list[dict[str, Any]] = []
    visual_styles: list[SeriesVisualStyle] = []
    colors: list[str] = []
    referenced_axis_ids: set[str] = set()

    for chart, series_indices in zip(chart_nodes, series_indices_by_plot):
        chart_tag = _local_name(chart.tag)
        if chart_tag == "barChart":
            if _bar_chart_type(chart) != "column":
                raise _UnsupportedChart("unsupported-combo-chart")
            chart_type = "column"
        elif chart_tag == "lineChart":
            chart_type = "line"
        elif chart_tag == "areaChart":
            chart_type = "area"
        else:
            raise _UnsupportedChart("unsupported-combo-chart")

        cat_id, cat_axis, val_id, val_axis = _plot_axis_pair(chart, axes_by_id)
        if _local_name(cat_axis.tag) != "catAx":
            raise _UnsupportedChart("unsupported-combo-chart")
        val_position = _element_val(val_axis.find("c:axPos", C_NS))
        if val_position == "l":
            axis_name = "primary"
            category_role = "category"
            value_role = "value"
            allowed_value_crosses = {"autoZero"}
        elif val_position == "r":
            axis_name = "secondary"
            category_role = "secondary_category"
            value_role = "secondary_value"
            allowed_value_crosses = {"max"}
        else:
            raise _UnsupportedChart("unsupported-combo-chart")

        axis_pair = (cat_id, val_id)
        previous_pair = axis_pairs.get(axis_name)
        if previous_pair is not None and previous_pair != axis_pair:
            raise _UnsupportedChart("unsupported-combo-chart")
        if any(
            set(existing_pair).intersection(axis_pair)
            for name, existing_pair in axis_pairs.items()
            if name != axis_name
        ):
            raise _UnsupportedChart("unsupported-combo-chart")
        axis_pairs[axis_name] = axis_pair

        category_axis = _axis_config_from_xml(
            cat_axis,
            role=category_role,
            expected_cross_axis_id=val_id,
            allowed_crosses={"autoZero"},
            expected_cross_between=None,
        )
        value_axis = _axis_config_from_xml(
            val_axis,
            role=value_role,
            expected_cross_axis_id=cat_id,
            allowed_crosses=allowed_value_crosses,
            expected_cross_between="between",
        )
        for role, config in (
            (category_role, category_axis),
            (value_role, value_axis),
        ):
            if role in axes and axes[role] != config:
                raise _UnsupportedChart("unsupported-combo-chart")
            axes[role] = config
        referenced_axis_ids.update((cat_id, val_id))

        categories_are_numeric = _category_cache_is_numeric(chart)
        if categories_are_numeric:
            expected_category_format = category_axis.get(
                "number_format",
                "General",
            )
            if _numeric_category_cache_format(chart) != expected_category_format:
                raise _UnsupportedChart("unsupported-combo-category-format")
        plot_payload = _category_payload(
            chart,
            chart_type,
            xfrm,
            category_kind="numeric" if categories_are_numeric else "text",
        )
        if axis_name == "primary" and primary_categories is None:
            primary_categories = list(plot_payload["categories"])
            primary_categories_are_numeric = categories_are_numeric
        plot_styles = _validate_chart_semantics(
            plot_payload,
            plot_area,
            chart,
            palette=palette,
            validate_axes=False,
        )
        visual_styles.extend(plot_styles)
        colors.extend(
            str(color)
            for color in plot_payload.get("style", {}).get("colors", [])
        )
        _apply_plot_data_labels(plot_payload, chart, palette=palette)
        plot_entry: dict[str, Any] = {
            "axis": axis_name,
            "categories": list(plot_payload["categories"]),
            "category_numeric": categories_are_numeric,
            "series": plot_payload["series"],
            "series_indices": series_indices,
            "type": chart_type,
        }
        for key in ("data_labels", "gap_width", "grouping", "line_style", "overlap"):
            if plot_payload.get(key) is not None:
                plot_entry[key] = plot_payload[key]
        plots.append(plot_entry)

    if primary_categories is None or not plots:
        raise _UnsupportedChart("unsupported-combo-chart")
    if referenced_axis_ids != set(axes_by_id):
        raise _UnsupportedChart("unsupported-combo-chart")

    category_layouts = {
        (
            bool(plot["category_numeric"]),
            tuple(plot["categories"]),
        )
        for plot in plots
    }
    if len(category_layouts) == 1:
        for plot in plots:
            plot.pop("categories")
            plot.pop("category_numeric")
    payload: dict[str, Any] = {
        **_bounds_payload(xfrm),
        "axes": axes,
        "categories": primary_categories,
        "plots": plots,
        "type": "combo",
    }
    if primary_categories_are_numeric:
        payload["category_numeric"] = True
    if colors:
        payload["style"] = {"colors": colors}
    _apply_chart_metadata(
        payload,
        chart_root,
        plot_area,
        chart_nodes[0],
        palette=palette,
        include_plot_labels=False,
    )
    return payload, visual_styles


def _validate_stock_semantics(
    plot_area: ET.Element,
    chart: ET.Element,
) -> None:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .chart_to_svg import (
        C_NS,
        _UnsupportedChart,
        _data_labels_payload,
        _element_val,
        _local_name,
        _strict_axis_bool,
    )
    allowed_children = {"axId", "dLbls", "hiLowLines", "ser", "upDownBars"}
    if any(_local_name(child.tag) not in allowed_children for child in chart):
        raise _UnsupportedChart("unsupported-stock-chart")
    if plot_area.find("c:dTable", C_NS) is not None:
        raise _UnsupportedChart("unsupported-chart-data-table")
    for tag in ("dropLines", "errBars", "trendline"):
        if chart.find(f".//c:{tag}", C_NS) is not None:
            raise _UnsupportedChart("unsupported-chart-analysis-features")
    if _data_labels_payload(chart.find("c:dLbls", C_NS)) is not None:
        raise _UnsupportedChart("unsupported-chart-data-labels")
    if len(chart.findall("c:dLbls", C_NS)) > 1:
        raise _UnsupportedChart("unsupported-stock-chart")

    hi_low_lines = chart.findall("c:hiLowLines", C_NS)
    up_down_bars = chart.findall("c:upDownBars", C_NS)
    if len(hi_low_lines) != 1 or len(up_down_bars) != 1:
        raise _UnsupportedChart("unsupported-stock-chart")
    hi_low_styles = hi_low_lines[0].findall("c:spPr", C_NS)
    if (
        hi_low_lines[0].attrib
        or len(hi_low_styles) > 1
        or any(_local_name(child.tag) != "spPr" for child in hi_low_lines[0])
    ):
        raise _UnsupportedChart("unsupported-stock-chart")
    up_down = up_down_bars[0]
    if up_down.attrib or any(
        _local_name(child.tag) not in {"downBars", "gapWidth", "upBars"}
        for child in up_down
    ):
        raise _UnsupportedChart("unsupported-stock-chart")
    gap_widths = up_down.findall("c:gapWidth", C_NS)
    if (
        len(gap_widths) != 1
        or set(gap_widths[0].attrib) != {"val"}
        or list(gap_widths[0])
        or _element_val(gap_widths[0]) != "150"
    ):
        raise _UnsupportedChart("unsupported-stock-chart")
    for tag in ("upBars", "downBars"):
        nodes = up_down.findall(f"c:{tag}", C_NS)
        styles = nodes[0].findall("c:spPr", C_NS) if nodes else []
        if (
            len(nodes) != 1
            or nodes[0].attrib
            or len(styles) > 1
            or any(_local_name(child.tag) != "spPr" for child in nodes[0])
        ):
            raise _UnsupportedChart("unsupported-stock-chart")

    allowed_series_children = {
        "cat", "extLst", "idx", "marker", "order", "smooth", "spPr",
        "tx", "val",
    }
    for series_node in chart.findall("c:ser", C_NS):
        if any(
            _local_name(child.tag) not in allowed_series_children
            for child in series_node
        ):
            raise _UnsupportedChart("unsupported-stock-chart")
        for child_name in allowed_series_children - {"extLst"}:
            if len(series_node.findall(f"c:{child_name}", C_NS)) > 1:
                raise _UnsupportedChart("unsupported-stock-chart")
        marker = series_node.find("c:marker", C_NS)
        symbol = marker.find("c:symbol", C_NS) if marker is not None else None
        if (
            marker is not None
            and (
                marker.attrib
                or len(marker) != 1
                or symbol is None
                or set(symbol.attrib) != {"val"}
                or list(symbol)
                or _element_val(symbol) != "none"
            )
        ):
            raise _UnsupportedChart("unsupported-stock-chart")
        smooth = series_node.find("c:smooth", C_NS)
        if smooth is not None:
            if set(smooth.attrib) != {"val"} or list(smooth):
                raise _UnsupportedChart("unsupported-stock-chart")
            if _strict_axis_bool(smooth, False):
                raise _UnsupportedChart("unsupported-stock-chart")
        for child_name in ("idx", "order"):
            child = series_node.find(f"c:{child_name}", C_NS)
            if child is None or set(child.attrib) != {"val"} or list(child):
                raise _UnsupportedChart("unsupported-stock-chart")


def _stock_payload(
    chart_root: ET.Element,
    plot_area: ET.Element,
    chart: ET.Element,
    xfrm: Xfrm,
    *,
    palette: ColorPalette | None,
) -> tuple[dict[str, Any], list[SeriesVisualStyle]]:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .chart_to_svg import (
        C_NS,
        _UnsupportedChart,
        _apply_chart_metadata,
        _bounds_payload,
        _chart_visual_styles,
        _element_val,
        _numeric_category_cache_format,
        _numeric_values,
        _series_name,
        _single_axis_contract,
        _validate_stock_semantics,
    )
    series_nodes = chart.findall("c:ser", C_NS)
    if len(series_nodes) != 4:
        raise _UnsupportedChart("unsupported-stock-chart")
    categories = _numeric_values(series_nodes[0].find("c:cat", C_NS))
    if not categories:
        raise _UnsupportedChart("unsupported-chart-cache")
    series: list[dict[str, Any]] = []
    for index, series_node in enumerate(series_nodes, start=1):
        expected_index = str(index - 1)
        if (
            _element_val(series_node.find("c:idx", C_NS)) != expected_index
            or _element_val(series_node.find("c:order", C_NS)) != expected_index
        ):
            raise _UnsupportedChart("unsupported-stock-chart")
        if _numeric_values(series_node.find("c:cat", C_NS)) != categories:
            raise _UnsupportedChart("unsupported-chart-cache")
        values = _numeric_values(series_node.find("c:val", C_NS))
        if len(values) != len(categories):
            raise _UnsupportedChart("unsupported-chart-cache")
        series.append({
            "name": _series_name(series_node, index),
            "values": values,
        })
    axes = _single_axis_contract(
        plot_area,
        chart,
        category_kind="date",
        cross_between="between",
    )
    expected_category_format = axes["category"].get(
        "number_format",
        "m/d/yyyy",
    )
    category_cache_format = _numeric_category_cache_format(chart)
    if category_cache_format not in {"General", expected_category_format}:
        raise _UnsupportedChart("unsupported-chart-category-format")
    _validate_stock_semantics(plot_area, chart)
    payload: dict[str, Any] = {
        **_bounds_payload(xfrm),
        "axes": axes,
        "categories": categories,
        "series": series,
        "type": "stock",
    }
    visual_styles = _chart_visual_styles(payload, chart, palette)
    _apply_chart_metadata(
        payload,
        chart_root,
        plot_area,
        chart,
        palette=palette,
    )
    return payload, visual_styles


