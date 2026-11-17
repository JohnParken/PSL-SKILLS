"""SVG element converters: rect, circle, line, path, polygon, polyline, text, image, ellipse."""

from __future__ import annotations

import base64
import binascii
import re
from xml.etree import ElementTree as ET

from pptx_shapes import (
    has_relationship_attributes,
    validate_ooxml_xfrm,
)
from pptx_effects import (
    preserved_native_effect_xml,
)

from .context import (
    ConvertContext,
)
from .utils import (
    px_to_emu,
    ctx_x,
    ctx_y,
    rect_to_dml_xfrm,
    get_effective_filter_id,
    matrix_multiply,
    parse_transform_matrix,
    transform_point,
    _xml_escape,
)
from .styles import (
    build_effect_xml,
    get_element_opacity,
)



# Adapted from elements.py; upstream MIT, Hugo He, 2025-2026.
def shape_display_name(elem: ET.Element, fallback: str) -> str:
    """Name a PowerPoint object after its SVG identity when it has one.

    ``data-pptx-shape-name`` wins, then the SVG ``id`` (or ``data-name``), so
    the Selection and Animation panes show ``p08-rail-edge`` rather than
    ``Freeform 9``; unnamed objects keep the positional fallback.
    """
    for attribute in ('data-pptx-shape-name', 'id', 'data-name'):
        value = (elem.get(attribute) or '').strip()
        if value:
            return value
    return fallback


def _wrap_shape(
    shape_id: int, name: str,
    off_x: int, off_y: int,
    ext_cx: int, ext_cy: int,
    geom_xml: str, fill_xml: str, stroke_xml: str,
    effect_xml: str = '', extra_xml: str = '',
    rot: int = 0,
    xfrm_attr: str = '',
    placeholder_xml: str = '',
) -> str:
    """Wrap DrawingML content into a <p:sp> shape element."""
    rot_attr = f' rot="{rot}"' if rot else ''
    xfrm_attrs = f'{xfrm_attr}{rot_attr}'
    return f'''<p:sp>
<p:nvSpPr>
<p:cNvPr id="{shape_id}" name="{_xml_escape(name)}"/>
<p:cNvSpPr/><p:nvPr>{placeholder_xml}</p:nvPr>
</p:nvSpPr>
<p:spPr>
<a:xfrm{xfrm_attrs}><a:off x="{off_x}" y="{off_y}"/><a:ext cx="{ext_cx}" cy="{ext_cy}"/></a:xfrm>
{geom_xml}
{fill_xml}
{stroke_xml}
{effect_xml}
</p:spPr>
{extra_xml}
</p:sp>'''


def _wrap_connector(
    shape_id: int,
    name: str,
    off_x: int,
    off_y: int,
    ext_cx: int,
    ext_cy: int,
    geom_xml: str,
    fill_xml: str,
    stroke_xml: str,
    effect_xml: str = '',
    rot: int = 0,
    xfrm_attr: str = '',
    connection_xml: str = '',
    extra_xml: str = '',
) -> str:
    """Wrap DrawingML content into a native ``p:cxnSp`` connector."""
    rot_attr = f' rot="{rot}"' if rot else ''
    xfrm_attrs = f'{xfrm_attr}{rot_attr}'
    return f'''<p:cxnSp>
<p:nvCxnSpPr>
<p:cNvPr id="{shape_id}" name="{_xml_escape(name)}"/>
<p:cNvCxnSpPr>{connection_xml}</p:cNvCxnSpPr><p:nvPr/>
</p:nvCxnSpPr>
<p:spPr>
<a:xfrm{xfrm_attrs}><a:off x="{off_x}" y="{off_y}"/><a:ext cx="{ext_cx}" cy="{ext_cy}"/></a:xfrm>
{geom_xml}
{fill_xml}
{stroke_xml}
{effect_xml}
</p:spPr>
{extra_xml}
</p:cxnSp>'''


def _wrap_geometry_object(
    elem: ET.Element,
    ctx: ConvertContext,
    shape_id: int,
    name: str,
    off_x: int,
    off_y: int,
    ext_cx: int,
    ext_cy: int,
    geom_xml: str,
    fill_xml: str,
    stroke_xml: str,
    effect_xml: str = '',
    xfrm_attr: str = '',
) -> str:
    """Wrap a semantic leaf as a shape or connector without guessing."""
    # Resolve shared symbols at call time; retain the public module contract.
    from .elements import (
        _connector_connection_xml,
        _decode_shape_style,
        _element_effect_xml,
        _imported_placeholder_xml,
        _wrap_connector,
        _wrap_shape,
        shape_display_name,
    )
    if not effect_xml:
        effect_xml = _element_effect_xml(elem, ctx)
    name = shape_display_name(elem, name)
    shape_style_xml = _decode_shape_style(elem)
    object_kind = elem.get('data-pptx-object')
    if object_kind != 'connector':
        return _wrap_shape(
            shape_id,
            name,
            off_x,
            off_y,
            ext_cx,
            ext_cy,
            geom_xml,
            fill_xml,
            stroke_xml,
            effect_xml,
            extra_xml=shape_style_xml,
            xfrm_attr=xfrm_attr,
            placeholder_xml=_imported_placeholder_xml(elem),
        )

    prst = elem.get('data-pptx-prst')
    is_custom = elem.get('data-pptx-geometry-kind') == 'custom'
    if prst is None and not is_custom:
        raise ValueError(
            'data-pptx-object="connector" requires preset or preserved '
            'custom geometry'
        )
    return _wrap_connector(
        shape_id,
        name,
        off_x,
        off_y,
        ext_cx,
        ext_cy,
        geom_xml,
        fill_xml,
        stroke_xml,
        effect_xml,
        xfrm_attr=xfrm_attr,
        connection_xml=_connector_connection_xml(elem, ctx),
        extra_xml=shape_style_xml,
    )


def _imported_placeholder_xml(elem: ET.Element) -> str:
    """Restore an imported slide placeholder marker when its identity is exact."""
    placeholder_type = elem.get('data-ph-type')
    placeholder_index = elem.get('data-pptx-placeholder-index')
    if placeholder_type is None and placeholder_index is None:
        return ''
    if placeholder_type is not None and not re.fullmatch(r'[A-Za-z][A-Za-z0-9]*', placeholder_type):
        raise ValueError(
            f'Invalid imported placeholder type: {placeholder_type!r}'
        )
    if placeholder_index is not None and (
        not placeholder_index.isdigit() or int(placeholder_index) > 0xFFFFFFFF
    ):
        raise ValueError(
            f'Invalid imported placeholder index: {placeholder_index!r}'
        )
    attrs = {name: value for name, value in (('type', placeholder_type), ('idx', placeholder_index))
             if value is not None}
    placeholder_size = elem.get('data-pptx-placeholder-size')
    if placeholder_size is not None:
        if placeholder_size not in {'full', 'half', 'quarter'}:
            raise ValueError(
                f'Invalid imported placeholder size: {placeholder_size!r}'
            )
        attrs['sz'] = placeholder_size
    orientation = elem.get('data-pptx-placeholder-orientation')
    if orientation is not None:
        if orientation not in {'horz', 'vert'}:
            raise ValueError(
                f'Invalid imported placeholder orientation: {orientation!r}'
            )
        attrs['orient'] = orientation
    serialized = ' '.join(
        f'{name}="{_xml_escape(value)}"'
        for name, value in attrs.items()
    )
    return f'<p:ph {serialized}/>'


def _element_effect_xml(elem: ET.Element, ctx: ConvertContext) -> str:
    """Honor an authored SVG filter before the imported native fallback."""
    filt_id = get_effective_filter_id(elem, ctx)
    if filt_id and filt_id in ctx.defs:
        return build_effect_xml(
            ctx.defs[filt_id],
            get_element_opacity(elem, ctx),
        )
    return preserved_native_effect_xml(elem) or ''


def _decode_shape_style(elem: ET.Element) -> str:
    encoded = elem.get('data-pptx-shape-style')
    if not encoded:
        return ''
    try:
        raw = base64.b64decode(encoded, validate=True)
        style = ET.fromstring(raw)
        decoded = raw.decode('utf-8')
    except (ValueError, binascii.Error, UnicodeDecodeError, ET.ParseError) as exc:
        raise ValueError(f'Invalid shape-style metadata: {exc}') from exc
    if style.tag != (
        '{http://schemas.openxmlformats.org/presentationml/2006/main}style'
    ):
        raise ValueError('Shape-style metadata payload must be p:style')
    if has_relationship_attributes(style):
        raise ValueError(
            'Shape-style metadata must not contain relationship attributes'
        )
    return decoded


def _connector_connection_xml(elem: ET.Element, ctx: ConvertContext) -> str:
    """Restore connector endpoint attachment using the reserved source id map."""
    parts: list[str] = []
    for endpoint, tag in (('start', 'stCxn'), ('end', 'endCxn')):
        raw_shape_id = elem.get(f'data-pptx-{endpoint}-shape-id')
        raw_site = elem.get(f'data-pptx-{endpoint}-site')
        if raw_shape_id is None and raw_site is None:
            continue
        if raw_shape_id is None or raw_site is None:
            raise ValueError(
                f'Connector {endpoint} endpoint requires both shape-id and site'
            )
        target_scope = (
            elem.get(f'data-pptx-{endpoint}-shape-scope')
            or elem.get('data-pptx-shape-scope')
            or 'slide'
        )
        target_id = ctx.reference_shape_id(raw_shape_id, target_scope)
        try:
            site = int(raw_site)
        except ValueError as exc:
            raise ValueError(
                f'Invalid connector {endpoint} site {raw_site!r}'
            ) from exc
        if site < 0 or site > 0xFFFFFFFF:
            raise ValueError(
                f'Connector {endpoint} site is outside unsigned integer range: {site}'
            )
        parts.append(f'<a:{tag} id="{target_id}" idx="{site}"/>')
    return ''.join(parts)


def _claim_element_shape_id(elem: ET.Element, ctx: ConvertContext) -> int:
    return ctx.claim_shape_id(
        elem.get('data-pptx-shape-id'),
        elem.get('data-pptx-shape-scope'),
    )


def _context_transform_matrix(ctx: ConvertContext) -> tuple[float, float, float, float, float, float]:
    """Return the current context as a full SVG affine matrix."""
    if ctx.use_transform_matrix:
        return ctx.transform_matrix
    return (
        ctx.scale_x, 0.0,
        0.0, ctx.scale_y,
        ctx.translate_x, ctx.translate_y,
    )


def _combined_transform_matrix(
    ctx: ConvertContext,
    transform: str | None,
) -> tuple[float, float, float, float, float, float]:
    """Compose context transform with an element-level transform attribute."""
    # Resolve shared symbols at call time; retain the public module contract.
    from .elements import (
        _context_transform_matrix,
    )
    matrix = _context_transform_matrix(ctx)
    if transform:
        matrix = matrix_multiply(matrix, parse_transform_matrix(transform))
    return matrix


def _uses_full_transform(ctx: ConvertContext, transform: str | None) -> bool:
    return ctx.use_transform_matrix or bool(transform)


def _transformed_point(
    ctx: ConvertContext,
    x: float,
    y: float,
    transform: str | None,
) -> tuple[float, float]:
    # Resolve shared symbols at call time; retain the public module contract.
    from .elements import (
        _combined_transform_matrix,
        _uses_full_transform,
    )
    if _uses_full_transform(ctx, transform):
        return transform_point(_combined_transform_matrix(ctx, transform), x, y)
    return ctx_x(x, ctx), ctx_y(y, ctx)


def _shape_xfrm_from_svg_rect(
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
    *,
    preserve_degenerate_axes: bool = False,
) -> tuple[str, int, int, int, int, tuple[int, int, int, int]]:
    """Build DrawingML xfrm data for an SVG rectangle-like element."""
    # Resolve shared symbols at call time; retain the public module contract.
    from .elements import (
        _combined_transform_matrix,
        _uses_full_transform,
    )
    if _uses_full_transform(ctx, transform):
        return rect_to_dml_xfrm(
            raw_x, raw_y, raw_w, raw_h,
            _combined_transform_matrix(ctx, transform),
            preserve_degenerate_axes=preserve_degenerate_axes,
        )

    off_x = px_to_emu(resolved_x)
    off_y = px_to_emu(resolved_y)
    ext_cx = px_to_emu(resolved_w)
    ext_cy = px_to_emu(resolved_h)
    validate_ooxml_xfrm(off_x, off_y, ext_cx, ext_cy)
    return '', off_x, off_y, ext_cx, ext_cy, (off_x, off_y, off_x + ext_cx, off_y + ext_cy)


# ---------------------------------------------------------------------------
# rect
# ---------------------------------------------------------------------------

# Cubic-Bézier control distance for approximating a quarter circle / ellipse.
# Distance from corner to control point along the tangent, expressed as a
# fraction of the radius. Standard "magic number" for a 90° arc (max error
# ~0.027% of the radius).
