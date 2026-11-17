"""Classic native chart XML emitters."""

from __future__ import annotations

from typing import Any
from xml.etree import ElementTree as ET

from ..drawingml.utils import (
    _xml_escape,
)
from .chart_data import (
    _chart_language,
)
from .chart_style import (
    _axis_titles,
    _chart_area_sp_pr_xml,
    _chart_text_sizes,
    _chart_title_is_bounded,
    _chart_tx_pr_xml,
    _classic_chart_style,
)
from .marker_attributes import native_json_is_authoritative
from .marker_common import (
    PACKAGE_REL_TYPE,
    _clean_hex,
)



# Adapted from chart_xml.py; upstream MIT, Hugo He, 2025-2026.
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
    # Resolve shared symbols at call time; retain the public module contract.
    from .chart_xml import (
        _chart_data_with_fallback_geometry,
        _chart_legend_xml,
        _chart_plot_xml,
        _chart_title_xml,
        _plot_area_layout_xml,
    )
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
