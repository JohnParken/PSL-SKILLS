"""Native chart styling and companion text helpers."""

from __future__ import annotations

from typing import Any
from xml.etree import ElementTree as ET

from .marker_attributes import (
    native_json_is_authoritative,
)

from .marker_common import (
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
)



# Adapted from chart_style.py; upstream MIT, Hugo He, 2025-2026.
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
    # Resolve shared symbols at call time; retain the public module contract.
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
    # Resolve shared symbols at call time; retain the public module contract.
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
    # Resolve shared symbols at call time; retain the public module contract.
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
    # Resolve shared symbols at call time; retain the public module contract.
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
    # Resolve shared symbols at call time; retain the public module contract.
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
