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



# Responsibility: converter_dispatch; adapted from the original module.
from .converter_groups import convert_a
from .converter_groups import convert_g

_NON_VISUAL_TAGS = frozenset(('defs', 'title', 'desc', 'metadata', 'style'))

_CONVERTERS = {
    'rect': convert_rect,
    'circle': convert_circle,
    'ellipse': convert_ellipse,
    'line': convert_line,
    'path': convert_path,
    'polygon': convert_polygon,
    'polyline': convert_polyline,
    'text': convert_text,
    'image': convert_image,
    'g': convert_g,
    'a': convert_a,
    'svg': convert_nested_svg,
}

_SUPPORTED_VISUAL_CHILD_TAGS = frozenset(('tspan',))


def _parse_svg_canvas(root: ET.Element) -> tuple[float, float, float, float]:
    """Return the SVG canvas as (x, y, width, height) in SVG units."""
    viewbox = parse_project_viewbox(root.get('viewBox'))
    return 0.0, 0.0, float(viewbox.width), float(viewbox.height)


def _is_full_canvas_rect(
    elem: ET.Element,
    ctx: ConvertContext,
    canvas: tuple[float, float, float, float],
) -> bool:
    """Return whether a rect is a safe candidate for native slide background."""
    if elem.get('transform') or elem.get('filter') or elem.get('clip-path'):
        return False
    if any(
        elem.get(attr) is not None
        for attr in (
            'data-pptx-object',
            'data-pptx-prst',
            'data-pptx-frame',
            'data-pptx-geometry-status',
        )
    ):
        return False
    if (
        parse_svg_length(elem.get('rx'), 0.0) > 0
        or parse_svg_length(elem.get('ry'), 0.0) > 0
    ):
        return False

    canvas_x, canvas_y, canvas_w, canvas_h = canvas
    if canvas_w <= 0 or canvas_h <= 0:
        return False

    tolerance = 0.5
    if abs(parse_svg_length(elem.get('x'), 0.0) - canvas_x) > tolerance:
        return False
    if abs(parse_svg_length(elem.get('y'), 0.0) - canvas_y) > tolerance:
        return False
    if abs(parse_svg_length(elem.get('width'), 0.0) - canvas_w) > tolerance:
        return False
    if abs(parse_svg_length(elem.get('height'), 0.0) - canvas_h) > tolerance:
        return False

    fill = _get_attr(elem, 'fill', ctx)
    if fill == 'none':
        return False

    stroke = _get_attr(elem, 'stroke', ctx)
    stroke_width = parse_svg_length(_get_attr(elem, 'stroke-width', ctx), 1.0)
    stroke_opacity = get_stroke_opacity(elem, ctx)
    if stroke and stroke != 'none' and stroke_width > 0 and stroke_opacity != 0:
        return False

    return True


def _background_xml_from_rect(
    elem: ET.Element,
    ctx: ConvertContext,
) -> str:
    """Build native ``p:bg`` XML from a full-slide SVG background rect."""
    fill_xml = build_fill_xml(
        elem,
        ctx,
        get_fill_opacity(elem, ctx),
        usage="background",
    )
    if not fill_xml or '<a:noFill' in fill_xml:
        return ''
    return f'<p:bg><p:bgPr>{fill_xml}<a:effectLst/></p:bgPr></p:bg>'


def _extract_background_candidate(
    root: ET.Element,
    ctx: ConvertContext,
) -> tuple[str, int | None]:
    """Promote a first-layer SVG background rect to native PowerPoint bgPr.

    PowerPoint stores page background fills under ``p:cSld/p:bg/p:bgPr``.
    Keeping the full-canvas SVG rect in ``p:spTree`` makes it an ordinary
    selectable shape, so users hit it during bulk element selection. Only the
    first visual layer is considered, matching pptx_to_svg's round-trip output
    and avoiding accidental promotion of content panels.
    """
    canvas = _parse_svg_canvas(root)
    for child in root:
        tag = child.tag.replace(f'{{{SVG_NS}}}', '')
        if tag in _NON_VISUAL_TAGS:
            continue
        hidden_reason = svg_hidden_reason(child, ctx.parent_by_id, preserve_native_carriers=True)
        if hidden_reason and (tag != 'g' or hidden_reason == 'display:none'):
            continue

        if tag == 'rect' and _is_full_canvas_rect(child, ctx, canvas):
            bg_xml = _background_xml_from_rect(child, ctx)
            if bg_xml:
                return bg_xml, id(child)
            return '', None

        if tag != 'g':
            return '', None
        if child.get('transform') or child.get('filter') or child.get('clip-path'):
            return '', None
        if child.get('data-pptx-shape-id') is not None:
            # A source-identified object (including the round-trip
            # native-restore placeholder for an unchanged full-canvas picture)
            # must stay a shape so its native original can take its place.
            return '', None
        style_overrides = _extract_inheritable_styles(child)
        local_opacity = get_element_opacity(child)
        child_ctx = ctx.child(
            style_overrides=style_overrides,
            opacity_multiplier=1.0 if local_opacity is None else local_opacity,
        )
        visual_children = [
            grandchild for grandchild in child
            if grandchild.tag.replace(f'{{{SVG_NS}}}', '') not in _NON_VISUAL_TAGS
        ]
        if len(visual_children) != 1:
            return '', None
        only_child = visual_children[0]
        if svg_hidden_reason(only_child, ctx.parent_by_id, preserve_native_carriers=True):
            return '', None
        only_tag = only_child.tag.replace(f'{{{SVG_NS}}}', '')
        if only_tag == 'rect' and _is_full_canvas_rect(only_child, child_ctx, canvas):
            bg_xml = _background_xml_from_rect(only_child, child_ctx)
            if bg_xml:
                ctx.sync_from_child(child_ctx)
                return bg_xml, id(child)
            return '', None
        return '', None

    return '', None


def collect_defs(root: ET.Element) -> dict[str, ET.Element]:
    """Collect all <defs> children into an {id: element} dictionary."""
    definitions, _duplicates = project_definition_index(root)
    return definitions


def _build_source_shape_id_map(root: ET.Element) -> dict[tuple[str, str], int]:
    """Allocate page-unique ids for part-scoped imported shape identities."""
    from .converter_validation import SvgNativeConversionError
    source_entries: list[tuple[tuple[str, str], int]] = []
    seen_keys: set[tuple[str, str]] = set()
    for elem in root.iter():
        raw_id = elem.get('data-pptx-shape-id')
        if raw_id is None:
            continue
        scope = elem.get('data-pptx-shape-scope') or 'slide'
        if re.fullmatch(r'[A-Za-z0-9_.-]{1,64}', scope) is None:
            raise SvgNativeConversionError(
                f'Invalid data-pptx-shape-scope {scope!r}'
            )
        try:
            shape_id = int(raw_id)
        except ValueError as exc:
            raise SvgNativeConversionError(
                f'Invalid data-pptx-shape-id {raw_id!r}'
            ) from exc
        if shape_id < 2 or shape_id > 0xFFFFFFFF:
            raise SvgNativeConversionError(
                f'data-pptx-shape-id must be between 2 and 4294967295, got {raw_id!r}'
            )
        key = (scope, raw_id)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        source_entries.append((key, shape_id))

    preferred_ids = {shape_id for _key, shape_id in source_entries}
    next_fresh = max(preferred_ids, default=1) + 1
    used: set[int] = set()
    mapping: dict[tuple[str, str], int] = {}
    for key, preferred in source_entries:
        output_id = preferred
        if output_id in used:
            while next_fresh in preferred_ids or next_fresh in used:
                next_fresh += 1
            if next_fresh > 0xFFFFFFFF:
                raise SvgNativeConversionError('Exhausted PowerPoint shape id range')
            output_id = next_fresh
            next_fresh += 1
        used.add(output_id)
        mapping[key] = output_id
    return mapping


def _geometry_trace_metadata(elem: ET.Element, result: ShapeResult) -> dict[str, Any]:
    """Describe the native geometry decision for conversion diagnostics."""
    xml = result.xml.lstrip()
    if xml.startswith('<p:grpSp>'):
        if native_replacement_kind(elem) == 'chart' and '<p:graphicFrame>' in xml:
            return {'output_geometry': 'native-object', 'fidelity': 'native-normalized'}
        return {'output_geometry': 'group', 'fidelity': 'visual-only'}
    if xml.startswith('<p:pic>'):
        return {'output_geometry': 'picture', 'fidelity': 'native-normalized'}
    if xml.startswith('<p:graphicFrame>'):
        return {'output_geometry': 'native-object', 'fidelity': 'native-normalized'}
    if xml.startswith('<mc:AlternateContent'):
        if elem.tag.replace(f'{{{SVG_NS}}}', '') == 'text':
            inline_formula_count = sum(
                1 for child in elem.iter()
                if child.get(INLINE_FORMULA_ATTR) is not None
            )
            return {
                'output_geometry': 'text',
                'inline_formula_count': inline_formula_count,
                'fidelity': 'native-normalized',
            }
        return {'output_geometry': 'native-formula', 'fidelity': 'exact'}

    preset_match = re.search(r'<a:prstGeom prst="([^"]+)"', xml)
    if preset_match is not None:
        preset = preset_match.group(1)
        source_preset = elem.get('data-pptx-prst')
        is_connector = xml.startswith('<p:cxnSp>')
        fidelity = (
            'exact'
            if source_preset == preset
            and elem.get('data-pptx-frame') is not None
            and not is_connector
            else 'native-normalized'
        )
        return {
            'output_geometry': 'preset',
            'preset': preset,
            'fidelity': fidelity,
        }
    if re.search(r'<a:custGeom(?:\s|>)', xml):
        carrier = next(
            (
                candidate
                for candidate in elem.iter()
                if candidate.get('data-pptx-part') == 'geometry'
            ),
            elem,
        )
        source_custom = (
            (
                carrier.get('data-pptx-geometry-kind')
                or elem.get('data-pptx-geometry-kind')
            ) == 'custom'
            and (
                carrier.get('data-pptx-frame')
                or elem.get('data-pptx-frame')
            ) is not None
        )
        expected_hash = carrier.get('data-pptx-geometry-sha256')
        actual_hash = hashlib.sha256(
            (carrier.get('d') or '').strip().encode('utf-8')
        ).hexdigest()
        unchanged = source_custom and expected_hash == actual_hash
        if unchanged:
            fidelity = 'exact'
            geometry_source = 'preserved-metadata'
        elif source_custom:
            fidelity = 'native-normalized'
            geometry_source = 'svg-recompiled'
        else:
            fidelity = 'visual-only'
            geometry_source = 'svg-authored'
        return {
            'output_geometry': 'custom',
            'fidelity': fidelity,
            'geometry_source': geometry_source,
        }
    return {'output_geometry': 'unknown', 'fidelity': 'visual-only'}


def collect_hidden_visuals(root: ET.Element) -> list[tuple[ET.Element, str]]:
    """List suppressed visual objects while preserving native transport carriers."""
    parents = {id(child): parent for parent in root.iter() for child in parent}
    definitions, _duplicates = project_definition_index(root)
    hidden: list[tuple[ET.Element, str]] = []

    def visit(element: ET.Element) -> bool:
        tag = _local_tag(element)
        if tag in _NON_VISUAL_TAGS:
            return False
        reason = svg_hidden_reason(element, parents, preserve_native_carriers=True)
        clip_reason = empty_clip_path_reason(element, definitions, parents)
        reason = reason or clip_reason
        hidden_before_children = len(hidden)
        children_visible = [visit(child) for child in element]
        container = tag in {'g', 'a', 'svg'}
        if container and reason != 'display:none' and clip_reason is None:
            if any(children_visible):
                return True
            if reason is None and tag != 'svg' and len(hidden) > hidden_before_children:
                reason = 'hidden descendants'
        if reason:
            hidden.append((element, reason))
        return reason is None and (not container or any(children_visible))

    visit(root)
    return hidden


def convert_element(elem: ET.Element, ctx: ConvertContext) -> ShapeResult | None:
    """Dispatch an SVG element to the appropriate converter."""
    from .converter_validation import SvgNativeConversionError
    tag = elem.tag.replace(f'{{{SVG_NS}}}', '')
    elem_id = elem.get('id')

    def trace(decision: str, **metadata: Any) -> None:
        if ctx.trace_events is None:
            return
        event: dict[str, Any] = {
            'tag': tag,
            'decision': decision,
        }
        if elem_id:
            event['id'] = elem_id
        for attr in (
            'data-pptx-layer',
            'data-pptx-object',
            'data-pptx-shape-id',
            'data-pptx-frame',
            'data-pptx-prst',
            'data-pptx-part',
            'data-pptx-geometry-status',
            'data-pptx-geometry-reason',
            'data-pptx-placeholder',
            'data-pptx-bounds',
            'data-pptx-carrier',
            'data-pptx-idx',
            'data-pptx-role',
        ):
            value = elem.get(attr)
            if value is not None:
                event[attr] = value
        adjustments = {
            attr[len('data-pptx-av-'):]: value
            for attr, value in elem.attrib.items()
            if attr.startswith('data-pptx-av-')
        }
        if adjustments:
            event['adjustments'] = dict(sorted(adjustments.items()))
        event.update(metadata)
        if (
            ctx.depth == 0
            and elem_id
            and elem_id in ctx.animation_group_overrides
        ):
            # Chrome promotion into layouts/masters must leave an explicitly
            # animated group on its slide.
            event['animation_override'] = True
        ctx.trace_events.append(event)

    if elem.get('data-pptx-part') == 'geometry-detail':
        trace('skip', reason='render-only-preset-geometry-detail')
        return None

    hidden_reason = svg_hidden_reason(elem, ctx.parent_by_id, preserve_native_carriers=True)
    clip_reason = empty_clip_path_reason(elem, ctx.defs, ctx.parent_by_id)
    if clip_reason:
        trace('skip', reason=clip_reason)
        return None
    if hidden_reason and (tag not in {'g', 'a'} or hidden_reason == 'display:none'):
        trace('skip', reason=hidden_reason)
        return None

    converter = _CONVERTERS.get(tag)
    if converter:
        try:
            result = converter(elem, ctx)
            shape_hyperlink = elem.get(SHAPE_HYPERLINK_ATTR)
            if result is not None and shape_hyperlink is not None:
                result = apply_shape_hyperlink(result, ctx, shape_hyperlink)
        except Exception as e:
            trace('error', error=str(e))
            label = f'<{tag} id="{elem_id}">' if elem_id else f'<{tag}>'
            raise SvgNativeConversionError(f'Failed to convert {label}: {e}') from e
        if result:
            shape_match = re.search(r'<p:cNvPr id="(\d+)"', result.xml)
            metadata: dict[str, Any] = {}
            if shape_match:
                metadata['shape_id'] = int(shape_match.group(1))
            if result.bounds_emu is not None:
                metadata['bounds_emu'] = list(result.bounds_emu)
            metadata.update(_geometry_trace_metadata(elem, result))
            trace('native', **metadata)
        else:
            trace('skip', reason='empty-or-non-rendering')
        return result

    if tag in _NON_VISUAL_TAGS:
        trace('skip', reason='non-visual')
        return None

    trace('unsupported')
    raise SvgNativeConversionError(f'Unsupported visual SVG element <{tag}>')


def _local_tag(elem: ET.Element) -> str:
    if not isinstance(elem.tag, str):
        return str(elem.tag)
    prefix = f'{{{SVG_NS}}}'
    return elem.tag[len(prefix):] if elem.tag.startswith(prefix) else elem.tag


def collect_unsupported_visuals(
    root: ET.Element,
    *,
    allow_data_icon_use: bool = False,
) -> list[str]:
    """Return visual element paths that the native converter cannot dispatch."""
    issues: list[str] = []

    def walk(
        elem: ET.Element,
        path: str,
        in_defs: bool = False,
        parent_tag: str | None = None,
    ) -> None:
        tag = _local_tag(elem)
        current = f'{path}/{tag}'
        if in_defs:
            return
        if tag in _NON_VISUAL_TAGS:
            return
        is_supported_visual_child = (
            tag in _SUPPORTED_VISUAL_CHILD_TAGS
            and parent_tag in {'text', 'tspan', 'a'}
        )
        is_data_icon_placeholder = (
            allow_data_icon_use
            and tag == 'use'
            and elem.get('data-icon') is not None
        )
        if (tag not in _CONVERTERS
                and tag not in _NON_VISUAL_TAGS
                and not is_supported_visual_child
                and not is_data_icon_placeholder):
            issues.append(current)
        for idx, child in enumerate(list(elem), start=1):
            walk(
                child,
                f'{current}[{idx}]',
                in_defs=(tag == 'defs'),
                parent_tag=tag,
            )

    for idx, child in enumerate(list(root), start=1):
        walk(child, f'/svg[{idx}]', parent_tag='svg')
    return issues
