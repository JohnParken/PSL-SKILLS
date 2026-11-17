"""Coordinate, transform, color, and font helpers for DrawingML conversion.

See references/shared-standards-core.md §2.1 for project geometry and
references/svg-effects.md §§6.2–6.8 for paint, image-fit, line-presentation,
and transform authoring contracts.
"""

from __future__ import annotations

from collections.abc import Iterator
from xml.etree import ElementTree as ET




# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


# Adapted from utils.py; upstream MIT, Hugo He, 2025-2026.
def iter_project_stroke_styles(
    root: ET.Element,
) -> Iterator[tuple[ET.Element, str, str, str]]:
    """Yield project line-style values with their declaration source."""
    # Resolve shared symbols at call time; retain the public module contract.
    from .utils import (
        _PROJECT_STROKE_STYLE_ATTRIBUTES,
        parse_inline_style,
    )
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
    # Resolve shared symbols at call time; retain the public module contract.
    from .utils import (
        _project_thick_circle_ids,
        _transform_element_label,
        iter_project_stroke_styles,
        parse_project_geometry_length,
        parse_project_stroke_dasharray,
        parse_project_stroke_enum,
    )
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
    # Resolve shared symbols at call time; retain the public module contract.
    from .utils import (
        _iter_visual_transform_tree,
    )
    return any(
        id(descendant) in thick_circle_ids
        for descendant in _iter_visual_transform_tree(elem)
    )
