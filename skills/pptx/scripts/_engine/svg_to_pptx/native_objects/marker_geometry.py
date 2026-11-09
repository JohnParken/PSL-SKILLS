# Adapted from PPT Master 5e8856b (MIT); see assets/licenses/ppt_master.txt.
"""Marker geometry implementation."""
from __future__ import annotations

from .marker_common_shared import (
    ET,
    IDENTITY_MATRIX,
    _FallbackShapeRecord,
    matrix_multiply,
    parse_svg_path,
    parse_svg_points,
    parse_transform_matrix,
    svg_path_to_absolute,
    transform_point,
)

def _bbox_union(
    first: tuple[float, float, float, float] | None,
    second: tuple[float, float, float, float] | None,
) -> tuple[float, float, float, float] | None:
    if first is None:
        return second
    if second is None:
        return first
    return (
        min(first[0], second[0]),
        min(first[1], second[1]),
        max(first[2], second[2]),
        max(first[3], second[3]),
    )


def _bbox_from_points(points: list[tuple[float, float]]) -> tuple[float, float, float, float] | None:
    if not points:
        return None
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    return min(xs), min(ys), max(xs), max(ys)


def _apply_matrix_bbox(
    bbox: tuple[float, float, float, float],
    matrix: tuple[float, float, float, float, float, float],
) -> tuple[float, float, float, float]:
    x1, y1, x2, y2 = bbox
    points = [
        transform_point(matrix, x1, y1),
        transform_point(matrix, x2, y1),
        transform_point(matrix, x2, y2),
        transform_point(matrix, x1, y2),
    ]
    result = _bbox_from_points(points)
    if result is None:
        raise RuntimeError("Native PPTX object fallback bbox inference failed")
    return result


def _points_attr_bbox(
    value: str | None,
    *,
    min_points: int,
) -> tuple[float, float, float, float] | None:
    try:
        points = parse_svg_points(value or "", min_points=min_points)
    except ValueError as exc:
        raise RuntimeError(f"Invalid native fallback points: {exc}") from exc
    return _bbox_from_points(points)


def _path_bbox(value: str | None) -> tuple[float, float, float, float] | None:
    try:
        commands = svg_path_to_absolute(parse_svg_path(value or ""))
    except ValueError as exc:
        raise RuntimeError(f"Invalid native fallback path d: {exc}") from exc

    points: list[tuple[float, float]] = []
    subpath_x = 0.0
    subpath_y = 0.0

    for command in commands:
        values = command.args
        if command.cmd == "M":
            subpath_x, subpath_y = values
            points.append((subpath_x, subpath_y))
        elif command.cmd in {"L", "T"}:
            points.append((values[0], values[1]))
        elif command.cmd == "C":
            points.extend(
                (values[index], values[index + 1])
                for index in range(0, 6, 2)
            )
        elif command.cmd in {"S", "Q"}:
            points.extend(
                (values[index], values[index + 1])
                for index in range(0, 4, 2)
            )
        elif command.cmd == "A":
            points.append((values[5], values[6]))
        elif command.cmd == "Z":
            points.append((subpath_x, subpath_y))

    return _bbox_from_points(points)


def _element_local_bbox(elem: ET.Element) -> tuple[float, float, float, float] | None:
    from .marker_values import _local_tag, _maybe_number, _project_geometry_number
    tag = _local_tag(elem)
    if tag == "metadata":
        return None
    if tag in {"defs", "clipPath", "mask", "filter", "style"}:
        return None
    if elem.get("display") == "none" or elem.get("visibility") == "hidden":
        return None

    if tag in {"g", "svg", "a"}:
        bbox = None
        for child in elem:
            bbox = _bbox_union(bbox, _fallback_bbox(child))
        return bbox

    if tag in {"rect", "image", "use"}:
        x = _project_geometry_number(elem, "x")
        y = _project_geometry_number(elem, "y")
        width = _project_geometry_number(elem, "width")
        height = _project_geometry_number(elem, "height")
        if width <= 0 or height <= 0:
            return None
        return x, y, x + width, y + height

    if tag == "circle":
        cx = _project_geometry_number(elem, "cx")
        cy = _project_geometry_number(elem, "cy")
        r = _project_geometry_number(elem, "r")
        if r <= 0:
            return None
        return cx - r, cy - r, cx + r, cy + r

    if tag == "ellipse":
        cx = _project_geometry_number(elem, "cx")
        cy = _project_geometry_number(elem, "cy")
        rx = _project_geometry_number(elem, "rx")
        ry = _project_geometry_number(elem, "ry")
        if rx <= 0 or ry <= 0:
            return None
        return cx - rx, cy - ry, cx + rx, cy + ry

    if tag == "line":
        points = [
            (
                _project_geometry_number(elem, "x1"),
                _project_geometry_number(elem, "y1"),
            ),
            (
                _project_geometry_number(elem, "x2"),
                _project_geometry_number(elem, "y2"),
            ),
        ]
        return _bbox_from_points(points)

    if tag in {"polygon", "polyline"}:
        return _points_attr_bbox(
            elem.get("points"),
            min_points=3 if tag == "polygon" else 2,
        )

    if tag == "path":
        # This intentionally approximates path geometry from command endpoints.
        # Explicit metadata remains the precise path for complex arcs/curves.
        return _path_bbox(elem.get("d"))

    if tag == "text":
        x = _project_geometry_number(elem, "x")
        y = _project_geometry_number(elem, "y")
        font_size = _maybe_number(elem.get("font-size")) or 16.0
        text = "".join(elem.itertext())
        width = max(len(text), 1) * font_size * 0.55
        height = font_size * 1.25
        return x, y - height * 0.8, x + width, y + height * 0.2

    return None


def _fallback_bbox(
    elem: ET.Element,
    matrix: tuple[float, float, float, float, float, float] = IDENTITY_MATRIX,
) -> tuple[float, float, float, float] | None:
    from .marker_values import _local_tag
    local_matrix = matrix
    transform = elem.get("transform")
    if transform:
        local_matrix = matrix_multiply(matrix, parse_transform_matrix(transform))

    tag = _local_tag(elem)
    if tag in {"g", "svg", "a"}:
        bbox = None
        for child in elem:
            bbox = _bbox_union(bbox, _fallback_bbox(child, local_matrix))
        return bbox

    local_bbox = _element_local_bbox(elem)
    if local_bbox is None:
        return None
    return _apply_matrix_bbox(local_bbox, local_matrix)


def _inferred_bounds(elem: ET.Element) -> tuple[float, float, float, float] | None:
    bbox = None
    for child in elem:
        bbox = _bbox_union(bbox, _fallback_bbox(child))
    return bbox


def _fallback_element_labels(elem: ET.Element) -> tuple[str, ...]:
    labels: list[str] = []
    for attribute in ("id", "class", "data-name"):
        raw = elem.get(attribute)
        if raw:
            labels.append(str(raw).strip().lower())
    return tuple(labels)


def _fallback_shape_records(
    elem: ET.Element,
    matrix: tuple[float, float, float, float, float, float] = IDENTITY_MATRIX,
    inherited_fill: str | None = "000000",
    inherited_stroke: str | None = None,
    inherited_labels: tuple[str, ...] = (),
) -> list[_FallbackShapeRecord]:
    """Collect visible painted fallback geometry with resolved bounds and ancestry."""
    from .marker_values import _hex_or_none, _local_tag, _own_fill_opacity, _paint_visible, _style_attr
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
    own_stroke = _style_attr(elem, "stroke")
    fill = own_fill if own_fill is not None else inherited_fill
    stroke = own_stroke if own_stroke is not None else inherited_stroke
    labels = inherited_labels + _fallback_element_labels(elem)

    records: list[_FallbackShapeRecord] = []
    if tag in {
        "circle", "ellipse", "line", "path", "polygon", "polyline", "rect",
    }:
        fill_color = (
            _hex_or_none(fill)
            if fill and _paint_visible(elem, "fill") and tag != "line"
            else None
        )
        stroke_color = (
            _hex_or_none(stroke)
            if stroke and _paint_visible(elem, "stroke")
            else None
        )
        if fill_color is not None or stroke_color is not None:
            local_bbox = _element_local_bbox(elem)
            if local_bbox is not None:
                records.append(
                    _FallbackShapeRecord(
                        tag=tag,
                        bounds=_apply_matrix_bbox(local_bbox, local_matrix),
                        fill=fill_color,
                        stroke=stroke_color,
                        labels=labels,
                        fill_opacity=_own_fill_opacity(elem) if fill_color else None,
                    )
                )

    for child in elem:
        records.extend(
            _fallback_shape_records(
                child,
                local_matrix,
                fill,
                stroke,
                labels,
            )
        )
    return records
