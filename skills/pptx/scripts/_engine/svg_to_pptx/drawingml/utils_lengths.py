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


# Responsibility: utils_lengths; adapted from the original module.

def px_to_emu(px: float) -> int:
    """Convert SVG pixels to EMU."""
    from .utils_constants import EMU_PER_PX
    return round(px * EMU_PER_PX)


def font_px_to_hpt(font_size_px: float) -> int:
    """Convert one legal SVG font size to DrawingML hundredths-of-a-point."""
    from .utils_constants import DRAWINGML_TEXT_FONT_SIZE_MAX
    from .utils_constants import DRAWINGML_TEXT_FONT_SIZE_MIN
    from .utils_constants import FONT_PX_TO_HUNDREDTHS_PT
    try:
        px = float(font_size_px)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(
            f"SVG font-size must be numeric, got {font_size_px!r}"
        ) from exc
    scaled = px * FONT_PX_TO_HUNDREDTHS_PT
    if not math.isfinite(scaled):
        raise ValueError(f"SVG font-size must be finite, got {font_size_px!r}")
    size = int(round(scaled / 10.0)) * 10
    if not DRAWINGML_TEXT_FONT_SIZE_MIN <= size <= DRAWINGML_TEXT_FONT_SIZE_MAX:
        raise ValueError(
            f"SVG font-size {font_size_px!r}px converts to DrawingML sz={size}; "
            f"expected {DRAWINGML_TEXT_FONT_SIZE_MIN}.."
            f"{DRAWINGML_TEXT_FONT_SIZE_MAX} (1..4000pt)"
        )
    return size


def _f(val: str | None, default: float = 0.0) -> float:
    """Parse a float attribute value, returning default if missing."""
    if val is None:
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


_LENGTH_RE = re.compile(r'^\s*([-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?)\s*([A-Za-z%]*)\s*$')
_CANONICAL_PROJECT_GEOMETRY_LENGTH_RE = re.compile(
    r'^-?(?:\d+(?:\.\d+)?|\.\d+)$'
)
_PROJECT_STROKE_DASH_NUMBER_PATTERN = (
    r'[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?'
)
_PROJECT_STROKE_DASH_NUMBER_RE = re.compile(
    _PROJECT_STROKE_DASH_NUMBER_PATTERN
)
_PROJECT_STROKE_DASHARRAY_RE = re.compile(
    rf'\s*{_PROJECT_STROKE_DASH_NUMBER_PATTERN}'
    rf'(?:(?:\s+|\s*,\s*){_PROJECT_STROKE_DASH_NUMBER_PATTERN})+\s*'
)
PROJECT_GEOMETRY_LENGTH_ATTRIBUTES = {
    'svg': frozenset({'x', 'y', 'width', 'height'}),
    'rect': frozenset({'x', 'y', 'width', 'height', 'rx', 'ry'}),
    'circle': frozenset({'cx', 'cy', 'r'}),
    'ellipse': frozenset({'cx', 'cy', 'rx', 'ry'}),
    'line': frozenset({'x1', 'y1', 'x2', 'y2'}),
    'text': frozenset({'x', 'y'}),
    'tspan': frozenset({'x', 'y', 'dx', 'dy'}),
    'image': frozenset({'x', 'y', 'width', 'height'}),
    'use': frozenset({'x', 'y', 'width', 'height'}),
}
PROJECT_NON_NEGATIVE_LENGTH_ATTRIBUTES = frozenset({
    'width', 'height', 'r', 'rx', 'ry', 'stroke-width',
})


def _parse_svg_length_parts(val: str) -> tuple[float, str]:
    """Parse one finite SVG length into its numeric part and lowercase unit."""
    match = _LENGTH_RE.match(str(val))
    if not match:
        raise ValueError(f'SVG length must be one finite literal, got {val!r}')
    number = float(match.group(1))
    if not math.isfinite(number):
        raise ValueError(f'SVG length must be finite, got {val!r}')
    return number, match.group(2).lower()


def parse_svg_length(
    val: str | None,
    default: float = 0.0,
    *,
    percent_base: float | None = None,
    font_size: float = 16.0,
) -> float:
    """Parse SVG/CSS length values into SVG px.

    Unitless and ``px`` values are already SVG px. Percentages need a caller
    supplied reference length because SVG uses different bases for x, y,
    width, height, and radii.

    A default applies only when the attribute is absent. Present but malformed,
    non-finite, unsupported, or context-free percentage values fail closed.
    """
    if val is None:
        return default
    number, unit = _parse_svg_length_parts(str(val))
    if unit == '%':
        if percent_base is None:
            raise ValueError(
                f'SVG percentage length requires a reference length, got {val!r}'
            )
        return percent_base * number / 100.0
    if unit in ('', 'px'):
        return number
    if unit == 'pt':
        return number * 96.0 / 72.0
    if unit in ('pc', 'pica'):
        return number * 16.0
    if unit == 'in':
        return number * 96.0
    if unit == 'cm':
        return number * 96.0 / 2.54
    if unit == 'mm':
        return number * 96.0 / 25.4
    if unit == 'q':
        return number * 96.0 / 101.6
    if unit in ('em', 'rem'):
        if not math.isfinite(font_size):
            raise ValueError(
                f'SVG relative length requires a finite font size, got {val!r}'
            )
        return number * font_size
    raise ValueError(f'Unsupported SVG length unit {unit!r} in {val!r}')


def parse_project_geometry_length(raw: str, attribute: str) -> float:
    """Parse one project geometry value without widening the authoring surface."""
    number, unit = _parse_svg_length_parts(raw)
    if unit not in {'', 'px'}:
        raise ValueError(
            f'uses unsupported unit {unit!r}; project geometry accepts only '
            'unitless values or the compatible px suffix'
        )
    numeric_literal = raw.strip()
    if unit == 'px':
        numeric_literal = numeric_literal[:-2].strip()
    if not _CANONICAL_PROJECT_GEOMETRY_LENGTH_RE.fullmatch(numeric_literal):
        raise ValueError(
            'uses an unsupported numeric spelling; use an ordinary decimal '
            'without a leading plus sign, exponent, or trailing decimal point'
        )
    if attribute in PROJECT_NON_NEGATIVE_LENGTH_ATTRIBUTES and number < 0:
        raise ValueError('must be non-negative')
    return number


def is_canonical_project_geometry_length(raw: str) -> bool:
    """Return whether a project geometry value uses the generated-SVG spelling."""
    return bool(_CANONICAL_PROJECT_GEOMETRY_LENGTH_RE.fullmatch(raw.strip()))


def format_project_geometry_length(value: float) -> str:
    """Format a parsed project geometry value as a plain unitless decimal."""
    if abs(value) < 1e-15:
        return '0'
    text = f'{value:.15f}'.rstrip('0').rstrip('.')
    return '0' if text in {'', '-0'} else text


def parse_project_opacity(
    raw: str,
    *,
    allow_percentage: bool = False,
) -> float:
    """Parse and clamp one opacity value from the closed project grammar."""
    try:
        number, unit = _parse_svg_length_parts(raw)
    except ValueError as exc:
        raise ValueError('must be one finite numeric opacity') from exc

    if unit == '%':
        if not allow_percentage:
            raise ValueError('must be unitless; percentages are not supported')
        number /= 100.0
    elif unit:
        raise ValueError(f'uses unsupported unit {unit!r}')
    return max(0.0, min(1.0, number))


def is_project_opacity_default_form(raw: str) -> bool:
    """Return whether opacity uses the generated finite unitless ``0..1`` form."""
    try:
        number, unit = _parse_svg_length_parts(raw)
    except ValueError:
        return False
    return unit == '' and 0.0 <= number <= 1.0


def format_project_opacity(value: float) -> str:
    """Format one parsed opacity as a compact unitless ``0..1`` value."""
    bounded = max(0.0, min(1.0, value))
    return f'{bounded:.6f}'.rstrip('0').rstrip('.') or '0'


def parse_project_image_aspect_ratio(raw: str | None) -> tuple[str, str]:
    """Parse the closed project ``<image>`` aspect-ratio grammar."""
    from .utils_constants import PROJECT_IMAGE_ASPECT_RATIO_ANCHORS
    from .utils_constants import PROJECT_IMAGE_ASPECT_RATIO_MODES
    if raw is None:
        return 'xMidYMid', 'meet'

    text = raw.strip()
    if not text:
        raise ValueError('must not be empty; omit the attribute for the default')

    parts = text.split()
    align = parts[0]
    if align == 'none':
        if len(parts) != 1:
            raise ValueError('value "none" must appear alone')
        return align, 'meet'

    if align not in PROJECT_IMAGE_ASPECT_RATIO_ANCHORS:
        choices = ', '.join(PROJECT_IMAGE_ASPECT_RATIO_ANCHORS)
        raise ValueError(
            f'alignment must be "none" or one of: {choices}'
        )
    if len(parts) > 2:
        raise ValueError('accepts at most one alignment and one mode token')

    mode = parts[1] if len(parts) == 2 else 'meet'
    if mode not in PROJECT_IMAGE_ASPECT_RATIO_MODES:
        choices = ', '.join(sorted(PROJECT_IMAGE_ASPECT_RATIO_MODES))
        raise ValueError(f'mode must be one of: {choices}')
    return align, mode


def format_project_image_aspect_ratio(align: str, mode: str) -> str:
    """Format one parsed image aspect ratio for generated project SVG."""
    if align == 'none':
        return 'none'
    return f'{align} {mode}'


def _parse_project_stroke_dasharray(
    raw: str,
    *,
    allow_zero_gap: bool = False,
) -> tuple[str | None, tuple[float, ...], tuple[str, ...]] | None:
    """Parse one project dash array without accepting general SVG lengths."""
    from .utils_constants import DASH_PRESETS
    text = raw.strip()
    if text == 'none':
        return None
    if not _PROJECT_STROKE_DASHARRAY_RE.fullmatch(text):
        raise ValueError(
            'must be "none" or at least two finite unitless numbers separated '
            'by spaces or single commas'
        )
    tokens = tuple(_PROJECT_STROKE_DASH_NUMBER_RE.findall(text))
    values = tuple(float(token) for token in tokens)
    if not all(math.isfinite(value) for value in values):
        raise ValueError('must contain only finite numbers')
    if values[0] <= 0:
        raise ValueError('dash length must be positive')
    if allow_zero_gap:
        if values[1] < 0:
            raise ValueError('dash gap must be non-negative')
    elif values[1] <= 0:
        raise ValueError('dash gap must be positive')
    if any(value <= 0 for value in values[2:]):
        raise ValueError('additional dash and gap values must be positive')
    return DASH_PRESETS.get(values), values, tokens


def parse_project_stroke_dasharray(
    raw: str,
    *,
    allow_zero_gap: bool = False,
) -> tuple[str | None, tuple[float, ...]] | None:
    """Return the registered preset and numeric values for one dash array."""
    parsed = _parse_project_stroke_dasharray(
        raw,
        allow_zero_gap=allow_zero_gap,
    )
    if parsed is None:
        return None
    preset, values, _tokens = parsed
    return preset, values


def noncanonical_stroke_dash_numbers(raw: str) -> tuple[str, ...]:
    """Return compatible dash numbers outside the generated-SVG spelling."""
    parsed = _parse_project_stroke_dasharray(raw, allow_zero_gap=True)
    if parsed is None:
        return ()
    _preset, _values, tokens = parsed
    return tuple(
        token
        for token in tokens
        if not _CANONICAL_PROJECT_GEOMETRY_LENGTH_RE.fullmatch(token)
    )


def parse_project_stroke_enum(attribute: str, raw: str) -> str:
    """Parse one closed line-presentation enumeration."""
    from .utils_constants import PROJECT_STROKE_ENUM_VALUES
    allowed = PROJECT_STROKE_ENUM_VALUES.get(attribute)
    if allowed is None:
        raise ValueError(f'has no registered project enumeration for {attribute!r}')
    value = raw.strip()
    if value not in allowed:
        choices = ', '.join(sorted(allowed))
        raise ValueError(f'must be one of: {choices}')
    return value


def is_thick_circle_shorthand(
    dasharray: str | None,
    stroke: str | None,
    fill: str | None,
    stroke_width: float,
    radius: float,
) -> bool:
    """Return whether one circle uses the converter's thick-arc shorthand."""
    from .utils_constants import THICK_CIRCLE_COVERAGE_TOLERANCE
    if (
        not dasharray
        or not stroke
        or stroke.strip().lower() in {'none', 'transparent'}
    ):
        return False
    if not fill or fill.strip().lower() != 'none':
        return False
    if stroke_width <= 0 or radius <= 0 or stroke_width >= 2 * radius:
        return False
    if stroke_width / radius < 0.15:
        return False
    try:
        parsed = parse_project_stroke_dasharray(
            dasharray,
            allow_zero_gap=True,
        )
    except ValueError:
        return False
    if parsed is None:
        return False
    preset, values = parsed
    if preset is not None or len(values) != 2:
        return False
    dash, gap = values
    circumference = 2 * math.pi * radius
    return (
        dash < circumference
        and dash + gap + THICK_CIRCLE_COVERAGE_TOLERANCE >= circumference
    )


def svg_length_x(val: str | None, ctx: ConvertContext, default: float = 0.0) -> float:
    return parse_svg_length(val, default, percent_base=ctx.viewport_width)


def svg_length_y(val: str | None, ctx: ConvertContext, default: float = 0.0) -> float:
    return parse_svg_length(val, default, percent_base=ctx.viewport_height)


def svg_length_size(val: str | None, ctx: ConvertContext, default: float = 0.0) -> float:
    base = min(ctx.viewport_width, ctx.viewport_height)
    return parse_svg_length(val, default, percent_base=base)


# ---------------------------------------------------------------------------
# SVG transform matrix helpers
# ---------------------------------------------------------------------------
