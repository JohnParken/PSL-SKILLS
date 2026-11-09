# Adapted from PPT Master 5e8856b (MIT); see assets/licenses/ppt_master.txt.
"""Chart style warnings implementation."""
from __future__ import annotations

from .chart_style_shared import (
    Any,
    ET,
    _AXIS_TITLE_KEY_GROUPS,
    _DEFAULT_CHART_COLORS,
    _category_axis_reversed,
    _clean_hex,
    _compact_key,
    _fallback_shape_records,
    _fallback_text_records,
    _first_present,
    _hex_or_none,
    _normalized_fallback_text,
    _visible_fallback_texts,
    native_import_source,
    native_json_is_authoritative,
)

def _metadata_text(value: Any) -> str | None:
    from .chart_style_text import _chart_text_entry
    entry = _chart_text_entry(value)
    if entry is None:
        return None
    text, _ = entry
    return _normalized_fallback_text(text)


def _native_chart_chrome_errors(elem: ET.Element, payload: dict[str, Any]) -> list[str]:
    from .chart_style_text import _axis_titles
    if native_json_is_authoritative(elem):
        return []
    fallback_texts = set(_visible_fallback_texts(elem))
    missing: list[str] = []

    for field_name in ("title", "subtitle"):
        text = _metadata_text(payload.get(field_name))
        if text and text not in fallback_texts:
            missing.append(f"{field_name}={text!r}")

    for field_name, value in _axis_titles(payload).items():
        text = _metadata_text(value)
        if text and text not in fallback_texts:
            missing.append(f"axis_titles.{field_name}={text!r}")

    if not missing:
        return []

    sample = ", ".join(missing[:5])
    suffix = "" if len(missing) <= 5 else f", and {len(missing) - 5} more"
    return [
        "Native PPTX chart metadata contains title/axis text that is not visible "
        "inside the fallback marker and would appear only after "
        f"--native-charts-and-tables: {sample}{suffix}. "
        "Use `name` for object naming, or draw the same text in the chart fallback."
    ]


def _native_chart_export_payload(
    elem: ET.Element,
    payload: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    from .chart_style_text import _axis_titles
    if native_import_source(elem) == "pptx" or native_json_is_authoritative(elem):
        return payload, []
    fallback_texts = set(_visible_fallback_texts(elem))
    output = payload
    messages: list[str] = []

    def mutable_payload() -> dict[str, Any]:
        nonlocal output
        if output is payload:
            output = dict(payload)
        return output

    def mutable_style(target: dict[str, Any]) -> dict[str, Any] | None:
        style = target.get("style")
        if not isinstance(style, dict):
            return None
        if target.get("style") is payload.get("style"):
            style = dict(style)
            target["style"] = style
        return style

    def drop_map_keys(source: dict[str, Any], map_key: str, keys: tuple[str, ...]) -> None:
        raw_map = source.get(map_key)
        if not isinstance(raw_map, dict):
            return
        next_map = dict(raw_map)
        for key in keys:
            next_map.pop(key, None)
        if next_map:
            source[map_key] = next_map
        else:
            source.pop(map_key, None)

    for key in ("title", "subtitle"):
        text = _metadata_text(payload.get(key))
        if text and text not in fallback_texts:
            mutable_payload().pop(key, None)
            messages.append(
                f"omitted native chart {key} {text!r} because it is not visible in the fallback"
            )

    for field_name, value in _axis_titles(payload).items():
        text = _metadata_text(value)
        if not text or text in fallback_texts:
            continue
        axis_keys, root_keys = _AXIS_TITLE_KEY_GROUPS[field_name]
        target = mutable_payload()
        for key in root_keys:
            target.pop(key, None)
        for map_key in ("axis_titles", "axisTitles"):
            drop_map_keys(target, map_key, axis_keys + root_keys)
        style = mutable_style(target)
        if style is not None:
            for key in root_keys:
                style.pop(key, None)
            for map_key in ("axis_titles", "axisTitles"):
                drop_map_keys(style, map_key, axis_keys + root_keys)
        messages.append(
            f"omitted native chart axis_titles.{field_name} {text!r} "
            "because it is not visible in the fallback"
        )

    style = payload.get("style") if isinstance(payload.get("style"), dict) else {}
    show_legend = payload.get("show_legend", style.get("show_legend", False))
    if show_legend:
        legend_texts = _legend_candidate_texts(payload)
        missing_legend = [
            text for text in legend_texts if text not in fallback_texts
        ]
        if missing_legend:
            mutable_payload()["show_legend"] = False
            sample = ", ".join(repr(text) for text in missing_legend)
            messages.append(
                "omitted native chart legend because these expected labels are "
                f"missing from the fallback: {sample}"
            )

    return output, messages


def _legend_candidate_texts(payload: dict[str, Any]) -> list[str]:
    series_values: list[str] = []
    category_values: list[str] = []
    categories = payload.get("categories")
    if isinstance(categories, list):
        category_values.extend(str(item) for item in categories if str(item).strip())
    series = payload.get("series")
    if isinstance(series, list):
        for item in series:
            if isinstance(item, dict) and item.get("name") is not None:
                series_values.append(str(item.get("name")))
    plots = payload.get("plots")
    if isinstance(plots, list):
        for plot in plots:
            if not isinstance(plot, dict):
                continue
            plot_series = plot.get("series")
            if not isinstance(plot_series, list):
                continue
            for item in plot_series:
                if isinstance(item, dict) and item.get("name") is not None:
                    series_values.append(str(item.get("name")))
    chart_type = _compact_key(payload.get("type") or payload.get("chart_type") or "")
    category_legend_types = {"pie", "doughnut", "donut", "ofpie", "pieofpie", "barofpie"}
    values = category_values if chart_type in category_legend_types else series_values
    normalized: list[str] = []
    for value in values:
        text = _normalized_fallback_text(value)
        if text:
            normalized.append(text)
    return normalized


def _native_chart_style_color_warnings(
    elem: ET.Element,
    payload: dict[str, Any],
) -> list[str]:
    from .chart_style_text import _chart_style_value, _fallback_chart_colors
    inferred = _fallback_chart_colors(elem)
    fields = {
        "text_color": (
            "text_color", "textColor", "label_color", "labelColor",
            "font_color", "fontColor",
        ),
        "axis_color": (
            "axis_color", "axisColor", "axis_line_color", "axisLineColor",
        ),
        "grid_color": (
            "grid_color", "gridColor", "gridline_color", "gridlineColor",
        ),
    }
    warnings: list[str] = []
    for field_name, aliases in fields.items():
        raw = _chart_style_value(payload, *aliases)
        if raw is None:
            continue
        payload_color = _hex_or_none(raw)
        fallback_color = inferred[field_name]
        if (
            payload_color is None
            or fallback_color is None
            or payload_color == fallback_color
        ):
            continue
        message = (
            f"Native PPTX chart style.{field_name} #{payload_color} differs "
            f"from fallback dominant {field_name} #{fallback_color}; set "
            f"style.{field_name} to #{fallback_color} or repaint the fallback "
            f"so #{payload_color} is its dominant {field_name}"
        )
        label_needle = {"axis_color": "axis", "grid_color": "grid"}.get(field_name)
        if label_needle and not inferred.get(f"{label_needle}_labeled"):
            message += (
                f" (no stroke carries an id or class naming '{label_needle}', so "
                f"the marker's dominant stroke was read instead — label the "
                f"{label_needle} lines to read their own color)"
            )
        warnings.append(message)
    return warnings


def _native_chart_point_color_warnings(
    elem: ET.Element,
    payload: dict[str, Any],
    chart_data: dict[str, Any],
) -> list[str]:
    from .chart_style_text import _record_has_label
    if chart_data.get("kind") != "category" or chart_data.get("type") not in {
        "bar", "column",
    }:
        return []
    series = chart_data.get("series")
    if not isinstance(series, list) or not series:
        return []
    style = payload.get("style") if isinstance(payload.get("style"), dict) else {}
    raw_colors = _first_present(style.get("colors"), payload.get("colors"))
    palette = (
        [_clean_hex(color, "#4472C4") for color in raw_colors]
        if isinstance(raw_colors, list) and raw_colors
        else list(_DEFAULT_CHART_COLORS)
    )
    series_colors = {
        palette[idx % len(palette)]
        for idx in range(len(series))
    }
    point_colors = {
        color
        for item in series
        if isinstance(item, dict)
        for color in item.get("point_colors", [])
    }
    deviations = sorted({
        record.fill
        for record in _fallback_shape_records(elem)
        if (
            record.tag == "rect"
            and record.fill is not None
            and _record_has_label(record, "bar", "column")
            and not _record_has_label(record, "legend")
            and record.fill not in series_colors
            and record.fill not in point_colors
        )
    })
    return [
        "Native PPTX chart data mark fill "
        f"#{color} deviates from its series color, but series point_colors is absent"
        for color in deviations
    ]


def _fallback_data_shapes(elem: ET.Element) -> list[Any]:
    """Painted fallback geometry that is not axis, grid, or legend chrome."""
    from .chart_style_text import _record_has_label
    return [
        record
        for record in _fallback_shape_records(elem)
        if not _record_has_label(record, "axis", "grid", "legend")
    ]


def _series_palette(payload: dict[str, Any], series_count: int) -> set[str]:
    style = payload.get("style") if isinstance(payload.get("style"), dict) else {}
    raw_colors = _first_present(style.get("colors"), payload.get("colors"))
    palette = (
        [_clean_hex(color, "#4472C4") for color in raw_colors]
        if isinstance(raw_colors, list) and raw_colors
        else list(_DEFAULT_CHART_COLORS)
    )
    return {palette[idx % len(palette)] for idx in range(series_count)}


def _typed_series(chart_data: dict[str, Any], chart_type: str) -> list[dict[str, Any]]:
    """Series of one plot type from a category chart or the matching combo plots."""
    if chart_data.get("kind") == "category" and chart_data.get("type") == chart_type:
        return list(chart_data.get("series") or [])
    if chart_data.get("kind") == "combo":
        return [
            item
            for plot in chart_data.get("plots") or []
            if plot.get("type") == chart_type
            for item in plot.get("series") or []
        ]
    return []


def _native_chart_line_marker_warnings(
    elem: ET.Element,
    payload: dict[str, Any],
    chart_data: dict[str, Any],
) -> list[str]:
    line_series = _typed_series(chart_data, "line")
    if not line_series:
        return []
    dots = [
        record
        for record in _fallback_data_shapes(elem)
        if record.tag in {"circle", "ellipse"} and record.fill is not None
    ]
    if not dots:
        return []
    if chart_data.get("kind") == "category":
        marker_styles = [chart_data.get("line_style")]
    else:
        marker_styles = [
            plot.get("line_style")
            for plot in chart_data.get("plots") or []
            if plot.get("type") == "line"
        ]
    has_markers = "lineMarker" in marker_styles or any(
        item.get("point_colors") for item in line_series
    )
    warnings: list[str] = []
    if not has_markers:
        warnings.append(
            f"Native PPTX line chart fallback draws {len(dots)} point marker(s) "
            "but the payload has none; set chart-root line_style \"lineMarker\" "
            "(combo: per plot; marker_size in px) for every point, or series "
            "point_colors with a colour per marked point "
            "and null elsewhere"
        )
    series_count = (
        len(chart_data.get("series") or [])
        if chart_data.get("kind") == "category"
        else sum(len(plot.get("series") or []) for plot in chart_data.get("plots") or [])
    )
    known = _series_palette(payload, series_count) | {
        color
        for item in line_series
        for color in item.get("point_colors", [])
        if color is not None
    }
    deviations = sorted({
        record.fill for record in dots if record.fill not in known
    })
    warnings.extend(
        f"Native PPTX line marker fill #{color} deviates from its series color, "
        "but series point_colors is absent"
        for color in deviations
    )
    return warnings


def _native_chart_area_warnings(
    elem: ET.Element,
    payload: dict[str, Any],
    chart_data: dict[str, Any],
) -> list[str]:
    area_series = _typed_series(chart_data, "area")
    if not area_series:
        return []
    shapes = _fallback_data_shapes(elem)
    regions = [
        record
        for record in shapes
        if record.tag in {"path", "polygon"} and record.fill is not None
    ]
    warnings: list[str] = []
    translucent = sorted({
        record.fill_opacity
        for record in regions
        if record.fill_opacity is not None and record.fill_opacity < 1
    })
    if translucent and not any(item.get("fill_opacity") is not None for item in area_series):
        sample = ", ".join(f"{value:g}" for value in translucent[:3])
        warnings.append(
            f"Native PPTX area chart fallback fills are translucent (fill-opacity {sample}) "
            "but no area series sets fill_opacity; native areas export opaque"
        )
    if chart_data.get("kind") != "category":
        return warnings
    strokes = [
        record
        for record in shapes
        if record.tag in {"path", "polyline"} and record.fill is None and record.stroke is not None
    ]
    dots = [
        record
        for record in shapes
        if record.tag in {"circle", "ellipse"} and record.fill is not None
    ]
    outlined = any(item.get("line_width") is not None for item in area_series)
    if (strokes and not outlined) or dots:
        warnings.append(
            "Native PPTX area chart fallback draws a line"
            + (" and point markers" if dots else "")
            + " over the filled region, but an area series exports as a fill without "
            "either; give the series line_width for a same-colour outline, or use type "
            "\"combo\" with an area plot (fill_opacity) under a line plot (line_width, "
            "line_style \"lineMarker\")"
        )
    return warnings


def _native_chart_category_order_warnings(
    elem: ET.Element,
    payload: dict[str, Any],
    chart_data: dict[str, Any],
) -> list[str]:
    from .chart_style_text import _record_has_label
    chart_type = chart_data.get("type")
    if chart_data.get("kind") != "category" or chart_type not in {"bar", "column"}:
        return []
    categories = [str(item) for item in chart_data.get("categories") or []]
    if len(categories) < 2:
        return []
    first_text = _normalized_fallback_text(categories[0])
    last_text = _normalized_fallback_text(categories[-1])
    records = [
        record
        for record in _fallback_text_records(elem)
        if not _record_has_label(record, "legend")
        and record.x is not None
        and record.y is not None
    ]
    firsts = [record for record in records if record.text == first_text]
    lasts = [record for record in records if record.text == last_text]
    if len(firsts) != 1 or len(lasts) != 1 or first_text == last_text:
        return []
    first, last = firsts[0], lasts[0]
    axes = chart_data.get("axes") if isinstance(chart_data.get("axes"), dict) else {}
    category = axes.get("category") if isinstance(axes.get("category"), dict) else {}
    resolved = _category_axis_reversed(category, chart_type)
    if chart_type == "bar":
        expected = first.y < last.y
        drawn = "top-down" if expected else "bottom-up"
    else:
        expected = first.x > last.x
        drawn = "right-to-left" if expected else "left-to-right"
    if expected == resolved:
        return []
    return [
        f"Native PPTX {chart_type} chart fallback lists categories {drawn} "
        f"({categories[0]!r} before {categories[-1]!r}) but the payload resolves "
        f"axes.category.reverse to {str(resolved).lower()}; set axes.category.reverse: "
        f"{str(expected).lower()}"
    ]
