"""Native chart styling and companion text helpers."""

from __future__ import annotations

from typing import Any
from xml.etree import ElementTree as ET

from .marker_attributes import native_import_source, native_json_is_authoritative

from .marker_common import (
    _compact_key,
    _first_present,
    _normalized_fallback_text,
    _visible_fallback_texts,
)



# Adapted from chart_style.py; upstream MIT, Hugo He, 2025-2026.
def _axis_titles(payload: dict[str, Any]) -> dict[str, Any]:
    # Resolve shared symbols at call time; retain the public module contract.
    from .chart_style import (
        _AXIS_TITLE_KEY_GROUPS,
    )
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


def _metadata_text(value: Any) -> str | None:
    # Resolve shared symbols at call time; retain the public module contract.
    from .chart_style import (
        _chart_text_entry,
    )
    entry = _chart_text_entry(value)
    if entry is None:
        return None
    text, _ = entry
    return _normalized_fallback_text(text)


def _native_chart_chrome_errors(elem: ET.Element, payload: dict[str, Any]) -> list[str]:
    # Resolve shared symbols at call time; retain the public module contract.
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
    # Resolve shared symbols at call time; retain the public module contract.
    from .chart_style import (
        _AXIS_TITLE_KEY_GROUPS,
    )
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
