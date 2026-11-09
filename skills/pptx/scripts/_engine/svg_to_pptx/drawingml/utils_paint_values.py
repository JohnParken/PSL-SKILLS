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


# Responsibility: utils_paint_values; adapted from the original module.

_CSS_NAMED_COLORS = {
    'black': '000000',
    'silver': 'C0C0C0',
    'gray': '808080',
    'grey': '808080',
    'white': 'FFFFFF',
    'maroon': '800000',
    'red': 'FF0000',
    'purple': '800080',
    'fuchsia': 'FF00FF',
    'magenta': 'FF00FF',
    'green': '008000',
    'lime': '00FF00',
    'olive': '808000',
    'yellow': 'FFFF00',
    'navy': '000080',
    'blue': '0000FF',
    'teal': '008080',
    'aqua': '00FFFF',
    'cyan': '00FFFF',
    'orange': 'FFA500',
    'brown': 'A52A2A',
    'pink': 'FFC0CB',
    'gold': 'FFD700',
    'transparent': None,
    'lightgray': 'D3D3D3',
    'lightgrey': 'D3D3D3',
    'darkgray': 'A9A9A9',
    'darkgrey': 'A9A9A9',
}


def parse_inline_style(style_str: str | None) -> dict[str, str]:
    """Parse an SVG inline style declaration into ``property: value`` pairs."""
    styles: dict[str, str] = {}
    if not style_str:
        return styles
    for part in style_str.split(';'):
        if ':' not in part:
            continue
        name, value = part.split(':', 1)
        name = name.strip().lower()
        value = value.strip()
        if name and value:
            styles[name] = value
    return styles


def svg_hidden_reason(
    element: ET.Element,
    parent_by_id: dict[int, ET.Element],
    *,
    preserve_native_carriers: bool = False,
) -> str | None:
    """Resolve display suppression and inherited visibility, including overrides."""
    visibility = None
    current: ET.Element | None = element
    while current is not None:
        styles = parse_inline_style(current.get('style'))
        display = styles.get('display', current.get('display', '')).strip().lower()
        if display == 'none':
            return 'display:none'
        native_carrier = (
            preserve_native_carriers
            and current is element
            and current.get('data-pptx-part') == 'geometry'
            and current.get('data-pptx-object') in {'shape', 'connector'}
        )
        if visibility is None and not native_carrier:
            value = styles.get('visibility', current.get('visibility', '')).strip().lower()
            if value and value not in {'inherit', 'unset'}:
                visibility = value
        current = parent_by_id.get(id(current))
    if visibility in {'hidden', 'collapse'}:
        return f'visibility:{visibility}'
    return None


def is_stroked_zero_axis_shape(
    element: ET.Element,
    ctx: ConvertContext | None = None,
) -> bool:
    """Recognize a native shape whose single collapsed frame axis still has an outline."""
    from .utils_stroke_transforms import _extract_inheritable_styles
    from .utils_lengths import parse_project_geometry_length
    from .utils_lengths import parse_project_opacity
    geometry_tags = {'rect', 'circle', 'ellipse', 'line', 'path', 'polygon', 'polyline'}
    if (
        element.tag.rsplit('}', 1)[-1] not in geometry_tags | {'g'}
        or element.get('data-pptx-object') != 'shape'
        or not (
            element.get('data-pptx-prst')
            or element.get('data-pptx-geometry-kind') == 'custom'
        )
    ):
        return False
    try:
        frame = tuple(float(value) for value in re.split(
            r'[\s,]+', (element.get('data-pptx-frame') or '').strip(),
        ))
    except ValueError:
        return False
    if (
        len(frame) != 4 or not all(math.isfinite(value) for value in frame)
        or not ((frame[2] == 0 and frame[3] > 0) or (frame[3] == 0 and frame[2] > 0))
    ):
        return False
    parents = {id(child): parent for parent in element.iter() for child in parent}
    for leaf in element.iter():
        if leaf.tag.rsplit('}', 1)[-1] not in geometry_tags or svg_hidden_reason(leaf, parents):
            continue
        lineage = [leaf]
        while id(lineage[-1]) in parents:
            lineage.append(parents[id(lineage[-1])])
        if any(node.tag.rsplit('}', 1)[-1] in {'defs', 'clipPath', 'mask', 'metadata'} for node in lineage):
            continue
        inherited = dict(ctx.inherited_styles) if ctx is not None else {}
        opacity = ctx.opacity_multiplier if ctx is not None else 1.0
        try:
            for node in reversed(lineage):
                inherited.update(_extract_inheritable_styles(node))
                local = parse_inline_style(node.get('style'))
                opacity *= parse_project_opacity(local.get('opacity', node.get('opacity', '1')))
            kind, value, alpha = parse_project_paint(inherited.get('stroke', 'none'), 'stroke')
            width = parse_project_geometry_length(inherited.get('stroke-width', '1'), 'stroke-width')
            opacity *= parse_project_opacity(inherited.get('stroke-opacity', '1')) * alpha
        except ValueError:
            continue
        if kind == 'color' and width > 0 and opacity > 0:
            return True
        if kind == 'reference' and ctx is not None and width > 0 and opacity > 0:
            gradient = ctx.defs.get(value)
            if gradient is None:
                continue
            try:
                # Only imported DrawingML gradients qualify for this source
                # frame exception; authored gradient contracts stay unchanged.
                if preserved_native_gradient_xml(gradient) is None:
                    continue
                for stop in gradient:
                    if stop.tag.rsplit('}', 1)[-1] != 'stop':
                        continue
                    style = parse_inline_style(stop.get('style'))
                    stop_kind, _color, stop_alpha = parse_project_paint(
                        style.get('stop-color', stop.get('stop-color', '#000000')),
                        'stop-color',
                    )
                    stop_alpha *= parse_project_opacity(
                        style.get('stop-opacity', stop.get('stop-opacity', '1')),
                    )
                    if stop_kind == 'color' and stop_alpha > 0:
                        return True
            except ValueError:
                continue
    return False


def iter_project_geometry_lengths(
    root: ET.Element,
) -> Iterator[tuple[ET.Element, str, str, str]]:
    """Yield project geometry values as element, attribute, raw value, source."""
    from .utils_lengths import PROJECT_GEOMETRY_LENGTH_ATTRIBUTES
    for elem in root.iter():
        tag = elem.tag.rsplit('}', 1)[-1] if '}' in str(elem.tag) else str(elem.tag)
        for attribute in sorted(
            PROJECT_GEOMETRY_LENGTH_ATTRIBUTES.get(tag, frozenset())
        ):
            raw = elem.get(attribute)
            if raw is not None:
                yield elem, attribute, raw, 'attribute'

        direct_stroke_width = elem.get('stroke-width')
        if direct_stroke_width is not None:
            yield elem, 'stroke-width', direct_stroke_width, 'attribute'

        style_stroke_width = parse_inline_style(elem.get('style')).get('stroke-width')
        if style_stroke_width is not None:
            yield elem, 'stroke-width', style_stroke_width, 'inline style'


def project_geometry_length_errors(root: ET.Element) -> list[str]:
    """Return blocking project geometry errors for converter preflight."""
    from .utils_lengths import parse_project_geometry_length
    errors: list[str] = []
    for elem, attribute, raw, source in iter_project_geometry_lengths(root):
        tag = elem.tag.rsplit('}', 1)[-1] if '}' in str(elem.tag) else str(elem.tag)
        elem_id = elem.get('id')
        label = f'<{tag} id={elem_id!r}>' if elem_id else f'<{tag}>'
        try:
            parse_project_geometry_length(raw, attribute)
        except ValueError as exc:
            errors.append(
                f'{label} {source} {attribute}={raw!r}: {exc}'
            )
    return errors


def iter_project_image_aspect_ratios(
    root: ET.Element,
) -> Iterator[tuple[ET.Element, str]]:
    """Yield explicit ``preserveAspectRatio`` values from image elements."""
    from .utils_transforms import _svg_element_tag
    for elem in root.iter():
        if _svg_element_tag(elem) != 'image':
            continue
        raw = elem.get('preserveAspectRatio')
        if raw is not None:
            yield elem, raw


def project_image_aspect_ratio_errors(root: ET.Element) -> list[str]:
    """Return blocking project image aspect-ratio errors for preflight."""
    from .utils_lengths import parse_project_image_aspect_ratio
    errors: list[str] = []
    for elem, raw in iter_project_image_aspect_ratios(root):
        elem_id = elem.get('id')
        label = f'<image id={elem_id!r}>' if elem_id else '<image>'
        try:
            parse_project_image_aspect_ratio(raw)
        except ValueError as exc:
            errors.append(f'{label} preserveAspectRatio={raw!r}: {exc}')
    return errors


def iter_project_opacities(
    root: ET.Element,
) -> Iterator[tuple[ET.Element, str, str, str]]:
    """Yield project opacity values as element, property, raw value, source."""
    from .utils_constants import PROJECT_OPACITY_PROPERTIES
    for elem in root.iter():
        for property_name in PROJECT_OPACITY_PROPERTIES:
            raw = elem.get(property_name)
            if raw is not None:
                yield elem, property_name, raw, 'attribute'

        for fragment in (elem.get('style') or '').split(';'):
            fragment = fragment.strip()
            if not fragment:
                continue
            if ':' in fragment:
                name, raw = fragment.split(':', 1)
                name = name.strip().lower()
                raw = raw.strip()
            else:
                name = fragment.lower()
                raw = ''
            if name in PROJECT_OPACITY_PROPERTIES:
                yield elem, name, raw, 'inline style'


def project_opacity_errors(root: ET.Element) -> list[str]:
    """Return blocking project opacity errors for converter preflight."""
    from .utils_constants import PROJECT_PERCENTAGE_OPACITY_PROPERTIES
    from .utils_transforms import _svg_element_tag
    from .utils_lengths import parse_project_opacity
    errors: list[str] = []
    for elem, property_name, raw, source in iter_project_opacities(root):
        tag = _svg_element_tag(elem) or str(elem.tag)
        elem_id = elem.get('id')
        label = f'<{tag} id={elem_id!r}>' if elem_id else f'<{tag}>'
        try:
            parse_project_opacity(
                raw,
                allow_percentage=(
                    property_name in PROJECT_PERCENTAGE_OPACITY_PROPERTIES
                ),
            )
        except ValueError as exc:
            errors.append(
                f'{label} {source} {property_name}={raw!r}: {exc}'
            )
    return errors


def _finite_float(raw: str) -> float:
    """Parse a finite floating-point number."""
    value = float(raw)
    if not math.isfinite(value):
        raise ValueError(f'Non-finite numeric value: {raw}')
    return value


def _parse_color_channel(raw: str) -> int:
    raw = raw.strip()
    if raw.endswith('%'):
        value = _finite_float(raw[:-1]) * 255.0 / 100.0
    else:
        value = _finite_float(raw)
    return max(0, min(255, int(round(value))))


def _parse_alpha_channel(raw: str) -> float:
    """Parse a CSS alpha channel as a clamped ``0..1`` ratio."""
    raw = raw.strip()
    value = (
        _finite_float(raw[:-1]) / 100.0
        if raw.endswith('%')
        else _finite_float(raw)
    )
    return max(0.0, min(1.0, value))


def parse_opacity(
    raw: str | None,
    default: float = 1.0,
    *,
    allow_percentage: bool = False,
) -> float:
    """Parse one project opacity or return the code-owned missing default."""
    from .utils_lengths import parse_project_opacity
    if raw is None:
        return max(0.0, min(1.0, default))
    return parse_project_opacity(raw, allow_percentage=allow_percentage)


def quantize_ooxml_unit_ratio(value: float) -> int:
    """Quantize one normalized ratio to DrawingML 1/100000 units."""
    if not math.isfinite(value):
        raise ValueError(f'OOXML unit ratio must be finite; got {value!r}')
    normalized = max(0.0, min(1.0, value))
    scaled = Decimal(str(normalized)) * Decimal(100000)
    return int(scaled.to_integral_value(rounding=ROUND_HALF_UP))


def quantize_ooxml_alpha(opacity: float) -> int:
    """Quantize one normalized alpha to DrawingML 1/100000 units."""
    if not math.isfinite(opacity):
        raise ValueError(f'Opacity must be finite; got {opacity!r}')
    return quantize_ooxml_unit_ratio(opacity)


def _functional_color_parts(body: str) -> tuple[list[str], str | None]:
    """Split legacy comma or modern space/slash functional color syntax."""
    before, separator, after = body.partition('/')
    parts = [part for part in re.split(r'[\s,]+', before.strip()) if part]
    alpha = after.strip() if separator else None
    if alpha is None and len(parts) > 3:
        alpha = parts.pop()
    return parts, alpha


def _parse_hue_degrees(raw: str) -> float:
    """Normalize a CSS hue angle to degrees."""
    value = raw.strip().lower()
    for suffix, multiplier in (
        ('turn', 360.0),
        ('grad', 0.9),
        ('rad', 180.0 / math.pi),
        ('deg', 1.0),
    ):
        if value.endswith(suffix):
            return _finite_float(value[:-len(suffix)]) * multiplier
    return _finite_float(value)


def _parse_percentage(raw: str) -> float:
    """Parse a CSS percentage channel as a clamped ``0..1`` ratio."""
    value = raw.strip()
    ratio = (
        _finite_float(value[:-1]) / 100.0
        if value.endswith('%')
        else _finite_float(value) / 100.0
    )
    return max(0.0, min(1.0, ratio))


def parse_svg_color(color_str: str) -> tuple[str | None, float]:
    """Parse an SVG/CSS color into ``(RRGGBB, alpha)``."""
    if not color_str:
        return None, 1.0
    color_str = color_str.strip()
    named = _CSS_NAMED_COLORS.get(color_str.lower())
    if named is not None or color_str.lower() in _CSS_NAMED_COLORS:
        if color_str.lower() == 'transparent':
            return '000000', 0.0
        return named, 1.0

    rgb_match = re.match(r'rgba?\((.+)\)$', color_str, flags=re.IGNORECASE)
    if rgb_match:
        channels, alpha_raw = _functional_color_parts(rgb_match.group(1))
        if len(channels) == 3:
            try:
                r, g, b = (_parse_color_channel(ch) for ch in channels)
                alpha = _parse_alpha_channel(alpha_raw) if alpha_raw is not None else 1.0
                return f'{r:02X}{g:02X}{b:02X}', alpha
            except ValueError:
                return None, 1.0

    hsl_match = re.match(r'hsla?\((.+)\)$', color_str, flags=re.IGNORECASE)
    if hsl_match:
        channels, alpha_raw = _functional_color_parts(hsl_match.group(1))
        if len(channels) == 3:
            try:
                hue = (_parse_hue_degrees(channels[0]) % 360.0) / 360.0
                saturation = _parse_percentage(channels[1])
                lightness = _parse_percentage(channels[2])
                red, green, blue = colorsys.hls_to_rgb(hue, lightness, saturation)
                alpha = _parse_alpha_channel(alpha_raw) if alpha_raw is not None else 1.0
                return (
                    f'{round(red * 255):02X}{round(green * 255):02X}{round(blue * 255):02X}',
                    alpha,
                )
            except ValueError:
                return None, 1.0

    if color_str.startswith('#'):
        color_str = color_str[1:]
    if len(color_str) == 3:
        color_str = ''.join(c * 2 for c in color_str)
    elif len(color_str) == 4:
        color_str = ''.join(c * 2 for c in color_str)
    if len(color_str) == 8 and all(c in '0123456789abcdefABCDEF' for c in color_str):
        return color_str[:6].upper(), int(color_str[6:], 16) / 255.0
    if len(color_str) == 6 and all(c in '0123456789abcdefABCDEF' for c in color_str):
        return color_str.upper(), 1.0
    return None, 1.0


def parse_project_paint(
    raw: str,
    property_name: str,
) -> tuple[str, str | None, float]:
    """Parse one paint value from the closed project grammar.

    Returns ``(kind, value, alpha)`` where ``kind`` is ``color``, ``none``,
    or ``reference``. Color values are normalized to ``RRGGBB``; reference
    values contain the local definition id.
    """
    from .utils_constants import PROJECT_PAINT_PROPERTIES
    from .utils_constants import PROJECT_REFERENCE_PAINT_PROPERTIES
    if property_name not in PROJECT_PAINT_PROPERTIES:
        raise ValueError(f'unknown project paint property {property_name!r}')

    value = raw.strip()
    if property_name in PROJECT_REFERENCE_PAINT_PROPERTIES:
        if value.lower() == 'none':
            return 'none', None, 1.0
        reference = re.fullmatch(r'url\(#([^)]+)\)', value)
        if reference is not None:
            return 'reference', reference.group(1), 1.0

    color, alpha = parse_svg_color(value)
    if color is not None:
        return 'color', color, alpha

    accepted = (
        'a supported color, none, or an exact local url(#id) reference'
        if property_name in PROJECT_REFERENCE_PAINT_PROPERTIES
        else 'a supported color'
    )
    raise ValueError(f'must be {accepted}')


def is_project_paint_default_form(raw: str, property_name: str) -> bool:
    """Return whether paint uses the generated project spelling."""
    from .utils_constants import PROJECT_REFERENCE_PAINT_PROPERTIES
    value = raw.strip()
    if property_name in PROJECT_REFERENCE_PAINT_PROPERTIES:
        if value == 'none':
            return True
        if re.fullmatch(r'url\(#[^)]+\)', value) is not None:
            return True
    return re.fullmatch(r'#[0-9A-F]{6}', value) is not None


def iter_project_paints(
    root: ET.Element,
) -> Iterator[tuple[ET.Element, str, str, str]]:
    """Yield project paint values as element, property, raw value, source."""
    from .utils_constants import PROJECT_PAINT_PROPERTIES
    for elem in root.iter():
        for property_name in PROJECT_PAINT_PROPERTIES:
            raw = elem.get(property_name)
            if raw is not None:
                yield elem, property_name, raw, 'attribute'

        for fragment in (elem.get('style') or '').split(';'):
            fragment = fragment.strip()
            if not fragment:
                continue
            if ':' in fragment:
                name, raw = fragment.split(':', 1)
                name = name.strip().lower()
                raw = raw.strip()
            else:
                name = fragment.lower()
                raw = ''
            if name in PROJECT_PAINT_PROPERTIES:
                yield elem, name, raw, 'inline style'


def project_paint_errors(root: ET.Element) -> list[str]:
    """Return blocking project paint errors for converter preflight."""
    from .utils_transforms import _svg_element_tag
    errors: list[str] = []
    for elem, property_name, raw, source in iter_project_paints(root):
        tag = _svg_element_tag(elem) or str(elem.tag)
        elem_id = elem.get('id')
        label = f'<{tag} id={elem_id!r}>' if elem_id else f'<{tag}>'
        try:
            parse_project_paint(raw, property_name)
        except ValueError as exc:
            errors.append(
                f'{label} {source} {property_name}={raw!r}: {exc}'
            )
    return errors
