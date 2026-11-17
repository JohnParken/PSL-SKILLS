"""SVG element converters: rect, circle, line, path, polygon, polyline, text, image, ellipse."""

from __future__ import annotations

import math
import re
from xml.etree import ElementTree as ET


from .context import (
    ConvertContext,
)
from .utils import (
    SVG_NS,
    px_to_emu,
    svg_length_x,
    svg_length_y,
    resolve_url_id,
    parse_inline_style,
    parse_project_geometry_length,
    project_definition_index,
    svg_hidden_reason,
)
from .paths import (
    PathCommand,
    parse_svg_path,
    parse_svg_points,
    svg_path_to_absolute,
    normalize_path_commands,
)



# Adapted from elements.py; upstream MIT, Hugo He, 2025-2026.
def _clip_commands_to_geom(
    commands: list[PathCommand],
    img_x: float, img_y: float,
    img_w: float, img_h: float,
    object_bbox: bool,
) -> str:
    """Convert clip path commands to DrawingML custGeom XML.

    Coordinates are transformed relative to the image bounding box so that
    (img_x, img_y) maps to (0, 0) and (img_x+img_w, img_y+img_h) maps to
    (w_emu, h_emu).
    """
    w_emu = px_to_emu(img_w)
    h_emu = px_to_emu(img_h)

    if w_emu <= 0 or h_emu <= 0:
        return '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom>'

    def _tx(x: float) -> int:
        if object_bbox:
            return int(x * w_emu)
        return px_to_emu(x - img_x)

    def _ty(y: float) -> int:
        if object_bbox:
            return int(y * h_emu)
        return px_to_emu(y - img_y)

    parts: list[str] = []
    for cmd in commands:
        if cmd.cmd == 'M':
            parts.append(
                f'<a:moveTo><a:pt x="{_tx(cmd.args[0])}" '
                f'y="{_ty(cmd.args[1])}"/></a:moveTo>'
            )
        elif cmd.cmd == 'L':
            parts.append(
                f'<a:lnTo><a:pt x="{_tx(cmd.args[0])}" '
                f'y="{_ty(cmd.args[1])}"/></a:lnTo>'
            )
        elif cmd.cmd == 'C':
            pts = ''.join(
                f'<a:pt x="{_tx(cmd.args[i])}" y="{_ty(cmd.args[i + 1])}"/>'
                for i in range(0, 6, 2)
            )
            parts.append(f'<a:cubicBezTo>{pts}</a:cubicBezTo>')
        elif cmd.cmd == 'Z':
            parts.append('<a:close/>')

    path_inner = '\n'.join(parts)
    return f'''<a:custGeom>
<a:avLst/><a:gdLst/><a:ahLst/><a:cxnLst/>
<a:rect l="l" t="t" r="r" b="b"/>
<a:pathLst><a:path w="{w_emu}" h="{h_emu}">
{path_inner}
</a:path></a:pathLst>
</a:custGeom>'''


def _element_contract_label(elem: ET.Element) -> str:
    tag = elem.tag.rsplit('}', 1)[-1]
    elem_id = (elem.get('id') or '').strip()
    return f'<{tag} id="{elem_id}">' if elem_id else f'<{tag}>'


def _unsupported_clip_rule_properties(elem: ET.Element) -> tuple[str, ...]:
    style_values = parse_inline_style(elem.get('style'))
    return tuple(
        name for name in ('clip-rule', 'fill-rule')
        if elem.get(name) is not None or name in style_values
    )


def _effective_clip_geometry_length(
    elem: ET.Element,
    attribute: str,
    *,
    default: float | None = None,
) -> float:
    style_values = parse_inline_style(elem.get('style'))
    raw = style_values.get(attribute)
    if raw is None:
        raw = elem.get(attribute)
    if raw is None:
        if default is None:
            raise ValueError(f'requires {attribute}')
        return default
    return parse_project_geometry_length(raw, attribute)


def _clip_preset_geometry_error(
    target: ET.Element,
    shape: ET.Element,
    clip_units: str,
) -> str | None:
    """Reject primitive clips that cannot map to a full-frame preset."""
    # Resolve shared symbols at call time; retain the public module contract.
    from .elements import (
        _effective_clip_geometry_length,
        _element_contract_label,
    )
    shape_tag = shape.tag.rsplit('}', 1)[-1].lower()
    if shape_tag not in {'circle', 'ellipse', 'rect'}:
        return None
    target_label = _element_contract_label(target)
    try:
        target_x = _effective_clip_geometry_length(target, 'x', default=0.0)
        target_y = _effective_clip_geometry_length(target, 'y', default=0.0)
        target_w = _effective_clip_geometry_length(target, 'width')
        target_h = _effective_clip_geometry_length(target, 'height')
    except ValueError as exc:
        return f'cannot validate {shape_tag} against {target_label}: {exc}'
    if target_w <= 0 or target_h <= 0:
        return (
            f'cannot validate {shape_tag} against {target_label}: target '
            'width and height must be positive'
        )

    object_bbox = clip_units == 'objectBoundingBox'
    expected_x = 0.0 if object_bbox else target_x
    expected_y = 0.0 if object_bbox else target_y
    expected_w = 1.0 if object_bbox else target_w
    expected_h = 1.0 if object_bbox else target_h

    def close(actual: float, expected: float) -> bool:
        return math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-6)

    try:
        if shape_tag == 'circle':
            cx = _effective_clip_geometry_length(shape, 'cx', default=0.0)
            cy = _effective_clip_geometry_length(shape, 'cy', default=0.0)
            radius = _effective_clip_geometry_length(shape, 'r', default=0.0)
            fits = (
                close(expected_w, expected_h)
                and close(cx, expected_x + expected_w / 2.0)
                and close(cy, expected_y + expected_h / 2.0)
                and close(radius, expected_w / 2.0)
            )
        elif shape_tag == 'ellipse':
            cx = _effective_clip_geometry_length(shape, 'cx', default=0.0)
            cy = _effective_clip_geometry_length(shape, 'cy', default=0.0)
            rx = _effective_clip_geometry_length(shape, 'rx', default=0.0)
            ry = _effective_clip_geometry_length(shape, 'ry', default=0.0)
            fits = (
                close(cx, expected_x + expected_w / 2.0)
                and close(cy, expected_y + expected_h / 2.0)
                and close(rx, expected_w / 2.0)
                and close(ry, expected_h / 2.0)
            )
        else:
            rect_x = _effective_clip_geometry_length(shape, 'x', default=0.0)
            rect_y = _effective_clip_geometry_length(shape, 'y', default=0.0)
            rect_w = _effective_clip_geometry_length(shape, 'width', default=0.0)
            rect_h = _effective_clip_geometry_length(shape, 'height', default=0.0)
            fits = (
                close(rect_x, expected_x)
                and close(rect_y, expected_y)
                and close(rect_w, expected_w)
                and close(rect_h, expected_h)
            )
            if fits:
                rx_raw = (
                    parse_inline_style(shape.get('style')).get('rx')
                    or shape.get('rx')
                )
                ry_raw = (
                    parse_inline_style(shape.get('style')).get('ry')
                    or shape.get('ry')
                )
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
                if rx > 0 or ry > 0:
                    if object_bbox:
                        fits = close(rx * target_w, ry * target_h)
                    else:
                        fits = close(rx, ry)
    except ValueError as exc:
        return f'{shape_tag} geometry for {target_label} is invalid: {exc}'

    if fits:
        return None
    return (
        f'{shape_tag} geometry must cover the complete frame of {target_label} '
        'for native preset mapping; use path or polygon for partial, offset, '
        'or non-uniform clips'
    )


def _nested_crop_clip_preset_geometry_error(
    wrapper: ET.Element,
    shape: ET.Element,
    clip_units: str,
) -> str | None:
    """Validate an inner-image clip against the crop wrapper's viewBox."""
    # Resolve shared symbols at call time; retain the public module contract.
    from .elements import (
        _effective_clip_geometry_length,
        parse_project_nested_svg_crop,
    )
    if clip_units != 'userSpaceOnUse':
        return (
            'inner <image> clip on a nested crop must use '
            'clipPathUnits="userSpaceOnUse" so browser and PowerPoint '
            'evaluate the visible viewBox region identically'
        )
    try:
        crop = parse_project_nested_svg_crop(wrapper)
    except ValueError as exc:
        return f'cannot validate nested crop geometry: {exc}'

    shape_tag = shape.tag.rsplit('}', 1)[-1].lower()
    if shape_tag not in {'circle', 'ellipse', 'rect'}:
        return None
    expected_x = crop.view_box_x
    expected_y = crop.view_box_y
    expected_w = crop.view_box_width
    expected_h = crop.view_box_height

    def close(actual: float, expected: float) -> bool:
        return math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-6)

    try:
        if shape_tag == 'circle':
            cx = _effective_clip_geometry_length(shape, 'cx', default=0.0)
            cy = _effective_clip_geometry_length(shape, 'cy', default=0.0)
            radius = _effective_clip_geometry_length(shape, 'r', default=0.0)
            fits = (
                close(expected_w, expected_h)
                and close(cx, expected_x + expected_w / 2.0)
                and close(cy, expected_y + expected_h / 2.0)
                and close(radius, expected_w / 2.0)
            )
        elif shape_tag == 'ellipse':
            cx = _effective_clip_geometry_length(shape, 'cx', default=0.0)
            cy = _effective_clip_geometry_length(shape, 'cy', default=0.0)
            rx = _effective_clip_geometry_length(shape, 'rx', default=0.0)
            ry = _effective_clip_geometry_length(shape, 'ry', default=0.0)
            fits = (
                close(cx, expected_x + expected_w / 2.0)
                and close(cy, expected_y + expected_h / 2.0)
                and close(rx, expected_w / 2.0)
                and close(ry, expected_h / 2.0)
            )
        else:
            rect_x = _effective_clip_geometry_length(shape, 'x', default=0.0)
            rect_y = _effective_clip_geometry_length(shape, 'y', default=0.0)
            rect_w = _effective_clip_geometry_length(shape, 'width', default=0.0)
            rect_h = _effective_clip_geometry_length(shape, 'height', default=0.0)
            fits = (
                close(rect_x, expected_x)
                and close(rect_y, expected_y)
                and close(rect_w, expected_w)
                and close(rect_h, expected_h)
            )
            if fits:
                rx_raw = (
                    parse_inline_style(shape.get('style')).get('rx')
                    or shape.get('rx')
                )
                ry_raw = (
                    parse_inline_style(shape.get('style')).get('ry')
                    or shape.get('ry')
                )
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
                if rx > 0 or ry > 0:
                    physical_rx = rx * crop.width / expected_w
                    physical_ry = ry * crop.height / expected_h
                    fits = math.isclose(
                        physical_rx,
                        physical_ry,
                        rel_tol=1e-9,
                        abs_tol=1e-3,
                    )
    except ValueError as exc:
        return f'{shape_tag} geometry for nested crop is invalid: {exc}'

    if fits:
        return None
    return (
        f'{shape_tag} geometry must cover the nested crop viewBox and use '
        'equal physical corner radii after viewport scaling'
    )


def _visible_clip_shapes(
    clip: ET.Element,
    parent_by_id: dict[int, ET.Element],
) -> list[ET.Element]:
    """Resolve clip children with the same inherited visibility as visuals."""
    # Resolve shared symbols at call time; retain the public module contract.
    from .elements import (
        _CLIP_NON_VISUAL_ELEMENTS,
    )
    return [
        child for child in clip
        if child.tag not in _CLIP_NON_VISUAL_ELEMENTS
        and svg_hidden_reason(child, parent_by_id) is None
    ]


def empty_clip_path_reason(
    element: ET.Element,
    definitions: dict[str, ET.Element],
    parent_by_id: dict[int, ET.Element],
) -> str | None:
    """Identify a valid clip reference whose children are all hidden."""
    # Resolve shared symbols at call time; retain the public module contract.
    from .elements import (
        _CLIP_NON_VISUAL_ELEMENTS,
        _visible_clip_shapes,
    )
    clip_id = resolve_url_id(element.get('clip-path', ''))
    clip = definitions.get(clip_id)
    if clip is not None and clip.tag == f'{{{SVG_NS}}}clipPath':
        if (
            any(child.tag not in _CLIP_NON_VISUAL_ELEMENTS for child in clip)
            and not _visible_clip_shapes(clip, parent_by_id)
        ):
            return f'empty clip: url(#{clip_id})'
    return None


def project_clip_path_errors(root: ET.Element) -> list[str]:
    """Return clip-path errors that would otherwise degrade picture geometry."""
    # Resolve shared symbols at call time; retain the public module contract.
    from .elements import (
        _CLIP_SHAPE_TAGS,
        _clip_preset_geometry_error,
        _element_contract_label,
        _nested_crop_clip_preset_geometry_error,
        _unsupported_clip_rule_properties,
        _visible_clip_shapes,
        empty_clip_path_reason,
    )
    definitions, duplicates = project_definition_index(root)
    parent_by_id = {
        id(child): parent
        for parent in root.iter()
        for child in list(parent)
    }
    errors: set[str] = set()
    for elem in root.iter():
        raw_ref = elem.get('clip-path')
        if raw_ref is None or raw_ref.strip().lower() == 'none':
            continue
        label = _element_contract_label(elem)
        is_svg_image = elem.tag == f'{{{SVG_NS}}}image'
        parent = parent_by_id.get(id(elem))
        is_nested_crop_image = (
            is_svg_image
            and parent is not None
            and parent.tag == f'{{{SVG_NS}}}svg'
            and parent is not root
            and parent.get('data-pptx-crop') == '1'
        )
        is_imported_crop = (
            elem.tag == f'{{{SVG_NS}}}svg'
            and elem.get('data-pptx-crop') == '1'
        )
        if not is_svg_image and not is_imported_crop:
            errors.add(
                f'{label} clip-path is allowed only on <image> or an imported '
                'data-pptx-crop="1" wrapper'
            )
        match = re.fullmatch(r'url\(#([^)]+)\)', raw_ref.strip())
        if match is None:
            errors.add(
                f'{label} clip-path must be an exact local url(#id) '
                f'reference; got {raw_ref!r}'
            )
            continue
        clip_id = match.group(1)
        if clip_id in duplicates:
            errors.add(
                f'{label} clip-path=url(#{clip_id}) is ambiguous because the '
                'definition id is duplicated'
            )
            continue
        clip = definitions.get(clip_id)
        if clip is None or clip.tag != f'{{{SVG_NS}}}clipPath':
            errors.add(
                f'{label} clip-path=url(#{clip_id}) has no matching direct '
                f'<defs><clipPath id="{clip_id}"> definition'
            )
            continue
        clip_label = f'<clipPath id="{clip_id}">'
        clip_units = clip.get('clipPathUnits', 'userSpaceOnUse')
        if clip_units not in {'userSpaceOnUse', 'objectBoundingBox'}:
            errors.add(
                f'{clip_label} has unsupported clipPathUnits={clip_units!r}'
            )
        if clip.get('transform'):
            errors.add(f'{clip_label} cannot use transform')
        clip_rules = _unsupported_clip_rule_properties(clip)
        if clip_rules:
            errors.add(
                f'{clip_label} cannot use {", ".join(clip_rules)}; native '
                'picture geometry has no equivalent winding-rule control'
            )
        visual_children = _visible_clip_shapes(clip, parent_by_id)
        if not visual_children and empty_clip_path_reason(elem, definitions, parent_by_id):
            continue
        if len(visual_children) != 1:
            errors.add(
                f'{clip_label} must contain exactly one direct supported shape'
            )
            continue
        shape = visual_children[0]
        shape_tag = shape.tag.rsplit('}', 1)[-1].lower()
        if (
            shape_tag not in _CLIP_SHAPE_TAGS
            or shape.tag != f'{{{SVG_NS}}}{shape_tag}'
        ):
            errors.add(
                f'{clip_label} child <{shape_tag}> is unsupported; use '
                'circle, ellipse, rect, path, or polygon'
            )
            continue
        if shape.get('transform'):
            errors.add(f'{clip_label} child <{shape_tag}> cannot use transform')
            continue
        shape_rules = _unsupported_clip_rule_properties(shape)
        if shape_rules:
            errors.add(
                f'{clip_label} child <{shape_tag}> cannot use '
                f'{", ".join(shape_rules)}; native picture geometry has no '
                'equivalent winding-rule control'
            )
            continue
        if clip_units in {'userSpaceOnUse', 'objectBoundingBox'}:
            if is_nested_crop_image:
                geometry_error = _nested_crop_clip_preset_geometry_error(
                    parent,
                    shape,
                    clip_units,
                )
            else:
                geometry_error = _clip_preset_geometry_error(
                    elem,
                    shape,
                    clip_units,
                )
            if geometry_error is not None:
                errors.add(f'{clip_label} {geometry_error}')
    return sorted(errors)


def _resolve_clip_geometry(
    elem: ET.Element,
    ctx: ConvertContext,
    raw_x: float, raw_y: float,
    raw_w: float, raw_h: float,
) -> str:
    """Resolve clip-path on an image element to DrawingML geometry XML.

    Supports:
      - circle / ellipse  → prstGeom ellipse
      - rect with rx/ry   → prstGeom roundRect
      - path / polygon     → custGeom

    Args:
        elem: SVG element bearing a clip-path attribute.
        ctx:  Conversion context (carries defs).
        raw_x, raw_y: Image position in SVG space (pre-ctx-transform).
        raw_w, raw_h: Image dimensions in SVG space (pre-ctx-transform).

    Returns:
        DrawingML geometry XML string.
    """
    # Resolve shared symbols at call time; retain the public module contract.
    from .elements import (
        _clip_commands_to_geom,
        _visible_clip_shapes,
    )
    DEFAULT = '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom>'

    clip_ref = elem.get('clip-path', '')
    if not clip_ref or clip_ref == 'none':
        return DEFAULT

    clip_id = resolve_url_id(clip_ref)
    if not clip_id or clip_id not in ctx.defs:
        return DEFAULT

    clip_elem = ctx.defs[clip_id]
    clip_tag = clip_elem.tag.replace(f'{{{SVG_NS}}}', '')
    if clip_tag != 'clipPath':
        return DEFAULT

    shapes = _visible_clip_shapes(clip_elem, ctx.parent_by_id)
    if not shapes:
        return DEFAULT
    if len(shapes) != 1:
        raise ValueError('clipPath must contain exactly one direct supported shape')
    shape = shapes[0]

    shape_tag = shape.tag.replace(f'{{{SVG_NS}}}', '')
    is_obb = clip_elem.get('clipPathUnits') == 'objectBoundingBox'

    # --- Circle / Ellipse → preset ellipse ---
    if shape_tag in ('circle', 'ellipse'):
        return '<a:prstGeom prst="ellipse"><a:avLst/></a:prstGeom>'

    # --- Rect with rx/ry → preset roundRect ---
    if shape_tag == 'rect':
        rx_attr = shape.get('rx')
        ry_attr = shape.get('ry')
        rx = svg_length_x(rx_attr, ctx) if rx_attr is not None else 0.0
        ry = svg_length_y(ry_attr, ctx) if ry_attr is not None else rx
        if rx <= 0 and ry <= 0:
            return DEFAULT  # plain rect clip is a no-op
        r = max(rx, ry)
        if is_obb:
            r = r * min(raw_w, raw_h)
        shorter = min(raw_w, raw_h)
        if shorter <= 0:
            return DEFAULT
        adj = int(min(r / (shorter / 2), 1.0) * 50000)
        return (
            f'<a:prstGeom prst="roundRect"><a:avLst>'
            f'<a:gd name="adj" fmla="val {adj}"/>'
            f'</a:avLst></a:prstGeom>'
        )

    # --- Path → custGeom ---
    if shape_tag == 'path':
        d = shape.get('d', '')
        if not d:
            return DEFAULT
        commands = parse_svg_path(d)
        commands = svg_path_to_absolute(commands)
        commands = normalize_path_commands(commands)
        if not commands:
            return DEFAULT
        return _clip_commands_to_geom(
            commands, raw_x, raw_y, raw_w, raw_h, is_obb,
        )

    # --- Polygon → custGeom ---
    if shape_tag == 'polygon':
        pts = parse_svg_points(shape.get('points', ''), min_points=3)
        commands = [PathCommand('M', [pts[0][0], pts[0][1]])]
        for px_, py_ in pts[1:]:
            commands.append(PathCommand('L', [px_, py_]))
        commands.append(PathCommand('Z', []))
        return _clip_commands_to_geom(
            commands, raw_x, raw_y, raw_w, raw_h, is_obb,
        )

    return DEFAULT


# ---------------------------------------------------------------------------
# image
# ---------------------------------------------------------------------------
