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



# Responsibility: converter_slide; adapted from the original module.

def convert_svg_to_slide_shapes(
    svg_path: str | Path,
    *,
    resource_root: Path,
    svg_text: str | None = None,
    slide_num: int = 1,
    slide_count: int | None = None,
    verbose: bool = False,
    merge_paragraphs: bool | None = None,
    image_optimize: bool = False,
    image_max_dimension: int | None = 2560,
    image_sizing: str = 'cap',
    image_scale: float = 2.0,
    image_quality: int = 85,
    native_objects: bool = False,
    animation_group_overrides: frozenset[str] | None = None,
    theme_font_spec: ThemeFontSpec | None = None,
    theme_color_spec: ThemeColorSpec | None = None,
    primary_language: str | None = None,
    trace_out: list[dict[str, Any]] | None = None,
    promote_background: bool = True,
    text_flow: str | None = None,
    dangerous_nonconforming_export: bool = False,
    package_parts: PackagePartRegistry | None = None,
) -> tuple[
    str,
    dict[str, bytes],
    list[dict[str, str]],
    list,
    dict[str, bytes],
    dict[str, str],
]:
    """Convert an SVG file to a complete DrawingML slide XML.

    Args:
        svg_path: Path to the SVG file.
        slide_num: Slide number (for naming).
        slide_count: Public deck size used to validate ``#slide-N`` targets.
        verbose: Print progress info.
        merge_paragraphs: Legacy compatibility option. True selects reflow;
            False selects split. Do not combine with ``text_flow``.
        text_flow: Positional-tspan policy. ``preserve`` keeps authored visual
            line breaks in one frame, ``reflow`` lets PowerPoint wrap the text,
            and ``split`` emits one text frame per visual line.
        image_optimize: Downsample oversized raster images for PPTX export.
        image_max_dimension: Maximum optimized image dimension in pixels.
        image_sizing: ``cap`` to only cap source dimensions, ``display`` to
            size from rendered SVG boxes.
        image_scale: Target image pixels per SVG display pixel.
        image_quality: JPEG quality used for opaque optimized rasters.
        native_objects: Convert opt-in ``data-pptx-replace-with`` chart/table
            markers to native PowerPoint Chart/Table objects. Formula markers
            remain intrinsically native; Chart/Table markers stay off otherwise.
        animation_group_overrides: Explicit top-level SVG group ids from
            ``animations.json`` that override the legacy chrome-name fallback.
            Explicit structural layer/role/placeholder markers remain excluded.
        theme_font_spec: Optional major/minor theme-font contract. Matching SVG
            families emit DrawingML theme tokens instead of fixed typefaces.
        theme_color_spec: Optional context-aware theme-color contract. Exact
            locked colors emit DrawingML scheme tokens while local colors stay
            fixed.
        primary_language: Canonical BCP-47 project content language. ``None``
            keeps the legacy per-run script heuristic.
        trace_out: Optional list populated with one per-slide trace dictionary.
        promote_background: Promote the first eligible full-canvas rectangle
            into native ``p:bg``. Structured export disables this generic pass
            and applies its narrower explicit background contract later.
        dangerous_nonconforming_export: Apply narrowly defined compatibility
            normalizations before the ordinary strict project preflight.
            Parsing, resource, conversion, and package failures remain blocking.
        resource_root: Explicit project boundary for local images and icons.
            Omit only for direct library calls that require legacy inference.
        package_parts: Shared native-part registry for the complete export,
            including the template/source package and previously rebuilt slides.

    Returns:
        (slide_xml, media_files, rel_entries, anim_targets,
        package_files, content_type_overrides) where:
        - slide_xml: Complete slide XML string.
        - media_files: Dict of {filename: bytes} for media to write.
        - rel_entries: List of relationship entries to add.
        - anim_targets: List of (shape_id, svg_id) tuples for top-level
          semantic groups, in z-order; consumed by the builder's optional
          per-element object-animation timing emitter.
        - package_files: Dict of {pptx internal path: bytes} for non-media
          OOXML parts such as native chart XML and embedded workbooks.
        - content_type_overrides: Dict of {pptx internal path: content type}
          for package_files that require [Content_Types].xml overrides.
    """
    from .converter_validation import SvgNativeConversionError
    from .converter_dispatch import _NON_VISUAL_TAGS
    from .converter_dispatch import _build_source_shape_id_map
    from .converter_dispatch import _extract_background_candidate
    from .converter_validation import _hydrate_native_payloads
    from .converter_text_metadata import _mark_unchanged_preset_previews
    from .converter_text_metadata import _mark_unchanged_txbody_groups
    from .converter_validation import _require_inline_formula_markers
    from .converter_validation import _require_native_marker_attributes
    from .converter_validation import _require_project_clip_paths
    from .converter_validation import _require_project_definitions
    from .converter_validation import _require_project_effect_status
    from .converter_validation import _require_project_filters
    from .converter_validation import _require_project_freeform_geometry
    from .converter_validation import _require_project_gradients
    from .converter_validation import _require_project_hyperlinks
    from .converter_validation import _require_project_image_aspect_ratios
    from .converter_validation import _require_project_images
    from .converter_validation import _require_project_line_end_markers
    from .converter_validation import _require_project_masks
    from .converter_validation import _require_project_nested_svg_crops
    from .converter_validation import _require_project_opacities
    from .converter_validation import _require_project_paint_references
    from .converter_validation import _require_project_paints
    from .converter_validation import _require_project_stroke_styles
    from .converter_validation import _require_project_text_properties
    from .converter_validation import _require_project_transforms
    from .converter_text_metadata import _root_viewport_size
    from .converter_dispatch import collect_defs
    from .converter_dispatch import collect_unsupported_visuals
    from .converter_dispatch import convert_element
    text_flow = resolve_text_flow(text_flow, merge_paragraphs)
    svg_path = Path(svg_path)
    if resource_root is not None:
        resource_root = Path(resource_root).resolve()
        try:
            svg_path.resolve().relative_to(resource_root)
        except ValueError as exc:
            raise SvgNativeConversionError(
                f'{svg_path.name}: SVG source is outside resource root '
                f'{resource_root}'
            ) from exc
    tree = ET.ElementTree(ET.fromstring(svg_text)) if svg_text is not None else ET.parse(str(svg_path))
    root = tree.getroot()
    _hydrate_native_payloads(root, svg_path)
    try:
        parse_project_svg_root(
            root,
            context=svg_path.name,
        )
    except CanvasContractError as exc:
        raise SvgNativeConversionError(str(exc)) from exc
    dangerous_normalizations = (
        normalize_single_child_group_filters(root)
        if dangerous_nonconforming_export
        else []
    )
    if dangerous_normalizations and verbose:
        print(
            '  [DANGEROUS][NORMALIZED] '
            f'{len(dangerous_normalizations)} single-child group filter(s)'
        )
    _require_native_marker_attributes(root, svg_path)
    _require_inline_formula_markers(root, svg_path)
    _require_project_hyperlinks(
        root,
        svg_path,
        slide_count=slide_count,
    )
    _require_project_nested_svg_crops(root, svg_path)
    _require_project_clip_paths(root, svg_path)
    authored_errors = validate_authored_preset_tree(root)
    if authored_errors:
        raise SvgNativeConversionError(
            'Invalid authored preset structure: ' + '; '.join(authored_errors)
        )
    # Validate the source contract once, then lower compact groups to the
    # established expanded transport IR.  Downstream conversion validates the
    # generated preview hash, not the source-format allowlist again.
    try:
        materialize_compact_authored_preset_tree(root)
    except ValueError as exc:
        raise SvgNativeConversionError(
            f'Invalid compact authored preset: {exc}'
        ) from exc
    _mark_unchanged_txbody_groups(root)
    _mark_unchanged_preset_previews(root)
    has_roundtrip_object = any(
        elem.get('data-pptx-roundtrip-object') is not None
        for elem in root.iter()
    )
    if native_objects or has_roundtrip_object:
        try:
            snapshot_native_fallback_freshness(root)
        except NativeMarkerAttributeError as exc:
            raise SvgNativeConversionError(
                f'{Path(svg_path).name}: conflicting chart/table replacement '
                f'metadata: {exc}'
            ) from exc
    trace_events: list[dict[str, Any]] | None = [] if trace_out is not None else None
    trace_steps: list[dict[str, Any]] = []

    from ..geometry_properties import (
        GeometryStyleError,
        materialize_inline_geometry_properties,
    )

    try:
        geometry_count = materialize_inline_geometry_properties(root)
    except GeometryStyleError as exc:
        raise SvgNativeConversionError(
            f'{svg_path.name}: inline geometry materialization failed: {exc}'
        ) from exc
    geometry_trace = None
    if geometry_count:
        geometry_trace = {
            'action': 'materialize-inline-geometry',
            'count': geometry_count,
        }
        trace_steps.append(geometry_trace)
        if verbose:
            print(f'  Materialized {geometry_count} inline geometry declaration(s)')

    geometry_length_errors = project_geometry_length_errors(root)
    if geometry_length_errors:
        preview = '; '.join(geometry_length_errors[:8])
        suffix = (
            '' if len(geometry_length_errors) <= 8
            else f'; +{len(geometry_length_errors) - 8} more'
        )
        raise SvgNativeConversionError(
            f'{Path(svg_path).name}: invalid project geometry length(s): '
            f'{preview}{suffix}'
        )

    _require_project_text_properties(root, svg_path)
    _require_project_freeform_geometry(root, svg_path)
    _require_project_stroke_styles(root, svg_path)
    _require_project_opacities(root, svg_path)
    _require_project_paints(root, svg_path)
    _require_project_masks(root, svg_path)
    _require_project_definitions(root, svg_path)
    _require_project_paint_references(root, svg_path)
    _require_project_line_end_markers(root, svg_path)
    _require_project_gradients(root, svg_path)
    _require_project_effect_status(root, svg_path)
    _require_project_filters(root, svg_path)
    _require_project_image_aspect_ratios(root, svg_path)
    _require_project_transforms(root, svg_path)

    viewport_width, viewport_height = _root_viewport_size(root)

    # Expand project icon placeholders and static same-document <use>
    # references before unsupported-element preflight.
    from ..use_expander import (
        UseExpansionError,
        expand_local_use_references,
        expand_use_data_icons,
    )

    icons_dir = icon_dir_for_project(resource_root)
    try:
        expanded = expand_use_data_icons(root, icons_dir)
    except UseExpansionError as exc:
        raise SvgNativeConversionError(
            f'{svg_path.name}: icon expansion failed: {exc}'
        ) from exc
    if expanded:
        trace_steps.append({'action': 'expand-use-data-icons', 'count': expanded})
    if verbose and expanded:
        print(f'  Expanded {expanded} <use data-icon="..."/> placeholder(s)')
    if expanded:
        hydrated = _hydrate_native_payloads(root, svg_path)
        if hydrated:
            trace_steps.append({
                'action': 'hydrate-native-payloads-from-icons',
                'count': hydrated,
            })
        _mark_unchanged_txbody_groups(root)
        _mark_unchanged_preset_previews(root)
        _require_project_freeform_geometry(root, svg_path)

    try:
        injected_geometry_count = materialize_inline_geometry_properties(root)
    except GeometryStyleError as exc:
        raise SvgNativeConversionError(
            f'{svg_path.name}: expanded icon geometry materialization failed: {exc}'
        ) from exc
    if injected_geometry_count:
        geometry_count += injected_geometry_count
        if geometry_trace is None:
            geometry_trace = {
                'action': 'materialize-inline-geometry',
                'count': geometry_count,
            }
            trace_steps.append(geometry_trace)
        else:
            geometry_trace['count'] = geometry_count
        if verbose:
            print(
                f'  Materialized {injected_geometry_count} inline geometry '
                'declaration(s) from expanded icons'
            )

    try:
        expanded_local = expand_local_use_references(root)
    except UseExpansionError as exc:
        raise SvgNativeConversionError(
            f'{svg_path.name}: local <use> expansion failed: {exc}'
        ) from exc
    if expanded_local:
        trace_steps.append({
            'action': 'expand-local-use-references',
            'count': expanded_local,
        })
        if verbose:
            print(f'  Expanded {expanded_local} local <use href="#..."/> instance(s)')

    if dangerous_nonconforming_export:
        expanded_normalizations = normalize_single_child_group_filters(root)
        if expanded_normalizations:
            dangerous_normalizations.extend(expanded_normalizations)
            if verbose:
                print(
                    '  [DANGEROUS][NORMALIZED] '
                    f'{len(expanded_normalizations)} expanded-resource '
                    'group filter(s)'
                )

    _require_inline_formula_markers(root, svg_path)
    _require_project_hyperlinks(
        root,
        svg_path,
        slide_count=slide_count,
    )

    # Recheck compiler-injected icon/use wrappers and cloned definition trees.
    _require_project_nested_svg_crops(root, svg_path)
    _require_project_images(root, svg_path, resource_root)
    _require_project_clip_paths(root, svg_path)
    _require_project_text_properties(root, svg_path)
    _require_project_stroke_styles(root, svg_path)
    _require_project_opacities(root, svg_path)
    _require_project_paints(root, svg_path)
    _require_project_masks(root, svg_path)
    _require_project_definitions(root, svg_path)
    _require_project_paint_references(root, svg_path)
    _require_project_gradients(root, svg_path)
    _require_project_effect_status(root, svg_path)
    _require_project_filters(root, svg_path)
    _require_project_image_aspect_ratios(root, svg_path)
    _require_project_transforms(root, svg_path)

    try:
        materialize_project_text_metrics(root)
    except ValueError as exc:
        raise SvgNativeConversionError(
            f'{svg_path.name}: text-metric materialization failed: {exc}'
        ) from exc

    inline_formula_count_before_text_lowering = sum(
        1 for elem in root.iter()
        if elem.get(INLINE_FORMULA_ATTR) is not None
    )
    hyperlink_count_before_text_lowering = sum(
        1 for elem in root.iter()
        if (
            elem.tag == f'{{{SVG_NS}}}a'
            or elem.get(SHAPE_HYPERLINK_ATTR) is not None
        )
    )

    # Flatten positional <tspan> (those with x/y/non-zero dy) into independent
    # <text> elements. DrawingML runs cannot reposition mid-paragraph, so a
    # dy-stacked block of tspans would otherwise collapse onto one baseline,
    # and an x-anchored tspan would render in the wrong column. finalize_svg
    # does the same flattening on disk; doing it here keeps native pptx output
    # correct when reading raw svg_output/.
    # Preserve/reflow modes additionally fold conservative paragraph blocks
    # into one annotated <text>. Preserve keeps visual lines as DrawingML hard
    # breaks; reflow joins wrapping lines; split keeps one frame per line.
    from ..tspan_flattener import flatten_positional_tspans
    flattened = flatten_positional_tspans(
        tree,
        merge_paragraphs=text_flow != TEXT_FLOW_SPLIT,
        preserve_line_breaks=text_flow == TEXT_FLOW_PRESERVE,
    )
    if flattened:
        trace_steps.append({
            'action': 'flatten-positional-tspans',
            'text_flow': text_flow,
            # Compatibility field for older trace readers.
            'merge_paragraphs': text_flow != TEXT_FLOW_SPLIT,
        })
        if verbose:
            print(f'  Lowered positional <tspan> using {text_flow} text flow')

    inline_formula_count_after_text_lowering = sum(
        1 for elem in root.iter()
        if elem.get(INLINE_FORMULA_ATTR) is not None
    )
    hyperlink_count_after_text_lowering = sum(
        1 for elem in root.iter()
        if (
            elem.tag == f'{{{SVG_NS}}}a'
            or elem.get(SHAPE_HYPERLINK_ATTR) is not None
        )
    )
    if (
        inline_formula_count_after_text_lowering
        != inline_formula_count_before_text_lowering
    ):
        raise SvgNativeConversionError(
            f'{svg_path.name}: positional text lowering changed inline formula '
            f'marker count from {inline_formula_count_before_text_lowering} '
            f'to {inline_formula_count_after_text_lowering}'
        )
    if hyperlink_count_after_text_lowering != hyperlink_count_before_text_lowering:
        raise SvgNativeConversionError(
            f'{svg_path.name}: positional text lowering changed hyperlink '
            f'count from {hyperlink_count_before_text_lowering} '
            f'to {hyperlink_count_after_text_lowering}'
        )
    _require_inline_formula_markers(root, svg_path)
    _require_project_hyperlinks(
        root,
        svg_path,
        slide_count=slide_count,
    )
    _require_project_text_properties(root, svg_path)
    try:
        text_font_sizes = resolve_project_font_sizes(root)
        text_letter_spacings = resolve_project_letter_spacings(
            root,
            text_font_sizes,
        )
    except ValueError as exc:
        raise SvgNativeConversionError(
            f'{svg_path.name}: invalid project text-metric inheritance: {exc}'
        ) from exc

    unsupported = collect_unsupported_visuals(root)
    if unsupported:
        preview = '; '.join(unsupported[:8])
        suffix = '' if len(unsupported) <= 8 else f'; +{len(unsupported) - 8} more'
        raise SvgNativeConversionError(
            f'{svg_path.name}: unsupported visual SVG element(s): {preview}{suffix}'
        )

    defs = collect_defs(root)
    source_shape_id_map = _build_source_shape_id_map(root)
    root_opacity = get_element_opacity(root)
    ctx = ConvertContext(
        defs=defs,
        reserved_shape_ids=frozenset(source_shape_id_map.values()),
        source_shape_id_map=source_shape_id_map,
        slide_num=slide_num,
        slide_count=slide_count,
        viewport_width=viewport_width,
        viewport_height=viewport_height,
        svg_dir=Path(svg_path).parent,
        resource_root=resource_root,
        text_flow=text_flow,
        image_optimize=image_optimize,
        image_max_dimension=image_max_dimension,
        image_sizing=image_sizing,
        image_scale=image_scale,
        image_quality=image_quality,
        native_objects_enabled=native_objects,
        animation_group_overrides=animation_group_overrides or frozenset(),
        trace_events=trace_events,
        theme_font_spec=theme_font_spec,
        theme_color_spec=theme_color_spec,
        primary_language=primary_language,
        package_parts=package_parts if package_parts is not None else PackagePartRegistry(),
        inherited_styles=_extract_inheritable_styles(root),
        opacity_multiplier=1.0 if root_opacity is None else root_opacity,
        parent_by_id={id(child): parent for parent in root.iter() for child in parent},
        text_font_sizes=text_font_sizes,
        text_letter_spacings=text_letter_spacings,
    )
    if native_objects:
        reserve_source_chart_parts(root, ctx)
    shapes: list[str] = []
    converted = 0
    skipped = 0
    has_top_level_group = any(
        effective_top_level(child).tag.replace(f'{{{SVG_NS}}}', '') == 'g'
        for child in root
    )
    background_xml, background_skip_id = (
        _extract_background_candidate(root, ctx)
        if promote_background
        else ('', None)
    )
    promoted_backgrounds = 1 if background_xml else 0
    if background_xml and trace_events is not None:
        trace_events.append({
            'tag': 'rect',
            'decision': 'native-background',
            'reason': 'promoted-full-canvas-rect-to-bgPr',
        })
    # Per-element shape ids of every top-level child, used as an animation
    # fallback when no <g id="..."> groups are present at the root.
    fallback_targets: list = []

    for child in root:
        tag = child.tag.replace(f'{{{SVG_NS}}}', '')
        if tag == 'defs':
            continue
        if id(child) == background_skip_id:
            continue
        try:
            result = convert_element(child, ctx)
        except SvgNativeConversionError as exc:
            raise SvgNativeConversionError(f'{svg_path.name}: {exc}') from exc
        if result:
            shapes.append(result.xml)
            converted += 1
            m = re.search(r'<p:cNvPr id="(\d+)"', result.xml)
            unit = effective_top_level(child)
            elem_id = unit.get('id')
            role = unit.get('data-pptx-role')
            placeholder = unit.get('data-pptx-placeholder')
            has_explicit_semantics = role is not None or placeholder is not None
            structurally_static = unit.get('data-pptx-layer') is not None
            legacy_chrome = (
                is_static_page_frame(role, placeholder)
                if has_explicit_semantics
                else is_chrome_id(elem_id)
            )
            explicit_legacy_override = (
                elem_id is not None
                and elem_id in ctx.animation_group_overrides
            )
            if (
                m
                and not structurally_static
                and (not legacy_chrome or explicit_legacy_override)
            ):
                fallback_targets.append((int(m.group(1)), elem_id or tag))
        else:
            if tag not in _NON_VISUAL_TAGS:
                skipped += 1

    unresolved_connector_targets = sorted(
        ctx.referenced_shape_ids - ctx.claimed_shape_ids
    )
    if unresolved_connector_targets:
        raise SvgNativeConversionError(
            'Connector target shape ids were reserved but not restored: '
            + ', '.join(str(shape_id) for shape_id in unresolved_connector_targets)
        )

    # Animation target fallback. Semantic <g id="..."> groups are the
    # preferred anchors (set inside convert_g). When the SVG has none
    # at the root we fall back to top-level primitives, but only when
    # the count is reasonable. Presenter-click animation should reveal
    # semantic blocks, not atomized drawing primitives, so fallback is
    # intentionally capped at a low count.
    _ANIM_FALLBACK_CAP = 8
    if (
        not has_top_level_group
        and not ctx.anim_targets
        and 0 < len(fallback_targets) <= _ANIM_FALLBACK_CAP
    ):
        ctx.anim_targets = fallback_targets

    if verbose:
        promoted = (
            f', promoted {promoted_backgrounds} background'
            if promoted_backgrounds else ''
        )
        print(f'  Converted {converted} elements, skipped {skipped}{promoted}')

    if trace_out is not None:
        trace_entry = {
            'slide_num': slide_num,
            'svg': str(svg_path),
            'page_role': root.get('data-pptx-page-role'),
            'summary': {
                'converted': converted,
                'skipped': skipped,
                'promoted_backgrounds': promoted_backgrounds,
                'media_files': len(ctx.media_files),
                'package_files': len(ctx.package_files),
                'relationships': len(ctx.rel_entries),
                'animation_targets': len(ctx.anim_targets),
            },
            'preprocess': trace_steps,
            'events': trace_events or [],
        }
        if dangerous_nonconforming_export:
            trace_entry['dangerous_normalizations'] = dangerous_normalizations
        trace_out.append(trace_entry)

    shapes_xml = '\n'.join(shapes)

    slide_xml = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
       xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
       xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
<p:cSld>
{background_xml}
<p:spTree>
<p:nvGrpSpPr>
<p:cNvPr id="1" name=""/>
<p:cNvGrpSpPr/><p:nvPr/>
</p:nvGrpSpPr>
<p:grpSpPr>
<a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/>
<a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm>
</p:grpSpPr>
{shapes_xml}
</p:spTree>
</p:cSld>
<p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr>
</p:sld>'''

    return (
        slide_xml,
        ctx.media_files,
        ctx.rel_entries,
        ctx.anim_targets,
        ctx.package_files,
        ctx.content_type_overrides,
    )
