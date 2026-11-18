#!/usr/bin/env python3
"""
PPT Master - Shape Boolean Core

Resolve closed SVG or text-outline operands into SVG-root-coordinate paths and
apply PowerPoint-compatible merge-shapes operations without mutating the source.
Callers insert returned paths at the original z-order under the final semantic
or structured parent, never under the old transformed ancestor.

Usage:
    Import render_boolean_svg_fragments from svg_to_pptx.shape_boolean.

Examples:
    fragment = render_boolean_svg_fragments(
        "projects/demo/svg_output/slide-01.svg",
        operation="intersect",
        source_ids=("shape-a", "shape-b"),
        output_id="shape-overlap",
    )

Dependencies:
    skia-pathops, local PPT Master modules, and uharfbuzz for text operands
"""

from __future__ import annotations


import math
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from pptx_to_svg.emu_units import Xfrm
from pptx_to_svg.preset_authoring import (
    authored_preset_encoding,
    validate_authored_preset_group,
)
from pptx_to_svg.preset_registry_to_svg import render_preset_geometry
from pptx_to_svg.preset_svg_markup import attrs_to_xml as attrs_to_xml

from .shape_boolean_paths import (
    _close_filled_commands as _close_filled_commands,
    _require_closed_commands as _require_closed_commands,
    _commands_to_pathops as _commands_to_pathops,
    _merge_paths as _merge_paths,
    _path_op as _path_op,
    _simplify_path as _simplify_path,
    _fragment_paths as _fragment_paths,
    _connected_components as _connected_components,
    _bounds_contain as _bounds_contain,
    _contour_depth as _contour_depth,
    _path_is_empty as _path_is_empty,
    _path_sort_key as _path_sort_key,
    _serialize_result_path as _serialize_result_path,
    _pathops_to_svg_path as _pathops_to_svg_path,
    _format_point as _format_point,
    _format_number as _format_number,
)

from .drawingml.context import AffineMatrix, IDENTITY_MATRIX
from .drawingml.paths import (
    PathCommand,
    normalize_path_commands,
    parse_svg_path,
    parse_svg_points,
    svg_path_to_absolute,
    transform_path_commands,
)
from .drawingml.utils import (
    format_project_geometry_length,
    matrix_multiply,
    parse_inline_style,
    parse_opacity,
    parse_project_geometry_length,
    parse_project_stroke_dasharray,
    parse_project_stroke_enum,
    parse_transform_matrix,
)


BOOLEAN_OPERATIONS = frozenset({
    "combine",
    "fragment",
    "intersect",
    "subtract",
    "union",
})

_BOOLEAN_TAGS = frozenset({"circle", "ellipse", "path", "polygon", "rect"})
_DEFINITION_TAGS = frozenset({
    "clipPath",
    "defs",
    "marker",
    "mask",
    "pattern",
    "symbol",
})
_ID_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.:-]*")
_STYLE_OVERRIDE_ATTRS = (
    "fill",
    "fill-opacity",
    "opacity",
    "stroke",
    "stroke-dasharray",
    "stroke-width",
    "stroke-opacity",
    "stroke-linecap",
    "stroke-linejoin",
)
_PRESENTATION_ATTRS = (
    *_STYLE_OVERRIDE_ATTRS,
    "vector-effect",
)
_ROUNDTRIP_TRANSPORT_ATTRS = frozenset({
    "data-pptx-custgeom",
    "data-pptx-custgeom-ref",
    "data-pptx-geometry-kind",
    "data-pptx-native",
    "data-pptx-native-ref",
    "data-pptx-native-source",
    "data-pptx-native-status",
    "data-pptx-part",
    "data-pptx-shape-id",
    "data-pptx-shape-scope",
    "data-pptx-source-ref",
})
_RESULT_PRECISION = 6
_BEZIER_QUARTER_K = 0.5522847498307936


def render_boolean_svg_fragments(
    svg_file: str | Path,
    *,
    operation: str,
    source_ids: Sequence[str],
    output_id: str,
    style: Mapping[str, str] | None = None,
    font_dirs: Sequence[str | Path] = (),
) -> str:
    """Return canonical SVG path fragments for one merge-shapes operation.

    The first source is the primary shape: subtract removes all later sources
    from it, and every result inherits its effective presentation attributes.
    All transforms are baked into SVG-root coordinates, so callers insert the
    result at the original z-order under the final semantic or structured
    parent, never under the old transformed ancestor. Fragment returns
    top-to-bottom, left-to-right sibling paths named ``<output_id>-1`` onward;
    every other operation returns one path named exactly ``output_id``.
    """
    if not isinstance(operation, str):
        raise ValueError("Shape Boolean operation must be a string")
    normalized_operation = operation.strip().lower()
    if normalized_operation not in BOOLEAN_OPERATIONS:
        allowed = ", ".join(sorted(BOOLEAN_OPERATIONS))
        raise ValueError(
            f"Unsupported shape Boolean operation {operation!r}; use {allowed}"
        )
    source_id_list = _validate_source_ids(source_ids)
    _validate_output_id(output_id)
    style_overrides = _validate_style_overrides(style)

    source_path = Path(svg_file)
    try:
        root = ET.parse(source_path).getroot()
    except ET.ParseError as exc:
        raise ValueError(f"Cannot parse SVG {source_path}: {exc}") from exc
    if _local_name(root.tag) != "svg":
        raise ValueError(f"Shape Boolean source must have an SVG root: {source_path}")
    if any(_local_name(element.tag) == "style" for element in root.iter()):
        raise ValueError(
            "Shape Boolean does not resolve embedded CSS; use explicit SVG "
            "presentation attributes on the operands"
        )

    parents = {
        child: parent
        for parent in root.iter()
        for child in list(parent)
    }
    elements_by_id, duplicate_ids = _index_element_ids(root)
    selected: list[ET.Element] = []
    for source_id in source_id_list:
        if source_id in duplicate_ids:
            raise ValueError(
                f"Shape Boolean source id {source_id!r} is not unique"
            )
        element = elements_by_id.get(source_id)
        if element is None:
            raise ValueError(f"Shape Boolean source id not found: {source_id!r}")
        selected.append(element)

    pathops = _load_pathops()
    operands = [
        _element_to_pathops(
            element,
            parents,
            pathops,
            font_dirs=font_dirs,
        )
        for element in selected
    ]
    inherited_style = _materialize_baked_stroke_style(
        _effective_style(selected[0], parents),
        _combined_transform(_element_chain(selected[0], parents)),
    )
    inherited_style.update(style_overrides)

    if normalized_operation == "fragment":
        results = _fragment_paths(operands, pathops)
        fragments: list[str] = []
        for index, result in enumerate(results, start=1):
            result_id = f"{output_id}-{index}"
            _validate_result_id_collision(
                result_id,
                elements_by_id,
                source_id_list,
            )
            fragments.append(
                _serialize_result_path(
                    result,
                    result_id,
                    inherited_style,
                    pathops,
                )
            )
        return "\n".join(fragments)

    result = _merge_paths(normalized_operation, operands, pathops)
    if _path_is_empty(result):
        raise ValueError(
            f"Shape Boolean {normalized_operation} produced no filled area"
        )
    _validate_result_id_collision(output_id, elements_by_id, source_id_list)
    return _serialize_result_path(
        result,
        output_id,
        inherited_style,
        pathops,
    )


def _validate_source_ids(source_ids: Sequence[str]) -> list[str]:
    if isinstance(source_ids, (str, bytes)):
        raise ValueError("Shape Boolean source_ids must be a sequence of ids")
    values = list(source_ids)
    if len(values) < 2:
        raise ValueError("Shape Boolean requires at least two source ids")
    if any(not isinstance(value, str) or not value for value in values):
        raise ValueError("Shape Boolean source ids must be non-empty strings")
    if len(set(values)) != len(values):
        raise ValueError("Shape Boolean source ids must be unique")
    return values


def _validate_output_id(output_id: str) -> None:
    if not isinstance(output_id, str) or _ID_RE.fullmatch(output_id) is None:
        raise ValueError(f"Invalid SVG output id: {output_id!r}")


def _validate_style_overrides(
    style: Mapping[str, str] | None,
) -> dict[str, str]:
    if style is None:
        return {}
    unknown = sorted(set(style) - set(_STYLE_OVERRIDE_ATTRS))
    if unknown:
        raise ValueError(
            "Unsupported shape Boolean style override(s): "
            + ", ".join(unknown)
        )
    normalized: dict[str, str] = {}
    for name in _STYLE_OVERRIDE_ATTRS:
        if name not in style:
            continue
        value = style[name]
        if not isinstance(value, str) or not value.strip():
            raise ValueError(
                f"Shape Boolean style override {name!r} must be a non-empty string"
            )
        normalized[name] = value.strip()
    return normalized


def _index_element_ids(
    root: ET.Element,
) -> tuple[dict[str, ET.Element], set[str]]:
    elements: dict[str, ET.Element] = {}
    duplicates: set[str] = set()
    for element in root.iter():
        element_id = element.get("id")
        if not element_id:
            continue
        if element_id in elements:
            duplicates.add(element_id)
        else:
            elements[element_id] = element
    return elements, duplicates


def _validate_result_id_collision(
    result_id: str,
    elements_by_id: Mapping[str, ET.Element],
    source_ids: Sequence[str],
) -> None:
    if result_id in elements_by_id and result_id not in source_ids:
        raise ValueError(
            f"Shape Boolean result id already exists outside the sources: "
            f"{result_id!r}"
        )


def _load_pathops() -> Any:
    """Reject excluded runtime backends without importing or probing them."""
    raise RuntimeError("Shape Boolean is unavailable in the declared dependency set; no geometry was substituted")


def _element_to_pathops(
    element: ET.Element,
    parents: Mapping[ET.Element, ET.Element],
    pathops: Any,
    *,
    font_dirs: Sequence[str | Path] = (),
) -> Any:
    chain = _element_chain(element, parents)
    _validate_source_location(element, chain)
    matrix = _combined_transform(chain)
    tag = _local_name(element.tag)

    if tag == "g":
        commands = _compact_preset_commands(element, matrix)
    elif tag in _BOOLEAN_TAGS:
        commands = _shape_commands(element)
        commands = transform_path_commands(commands, matrix)
    elif tag == "text":
        from .text_outline import text_element_to_path_commands

        commands = text_element_to_path_commands(
            element,
            parents,
            font_dirs=font_dirs,
        )
        commands = transform_path_commands(commands, matrix)
    else:
        supported = ", ".join((*sorted(_BOOLEAN_TAGS), "text"))
        raise ValueError(
            f"Shape Boolean source {_element_label(element)} must be a "
            f"supported {supported}, or a compact authored preset group"
        )

    result = _simplify_path(_commands_to_pathops(commands, pathops), pathops)
    if _path_is_empty(result):
        raise ValueError(
            f"Shape Boolean source {_element_label(element)} has no filled area"
        )
    return result


def _element_chain(
    element: ET.Element,
    parents: Mapping[ET.Element, ET.Element],
) -> list[ET.Element]:
    chain = [element]
    current = element
    while current in parents:
        current = parents[current]
        chain.append(current)
    chain.reverse()
    return chain


def _validate_source_location(
    element: ET.Element,
    chain: Sequence[ET.Element],
) -> None:
    definition_ancestor = next(
        (
            ancestor
            for ancestor in chain[:-1]
            if _local_name(ancestor.tag) in _DEFINITION_TAGS
        ),
        None,
    )
    if definition_ancestor is not None:
        raise ValueError(
            f"Shape Boolean source {_element_label(element)} is inside "
            f"<{_local_name(definition_ancestor.tag)}>"
        )
    nested_svg_count = sum(
        1 for ancestor in chain if _local_name(ancestor.tag) == "svg"
    )
    if nested_svg_count > 1:
        raise ValueError(
            f"Shape Boolean source {_element_label(element)} is inside a nested "
            "<svg>; materialize its viewport transform first"
        )
    for ancestor in chain[:-1]:
        if (
            _local_name(ancestor.tag) == "g"
            and authored_preset_encoding(ancestor) is not None
        ):
            raise ValueError(
                f"Shape Boolean source {_element_label(element)} is inside an "
                "authored preset; select the preset group's id"
            )
    _validate_geometry_presentation(element, chain)


def _validate_geometry_presentation(
    element: ET.Element,
    chain: Sequence[ET.Element],
) -> None:
    fill_rule = "nonzero"
    for current in chain:
        if current.get("class") is not None:
            raise ValueError(
                f"Shape Boolean source {_element_label(element)} uses class; "
                "materialize its computed presentation first"
            )
        transport_attrs = sorted(
            name
            for name in _ROUNDTRIP_TRANSPORT_ATTRS
            if current.get(name) is not None
        )
        if transport_attrs:
            raise ValueError(
                f"Shape Boolean source {_element_label(element)} carries "
                "PPTX import/round-trip metadata "
                f"({', '.join(transport_attrs)}); author or materialize an "
                "ordinary SVG shape first"
            )
        inline = parse_inline_style(current.get("style"))
        inline_transform = inline.get("transform")
        if (
            inline_transform is not None
            and inline_transform.strip().lower() not in {"", "none"}
        ):
            raise ValueError(
                f"Shape Boolean source {_element_label(element)} uses a CSS "
                "transform; write it as an SVG transform attribute first"
            )
        display = inline.get("display", current.get("display"))
        if display is not None and display.strip().lower() == "none":
            raise ValueError(
                f"Shape Boolean source {_element_label(element)} is not "
                "displayed"
            )
        visibility = inline.get("visibility", current.get("visibility"))
        if visibility is not None and visibility.strip().lower() in {
            "collapse",
            "hidden",
        }:
            raise ValueError(
                f"Shape Boolean source {_element_label(element)} is not visible"
            )
        for name in ("clip-path", "mask"):
            raw = inline.get(name, current.get(name))
            if raw is not None and raw.strip().lower() not in {"", "none"}:
                raise ValueError(
                    f"Shape Boolean source {_element_label(element)} uses "
                    f"{name}; materialize the visible contour first"
                )
        raw_filter = inline.get("filter", current.get("filter"))
        if raw_filter is not None and raw_filter.strip().lower() not in {
            "",
            "none",
        }:
            raise ValueError(
                f"Shape Boolean source {_element_label(element)} uses filter; "
                "reapply the effect after materializing the result"
            )
        raw_dash_offset = inline.get(
            "stroke-dashoffset",
            current.get("stroke-dashoffset"),
        )
        if raw_dash_offset is not None:
            raise ValueError(
                f"Shape Boolean source {_element_label(element)} uses "
                "stroke-dashoffset; dashed-arc phase cannot be materialized "
                "as filled Boolean geometry"
            )
        raw_fill_rule = inline.get("fill-rule", current.get("fill-rule"))
        if (
            raw_fill_rule is not None
            and raw_fill_rule.strip().lower() != "inherit"
        ):
            fill_rule = raw_fill_rule.strip().lower()
    if fill_rule != "nonzero":
        raise ValueError(
            f"Shape Boolean source {_element_label(element)} uses "
            f"fill-rule={fill_rule!r}; use explicit nonzero contour direction"
        )


def _combined_transform(chain: Sequence[ET.Element]) -> AffineMatrix:
    matrix = IDENTITY_MATRIX
    for element in chain[1:]:
        transform = element.get("transform")
        if transform:
            matrix = matrix_multiply(
                matrix,
                parse_transform_matrix(transform),
            )
    return matrix


def _materialize_baked_stroke_style(
    style: Mapping[str, str],
    matrix: AffineMatrix,
) -> dict[str, str]:
    """Keep stroke metrics visually stable after baking a geometry transform."""
    materialized = dict(style)
    raw_vector_effect = materialized.get("vector-effect")
    vector_effect = None
    if raw_vector_effect is not None:
        try:
            vector_effect = parse_project_stroke_enum(
                "vector-effect",
                raw_vector_effect,
            )
        except ValueError as exc:
            raise ValueError(
                f"Shape Boolean primary vector-effect "
                f"{raw_vector_effect!r} {exc}"
            ) from exc
        materialized["vector-effect"] = vector_effect

    stroke = materialized.get("stroke")
    if not stroke or stroke.strip().lower() in {"none", "transparent"}:
        return materialized
    if vector_effect == "non-scaling-stroke":
        return materialized

    a, b, c, d, _e, _f = matrix
    scale = math.sqrt(abs(a * d - b * c))
    if not math.isfinite(scale):
        raise ValueError(
            "Shape Boolean primary transform produces a non-finite stroke scale"
        )
    if scale == 1.0:
        return materialized

    raw_width = materialized.get("stroke-width", "1")
    try:
        source_width = parse_project_geometry_length(
            raw_width,
            "stroke-width",
        )
    except ValueError as exc:
        raise ValueError(
            f"Shape Boolean primary stroke-width {raw_width!r} {exc}"
        ) from exc
    materialized["stroke-width"] = format_project_geometry_length(
        source_width * scale
    )

    raw_dasharray = materialized.get("stroke-dasharray")
    if raw_dasharray is None or raw_dasharray.strip().lower() == "none":
        return materialized
    try:
        parsed_dasharray = parse_project_stroke_dasharray(raw_dasharray)
    except ValueError as exc:
        raise ValueError(
            f"Shape Boolean primary stroke-dasharray {raw_dasharray!r} {exc}"
        ) from exc
    if parsed_dasharray is not None:
        _preset, values = parsed_dasharray
        materialized["stroke-dasharray"] = " ".join(
            format_project_geometry_length(value * scale)
            for value in values
        )
    return materialized


def _shape_commands(element: ET.Element) -> list[PathCommand]:
    tag = _local_name(element.tag)
    if tag == "path":
        raw_path = element.get("d")
        if raw_path is None:
            raise ValueError(f"{_element_label(element)} requires d")
        commands = normalize_path_commands(
            svg_path_to_absolute(parse_svg_path(raw_path))
        )
        _require_closed_commands(commands, _element_label(element))
        return commands

    if tag == "polygon":
        points = parse_svg_points(
            element.get("points", ""),
            min_points=3,
        )
        commands = [PathCommand("M", [*points[0]])]
        commands.extend(
            PathCommand("L", [x, y])
            for x, y in points[1:]
        )
        commands.append(PathCommand("Z", []))
        return commands

    if tag == "rect":
        return _rect_commands(element)

    if tag == "circle":
        cx = _geometry_value(element, "cx", default=0.0)
        cy = _geometry_value(element, "cy", default=0.0)
        radius = _geometry_value(element, "r", required=True)
        if radius <= 0:
            raise ValueError(f"{_element_label(element)} r must be greater than 0")
        return _ellipse_commands(cx, cy, radius, radius)

    if tag == "ellipse":
        cx = _geometry_value(element, "cx", default=0.0)
        cy = _geometry_value(element, "cy", default=0.0)
        radius_x = _geometry_value(element, "rx", required=True)
        radius_y = _geometry_value(element, "ry", required=True)
        if radius_x <= 0 or radius_y <= 0:
            raise ValueError(
                f"{_element_label(element)} rx and ry must be greater than 0"
            )
        return _ellipse_commands(cx, cy, radius_x, radius_y)

    raise AssertionError(f"Unhandled Boolean source tag: {tag}")


def _geometry_value(
    element: ET.Element,
    attribute: str,
    *,
    default: float | None = None,
    required: bool = False,
) -> float:
    raw = element.get(attribute)
    if raw is None:
        if required:
            raise ValueError(f"{_element_label(element)} requires {attribute}")
        if default is None:
            raise AssertionError("Geometry attribute requires a default")
        return default
    try:
        return parse_project_geometry_length(raw, attribute)
    except ValueError as exc:
        raise ValueError(
            f"{_element_label(element)} {attribute}: {exc}"
        ) from exc


def _rect_commands(element: ET.Element) -> list[PathCommand]:
    x = _geometry_value(element, "x", default=0.0)
    y = _geometry_value(element, "y", default=0.0)
    width = _geometry_value(element, "width", required=True)
    height = _geometry_value(element, "height", required=True)
    if width <= 0 or height <= 0:
        raise ValueError(
            f"{_element_label(element)} width and height must be greater than 0"
        )

    raw_rx = element.get("rx")
    raw_ry = element.get("ry")
    radius_x = (
        _geometry_value(element, "rx", required=True)
        if raw_rx is not None else 0.0
    )
    radius_y = (
        _geometry_value(element, "ry", required=True)
        if raw_ry is not None else 0.0
    )
    if raw_rx is not None and raw_ry is None:
        radius_y = radius_x
    elif raw_ry is not None and raw_rx is None:
        radius_x = radius_y
    if radius_x < 0 or radius_y < 0:
        raise ValueError(
            f"{_element_label(element)} rx and ry must not be negative"
        )

    radius_x = min(radius_x, width / 2.0)
    radius_y = min(radius_y, height / 2.0)
    if radius_x <= 0 or radius_y <= 0:
        return [
            PathCommand("M", [x, y]),
            PathCommand("L", [x + width, y]),
            PathCommand("L", [x + width, y + height]),
            PathCommand("L", [x, y + height]),
            PathCommand("Z", []),
        ]

    kx = radius_x * _BEZIER_QUARTER_K
    ky = radius_y * _BEZIER_QUARTER_K
    right = x + width
    bottom = y + height
    return [
        PathCommand("M", [x + radius_x, y]),
        PathCommand("L", [right - radius_x, y]),
        PathCommand(
            "C",
            [
                right - radius_x + kx,
                y,
                right,
                y + radius_y - ky,
                right,
                y + radius_y,
            ],
        ),
        PathCommand("L", [right, bottom - radius_y]),
        PathCommand(
            "C",
            [
                right,
                bottom - radius_y + ky,
                right - radius_x + kx,
                bottom,
                right - radius_x,
                bottom,
            ],
        ),
        PathCommand("L", [x + radius_x, bottom]),
        PathCommand(
            "C",
            [
                x + radius_x - kx,
                bottom,
                x,
                bottom - radius_y + ky,
                x,
                bottom - radius_y,
            ],
        ),
        PathCommand("L", [x, y + radius_y]),
        PathCommand(
            "C",
            [
                x,
                y + radius_y - ky,
                x + radius_x - kx,
                y,
                x + radius_x,
                y,
            ],
        ),
        PathCommand("Z", []),
    ]


def _ellipse_commands(
    center_x: float,
    center_y: float,
    radius_x: float,
    radius_y: float,
) -> list[PathCommand]:
    kx = radius_x * _BEZIER_QUARTER_K
    ky = radius_y * _BEZIER_QUARTER_K
    return [
        PathCommand("M", [center_x + radius_x, center_y]),
        PathCommand(
            "C",
            [
                center_x + radius_x,
                center_y + ky,
                center_x + kx,
                center_y + radius_y,
                center_x,
                center_y + radius_y,
            ],
        ),
        PathCommand(
            "C",
            [
                center_x - kx,
                center_y + radius_y,
                center_x - radius_x,
                center_y + ky,
                center_x - radius_x,
                center_y,
            ],
        ),
        PathCommand(
            "C",
            [
                center_x - radius_x,
                center_y - ky,
                center_x - kx,
                center_y - radius_y,
                center_x,
                center_y - radius_y,
            ],
        ),
        PathCommand(
            "C",
            [
                center_x + kx,
                center_y - radius_y,
                center_x + radius_x,
                center_y - ky,
                center_x + radius_x,
                center_y,
            ],
        ),
        PathCommand("Z", []),
    ]


def _compact_preset_commands(
    group: ET.Element,
    matrix: AffineMatrix,
) -> list[PathCommand]:
    encoding = authored_preset_encoding(group)
    if encoding != "compact":
        raise ValueError(
            f"Shape Boolean source {_element_label(group)} must be a compact "
            "authored preset group"
        )
    errors = validate_authored_preset_group(group)
    if errors:
        raise ValueError(
            f"Invalid compact authored preset {_element_label(group)}: "
            + "; ".join(errors)
        )
    if group.get("data-pptx-object") != "shape":
        raise ValueError(
            f"Shape Boolean source {_element_label(group)} must be a shape, "
            "not a connector"
        )

    frame = tuple(
        float(value)
        for value in (group.get("data-pptx-frame") or "").split()
    )
    if len(frame) != 4:
        raise ValueError(
            f"Compact authored preset {_element_label(group)} has an invalid "
            "data-pptx-frame"
        )
    adjustments = {
        name[len("data-pptx-av-"):]: value
        for name, value in group.attrib.items()
        if name.startswith("data-pptx-av-")
    }
    rendered = render_preset_geometry(
        group.get("data-pptx-prst") or "",
        Xfrm(x=frame[0], y=frame[1], w=frame[2], h=frame[3]),
        adjustments,
    )
    children = list(group)
    if len(children) != len(rendered.paths):
        raise ValueError(
            f"Compact authored preset {_element_label(group)} layer count "
            "differs from its registry geometry"
        )

    commands: list[PathCommand] = []
    for child, layer in zip(children, rendered.paths):
        if layer.fill == "none":
            continue
        raw_path = child.get("d")
        if raw_path is None:
            raise ValueError(
                f"Compact authored preset {_element_label(group)} contains a "
                "path without d"
            )
        child_commands = normalize_path_commands(
            svg_path_to_absolute(parse_svg_path(raw_path))
        )
        child_commands = _close_filled_commands(
            child_commands,
            f"{_element_label(group)} preset layer",
        )
        commands.extend(transform_path_commands(child_commands, matrix))
    if not commands:
        raise ValueError(
            f"Compact authored preset {_element_label(group)} has no filled "
            "silhouette"
        )
    return commands


























def _effective_style(
    element: ET.Element,
    parents: Mapping[ET.Element, ET.Element],
) -> dict[str, str]:
    values: dict[str, str] = {}
    opacity = 1.0
    parent_opacity = 1.0
    chain = _element_chain(element, parents)
    for index, current in enumerate(chain):
        inline = parse_inline_style(current.get("style"))
        raw_opacity = inline.get("opacity", current.get("opacity"))
        if raw_opacity is None:
            local_opacity = 1.0
        elif raw_opacity.strip() == "inherit":
            local_opacity = parent_opacity
        else:
            local_opacity = parse_opacity(raw_opacity)
        if index > 0:
            opacity *= local_opacity
        parent_opacity = local_opacity
        for name in _PRESENTATION_ATTRS:
            if name == "opacity":
                continue
            raw = inline.get(name, current.get(name))
            if raw is not None and raw.strip() != "inherit":
                values[name] = raw.strip()
    if opacity < 1.0:
        values["opacity"] = _format_number(opacity)
    return {
        name: values[name]
        for name in _PRESENTATION_ATTRS
        if name in values
    }










def _local_name(tag: object) -> str:
    raw = str(tag)
    return raw.rsplit("}", 1)[-1] if "}" in raw else raw


def _element_label(element: ET.Element) -> str:
    tag = _local_name(element.tag)
    element_id = element.get("id")
    return f"<{tag} id={element_id!r}>" if element_id else f"<{tag}>"
