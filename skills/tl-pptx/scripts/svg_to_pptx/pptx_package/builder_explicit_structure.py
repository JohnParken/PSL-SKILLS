"""Core PPTX assembly: create_pptx_with_native_svg."""
# Adapted from hugohe3/ppt-master 7e0df6705de0cd24345210d31f07fcb5a32b6900.
# MIT; Copyright (c) 2025-2026 Hugo He. See references/LICENSE-PPT-MASTER.txt and references/upstream.md.

from __future__ import annotations
from pathlib import (
    Path,
)
from typing import Any
from xml.etree import ElementTree as ET
from ..drawingml.theme_fonts import (
    ThemeFontSpec,
)
from .template_structure import (
    NativeStructureContract,
    TemplateSlideSpec,
    TemplateStructureError,
    is_proxy_placeholder,
    match_native_placeholders,
    template_placeholder_bindings,
)

from .builder import (
    PptxStructureContext,
    _TemplateRuntimeSlide,
)

def _apply_explicit_layout_structure(
    extract_dir: Path,
    structure: PptxStructureContext,
    specs: list[TemplateSlideSpec],
    conversion_traces: list[dict[str, Any]] | None,
    theme_font_spec: ThemeFontSpec | None,
    *,
    use_layout_placeholder_frames: bool = False,
    public_slide_count: int | None = None,
    verbose: bool = False,
) -> tuple[
    dict[str, str | None],
    dict[str, tuple[str, ...]],
    dict[str, str],
    dict[str, str],
]:
    """Materialize explicit SVG master/layout/placeholder metadata into OOXML."""
    from .builder import (
        _append_shape_to_part,
        _append_shape_to_runtime_slide,
        _apply_layout_frame_to_placeholder_carrier,
        _apply_template_slide_backgrounds,
        _assign_structured_masters,
        _clear_master_placeholder_shapes,
        _create_custom_layout,
        _extract_slide_background_xml,
        _layout_placeholder_shape,
        _move_template_static_shape,
        _patch_slide_placeholder,
        _placeholder_binding_proxy,
        _presentation_slide_size_emu,
        _relationships_path_for_part,
        _resolve_package_target,
        _set_shape_name,
        _set_slide_layout_target,
        _set_template_layout_header_footer,
        _template_runtime_slides,
        _template_shape_for_item,
        _top_level_shape_name_roster,
        _unwrap_placeholder_carrier,
        _write_xml_tree,
    )
    master_parts_by_key = _assign_structured_masters(
        extract_dir,
        structure,
        specs,
    )
    states = _template_runtime_slides(extract_dir, specs, conversion_traces)
    states_by_slide = {state.spec.slide_num: state for state in states}
    slide_size_emu = _presentation_slide_size_emu(extract_dir)

    expected_backgrounds: dict[str, str | None] = {}
    expected_shape_rosters: dict[str, tuple[str, ...]] = {}
    states_by_master: dict[str, list[_TemplateRuntimeSlide]] = {}
    for state in states:
        master_part = master_parts_by_key[state.spec.master_key]
        states_by_master.setdefault(master_part, []).append(state)
    master_shape_count = 0
    for master_part, master_states in states_by_master.items():
        master_path = extract_dir / master_part
        master_rels_path = _relationships_path_for_part(extract_dir, master_part)
        expected_backgrounds[master_part] = _extract_slide_background_xml(
            master_path.read_text(encoding="utf-8")
        )
        _clear_master_placeholder_shapes(master_path)
        master_items = master_states[0].spec.master_elements
        for item in master_items:
            background_xml = _move_template_static_shape(
                master_states,
                item,
                master_path,
                master_rels_path,
                slide_size_emu,
                public_slide_count=public_slide_count,
            )
            if background_xml is not None:
                expected_backgrounds[master_part] = background_xml
            master_shape_count += 1

    specs_by_layout: dict[str, list[TemplateSlideSpec]] = {}
    for spec in specs:
        specs_by_layout.setdefault(spec.layout_key, []).append(spec)
    placeholder_count = 0
    layout_shape_count = 0
    created_layout_parts: set[str] = set()
    layout_parts_by_key: dict[str, str] = {}
    for layout_key, layout_specs in specs_by_layout.items():
        layout_states = [states_by_slide[spec.slide_num] for spec in layout_specs]
        master_parts = {
            structure.slide_master_part(spec.slide_num) for spec in layout_specs
        }
        if len(master_parts) != 1:
            raise TemplateStructureError(
                f"Layout {layout_key!r} spans multiple slide masters; use distinct "
                "layout keys per master"
            )
        master_part = next(iter(master_parts))
        prototype = layout_specs[0]
        base_target = structure.slide_layout_target(prototype.slide_num)
        base_layout_part = _resolve_package_target(
            f"ppt/slides/slide{prototype.slide_num}.xml",
            base_target,
        )
        layout_target, layout_part = _create_custom_layout(
            extract_dir,
            master_part,
            base_layout_part,
            prototype.layout_name,
            show_master_shapes=prototype.layout_show_master_shapes,
        )
        layout_path = extract_dir / layout_part
        layout_rels_path = _relationships_path_for_part(extract_dir, layout_part)
        created_layout_parts.add(layout_part)
        layout_parts_by_key[layout_key] = layout_part

        placeholder_bindings = {
            binding.element.element_id: binding
            for binding in template_placeholder_bindings(prototype)
        }
        for item in prototype.elements:
            if item.layer == "layout":
                background_xml = _move_template_static_shape(
                    layout_states,
                    item,
                    layout_path,
                    layout_rels_path,
                    slide_size_emu,
                )
                if background_xml is not None:
                    expected_backgrounds[layout_part] = background_xml
                layout_shape_count += 1
                continue
            if not item.placeholder:
                continue
            proxy_binding = is_proxy_placeholder(item)
            if proxy_binding:
                placeholder_shapes = [
                    _template_shape_for_item(state, item)
                    for state in layout_states
                ]
                if any(shape is None for shape in placeholder_shapes):
                    raise TemplateStructureError(
                        f"Placeholder {item.element_id!r} cannot be a slide background"
                    )
                resolved_shapes = [
                    shape for shape in placeholder_shapes if shape is not None
                ]
            else:
                resolved_shapes = [
                    _unwrap_placeholder_carrier(state, item)
                    for state in layout_states
                ]
            prototype_shape = resolved_shapes[0]
            binding = placeholder_bindings[item.element_id]
            assigned_idx = binding.assigned_idx
            layout_placeholder = _layout_placeholder_shape(
                prototype_shape,
                item,
                assigned_idx,
                theme_font_spec,
            )
            _append_shape_to_part(layout_path, layout_placeholder)
            for state, shape in zip(layout_states, resolved_shapes):
                if not proxy_binding:
                    _patch_slide_placeholder(
                        shape,
                        item,
                        assigned_idx,
                        theme_font_spec=theme_font_spec,
                    )
                    if use_layout_placeholder_frames:
                        _apply_layout_frame_to_placeholder_carrier(shape, item)
                    _set_shape_name(
                        shape,
                        f"{item.element_id} Placeholder Carrier",
                    )
                else:
                    _set_shape_name(
                        shape,
                        f"{item.element_id} Proxy Content",
                    )
                    _append_shape_to_runtime_slide(
                        state,
                        _placeholder_binding_proxy(layout_placeholder, item),
                    )
            placeholder_count += 1

        _set_template_layout_header_footer(layout_path, prototype.placeholders)
        for state in layout_states:
            _set_slide_layout_target(state.rels_path, layout_target)

    slide_backgrounds = _apply_template_slide_backgrounds(
        states,
        slide_size_emu,
    )
    expected_backgrounds.update(slide_backgrounds)
    for state in states:
        state.root.set(
            "showMasterSp",
            "1" if state.spec.slide_show_inherited_shapes else "0",
        )
        _write_xml_tree(state.slide_path, state.tree)
        expected_backgrounds.setdefault(
            f"ppt/slides/slide{state.spec.slide_num}.xml",
            None,
        )
        expected_shape_rosters[
            f"ppt/slides/slide{state.spec.slide_num}.xml"
        ] = _top_level_shape_name_roster(state.root)
    for part in states_by_master:
        expected_backgrounds.setdefault(
            part,
            _extract_slide_background_xml(
                (extract_dir / part).read_text(encoding="utf-8")
            ),
        )
    for part in created_layout_parts:
        expected_backgrounds.setdefault(part, None)
    for part in (*states_by_master, *created_layout_parts):
        expected_shape_rosters[part] = _top_level_shape_name_roster(
            ET.parse(extract_dir / part).getroot()
        )

    if verbose:
        print(
            "  Explicit Layout structure: "
            f"{len(states_by_master)} master(s), "
            f"{len(specs_by_layout)} layout(s), "
            f"{master_shape_count} master element(s), "
            f"{layout_shape_count} layout element(s), "
            f"{len(slide_backgrounds)} slide background(s), "
            f"{placeholder_count} placeholder definition(s)"
        )
    return (
        expected_backgrounds,
        expected_shape_rosters,
        layout_parts_by_key,
        master_parts_by_key,
    )


def _apply_preserved_structure(
    extract_dir: Path,
    specs: list[TemplateSlideSpec],
    contract: NativeStructureContract,
    conversion_traces: list[dict[str, Any]] | None,
    *,
    verbose: bool = False,
) -> None:
    """Drop preview-only inherited layers and bind content to source placeholders."""
    from .builder import (
        PML_NS,
        _patch_slide_placeholder,
        _remove_template_shape,
        _template_runtime_slides,
        _template_shape_for_item,
        _write_xml_tree,
    )
    states = _template_runtime_slides(extract_dir, specs, conversion_traces)
    removed_preview_shapes = 0
    removed_preview_backgrounds = 0
    placeholder_count = 0
    for state in states:
        removed_background = False
        for item in state.spec.elements:
            if item.layer not in {"master", "layout"}:
                continue
            shape = _template_shape_for_item(state, item)
            if shape is not None:
                _remove_template_shape(state, shape)
                removed_preview_shapes += 1
                continue
            if removed_background:
                raise TemplateStructureError(
                    f"{state.spec.svg_path.name}: multiple inherited preview "
                    "backgrounds resolved to one slide background"
                )
            common_slide = state.root.find(f"{{{PML_NS}}}cSld")
            background = (
                common_slide.find(f"{{{PML_NS}}}bg")
                if common_slide is not None
                else None
            )
            if common_slide is None or background is None:
                raise TemplateStructureError(
                    f"{state.spec.svg_path.name}: inherited preview background "
                    "did not produce a removable slide background"
                )
            common_slide.remove(background)
            removed_background = True
            removed_preview_backgrounds += 1

        layout = contract.layout(state.spec.layout_key)
        for item, source_placeholder in match_native_placeholders(state.spec, layout):
            shape = _template_shape_for_item(state, item)
            if shape is None:
                raise TemplateStructureError(
                    f"{state.spec.svg_path.name}: placeholder {item.element_id!r} "
                    "cannot resolve to a slide background"
                )
            _patch_slide_placeholder(
                shape,
                item,
                source_placeholder.idx,
                source_placeholder.placeholder_type,
            )
            placeholder_count += 1
        _write_xml_tree(state.slide_path, state.tree)

    if verbose:
        print(
            "  Preserved structure: "
            f"{len({spec.layout_key for spec in specs})} source layout(s), "
            f"{removed_preview_shapes} preview shape(s) removed, "
            f"{removed_preview_backgrounds} preview background(s) removed, "
            f"{placeholder_count} source placeholder binding(s)"
        )
