"""Native PowerPoint table conversion."""

from __future__ import annotations

from .table import _TableBorderSpec

from typing import Any



from ..drawingml.theme_colors import ThemeColorSpec, color_node_xml
from ..drawingml.utils import (
    _xml_escape,
    detect_text_lang,
    text_has_rtl_characters,
    text_uses_rtl,
)
from .chart_style import (
    _font_face_xml,
)
from .marker_common import (
    _bool_attr,
    _powerpoint_line_width_emu,
)



# Adapted from table.py; upstream MIT, Hugo He, 2025-2026.
def _table_text_run(
    text: str,
    *,
    color: str | None,
    bold: bool | None,
    font_size: int | None,
    font_face: str | None,
    language: str | None,
    default_language: str | None,
    theme_color_spec: ThemeColorSpec | None,
    italic: bool | None = None,
    underline: bool | None = None,
    strike: bool | None = None,
    alt_language: str | None = None,
    exact_font_face: bool = False,
    baseline: int | None = None,
    outline: _TableBorderSpec | None = None,
) -> str:
    size_attr = f' sz="{font_size}"' if font_size is not None else ""
    bold_attr = f' b="{_bool_attr(bold)}"' if bold is not None else ""
    italic_attr = f' i="{_bool_attr(italic)}"' if italic is not None else ""
    underline_attr = (
        f' u="{"sng" if underline else "none"}"'
        if underline is not None else ""
    )
    strike_attr = (
        f' strike="{"sngStrike" if strike else "noStrike"}"'
        if strike is not None else ""
    )
    resolved_language = language or detect_text_lang(text, default_language)
    language_attr = f' lang="{_xml_escape(resolved_language)}"'
    alt_language_attr = (
        f' altLang="{_xml_escape(alt_language)}"' if alt_language else ""
    )
    baseline_attr = f' baseline="{baseline}"' if baseline is not None else ""
    color_xml = (
        f'<a:solidFill>{color_node_xml(color, theme_color_spec, "text")}</a:solidFill>'
        if color else ""
    )
    if exact_font_face and font_face:
        escaped_face = _xml_escape(font_face)
        font_xml = (
            f'<a:latin typeface="{escaped_face}"/>'
            f'<a:ea typeface="{escaped_face}"/>'
            f'<a:cs typeface="{escaped_face}"/>'
        )
    else:
        font_xml = _font_face_xml(font_face, default_language)
    outline_xml = ""
    if outline is not None and outline.style == "solid":
        assert outline.color is not None and outline.width is not None
        width = _powerpoint_line_width_emu(
            outline.width,
            "table text outline width",
        )
        outline_xml = (
            f'<a:ln w="{width}">'
            '<a:solidFill>'
            f'{color_node_xml(outline.color, theme_color_spec, "stroke")}'
            '</a:solidFill><a:prstDash val="solid"/></a:ln>'
        )
    rtl_xml = '<a:rtl val="1"/>' if text_has_rtl_characters(text) else ''
    space_attr = ' xml:space="preserve"' if text != text.strip() else ""
    return (
        f'<a:r><a:rPr{language_attr}{alt_language_attr}{size_attr}{bold_attr}'
        f'{italic_attr}{underline_attr}{strike_attr}{baseline_attr}>'
        f'{outline_xml}'
        f'{color_xml}'
        f'{font_xml}'
        f'{rtl_xml}'
        "</a:rPr>"
        f"<a:t{space_attr}>{_xml_escape(text)}</a:t></a:r>"
    )


def _table_paragraph_properties(
    align: str,
    *,
    emit_align: bool,
    text: str,
    language: str | None,
    line_spacing_percent: float | None = None,
) -> str:
    """Build table paragraph properties with project-aware direction."""
    attrs = []
    if emit_align:
        attrs.append(f'algn="{align}"')
    if text_uses_rtl(text, language):
        attrs.append('rtl="1"')
    suffix = f" {' '.join(attrs)}" if attrs else ''
    if line_spacing_percent is None:
        return f'<a:pPr{suffix}/>'
    spacing = round(line_spacing_percent * 1000)
    return (
        f'<a:pPr{suffix}><a:lnSpc><a:spcPct val="{spacing}"/>'
        '</a:lnSpc></a:pPr>'
    )


def _cell_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return {"text": "" if value is None else str(value)}
