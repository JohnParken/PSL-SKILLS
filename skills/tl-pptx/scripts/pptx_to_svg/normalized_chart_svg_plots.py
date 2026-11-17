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

def _render_bars(
    payload: dict[str, Any],
    categories: list[str],
    series: list[dict[str, Any]],
    styles: list[SeriesVisualStyle],
    segments: list[list[tuple[float, float]]],
    plot: _Rect,
    lo: float,
    hi: float,
    grouping: str,
    *,
    horizontal: bool,
    label_size: float,
    percent: bool,
) -> list[str]:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .normalized_chart_svg import (
        _data_label,
        _entry_color,
        _fmt,
        _label_font_size,
        _map,
        _point_data_labels,
        _text,
    )
    parts: list[str] = []
    stacked = grouping in {"stacked", "percentStacked"}
    category_span = (plot.h if horizontal else plot.w) / max(len(categories), 1)
    cluster = category_span * 0.72
    bar_span = cluster if stacked else cluster / max(len(series), 1)
    for series_index, (item, style, row) in enumerate(zip(series, styles, segments)):
        fill = style.fill or "none"
        stroke = style.stroke or "none"
        for category_index, (start, end) in enumerate(row):
            if item["values"][category_index] is None:
                continue
            offset = 0.0 if stacked else series_index * bar_span
            display_index = (
                len(categories) - 1 - category_index
                if horizontal else category_index
            )
            cluster_start = display_index * category_span + (category_span - cluster) / 2
            if horizontal:
                x1 = _map(end, lo, hi, plot.x, plot.x + plot.w)
                x0 = _map(start, lo, hi, plot.x, plot.x + plot.w)
                x = min(x0, x1)
                y = plot.y + cluster_start + offset
                w = max(abs(x1 - x0), 0.35)
                h = max(bar_span * 0.9, 0.5)
            else:
                y1 = _map(end, lo, hi, plot.y + plot.h, plot.y)
                y0 = _map(start, lo, hi, plot.y + plot.h, plot.y)
                x = plot.x + cluster_start + offset
                y = min(y0, y1)
                w = max(bar_span * 0.9, 0.5)
                h = max(abs(y1 - y0), 0.35)
            parts.append(
                f'<rect x="{_fmt(x)}" y="{_fmt(y)}" width="{_fmt(w)}" '
                f'height="{_fmt(h)}" fill="{fill}" fill-opacity="{_fmt(style.fill_opacity)}" '
                f'stroke="{stroke}" stroke-opacity="{_fmt(style.stroke_opacity)}" '
                f'stroke-width="{_fmt(max(0.4, style.stroke_width))}"/>'
            )
            labels = _point_data_labels(payload, item, category_index)
            if labels:
                value = float(item["values"][category_index])
                normalized_percent = end - start if percent else None
                label = _data_label(
                    labels,
                    str(item.get("name") or ""),
                    categories[category_index],
                    value,
                    percent_value=normalized_percent,
                )
                if label:
                    if horizontal:
                        tx = x1 + (4.0 if end >= start else -4.0)
                        ty = y + h / 2 + label_size * 0.32
                        anchor = "start" if end >= start else "end"
                    else:
                        tx = x + w / 2
                        ty = y1 - 4.0 if end >= start else y1 + label_size + 3.0
                        anchor = "middle"
                    parts.append(_text(
                        tx,
                        ty,
                        label,
                        size=_label_font_size(labels, label_size),
                        anchor=anchor,
                        fill=_entry_color(labels, "#444444"),
                        weight="600" if labels.get("bold") else None,
                        font_family=str(labels.get("font_family") or "Arial"),
                    ))
    return parts


def _render_lines_or_areas(
    payload: dict[str, Any],
    categories: list[str],
    series: list[dict[str, Any]],
    styles: list[SeriesVisualStyle],
    segments: list[list[tuple[float, float]]],
    plot: _Rect,
    lo: float,
    hi: float,
    chart_type: str,
    label_size: float,
    percent: bool,
) -> list[str]:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .normalized_chart_svg import (
        _category_point_x,
        _data_label,
        _entry_color,
        _fmt,
        _label_font_size,
        _map,
        _point_data_labels,
        _points,
        _text,
    )
    parts: list[str] = []
    count = len(categories)
    x_positions = [
        _category_point_x(plot, idx, count)
        for idx in range(count)
    ]
    show_markers = chart_type == "line" and payload.get("line_style") == "lineMarker"
    for series_index, (item, style, row) in enumerate(zip(series, styles, segments)):
        fill_color = style.fill or "none"
        line_color = style.stroke
        top = [
            (x_positions[idx], _map(end, lo, hi, plot.y + plot.h, plot.y))
            for idx, (_start, end) in enumerate(row)
        ]
        runs: list[list[int]] = [[]]
        for idx, value in enumerate(item["values"][:count]):
            if value is None:
                if runs[-1]:
                    runs.append([])
            else:
                runs[-1].append(idx)
        for indices in runs:
            if not indices:
                continue
            points = [top[idx] for idx in indices]
            if chart_type == "area":
                bottom = [
                    (x_positions[idx], _map(row[idx][0], lo, hi, plot.y + plot.h, plot.y))
                    for idx in indices
                ]
                points += list(reversed(bottom))
                parts.append(
                    f'<polygon points="{_points(points)}" fill="{fill_color}" '
                    f'fill-opacity="{_fmt(min(style.fill_opacity, 0.58))}" '
                    f'stroke="{line_color or "none"}" stroke-opacity="{_fmt(style.stroke_opacity)}" '
                    f'stroke-width="{_fmt(max(0.7, style.stroke_width))}" '
                    'stroke-linejoin="round"/>'
                )
            elif line_color is not None and len(points) >= 2:
                # An isolated value has no line segment across a gap. Its
                # marker and data label are rendered independently below.
                parts.append(
                    f'<polyline points="{_points(points)}" fill="none" stroke="{line_color}" '
                    f'stroke-opacity="{_fmt(style.stroke_opacity)}" '
                    f'stroke-width="{_fmt(max(1.0, style.stroke_width))}" '
                    f'stroke-linecap="{style.line_cap}" stroke-linejoin="round"/>'
                )
        if show_markers:
            for idx, (x, y) in enumerate(top):
                if item["values"][idx] is None:
                    continue
                radius = max(2.0, style.marker_size / 2)
                parts.append(
                    f'<circle cx="{_fmt(x)}" cy="{_fmt(y)}" r="{_fmt(radius)}" '
                    f'fill="{style.marker_fill or "none"}" '
                    f'fill-opacity="{_fmt(style.marker_fill_opacity)}" '
                    f'stroke="{style.marker_stroke or "none"}" '
                    f'stroke-opacity="{_fmt(style.marker_stroke_opacity)}" '
                    f'stroke-width="{_fmt(max(0.6, style.marker_stroke_width))}"/>'
                )
        for idx, (x, y) in enumerate(top):
            if item["values"][idx] is None:
                continue
            labels = _point_data_labels(payload, item, idx)
            if labels:
                value = float(item["values"][idx])
                start, end = row[idx]
                normalized_percent = end - start if percent else None
                label = _data_label(
                    labels,
                    str(item.get("name") or ""),
                    categories[idx],
                    value,
                    percent_value=normalized_percent,
                )
                if label:
                    parts.append(_text(
                        x,
                        y - 5.0,
                        label,
                        size=_label_font_size(labels, label_size),
                        anchor="middle",
                        fill=_entry_color(labels, "#444444"),
                        weight="600" if labels.get("bold") else None,
                        font_family=str(labels.get("font_family") or "Arial"),
                    ))
    return parts


def _render_pie(
    payload: dict[str, Any],
    styles: list[SeriesVisualStyle],
    content: _Rect,
    doughnut: bool,
) -> list[str]:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .normalized_chart_svg import (
        _complete_styles,
        _contrast_color,
        _fmt,
        _format_percent,
        _full_ring_path,
        _sector_path,
        _text,
    )
    categories = [str(value) for value in payload.get("categories") or []]
    series = payload.get("series") or []
    if not categories or len(series) != 1:
        return []
    values = [abs(float(value)) for value in series[0].get("values") or []]
    total = sum(values)
    styles = _complete_styles(styles, len(categories))
    label_size = max(6.0, min(10.0, content.h * 0.035, content.w * 0.018))
    radius = max(5.0, min(content.w, content.h) * 0.34)
    inner_radius = radius * (
        float(payload.get("hole_size", 75)) / 100.0
        if doughnut
        else 0.0
    )
    cx = content.x + content.w / 2
    cy = content.y + content.h / 2
    if total <= 0:
        parts = [
            f'<circle cx="{_fmt(cx)}" cy="{_fmt(cy)}" r="{_fmt(radius)}" '
            'fill="none" stroke="#B8C0CC" stroke-width="1.2" stroke-dasharray="4 3"/>'
        ]
        if doughnut:
            parts.append(
                f'<circle cx="{_fmt(cx)}" cy="{_fmt(cy)}" r="{_fmt(inner_radius)}" '
                'fill="none" stroke="#D5DAE1" stroke-width="1"/>'
            )
        parts.append(
            _text(cx, cy + label_size * 0.32, "0", size=label_size, anchor="middle")
        )
        return parts
    parts: list[str] = []
    angle = -math.pi / 2
    for idx, (category, value, style) in enumerate(zip(categories, values, styles)):
        sweep = math.tau * value / total
        end = angle + sweep
        # Pie styles are completed before rendering, so ``None`` here means
        # the source explicitly used ``noFill`` rather than an absent style.
        fill = style.fill or "none"
        stroke = style.stroke or "none"
        if sweep >= math.tau - 1e-9:
            if doughnut:
                path = _full_ring_path(cx, cy, radius, inner_radius)
                parts.append(
                    f'<path d="{path}" fill="{fill}" fill-opacity="{_fmt(style.fill_opacity)}" '
                    f'stroke="{stroke}" stroke-width="{_fmt(max(0.6, style.stroke_width))}" '
                    'fill-rule="evenodd"/>'
                )
            else:
                parts.append(
                    f'<circle cx="{_fmt(cx)}" cy="{_fmt(cy)}" r="{_fmt(radius)}" '
                    f'fill="{fill}" fill-opacity="{_fmt(style.fill_opacity)}" '
                    f'stroke="{stroke}" stroke-width="{_fmt(max(0.6, style.stroke_width))}"/>'
                )
        else:
            path = _sector_path(
                cx,
                cy,
                radius,
                angle,
                end,
                inner_radius,
            )
            parts.append(
                f'<path d="{path}" fill="{fill}" fill-opacity="{_fmt(style.fill_opacity)}" '
                f'stroke="{stroke}" stroke-opacity="{_fmt(style.stroke_opacity)}" '
                f'stroke-width="{_fmt(max(0.6, style.stroke_width))}" fill-rule="evenodd"/>'
            )
        mid = angle + sweep / 2
        label_radius = radius * (0.87 if doughnut else 0.68)
        lx = cx + math.cos(mid) * label_radius
        ly = cy + math.sin(mid) * label_radius + label_size * 0.32
        percent = value / total
        label = f"{category} {_format_percent(percent)}"
        parts.append(
            _text(
                lx,
                ly,
                label,
                size=label_size,
                anchor="middle",
                fill="#444444" if fill == "none" else _contrast_color(fill),
            )
        )
        angle = end
    return parts


def _render_xy(
    payload: dict[str, Any],
    styles: list[SeriesVisualStyle],
    content: _Rect,
    chart_type: str,
) -> list[str]:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .normalized_chart_svg import (
        _DEFAULT_COLORS,
        _Rect,
        _complete_styles,
        _fmt,
        _map,
        _nice_scale,
        _points,
        _xy_axis_titles,
        _xy_grid_and_ticks,
    )
    series = payload.get("series") or []
    if not series:
        return []
    styles = _complete_styles(styles, len(series))
    axis_titles = payload.get("axis_titles") if isinstance(payload.get("axis_titles"), dict) else {}
    label_size = max(6.0, min(11.0, content.h * 0.037, content.w * 0.021))
    left = max(40.0, content.w * 0.09) + (14.0 if axis_titles.get("y") else 0.0)
    bottom = max(26.0, content.h * 0.1) + (14.0 if axis_titles.get("x") else 0.0)
    plot = _Rect(
        content.x + left,
        content.y + 6.0,
        max(12.0, content.w - left - 10.0),
        max(12.0, content.h - bottom - 8.0),
    )
    x_values = [float(value) for item in series for value in item.get("x") or []]
    y_values = [float(value) for item in series for value in item.get("y") or []]
    x_lo, x_hi, x_ticks = _nice_scale(x_values, include_zero=False)
    y_lo, y_hi, y_ticks = _nice_scale(y_values, include_zero=False)
    axes = payload.get("axes")
    x_axis = axes.get("x") if isinstance(axes, dict) else None
    y_axis = axes.get("y") if isinstance(axes, dict) else None
    x_major_gridlines = (
        bool(x_axis.get("major_gridlines", False))
        if isinstance(x_axis, dict)
        else False
    )
    y_major_gridlines = (
        bool(y_axis.get("major_gridlines", True))
        if isinstance(y_axis, dict)
        else True
    )
    parts = _xy_grid_and_ticks(
        plot,
        x_ticks,
        y_ticks,
        x_lo,
        x_hi,
        y_lo,
        y_hi,
        label_size,
        show_x_gridlines=x_major_gridlines,
        show_y_gridlines=y_major_gridlines,
    )
    parts.extend(_xy_axis_titles(plot, content, axis_titles, label_size))
    nonnegative_bubble_sizes = [
        float(value)
        for item in series
        for value in item.get("sizes") or []
        if float(value) >= 0
    ]
    bubble_max = max(max(nonnegative_bubble_sizes or [0.0]), 1e-12)
    scatter_style = str(payload.get("scatter_style") or "marker")
    has_line = chart_type == "scatter" and scatter_style in {"line", "lineMarker", "smooth", "smoothMarker"}
    has_marker = chart_type == "bubble" or scatter_style in {"marker", "lineMarker", "smoothMarker"}
    for idx, (item, style) in enumerate(zip(series, styles)):
        color = style.stroke or style.fill or style.marker_fill or _DEFAULT_COLORS[idx % len(_DEFAULT_COLORS)]
        points = [
            (
                _map(float(x), x_lo, x_hi, plot.x, plot.x + plot.w),
                _map(float(y), y_lo, y_hi, plot.y + plot.h, plot.y),
            )
            for x, y in zip(item.get("x") or [], item.get("y") or [])
        ]
        if has_line and style.stroke is not None and len(points) >= 2:
            parts.append(
                f'<polyline points="{_points(points)}" fill="none" stroke="{color}" '
                f'stroke-opacity="{_fmt(style.stroke_opacity)}" '
                f'stroke-width="{_fmt(max(1.0, style.stroke_width))}" '
                f'stroke-linecap="{style.line_cap}" stroke-linejoin="round"/>'
            )
        if has_marker:
            sizes = item.get("sizes") or [style.marker_size * style.marker_size] * len(points)
            for point_index, (x, y) in enumerate(points):
                if chart_type == "bubble":
                    bubble_size = float(sizes[point_index])
                    if bubble_size < 0:
                        continue
                    radius = 3.0 + 13.0 * math.sqrt(bubble_size / bubble_max)
                else:
                    radius = max(2.0, style.marker_size / 2)
                fill = (
                    style.fill or "none"
                    if chart_type == "bubble"
                    else style.marker_fill or "none"
                )
                stroke = (
                    style.stroke or "none"
                    if chart_type == "bubble"
                    else style.marker_stroke or "none"
                )
                parts.append(
                    f'<circle cx="{_fmt(x)}" cy="{_fmt(y)}" r="{_fmt(radius)}" '
                    f'fill="{fill}" fill-opacity="{_fmt(style.marker_fill_opacity if chart_type == "scatter" else style.fill_opacity)}" '
                    f'stroke="{stroke}" stroke-opacity="{_fmt(style.marker_stroke_opacity)}" '
                    f'stroke-width="{_fmt(max(0.6, style.marker_stroke_width))}"/>'
                )
    return parts


