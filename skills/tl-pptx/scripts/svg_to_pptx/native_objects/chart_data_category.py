"""Native chart metadata normalization."""

from __future__ import annotations

from typing import Any


from .marker_common import (
    _chart_bool,
    _compact_key,
    _first_present,
    _number,
    _powerpoint_line_width_emu,
)



# Adapted from chart_data.py; upstream MIT, Hugo He, 2025-2026.
def _category_series(
    payload: dict[str, Any],
    categories: list[Any],
    *,
    chart_type: str,
    grouping: str | None,
    typed_combo: bool = False,
) -> list[dict[str, Any]]:
    # Resolve shared symbols at call time; retain the public module contract.
    from .chart_data import (
        _chart_data_labels,
        _chart_list,
        _chart_point_value,
        _marker_size,
        _point_color,
    )
    raw_series = payload.get("series", [])
    if not categories or not isinstance(raw_series, list) or not raw_series:
        raise RuntimeError("Native PPTX chart requires non-empty categories and series")
    root_point_colors = _first_present(
        payload.get("point_colors"),
        payload.get("pointColors"),
    )
    if root_point_colors is not None and len(raw_series) != 1:
        raise RuntimeError("Native PPTX chart root point_colors is only valid for one series")

    series: list[dict[str, Any]] = []
    for idx, item in enumerate(raw_series, start=1):
        if not isinstance(item, dict):
            raise RuntimeError("Native PPTX chart series entries must be objects")
        if (
            _first_present(item.get("line_style"), item.get("lineStyle")) is not None
            and not typed_combo
        ):
            raise RuntimeError(
                "Native PPTX chart series[].line_style is not a series option; "
                "set line_style on the chart root (combo: on the plot or typed series)"
            )
        values = [
            _chart_point_value(value)
            for value in _chart_list(item.get("values", []), "series[].values")
        ]
        if len(values) != len(categories):
            raise RuntimeError("Native PPTX chart series values must match categories length")
        if all(value is None for value in values):
            raise RuntimeError("Native PPTX chart series values must contain a number")
        if chart_type not in {"area", "bar", "column", "line"} and None in values:
            raise RuntimeError(
                f"Native PPTX {chart_type} chart series values cannot contain null gaps"
            )
        raw_point_colors = _first_present(
            item.get("point_colors"),
            item.get("pointColors"),
            root_point_colors if idx == 1 else None,
        )
        point_colors = [
            _point_color(color, chart_type)
            for color in _chart_list(raw_point_colors, "series[].point_colors")
        ]
        if point_colors and len(point_colors) != len(values):
            raise RuntimeError("Native PPTX chart series point_colors must match values length")
        series_item = {"name": str(item.get("name") or f"Series {idx}"), "values": values}
        if point_colors:
            series_item["point_colors"] = point_colors
        marker_size = _marker_size(item, chart_type)
        if marker_size is not None:
            series_item["marker_size"] = marker_size
        fill_opacity = _first_present(
            item.get("fill_opacity"),
            item.get("fillOpacity"),
        )
        if fill_opacity is not None:
            fill_opacity = _number(fill_opacity, "series fill_opacity")
            if not 0 <= fill_opacity <= 1:
                raise RuntimeError(
                    "Native PPTX chart series fill_opacity must be between 0 and 1"
                )
            series_item["fill_opacity"] = fill_opacity
        line_width = _first_present(
            item.get("line_width"),
            item.get("lineWidth"),
        )
        if line_width is not None:
            line_width = _number(line_width, "series line_width")
            if line_width <= 0:
                raise RuntimeError("Native PPTX chart series line_width must be positive")
            _powerpoint_line_width_emu(line_width, "series line_width")
            series_item["line_width"] = line_width
        data_labels = _chart_data_labels(
            item,
            chart_type,
            grouping,
            len(categories),
        )
        if data_labels is not None:
            series_item["data_labels"] = data_labels
        series.append(series_item)
    return series


def _category_chart_data(
    payload: dict[str, Any],
    chart_type: str,
    alias_grouping: str | None,
    alias_style: str | None,
) -> dict[str, Any]:
    # Resolve shared symbols at call time; retain the public module contract.
    from .chart_data import (
        _category_axis_is_date,
        _chart_axes,
        _chart_data_labels,
        _chart_grouping,
        _chart_list,
        _chart_number,
        _doughnut_hole_size,
        _gap_width,
        _line_style,
        _marker_size,
        _overlap,
        _radar_style,
    )
    axes = _chart_axes(
        payload,
        {"category", "value"},
        bar_orientation=chart_type == "bar",
    )
    if axes and chart_type in {"doughnut", "of_pie", "pie"}:
        raise RuntimeError(
            f"Native PPTX {chart_type} chart axes are outside current support"
        )
    if _category_axis_is_date(axes) and chart_type != "area":
        raise RuntimeError(
            "Native PPTX date category axes are currently supported for area charts only"
        )
    raw_categories = _chart_list(payload.get("categories", []), "categories")
    categories = (
        [_chart_number(item) for item in raw_categories]
        if _category_axis_is_date(axes)
        else [str(item) for item in raw_categories]
    )
    style = payload.get("style") if isinstance(payload.get("style"), dict) else {}

    grouping = (
        _chart_grouping(chart_type, payload, alias_grouping)
        if chart_type in {"bar", "column", "line", "area"}
        else None
    )
    series = _category_series(
        payload,
        categories,
        chart_type=chart_type,
        grouping=grouping,
    )
    if chart_type in {"doughnut", "of_pie", "pie"}:
        if len(series) != 1:
            raise RuntimeError("Native PPTX pie-family charts support exactly one series")

    of_pie_type = None
    if chart_type == "of_pie":
        raw_of_pie_type = (
            payload.get("of_pie_type")
            or payload.get("ofPieType")
            or payload.get("secondary_type")
            or alias_style
            or "pie"
        )
        of_pie_aliases = {
            "bar": "bar",
            "barofpie": "bar",
            "pie": "pie",
            "pieofpie": "pie",
        }
        of_pie_type = of_pie_aliases.get(_compact_key(raw_of_pie_type))
        if not of_pie_type:
            raise RuntimeError("Native PPTX of_pie_type must be one of: bar, pie")

    line_style = _line_style(payload, alias_style) if chart_type == "line" else None
    radar_style = None
    radar_marker_style = None
    if chart_type == "radar":
        radar_style, radar_marker_style = _radar_style(payload, alias_style)

    if alias_style == "exploded" or payload.get("exploded"):
        raise RuntimeError("Native PPTX exploded pie/doughnut is outside current basic chart support")

    return {
        "kind": "category",
        "type": chart_type,
        "categories": categories,
        "grouping": grouping,
        "of_pie_type": of_pie_type,
        "hole_size": _doughnut_hole_size(payload, chart_type),
        "line_style": line_style,
        "marker_size": _marker_size(payload, chart_type),
        "gap_width": _gap_width(payload, chart_type),
        "overlap": _overlap(payload, chart_type),
        "radar_marker_style": radar_marker_style,
        "radar_style": radar_style,
        "show_value_axis_labels": _chart_bool(
            _first_present(
                payload.get("show_value_axis_labels"),
                payload.get("showValueAxisLabels"),
                style.get("show_value_axis_labels"),
                style.get("showValueAxisLabels"),
            ),
            True,
        ),
        "data_labels": _chart_data_labels(
            payload,
            chart_type,
            grouping,
            len(categories),
        ),
        "axes": axes,
        "series": series,
    }
