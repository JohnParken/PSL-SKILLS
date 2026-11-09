# Adapted from PPT Master 5e8856b (MIT); see assets/licenses/ppt_master.txt.
"""Core SVG -> DrawingML dispatcher, group handling, and main entry point."""

from __future__ import annotations

import base64
import binascii
import copy
import hashlib
import math
import re
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from native_payloads import NativePayloadError, hydrate_native_payload_refs
from pptx_ooxml.package import PackagePartRegistry
from hyperlink_contract import (
    SHAPE_HYPERLINK_ATTR,
    project_hyperlink_errors,
    svg_hyperlink_href,
)
from pptx_shapes import (
    DRAWINGML_NS,
    UnknownPresetShapeError,
    has_relationship_attributes,
    resolve_preset_preview_hash,
    svg_preset_preview_fingerprint,
    svg_text_fingerprint,
    validate_ooxml_xfrm,
)
from pptx_effects import project_effect_status_errors, txbody_has_run_effects
from pptx_to_svg.preset_authoring import (
    materialize_compact_authored_preset_tree,
    validate_authored_preset_tree,
)
from resource_paths import icon_dir_for_project
from svg_authoring_view import (
    SEMANTIC_OBJECT_ATTRIBUTE,
    SEMANTIC_SHAPE_KIND,
    semantic_shape_text_component,
)
from svg_compatibility import normalize_single_child_group_filters

from .context import (
    TEXT_FLOW_PRESERVE,
    TEXT_FLOW_SPLIT,
    ConvertContext,
    ShapeResult,
    resolve_text_flow,
)
from .hyperlinks import apply_shape_hyperlink
from .paths import (
    project_freeform_geometry_errors,
    project_gradient_geometry_errors,
)
from .theme_colors import ThemeColorSpec
from .theme_fonts import ThemeFontSpec
from .text_properties import (
    materialize_project_text_metrics,
    project_text_property_errors,
    resolve_project_font_sizes,
    resolve_project_letter_spacings,
)
from .utils import (
    EMU_PER_PX,
    SVG_NS,
    _extract_inheritable_styles,
    _get_attr,
    _is_unit_axis_reflection,
    _xml_escape,
    is_picture_effect_carrier,
    parse_svg_length,
    parse_transform_operations,
    parse_transform_matrix,
    project_definition_errors,
    project_definition_index,
    project_filter_errors,
    project_geometry_length_errors,
    project_gradient_errors,
    project_image_aspect_ratio_errors,
    project_mask_errors,
    project_marker_errors,
    project_opacity_errors,
    project_paint_errors,
    project_paint_reference_errors,
    project_stroke_style_errors,
    project_transform_errors,
    resolve_url_id,
    svg_hidden_reason,
    supports_full_project_transform,
    validate_dml_shape_matrix,
)
from .styles import (
    build_effect_xml, build_fill_xml,
    get_element_opacity, get_fill_opacity, get_stroke_opacity,
)
from .elements import (
    complete_preset_adjustments,
    shape_display_name,
    empty_clip_path_reason,
    convert_rect, convert_circle, convert_ellipse,
    convert_line, convert_path,
    convert_polygon, convert_polyline,
    convert_text, convert_image, convert_nested_svg,
    project_clip_path_errors,
    project_image_errors,
    project_nested_svg_crop_errors,
)
from ..animation_config import (
    anchor_wrapped_group,
    effective_top_level,
    is_chrome_id,
    usable_animation_group_id,
)
from ..canvas_contract import (
    CanvasContractError,
    parse_project_svg_root,
    parse_project_viewbox,
)
from ..native_objects import (
    INLINE_FORMULA_ATTR,
    NativeMarkerAttributeError,
    convert_native_object,
    inline_formula_marker_errors,
    native_metadata_payload_matches,
    native_replacement_kind,
    native_marker_transform,
    require_fresh_native_fallback,
    reserve_source_chart_parts,
    snapshot_native_fallback_freshness,
)
from ..native_objects.formula import formula_fallback_xml
from ..native_objects.inline_formula import wrap_inline_formula_shape
from ..native_objects.marker_status import native_marker_status_errors
from ..semantic_markers import is_static_page_frame



# Responsibility: converter_groups; adapted from the original module.

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
    from .converter_validation import SvgNativeConversionError
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

def convert_g(elem: ET.Element, ctx: ConvertContext) -> ShapeResult | None:
    """Convert SVG <g> to DrawingML group shape <p:grpSp>.

    Preserves group structure so elements can be selected and moved together
    in PowerPoint. Single-child groups are flattened to avoid unnecessary nesting.

    Uses identity coordinate mapping (chOff/chExt == off/ext) so child shapes
    keep their absolute slide coordinates unchanged.
    """
    from .converter_validation import SvgNativeConversionError
    from .converter_dispatch import _NON_VISUAL_TAGS
    from .converter_text_metadata import _append_shape_text
    from .converter_validation import _contains_enabled_native_replacement
    from .converter_text_metadata import _convert_semantic_shape
    from .converter_text_metadata import _extract_rotate_pivot
    from .converter_validation import _native_replacement_enabled
    from .converter_text_metadata import _require_unchanged_preset_preview
    from .converter_text_metadata import _restore_placeholder_sp_pr
    from .converter_text_metadata import _txbody_has_office_math
    from .converter_text_metadata import _with_office_math_fallback
    from .converter_dispatch import convert_element
    from .converter_text_metadata import parse_transform
    from .converter_text_metadata import preserved_native_text_body
    ctx.parent_by_id.update({id(child): elem for child in elem})
    hidden_reason = svg_hidden_reason(elem, ctx.parent_by_id, preserve_native_carriers=True)
    if hidden_reason == 'display:none':
        return None
    exact_graphic_frame = _roundtrip_graphic_frame(elem, ctx) if hidden_reason is None else None
    if exact_graphic_frame is not None:
        return exact_graphic_frame

    transform = elem.get('transform', '')
    native_subtree_active = _contains_enabled_native_replacement(elem, ctx)
    if native_subtree_active:
        dx, dy, sx, sy = native_marker_transform(transform)
        angle_deg = 0.0
    else:
        dx, dy, sx, sy, angle_deg = parse_transform(transform)

    filter_id = resolve_url_id(elem.get('filter', ''))
    style_overrides = _extract_inheritable_styles(elem)
    local_opacity = get_element_opacity(elem)
    if local_opacity is None:
        local_opacity = 1.0

    elem_id = usable_animation_group_id(elem.get('id'))
    semantic_role = elem.get('data-pptx-role')
    placeholder = elem.get('data-pptx-placeholder')
    has_explicit_semantics = (
        semantic_role is not None or placeholder is not None
    )
    is_chrome = (
        is_static_page_frame(semantic_role, placeholder)
        if has_explicit_semantics
        else is_chrome_id(elem_id)
    )
    # Chrome — by name or by static role/placeholder marker — stays out of
    # automatic animation, but an explicit sidecar entry animates it: a
    # background field can pan, a cover band can slide. Only a structural
    # layer (master/layout) is beyond reach, because it is not on the slide.
    should_animate_group = (
        ctx.depth == 0
        and elem_id
        and (not is_chrome or elem_id in ctx.animation_group_overrides)
        and elem.get('data-pptx-layer') is None
    )
    visual_children = [
        child for child in elem
        if child.tag.replace(f'{{{SVG_NS}}}', '') not in _NON_VISUAL_TAGS
    ]
    unit_axis_reflection = (
        bool(transform)
        and _is_unit_axis_reflection(parse_transform_operations(transform))
    )
    matrix_supported = (
        not native_subtree_active
        and bool(transform)
        and visual_children
        and (
            supports_full_project_transform(elem)
            or unit_axis_reflection
        )
    )
    # A pure ``rotate(angle [cx cy])`` falls through to the fallback path
    # below (children are rect/text/path/etc. that don't consume a full
    # matrix). Decomposing the matrix produces translation components
    # (e, f) that encode the pivot — handing those to children would
    # *double-translate* them because grpSp's own ``rot`` already
    # rotates around the group's bounding-box centre. Skip the child
    # translation here and apply pivot-centre compensation to ``a:off``
    # below instead.
    rotate_pivot = _extract_rotate_pivot(transform) if not matrix_supported else None
    if rotate_pivot is not None:
        angle_deg = parse_transform_operations(transform)[0][1][0]
    if matrix_supported:
        child_ctx = ctx.child(
            0, 0, 1.0, 1.0,
            transform_matrix=parse_transform_matrix(transform),
            filter_id=filter_id,
            style_overrides=style_overrides,
            opacity_multiplier=local_opacity,
        )
    elif rotate_pivot is not None:
        child_ctx = ctx.child(
            0, 0, 1.0, 1.0,
            filter_id=filter_id,
            style_overrides=style_overrides,
            opacity_multiplier=local_opacity,
        )
    else:
        child_ctx = ctx.child(
            ctx.scale_x * dx if native_subtree_active else dx,
            ctx.scale_y * dy if native_subtree_active else dy,
            sx,
            sy,
            filter_id=filter_id,
            style_overrides=style_overrides,
            opacity_multiplier=local_opacity,
        )

    if native_subtree_active and child_ctx.opacity_multiplier < 1.0:
        raise SvgNativeConversionError(
            "Group opacity cannot be applied to an active native replacement; "
            "remove the group opacity or remove data-pptx-replace-with to keep "
            "the subtree as ordinary SVG"
        )

    if hidden_reason is None and _native_replacement_enabled(elem, child_ctx):
        native_result = convert_native_object(elem, child_ctx)
        if native_result:
            ctx.sync_from_child(child_ctx)
            if should_animate_group:
                shape_match = re.search(r'<p:cNvPr id="(\d+)"', native_result.xml)
                if shape_match:
                    ctx.anim_targets.append((int(shape_match.group(1)), elem_id))
            return native_result

    if hidden_reason is None and elem.get(SEMANTIC_OBJECT_ATTRIBUTE) == SEMANTIC_SHAPE_KIND:
        geometry_ctx = child_ctx
        if transform and not native_subtree_active:
            geometry_ctx = ctx.child(
                0, 0, 1.0, 1.0,
                transform_matrix=parse_transform_matrix(transform),
                filter_id=filter_id,
                style_overrides=style_overrides,
                opacity_multiplier=local_opacity,
            )
        semantic_result = _convert_semantic_shape(elem, geometry_ctx)
        ctx.sync_from_child(geometry_ctx)
        if semantic_result is None:
            raise SvgNativeConversionError(
                'Semantic shape marker did not produce a native shape'
            )
        if should_animate_group and elem_id:
            shape_match = re.search(r'<p:cNvPr id="(\d+)"', semantic_result.xml)
            if shape_match:
                ctx.anim_targets.append((int(shape_match.group(1)), elem_id))
        return semantic_result

    if (
        hidden_reason is None
        and elem.get('data-pptx-object') in {'shape', 'connector'}
        and elem.get('data-pptx-prst') is not None
    ):
        _require_unchanged_preset_preview(elem)

    preserved_text = preserved_native_text_body(elem) if hidden_reason is None else None
    if preserved_text is not None:
        geometry_carrier, native_text = preserved_text
        geometry_ctx = child_ctx
        if transform and not native_subtree_active:
            geometry_ctx = ctx.child(
                0, 0, 1.0, 1.0,
                transform_matrix=parse_transform_matrix(transform),
                filter_id=filter_id,
                style_overrides=style_overrides,
                opacity_multiplier=local_opacity,
            )
        geometry_result = convert_element(geometry_carrier, geometry_ctx)
        ctx.sync_from_child(geometry_ctx)
        if geometry_result is None:
            raise SvgNativeConversionError(
                'Logical text shape has no convertible geometry carrier'
            )
        restored = _append_shape_text(
            geometry_result,
            native_text,
        )
        restored = _restore_placeholder_sp_pr(restored, elem)
        if _txbody_has_office_math(native_text):
            restored = _with_office_math_fallback(
                elem, restored, geometry_result, geometry_ctx,
            )
            ctx.sync_from_child(geometry_ctx)
        if should_animate_group and elem_id:
            shape_match = re.search(r'<p:cNvPr id="(\d+)"', restored.xml)
            if shape_match:
                ctx.anim_targets.append((int(shape_match.group(1)), elem_id))
        return restored

    child_results: list[ShapeResult] = []
    for child in elem:
        result = convert_element(child, child_ctx)
        if result:
            child_results.append(result)

    ctx.sync_from_child(child_ctx)

    if not child_results:
        return None

    # A logical imported preset may contain several render-only SVG detail
    # paths, but after those are skipped it owns exactly one native object.
    # Flatten that carrier even at the top level; otherwise animation grouping
    # would turn one source ``p:sp`` into a ``p:grpSp`` wrapper. Retarget an
    # optional animation to the restored leaf shape ID.
    logical_native_shape_group = (
        elem.get('data-pptx-object') in {'shape', 'connector'}
        and (
            elem.get('data-pptx-prst') is not None
            or elem.get('data-pptx-geometry-kind') == 'custom'
        )
    )
    logical_picture_effect_group = (
        filter_id is not None
        and is_picture_effect_carrier(elem)
    )
    explicit_native_group = elem.get('data-pptx-object') == 'group'
    if (
        len(child_results) == 1
        # A fallback rotation still belongs to this group. Keep its container
        # so the pivot compensation below runs even for a single text child.
        and (matrix_supported or not angle_deg)
        and not explicit_native_group
        and (
            not should_animate_group
            or logical_native_shape_group
            or logical_picture_effect_group
        )
    ):
        restored_child = _restore_placeholder_sp_pr(child_results[0], elem)
        if should_animate_group and elem_id:
            shape_match = re.search(r'<p:cNvPr id="(\d+)"', restored_child.xml)
            if shape_match:
                ctx.anim_targets.append((int(shape_match.group(1)), elem_id))
        return restored_child

    # Multiple children, or a top-level semantic one-child group: wrap in
    # <p:grpSp> so PowerPoint can animate the group as one unit.
    min_x = min_y = float('inf')
    max_x = max_y = float('-inf')

    for child_result in child_results:
        bounds = child_result.bounds_emu
        if bounds is None:
            continue
        min_x = min(min_x, bounds[0])
        min_y = min(min_y, bounds[1])
        max_x = max(max_x, bounds[2])
        max_y = max(max_y, bounds[3])

    if min_x == float('inf'):
        return ShapeResult(xml='\n'.join(result.xml for result in child_results))

    group_x = int(min_x)
    group_y = int(min_y)
    group_w = max(int(max_x - min_x), 1)
    group_h = max(int(max_y - min_y), 1)

    # ``rotate(angle, cx, cy)`` rotates around the SVG pivot, but DrawingML
    # grpSp ``rot`` always rotates around the group's own bbox centre. When
    # those centres differ, the visual position drifts by exactly the
    # translation a rotate-around-pivot equals. Compensate by offsetting the
    # outer <a:off> only; <a:chOff> stays on the unshifted bbox so children
    # (still at their original SVG positions because rotate_pivot suppressed
    # the dx/dy translation above) remain aligned inside the group.
    off_x = group_x
    off_y = group_y
    if rotate_pivot is not None and angle_deg:
        cx_svg, cy_svg = rotate_pivot
        pivot_ex = (cx_svg + ctx.translate_x) * EMU_PER_PX
        pivot_ey = (cy_svg + ctx.translate_y) * EMU_PER_PX
        bbox_cx = group_x + group_w / 2
        bbox_cy = group_y + group_h / 2
        theta = math.radians(angle_deg)
        cos_t = math.cos(theta)
        sin_t = math.sin(theta)
        # Where the bbox centre lands after rotating around the pivot, minus
        # where DrawingML's grpSp rot would leave it (i.e. unchanged).
        delta_x = (bbox_cx - pivot_ex) * cos_t - (bbox_cy - pivot_ey) * sin_t + pivot_ex - bbox_cx
        delta_y = (bbox_cx - pivot_ex) * sin_t + (bbox_cy - pivot_ey) * cos_t + pivot_ey - bbox_cy
        off_x = int(round(group_x + delta_x))
        off_y = int(round(group_y + delta_y))

    shapes_xml = '\n'.join(result.xml for result in child_results)
    group_id = (
        ctx.claim_shape_id(
            elem.get('data-pptx-shape-id'),
            elem.get('data-pptx-shape-scope'),
        )
        if elem.get('data-pptx-object') == 'group'
        else ctx.next_id()
    )

    # Record top-level semantic groups (e.g. <g id="p02-title">) so the
    # PPTX builder can emit per-element object timing. Only the outermost
    # multi-child wrapper qualifies — flattened single-child groups have no
    # <p:grpSp> to anchor a timing target on, and nested groups are
    # ignored to keep the animation budget at ~per-section granularity.
    if should_animate_group:
        ctx.anim_targets.append((group_id, elem_id))

    group_effect = ''
    if filter_id and filter_id in ctx.defs:
        group_effect = build_effect_xml(
            ctx.defs[filter_id],
            child_ctx.opacity_multiplier,
        )

    rot_emu = 0 if matrix_supported else int(angle_deg * 60000)
    rot_attr = f' rot="{rot_emu}"' if rot_emu else ''
    validate_ooxml_xfrm(off_x, off_y, group_w, group_h)
    validate_ooxml_xfrm(group_x, group_y, group_w, group_h)

    return ShapeResult(xml=f'''<p:grpSp>
<p:nvGrpSpPr>
<p:cNvPr id="{group_id}" name="{_xml_escape(shape_display_name(elem, f'Group {group_id}'))}"/>
<p:cNvGrpSpPr/>
<p:nvPr/>
</p:nvGrpSpPr>
<p:grpSpPr>
<a:xfrm{rot_attr}>
<a:off x="{off_x}" y="{off_y}"/>
<a:ext cx="{group_w}" cy="{group_h}"/>
<a:chOff x="{group_x}" y="{group_y}"/>
<a:chExt cx="{group_w}" cy="{group_h}"/>
</a:xfrm>
{group_effect}
</p:grpSpPr>
{shapes_xml}
</p:grpSp>''', bounds_emu=(group_x, group_y, group_x + group_w, group_y + group_h))


def convert_a(elem: ET.Element, ctx: ConvertContext) -> ShapeResult | None:
    """Convert one standard SVG anchor into a clickable DrawingML object."""
    # A bare anchor around one <g> is transparent: the group converts at the
    # anchor's own depth, so a top-level <a href><g id> stays an animation
    # anchor, and the click lands on its leaves.
    wrapped = anchor_wrapped_group(elem)
    result = convert_g(wrapped if wrapped is not None else elem, ctx)
    if result is None:
        return None
    return apply_shape_hyperlink(result, ctx, svg_hyperlink_href(elem))


# ---------------------------------------------------------------------------
# Defs collection & element dispatch
# ---------------------------------------------------------------------------
