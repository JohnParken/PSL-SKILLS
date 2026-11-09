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



# Responsibility: elements_basic_shapes; adapted from the original module.

def _build_round_rect_custgeom(w: float, h: float, rx: float, ry: float) -> str:
    """Build a DrawingML ``custGeom`` for a rectangle with elliptical corners.

    Used when ``<rect>`` has rx ≠ ry, which DrawingML's preset ``roundRect``
    cannot express (the preset takes a single ``adj`` shared by all four
    corners and is implicitly symmetric). Each 90° elliptical arc is
    approximated by one cubic Bézier — within 0.03% of the true ellipse, far
    below any visible threshold at slide resolution.

    Trade-off vs. the symmetric ``prstGeom roundRect`` path: this geometry
    is custom, so PowerPoint's yellow corner-radius handle is gone and the
    shape can no longer be retuned in-place. That matches the underlying
    reality — rx ≠ ry has no single "radius" to drag — and remains far
    better than the previous behaviour (silently dropping all corners and
    rendering a hard rectangle).

    Args:
        w, h:   Pixel dimensions of the rectangle (post ctx-scale).
        rx, ry: Pixel corner radii along x and y. Will be clamped to half
                of w / h respectively per the SVG spec.

    Returns:
        A complete ``<a:custGeom>...</a:custGeom>`` XML string. Coordinates
        are emitted in EMU within a path-local coordinate system whose
        ``w`` / ``h`` equal the rectangle's pixel-converted dimensions.
    """
    from .elements_shape_xml import _BEZIER_QUARTER_K
    # Clamp radii (SVG spec): rx > w/2 collapses to a half-circle end.
    rx = min(max(rx, 0.0), w / 2)
    ry = min(max(ry, 0.0), h / 2)

    width_emu = px_to_emu(w)
    height_emu = px_to_emu(h)
    rx_emu = px_to_emu(rx)
    ry_emu = px_to_emu(ry)

    cx_off = int(round(rx_emu * _BEZIER_QUARTER_K))
    cy_off = int(round(ry_emu * _BEZIER_QUARTER_K))

    def pt(x: int, y: int) -> str:
        return f'<a:pt x="{x}" y="{y}"/>'

    def cubic(c1: tuple[int, int], c2: tuple[int, int], end: tuple[int, int]) -> str:
        return (
            f'<a:cubicBezTo>{pt(*c1)}{pt(*c2)}{pt(*end)}</a:cubicBezTo>'
        )

    # Path traversed clockwise, starting just past the top-left corner.
    parts = [
        f'<a:moveTo>{pt(rx_emu, 0)}</a:moveTo>',
        f'<a:lnTo>{pt(width_emu - rx_emu, 0)}</a:lnTo>',
        # Top-right corner: (W-Rx, 0) → (W, Ry)
        cubic(
            (width_emu - rx_emu + cx_off, 0),
            (width_emu, ry_emu - cy_off),
            (width_emu, ry_emu),
        ),
        f'<a:lnTo>{pt(width_emu, height_emu - ry_emu)}</a:lnTo>',
        # Bottom-right corner: (W, H-Ry) → (W-Rx, H)
        cubic(
            (width_emu, height_emu - ry_emu + cy_off),
            (width_emu - rx_emu + cx_off, height_emu),
            (width_emu - rx_emu, height_emu),
        ),
        f'<a:lnTo>{pt(rx_emu, height_emu)}</a:lnTo>',
        # Bottom-left corner: (Rx, H) → (0, H-Ry)
        cubic(
            (rx_emu - cx_off, height_emu),
            (0, height_emu - ry_emu + cy_off),
            (0, height_emu - ry_emu),
        ),
        f'<a:lnTo>{pt(0, ry_emu)}</a:lnTo>',
        # Top-left corner: (0, Ry) → (Rx, 0)
        cubic(
            (0, ry_emu - cy_off),
            (rx_emu - cx_off, 0),
            (rx_emu, 0),
        ),
        '<a:close/>',
    ]

    path_xml = '\n'.join(parts)
    return (
        '<a:custGeom>'
        '<a:avLst/><a:gdLst/><a:ahLst/><a:cxnLst/>'
        '<a:rect l="l" t="t" r="r" b="b"/>'
        f'<a:pathLst><a:path w="{width_emu}" h="{height_emu}">'
        f'\n{path_xml}\n'
        '</a:path></a:pathLst>'
        '</a:custGeom>'
    )


def convert_rect(elem: ET.Element, ctx: ConvertContext) -> ShapeResult | None:
    """Convert SVG <rect> to DrawingML shape.

    Symmetric rounded corners (rx == ry) are emitted as ``prstGeom roundRect``
    so PowerPoint treats them as a native rounded-rectangle shape: the yellow
    adjustment handle stays draggable, and "Reset Picture / Shape" works as
    expected. Elliptical corners (rx != ry) fall back to plain rect geometry
    for now — current corpora contain none, but the branch keeps callers from
    silently producing distorted custom geometry if one ever appears.
    """
    from .elements_preset_geometry import _build_preset_geom_from_meta
    from .elements_shape_xml import _claim_element_shape_id
    from .elements_preset_geometry import _shape_xfrm_from_preset_frame
    from .elements_shape_xml import _shape_xfrm_from_svg_rect
    from .elements_shape_xml import _wrap_geometry_object
    raw_x = svg_length_x(elem.get('x'), ctx)
    raw_y = svg_length_y(elem.get('y'), ctx)
    raw_w = svg_length_x(elem.get('width'), ctx)
    raw_h = svg_length_y(elem.get('height'), ctx)
    x = ctx_x(raw_x, ctx)
    y = ctx_y(raw_y, ctx)
    w = ctx_w(raw_w, ctx)
    h = ctx_h(raw_h, ctx)
    preset_geom = _build_preset_geom_from_meta(elem, ctx)

    if (w <= 0 or h <= 0) and not (
        preset_geom is not None and is_stroked_zero_axis_shape(elem, ctx)
    ):
        return None

    # SVG spec: when only one of rx/ry is specified, the other inherits its
    # value. Real-world svg_output decks always write only `rx`, so ry must
    # be inferred to keep round corners from collapsing to zero on one axis.
    rx_attr = elem.get('rx')
    ry_attr = elem.get('ry')
    rx_raw = svg_length_x(rx_attr, ctx) if rx_attr is not None else 0.0
    ry_raw = svg_length_y(ry_attr, ctx) if ry_attr is not None else 0.0
    if rx_attr is not None and ry_attr is None:
        ry_raw = rx_raw
    elif ry_attr is not None and rx_attr is None:
        rx_raw = ry_raw
    rx = rx_raw * ctx.scale_x
    ry = ry_raw * ctx.scale_y

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

    transform = elem.get('transform')

    if preset_geom is not None:
        geom = preset_geom
    elif rx > 0 and abs(rx - ry) < 0.5:
        # Symmetric corners → native PowerPoint rounded rectangle. adj is
        # the corner radius as a fraction of the shorter side, in 1/1000-
        # percent units, capped at 50000 (= radius equals half the shorter
        # side, i.e. capsule end).
        short_side = min(w, h)
        radius = min(rx, short_side / 2)
        adj = max(0, min(50000, int(round(radius / short_side * 100000))))
        geom = (
            '<a:prstGeom prst="roundRect">'
            f'<a:avLst><a:gd name="adj" fmla="val {adj}"/></a:avLst>'
            '</a:prstGeom>'
        )
    elif rx > 0 or ry > 0:
        # Asymmetric corners (rx != ry) → DrawingML has no preset for
        # elliptical-corner rectangles, so emit a custGeom with one cubic
        # Bézier per 90° arc. We lose the prstGeom roundRect adjustment
        # handle, but symmetric and asymmetric cases now both render with
        # rounded corners instead of one of them silently flattening to
        # a hard rectangle.
        geom = _build_round_rect_custgeom(w, h, rx, ry)
    else:
        geom = '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom>'

    shape_id = _claim_element_shape_id(elem, ctx)
    if preset_geom is not None:
        xfrm = _shape_xfrm_from_preset_frame(
            elem,
            ctx,
            (raw_x, raw_y, raw_w, raw_h),
            (x, y, w, h),
            transform,
        )
    else:
        xfrm = _shape_xfrm_from_svg_rect(
            ctx,
            raw_x,
            raw_y,
            raw_w,
            raw_h,
            x,
            y,
            w,
            h,
            transform,
        )
    xfrm_attr, off_x, off_y, ext_cx, ext_cy, bounds_emu = xfrm
    return ShapeResult(
        xml=_wrap_geometry_object(
            elem,
            ctx,
            shape_id, f'Rectangle {shape_id}',
            off_x, off_y, ext_cx, ext_cy,
            geom, fill, stroke, effect, xfrm_attr=xfrm_attr,
        ),
        bounds_emu=bounds_emu,
    )


# ---------------------------------------------------------------------------
# circle (including donut-chart arc segments)
# ---------------------------------------------------------------------------

def _build_arc_ring_path(
    cx: float, cy: float, r: float,
    stroke_width: float,
    dash_len: float, dash_offset: float,
    rotate_deg: float,
    sx: float, sy: float,
) -> tuple[str, int, int, int, int]:
    """Build a filled annular-sector (donut segment) as DrawingML custGeom.

    SVG donut charts use stroke-dasharray on a circle to draw arc segments.
    DrawingML cannot reproduce this, so we convert each arc segment into a
    filled ring shape (outer arc -> line -> inner arc -> close).

    Returns:
        (geom_xml, min_x_emu, min_y_emu, w_emu, h_emu).
    """
    circumference = 2 * math.pi * r
    if circumference <= 0:
        return '', 0, 0, 0, 0

    start_frac = -dash_offset / circumference
    end_frac = start_frac + dash_len / circumference

    start_angle = start_frac * 2 * math.pi + math.radians(rotate_deg)
    end_angle = end_frac * 2 * math.pi + math.radians(rotate_deg)

    half_sw = stroke_width / 2
    r_outer = r + half_sw
    r_inner = r - half_sw

    num_segments = max(16, int(abs(end_angle - start_angle) / (math.pi / 32)))
    angles = [
        start_angle + (end_angle - start_angle) * i / num_segments
        for i in range(num_segments + 1)
    ]

    outer_pts = [(cx + r_outer * math.sin(a), cy - r_outer * math.cos(a)) for a in angles]
    inner_pts = [(cx + r_inner * math.sin(a), cy - r_inner * math.cos(a)) for a in reversed(angles)]

    all_pts = [(px * sx, py * sy) for px, py in outer_pts + inner_pts]

    xs = [p[0] for p in all_pts]
    ys = [p[1] for p in all_pts]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    width = max_x - min_x
    height = max_y - min_y

    if width <= 0 or height <= 0:
        return '', 0, 0, 0, 0

    w_emu = px_to_emu(width)
    h_emu = px_to_emu(height)

    lines: list[str] = []
    for i, (px, py) in enumerate(all_pts):
        lx = px_to_emu(px - min_x)
        ly = px_to_emu(py - min_y)
        if i == 0:
            lines.append(f'<a:moveTo><a:pt x="{lx}" y="{ly}"/></a:moveTo>')
        else:
            lines.append(f'<a:lnTo><a:pt x="{lx}" y="{ly}"/></a:lnTo>')
    lines.append('<a:close/>')

    path_xml = '\n'.join(lines)
    geom = f'''<a:custGeom>
<a:avLst/><a:gdLst/><a:ahLst/><a:cxnLst/>
<a:rect l="l" t="t" r="r" b="b"/>
<a:pathLst><a:path w="{w_emu}" h="{h_emu}">
{path_xml}
</a:path></a:pathLst>
</a:custGeom>'''

    return geom, px_to_emu(min_x), px_to_emu(min_y), w_emu, h_emu


def _is_donut_circle(elem: ET.Element, ctx: ConvertContext) -> bool:
    """Detect if a circle uses stroke-dasharray to simulate an arc segment."""
    dasharray = _get_attr(elem, 'stroke-dasharray', ctx)
    stroke = _get_attr(elem, 'stroke', ctx)
    fill = _get_attr(elem, 'fill', ctx)
    sw = svg_length_size(_get_attr(elem, 'stroke-width', ctx), ctx, 0)
    r = svg_length_size(elem.get('r'), ctx, 0)
    return is_thick_circle_shorthand(dasharray, stroke, fill, sw, r)


def convert_circle(elem: ET.Element, ctx: ConvertContext) -> ShapeResult | None:
    """Convert SVG <circle> to DrawingML ellipse or donut-arc shape."""
    from .elements_preset_geometry import _build_preset_geom_from_meta
    from .elements_shape_xml import _claim_element_shape_id
    from .elements_preset_geometry import _shape_xfrm_from_preset_frame
    from .elements_shape_xml import _shape_xfrm_from_svg_rect
    from .elements_shape_xml import _wrap_geometry_object
    from .elements_shape_xml import _wrap_shape
    cx_ = svg_length_x(elem.get('cx'), ctx)
    cy_ = svg_length_y(elem.get('cy'), ctx)
    r = svg_length_size(elem.get('r'), ctx)
    preset_geom = _build_preset_geom_from_meta(elem, ctx)

    if r <= 0:
        return None

    # --- Donut-chart arc segment detection ---
    if preset_geom is None and _is_donut_circle(elem, ctx):
        dasharray = _get_attr(elem, 'stroke-dasharray', ctx)
        parsed_dasharray = parse_project_stroke_dasharray(
            dasharray,
            allow_zero_gap=True,
        )
        if parsed_dasharray is None:
            raise ValueError('Thick-circle arc requires one dash/gap pair')
        _preset, dash_values = parsed_dasharray
        dash_len = dash_values[0]
        raw_dash_offset = elem.get('stroke-dashoffset')
        dash_offset = (
            parse_project_geometry_length(
                raw_dash_offset,
                'stroke-dashoffset',
            )
            if raw_dash_offset is not None else 0.0
        )
        stroke_width = svg_length_size(_get_attr(elem, 'stroke-width', ctx), ctx, 1)

        rotate_deg = 0.0
        transform = elem.get('transform', '')
        if transform:
            operations = parse_transform_operations(transform)
            if len(operations) != 1 or operations[0][0] != 'rotate':
                raise ValueError(
                    'Thick-circle arc transform must be one rotate operation'
                )
            rotate_deg = operations[0][1][0]

        geom, min_x, min_y, w_emu, h_emu = _build_arc_ring_path(
            ctx_x(cx_, ctx) / ctx.scale_x,
            ctx_y(cy_, ctx) / ctx.scale_y,
            r, stroke_width, dash_len, dash_offset, rotate_deg,
            ctx.scale_x, ctx.scale_y,
        )
        if not geom:
            return None

        # Use the stroke color/gradient as fill for the arc shape
        stroke_val = _get_attr(elem, 'stroke', ctx)
        op = get_stroke_opacity(elem, ctx)
        grad_id = resolve_url_id(stroke_val) if stroke_val else None
        if grad_id and grad_id in ctx.defs:
            fill = build_gradient_fill(
                ctx.defs[grad_id],
                op,
                ctx.theme_color_spec,
                "fill",
            )
        elif stroke_val:
            color, color_alpha = parse_svg_color(stroke_val)
            fill = (
                build_solid_fill(
                    color,
                    combine_opacity(op, color_alpha),
                    ctx.theme_color_spec,
                    "fill",
                )
                if color else '<a:noFill/>'
            )
        else:
            fill = '<a:noFill/>'

        stroke_xml = '<a:ln><a:noFill/></a:ln>'

        effect = ''
        filt_id = get_effective_filter_id(elem, ctx)
        if filt_id and filt_id in ctx.defs:
            effect = build_effect_xml(
                ctx.defs[filt_id],
                get_element_opacity(elem, ctx),
            )

        shape_id = _claim_element_shape_id(elem, ctx)
        return ShapeResult(
            xml=_wrap_shape(
                shape_id, f'Arc {shape_id}',
                min_x, min_y, w_emu, h_emu,
                geom, fill, stroke_xml, effect,
            ),
            bounds_emu=(min_x, min_y, min_x + w_emu, min_y + h_emu),
        )

    # --- Normal circle ---
    transform = elem.get('transform')
    cx_s = ctx_x(cx_, ctx)
    cy_s = ctx_y(cy_, ctx)
    r_x = r * ctx.scale_x
    r_y = r * ctx.scale_y

    x = cx_s - r_x
    y = cy_s - r_y
    w = r_x * 2
    h = r_y * 2

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

    geom = preset_geom or '<a:prstGeom prst="ellipse"><a:avLst/></a:prstGeom>'

    shape_id = _claim_element_shape_id(elem, ctx)
    if preset_geom is not None:
        xfrm = _shape_xfrm_from_preset_frame(
            elem,
            ctx,
            (cx_ - r, cy_ - r, r * 2, r * 2),
            (x, y, w, h),
            transform,
        )
    else:
        xfrm = _shape_xfrm_from_svg_rect(
            ctx,
            cx_ - r,
            cy_ - r,
            r * 2,
            r * 2,
            x,
            y,
            w,
            h,
            transform,
        )
    xfrm_attr, off_x, off_y, ext_cx, ext_cy, bounds_emu = xfrm
    return ShapeResult(
        xml=_wrap_geometry_object(
            elem,
            ctx,
            shape_id, f'Ellipse {shape_id}',
            off_x, off_y, ext_cx, ext_cy,
            geom, fill, stroke, effect, xfrm_attr=xfrm_attr,
        ),
        bounds_emu=bounds_emu,
    )


# ---------------------------------------------------------------------------
# line
# ---------------------------------------------------------------------------

def convert_line(elem: ET.Element, ctx: ConvertContext) -> ShapeResult | None:
    """Convert SVG <line> to DrawingML shape.

    Lines with marker-start / marker-end are converted using the 'line' preset
    geometry (prstGeom prst="line") so that PowerPoint renders native arrow
    heads (headEnd / tailEnd) correctly.  Plain lines (no markers) continue to
    use custom geometry which is sufficient and avoids flipH/flipV complexity.
    """
    from .elements_preset_geometry import _build_preset_geom_from_meta
    from .elements_shape_xml import _claim_element_shape_id
    from .elements_preset_geometry import _shape_xfrm_from_preset_frame
    from .elements_shape_xml import _transformed_point
    from .elements_shape_xml import _uses_full_transform
    from .elements_shape_xml import _wrap_geometry_object
    from .elements_shape_xml import _wrap_shape
    preset_geom = _build_preset_geom_from_meta(elem, ctx)
    transform = elem.get('transform')
    raw_x1 = svg_length_x(elem.get('x1'), ctx)
    raw_y1 = svg_length_y(elem.get('y1'), ctx)
    raw_x2 = svg_length_x(elem.get('x2'), ctx)
    raw_y2 = svg_length_y(elem.get('y2'), ctx)
    x1, y1 = _transformed_point(
        ctx,
        raw_x1,
        raw_y1,
        transform,
    )
    x2, y2 = _transformed_point(
        ctx,
        raw_x2,
        raw_y2,
        transform,
    )

    min_x = min(x1, x2)
    min_y = min(y1, y2)

    stroke_op = get_stroke_opacity(elem, ctx)
    stroke = build_stroke_xml(elem, ctx, stroke_op)

    shape_id = _claim_element_shape_id(elem, ctx)
    off_x = px_to_emu(min_x)
    off_y = px_to_emu(min_y)

    # Determine if this line carries arrow markers.
    has_marker = bool(
        _get_attr(elem, 'marker-start', ctx) or
        _get_attr(elem, 'marker-end', ctx)
    )

    if preset_geom is not None:
        # The preserved logical frame, not the rendered stroke/marker bounds,
        # owns the native shape size. Horizontal/vertical connectors retain a
        # one-EMU extent on the degenerate axis as required by DrawingML.
        raw_w = abs(raw_x2 - raw_x1)
        raw_h = abs(raw_y2 - raw_y1)
        resolved_w = abs(x2 - x1)
        resolved_h = abs(y2 - y1)
        xfrm_attr, off_x, off_y, w_emu, h_emu, bounds_emu = (
            _shape_xfrm_from_preset_frame(
                elem,
                ctx,
                (min(raw_x1, raw_x2), min(raw_y1, raw_y2), raw_w, raw_h),
                (min_x, min_y, resolved_w, resolved_h),
                transform,
            )
        )
        if not _uses_full_transform(ctx, transform):
            flip_attrs = []
            if x1 > x2:
                flip_attrs.append(' flipH="1"')
            if y1 > y2:
                flip_attrs.append(' flipV="1"')
            xfrm_attr += ''.join(flip_attrs)
        xml = _wrap_geometry_object(
            elem,
            ctx,
            shape_id,
            f'Connector {shape_id}' if elem.get('data-pptx-object') == 'connector'
            else f'Line {shape_id}',
            off_x,
            off_y,
            w_emu,
            h_emu,
            preset_geom,
            '<a:noFill/>',
            stroke,
            xfrm_attr=xfrm_attr,
        )
        return ShapeResult(xml=xml, bounds_emu=bounds_emu)

    if has_marker:
        # ----------------------------------------------------------------
        # Preset geometry approach: prstGeom prst="line"
        # PowerPoint only renders headEnd / tailEnd on lines whose geometry
        # it can intrinsically understand as a "line" (i.e. preset or
        # connector shapes).  Custom geometry shapes silently ignore
        # headEnd / tailEnd in most PowerPoint versions.
        #
        # The "line" preset draws from (0,0) to (w,h).
        #   headEnd  → placed at the start of the line = (x1, y1)
        #   tailEnd  → placed at the end   of the line = (x2, y2)
        # We set flipH / flipV so that the preset start/end align with the
        # original SVG endpoints:
        #   default  (no flip)  : top-left  → bottom-right  (x1≤x2, y1≤y2)
        #   flipH               : top-right → bottom-left   (x1>x2, y1≤y2)
        #   flipV               : bottom-left → top-right   (x1≤x2, y1>y2)
        #   flipH + flipV       : bottom-right → top-left   (x1>x2, y1>y2)
        # ----------------------------------------------------------------
        w = abs(x2 - x1)
        h = abs(y2 - y1)
        # DrawingML requires ext cx/cy ≥ 1 EMU
        w_emu = px_to_emu(w) if w > 0 else 1
        h_emu = px_to_emu(h) if h > 0 else 1

        flip_h = x1 > x2
        flip_v = y1 > y2
        flip_attr = ''
        if flip_h and flip_v:
            flip_attr = ' flipH="1" flipV="1"'
        elif flip_h:
            flip_attr = ' flipH="1"'
        elif flip_v:
            flip_attr = ' flipV="1"'

        xml = _wrap_shape(
            shape_id,
            f'Line {shape_id}',
            off_x,
            off_y,
            w_emu,
            h_emu,
            '<a:prstGeom prst="line"><a:avLst/></a:prstGeom>',
            '<a:noFill/>',
            stroke,
            xfrm_attr=flip_attr,
        )
    else:
        # ----------------------------------------------------------------
        # Custom geometry (original behaviour) for plain lines.
        # ----------------------------------------------------------------
        w = max(abs(x2 - x1), 1)
        h = max(abs(y2 - y1), 1)
        w_emu = px_to_emu(w)
        h_emu = px_to_emu(h)

        lx1 = px_to_emu(x1 - min_x)
        ly1 = px_to_emu(y1 - min_y)
        lx2 = px_to_emu(x2 - min_x)
        ly2 = px_to_emu(y2 - min_y)

        geom = (
            f'<a:custGeom>'
            f'<a:avLst/><a:gdLst/><a:ahLst/><a:cxnLst/>'
            f'<a:rect l="l" t="t" r="r" b="b"/>'
            f'<a:pathLst><a:path w="{w_emu}" h="{h_emu}">'
            f'<a:moveTo><a:pt x="{lx1}" y="{ly1}"/></a:moveTo>'
            f'<a:lnTo><a:pt x="{lx2}" y="{ly2}"/></a:lnTo>'
            f'</a:path></a:pathLst>'
            f'</a:custGeom>'
        )
        xml = _wrap_shape(
            shape_id, f'Line {shape_id}',
            off_x, off_y, w_emu, h_emu,
            geom, '<a:noFill/>', stroke,
        )

    return ShapeResult(xml=xml, bounds_emu=(off_x, off_y, off_x + w_emu, off_y + h_emu))


# ---------------------------------------------------------------------------
# path
# ---------------------------------------------------------------------------
