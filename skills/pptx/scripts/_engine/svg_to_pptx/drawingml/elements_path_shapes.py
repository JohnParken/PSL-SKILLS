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



# Responsibility: elements_path_shapes; adapted from the original module.

def convert_path(elem: ET.Element, ctx: ConvertContext) -> ShapeResult | None:
    """Convert SVG <path> to DrawingML custom geometry shape."""
    from .elements_preset_geometry import _build_preserved_custom_geom
    from .elements_preset_geometry import _build_preset_geom_from_meta
    from .elements_shape_xml import _claim_element_shape_id
    from .elements_shape_xml import _combined_transform_matrix
    from .elements_preset_geometry import _pathlike_preset_xfrm
    from .elements_shape_xml import _uses_full_transform
    from .elements_shape_xml import _wrap_geometry_object
    preset_geom = _build_preset_geom_from_meta(elem, ctx)
    preserved_custom_geom = _build_preserved_custom_geom(elem)
    native_geom = preset_geom or preserved_custom_geom
    d = elem.get('d', '')
    if not d:
        if native_geom is not None:
            raise ValueError('Native-geometry <path> requires a non-empty d attribute')
        return None

    commands = parse_svg_path(d)
    commands = svg_path_to_absolute(commands)
    commands = normalize_path_commands(commands)

    transform = elem.get('transform')
    if _uses_full_transform(ctx, transform):
        commands = transform_path_commands(commands, _combined_transform_matrix(ctx, transform))
        path_xml, min_x, min_y, width, height = path_commands_to_drawingml(
            commands, 0, 0, 1.0, 1.0,
        )
    else:
        path_xml, min_x, min_y, width, height = path_commands_to_drawingml(
            commands, ctx.translate_x, ctx.translate_y,
            ctx.scale_x, ctx.scale_y,
        )

    if not path_xml:
        return None

    w_emu = px_to_emu(width)
    h_emu = px_to_emu(height)

    geom = native_geom
    if geom is None:
        geom = f'''<a:custGeom>
<a:avLst/><a:gdLst/><a:ahLst/><a:cxnLst/>
<a:rect l="l" t="t" r="r" b="b"/>
<a:pathLst><a:path w="{w_emu}" h="{h_emu}">
{path_xml}
</a:path></a:pathLst>
</a:custGeom>'''

    fill_op = get_fill_opacity(elem, ctx)
    stroke_op = get_stroke_opacity(elem, ctx)
    fill = build_fill_xml(elem, ctx, fill_op)
    stroke = build_stroke_xml(elem, ctx, stroke_op)

    effect = ''
    filt_id = get_effective_filter_id(elem, ctx)
    if filt_id and filt_id in ctx.defs:
        effect = build_effect_xml(
            ctx.defs[filt_id],
            get_element_opacity(elem, ctx),
        )

    shape_id = _claim_element_shape_id(elem, ctx)
    xfrm_attr = ''
    off_x = px_to_emu(min_x)
    off_y = px_to_emu(min_y)
    bounds_emu = (off_x, off_y, off_x + w_emu, off_y + h_emu)
    if native_geom is not None:
        xfrm = _pathlike_preset_xfrm(
            elem,
            ctx,
            transform,
            min_x,
            min_y,
            width,
            height,
        )
        xfrm_attr, off_x, off_y, w_emu, h_emu, bounds_emu = xfrm
    return ShapeResult(
        xml=_wrap_geometry_object(
            elem,
            ctx,
            shape_id, f'Freeform {shape_id}',
            off_x, off_y, w_emu, h_emu,
            geom, fill, stroke, effect, xfrm_attr=xfrm_attr,
        ),
        bounds_emu=bounds_emu,
    )


# ---------------------------------------------------------------------------
# polygon / polyline
# ---------------------------------------------------------------------------

def convert_polygon(elem: ET.Element, ctx: ConvertContext) -> ShapeResult | None:
    """Convert SVG <polygon> to DrawingML custom geometry shape."""
    from .elements_preset_geometry import _build_preset_geom_from_meta
    from .elements_shape_xml import _claim_element_shape_id
    from .elements_shape_xml import _combined_transform_matrix
    from .elements_preset_geometry import _pathlike_preset_xfrm
    from .elements_shape_xml import _uses_full_transform
    from .elements_shape_xml import _wrap_geometry_object
    preset_geom = _build_preset_geom_from_meta(elem, ctx)
    points = parse_svg_points(elem.get('points', ''), min_points=3)

    commands = [PathCommand('M', [points[0][0], points[0][1]])]
    for px_, py_ in points[1:]:
        commands.append(PathCommand('L', [px_, py_]))
    commands.append(PathCommand('Z', []))

    transform = elem.get('transform')
    if _uses_full_transform(ctx, transform):
        commands = transform_path_commands(commands, _combined_transform_matrix(ctx, transform))
        path_xml, min_x, min_y, width, height = path_commands_to_drawingml(
            commands, 0, 0, 1.0, 1.0,
        )
    else:
        path_xml, min_x, min_y, width, height = path_commands_to_drawingml(
            commands, ctx.translate_x, ctx.translate_y,
            ctx.scale_x, ctx.scale_y,
        )

    if not path_xml:
        return None

    w_emu = px_to_emu(width)
    h_emu = px_to_emu(height)

    geom = preset_geom or f'''<a:custGeom>
<a:avLst/><a:gdLst/><a:ahLst/><a:cxnLst/>
<a:rect l="l" t="t" r="r" b="b"/>
<a:pathLst><a:path w="{w_emu}" h="{h_emu}">
{path_xml}
</a:path></a:pathLst>
</a:custGeom>'''

    fill_op = get_fill_opacity(elem, ctx)
    stroke_op = get_stroke_opacity(elem, ctx)
    fill = build_fill_xml(elem, ctx, fill_op)
    stroke = build_stroke_xml(elem, ctx, stroke_op)

    effect = ''
    filt_id = get_effective_filter_id(elem, ctx)
    if filt_id and filt_id in ctx.defs:
        effect = build_effect_xml(
            ctx.defs[filt_id],
            get_element_opacity(elem, ctx),
        )

    shape_id = _claim_element_shape_id(elem, ctx)
    xfrm_attr = ''
    off_x = px_to_emu(min_x)
    off_y = px_to_emu(min_y)
    bounds_emu = (off_x, off_y, off_x + w_emu, off_y + h_emu)
    if preset_geom is not None:
        xfrm = _pathlike_preset_xfrm(
            elem,
            ctx,
            transform,
            min_x,
            min_y,
            width,
            height,
        )
        xfrm_attr, off_x, off_y, w_emu, h_emu, bounds_emu = xfrm
    return ShapeResult(
        xml=_wrap_geometry_object(
            elem,
            ctx,
            shape_id, f'Polygon {shape_id}',
            off_x, off_y, w_emu, h_emu,
            geom, fill, stroke, effect, xfrm_attr=xfrm_attr,
        ),
        bounds_emu=bounds_emu,
    )


def convert_polyline(elem: ET.Element, ctx: ConvertContext) -> ShapeResult | None:
    """Convert SVG <polyline> to DrawingML custom geometry shape."""
    from .elements_preset_geometry import _build_preset_geom_from_meta
    from .elements_shape_xml import _claim_element_shape_id
    from .elements_shape_xml import _combined_transform_matrix
    from .elements_preset_geometry import _pathlike_preset_xfrm
    from .elements_shape_xml import _uses_full_transform
    from .elements_shape_xml import _wrap_geometry_object
    preset_geom = _build_preset_geom_from_meta(elem, ctx)
    points = parse_svg_points(elem.get('points', ''), min_points=2)

    commands = [PathCommand('M', [points[0][0], points[0][1]])]
    for px_, py_ in points[1:]:
        commands.append(PathCommand('L', [px_, py_]))

    transform = elem.get('transform')
    if _uses_full_transform(ctx, transform):
        commands = transform_path_commands(commands, _combined_transform_matrix(ctx, transform))
        path_xml, min_x, min_y, width, height = path_commands_to_drawingml(
            commands, 0, 0, 1.0, 1.0,
        )
    else:
        path_xml, min_x, min_y, width, height = path_commands_to_drawingml(
            commands, ctx.translate_x, ctx.translate_y,
            ctx.scale_x, ctx.scale_y,
        )

    if not path_xml:
        return None

    w_emu = px_to_emu(width)
    h_emu = px_to_emu(height)

    geom = preset_geom or f'''<a:custGeom>
<a:avLst/><a:gdLst/><a:ahLst/><a:cxnLst/>
<a:rect l="l" t="t" r="r" b="b"/>
<a:pathLst><a:path w="{w_emu}" h="{h_emu}">
{path_xml}
</a:path></a:pathLst>
</a:custGeom>'''

    fill_op = get_fill_opacity(elem, ctx)
    stroke_op = get_stroke_opacity(elem, ctx)
    fill = build_fill_xml(elem, ctx, fill_op)
    stroke = build_stroke_xml(elem, ctx, stroke_op)

    shape_id = _claim_element_shape_id(elem, ctx)
    xfrm_attr = ''
    off_x = px_to_emu(min_x)
    off_y = px_to_emu(min_y)
    bounds_emu = (off_x, off_y, off_x + w_emu, off_y + h_emu)
    if preset_geom is not None:
        xfrm = _pathlike_preset_xfrm(
            elem,
            ctx,
            transform,
            min_x,
            min_y,
            width,
            height,
        )
        xfrm_attr, off_x, off_y, w_emu, h_emu, bounds_emu = xfrm
    return ShapeResult(
        xml=_wrap_geometry_object(
            elem,
            ctx,
            shape_id, f'Polyline {shape_id}',
            off_x, off_y, w_emu, h_emu,
            geom, '<a:noFill/>', stroke, xfrm_attr=xfrm_attr,
        ),
        bounds_emu=bounds_emu,
    )


# ---------------------------------------------------------------------------
# text
# ---------------------------------------------------------------------------

_SERIF_WIDTH_FAMILIES = {
    'book antiqua',
    'cambria',
    'fangsong',
    'garamond',
    'georgia',
    'kaiti',
    'palatino',
    'palatino linotype',
    'serif',
    'simsun',
    'songti',
    'times',
    'times new roman',
}

_TEXTBOX_PADDING_MIN_PX = 0.5
_TEXTBOX_PADDING_MAX_PX = 2.0
_TEXTBOX_PADDING_RATIO = 0.04
# Single-line auto-fit headroom interpolates between a low-caps base and an
# all-caps ceiling for each script segment of a run. The crude per-char width
# estimate undercounts capitals most, so all-caps Latin segments need the
# ceiling to keep wrap-ignoring renderers (LibreOffice) from folding. The
# serif tier follows the typeface that draws the segment (``latin`` or ``ea``
# as resolved by parse_font_family), so a CJK segment drawn by Microsoft YaHei
# never takes the Times New Roman tier of the same stack, and a short serif
# label cannot force its multiplier onto an otherwise sans-serif line. Values
# are calibrated against LibreOffice renders of all-caps bold lines, with bases
# left above mixed-case and CJK render ratios; exact ratios shift with font
# substitution, so these carry deliberate margin rather than tracking one
# environment's numbers.
_TEXT_WIDTH_HEADROOM_BASE = 1.06
_TEXT_WIDTH_HEADROOM_CAPS = 1.12
_SERIF_TEXT_WIDTH_HEADROOM_BASE = 1.12
_SERIF_TEXT_WIDTH_HEADROOM_CAPS = 1.36
_TEXT_BULLET_MARKERS = {
    '·': '•',
    '•': '•',
    '●': '●',
    '▪': '▪',
    '■': '■',
    '◆': '◆',
    '◇': '◇',
    '◦': '◦',
    '‣': '‣',
}
_TEXT_BULLET_RE = re.compile(
    r'^(?P<prefix>\s*)(?P<marker>[·•●▪■◆◇◦‣])(?P<space>\s*)'
)
_INLINE_FORMULA_ATTR = 'data-pptx-inline-formula'
_INLINE_FORMULA_KEY = '_inline_formula_latex'
