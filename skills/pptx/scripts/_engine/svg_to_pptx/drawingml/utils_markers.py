# Adapted from PPT Master 5e8856b (MIT); see assets/licenses/ppt_master.txt.
"""Coordinate, transform, color, and font helpers for DrawingML conversion.

See references/shared-standards-core.md §2.1 for project geometry and
references/svg-effects.md §§6.2–6.8 for paint, image-fit, line-presentation,
and transform authoring contracts.
"""

from __future__ import annotations

import colorsys
import json
import math
import re
import unicodedata
from collections import Counter
from collections.abc import Iterator
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from xml.etree import ElementTree as ET

from pptx_gradients import (
    NATIVE_GRADIENT_ATTR,
    NATIVE_GRADIENT_PREVIEW_SHA256_ATTR,
    NATIVE_GRADIENT_SHA256_ATTR,
    preserved_native_gradient_xml,
)

from pptx_shapes import (
    OOXML_COORDINATE_MAX,
    resolve_preset_preview_hash,
    svg_preset_preview_fingerprint,
    validate_ooxml_xfrm,
)
from language_tags import language_base, language_uses_rtl, normalize_language_tag

from .context import AffineMatrix, ConvertContext, IDENTITY_MATRIX

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


# Responsibility: utils_markers; adapted from the original module.

def project_definition_index(
    root: ET.Element,
) -> tuple[dict[str, ET.Element], set[str]]:
    """Return direct ``<defs>`` children by id plus duplicate ids."""
    from .utils_transforms import _svg_element_tag
    definitions: dict[str, ET.Element] = {}
    duplicates: set[str] = set()
    for defs_elem in root.iter():
        if _svg_element_tag(defs_elem) != 'defs':
            continue
        for child in defs_elem:
            definition_id = (child.get('id') or '').strip()
            if not definition_id:
                continue
            if definition_id in definitions:
                duplicates.add(definition_id)
            definitions[definition_id] = child
    return definitions, duplicates


def project_definition_errors(root: ET.Element) -> list[str]:
    """Return errors for definitions outside the closed local-ref contract."""
    from .utils_constants import PROJECT_DEFINITION_TAGS
    from .utils_transforms import _svg_element_tag
    from .utils_transforms import _transform_element_label
    parent_by_id = {
        id(child): parent
        for parent in root.iter()
        for child in list(parent)
    }
    definitions, duplicate_definition_ids = project_definition_index(root)
    errors = {
        f'Duplicate direct <defs> id {definition_id!r} makes local references ambiguous'
        for definition_id in duplicate_definition_ids
    }
    all_id_counts = Counter(
        elem.get('id')
        for elem in root.iter()
        if (elem.get('id') or '').strip()
    )
    for definition_id in definitions:
        if all_id_counts[definition_id] > 1:
            errors.add(
                f'Definition id {definition_id!r} is duplicated in the SVG; '
                'local references require one unique target'
            )

    for elem in root.iter():
        tag = _svg_element_tag(elem)
        if tag not in PROJECT_DEFINITION_TAGS:
            continue
        label = _transform_element_label(elem)
        parent = parent_by_id.get(id(elem))
        if parent is None or _svg_element_tag(parent) != 'defs':
            errors.add(f'{label} must be a direct child of <defs>')
        if not (elem.get('id') or '').strip():
            errors.add(f'{label} requires a non-empty unique id')
    return sorted(errors)


def _project_marker_polygon_points(
    raw: str,
) -> list[tuple[float, float]] | None:
    """Parse finite marker polygon points from the closed project grammar."""
    tokens = [token for token in re.split(r'[\s,]+', raw.strip()) if token]
    if not tokens or len(tokens) % 2:
        return None
    try:
        values = [float(token) for token in tokens]
    except ValueError:
        return None
    if not all(math.isfinite(value) for value in values):
        return None
    return list(zip(values[::2], values[1::2]))


def _project_marker_path_points(raw: str) -> list[tuple[float, float]]:
    """Return the explicit M/L points from an already-validated marker path."""
    from .utils_constants import _PROJECT_MARKER_COMMAND_POINT_RE
    points = [
        (float(x), float(y))
        for x, y in _PROJECT_MARKER_COMMAND_POINT_RE.findall(raw)
    ]
    return [
        point
        for point in points
        if all(math.isfinite(coordinate) for coordinate in point)
    ]


def _project_marker_cross(
    first: tuple[float, float],
    second: tuple[float, float],
    third: tuple[float, float],
) -> float:
    """Return the signed turn for three marker vertices."""
    return (
        (second[0] - first[0]) * (third[1] - second[1])
        - (second[1] - first[1]) * (third[0] - second[0])
    )


def _project_marker_segments_cross(
    first_start: tuple[float, float],
    first_end: tuple[float, float],
    second_start: tuple[float, float],
    second_end: tuple[float, float],
) -> bool:
    """Return whether two non-adjacent marker edges strictly intersect."""
    first_a = _project_marker_cross(first_start, first_end, second_start)
    first_b = _project_marker_cross(first_start, first_end, second_end)
    second_a = _project_marker_cross(second_start, second_end, first_start)
    second_b = _project_marker_cross(second_start, second_end, first_end)
    return first_a * first_b < 0 and second_a * second_b < 0


def _project_marker_quadrilateral_type(
    points: list[tuple[float, float]],
) -> str | None:
    """Classify one simple four-point marker as diamond or stealth."""
    if len(points) != 4:
        return None
    if (
        _project_marker_segments_cross(points[0], points[1], points[2], points[3])
        or _project_marker_segments_cross(
            points[1], points[2], points[3], points[0]
        )
    ):
        return None
    turns = [
        _project_marker_cross(
            points[index],
            points[(index + 1) % 4],
            points[(index + 2) % 4],
        )
        for index in range(4)
    ]
    if any(abs(turn) <= 1e-12 for turn in turns):
        return None
    signs = {turn > 0 for turn in turns}
    return 'diamond' if len(signs) == 1 else 'stealth'


def classify_project_marker_shape(marker_elem: ET.Element) -> str | None:
    """Classify one marker into a DrawingML line-end shape, if representable."""
    from .utils_constants import PROJECT_NON_VISUAL_DEFINITION_CHILD_TAGS
    from .utils_constants import _PROJECT_MARKER_ARROW_PATH_RE
    from .utils_constants import _PROJECT_MARKER_DIAMOND_PATH_RE
    from .utils_constants import _PROJECT_MARKER_TRIANGLE_PATH_RE
    from .utils_transforms import _svg_element_tag
    visual_children = [
        child
        for child in list(marker_elem)
        if _svg_element_tag(child)
        not in PROJECT_NON_VISUAL_DEFINITION_CHILD_TAGS
    ]
    if len(visual_children) != 1:
        return None
    shape = visual_children[0]
    tag = (_svg_element_tag(shape) or '').lower()
    if tag in {'circle', 'ellipse'}:
        return 'oval'
    if tag == 'path':
        path_data = shape.get('d', '')
        if _PROJECT_MARKER_TRIANGLE_PATH_RE.fullmatch(path_data):
            return 'triangle'
        if _PROJECT_MARKER_ARROW_PATH_RE.fullmatch(path_data):
            return 'arrow'
        if _PROJECT_MARKER_DIAMOND_PATH_RE.fullmatch(path_data):
            points = _project_marker_path_points(path_data)
            return _project_marker_quadrilateral_type(points)
        return None
    if tag == 'polygon':
        points = _project_marker_polygon_points(shape.get('points', ''))
        if points is None:
            return None
        if len(points) == 3:
            return 'triangle'
        return _project_marker_quadrilateral_type(points)
    return None


def _project_effective_presentation_value(
    elem: ET.Element,
    name: str,
    parent_by_id: dict[int, ET.Element],
) -> str | None:
    """Resolve one inherited presentation value for project validation."""
    from .utils_paint_values import parse_inline_style
    current: ET.Element | None = elem
    while current is not None:
        style_values = parse_inline_style(current.get('style'))
        if name in style_values:
            return style_values[name]
        direct = current.get(name)
        if direct is not None:
            return direct
        current = parent_by_id.get(id(current))
    return None


def project_marker_errors(root: ET.Element) -> list[str]:
    """Validate SVG line-end markers against the native arrow contract."""
    from .utils_constants import PROJECT_NON_VISUAL_DEFINITION_CHILD_TAGS
    from .utils_transforms import _svg_element_tag
    from .utils_transforms import _transform_element_label
    from .utils_paint_values import parse_svg_color
    from .utils_fonts import resolve_url_id
    definitions, _duplicates = project_definition_index(root)
    parent_by_id = {
        id(child): parent
        for parent in root.iter()
        for child in list(parent)
    }
    errors: set[str] = set()
    checked_markers: set[str] = set()

    for elem in root.iter():
        for attribute_name in ('marker-start', 'marker-end'):
            raw_reference = elem.get(attribute_name)
            if (
                raw_reference is None
                or raw_reference.strip().lower() == 'none'
            ):
                continue

            label = _transform_element_label(elem)
            tag = (_svg_element_tag(elem) or '').lower()
            if tag not in {'line', 'path'}:
                errors.add(
                    f'{label} {attribute_name} is allowed only on <line> '
                    'or <path>'
                )

            match = re.fullmatch(r'url\(#([^)]+)\)', raw_reference.strip())
            if match is None:
                errors.add(
                    f'{label} {attribute_name} must be an exact local '
                    f'url(#id) reference; got {raw_reference!r}'
                )
                continue

            marker_id = match.group(1)
            marker = definitions.get(marker_id)
            if marker is None or _svg_element_tag(marker) != 'marker':
                errors.add(
                    f'{label} {attribute_name}=url(#{marker_id}) has no '
                    f'matching direct <defs><marker id="{marker_id}"> '
                    'definition'
                )
                continue

            visual_children = [
                child
                for child in list(marker)
                if _svg_element_tag(child)
                not in PROJECT_NON_VISUAL_DEFINITION_CHILD_TAGS
            ]
            shape = visual_children[0] if len(visual_children) == 1 else None
            marker_shape_type = (
                classify_project_marker_shape(marker)
                if shape is not None
                else None
            )
            if marker_id not in checked_markers:
                checked_markers.add(marker_id)
                marker_label = f'<marker id="{marker_id}">'
                if marker.get('orient') not in {
                    'auto',
                    'auto-start-reverse',
                }:
                    errors.add(
                        f'{marker_label} requires orient="auto" or '
                        'orient="auto-start-reverse"'
                    )
                marker_units = marker.get('markerUnits', 'strokeWidth')
                if marker_units not in {'strokeWidth', 'userSpaceOnUse'}:
                    errors.add(
                        f'{marker_label} has unsupported '
                        f'markerUnits={marker_units!r}'
                    )
                for size_attribute in ('markerWidth', 'markerHeight'):
                    raw_size = marker.get(size_attribute)
                    if raw_size is None:
                        continue
                    try:
                        size = float(raw_size)
                    except ValueError:
                        size = math.nan
                    if not math.isfinite(size) or size <= 0:
                        errors.add(
                            f'{marker_label} {size_attribute} must be a '
                            f'positive finite number; got {raw_size!r}'
                        )

                if shape is None:
                    errors.add(
                        f'{marker_label} must contain exactly one direct '
                        'triangle, stealth, arrow, diamond, or oval shape'
                    )
                else:
                    shape_tag = (_svg_element_tag(shape) or '').lower()
                    if shape.get('transform'):
                        errors.add(
                            f'{marker_label} child <{shape_tag}> cannot use '
                            'transform'
                        )
                    if marker_shape_type is None and shape_tag == 'path':
                        errors.add(
                            f'{marker_label} path must be a closed 3-vertex '
                            'triangle, a simple closed 4-vertex '
                            'diamond/stealth, or an open 3-vertex arrow, '
                            'with one explicit M/L command per vertex'
                        )
                    elif (
                        marker_shape_type is None
                        and shape_tag == 'polygon'
                    ):
                        errors.add(
                            f'{marker_label} polygon must contain exactly '
                            '3 finite vertices or 4 finite vertices forming '
                            'a simple diamond/stealth quadrilateral'
                        )
                    elif (
                        marker_shape_type is None
                        and shape_tag not in {'circle', 'ellipse'}
                    ):
                        errors.add(
                            f'{marker_label} child <{shape_tag}> has no native '
                            'line-end mapping'
                        )

            if shape is None:
                continue
            stroke_value = _project_effective_presentation_value(
                elem,
                'stroke',
                parent_by_id,
            )
            marker_fill = _project_effective_presentation_value(
                shape,
                'fill',
                parent_by_id,
            ) or '#000000'
            if marker_shape_type == 'arrow':
                if marker_fill.strip().lower() != 'none':
                    errors.add(
                        f'{label} {attribute_name}=url(#{marker_id}) open '
                        'arrow marker requires fill="none"'
                    )
                marker_channel = 'stroke'
                marker_paint = _project_effective_presentation_value(
                    shape,
                    marker_channel,
                    parent_by_id,
                ) or 'none'
            else:
                marker_channel = 'fill'
                marker_paint = marker_fill
            gradient_id = resolve_url_id(stroke_value)
            gradient = definitions.get(gradient_id) if gradient_id else None
            if gradient is not None:
                try:
                    native_gradient = preserved_native_gradient_xml(gradient)
                except ValueError:
                    native_gradient = None
                if native_gradient is not None:
                    # The original DrawingML line owns both gradient stroke
                    # and arrowhead paint; the solid SVG marker is its preview.
                    continue
            stroke_color, _stroke_alpha = parse_svg_color(stroke_value or '')
            marker_color, _marker_alpha = parse_svg_color(marker_paint)
            if stroke_color is None or marker_color is None:
                errors.add(
                    f'{label} {attribute_name} marker {marker_channel} and '
                    'line stroke must both be supported solid colors'
                )
            elif stroke_color != marker_color:
                errors.add(
                    f'{label} {attribute_name}=url(#{marker_id}) marker '
                    f'{marker_channel} {marker_paint!r} does not match '
                    f'effective line stroke {stroke_value!r}'
                )

    return sorted(errors)
