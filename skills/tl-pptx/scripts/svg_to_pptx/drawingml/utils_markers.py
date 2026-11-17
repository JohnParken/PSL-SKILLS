"""Coordinate, transform, color, and font helpers for DrawingML conversion.

See references/shared-standards-core.md §2.1 for project geometry and
references/svg-effects.md §§6.2–6.8 for paint, image-fit, line-presentation,
and transform authoring contracts.
"""

from __future__ import annotations

import math
import re
from xml.etree import ElementTree as ET

from pptx_gradients import (
    preserved_native_gradient_xml,
)



# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


# Adapted from utils.py; upstream MIT, Hugo He, 2025-2026.
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
    # Resolve shared symbols at call time; retain the public module contract.
    from .utils import (
        _PROJECT_MARKER_COMMAND_POINT_RE,
    )
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
    # Resolve shared symbols at call time; retain the public module contract.
    from .utils import (
        _project_marker_cross,
    )
    first_a = _project_marker_cross(first_start, first_end, second_start)
    first_b = _project_marker_cross(first_start, first_end, second_end)
    second_a = _project_marker_cross(second_start, second_end, first_start)
    second_b = _project_marker_cross(second_start, second_end, first_end)
    return first_a * first_b < 0 and second_a * second_b < 0


def _project_marker_quadrilateral_type(
    points: list[tuple[float, float]],
) -> str | None:
    """Classify one simple four-point marker as diamond or stealth."""
    # Resolve shared symbols at call time; retain the public module contract.
    from .utils import (
        _project_marker_cross,
        _project_marker_segments_cross,
    )
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
    # Resolve shared symbols at call time; retain the public module contract.
    from .utils import (
        PROJECT_NON_VISUAL_DEFINITION_CHILD_TAGS,
        _PROJECT_MARKER_ARROW_PATH_RE,
        _PROJECT_MARKER_DIAMOND_PATH_RE,
        _PROJECT_MARKER_TRIANGLE_PATH_RE,
        _project_marker_path_points,
        _project_marker_polygon_points,
        _project_marker_quadrilateral_type,
        _svg_element_tag,
    )
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
    # Resolve shared symbols at call time; retain the public module contract.
    from .utils import (
        parse_inline_style,
    )
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
    # Resolve shared symbols at call time; retain the public module contract.
    from .utils import (
        PROJECT_NON_VISUAL_DEFINITION_CHILD_TAGS,
        _project_effective_presentation_value,
        _svg_element_tag,
        _transform_element_label,
        classify_project_marker_shape,
        parse_svg_color,
        project_definition_index,
        resolve_url_id,
    )
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


def resolve_project_text_image_fill(pattern: ET.Element) -> tuple[str, ET.Element]:
    """Resolve the controlled one-image pattern used for native text picture fills."""
    # Resolve shared symbols at call time; retain the public module contract.
    from .utils import (
        PROJECT_TEXT_IMAGE_FILL_ATTR,
        PROJECT_TEXT_IMAGE_FILL_MODES,
        SVG_NS,
        _svg_element_tag,
    )
    if _svg_element_tag(pattern) != 'pattern':
        raise ValueError('definition must be an SVG <pattern>')

    mode = pattern.get(PROJECT_TEXT_IMAGE_FILL_ATTR, '')
    if mode not in PROJECT_TEXT_IMAGE_FILL_MODES:
        supported = ', '.join(sorted(PROJECT_TEXT_IMAGE_FILL_MODES))
        raise ValueError(f'{PROJECT_TEXT_IMAGE_FILL_ATTR} must be one of: {supported}')
    preset_attributes = [
        name
        for name in ('data-pptx-pattern', 'data-pptx-fg', 'data-pptx-bg')
        if pattern.get(name) is not None
    ]
    if preset_attributes:
        raise ValueError(
            'text image fill must not combine preset-pattern attributes: '
            f'{", ".join(preset_attributes)}'
        )
    if pattern.get('patternTransform') is not None:
        raise ValueError('pattern must not use patternTransform')

    children = list(pattern)
    if len(children) != 1 or children[0].tag != f'{{{SVG_NS}}}image':
        raise ValueError('pattern must contain exactly one direct SVG <image> child')

    image = children[0]
    unsupported = [
        name
        for name in (
            'clip-path',
            'filter',
            'fill-opacity',
            'mask',
            'opacity',
            'style',
            'transform',
        )
        if image.get(name) is not None
    ]
    if unsupported:
        raise ValueError(f"pattern image must not use {', '.join(unsupported)}")
    return mode, image


def project_paint_reference_errors(root: ET.Element) -> list[str]:
    """Validate local paint-server references and their native contexts."""
    # Resolve shared symbols at call time; retain the public module contract.
    from .utils import (
        PROJECT_REFERENCE_PAINT_PROPERTIES,
        PROJECT_TEXT_IMAGE_FILL_ATTR,
        _svg_element_tag,
        parse_inline_style,
        parse_project_paint,
        project_definition_index,
        resolve_project_text_image_fill,
    )
    definitions, _duplicates = project_definition_index(root)
    pattern_descendant_ids = {
        id(descendant)
        for pattern in root.iter()
        if _svg_element_tag(pattern) == 'pattern'
        for descendant in pattern.iter()
        if descendant is not pattern
    }
    fill_shape_tags = frozenset({
        'rect', 'circle', 'ellipse', 'path', 'polygon', 'polyline',
    })
    stroke_shape_tags = fill_shape_tags | {'line'}
    errors: set[str] = set()

    for elem in root.iter():
        style_values = parse_inline_style(elem.get('style'))
        for property_name in PROJECT_REFERENCE_PAINT_PROPERTIES:
            raw = (
                style_values[property_name]
                if property_name in style_values
                else elem.get(property_name)
            )
            if raw is None:
                continue
            try:
                kind, reference_id, _alpha = parse_project_paint(
                    raw,
                    property_name,
                )
            except ValueError:
                continue
            if kind != 'reference' or reference_id is None:
                continue

            elem_tag = _svg_element_tag(elem) or str(elem.tag)
            elem_tag_lower = elem_tag.lower()
            target = definitions.get(reference_id)
            if target is None:
                errors.add(
                    f'<{elem_tag}> {property_name}=url(#{reference_id}) has no '
                    'matching direct <defs> definition'
                )
                continue

            has_text_descendant = any(
                (_svg_element_tag(descendant) or '').lower() in {'text', 'tspan'}
                for descendant in elem.iter()
                if descendant is not elem
            )
            if id(elem) in pattern_descendant_ids:
                allowed_tags: tuple[str, ...] = ()
            elif property_name == 'fill' and elem_tag_lower in fill_shape_tags:
                allowed_tags = ('lineargradient', 'radialgradient', 'pattern')
            elif property_name == 'stroke' and elem_tag_lower in stroke_shape_tags:
                allowed_tags = ('lineargradient', 'radialgradient')
            elif property_name == 'fill' and elem_tag_lower in {'text', 'tspan'}:
                target_tag = (_svg_element_tag(target) or str(target.tag)).lower()
                if target_tag == 'pattern' and target.get(PROJECT_TEXT_IMAGE_FILL_ATTR) is not None:
                    allowed_tags = ('lineargradient', 'radialgradient', 'pattern')
                else:
                    allowed_tags = ('lineargradient', 'radialgradient')
            elif property_name == 'fill' and elem_tag_lower == 'g':
                allowed_tags = (
                    ('lineargradient', 'radialgradient')
                    if has_text_descendant
                    else ('lineargradient', 'radialgradient', 'pattern')
                )
            elif (
                property_name == 'stroke'
                and elem_tag_lower == 'g'
                and not has_text_descendant
            ):
                allowed_tags = ('lineargradient', 'radialgradient')
            else:
                allowed_tags = ()

            if not allowed_tags:
                errors.add(
                    f'<{elem_tag}> {property_name}=url(#{reference_id}) is not '
                    'supported by native PPTX conversion in this context'
                )
                continue

            target_tag = (_svg_element_tag(target) or str(target.tag)).lower()
            is_text_image_fill = (
                target_tag == 'pattern'
                and target.get(PROJECT_TEXT_IMAGE_FILL_ATTR) is not None
            )
            if is_text_image_fill and not (
                property_name == 'fill'
                and elem_tag_lower in {'text', 'tspan'}
            ):
                errors.add(
                    f'<{elem_tag}> {property_name}=url(#{reference_id}) uses a '
                    'text image fill pattern outside <text>/<tspan>'
                )
                continue
            if target_tag not in allowed_tags:
                tag_labels = {
                    'lineargradient': 'linearGradient',
                    'radialgradient': 'radialGradient',
                    'pattern': 'pattern',
                }
                expected = '/'.join(tag_labels[tag] for tag in allowed_tags)
                errors.add(
                    f'<{elem_tag}> {property_name}=url(#{reference_id}) resolves '
                    f'to <{_svg_element_tag(target) or target.tag}>; expected '
                    f'{expected}'
                )
                continue

            if property_name == 'fill' and elem_tag_lower in {'text', 'tspan'} and target_tag == 'pattern':
                try:
                    resolve_project_text_image_fill(target)
                except ValueError as exc:
                    errors.add(
                        f'<{elem_tag}> fill=url(#{reference_id}) has an invalid '
                        f'text image fill: {exc}'
                    )
    return sorted(errors)
