# Adapted from PPT Master 5e8856b (MIT); see assets/licenses/ppt_master.txt.
"""Chart data combo implementation."""
from __future__ import annotations

from .chart_data_shared import (
    Any,
    _compact_key,
    _first_present,
)

def _combo_axis_name(plot_payload: dict[str, Any]) -> str:
    axis = plot_payload.get("axis") or plot_payload.get("value_axis")
    if axis is None and plot_payload.get("secondary_axis"):
        axis = "secondary"
    axis_key = _compact_key(axis or "primary")
    aliases = {
        "left": "primary",
        "primary": "primary",
        "right": "secondary",
        "secondary": "secondary",
        "secondaryaxis": "secondary",
    }
    normalized = aliases.get(axis_key)
    if not normalized:
        raise RuntimeError("Native PPTX combo plot axis must be primary or secondary")
    return normalized


def _combo_plot_type(plot_payload: dict[str, Any]) -> tuple[str, str | None, str | None]:
    from .chart_data_category import _chart_kind
    chart_type, alias_grouping, alias_style = _chart_kind(plot_payload)
    if chart_type not in {"area", "column", "line"}:
        raise RuntimeError("Native PPTX combo plots support column, line, and area only")
    has_area_fill = bool(_first_present(plot_payload.get("area_fill"), plot_payload.get("areaFill")))
    if chart_type == "line" and has_area_fill:
        chart_type = "area"
    return chart_type, alias_grouping, alias_style


def _plot_series_area_style(plot_payload: dict[str, Any]) -> bool:
    from .chart_data_labels import _chart_list
    for item in _chart_list(plot_payload.get("series", []), "series"):
        if not isinstance(item, dict):
            continue
        if _first_present(
            item.get("fill_opacity"),
            item.get("fillOpacity"),
        ) is not None:
            return True
    return False


def _combo_series_indices(
    plot_payload: dict[str, Any],
    series_count: int,
) -> list[int] | None:
    from .chart_data_labels import _chart_list
    raw_indices = plot_payload.get("series_indices")
    if raw_indices is None:
        return None
    indices: list[int] = []
    for value in _chart_list(raw_indices, "plots[].series_indices"):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise RuntimeError(
                "Native PPTX combo series_indices must contain non-negative integers"
            )
        indices.append(value)
    if len(indices) != series_count or len(set(indices)) != len(indices):
        raise RuntimeError(
            "Native PPTX combo series_indices must be unique and match series length"
        )
    return indices


def _combo_plot_entry(
    plot_payload: dict[str, Any],
    categories: list[Any],
    *,
    category_is_numeric: bool,
    axes: dict[str, dict[str, Any]],
    fallback_series: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    from .chart_data_labels import _chart_data_labels, _chart_list, _chart_number
    from .chart_data_category import _category_series, _chart_grouping, _gap_width, _line_style, _marker_size, _overlap
    chart_type, alias_grouping, alias_style = _combo_plot_type(plot_payload)
    if chart_type == "line" and _plot_series_area_style(plot_payload):
        raise RuntimeError(
            "Native PPTX combo line plot with series fill_opacity requires area_fill: true"
        )
    axis = _combo_axis_name(plot_payload)
    category_role = "secondary_category" if axis == "secondary" else "category"
    axis_is_date = axes.get(category_role, {}).get("kind") == "date"
    raw_numeric = plot_payload.get("category_numeric")
    if raw_numeric is not None and not isinstance(raw_numeric, bool):
        raise RuntimeError(
            "Native PPTX combo plot category_numeric must be a boolean"
        )
    if axis_is_date and raw_numeric is False:
        raise RuntimeError(
            "Native PPTX combo date-axis categories must remain numeric"
        )
    plot_category_is_numeric = axis_is_date or (
        raw_numeric if raw_numeric is not None else category_is_numeric
    )
    raw_plot_categories = plot_payload.get("categories")
    category_items = (
        categories
        if raw_plot_categories is None
        else _chart_list(raw_plot_categories, "plots[].categories")
    )
    plot_categories = (
        [_chart_number(item) for item in category_items]
        if plot_category_is_numeric
        else [str(item) for item in category_items]
    )
    if not plot_categories:
        raise RuntimeError("Native PPTX combo plot categories must be non-empty")
    grouping = (
        _chart_grouping(chart_type, plot_payload, alias_grouping)
        if chart_type in {"area", "column", "line"}
        else None
    )
    plot_series = fallback_series or _category_series(
        plot_payload,
        plot_categories,
        chart_type=chart_type,
        grouping=grouping,
    )
    entry: dict[str, Any] = {
        "axis": axis,
        "categories": plot_categories,
        "category_is_numeric": plot_category_is_numeric,
        "data_labels": _chart_data_labels(
            plot_payload,
            chart_type,
            grouping,
            len(plot_categories),
        ),
        "grouping": grouping,
        "series": plot_series,
        "type": chart_type,
    }
    series_indices = _combo_series_indices(plot_payload, len(plot_series))
    if series_indices is not None:
        entry["series_indices"] = series_indices
    if chart_type == "line":
        entry["line_style"] = _line_style(plot_payload, alias_style)
        entry["marker_size"] = _marker_size(plot_payload, chart_type)
    if chart_type == "column":
        entry["gap_width"] = _gap_width(plot_payload, chart_type)
        entry["overlap"] = _overlap(plot_payload, chart_type)
    return entry


def _combo_chart_data(payload: dict[str, Any]) -> dict[str, Any]:
    from .chart_data_labels import _chart_list, _chart_number
    from .chart_data_axes import _category_axis_is_date, _chart_axes
    from .chart_data_category import _category_series, _chart_grouping
    axes = _chart_axes(
        payload,
        {"category", "secondary_category", "secondary_value", "value"},
    )
    raw_category_numeric = payload.get("category_numeric")
    if raw_category_numeric is not None and not isinstance(raw_category_numeric, bool):
        raise RuntimeError("Native PPTX combo category_numeric must be a boolean")
    primary_axis_is_date = _category_axis_is_date(axes)
    if primary_axis_is_date and raw_category_numeric is False:
        raise RuntimeError("Native PPTX combo date-axis categories must remain numeric")
    category_is_numeric = primary_axis_is_date or raw_category_numeric is True
    raw_categories = _chart_list(payload.get("categories", []), "categories")
    categories = (
        [_chart_number(item) for item in raw_categories]
        if category_is_numeric
        else [str(item) for item in raw_categories]
    )
    if not categories:
        raise RuntimeError("Native PPTX combo chart categories must be non-empty")
    raw_plots = payload.get("plots", payload.get("chart_plots"))
    plots: list[dict[str, Any]] = []

    if raw_plots is not None:
        for item in _chart_list(raw_plots, "plots"):
            if not isinstance(item, dict):
                raise RuntimeError("Native PPTX combo plots must be objects")
            plots.append(_combo_plot_entry(
                item,
                categories,
                category_is_numeric=category_is_numeric,
                axes=axes,
            ))
    else:
        raw_series = _chart_list(payload.get("series", []), "series")
        if not raw_series:
            raise RuntimeError("Native PPTX combo chart requires plots or typed series")
        for idx, item in enumerate(raw_series, start=1):
            if not isinstance(item, dict):
                raise RuntimeError("Native PPTX chart series entries must be objects")
            if not (item.get("type") or item.get("chart_type")):
                raise RuntimeError("Native PPTX combo series entries require type")
            if any(
                field in item
                for field in ("categories", "category_numeric", "series_indices")
            ):
                raise RuntimeError(
                    "Native PPTX combo typed series with plot-scoped metadata "
                    "must use plots"
                )
            typed_chart_type, typed_grouping_alias, _typed_style = _combo_plot_type(item)
            typed_grouping = (
                _chart_grouping(typed_chart_type, item, typed_grouping_alias)
                if typed_chart_type in {"area", "column", "line"}
                else None
            )
            one_series = _category_series(
                {"series": [item]},
                categories,
                chart_type=typed_chart_type,
                grouping=typed_grouping,
                typed_combo=True,
            )
            plot = _combo_plot_entry(
                item,
                categories,
                category_is_numeric=category_is_numeric,
                axes=axes,
                fallback_series=one_series,
            )
            signature = (
                plot["axis"],
                plot.get("grouping"),
                plot.get("line_style"),
                plot["type"],
            )
            previous = plots[-1] if plots else None
            previous_signature = (
                previous.get("axis"),
                previous.get("grouping"),
                previous.get("line_style"),
                previous.get("type"),
            ) if previous else None
            if (
                previous is not None
                and signature == previous_signature
                and plot.get("data_labels") == previous.get("data_labels")
            ):
                previous["series"].extend(plot["series"])
            else:
                plots.append(plot)

    if not plots:
        raise RuntimeError("Native PPTX combo chart requires at least one plot")
    if not any(plot["axis"] == "primary" for plot in plots):
        raise RuntimeError("Native PPTX combo chart requires a primary-axis plot")
    has_secondary_plot = any(plot["axis"] == "secondary" for plot in plots)
    if not has_secondary_plot and {
        "secondary_category", "secondary_value",
    }.intersection(axes):
        raise RuntimeError(
            "Native PPTX combo secondary axes require a secondary-axis plot"
        )
    series_index_groups = [plot.get("series_indices") for plot in plots]
    if any(group is not None for group in series_index_groups):
        if any(group is None for group in series_index_groups):
            raise RuntimeError(
                "Native PPTX combo series_indices must cover every plot"
            )
        flat_indices = [
            index
            for group in series_index_groups
            for index in group
        ]
        if sorted(flat_indices) != list(range(len(flat_indices))):
            raise RuntimeError(
                "Native PPTX combo series_indices must form one contiguous range"
            )
    flat_series: list[dict[str, Any]] = []
    independent_categories = any(
        plot["categories"] != categories
        or plot["category_is_numeric"] != category_is_numeric
        for plot in plots
    )
    next_column = 1
    for plot in plots:
        plot["start_index"] = len(flat_series)
        if independent_categories:
            plot["category_column"] = next_column
            plot["start_column"] = next_column + 1
            next_column += len(plot["series"]) + 1
        flat_series.extend(plot["series"])
    if not flat_series:
        raise RuntimeError("Native PPTX combo chart requires at least one series")

    return {
        "axes": axes,
        "categories": categories,
        "category_is_numeric": category_is_numeric,
        "independent_categories": independent_categories,
        "kind": "combo",
        "plots": plots,
        "series": flat_series,
        "type": "combo",
    }
