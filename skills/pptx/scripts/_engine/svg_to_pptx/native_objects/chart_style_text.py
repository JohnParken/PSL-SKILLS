# Adapted from PPT Master 5e8856b (MIT); see assets/licenses/ppt_master.txt.
"""Chart style text implementation."""
from __future__ import annotations

from .chart_style_shared import (
    Any,
    ET,
    _AXIS_TITLE_KEY_GROUPS,
    _THEME_FONT_TOKEN,
    _fallback_shape_records,
    _fallback_stroke_colors,
    _fallback_text_colors,
    _first_present,
    _font_size_hpt,
    _hex_or_none,
    _inferred_chart_background,
    _local_tag,
    _maybe_number,
    _most_common_color,
    _style_attr,
    _xml_escape,
    detect_text_lang,
    math,
    native_json_is_authoritative,
    parse_font_family,
    quantize_ooxml_alpha,
    text_has_rtl_characters,
    text_uses_rtl,
)

def _chart_style_value(payload: dict[str, Any], *keys: str) -> Any:
    style = payload.get("style") if isinstance(payload.get("style"), dict) else {}
    for source in (payload, style):
        for key in keys:
            if source.get(key) is not None:
                return source.get(key)
    return None


def _chart_style_color(
    payload: dict[str, Any],
    keys: tuple[str, ...],
    default: str | None,
) -> str | None:
    raw = _chart_style_value(payload, *keys)
    if raw is None:
        return default
    if str(raw).strip().lower() in {"none", "transparent"}:
        return None
    return _hex_or_none(raw) or default


def _fallback_text_attr_values(
    elem: ET.Element,
    attr: str,
    inherited_value: str | None = None,
) -> list[str]:
    tag = _local_tag(elem)
    if tag == "metadata" or tag in {"defs", "clipPath", "mask", "filter", "style"}:
        return []
    if elem.get("display") == "none" or elem.get("visibility") == "hidden":
        return []

    own_value = _style_attr(elem, attr)
    next_value = own_value if own_value is not None else inherited_value
    values: list[str] = []
    if tag in {"text", "tspan"} and next_value:
        values.append(str(next_value).strip())
    for child in elem:
        values.extend(_fallback_text_attr_values(child, attr, next_value))
    return values


def _most_common_value(values: list[str]) -> str | None:
    if not values:
        return None
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return max(counts.items(), key=lambda item: item[1])[0]


def _most_common_font_size(values: list[str]) -> str | None:
    if not values:
        return None
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    max_count = max(counts.values())
    candidates = [value for value, count in counts.items() if count == max_count]
    numeric_candidates = [
        (float(value), value)
        for value in candidates
        if _maybe_number(value) is not None
    ]
    if numeric_candidates:
        return min(numeric_candidates, key=lambda item: item[0])[1]
    return candidates[0]


def _record_has_label(record: Any, *needles: str) -> bool:
    return any(
        needle in label
        for label in record.labels
        for needle in needles
    )


def _fallback_chart_colors(
    elem: ET.Element,
    inherited_styles: dict[str, str] | None = None,
) -> dict[str, str | None]:
    inherited_styles = inherited_styles or {}
    text_color = _most_common_color(
        _fallback_text_colors(elem, inherited_styles.get("fill"))
    )
    stroke_colors = _fallback_stroke_colors(
        elem,
        inherited_styles.get("stroke"),
    )
    shape_records = _fallback_shape_records(
        elem,
        inherited_stroke=inherited_styles.get("stroke"),
    )
    axis_colors = [
        record.stroke
        for record in shape_records
        if record.stroke and _record_has_label(record, "axis")
    ]
    grid_colors = [
        record.stroke
        for record in shape_records
        if record.stroke and _record_has_label(record, "grid", "gridline")
    ]
    dominant_stroke = _most_common_color(stroke_colors)
    return {
        "text_color": text_color,
        "axis_color": _most_common_color(axis_colors) or dominant_stroke,
        "grid_color": _most_common_color(grid_colors) or dominant_stroke,
        # Which roles were read from labeled strokes; an unlabeled role fell
        # back to the marker's dominant stroke and the warning says so.
        "axis_labeled": bool(axis_colors),
        "grid_labeled": bool(grid_colors),
    }


def _classic_chart_style(
    payload: dict[str, Any],
    elem: ET.Element,
    inherited_styles: dict[str, str] | None = None,
) -> dict[str, str | None]:
    inherited_styles = inherited_styles or {}
    json_authority = native_json_is_authoritative(elem)
    fallback_background = None if json_authority else _inferred_chart_background(elem)
    fallback_colors = (
        {"text_color": None, "axis_color": None, "grid_color": None}
        if json_authority
        else _fallback_chart_colors(elem, inherited_styles)
    )
    text_color = fallback_colors["text_color"] or "404040"
    raw_font_face = _chart_style_value(payload, "font_family", "fontFamily", "font_face", "fontFace")
    fallback_font_face = (
        None
        if json_authority
        else _most_common_value(
            _fallback_text_attr_values(
                elem,
                "font-family",
                inherited_styles.get("font-family"),
            )
        )
    )
    font_face = str(raw_font_face).strip() if raw_font_face is not None else fallback_font_face
    axis_color = fallback_colors["axis_color"] or text_color
    grid_color = fallback_colors["grid_color"] or "D9DED8"
    chart_fill = _chart_style_color(
        payload,
        (
            "chart_area_fill",
            "chartAreaFill",
            "chart_fill",
            "chartFill",
            "background",
            "background_color",
            "backgroundColor",
            "fill",
        ),
        fallback_background,
    )
    return {
        "axis_color": _chart_style_color(
            payload,
            ("axis_color", "axisColor", "axis_line_color", "axisLineColor"),
            axis_color,
        ),
        "chart_fill": chart_fill,
        "grid_color": _chart_style_color(
            payload,
            ("grid_color", "gridColor", "gridline_color", "gridlineColor"),
            grid_color,
        ),
        "plot_fill": _chart_style_color(
            payload,
            ("plot_area_fill", "plotAreaFill", "plot_background", "plotBackground"),
            None,
        ),
        "text_color": _chart_style_color(
            payload,
            ("text_color", "textColor", "label_color", "labelColor", "font_color", "fontColor"),
            text_color,
        ),
        "font_face": font_face or None,
    }


def _chart_text_sizes(
    payload: dict[str, Any],
    elem: ET.Element | None = None,
    inherited_styles: dict[str, str] | None = None,
) -> dict[str, int]:
    style = payload.get("style") if isinstance(payload.get("style"), dict) else {}
    inherited_styles = inherited_styles or {}
    if elem is not None and native_json_is_authoritative(elem):
        elem = None
    fallback_font_size = (
        _most_common_font_size(
            _fallback_text_attr_values(
                elem,
                "font-size",
                inherited_styles.get("font-size"),
            )
        )
        if elem is not None else None
    )
    base_raw = _first_present(
        payload.get("font_size"),
        payload.get("chart_font_size"),
        payload.get("chartFontSize"),
        style.get("font_size"),
        style.get("chart_font_size"),
        style.get("chartFontSize"),
        fallback_font_size,
    )
    axis_raw = _first_present(
        payload.get("axis_font_size"),
        payload.get("axisFontSize"),
        payload.get("tick_font_size"),
        payload.get("tickFontSize"),
        style.get("axis_font_size"),
        style.get("axisFontSize"),
        style.get("tick_font_size"),
        style.get("tickFontSize"),
        base_raw,
    )
    axis_title_raw = _first_present(
        payload.get("axis_title_font_size"),
        payload.get("axisTitleFontSize"),
        style.get("axis_title_font_size"),
        style.get("axisTitleFontSize"),
        axis_raw,
    )
    legend_raw = _first_present(
        payload.get("legend_font_size"),
        payload.get("legendFontSize"),
        style.get("legend_font_size"),
        style.get("legendFontSize"),
        axis_raw,
    )
    title_raw = _first_present(
        payload.get("title_font_size"),
        payload.get("titleFontSize"),
        style.get("title_font_size"),
        style.get("titleFontSize"),
    )
    subtitle_raw = _first_present(
        payload.get("subtitle_font_size"),
        payload.get("subtitleFontSize"),
        style.get("subtitle_font_size"),
        style.get("subtitleFontSize"),
        base_raw,
    )
    note_raw = _first_present(
        payload.get("note_font_size"),
        payload.get("noteFontSize"),
        style.get("note_font_size"),
        style.get("noteFontSize"),
        style.get("caption_font_size"),
        style.get("captionFontSize"),
        base_raw,
    )
    return {
        "axis": _font_size_hpt(axis_raw, 12),
        "axis_title": _font_size_hpt(axis_title_raw, 12),
        "base": _font_size_hpt(base_raw, 12),
        "legend": _font_size_hpt(legend_raw, 12),
        "note": _font_size_hpt(note_raw, 12),
        "subtitle": _font_size_hpt(subtitle_raw, 12),
        "title": _font_size_hpt(title_raw, 16),
    }


def _solid_fill_xml(color: str | None) -> str:
    if not color:
        return "<a:noFill/>"
    return f'<a:solidFill><a:srgbClr val="{color}"/></a:solidFill>'


def _chart_area_sp_pr_xml(fill_color: str | None) -> str:
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
    return f'<c:majorGridlines>{_chart_line_sp_pr_xml(color, width=6350)}</c:majorGridlines>'


def _font_face_xml(font_face: str | None, language: str | None = None) -> str:
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
    from .chart_style_companions import _chart_companion_box
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


def _axis_titles(payload: dict[str, Any]) -> dict[str, Any]:
    style = payload.get("style") if isinstance(payload.get("style"), dict) else {}
    raw = payload.get("axis_titles", payload.get("axisTitles"))
    style_raw = style.get("axis_titles", style.get("axisTitles"))
    axis_map = raw if isinstance(raw, dict) else {}
    style_axis_map = style_raw if isinstance(style_raw, dict) else {}

    def pick(axis_keys: tuple[str, ...], root_keys: tuple[str, ...]) -> Any:
        values: list[Any] = []
        for key in root_keys:
            values.extend((
                payload.get(key),
                style.get(key),
            ))
        for key in axis_keys + root_keys:
            values.extend((
                axis_map.get(key),
                style_axis_map.get(key),
            ))
        return _first_present(*values)

    return {
        field_name: pick(axis_keys, root_keys)
        for field_name, (axis_keys, root_keys) in _AXIS_TITLE_KEY_GROUPS.items()
    }
