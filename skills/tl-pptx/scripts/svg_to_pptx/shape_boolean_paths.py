"""Path Boolean operations and SVG serialization, split unchanged from upstream."""
from __future__ import annotations
import math
from collections.abc import Mapping, Sequence
from typing import Any
from pptx_to_svg.preset_svg_markup import attrs_to_xml
from .drawingml.paths import PathCommand
_RESULT_PRECISION = 6

def _close_filled_commands(
    commands: Sequence[PathCommand],
    label: str,
) -> list[PathCommand]:
    closed: list[PathCommand] = []
    open_subpath = False
    drew_segment = False
    for command in commands:
        if command.cmd == "M":
            if open_subpath:
                if not drew_segment:
                    raise ValueError(f"{label} has an empty subpath")
                closed.append(PathCommand("Z", []))
            open_subpath = True
            drew_segment = False
        elif command.cmd in {"L", "C"}:
            if not open_subpath:
                raise ValueError(f"{label} draws before its first move")
            drew_segment = True
        elif command.cmd == "Z":
            if not open_subpath:
                raise ValueError(f"{label} closes without an open subpath")
            if not drew_segment:
                raise ValueError(f"{label} has an empty subpath")
            open_subpath = False
        closed.append(command)
    if open_subpath:
        if not drew_segment:
            raise ValueError(f"{label} has an empty subpath")
        closed.append(PathCommand("Z", []))
    if not closed:
        raise ValueError(f"{label} has no drawable contour")
    return closed



def _require_closed_commands(
    commands: Sequence[PathCommand],
    label: str,
) -> None:
    open_subpath = False
    drew_segment = False
    closed_contour = False
    for command in commands:
        if command.cmd == "M":
            if open_subpath:
                raise ValueError(f"{label} contains an open subpath")
            open_subpath = True
            drew_segment = False
        elif command.cmd in {"L", "C"}:
            if not open_subpath:
                raise ValueError(f"{label} draws before its first move")
            drew_segment = True
        elif command.cmd == "Z":
            if not open_subpath:
                raise ValueError(f"{label} closes without an open subpath")
            if not drew_segment:
                raise ValueError(f"{label} has an empty subpath")
            open_subpath = False
            closed_contour = True
    if open_subpath:
        raise ValueError(f"{label} contains an open subpath")
    if not closed_contour:
        raise ValueError(f"{label} has no drawable closed contour")



def _commands_to_pathops(
    commands: Sequence[PathCommand],
    pathops: Any,
) -> Any:
    result = pathops.Path(fillType=pathops.FillType.WINDING)
    for command in commands:
        args = command.args
        if command.cmd == "M":
            result.moveTo(args[0], args[1])
        elif command.cmd == "L":
            result.lineTo(args[0], args[1])
        elif command.cmd == "C":
            result.cubicTo(*args)
        elif command.cmd == "Z":
            result.close()
        else:
            raise AssertionError(
                f"Unexpected normalized path command: {command.cmd}"
            )
    return result



def _merge_paths(
    operation: str,
    operands: Sequence[Any],
    pathops: Any,
) -> Any:
    operator_by_name = {
        "combine": pathops.PathOp.XOR,
        "intersect": pathops.PathOp.INTERSECTION,
        "subtract": pathops.PathOp.DIFFERENCE,
        "union": pathops.PathOp.UNION,
    }
    operator = operator_by_name[operation]
    result = operands[0]
    for operand in operands[1:]:
        result = _path_op(result, operand, operator, pathops)
        if _path_is_empty(result) and operation in {"intersect", "subtract"}:
            break
    return result



def _path_op(
    first: Any,
    second: Any,
    operator: Any,
    pathops: Any,
) -> Any:
    try:
        return pathops.op(
            first,
            second,
            operator,
            fix_winding=True,
            keep_starting_points=False,
            clockwise=False,
        )
    except pathops.PathOpsError as exc:
        raise ValueError(f"Skia path Boolean operation failed: {exc}") from exc



def _simplify_path(path: Any, pathops: Any) -> Any:
    """Resolve winding and self-intersections before filled-area checks."""
    try:
        return pathops.simplify(
            path,
            fix_winding=True,
            keep_starting_points=False,
            clockwise=False,
        )
    except pathops.PathOpsError as exc:
        raise ValueError(f"Skia path simplification failed: {exc}") from exc



def _fragment_paths(
    operands: Sequence[Any],
    pathops: Any,
) -> list[Any]:
    pieces: list[Any] = []
    covered: Any | None = None
    for operand in operands:
        split_pieces: list[Any] = []
        for piece in pieces:
            difference = _path_op(
                piece,
                operand,
                pathops.PathOp.DIFFERENCE,
                pathops,
            )
            intersection = _path_op(
                piece,
                operand,
                pathops.PathOp.INTERSECTION,
                pathops,
            )
            if not _path_is_empty(difference):
                split_pieces.append(difference)
            if not _path_is_empty(intersection):
                split_pieces.append(intersection)

        unique = (
            operand
            if covered is None
            else _path_op(
                operand,
                covered,
                pathops.PathOp.DIFFERENCE,
                pathops,
            )
        )
        if not _path_is_empty(unique):
            split_pieces.append(unique)
        pieces = split_pieces
        covered = (
            operand
            if covered is None
            else _path_op(
                covered,
                operand,
                pathops.PathOp.UNION,
                pathops,
            )
        )

    components = [
        component
        for piece in pieces
        for component in _connected_components(piece, pathops)
        if not _path_is_empty(component)
    ]
    if not components:
        raise ValueError("Shape Boolean fragment produced no filled area")
    components.sort(key=_path_sort_key)
    return components



def _connected_components(path: Any, pathops: Any) -> list[Any]:
    contours = [
        contour
        for contour in path.contours
        if not _path_is_empty(contour)
    ]
    if len(contours) <= 1:
        return contours

    parents: list[int | None] = [None] * len(contours)
    for child_index, child in enumerate(contours):
        child_bounds = child.bounds
        child_points = child.firstPoints
        if child_bounds is None or not child_points:
            continue
        candidates: list[tuple[float, int]] = []
        for parent_index, candidate in enumerate(contours):
            if child_index == parent_index:
                continue
            candidate_bounds = candidate.bounds
            if candidate_bounds is None:
                continue
            if not _bounds_contain(candidate_bounds, child_bounds):
                continue
            if candidate.area <= child.area:
                continue
            if candidate.contains(child_points[0]):
                candidates.append((candidate.area, parent_index))
        if candidates:
            parents[child_index] = min(candidates)[1]

    depths = [
        _contour_depth(index, parents)
        for index in range(len(contours))
    ]
    components: list[Any] = []
    for index, contour in enumerate(contours):
        if depths[index] % 2:
            continue
        component = pathops.Path(fillType=pathops.FillType.WINDING)
        component.addPath(contour)
        for child_index, parent_index in enumerate(parents):
            if parent_index == index and depths[child_index] % 2:
                component.addPath(contours[child_index])
        components.append(component)
    return components



def _bounds_contain(
    outer: tuple[float, float, float, float],
    inner: tuple[float, float, float, float],
) -> bool:
    scale = max(
        *(abs(value) for value in outer),
        *(abs(value) for value in inner),
        1.0,
    )
    tolerance = scale * 1e-7
    return (
        outer[0] <= inner[0] + tolerance
        and outer[1] <= inner[1] + tolerance
        and outer[2] >= inner[2] - tolerance
        and outer[3] >= inner[3] - tolerance
    )



def _contour_depth(
    index: int,
    parents: Sequence[int | None],
) -> int:
    depth = 0
    seen = {index}
    parent = parents[index]
    while parent is not None:
        if parent in seen:
            raise ValueError("Shape Boolean fragment produced cyclic contours")
        seen.add(parent)
        depth += 1
        parent = parents[parent]
    return depth



def _path_is_empty(path: Any) -> bool:
    bounds = path.bounds
    if bounds is None or len(path) == 0:
        return True
    width = bounds[2] - bounds[0]
    height = bounds[3] - bounds[1]
    scale = max(abs(width), abs(height), 1.0)
    return (
        width <= 0
        or height <= 0
        or not math.isfinite(path.area)
        or path.area <= scale * scale * 1e-12
    )



def _path_sort_key(
    path: Any,
) -> tuple[
    float,
    float,
    float,
    float,
    float,
    tuple[tuple[str, tuple[float, ...]], ...],
]:
    bounds = path.bounds
    signature = tuple(
        (
            str(verb),
            tuple(
                round(coordinate, _RESULT_PRECISION)
                for point in points
                for coordinate in point
            ),
        )
        for verb, points in path
    )
    if bounds is None:
        return (
            math.inf,
            math.inf,
            math.inf,
            math.inf,
            math.inf,
            signature,
        )
    return (
        round(bounds[1], _RESULT_PRECISION),
        round(bounds[0], _RESULT_PRECISION),
        round(bounds[3], _RESULT_PRECISION),
        round(bounds[2], _RESULT_PRECISION),
        round(path.area, _RESULT_PRECISION),
        signature,
    )



def _serialize_result_path(
    path: Any,
    result_id: str,
    style: Mapping[str, str],
    pathops: Any,
) -> str:
    path_data = _pathops_to_svg_path(path, pathops)
    attributes = {
        "id": result_id,
        "d": path_data,
        **style,
    }
    return f"<path{attrs_to_xml(attributes)}/>"



def _pathops_to_svg_path(path: Any, pathops: Any) -> str:
    path.convertConicsToQuads(0.05)
    parts: list[str] = []
    current: tuple[float, float] | None = None
    subpath_start: tuple[float, float] | None = None
    open_subpath = False

    for verb, points in path:
        if verb == pathops.PathVerb.MOVE:
            point = points[0]
            parts.append(
                f"M {_format_number(point[0])} {_format_number(point[1])}"
            )
            current = point
            subpath_start = point
            open_subpath = True
        elif verb == pathops.PathVerb.LINE:
            point = points[0]
            parts.append(
                f"L {_format_number(point[0])} {_format_number(point[1])}"
            )
            current = point
        elif verb == pathops.PathVerb.QUAD:
            if current is None:
                raise ValueError("Shape Boolean result quadratic has no start point")
            control, end = points
            control_1 = _quadratic_first_control(current, control)
            control_2 = (
                end[0] + (control[0] - end[0]) * 2.0 / 3.0,
                end[1] + (control[1] - end[1]) * 2.0 / 3.0,
            )
            parts.append(
                "C "
                f"{_format_point(control_1)} "
                f"{_format_point(control_2)} "
                f"{_format_point(end)}"
            )
            current = end
        elif verb == pathops.PathVerb.CUBIC:
            control_1, control_2, end = points
            parts.append(
                "C "
                f"{_format_point(control_1)} "
                f"{_format_point(control_2)} "
                f"{_format_point(end)}"
            )
            current = end
        elif verb == pathops.PathVerb.CLOSE:
            parts.append("Z")
            current = subpath_start
            open_subpath = False
        else:
            raise ValueError(f"Unsupported skia-pathops result verb: {verb}")

    if open_subpath:
        raise ValueError("Shape Boolean engine returned an open contour")
    if not parts:
        raise ValueError("Shape Boolean engine returned no path commands")
    return " ".join(parts)



def _format_point(point: tuple[float, float]) -> str:
    return f"{_format_number(point[0])} {_format_number(point[1])}"



def _format_number(value: float) -> str:
    if not math.isfinite(value):
        raise ValueError("Shape Boolean result contains a non-finite coordinate")
    rounded = round(value, _RESULT_PRECISION)
    if abs(rounded) < 10 ** (-_RESULT_PRECISION):
        return "0"
    if rounded == int(rounded):
        return str(int(rounded))
    return f"{rounded:.{_RESULT_PRECISION}f}".rstrip("0").rstrip(".")



def _quadratic_first_control(current: tuple[float, float], control: tuple[float, float]) -> tuple[float, float]:
    """Convert a checked quadratic start/control pair to its first cubic control."""
    return (
        current[0] + (control[0] - current[0]) * 2.0 / 3.0,
        current[1] + (control[1] - current[1]) * 2.0 / 3.0,
    )
