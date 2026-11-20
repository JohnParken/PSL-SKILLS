"""Slide round-trip, narration, notes, and validation helpers for export_slides."""
# Adapted from hugohe3/ppt-master 7e0df6705de0cd24345210d31f07fcb5a32b6900.
# MIT; Copyright (c) 2025-2026 Hugo He. See references/LICENSE-PPT-MASTER.txt and references/upstream.md.

from __future__ import annotations

import shutil
from dataclasses import asdict

from pptx_animations import validate_generated_animation_xml
from pptx_transitions import (
    read_slide_transition_xml,
    validate_generated_transition_xml,
)

from .builder import (
    _apply_roundtrip_slide_overlay,
    _apply_roundtrip_transition_overlay,
    _apply_slide_notes,
    _part_has_relationship_references,
    _read_relationships,
    _remove_relationship,
    _resolve_package_target,
    _roundtrip_morph_shape_ids,
    _slide_animation_settings,
    _slide_config,
    _slide_transition_settings,
    _unused_part_path,
)
from .builder_state import BuildState
from .narration import (
    AUDIO_MARKER_PNG_BYTES,
    AUDIO_REL_TYPE,
    IMAGE_REL_TYPE,
    MEDIA_REL_TYPE,
    apply_recorded_timing,
    inject_narration,
    narration_lead_in_seconds,
    next_shape_id,
    probe_audio_duration,
    remove_narration,
)
from .template_structure import TemplateStructureError


def _record_notes_master(state: BuildState) -> None:
    if state.notes_master is None:
        return
    state.notes_slides_created.add(state.slide_num)
    state.notes_master_parts_used.add(state.notes_master.package_part)
    if state.notes_master.created_theme_part is not None:
        state.notes_master_theme_parts_created.add(state.notes_master.created_theme_part)


def try_roundtrip_shortcut(state: BuildState) -> bool:
    """Handle passthrough, motion-only overlay, or notes/identity overlay slides."""
    if state.slide_num in state.passthrough_slides:
        if state.is_layout_definition:
            raise TemplateStructureError(
                "Internal Layout definitions cannot use source-slide passthrough"
            )
        state.morph_shape_ids.update(_roundtrip_morph_shape_ids(
            state.svg_path, state.morph_group_overrides_by_slide.get(state.svg_path.stem, set()),
        ))
        state.morph_preserved_slides.add(state.slide_num)
        if state.verbose:
            print(f"  {state.progress_label} {state.svg_path.name} (Source slide passthrough)")
        state.success_count += 1
        return True

    if (
        state.slide_patch is not None
        and not state.slide_patch.visual_changed
        and state.slide_patch.motion_changed
        and _try_motion_overlay(state)
    ):
        return True

    if (
        state.slide_patch is not None
        and not state.slide_patch.visual_changed
        and not state.slide_patch.motion_changed
        and (state.slide_patch.notes_changed or state.morph_group_overrides_by_slide.get(state.svg_path.stem))
    ):
        state.morph_shape_ids.update(_roundtrip_morph_shape_ids(
            state.svg_path, state.morph_group_overrides_by_slide.get(state.svg_path.stem, set()),
        ))
        state.morph_preserved_slides.add(state.slide_num)
        state.notes_master = None
        if state.slide_patch.notes_changed:
            state.notes_content = state.notes.get(state.svg_path.stem, '') if state.notes else ''
            state.notes_master = _apply_slide_notes(
                state.extract_dir, state.rels_path, state.slide_num,
                state.notes_content, state.primary_language, enable_notes=state.enable_notes,
            )
        _record_notes_master(state)
        if state.verbose:
            print(f"  {state.progress_label} {state.svg_path.name} (Source slide notes / identity overlay)")
        state.success_count += 1
        return True

    return False


def _try_motion_overlay(state: BuildState) -> bool:
    state.overlay_slide_cfg = _slide_config(state.animation_config, state.svg_path.stem)
    state.overlay_groups = state.overlay_slide_cfg.get("groups", {})
    if not isinstance(state.overlay_groups, dict):
        raise ValueError("animations.json slide groups must be an object")
    (
        state.overlay_transition,
        state.overlay_transition_options,
        state.overlay_transition_duration,
        state.overlay_auto_advance,
        state.overlay_transition_sound,
    ) = _slide_transition_settings(
        state.default_transition_cfg, state.overlay_slide_cfg,
        state.transition, state.transition_effect_options,
        state.transition_duration, state.auto_advance,
        state.transition_sound, state.animation_cli_overrides,
    )
    (
        state.overlay_animation,
        state._overlay_animation_duration,
        state._overlay_animation_stagger,
        state._overlay_animation_trigger,
        state._overlay_animation_cfg,
    ) = _slide_animation_settings(
        state.overlay_slide_cfg, state.default_animation_cfg,
        state.animation, state.animation_duration,
        state.animation_stagger, state.animation_trigger,
        state.animation_cli_overrides,
    )
    state.animation_override_requested = any(
        state.animation_cli_overrides.get(key, False)
        for key in ("animation", "animation_duration", "animation_trigger", "animation_stagger")
    )
    state.direct_transition_overlay = (
        state.overlay_animation is None
        and not state.slide_patch.animation_changed
        and not state.overlay_groups
        and not state.animation_override_requested
        and state.overlay_transition_sound is None
        and not state.morph_group_overrides_by_slide.get(state.svg_path.stem)
        and not (state.narration_audio and state.narration_audio.get(state.svg_path.stem) is not None)
        and not state.use_narration_timings
    )
    if not state.direct_transition_overlay:
        return False
    if _apply_roundtrip_transition_overlay(
        state.slide_xml_path,
        effect=state.overlay_transition,
        effect_options=state.overlay_transition_options,
        duration=state.overlay_transition_duration,
        auto_advance=state.overlay_auto_advance,
        replace_transition=state.slide_patch.transition_replaced,
        advance_changed=state.slide_patch.advance_changed,
    ):
        state.package_uses_timings = True
    if state.slide_patch.notes_changed:
        state.notes_content = state.notes.get(state.svg_path.stem, '') if state.notes else ''
        state.notes_master = _apply_slide_notes(
            state.extract_dir, state.rels_path, state.slide_num,
            state.notes_content, state.primary_language, enable_notes=state.enable_notes,
        )
        _record_notes_master(state)
    if state.verbose:
        print(f"  {state.progress_label} {state.svg_path.name} (Source slide motion overlay)")
    state.success_count += 1
    return True


def apply_post_overlay_and_notes(state: BuildState) -> None:
    """Apply round-trip visual overlays, preserved transition timings, and notes."""
    if state.slide_patch is not None:
        if state.source_slide_bytes is None or state.source_rels_bytes is None:
            raise TemplateStructureError(f"Round-trip slide {state.slide_num} source snapshot is missing")
        state.trace_sink = state.conversion_trace if state.conversion_trace is not None else state.structure_trace
        state.slide_conversion_trace = next(
            (entry for entry in reversed(state.trace_sink or []) if entry.get("slide_num") == state.slide_num),
            None,
        )
        state.overlay_shape_id_map = _apply_roundtrip_slide_overlay(
            state.source_slide_bytes, state.source_rels_bytes, state.slide_xml_path,
            state.rels_path, state.slide_patch, state.slide_conversion_trace,
        )
        for state.target in state.expected_animation_targets:
            for state.field in ("shape_id", "trigger_shape_id"):
                state.shape_id = state.target.get(state.field)
                if (
                    isinstance(state.shape_id, int)
                    and not isinstance(state.shape_id, bool)
                    and state.shape_id in state.overlay_shape_id_map
                ):
                    state.target[state.field] = state.overlay_shape_id_map[state.shape_id]
        for state.morph_key, state.shape_id in tuple(state.morph_shape_ids.items()):
            if state.morph_key[0] == state.svg_path.stem and state.shape_id in state.overlay_shape_id_map:
                state.morph_shape_ids[state.morph_key] = state.overlay_shape_id_map[state.shape_id]

    state.resolved_advance_after = state.slide_auto_advance
    state.resolved_advance_on_click = True
    if state.slide_patch is not None and not state.slide_patch.advance_changed:
        state.source_motion = read_slide_transition_xml(state.source_slide_bytes)
        state.resolved_advance_on_click = state.source_motion.advance_on_click
        state.resolved_advance_after = (
            state.source_motion.advance_after_ms / 1000
            if state.source_motion.advance_after_ms is not None else None
        )

    state.notes_changed = state.slide_patch is None or state.slide_patch.notes_changed
    if not state.is_layout_definition and state.notes_changed:
        state.notes_content = state.notes.get(state.svg_path.stem, '') if state.notes else ''
        state.notes_master = _apply_slide_notes(
            state.extract_dir, state.rels_path, state.slide_num,
            state.notes_content, state.primary_language, enable_notes=state.enable_notes,
        )
        _record_notes_master(state)


def attach_narration_audio(state: BuildState) -> None:
    """Attach optional narration media and update recorded advance timings."""
    state.svg_stem = state.svg_path.stem
    state.audio_path = (
        state.narration_audio.get(state.svg_stem)
        if state.narration_audio and not state.is_layout_definition
        else None
    )
    if not state.audio_path:
        return
    state.slide_xml_path = state.extract_dir / 'ppt' / 'slides' / f'slide{state.slide_num}.xml'
    state.rels_path = state.extract_dir / 'ppt' / 'slides' / '_rels' / f'slide{state.slide_num}.xml.rels'
    state.ext = state.audio_path.suffix.lower()
    state.slide_xml, state.removed_rids = remove_narration(state.slide_xml_path.read_text(encoding='utf-8'))
    state.source_relationships = _read_relationships(state.rels_path)
    for state.rel_id in state.removed_rids:
        state.relationship = state.source_relationships.get(state.rel_id, {})
        if state.relationship.get('Target') and state.relationship.get('TargetMode') != 'External':
            state.replaced_narration_parts.add(_resolve_package_target(
                f'ppt/slides/slide{state.slide_num}.xml', state.relationship['Target'],
            ))
        _remove_relationship(state.rels_path, state.rel_id)
    state.requested_media = state.media_dir / f'narration{state.slide_num}{state.ext}'
    if (
        state.requested_media.exists()
        and state.requested_media.relative_to(state.extract_dir).as_posix() in state.replaced_narration_parts
        and not _part_has_relationship_references(
            state.extract_dir, state.requested_media.relative_to(state.extract_dir).as_posix(),
        )
    ):
        state.requested_media.unlink()
    state.media_name = _unused_part_path(state.media_dir, f'narration{state.slide_num}{state.ext}').name
    shutil.copy2(state.audio_path, state.media_dir / state.media_name)
    state.audio_exts_used.add(state.ext)

    state.poster_name = 'narration_poster.png'
    state.poster_path = state.media_dir / state.poster_name
    if state.poster_path.exists() and state.poster_path.read_bytes() != AUDIO_MARKER_PNG_BYTES:
        state.poster_path = _unused_part_path(state.media_dir, state.poster_name)
        state.poster_name = state.poster_path.name
    if not state.poster_path.exists():
        state.poster_path.write_bytes(AUDIO_MARKER_PNG_BYTES)
    state.has_any_image = True
    state.image_exts_used.add('png')

    state.media_rid = _append_relationship(state.rels_path, MEDIA_REL_TYPE, f'../media/{state.media_name}')
    state.audio_rid = _append_relationship(state.rels_path, AUDIO_REL_TYPE, f'../media/{state.media_name}')
    state.poster_rid = _append_relationship(state.rels_path, IMAGE_REL_TYPE, f'../media/{state.poster_name}')

    state.narration_shape_id = next_shape_id(state.slide_xml)
    state.narration_transition_duration = (
        state.slide_transition_duration if state.slide_transition is not None else 0.0
    )
    state.narration_lead_in = narration_lead_in_seconds(
        state.narration_transition_duration, start_floor=state.narration_start_floor,
    )
    state.slide_xml = inject_narration(
        state.slide_xml, shape_id=state.narration_shape_id, shape_name=state.media_name,
        audio_rid=state.audio_rid, media_rid=state.media_rid,
        poster_rid=state.poster_rid, start_delay=state.narration_lead_in,
    )
    if state.use_narration_timings:
        state.duration = probe_audio_duration(state.audio_path)
        if state.duration is None:
            raise RuntimeError(f"Unable to read narration duration with ffprobe: {state.audio_path}")
        state.narration_advance_after = state.narration_lead_in + state.duration + state.narration_padding
        state.slide_xml = apply_recorded_timing(
            state.slide_xml, advance_after=state.narration_advance_after,
            transition_duration=state.slide_transition_duration, transition_effect=state.slide_transition,
        )
        state.resolved_advance_after = state.narration_advance_after
        state.resolved_advance_on_click = False
        state.package_uses_timings = True
    state.slide_xml_path.write_text(state.slide_xml, encoding='utf-8')
    state.narration_slides_created.add(state.slide_num)


def validate_slide_motion_and_trace(state: BuildState) -> None:
    """Validate generated slide transition/timing XML and update conversion trace."""
    state.final_slide_xml = state.slide_xml_path.read_text(encoding='utf-8')
    state.preserved_source_transition = (
        state.slide_patch is not None and not state.slide_patch.transition_replaced
    )
    state.preserved_source_animation = (
        state.slide_patch is not None and not state.slide_patch.animation_changed
    )
    state.resolved_motion = None
    state.resolved_animation = None
    if not state.preserved_source_transition:
        try:
            state.resolved_motion = validate_generated_transition_xml(
                state.final_slide_xml, effect=state.slide_transition,
                effect_options=state.slide_transition_effect_options,
                duration=state.slide_transition_duration,
                advance_on_click=state.resolved_advance_on_click,
                advance_after=state.resolved_advance_after, sound=state.expected_transition_sound,
            )
        except ValueError as exc:
            raise RuntimeError(f'Slide {state.slide_num} transition validation failed: {exc}') from exc
    if not state.preserved_source_animation:
        try:
            state.resolved_animation = validate_generated_animation_xml(
                state.final_slide_xml, state.expected_animation_targets,
                duration=state.expected_animation_duration, trigger=state.expected_animation_trigger,
            )
        except ValueError as exc:
            raise RuntimeError(f'Slide {state.slide_num} animation validation failed: {exc}') from exc

    if state.conversion_trace is not None:
        state.motion_summary = (
            {"source_preserved": True} if state.resolved_motion is None else asdict(state.resolved_motion)
        )
        for state.trace_entry in reversed(state.conversion_trace):
            if state.trace_entry.get('slide_num') == state.slide_num:
                state.trace_entry['motion'] = state.motion_summary
                state.trace_entry['animation'] = (
                    {"source_preserved": True} if state.resolved_animation is None else asdict(state.resolved_animation)
                )
                break

    if state.verbose:
        if state.use_native_shapes:
            state.mode_str = " (Native)"
        elif state.use_compat_mode and not state.use_native_shapes:
            state.mode_str = " (PNG+SVG)" if state.has_any_image else " (SVG)"
        else:
            state.mode_str = " (SVG)"
        state.has_notes = state.slide_num in state.notes_slides_created
        state.notes_str = " +notes" if state.has_notes else ""
        state.narration_str = " +narration" if state.slide_num in state.narration_slides_created else ""
        print(f"  {state.progress_label} {state.svg_path.name}{state.mode_str}{state.notes_str}{state.narration_str}")
