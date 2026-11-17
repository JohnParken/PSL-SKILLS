"""Deterministic normalized SVG fallback for parsed classic charts.

The renderer is intentionally independent from the native-chart OOXML
emitter.  It visualizes only data and semantics that ``chart_to_svg`` has
already accepted, so a rendering failure can never invalidate an otherwise
valid editable-chart payload.
"""

from __future__ import annotations

import html
import math
import re
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Any



# Upstream MIT, Hugo He 2025-2026; algorithms retained during module split.
from .normalized_chart_svg import (
    SeriesVisualStyle,
    _Rect,
)

def render_normalized_chart_svg(
    payload: dict[str, Any],
    styles: list[SeriesVisualStyle],
) -> str | None:
    """Render a readable normalized fallback for supported classic charts."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .normalized_chart_svg import (
        _Rect,
        _legend_entries,
        _outer_layout,
        _render_category,
        _render_combo,
        _render_legend,
        _render_pie,
        _render_xy,
    )
    chart_type = str(payload.get("type") or "")
    if chart_type not in {
        "area", "bar", "column", "doughnut", "line", "pie",
        "scatter", "bubble", "combo",
    }:
        return None

    try:
        bounds = _Rect(
            float(payload["x"]),
            float(payload["y"]),
            float(payload["width"]),
            float(payload["height"]),
        )
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    if (
        bounds.w <= 0
        or bounds.h <= 0
        or not all(math.isfinite(value) for value in vars(bounds).values())
    ):
        return None

    legend_entries = _legend_entries(payload, styles)
    content, legend_rect, title_parts = _outer_layout(
        payload,
        bounds,
        len(legend_entries),
    )
    parts = [*title_parts]
    if chart_type in {"pie", "doughnut"}:
        parts.extend(_render_pie(payload, styles, content, chart_type == "doughnut"))
    elif chart_type == "combo":
        parts.extend(_render_combo(payload, styles, content))
    elif chart_type in {"scatter", "bubble"}:
        parts.extend(_render_xy(payload, styles, content, chart_type))
    else:
        parts.extend(_render_category(payload, styles, content, chart_type))
    if legend_rect is not None:
        parts.extend(
            _render_legend(
                legend_entries,
                legend_rect,
                str(payload.get("legend_position") or "b"),
            )
        )
    return "\n".join(part for part in parts if part)


def _outer_layout(
    payload: dict[str, Any],
    bounds: _Rect,
    legend_count: int,
) -> tuple[_Rect, _Rect | None, list[str]]:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .normalized_chart_svg import (
        _Rect,
        _entry_color,
        _entry_text,
        _entry_value,
        _font_size,
        _text,
    )
    pad = max(4.0, min(bounds.w, bounds.h) * 0.018)
    content = _Rect(
        bounds.x + pad,
        bounds.y + pad,
        max(1.0, bounds.w - 2 * pad),
        max(1.0, bounds.h - 2 * pad),
    )
    title_parts: list[str] = []
    title = _entry_text(payload.get("title"))
    subtitle = _entry_text(payload.get("subtitle"))
    if title:
        title_size = _font_size(
            _entry_value(payload.get("title"), "font_size"),
            min(18.0, max(10.0, bounds.h * 0.055)),
            bounds,
        )
        title_y = content.y + title_size
        title_parts.append(
            _text(
                content.x + content.w / 2,
                title_y,
                title,
                size=title_size,
                anchor="middle",
                weight="600",
                fill=_entry_color(payload.get("title"), "#333333"),
            )
        )
        used = title_size + max(4.0, title_size * 0.35)
        content = _Rect(content.x, content.y + used, content.w, max(1.0, content.h - used))
    if subtitle:
        subtitle_size = _font_size(
            _entry_value(payload.get("subtitle"), "font_size"),
            min(12.0, max(8.0, bounds.h * 0.035)),
            bounds,
        )
        title_parts.append(
            _text(
                content.x + content.w / 2,
                content.y + subtitle_size,
                subtitle,
                size=subtitle_size,
                anchor="middle",
                fill=_entry_color(payload.get("subtitle"), "#666666"),
            )
        )
        used = subtitle_size + max(3.0, subtitle_size * 0.3)
        content = _Rect(content.x, content.y + used, content.w, max(1.0, content.h - used))

    if not payload.get("show_legend") or legend_count <= 0:
        return content, None, title_parts
    position = str(payload.get("legend_position") or "b").lower()
    if position in {"l", "r", "left", "right"}:
        legend_w = min(max(86.0, content.w * 0.24), max(1.0, content.w * 0.38))
        if position in {"l", "left"}:
            legend = _Rect(content.x, content.y, legend_w, content.h)
            content = _Rect(
                content.x + legend_w,
                content.y,
                max(1.0, content.w - legend_w),
                content.h,
            )
        else:
            legend = _Rect(content.x + content.w - legend_w, content.y, legend_w, content.h)
            content = _Rect(content.x, content.y, max(1.0, content.w - legend_w), content.h)
        return content, legend, title_parts

    columns = max(1, min(4, int(content.w // 130) or 1))
    rows = math.ceil(legend_count / columns)
    legend_h = min(content.h * 0.32, max(20.0, rows * 17.0 + 4.0))
    if position in {"t", "top"}:
        legend = _Rect(content.x, content.y, content.w, legend_h)
        content = _Rect(
            content.x,
            content.y + legend_h,
            content.w,
            max(1.0, content.h - legend_h),
        )
    else:
        legend = _Rect(content.x, content.y + content.h - legend_h, content.w, legend_h)
        content = _Rect(content.x, content.y, content.w, max(1.0, content.h - legend_h))
    return content, legend, title_parts


def _render_category(
    payload: dict[str, Any],
    styles: list[SeriesVisualStyle],
    content: _Rect,
    chart_type: str,
) -> list[str]:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .normalized_chart_svg import (
        _Rect,
        _axis_titles,
        _bar_category_labels,
        _category_labels,
        _category_segments,
        _column_category_labels,
        _complete_styles,
        _horizontal_grid_and_ticks,
        _nice_scale,
        _render_bars,
        _render_lines_or_areas,
        _vertical_grid_and_ticks,
    )
    categories = _category_labels(payload)
    series = payload.get("series") or []
    if categories is None or not categories or not series:
        return []
    styles = _complete_styles(styles, len(series))
    axis_titles = payload.get("axis_titles") if isinstance(payload.get("axis_titles"), dict) else {}
    is_bar = chart_type == "bar"
    label_size = max(6.0, min(11.0, content.h * 0.037, content.w * 0.021))
    left = max(34.0, content.w * 0.075)
    bottom = max(25.0, content.h * 0.105)
    if is_bar:
        left = min(content.w * 0.34, max(66.0, content.w * 0.18))
        bottom = max(24.0, content.h * 0.085)
    if axis_titles.get("value"):
        left += 14.0 if not is_bar else 0.0
        bottom += 14.0 if is_bar else 0.0
    if axis_titles.get("category"):
        bottom += 14.0 if not is_bar else 0.0
        left += 14.0 if is_bar else 0.0
    plot = _Rect(
        content.x + left,
        content.y + 5.0,
        max(12.0, content.w - left - 10.0),
        max(12.0, content.h - bottom - 8.0),
    )

    grouping = str(payload.get("grouping") or ("clustered" if chart_type in {"bar", "column"} else "standard"))
    segments, percent = _category_segments(series, len(categories), grouping)
    scale_values = [value for row in segments for pair in row for value in pair]
    lo, hi, ticks = _nice_scale(scale_values, include_zero=True, percent=percent)
    parts: list[str] = []
    show_value_labels = payload.get("show_value_axis_labels") is not False

    if is_bar:
        parts.extend(_horizontal_grid_and_ticks(
            plot,
            ticks,
            lo,
            hi,
            label_size,
            percent,
            show_labels=show_value_labels,
        ))
        parts.extend(_bar_category_labels(plot, categories, label_size))
    else:
        parts.extend(_vertical_grid_and_ticks(
            plot,
            ticks,
            lo,
            hi,
            label_size,
            percent,
            show_labels=show_value_labels,
        ))
        parts.extend(_column_category_labels(
            plot,
            categories,
            label_size,
            point_aligned=chart_type in {"line", "area"},
        ))
    parts.extend(_axis_titles(plot, content, axis_titles, label_size, is_bar=is_bar))

    if chart_type in {"bar", "column"}:
        parts.extend(
            _render_bars(
                payload,
                categories,
                series,
                styles,
                segments,
                plot,
                lo,
                hi,
                grouping,
                horizontal=is_bar,
                label_size=label_size,
                percent=percent,
            )
        )
    else:
        parts.extend(
            _render_lines_or_areas(
                payload,
                categories,
                series,
                styles,
                segments,
                plot,
                lo,
                hi,
                chart_type,
                label_size,
                percent,
            )
        )
    return parts


def _render_combo(
    payload: dict[str, Any],
    styles: list[SeriesVisualStyle],
    content: _Rect,
) -> list[str]:
    """Render supported category combo plots against one shared value scale."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .normalized_chart_svg import (
        _Rect,
        _category_segments,
        _column_category_labels,
        _complete_styles,
        _nice_scale,
        _render_bars,
        _render_lines_or_areas,
        _vertical_grid_and_ticks,
    )
    categories = [str(value) for value in payload.get("categories") or []]
    plots = payload.get("plots") or []
    if not categories or not isinstance(plots, list) or not plots:
        return []
    all_series = [
        series
        for plot_payload in plots
        if isinstance(plot_payload, dict)
        for series in plot_payload.get("series") or []
        if isinstance(series, dict)
    ]
    values = [
        float(value)
        for series in all_series
        for value in series.get("values") or []
        if value is not None
    ]
    if not all_series or not values:
        return []

    label_size = max(6.0, min(11.0, content.h * 0.037, content.w * 0.021))
    axes = payload.get("axes") if isinstance(payload.get("axes"), dict) else {}
    value_axis = axes.get("value") if isinstance(axes.get("value"), dict) else {}
    show_value_labels = value_axis.get("visible") is not False
    show_gridlines = value_axis.get("major_gridlines") is not False
    left = max(12.0, content.w * 0.035)
    if show_value_labels:
        left = max(34.0, content.w * 0.075)
    bottom = max(25.0, content.h * 0.105)
    plot_rect = _Rect(
        content.x + left,
        content.y + 5.0,
        max(12.0, content.w - left - 10.0),
        max(12.0, content.h - bottom - 8.0),
    )
    lo, hi, ticks = _nice_scale(values, include_zero=True)
    parts = _vertical_grid_and_ticks(
        plot_rect,
        ticks,
        lo,
        hi,
        label_size,
        False,
        show_labels=show_value_labels,
        show_gridlines=show_gridlines,
    )
    parts.extend(
        _column_category_labels(
            plot_rect,
            categories,
            label_size,
            point_aligned=False,
        )
    )

    style_offset = 0
    for plot_payload in plots:
        if not isinstance(plot_payload, dict):
            continue
        plot_type = str(plot_payload.get("type") or "")
        plot_series = plot_payload.get("series") or []
        if not isinstance(plot_series, list) or not plot_series:
            continue
        plot_styles = _complete_styles(
            styles[style_offset:style_offset + len(plot_series)],
            len(plot_series),
        )
        style_offset += len(plot_series)
        grouping = str(plot_payload.get("grouping") or "standard")
        segments, percent = _category_segments(
            plot_series,
            len(categories),
            grouping,
        )
        normalized_plot = {
            **payload,
            **plot_payload,
            "categories": categories,
            "series": plot_series,
        }
        if plot_type == "column":
            parts.extend(
                _render_bars(
                    normalized_plot,
                    categories,
                    plot_series,
                    plot_styles,
                    segments,
                    plot_rect,
                    lo,
                    hi,
                    grouping,
                    horizontal=False,
                    label_size=label_size,
                    percent=percent,
                )
            )
        elif plot_type in {"line", "area"}:
            category_span = plot_rect.w / max(len(categories), 1)
            point_plot = _Rect(
                plot_rect.x + category_span / 2.0,
                plot_rect.y,
                max(1.0, plot_rect.w - category_span),
                plot_rect.h,
            )
            parts.extend(
                _render_lines_or_areas(
                    normalized_plot,
                    categories,
                    plot_series,
                    plot_styles,
                    segments,
                    point_plot,
                    lo,
                    hi,
                    plot_type,
                    label_size,
                    percent,
                )
            )
    return parts


def _category_labels(payload: dict[str, Any]) -> list[str] | None:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .normalized_chart_svg import (
        _excel_1900_date_parts,
    )
    raw_categories = payload.get("categories") or []
    axes = payload.get("axes")
    category_axis = axes.get("category") if isinstance(axes, dict) else None
    if not isinstance(category_axis, dict) or category_axis.get("kind") != "date":
        return [str(value) for value in raw_categories]

    number_format = str(category_axis.get("number_format") or "").lower()
    normalized_format = re.sub(r'\\.|"[^"]*"|\[[^]]*]', "", number_format)
    labels: list[str] = []
    for raw_value in raw_categories:
        try:
            serial = float(raw_value)
        except (TypeError, ValueError, OverflowError):
            return None
        if not math.isfinite(serial):
            return None
        date_parts = _excel_1900_date_parts(serial)
        if date_parts is None:
            return None
        year, month, day = date_parts
        if "yyyy-mm-dd" in normalized_format:
            labels.append(f"{year:04d}-{month:02d}-{day:02d}")
        elif "mm/dd/yyyy" in normalized_format:
            labels.append(f"{month:02d}/{day:02d}/{year:04d}")
        elif "m/d/yyyy" in normalized_format:
            labels.append(f"{month}/{day}/{year:04d}")
        else:
            labels.append(f"{year:04d}-{month:02d}-{day:02d}")
    return labels


def _excel_1900_date_parts(serial: float) -> tuple[int, int, int] | None:
    day_number = math.floor(serial)
    if day_number < 1:
        return None
    if day_number == 60:
        return 1900, 2, 29
    epoch = date(1899, 12, 31) if day_number < 60 else date(1899, 12, 30)
    try:
        value = epoch + timedelta(days=day_number)
    except (OverflowError, ValueError):
        return None
    return value.year, value.month, value.day


def _category_segments(
    series: list[dict[str, Any]],
    count: int,
    grouping: str,
) -> tuple[list[list[tuple[float, float]]], bool]:
    raw = [
        [float(value) if value is not None else 0.0 for value in item.get("values", [])[:count]]
        for item in series
    ]
    percent = grouping == "percentStacked"
    if percent:
        positive = [sum(max(row[idx], 0.0) for row in raw) for idx in range(count)]
        negative = [sum(abs(min(row[idx], 0.0)) for row in raw) for idx in range(count)]
        for row in raw:
            for idx, value in enumerate(row):
                denominator = positive[idx] if value >= 0 else negative[idx]
                row[idx] = value / denominator if denominator else 0.0
    if grouping not in {"stacked", "percentStacked"}:
        return [[(0.0, value) for value in row] for row in raw], percent

    positive_base = [0.0] * count
    negative_base = [0.0] * count
    result: list[list[tuple[float, float]]] = []
    for row in raw:
        segments: list[tuple[float, float]] = []
        for idx, value in enumerate(row):
            if value >= 0:
                start = positive_base[idx]
                positive_base[idx] += value
                end = positive_base[idx]
            else:
                start = negative_base[idx]
                negative_base[idx] += value
                end = negative_base[idx]
            segments.append((start, end))
        result.append(segments)
    return result, percent


