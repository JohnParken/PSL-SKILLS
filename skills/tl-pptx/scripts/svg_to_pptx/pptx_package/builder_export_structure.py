"""Native PPTX export phase; retains upstream control flow."""
# Adapted from hugohe3/ppt-master 7e0df6705de0cd24345210d31f07fcb5a32b6900.
# MIT; Copyright (c) 2025-2026 Hugo He. See references/LICENSE-PPT-MASTER.txt and references/upstream.md.

from __future__ import annotations
from xml.etree import ElementTree as ET
from ..drawingml.theme_fonts import (
    apply_master_text_style_spec,
)
from .template_structure import (
    TemplateStructureError,
)

from .builder import (
    _apply_explicit_layout_structure,
    _apply_morph_shape_names,
    _apply_preserved_structure,
    _convert_page_number_texts_to_fields,
    _extract_baseline_layout_families,
    _install_source_themes_by_master,
    _prepare_flat_structure,
    _promote_common_chrome_shapes_to_layouts,
    _promote_common_chrome_shapes_to_masters,
    _promote_common_slide_backgrounds_to_masters,
    _prune_unreferenced_definition_payload_parts,
    _prune_unused_slide_layouts,
    _remove_trailing_layout_definition_slides,
    _top_level_shape_name_roster,
)

from .builder_state import BuildState

def export_structure(state: BuildState) -> None:
    """Execute upstream structure stage with explicit shared state."""
    if (
        state.use_native_shapes
        and state.pptx_structure == "baseline"
        and state.success_count == len(state.svg_files)
    ):
        _convert_page_number_texts_to_fields(
            state.extract_dir,
            len(state.svg_files),
            state.conversion_trace if state.conversion_trace is not None else state.structure_trace,
            context="Baseline",
            verbose=state.verbose,
        )
        _promote_common_slide_backgrounds_to_masters(
            state.extract_dir,
            state.structure,
            len(state.svg_files),
            verbose=state.verbose,
        )
        _promote_common_chrome_shapes_to_masters(
            state.extract_dir,
            state.structure,
            len(state.svg_files),
            state.conversion_trace if state.conversion_trace is not None else state.structure_trace,
            verbose=state.verbose,
        )
        _extract_baseline_layout_families(
            state.extract_dir,
            state.structure,
            state.svg_files,
            verbose=state.verbose,
        )
        _promote_common_chrome_shapes_to_layouts(
            state.extract_dir,
            len(state.svg_files),
            state.conversion_trace if state.conversion_trace is not None else state.structure_trace,
            verbose=state.verbose,
        )
        _prune_unused_slide_layouts(
            state.extract_dir,
            state.structure,
            len(state.svg_files),
            verbose=state.verbose,
        )

    if (
        state.use_native_shapes
        and state.pptx_structure == "flat"
        and state.success_count == len(state.svg_files)
    ):
        _prepare_flat_structure(
            state.extract_dir,
            state.structure,
            len(state.svg_files),
            state.master_text_style_spec,
            state.structure_name,
            verbose=state.verbose,
        )

    if (
        state.use_native_shapes
        and state.pptx_structure == "structured"
        and state.success_count == len(state.svg_files)
    ):
        _convert_page_number_texts_to_fields(
            state.extract_dir,
            len(state.svg_files),
            state.conversion_trace if state.conversion_trace is not None else state.structure_trace,
            context="Structured",
            verbose=state.verbose,
        )
        if state.template_specs is None:
            raise TemplateStructureError(
                "Structured metadata was not parsed before export"
            )
        (
            state.template_background_expectations,
            state.template_shape_roster_expectations,
            state.template_layout_parts_by_key,
            state.template_master_parts_by_key,
        ) = _apply_explicit_layout_structure(
            state.extract_dir,
            state.structure,
            state.template_specs,
            state.conversion_trace if state.conversion_trace is not None else state.structure_trace,
            state.active_theme_font_spec,
            use_layout_placeholder_frames=state.use_layout_placeholder_frames,
            public_slide_count=state.public_slide_count,
            verbose=state.verbose,
        )
        if state.source_theme_xml_by_master is not None:
            _install_source_themes_by_master(
                state.extract_dir,
                state.template_master_parts_by_key,
                state.source_theme_xml_by_master,
            )
        state.master_count = apply_master_text_style_spec(
            state.extract_dir,
            state.master_text_style_spec,
        )
        if state.verbose:
            print(
                "  Structured master text styles: "
                f"{state.master_count} master(s), "
                f"title {state.master_text_style_spec.title_hpt / 100:g}pt, "
                "body levels "
                f"{state.master_text_style_spec.body_levels_hpt[0] / 100:g}–"
                f"{state.master_text_style_spec.body_levels_hpt[-1] / 100:g}pt"
            )
        _prune_unused_slide_layouts(
            state.extract_dir,
            state.structure,
            len(state.svg_files),
            verbose=state.verbose,
        )

    if (
        state.use_native_shapes
        and state.pptx_structure == "preserve"
        and state.success_count == len(state.svg_files)
    ):
        _convert_page_number_texts_to_fields(
            state.extract_dir,
            len(state.svg_files),
            state.conversion_trace if state.conversion_trace is not None else state.structure_trace,
            context="Preserve",
            verbose=state.verbose,
        )
        if state.template_specs is None or state.native_structure_contract is None:
            raise TemplateStructureError(
                "Preserved structure metadata was not parsed before export"
            )
        state.rebuilt_specs = [
            spec
            for spec in state.template_specs
            if spec.slide_num not in state.passthrough_slides | state.overlay_slides
        ]
        if state.rebuilt_specs:
            _apply_preserved_structure(
                state.extract_dir,
                state.rebuilt_specs,
                state.native_structure_contract,
                state.conversion_trace if state.conversion_trace is not None else state.structure_trace,
                verbose=state.verbose,
            )

    if state.roundtrip_export:
        state.pruned_roundtrip_payloads = (
            _prune_unreferenced_definition_payload_parts(
                state.extract_dir,
                preserved_parts=state.roundtrip_source_parts - state.replaced_narration_parts,
            )
        )
        if state.verbose and state.pruned_roundtrip_payloads:
            print(
                "  Round-trip overlay: pruned "
                f"{state.pruned_roundtrip_payloads} unused payload part(s)"
            )

    if (
        state.use_native_shapes
        and state.pptx_structure == "structured"
        and state.definition_svg_files
        and state.success_count == state.total_slide_count
    ):
        state.removed = _remove_trailing_layout_definition_slides(
            state.extract_dir,
            state.public_slide_count,
            state.total_slide_count,
        )
        state.pruned_payload_parts = _prune_unreferenced_definition_payload_parts(
            state.extract_dir
        )
        for state.slide_num in range(state.public_slide_count + 1, state.total_slide_count + 1):
            state.slide_part = f"ppt/slides/slide{state.slide_num}.xml"
            if state.template_background_expectations is not None:
                state.template_background_expectations.pop(state.slide_part, None)
            if state.template_shape_roster_expectations is not None:
                state.template_shape_roster_expectations.pop(state.slide_part, None)
        if state.verbose:
            print(
                "  Layout definition carriers: "
                f"removed {state.removed} internal slide(s), pruned "
                f"{state.pruned_payload_parts} orphan payload part(s)"
            )

    state.morph_trace_names = _apply_morph_shape_names(
        state.extract_dir,
        state.morph_pairs,
        state.public_slide_numbers,
        state.morph_shape_ids,
        preserved_slides=state.morph_preserved_slides,
    )
    if state.template_shape_roster_expectations is not None:
        for state.slide_number in state.morph_trace_names:
            state.slide_part = f"ppt/slides/slide{state.slide_number}.xml"
            state.template_shape_roster_expectations[
                state.slide_part
            ] = _top_level_shape_name_roster(
                ET.parse(state.extract_dir / state.slide_part).getroot()
            )
    if state.conversion_trace is not None:
        for state.trace_entry in state.conversion_trace:
            state.slide_number = int(state.trace_entry.get("slide_num", 0))
            state.names = state.morph_trace_names.get(state.slide_number)
            if state.names:
                state.trace_entry["morph_names"] = dict(sorted(state.names.items()))
