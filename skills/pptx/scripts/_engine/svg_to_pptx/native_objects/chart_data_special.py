# Adapted from PPT Master 5e8856b (MIT); see assets/licenses/ppt_master.txt.
"""Chart data special implementation."""
from __future__ import annotations

from .chart_data_shared import (
    Any,
    LanguageTagError,
    _compact_key,
    normalize_language_tag,
)

def _chart_values(payload: dict[str, Any], field_name: str = "values") -> list[int | float]:
    from .chart_data_labels import _chart_list, _chart_number
    raw_values = payload.get(field_name)
    if raw_values is None and isinstance(payload.get("series"), list) and payload["series"]:
        first_series = payload["series"][0]
        if isinstance(first_series, dict):
            raw_values = first_series.get("values")
    values = [_chart_number(value) for value in _chart_list(raw_values, field_name)]
    if not values:
        raise RuntimeError(f"Native PPTX chart {field_name} must be a non-empty list")
    return values


def _chart_categories(payload: dict[str, Any], count: int | None = None) -> list[str]:
    from .chart_data_labels import _chart_list
    raw_categories = payload.get("categories", payload.get("labels", []))
    categories = [str(item) for item in _chart_list(raw_categories, "categories")]
    if count is not None:
        if not categories:
            categories = [f"Category {idx + 1}" for idx in range(count)]
        if len(categories) != count:
            raise RuntimeError("Native PPTX chart categories length must match values length")
    elif not categories:
        raise RuntimeError("Native PPTX chart requires non-empty categories")
    return categories


def _hierarchy_levels(payload: dict[str, Any], count: int) -> list[list[str]]:
    from .chart_data_labels import _chart_list
    raw_levels = payload.get("levels")
    if raw_levels is not None:
        levels = [
            [str(value) for value in _chart_list(level, "levels[]")]
            for level in _chart_list(raw_levels, "levels")
        ]
    else:
        raw_categories = _chart_list(payload.get("categories", []), "categories")
        if raw_categories and all(isinstance(item, list) for item in raw_categories):
            path_rows = [[str(value) for value in item] for item in raw_categories]
        else:
            path_rows = [[str(item)] for item in raw_categories]
        if len(path_rows) != count:
            raise RuntimeError("Native PPTX hierarchical chart categories length must match values length")
        max_depth = max((len(row) for row in path_rows), default=0)
        levels = [
            [row[depth] if depth < len(row) else "" for row in path_rows]
            for depth in range(max_depth)
        ]

    if not levels:
        raise RuntimeError("Native PPTX hierarchical charts require levels or path categories")
    for level in levels:
        if len(level) != count:
            raise RuntimeError("Native PPTX hierarchical chart levels must match values length")
    return levels


def _treemap_parent_labels(payload: dict[str, Any]) -> str:
    raw = payload.get("parent_label_layout", payload.get("parent_labels", "overlapping"))
    aliases = {
        "banner": "banner",
        "none": "none",
        "overlapping": "overlapping",
    }
    layout = aliases.get(_compact_key(raw))
    if not layout:
        raise RuntimeError(
            "Native PPTX treemap parent_label_layout must be one of: banner, none, overlapping"
        )
    return layout


def _chartex_chart_data(payload: dict[str, Any], chart_type: str) -> dict[str, Any]:
    from .chart_data_labels import _chart_list, _chart_number
    if chart_type in {"sunburst", "treemap"}:
        values = _chart_values(payload)
        levels = _hierarchy_levels(payload, len(values))
        data = {
            "kind": "chartex",
            "levels": levels,
            "type": chart_type,
            "values": values,
        }
        if chart_type == "treemap":
            data["parent_labels"] = _treemap_parent_labels(payload)
        return data

    if chart_type == "histogram":
        return {
            "kind": "chartex",
            "type": chart_type,
            "values": _chart_values(payload),
        }

    if chart_type in {"funnel", "pareto", "waterfall"}:
        values = _chart_values(payload)
        data = {
            "categories": _chart_categories(payload, len(values)),
            "kind": "chartex",
            "type": chart_type,
            "values": values,
        }
        if chart_type == "waterfall":
            raw_subtotals = payload.get(
                "subtotals",
                payload.get("subtotal_indices", []),
            )
            subtotals: list[int] = []
            seen_subtotals: set[int] = set()
            for value in _chart_list(raw_subtotals, "subtotals"):
                index = _chart_number(value)
                if not isinstance(index, int):
                    raise RuntimeError("Native PPTX waterfall subtotal indices must be integers")
                if index < 0 or index >= len(values):
                    raise RuntimeError(
                        "Native PPTX waterfall subtotal index is outside point range"
                    )
                if index in seen_subtotals:
                    raise RuntimeError(
                        "Native PPTX waterfall subtotal indices must be unique"
                    )
                seen_subtotals.add(index)
                subtotals.append(index)
            data["subtotals"] = subtotals
        return data

    if chart_type == "box_whisker":
        raw_series = _chart_list(payload.get("series", []), "series")
        if not raw_series:
            raise RuntimeError("Native PPTX boxWhisker chart requires non-empty series")
        series: list[dict[str, Any]] = []
        for idx, item in enumerate(raw_series, start=1):
            if not isinstance(item, dict):
                raise RuntimeError("Native PPTX chart series entries must be objects")
            values = [_chart_number(value) for value in _chart_list(item.get("values", []), "series[].values")]
            if not values:
                raise RuntimeError("Native PPTX boxWhisker series values must be non-empty")
            categories = item.get("categories")
            if categories is None:
                categories = [str(item.get("name") or f"Series {idx}")] * len(values)
            categories_list = [str(value) for value in _chart_list(categories, "series[].categories")]
            if len(categories_list) != len(values):
                raise RuntimeError("Native PPTX boxWhisker series categories must match values length")
            series.append({
                "categories": categories_list,
                "name": str(item.get("name") or f"Series {idx}"),
                "values": values,
            })
        return {
            "kind": "chartex",
            "series": series,
            "type": chart_type,
        }

    raise RuntimeError(f"Native PPTX {chart_type} chart is outside current basic chart support")


def _stock_chart_data(payload: dict[str, Any]) -> dict[str, Any]:
    from .chart_data_labels import _chart_list, _chart_number, _data_labels_config
    from .chart_data_axes import _category_axis_is_date, _chart_axes
    from .chart_data_category import _category_series
    if _data_labels_config(payload) is not None:
        raise RuntimeError("Native PPTX stock chart data labels are outside current support")
    axes = _chart_axes(payload, {"category", "value"})
    if "category" in axes and not _category_axis_is_date(axes):
        raise RuntimeError("Native PPTX stock chart category axis must be date")
    categories = [
        _chart_number(item)
        for item in _chart_list(payload.get("categories", payload.get("dates", [])), "categories")
    ]
    if not categories:
        raise RuntimeError("Native PPTX stock chart requires non-empty categories or dates")

    raw_series = payload.get("series")
    if raw_series is None:
        field_names = [("open", "Open"), ("high", "High"), ("low", "Low"), ("close", "Close")]
        raw_series = [
            {"name": default_name, "values": payload.get(field_name, [])}
            for field_name, default_name in field_names
        ]
    series = _category_series(
        {"series": raw_series},
        categories,
        chart_type="stock",
        grouping=None,
    )
    if len(series) != 4:
        raise RuntimeError("Native PPTX stock chart requires exactly four series: open, high, low, close")
    return {
        "axes": axes,
        "categories": categories,
        "kind": "category",
        "series": series,
        "type": "stock",
    }


def _point_values(point: Any, *, chart_type: str) -> tuple[Any, Any, Any | None]:
    if isinstance(point, dict):
        return point.get("x"), point.get("y"), point.get("size", point.get("bubble_size"))
    if isinstance(point, (list, tuple)):
        if len(point) < 2:
            raise RuntimeError("Native PPTX XY chart points require x and y")
        size = point[2] if len(point) > 2 else None
        return point[0], point[1], size
    raise RuntimeError("Native PPTX XY chart points must be objects or arrays")


def _xy_chart_data(
    payload: dict[str, Any],
    chart_type: str,
    alias_style: str | None,
) -> dict[str, Any]:
    from .chart_data_labels import _chart_list, _chart_number, _data_labels_config
    from .chart_data_axes import _chart_axes
    if _data_labels_config(payload) is not None:
        raise RuntimeError(
            f"Native PPTX {chart_type} chart data labels are outside current support"
        )
    axes = _chart_axes(payload, {"x", "y"})
    raw_series = payload.get("series", [])
    if not isinstance(raw_series, list) or not raw_series:
        raise RuntimeError("Native PPTX XY chart requires non-empty series")

    series: list[dict[str, Any]] = []
    for idx, item in enumerate(raw_series, start=1):
        if not isinstance(item, dict):
            raise RuntimeError("Native PPTX chart series entries must be objects")

        if item.get("points") is not None:
            points = [
                _point_values(point, chart_type=chart_type)
                for point in _chart_list(item.get("points"), "series[].points")
            ]
            x_values = [_chart_number(point[0]) for point in points]
            y_values = [_chart_number(point[1]) for point in points]
            size_values = [_chart_number(point[2]) for point in points if point[2] is not None]
        else:
            x_raw = _chart_list(item.get("x", item.get("xs", [])), "series[].x")
            y_raw = _chart_list(
                item.get("y", item.get("ys", item.get("values", []))),
                "series[].y",
            )
            size_raw = _chart_list(
                item.get("size", item.get("sizes", item.get("bubble_size", []))),
                "series[].size",
            )
            x_values = [_chart_number(value) for value in x_raw]
            y_values = [_chart_number(value) for value in y_raw]
            size_values = [_chart_number(value) for value in size_raw]

        if not x_values or len(x_values) != len(y_values):
            raise RuntimeError("Native PPTX XY chart x/y values must be non-empty and same length")
        if chart_type == "bubble" and len(size_values) != len(x_values):
            raise RuntimeError("Native PPTX bubble chart requires one size per x/y value")

        series.append({
            "name": str(item.get("name") or f"Series {idx}"),
            "sizes": size_values,
            "x": x_values,
            "y": y_values,
        })

    scatter_style = _compact_key(payload.get("scatter_style") or alias_style or "marker")
    style_aliases = {
        "line": "line",
        "linemarker": "lineMarker",
        "markers": "marker",
        "marker": "marker",
        "smooth": "smooth",
        "smoothmarker": "smoothMarker",
    }
    if chart_type == "scatter" and scatter_style not in style_aliases:
        raise RuntimeError("Native PPTX scatter_style is unsupported")
    return {
        "axes": axes,
        "kind": "xy",
        "type": chart_type,
        "scatter_style": style_aliases.get(scatter_style, "marker"),
        "series": series,
    }


def _chart_language(payload: dict[str, Any]) -> str | None:
    if "lang" not in payload:
        return None
    try:
        return normalize_language_tag(payload["lang"])
    except LanguageTagError as exc:
        raise RuntimeError(f"Native PPTX chart lang: {exc}") from exc
