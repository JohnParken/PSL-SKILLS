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

def _vertical_grid_and_ticks(
    plot: _Rect,
    ticks: list[float],
    lo: float,
    hi: float,
    size: float,
    percent: bool,
    *,
    show_labels: bool,
    show_gridlines: bool = True,
) -> list[str]:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .normalized_chart_svg import (
        _fmt,
        _format_number,
        _format_percent,
        _map,
        _text,
    )
    parts: list[str] = []
    for tick in ticks:
        y = _map(tick, lo, hi, plot.y + plot.h, plot.y)
        if show_gridlines:
            parts.append(
                f'<line x1="{_fmt(plot.x)}" y1="{_fmt(y)}" x2="{_fmt(plot.x + plot.w)}" '
                f'y2="{_fmt(y)}" stroke="#D9D9D9" stroke-width="0.7"/>'
            )
        if show_labels:
            label = _format_percent(tick) if percent else _format_number(tick)
            parts.append(_text(plot.x - 5.0, y + size * 0.32, label, size=size, anchor="end", fill="#666666"))
    parts.append(
        f'<line x1="{_fmt(plot.x)}" y1="{_fmt(plot.y)}" x2="{_fmt(plot.x)}" '
        f'y2="{_fmt(plot.y + plot.h)}" stroke="#808080" stroke-width="1"/>'
    )
    return parts


def _horizontal_grid_and_ticks(
    plot: _Rect,
    ticks: list[float],
    lo: float,
    hi: float,
    size: float,
    percent: bool,
    *,
    show_labels: bool,
) -> list[str]:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .normalized_chart_svg import (
        _fmt,
        _format_number,
        _format_percent,
        _map,
        _text,
    )
    parts: list[str] = []
    for tick in ticks:
        x = _map(tick, lo, hi, plot.x, plot.x + plot.w)
        parts.append(
            f'<line x1="{_fmt(x)}" y1="{_fmt(plot.y)}" x2="{_fmt(x)}" '
            f'y2="{_fmt(plot.y + plot.h)}" stroke="#D9D9D9" stroke-width="0.7"/>'
        )
        if show_labels:
            label = _format_percent(tick) if percent else _format_number(tick)
            parts.append(_text(x, plot.y + plot.h + size + 4.0, label, size=size, anchor="middle", fill="#666666"))
    parts.append(
        f'<line x1="{_fmt(plot.x)}" y1="{_fmt(plot.y + plot.h)}" '
        f'x2="{_fmt(plot.x + plot.w)}" y2="{_fmt(plot.y + plot.h)}" '
        'stroke="#808080" stroke-width="1"/>'
    )
    return parts


def _column_category_labels(
    plot: _Rect,
    categories: list[str],
    size: float,
    *,
    point_aligned: bool,
) -> list[str]:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .normalized_chart_svg import (
        _category_point_x,
        _fmt,
    )
    parts: list[str] = []
    span = plot.w / max(len(categories), 1)
    rotate = len(categories) > 8 or any(len(value) > 10 for value in categories)
    for idx, category in enumerate(categories):
        if point_aligned:
            x = _category_point_x(plot, idx, len(categories))
        else:
            x = plot.x + span * (idx + 0.5)
        y = plot.y + plot.h + size + 5.0
        transform = f' transform="rotate(-35 {_fmt(x)} {_fmt(y)})"' if rotate else ""
        anchor = "end" if rotate else "middle"
        parts.append(
            f'<text x="{_fmt(x)}" y="{_fmt(y)}" text-anchor="{anchor}" '
            f'font-family="Arial" font-size="{_fmt(size)}" fill="#555555"{transform}>'
            f'{html.escape(category)}</text>'
        )
    return parts


def _category_point_x(plot: _Rect, index: int, count: int) -> float:
    """Align line/area data points and their category labels."""
    if count <= 1:
        return plot.x + plot.w / 2
    return plot.x + plot.w * index / (count - 1)


def _bar_category_labels(plot: _Rect, categories: list[str], size: float) -> list[str]:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .normalized_chart_svg import (
        _text,
    )
    span = plot.h / max(len(categories), 1)
    return [
        _text(
            plot.x - 6.0,
            plot.y + span * (len(categories) - idx - 0.5) + size * 0.32,
            category,
            size=size,
            anchor="end",
            fill="#555555",
        )
        for idx, category in enumerate(categories)
    ]


def _xy_grid_and_ticks(
    plot: _Rect,
    x_ticks: list[float],
    y_ticks: list[float],
    x_lo: float,
    x_hi: float,
    y_lo: float,
    y_hi: float,
    size: float,
    *,
    show_x_gridlines: bool,
    show_y_gridlines: bool,
) -> list[str]:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .normalized_chart_svg import (
        _fmt,
        _format_number,
        _map,
        _text,
        _vertical_grid_and_ticks,
    )
    parts = _vertical_grid_and_ticks(
        plot,
        y_ticks,
        y_lo,
        y_hi,
        size,
        False,
        show_labels=True,
        show_gridlines=show_y_gridlines,
    )
    for tick in x_ticks:
        x = _map(tick, x_lo, x_hi, plot.x, plot.x + plot.w)
        if show_x_gridlines:
            parts.append(
                f'<line x1="{_fmt(x)}" y1="{_fmt(plot.y)}" x2="{_fmt(x)}" '
                f'y2="{_fmt(plot.y + plot.h)}" stroke="#D9D9D9" stroke-width="0.7"/>'
            )
        parts.append(_text(x, plot.y + plot.h + size + 4.0, _format_number(tick), size=size, anchor="middle", fill="#666666"))
    parts.append(
        f'<line x1="{_fmt(plot.x)}" y1="{_fmt(plot.y + plot.h)}" '
        f'x2="{_fmt(plot.x + plot.w)}" y2="{_fmt(plot.y + plot.h)}" '
        'stroke="#808080" stroke-width="1"/>'
    )
    return parts


def _axis_titles(
    plot: _Rect,
    content: _Rect,
    titles: dict[str, Any],
    size: float,
    *,
    is_bar: bool,
) -> list[str]:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .normalized_chart_svg import (
        _draw_axis_titles,
    )
    category = str(titles.get("category") or "")
    value = str(titles.get("value") or "")
    x_title = value if is_bar else category
    y_title = category if is_bar else value
    return _draw_axis_titles(plot, content, x_title, y_title, size)


def _xy_axis_titles(
    plot: _Rect,
    content: _Rect,
    titles: dict[str, Any],
    size: float,
) -> list[str]:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .normalized_chart_svg import (
        _draw_axis_titles,
    )
    return _draw_axis_titles(
        plot,
        content,
        str(titles.get("x") or ""),
        str(titles.get("y") or ""),
        size,
    )


def _draw_axis_titles(
    plot: _Rect,
    content: _Rect,
    x_title: str,
    y_title: str,
    size: float,
) -> list[str]:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .normalized_chart_svg import (
        _fmt,
        _text,
    )
    parts: list[str] = []
    if x_title:
        parts.append(_text(plot.x + plot.w / 2, content.y + content.h - 2.0, x_title, size=size, anchor="middle", weight="600"))
    if y_title:
        x = content.x + size
        y = plot.y + plot.h / 2
        parts.append(
            f'<text x="{_fmt(x)}" y="{_fmt(y)}" text-anchor="middle" '
            f'font-family="Arial" font-size="{_fmt(size)}" font-weight="600" '
            f'fill="#444444" transform="rotate(-90 {_fmt(x)} {_fmt(y)})">'
            f'{html.escape(y_title)}</text>'
        )
    return parts


def _legend_entries(
    payload: dict[str, Any],
    styles: list[SeriesVisualStyle],
) -> list[tuple[str, str]]:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .normalized_chart_svg import (
        _DEFAULT_COLORS,
        _complete_styles,
    )
    chart_type = str(payload.get("type") or "")
    if chart_type in {"pie", "doughnut"}:
        labels = [str(value) for value in payload.get("categories") or []]
    elif chart_type == "combo":
        labels = [
            str(series.get("name") or f"Series {index + 1}")
            for index, series in enumerate(
                series
                for plot_payload in payload.get("plots") or []
                if isinstance(plot_payload, dict)
                for series in plot_payload.get("series") or []
                if isinstance(series, dict)
            )
        ]
    else:
        labels = [str(item.get("name") or f"Series {idx + 1}") for idx, item in enumerate(payload.get("series") or [])]
    completed = _complete_styles(styles, len(labels))
    return [
        (
            label,
            style.fill or style.stroke or style.marker_fill or _DEFAULT_COLORS[idx % len(_DEFAULT_COLORS)],
        )
        for idx, (label, style) in enumerate(zip(labels, completed))
    ]


def _render_legend(
    entries: list[tuple[str, str]],
    rect: _Rect,
    position: str,
) -> list[str]:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .normalized_chart_svg import (
        _fmt,
        _text,
    )
    if not entries:
        return []
    size = max(6.0, min(10.0, rect.h * 0.18 if position.lower() in {"l", "r", "left", "right"} else 9.0))
    parts: list[str] = []
    if position.lower() in {"l", "r", "left", "right"}:
        row_h = min(18.0, rect.h / max(len(entries), 1))
        for idx, (label, color) in enumerate(entries):
            y = rect.y + row_h * (idx + 0.5)
            parts.append(
                f'<rect x="{_fmt(rect.x + 5)}" y="{_fmt(y - 4)}" width="8" height="8" fill="{color}"/>'
            )
            parts.append(_text(rect.x + 18.0, y + size * 0.32, label, size=size, anchor="start", fill="#444444"))
        return parts

    columns = max(1, min(4, int(rect.w // 130) or 1))
    rows = math.ceil(len(entries) / columns)
    row_h = rect.h / max(rows, 1)
    col_w = rect.w / columns
    for idx, (label, color) in enumerate(entries):
        row = idx // columns
        col = idx % columns
        x = rect.x + col * col_w + 5.0
        y = rect.y + row_h * (row + 0.5)
        parts.append(
            f'<rect x="{_fmt(x)}" y="{_fmt(y - 4)}" width="8" height="8" fill="{color}"/>'
        )
        parts.append(_text(x + 13.0, y + size * 0.32, label, size=size, anchor="start", fill="#444444"))
    return parts


