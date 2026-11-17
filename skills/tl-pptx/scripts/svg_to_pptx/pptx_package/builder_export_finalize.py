"""Native PPTX export phase; retains upstream control flow."""
# Adapted from hugohe3/ppt-master 7e0df6705de0cd24345210d31f07fcb5a32b6900.
# MIT; Copyright (c) 2025-2026 Hugo He. See references/LICENSE-PPT-MASTER.txt and references/upstream.md.

from __future__ import annotations
import json
import os
import shutil
import zipfile
from pptx_transitions import (
    set_directory_use_timings,
    validate_pptx_morph_pairs,
    validate_pptx_transition_package,
)
from pptx_animations import (
    validate_pptx_animation_package,
)
from pptx_opc_validation import (
    verify_internal_relationships,
)
from pptx_ooxml.package import (
    prune_unreferenced_directory_parts,
)
from .media import (
    PNG_RENDERER,
)
from .narration import (
    AUDIO_CONTENT_TYPES,
)
from .template_structure import (
    TemplateStructureError,
)
from .template_validation import validate_pptx_template_package

from .builder import (
    NOTES_SLIDE_REL_TYPE,
    THEME_CONTENT_TYPE,
    _NOTES_MASTER_CONTENT_TYPE,
    _add_content_type_override,
    _add_default_content_type,
    _apply_template_text_language,
    _content_type_contract,
    _content_type_for_extension,
    _find_relationship_target,
    _presentation_format,
    _reinject_roundtrip_resources,
    _relax_output_permissions,
    _remove_all_notes_parts,
    _resolve_package_target,
    _stamp_docprops,
)

from .builder_state import BuildState

def export_finalize(state: BuildState) -> bool:
    """Execute upstream finalize stage with explicit shared state."""
    state.content_types_path = state.extract_dir / '[Content_Types].xml'
    with open(state.content_types_path, 'r', encoding='utf-8') as state.f:
        state.content_types = state.f.read()

    if not state.use_native_shapes:
        state.content_types = _add_default_content_type(state.content_types, 'svg', 'image/svg+xml')
    for state.ext in sorted(state.image_exts_used):
        state.content_types = _add_default_content_type(
            state.content_types,
            state.ext,
            _content_type_for_extension(state.ext),
        )
    if 'xlsx' in state.package_exts_used:
        state.content_types = _add_default_content_type(
            state.content_types,
            'xlsx',
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
    for state.part_name, state.content_type in sorted(state.package_content_overrides.items()):
        state.content_types = _add_content_type_override(state.content_types, state.part_name, state.content_type)
    with open(state.content_types_path, 'w', encoding='utf-8') as state.f:
        state.f.write(state.content_types)

    if state.audio_exts_used:
        for state.ext in sorted(state.audio_exts_used):
            state.content_type = AUDIO_CONTENT_TYPES.get(state.ext)
            if state.content_type:
                state.content_types = _add_default_content_type(
                    state.content_types,
                    state.ext.removeprefix('.'),
                    state.content_type,
                )
        if 'Extension="png"' not in state.content_types:
            state.content_types = _add_default_content_type(state.content_types, 'png', 'image/png')
        with open(state.content_types_path, 'w', encoding='utf-8') as state.f:
            state.f.write(state.content_types)

    # Add notes master / slides content types
    if state.enable_notes and state.notes_slides_created:
        for state.part_name in sorted(state.notes_master_theme_parts_created):
            state.content_types = _add_content_type_override(
                state.content_types,
                state.part_name,
                THEME_CONTENT_TYPE,
            )
        for state.part_name in sorted(state.notes_master_parts_used):
            state.content_types = _add_content_type_override(
                state.content_types,
                state.part_name,
                _NOTES_MASTER_CONTENT_TYPE,
            )
        for state.i in sorted(state.notes_slides_created):
            state.notes_target = _find_relationship_target(
                state.extract_dir / 'ppt' / 'slides' / '_rels' / f'slide{state.i}.xml.rels',
                NOTES_SLIDE_REL_TYPE,
            )
            if state.notes_target is None:
                raise RuntimeError(f"Slide {state.i} lost its generated notes relationship")
            state.content_types = _add_content_type_override(
                state.content_types,
                '/' + _resolve_package_target(f'ppt/slides/slide{state.i}.xml', state.notes_target),
                'application/vnd.openxmlformats-officedocument.presentationml.'
                'notesSlide+xml',
            )
        with open(state.content_types_path, 'w', encoding='utf-8') as state.f:
            state.f.write(state.content_types)

    if state.source_content_types_bytes is not None:
        state.current_content_types = state.content_types_path.read_bytes()
        if (
            _content_type_contract(state.current_content_types)
            == _content_type_contract(state.source_content_types_bytes)
        ):
            state.content_types_path.write_bytes(state.source_content_types_bytes)

    if state.roundtrip_export:
        state.edited_slide_parts = {
            f"ppt/slides/slide{index}.xml" for index, patch in state.slide_patches.items()
            if patch.visual_changed
        }
    else:
        # Generated slides own every relationship they carry; a shape
        # promoted to a Master/Layout part leaves its slide entry behind.
        state.edited_slide_parts = {
            path.relative_to(state.extract_dir).as_posix()
            for path in (state.extract_dir / 'ppt' / 'slides').glob('slide*.xml')
        }
    if state.page_plan_export or state.edited_slide_parts:
        state.pruned_page_plan_parts = prune_unreferenced_directory_parts(
            state.extract_dir, edited_slide_parts=state.edited_slide_parts,
        )
        if state.verbose and state.pruned_page_plan_parts:
            print(
                "  Package: pruned "
                f"{state.pruned_page_plan_parts} unreachable part(s)"
            )

    if state.package_uses_timings or state.kiosk:
        set_directory_use_timings(
            state.extract_dir,
            enabled=True if state.package_uses_timings else None,
            kiosk=state.kiosk,
        )

    state.reinjected_resources = _reinject_roundtrip_resources(
        state.extract_dir,
        state.resource_root,
        state.late_roundtrip_resources,
    )
    if state.verbose and state.reinjected_resources:
        print(
            "  Round-trip resources: reinjected "
            f"{state.reinjected_resources} source package part(s)"
        )

    if not state.enable_notes:
        _remove_all_notes_parts(state.extract_dir)

    state.rels_problems = verify_internal_relationships(state.extract_dir)
    if state.rels_problems:
        state.details = '\n'.join(f'  - {p}' for p in state.rels_problems)
        raise RuntimeError(
            'PPTX package contains dangling internal relationship targets; '
            'PowerPoint will report the file as corrupt:\n' + state.details
        )

    if state.primary_language is not None and not state.roundtrip_export:
        _apply_template_text_language(state.extract_dir, state.primary_language)

    # Replace the python-pptx base-template metadata (stale "Steve Canny"
    # author, 2013 dates, "generated using python-pptx", Slides=0) with
    # accurate, tool-neutral document properties.
    state.pres_format = _presentation_format(state.width_emu, state.height_emu)
    state.effective_doc_metadata = dict(state.doc_metadata or {})
    if state.primary_language is not None:
        state.effective_doc_metadata['language'] = state.primary_language
    if not state.roundtrip_export or state.doc_metadata:
        _stamp_docprops(
            state.extract_dir,
            state.public_slide_count,
            state.pres_format,
            state.effective_doc_metadata,
        )

    # Repackage PPTX to a temporary file first. The public output path is
    # replaced only after every slide and relationship has succeeded.
    state.temp_output_path = state.temp_dir / 'result.pptx'
    with zipfile.ZipFile(state.temp_output_path, 'w', zipfile.ZIP_DEFLATED) as state.zf:
        for state.file_path in state.extract_dir.rglob('*'):
            if state.file_path.is_file():
                state.arcname = state.file_path.relative_to(state.extract_dir)
                state.zf.write(state.file_path, state.arcname)
    if (
        state.use_native_shapes
        and state.pptx_structure == "structured"
        and state.success_count == len(state.svg_files)
    ):
        if state.template_specs is None or state.public_template_specs is None:
            raise TemplateStructureError(
                "Explicit Layout metadata was not parsed before validation"
            )
        try:
            validate_pptx_template_package(
                state.temp_output_path,
                state.public_template_specs,
                layout_specs=state.template_specs,
                expected_layout_parts=state.template_layout_parts_by_key,
                expected_master_parts=state.template_master_parts_by_key,
                expected_backgrounds=state.template_background_expectations,
                expected_shape_rosters=state.template_shape_roster_expectations,
            )
        except ValueError as exc:
            raise TemplateStructureError(
                f"PPTX structured package validation failed: {exc}"
            ) from exc
    try:
        validate_pptx_transition_package(
            state.temp_output_path,
            require_use_timings=state.package_uses_timings,
        )
    except ValueError as exc:
        raise RuntimeError(
            f'PPTX transition package validation failed: {exc}'
        ) from exc
    try:
        validate_pptx_morph_pairs(
            state.temp_output_path,
            state.morph_expectations,
        )
    except ValueError as exc:
        raise RuntimeError(
            f'PPTX Morph package validation failed: {exc}'
        ) from exc
    try:
        validate_pptx_animation_package(
            state.temp_output_path,
            require_supported_effects=True,
            skip_slide_numbers=state.source_motion_slides,
        )
    except ValueError as exc:
        raise RuntimeError(
            f'PPTX animation package validation failed: {exc}'
        ) from exc
    if state.pptx_structure == 'preserve':
        try:
            os.replace(state.temp_output_path, state.output_path)
        except OSError as exc:
            raise TemplateStructureError(
                f"Cannot atomically publish round-trip output {state.output_path}: {exc}. "
                "Choose a different output path on a writable local filesystem."
            ) from exc
    else:
        shutil.move(str(state.temp_output_path), str(state.output_path))
    state.permission_warnings = _relax_output_permissions(state.output_path)

    if state.conversion_trace_path and state.conversion_trace is not None:
        state.conversion_trace_path.parent.mkdir(parents=True, exist_ok=True)
        state.payload = {
            'output': str(state.output_path),
            'slide_count': state.public_slide_count,
            'project_contract': {
                'mode': (
                    'strict-after-dangerous-normalization'
                    if state.dangerous_nonconforming_export
                    else 'strict'
                ),
            },
            'slides': [
                entry
                for entry in state.conversion_trace
                if int(entry.get('slide_num', 0)) <= state.public_slide_count
            ],
        }
        state.conversion_trace_path.write_text(
            json.dumps(state.payload, ensure_ascii=False, indent=2),
            encoding='utf-8',
        )

    if state.verbose:
        print()
        print(f"[Done] Saved: {state.output_path}")
        if state.roundtrip_export:
            from pptx_to_svg.notes_import import import_speaker_notes
            from pptx_to_svg.ooxml_loader import OoxmlPackage

            with OoxmlPackage(state.output_path) as state.package:
                print(f"  Speaker notes: {len(import_speaker_notes(state.package))} page(s)")
        for state.warning in state.permission_warnings:
            print(f"  [warn] {state.warning}")
        if state.conversion_trace_path and state.conversion_trace is not None:
            print(f"  Trace: {state.conversion_trace_path}")
        print(
            f"  Slides: {state.public_slide_count}; "
            "unselected Layout prototype carriers: "
            f"{len(state.definition_svg_files)}"
        )
        if state.use_compat_mode and state.has_any_image:
            print(f"  Mode: Office compatibility mode (supports all Office versions)")
            if PNG_RENDERER == 'svglib' and state.renderer_hint:
                print(f"  [Tip] {state.renderer_hint}")

    return state.success_count == len(state.svg_files)
