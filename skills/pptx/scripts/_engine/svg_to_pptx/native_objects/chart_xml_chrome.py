# Adapted from PPT Master 5e8856b (MIT); see assets/licenses/ppt_master.txt.
"""Chart xml chrome implementation."""
from __future__ import annotations

from .chart_xml_shared import (
    Any,
    _DEFAULT_GAP_WIDTH,
    _bool_attr,
    _chart_plot_area_layout,
    _chart_text_entry,
    _chart_text_entry_color,
    _chart_text_entry_font_face,
    _chart_text_entry_font_size,
    _chart_tx_pr_xml,
    _compact_key,
    _excel_col,
    _font_face_xml,
    _xml_escape,
    detect_text_lang,
    text_has_rtl_characters,
    text_uses_rtl,
)

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
    from .chart_xml_labels import _series_color_xml
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
    from .chart_xml_labels import _number_cache, _series_color_xml, _string_cache
    from .chart_xml_series import _chart_color
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
