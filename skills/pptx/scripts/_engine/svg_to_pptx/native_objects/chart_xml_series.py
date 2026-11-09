# Adapted from PPT Master 5e8856b (MIT); see assets/licenses/ppt_master.txt.
"""Chart xml series implementation."""
from __future__ import annotations

from .chart_xml_shared import (
    Any,
    _DEFAULT_CHART_COLORS,
    _clean_hex,
    _data_labels_config,
    _excel_col,
    _xml_escape,
)

def _chart_color(colors: list[str], index: int) -> str:
    if index < len(colors):
        return colors[index]
    return _DEFAULT_CHART_COLORS[index % len(_DEFAULT_CHART_COLORS)]


def _data_point_colors_xml(
    count: int,
    colors: list[str],
    *,
    disable_negative_invert: bool = False,
) -> str:
    from .chart_xml_labels import _series_color_xml
    invert_xml = '<c:invertIfNegative val="0"/>' if disable_negative_invert else ""
    return "".join(
        f'<c:dPt><c:idx val="{idx}"/>{invert_xml}'
        f'{_series_color_xml(_chart_color(colors, idx))}</c:dPt>'
        for idx in range(count)
    )


def _marker_xml(
    symbol: str | None,
    *,
    size_pt: int | None = None,
    color: str | None = None,
) -> str:
    if not symbol:
        return ""
    if symbol == "none":
        return '<c:marker><c:symbol val="none"/></c:marker>'
    size_xml = f'<c:size val="{size_pt}"/>' if size_pt is not None else ""
    sp_pr_xml = ""
    if color:
        clean = _clean_hex(color, "#4472C4")
        sp_pr_xml = (
            f'<c:spPr><a:solidFill><a:srgbClr val="{clean}"/></a:solidFill>'
            f'<a:ln><a:solidFill><a:srgbClr val="{clean}"/></a:solidFill></a:ln></c:spPr>'
        )
    return f'<c:marker><c:symbol val="{_xml_escape(symbol)}"/>{size_xml}{sp_pr_xml}</c:marker>'


def _marker_size_pt(size_px: Any) -> int | None:
    if size_px is None:
        return None
    return max(2, min(72, int(round(float(size_px) * 0.75))))


def _line_point_markers_xml(
    point_colors: list[str | None],
    *,
    size_pt: int | None,
) -> str:
    """Per-point line markers: a colour marks the point, ``None`` leaves it bare."""
    return "".join(
        f'<c:dPt><c:idx val="{idx}"/>'
        f'{_marker_xml("none" if color is None else "circle", size_pt=size_pt, color=color)}'
        "</c:dPt>"
        for idx, color in enumerate(point_colors)
    )


def _series_xml(
    categories: list[Any],
    series: list[dict[str, Any]],
    *,
    chart_type: str,
    grouping: str | None = None,
    line_style: str = "line",
    marker_size: Any = None,
    radar_marker_style: str | None = None,
    radar_style: str = "marker",
    colors: list[str],
    category_is_numeric: bool = False,
    category_number_format: str | None = None,
    data_labels: dict[str, Any] | None = None,
    data_label_font_size: int = 900,
    data_label_color: str | None = None,
    data_label_font_face: str | None = None,
    language: str | None = None,
    category_column: int = 1,
    color_start_index: int | None = None,
    series_indices: list[int] | None = None,
    start_column: int = 2,
    start_index: int = 0,
) -> str:
    from .chart_xml_labels import _category_reference_xml, _data_labels_xml, _number_cache, _series_color_xml, _series_scoped_data_labels, _string_cache
    parts: list[str] = []
    category_xml = _category_reference_xml(
        categories,
        column_index=category_column,
        numeric=category_is_numeric,
        number_format=category_number_format,
    )
    for offset, item in enumerate(series):
        index = (
            series_indices[offset]
            if series_indices is not None
            else start_index + offset
        )
        color_index = (
            color_start_index
            if color_start_index is not None
            else start_index
        ) + offset
        column_index = offset + start_column
        fill_opacity = item.get("fill_opacity") if chart_type == "area" else None
        line_width = item.get("line_width") if chart_type in {"area", "line"} else None
        series_color = _chart_color(colors, color_index)
        # An area is a filled region: it draws an outline only when the payload
        # asks for one with line_width, otherwise the fill edge is the shape.
        color_xml = _series_color_xml(
            series_color,
            line=chart_type != "area" or line_width is not None,
            fill_opacity=fill_opacity,
            line_width=line_width,
        )
        point_colors_xml = ""
        marker_xml = ""
        smooth_xml = ""
        if chart_type in {"doughnut", "of_pie", "pie"}:
            color_xml = ""
            point_count = (
                len(categories) + 1
                if chart_type == "of_pie"
                else len(categories)
            )
            point_colors_xml = _data_point_colors_xml(
                point_count,
                item.get("point_colors") or colors,
            )
        elif chart_type in {"bar", "column"} and item.get("point_colors"):
            point_colors_xml = _data_point_colors_xml(
                len(item["values"]),
                item["point_colors"],
                disable_negative_invert=True,
            )
        if chart_type == "line":
            size_pt = _marker_size_pt(
                item.get("marker_size") if item.get("marker_size") is not None else marker_size
            )
            marker_xml = _marker_xml(
                "circle" if line_style == "lineMarker" else "none",
                size_pt=size_pt,
                color=series_color if line_style == "lineMarker" else None,
            )
            if item.get("point_colors"):
                point_colors_xml = _line_point_markers_xml(
                    item["point_colors"],
                    size_pt=size_pt,
                )
            smooth_xml = '<c:smooth val="0"/>'
        if chart_type == "radar":
            if radar_style == "filled":
                color_xml = _series_color_xml(
                    _chart_color(colors, color_index),
                    line=False,
                )
            marker_xml = _marker_xml(radar_marker_style)
        invert_xml = '<c:invertIfNegative val="0"/>' if chart_type in {"bar", "column"} else ""
        item_data_labels = _data_labels_config(item)
        effective_data_labels = (
            item_data_labels if item_data_labels is not None else data_labels
        )
        data_labels_xml = (
            _data_labels_xml(
                effective_data_labels,
                chart_type=chart_type,
                grouping=grouping,
                point_count=len(item["values"]),
                font_size=data_label_font_size,
                default_color=data_label_color,
                default_font_face=data_label_font_face,
                language=language,
            )
            if (
                item_data_labels is not None
                or _series_scoped_data_labels(data_labels)
            )
            and chart_type in {"area", "bar", "column", "line"}
            else ""
        )
        parts.append(
            "<c:ser>"
            f'<c:idx val="{index}"/><c:order val="{index}"/>'
            "<c:tx><c:strRef>"
            f"<c:f>Sheet1!${_excel_col(column_index)}$1</c:f>"
            f"{_string_cache([str(item['name'])])}"
            "</c:strRef></c:tx>"
            f"{color_xml}{invert_xml}{marker_xml}{point_colors_xml}"
            f"{data_labels_xml}"
            f"{category_xml}"
            "<c:val><c:numRef>"
            f"<c:f>Sheet1!${_excel_col(column_index)}$2:${_excel_col(column_index)}${len(categories) + 1}</c:f>"
            f"{_number_cache(item['values'])}"
            "</c:numRef></c:val>"
            f"{smooth_xml}"
            "</c:ser>"
        )
    return "".join(parts)
