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



# Responsibility: elements_pictures; adapted from the original module.

def _picture_xfrm_from_rect(
    ctx: ConvertContext,
    x: float,
    y: float,
    w: float,
    h: float,
) -> tuple[str, int, int, int, int, tuple[int, int, int, int]]:
    """Build DrawingML xfrm data for a picture rectangle.

    Coordinates ``x``, ``y``, ``w``, ``h`` MUST already be in ctx-resolved
    space (i.e. callers have applied ``ctx_x`` / ``ctx_w`` upstream). When
    ``ctx.use_transform_matrix`` is set, raw SVG-space coordinates are
    expected and the matrix path applies the transform itself.
    """
    if ctx.use_transform_matrix:
        return rect_to_dml_xfrm(x, y, w, h, ctx.transform_matrix)

    off_x = px_to_emu(x)
    off_y = px_to_emu(y)
    ext_cx = px_to_emu(w)
    ext_cy = px_to_emu(h)
    return '', off_x, off_y, ext_cx, ext_cy, (off_x, off_y, off_x + ext_cx, off_y + ext_cy)


def _picture_xfrm_from_svg_rect(
    ctx: ConvertContext,
    raw_x: float,
    raw_y: float,
    raw_w: float,
    raw_h: float,
    resolved_x: float,
    resolved_y: float,
    resolved_w: float,
    resolved_h: float,
    transform: str | None,
) -> tuple[str, int, int, int, int, tuple[int, int, int, int]]:
    """Build picture xfrm data, honoring element-level SVG transforms.

    ``raw_*`` values stay in the element's source SVG coordinate space for
    matrix decomposition; ``resolved_*`` values are the existing scalar path.
    """
    if ctx.use_transform_matrix:
        matrix = ctx.transform_matrix
        if transform:
            matrix = matrix_multiply(matrix, parse_transform_matrix(transform))
        return rect_to_dml_xfrm(raw_x, raw_y, raw_w, raw_h, matrix)

    if transform:
        context_matrix = (
            ctx.scale_x, 0.0,
            0.0, ctx.scale_y,
            ctx.translate_x, ctx.translate_y,
        )
        matrix = matrix_multiply(context_matrix, parse_transform_matrix(transform))
        return rect_to_dml_xfrm(raw_x, raw_y, raw_w, raw_h, matrix)

    return _picture_xfrm_from_rect(ctx, resolved_x, resolved_y, resolved_w, resolved_h)


def _picture_rendered_frame_size(
    ctx: ConvertContext,
    raw_x: float,
    raw_y: float,
    raw_w: float,
    raw_h: float,
    resolved_x: float,
    resolved_y: float,
    resolved_w: float,
    resolved_h: float,
    transform: str | None,
) -> tuple[float, float]:
    """Return final DrawingML picture-axis lengths in rendered SVG pixels."""
    _attr, _x, _y, ext_cx, ext_cy, _bounds = _picture_xfrm_from_svg_rect(
        ctx,
        raw_x,
        raw_y,
        raw_w,
        raw_h,
        resolved_x,
        resolved_y,
        resolved_w,
        resolved_h,
        transform,
    )
    emu_per_px = px_to_emu(1.0)
    return ext_cx / emu_per_px, ext_cy / emu_per_px


def _read_image_size(data: bytes) -> tuple[float | None, float | None]:
    """Read intrinsic dimensions without transforming embedded PNG/JPEG bytes.

    EXIF orientation changes are rejected by the source contract, so the
    dimensions are the same dimensions used by native crop/meet geometry.
    """
    try:
        info = inspect_image(data)
    except ImageValidationError:
        return None, None
    return float(info.width), float(info.height)


def _optimize_image_for_pptx(
    ctx: ConvertContext,
    img_data: bytes,
    img_format: str,
    display_w: float,
    display_h: float,
    *,
    visible_source_fraction: tuple[float, float] | None = None,
) -> tuple[bytes, str]:
    """Preserve the validated source; pixel resampling is outside this port."""
    if ctx.image_optimize:
        raise ValueError('image optimization is unavailable in the standard-library edition; set image_optimize=False')
    return img_data, img_format

def _compute_slice_src_rect(
    img_w: float, img_h: float,
    box_w: float, box_h: float,
    align: str,
) -> tuple[int, int, int, int] | None:
    """Compute DrawingML ``<a:srcRect>`` (l, t, r, b) for SVG slice mode.

    SVG ``preserveAspectRatio="<align> slice"`` means: scale the image so it
    fully covers the box (CSS object-fit: cover) and crop the overflow at the
    given alignment anchor. DrawingML ``srcRect`` expresses the same intent
    by specifying which sub-rectangle of the source image to display, in
    units of 1/1000 of a percent (0–100000).

    Returns ``None`` when no cropping is required (image and box already
    match) or when inputs are degenerate.
    """
    if img_w <= 0 or img_h <= 0 or box_w <= 0 or box_h <= 0:
        return None

    # Scale factor that makes the image cover the box (cover semantics).
    scale = max(box_w / img_w, box_h / img_h)
    visible_w = box_w / scale  # ≤ img_w
    visible_h = box_h / scale  # ≤ img_h

    if (
        math.isclose(visible_w, img_w, rel_tol=1e-9, abs_tol=1e-9)
        and math.isclose(visible_h, img_h, rel_tol=1e-9, abs_tol=1e-9)
    ):
        return None  # No crop needed

    x_anchor, y_anchor = PROJECT_IMAGE_ASPECT_RATIO_ANCHORS[align]
    crop_w_total = max(
        0,
        min(99999, int(round((1.0 - visible_w / img_w) * 100000))),
    )
    crop_h_total = max(
        0,
        min(99999, int(round((1.0 - visible_h / img_h) * 100000))),
    )
    l = max(0, min(crop_w_total, int(round(crop_w_total * x_anchor))))
    t = max(0, min(crop_h_total, int(round(crop_h_total * y_anchor))))
    r = crop_w_total - l
    b = crop_h_total - t
    if not any((l, t, r, b)):
        return None

    return (l, t, r, b)


def _resolve_image_src_rect_values(
    elem: ET.Element,
    img_data: bytes,
    box_w: float, box_h: float,
) -> tuple[int, int, int, int] | None:
    """Resolve DrawingML source-crop values for an SVG slice image.

    Slice mode is resolved into a srcRect so the original image is embedded
    intact and PowerPoint's crop tool / "Reset Picture" continue to work.
    Meet mode is handled separately by ``_resolve_image_meet_fit`` (which
    shrinks the picture frame to match image aspect ratio); none mode keeps
    the legacy stretch behaviour intentionally.
    """
    align, mode = parse_project_image_aspect_ratio(
        elem.get('preserveAspectRatio')
    )

    if align == 'none' or mode != 'slice':
        return None

    img_w, img_h = _read_image_size(img_data)
    if img_w is None or img_h is None:
        return None

    return _compute_slice_src_rect(
        float(img_w),
        float(img_h),
        box_w,
        box_h,
        align,
    )


def _src_rect_xml(rect: tuple[int, int, int, int] | None) -> str:
    """Serialize an optional DrawingML source crop."""
    if rect is None:
        return ''
    l, t, r, b = rect
    return f'<a:srcRect l="{l}" t="{t}" r="{r}" b="{b}"/>'


def _resolve_image_meet_fit(
    elem: ET.Element,
    img_data: bytes,
    box_w: float, box_h: float,
) -> tuple[float, float, float, float] | None:
    """For SVG ``preserveAspectRatio="<align> meet"``, compute the letterboxed
    sub-rectangle ``(dx, dy, fit_w, fit_h)`` inside the original box that
    matches the image's intrinsic aspect ratio.

    PowerPoint has no native ``meet`` semantic — ``<a:stretch><a:fillRect/>``
    fills the entire frame and would distort the image whenever the SVG
    container ratio differs from the source image ratio. The fix is to shrink
    the ``<p:pic>`` frame itself (off + ext) so the frame and image share an
    aspect ratio; the stretch then fills a correctly-shaped frame.

    Returns ``None`` when the adjustment is not applicable:
      - mode is ``slice`` (handled by srcRect path)
      - align is ``none`` (SVG spec says: stretch — do not adjust)
      - intrinsic image dimensions cannot be read
      - frame already matches image ratio (no-op)
    """
    align, mode = parse_project_image_aspect_ratio(
        elem.get('preserveAspectRatio')
    )

    if align == 'none' or mode == 'slice':
        return None

    img_w, img_h = _read_image_size(img_data)
    if img_w is None or img_h is None or img_w <= 0 or img_h <= 0:
        return None
    if box_w <= 0 or box_h <= 0:
        return None

    scale = min(box_w / img_w, box_h / img_h)
    fit_w = img_w * scale
    fit_h = img_h * scale

    if abs(fit_w - box_w) < 0.5 and abs(fit_h - box_h) < 0.5:
        return None  # already matches — no adjustment

    x_anchor, y_anchor = PROJECT_IMAGE_ASPECT_RATIO_ANCHORS[align]

    dx = (box_w - fit_w) * x_anchor
    dy = (box_h - fit_h) * y_anchor

    return (dx, dy, fit_w, fit_h)


def _build_image_blip_xml(r_id: str, opacity: float | None) -> str:
    """Build an image blip with native DrawingML transparency when requested."""
    if opacity is None:
        return f'<a:blip r:embed="{r_id}"/>'
    alpha = quantize_ooxml_alpha(opacity)
    return (
        f'<a:blip r:embed="{r_id}">'
        f'<a:alphaModFix amt="{alpha}"/>'
        '</a:blip>'
    )


def _register_image_media(
    ctx: ConvertContext,
    img_format: str,
    img_data: bytes,
    *,
    reuse_text_fill: bool = False,
) -> str:
    """Register image bytes and return a DrawingML relationship id."""
    cache_key: tuple[str, str] | None = None
    if reuse_text_fill:
        cache_key = (img_format, hashlib.sha256(img_data).hexdigest())
        if cache_key in ctx.text_image_fill_cache:
            return ctx.text_image_fill_cache[cache_key]

    img_idx = len(ctx.media_files) + 1
    img_filename = f's{ctx.slide_num}_img{img_idx}.{img_format}'
    ctx.media_files[img_filename] = img_data
    r_id = ctx.next_rel_id()
    ctx.rel_entries.append({
        'id': r_id,
        'type': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/image',
        'target': f'../media/{img_filename}',
    })
    if cache_key is not None:
        ctx.text_image_fill_cache[cache_key] = r_id
    return r_id


def convert_image(elem: ET.Element, ctx: ConvertContext) -> ShapeResult | None:
    """Convert SVG <image> to DrawingML picture element.

    Supports clip-path attribute: when present, the clipPath shape is mapped
    to DrawingML picture geometry (prstGeom or custGeom) so the image is
    natively clipped in PowerPoint.
    """
    from .elements_shape_xml import _claim_element_shape_id
    from .elements_shape_xml import _element_effect_xml
    from .elements_clip_geometry import _resolve_clip_geometry
    from .elements_image_sources import load_project_image_source
    from .elements_shape_xml import shape_display_name
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
    # The standard-library edition preserves every source image byte; crop
    # metadata only changes the visible rectangle in PowerPoint.
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
    from .elements_preset_geometry import _build_preset_geom_from_meta
    from .elements_shape_xml import _claim_element_shape_id
    from .elements_preset_geometry import _shape_xfrm_from_preset_frame
    from .elements_shape_xml import _shape_xfrm_from_svg_rect
    from .elements_shape_xml import _wrap_geometry_object
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
