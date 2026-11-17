"""SVG element converters: rect, circle, line, path, polygon, polyline, text, image, ellipse."""

from __future__ import annotations

from xml.etree import ElementTree as ET


from .context import (
    ConvertContext,
    ShapeResult,
)
from .utils import (
    svg_length_x,
    svg_length_y,
    ctx_x,
    ctx_y,
    ctx_w,
    ctx_h,
    parse_project_image_aspect_ratio,
    is_stroked_zero_axis_shape,
    _xml_escape,
)
from .styles import (
    build_fill_xml,
    build_stroke_xml,
    get_element_opacity,
    get_fill_opacity,
    get_stroke_opacity,
)



# Adapted from elements.py; upstream MIT, Hugo He, 2025-2026.
def convert_image(elem: ET.Element, ctx: ConvertContext) -> ShapeResult | None:
    """Convert SVG <image> to DrawingML picture element.

    Supports clip-path attribute: when present, the clipPath shape is mapped
    to DrawingML picture geometry (prstGeom or custGeom) so the image is
    natively clipped in PowerPoint.
    """
    # Resolve shared symbols at call time; retain the public module contract.
    from .elements import (
        _build_image_blip_xml,
        _claim_element_shape_id,
        _element_effect_xml,
        _optimize_image_for_pptx,
        _picture_rendered_frame_size,
        _picture_xfrm_from_svg_rect,
        _register_image_media,
        _resolve_clip_geometry,
        _resolve_image_meet_fit,
        _resolve_image_src_rect_values,
        _src_rect_xml,
        load_project_image_source,
        shape_display_name,
    )
    source = load_project_image_source(elem, ctx.svg_dir, ctx.resource_root)

    # Raw coordinates (pre-context-transform) for clip path calculations
    raw_x = svg_length_x(elem.get('x'), ctx)
    raw_y = svg_length_y(elem.get('y'), ctx)
    raw_w = svg_length_x(elem.get('width'), ctx)
    raw_h = svg_length_y(elem.get('height'), ctx)

    if ctx.use_transform_matrix:
        x = raw_x
        y = raw_y
        w = raw_w
        h = raw_h
    else:
        x = ctx_x(raw_x, ctx)
        y = ctx_y(raw_y, ctx)
        w = ctx_w(raw_w, ctx)
        h = ctx_h(raw_h, ctx)

    if w <= 0 or h <= 0:
        raise ValueError('image width and height must be positive')

    img_format = source.img_format
    img_data = source.img_data

    transform = elem.get('transform')
    rendered_w, rendered_h = _picture_rendered_frame_size(
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
    align, mode = parse_project_image_aspect_ratio(
        elem.get('preserveAspectRatio')
    )
    src_rect = _resolve_image_src_rect_values(
        elem,
        img_data,
        raw_w,
        raw_h,
    )
    visible_source_fraction = None
    if align == 'none':
        visible_source_fraction = (1.0, 1.0)
    elif mode == 'slice':
        if src_rect is None:
            visible_source_fraction = (1.0, 1.0)
        else:
            src_l, src_t, src_r, src_b = src_rect
            visible_source_fraction = (
                1.0 - (src_l + src_r) / 100000.0,
                1.0 - (src_t + src_b) / 100000.0,
            )
    img_data, img_format = _optimize_image_for_pptx(
        ctx,
        img_data,
        img_format,
        rendered_w,
        rendered_h,
        visible_source_fraction=visible_source_fraction,
    )

    r_id = _register_image_media(ctx, img_format, img_data)

    # Resolve clip-path → DrawingML geometry
    clip_geom = _resolve_clip_geometry(elem, ctx, raw_x, raw_y, raw_w, raw_h)
    effect_xml = _element_effect_xml(elem, ctx)

    # Resolve preserveAspectRatio="<align> slice" as DrawingML crop metadata.
    # Image optimization only downscales the full source image; it never crops
    # pixels out of the embedded media.
    src_rect_xml = _src_rect_xml(src_rect)
    blip_xml = _build_image_blip_xml(r_id, get_element_opacity(elem, ctx))

    # Resolve preserveAspectRatio="<align> meet" by shrinking the picture
    # frame to match the image's aspect ratio. Skipped when a real clip-path
    # produces non-trivial geometry: such clip rectangles are defined against
    # the original box and would no longer line up after a frame shift.
    # A clip-path that resolves back to the default rect geometry (e.g. plain
    # <rect> without rx/ry) is a no-op and must not block meet adjustment.
    clip_is_noop = clip_geom == '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom>'
    meet_fit = None if not clip_is_noop else _resolve_image_meet_fit(elem, img_data, w, h)

    shape_id = _claim_element_shape_id(elem, ctx)
    if meet_fit is not None:
        dx, dy, fit_w, fit_h = meet_fit
        if ctx.use_transform_matrix:
            raw_fit_x = raw_x + dx
            raw_fit_y = raw_y + dy
            raw_fit_w = fit_w
            raw_fit_h = fit_h
        else:
            raw_fit_x = raw_x + (dx / ctx.scale_x if ctx.scale_x else dx)
            raw_fit_y = raw_y + (dy / ctx.scale_y if ctx.scale_y else dy)
            raw_fit_w = fit_w / ctx.scale_x if ctx.scale_x else fit_w
            raw_fit_h = fit_h / ctx.scale_y if ctx.scale_y else fit_h
        xfrm_attr, off_x, off_y, ext_cx, ext_cy, bounds_emu = _picture_xfrm_from_svg_rect(
            ctx,
            raw_fit_x,
            raw_fit_y,
            raw_fit_w,
            raw_fit_h,
            x + dx,
            y + dy,
            fit_w,
            fit_h,
            transform,
        )
    else:
        xfrm_attr, off_x, off_y, ext_cx, ext_cy, bounds_emu = _picture_xfrm_from_svg_rect(
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

    return ShapeResult(xml=f'''<p:pic>
<p:nvPicPr>
<p:cNvPr id="{shape_id}" name="{_xml_escape(shape_display_name(elem, f'Image {shape_id}'))}"/>
<p:cNvPicPr><a:picLocks noChangeAspect="1"/></p:cNvPicPr>
<p:nvPr/>
</p:nvPicPr>
<p:blipFill>
{blip_xml}
{src_rect_xml}<a:stretch><a:fillRect/></a:stretch>
</p:blipFill>
<p:spPr>
<a:xfrm{xfrm_attr}><a:off x="{off_x}" y="{off_y}"/>
<a:ext cx="{ext_cx}" cy="{ext_cy}"/></a:xfrm>
{clip_geom}
{effect_xml}
</p:spPr>
</p:pic>''', bounds_emu=bounds_emu)


# ---------------------------------------------------------------------------
# ellipse
# ---------------------------------------------------------------------------

def convert_ellipse(elem: ET.Element, ctx: ConvertContext) -> ShapeResult | None:
    """Convert SVG <ellipse> to DrawingML ellipse shape."""
    # Resolve shared symbols at call time; retain the public module contract.
    from .elements import (
        _build_preset_geom_from_meta,
        _claim_element_shape_id,
        _shape_xfrm_from_preset_frame,
        _shape_xfrm_from_svg_rect,
        _wrap_geometry_object,
    )
    preset_geom = _build_preset_geom_from_meta(elem, ctx)
    raw_cx = svg_length_x(elem.get('cx'), ctx)
    raw_cy = svg_length_y(elem.get('cy'), ctx)
    rx_attr = elem.get('rx')
    ry_attr = elem.get('ry')
    raw_rx = svg_length_x(rx_attr, ctx) if rx_attr is not None else 0.0
    raw_ry = svg_length_y(ry_attr, ctx) if ry_attr is not None else 0.0
    if rx_attr is not None and ry_attr is None:
        raw_ry = raw_rx
    elif ry_attr is not None and rx_attr is None:
        raw_rx = raw_ry
    cx_ = ctx_x(raw_cx, ctx)
    cy_ = ctx_y(raw_cy, ctx)
    rx = raw_rx * ctx.scale_x
    ry = raw_ry * ctx.scale_y

    if (rx <= 0 or ry <= 0) and not (
        preset_geom is not None and is_stroked_zero_axis_shape(elem, ctx)
    ):
        return None

    x = cx_ - rx
    y = cy_ - ry
    w = rx * 2
    h = ry * 2

    fill_op = get_fill_opacity(elem, ctx)
    stroke_op = get_stroke_opacity(elem, ctx)
    fill = build_fill_xml(elem, ctx, fill_op)
    stroke = build_stroke_xml(elem, ctx, stroke_op)

    geom = preset_geom or '<a:prstGeom prst="ellipse"><a:avLst/></a:prstGeom>'

    transform = elem.get('transform')

    shape_id = _claim_element_shape_id(elem, ctx)
    if preset_geom is not None:
        xfrm = _shape_xfrm_from_preset_frame(
            elem,
            ctx,
            (raw_cx - raw_rx, raw_cy - raw_ry, raw_rx * 2, raw_ry * 2),
            (x, y, w, h),
            transform,
        )
    else:
        xfrm = _shape_xfrm_from_svg_rect(
            ctx,
            raw_cx - raw_rx,
            raw_cy - raw_ry,
            raw_rx * 2,
            raw_ry * 2,
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
            geom, fill, stroke, xfrm_attr=xfrm_attr,
        ),
        bounds_emu=bounds_emu,
    )


# ---------------------------------------------------------------------------
# nested <svg> sprite (template-import round-trip)
# ---------------------------------------------------------------------------

# Inverse of pptx_to_svg/pic_to_svg.py:101-113 — that path writes a cropped
# DrawingML picture as an outer <svg viewBox> wrapping a unit-rectangle <image>.
# Without this converter, every cropped picture in a template-import SVG is
# silently dropped on re-export.
