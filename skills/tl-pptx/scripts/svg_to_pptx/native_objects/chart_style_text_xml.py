"""Native chart styling and companion text helpers."""

from __future__ import annotations

import math
from typing import Any


from ..drawingml.utils import (
    _xml_escape,
    detect_text_lang,
    parse_font_family,
    quantize_ooxml_alpha,
    text_has_rtl_characters,
    text_uses_rtl,
)
from .marker_common import (
    _first_present,
    _font_size_hpt,
    _hex_or_none,
)



# Adapted from chart_style.py; upstream MIT, Hugo He, 2025-2026.
def _solid_fill_xml(color: str | None) -> str:
    if not color:
        return "<a:noFill/>"
    return f'<a:solidFill><a:srgbClr val="{color}"/></a:solidFill>'


def _chart_area_sp_pr_xml(fill_color: str | None) -> str:
    # Resolve shared symbols at call time; retain the public module contract.
    return f"<c:spPr>{_solid_fill_xml(fill_color)}<a:ln><a:noFill/></a:ln></c:spPr>"


def _chart_line_sp_pr_xml(color: str | None, *, width: int = 9525) -> str:
    if not color:
        line_xml = "<a:ln><a:noFill/></a:ln>"
    else:
        line_xml = (
            f'<a:ln w="{width}" cap="flat" cmpd="sng" algn="ctr">'
            f'<a:solidFill><a:srgbClr val="{color}"/></a:solidFill>'
            "<a:round/></a:ln>"
        )
    return f"<c:spPr>{line_xml}</c:spPr>"


def _major_gridlines_xml(color: str | None) -> str:
    # Resolve shared symbols at call time; retain the public module contract.
    return f'<c:majorGridlines>{_chart_line_sp_pr_xml(color, width=6350)}</c:majorGridlines>'


def _font_face_xml(font_face: str | None, language: str | None = None) -> str:
    # Resolve shared symbols at call time; retain the public module contract.
    from .chart_style import (
        _THEME_FONT_TOKEN,
    )
    if not font_face:
        return ""
    # Imported charts keep theme font tokens such as ``+mn-lt, +mn-ea``;
    # each token owns its own slot and named faces fill the rest.
    tokens: dict[str, str] = {}
    named: list[str] = []
    for entry in font_face.split(","):
        face = entry.strip().strip("'\"")
        match = _THEME_FONT_TOKEN.match(face)
        if match:
            tokens.setdefault(match.group(2), face)
        elif face:
            named.append(face)
    fonts = parse_font_family(", ".join(named), language) if named or not tokens else {}
    latin_font = tokens.get("lt") or fonts.get("latin")
    if latin_font is None:
        latin_font = tokens.get("ea", "").replace("-ea", "-lt") or "Segoe UI"
    ea_font = tokens.get("ea") or fonts.get("ea")
    if ea_font is None:
        ea_font = latin_font.replace("-lt", "-ea")
    cs_font = tokens.get("cs") or (
        latin_font.replace("-lt", "-cs") if _THEME_FONT_TOKEN.match(latin_font) else latin_font
    )
    return (
        f'<a:latin typeface="{_xml_escape(latin_font)}"/>'
        f'<a:ea typeface="{_xml_escape(ea_font)}"/>'
        f'<a:cs typeface="{_xml_escape(cs_font)}"/>'
    )


def _chart_tx_pr_xml(
    font_size: int,
    color: str | None = None,
    *,
    bold: bool = False,
    font_face: str | None = None,
    language: str | None = None,
) -> str:
    # Resolve shared symbols at call time; retain the public module contract.
    fill_xml = (
        f'<a:solidFill><a:srgbClr val="{color}"/></a:solidFill>'
        if color else ""
    )
    bold_attr = ' b="1"' if bold else ""
    resolved_language = language or 'en-US'
    rtl_attr = ' rtl="1"' if text_uses_rtl('', language) else ''
    return (
        f"<c:txPr><a:bodyPr/><a:lstStyle/><a:p><a:pPr{rtl_attr}>"
        f'<a:defRPr lang="{resolved_language}" sz="{font_size}"{bold_attr}>'
        f'{fill_xml}{_font_face_xml(font_face, language)}</a:defRPr>'
        f'</a:pPr><a:endParaRPr lang="{resolved_language}"/></a:p></c:txPr>'
    )


def _chart_text_entry(value: Any) -> tuple[str, dict[str, Any]] | None:
    if isinstance(value, dict):
        text = _first_present(value.get("text"), value.get("value"), value.get("content"))
        if text is None or not str(text).strip():
            return None
        return str(text).strip(), value
    if value is None or not str(value).strip():
        return None
    return str(value).strip(), {}


def _chart_title_is_bounded(payload: dict[str, Any]) -> bool:
    """Return whether the classic title requests an explicit companion box."""
    # Resolve shared symbols at call time; retain the public module contract.
    from .chart_style import (
        _chart_companion_box,
    )
    title = payload.get("title")
    return isinstance(title, dict) and _chart_companion_box(title) is not None


def _chart_text_entry_font_size(item: dict[str, Any], fallback: int) -> int:
    raw = _first_present(item.get("font_size"), item.get("fontSize"))
    if raw is None:
        return fallback
    return _font_size_hpt(raw, 12)


def _chart_text_entry_color(item: dict[str, Any], fallback: str | None) -> str | None:
    return _hex_or_none(_first_present(
        item.get("color"),
        item.get("font_color"),
        item.get("fontColor"),
    )) or fallback


def _chart_text_entry_font_face(item: dict[str, Any], fallback: str | None) -> str | None:
    raw = _first_present(
        item.get("font_family"),
        item.get("fontFamily"),
        item.get("font_face"),
        item.get("fontFace"),
    )
    if raw is None:
        return fallback
    font_face = str(raw).strip()
    return font_face or fallback


def _alpha_xml(value: Any, field_name: str = "fill_opacity") -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        raise RuntimeError(f"Native PPTX chart {field_name} must be numeric")
    try:
        alpha = float(value)
    except (TypeError, ValueError, OverflowError):
        raise RuntimeError(f"Native PPTX chart {field_name} must be numeric") from None
    if not math.isfinite(alpha):
        raise RuntimeError(f"Native PPTX chart {field_name} must be finite")
    if alpha < 0 or alpha > 1:
        raise RuntimeError(f"Native PPTX chart {field_name} must be between 0 and 1")
    return f'<a:alpha val="{quantize_ooxml_alpha(alpha)}"/>'


def _axis_title_xml(
    title: Any,
    *,
    font_size: int,
    color: str | None = None,
    font_face: str | None = None,
    primary_language: str | None = None,
) -> str:
    # Resolve shared symbols at call time; retain the public module contract.
    entry = _chart_text_entry(title)
    if entry is None:
        return ""
    text, item = entry
    text_color = _chart_text_entry_color(item, color)
    fill_xml = (
        f'<a:solidFill><a:srgbClr val="{text_color}"/></a:solidFill>'
        if text_color else ""
    )
    lang = detect_text_lang(text, primary_language)
    rtl_attr = (
        ' rtl="1"'
        if text_uses_rtl(text, primary_language)
        else ''
    )
    run_rtl = '<a:rtl val="1"/>' if text_has_rtl_characters(text) else ''
    return (
        "<c:title><c:tx><c:rich><a:bodyPr/><a:lstStyle/>"
        f'<a:p><a:pPr{rtl_attr}/><a:r><a:rPr lang="{lang}" '
        f'sz="{_chart_text_entry_font_size(item, font_size)}">'
        f"{fill_xml}{_font_face_xml(_chart_text_entry_font_face(item, font_face), primary_language)}"
        f"{run_rtl}</a:rPr>"
        f"<a:t>{_xml_escape(text)}</a:t></a:r></a:p>"
        "</c:rich></c:tx><c:layout/><c:overlay val=\"0\"/></c:title>"
    )
