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


# Responsibility: utils_filters; adapted from the original module.

def parse_project_filter_params(
    filter_elem: ET.Element,
) -> dict[str, float | str | bool]:
    """Extract the shared native shadow/glow parameters from one filter."""
    from .utils_lengths import _f
    from .utils_transforms import _svg_element_tag
    from .utils_paint_values import parse_inline_style
    from .utils_paint_values import parse_opacity
    from .utils_paint_values import parse_svg_color
    primitive_units = filter_elem.get('primitiveUnits')
    if primitive_units not in (None, 'userSpaceOnUse'):
        raise ValueError(
            'filter primitiveUnits must be userSpaceOnUse when explicit; '
            f'got {primitive_units!r}'
        )
    std_dev: float | None = None
    dx = 0.0
    dy = 0.0
    paint_opacity: float | None = None
    transfer_opacity: float | None = None
    color_alpha = 1.0
    color = '000000'
    has_offset = False

    def required_number(primitive: ET.Element, attribute_name: str) -> float:
        primitive_tag = _svg_element_tag(primitive) or str(primitive.tag)
        raw_value = primitive.get(attribute_name)
        if raw_value is None:
            raise ValueError(
                f'<{primitive_tag}> requires explicit {attribute_name}'
            )
        try:
            value = float(raw_value)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f'<{primitive_tag}> {attribute_name} must be a finite number; '
                f'got {raw_value!r}'
            ) from exc
        if not math.isfinite(value):
            raise ValueError(
                f'<{primitive_tag}> {attribute_name} must be a finite number; '
                f'got {raw_value!r}'
            )
        return value

    for child in filter_elem.iter():
        tag = _svg_element_tag(child)
        style_values = parse_inline_style(child.get('style'))

        def effect_attr(name: str, default: str | None = None) -> str | None:
            return style_values.get(name) or child.get(name, default)

        def required_effect_attr(name: str) -> str:
            raw_value = effect_attr(name)
            if raw_value is None:
                raise ValueError(f'<{tag}> requires explicit {name}')
            return raw_value

        if tag == 'feDropShadow':
            std_dev = required_number(child, 'stdDeviation')
            dx = required_number(child, 'dx')
            dy = required_number(child, 'dy')
            if abs(dx) > 0.01 or abs(dy) > 0.01:
                has_offset = True
            paint_opacity = parse_opacity(
                required_effect_attr('flood-opacity'),
                allow_percentage=True,
            )
            parsed_color, parsed_alpha = parse_svg_color(
                effect_attr('flood-color', '#000000')
            )
            if parsed_color:
                color = parsed_color
                color_alpha = parsed_alpha
        elif tag == 'feGaussianBlur':
            if child.get('edgeMode') is not None:
                raise ValueError(
                    '<feGaussianBlur> edgeMode is unsupported by the native '
                    'effect mapping'
                )
            std_dev = required_number(child, 'stdDeviation')
        elif tag == 'feOffset':
            dx = _f(child.get('dx'), 0.0)
            dy = _f(child.get('dy'), 0.0)
            if abs(dx) > 0.01 or abs(dy) > 0.01:
                has_offset = True
        elif tag == 'feFlood':
            paint_opacity = parse_opacity(
                required_effect_attr('flood-opacity'),
                allow_percentage=True,
            )
            parsed_color, parsed_alpha = parse_svg_color(
                effect_attr('flood-color', '#000000')
            )
            if parsed_color:
                color = parsed_color
                color_alpha = parsed_alpha
        elif tag == 'feFuncA' and child.get('type') == 'linear':
            if child.get('intercept') is not None:
                raise ValueError(
                    '<feFuncA> intercept is unsupported; project alpha '
                    'transfer maps slope multiplication only'
                )
            slope = required_number(child, 'slope')
            transfer_opacity = (
                slope
                if transfer_opacity is None
                else transfer_opacity * slope
            )

    if paint_opacity is None:
        opacity = transfer_opacity if transfer_opacity is not None else 0.3
    elif transfer_opacity is None:
        opacity = paint_opacity
    else:
        opacity = paint_opacity * transfer_opacity
    opacity = max(0.0, min(1.0, opacity * color_alpha))

    if std_dev is None:
        raise ValueError('filter requires feDropShadow or feGaussianBlur')

    return {
        'std_dev': std_dev,
        'dx': dx,
        'dy': dy,
        'opacity': opacity,
        'color': color,
        'has_offset': has_offset,
    }


def project_filter_drawingml_coordinates(
    params: dict[str, float | str | bool],
    effect_kind: str | None = None,
) -> dict[str, int]:
    """Map filter geometry into validated DrawingML effect coordinates."""
    from .utils_constants import EMU_PER_PX
    kind = effect_kind or ('shadow' if params['has_offset'] else 'glow')
    std_dev = float(params['std_dev'])
    dx = float(params['dx'])
    dy = float(params['dy'])
    if kind == 'shadow':
        coordinates_px = {
            'blurRad': std_dev * 2.0,
            'dist': math.hypot(dx, dy),
        }
    elif kind == 'glow':
        coordinates_px = {'rad': std_dev}
    else:
        raise ValueError(f'unsupported native filter kind {kind!r}')

    coordinates: dict[str, int] = {}
    for attribute_name, value_px in coordinates_px.items():
        scaled = value_px * EMU_PER_PX
        if not math.isfinite(scaled):
            raise ValueError(
                f'DrawingML {attribute_name} must be finite after EMU mapping'
            )
        mapped = round(scaled)
        if not 0 <= mapped <= OOXML_COORDINATE_MAX:
            raise ValueError(
                f'DrawingML {attribute_name} must map within '
                f'0..{OOXML_COORDINATE_MAX}; got {mapped}'
            )
        coordinates[attribute_name] = mapped
    return coordinates


def project_filter_errors(root: ET.Element) -> list[str]:
    """Validate filters against the native shadow/glow approximation."""
    from .utils_constants import PROJECT_FILTER_EFFECT_PRIMITIVES
    from .utils_constants import PROJECT_FILTER_PRIMITIVES
    from .utils_constants import PROJECT_FILTER_PUBLIC_TARGETS
    from .utils_transforms import _svg_element_tag
    from .utils_transforms import _transform_element_label
    from .utils_paint_values import parse_inline_style
    from .utils_markers import project_definition_index
    definitions, _duplicates = project_definition_index(root)
    filters_by_id = {
        filter_id: elem
        for filter_id, elem in definitions.items()
        if _svg_element_tag(elem) == 'filter'
    }
    errors: set[str] = set()
    parents = {
        child: parent
        for parent in root.iter()
        for child in parent
    }

    for elem in root.iter():
        tag = (_svg_element_tag(elem) or str(elem.tag)).lower()
        label = _transform_element_label(elem)
        style_values = parse_inline_style(elem.get('style'))
        if style_values.get('filter'):
            errors.add(
                f'{label} filter must use a direct filter="url(#id)" '
                'attribute; inline style filters are not supported'
            )

        raw_filter = elem.get('filter')
        if raw_filter is None:
            continue
        if (
            tag not in PROJECT_FILTER_PUBLIC_TARGETS
            and not _is_compact_authored_preset_filter_target(elem)
            and not _is_imported_preset_preview_filter_target(elem, parents)
            and not is_picture_effect_carrier(elem)
        ):
            errors.add(
                f'{label} cannot use filter; supported native targets are '
                'rect, circle, image, path, polygon, text, a validated compact authored-'
                'preset shape, and an exact registered carrier group'
            )
        if tag == 'image' and elem.get('clip-path') is not None:
            errors.add(
                f'{label} cannot combine filter and clip-path on the same '
                'image; put the filter on an exact single-image outer <g>'
            )
        match = re.fullmatch(r'url\(#([^)]+)\)', raw_filter.strip())
        if match is None:
            errors.add(
                f'{label} filter must be an exact local url(#id) reference; '
                f'got {raw_filter!r}'
            )
            continue
        filter_id = match.group(1)
        if filter_id not in filters_by_id:
            errors.add(
                f'{label} filter=url(#{filter_id}) has no matching direct '
                f'<defs><filter id="{filter_id}"> definition'
            )

    for filter_id, filter_elem in filters_by_id.items():
        label = f'filter #{filter_id}'
        parameters_are_valid = True
        primitive_units = filter_elem.get('primitiveUnits')
        if primitive_units not in (None, 'userSpaceOnUse'):
            parameters_are_valid = False
            errors.add(
                f'{label} primitiveUnits must be userSpaceOnUse when '
                f'explicit; got {primitive_units!r}'
            )
        primitives = [
            _svg_element_tag(descendant) or str(descendant.tag)
            for descendant in filter_elem.iter()
            if descendant is not filter_elem
        ]
        unsupported = sorted(set(primitives) - PROJECT_FILTER_PRIMITIVES)
        if unsupported:
            errors.add(
                f'{label} uses unsupported filter primitive(s): '
                f'{", ".join(unsupported)}'
            )
        effect_primitives = [
            primitive
            for primitive in primitives
            if primitive in PROJECT_FILTER_EFFECT_PRIMITIVES
        ]
        if not effect_primitives:
            errors.add(f'{label} must contain feDropShadow or feGaussianBlur')
        elif len(effect_primitives) > 1:
            errors.add(
                f'{label} contains multiple shadow/glow primitives; one '
                'filter must map to exactly one native effect'
            )
        if any(
            _svg_element_tag(descendant) == 'feFuncA'
            and descendant.get('type') != 'linear'
            for descendant in filter_elem.iter()
        ):
            errors.add(f'{label} requires feFuncA type="linear"')

        for primitive in filter_elem.iter():
            primitive_tag = _svg_element_tag(primitive)
            if primitive_tag in {'feDropShadow', 'feFlood'}:
                style_values = parse_inline_style(primitive.get('style'))
                if (
                    primitive.get('flood-opacity') is None
                    and 'flood-opacity' not in style_values
                ):
                    parameters_are_valid = False
                    errors.add(
                        f'{label} <{primitive_tag}> requires explicit '
                        'flood-opacity'
                    )
            if (
                primitive_tag == 'feFuncA'
                and primitive.get('intercept') is not None
            ):
                parameters_are_valid = False
                errors.add(
                    f'{label} <feFuncA> intercept is unsupported; project '
                    'alpha transfer maps slope multiplication only'
                )
            if (
                primitive_tag == 'feGaussianBlur'
                and primitive.get('edgeMode') is not None
            ):
                parameters_are_valid = False
                errors.add(
                    f'{label} <feGaussianBlur> edgeMode is unsupported by '
                    'the native effect mapping'
                )
            numeric_attrs: tuple[tuple[str, bool, bool], ...] = ()
            if primitive_tag in {'feDropShadow', 'feGaussianBlur'}:
                numeric_attrs = (('stdDeviation', True, True),)
            elif primitive_tag == 'feOffset':
                numeric_attrs = (
                    ('dx', False, False),
                    ('dy', False, False),
                )
            elif primitive_tag == 'feFuncA':
                numeric_attrs = (('slope', True, True),)
            if primitive_tag == 'feDropShadow':
                numeric_attrs += (
                    ('dx', False, True),
                    ('dy', False, True),
                )
            for attribute_name, non_negative, required in numeric_attrs:
                raw_value = primitive.get(attribute_name)
                if raw_value is None:
                    if required:
                        parameters_are_valid = False
                        errors.add(
                            f'{label} <{primitive_tag}> requires explicit '
                            f'{attribute_name}'
                        )
                    continue
                try:
                    value = float(raw_value)
                except (TypeError, ValueError):
                    value = math.nan
                if (
                    not math.isfinite(value)
                    or (non_negative and value < 0)
                    or (
                        primitive_tag == 'feFuncA'
                        and attribute_name == 'slope'
                        and value > 1
                    )
                ):
                    if attribute_name in {'stdDeviation', 'dx', 'dy'}:
                        parameters_are_valid = False
                    qualifier = (
                        ' from 0 to 1'
                        if primitive_tag == 'feFuncA'
                        else ''
                    )
                    errors.add(
                        f'{label} <{primitive_tag}> {attribute_name} must be a '
                        f'finite number{qualifier}; got {raw_value!r}'
                    )
        if len(effect_primitives) == 1 and parameters_are_valid:
            try:
                params = parse_project_filter_params(filter_elem)
                project_filter_drawingml_coordinates(params)
            except (TypeError, ValueError) as exc:
                errors.add(f'{label} {exc}')
    return sorted(errors)


def _is_compact_authored_preset_filter_target(elem: ET.Element) -> bool:
    """Recognize one validated project-authored preset shape filter target."""
    from .utils_transforms import _svg_element_tag
    if (
        _svg_element_tag(elem) != 'g'
        or elem.get('data-pptx-authoring') != 'preset'
        or elem.get('data-pptx-object') != 'shape'
        or elem.get('data-pptx-part') is not None
    ):
        return False
    from pptx_to_svg.preset_authoring import (  # Local to avoid layer coupling.
        authored_preset_encoding,
        validate_authored_preset_group,
    )

    return (
        authored_preset_encoding(elem) == 'compact'
        and not validate_authored_preset_group(elem)
    )


def _is_imported_preset_preview_filter_target(
    elem: ET.Element,
    parents: dict[ET.Element, ET.Element],
) -> bool:
    """Recognize the render-only aggregate filter on an imported preset.

    DrawingML presets can contain several visible path layers but own one
    shape-level effect.  The lossless importer therefore keeps the native
    filter on the hidden geometry carrier and mirrors the same reference onto
    its hash-locked preview group.  The preview group is never exported as a
    separate PowerPoint object; other ordinary or authored ``<g filter>``
    forms remain outside the project contract.
    """
    from .utils_transforms import _svg_element_tag
    if (
        _svg_element_tag(elem) != 'g'
        or elem.get('data-pptx-part') != 'geometry-preview'
    ):
        return False
    parent = parents.get(elem)
    if (
        parent is None
        or _svg_element_tag(parent) != 'g'
        or parent.get('data-pptx-object') not in {'shape', 'connector'}
        or not parent.get('data-pptx-prst')
        or not parent.get('data-pptx-frame')
    ):
        return False
    previews = [
        child
        for child in parent
        if child.get('data-pptx-part') == 'geometry-preview'
    ]
    if len(previews) != 1 or previews[0] is not elem:
        return False
    preview_children = list(elem)
    if not preview_children or any(
        _svg_element_tag(child) != 'path'
        or child.get('data-pptx-part') != 'geometry-detail'
        or len(child) != 0
        for child in preview_children
    ):
        return False
    carriers = [
        child
        for child in parent
        if child.get('data-pptx-part') == 'geometry'
    ]
    if len(carriers) != 1:
        return False
    carrier = carriers[0]
    if not (
        _svg_element_tag(carrier) == 'path'
        and carrier.get('visibility') == 'hidden'
        and carrier.get('pointer-events') == 'none'
        and carrier.get('data-pptx-object') == parent.get('data-pptx-object')
        and carrier.get('data-pptx-prst') == parent.get('data-pptx-prst')
        and carrier.get('data-pptx-frame') == parent.get('data-pptx-frame')
        and carrier.get('filter') == elem.get('filter')
    ):
        return False
    try:
        expected_hash = resolve_preset_preview_hash(parent)
    except ValueError:
        return False
    return (
        expected_hash is not None
        and svg_preset_preview_fingerprint(parent) == expected_hash
    )


def is_picture_effect_carrier(elem: ET.Element) -> bool:
    """Recognize one effect carrier around exactly one clipped picture."""
    from .utils_constants import PROJECT_NON_VISUAL_DEFINITION_CHILD_TAGS
    from .utils_transforms import _svg_element_tag
    from .utils_fonts import resolve_url_id
    if (
        _svg_element_tag(elem) != 'g'
        or elem.get('data-pptx-object') == 'group'
        or re.fullmatch(
            r'url\(#([^)]+)\)',
            (elem.get('filter') or '').strip(),
        ) is None
        or elem.get('data-pptx-layer') not in {None, 'master', 'layout'}
        or any(
            elem.get(attribute) is not None
            for attribute in (
                'data-pptx-placeholder',
                'data-pptx-binding',
                'data-pptx-replace-with',
                'data-pptx-native',
            )
        )
    ):
        return False
    children = [
        child for child in elem
        if _svg_element_tag(child) not in PROJECT_NON_VISUAL_DEFINITION_CHILD_TAGS
    ]
    if len(children) != 1:
        return False
    picture = children[0]
    if picture.get('filter') is not None:
        return False
    owner_kind = elem.get('data-pptx-object')
    if _svg_element_tag(picture) == 'image':
        if resolve_url_id(picture.get('clip-path', '')) is None:
            return False
        if owner_kind is None:
            return True
        shape_id = elem.get('data-pptx-shape-id')
        return (
            owner_kind == 'picture'
            and shape_id is not None
            and picture.get('data-pptx-object') == 'picture'
            and picture.get('data-pptx-shape-id') == shape_id
        )
    if (
        _svg_element_tag(picture) != 'svg'
        or owner_kind != 'picture'
        or picture.get('data-pptx-object') != 'picture'
        or picture.get('viewBox') is None
        or picture.get('preserveAspectRatio') != 'none'
    ):
        return False
    shape_id = elem.get('data-pptx-shape-id')
    if (
        shape_id is None
        or picture.get('data-pptx-shape-id') != shape_id
    ):
        return False
    crop_children = list(picture)
    return (
        len(crop_children) == 1
        and _svg_element_tag(crop_children[0]) == 'image'
    )
