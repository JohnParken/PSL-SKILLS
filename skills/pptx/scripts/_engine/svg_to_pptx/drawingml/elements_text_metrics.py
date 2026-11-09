# Adapted from PPT Master 5e8856b (MIT); see assets/licenses/ppt_master.txt.
"""SVG element converters: rect, circle, line, path, polygon, polyline, text, image, ellipse."""

from __future__ import annotations

import base64
import binascii
import hashlib
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote_to_bytes
from xml.etree import ElementTree as ET

from pptx_core.images import ImageValidationError, MAX_IMAGE_BYTES, inspect_image

from pptx_shapes import (
    CONNECTOR_PRESET_TYPES,
    OOXML_COORDINATE_MAX,
    OOXML_COORDINATE_MIN,
    get_preset_registry,
    has_relationship_attributes,
    load_shape_type_values,
    validate_ooxml_xfrm,
)
from pptx_effects import (
    EFFECT_REASON_ATTR,
    EFFECT_STATUS_ATTR,
    NATIVE_EFFECT_ATTR,
    NATIVE_EFFECT_SHA256_ATTR,
    preserved_native_effect_xml,
)
from hyperlink_contract import svg_hyperlink_href
from pptx_to_svg.preset_authoring import AUTHORING_ATTR, AUTHORING_VALUE
from resource_paths import (
    resolve_external_image_reference,
)

from .context import (
    TEXT_FLOW_PRESERVE,
    TEXT_FLOW_SPLIT,
    ConvertContext,
    ShapeResult,
)
from .hyperlinks import (
    HYPERLINK_ACTION_KEY,
    HYPERLINK_RID_KEY,
    hyperlink_click_xml,
    hyperlink_run_metadata,
)
from .theme_colors import color_node_xml
from .theme_fonts import theme_font_tokens
from .text_baseline import drawingml_text_baseline_offset
from .text_properties import (
    drawingml_letter_spacing,
    normalize_project_text_segments,
    parse_project_baseline_shift,
    parse_project_font_style,
    parse_project_font_weight,
    parse_project_letter_spacing,
    parse_project_text_anchor,
    parse_project_text_decoration,
    resolve_project_xml_space,
)
from .utils import (
    SVG_NS, XLINK_NS, ANGLE_UNIT, FONT_PX_TO_HUNDREDTHS_PT,
    PROJECT_IMAGE_ASPECT_RATIO_ANCHORS,
    px_to_emu, _f, _get_attr, parse_svg_length,
    svg_length_x, svg_length_y, svg_length_size,
    ctx_x, ctx_y, ctx_w, ctx_h,
    rect_to_dml_xfrm,
    combine_opacity, parse_hex_color, parse_svg_color,
    resolve_project_text_image_fill, resolve_url_id, get_effective_filter_id,
    parse_inline_style, parse_font_family, is_cjk_char,
    detect_text_lang, estimate_text_cluster_widths, font_px_to_hpt,
    get_font_advances, primary_font_family,
    resolve_text_run_fonts, split_project_text_clusters,
    text_has_rtl_characters, text_uses_rtl,
    is_thick_circle_shorthand, parse_project_geometry_length,
    is_canonical_project_geometry_length,
    parse_project_image_aspect_ratio,
    parse_project_opacity,
    parse_project_stroke_dasharray,
    quantize_ooxml_alpha,
    project_definition_index,
    svg_hidden_reason,
    is_stroked_zero_axis_shape,
    matrix_multiply, parse_transform_matrix, parse_transform_operations,
    transform_point, _xml_escape,
)
from .styles import (
    build_solid_fill, build_gradient_fill,
    build_fill_xml, build_stroke_xml, build_effect_xml, classify_filter_effect,
    get_element_opacity, get_fill_opacity, get_stroke_opacity,
)
from .paths import (
    PathCommand, parse_svg_path, parse_svg_points, svg_path_to_absolute,
    normalize_path_commands, path_commands_to_drawingml,
    transform_path_commands,
)



# Responsibility: elements_text_metrics; adapted from the original module.

def _text_line_vertical_extent(
    runs: list[dict[str, Any]],
    font_size: float,
) -> tuple[float, float, bool]:
    """Return native-math-aware ascent/descent for one authored text line."""
    from .elements_path_shapes import _INLINE_FORMULA_KEY
    ascent = font_size * 0.85
    descent = font_size * 0.35
    has_inline_formula = False
    from ..native_objects.formula_compiler import (
        estimate_inline_formula_vertical_extent,
    )

    for run in runs:
        latex = run.get(_INLINE_FORMULA_KEY)
        if latex is None:
            continue
        has_inline_formula = True
        run_font_size = float(run.get('font_size', font_size))
        extent = estimate_inline_formula_vertical_extent(str(latex))
        ascent = max(ascent, run_font_size * extent.ascent_em)
        descent = max(descent, run_font_size * extent.descent_em)
    return ascent, descent, has_inline_formula


def _native_text_line_frame_height(
    ascent: float,
    descent: float,
    font_size: float,
) -> float:
    """Add the ordinary text-frame headroom to one visible line extent."""
    return ascent + descent + font_size * 0.30


def _normalize_text_run_whitespace(
    runs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Apply the shared whitespace contract without losing run ownership."""
    normalized: list[dict[str, Any]] = []
    segments = [
        (str(run.get('_xml_space', 'default')), str(run.get('text', '')))
        for run in runs
    ]
    text_by_index = dict(normalize_project_text_segments(segments))
    for index, source_run in enumerate(runs):
        if '_inline_dx' in source_run:
            # Insert after whitespace normalization so dx neither preserves
            # indentation nor disappears with an empty/whitespace-only tspan.
            run = {**source_run, 'text': ' ', 'letter_spacing': 0.0, 'text_decoration': 'none'}
            dx = run.pop('_inline_dx')
            run['letter_spacing'] = dx - _estimate_run_text_width(run)
            run.update(text='\u00a0', _inline_dx=dx)
            run.pop('_xml_space', None)
            normalized.append(run)
            continue
        text = text_by_index.get(index)
        if text is None:
            continue
        run = {**runs[index], 'text': text}
        run.pop('_xml_space', None)
        normalized.append(run)
    return normalized


def _letter_spacing_to_drawingml_spc(letter_spacing_px: float) -> str:
    """Convert SVG px letter spacing into DrawingML rPr@spc."""
    spacing = drawingml_letter_spacing(letter_spacing_px)
    if spacing == 0:
        return ''
    return f' spc="{spacing}"'


def _is_serif_face(typeface: str) -> bool:
    """Return whether one resolved DrawingML typeface is serif-like."""
    from .elements_path_shapes import _SERIF_WIDTH_FAMILIES
    name = typeface.strip().strip("'\"").lower()
    if not name or name in {'sans-serif', 'sans serif'}:
        return False
    if name in _SERIF_WIDTH_FAMILIES:
        return True
    return 'serif' in name and 'sans' not in name


def _run_script_segments(run: dict[str, Any]) -> list[tuple[str, bool]]:
    """Split one run's text into ``(text, is_cjk)`` segments.

    PowerPoint draws CJK clusters (fullwidth punctuation included) with the
    run's ``ea`` typeface and every other cluster with ``latin``, the same
    split :func:`parse_font_family` resolves. Width headroom is a property of
    the face that actually draws a glyph, so it is applied per segment.
    """
    segments: list[list[Any]] = []
    for cluster in split_project_text_clusters(str(run.get('text', ''))):
        cjk = any(is_cjk_char(ch) for ch in cluster)
        if segments and segments[-1][1] == cjk:
            segments[-1][0] += cluster
        else:
            segments.append([cluster, cjk])
    return [(text, cjk) for text, cjk in segments]


def _estimate_run_width_with_headroom(run: dict[str, Any]) -> float:
    """Estimate one run with headroom chosen per script segment.

    The serif/sans tier follows the typeface that draws each segment (``ea``
    for CJK, ``latin`` otherwise), and the uppercase interpolation only ever
    applies to the Latin segments: CJK advances are fixed-width, so a Chinese
    sentence that mentions ``AI`` keeps its own base headroom instead of
    inheriting the all-caps ceiling for the whole line.
    """
    from .elements_path_shapes import _SERIF_TEXT_WIDTH_HEADROOM_BASE
    from .elements_path_shapes import _SERIF_TEXT_WIDTH_HEADROOM_CAPS
    from .elements_path_shapes import _TEXT_WIDTH_HEADROOM_BASE
    from .elements_path_shapes import _TEXT_WIDTH_HEADROOM_CAPS
    segments = _run_script_segments(run)
    if not segments:
        return 0.0
    faces = parse_font_family(str(run.get('font_family', '')))
    serif_by_script = {
        False: _is_serif_face(faces['latin']),
        True: _is_serif_face(faces['ea']),
    }
    letter_spacing_px = (
        drawingml_letter_spacing(
            float(run.get('letter_spacing', 0.0) or 0.0)
        )
        / FONT_PX_TO_HUNDREDTHS_PT
    )
    width = 0.0
    for text, cjk in segments:
        segment = dict(run, text=text)
        if serif_by_script[cjk]:
            base = _SERIF_TEXT_WIDTH_HEADROOM_BASE
            ceiling = _SERIF_TEXT_WIDTH_HEADROOM_CAPS
        else:
            base = _TEXT_WIDTH_HEADROOM_BASE
            ceiling = _TEXT_WIDTH_HEADROOM_CAPS
        caps = 0.0 if cjk else _uppercase_fraction([segment])
        width += _estimate_run_text_width(segment) * (
            base + (ceiling - base) * caps
        )
    # Splitting the run drops the tracking gap at each segment boundary; add
    # it back unscaled so a negative gap is not amplified by the headroom of
    # the segment that follows it. Headroom is a safety margin, so the result
    # never falls below the run's raw advance either.
    width += letter_spacing_px * (len(segments) - 1)
    return max(width, _estimate_run_text_width(run))


# Faces whose glyphs run wider than the generic advance table at the same
# weight. Measured against the installed fonts on 2026-09-04: Arial Black
# renders 24% wider than the caps estimate, Verdana 6%. Only factors ≥ 1 are
# listed — a narrower face wastes space, a wider one overflows the bounds.
# (mixed-case factor, all-caps factor): the generic table is already
# pessimistic on lowercase and optimistic on capitals, so the correction
# scales with the run's uppercase fraction like the headroom does.
_WIDE_FAMILY_WIDTH_FACTORS = {
    'arial black': (1.06, 1.25),
    'verdana': (1.02, 1.08),
}

# Monospaced faces advance every Latin glyph by one fixed em fraction, so the
# per-glyph table (tuned for proportional sans faces) undershoots them by
# 14–22%: measured 2026-09-05 at 20px, Courier New, DejaVu Sans Mono and Noto
# Sans Mono all render 0.600 em per character where the generic estimate gives
# 0.46–0.50. A monospaced run is therefore measured as characters × advance
# instead of scaled by a factor; CJK glyphs in these faces stay full-width and
# keep the generic estimate. Advances are the faces' published hmtx values.
_MONOSPACE_ADVANCE_EM = {
    'andale mono': 0.60,
    'cascadia code': 0.586,
    'cascadia mono': 0.586,
    'consolas': 0.55,
    'courier': 0.60,
    'courier new': 0.60,
    'dejavu sans mono': 0.602,
    'fira code': 0.60,
    'fira mono': 0.60,
    'hack': 0.602,
    'ibm plex mono': 0.60,
    'inconsolata': 0.50,
    'jetbrains mono': 0.60,
    'liberation mono': 0.60,
    'lucida console': 0.60,
    'menlo': 0.602,
    'monaco': 0.60,
    'monospace': 0.60,
    'noto sans mono': 0.60,
    'pt mono': 0.60,
    'roboto mono': 0.60,
    'sf mono': 0.602,
    'source code pro': 0.60,
    'ubuntu mono': 0.50,
}


def _run_primary_family(run: dict[str, Any]) -> str:
    return primary_font_family(run.get('font_family'))


# Fixed-pitch faces outside the table almost always say so in their name
# (Cascadia Mono, Fira Code, Victor Mono, Noto Sans Mono CJK); 0.60 em is the
# common advance of the Courier-derived and modern coding families alike.
# "Arial Unicode MS" and "Monotype Corsiva" are proportional despite the letters.
_MONOSPACE_NAME_HINT_RE = re.compile(r'mono(?!type)|(?<!uni)code|courier|consol|typewriter')
_MONOSPACE_DEFAULT_ADVANCE_EM = 0.60


def _monospace_advance_em(run: dict[str, Any]) -> float | None:
    """Return the fixed per-character advance of a monospaced run, if any."""
    family = _run_primary_family(run)
    if not family:
        return None
    advance = _MONOSPACE_ADVANCE_EM.get(family)
    if advance is not None:
        return advance
    if _MONOSPACE_NAME_HINT_RE.search(family):
        return _MONOSPACE_DEFAULT_ADVANCE_EM
    return None


def _family_width_factor(run: dict[str, Any]) -> float:
    if get_font_advances(
        run.get('font_family'),
        str(run.get('font_weight', '400')),
        str(run.get('font_style', 'normal')),
    ) is not None:
        return 1.0
    factors = _WIDE_FAMILY_WIDTH_FACTORS.get(_run_primary_family(run))
    if factors is None:
        return 1.0
    base, caps = factors
    return base + (caps - base) * _uppercase_fraction([run])


def _estimate_run_text_width(run: dict[str, Any]) -> float:
    """Estimate one run using the metrics actually emitted to DrawingML."""
    if '_inline_dx' in run:
        return float(run['_inline_dx'])
    text = str(run.get('text', ''))
    font_size_px = (
        font_px_to_hpt(float(run.get('font_size', 16)))
        / FONT_PX_TO_HUNDREDTHS_PT
    )
    cluster_widths = estimate_text_cluster_widths(
        text,
        font_size_px,
        str(run.get('font_weight', '400')),
        font_family=run.get('font_family'),
        font_style=str(run.get('font_style', 'normal')),
    )
    clusters = split_project_text_clusters(text)
    cjk_flags = [any(is_cjk_char(ch) for ch in cluster) for cluster in clusters]
    monospace_advance = _monospace_advance_em(run)
    if monospace_advance is not None:
        # Fixed-pitch faces ignore weight and glyph shape for Latin text.
        cluster_widths = [
            width if cjk else monospace_advance * font_size_px
            for cjk, width in zip(cjk_flags, cluster_widths)
        ]
    # The wide-face correction belongs to the Latin typeface only: CJK
    # clusters draw with the ``ea`` face, so they neither widen with the
    # family nor count toward its uppercase fraction.
    latin_factor = _family_width_factor(dict(
        run,
        text=''.join(
            cluster for cluster, cjk in zip(clusters, cjk_flags) if not cjk
        ),
    ))
    cluster_widths = [
        width if cjk else width * latin_factor
        for cjk, width in zip(cjk_flags, cluster_widths)
    ]
    letter_spacing_px = (
        drawingml_letter_spacing(
            float(run.get('letter_spacing', 0.0) or 0.0)
        )
        / FONT_PX_TO_HUNDREDTHS_PT
    )
    return sum(cluster_widths) + letter_spacing_px * max(len(cluster_widths) - 1, 0)


def validate_text_run_advances(runs: list[dict[str, Any]]) -> None:
    """Reject negative tracking that reverses or collapses one output run."""
    for run in runs:
        text = str(run.get('text', ''))
        letter_spacing = float(run.get('letter_spacing', 0.0) or 0.0)
        if len(split_project_text_clusters(text)) < 2 or letter_spacing >= 0:
            continue
        advance = _estimate_run_text_width(run)
        if advance > 0:
            continue
        snippet = re.sub(r'\s+', ' ', text)
        raise ValueError(
            'negative letter-spacing produces a non-positive DrawingML '
            f'text-run advance for {snippet!r} (advance={advance:g}px)'
        )


def _uppercase_fraction(runs: list[dict[str, Any]]) -> float:
    """Fraction of cased letters across ``runs`` that are uppercase.

    Caseless scripts (CJK, digits, punctuation) are ignored, so a Chinese or
    numeric line reports 0.0 and takes the low-caps headroom base.
    """
    upper = 0
    cased = 0
    for run in runs:
        for ch in str(run.get('text', '')):
            if ch.lower() != ch.upper():
                cased += 1
                if ch.isupper():
                    upper += 1
    if not cased:
        return 0.0
    return upper / cased


def _estimate_text_runs_width(
    runs: list[dict[str, Any]],
    *,
    include_headroom: bool = True,
) -> float:
    """Estimate a line of text runs.

    ``include_headroom`` is useful for single-line auto-fit boxes where a
    renderer that measures text slightly wider would otherwise wrap. The
    headroom scales independently with each script segment's typeface and,
    for Latin segments, its uppercase fraction. This keeps mixed-font and
    mixed-script lines from inheriting the most conservative segment's
    multiplier. Paragraph boxes use this value as a wrapping constraint, so
    adding headroom there stretches the merged text frame beyond the author's
    source line width.
    """
    if any('_inline_dx' in run for run in runs):
        # A negative dx moves the cursor back; it must not subtract space
        # already occupied by earlier glyphs or make the frame extent negative.
        advance = right = 0.0
        for run in runs:
            if '_inline_dx' in run:
                advance += float(run['_inline_dx'])
                continue
            advance += _estimate_text_runs_width([run], include_headroom=include_headroom)
            right = max(right, advance)
        return right
    if not include_headroom:
        return sum(_estimate_run_text_width(run) for run in runs)

    return sum(_estimate_run_width_with_headroom(run) for run in runs)


def estimate_single_line_text_frame_width(
    runs: list[dict[str, Any]],
    *,
    include_headroom: bool = True,
) -> float:
    """Estimate one DrawingML textbox width with optional safety headroom."""
    content_runs, bullet = _extract_text_bullet(runs)
    width = _estimate_text_runs_width(
        content_runs,
        include_headroom=include_headroom,
    )
    if bullet:
        font_size = (
            float(content_runs[0].get('font_size', 16))
            if content_runs else 16.0
        )
        width += _bullet_margin_px(bullet, font_size)
    return width


def validate_single_line_text_run_advances(
    runs: list[dict[str, Any]],
) -> None:
    """Validate the runs that remain after single-line bullet promotion."""
    content_runs, _bullet = _extract_text_bullet(runs)
    validate_text_run_advances(content_runs)


def _first_nonspace_run(runs: list[dict[str, Any]]) -> dict[str, Any] | None:
    for run in runs:
        if str(run.get('text', '')).strip():
            return run
    return None


def _strip_leading_chars_from_runs(
    runs: list[dict[str, Any]],
    char_count: int,
) -> list[dict[str, Any]]:
    stripped: list[dict[str, Any]] = []
    remaining = char_count
    for run in runs:
        if run.get('_line_break'):
            if remaining == 0:
                stripped.append(run)
            continue
        text = str(run.get('text', ''))
        if remaining >= len(text):
            remaining -= len(text)
            continue
        if remaining > 0:
            text = text[remaining:]
            remaining = 0
        if text:
            stripped.append({**run, 'text': text})
    return stripped


def _take_leading_chars_from_runs(
    runs: list[dict[str, Any]],
    char_count: int,
) -> list[dict[str, Any]]:
    taken: list[dict[str, Any]] = []
    remaining = char_count
    for run in runs:
        if remaining <= 0:
            break
        text = str(run.get('text', ''))
        if remaining >= len(text):
            prefix = text
            remaining -= len(text)
        else:
            prefix = text[:remaining]
            remaining = 0
        if prefix:
            taken.append({**run, 'text': prefix})
    return taken


def _extract_text_bullet(
    runs: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Convert a leading text bullet marker into paragraph metadata."""
    from .elements_path_shapes import _INLINE_FORMULA_KEY
    from .elements_path_shapes import _TEXT_BULLET_MARKERS
    from .elements_path_shapes import _TEXT_BULLET_RE
    if any('_inline_dx' in run for run in runs):
        # Keep positioned markers literal so bullet normalization cannot
        # discard or relocate an authored displacement.
        return runs, None
    first_nonspace = _first_nonspace_run(runs)
    if first_nonspace and (
        first_nonspace.get(_INLINE_FORMULA_KEY) is not None
        or first_nonspace.get(HYPERLINK_RID_KEY) is not None
    ):
        return runs, None
    full_text = ''.join(str(run.get('text', '')) for run in runs)
    match = _TEXT_BULLET_RE.match(full_text)
    if not match:
        return runs, None
    if not full_text[match.end():].strip():
        return runs, None

    marker = match.group('marker')
    marker_run = _first_nonspace_run(runs) or {}
    prefix_runs = _take_leading_chars_from_runs(runs, match.end())
    replacement_prefix = _TEXT_BULLET_MARKERS.get(marker, marker) + (match.group('space') or ' ')
    replacement_runs = [{**marker_run, 'text': replacement_prefix}] if marker_run else []
    bullet = {
        'char': _TEXT_BULLET_MARKERS.get(marker, marker),
        'fill': marker_run.get('fill'),
        'fill_raw': marker_run.get('fill_raw'),
        'opacity': marker_run.get('opacity'),
        'source_prefix_width_px': _estimate_text_runs_width(prefix_runs, include_headroom=False),
        'margin_px': max(
            _estimate_text_runs_width(replacement_runs, include_headroom=False),
            8.0,
        ),
    }
    stripped = _strip_leading_chars_from_runs(runs, match.end())
    return (stripped or runs), bullet


def _bullet_margin_px(bullet: dict[str, Any], font_size: float) -> float:
    try:
        return float(bullet.get('margin_px', 0.0))
    except (TypeError, ValueError):
        return max(font_size * 0.95, 12.0)


def _bullet_indent_px(bullet: dict[str, Any], font_size: float) -> float:
    return -_bullet_margin_px(bullet, font_size)


def _build_bullet_xml(
    bullet: dict[str, Any] | None,
    ctx: ConvertContext | None,
) -> str:
    if not bullet:
        return ''
    fill = bullet.get('fill')
    fill_raw = bullet.get('fill_raw')
    color, color_alpha = parse_svg_color(
        fill_raw if isinstance(fill_raw, str) else ''
    )
    if color is None and isinstance(fill, str):
        color = parse_hex_color(fill)
    if color:
        opacity = combine_opacity(bullet.get('opacity'), color_alpha)
        alpha_xml = (
            f'<a:alphaMod val="{quantize_ooxml_alpha(opacity)}"/>'
            if opacity is not None else ''
        )
        theme_spec = ctx.theme_color_spec if ctx is not None else None
        color_xml = (
            f'<a:buClr>{color_node_xml(color, theme_spec, "text", alpha_xml)}</a:buClr>'
        )
    else:
        color_xml = '<a:buClrTx/>'
    return (
        f'{color_xml}<a:buSzTx/><a:buFontTx/>'
        f'<a:buChar char="{_xml_escape(str(bullet.get("char", "•")))}"/>'
    )


def _paragraph_pr_xml(
    *,
    algn: str,
    font_size: float,
    body_xml: str = '',
    bullet: dict[str, Any] | None = None,
    ctx: ConvertContext | None = None,
    rtl: bool = False,
) -> str:
    attrs = f'algn="{algn}"'
    if rtl:
        attrs += ' rtl="1"'
    if bullet:
        margin = px_to_emu(_bullet_margin_px(bullet, font_size))
        indent = px_to_emu(_bullet_indent_px(bullet, font_size))
        attrs += f' marL="{margin}" indent="{indent}"'
    return f'<a:pPr {attrs}>{body_xml}{_build_bullet_xml(bullet, ctx)}</a:pPr>'


def _estimate_bullet_line_width(
    runs: list[dict[str, Any]],
    default_fonts: dict[str, str],
    ctx: ConvertContext,
) -> float:
    from .elements_text_runs import _coalesce_text_runs
    line_runs, bullet = _extract_text_bullet(runs)
    line_runs = _coalesce_text_runs(line_runs, default_fonts, ctx)
    width = _estimate_text_runs_width(line_runs, include_headroom=False)
    if bullet:
        fs_px = float(line_runs[0].get('font_size', 16)) if line_runs else 16.0
        width += _bullet_margin_px(bullet, fs_px)
    return width


def _textbox_padding(font_size: float) -> float:
    """Return small text-frame slack without visibly lengthening the box."""
    from .elements_path_shapes import _TEXTBOX_PADDING_MAX_PX
    from .elements_path_shapes import _TEXTBOX_PADDING_MIN_PX
    from .elements_path_shapes import _TEXTBOX_PADDING_RATIO
    return max(
        _TEXTBOX_PADDING_MIN_PX,
        min(_TEXTBOX_PADDING_MAX_PX, font_size * _TEXTBOX_PADDING_RATIO),
    )


def drawingml_text_frame_width_emu(
    text_width: float,
    font_size: float,
) -> int:
    """Return the exact horizontal extent used by a generated text frame."""
    return px_to_emu(text_width + _textbox_padding(font_size) * 2)


def _text_opacity_ratio(value: str | None) -> float:
    """Parse a text opacity component and clamp it to the SVG ``0..1`` range."""
    if value is None:
        return 1.0
    return parse_project_opacity(value)
