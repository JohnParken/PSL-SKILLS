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

def _data_label(
    config: dict[str, Any],
    series: str,
    category: str,
    value: float,
    *,
    percent_value: float | None,
) -> str:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .normalized_chart_svg import (
        _format_data_label_value,
    )
    if config.get("text") is not None:
        return str(config["text"])
    fields: list[str] = []
    if config.get("show_series"):
        fields.append(series)
    if config.get("show_category"):
        fields.append(category)
    if config.get("show_value"):
        fields.append(_format_data_label_value(
            value,
            config.get("number_format"),
        ))
    if config.get("show_percent"):
        if percent_value is None:
            raise ValueError(
                "normalized percent labels require percent-stacked segments"
            )
        number_format = config.get("number_format")
        normalized_format = str(number_format or "").strip()
        percent_format = (
            "0%"
            if not normalized_format or normalized_format.lower() == "general"
            else normalized_format
        )
        fields.append(_format_data_label_value(
            percent_value,
            percent_format,
        ))
    return " · ".join(field for field in fields if field)


def _point_data_labels(
    payload: dict[str, Any],
    series: dict[str, Any],
    point_index: int,
) -> dict[str, Any] | None:
    root_labels = payload.get("data_labels")
    series_labels = series.get("data_labels")
    config = series_labels if isinstance(series_labels, dict) else root_labels
    if not isinstance(config, dict):
        return None
    points = config.get("points")
    if not isinstance(points, list):
        return config
    point = next(
        (
            item for item in points
            if isinstance(item, dict) and item.get("idx") == point_index
        ),
        None,
    )
    if point is None:
        inherited = {
            key: value
            for key, value in config.items()
            if key not in {"points", "source_ooxml"}
        }
        return inherited if any(
            inherited.get(field)
            for field in (
                "show_category",
                "show_percent",
                "show_series",
                "show_value",
            )
        ) else None
    if point.get("delete") is True:
        return None
    return {**config, **point}


def _label_font_size(config: dict[str, Any], fallback: float) -> float:
    try:
        size = float(config.get("font_size", fallback - 1.0))
    except (TypeError, ValueError, OverflowError):
        size = fallback - 1.0
    return max(6.0, min(size, 24.0))


def _format_data_label_value(value: float, number_format: Any) -> str:
    """Render the safe numeric subset used by normalized data labels.

    Unknown Excel format programs raise so the caller falls back to the
    reconstruction-only route instead of displaying a materially wrong label.
    """
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .normalized_chart_svg import (
        _format_number,
    )
    code = str(number_format or "").strip()
    if not code or code.lower() == "general":
        return _format_number(value)
    match = re.fullmatch(
        r"(?P<prefix>[$¥￥€£]?)(?P<group>#,##)?0"
        r"(?P<decimals>\.0+)?(?P<percent>%?)",
        code,
    )
    if match is None:
        raise ValueError(f"unsupported normalized data-label format: {code!r}")
    scaled = value * 100.0 if match.group("percent") else value
    decimals = len((match.group("decimals") or "").lstrip("."))
    grouping = "," if match.group("group") else ""
    quantum = Decimal(1).scaleb(-decimals)
    rounded = Decimal(str(abs(scaled))).quantize(
        quantum,
        rounding=ROUND_HALF_UP,
    )
    absolute = format(rounded, f"{grouping}.{decimals}f")
    sign = "-" if scaled < 0 else ""
    return (
        f"{sign}{match.group('prefix')}{absolute}{match.group('percent')}"
    )


def _nice_scale(
    values: list[float],
    *,
    include_zero: bool,
    percent: bool = False,
) -> tuple[float, float, list[float]]:
    finite = [value for value in values if math.isfinite(value)]
    if not finite:
        finite = [0.0, 1.0]
    lo = min(finite)
    hi = max(finite)
    if include_zero:
        lo = min(lo, 0.0)
        hi = max(hi, 0.0)
    if percent:
        lo = min(lo, 0.0)
        hi = max(hi, 1.0)
    if math.isclose(lo, hi):
        delta = max(abs(lo) * 0.1, 1.0)
        lo -= delta
        hi += delta
    raw_step = (hi - lo) / 5.0
    magnitude = 10 ** math.floor(math.log10(raw_step))
    normalized = raw_step / magnitude
    nice = 1.0 if normalized <= 1 else 2.0 if normalized <= 2 else 2.5 if normalized <= 2.5 else 5.0 if normalized <= 5 else 10.0
    step = nice * magnitude
    nice_lo = math.floor(lo / step) * step
    nice_hi = math.ceil(hi / step) * step
    count = max(1, int(round((nice_hi - nice_lo) / step)))
    ticks = [nice_lo + idx * step for idx in range(count + 1)]
    return nice_lo, nice_hi, ticks


def _sector_path(
    cx: float,
    cy: float,
    outer: float,
    start: float,
    end: float,
    inner: float,
) -> str:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .normalized_chart_svg import (
        _fmt,
    )
    x1 = cx + math.cos(start) * outer
    y1 = cy + math.sin(start) * outer
    x2 = cx + math.cos(end) * outer
    y2 = cy + math.sin(end) * outer
    large = 1 if end - start > math.pi else 0
    if inner <= 0:
        return (
            f"M {_fmt(cx)} {_fmt(cy)} L {_fmt(x1)} {_fmt(y1)} "
            f"A {_fmt(outer)} {_fmt(outer)} 0 {large} 1 {_fmt(x2)} {_fmt(y2)} Z"
        )
    ix2 = cx + math.cos(end) * inner
    iy2 = cy + math.sin(end) * inner
    ix1 = cx + math.cos(start) * inner
    iy1 = cy + math.sin(start) * inner
    return (
        f"M {_fmt(x1)} {_fmt(y1)} A {_fmt(outer)} {_fmt(outer)} 0 {large} 1 {_fmt(x2)} {_fmt(y2)} "
        f"L {_fmt(ix2)} {_fmt(iy2)} A {_fmt(inner)} {_fmt(inner)} 0 {large} 0 {_fmt(ix1)} {_fmt(iy1)} Z"
    )


def _full_ring_path(cx: float, cy: float, outer: float, inner: float) -> str:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .normalized_chart_svg import (
        _fmt,
    )
    return (
        f"M {_fmt(cx + outer)} {_fmt(cy)} "
        f"A {_fmt(outer)} {_fmt(outer)} 0 1 1 {_fmt(cx - outer)} {_fmt(cy)} "
        f"A {_fmt(outer)} {_fmt(outer)} 0 1 1 {_fmt(cx + outer)} {_fmt(cy)} Z "
        f"M {_fmt(cx + inner)} {_fmt(cy)} "
        f"A {_fmt(inner)} {_fmt(inner)} 0 1 0 {_fmt(cx - inner)} {_fmt(cy)} "
        f"A {_fmt(inner)} {_fmt(inner)} 0 1 0 {_fmt(cx + inner)} {_fmt(cy)} Z"
    )


def _complete_styles(
    styles: list[SeriesVisualStyle],
    count: int,
) -> list[SeriesVisualStyle]:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .normalized_chart_svg import (
        SeriesVisualStyle,
        _DEFAULT_COLORS,
    )
    result = list(styles[:count])
    while len(result) < count:
        color = _DEFAULT_COLORS[len(result) % len(_DEFAULT_COLORS)]
        result.append(
            SeriesVisualStyle(
                fill=color,
                stroke=color,
                marker_fill=color,
                marker_stroke=color,
            )
        )
    return result


def _entry_text(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("text") or "")
    return str(value or "")


def _entry_value(value: Any, key: str) -> Any:
    return value.get(key) if isinstance(value, dict) else None


def _entry_color(value: Any, default: str) -> str:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .normalized_chart_svg import (
        _entry_value,
    )
    color = _entry_value(value, "color")
    return str(color) if isinstance(color, str) and color.startswith("#") else default


def _font_size(value: Any, default: float, bounds: _Rect) -> float:
    try:
        size = float(value)
    except (TypeError, ValueError, OverflowError):
        size = default
    return max(6.0, min(size, max(8.0, bounds.h * 0.18)))


def _text(
    x: float,
    y: float,
    value: str,
    *,
    size: float,
    anchor: str = "start",
    fill: str = "#444444",
    weight: str | None = None,
    font_family: str = "Arial",
) -> str:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .normalized_chart_svg import (
        _fmt,
    )
    weight_attr = f' font-weight="{weight}"' if weight else ""
    return (
        f'<text x="{_fmt(x)}" y="{_fmt(y)}" text-anchor="{anchor}" '
        f'font-family="{html.escape(font_family, quote=True)}" '
        f'font-size="{_fmt(size)}" fill="{fill}"{weight_attr}>'
        f'{html.escape(str(value))}</text>'
    )


def _map(value: float, lo: float, hi: float, out_lo: float, out_hi: float) -> float:
    if math.isclose(lo, hi):
        return (out_lo + out_hi) / 2
    return out_lo + (value - lo) * (out_hi - out_lo) / (hi - lo)


def _points(points: list[tuple[float, float]]) -> str:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .normalized_chart_svg import (
        _fmt,
    )
    return " ".join(f"{_fmt(x)},{_fmt(y)}" for x, y in points)


def _format_number(value: float) -> str:
    if abs(value) >= 1_000_000:
        return f"{value / 1_000_000:.1f}M".replace(".0M", "M")
    if abs(value) >= 1_000:
        return f"{value / 1_000:.1f}K".replace(".0K", "K")
    if math.isclose(value, round(value)):
        return str(int(round(value)))
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _format_percent(value: float) -> str:
    percent = value * 100.0
    return f"{percent:.1f}%".replace(".0%", "%")


def _contrast_color(color: str) -> str:
    token = color.lstrip("#")
    try:
        r, g, b = (int(token[idx:idx + 2], 16) for idx in (0, 2, 4))
    except (ValueError, TypeError):
        return "#FFFFFF"
    luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255.0
    return "#222222" if luminance > 0.62 else "#FFFFFF"


def _fmt(value: float) -> str:
    if not math.isfinite(float(value)):
        return "0"
    rounded = round(float(value), 3)
    if rounded == 0:
        return "0"
    if rounded.is_integer():
        return str(int(rounded))
    return f"{rounded:.3f}".rstrip("0").rstrip(".")
