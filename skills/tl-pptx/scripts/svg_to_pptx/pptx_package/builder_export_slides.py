"""Native PPTX export phase; retains upstream control flow."""
# Adapted from hugohe3/ppt-master 7e0df6705de0cd24345210d31f07fcb5a32b6900.
# MIT; Copyright (c) 2025-2026 Hugo He. See references/LICENSE-PPT-MASTER.txt and references/upstream.md.

from __future__ import annotations
import hashlib
import json
import shutil
from dataclasses import (
    asdict,
)
from pathlib import (
    Path,
)
from typing import Any
from xml.etree import ElementTree as ET
from xml.sax.saxutils import (
    quoteattr,
)
from pptx_transitions import (
    create_transition_xml,
    read_slide_transition_xml,
    validate_generated_transition_xml,
)
from pptx_animations import (
    create_sequence_timing_xml,
    normalize_animation_trigger,
    validate_generated_animation_xml,
)
from hyperlink_contract import (
    trigger_shape_hyperlink_errors,
)
from ..animation_config import (
    animation_group_effect_entries,
)
from ..drawingml.converter import convert_svg_to_slide_shapes
from ..drawingml.theme_colors import (
    rewrite_chart_accent_colors,
)
from .media import (
    convert_svg_to_png,
)
from .narration import (
    AUDIO_CONTENT_TYPES,
    AUDIO_REL_TYPE,
    AUDIO_MARKER_PNG_BYTES,
    IMAGE_REL_TYPE,
    MEDIA_REL_TYPE,
    apply_recorded_timing,
    inject_narration,
    narration_lead_in_seconds,
    next_shape_id,
    probe_audio_duration,
    remove_narration,
)
from .slide_xml import (
    create_slide_xml_with_svg, create_slide_rels_xml,
)
from .template_structure import (
    TemplateStructureError,
)

from .builder import (
    SLIDE_LAYOUT_REL_TYPE,
    _append_relationship,
    _apply_roundtrip_slide_overlay,
    _apply_roundtrip_transition_overlay,
    _apply_slide_notes,
    _build_sequence_targets,
    _content_type_for_extension,
    _materialize_animation_sounds,
    _materialize_transition_sound,
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

def export_slides(state: BuildState) -> None:
    """Execute upstream slides stage with explicit shared state."""
    for state.i, state.svg_path in enumerate(state.svg_files, 1):
        state.slide_num = state.i
        state.is_layout_definition = state.slide_num > state.public_slide_count
        state.progress_label = (
            f"[Layout carrier {state.slide_num - state.public_slide_count}/"
            f"{len(state.definition_svg_files)}]"
            if state.is_layout_definition
            else f"[Slide {state.slide_num}/{state.public_slide_count}]"
        )
        state.expected_animation_targets: list[dict[str, Any]] = []
        state.expected_animation_duration = state.animation_duration
        state.expected_animation_trigger = normalize_animation_trigger(state.animation_trigger)
        state.expected_transition_sound: dict[str, str] | None = None

        try:
            state.slide_xml_path = (
                state.extract_dir / 'ppt' / 'slides' / f'slide{state.slide_num}.xml'
            )
            state.rels_path = (
                state.extract_dir / 'ppt' / 'slides' / '_rels'
                / f'slide{state.slide_num}.xml.rels'
            )
            state.slide_patch = state.slide_patches.get(state.slide_num)
            state.source_slide_bytes: bytes | None = None
            state.source_rels_bytes: bytes | None = None
            if state.slide_patch is not None:
                if state.is_layout_definition:
                    raise TemplateStructureError(
                        "Internal Layout definitions cannot use round-trip overlays"
                    )
                try:
                    state.source_slide_bytes = state.slide_xml_path.read_bytes()
                    state.source_rels_bytes = state.rels_path.read_bytes()
                except OSError as exc:
                    raise TemplateStructureError(
                        f"Cannot read source slide {state.slide_num} for overlay: {exc}"
                    ) from exc
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
                    print(
                        f"  {state.progress_label} {state.svg_path.name} "
                        "(Source slide passthrough)"
                    )
                state.success_count += 1
                continue
            if (
                state.slide_patch is not None
                and not state.slide_patch.visual_changed
                and state.slide_patch.motion_changed
            ):
                state.overlay_slide_cfg = _slide_config(
                    state.animation_config,
                    state.svg_path.stem,
                )
                state.overlay_groups = state.overlay_slide_cfg.get("groups", {})
                if not isinstance(state.overlay_groups, dict):
                    raise ValueError(
                        "animations.json slide groups must be an object"
                    )
                (
                    state.overlay_transition,
                    state.overlay_transition_options,
                    state.overlay_transition_duration,
                    state.overlay_auto_advance,
                    state.overlay_transition_sound,
                ) = _slide_transition_settings(
                    state.default_transition_cfg,
                    state.overlay_slide_cfg,
                    state.transition,
                    state.transition_effect_options,
                    state.transition_duration,
                    state.auto_advance,
                    state.transition_sound,
                    state.animation_cli_overrides,
                )
                (
                    state.overlay_animation,
                    state._overlay_animation_duration,
                    state._overlay_animation_stagger,
                    state._overlay_animation_trigger,
                    state._overlay_animation_cfg,
                ) = _slide_animation_settings(
                    state.overlay_slide_cfg,
                    state.default_animation_cfg,
                    state.animation,
                    state.animation_duration,
                    state.animation_stagger,
                    state.animation_trigger,
                    state.animation_cli_overrides,
                )
                state.animation_override_requested = any(
                    state.animation_cli_overrides.get(key, False)
                    for key in (
                        "animation",
                        "animation_duration",
                        "animation_stagger",
                        "animation_trigger",
                    )
                )
                state.direct_transition_overlay = (
                    state.overlay_animation is None
                    and not state.slide_patch.animation_changed
                    and not state.overlay_groups
                    and not state.animation_override_requested
                    and state.overlay_transition_sound is None
                    and not state.morph_group_overrides_by_slide.get(
                        state.svg_path.stem
                    )
                    and not (
                        state.narration_audio
                        and state.narration_audio.get(state.svg_path.stem) is not None
                    )
                    and not state.use_narration_timings
                )
                if state.direct_transition_overlay:
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
                        state.notes_content = (
                            state.notes.get(state.svg_path.stem, '') if state.notes else ''
                        )
                        state.notes_master = _apply_slide_notes(
                            state.extract_dir,
                            state.rels_path,
                            state.slide_num,
                            state.notes_content,
                            state.primary_language,
                            enable_notes=state.enable_notes,
                        )
                        if state.notes_master is not None:
                            state.notes_slides_created.add(state.slide_num)
                            state.notes_master_parts_used.add(
                                state.notes_master.package_part
                            )
                            if state.notes_master.created_theme_part is not None:
                                state.notes_master_theme_parts_created.add(
                                    state.notes_master.created_theme_part
                                )
                    if state.verbose:
                        print(
                            f"  {state.progress_label} {state.svg_path.name} "
                            "(Source slide motion overlay)"
                        )
                    state.success_count += 1
                    continue
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
                        state.extract_dir,
                        state.rels_path,
                        state.slide_num,
                        state.notes_content,
                        state.primary_language,
                        enable_notes=state.enable_notes,
                    )
                if state.notes_master is not None:
                    state.notes_slides_created.add(state.slide_num)
                    state.notes_master_parts_used.add(state.notes_master.package_part)
                    if state.notes_master.created_theme_part is not None:
                        state.notes_master_theme_parts_created.add(
                            state.notes_master.created_theme_part
                        )
                if state.verbose:
                    print(
                        f"  {state.progress_label} {state.svg_path.name} "
                        "(Source slide notes / identity overlay)"
                    )
                state.success_count += 1
                continue

            # ---- Native shapes mode ----
            if state.use_native_shapes:
                state.slide_cfg = (
                    {}
                    if state.is_layout_definition
                    else _slide_config(state.animation_config, state.svg_path.stem)
                )
                if state.is_layout_definition:
                    state.slide_transition = None
                    state.slide_transition_effect_options = {}
                    state.slide_transition_duration = state.transition_duration
                    state.slide_auto_advance = None
                    state.slide_transition_sound_path = None
                    state.slide_animation = None
                    state.slide_animation_duration = state.animation_duration
                    state.slide_animation_stagger = state.animation_stagger
                    state.slide_animation_trigger = state.animation_trigger
                    state.slide_animation_cfg = {}
                else:
                    (
                        state.slide_transition,
                        state.slide_transition_effect_options,
                        state.slide_transition_duration,
                        state.slide_auto_advance,
                        state.slide_transition_sound_path,
                    ) = _slide_transition_settings(
                        state.default_transition_cfg,
                        state.slide_cfg,
                        state.transition,
                        state.transition_effect_options,
                        state.transition_duration,
                        state.auto_advance,
                        state.transition_sound,
                        state.animation_cli_overrides,
                    )
                    (
                        state.slide_animation,
                        state.slide_animation_duration,
                        state.slide_animation_stagger,
                        state.slide_animation_trigger,
                        state.slide_animation_cfg,
                    ) = _slide_animation_settings(
                        state.slide_cfg,
                        state.default_animation_cfg,
                        state.animation,
                        state.animation_duration,
                        state.animation_stagger,
                        state.animation_trigger,
                        state.animation_cli_overrides,
                    )
                    if state.morph_pairs_by_destination.get(state.svg_path.stem):
                        if (
                            state.slide_transition != "morph"
                            or state.slide_transition_effect_options.get(
                                "morph_by",
                                "object",
                            )
                            != "object"
                        ):
                            raise ValueError(
                                f'animations.json slide "{state.svg_path.stem}" '
                                'declares deterministic Morph pairs, but '
                                'the resolved transition is not Morph by object'
                            )
                state.groups_value = state.slide_cfg.get('groups', {})
                if not isinstance(state.groups_value, dict):
                    raise ValueError(
                        'animations.json slide groups must be an object'
                    )
                state.animation_hard_disabled = (
                    state.animation_cli_overrides.get('animation', False)
                    and state.animation is None
                )
                state.explicit_group_ids: set[str] = set()
                state.trigger_group_ids: set[str] = set()
                if not state.animation_hard_disabled:
                    for state.group_id, state.group_cfg in state.groups_value.items():
                        if not isinstance(state.group_cfg, dict):
                            continue
                        state.group_path = (
                            f'slides['
                            f'{json.dumps(state.svg_path.stem, ensure_ascii=False)}'
                            f'].groups['
                            f'{json.dumps(str(state.group_id), ensure_ascii=False)}'
                            f']'
                        )
                        state.effect_entries = animation_group_effect_entries(
                            state.group_cfg,
                            path=state.group_path,
                        )
                        if any(
                            effect_cfg.get('effect') != 'none'
                            and (
                                state.slide_animation is not None
                                or 'effect' in effect_cfg
                            )
                            for _effect_path, effect_cfg in state.effect_entries
                        ):
                            state.explicit_group_ids.add(str(state.group_id))
                        for state._effect_path, state.effect_cfg in state.effect_entries:
                            state.trigger_shape = state.effect_cfg.get('trigger_shape')
                            if (
                                isinstance(state.trigger_shape, str)
                                and state.trigger_shape.strip()
                            ):
                                state.trigger_group_ids.add(state.trigger_shape)
                state.explicit_animation_groups = frozenset(
                    state.explicit_group_ids | state.trigger_group_ids
                )
                if state.trigger_group_ids:
                    state.hyperlink_trigger_errors = trigger_shape_hyperlink_errors(
                        ET.parse(state.svg_path).getroot(),
                        state.trigger_group_ids,
                    )
                    if state.hyperlink_trigger_errors:
                        raise ValueError('; '.join(state.hyperlink_trigger_errors))
                state.converter_group_overrides = (
                    state.explicit_animation_groups
                    | frozenset(
                        state.morph_group_overrides_by_slide.get(
                            state.svg_path.stem,
                            set(),
                        )
                    )
                )
                (
                    state.slide_xml,
                    state.media_files_dict,
                    state.rel_entries,
                    state.anim_targets,
                    state.package_files_dict,
                    state.content_type_overrides,
                ) = (
                    convert_svg_to_slide_shapes(
                        state.svg_path, slide_num=state.slide_num,
                        slide_count=state.public_slide_count,
                        verbose=state.verbose,
                        text_flow=state.text_flow,
                        image_optimize=state.image_optimize,
                        image_max_dimension=state.image_max_dimension,
                        image_sizing=state.image_sizing,
                        image_scale=state.image_scale,
                        image_quality=state.image_quality,
                        native_objects=state.native_objects,
                        package_parts=state.package_parts,
                        animation_group_overrides=state.converter_group_overrides,
                        theme_font_spec=state.active_theme_font_spec,
                        theme_color_spec=state.active_theme_color_spec,
                        primary_language=state.primary_language,
                        # A round-trip overlay keeps the source p:bg, so a
                        # new full-canvas rect must stay a shape to survive.
                        promote_background=(
                            state.pptx_structure != "structured"
                            and state.slide_patch is None
                        ),
                        dangerous_nonconforming_export=(
                            state.dangerous_nonconforming_export
                        ),
                        resource_root=state.resource_root,
                        trace_out=state.conversion_trace
                        if state.conversion_trace is not None
                        else state.structure_trace,
                    )
                )
                state.morph_group_ids = state.morph_group_overrides_by_slide.get(
                    state.svg_path.stem,
                    set(),
                )
                if state.morph_group_ids:
                    state.target_ids_by_group: dict[str, list[int]] = {}
                    for state.shape_id, state.group_id in state.anim_targets:
                        state.target_ids_by_group.setdefault(
                            str(state.group_id),
                            [],
                        ).append(int(state.shape_id))
                    for state.group_id in sorted(state.morph_group_ids):
                        state.resolved_shape_ids = state.target_ids_by_group.get(
                            state.group_id,
                            [],
                        )
                        if len(state.resolved_shape_ids) != 1:
                            raise ValueError(
                                f'Morph target "{state.svg_path.stem}/{state.group_id}" '
                                'must resolve to exactly one Slide-local '
                                'PowerPoint shape'
                            )
                        state.morph_shape_ids[
                            (state.svg_path.stem, state.group_id)
                        ] = state.resolved_shape_ids[0]
                # Order matters: OOXML schema requires <p:transition>
                # to precede <p:timing> inside <p:sld>. Both use the same
                # </p:sld> string-replace anchor, so transition must be
                # injected first and timing second.
                state.packaged_sounds_by_source: dict[Path, tuple[str, str]] = {}
                state.expected_transition_sound = _materialize_transition_sound(
                    (
                        state.animation_resource_root
                        if state.animation_resource_root is not None
                        else state.svg_files[0].parent.parent
                    ),
                    state.slide_transition_sound_path,
                    state.media_files_dict,
                    state.rel_entries,
                    state.audio_exts_used,
                    state.packaged_sounds_by_source,
                )
                if (
                    state.slide_transition is not None
                    or state.slide_auto_advance is not None
                    or state.expected_transition_sound is not None
                ):
                    state.transition_fragment = create_transition_xml(
                        effect=state.slide_transition,
                        duration=state.slide_transition_duration,
                        advance_after=state.slide_auto_advance,
                        effect_options=state.slide_transition_effect_options,
                        sound=state.expected_transition_sound,
                    )
                    if state.transition_fragment:
                        state.slide_xml = state.slide_xml.replace(
                            '</p:sld>',
                            '\n' + state.transition_fragment + '\n</p:sld>',
                        )
                    if state.slide_auto_advance is not None:
                        state.package_uses_timings = True

                state.expected_animation_duration = state.slide_animation_duration
                state.expected_animation_trigger = state.slide_animation_trigger
                if (
                    not state.animation_hard_disabled
                    and (state.slide_animation or state.explicit_animation_groups)
                    and state.anim_targets
                ):
                    state.seq_targets, state.mixed_count = _build_sequence_targets(
                        state.anim_targets,
                        state.svg_path.stem,
                        state.slide_cfg,
                        state.slide_animation,
                        state.slide_animation_cfg,
                        state.slide_animation_duration,
                        state.slide_animation_stagger,
                        state.mixed_animation_offset,
                        state.animation_rng,
                    )
                    state.seq_targets = _materialize_animation_sounds(
                        (
                            state.animation_resource_root
                            if state.animation_resource_root is not None
                            else state.svg_files[0].parent.parent
                        ),
                        state.seq_targets,
                        state.media_files_dict,
                        state.rel_entries,
                        state.audio_exts_used,
                        state.packaged_sounds_by_source,
                    )
                    state.expected_animation_targets = state.seq_targets
                    if state.mixed_count:
                        state.mixed_animation_offset += state.mixed_count
                    state.timing_xml = '\n' + create_sequence_timing_xml(
                        state.seq_targets, duration=state.slide_animation_duration,
                        trigger=state.slide_animation_trigger,
                    )
                    state.slide_xml = state.slide_xml.replace(
                        '</p:sld>',
                        state.timing_xml + '\n</p:sld>',
                    )

                # Write slide XML
                state.slide_xml_path = state.extract_dir / 'ppt' / 'slides' / f'slide{state.slide_num}.xml'
                with open(state.slide_xml_path, 'w', encoding='utf-8') as state.f:
                    state.f.write(state.slide_xml)

                # Write media files
                state.media_name_map: dict[str, str] = {}
                for state.media_name, state.media_data in state.media_files_dict.items():
                    state.ext = state.media_name.rsplit('.', 1)[-1].lower()
                    state.media_hash = hashlib.sha256(state.media_data).hexdigest()
                    state.cache_key = (state.ext, state.media_hash)
                    state.cached_name = state.media_cache.get(state.cache_key)

                    if state.cached_name is None:
                        state.prefix = (
                            'audio'
                            if f'.{state.ext}' in AUDIO_CONTENT_TYPES
                            else 'image'
                        )
                        state.cached_name = f'{state.prefix}_{state.media_hash[:16]}.{state.ext}'
                        state.media_cache[state.cache_key] = state.cached_name
                        with open(state.media_dir / state.cached_name, 'wb') as state.f:
                            state.f.write(state.media_data)

                    state.media_name_map[state.media_name] = state.cached_name

                for state.rel in state.rel_entries:
                    state.target = state.rel.get('target', '')
                    if not state.target.startswith('../media/'):
                        continue
                    state.media_name = state.target.split('../media/', 1)[1]
                    state.mapped_name = state.media_name_map.get(state.media_name)
                    if state.mapped_name:
                        state.rel['target'] = f'../media/{state.mapped_name}'

                # Write non-media OOXML package parts produced by native
                # object converters, e.g. chart XML, chart rels, and
                # embedded workbooks.
                for state.part_name, state.part_data in state.package_files_dict.items():
                    if (
                        state.part_name.startswith('ppt/charts/')
                        and state.part_name.endswith('.xml')
                    ):
                        state.part_data = rewrite_chart_accent_colors(
                            state.part_data,
                            state.active_theme_color_spec,
                        )
                    state.package_path = state.extract_dir / state.part_name
                    state.package_path.parent.mkdir(parents=True, exist_ok=True)
                    with open(state.package_path, 'wb') as state.f:
                        state.f.write(state.part_data)
                    state.suffix = state.package_path.suffix.lstrip('.').lower()
                    if state.suffix:
                        state.package_exts_used.add(state.suffix)
                state.package_content_overrides.update(state.content_type_overrides)

                # Build relationships XML
                state.rels_dir = state.extract_dir / 'ppt' / 'slides' / '_rels'
                state.rels_dir.mkdir(exist_ok=True)
                state.rels_path = state.rels_dir / f'slide{state.slide_num}.xml.rels'

                state.extra_rels = ''
                for state.rel in state.rel_entries:
                    state.target_mode = state.rel.get('target_mode')
                    state.mode_attr = (
                        f" TargetMode={quoteattr(state.target_mode)}"
                        if state.target_mode is not None
                        else ''
                    )
                    state.extra_rels += (
                        f"\n  <Relationship Id={quoteattr(state.rel['id'])} "
                        f"Type={quoteattr(state.rel['type'])} "
                        f"Target={quoteattr(state.rel['target'])}{state.mode_attr}/>"
                    )

                state.rels_xml = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1"
                Type="{SLIDE_LAYOUT_REL_TYPE}"
                Target="{state.structure.slide_layout_target(state.slide_num)}"/>{state.extra_rels}
</Relationships>'''
                with open(state.rels_path, 'w', encoding='utf-8') as state.f:
                    state.f.write(state.rels_xml)

                # Track image formats for Content_Types
                for state.media_name in state.media_name_map.values():
                    state.ext = state.media_name.rsplit('.', 1)[-1].lower()
                    state.dotted_ext = f'.{state.ext}'
                    if state.dotted_ext in AUDIO_CONTENT_TYPES:
                        state.audio_exts_used.add(state.dotted_ext)
                    else:
                        _content_type_for_extension(state.ext)
                        state.image_exts_used.add(state.ext)
                        state.has_any_image = True

            # ---- Legacy SVG embedding mode ----
            else:
                state.slide_cfg = _slide_config(state.animation_config, state.svg_path.stem)
                (
                    state.slide_transition,
                    state.slide_transition_effect_options,
                    state.slide_transition_duration,
                    state.slide_auto_advance,
                    state.slide_transition_sound_path,
                ) = (
                    _slide_transition_settings(
                        state.default_transition_cfg,
                        state.slide_cfg,
                        state.transition,
                        state.transition_effect_options,
                        state.transition_duration,
                        state.auto_advance,
                        state.transition_sound,
                        state.animation_cli_overrides,
                    )
                )
                state.svg_filename = f'image{state.i}.svg'
                state.png_filename = f'image{state.i}.png'
                state.png_rid = 'rId2'
                state.svg_rid = 'rId3' if state.use_compat_mode else 'rId2'

                shutil.copy(state.svg_path, state.media_dir / state.svg_filename)

                state.slide_has_png = False
                if state.use_compat_mode:
                    if state.prerender_results is not None:
                        state.png_success = state.prerender_results.get(state.i, False)
                    else:
                        state.png_path = state.media_dir / state.png_filename
                        state.png_success = convert_svg_to_png(
                            state.svg_path, state.png_path,
                            width=state.pixel_width, height=state.pixel_height,
                        )
                    if state.png_success:
                        state.slide_has_png = True
                        state.has_any_image = True
                        state.image_exts_used.add('png')
                    else:
                        if state.verbose:
                            print(
                                f"  [{state.i}/{len(state.svg_files)}] {state.svg_path.name} - "
                                "PNG generation failed, using pure SVG"
                            )
                        state.svg_rid = 'rId2'

                state.slide_xml_path = state.extract_dir / 'ppt' / 'slides' / f'slide{state.slide_num}.xml'
                state.slide_xml = create_slide_xml_with_svg(
                    state.slide_num,
                    png_rid=state.png_rid, svg_rid=state.svg_rid,
                    width_emu=state.width_emu, height_emu=state.height_emu,
                    transition=state.slide_transition,
                    transition_effect_options=state.slide_transition_effect_options,
                    transition_duration=state.slide_transition_duration,
                    auto_advance=state.slide_auto_advance,
                    use_compat_mode=(state.use_compat_mode and state.slide_has_png),
                )
                with open(state.slide_xml_path, 'w', encoding='utf-8') as state.f:
                    state.f.write(state.slide_xml)

                state.rels_dir = state.extract_dir / 'ppt' / 'slides' / '_rels'
                state.rels_dir.mkdir(exist_ok=True)
                state.rels_path = state.rels_dir / f'slide{state.slide_num}.xml.rels'
                state.rels_xml = create_slide_rels_xml(
                    png_rid=state.png_rid, png_filename=state.png_filename,
                    svg_rid=state.svg_rid, svg_filename=state.svg_filename,
                    use_compat_mode=(state.use_compat_mode and state.slide_has_png),
                    slide_layout_target=state.structure.slide_layout_target(state.slide_num),
                )
                with open(state.rels_path, 'w', encoding='utf-8') as state.f:
                    state.f.write(state.rels_xml)

            if state.slide_patch is not None:
                if state.source_slide_bytes is None or state.source_rels_bytes is None:
                    raise TemplateStructureError(
                        f"Round-trip slide {state.slide_num} source snapshot is missing"
                    )
                state.trace_sink = (
                    state.conversion_trace
                    if state.conversion_trace is not None
                    else state.structure_trace
                )
                state.slide_conversion_trace = next(
                    (
                        entry
                        for entry in reversed(state.trace_sink or [])
                        if entry.get("slide_num") == state.slide_num
                    ),
                    None,
                )
                state.overlay_shape_id_map = _apply_roundtrip_slide_overlay(
                    state.source_slide_bytes,
                    state.source_rels_bytes,
                    state.slide_xml_path,
                    state.rels_path,
                    state.slide_patch,
                    state.slide_conversion_trace,
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
                for state.morph_key, state.shape_id in tuple(
                    state.morph_shape_ids.items()
                ):
                    if (
                        state.morph_key[0] == state.svg_path.stem
                        and state.shape_id in state.overlay_shape_id_map
                    ):
                        state.morph_shape_ids[state.morph_key] = (
                            state.overlay_shape_id_map[state.shape_id]
                        )

            state.resolved_advance_after = state.slide_auto_advance
            state.resolved_advance_on_click = True
            if state.slide_patch is not None and not state.slide_patch.advance_changed:
                state.source_motion = read_slide_transition_xml(state.source_slide_bytes)
                state.resolved_advance_on_click = state.source_motion.advance_on_click
                state.resolved_advance_after = (
                    state.source_motion.advance_after_ms / 1000
                    if state.source_motion.advance_after_ms is not None else None
                )

            # --- Process notes (shared between native and legacy mode) ---
            state.notes_changed = state.slide_patch is None or state.slide_patch.notes_changed
            if not state.is_layout_definition and state.notes_changed:
                state.notes_content = (
                    state.notes.get(state.svg_path.stem, '') if state.notes else ''
                )
                state.notes_master = _apply_slide_notes(
                    state.extract_dir,
                    state.rels_path,
                    state.slide_num,
                    state.notes_content,
                    state.primary_language,
                    enable_notes=state.enable_notes,
                )
                if state.notes_master is not None:
                    state.notes_slides_created.add(state.slide_num)
                    state.notes_master_parts_used.add(state.notes_master.package_part)
                    if state.notes_master.created_theme_part is not None:
                        state.notes_master_theme_parts_created.add(
                            state.notes_master.created_theme_part
                        )

            # --- Process narration audio (shared between native and legacy mode) ---
            state.svg_stem = state.svg_path.stem
            state.audio_path = (
                state.narration_audio.get(state.svg_stem)
                if state.narration_audio and not state.is_layout_definition
                else None
            )
            if state.audio_path:
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
                    and state.requested_media.relative_to(state.extract_dir).as_posix()
                    in state.replaced_narration_parts
                    and not _part_has_relationship_references(
                        state.extract_dir,
                        state.requested_media.relative_to(state.extract_dir).as_posix(),
                    )
                ):
                    # The earlier PPT Master narration for this slide was
                    # just unlinked; reuse its name instead of growing a
                    # `_1`, `_1_1`, ... suffix with every re-recording.
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

                state.media_rid = _append_relationship(
                    state.rels_path,
                    MEDIA_REL_TYPE,
                    f'../media/{state.media_name}',
                )
                state.audio_rid = _append_relationship(
                    state.rels_path,
                    AUDIO_REL_TYPE,
                    f'../media/{state.media_name}',
                )
                state.poster_rid = _append_relationship(
                    state.rels_path,
                    IMAGE_REL_TYPE,
                    f'../media/{state.poster_name}',
                )

                state.narration_shape_id = next_shape_id(state.slide_xml)
                state.narration_transition_duration = (
                    state.slide_transition_duration
                    if state.slide_transition is not None
                    else 0.0
                )
                state.narration_lead_in = narration_lead_in_seconds(
                    state.narration_transition_duration,
                    start_floor=state.narration_start_floor,
                )
                state.slide_xml = inject_narration(
                    state.slide_xml,
                    shape_id=state.narration_shape_id,
                    shape_name=state.media_name,
                    audio_rid=state.audio_rid,
                    media_rid=state.media_rid,
                    poster_rid=state.poster_rid,
                    start_delay=state.narration_lead_in,
                )

                if state.use_narration_timings:
                    state.duration = probe_audio_duration(state.audio_path)
                    if state.duration is None:
                        raise RuntimeError(
                            f"Unable to read narration duration with ffprobe: {state.audio_path}"
                        )
                    state.narration_advance_after = (
                        state.narration_lead_in + state.duration + state.narration_padding
                    )
                    state.slide_xml = apply_recorded_timing(
                        state.slide_xml,
                        advance_after=state.narration_advance_after,
                        transition_duration=state.slide_transition_duration,
                        transition_effect=state.slide_transition,
                    )
                    state.resolved_advance_after = state.narration_advance_after
                    state.resolved_advance_on_click = False
                    state.package_uses_timings = True
                state.slide_xml_path.write_text(state.slide_xml, encoding='utf-8')
                state.narration_slides_created.add(state.slide_num)

            state.final_slide_xml = state.slide_xml_path.read_text(encoding='utf-8')
            state.preserved_source_transition = (
                state.slide_patch is not None
                and not state.slide_patch.transition_replaced
            )
            state.preserved_source_animation = (
                state.slide_patch is not None and not state.slide_patch.animation_changed
            )
            state.resolved_motion = None
            state.resolved_animation = None
            if not state.preserved_source_transition:
                try:
                    state.resolved_motion = validate_generated_transition_xml(
                        state.final_slide_xml,
                        effect=state.slide_transition,
                        effect_options=state.slide_transition_effect_options,
                        duration=state.slide_transition_duration,
                        advance_on_click=state.resolved_advance_on_click,
                        advance_after=state.resolved_advance_after,
                        sound=state.expected_transition_sound,
                    )
                except ValueError as exc:
                    raise RuntimeError(
                        f'Slide {state.slide_num} transition validation failed: {exc}'
                    ) from exc
            if not state.preserved_source_animation:
                try:
                    state.resolved_animation = validate_generated_animation_xml(
                        state.final_slide_xml,
                        state.expected_animation_targets,
                        duration=state.expected_animation_duration,
                        trigger=state.expected_animation_trigger,
                    )
                except ValueError as exc:
                    raise RuntimeError(
                        f'Slide {state.slide_num} animation validation failed: {exc}'
                    ) from exc

            if state.conversion_trace is not None:
                state.motion_summary = (
                    {"source_preserved": True}
                    if state.resolved_motion is None
                    else asdict(state.resolved_motion)
                )
                for state.trace_entry in reversed(state.conversion_trace):
                    if state.trace_entry.get('slide_num') == state.slide_num:
                        state.trace_entry['motion'] = state.motion_summary
                        state.trace_entry['animation'] = (
                            {"source_preserved": True}
                            if state.resolved_animation is None
                            else asdict(state.resolved_animation)
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
                print(
                    f"  {state.progress_label} {state.svg_path.name}{state.mode_str}"
                    f"{state.notes_str}{state.narration_str}"
                )

            state.success_count += 1

        except Exception as e:
            if state.verbose:
                print(
                    f"  {state.progress_label} {state.svg_path.name} - Error: {e}"
                )
            if state.use_native_shapes:
                raise
