"""Core SVG -> DrawingML dispatcher, group handling, and main entry point."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from hyperlink_contract import SHAPE_HYPERLINK_ATTR
from pptx_ooxml.package import PackagePartRegistry
from pptx_to_svg.preset_authoring import (
    materialize_compact_authored_preset_tree,
    validate_authored_preset_tree,
)
from resource_paths import icon_dir_for_project
from svg_compatibility import normalize_single_child_group_filters

from ..animation_config import effective_top_level, is_chrome_id
from ..canvas_contract import CanvasContractError, parse_project_svg_root
from ..native_objects import (
    INLINE_FORMULA_ATTR,
    NativeMarkerAttributeError,
    reserve_source_chart_parts,
    snapshot_native_fallback_freshness,
)
from ..semantic_markers import is_static_page_frame
from .context import (
    TEXT_FLOW_PRESERVE,
    TEXT_FLOW_SPLIT,
    ConvertContext,
    resolve_text_flow,
)
from .styles import get_element_opacity
from .text_properties import (
    materialize_project_text_metrics,
    resolve_project_font_sizes,
    resolve_project_letter_spacings,
)
from .theme_colors import ThemeColorSpec
from .theme_fonts import ThemeFontSpec
from .utils import SVG_NS, _extract_inheritable_styles, project_geometry_length_errors


@dataclass(frozen=True)
class _ConverterDeps:
    """Lazily imported symbols from .converter to avoid circular imports."""

    error_cls: Any
    non_visual_tags: Any
    build_source_shape_id_map: Any
    extract_background_candidate: Any
    hydrate_native_payloads: Any
    mark_unchanged_preset_previews: Any
    mark_unchanged_txbody_groups: Any
    require_inline_formula_markers: Any
    require_native_marker_attributes: Any
    require_project_clip_paths: Any
    require_project_definitions: Any
    require_project_effect_status: Any
    require_project_filters: Any
    require_project_freeform_geometry: Any
    require_project_gradients: Any
    require_project_hyperlinks: Any
    require_project_image_aspect_ratios: Any
    require_project_images: Any
    require_project_line_end_markers: Any
    require_project_masks: Any
    require_project_nested_svg_crops: Any
    require_project_opacities: Any
    require_project_paint_references: Any
    require_project_paints: Any
    require_project_stroke_styles: Any
    require_project_text_properties: Any
    require_project_transforms: Any
    root_viewport_size: Any
    collect_defs: Any
    collect_unsupported_visuals: Any
    convert_element: Any


@dataclass
class _SlideConvertState:
    """Intermediate pipeline state across SVG slide preflight and lowering stages."""

    svg_path: Path
    tree: ET.ElementTree
    root: ET.Element
    text_flow: str
    resource_root: Path | None
    slide_count: int | None
    verbose: bool
    native_objects: bool
    dangerous_nonconforming_export: bool
    dangerous_normalizations: list[Any]
    trace_events: list[dict[str, Any]] | None
    trace_steps: list[dict[str, Any]] = field(default_factory=list)
    geometry_count: int = 0
    geometry_trace: dict[str, Any] | None = None
    viewport_width: float = 0.0
    viewport_height: float = 0.0
    text_font_sizes: dict[int, float] = field(default_factory=dict)
    text_letter_spacings: dict[int, float] = field(default_factory=dict)


def _load_converter_deps() -> _ConverterDeps:
    """Load shared .converter helpers at call time while preserving facade bindings."""
    from .converter import (
        SvgNativeConversionError,
        _NON_VISUAL_TAGS,
        _build_source_shape_id_map,
        _extract_background_candidate,
        _hydrate_native_payloads,
        _mark_unchanged_preset_previews,
        _mark_unchanged_txbody_groups,
        _require_inline_formula_markers,
        _require_native_marker_attributes,
        _require_project_clip_paths,
        _require_project_definitions,
        _require_project_effect_status,
        _require_project_filters,
        _require_project_freeform_geometry,
        _require_project_gradients,
        _require_project_hyperlinks,
        _require_project_image_aspect_ratios,
        _require_project_images,
        _require_project_line_end_markers,
        _require_project_masks,
        _require_project_nested_svg_crops,
        _require_project_opacities,
        _require_project_paint_references,
        _require_project_paints,
        _require_project_stroke_styles,
        _require_project_text_properties,
        _require_project_transforms,
        _root_viewport_size,
        collect_defs,
        collect_unsupported_visuals,
        convert_element,
    )

    return _ConverterDeps(
        error_cls=SvgNativeConversionError,
        non_visual_tags=_NON_VISUAL_TAGS,
        build_source_shape_id_map=_build_source_shape_id_map,
        extract_background_candidate=_extract_background_candidate,
        hydrate_native_payloads=_hydrate_native_payloads,
        mark_unchanged_preset_previews=_mark_unchanged_preset_previews,
        mark_unchanged_txbody_groups=_mark_unchanged_txbody_groups,
        require_inline_formula_markers=_require_inline_formula_markers,
        require_native_marker_attributes=_require_native_marker_attributes,
        require_project_clip_paths=_require_project_clip_paths,
        require_project_definitions=_require_project_definitions,
        require_project_effect_status=_require_project_effect_status,
        require_project_filters=_require_project_filters,
        require_project_freeform_geometry=_require_project_freeform_geometry,
        require_project_gradients=_require_project_gradients,
        require_project_hyperlinks=_require_project_hyperlinks,
        require_project_image_aspect_ratios=_require_project_image_aspect_ratios,
        require_project_images=_require_project_images,
        require_project_line_end_markers=_require_project_line_end_markers,
        require_project_masks=_require_project_masks,
        require_project_nested_svg_crops=_require_project_nested_svg_crops,
        require_project_opacities=_require_project_opacities,
        require_project_paint_references=_require_project_paint_references,
        require_project_paints=_require_project_paints,
        require_project_stroke_styles=_require_project_stroke_styles,
        require_project_text_properties=_require_project_text_properties,
        require_project_transforms=_require_project_transforms,
        root_viewport_size=_root_viewport_size,
        collect_defs=collect_defs,
        collect_unsupported_visuals=collect_unsupported_visuals,
        convert_element=convert_element,
    )


def _require_common_project_styles(
    root: ET.Element,
    svg_path: Path,
    deps: _ConverterDeps,
) -> None:
    """Run the shared visual style, paint, filter, and transform preflight checks."""
    deps.require_project_text_properties(root, svg_path)
    deps.require_project_stroke_styles(root, svg_path)
    deps.require_project_opacities(root, svg_path)
    deps.require_project_paints(root, svg_path)
    deps.require_project_masks(root, svg_path)
    deps.require_project_definitions(root, svg_path)
    deps.require_project_paint_references(root, svg_path)
    deps.require_project_gradients(root, svg_path)
    deps.require_project_effect_status(root, svg_path)
    deps.require_project_filters(root, svg_path)
    deps.require_project_image_aspect_ratios(root, svg_path)
    deps.require_project_transforms(root, svg_path)


def _preflight_and_normalize_svg_root(
    svg_path: str | Path,
    *,
    deps: _ConverterDeps,
    resource_root: Path | None,
    slide_count: int | None,
    verbose: bool,
    native_objects: bool,
    text_flow: str,
    trace_out: list[dict[str, Any]] | None,
    dangerous_nonconforming_export: bool,
) -> _SlideConvertState:
    """Validate path bounds, parse SVG, lower authored presets, and materialize geometry."""
    path_obj = Path(svg_path)
    resolved_root = Path(resource_root).resolve() if resource_root is not None else None
    if resolved_root is not None:
        try:
            path_obj.resolve().relative_to(resolved_root)
        except ValueError as exc:
            raise deps.error_cls(
                f'{path_obj.name}: SVG source is outside resource root {resolved_root}'
            ) from exc
    tree = ET.parse(str(path_obj))
    root = tree.getroot()
    deps.hydrate_native_payloads(root, path_obj)
    try:
        parse_project_svg_root(root, context=path_obj.name)
    except CanvasContractError as exc:
        raise deps.error_cls(str(exc)) from exc
    dangerous_normalizations = (
        normalize_single_child_group_filters(root) if dangerous_nonconforming_export else []
    )
    if dangerous_normalizations and verbose:
        print(
            '  [DANGEROUS][NORMALIZED] '
            f'{len(dangerous_normalizations)} single-child group filter(s)'
        )
    deps.require_native_marker_attributes(root, path_obj)
    deps.require_inline_formula_markers(root, path_obj)
    deps.require_project_hyperlinks(root, path_obj, slide_count=slide_count)
    deps.require_project_nested_svg_crops(root, path_obj)
    deps.require_project_clip_paths(root, path_obj)
    authored_errors = validate_authored_preset_tree(root)
    if authored_errors:
        raise deps.error_cls(
            'Invalid authored preset structure: ' + '; '.join(authored_errors)
        )
    # Validate the source contract once, then lower compact groups to expanded IR.
    try:
        materialize_compact_authored_preset_tree(root)
    except ValueError as exc:
        raise deps.error_cls(f'Invalid compact authored preset: {exc}') from exc
    deps.mark_unchanged_txbody_groups(root)
    deps.mark_unchanged_preset_previews(root)
    has_roundtrip = any(
        elem.get('data-pptx-roundtrip-object') is not None for elem in root.iter()
    )
    if native_objects or has_roundtrip:
        try:
            snapshot_native_fallback_freshness(root)
        except NativeMarkerAttributeError as exc:
            raise deps.error_cls(
                f'{path_obj.name}: conflicting chart/table replacement metadata: {exc}'
            ) from exc
    state = _SlideConvertState(
        svg_path=path_obj,
        tree=tree,
        root=root,
        text_flow=text_flow,
        resource_root=resolved_root,
        slide_count=slide_count,
        verbose=verbose,
        native_objects=native_objects,
        dangerous_nonconforming_export=dangerous_nonconforming_export,
        dangerous_normalizations=dangerous_normalizations,
        trace_events=[] if trace_out is not None else None,
    )
    from ..geometry_properties import (
        GeometryStyleError,
        materialize_inline_geometry_properties,
    )

    try:
        state.geometry_count = materialize_inline_geometry_properties(root)
    except GeometryStyleError as exc:
        raise deps.error_cls(
            f'{path_obj.name}: inline geometry materialization failed: {exc}'
        ) from exc
    if state.geometry_count:
        state.geometry_trace = {
            'action': 'materialize-inline-geometry',
            'count': state.geometry_count,
        }
        state.trace_steps.append(state.geometry_trace)
        if verbose:
            print(f'  Materialized {state.geometry_count} inline geometry declaration(s)')
    length_errors = project_geometry_length_errors(root)
    if length_errors:
        preview = '; '.join(length_errors[:8])
        suffix = '' if len(length_errors) <= 8 else f'; +{len(length_errors) - 8} more'
        raise deps.error_cls(
            f'{path_obj.name}: invalid project geometry length(s): {preview}{suffix}'
        )
    _require_common_project_styles(root, path_obj, deps)
    deps.require_project_freeform_geometry(root, path_obj)
    deps.require_project_line_end_markers(root, path_obj)
    state.viewport_width, state.viewport_height = deps.root_viewport_size(root)
    return state


def _expand_icons_and_local_uses(state: _SlideConvertState, deps: _ConverterDeps) -> None:
    """Expand icon placeholders and static local <use> trees, then recheck styles."""
    from ..geometry_properties import (
        GeometryStyleError,
        materialize_inline_geometry_properties,
    )
    from ..use_expander import (
        UseExpansionError,
        expand_local_use_references,
        expand_use_data_icons,
    )

    root, svg_path = state.root, state.svg_path
    icons_dir = icon_dir_for_project(state.resource_root)
    try:
        expanded = expand_use_data_icons(root, icons_dir)
    except UseExpansionError as exc:
        raise deps.error_cls(f'{svg_path.name}: icon expansion failed: {exc}') from exc
    if expanded:
        state.trace_steps.append({'action': 'expand-use-data-icons', 'count': expanded})
        if state.verbose:
            print(f'  Expanded {expanded} <use data-icon="..."/> placeholder(s)')
        hydrated = deps.hydrate_native_payloads(root, svg_path)
        if hydrated:
            state.trace_steps.append({
                'action': 'hydrate-native-payloads-from-icons',
                'count': hydrated,
            })
        deps.mark_unchanged_txbody_groups(root)
        deps.mark_unchanged_preset_previews(root)
        deps.require_project_freeform_geometry(root, svg_path)
    try:
        injected_count = materialize_inline_geometry_properties(root)
    except GeometryStyleError as exc:
        raise deps.error_cls(
            f'{svg_path.name}: expanded icon geometry materialization failed: {exc}'
        ) from exc
    if injected_count:
        state.geometry_count += injected_count
        if state.geometry_trace is None:
            state.geometry_trace = {
                'action': 'materialize-inline-geometry',
                'count': state.geometry_count,
            }
            state.trace_steps.append(state.geometry_trace)
        else:
            state.geometry_trace['count'] = state.geometry_count
        if state.verbose:
            print(
                f'  Materialized {injected_count} inline geometry '
                'declaration(s) from expanded icons'
            )
    try:
        expanded_local = expand_local_use_references(root)
    except UseExpansionError as exc:
        raise deps.error_cls(
            f'{svg_path.name}: local <use> expansion failed: {exc}'
        ) from exc
    if expanded_local:
        state.trace_steps.append({'action': 'expand-local-use-references', 'count': expanded_local})
        if state.verbose:
            print(f'  Expanded {expanded_local} local <use href="#..."/> instance(s)')
    if state.dangerous_nonconforming_export:
        extra_norms = normalize_single_child_group_filters(root)
        if extra_norms:
            state.dangerous_normalizations.extend(extra_norms)
            if state.verbose:
                print(
                    '  [DANGEROUS][NORMALIZED] '
                    f'{len(extra_norms)} expanded-resource group filter(s)'
                )
    deps.require_inline_formula_markers(root, svg_path)
    deps.require_project_hyperlinks(root, svg_path, slide_count=state.slide_count)
    # Recheck compiler-injected icon/use wrappers and cloned definition trees.
    deps.require_project_nested_svg_crops(root, svg_path)
    deps.require_project_images(root, svg_path, state.resource_root)
    deps.require_project_clip_paths(root, svg_path)
    _require_common_project_styles(root, svg_path, deps)


def _lower_positional_text_and_metrics(
    state: _SlideConvertState,
    deps: _ConverterDeps,
) -> None:
    """Materialize text metrics, flatten positional <tspan> trees, and validate visuals."""
    from ..tspan_flattener import flatten_positional_tspans

    root, svg_path = state.root, state.svg_path
    try:
        materialize_project_text_metrics(root)
    except ValueError as exc:
        raise deps.error_cls(
            f'{svg_path.name}: text-metric materialization failed: {exc}'
        ) from exc
    formula_before = sum(1 for el in root.iter() if el.get(INLINE_FORMULA_ATTR) is not None)
    link_before = sum(
        1 for el in root.iter()
        if el.tag == f'{{{SVG_NS}}}a' or el.get(SHAPE_HYPERLINK_ATTR) is not None
    )
    flattened = flatten_positional_tspans(
        state.tree,
        merge_paragraphs=state.text_flow != TEXT_FLOW_SPLIT,
        preserve_line_breaks=state.text_flow == TEXT_FLOW_PRESERVE,
    )
    if flattened:
        state.trace_steps.append({
            'action': 'flatten-positional-tspans',
            'text_flow': state.text_flow,
            'merge_paragraphs': state.text_flow != TEXT_FLOW_SPLIT,
        })
        if state.verbose:
            print(f'  Lowered positional <tspan> using {state.text_flow} text flow')
    formula_after = sum(1 for el in root.iter() if el.get(INLINE_FORMULA_ATTR) is not None)
    link_after = sum(
        1 for el in root.iter()
        if el.tag == f'{{{SVG_NS}}}a' or el.get(SHAPE_HYPERLINK_ATTR) is not None
    )
    if formula_after != formula_before:
        raise deps.error_cls(
            f'{svg_path.name}: positional text lowering changed inline formula '
            f'marker count from {formula_before} to {formula_after}'
        )
    if link_after != link_before:
        raise deps.error_cls(
            f'{svg_path.name}: positional text lowering changed hyperlink '
            f'count from {link_before} to {link_after}'
        )
    deps.require_inline_formula_markers(root, svg_path)
    deps.require_project_hyperlinks(root, svg_path, slide_count=state.slide_count)
    deps.require_project_text_properties(root, svg_path)
    try:
        state.text_font_sizes = resolve_project_font_sizes(root)
        state.text_letter_spacings = resolve_project_letter_spacings(
            root,
            state.text_font_sizes,
        )
    except ValueError as exc:
        raise deps.error_cls(
            f'{svg_path.name}: invalid project text-metric inheritance: {exc}'
        ) from exc
    unsupported = deps.collect_unsupported_visuals(root)
    if unsupported:
        preview = '; '.join(unsupported[:8])
        suffix = '' if len(unsupported) <= 8 else f'; +{len(unsupported) - 8} more'
        raise deps.error_cls(
            f'{svg_path.name}: unsupported visual SVG element(s): {preview}{suffix}'
        )


def _convert_top_level_shapes(
    state: _SlideConvertState,
    ctx: ConvertContext,
    deps: _ConverterDeps,
    promote_background: bool,
) -> tuple[list[str], str, int, int, int]:
    """Promote background candidate, convert top-level children, and collect animation fallbacks."""
    root = state.root
    if state.native_objects:
        reserve_source_chart_parts(root, ctx)
    shapes: list[str] = []
    converted = skipped = 0
    has_top_level_group = any(
        effective_top_level(child).tag.replace(f'{{{SVG_NS}}}', '') == 'g'
        for child in root
    )
    background_xml, background_skip_id = (
        deps.extract_background_candidate(root, ctx) if promote_background else ('', None)
    )
    promoted_backgrounds = 1 if background_xml else 0
    if background_xml and state.trace_events is not None:
        state.trace_events.append({
            'tag': 'rect',
            'decision': 'native-background',
            'reason': 'promoted-full-canvas-rect-to-bgPr',
        })
    fallback_targets: list = []
    for child in root:
        tag = child.tag.replace(f'{{{SVG_NS}}}', '')
        if tag == 'defs' or id(child) == background_skip_id:
            continue
        try:
            result = deps.convert_element(child, ctx)
        except deps.error_cls as exc:
            raise deps.error_cls(f'{state.svg_path.name}: {exc}') from exc
        if not result:
            if tag not in deps.non_visual_tags:
                skipped += 1
            continue
        shapes.append(result.xml)
        converted += 1
        match = re.search(r'<p:cNvPr id="(\d+)"', result.xml)
        unit = effective_top_level(child)
        elem_id = unit.get('id')
        role = unit.get('data-pptx-role')
        placeholder = unit.get('data-pptx-placeholder')
        legacy_chrome = (
            is_static_page_frame(role, placeholder)
            if (role is not None or placeholder is not None)
            else is_chrome_id(elem_id)
        )
        override = elem_id is not None and elem_id in ctx.animation_group_overrides
        if match and unit.get('data-pptx-layer') is None and (not legacy_chrome or override):
            fallback_targets.append((int(match.group(1)), elem_id or tag))
    unresolved = sorted(ctx.referenced_shape_ids - ctx.claimed_shape_ids)
    if unresolved:
        raise deps.error_cls(
            'Connector target shape ids were reserved but not restored: '
            + ', '.join(str(shape_id) for shape_id in unresolved)
        )
    if not has_top_level_group and not ctx.anim_targets and 0 < len(fallback_targets) <= 8:
        ctx.anim_targets = fallback_targets
    if state.verbose:
        promoted = f', promoted {promoted_backgrounds} background' if promoted_backgrounds else ''
        print(f'  Converted {converted} elements, skipped {skipped}{promoted}')
    return shapes, background_xml, converted, skipped, promoted_backgrounds


def _build_slide_xml_and_trace(
    state: _SlideConvertState,
    ctx: ConvertContext,
    shapes: list[str],
    background_xml: str,
    converted: int,
    skipped: int,
    promoted_backgrounds: int,
    slide_num: int,
    trace_out: list[dict[str, Any]] | None,
) -> str:
    """Append per-slide trace entry (if requested) and format complete <p:sld> XML."""
    if trace_out is not None:
        trace_entry = {
            'slide_num': slide_num,
            'svg': str(state.svg_path),
            'page_role': state.root.get('data-pptx-page-role'),
            'summary': {
                'converted': converted,
                'skipped': skipped,
                'promoted_backgrounds': promoted_backgrounds,
                'media_files': len(ctx.media_files),
                'package_files': len(ctx.package_files),
                'relationships': len(ctx.rel_entries),
                'animation_targets': len(ctx.anim_targets),
            },
            'preprocess': state.trace_steps,
            'events': state.trace_events or [],
        }
        if state.dangerous_nonconforming_export:
            trace_entry['dangerous_normalizations'] = state.dangerous_normalizations
        trace_out.append(trace_entry)
    shapes_xml = '\n'.join(shapes)
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
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


# Adapted from converter.py; upstream MIT, Hugo He, 2025-2026.
def convert_svg_to_slide_shapes(
    svg_path: str | Path,
    *,
    resource_root: Path,
    slide_num: int = 1,
    slide_count: int | None = None,
    verbose: bool = False,
    merge_paragraphs: bool | None = None,
    image_optimize: bool = True,
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
    """Convert an SVG file to a complete DrawingML slide XML."""
    deps = _load_converter_deps()
    resolved_flow = resolve_text_flow(text_flow, merge_paragraphs)
    state = _preflight_and_normalize_svg_root(
        svg_path,
        deps=deps,
        resource_root=resource_root,
        slide_count=slide_count,
        verbose=verbose,
        native_objects=native_objects,
        text_flow=resolved_flow,
        trace_out=trace_out,
        dangerous_nonconforming_export=dangerous_nonconforming_export,
    )
    _expand_icons_and_local_uses(state, deps)
    _lower_positional_text_and_metrics(state, deps)
    defs = deps.collect_defs(state.root)
    source_shape_id_map = deps.build_source_shape_id_map(state.root)
    root_opacity = get_element_opacity(state.root)
    ctx = ConvertContext(
        defs=defs,
        reserved_shape_ids=frozenset(source_shape_id_map.values()),
        source_shape_id_map=source_shape_id_map,
        slide_num=slide_num,
        slide_count=slide_count,
        viewport_width=state.viewport_width,
        viewport_height=state.viewport_height,
        svg_dir=state.svg_path.parent,
        resource_root=state.resource_root,
        text_flow=state.text_flow,
        image_optimize=image_optimize,
        image_max_dimension=image_max_dimension,
        image_sizing=image_sizing,
        image_scale=image_scale,
        image_quality=image_quality,
        native_objects_enabled=native_objects,
        animation_group_overrides=animation_group_overrides or frozenset(),
        trace_events=state.trace_events,
        theme_font_spec=theme_font_spec,
        theme_color_spec=theme_color_spec,
        primary_language=primary_language,
        package_parts=package_parts if package_parts is not None else PackagePartRegistry(),
        inherited_styles=_extract_inheritable_styles(state.root),
        opacity_multiplier=1.0 if root_opacity is None else root_opacity,
        parent_by_id={id(child): parent for parent in state.root.iter() for child in parent},
        text_font_sizes=state.text_font_sizes,
        text_letter_spacings=state.text_letter_spacings,
    )
    shapes, background_xml, converted, skipped, promoted_backgrounds = _convert_top_level_shapes(
        state, ctx, deps, promote_background,
    )
    slide_xml = _build_slide_xml_and_trace(
        state, ctx, shapes, background_xml, converted, skipped,
        promoted_backgrounds, slide_num, trace_out,
    )
    return (
        slide_xml,
        ctx.media_files,
        ctx.rel_entries,
        ctx.anim_targets,
        ctx.package_files,
        ctx.content_type_overrides,
    )
