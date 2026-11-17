"""Core SVG -> DrawingML dispatcher, group handling, and main entry point."""

from __future__ import annotations

import base64
import binascii
import copy
import hashlib
import math
import re
from xml.etree import ElementTree as ET

from pptx_shapes import (
    DRAWINGML_NS,
    UnknownPresetShapeError,
    has_relationship_attributes,
    resolve_preset_preview_hash,
    svg_preset_preview_fingerprint,
    svg_text_fingerprint,
)
from pptx_effects import (
    txbody_has_run_effects,
)
from svg_authoring_view import (
    SEMANTIC_OBJECT_ATTRIBUTE,
    SEMANTIC_SHAPE_KIND,
    semantic_shape_text_component,
)

from .context import (
    ConvertContext,
    ShapeResult,
)
from .utils import (
    EMU_PER_PX,
    SVG_NS,
    _xml_escape,
    parse_transform_operations,
    parse_transform_matrix,
    validate_dml_shape_matrix,
)
from .elements import (
    complete_preset_adjustments,
    shape_display_name,
    convert_text,
)
from ..canvas_contract import (
    parse_project_viewbox,
)
from ..native_objects import (
    native_replacement_kind,
    require_fresh_native_fallback,
)
from ..native_objects.formula import formula_fallback_xml
from ..native_objects.inline_formula import wrap_inline_formula_shape



# Adapted from converter.py; upstream MIT, Hugo He, 2025-2026.
def parse_transform(transform_str: str) -> tuple[float, float, float, float, float]:
    """Parse an SVG transform list into (dx, dy, sx, sy, angle_deg).

    Composes every translate/scale/rotate/matrix operation rather than picking
    the first occurrence — needed for idioms like
    ``translate(cx cy) scale(-1 -1) translate(-cx -cy)`` which encode a flip
    around a non-origin pivot.

    When the composed matrix has no rotation, the decomposition preserves
    signed scale for flips. With rotation, scale uses the column magnitudes and
    angle uses the first transformed axis. Zero or non-orthogonal axes fail
    before decomposition because this tuple cannot represent them faithfully.
    """
    if not transform_str:
        return 0.0, 0.0, 1.0, 1.0, 0.0

    matrix = parse_transform_matrix(transform_str)
    validate_dml_shape_matrix(matrix)
    a, b, c, d, e, f = matrix

    # No shear / rotation: direct decomposition preserves the original signs of
    # sx / sy. ctx_x / ctx_y use the simple ``val * sx + tx`` formula, so this
    # is the only form that survives flip-around-pivot composites without
    # collapsing them into a rotation that the consumer can't honour.
    if abs(b) < 1e-9 and abs(c) < 1e-9:
        sx = a if a != 0 else 1.0
        sy = d if d != 0 else 1.0
        return e, f, sx, sy, 0.0

    sx = math.hypot(a, b)
    sy = math.hypot(c, d)
    if sx == 0:
        sx = 1.0
    if sy == 0:
        sy = 1.0

    angle_deg = math.degrees(math.atan2(b, a))
    return e, f, sx, sy, angle_deg


# ``rotate(angle)`` defaults to pivot (0,0); ``rotate(angle, cx, cy)`` rotates
# around (cx, cy). DrawingML grpSp ``rot`` always rotates around the group's
# own bounding-box centre — we need the SVG pivot so ``convert_g`` can
# compensate for the offset between those two centres.
def _root_viewport_size(root: ET.Element) -> tuple[float, float]:
    """Return the SVG root viewport size in user units."""
    viewbox = parse_project_viewbox(root.get('viewBox'))
    return float(viewbox.width), float(viewbox.height)


def _extract_rotate_pivot(transform_str: str) -> tuple[float, float] | None:
    """Return the (cx, cy) pivot of a sole ``rotate(...)`` in *transform_str*.

    Returns ``None`` when the transform list contains anything other than one
    rotate (other ops compose with rotate in a way the pivot-compensation
    fallback can't express). A bare ``rotate(angle)`` returns (0, 0).
    """
    if not transform_str:
        return None
    operations = parse_transform_operations(transform_str)
    if len(operations) != 1 or operations[0][0] != 'rotate':
        return None
    args = operations[0][1]
    cx = args[1] if len(args) == 3 else 0.0
    cy = args[2] if len(args) == 3 else 0.0
    return cx, cy


def _txbody_metadata(elem: ET.Element) -> ET.Element | None:
    for child in elem:
        if (
            child.tag.replace(f'{{{SVG_NS}}}', '') == 'metadata'
            and child.get('data-pptx-part') == 'txbody'
        ):
            return child
    return None


def _placeholder_sp_pr_metadata(elem: ET.Element) -> ET.Element | None:
    for child in elem:
        if (
            child.tag.replace(f'{{{SVG_NS}}}', '') == 'metadata'
            and child.get('data-pptx-part') == 'placeholder-sppr'
        ):
            return child
    return None


def _mark_unchanged_txbody_groups(root: ET.Element) -> None:
    """Snapshot author-visible text state before exporter preprocessing."""
    # Resolve shared symbols at call time; retain the public module contract.
    from .converter import (
        _TXBODY_UNCHANGED_ATTR,
        _txbody_metadata,
    )
    for group in root.iter():
        if group.tag.replace(f'{{{SVG_NS}}}', '') != 'g':
            continue
        metadata = _txbody_metadata(group)
        if metadata is None:
            continue
        expected = metadata.get('data-pptx-text-sha256')
        actual = svg_text_fingerprint(group)
        group.set(_TXBODY_UNCHANGED_ATTR, '1' if expected == actual else '0')


def _mark_unchanged_preset_previews(root: ET.Element) -> None:
    """Snapshot visible preset layers before exporter preprocessing."""
    # Resolve shared symbols at call time; retain the public module contract.
    from .converter import (
        SvgNativeConversionError,
        _PREVIEW_UNCHANGED_ATTR,
    )
    for group in root.iter():
        if group.tag.replace(f'{{{SVG_NS}}}', '') != 'g':
            continue
        if (
            group.get('data-pptx-object') not in {'shape', 'connector'}
            or group.get('data-pptx-prst') is None
        ):
            continue
        try:
            expected = resolve_preset_preview_hash(group)
        except ValueError as exc:
            raise SvgNativeConversionError(
                f'Invalid preset preview fingerprint contract: {exc}'
            ) from exc
        if expected is None:
            continue
        actual = svg_preset_preview_fingerprint(group)
        group.set(_PREVIEW_UNCHANGED_ATTR, '1' if expected == actual else '0')


def _require_unchanged_preset_preview(group: ET.Element) -> None:
    # Resolve shared symbols at call time; retain the public module contract.
    from .converter import (
        SvgNativeConversionError,
        _PREVIEW_UNCHANGED_ATTR,
    )
    try:
        expected = resolve_preset_preview_hash(group)
    except ValueError as exc:
        raise SvgNativeConversionError(
            f'Invalid preset preview fingerprint contract: {exc}'
        ) from exc
    if expected is None:
        return
    snapshot = group.get(_PREVIEW_UNCHANGED_ATTR)
    if snapshot == '1':
        return
    if snapshot is None and svg_preset_preview_fingerprint(group) == expected:
        return
    raise SvgNativeConversionError(
        'Visible preset preview was edited without updating its native '
        'data-pptx-prst/frame/adjustment carrier; export stopped to avoid '
        'silently discarding the SVG edit'
    )


def _decode_unchanged_txbody(
    group: ET.Element,
    metadata: ET.Element,
    *,
    trust_runtime_snapshot: bool = True,
) -> tuple[str, bool] | None:
    # Resolve shared symbols at call time; retain the public module contract.
    from .converter import (
        SvgNativeConversionError,
        _TXBODY_UNCHANGED_ATTR,
    )
    expected_hash = metadata.get('data-pptx-text-sha256')
    if not expected_hash:
        raise SvgNativeConversionError('txbody metadata requires a text hash')
    snapshot = (
        group.get(_TXBODY_UNCHANGED_ATTR)
        if trust_runtime_snapshot else None
    )
    unchanged = snapshot == '1' or (
        snapshot != '0'
        and svg_text_fingerprint(group) == expected_hash
    )
    if metadata.get('data-pptx-encoding') != 'base64':
        raise SvgNativeConversionError('txbody metadata requires base64 encoding')
    try:
        raw = base64.b64decode((metadata.text or '').strip(), validate=True)
        txbody = ET.fromstring(raw)
        decoded = raw.decode('utf-8')
    except (ValueError, binascii.Error, UnicodeDecodeError, ET.ParseError) as exc:
        raise SvgNativeConversionError(f'Invalid txbody metadata: {exc}') from exc
    if txbody.tag != (
        '{http://schemas.openxmlformats.org/presentationml/2006/main}txBody'
    ):
        raise SvgNativeConversionError('txbody metadata payload must be p:txBody')
    if has_relationship_attributes(txbody):
        raise SvgNativeConversionError(
            'txbody metadata must not contain part-local relationship attributes'
        )
    if not unchanged:
        if txbody_has_run_effects(txbody):
            raise SvgNativeConversionError(
                'Visible text or typography was edited while the source '
                'txBody contains run-level effects; export stopped to avoid '
                'silently discarding those effects'
            )
        return None
    return decoded, txbody_has_run_effects(txbody)


def _append_shape_text(
    shape: ShapeResult,
    txbody_xml: str,
) -> ShapeResult:
    # Resolve shared symbols at call time; retain the public module contract.
    from .converter import (
        SvgNativeConversionError,
    )
    if not shape.xml.lstrip().startswith('<p:sp>') or not shape.xml.rstrip().endswith('</p:sp>'):
        raise SvgNativeConversionError('Native txBody can only attach to p:sp')
    closing = shape.xml.rfind('</p:sp>')
    return ShapeResult(
        xml=(
            shape.xml[:closing]
            + txbody_xml
            + '\n'
            + shape.xml[closing:]
        ),
        bounds_emu=shape.bounds_emu,
    )


def _txbody_has_office_math(txbody_xml: str) -> bool:
    # Resolve shared symbols at call time; retain the public module contract.
    from .converter import (
        _A14_MATH_TAG,
        _TXBODY_PREFIXES,
    )
    try:
        body = re.sub(r'^\s*<\?xml[^>]*\?>', '', txbody_xml)
        root = ET.fromstring(f'<root {_TXBODY_PREFIXES}>{body}</root>')
    except ET.ParseError:
        return False
    return any(node.tag == _A14_MATH_TAG for node in root.iter())


def _with_office_math_fallback(
    elem: ET.Element,
    restored: ShapeResult,
    geometry: ShapeResult,
    ctx: ConvertContext,
) -> ShapeResult:
    """Give a shape's Office Math text its visible SVG text as MCE Fallback."""
    # Resolve shared symbols at call time; retain the public module contract.
    from .converter import (
        convert_element,
    )
    # The copy is a plain picture of the shape: fresh id, no placeholder role.
    carrier_xml = re.sub(
        r'<p:cNvPr id="\d+"',
        f'<p:cNvPr id="{ctx.next_id()}"',
        geometry.xml,
        count=1,
    )
    carrier_xml = re.sub(r'<p:ph\b[^>]*/>', '', carrier_xml)
    results = [ShapeResult(xml=carrier_xml, bounds_emu=geometry.bounds_emu)]
    for child in elem:
        if (
            child.get('data-pptx-part') is None
            and child.tag.replace(f'{{{SVG_NS}}}', '') in {'text', 'g'}
        ):
            result = convert_element(child, ctx)
            if result:
                results.append(result)
    fallback_xml = formula_fallback_xml(
        results,
        ctx,
        _xml_escape(shape_display_name(elem, 'Formula')),
    )
    return ShapeResult(
        xml=wrap_inline_formula_shape(restored.xml, fallback_xml),
        bounds_emu=restored.bounds_emu,
    )


def _make_semantic_geometry_carrier_visible(element: ET.Element) -> None:
    """Remove authoring-only hiding from a semantic geometry carrier clone."""
    for name in ('visibility', 'display', 'pointer-events'):
        element.attrib.pop(name, None)
    style = element.get('style')
    if style is None:
        return
    declarations = [
        declaration.strip()
        for declaration in style.split(';')
        if declaration.strip()
    ]
    retained = [
        declaration
        for declaration in declarations
        if declaration.split(':', 1)[0].strip().lower()
        not in {'display', 'pointer-events', 'visibility'}
    ]
    if retained:
        element.set('style', '; '.join(retained))
    else:
        element.attrib.pop('style', None)


def _semantic_shape_text_body(
    shape: ET.Element,
    ctx: ConvertContext,
) -> str | None:
    # Resolve shared symbols at call time; retain the public module contract.
    from .converter import (
        SvgNativeConversionError,
        _decode_unchanged_txbody,
        _txbody_metadata,
    )
    metadata = _txbody_metadata(shape)
    if metadata is not None:
        preserved = _decode_unchanged_txbody(shape, metadata)
        if preserved is not None:
            return preserved[0]
    try:
        component = semantic_shape_text_component(shape)
    except ValueError as exc:
        raise SvgNativeConversionError(str(exc)) from exc
    if component is None:
        return None
    frame = shape.get('data-pptx-frame')

    text = copy.deepcopy(component)
    text.set('data-pptx-frame', frame)
    for name in (
        'data-pptx-shape-id',
        'data-pptx-shape-name',
        'data-pptx-shape-scope',
    ):
        text.attrib.pop(name, None)
    result = convert_text(text, ctx)
    if result is None:
        raise SvgNativeConversionError(
            'Semantic shape text component produced no native text body'
        )
    match = re.search(r'(<p:txBody>.*?</p:txBody>)', result.xml, re.DOTALL)
    if match is None:
        raise SvgNativeConversionError(
            'Semantic shape text did not compile to a p:txBody'
        )
    return match.group(1)


def _convert_semantic_shape(
    shape: ET.Element,
    ctx: ConvertContext,
) -> ShapeResult | None:
    """Compile one normalized semantic SVG shape into one native PPT shape."""
    # Resolve shared symbols at call time; retain the public module contract.
    from .converter import (
        SvgNativeConversionError,
        _append_shape_text,
        _make_semantic_geometry_carrier_visible,
        _semantic_shape_text_body,
        _txbody_has_office_math,
        _with_office_math_fallback,
        convert_element,
    )
    if shape.get(SEMANTIC_OBJECT_ATTRIBUTE) != SEMANTIC_SHAPE_KIND:
        return None
    carriers = [
        child
        for child in shape
        if child.get('data-pptx-part') == 'geometry'
        and child.tag.replace(f'{{{SVG_NS}}}', '')
        in {'circle', 'ellipse', 'line', 'path', 'polygon', 'polyline', 'rect'}
    ]
    if len(carriers) != 1:
        raise SvgNativeConversionError(
            'Semantic shape requires exactly one direct geometry carrier'
        )
    carrier = copy.deepcopy(carriers[0])
    _make_semantic_geometry_carrier_visible(carrier)
    for name, value in shape.attrib.items():
        if (
            name in {
                'data-pptx-frame',
                'data-pptx-geometry-kind',
                'data-pptx-object',
                'data-pptx-prst',
            }
            or name.startswith('data-pptx-av-')
        ) and carrier.get(name) is None:
            carrier.set(name, value)
    for name in (
        'data-pptx-shape-id',
        'data-pptx-shape-name',
        'data-pptx-shape-scope',
        'data-name',
        'data-ph-type',
        'data-pptx-placeholder-index',
        'data-pptx-placeholder-size',
        'data-pptx-placeholder-orientation',
    ):
        if carrier.get(name) is None and shape.get(name) is not None:
            carrier.set(name, str(shape.get(name)))
    if carrier.get('data-name') is None and shape.get('id'):
        # The carrier path becomes the native object; keep the group's name.
        carrier.set('data-name', str(shape.get('id')))

    # Imported carriers retain their hash-guarded source geometry. Only a
    # semantic custom shape without a source payload compiles from SVG alone.
    if (
        shape.get('data-pptx-geometry-kind') == 'custom'
        and carrier.get('data-pptx-custgeom') is None
    ):
        for name in (
            'data-pptx-custgeom',
            'data-pptx-geometry-kind',
            'data-pptx-geometry-sha256',
        ):
            carrier.attrib.pop(name, None)

    geometry = convert_element(carrier, ctx)
    if geometry is None:
        raise SvgNativeConversionError(
            'Semantic shape geometry carrier produced no native shape'
        )
    text_body = _semantic_shape_text_body(shape, ctx)
    if not text_body:
        return geometry
    restored = _append_shape_text(geometry, text_body)
    if _txbody_has_office_math(text_body):
        return _with_office_math_fallback(shape, restored, geometry, ctx)
    return restored


def _restore_placeholder_sp_pr(
    shape: ShapeResult,
    group: ET.Element,
) -> ShapeResult:
    """Restore an unchanged imported placeholder's local p:spPr container."""
    # Resolve shared symbols at call time; retain the public module contract.
    from .converter import (
        SvgNativeConversionError,
        _complete_placeholder_preset_adjustments,
        _placeholder_sp_pr_metadata,
    )
    metadata = _placeholder_sp_pr_metadata(group)
    if metadata is None:
        return shape
    if metadata.get('data-pptx-encoding') != 'base64':
        raise SvgNativeConversionError(
            'placeholder spPr metadata requires base64 encoding'
        )
    expected_hash = metadata.get('data-pptx-ooxml-sha256')
    if not expected_hash or not re.fullmatch(r'[0-9a-f]{64}', expected_hash):
        raise SvgNativeConversionError(
            'placeholder spPr metadata requires a lowercase SHA-256 digest'
        )
    try:
        raw = base64.b64decode((metadata.text or '').strip(), validate=True)
        sp_pr = ET.fromstring(raw)
        decoded = raw.decode('utf-8')
    except (ValueError, binascii.Error, UnicodeDecodeError, ET.ParseError) as exc:
        raise SvgNativeConversionError(
            f'Invalid placeholder spPr metadata: {exc}'
        ) from exc
    if hashlib.sha256(raw).hexdigest() != expected_hash:
        raise SvgNativeConversionError(
            'placeholder spPr metadata hash does not match its payload'
        )
    if sp_pr.tag != (
        '{http://schemas.openxmlformats.org/presentationml/2006/main}spPr'
    ):
        raise SvgNativeConversionError(
            'placeholder spPr metadata payload must be p:spPr'
        )
    if has_relationship_attributes(sp_pr):
        raise SvgNativeConversionError(
            'placeholder spPr metadata cannot contain relationships'
        )
    pattern = re.compile(r'<p:spPr>.*?</p:spPr>', re.DOTALL)
    if pattern.search(shape.xml) is None:
        raise SvgNativeConversionError(
            'placeholder spPr metadata can only attach to p:sp'
        )
    decoded = _complete_placeholder_preset_adjustments(decoded, sp_pr)
    return ShapeResult(
        xml=pattern.sub(lambda _match: decoded, shape.xml, count=1),
        bounds_emu=shape.bounds_emu,
    )


def _complete_placeholder_preset_adjustments(
    decoded: str,
    sp_pr: ET.Element,
) -> str:
    """Rewrite only a partially authored preset avLst in validated spPr XML."""
    prst_geom = sp_pr.find(f'{{{DRAWINGML_NS}}}prstGeom')
    if prst_geom is None:
        return decoded
    av_lst = prst_geom.find(f'{{{DRAWINGML_NS}}}avLst')
    prst = prst_geom.get('prst')
    if av_lst is None or not prst:
        return decoded

    authored: list[tuple[str, str]] = []
    for guide in av_lst:
        name = guide.get('name')
        formula = guide.get('fmla')
        if (
            guide.tag != f'{{{DRAWINGML_NS}}}gd'
            or name is None
            or formula is None
        ):
            return decoded
        authored.append((name, formula))

    try:
        completed = complete_preset_adjustments(prst, authored)
    except UnknownPresetShapeError:
        return decoded
    authored_names = [name for name, _formula in authored]
    defined_names = {name for name, _formula in completed}
    if (
        not 0 < len(authored) < len(completed)
        or len(set(authored_names)) != len(authored_names)
        or not set(authored_names).issubset(defined_names)
    ):
        return decoded

    av_lst_pattern = re.compile(
        r'(?P<open><(?P<qname>(?:[A-Za-z_][\w.-]*:)?avLst)\s*>)'
        r'.*?'
        r'(?P<close></(?P=qname)\s*>)',
        re.DOTALL,
    )
    matches = list(av_lst_pattern.finditer(decoded))
    if len(matches) != 1:
        return decoded
    match = matches[0]
    prefix = match.group('qname')[:-len('avLst')]
    guide_xml = ''.join(
        f'<{prefix}gd name="{_xml_escape(name)}" '
        f'fmla="{_xml_escape(formula)}"/>'
        for name, formula in completed
    )
    return (
        decoded[:match.end('open')]
        + guide_xml
        + decoded[match.start('close'):]
    )


def preserved_native_text_body(
    group: ET.Element,
    *,
    trust_runtime_snapshot: bool = True,
) -> tuple[ET.Element, str] | None:
    """Return the geometry carrier and unchanged native text body, if usable."""
    # Resolve shared symbols at call time; retain the public module contract.
    from .converter import (
        SvgNativeConversionError,
        _decode_unchanged_txbody,
        _is_import_text_flip_compensation,
        _txbody_metadata,
    )
    metadata = _txbody_metadata(group)
    logical_text_shape = (
        group.get('data-pptx-object') == 'shape'
        and (
            group.get('data-pptx-prst') is not None
            or group.get('data-pptx-geometry-kind') == 'custom'
        )
        and metadata is not None
    )
    if not logical_text_shape:
        return None
    decoded_text = _decode_unchanged_txbody(
        group,
        metadata,
        trust_runtime_snapshot=trust_runtime_snapshot,
    )
    carrier_children = [
        child for child in group
        if child.get('data-pptx-part') == 'geometry'
    ]
    allowed_parts = {
        'geometry',
        'geometry-detail',
        'geometry-preview',
        'txbody',
    }
    has_foreign_visual = any(
        child.tag.replace(f'{{{SVG_NS}}}', '') not in {'text', 'metadata'}
        and child.get('data-pptx-part') not in allowed_parts
        and not _is_import_text_flip_compensation(child)
        for child in group
    )
    if decoded_text is None:
        return None
    native_text, has_run_effects = decoded_text
    if len(carrier_children) != 1 or has_foreign_visual:
        if has_run_effects:
            raise SvgNativeConversionError(
                'The source txBody contains run-level effects but cannot be '
                'restored as one native text shape; export stopped to avoid '
                'silently discarding those effects'
            )
        return None
    return carrier_children[0], native_text


def _is_import_text_flip_compensation(elem: ET.Element) -> bool:
    """Recognize the importer-only wrapper that keeps flipped text upright."""
    if (
        elem.tag.replace(f'{{{SVG_NS}}}', '') != 'g'
        or elem.get('data-pptx-text-flip-compensation') != 'true'
    ):
        return False
    return all(
        child.tag.replace(f'{{{SVG_NS}}}', '') in {'g', 'text', 'tspan', 'a'}
        for child in elem.iter()
        if child is not elem
    )


def _roundtrip_graphic_frame(
    elem: ET.Element,
    ctx: ConvertContext,
) -> ShapeResult | None:
    """Restore an unchanged relationship-free imported graphicFrame."""
    # Resolve shared symbols at call time; retain the public module contract.
    from .converter import (
        SvgNativeConversionError,
    )
    if elem.get('data-pptx-roundtrip-object') != 'graphic-frame':
        return None
    if native_replacement_kind(elem) in {'chart', 'table'}:
        # Eligible data objects follow the public authority/activation route:
        # default export keeps their SVG fallback, while explicit native export
        # rebuilds them from inline JSON. The opaque round-trip payload must not
        # bypass either decision.
        return None
    if elem.get('data-pptx-object') != 'graphic-frame':
        raise SvgNativeConversionError(
            'round-trip graphic-frame metadata requires '
            'data-pptx-object="graphic-frame"'
        )
    require_fresh_native_fallback(
        elem,
        use_runtime_snapshot=True,
    )
    metadata = next(
        (
            child
            for child in elem
            if child.tag.replace(f'{{{SVG_NS}}}', '') == 'metadata'
            and child.get('data-pptx-part') == 'roundtrip-graphic-frame'
        ),
        None,
    )
    if metadata is None or metadata.get('data-pptx-encoding') != 'base64':
        raise SvgNativeConversionError(
            'round-trip graphic-frame metadata requires a base64 payload'
        )
    expected_hash = metadata.get('data-pptx-ooxml-sha256')
    if not expected_hash or not re.fullmatch(r'[0-9a-f]{64}', expected_hash):
        raise SvgNativeConversionError(
            'round-trip graphic-frame metadata requires a lowercase SHA-256'
        )
    try:
        raw = base64.b64decode((metadata.text or '').strip(), validate=True)
        frame = ET.fromstring(raw)
    except (ValueError, binascii.Error, ET.ParseError) as exc:
        raise SvgNativeConversionError(
            f'Invalid round-trip graphic-frame metadata: {exc}'
        ) from exc
    if hashlib.sha256(raw).hexdigest() != expected_hash:
        raise SvgNativeConversionError(
            'round-trip graphic-frame metadata hash does not match its payload'
        )
    pml_namespace = (
        'http://schemas.openxmlformats.org/presentationml/2006/main'
    )
    if frame.tag != f'{{{pml_namespace}}}graphicFrame':
        raise SvgNativeConversionError(
            'round-trip graphic-frame payload must be p:graphicFrame'
        )
    if has_relationship_attributes(frame):
        raise SvgNativeConversionError(
            'round-trip graphic-frame payload cannot contain relationships'
        )
    shape_id = ctx.claim_shape_id(
        elem.get('data-pptx-shape-id'),
        elem.get('data-pptx-shape-scope'),
    )
    c_nv_pr = frame.find(
        f'{{{pml_namespace}}}nvGraphicFramePr/'
        f'{{{pml_namespace}}}cNvPr'
    )
    if c_nv_pr is None:
        raise SvgNativeConversionError(
            'round-trip graphic-frame payload has no p:cNvPr'
        )
    c_nv_pr.set('id', str(shape_id))
    raw_frame = elem.get('data-pptx-frame')
    try:
        values = tuple(
            float(value)
            for value in re.split(r'[\s,]+', (raw_frame or '').strip())
        )
    except ValueError as exc:
        raise SvgNativeConversionError(
            f'Invalid round-trip graphic-frame bounds: {raw_frame!r}'
        ) from exc
    if (
        len(values) != 4
        or not all(math.isfinite(value) for value in values)
        or values[2] <= 0
        or values[3] <= 0
    ):
        raise SvgNativeConversionError(
            f'Invalid round-trip graphic-frame bounds: {raw_frame!r}'
        )
    bounds = (
        round(values[0] * EMU_PER_PX),
        round(values[1] * EMU_PER_PX),
        round((values[0] + values[2]) * EMU_PER_PX),
        round((values[1] + values[3]) * EMU_PER_PX),
    )
    return ShapeResult(
        xml=ET.tostring(frame, encoding='unicode'),
        bounds_emu=bounds,
    )


# ---------------------------------------------------------------------------
# Group handling
# ---------------------------------------------------------------------------
