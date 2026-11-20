"""SVG element converters: rect, circle, line, path, polygon, polyline, text, image, ellipse."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any
from xml.etree import ElementTree as ET

from pptx_shapes import validate_ooxml_xfrm

from .context import (
    TEXT_FLOW_PRESERVE,
    TEXT_FLOW_SPLIT,
    ConvertContext,
    ShapeResult,
)
from .hyperlinks import HYPERLINK_ACTION_KEY, HYPERLINK_RID_KEY
from .styles import (
    build_effect_xml,
    classify_filter_effect,
    get_element_opacity,
    get_fill_opacity,
    get_stroke_opacity,
)
from .text_baseline import drawingml_text_baseline_offset
from .text_properties import (
    parse_project_font_style,
    parse_project_font_weight,
    parse_project_letter_spacing,
    parse_project_text_anchor,
    parse_project_text_decoration,
    resolve_project_xml_space,
)
from .utils import (
    ANGLE_UNIT,
    FONT_PX_TO_HUNDREDTHS_PT,
    SVG_NS,
    _f,
    _get_attr,
    _xml_escape,
    ctx_x,
    ctx_y,
    font_px_to_hpt,
    get_effective_filter_id,
    is_cjk_char,
    parse_font_family,
    parse_hex_color,
    parse_svg_length,
    parse_transform_matrix,
    parse_transform_operations,
    px_to_emu,
    svg_length_size,
    svg_length_x,
    svg_length_y,
    text_uses_rtl,
    transform_point,
)


@dataclass(frozen=True)
class _TextDeps:
    """Lazily resolved helpers from .elements to preserve facade bindings."""

    inline_formula_key: str
    build_run_xml: Any
    build_text_runs: Any
    bullet_margin_px: Any
    claim_element_shape_id: Any
    coalesce_text_runs: Any
    collect_tspan_runs: Any
    estimate_bullet_line_width: Any
    estimate_text_runs_width: Any
    extract_text_bullet: Any
    inline_formula_preview_run: Any
    native_text_line_frame_height: Any
    normalize_text_run_whitespace: Any
    paragraph_pr_xml: Any
    parse_preset_geometry_metadata: Any
    text_axis_transform: Any
    text_line_vertical_extent: Any
    text_opacity_ratio: Any
    textbox_padding: Any
    drawingml_text_frame_width_emu: Any
    shape_display_name: Any
    validate_text_run_advances: Any


@dataclass
class _ResolvedTextStyle:
    """Resolved base position, flip flags, font specs, and parent attributes."""

    x: float
    y: float
    use_axis_transform: bool
    reflect_x: bool
    reflect_y: bool
    font_size: float
    text_anchor: str
    fonts: Any
    parent_attrs: dict[str, Any]


@dataclass
class _CollectedRuns:
    """Paragraph and visual-line runs assembled from SVG <text>/<tspan> children."""

    runs: list[dict[str, Any]]
    paragraph_runs: list[list[dict[str, Any]]] | None
    paragraph_space_before: list[float]
    paragraph_bullets: list[dict[str, Any] | None]
    visual_line_widths: list[float]
    visual_line_runs: list[list[dict[str, Any]]]
    single_bullet: dict[str, Any] | None
    line_height_px: float | None
    is_placeholder_carrier: bool
    full_text: str


@dataclass
class _TextBoxGeometry:
    """Computed DrawingML text-box coordinates, dimensions, and exact frame insets."""

    box_x: float
    box_y: float
    box_w: float
    box_h: float
    text_width: float
    exact_text_frame: Any
    exact_text_insets: tuple[float, float, float] | None
    text_rot: int


def _load_text_deps() -> _TextDeps:
    """Import .elements helpers lazily while avoiding circular import at load time."""
    from .elements import (
        _INLINE_FORMULA_KEY,
        _build_run_xml,
        _build_text_runs,
        _bullet_margin_px,
        _claim_element_shape_id,
        _coalesce_text_runs,
        _collect_tspan_runs,
        _estimate_bullet_line_width,
        _estimate_text_runs_width,
        _extract_text_bullet,
        _inline_formula_preview_run,
        _native_text_line_frame_height,
        _normalize_text_run_whitespace,
        _paragraph_pr_xml,
        _parse_preset_geometry_metadata,
        _text_axis_transform,
        _text_line_vertical_extent,
        _text_opacity_ratio,
        _textbox_padding,
        drawingml_text_frame_width_emu,
        shape_display_name,
        validate_text_run_advances,
    )

    return _TextDeps(
        inline_formula_key=_INLINE_FORMULA_KEY,
        build_run_xml=_build_run_xml,
        build_text_runs=_build_text_runs,
        bullet_margin_px=_bullet_margin_px,
        claim_element_shape_id=_claim_element_shape_id,
        coalesce_text_runs=_coalesce_text_runs,
        collect_tspan_runs=_collect_tspan_runs,
        estimate_bullet_line_width=_estimate_bullet_line_width,
        estimate_text_runs_width=_estimate_text_runs_width,
        extract_text_bullet=_extract_text_bullet,
        inline_formula_preview_run=_inline_formula_preview_run,
        native_text_line_frame_height=_native_text_line_frame_height,
        normalize_text_run_whitespace=_normalize_text_run_whitespace,
        paragraph_pr_xml=_paragraph_pr_xml,
        parse_preset_geometry_metadata=_parse_preset_geometry_metadata,
        text_axis_transform=_text_axis_transform,
        text_line_vertical_extent=_text_line_vertical_extent,
        text_opacity_ratio=_text_opacity_ratio,
        textbox_padding=_textbox_padding,
        drawingml_text_frame_width_emu=drawingml_text_frame_width_emu,
        shape_display_name=shape_display_name,
        validate_text_run_advances=validate_text_run_advances,
    )


def _resolve_text_style_and_position(
    elem: ET.Element,
    ctx: ConvertContext,
    deps: _TextDeps,
) -> _ResolvedTextStyle:
    """Resolve base SVG coordinates, axis reflection, font metrics, and inheritable run styles."""
    raw_x = svg_length_x(elem.get('x'), ctx)
    raw_y = svg_length_y(elem.get('y'), ctx)
    use_axis_transform, reflect_x, reflect_y = deps.text_axis_transform(ctx)
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
            _get_attr(elem, 'font-size', ctx), 16, font_size=16,
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
        text_anchor = {'start': 'end', 'end': 'start'}.get(text_anchor, text_anchor)
    fill_raw = _get_attr(elem, 'fill', ctx) or '#000000'
    fill_color = parse_hex_color(fill_raw) or '000000'
    opacity = get_fill_opacity(elem, ctx)
    object_opacity = get_element_opacity(elem, ctx)
    object_opacity = 1.0 if object_opacity is None else object_opacity
    fill_opacity = deps.text_opacity_ratio(_get_attr(elem, 'fill-opacity', ctx))
    stroke_raw = _get_attr(elem, 'stroke', ctx) or ''
    stroke_width = svg_length_size(_get_attr(elem, 'stroke-width', ctx), ctx, 1.0)
    stroke_opacity = get_stroke_opacity(elem, ctx)
    stroke_opacity_value = deps.text_opacity_ratio(_get_attr(elem, 'stroke-opacity', ctx))
    font_style = parse_project_font_style(
        _get_attr(elem, 'font-style', ctx) or 'normal'
    ).canonical
    text_decoration = parse_project_text_decoration(
        _get_attr(elem, 'text-decoration', ctx) or 'none'
    ).canonical
    raw_letter_spacing = _get_attr(elem, 'letter-spacing', ctx)
    resolved_ls = ctx.text_letter_spacings.get(id(elem))
    if resolved_ls is not None:
        letter_spacing_px = resolved_ls * ctx.scale_x
    elif raw_letter_spacing is not None:
        letter_spacing_px = parse_project_letter_spacing(
            raw_letter_spacing, font_size=font_size, scale_x=ctx.scale_x or 1.0,
        ).value
    else:
        letter_spacing_px = 0.0
    fonts = parse_font_family(font_family_str, ctx.primary_language)
    parent_attrs: dict[str, Any] = {
        'fill': fill_color, 'fill_raw': fill_raw, 'font_weight': font_weight,
        'font_size': font_size, 'font_family': font_family_str, 'font_style': font_style,
        'text_decoration': text_decoration, 'letter_spacing': letter_spacing_px,
        'baseline_shift': 0, '_scale_x': ctx.scale_x or 1.0,
        '_object_opacity': object_opacity, '_fill_opacity': fill_opacity,
        '_stroke_opacity': stroke_opacity_value, 'opacity': opacity,
        'stroke_raw': stroke_raw, 'stroke_width': stroke_width, 'stroke_opacity': stroke_opacity,
    }
    return _ResolvedTextStyle(
        x=x, y=y, use_axis_transform=use_axis_transform, reflect_x=reflect_x,
        reflect_y=reflect_y, font_size=font_size, text_anchor=text_anchor,
        fonts=fonts, parent_attrs=parent_attrs,
    )


def _collect_paragraph_and_visual_runs(
    elem: ET.Element,
    style: _ResolvedTextStyle,
    ctx: ConvertContext,
    deps: _TextDeps,
) -> _CollectedRuns | None:
    """Collect paragraph/visual runs from annotated <tspan> children or plain <text>."""
    line_height_attr = (
        elem.get('data-paragraph-line-height') if ctx.text_flow != TEXT_FLOW_SPLIT else None
    )
    line_height_px = _f(line_height_attr) if line_height_attr is not None else None
    paragraph_runs: list[list[dict[str, Any]]] | None = None
    paragraph_space_before: list[float] = []
    paragraph_bullets: list[dict[str, Any] | None] = []
    visual_line_widths: list[float] = []
    visual_line_runs: list[list[dict[str, Any]]] = []
    single_bullet: dict[str, Any] | None = None
    if line_height_px is not None and line_height_px > 0:
        xml_space = resolve_project_xml_space(elem)
        paragraph_runs = []
        for child in elem:
            if child.tag != f'{{{SVG_NS}}}tspan':
                continue
            line_runs = deps.collect_tspan_runs(child, style.parent_attrs, ctx, xml_space)
            line_runs = deps.normalize_text_run_whitespace(line_runs)
            if not line_runs:
                continue
            visual_line_runs.append(line_runs)
            visual_line_widths.append(deps.estimate_bullet_line_width(line_runs, style.fonts, ctx))
            soft_break = child.get('data-paragraph-soft-break') == '1'
            line_break = child.get('data-paragraph-line-break') == '1'
            if line_break and paragraph_runs:
                paragraph_runs[-1].append({'_line_break': True})
                paragraph_runs[-1].extend(line_runs)
            elif soft_break and paragraph_runs:
                prev = paragraph_runs[-1]
                prev_text = prev[-1]['text'] if prev else ''
                next_text = line_runs[0]['text']
                boundary_cjk = (
                    (prev_text and is_cjk_char(prev_text[-1]))
                    or (next_text and is_cjk_char(next_text[0]))
                )
                if prev and not prev_text.endswith(' ') and not next_text.startswith(' ') and not boundary_cjk:
                    joining_space = {**prev[-1], 'text': ' ', 'letter_spacing': 0.0}
                    joining_space.pop(deps.inline_formula_key, None)
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
                stripped_runs, bullet = deps.extract_text_bullet(line_runs)
                stripped_paragraphs.append(deps.coalesce_text_runs(stripped_runs, style.fonts, ctx))
                paragraph_bullets.append(bullet)
            paragraph_runs = stripped_paragraphs
    if paragraph_runs is not None:
        runs = [r for line in paragraph_runs for r in line]
    else:
        runs = deps.build_text_runs(elem, style.parent_attrs, ctx)
        runs, single_bullet = deps.extract_text_bullet(runs)
        runs = deps.coalesce_text_runs(runs, style.fonts, ctx)
    is_carrier = (elem.get('data-pptx-carrier') or '').strip().lower() == 'true'
    full_text = ''.join(str(r.get('text', '')) for r in runs) if runs else ''
    if not full_text.strip():
        if not is_carrier:
            return None
        # A declared carrier must compile to one native text shape even when blank.
        runs = [{**style.parent_attrs, 'text': '\u200b'}]
        paragraph_runs = None
        paragraph_space_before, paragraph_bullets = [], []
        visual_line_widths, visual_line_runs = [], []
        single_bullet = None
    return _CollectedRuns(
        runs=runs, paragraph_runs=paragraph_runs,
        paragraph_space_before=paragraph_space_before, paragraph_bullets=paragraph_bullets,
        visual_line_widths=visual_line_widths, visual_line_runs=visual_line_runs,
        single_bullet=single_bullet, line_height_px=line_height_px,
        is_placeholder_carrier=is_carrier, full_text=full_text,
    )


def _estimate_text_box_geometry(
    elem: ET.Element,
    style: _ResolvedTextStyle,
    collected: _CollectedRuns,
    ctx: ConvertContext,
    deps: _TextDeps,
) -> tuple[float, float, float, float, float, float]:
    """Compute initial textbox bounds (box_x, box_y, box_w, box_h, text_width, visual_box_y)."""
    font_size = style.font_size
    if collected.paragraph_runs is not None:
        text_width = max(collected.visual_line_widths) if collected.visual_line_widths else 0.0
        line_extents = [
            deps.text_line_vertical_extent(line, font_size) for line in collected.visual_line_runs
        ]
        text_height = sum(collected.paragraph_space_before)
        for ascent, descent, has_formula in line_extents[:-1]:
            advance = collected.line_height_px
            if has_formula:
                advance = max(advance, deps.native_text_line_frame_height(ascent, descent, font_size))
            text_height += advance
        first_line_ascent = line_extents[0][0]
        last_ascent, last_descent, last_has_formula = line_extents[-1]
        last_height = font_size * 1.5
        if last_has_formula:
            last_height = max(
                last_height, deps.native_text_line_frame_height(last_ascent, last_descent, font_size))
        text_height += last_height
    else:
        text_width = deps.estimate_text_runs_width(collected.runs)
        if collected.single_bullet:
            fs_px = float(collected.runs[0].get('font_size', font_size)) if collected.runs else font_size
            text_width += deps.bullet_margin_px(collected.single_bullet, fs_px)
        first_line_ascent, line_descent, has_formula = deps.text_line_vertical_extent(
            collected.runs, font_size)
        text_height = font_size * 1.5
        if has_formula:
            text_height = max(
                text_height, deps.native_text_line_frame_height(first_line_ascent, line_descent, font_size))
    metrics_baseline = (
        elem.get('data-pptx-frame') is None or elem.get('data-pptx-text-baseline') == 'metrics-v1'
    )
    if metrics_baseline and not any(
        run.get(deps.inline_formula_key) is not None or run.get('baseline_shift')
        for run in collected.runs
    ):
        first_line_runs = collected.runs
        last_line_runs = collected.runs
        if collected.paragraph_runs is not None:
            first_line_runs, _bullet = deps.extract_text_bullet(collected.visual_line_runs[0])
            last_line_runs = collected.paragraph_runs[-1]
            for index, run in enumerate(last_line_runs):
                if run.get('_line_break'):
                    last_line_runs = collected.paragraph_runs[-1][index + 1:]
        first_line_ascent = drawingml_text_baseline_offset(
            first_line_runs, style.fonts, default_size=font_size,
            line_spacing_px=collected.line_height_px if collected.paragraph_runs is not None else None,
            language=ctx.primary_language,
        )
        largest_last = max(
            (font_px_to_hpt(run.get('font_size', font_size)) / FONT_PX_TO_HUNDREDTHS_PT
             for run in last_line_runs if run.get('text')),
            default=font_size,
        )
        text_height += max(0.0, (largest_last - font_size) * 1.5)
        for paragraph in collected.paragraph_runs or []:
            previous_run = None
            for run in paragraph:
                if run.get('_line_break') and previous_run is not None:
                    run['_line_break_run'] = previous_run
                elif not run.get('_line_break'):
                    previous_run = run
    padding = deps.textbox_padding(font_size)
    if style.text_anchor == 'middle':
        box_x = style.x - text_width / 2 - padding
    elif style.text_anchor == 'end':
        box_x = style.x - text_width - padding
    else:
        box_x = style.x - padding
    box_y = style.y - first_line_ascent
    box_w = text_width + padding * 2
    box_h = text_height + padding
    if style.reflect_y:
        box_y = 2 * style.y - box_y - box_h
    return box_x, box_y, box_w, box_h, text_width, box_y


def _apply_frame_transform_and_rotation(
    elem: ET.Element,
    style: _ResolvedTextStyle,
    box_x: float,
    box_y: float,
    box_w: float,
    box_h: float,
    text_width: float,
    visual_box_y: float,
    ctx: ConvertContext,
    deps: _TextDeps,
) -> _TextBoxGeometry:
    """Apply explicit imported text-frame insets, pure translations, and pivot rotation."""
    exact_text_frame = None
    exact_text_insets: tuple[float, float, float] | None = None
    if elem.get('data-pptx-frame') is not None:
        _preset, _guides, exact_text_frame = deps.parse_preset_geometry_metadata(elem)
        if exact_text_frame is None:
            raise ValueError('data-pptx-frame did not resolve to a text frame')
        rx, ry, rw, rh = exact_text_frame
        if style.use_axis_transform:
            fx1, fy1 = transform_point(ctx.transform_matrix, rx, ry)
            fx2, fy2 = transform_point(ctx.transform_matrix, rx + rw, ry + rh)
        else:
            fx1, fx2 = ctx_x(rx, ctx), ctx_x(rx + rw, ctx)
            fy1, fy2 = ctx_y(ry, ctx), ctx_y(ry + rh, ctx)
        box_x, box_y = min(fx1, fx2), min(fy1, fy2)
        box_w, box_h = abs(fx2 - fx1), abs(fy2 - fy1)
        top_inset = visual_box_y - box_y
        if style.text_anchor == 'start':
            left_inset, right_inset = style.x - box_x, 0.0
        elif style.text_anchor == 'end':
            left_inset, right_inset = 0.0, box_x + box_w - style.x
        else:
            delta = style.x - (box_x + box_w / 2)
            left_inset, right_inset = max(0.0, delta * 2), max(0.0, -delta * 2)
        exact_text_insets = (left_inset, top_inset, right_inset)
    text_transform = elem.get('transform', '')
    ops = parse_transform_operations(text_transform) if text_transform else ()
    translate_only = bool(ops) and all(name == 'translate' for name, _args in ops)
    rotate_only = len(ops) == 1 and ops[0][0] == 'rotate'
    if ops and not (translate_only or rotate_only):
        raise ValueError('Text transform must be a translate-only list or one rotate operation')
    if translate_only and not ctx.use_transform_matrix:
        a, b, c, d, e, f = parse_transform_matrix(text_transform)
        if abs(a - 1.0) < 1e-9 and abs(b) < 1e-9 and abs(c) < 1e-9 and abs(d - 1.0) < 1e-9:
            sx, sy = ctx.scale_x or 1.0, ctx.scale_y or 1.0
            box_x = ctx.translate_x + sx * (a * ((box_x - ctx.translate_x) / sx) + e)
            box_y = ctx.translate_y + sy * (d * ((box_y - ctx.translate_y) / sy) + f)
    elif translate_only and style.use_axis_transform:
        _a, _b, _c, _d, e, f = parse_transform_matrix(text_transform)
        ma, mb, mc, md, _me, _mf = ctx.transform_matrix
        box_x += ma * e + mc * f
        box_y += mb * e + md * f
    text_rot = 0
    if rotate_only:
        rargs = ops[0][1]
        angle_deg = -rargs[0] if style.reflect_x != style.reflect_y else rargs[0]
        text_rot = int(angle_deg * ANGLE_UNIT)
        if len(rargs) == 3:
            if style.use_axis_transform:
                px, py = transform_point(ctx.transform_matrix, rargs[1], rargs[2])
            else:
                px, py = ctx_x(rargs[1], ctx), ctx_y(rargs[2], ctx)
            cx, cy = box_x + box_w / 2, box_y + box_h / 2
            rad = math.radians(angle_deg)
            dx, dy = cx - px, cy - py
            box_x = (px + dx * math.cos(rad) - dy * math.sin(rad)) - box_w / 2
            box_y = (py + dx * math.sin(rad) + dy * math.cos(rad)) - box_h / 2
    return _TextBoxGeometry(
        box_x=box_x, box_y=box_y, box_w=box_w, box_h=box_h, text_width=text_width,
        exact_text_frame=exact_text_frame, exact_text_insets=exact_text_insets, text_rot=text_rot,
    )


def _build_text_body_and_shape_xml(
    elem: ET.Element,
    style: _ResolvedTextStyle,
    collected: _CollectedRuns,
    geom: _TextBoxGeometry,
    ctx: ConvertContext,
    deps: _TextDeps,
) -> ShapeResult:
    """Serialize DrawingML paragraphs, bodyPr insets/wrapping, and optional inline math wrapper."""
    algn = {'start': 'l', 'middle': 'ctr', 'end': 'r'}.get(style.text_anchor, 'l')
    shape_effect_xml = text_effect_xml = ''
    filt_id = get_effective_filter_id(elem, ctx)
    if filt_id and filt_id in ctx.defs:
        filter_elem = ctx.defs[filt_id]
        effect_kind = classify_filter_effect(filter_elem)
        if effect_kind == 'glow':
            text_effect_xml = build_effect_xml(filter_elem, get_element_opacity(elem, ctx))
        elif effect_kind == 'shadow':
            shape_effect_xml = build_effect_xml(filter_elem, get_element_opacity(elem, ctx))

    def _paragraphs_xml(formula_previews: bool) -> str:
        def _runs_xml(line_runs: list[dict[str, Any]]) -> str:
            return '\n'.join(
                deps.build_run_xml(
                    deps.inline_formula_preview_run(r) if formula_previews else r,
                    style.fonts, ctx, text_effect_xml,
                )
                for r in line_runs
            )

        if collected.paragraph_runs is not None:
            line_spc_val = round(collected.line_height_px * FONT_PX_TO_HUNDREDTHS_PT)
            ln_spc_xml = f'<a:lnSpc><a:spcPts val="{line_spc_val}"/></a:lnSpc>'
            chunks = []
            for line, extra_px, bullet in zip(
                collected.paragraph_runs, collected.paragraph_space_before, collected.paragraph_bullets,
            ):
                spc_bef = (
                    f'<a:spcBef><a:spcPts val="{round(extra_px * FONT_PX_TO_HUNDREDTHS_PT)}"/></a:spcBef>'
                    if extra_px > 0 else ''
                )
                first_run = next((r for r in line if not r.get('_line_break')), None)
                line_spc = '' if any(r.get(deps.inline_formula_key) is not None for r in line) else ln_spc_xml
                p_pr = deps.paragraph_pr_xml(
                    algn=algn,
                    font_size=float(first_run.get('font_size', style.font_size)) if first_run else style.font_size,
                    body_xml=f'{line_spc}{spc_bef}', bullet=bullet, ctx=ctx,
                    rtl=text_uses_rtl(''.join(str(r.get('text', '')) for r in line), ctx.primary_language),
                )
                chunks.append(f'<a:p>\n{p_pr}\n{_runs_xml(line)}\n</a:p>')
            return '\n'.join(chunks)
        p_pr = deps.paragraph_pr_xml(
            algn=algn,
            font_size=float(collected.runs[0].get('font_size', style.font_size)) if collected.runs else style.font_size,
            bullet=collected.single_bullet, ctx=ctx,
            rtl=text_uses_rtl(collected.full_text, ctx.primary_language),
        )
        return f'<a:p>\n{p_pr}\n{_runs_xml(collected.runs)}\n</a:p>'

    off_x, off_y = px_to_emu(geom.box_x), px_to_emu(geom.box_y)
    ext_cx = (
        px_to_emu(geom.box_w) if geom.exact_text_frame is not None
        else deps.drawingml_text_frame_width_emu(geom.text_width, style.font_size)
    )
    ext_cy = px_to_emu(geom.box_h)
    if ext_cx < 1 or ext_cy < 1:
        raise ValueError(
            f'negative letter-spacing produces a non-positive DrawingML text-frame extent (cx={ext_cx}, cy={ext_cy})'
        )
    validate_ooxml_xfrm(off_x, off_y, ext_cx, ext_cy)
    deps.validate_text_run_advances(collected.runs)
    if geom.exact_text_frame is not None:
        if geom.exact_text_insets is None:
            raise ValueError('data-pptx-frame text insets were not resolved')
        li, ti, ri = geom.exact_text_insets
        wrap = 'none' if ctx.text_flow == TEXT_FLOW_PRESERVE else 'square'
        body_pr_xml = (
            f'<a:bodyPr wrap="{wrap}" lIns="{px_to_emu(li)}" tIns="{px_to_emu(ti)}" '
            f'rIns="{px_to_emu(ri)}" bIns="0" anchor="t" anchorCtr="0">\n<a:noAutofit/>\n</a:bodyPr>'
        )
    elif collected.paragraph_runs is not None:
        wrap = 'none' if ctx.text_flow == TEXT_FLOW_PRESERVE else 'square'
        autofit = (
            '<a:spAutoFit/>'
            if ctx.text_flow == TEXT_FLOW_PRESERVE and not collected.is_placeholder_carrier
            else '<a:noAutofit/>'
        )
        body_pr_xml = f'<a:bodyPr wrap="{wrap}" lIns="0" tIns="0" rIns="0" bIns="0" anchor="t" anchorCtr="0">\n{autofit}\n</a:bodyPr>'
    else:
        body_pr_xml = '<a:bodyPr wrap="none" lIns="0" tIns="0" rIns="0" bIns="0" anchor="t" anchorCtr="0">\n<a:spAutoFit/>\n</a:bodyPr>'

    rot_attr = f' rot="{geom.text_rot}"' if geom.text_rot else ''

    def _shape_xml(sp_id: int, sp_paragraphs_xml: str) -> str:
        return f'''<p:sp>
<p:nvSpPr>
<p:cNvPr id="{sp_id}" name="{_xml_escape(deps.shape_display_name(elem, f'TextBox {sp_id}'))}"/>
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

    shape_xml = _shape_xml(deps.claim_element_shape_id(elem, ctx), _paragraphs_xml(False))
    if any(r.get(deps.inline_formula_key) is not None for r in collected.runs):
        from ..native_objects.inline_formula import wrap_inline_formula_shape
        shape_xml = wrap_inline_formula_shape(
            shape_xml, _shape_xml(ctx.next_id(), _paragraphs_xml(True)),
        )
    return ShapeResult(xml=shape_xml, bounds_emu=(off_x, off_y, off_x + ext_cx, off_y + ext_cy))


# Adapted from elements.py; upstream MIT, Hugo He, 2025-2026.
def convert_text(elem: ET.Element, ctx: ConvertContext) -> ShapeResult | None:
    """Convert SVG <text> to DrawingML text shape with multi-run support."""
    deps = _load_text_deps()
    style = _resolve_text_style_and_position(elem, ctx, deps)
    collected = _collect_paragraph_and_visual_runs(elem, style, ctx, deps)
    if collected is None:
        return None
    box_x, box_y, box_w, box_h, text_width, visual_box_y = _estimate_text_box_geometry(
        elem, style, collected, ctx, deps,
    )
    geom = _apply_frame_transform_and_rotation(
        elem, style, box_x, box_y, box_w, box_h, text_width, visual_box_y, ctx, deps,
    )
    return _build_text_body_and_shape_xml(elem, style, collected, geom, ctx, deps)


# ---------------------------------------------------------------------------
# clipPath support (image clipping)
# ---------------------------------------------------------------------------
