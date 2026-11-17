"""SVG element converters: rect, circle, line, path, polygon, polyline, text, image, ellipse."""

from __future__ import annotations

from xml.etree import ElementTree as ET


from .context import (
    ConvertContext,
    ShapeResult,
)
from .utils import (
    px_to_emu,
    _get_attr,
    svg_length_x,
    svg_length_y,
    get_effective_filter_id,
)
from .styles import (
    build_fill_xml,
    build_stroke_xml,
    build_effect_xml,
    get_element_opacity,
    get_fill_opacity,
    get_stroke_opacity,
)
from .paths import (
    PathCommand, parse_svg_path, parse_svg_points, svg_path_to_absolute,
    normalize_path_commands, path_commands_to_drawingml,
    transform_path_commands,
)



# Adapted from elements.py; upstream MIT, Hugo He, 2025-2026.
def convert_line(elem: ET.Element, ctx: ConvertContext) -> ShapeResult | None:
    """Convert SVG <line> to DrawingML shape.

    Lines with marker-start / marker-end are converted using the 'line' preset
    geometry (prstGeom prst="line") so that PowerPoint renders native arrow
    heads (headEnd / tailEnd) correctly.  Plain lines (no markers) continue to
    use custom geometry which is sufficient and avoids flipH/flipV complexity.
    """
    # Resolve shared symbols at call time; retain the public module contract.
    from .elements import (
        _build_preset_geom_from_meta,
        _claim_element_shape_id,
        _shape_xfrm_from_preset_frame,
        _transformed_point,
        _uses_full_transform,
        _wrap_geometry_object,
        _wrap_shape,
    )
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

def convert_path(elem: ET.Element, ctx: ConvertContext) -> ShapeResult | None:
    """Convert SVG <path> to DrawingML custom geometry shape."""
    # Resolve shared symbols at call time; retain the public module contract.
    from .elements import (
        _build_preserved_custom_geom,
        _build_preset_geom_from_meta,
        _claim_element_shape_id,
        _combined_transform_matrix,
        _pathlike_preset_xfrm,
        _uses_full_transform,
        _wrap_geometry_object,
    )
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
    # Resolve shared symbols at call time; retain the public module contract.
    from .elements import (
        _build_preset_geom_from_meta,
        _claim_element_shape_id,
        _combined_transform_matrix,
        _pathlike_preset_xfrm,
        _uses_full_transform,
        _wrap_geometry_object,
    )
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
    # Resolve shared symbols at call time; retain the public module contract.
    from .elements import (
        _build_preset_geom_from_meta,
        _claim_element_shape_id,
        _combined_transform_matrix,
        _pathlike_preset_xfrm,
        _uses_full_transform,
        _wrap_geometry_object,
    )
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
