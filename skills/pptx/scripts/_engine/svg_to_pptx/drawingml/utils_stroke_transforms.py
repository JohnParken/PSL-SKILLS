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


# Responsibility: utils_stroke_transforms; adapted from the original module.

_PROJECT_STROKE_STYLE_ATTRIBUTES = (
    'stroke-dasharray',
    'stroke-dashoffset',
    'stroke-linecap',
    'stroke-linejoin',
    'vector-effect',
)


def iter_project_stroke_styles(
    root: ET.Element,
) -> Iterator[tuple[ET.Element, str, str, str]]:
    """Yield project line-style values with their declaration source."""
    from .utils_paint_values import parse_inline_style
    for elem in root.iter():
        for attribute in _PROJECT_STROKE_STYLE_ATTRIBUTES:
            raw = elem.get(attribute)
            if raw is not None:
                yield elem, attribute, raw, 'attribute'
        inline_style = parse_inline_style(elem.get('style'))
        for attribute in _PROJECT_STROKE_STYLE_ATTRIBUTES:
            raw = inline_style.get(attribute)
            if raw is not None:
                yield elem, attribute, raw, 'inline style'


def project_stroke_style_errors(root: ET.Element) -> list[str]:
    """Return blocking line-style grammar and mapping errors for preflight."""
    from .utils_transforms import _project_thick_circle_ids
    from .utils_transforms import _transform_element_label
    from .utils_lengths import parse_project_geometry_length
    from .utils_lengths import parse_project_stroke_dasharray
    from .utils_lengths import parse_project_stroke_enum
    thick_circle_ids = _project_thick_circle_ids(root)
    errors: set[str] = set()
    for elem, attribute, raw, source in iter_project_stroke_styles(root):
        label = _transform_element_label(elem)
        try:
            if attribute == 'stroke-dasharray':
                parse_project_stroke_dasharray(
                    raw,
                    allow_zero_gap=id(elem) in thick_circle_ids,
                )
            elif attribute == 'stroke-dashoffset':
                if source != 'attribute':
                    raise ValueError(
                        'is supported only as a direct attribute on a '
                        'thick-circle arc'
                    )
                parse_project_geometry_length(raw, attribute)
                if id(elem) not in thick_circle_ids:
                    raise ValueError(
                        'is supported only on a circle that satisfies the '
                        'thick-circle arc contract'
                    )
            else:
                parse_project_stroke_enum(attribute, raw)
        except ValueError as exc:
            errors.add(f'{label} {source} {attribute}={raw!r}: {exc}')
    return sorted(errors)


def _contains_thick_circle(elem: ET.Element, thick_circle_ids: set[int]) -> bool:
    from .utils_transforms import _iter_visual_transform_tree
    return any(
        id(descendant) in thick_circle_ids
        for descendant in _iter_visual_transform_tree(elem)
    )


def _is_unit_axis_reflection(
    operations: tuple[tuple[str, tuple[float, ...]], ...],
) -> bool:
    """Return whether a transform is translation plus an unscaled axis flip."""
    from .utils_transforms import _transform_operations_matrix
    has_explicit_flip = any(
        (
            name == 'scale'
            and (
                args[0] < 0
                or (len(args) > 1 and args[1] < 0)
            )
        )
        or (
            name == 'matrix'
            and (args[0] < 0 or args[3] < 0)
        )
        for name, args in operations
    )
    if not has_explicit_flip:
        return False
    matrix = _transform_operations_matrix(operations)
    a, b, c, d, _e, _f = matrix
    return (
        abs(b) <= 1e-9
        and abs(c) <= 1e-9
        and math.isclose(abs(a), 1.0, abs_tol=1e-9)
        and math.isclose(abs(d), 1.0, abs_tol=1e-9)
        and (a < 0 or d < 0)
    )


def _transform_semantic_error(
    elem: ET.Element,
    operations: tuple[tuple[str, tuple[float, ...]], ...],
    *,
    is_root: bool,
    thick_circle_ids: set[int],
) -> str | None:
    from .utils_transforms import _FULL_TRANSFORM_TAGS
    from .utils_transforms import _TRANSFORM_CONTAINER_TAGS
    from .utils_transforms import _contains_native_marker
    from .utils_transforms import _contains_rounded_rect
    from .utils_transforms import _has_positive_rounding
    from .utils_transforms import _svg_element_tag
    from .utils_transforms import _transform_element_label
    from .utils_transforms import supports_full_project_transform
    tag = _svg_element_tag(elem)
    names = tuple(name for name, _args in operations)
    label = _transform_element_label(elem)

    if is_root:
        return (
            'Root <svg> transform is unsupported; apply transforms to child '
            'elements or groups'
        )

    if tag == 'text':
        if all(name == 'translate' for name in names):
            return None
        if len(names) == 1 and names[0] == 'rotate':
            return None
        return (
            f'{label} text transform must be a translate-only list or one '
            'rotate operation; text scale, matrix, and mixed operations are '
            'not mapped'
        )

    if tag in _TRANSFORM_CONTAINER_TAGS:
        if _contains_native_marker(elem):
            if all(name in {'translate', 'scale'} for name in names):
                return None
            return (
                f'{label} native replacement marker transforms support only '
                'translate and scale'
            )
        if _contains_thick_circle(elem, thick_circle_ids):
            if all(name == 'translate' for name in names):
                return None
            return (
                f'{label} contains a thick-circle arc shorthand; ancestor '
                'transforms must be translate-only'
            )
        if _is_unit_axis_reflection(operations):
            # Imported PowerPoint groups encode flipH/flipV as a translate /
            # unit-scale / translate list. The converter distributes that
            # signed unit scale to child geometry and text positions without
            # scaling font metrics, so this exact no-shear case is lossless.
            return None
        if supports_full_project_transform(elem):
            if 'matrix' in names and _contains_rounded_rect(elem):
                return (
                    f'{label} matrix transform cannot target a rounded '
                    'rectangle subtree'
                )
            return None
        if all(name == 'translate' for name in names):
            return None
        if len(names) == 1 and names[0] == 'rotate':
            return None
        return (
            f'{label} contains text or another non-matrix visual; its transform '
            'must be a translate-only list or one rotate operation'
        )

    if tag in _FULL_TRANSFORM_TAGS:
        if id(elem) in thick_circle_ids:
            if len(names) == 1 and names[0] == 'rotate':
                return None
            return (
                f'{label} thick-circle arc transform must be one rotate '
                'operation'
            )
        if tag == 'rect' and 'matrix' in names and _has_positive_rounding(elem):
            return f'{label} rounded rectangles cannot use matrix transforms'
        return None

    if tag == 'svg' and supports_full_project_transform(elem):
        return None

    return f'{label} has no registered project transform mapping'


def project_transform_errors(root: ET.Element) -> list[str]:
    """Return blocking transform grammar and mapping errors for preflight."""
    from .utils_transforms import _TRANSFORM_DEFINITION_BOUNDARIES
    from .utils_transforms import _project_thick_circle_ids
    from .utils_transforms import _svg_element_tag
    from .utils_transforms import _transform_element_label
    from .utils_transforms import _transform_operations_matrix
    from .utils_transforms import iter_project_transforms
    from .utils_transforms import matrix_multiply
    from .utils_transforms import parse_transform_operations
    from .utils_transforms import validate_dml_shape_matrix
    parent_by_id = {
        id(child): parent
        for parent in root.iter()
        for child in list(parent)
    }
    thick_circle_ids = _project_thick_circle_ids(root)
    parsed: dict[int, tuple[AffineMatrix, bool]] = {}
    errors: set[str] = set()

    for elem, raw in iter_project_transforms(root):
        label = _transform_element_label(elem)
        restricted_ancestor = None
        current = parent_by_id.get(id(elem))
        while current is not None:
            current_tag = _svg_element_tag(current)
            if current_tag in _TRANSFORM_DEFINITION_BOUNDARIES:
                restricted_ancestor = current_tag
                break
            current = parent_by_id.get(id(current))
        if restricted_ancestor is not None:
            errors.add(
                f'{label} cannot use transform inside <{restricted_ancestor}>'
            )
            parsed[id(elem)] = (IDENTITY_MATRIX, False)
            continue

        try:
            operations = parse_transform_operations(raw)
            if not operations:
                raise ValueError('SVG transform must not be empty')
            matrix = _transform_operations_matrix(operations)
        except ValueError as exc:
            errors.add(f'{label} transform={raw!r}: {exc}')
            parsed[id(elem)] = (IDENTITY_MATRIX, False)
            continue

        semantic_error = _transform_semantic_error(
            elem,
            operations,
            is_root=elem is root,
            thick_circle_ids=thick_circle_ids,
        )
        if semantic_error is not None:
            errors.add(semantic_error)
        parsed[id(elem)] = (matrix, semantic_error is None)

    def validate_branch(elem: ET.Element, parent_matrix: AffineMatrix) -> None:
        current_matrix = parent_matrix
        entry = parsed.get(id(elem))
        if entry is not None:
            local_matrix, semantic_ok = entry
            if not semantic_ok:
                return
            current_matrix = matrix_multiply(parent_matrix, local_matrix)
            try:
                validate_dml_shape_matrix(current_matrix)
            except ValueError as exc:
                errors.add(
                    f'{_transform_element_label(elem)} has an unsupported '
                    f'cumulative transform: {exc}'
                )
                return
        for child in elem:
            validate_branch(child, current_matrix)

    validate_branch(root, IDENTITY_MATRIX)
    return sorted(errors)


def rect_to_dml_xfrm(
    x: float,
    y: float,
    w: float,
    h: float,
    matrix: AffineMatrix,
    *,
    preserve_degenerate_axes: bool = False,
) -> tuple[str, int, int, int, int, tuple[int, int, int, int]]:
    """Map a transformed SVG rectangle to DrawingML xfrm attributes.

    DrawingML can represent rotated/flipped rectangles, but not arbitrary
    shear. Template-import picture wrappers only use translate/rotate/scale,
    so decomposing the transformed local X/Y axes is sufficient here.
    """
    from .utils_constants import ANGLE_UNIT
    from .utils_lengths import px_to_emu
    from .utils_transforms import transform_point
    from .utils_transforms import validate_dml_shape_matrix
    p0 = transform_point(matrix, x, y)
    p1 = transform_point(matrix, x + w, y)
    p2 = transform_point(matrix, x + w, y + h)
    p3 = transform_point(matrix, x, y + h)

    ux = p1[0] - p0[0]
    uy = p1[1] - p0[1]
    vx = p3[0] - p0[0]
    vy = p3[1] - p0[1]

    rect_w = math.hypot(ux, uy)
    rect_h = math.hypot(vx, vy)
    validate_dml_shape_matrix(matrix)
    if not preserve_degenerate_axes:
        rect_w = max(rect_w, 0.001)
        rect_h = max(rect_h, 0.001)
    cross = ux * vy - uy * vx

    if rect_w <= 1e-12 and rect_h > 1e-12:
        angle_deg = math.degrees(math.atan2(vy, vx)) - 90.0
        flip_attr = ''
    elif cross < 0:
        angle_deg = math.degrees(math.atan2(-uy, -ux))
        flip_attr = ' flipH="1"'
    else:
        angle_deg = math.degrees(math.atan2(uy, ux))
        flip_attr = ''

    rot = round(angle_deg * ANGLE_UNIT)
    rot_attr = f' rot="{rot}"' if rot else ''

    center_x = (p0[0] + p2[0]) / 2
    center_y = (p0[1] + p2[1]) / 2
    off_x = px_to_emu(center_x - rect_w / 2)
    off_y = px_to_emu(center_y - rect_h / 2)
    ext_cx = px_to_emu(rect_w)
    ext_cy = px_to_emu(rect_h)
    validate_ooxml_xfrm(off_x, off_y, ext_cx, ext_cy)

    xs = [p0[0], p1[0], p2[0], p3[0]]
    ys = [p0[1], p1[1], p2[1], p3[1]]
    bounds = (
        px_to_emu(min(xs)),
        px_to_emu(min(ys)),
        px_to_emu(max(xs)),
        px_to_emu(max(ys)),
    )

    return f'{flip_attr}{rot_attr}', off_x, off_y, ext_cx, ext_cy, bounds


def _extract_inheritable_styles(elem: ET.Element) -> dict[str, str]:
    """Extract all SVG-inheritable presentation attributes from an element."""
    from .utils_constants import INHERITABLE_ATTRS
    from .utils_paint_values import parse_inline_style
    styles: dict[str, str] = {}
    for attr in INHERITABLE_ATTRS:
        val = elem.get(attr)
        if val is not None:
            styles[attr] = val
    styles.update({
        attr: val
        for attr, val in parse_inline_style(elem.get('style')).items()
        if attr in INHERITABLE_ATTRS
    })
    return styles


def _get_attr(elem: ET.Element, attr: str, ctx: ConvertContext) -> str | None:
    """Get effective attribute: element's own value first, then inherited."""
    from .utils_paint_values import parse_inline_style
    style_val = parse_inline_style(elem.get('style')).get(attr)
    if style_val is not None:
        return style_val
    val = elem.get(attr)
    if val is not None:
        return val
    return ctx.inherited_styles.get(attr)


def ctx_x(val: float, ctx: ConvertContext) -> float:
    """Apply context scale + translate to an X coordinate."""
    return val * ctx.scale_x + ctx.translate_x


def ctx_y(val: float, ctx: ConvertContext) -> float:
    """Apply context scale + translate to a Y coordinate."""
    return val * ctx.scale_y + ctx.translate_y


def ctx_w(val: float, ctx: ConvertContext) -> float:
    """Apply context scale to a width value."""
    return val * ctx.scale_x


def ctx_h(val: float, ctx: ConvertContext) -> float:
    """Apply context scale to a height value."""
    return val * ctx.scale_y


# ---------------------------------------------------------------------------
# Color / style parsing
# ---------------------------------------------------------------------------
