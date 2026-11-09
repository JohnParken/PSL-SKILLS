# Adapted from PPT Master 5e8856b (MIT); see assets/licenses/ppt_master.txt.
"""Chart data dispatch implementation."""
from __future__ import annotations

from .chart_data_shared import (
    Any,
    _CATEGORY_CHART_TYPES,
    _CHARTEX_CHART_TYPES,
    _XY_CHART_TYPES,
)

def _chart_data(payload: dict[str, Any]) -> dict[str, Any]:
    from .chart_data_labels import _data_labels_config
    from .chart_data_axes import _chart_plot_area
    from .chart_data_category import _category_chart_data, _chart_kind
    from .chart_data_combo import _combo_chart_data
    from .chart_data_special import _chart_language, _chartex_chart_data, _stock_chart_data, _xy_chart_data
    chart_type, alias_grouping, alias_style = _chart_kind(payload)
    if _chart_language(payload) is not None and chart_type in _CHARTEX_CHART_TYPES:
        raise RuntimeError("Native PPTX chart lang is supported for classic charts only")
    if payload.get("hole_size") is not None and chart_type != "doughnut":
        raise RuntimeError(
            "Native PPTX chart hole_size is supported for doughnut charts only"
        )
    plot_area = _chart_plot_area(payload)
    if plot_area is not None and chart_type in _CHARTEX_CHART_TYPES:
        raise RuntimeError(
            "Native PPTX chart plot_area is supported for classic charts only"
        )
    if payload.get("axes") is not None and chart_type in _CHARTEX_CHART_TYPES:
        raise RuntimeError(
            "Native PPTX chart axes are supported for classic charts only; "
            f"a ChartEx {chart_type} chart draws its own axes and shows values "
            "through companion text"
        )
    if (
        chart_type not in _CATEGORY_CHART_TYPES | {"combo", "stock"} | _XY_CHART_TYPES
        and _data_labels_config(payload) is not None
    ):
        raise RuntimeError(
            f"Native PPTX {chart_type} chart data labels are outside current support"
        )
    if chart_type == "combo":
        chart_data = _combo_chart_data(payload)
        chart_data["plot_area"] = plot_area
        return chart_data
    if chart_type in _CHARTEX_CHART_TYPES:
        return _chartex_chart_data(payload, chart_type)
    if chart_type == "stock":
        chart_data = _stock_chart_data(payload)
        chart_data["plot_area"] = plot_area
        return chart_data
    if chart_type in _XY_CHART_TYPES:
        chart_data = _xy_chart_data(payload, chart_type, alias_style)
        chart_data["plot_area"] = plot_area
        return chart_data
    chart_data = _category_chart_data(
        payload,
        chart_type,
        alias_grouping,
        alias_style,
    )
    chart_data["plot_area"] = plot_area
    return chart_data


def validate_chart_payload(payload: dict[str, Any]) -> None:
    """Check a native chart payload against the export schema.

    Public contract for the pptx_to_svg importer: raises RuntimeError on any
    payload the native chart exporter cannot represent.
    """
    _chart_data(payload)


def validate_data_label_position(
    value: Any,
    chart_type: str,
    grouping: str | None,
) -> None:
    """Check a data-label position against the export schema.

    Public contract for the pptx_to_svg importer: raises RuntimeError when the
    position is not representable for the given plot.
    """
    from .chart_data_labels import _data_label_position
    _data_label_position(value, chart_type, grouping)
