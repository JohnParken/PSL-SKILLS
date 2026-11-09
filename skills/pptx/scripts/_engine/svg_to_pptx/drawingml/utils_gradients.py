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


# Responsibility: utils_gradients; adapted from the original module.

def resolve_project_text_image_fill(pattern: ET.Element) -> tuple[str, ET.Element]:
    """Resolve the controlled one-image pattern used for native text picture fills."""
    from .utils_constants import PROJECT_TEXT_IMAGE_FILL_ATTR
    from .utils_constants import PROJECT_TEXT_IMAGE_FILL_MODES
    from .utils_constants import SVG_NS
    from .utils_transforms import _svg_element_tag
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
    from .utils_constants import PROJECT_REFERENCE_PAINT_PROPERTIES
    from .utils_constants import PROJECT_TEXT_IMAGE_FILL_ATTR
    from .utils_transforms import _svg_element_tag
    from .utils_paint_values import parse_inline_style
    from .utils_paint_values import parse_project_paint
    from .utils_markers import project_definition_index
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


def project_mask_errors(root: ET.Element) -> list[str]:
    """Reject SVG masks that native PPTX conversion cannot preserve."""
    from .utils_transforms import _svg_element_tag
    from .utils_transforms import _transform_element_label
    from .utils_paint_values import parse_inline_style
    errors: set[str] = set()
    for elem in root.iter():
        label = _transform_element_label(elem)
        if _svg_element_tag(elem) == 'mask':
            errors.add(
                f'{label} is an unsupported SVG mask definition; replace it '
                'with editable overlay or Boolean/cutout shapes, an image '
                'clip-path, or pre-rendered alpha imagery'
            )

        sources: list[str] = []
        if any(
            name.rsplit('}', 1)[-1].lower() == 'mask'
            for name in elem.attrib
        ):
            sources.append('mask attribute')
        if 'mask' in parse_inline_style(elem.get('style')):
            sources.append('inline style mask property')
        if sources:
            errors.add(
                f'{label} uses unsupported SVG mask presentation via '
                f'{", ".join(sources)}; native PPTX export would drop the '
                'effect. Use editable overlay or Boolean/cutout shapes, an '
                'image clip-path, or pre-rendered alpha imagery'
            )
    return sorted(errors)


def parse_project_gradient_ratio(raw: str) -> float:
    """Parse one normalized gradient coordinate or stop offset."""
    from .utils_lengths import _parse_svg_length_parts
    number, unit = _parse_svg_length_parts(raw)
    if unit == '%':
        number /= 100.0
    elif unit:
        raise ValueError('must be unitless or a percentage')
    if not 0.0 <= number <= 1.0:
        raise ValueError('must be within 0..1 or 0%..100%')
    return number


def parse_project_linear_gradient_coordinate(raw: str) -> float:
    """Parse one objectBoundingBox linear-gradient projection coordinate."""
    from .utils_constants import PROJECT_LINEAR_GRADIENT_COORDINATE_MAX
    from .utils_constants import PROJECT_LINEAR_GRADIENT_COORDINATE_MIN
    from .utils_lengths import _parse_svg_length_parts
    number, unit = _parse_svg_length_parts(raw)
    if unit == '%':
        number /= 100.0
    elif unit:
        raise ValueError('must be unitless or a percentage')
    if not (
        PROJECT_LINEAR_GRADIENT_COORDINATE_MIN
        <= number
        <= PROJECT_LINEAR_GRADIENT_COORDINATE_MAX
    ):
        raise ValueError(
            'must be within -0.105..1.105 or -10.5%..110.5%'
        )
    return number


def is_project_radial_focus_point(focus_x: float, focus_y: float) -> bool:
    """Return whether a focus lies inside the canonical SVG radial circle."""
    from .utils_constants import PROJECT_RADIAL_FOCUS_TOLERANCE
    if not math.isfinite(focus_x) or not math.isfinite(focus_y):
        return False
    return (
        (focus_x - 0.5) ** 2 + (focus_y - 0.5) ** 2
        <= 0.25 + PROJECT_RADIAL_FOCUS_TOLERANCE
    )


def project_gradient_errors(root: ET.Element) -> list[str]:
    """Validate the normalized native gradient authoring interface."""
    from .utils_constants import PROJECT_GRADIENT_TAGS
    from .utils_constants import PROJECT_NON_VISUAL_DEFINITION_CHILD_TAGS
    from .utils_transforms import _svg_element_tag
    from .utils_paint_values import parse_inline_style
    errors: set[str] = set()
    for gradient in root.iter():
        tag = _svg_element_tag(gradient)
        if tag not in PROJECT_GRADIENT_TAGS:
            continue
        gradient_id = gradient.get('id')
        label = f'<{tag} id="{gradient_id}">' if gradient_id else f'<{tag}>'
        if any(
            gradient.get(name) is not None
            for name in (
                NATIVE_GRADIENT_ATTR,
                NATIVE_GRADIENT_SHA256_ATTR,
                NATIVE_GRADIENT_PREVIEW_SHA256_ATTR,
            )
        ):
            try:
                native = preserved_native_gradient_xml(gradient)
            except ValueError as exc:
                errors.add(f'{label} has invalid imported gradient payload: {exc}')
                continue
            if native is not None:
                continue
        attribute_names = {
            name.rsplit('}', 1)[-1]
            for name in gradient.attrib
        }
        if 'href' in attribute_names:
            errors.add(
                f'{label} cannot inherit from href/xlink:href; '
                'define gradient stops directly'
            )
        if 'gradientTransform' in attribute_names:
            errors.add(f'{label} cannot use gradientTransform')
        if 'spreadMethod' in attribute_names:
            errors.add(f'{label} cannot use spreadMethod')
        gradient_units = gradient.get('gradientUnits')
        if gradient_units not in {None, 'objectBoundingBox'}:
            errors.add(
                f'{label} cannot use gradientUnits={gradient_units!r}; '
                'use normalized objectBoundingBox coordinates'
            )

        if tag == 'linearGradient':
            coordinate_defaults = {
                'x1': '0',
                'y1': '0',
                'x2': '1',
                'y2': '0',
            }
            coordinates: dict[str, float] = {}
            for coordinate_name, default in coordinate_defaults.items():
                raw_coordinate = gradient.get(coordinate_name, default)
                try:
                    coordinates[coordinate_name] = (
                        parse_project_linear_gradient_coordinate(raw_coordinate)
                    )
                except ValueError:
                    errors.add(
                        f'{label} {coordinate_name} must be a finite '
                        'objectBoundingBox projection coordinate within '
                        '-0.105..1.105 or -10.5%..110.5%; '
                        f'got {raw_coordinate!r}'
                    )
            if len(coordinates) == 4 and (
                math.isclose(
                    coordinates['x1'],
                    coordinates['x2'],
                    rel_tol=0.0,
                    abs_tol=1e-12,
                )
                and math.isclose(
                    coordinates['y1'],
                    coordinates['y2'],
                    rel_tol=0.0,
                    abs_tol=1e-12,
                )
            ):
                errors.add(
                    f'{label} linear gradient axis must not collapse to one '
                    'point; use different x1/y1 and x2/y2 coordinates'
                )
        else:
            radial_coordinates: dict[str, float] = {}
            for coordinate_name in ('cx', 'cy', 'r', 'fx', 'fy'):
                raw_coordinate = gradient.get(coordinate_name)
                if raw_coordinate is None:
                    continue
                try:
                    coordinate = parse_project_gradient_ratio(raw_coordinate)
                except ValueError:
                    errors.add(
                        f'{label} {coordinate_name} must be a normalized finite '
                        'value from 0 to 1 or 0% to 100%; '
                        f'got {raw_coordinate!r}'
                    )
                    continue
                radial_coordinates[coordinate_name] = coordinate
                if coordinate_name == 'r' and coordinate <= 0:
                    errors.add(f'{label} r must be greater than 0')
            focus_x_name = (
                'fx' if gradient.get('fx') is not None else 'cx'
            )
            focus_y_name = (
                'fy' if gradient.get('fy') is not None else 'cy'
            )
            focus_is_valid = (
                (
                    gradient.get(focus_x_name) is None
                    or focus_x_name in radial_coordinates
                )
                and (
                    gradient.get(focus_y_name) is None
                    or focus_y_name in radial_coordinates
                )
            )
            if focus_is_valid:
                focus_x = radial_coordinates.get(focus_x_name, 0.5)
                focus_y = radial_coordinates.get(focus_y_name, 0.5)
                if not is_project_radial_focus_point(focus_x, focus_y):
                    errors.add(
                        f'{label} effective focus (fx/fy, otherwise cx/cy) '
                        'must lie within the canonical circle centered at '
                        f'0.5,0.5 with radius 0.5; got ({focus_x}, {focus_y})'
                    )

        stops: list[ET.Element] = []
        for child in list(gradient):
            child_tag = _svg_element_tag(child) or str(child.tag)
            if child_tag in PROJECT_NON_VISUAL_DEFINITION_CHILD_TAGS:
                continue
            if child_tag != 'stop':
                errors.add(
                    f'{label} has unsupported direct child <{child_tag}>; '
                    'gradient definitions may contain only direct <stop> children'
                )
                continue
            stops.append(child)
        if len(stops) < 2:
            errors.add(
                f'{label} requires at least two direct <stop> children for '
                'native PPTX gradient interpolation'
            )
        previous_offset: float | None = None
        for index, stop in enumerate(stops, start=1):
            stop_label = f'{label} stop #{index}'
            raw_offset = stop.get('offset')
            try:
                if raw_offset is None:
                    raise ValueError
                offset = parse_project_gradient_ratio(raw_offset)
            except ValueError:
                errors.add(
                    f'{stop_label} offset must be explicit and within 0..1 '
                    f'or 0%..100%; got {raw_offset!r}'
                )
            else:
                if (
                    previous_offset is not None
                    and offset < previous_offset
                ):
                    errors.add(
                        f'{label} stop offsets must be non-decreasing; '
                        f'stop #{index} offset {raw_offset!r} precedes a '
                        f'larger offset at stop #{index - 1}'
                    )
                previous_offset = offset
            style_values = parse_inline_style(stop.get('style'))
            if not (style_values.get('stop-color') or stop.get('stop-color')):
                errors.add(f'{stop_label} requires an explicit stop-color')
    return sorted(errors)
