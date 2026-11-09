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



# Responsibility: elements_nested_crops; adapted from the original module.

@dataclass(frozen=True)
class NestedSvgCropSpec:
    """Validated transport fields for one imported cropped picture."""

    image: ET.Element
    x: float
    y: float
    width: float
    height: float
    view_box_x: float
    view_box_y: float
    view_box_width: float
    view_box_height: float
    src_l: int
    src_t: int
    src_r: int
    src_b: int


_NESTED_CROP_OUTER_ATTRIBUTES = frozenset({
    'clip-path',
    'data-pptx-crop',
    'data-pptx-editable',
    EFFECT_REASON_ATTR,
    EFFECT_STATUS_ATTR,
    NATIVE_EFFECT_ATTR,
    NATIVE_EFFECT_SHA256_ATTR,
    'data-pptx-frame',
    'data-pptx-layer',
    'data-pptx-object',
    'data-pptx-carrier',
    'data-pptx-prst',
    'data-pptx-shape-id',
    'data-pptx-shape-name',
    'data-pptx-shape-scope',
    'id',
    'overflow',
    'preserveAspectRatio',
    'transform',
    'viewBox',
    'x',
    'y',
    'width',
    'height',
})
_NESTED_CROP_IMAGE_ATTRIBUTES = frozenset({
    'clip-path',
    'href',
    f'{{{XLINK_NS}}}href',
    'opacity',
    'preserveAspectRatio',
    'x',
    'y',
    'width',
    'height',
})
_DRAWINGML_PERCENTAGE_MIN = -(2 ** 31)
_DRAWINGML_PERCENTAGE_MAX = 2 ** 31 - 1


def _unsupported_nested_crop_attributes(
    elem: ET.Element,
    allowed: frozenset[str],
) -> list[str]:
    unsupported = []
    for name in elem.attrib:
        if name in allowed:
            continue
        unsupported.append(name.rsplit('}', 1)[-1])
    return sorted(unsupported)


def parse_project_nested_svg_crop(elem: ET.Element) -> NestedSvgCropSpec:
    """Parse the closed nested-SVG transport written by ``pptx_to_svg``."""
    from .elements_image_sources import _project_image_href
    if elem.tag != f'{{{SVG_NS}}}svg':
        raise ValueError('expected an SVG-namespace nested <svg> crop wrapper')

    unsupported = _unsupported_nested_crop_attributes(
        elem,
        _NESTED_CROP_OUTER_ATTRIBUTES,
    )
    if unsupported:
        raise ValueError(
            'nested crop <svg> has unsupported attribute(s): '
            + ', '.join(unsupported)
        )
    crop_marker = elem.get('data-pptx-crop')
    overflow = elem.get('overflow')
    if overflow is not None and overflow != 'hidden':
        raise ValueError(
            'nested crop overflow must be exactly "hidden" when present'
        )
    if elem.text and elem.text.strip():
        raise ValueError(
            'nested crop <svg> cannot contain non-whitespace character data'
        )

    children = list(elem)
    if (
        len(children) != 1
        or children[0].tag != f'{{{SVG_NS}}}image'
    ):
        raise ValueError(
            'nested <svg> is reserved for imported picture crops; expected '
            'exactly one direct SVG-namespace <image> child'
        )
    image_elem = children[0]
    if image_elem.tail and image_elem.tail.strip():
        raise ValueError(
            'nested crop <svg> cannot contain non-whitespace character data'
        )
    if list(image_elem) or (image_elem.text and image_elem.text.strip()):
        raise ValueError('nested crop <image> must be an empty element')

    unsupported = _unsupported_nested_crop_attributes(
        image_elem,
        _NESTED_CROP_IMAGE_ATTRIBUTES,
    )
    if unsupported:
        raise ValueError(
            'nested crop <image> has unsupported attribute(s): '
            + ', '.join(unsupported)
        )

    outer_clip_path = elem.get('clip-path')
    inner_clip_path = image_elem.get('clip-path')
    if outer_clip_path is not None and inner_clip_path is not None:
        raise ValueError(
            'nested crop clip-path must occur on either the outer <svg> or '
            'the inner <image>, not both'
        )
    clip_path = inner_clip_path or outer_clip_path
    if crop_marker is not None and crop_marker != '1':
        raise ValueError('nested crop data-pptx-crop must be exactly "1"')
    if clip_path is None:
        if crop_marker is not None:
            raise ValueError(
                'nested crop data-pptx-crop="1" requires clip-path'
            )
    elif clip_path.strip().lower() == 'none':
        raise ValueError('nested crop clip-path cannot be "none"')
    elif crop_marker != '1':
        raise ValueError(
            'nested crop clip-path requires data-pptx-crop="1"'
        )
    if inner_clip_path is not None and overflow != 'hidden':
        raise ValueError(
            'nested crop with inner <image> clip-path requires '
            'overflow="hidden" on the outer <svg>'
        )

    try:
        _project_image_href(image_elem)
    except ValueError as exc:
        raise ValueError(f'nested crop <image> {exc}') from exc

    required_outer = (
        'x',
        'y',
        'width',
        'height',
        'viewBox',
        'preserveAspectRatio',
    )
    missing = [name for name in required_outer if elem.get(name) is None]
    if missing:
        raise ValueError(
            'nested crop <svg> requires explicit x, y, width, height, '
            'viewBox, and preserveAspectRatio="none"; missing '
            + ', '.join(missing)
        )
    if elem.get('preserveAspectRatio') != 'none':
        raise ValueError(
            'nested crop <svg> preserveAspectRatio must be exactly "none"'
        )

    frame_values: dict[str, float] = {}
    for name in ('x', 'y', 'width', 'height'):
        raw = elem.get(name)
        assert raw is not None
        try:
            frame_values[name] = parse_project_geometry_length(raw, name)
        except ValueError as exc:
            raise ValueError(
                f'nested crop <svg> {name}={raw!r}: {exc}'
            ) from exc
    if frame_values['width'] <= 0 or frame_values['height'] <= 0:
        raise ValueError('nested crop <svg> width and height must be positive')

    view_box = elem.get('viewBox') or ''
    view_box_tokens = view_box.strip().split()
    if (
        len(view_box_tokens) != 4
        or any(
            not is_canonical_project_geometry_length(token)
            for token in view_box_tokens
        )
    ):
        raise ValueError(
            'nested crop viewBox must contain four finite unitless ordinary '
            'decimals separated by whitespace'
        )
    vb_x, vb_y, vb_w, vb_h = (
        parse_project_geometry_length(token, 'x')
        for token in view_box_tokens
    )
    if vb_w <= 0 or vb_h <= 0:
        raise ValueError('nested crop viewBox width and height must be positive')
    src_l = round(vb_x * 100000)
    src_t = round(vb_y * 100000)
    src_r = round((1.0 - vb_x - vb_w) * 100000)
    src_b = round((1.0 - vb_y - vb_h) * 100000)
    src_values = (src_l, src_t, src_r, src_b)
    if (
        any(
            value < _DRAWINGML_PERCENTAGE_MIN
            or value > _DRAWINGML_PERCENTAGE_MAX
            for value in src_values
        )
        or src_l + src_r >= 100000
        or src_t + src_b >= 100000
    ):
        raise ValueError(
            'nested crop viewBox cannot be represented as a DrawingML '
            'srcRect with a positive visible region within the signed '
            'percentage range'
        )
    if not any(src_values):
        raise ValueError(
            'nested crop viewBox="0 0 1 1" is redundant; use a plain <image>'
        )

    required_image_values = {
        'x': '0',
        'y': '0',
        'width': '1',
        'height': '1',
        'preserveAspectRatio': 'none',
    }
    invalid_image_values = [
        f'{name}={image_elem.get(name)!r}'
        for name, expected in required_image_values.items()
        if image_elem.get(name) != expected
    ]
    if invalid_image_values:
        raise ValueError(
            'nested crop <image> must use x="0", y="0", width="1", '
            'height="1", and preserveAspectRatio="none"; got '
            + ', '.join(invalid_image_values)
        )

    return NestedSvgCropSpec(
        image=image_elem,
        x=frame_values['x'],
        y=frame_values['y'],
        width=frame_values['width'],
        height=frame_values['height'],
        view_box_x=vb_x,
        view_box_y=vb_y,
        view_box_width=vb_w,
        view_box_height=vb_h,
        src_l=src_l,
        src_t=src_t,
        src_r=src_r,
        src_b=src_b,
    )


def project_nested_svg_crop_errors(root: ET.Element) -> list[str]:
    """Return contract errors for every nested SVG transport wrapper."""
    errors: list[str] = []
    parent_by_id = {
        id(child): parent
        for parent in root.iter()
        for child in list(parent)
    }
    for elem in root.iter():
        if elem is root or elem.tag.rsplit('}', 1)[-1] != 'svg':
            continue
        elem_id = (elem.get('id') or '').strip()
        label = f'<svg id="{elem_id}">' if elem_id else '<svg>'
        ancestor = parent_by_id.get(id(elem))
        invalid_ancestor: ET.Element | None = None
        while ancestor is not None and ancestor is not root:
            if (
                ancestor.tag != f'{{{SVG_NS}}}g'
                or ancestor.get('data-pptx-part') is not None
            ):
                invalid_ancestor = ancestor
                break
            ancestor = parent_by_id.get(id(ancestor))
        if invalid_ancestor is not None:
            errors.append(
                f'{label} invalid imported crop wrapper: visual ancestor chain '
                'may contain only ordinary <g> elements'
            )
            continue
        try:
            parse_project_nested_svg_crop(elem)
        except ValueError as exc:
            errors.append(f'{label} invalid imported crop wrapper: {exc}')
    return sorted(errors)


def _resolve_nested_svg_clip_geometry(
    crop: NestedSvgCropSpec,
    image_elem: ET.Element,
    ctx: ConvertContext,
) -> str:
    """Resolve a preview-safe inner-image clip into picture geometry."""
    from .elements_clip_geometry import _resolve_clip_geometry
    from .elements_clip_geometry import _visible_clip_shapes
    default = '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom>'
    clip_id = resolve_url_id(image_elem.get('clip-path', ''))
    if not clip_id or clip_id not in ctx.defs:
        return default
    clip_elem = ctx.defs[clip_id]
    shapes = _visible_clip_shapes(clip_elem, ctx.parent_by_id)
    if not shapes:
        return default
    if len(shapes) != 1:
        raise ValueError('clipPath must contain exactly one direct supported shape')
    shape = shapes[0]
    if (
        clip_elem.get('clipPathUnits', 'userSpaceOnUse')
        != 'userSpaceOnUse'
    ):
        return _resolve_clip_geometry(
            image_elem,
            ctx,
            crop.x,
            crop.y,
            crop.width,
            crop.height,
        )

    shape_tag = shape.tag.rsplit('}', 1)[-1]
    if shape_tag == 'rect':
        style_values = parse_inline_style(shape.get('style'))
        rx_raw = style_values.get('rx') or shape.get('rx')
        ry_raw = style_values.get('ry') or shape.get('ry')
        rx = (
            parse_project_geometry_length(rx_raw, 'rx')
            if rx_raw is not None else None
        )
        ry = (
            parse_project_geometry_length(ry_raw, 'ry')
            if ry_raw is not None else None
        )
        if rx is None and ry is not None:
            rx = ry
        elif ry is None and rx is not None:
            ry = rx
        rx = rx or 0.0
        ry = ry or 0.0
        if rx <= 0 and ry <= 0:
            return default
        physical_rx = rx * crop.width / crop.view_box_width
        physical_ry = ry * crop.height / crop.view_box_height
        radius = (physical_rx + physical_ry) / 2.0
        shorter = min(crop.width, crop.height)
        if shorter <= 0:
            return default
        adj = int(min(radius / (shorter / 2.0), 1.0) * 50000)
        return (
            f'<a:prstGeom prst="roundRect"><a:avLst>'
            f'<a:gd name="adj" fmla="val {adj}"/>'
            f'</a:avLst></a:prstGeom>'
        )

    return _resolve_clip_geometry(
        image_elem,
        ctx,
        crop.view_box_x,
        crop.view_box_y,
        crop.view_box_width,
        crop.view_box_height,
    )


def convert_nested_svg(elem: ET.Element, ctx: ConvertContext) -> ShapeResult | None:
    """Convert a nested <svg> sprite-crop wrapper to a DrawingML picture.

    Pattern produced by pptx_to_svg::

        <svg x="10" y="20" width="200" height="300" viewBox="0.5 0.3 0.5 0.7">
          <image href="..." x="0" y="0" width="1" height="1" preserveAspectRatio="none"/>
        </svg>

    The viewBox crops the unit-rectangle inner image; that crop is mapped to a
    DrawingML <a:srcRect> so PowerPoint can re-crop / "Reset Picture".
    """
    from .elements_pictures import _build_image_blip_xml
    from .elements_shape_xml import _claim_element_shape_id
    from .elements_shape_xml import _element_effect_xml
    from .elements_pictures import _optimize_image_for_pptx
    from .elements_pictures import _picture_rendered_frame_size
    from .elements_pictures import _picture_xfrm_from_svg_rect
    from .elements_pictures import _register_image_media
    from .elements_clip_geometry import _resolve_clip_geometry
    from .elements_clip_geometry import empty_clip_path_reason
    from .elements_image_sources import load_project_image_source
    from .elements_shape_xml import shape_display_name
    crop = parse_project_nested_svg_crop(elem)
    image_elem = crop.image
    if empty_clip_path_reason(image_elem, ctx.defs, ctx.parent_by_id):
        return None
    source = load_project_image_source(
        image_elem,
        ctx.svg_dir,
        ctx.resource_root,
    )

    svg_x = crop.x
    svg_y = crop.y
    svg_w = crop.width
    svg_h = crop.height

    if ctx.use_transform_matrix:
        x = svg_x
        y = svg_y
        w = svg_w
        h = svg_h
    else:
        x = ctx_x(svg_x, ctx)
        y = ctx_y(svg_y, ctx)
        w = ctx_w(svg_w, ctx)
        h = ctx_h(svg_h, ctx)

    src_rect_xml = (
        f'<a:srcRect l="{crop.src_l}" t="{crop.src_t}" '
        f'r="{crop.src_r}" b="{crop.src_b}"/>'
    )

    img_format = source.img_format
    img_data = source.img_data

    transform = elem.get('transform')
    rendered_w, rendered_h = _picture_rendered_frame_size(
        ctx,
        svg_x,
        svg_y,
        svg_w,
        svg_h,
        x,
        y,
        w,
        h,
        transform,
    )
    img_data, img_format = _optimize_image_for_pptx(
        ctx,
        img_data,
        img_format,
        rendered_w,
        rendered_h,
        visible_source_fraction=(
            1.0 - (crop.src_l + crop.src_r) / 100000.0,
            1.0 - (crop.src_t + crop.src_b) / 100000.0,
        ),
    )

    r_id = _register_image_media(ctx, img_format, img_data)

    shape_id = _claim_element_shape_id(elem, ctx)
    xfrm_attr, off_x, off_y, ext_cx, ext_cy, bounds_emu = _picture_xfrm_from_svg_rect(
        ctx,
        svg_x,
        svg_y,
        svg_w,
        svg_h,
        x,
        y,
        w,
        h,
        transform,
    )
    if image_elem.get('clip-path') is not None:
        clip_geom = _resolve_nested_svg_clip_geometry(crop, image_elem, ctx)
    else:
        clip_geom = _resolve_clip_geometry(
            elem,
            ctx,
            svg_x,
            svg_y,
            svg_w,
            svg_h,
        )
    effect_xml = _element_effect_xml(elem, ctx)
    blip_xml = _build_image_blip_xml(
        r_id,
        get_element_opacity(image_elem, ctx),
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
