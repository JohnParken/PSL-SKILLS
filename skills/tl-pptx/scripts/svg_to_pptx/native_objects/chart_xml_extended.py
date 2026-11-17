"""Classic native chart XML emitters."""

from __future__ import annotations

from typing import Any
from xml.etree import ElementTree as ET

from .chart_style import (
    _axis_title_xml,
    _chart_line_sp_pr_xml,
    _inferred_bar_gap_width,
    _inferred_bar_overlap,
    _inferred_cross_between,
)
from .marker_common import (
    _bool_attr,
    _excel_col,
    _first_present,
)



# Adapted from chart_xml.py; upstream MIT, Hugo He, 2025-2026.
def _axis_xml(
    cat_ax_id: str,
    val_ax_id: str,
    *,
    axis_font_size: int,
    axis_title_font_size: int,
    axis_titles: dict[str, Any],
    chart_style: dict[str, str | None],
    chart_type: str,
    grouping: str | None = None,
    show_value_axis_labels: bool = True,
    axes: dict[str, dict[str, Any]] | None = None,
) -> str:
    # Resolve shared symbols at call time; retain the public module contract.
    from .chart_xml import (
        _axis_pair_xml,
    )
    return _axis_pair_xml(
        cat_ax_id,
        val_ax_id,
        axis_font_size=axis_font_size,
        axis_title_font_size=axis_title_font_size,
        axis_titles=axis_titles,
        chart_style=chart_style,
        chart_type=chart_type,
        grouping=grouping,
        show_value_axis_labels=show_value_axis_labels,
        axes=axes or {},
        secondary=False,
    )


def _xy_axis_xml(
    x_ax_id: str,
    y_ax_id: str,
    *,
    axis_font_size: int,
    axis_title_font_size: int,
    axis_titles: dict[str, Any],
    chart_style: dict[str, str | None],
    axes: dict[str, dict[str, Any]] | None = None,
) -> str:
    # Resolve shared symbols at call time; retain the public module contract.
    from .chart_xml import (
        _axis_label_position,
        _axis_major_gridlines_xml,
        _axis_number_format_xml,
        _axis_position,
        _axis_scaling_xml,
        _axis_tx_pr_xml,
    )
    primary_language = chart_style.get("primary_language")
    normalized_axes = axes or {}
    x_axis = normalized_axes.get("x", {})
    y_axis = normalized_axes.get("y", {})
    axis_sp_pr = _chart_line_sp_pr_xml(chart_style.get("axis_color"))
    x_title_xml = _axis_title_xml(
        _first_present(axis_titles.get("x"), axis_titles.get("category")),
        font_size=axis_title_font_size,
        color=chart_style.get("text_color"),
        font_face=chart_style.get("font_face"),
        primary_language=primary_language,
    )
    y_title_xml = _axis_title_xml(
        _first_present(axis_titles.get("y"), axis_titles.get("value")),
        font_size=axis_title_font_size,
        color=chart_style.get("text_color"),
        font_face=chart_style.get("font_face"),
        primary_language=primary_language,
    )

    def value_axis_xml(
        axis_id: str,
        cross_axis_id: str,
        config: dict[str, Any],
        *,
        default_position: str,
        default_gridlines: bool,
        title_xml: str,
    ) -> str:
        delete = _bool_attr(not config.get("visible", True))
        position = _axis_position(config, default_position)
        gridlines = _axis_major_gridlines_xml(
            config,
            default=default_gridlines,
            color=chart_style.get("grid_color"),
        )
        number_format = _axis_number_format_xml(config)
        tick_label_position = _axis_label_position(config, "nextTo")
        major_unit = (
            f'<c:majorUnit val="{config["major_unit"]}"/>'
            if config.get("major_unit") is not None else ""
        )
        return (
            "<c:valAx>"
            f'<c:axId val="{axis_id}"/>{_axis_scaling_xml(config)}'
            f'<c:delete val="{delete}"/><c:axPos val="{position}"/>'
            f"{gridlines}{title_xml}{number_format}"
            f'<c:majorTickMark val="{config.get("tick_marks", "none")}"/><c:minorTickMark val="none"/>'
            f'<c:tickLblPos val="{tick_label_position}"/>'
            f"{axis_sp_pr}{_axis_tx_pr_xml(config, axis_font_size, chart_style)}"
            f'<c:crossAx val="{cross_axis_id}"/><c:crosses val="autoZero"/>'
            f'<c:crossBetween val="midCat"/>{major_unit}'
            "</c:valAx>"
        )

    return value_axis_xml(
        x_ax_id,
        y_ax_id,
        x_axis,
        default_position="b",
        default_gridlines=False,
        title_xml=x_title_xml,
    ) + value_axis_xml(
        y_ax_id,
        x_ax_id,
        y_axis,
        default_position="l",
        default_gridlines=True,
        title_xml=y_title_xml,
    )


def _stock_series_xml(
    categories: list[int | float],
    series: list[dict[str, Any]],
    *,
    colors: list[str],
    category_number_format: str | None = None,
) -> str:
    # Resolve shared symbols at call time; retain the public module contract.
    from .chart_xml import (
        _number_cache,
        _string_cache,
    )
    parts: list[str] = []
    for index, item in enumerate(series):
        column_index = index + 2
        parts.append(
            "<c:ser>"
            f'<c:idx val="{index}"/><c:order val="{index}"/>'
            "<c:tx><c:strRef>"
            f"<c:f>Sheet1!${_excel_col(column_index)}$1</c:f>"
            f"{_string_cache([str(item['name'])])}"
            "</c:strRef></c:tx>"
            '<c:spPr><a:ln><a:noFill/></a:ln></c:spPr>'
            '<c:marker><c:symbol val="none"/></c:marker>'
            "<c:cat><c:numRef>"
            f"<c:f>Sheet1!$A$2:$A${len(categories) + 1}</c:f>"
            f"{_number_cache(categories, category_number_format or 'General')}"
            "</c:numRef></c:cat>"
            "<c:val><c:numRef>"
            f"<c:f>Sheet1!${_excel_col(column_index)}$2:${_excel_col(column_index)}${len(categories) + 1}</c:f>"
            f"{_number_cache(item['values'])}"
            "</c:numRef></c:val>"
            '<c:smooth val="0"/>'
            "</c:ser>"
        )
    return "".join(parts)


def _stock_axis_xml(
    cat_ax_id: str,
    val_ax_id: str,
    *,
    axis_font_size: int,
    axis_title_font_size: int,
    axis_titles: dict[str, Any],
    chart_style: dict[str, str | None],
    axes: dict[str, dict[str, Any]] | None = None,
) -> str:
    # Resolve shared symbols at call time; retain the public module contract.
    from .chart_xml import (
        _axis_pair_xml,
    )
    normalized_axes = dict(axes or {})
    normalized_axes.setdefault("category", {"kind": "date", "position": "bottom"})
    return _axis_pair_xml(
        cat_ax_id,
        val_ax_id,
        axis_font_size=axis_font_size,
        axis_title_font_size=axis_title_font_size,
        axis_titles=axis_titles,
        chart_style=chart_style,
        chart_type="stock",
        grouping=None,
        show_value_axis_labels=True,
        axes=normalized_axes,
        secondary=False,
    )


def _chart_data_with_fallback_geometry(
    elem: ET.Element,
    chart_data: dict[str, Any],
) -> dict[str, Any]:
    """SVG-first only: fill bar spacing and tick crossing from the fallback drawing."""
    if chart_data.get("gap_width") is None:
        inferred_gap_width = _inferred_bar_gap_width(elem, chart_data)
        if inferred_gap_width is not None:
            chart_data = {**chart_data, "gap_width": inferred_gap_width}
    if chart_data.get("overlap") is None:
        inferred_overlap = _inferred_bar_overlap(elem, chart_data)
        if inferred_overlap is not None:
            chart_data = {**chart_data, "overlap": inferred_overlap}
    axes = chart_data.get("axes") or {}
    value_axis = axes.get("value") or {}
    if value_axis.get("cross_between") is None:
        inferred_cross_between = _inferred_cross_between(elem, chart_data)
        if inferred_cross_between is not None:
            chart_data = {
                **chart_data,
                "axes": {
                    **axes,
                    "value": {**value_axis, "cross_between": inferred_cross_between},
                },
            }
    return chart_data
