# Adapted from PPT Master 5e8856b (MIT); see assets/licenses/ppt_master.txt.
"""Chart xml plots implementation."""
from __future__ import annotations

from .chart_xml_shared import (
    Any,
    _DEFAULT_GAP_WIDTH,
    _category_axis_is_date,
    _data_labels_config,
)

def _combo_axis_grouping(plots: list[dict[str, Any]], axis: str) -> str | None:
    for plot in plots:
        if plot.get("axis") == axis and plot.get("grouping") == "percentStacked":
            return "percentStacked"
    return None


def _combo_plot_layer(plot: dict[str, Any]) -> int:
    return {
        "area": 0,
        "column": 1,
        "line": 2,
    }.get(str(plot.get("type")), 1)


def _combo_plot_xml(
    chart_data: dict[str, Any],
    colors: list[str],
    *,
    axis_font_size: int,
    axis_title_font_size: int,
    axis_titles: dict[str, Any],
    chart_style: dict[str, str | None],
) -> str:
    from .chart_xml_labels import _data_labels_xml, _series_scoped_data_labels
    from .chart_xml_series import _series_xml
    from .chart_xml_chrome import _bar_chart_group_xml, _line_area_chart_group_xml
    from .chart_xml_axes import _secondary_axis_xml
    from .chart_xml_special import _axis_xml
    axes = chart_data.get("axes") or {}
    primary_cat_ax_id = "2068027336"
    primary_val_ax_id = "2113994440"
    secondary_cat_ax_id = "2080229232"
    secondary_val_ax_id = "2098941040"
    parts: list[str] = []

    for plot in sorted(chart_data["plots"], key=_combo_plot_layer):
        categories = plot["categories"]
        category_is_numeric = bool(plot.get("category_is_numeric"))
        chart_type = plot["type"]
        axis = plot.get("axis", "primary")
        cat_ax_id = secondary_cat_ax_id if axis == "secondary" else primary_cat_ax_id
        val_ax_id = secondary_val_ax_id if axis == "secondary" else primary_val_ax_id
        category_role = "secondary_category" if axis == "secondary" else "category"
        category_number_format = axes.get(category_role, {}).get("number_format")
        start_index = int(plot.get("start_index", 0))
        grouping = plot.get("grouping") or ("clustered" if chart_type == "column" else "standard")
        ser_xml = _series_xml(
            categories,
            plot["series"],
            chart_type=chart_type,
            grouping=grouping,
            colors=colors,
            category_is_numeric=category_is_numeric,
            category_number_format=category_number_format,
            data_labels=_data_labels_config(plot),
            data_label_font_size=axis_font_size,
            data_label_color=chart_style.get("text_color"),
            data_label_font_face=chart_style.get("font_face"),
            language=chart_style.get("primary_language"),
            line_style=plot.get("line_style", "line"),
            marker_size=plot.get("marker_size"),
            category_column=int(plot.get("category_column", 1)),
            color_start_index=start_index,
            series_indices=plot.get("series_indices"),
            start_column=int(plot.get("start_column", 2 + start_index)),
            start_index=start_index,
        )
        data_labels = _data_labels_config(plot)
        data_labels_xml = (
            _data_labels_xml(
                data_labels,
                chart_type=chart_type,
                grouping=grouping,
                point_count=len(plot["series"][0]["values"]),
                font_size=axis_font_size,
                default_color=chart_style.get("text_color"),
                default_font_face=chart_style.get("font_face"),
                language=chart_style.get("primary_language"),
            )
            if not _series_scoped_data_labels(data_labels)
            else ""
        )
        if chart_type == "column":
            parts.append(_bar_chart_group_xml(
                chart_type,
                grouping,
                ser_xml,
                cat_ax_id=cat_ax_id,
                val_ax_id=val_ax_id,
                vary_colors=any(item.get("point_colors") for item in plot["series"]),
                data_labels_xml=data_labels_xml,
                gap_width=plot.get("gap_width"),
                overlap=plot.get("overlap"),
            ))
        elif chart_type in {"area", "line"}:
            parts.append(_line_area_chart_group_xml(
                chart_type,
                grouping,
                ser_xml,
                cat_ax_id=cat_ax_id,
                val_ax_id=val_ax_id,
                data_labels_xml=data_labels_xml,
            ))
        else:
            raise RuntimeError("Native PPTX combo plots support column, line, and area only")

    has_secondary_axis = any(plot.get("axis") == "secondary" for plot in chart_data["plots"])
    axes_xml = _axis_xml(
        primary_cat_ax_id,
        primary_val_ax_id,
        axis_font_size=axis_font_size,
        axis_title_font_size=axis_title_font_size,
        axis_titles=axis_titles,
        chart_style=chart_style,
        chart_type="combo",
        grouping=_combo_axis_grouping(chart_data["plots"], "primary"),
        axes=axes,
    )
    if has_secondary_axis:
        axes_xml += _secondary_axis_xml(
            secondary_cat_ax_id,
            secondary_val_ax_id,
            axis_font_size=axis_font_size,
            axis_title_font_size=axis_title_font_size,
            axis_titles=axis_titles,
            chart_style=chart_style,
            grouping=_combo_axis_grouping(chart_data["plots"], "secondary"),
            axes=axes,
        )
    return "".join(parts) + axes_xml


def _chart_plot_xml(
    chart_data: dict[str, Any],
    colors: list[str],
    *,
    axis_font_size: int,
    axis_title_font_size: int,
    axis_titles: dict[str, Any],
    chart_style: dict[str, str | None],
) -> str:
    from .chart_xml_labels import _data_labels_xml, _series_scoped_data_labels
    from .chart_xml_series import _series_xml
    from .chart_xml_chrome import _bar_overlap_xml, _xy_series_xml
    from .chart_xml_special import _axis_xml, _stock_axis_xml, _stock_series_xml, _xy_axis_xml
    chart_type = chart_data["type"]
    cat_ax_id = "2068027336"
    val_ax_id = "2113994440"
    if chart_data["kind"] == "combo":
        return _combo_plot_xml(
            chart_data,
            colors,
            axis_font_size=axis_font_size,
            axis_title_font_size=axis_title_font_size,
            axis_titles=axis_titles,
            chart_style=chart_style,
        )
    if chart_data["kind"] == "xy":
        x_ax_id = "2080229232"
        y_ax_id = "2098941040"
        ser_xml = _xy_series_xml(
            chart_data["series"],
            chart_type=chart_type,
            colors=colors,
            scatter_style=chart_data.get("scatter_style", "lineMarker"),
        )
        if chart_type == "scatter":
            scatter_style = chart_data.get("scatter_style", "lineMarker")
            axes_xml = _xy_axis_xml(
                x_ax_id,
                y_ax_id,
                axis_font_size=axis_font_size,
                axis_title_font_size=axis_title_font_size,
                axis_titles=axis_titles,
                chart_style=chart_style,
                axes=chart_data.get("axes") or {},
            )
            return (
                f'<c:scatterChart><c:scatterStyle val="{scatter_style}"/>'
                '<c:varyColors val="0"/>'
                f"{ser_xml}"
                f'<c:axId val="{x_ax_id}"/><c:axId val="{y_ax_id}"/>'
                "</c:scatterChart>"
                f"{axes_xml}"
            )
        axes_xml = _xy_axis_xml(
            x_ax_id,
            y_ax_id,
            axis_font_size=axis_font_size,
            axis_title_font_size=axis_title_font_size,
            axis_titles=axis_titles,
            chart_style=chart_style,
            axes=chart_data.get("axes") or {},
        )
        return (
            '<c:bubbleChart><c:varyColors val="0"/>'
            f"{ser_xml}"
            '<c:bubbleScale val="100"/><c:showNegBubbles val="0"/>'
            f'<c:axId val="{x_ax_id}"/><c:axId val="{y_ax_id}"/>'
            "</c:bubbleChart>"
            f"{axes_xml}"
        )

    categories = chart_data["categories"]
    series = chart_data["series"]
    if chart_type == "stock":
        stock_cat_ax_id = "2068027336"
        stock_val_ax_id = "2113994440"
        stock_axes = chart_data.get("axes") or {}
        stock_category_format = None
        if stock_axes:
            stock_category_format = (
                stock_axes.get("category", {}).get("number_format")
                or "m/d/yyyy"
            )
        stock_series_xml = _stock_series_xml(
            categories,
            series,
            colors=colors,
            category_number_format=stock_category_format,
        )
        axes_xml = _stock_axis_xml(
            stock_cat_ax_id,
            stock_val_ax_id,
            axis_font_size=axis_font_size,
            axis_title_font_size=axis_title_font_size,
            axis_titles=axis_titles,
            chart_style=chart_style,
            axes=stock_axes,
        )
        return (
            "<c:stockChart>"
            f"{stock_series_xml}"
            '<c:hiLowLines/>'
            '<c:upDownBars><c:gapWidth val="150"/><c:upBars/><c:downBars/></c:upDownBars>'
            f'<c:axId val="{stock_cat_ax_id}"/><c:axId val="{stock_val_ax_id}"/>'
            "</c:stockChart>"
            f"{axes_xml}"
        )
    series_grouping = chart_data.get("grouping") or (
        "clustered" if chart_type in {"bar", "column"} else "standard"
    )
    ser_xml = _series_xml(
        categories,
        series,
        chart_type=chart_type,
        grouping=series_grouping,
        line_style=chart_data.get("line_style", "line"),
        marker_size=chart_data.get("marker_size"),
        radar_marker_style=chart_data.get("radar_marker_style"),
        radar_style=chart_data.get("radar_style", "marker"),
        colors=colors,
        category_is_numeric=_category_axis_is_date(chart_data.get("axes") or {}),
        category_number_format=(
            (chart_data.get("axes") or {})
            .get("category", {})
            .get(
                "number_format",
                "m/d/yyyy"
                if _category_axis_is_date(chart_data.get("axes") or {})
                else None,
            )
        ),
        data_labels=chart_data.get("data_labels"),
        data_label_font_size=axis_font_size,
        data_label_color=chart_style.get("text_color"),
        data_label_font_face=chart_style.get("font_face"),
        language=chart_style.get("primary_language"),
    )
    data_labels_xml = (
        _data_labels_xml(
            chart_data.get("data_labels"),
            chart_type=chart_type,
            grouping=series_grouping,
            point_count=len(series[0]["values"]),
            font_size=axis_font_size,
            default_color=chart_style.get("text_color"),
            default_font_face=chart_style.get("font_face"),
            language=chart_style.get("primary_language"),
        )
        if chart_type in {"area", "bar", "column", "line"}
        and not _series_scoped_data_labels(chart_data.get("data_labels"))
        else ""
    )

    if chart_type in {"bar", "column"}:
        bar_dir = "bar" if chart_type == "bar" else "col"
        grouping = series_grouping
        gap_width = chart_data.get("gap_width")
        if gap_width is None:
            gap_width = _DEFAULT_GAP_WIDTH
        axes_xml = _axis_xml(
            cat_ax_id,
            val_ax_id,
            axis_font_size=axis_font_size,
            axis_title_font_size=axis_title_font_size,
            axis_titles=axis_titles,
            chart_style=chart_style,
            chart_type=chart_type,
            grouping=grouping,
            show_value_axis_labels=chart_data.get("show_value_axis_labels", True),
            axes=chart_data.get("axes") or {},
        )
        overlap_xml = _bar_overlap_xml(grouping, chart_data.get("overlap"))
        vary_colors_xml = (
            '<c:varyColors val="1"/>'
            if any(item.get("point_colors") for item in series)
            else '<c:varyColors val="0"/>'
        )
        return (
            "<c:barChart>"
            f'<c:barDir val="{bar_dir}"/><c:grouping val="{grouping}"/>'
            f"{vary_colors_xml}"
            f"{ser_xml}"
            f"{data_labels_xml}"
            f'<c:gapWidth val="{gap_width}"/>'
            f"{overlap_xml}"
            f'<c:axId val="{cat_ax_id}"/><c:axId val="{val_ax_id}"/>'
            "</c:barChart>"
            f"{axes_xml}"
        )
    if chart_type in {"line", "area"}:
        tag = "lineChart" if chart_type == "line" else "areaChart"
        grouping = series_grouping
        axes_xml = _axis_xml(
            cat_ax_id,
            val_ax_id,
            axis_font_size=axis_font_size,
            axis_title_font_size=axis_title_font_size,
            axis_titles=axis_titles,
            chart_style=chart_style,
            chart_type=chart_type,
            grouping=grouping,
            show_value_axis_labels=chart_data.get("show_value_axis_labels", True),
            axes=chart_data.get("axes") or {},
        )
        line_tail_xml = '<c:marker val="1"/><c:smooth val="0"/>' if chart_type == "line" else ""
        return (
            f'<c:{tag}><c:grouping val="{grouping}"/><c:varyColors val="0"/>'
            f"{ser_xml}"
            f"{data_labels_xml}"
            f"{line_tail_xml}"
            f'<c:axId val="{cat_ax_id}"/><c:axId val="{val_ax_id}"/>'
            f"</c:{tag}>"
            f"{axes_xml}"
        )
    if chart_type == "doughnut":
        return (
            '<c:doughnutChart><c:varyColors val="1"/>'
            f"{ser_xml}"
            '<c:firstSliceAng val="0"/>'
            f'<c:holeSize val="{chart_data["hole_size"]}"/>'
            "</c:doughnutChart>"
        )
    if chart_type == "of_pie":
        of_pie_type = chart_data.get("of_pie_type", "pie")
        return (
            f'<c:ofPieChart><c:ofPieType val="{of_pie_type}"/>'
            '<c:varyColors val="1"/>'
            f"{ser_xml}"
            '<c:gapWidth val="100"/><c:secondPieSize val="75"/><c:serLines/>'
            "</c:ofPieChart>"
        )
    if chart_type == "radar":
        radar_style = chart_data.get("radar_style", "marker")
        axes_xml = _axis_xml(
            cat_ax_id,
            val_ax_id,
            axis_font_size=axis_font_size,
            axis_title_font_size=axis_title_font_size,
            axis_titles=axis_titles,
            chart_style=chart_style,
            chart_type=chart_type,
            show_value_axis_labels=chart_data.get("show_value_axis_labels", True),
            axes=chart_data.get("axes") or {},
        )
        return (
            f'<c:radarChart><c:radarStyle val="{radar_style}"/>'
            '<c:varyColors val="0"/>'
            f"{ser_xml}"
            f'<c:axId val="{cat_ax_id}"/><c:axId val="{val_ax_id}"/>'
            "</c:radarChart>"
            f"{axes_xml}"
        )
    return f'<c:pieChart><c:varyColors val="1"/>{ser_xml}<c:firstSliceAng val="0"/></c:pieChart>'
