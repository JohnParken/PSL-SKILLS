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


# Responsibility: utils_transforms; adapted from the original module.

_TRANSFORM_NUMBER_PATTERN = (
    r'[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?'
)
_TRANSFORM_NUMBER_RE = re.compile(_TRANSFORM_NUMBER_PATTERN)
_CANONICAL_TRANSFORM_NUMBER_RE = re.compile(
    r'-?(?:\d+(?:\.\d+)?|\.\d+)$'
)
_TRANSFORM_OPERATION_RE = re.compile(r'([A-Za-z]+)\(([^()]*)\)')
_TRANSFORM_WHITESPACE_RE = re.compile(r'[ \t\r\n]*')
_TRANSFORM_SEPARATOR_PATTERN = (
    r'(?:[ \t\r\n]+|[ \t\r\n]*,[ \t\r\n]*)'
)
_TRANSFORM_SEPARATOR_RE = re.compile(_TRANSFORM_SEPARATOR_PATTERN)
_TRANSFORM_ARGUMENTS_RE = re.compile(
    rf'[ \t\r\n]*{_TRANSFORM_NUMBER_PATTERN}'
    rf'(?:{_TRANSFORM_SEPARATOR_PATTERN}{_TRANSFORM_NUMBER_PATTERN})*'
    r'[ \t\r\n]*'
)
_TRANSFORM_ARITIES = {
    'matrix': frozenset({6}),
    'translate': frozenset({1, 2}),
    'scale': frozenset({1, 2}),
    'rotate': frozenset({1, 3}),
}
_FULL_TRANSFORM_TAGS = frozenset({
    'rect', 'circle', 'ellipse', 'line', 'path', 'polygon', 'polyline',
    'image',
})
_TRANSFORM_CONTAINER_TAGS = frozenset({'g', 'use'})
_TRANSFORM_DEFINITION_BOUNDARIES = frozenset({'clipPath', 'marker', 'pattern'})
_NON_VISUAL_TRANSFORM_CHILD_TAGS = frozenset({
    'defs', 'title', 'desc', 'metadata', 'style',
})


def matrix_multiply(left: AffineMatrix, right: AffineMatrix) -> AffineMatrix:
    """Compose two SVG affine matrices, applying ``right`` before ``left``."""
    a1, b1, c1, d1, e1, f1 = left
    a2, b2, c2, d2, e2, f2 = right
    return (
        a1 * a2 + c1 * b2,
        b1 * a2 + d1 * b2,
        a1 * c2 + c1 * d2,
        b1 * c2 + d1 * d2,
        a1 * e2 + c1 * f2 + e1,
        b1 * e2 + d1 * f2 + f1,
    )


def _translate_matrix(tx: float, ty: float = 0.0) -> AffineMatrix:
    return (1.0, 0.0, 0.0, 1.0, tx, ty)


def _scale_matrix(sx: float, sy: float | None = None) -> AffineMatrix:
    return (sx, 0.0, 0.0, sx if sy is None else sy, 0.0, 0.0)


def _rotate_matrix(angle_deg: float, cx: float | None = None, cy: float | None = None) -> AffineMatrix:
    rad = math.radians(angle_deg)
    cos_a = math.cos(rad)
    sin_a = math.sin(rad)
    rot = (cos_a, sin_a, -sin_a, cos_a, 0.0, 0.0)
    if cx is None or cy is None:
        return rot
    return matrix_multiply(
        matrix_multiply(_translate_matrix(cx, cy), rot),
        _translate_matrix(-cx, -cy),
    )


def _parse_transform_operations(
    transform_str: str,
) -> tuple[
    tuple[tuple[str, tuple[float, ...]], ...],
    tuple[str, ...],
]:
    """Parse a complete project transform list and retain numeric tokens."""
    if not transform_str:
        return (), ()

    operations: list[tuple[str, tuple[float, ...]]] = []
    number_tokens: list[str] = []
    cursor = 0
    matches = list(_TRANSFORM_OPERATION_RE.finditer(transform_str))
    if not matches:
        if _TRANSFORM_WHITESPACE_RE.fullmatch(transform_str):
            raise ValueError('SVG transform must not be empty')
        raise ValueError(f'Invalid SVG transform syntax {transform_str!r}')

    for index, match in enumerate(matches):
        gap = transform_str[cursor:match.start()]
        gap_pattern = (
            _TRANSFORM_WHITESPACE_RE
            if index == 0 else _TRANSFORM_SEPARATOR_RE
        )
        if gap_pattern.fullmatch(gap) is None:
            if index > 0 and not gap:
                raise ValueError(
                    f'SVG transform operations require a separator at '
                    f'offset {cursor}'
                )
            raise ValueError(
                f'Invalid SVG transform syntax at offset {cursor}: '
                f'{gap!r}'
            )
        name, raw_args = match.groups()
        if name not in _TRANSFORM_ARITIES:
            raise ValueError(
                f'Unsupported SVG transform operation {name!r}; use lowercase '
                'matrix, translate, scale, or rotate'
            )
        if raw_args.strip() and _TRANSFORM_ARGUMENTS_RE.fullmatch(raw_args) is None:
            raise ValueError(
                f'Invalid arguments for SVG transform {name!r}: {raw_args!r}'
            )
        tokens = tuple(_TRANSFORM_NUMBER_RE.findall(raw_args))
        values = tuple(float(token) for token in tokens)
        if not all(math.isfinite(value) for value in values):
            raise ValueError(f'Non-finite arguments for SVG transform {name!r}')
        if len(values) not in _TRANSFORM_ARITIES[name]:
            expected = '/'.join(str(value) for value in sorted(_TRANSFORM_ARITIES[name]))
            raise ValueError(
                f'SVG transform {name!r} has {len(values)} argument(s); '
                f'expected {expected}'
            )
        operations.append((name, values))
        number_tokens.extend(tokens)
        cursor = match.end()

    trailing = transform_str[cursor:]
    if _TRANSFORM_WHITESPACE_RE.fullmatch(trailing) is None:
        raise ValueError(
            f'Invalid SVG transform trailing syntax at offset {cursor}: '
            f'{trailing!r}'
        )

    return tuple(operations), tuple(number_tokens)


def parse_transform_operations(
    transform_str: str,
) -> tuple[tuple[str, tuple[float, ...]], ...]:
    """Parse one complete supported SVG transform list."""
    operations, _ = _parse_transform_operations(transform_str)
    return operations


def noncanonical_transform_numbers(transform_str: str) -> tuple[str, ...]:
    """Return compatible transform numbers generated SVG should normalize."""
    _, tokens = _parse_transform_operations(transform_str)
    return tuple(
        token
        for token in tokens
        if _CANONICAL_TRANSFORM_NUMBER_RE.fullmatch(token) is None
    )


def _transform_operations_matrix(
    operations: tuple[tuple[str, tuple[float, ...]], ...],
) -> AffineMatrix:
    matrix = IDENTITY_MATRIX
    for name, args in operations:
        if name == 'matrix':
            local = (args[0], args[1], args[2], args[3], args[4], args[5])
        elif name == 'translate':
            local = _translate_matrix(
                args[0],
                args[1] if len(args) > 1 else 0.0,
            )
        elif name == 'scale':
            local = _scale_matrix(
                args[0],
                args[1] if len(args) > 1 else None,
            )
        else:
            local = _rotate_matrix(
                args[0],
                args[1] if len(args) > 2 else None,
                args[2] if len(args) > 2 else None,
            )
        matrix = matrix_multiply(matrix, local)
    return matrix


def parse_transform_matrix(transform_str: str) -> AffineMatrix:
    """Parse a complete SVG transform list into one affine matrix.

    Unsupported or malformed operations fail closed.  Treating an unknown
    operation as the identity would silently discard a visible SVG edit.
    """
    if not transform_str:
        return IDENTITY_MATRIX
    return _transform_operations_matrix(parse_transform_operations(transform_str))


def transform_point(matrix: AffineMatrix, x: float, y: float) -> tuple[float, float]:
    """Apply an SVG affine matrix to a point."""
    a, b, c, d, e, f = matrix
    return a * x + c * y + e, b * x + d * y + f


def validate_dml_shape_matrix(matrix: AffineMatrix) -> None:
    """Reject affine shear that a DrawingML shape transform cannot express."""
    if not all(math.isfinite(value) for value in matrix):
        raise ValueError('SVG transform produces non-finite matrix values')
    a, b, c, d, _e, _f = matrix
    x_length = math.hypot(a, b)
    y_length = math.hypot(c, d)
    if not math.isfinite(x_length) or not math.isfinite(y_length):
        raise ValueError('SVG transform produces non-finite axis lengths')
    if x_length <= 1e-12 or y_length <= 1e-12:
        raise ValueError(
            'SVG zero-scale transform cannot be represented by a visible '
            'DrawingML shape'
        )
    normalized_dot = (
        (a / x_length) * (c / y_length)
        + (b / x_length) * (d / y_length)
    )
    if not math.isfinite(normalized_dot) or abs(normalized_dot) > 1e-9:
        raise ValueError(
            'SVG shear/skew cannot be represented by a DrawingML '
            'shape transform'
        )


def _svg_element_tag(elem: ET.Element) -> str | None:
    from .utils_constants import SVG_NS
    raw_tag = str(elem.tag)
    if raw_tag.startswith('{'):
        namespace, tag = raw_tag[1:].split('}', 1)
        return tag if namespace == SVG_NS else None
    return raw_tag


def _transform_element_label(elem: ET.Element) -> str:
    tag = _svg_element_tag(elem) or str(elem.tag)
    elem_id = elem.get('id')
    return f'<{tag} id={elem_id!r}>' if elem_id else f'<{tag}>'


def _visual_transform_children(elem: ET.Element) -> list[ET.Element]:
    return [
        child
        for child in elem
        if _svg_element_tag(child) not in _NON_VISUAL_TRANSFORM_CHILD_TAGS
    ]


def _iter_visual_transform_tree(elem: ET.Element) -> Iterator[ET.Element]:
    """Yield one rendered subtree while excluding definition/metadata branches."""
    yield elem
    for child in _visual_transform_children(elem):
        yield from _iter_visual_transform_tree(child)


_TRANSFORM_ARC_STYLE_ATTRS = (
    'fill',
    'stroke',
    'stroke-width',
    'stroke-dasharray',
)


def _transform_arc_styles(
    elem: ET.Element,
    inherited: dict[str, str] | None = None,
) -> dict[str, str]:
    from .utils_paint_values import parse_inline_style
    values = dict(inherited or {})
    inline_style = parse_inline_style(elem.get('style'))
    for name in _TRANSFORM_ARC_STYLE_ATTRS:
        direct = elem.get(name)
        if direct is not None:
            values[name] = direct
        if name in inline_style:
            values[name] = inline_style[name]
    return values


def _is_project_thick_circle(
    elem: ET.Element,
    arc_styles: dict[str, str],
) -> bool:
    from .utils_lengths import is_thick_circle_shorthand
    from .utils_lengths import parse_project_geometry_length
    if _svg_element_tag(elem) != 'circle':
        return False
    try:
        radius = parse_project_geometry_length(elem.get('r') or '0', 'r')
        stroke_width = parse_project_geometry_length(
            arc_styles.get('stroke-width', '0'),
            'stroke-width',
        )
    except ValueError:
        # Geometry preflight owns malformed length diagnostics.
        return False
    return is_thick_circle_shorthand(
        arc_styles.get('stroke-dasharray'),
        arc_styles.get('stroke'),
        arc_styles.get('fill'),
        stroke_width,
        radius,
    )


def supports_full_project_transform(
    elem: ET.Element,
    inherited_arc_styles: dict[str, str] | None = None,
) -> bool:
    """Return whether one subtree can consume an affine matrix without text loss."""
    tag = _svg_element_tag(elem)
    arc_styles = _transform_arc_styles(elem, inherited_arc_styles)
    if _is_project_thick_circle(elem, arc_styles):
        # Thick-circle arcs consume scalar context plus one local rotation;
        # treating an ancestor as a full matrix would silently drop it.
        return False
    if tag in _FULL_TRANSFORM_TAGS:
        return True
    if tag == 'use':
        # Local/data-icon use references are validated again after expansion.
        return True
    if tag == 'svg':
        children = _visual_transform_children(elem)
        return len(children) == 1 and _svg_element_tag(children[0]) == 'image'
    if tag == 'g':
        children = _visual_transform_children(elem)
        return bool(children) and all(
            supports_full_project_transform(child, arc_styles)
            for child in children
        )
    return False


def iter_project_transforms(
    root: ET.Element,
) -> Iterator[tuple[ET.Element, str]]:
    """Yield explicit SVG transform attributes from the project surface."""
    for elem in root.iter():
        if _svg_element_tag(elem) is None:
            continue
        raw = elem.get('transform')
        if raw is not None:
            yield elem, raw


def _has_positive_rounding(elem: ET.Element) -> bool:
    from .utils_lengths import parse_project_geometry_length
    for attr in ('rx', 'ry'):
        raw = elem.get(attr)
        if raw is None:
            continue
        try:
            if parse_project_geometry_length(raw, attr) > 0:
                return True
        except ValueError:
            # Geometry preflight owns the malformed length diagnostic.
            continue
    return False


def _contains_rounded_rect(elem: ET.Element) -> bool:
    return any(
        _svg_element_tag(descendant) == 'rect'
        and _has_positive_rounding(descendant)
        for descendant in _iter_visual_transform_tree(elem)
    )


def _contains_native_marker(elem: ET.Element) -> bool:
    # Import lazily to avoid the native-object package's dependency on this
    # shared DrawingML utility module during initialization.
    from ..native_objects.marker_attributes import native_replacement_kind

    return any(
        native_replacement_kind(descendant) in {'table', 'chart', 'formula'}
        for descendant in _iter_visual_transform_tree(elem)
    )


def _project_thick_circle_ids(
    root: ET.Element,
) -> set[int]:
    thick_circle_ids: set[int] = set()

    def visit(
        elem: ET.Element,
        inherited: dict[str, str] | None = None,
    ) -> None:
        arc_styles = _transform_arc_styles(elem, inherited)
        if _is_project_thick_circle(elem, arc_styles):
            thick_circle_ids.add(id(elem))
        for child in elem:
            visit(child, arc_styles)

    visit(root)
    return thick_circle_ids
