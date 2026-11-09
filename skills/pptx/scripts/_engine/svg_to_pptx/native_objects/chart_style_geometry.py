# Adapted from PPT Master 5e8856b (MIT); see assets/licenses/ppt_master.txt.
"""Chart style geometry implementation."""
from __future__ import annotations

from .chart_style_shared import (
    Any,
    ET,
    _clean_hex,
    _fallback_concentric_circle_radii,
    _fallback_shape_records,
    _first_present,
)

def _fallback_bar_clusters(
    elem: ET.Element,
    chart_data: dict[str, Any],
) -> tuple[float, dict[int, list[tuple[float, float]]]] | None:
    """Fallback bars grouped by category slot: slot size plus (low, high) edges per slot."""
    from .chart_style_warnings import _fallback_data_shapes
    chart_type = chart_data.get("type")
    plot_area = chart_data.get("plot_area")
    if chart_data.get("kind") != "category" or chart_type not in {"bar", "column"}:
        return None
    if not isinstance(plot_area, dict):
        return None
    categories = chart_data.get("categories") or []
    if not categories or not chart_data.get("series"):
        return None
    px, py = float(plot_area["x"]), float(plot_area["y"])
    pw, ph = float(plot_area["width"]), float(plot_area["height"])
    along = 0 if chart_type == "column" else 1  # axis index that carries the categories
    origin = px if along == 0 else py
    extent = pw if along == 0 else ph
    slot = extent / len(categories)
    clusters: dict[int, list[tuple[float, float]]] = {}
    for record in _fallback_data_shapes(elem):
        if record.tag != "rect" or record.fill is None:
            continue
        x0, y0, x1, y1 = record.bounds
        if (x1 - x0) * (y1 - y0) >= 0.9 * pw * ph:
            continue
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        if not (px <= cx <= px + pw and py <= cy <= py + ph):
            continue
        low, high = (x0, x1) if along == 0 else (y0, y1)
        index = min(len(categories) - 1, max(0, int((((low + high) / 2) - origin) // slot)))
        clusters.setdefault(index, []).append((low, high))
    if not clusters:
        return None
    return slot, clusters


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[len(ordered) // 2]


def _inferred_bar_gap_width(
    elem: ET.Element,
    chart_data: dict[str, Any],
) -> int | None:
    """Read the bar/column gap width from fallback bars inside a known plot area.

    PowerPoint's gapWidth is the empty slot share as a percentage of one bar
    width; the fallback slot is the plot extent divided by the category count.
    """
    grouped = _fallback_bar_clusters(elem, chart_data)
    if grouped is None:
        return None
    slot, clusters = grouped
    cluster = _median([
        max(high for _, high in edges) - min(low for low, _ in edges)
        for edges in clusters.values()
    ])
    if cluster <= 0:
        return None
    if cluster >= slot:
        return 0
    clustered = chart_data.get("grouping") == "clustered"
    bars_per_cluster = len(chart_data.get("series") or []) if clustered else 1
    bar_width = cluster / bars_per_cluster
    return max(0, min(500, int(round((slot - cluster) / bar_width * 100))))


def _inferred_bar_overlap(
    elem: ET.Element,
    chart_data: dict[str, Any],
) -> int | None:
    """Read clustered bar overlap: the share of one bar that the next bar covers (negative = gap)."""
    if chart_data.get("grouping") != "clustered" or len(chart_data.get("series") or []) < 2:
        return None
    grouped = _fallback_bar_clusters(elem, chart_data)
    if grouped is None:
        return None
    _, clusters = grouped
    pitches: list[float] = []
    widths: list[float] = []
    for edges in clusters.values():
        ordered = sorted(edges)
        if len(ordered) < 2:
            continue
        widths.extend(high - low for low, high in ordered)
        pitches.extend(
            ordered[idx + 1][0] - ordered[idx][0] for idx in range(len(ordered) - 1)
        )
    if not pitches or not widths:
        return None
    bar_width = _median(widths)
    if bar_width <= 0:
        return None
    return max(-100, min(100, int(round((bar_width - _median(pitches)) / bar_width * 100))))


def _inferred_cross_between(
    elem: ET.Element,
    chart_data: dict[str, Any],
) -> str | None:
    """Read whether fallback line/area points sit between ticks or on the axis edge.

    The first data point of a category line/area starts either half a slot
    inside the plot (``between``) or on the plot edge (``midCat``).
    """
    from .chart_style_warnings import _fallback_data_shapes
    chart_type = chart_data.get("type")
    plot_area = chart_data.get("plot_area")
    if chart_data.get("kind") != "category" or chart_type not in {"area", "line"}:
        return None
    if not isinstance(plot_area, dict):
        return None
    categories = chart_data.get("categories") or []
    if len(categories) < 2:
        return None
    px, pw = float(plot_area["x"]), float(plot_area["width"])
    slot = pw / len(categories)
    starts = [
        record.bounds[0]
        for record in _fallback_data_shapes(elem)
        if record.tag in {"path", "polygon", "polyline"}
        and (record.bounds[2] - record.bounds[0]) >= slot
    ]
    if not starts:
        return None
    start = min(starts)
    return "midCat" if abs(start - px) < abs(start - (px + slot / 2)) else "between"


def _native_chart_radial_warnings(
    elem: ET.Element,
    payload: dict[str, Any],
    chart_data: dict[str, Any],
) -> list[str]:
    if chart_data.get("kind") != "category" or chart_data.get("type") != "radar":
        return []
    radii = _fallback_concentric_circle_radii(elem)
    if len(radii) < 2:
        return []
    warnings: list[str] = []
    if payload.get("plot_area") is None:
        warnings.append(
            "Native PPTX radial chart plot area is visible in the fallback, but "
            "classic payload has no plot_area"
        )
    axes = chart_data.get("axes") if isinstance(chart_data.get("axes"), dict) else {}
    value_axis = axes.get("value") if isinstance(axes.get("value"), dict) else {}
    if not all(value_axis.get(field) is not None for field in (
        "minimum", "maximum", "major_unit",
    )):
        warnings.append(
            f"Native PPTX radial chart has {len(radii)} countable radial gridlines, "
            "but payload has no complete axes.value minimum/maximum/major_unit scale"
        )
    return warnings


def _native_chart_tile_color_warnings(
    elem: ET.Element,
    payload: dict[str, Any],
    chart_data: dict[str, Any],
) -> list[str]:
    from .chart_style_text import _record_has_label
    if chart_data.get("kind") != "chartex" or chart_data.get("type") not in {
        "sunburst", "treemap",
    }:
        return []
    values = chart_data.get("values")
    if not isinstance(values, list) or not values:
        return []
    all_shapes = _fallback_shape_records(elem)
    tile_shapes = [
        record
        for record in all_shapes
        if (
            record.fill is not None
            and record.tag in {"path", "polygon", "rect"}
            and _record_has_label(record, "tile", "sector", "segment", "slice")
            and not _record_has_label(record, "legend")
        )
    ]
    if len(tile_shapes) < len(values):
        return []
    fallback_colors = [record.fill for record in tile_shapes[:len(values)]]
    style = payload.get("style") if isinstance(payload.get("style"), dict) else {}
    raw_colors = _first_present(style.get("colors"), payload.get("colors"))
    payload_colors = (
        [_clean_hex(color, "#4472C4") for color in raw_colors]
        if isinstance(raw_colors, list)
        else []
    )
    if fallback_colors == payload_colors:
        return []
    return [
        "Native PPTX ChartEx tile color sequence not projected: fallback has "
        f"{len(fallback_colors)} colors, payload style.colors has "
        f"{len(payload_colors)}"
    ]
