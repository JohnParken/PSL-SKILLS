# Adapted from PPT Master 5e8856b (MIT); see assets/licenses/ppt_master.txt.
"""Chart xml document implementation."""
from __future__ import annotations

from .chart_xml_shared import (
    Any,
    ET,
    PACKAGE_REL_TYPE,
    _axis_titles,
    _chart_area_sp_pr_xml,
    _chart_language,
    _chart_text_sizes,
    _chart_title_is_bounded,
    _chart_tx_pr_xml,
    _classic_chart_style,
    _clean_hex,
    _inferred_bar_gap_width,
    _inferred_bar_overlap,
    _inferred_cross_between,
    _xml_escape,
    native_json_is_authoritative,
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


def _chart_xml(
    elem: ET.Element,
    payload: dict[str, Any],
    *,
    chart_rels_id: str,
    chart_data: dict[str, Any],
    chart_bounds: tuple[int, int, int, int],
    inherited_styles: dict[str, str] | None = None,
    primary_language: str | None = None,
) -> bytes:
    from .chart_xml_chrome import _chart_legend_xml, _chart_title_xml, _plot_area_layout_xml
    from .chart_xml_plots import _chart_plot_xml
    primary_language = _chart_language(payload) or primary_language
    style = payload.get("style") if isinstance(payload.get("style"), dict) else {}
    colors = (
        [_clean_hex(color, "#4472C4") for color in style.get("colors", [])]
        if isinstance(style.get("colors"), list)
        else []
    )
    text_sizes = _chart_text_sizes(payload, elem, inherited_styles)
    axis_titles = _axis_titles(payload)
    chart_style = _classic_chart_style(payload, elem, inherited_styles)
    chart_style["primary_language"] = primary_language
    if not native_json_is_authoritative(elem):
        chart_data = _chart_data_with_fallback_geometry(elem, chart_data)
    plot_xml = _chart_plot_xml(
        chart_data,
        colors,
        axis_font_size=text_sizes["axis"],
        axis_title_font_size=text_sizes["axis_title"],
        axis_titles=axis_titles,
        chart_style=chart_style,
    )
    bounded_title = _chart_title_is_bounded(payload)
    title_xml = _chart_title_xml(
        None if bounded_title else payload.get("title"),
        font_size=text_sizes["title"],
        color=chart_style.get("text_color"),
        subtitle=None if bounded_title else payload.get("subtitle"),
        subtitle_font_size=text_sizes["subtitle"],
        font_face=chart_style.get("font_face"),
        primary_language=primary_language,
    )
    legend_xml = _chart_legend_xml(
        payload,
        font_size=text_sizes["legend"],
        color=chart_style.get("text_color"),
        font_face=chart_style.get("font_face"),
        primary_language=primary_language,
    )
    chart_language = primary_language or "en-US"
    base_text_properties_xml = _chart_tx_pr_xml(
        text_sizes["base"],
        chart_style.get("text_color"),
        font_face=chart_style.get("font_face"),
        language=primary_language,
    )
    xml = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<c:chartSpace xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart"
              xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
              xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<c:date1904 val="0"/>
<c:lang val="{_xml_escape(chart_language)}"/>
<c:chart>
{title_xml}
<c:plotArea>{_plot_area_layout_xml(chart_data, chart_bounds)}{plot_xml}{_chart_area_sp_pr_xml(chart_style.get("plot_fill"))}</c:plotArea>
{legend_xml}
<c:plotVisOnly val="1"/>
<c:dispBlanksAs val="gap"/>
</c:chart>
{_chart_area_sp_pr_xml(chart_style.get("chart_fill"))}
{base_text_properties_xml}
<c:externalData r:id="{chart_rels_id}"><c:autoUpdate val="0"/></c:externalData>
</c:chartSpace>'''
    return xml.encode("utf-8")


def _chart_rels_xml(workbook_target: str) -> bytes:
    xml = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="{PACKAGE_REL_TYPE}" Target="{_xml_escape(workbook_target)}"/>
</Relationships>'''
    return xml.encode("utf-8")
