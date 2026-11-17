"""Classic native chart XML emitters."""

from __future__ import annotations

from typing import Any

from ..drawingml.utils import (
    _xml_escape,
    detect_text_lang,
    text_has_rtl_characters,
    text_uses_rtl,
)
from .chart_data import (
    _chart_plot_area_layout,
    _data_labels_config,
)
from .chart_style import (
    _chart_text_entry,
    _chart_text_entry_color,
    _chart_text_entry_font_face,
    _chart_text_entry_font_size,
    _chart_tx_pr_xml,
    _font_face_xml,
)
from .marker_common import (
    _bool_attr,
    _compact_key,
    _excel_col,
)



# Adapted from chart_xml.py; upstream MIT, Hugo He, 2025-2026.
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
    # Resolve shared symbols at call time; retain the public module contract.
    from .chart_xml import (
        _category_reference_xml,
        _chart_color,
        _data_labels_xml,
        _data_point_colors_xml,
        _line_point_markers_xml,
        _marker_size_pt,
        _marker_xml,
        _number_cache,
        _series_color_xml,
        _series_scoped_data_labels,
        _string_cache,
    )
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


def _chart_title_paragraph_xml(
    text: str,
    *,
    font_size: int,
    color: str | None = None,
    font_face: str | None = None,
    primary_language: str | None = None,
) -> str:
    fill_xml = (
        f'<a:solidFill><a:srgbClr val="{color}"/></a:solidFill>'
        if color else ""
    )
    lang = detect_text_lang(text, primary_language)
    rtl_attr = (
        ' rtl="1"'
        if text_uses_rtl(text, primary_language)
        else ''
    )
    run_rtl = '<a:rtl val="1"/>' if text_has_rtl_characters(text) else ''
    return (
        f'<a:p><a:pPr{rtl_attr}/><a:r><a:rPr lang="{lang}" '
        f'sz="{font_size}">{fill_xml}{_font_face_xml(font_face, primary_language)}'
        f'{run_rtl}</a:rPr>'
        f"<a:t>{_xml_escape(text)}</a:t></a:r></a:p>"
    )


def _chart_title_xml(
    title: Any,
    *,
    font_size: int,
    color: str | None = None,
    subtitle: Any = None,
    subtitle_font_size: int | None = None,
    font_face: str | None = None,
    primary_language: str | None = None,
) -> str:
    # Resolve shared symbols at call time; retain the public module contract.
    title_entry = _chart_text_entry(title)
    subtitle_entry = _chart_text_entry(subtitle)
    if title_entry is None and subtitle_entry is None:
        return '<c:autoTitleDeleted val="1"/>'
    paragraphs = []
    if title_entry is not None:
        text, item = title_entry
        paragraphs.append(_chart_title_paragraph_xml(
            text,
            font_size=_chart_text_entry_font_size(item, font_size),
            color=_chart_text_entry_color(item, color),
            font_face=_chart_text_entry_font_face(item, font_face),
            primary_language=primary_language,
        ))
    if subtitle_entry is not None:
        text, item = subtitle_entry
        paragraphs.append(_chart_title_paragraph_xml(
            text,
            font_size=_chart_text_entry_font_size(item, subtitle_font_size or font_size),
            color=_chart_text_entry_color(item, color),
            font_face=_chart_text_entry_font_face(item, font_face),
            primary_language=primary_language,
        ))
    return (
        "<c:title><c:tx><c:rich><a:bodyPr/><a:lstStyle/>"
        f"{''.join(paragraphs)}"
        "</c:rich></c:tx><c:layout/><c:overlay val=\"0\"/></c:title>"
        '<c:autoTitleDeleted val="0"/>'
    )


def _plot_area_layout_xml(
    chart_data: dict[str, Any],
    chart_bounds: tuple[int, int, int, int],
) -> str:
    layout = _chart_plot_area_layout(chart_data, chart_bounds)
    if layout is None:
        return "<c:layout/>"
    x, y, width, height = layout
    return (
        "<c:layout><c:manualLayout>"
        '<c:layoutTarget val="inner"/>'
        '<c:xMode val="edge"/><c:yMode val="edge"/>'
        '<c:wMode val="factor"/><c:hMode val="factor"/>'
        f'<c:x val="{x:.12g}"/><c:y val="{y:.12g}"/>'
        f'<c:w val="{width:.12g}"/><c:h val="{height:.12g}"/>'
        "</c:manualLayout></c:layout>"
    )


def _chart_legend_xml(
    payload: dict[str, Any],
    *,
    font_size: int,
    color: str | None = None,
    font_face: str | None = None,
    primary_language: str | None = None,
) -> str:
    style = payload.get("style") if isinstance(payload.get("style"), dict) else {}
    show_legend = payload.get("show_legend", style.get("show_legend", False))
    if not show_legend:
        return ""
    position_key = _compact_key(payload.get("legend_position") or style.get("legend_position") or "bottom")
    positions = {
        "bottom": "b",
        "b": "b",
        "left": "l",
        "l": "l",
        "right": "r",
        "r": "r",
        "top": "t",
        "t": "t",
    }
    position = positions.get(position_key, "b")
    return (
        f'<c:legend><c:legendPos val="{position}"/><c:layout/>'
        '<c:overlay val="0"/>'
        f'{_chart_tx_pr_xml(font_size, color, font_face=font_face, language=primary_language)}'
        '</c:legend>'
    )


def _scatter_series_style_xml(scatter_style: str, color: str) -> tuple[str, str, str]:
    # Resolve shared symbols at call time; retain the public module contract.
    from .chart_xml import (
        _series_color_xml,
    )
    has_line = scatter_style in {"line", "lineMarker", "smooth", "smoothMarker"}
    has_marker = scatter_style in {"lineMarker", "marker", "smoothMarker"}
    smooth = scatter_style in {"smooth", "smoothMarker"}
    marker_symbol = "circle" if has_marker else "none"
    return (
        _series_color_xml(color, line=has_line),
        f'<c:marker><c:symbol val="{marker_symbol}"/></c:marker>',
        f'<c:smooth val="{_bool_attr(smooth)}"/>',
    )


def _xy_series_xml(
    series: list[dict[str, Any]],
    *,
    chart_type: str,
    colors: list[str],
    scatter_style: str = "lineMarker",
) -> str:
    # Resolve shared symbols at call time; retain the public module contract.
    from .chart_xml import (
        _chart_color,
        _number_cache,
        _series_color_xml,
        _string_cache,
    )
    parts: list[str] = []
    column_stride = 3 if chart_type == "bubble" else 2
    for index, item in enumerate(series):
        x_col = 1 + index * column_stride
        y_col = x_col + 1
        first_row = 2
        last_row = len(item["x"]) + 1
        color = _chart_color(colors, index)
        color_xml = _series_color_xml(color)
        marker_xml = ""
        smooth_xml = ""
        if chart_type == "scatter":
            color_xml, marker_xml, smooth_xml = _scatter_series_style_xml(scatter_style, color)
        invert_xml = '<c:invertIfNegative val="0"/>' if chart_type == "bubble" else ""
        size_xml = ""
        if chart_type == "bubble":
            size_col = x_col + 2
            size_xml = (
                "<c:bubbleSize><c:numRef>"
                f"<c:f>Sheet1!${_excel_col(size_col)}${first_row}:"
                f"${_excel_col(size_col)}${last_row}</c:f>"
                f"{_number_cache(item['sizes'])}"
                "</c:numRef></c:bubbleSize><c:bubble3D val=\"0\"/>"
            )
        parts.append(
            "<c:ser>"
            f'<c:idx val="{index}"/><c:order val="{index}"/>'
            "<c:tx><c:strRef>"
            f"<c:f>Sheet1!${_excel_col(y_col)}$1</c:f>"
            f"{_string_cache([str(item['name'])])}"
            "</c:strRef></c:tx>"
            f"{color_xml}"
            f"{marker_xml}"
            f"{invert_xml}"
            "<c:xVal><c:numRef>"
            f"<c:f>Sheet1!${_excel_col(x_col)}${first_row}:"
            f"${_excel_col(x_col)}${last_row}</c:f>"
            f"{_number_cache(item['x'])}"
            "</c:numRef></c:xVal>"
            "<c:yVal><c:numRef>"
            f"<c:f>Sheet1!${_excel_col(y_col)}${first_row}:"
            f"${_excel_col(y_col)}${last_row}</c:f>"
            f"{_number_cache(item['y'])}"
            "</c:numRef></c:yVal>"
            f"{size_xml}"
            f"{smooth_xml}"
            "</c:ser>"
        )
    return "".join(parts)


def _bar_overlap_xml(grouping: str | None, overlap: int | None) -> str:
    if grouping in {"stacked", "percentStacked"}:
        return '<c:overlap val="100"/>'
    if overlap is None:
        return ""
    return f'<c:overlap val="{overlap}"/>'


def _bar_chart_group_xml(
    chart_type: str,
    grouping: str,
    ser_xml: str,
    *,
    cat_ax_id: str,
    val_ax_id: str,
    vary_colors: bool = False,
    data_labels_xml: str = "",
    gap_width: int | None = None,
    overlap: int | None = None,
) -> str:
    # Resolve shared symbols at call time; retain the public module contract.
    from .chart_xml import (
        _DEFAULT_GAP_WIDTH,
    )
    bar_dir = "bar" if chart_type == "bar" else "col"
    vary_colors_xml = '<c:varyColors val="1"/>' if vary_colors else '<c:varyColors val="0"/>'
    overlap_xml = _bar_overlap_xml(grouping, overlap)
    return (
        "<c:barChart>"
        f'<c:barDir val="{bar_dir}"/><c:grouping val="{grouping}"/>'
        f"{vary_colors_xml}"
        f"{ser_xml}"
        f"{data_labels_xml}"
        f'<c:gapWidth val="{_DEFAULT_GAP_WIDTH if gap_width is None else gap_width}"/>'
        f"{overlap_xml}"
        f'<c:axId val="{cat_ax_id}"/><c:axId val="{val_ax_id}"/>'
        "</c:barChart>"
    )


def _line_area_chart_group_xml(
    chart_type: str,
    grouping: str,
    ser_xml: str,
    *,
    cat_ax_id: str,
    val_ax_id: str,
    data_labels_xml: str = "",
) -> str:
    tag = "lineChart" if chart_type == "line" else "areaChart"
    line_tail_xml = '<c:marker val="1"/><c:smooth val="0"/>' if chart_type == "line" else ""
    return (
        f'<c:{tag}><c:grouping val="{grouping}"/><c:varyColors val="0"/>'
        f"{ser_xml}"
        f"{data_labels_xml}"
        f"{line_tail_xml}"
        f'<c:axId val="{cat_ax_id}"/><c:axId val="{val_ax_id}"/>'
        f"</c:{tag}>"
    )
