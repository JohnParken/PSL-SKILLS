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



# Responsibility: elements_text_runs; adapted from the original module.

def _override_run_attrs(
    parent_attrs: dict[str, Any],
    tspan: ET.Element,
    ctx: ConvertContext,
) -> dict[str, Any]:
    """Layer a tspan's styling attributes over the inherited run attrs."""
    from .elements_text_metrics import _text_opacity_ratio
    run_attrs = dict(parent_attrs)
    inline_style = parse_inline_style(tspan.get('style'))

    def tspan_attr(name: str) -> str | None:
        return inline_style.get(name) or tspan.get(name)

    object_opacity = float(run_attrs.get('_object_opacity', 1.0))
    fill_opacity = float(run_attrs.get('_fill_opacity', 1.0))
    stroke_opacity = float(run_attrs.get('_stroke_opacity', 1.0))
    if tspan_attr('opacity') is not None:
        object_opacity *= _text_opacity_ratio(tspan_attr('opacity'))
    if tspan_attr('fill-opacity') is not None:
        fill_opacity = _text_opacity_ratio(tspan_attr('fill-opacity'))
    if tspan_attr('stroke-opacity') is not None:
        stroke_opacity = _text_opacity_ratio(tspan_attr('stroke-opacity'))
    run_attrs['_object_opacity'] = object_opacity
    run_attrs['_fill_opacity'] = fill_opacity
    run_attrs['_stroke_opacity'] = stroke_opacity
    effective_fill_opacity = object_opacity * fill_opacity
    effective_stroke_opacity = object_opacity * stroke_opacity
    run_attrs['opacity'] = (
        effective_fill_opacity if effective_fill_opacity < 1.0 else None
    )
    run_attrs['stroke_opacity'] = (
        effective_stroke_opacity if effective_stroke_opacity < 1.0 else None
    )

    if tspan_attr('font-weight'):
        run_attrs['font_weight'] = parse_project_font_weight(
            tspan_attr('font-weight')
        ).canonical
    raw_baseline_shift = tspan.get('baseline-shift')
    if raw_baseline_shift is not None:
        run_attrs['baseline_shift'] = int(
            parse_project_baseline_shift(raw_baseline_shift).value
        )
    if tspan_attr('fill'):
        child_fill = tspan_attr('fill')
        run_attrs['fill_raw'] = child_fill
        c = parse_hex_color(child_fill)
        if c:
            run_attrs['fill'] = c
    if tspan_attr('stroke'):
        run_attrs['stroke_raw'] = tspan_attr('stroke')
    if tspan_attr('stroke-width'):
        run_attrs['stroke_width'] = parse_svg_length(
            tspan_attr('stroke-width'),
            run_attrs.get('stroke_width', 1.0),
            font_size=float(run_attrs.get('font_size', 16)),
        )
    resolved_font_size = ctx.text_font_sizes.get(id(tspan))
    if resolved_font_size is not None:
        run_attrs['font_size'] = resolved_font_size * ctx.scale_y
    elif tspan_attr('font-size'):
        run_attrs['font_size'] = parse_svg_length(
            tspan_attr('font-size'),
            run_attrs['font_size'],
            font_size=float(run_attrs.get('font_size', 16)),
        )
    if tspan_attr('font-family'):
        run_attrs['font_family'] = tspan_attr('font-family')
    if tspan_attr('font-style'):
        run_attrs['font_style'] = parse_project_font_style(
            tspan_attr('font-style')
        ).canonical
    if tspan_attr('text-decoration'):
        run_attrs['text_decoration'] = parse_project_text_decoration(
            tspan_attr('text-decoration')
        ).canonical
    resolved_letter_spacing = ctx.text_letter_spacings.get(id(tspan))
    if resolved_letter_spacing is not None:
        run_attrs['letter_spacing'] = resolved_letter_spacing * ctx.scale_x
    elif tspan_attr('letter-spacing'):
        run_attrs['letter_spacing'] = parse_project_letter_spacing(
            tspan_attr('letter-spacing'),
            font_size=float(run_attrs.get('font_size', 16)),
            scale_x=float(run_attrs.get('_scale_x', 1.0)),
        ).value
    return run_attrs


def _collect_tspan_runs(
    tspan: ET.Element,
    inherited_attrs: dict[str, Any],
    ctx: ConvertContext,
    inherited_xml_space: str = 'default',
) -> list[dict[str, Any]]:
    """Recursively turn one inline SVG subtree into DrawingML text runs."""
    return _collect_inline_runs(
        tspan,
        inherited_attrs,
        ctx,
        inherited_xml_space,
    )


def _collect_inline_runs(
    container: ET.Element,
    inherited_attrs: dict[str, Any],
    ctx: ConvertContext,
    inherited_xml_space: str = 'default',
    inherited_hyperlink: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Collect nested ``tspan``/``a`` content with style and link inheritance."""
    from .elements_path_shapes import _INLINE_FORMULA_ATTR
    from .elements_path_shapes import _INLINE_FORMULA_KEY
    runs: list[dict[str, Any]] = []
    own_attrs = _override_run_attrs(inherited_attrs, container, ctx)
    own_xml_space = resolve_project_xml_space(container, inherited_xml_space)
    container_tag = container.tag.replace(f'{{{SVG_NS}}}', '')
    own_hyperlink = inherited_hyperlink
    if container_tag == 'a':
        own_hyperlink = hyperlink_run_metadata(
            ctx,
            svg_hyperlink_href(container),
        )

    if container_tag == 'tspan' and container.get('dx') is not None:
        dx = parse_svg_length(
            container.get('dx'),
            percent_base=ctx.viewport_width,
            font_size=float(own_attrs.get('font_size', 16)) / (ctx.scale_y or 1.0),
        ) * ctx.scale_x
        if dx:
            runs.append({**own_attrs, 'text': '', '_inline_dx': dx})

    if container.text:
        run = {
            **own_attrs,
            'text': container.text,
            '_xml_space': own_xml_space,
        }
        if own_hyperlink is not None:
            run.update(own_hyperlink)
        inline_formula = container.get(_INLINE_FORMULA_ATTR)
        if inline_formula is not None:
            run[_INLINE_FORMULA_KEY] = inline_formula
        runs.append(run)

    for child in container:
        child_tag = child.tag.replace(f'{{{SVG_NS}}}', '')
        if child_tag in {'tspan', 'a'}:
            runs.extend(
                _collect_inline_runs(
                    child,
                    own_attrs,
                    ctx,
                    own_xml_space,
                    own_hyperlink,
                )
            )
            if child.tail:
                tail_run = {
                    **own_attrs,
                    'text': child.tail,
                    '_xml_space': own_xml_space,
                }
                if own_hyperlink is not None:
                    tail_run.update(own_hyperlink)
                runs.append(tail_run)

    return runs


def _build_text_runs(
    elem: ET.Element,
    parent_attrs: dict[str, Any],
    ctx: ConvertContext,
) -> list[dict[str, Any]]:
    """Build a list of text runs from a <text> element, handling <tspan> children.

    Each run carries text plus resolved paint, typography, tracking, and baseline
    shift. Nested tspans are walked recursively so inline format changes still
    produce distinct runs.
    """
    from .elements_text_metrics import _normalize_text_run_whitespace
    runs: list[dict[str, Any]] = []
    xml_space = resolve_project_xml_space(elem)

    if elem.text:
        runs.append({
            **parent_attrs,
            'text': elem.text,
            '_xml_space': xml_space,
        })

    for child in elem:
        child_tag = child.tag.replace(f'{{{SVG_NS}}}', '')
        if child_tag in {'tspan', 'a'}:
            runs.extend(_collect_inline_runs(
                child,
                parent_attrs,
                ctx,
                xml_space,
            ))
            if child.tail:
                runs.append({
                    **parent_attrs,
                    'text': child.tail,
                    '_xml_space': xml_space,
                })

    return _normalize_text_run_whitespace(runs)


def _build_text_fill_xml(
    fill: str,
    fill_raw: str,
    opacity: float | None,
    ctx: ConvertContext | None,
) -> str:
    """Build DrawingML fill XML for a text run."""
    from .elements_pictures import _build_image_blip_xml
    from .elements_pictures import _register_image_media
    from .elements_image_sources import load_project_image_source
    if fill_raw.strip().lower() in ('none', 'transparent'):
        return '<a:noFill/>'

    paint_id = resolve_url_id(fill_raw)
    if paint_id and ctx and paint_id in ctx.defs:
        paint = ctx.defs[paint_id]
        paint_tag = paint.tag.rsplit('}', 1)[-1]
        if paint_tag in {'linearGradient', 'radialGradient'}:
            return build_gradient_fill(
                paint,
                opacity,
                ctx.theme_color_spec,
                "text",
            )
        if paint_tag == 'pattern':
            mode, image = resolve_project_text_image_fill(paint)
            source = load_project_image_source(
                image,
                ctx.svg_dir,
                ctx.resource_root,
            )
            r_id = _register_image_media(
                ctx,
                source.img_format,
                source.img_data,
                reuse_text_fill=True,
            )
            blip_xml = _build_image_blip_xml(r_id, opacity)
            if mode == 'stretch':
                fill_mode_xml = '<a:stretch><a:fillRect/></a:stretch>'
            else:
                fill_mode_xml = '<a:tile/>'
            return f'<a:blipFill>{blip_xml}{fill_mode_xml}</a:blipFill>'

    parsed_color, color_alpha = parse_svg_color(fill_raw)
    fill = parsed_color or fill
    opacity = combine_opacity(opacity, color_alpha)
    alpha_xml = ''
    if opacity is not None:
        alpha_xml = f'<a:alphaMod val="{quantize_ooxml_alpha(opacity)}"/>'
    theme_spec = ctx.theme_color_spec if ctx is not None else None
    return (
        '<a:solidFill>'
        f'{color_node_xml(fill, theme_spec, "text", alpha_xml)}'
        '</a:solidFill>'
    )


def _build_text_outline_xml(
    run: dict[str, Any],
    ctx: ConvertContext | None,
) -> str:
    """Build DrawingML outline XML for a text run from SVG stroke attributes."""
    stroke_raw = run.get('stroke_raw')
    if not stroke_raw or stroke_raw.strip().lower() in ('none', 'transparent'):
        return ''

    color, color_alpha = parse_svg_color(stroke_raw)
    if not color:
        return ''

    stroke_width = _f(str(run.get('stroke_width', 1.0)), 1.0)
    stroke_opacity = combine_opacity(run.get('stroke_opacity'), color_alpha)
    alpha_xml = ''
    if stroke_opacity is not None:
        alpha_xml = (
            f'<a:alphaMod val="{quantize_ooxml_alpha(stroke_opacity)}"/>'
        )

    theme_spec = ctx.theme_color_spec if ctx is not None else None
    return (
        f'<a:ln w="{px_to_emu(stroke_width)}">'
        '<a:solidFill>'
        f'{color_node_xml(color, theme_spec, "stroke", alpha_xml)}'
        '</a:solidFill>'
        '</a:ln>'
    )


def _build_run_properties_xml(
    run: dict[str, Any],
    default_fonts: dict[str, str],
    ctx: ConvertContext | None = None,
    effect_xml: str = '',
    fixed_font_family: str | None = None,
) -> str:
    """Build the final ``a:rPr`` used to compare and emit one text run."""
    from .elements_text_metrics import _letter_spacing_to_drawingml_spc
    text = str(run['text'])
    fill = run.get('fill', '000000')
    fill_raw = run.get('fill_raw', '')
    fw = run.get('font_weight', '400')
    fs_px = run.get('font_size', 16)
    fstyle = run.get('font_style', '')
    ff = run.get('font_family', '')
    letter_spacing_px = float(run.get('letter_spacing', 0.0) or 0.0)
    baseline_shift = int(run.get('baseline_shift', 0) or 0)
    opacity = run.get('opacity')

    text_dec = run.get('text_decoration', '')

    # Exported font size = fs_px * FONT_PX_TO_HUNDREDTHS_PT hundredths-of-pt,
    # rounded to **one decimal place of pt** (the nearest 10 hundredths). No 0.5pt
    # / integer snapping — whatever the px works out to is the size, e.g.
    # 18px -> 13.5pt, 24px -> 18.0pt, 42px -> 31.5pt.
    sz = font_px_to_hpt(fs_px)
    b_attr = ' b="1"' if parse_project_font_weight(fw).value else ''
    i_attr = ' i="1"' if fstyle == 'italic' else ''
    underline, strike = tuple(
        parse_project_text_decoration(text_dec or 'none').value
    )
    u_attr = ' u="sng"' if underline else ''
    strike_attr = ' strike="sngStrike"' if strike else ''
    spc_attr = _letter_spacing_to_drawingml_spc(letter_spacing_px)
    baseline_attr = f' baseline="{baseline_shift}"' if baseline_shift else ''

    fonts = (
        parse_font_family(ff, ctx.primary_language if ctx is not None else None)
        if ff else default_fonts
    )
    run_fonts = (
        {
            'latin': fixed_font_family,
            'ea': fixed_font_family,
            'cs': fixed_font_family,
        }
        if fixed_font_family is not None
        else theme_font_tokens(
            fonts,
            ctx.theme_font_spec if ctx is not None else None,
        ) or resolve_text_run_fonts(text, fonts)
    )
    lang = str(run.get('_language_override') or detect_text_lang(
        text,
        ctx.primary_language if ctx is not None else None,
    ))
    rtl_xml = (
        '\n<a:rtl val="1"/>'
        if text_has_rtl_characters(text)
        else ''
    )

    fill_xml = _build_text_fill_xml(fill, fill_raw, opacity, ctx)
    outline_xml = _build_text_outline_xml(run, ctx)
    relationship_id = run.get(HYPERLINK_RID_KEY)
    hyperlink_xml = (
        hyperlink_click_xml(
            str(relationship_id),
            str(run.get(HYPERLINK_ACTION_KEY))
            if run.get(HYPERLINK_ACTION_KEY) is not None
            else None,
        )
        if relationship_id is not None
        else ''
    )

    return f'''<a:rPr lang="{lang}" sz="{sz}"{b_attr}{i_attr}{u_attr}{strike_attr}{spc_attr}{baseline_attr} dirty="0">
{outline_xml}
{fill_xml}
{effect_xml}
<a:latin typeface="{_xml_escape(run_fonts['latin'])}"/>
<a:ea typeface="{_xml_escape(run_fonts['ea'])}"/>
<a:cs typeface="{_xml_escape(run_fonts['cs'])}"/>
{hyperlink_xml}{rtl_xml}
</a:rPr>'''


def _coalesce_text_runs(
    runs: list[dict[str, Any]],
    default_fonts: dict[str, str],
    ctx: ConvertContext | None,
) -> list[dict[str, Any]]:
    """Join adjacent runs that PowerPoint sees as one formatting run."""
    from .elements_path_shapes import _INLINE_FORMULA_KEY
    merged: list[dict[str, Any]] = []
    previous_properties: str | None = None
    for run in runs:
        if run.get('_line_break'):
            merged.append({'_line_break': True})
            previous_properties = None
            continue
        text = str(run.get('text', ''))
        if not text:
            continue
        if run.get(_INLINE_FORMULA_KEY) is not None or '_inline_dx' in run:
            merged.append({**run, 'text': text})
            previous_properties = None
            continue
        properties = _build_run_properties_xml(run, default_fonts, ctx)
        if (
            merged
            and merged[-1].get(_INLINE_FORMULA_KEY) is None
            and merged[-1].get(HYPERLINK_RID_KEY) == run.get(HYPERLINK_RID_KEY)
            and merged[-1].get(HYPERLINK_ACTION_KEY) == run.get(HYPERLINK_ACTION_KEY)
            and properties == previous_properties
        ):
            candidate = {
                **merged[-1],
                'text': str(merged[-1].get('text', '')) + text,
            }
            candidate_properties = _build_run_properties_xml(
                candidate,
                default_fonts,
                ctx,
            )
            if candidate_properties == previous_properties:
                merged[-1] = candidate
                previous_properties = candidate_properties
                continue
        merged.append({**run, 'text': text})
        previous_properties = properties
    return merged


def _text_axis_transform(ctx: ConvertContext) -> tuple[bool, bool, bool]:
    """Return whether text can consume the context matrix and its axis flips."""
    if not ctx.use_transform_matrix:
        return False, False, False
    a, b, c, d, _e, _f = ctx.transform_matrix
    if abs(b) > 1e-9 or abs(c) > 1e-9:
        return False, False, False
    return True, a < 0, d < 0


def _inline_formula_preview_run(run: dict[str, Any]) -> dict[str, Any]:
    """Return an inline formula run as its ordinary SVG preview-text run."""
    from .elements_path_shapes import _INLINE_FORMULA_KEY
    if run.get(_INLINE_FORMULA_KEY) is None:
        return run
    preview = dict(run)
    del preview[_INLINE_FORMULA_KEY]
    return preview


def _build_run_xml(
    run: dict[str, Any],
    default_fonts: dict[str, str],
    ctx: ConvertContext | None = None,
    effect_xml: str = '',
) -> str:
    """Build a single <a:r> XML from a run dict. Supports gradient fills on text."""
    from .elements_path_shapes import _INLINE_FORMULA_KEY
    if run.get('_line_break'):
        break_run = run.get('_line_break_run')
        if break_run is not None:
            properties = dict(break_run)
            properties.pop(HYPERLINK_RID_KEY, None)
            properties.pop(HYPERLINK_ACTION_KEY, None)
            return f'<a:br>{_build_run_properties_xml(properties, default_fonts, ctx)}</a:br>'
        return '<a:br/>'
    text = str(run['text'])
    inline_formula = run.get(_INLINE_FORMULA_KEY)
    if inline_formula is not None:
        fill_raw = str(run.get('fill_raw') or f"#{run.get('fill', '000000')}")
        fill_color, fill_alpha = parse_svg_color(fill_raw)
        if fill_color is None or fill_alpha <= 0:
            raise ValueError(
                'inline formula text requires one visible solid fill color'
            )
        math_run = {
            **run,
            'font_family': 'Cambria Math',
            'font_weight': '400',
            'font_style': 'normal',
            'text_decoration': 'none',
            'letter_spacing': 0.0,
            'stroke_raw': '',
            'stroke_opacity': None,
            '_language_override': (
                ctx.primary_language
                if ctx is not None and ctx.primary_language is not None
                else 'en-US'
            ),
        }
        properties_xml = _build_run_properties_xml(
            math_run,
            default_fonts,
            ctx,
            fixed_font_family='Cambria Math',
        )
        from ..native_objects.inline_formula import build_inline_formula_xml
        return build_inline_formula_xml(str(inline_formula), properties_xml)
    properties_xml = _build_run_properties_xml(
        run,
        default_fonts,
        ctx,
        effect_xml,
    )
    space_attr = ' xml:space="preserve"' if text != text.strip() or '  ' in text else ''

    return f'''<a:r>
{properties_xml}
<a:t{space_attr}>{_xml_escape(text)}</a:t>
</a:r>'''
