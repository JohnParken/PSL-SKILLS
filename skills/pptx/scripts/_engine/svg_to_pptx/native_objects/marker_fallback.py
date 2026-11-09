# Adapted from PPT Master 5e8856b (MIT); see assets/licenses/ppt_master.txt.
"""Marker fallback implementation."""
from __future__ import annotations

from .marker_common_shared import (
    ET,
    IDENTITY_MATRIX,
    Iterable,
    Iterator,
    _FALLBACK_TEXT_INHERITANCE,
    _FallbackTextRecord,
    _FallbackTextRun,
    _INHERITED_TEXT_ATTRS,
    _UNSET,
    contextmanager,
    matrix_multiply,
    parse_transform_matrix,
    transform_point,
)

def inherited_text_attrs(chain: Iterable[ET.Element]) -> dict[str, str]:
    """Resolve inherited text attributes along an outermost-first element chain."""
    from .marker_values import _style_attr
    values: dict[str, str] = {}
    for element in chain:
        for name in _INHERITED_TEXT_ATTRS:
            value = _style_attr(element, name)
            if value is not None:
                values[name] = value
    return values


@contextmanager
def fallback_text_inheritance(values: dict[str, str]) -> Iterator[None]:
    """Let fallback text records start from a marker's inherited attributes."""
    token = _FALLBACK_TEXT_INHERITANCE.set(
        {name: value for name, value in values.items() if name in _INHERITED_TEXT_ATTRS}
    )
    try:
        yield
    finally:
        _FALLBACK_TEXT_INHERITANCE.reset(token)


def _fallback_text_bold(weight: str | None) -> bool:
    from .marker_values import _maybe_number
    raw_weight = str(weight or "").strip().lower()
    numeric_weight = _maybe_number(raw_weight)
    return raw_weight in {"bold", "bolder"} or (
        numeric_weight is not None and numeric_weight >= 600
    )


def _dominant_text_style(
    elem: ET.Element,
    fill: str | None,
    weight: str | None,
    *,
    runs: list[_FallbackTextRun] | None = None,
) -> tuple[str | None, str | None]:
    """Return the fill and weight carried by most visible characters."""
    from .marker_values import _hex_or_none, _style_attr
    counts: dict[tuple[str | None, str | None], int] = {}

    def add_text(text: str, node_fill: str | None, node_weight: str | None) -> None:
        key = (node_fill, node_weight)
        counts[key] = counts.get(key, 0) + len("".join(text.split()))
        if text and runs is not None:
            runs.append(_FallbackTextRun(text, _hex_or_none(node_fill), _fallback_text_bold(node_weight)))

    def visit(node: ET.Element, node_fill: str | None, node_weight: str | None) -> None:
        node_fill = _style_attr(node, "fill") or node_fill
        node_weight = _style_attr(node, "font-weight") or node_weight
        add_text(node.text or "", node_fill, node_weight)
        for child in node:
            visit(child, node_fill, node_weight)
            add_text(child.tail or "", node_fill, node_weight)

    visit(elem, fill, weight)
    return max(counts, key=counts.get) if any(counts.values()) else (fill, weight)


def _fallback_text_records(
    elem: ET.Element,
    matrix: tuple[float, float, float, float, float, float] = IDENTITY_MATRIX,
    inherited_fill: str | None = _UNSET,
    inherited_weight: str | None = _UNSET,
    inherited_anchor: str | None = _UNSET,
    inherited_labels: tuple[str, ...] = (),
) -> list[_FallbackTextRecord]:
    """Collect visible fallback text with resolved paint, emphasis, and anchor."""
    from .marker_values import _hex_or_none, _local_tag, _normalized_fallback_text, _project_geometry_number, _style_attr
    from .marker_geometry import _fallback_element_labels
    outer = _FALLBACK_TEXT_INHERITANCE.get()
    if inherited_fill is _UNSET:
        inherited_fill = outer.get("fill", "000000")
    if inherited_weight is _UNSET:
        inherited_weight = outer.get("font-weight")
    if inherited_anchor is _UNSET:
        inherited_anchor = outer.get("text-anchor")
    tag = _local_tag(elem)
    if tag == "metadata" or tag in {"defs", "clipPath", "mask", "filter", "style"}:
        return []
    if (
        _style_attr(elem, "display") == "none"
        or _style_attr(elem, "visibility") == "hidden"
    ):
        return []

    local_matrix = matrix
    transform = elem.get("transform")
    if transform:
        local_matrix = matrix_multiply(matrix, parse_transform_matrix(transform))

    own_fill = _style_attr(elem, "fill")
    own_weight = _style_attr(elem, "font-weight")
    own_anchor = _style_attr(elem, "text-anchor")
    fill = own_fill if own_fill is not None else inherited_fill
    weight = own_weight if own_weight is not None else inherited_weight
    anchor = own_anchor if own_anchor is not None else inherited_anchor
    labels = inherited_labels + _fallback_element_labels(elem)

    if tag == "text":
        text = _normalized_fallback_text("".join(elem.itertext()))
        if not text:
            return []
        # A <text> whose visible characters mostly sit in styled <tspan>s
        # reads in their paint and weight (a whole cell set in an emphasis
        # tspan is that colour, not the <text> default).
        runs: list[_FallbackTextRun] = []
        fill, weight = _dominant_text_style(elem, fill, weight, runs=runs)
        x = _project_geometry_number(elem, "x") if elem.get("x") is not None else None
        y = _project_geometry_number(elem, "y") if elem.get("y") is not None else None
        if x is not None and y is not None:
            x, y = transform_point(local_matrix, x, y)
        return [
            _FallbackTextRecord(
                text=text,
                x=x,
                y=y,
                fill=_hex_or_none(fill),
                bold=_fallback_text_bold(weight),
                anchor=str(anchor or "start").strip().lower(),
                labels=labels,
                runs=tuple(runs),
            )
        ]

    records: list[_FallbackTextRecord] = []
    for child in elem:
        records.extend(
            _fallback_text_records(
                child,
                local_matrix,
                fill,
                weight,
                anchor,
                labels,
            )
        )
    return records


def _fallback_concentric_circle_radii(elem: ET.Element) -> list[float]:
    """Return the largest countable set of concentric circular grid radii."""
    from .marker_geometry import _fallback_shape_records
    groups: list[tuple[float, float, list[float]]] = []
    for record in _fallback_shape_records(elem):
        if record.tag != "circle" or record.stroke is None:
            continue
        x1, y1, x2, y2 = record.bounds
        radius_x = (x2 - x1) / 2
        radius_y = (y2 - y1) / 2
        if radius_x <= 0 or abs(radius_x - radius_y) > 1:
            continue
        center_x = (x1 + x2) / 2
        center_y = (y1 + y2) / 2
        group = next(
            (
                item
                for item in groups
                if abs(item[0] - center_x) <= 1 and abs(item[1] - center_y) <= 1
            ),
            None,
        )
        if group is None:
            group = (center_x, center_y, [])
            groups.append(group)
        group[2].append(radius_x)
    if not groups:
        return []
    radii = max(groups, key=lambda item: len(item[2]))[2]
    return sorted({round(radius, 3) for radius in radii})


def _fallback_fill_candidates(
    elem: ET.Element,
    matrix: tuple[float, float, float, float, float, float] = IDENTITY_MATRIX,
    inherited_fill: str | None = None,
) -> list[tuple[float, str]]:
    from .marker_values import _hex_or_none, _local_tag, _paint_visible, _style_attr
    from .marker_geometry import _apply_matrix_bbox, _element_local_bbox
    tag = _local_tag(elem)
    if tag == "metadata" or tag in {"defs", "clipPath", "mask", "filter", "style"}:
        return []
    if elem.get("display") == "none" or elem.get("visibility") == "hidden":
        return []
    if not _paint_visible(elem, "fill"):
        return []

    local_matrix = matrix
    transform = elem.get("transform")
    if transform:
        local_matrix = matrix_multiply(matrix, parse_transform_matrix(transform))

    fill = _style_attr(elem, "fill")
    next_fill = fill if fill is not None else inherited_fill
    if tag in {"g", "svg", "a"}:
        candidates: list[tuple[float, str]] = []
        for child in elem:
            candidates.extend(_fallback_fill_candidates(child, local_matrix, next_fill))
        return candidates

    if tag != "rect":
        return []
    if not next_fill or next_fill.strip().lower() in {"none", "transparent"}:
        return []
    color = _hex_or_none(next_fill)
    if not color:
        return []
    local_bbox = _element_local_bbox(elem)
    if local_bbox is None:
        return []
    x1, y1, x2, y2 = _apply_matrix_bbox(local_bbox, local_matrix)
    area = max(x2 - x1, 0.0) * max(y2 - y1, 0.0)
    return [(area, color)] if area > 0 else []


def _inferred_chart_background(elem: ET.Element) -> str | None:
    from .marker_geometry import _inferred_bounds
    bounds = _inferred_bounds(elem)
    if bounds is None:
        return None
    x1, y1, x2, y2 = bounds
    bounds_area = max(x2 - x1, 0.0) * max(y2 - y1, 0.0)
    if bounds_area <= 0:
        return None

    candidates: list[tuple[float, str]] = []
    for child in elem:
        candidates.extend(_fallback_fill_candidates(child))
    if not candidates:
        return None
    area, color = max(candidates, key=lambda item: item[0])
    # Avoid mistaking a large data bar for a chart background when no panel /
    # plot-area rectangle exists in the fallback drawing.
    return color if area >= bounds_area * 0.25 else None


def _fallback_text_colors(
    elem: ET.Element,
    inherited_fill: str | None = None,
) -> list[str]:
    from .marker_values import _hex_or_none, _local_tag, _paint_visible, _style_attr
    tag = _local_tag(elem)
    if tag == "metadata" or tag in {"defs", "clipPath", "mask", "filter", "style"}:
        return []
    if elem.get("display") == "none" or elem.get("visibility") == "hidden":
        return []
    if not _paint_visible(elem, "fill"):
        return []

    fill = _style_attr(elem, "fill")
    next_fill = fill if fill is not None else inherited_fill
    colors: list[str] = []
    if tag in {"text", "tspan"} and next_fill:
        color = _hex_or_none(next_fill)
        if color:
            colors.append(color)
    for child in elem:
        colors.extend(_fallback_text_colors(child, next_fill))
    return colors


def _fallback_stroke_colors(
    elem: ET.Element,
    inherited_stroke: str | None = None,
) -> list[str]:
    from .marker_values import _hex_or_none, _local_tag, _paint_visible, _style_attr
    tag = _local_tag(elem)
    if tag == "metadata" or tag in {"defs", "clipPath", "mask", "filter", "style"}:
        return []
    if elem.get("display") == "none" or elem.get("visibility") == "hidden":
        return []
    if not _paint_visible(elem, "stroke"):
        return []

    stroke = _style_attr(elem, "stroke")
    next_stroke = stroke if stroke is not None else inherited_stroke
    colors: list[str] = []
    if tag in {"circle", "ellipse", "line", "path", "polygon", "polyline", "rect"} and next_stroke:
        color = _hex_or_none(next_stroke)
        if color:
            colors.append(color)
    for child in elem:
        colors.extend(_fallback_stroke_colors(child, next_stroke))
    return colors


def _most_common_color(colors: list[str]) -> str | None:
    if not colors:
        return None
    counts: dict[str, int] = {}
    for color in colors:
        counts[color] = counts.get(color, 0) + 1
    return max(counts.items(), key=lambda item: item[1])[0]


def _relative_luminance(color: str) -> float:
    channels = [int(color[idx:idx + 2], 16) / 255.0 for idx in (0, 2, 4)]
    linear = [
        channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4
        for channel in channels
    ]
    return linear[0] * 0.2126 + linear[1] * 0.7152 + linear[2] * 0.0722
