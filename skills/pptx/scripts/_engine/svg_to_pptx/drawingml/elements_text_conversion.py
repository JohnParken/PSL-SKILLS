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



# Responsibility: elements_text_conversion; adapted from the original module.

def convert_text(elem: ET.Element, ctx: ConvertContext) -> ShapeResult | None:
    """Convert SVG <text> to DrawingML text shape with multi-run support."""
    from .elements_path_shapes import _INLINE_FORMULA_KEY
    from .elements_text_runs import _build_run_xml
    from .elements_text_runs import _build_text_runs
    from .elements_text_metrics import _bullet_margin_px
    from .elements_shape_xml import _claim_element_shape_id
    from .elements_text_runs import _coalesce_text_runs
    from .elements_text_runs import _collect_tspan_runs
    from .elements_text_metrics import _estimate_bullet_line_width
    from .elements_text_metrics import _estimate_text_runs_width
    from .elements_text_metrics import _extract_text_bullet
    from .elements_text_runs import _inline_formula_preview_run
    from .elements_text_metrics import _native_text_line_frame_height
    from .elements_text_metrics import _normalize_text_run_whitespace
    from .elements_text_metrics import _paragraph_pr_xml
    from .elements_preset_geometry import _parse_preset_geometry_metadata
    from .elements_text_runs import _text_axis_transform
    from .elements_text_metrics import _text_line_vertical_extent
    from .elements_text_metrics import _text_opacity_ratio
    from .elements_text_metrics import _textbox_padding
    from .elements_text_metrics import drawingml_text_frame_width_emu
    from .elements_shape_xml import shape_display_name
    from .elements_text_metrics import validate_text_run_advances
    raw_x = svg_length_x(elem.get('x'), ctx)
    raw_y = svg_length_y(elem.get('y'), ctx)
    use_axis_transform, reflect_x, reflect_y = _text_axis_transform(ctx)
    if use_axis_transform:
        x, y = transform_point(ctx.transform_matrix, raw_x, raw_y)
    else:
        x = ctx_x(raw_x, ctx)
        y = ctx_y(raw_y, ctx)
    resolved_font_size = ctx.text_font_sizes.get(id(elem))
    font_size = (
        resolved_font_size * ctx.scale_y
        if resolved_font_size is not None
        else parse_svg_length(
            _get_attr(elem, 'font-size', ctx),
            16,
            font_size=16,
        ) * ctx.scale_y
    )
    font_weight = parse_project_font_weight(
        _get_attr(elem, 'font-weight', ctx) or '400'
    ).canonical
    font_family_str = _get_attr(elem, 'font-family', ctx) or ''
    text_anchor = parse_project_text_anchor(
        _get_attr(elem, 'text-anchor', ctx) or 'start'
    ).canonical
    if reflect_x:
        text_anchor = {
            'start': 'end',
            'end': 'start',
        }.get(text_anchor, text_anchor)
    fill_raw = _get_attr(elem, 'fill', ctx) or '#000000'
    fill_color = parse_hex_color(fill_raw) or '000000'
    opacity = get_fill_opacity(elem, ctx)
    object_opacity = get_element_opacity(elem, ctx)
    object_opacity = 1.0 if object_opacity is None else object_opacity
    fill_opacity = _text_opacity_ratio(_get_attr(elem, 'fill-opacity', ctx))
    stroke_raw = _get_attr(elem, 'stroke', ctx) or ''
    stroke_width = svg_length_size(_get_attr(elem, 'stroke-width', ctx), ctx, 1.0)
    stroke_opacity = get_stroke_opacity(elem, ctx)
    stroke_opacity_value = _text_opacity_ratio(_get_attr(elem, 'stroke-opacity', ctx))
    font_style = parse_project_font_style(
        _get_attr(elem, 'font-style', ctx) or 'normal'
    ).canonical
    text_decoration = parse_project_text_decoration(
        _get_attr(elem, 'text-decoration', ctx) or 'none'
    ).canonical
    raw_letter_spacing = _get_attr(elem, 'letter-spacing', ctx)
    resolved_letter_spacing = ctx.text_letter_spacings.get(id(elem))
    if resolved_letter_spacing is not None:
        letter_spacing_px = resolved_letter_spacing * ctx.scale_x
    elif raw_letter_spacing is not None:
        letter_spacing_px = parse_project_letter_spacing(
            raw_letter_spacing,
            font_size=font_size,
            scale_x=ctx.scale_x or 1.0,
        ).value
    else:
        letter_spacing_px = 0.0

    fonts = parse_font_family(font_family_str, ctx.primary_language)

    parent_attrs: dict[str, Any] = {
        'fill': fill_color,
        'fill_raw': fill_raw,
        'font_weight': font_weight,
        'font_size': font_size,
        'font_family': font_family_str,
        'font_style': font_style,
        'text_decoration': text_decoration,
        'letter_spacing': letter_spacing_px,
        'baseline_shift': 0,
        '_scale_x': ctx.scale_x or 1.0,
        '_object_opacity': object_opacity,
        '_fill_opacity': fill_opacity,
        '_stroke_opacity': stroke_opacity_value,
        'opacity': opacity,
        'stroke_raw': stroke_raw,
        'stroke_width': stroke_width,
        'stroke_opacity': stroke_opacity,
    }

    # Single-frame modes annotate conservative dy-stacked text with one base
    # line height. Semantic paragraphs become <a:p>; authored visual rows
    # either become <a:br/> (preserve) or join for wrapping (reflow).
    line_height_attr = (
        elem.get('data-paragraph-line-height')
        if ctx.text_flow != TEXT_FLOW_SPLIT
        else None
    )
    line_height_px = _f(line_height_attr) if line_height_attr is not None else None
    paragraph_runs: list[list[dict[str, Any]]] | None = None
    paragraph_space_before: list[float] = []
    paragraph_bullets: list[dict[str, Any] | None] = []
    # Per-tspan widths (visual lines as the deck author drew them) regardless
    # of how many merge into one <a:p>; used to size the textbox so PowerPoint
    # has room to wrap text to the SVG's original line widths.
    visual_line_widths: list[float] = []
    visual_line_runs: list[list[dict[str, Any]]] = []
    if line_height_px is not None and line_height_px > 0:
        xml_space = resolve_project_xml_space(elem)
        paragraph_runs = []
        for child in elem:
            if child.tag != f'{{{SVG_NS}}}tspan':
                continue
            line_runs = _collect_tspan_runs(
                child,
                parent_attrs,
                ctx,
                xml_space,
            )
            line_runs = _normalize_text_run_whitespace(line_runs)
            if not line_runs:
                continue
            visual_line_runs.append(line_runs)
            visual_line_widths.append(
                _estimate_bullet_line_width(line_runs, fonts, ctx)
            )
            soft_break = child.get('data-paragraph-soft-break') == '1'
            line_break = child.get('data-paragraph-line-break') == '1'
            if line_break and paragraph_runs:
                paragraph_runs[-1].append({'_line_break': True})
                paragraph_runs[-1].extend(line_runs)
            elif soft_break and paragraph_runs:
                # Append to the previous paragraph. A Latin line-wrap needs a
                # space to keep two words apart (SVG used a dy break, not
                # punctuation); CJK wraps mid-sentence with no inter-character
                # space, so a joining space there is a spurious artifact.
                prev = paragraph_runs[-1]
                prev_text = prev[-1]['text'] if prev else ''
                next_text = line_runs[0]['text']
                boundary_is_cjk = (
                    (prev_text and is_cjk_char(prev_text[-1]))
                    or (next_text and is_cjk_char(next_text[0]))
                )
                if prev and not prev_text.endswith(' ') \
                        and not next_text.startswith(' ') \
                        and not boundary_is_cjk:
                    joining_space = {
                        **prev[-1],
                        'text': ' ',
                        'letter_spacing': 0.0,
                    }
                    joining_space.pop(_INLINE_FORMULA_KEY, None)
                    joining_space.pop(HYPERLINK_RID_KEY, None)
                    joining_space.pop(HYPERLINK_ACTION_KEY, None)
                    prev.append(joining_space)
                prev.extend(line_runs)
            else:
                paragraph_runs.append(line_runs)
                sb_attr = child.get('data-paragraph-space-before')
                paragraph_space_before.append(_f(sb_attr) if sb_attr else 0.0)
        if not paragraph_runs:
            paragraph_runs = None
            paragraph_space_before = []
            visual_line_widths = []
            visual_line_runs = []
        else:
            stripped_paragraphs: list[list[dict[str, Any]]] = []
            for line_runs in paragraph_runs:
                stripped_runs, bullet = _extract_text_bullet(line_runs)
                stripped_paragraphs.append(
                    _coalesce_text_runs(stripped_runs, fonts, ctx)
                )
                paragraph_bullets.append(bullet)
            paragraph_runs = stripped_paragraphs

    if paragraph_runs is not None:
        runs = [r for line in paragraph_runs for r in line]
    else:
        runs = _build_text_runs(elem, parent_attrs, ctx)
        runs, single_bullet = _extract_text_bullet(runs)
        runs = _coalesce_text_runs(runs, fonts, ctx)

    is_placeholder_carrier = (
        (elem.get('data-pptx-carrier') or '').strip().lower() == 'true'
    )
    full_text = ''.join(str(r.get('text', '')) for r in runs) if runs else ''
    if not full_text.strip():
        if not is_placeholder_carrier:
            return None
        # A declared carrier must compile to one native text shape even when its
        # authored visual is blank. U+200B is invisible but survives DrawingML.
        runs = [{**parent_attrs, 'text': '\u200b'}]
        paragraph_runs = None
        paragraph_space_before = []
        paragraph_bullets = []
        visual_line_widths = []
        visual_line_runs = []
        single_bullet = None

    # Estimate text dimensions
    if paragraph_runs is not None:
        # Use the widest authored visual line, not a reflow-joined paragraph.
        text_width = max(visual_line_widths) if visual_line_widths else 0.0
        line_extents = [
            _text_line_vertical_extent(line, font_size)
            for line in visual_line_runs
        ]
        text_height = sum(paragraph_space_before)
        for ascent, descent, has_formula in line_extents[:-1]:
            line_advance = line_height_px
            if has_formula:
                line_advance = max(
                    line_advance,
                    _native_text_line_frame_height(
                        ascent,
                        descent,
                        font_size,
                    ),
                )
            text_height += line_advance
        first_line_ascent = line_extents[0][0]
        last_ascent, last_descent, last_has_formula = line_extents[-1]
        last_line_height = font_size * 1.5
        if last_has_formula:
            last_line_height = max(
                last_line_height,
                _native_text_line_frame_height(
                    last_ascent,
                    last_descent,
                    font_size,
                ),
            )
        text_height += last_line_height
    else:
        text_width = _estimate_text_runs_width(runs)
        if single_bullet:
            fs_px = float(runs[0].get('font_size', font_size)) if runs else font_size
            text_width += _bullet_margin_px(single_bullet, fs_px)
        first_line_ascent, line_descent, has_formula = (
            _text_line_vertical_extent(runs, font_size)
        )
        text_height = font_size * 1.5
        if has_formula:
            text_height = max(
                text_height,
                _native_text_line_frame_height(
                    first_line_ascent,
                    line_descent,
                    font_size,
                ),
            )
    # Legacy imported frames retain their original baseline/inset convention.
    # Native math and shifted runs retain their own vertical-extent model.
    # Ordinary text instead needs PowerPoint's first-line baseline, which is
    # distinct from the glyph bounds used above and by SVG quality checks.
    metrics_baseline = (
        elem.get('data-pptx-frame') is None
        or elem.get('data-pptx-text-baseline') == 'metrics-v1'
    )
    if metrics_baseline and not any(
        run.get(_INLINE_FORMULA_KEY) is not None or run.get('baseline_shift')
        for run in runs
    ):
        first_line_runs = runs
        last_line_runs = runs
        if paragraph_runs is not None:
            # Paragraph extraction replaces a typed marker with a native bullet
            # that inherits the body font; its discarded run is not line ink.
            first_line_runs, _bullet = _extract_text_bullet(visual_line_runs[0])
            last_line_runs = paragraph_runs[-1]
            for index, run in enumerate(last_line_runs):
                if run.get('_line_break'):
                    last_line_runs = paragraph_runs[-1][index + 1:]
        first_line_ascent = drawingml_text_baseline_offset(
            first_line_runs,
            fonts,
            default_size=font_size,
            line_spacing_px=line_height_px if paragraph_runs is not None else None,
            language=ctx.primary_language,
        )
        largest_last_size = max(
            (font_px_to_hpt(run.get('font_size', font_size)) / FONT_PX_TO_HUNDREDTHS_PT
             for run in last_line_runs if run.get('text')),
            default=font_size,
        )
        text_height += max(0.0, (largest_last_size - font_size) * 1.5)
        # An unformatted break can inherit the presentation's default size
        # instead of the preceding run, changing first-line ascent and advance.
        for paragraph in paragraph_runs or []:
            previous_run = None
            for run in paragraph:
                if run.get('_line_break') and previous_run is not None:
                    run['_line_break_run'] = previous_run
                elif not run.get('_line_break'):
                    previous_run = run
    padding = _textbox_padding(font_size)

    # Adjust position based on text-anchor. This first box follows the visible
    # glyph baseline and remains useful for reconstructing imported text-body
    # insets when data-pptx-frame supplies the owning PowerPoint shape frame.
    if text_anchor == 'middle':
        box_x = x - text_width / 2 - padding
    elif text_anchor == 'end':
        box_x = x - text_width - padding
    else:
        box_x = x - padding

    box_y = y - first_line_ascent
    box_w = text_width + padding * 2
    box_h = text_height + padding
    if reflect_y:
        box_y = 2 * y - box_y - box_h

    visual_box_x = box_x
    visual_box_y = box_y
    exact_text_frame = None
    exact_text_insets: tuple[float, float, float] | None = None
    if elem.get('data-pptx-frame') is not None:
        _preset, _guides, exact_text_frame = _parse_preset_geometry_metadata(elem)
        if exact_text_frame is None:
            raise ValueError('data-pptx-frame did not resolve to a text frame')
        raw_frame_x, raw_frame_y, raw_frame_w, raw_frame_h = exact_text_frame
        if use_axis_transform:
            frame_x_1, frame_y_1 = transform_point(
                ctx.transform_matrix,
                raw_frame_x,
                raw_frame_y,
            )
            frame_x_2, frame_y_2 = transform_point(
                ctx.transform_matrix,
                raw_frame_x + raw_frame_w,
                raw_frame_y + raw_frame_h,
            )
        else:
            frame_x_1 = ctx_x(raw_frame_x, ctx)
            frame_x_2 = ctx_x(raw_frame_x + raw_frame_w, ctx)
            frame_y_1 = ctx_y(raw_frame_y, ctx)
            frame_y_2 = ctx_y(raw_frame_y + raw_frame_h, ctx)
        box_x = min(frame_x_1, frame_x_2)
        box_y = min(frame_y_1, frame_y_2)
        box_w = abs(frame_x_2 - frame_x_1)
        box_h = abs(frame_y_2 - frame_y_1)
        top_inset = visual_box_y - box_y
        if text_anchor == 'start':
            left_inset = x - box_x
            right_inset = 0.0
        elif text_anchor == 'end':
            left_inset = 0.0
            right_inset = box_x + box_w - x
        else:
            center_delta = x - (box_x + box_w / 2)
            left_inset = max(0.0, center_delta * 2)
            right_inset = max(0.0, -center_delta * 2)
        exact_text_insets = (left_inset, top_inset, right_inset)

    text_transform = elem.get('transform', '')
    text_operations = (
        parse_transform_operations(text_transform)
        if text_transform else ()
    )
    translate_only = bool(text_operations) and all(
        name == 'translate' for name, _args in text_operations
    )
    rotate_only = (
        len(text_operations) == 1
        and text_operations[0][0] == 'rotate'
    )
    if text_operations and not (translate_only or rotate_only):
        raise ValueError(
            'Text transform must be a translate-only list or one rotate operation'
        )
    if translate_only and not ctx.use_transform_matrix:
        a, b, c, d, e, f = parse_transform_matrix(text_transform)
        # A pure-translate transform on a text element (hand-authored, or written
        # by a live-preview move) was otherwise ignored here, drifting the text.
        # Absorb the translation into the frame position.
        if (
            abs(a - 1.0) < 1e-9 and abs(b) < 1e-9
            and abs(c) < 1e-9 and abs(d - 1.0) < 1e-9
        ):
            sx = ctx.scale_x or 1.0
            sy = ctx.scale_y or 1.0
            raw_box_x = (box_x - ctx.translate_x) / sx
            raw_box_y = (box_y - ctx.translate_y) / sy
            box_x = ctx.translate_x + sx * (a * raw_box_x + e)
            box_y = ctx.translate_y + sy * (d * raw_box_y + f)
    elif translate_only and use_axis_transform:
        _a, _b, _c, _d, e, f = parse_transform_matrix(text_transform)
        matrix_a, matrix_b, matrix_c, matrix_d, _matrix_e, _matrix_f = (
            ctx.transform_matrix
        )
        box_x += matrix_a * e + matrix_c * f
        box_y += matrix_b * e + matrix_d * f

    # Text rotation. SVG's rotate(angle [cx cy]) rotates around (cx, cy), but
    # DrawingML's <a:xfrm rot="..."> rotates the shape around its own center.
    # When a pivot is given (and differs from the box center), translate the
    # box so its center lands where SVG would place the rotated visual center —
    # otherwise rotated y-axis labels etc. drift to the wrong location.
    text_rot = 0
    if rotate_only:
        rotate_args = text_operations[0][1]
        angle_deg = rotate_args[0]
        if reflect_x != reflect_y:
            angle_deg = -angle_deg
        text_rot = int(angle_deg * ANGLE_UNIT)
        if len(rotate_args) == 3:
            if use_axis_transform:
                pivot_x, pivot_y = transform_point(
                    ctx.transform_matrix,
                    rotate_args[1],
                    rotate_args[2],
                )
            else:
                pivot_x = ctx_x(rotate_args[1], ctx)
                pivot_y = ctx_y(rotate_args[2], ctx)
            cx_box = box_x + box_w / 2
            cy_box = box_y + box_h / 2
            rad = math.radians(angle_deg)
            dx = cx_box - pivot_x
            dy = cy_box - pivot_y
            new_cx = pivot_x + dx * math.cos(rad) - dy * math.sin(rad)
            new_cy = pivot_y + dx * math.sin(rad) + dy * math.cos(rad)
            box_x = new_cx - box_w / 2
            box_y = new_cy - box_h / 2

    # Alignment
    algn_map = {'start': 'l', 'middle': 'ctr', 'end': 'r'}
    algn = algn_map.get(text_anchor, 'l')

    # Shadow effect
    shape_effect_xml = ''
    text_effect_xml = ''
    filt_id = get_effective_filter_id(elem, ctx)
    if filt_id and filt_id in ctx.defs:
        filter_elem = ctx.defs[filt_id]
        effect_kind = classify_filter_effect(filter_elem)
        if effect_kind == 'glow':
            text_effect_xml = build_effect_xml(
                filter_elem,
                get_element_opacity(elem, ctx),
            )
        elif effect_kind == 'shadow':
            shape_effect_xml = build_effect_xml(
                filter_elem,
                get_element_opacity(elem, ctx),
            )

    shape_id = _claim_element_shape_id(elem, ctx)
    rot_attr = f' rot="{text_rot}"' if text_rot else ''

    def _paragraphs_xml(formula_previews: bool) -> str:
        # The MCE Fallback copy writes each inline formula as its SVG preview
        # text so a reader without a14 math still shows the formula.
        def _runs_xml(line_runs: list[dict[str, Any]]) -> str:
            return '\n'.join(
                _build_run_xml(
                    _inline_formula_preview_run(r) if formula_previews else r,
                    fonts,
                    ctx,
                    text_effect_xml,
                )
                for r in line_runs
            )

        if paragraph_runs is not None:
            # SVG dy(px) -> hundredths-of-a-point: dy_pt = dy_px * 0.75, then x100.
            line_spc_val = round(line_height_px * FONT_PX_TO_HUNDREDTHS_PT)
            ln_spc_xml = f'<a:lnSpc><a:spcPts val="{line_spc_val}"/></a:lnSpc>'
            paragraph_xml_chunks = []
            for line, extra_px, bullet in zip(paragraph_runs, paragraph_space_before, paragraph_bullets):
                spc_bef_xml = ''
                if extra_px > 0:
                    spc_bef_val = round(extra_px * FONT_PX_TO_HUNDREDTHS_PT)
                    spc_bef_xml = f'<a:spcBef><a:spcPts val="{spc_bef_val}"/></a:spcBef>'
                runs_inner = _runs_xml(line)
                first_text_run = next(
                    (run for run in line if not run.get('_line_break')),
                    None,
                )
                effective_line_spacing = (
                    ''
                    if any(run.get(_INLINE_FORMULA_KEY) is not None for run in line)
                    else ln_spc_xml
                )
                p_pr_xml = _paragraph_pr_xml(
                    algn=algn,
                    font_size=(
                        float(first_text_run.get('font_size', font_size))
                        if first_text_run is not None
                        else font_size
                    ),
                    body_xml=f'{effective_line_spacing}{spc_bef_xml}',
                    bullet=bullet,
                    ctx=ctx,
                    rtl=text_uses_rtl(
                        ''.join(str(run.get('text', '')) for run in line),
                        ctx.primary_language,
                    ),
                )
                paragraph_xml_chunks.append(
                    f'<a:p>\n{p_pr_xml}\n{runs_inner}\n</a:p>'
                )
            return '\n'.join(paragraph_xml_chunks)

        runs_xml = _runs_xml(runs)
        p_pr_xml = _paragraph_pr_xml(
            algn=algn,
            font_size=float(runs[0].get('font_size', font_size)) if runs else font_size,
            bullet=single_bullet,
            ctx=ctx,
            rtl=text_uses_rtl(full_text, ctx.primary_language),
        )
        return f'<a:p>\n{p_pr_xml}\n{runs_xml}\n</a:p>'

    paragraphs_xml = _paragraphs_xml(False)

    off_x = px_to_emu(box_x)
    off_y = px_to_emu(box_y)
    ext_cx = (
        px_to_emu(box_w)
        if exact_text_frame is not None
        else drawingml_text_frame_width_emu(text_width, font_size)
    )
    ext_cy = px_to_emu(box_h)
    if ext_cx < 1 or ext_cy < 1:
        raise ValueError(
            'negative letter-spacing produces a non-positive DrawingML '
            f'text-frame extent (cx={ext_cx}, cy={ext_cy})'
        )
    validate_ooxml_xfrm(off_x, off_y, ext_cx, ext_cy)
    validate_text_run_advances(runs)

    # Imported text carriers with data-pptx-frame retain the source shape frame
    # instead of shrinking to glyph bounds. Reconstruct insets from the SVG
    # anchor/baseline so the visible text stays at its imported position while
    # remaining ordinary editable DrawingML text.
    if exact_text_frame is not None:
        if exact_text_insets is None:
            raise ValueError('data-pptx-frame text insets were not resolved')
        left_inset, top_inset, right_inset = exact_text_insets
        exact_frame_wrap = (
            'none' if ctx.text_flow == TEXT_FLOW_PRESERVE else 'square'
        )
        body_pr_xml = (
            f'<a:bodyPr wrap="{exact_frame_wrap}" '
            f'lIns="{px_to_emu(left_inset)}" '
            f'tIns="{px_to_emu(top_inset)}" '
            f'rIns="{px_to_emu(right_inset)}" bIns="0" '
            'anchor="t" anchorCtr="0">\n<a:noAutofit/>\n</a:bodyPr>'
        )
    # Preserve mode keeps authored <a:br/> boundaries and lets an ordinary
    # generated text box follow later manual edits, such as deleting a break.
    # Reflow mode keeps the source width fixed as its wrapping constraint.
    # Exact imported frames above and structured placeholder carriers remain
    # fixed regardless of text-flow mode.
    elif paragraph_runs is not None:
        paragraph_wrap = (
            'none' if ctx.text_flow == TEXT_FLOW_PRESERVE else 'square'
        )
        paragraph_autofit = (
            '<a:spAutoFit/>'
            if (
                ctx.text_flow == TEXT_FLOW_PRESERVE
                and not is_placeholder_carrier
            )
            else '<a:noAutofit/>'
        )
        body_pr_xml = (
            f'<a:bodyPr wrap="{paragraph_wrap}" '
            'lIns="0" tIns="0" rIns="0" bIns="0" '
            f'anchor="t" anchorCtr="0">\n{paragraph_autofit}\n</a:bodyPr>'
        )
    else:
        body_pr_xml = (
            '<a:bodyPr wrap="none" lIns="0" tIns="0" rIns="0" bIns="0" '
            'anchor="t" anchorCtr="0">\n<a:spAutoFit/>\n</a:bodyPr>'
        )

    def _shape_xml(sp_id: int, sp_paragraphs_xml: str) -> str:
        return f'''<p:sp>
<p:nvSpPr>
<p:cNvPr id="{sp_id}" name="{_xml_escape(shape_display_name(elem, f'TextBox {sp_id}'))}"/>
<p:cNvSpPr txBox="1"/><p:nvPr/>
</p:nvSpPr>
<p:spPr>
<a:xfrm{rot_attr}><a:off x="{off_x}" y="{off_y}"/>
<a:ext cx="{ext_cx}" cy="{ext_cy}"/></a:xfrm>
<a:prstGeom prst="rect"><a:avLst/></a:prstGeom>
<a:noFill/>
<a:ln><a:noFill/></a:ln>
{shape_effect_xml}
</p:spPr>
<p:txBody>
{body_pr_xml}
<a:lstStyle/>
{sp_paragraphs_xml}
</p:txBody>
</p:sp>'''

    shape_xml = _shape_xml(shape_id, paragraphs_xml)
    if any(run.get(_INLINE_FORMULA_KEY) is not None for run in runs):
        from ..native_objects.inline_formula import wrap_inline_formula_shape
        shape_xml = wrap_inline_formula_shape(
            shape_xml,
            _shape_xml(ctx.next_id(), _paragraphs_xml(True)),
        )
    return ShapeResult(
        xml=shape_xml,
        bounds_emu=(off_x, off_y, off_x + ext_cx, off_y + ext_cy),
    )


# ---------------------------------------------------------------------------
# clipPath support (image clipping)
# ---------------------------------------------------------------------------
