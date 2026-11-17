"""PPT Master motion core; responsibility-split, with stable original API.

MIT Copyright (c) 2025-2026 Hugo He.
Upstream commit: 7e0df6705de0cd24345210d31f07fcb5a32b6900.
"""
from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from pptx_animations import AnimationTarget, AnimationRowSummary
import copy
from typing import Mapping
from xml.etree import ElementTree as ET
from pptx_transitions import PML_NS

from pptx_animations_registry import ET, Mapping, NATIVE_ANIMATIONS, PML_NS, _MC_NS, _NODE_TYPE_TRIGGERS, _PRESET_CLASS_BY_CATEGORY, copy

def _row_matches_powerpoint_behavior(
    row: ET.Element,
    *,
    shape_id: int,
    effect: str,
    effect_options: Mapping[str, object],
    trigger: str,
    duration_ms: int,
    repeat_count: float | None,
    repeat_duration_ms: int | None,
    auto_reverse: bool,
    rewind: bool,
    accelerate: float,
    decelerate: float,
    bounce_end: float,
    restart: str,
    after_effect: str,
    after_effect_color: str | None,
    sound_relationship_id: str | None,
    sound_name: str | None,
) -> bool:
    """Match one read-back row to its reconstructed native behavior tree."""
    from pptx_animations import (
        AnimationTarget,
        ET,
        NATIVE_ANIMATIONS,
        _animation_row_for_options,
        _animation_spec_matches_row,
        _append_after_effect,
        _append_animation_sound,
        _apply_timing_options,
        _scale_animation_row_duration,
    )
    spec = NATIVE_ANIMATIONS[effect]
    target = AnimationTarget(
        shape_id=shape_id,
        delay_ms=0,
        effect=effect,
        duration_ms=duration_ms,
        effect_options=effect_options,
        trigger=trigger,
        repeat_count=repeat_count,
        repeat_duration_ms=repeat_duration_ms,
        auto_reverse=True if auto_reverse else None,
        rewind=True if rewind else None,
        accelerate=accelerate or None,
        decelerate=decelerate or None,
        bounce_end=bounce_end or None,
        restart=restart if row.get('restart') is not None else None,
        after_effect=after_effect,
        after_effect_color=after_effect_color,
        sound_relationship_id=sound_relationship_id,
        sound_name=sound_name,
    )
    option_candidates = [dict(effect_options)]
    option_candidates.extend(
        {
            name: value
            for name, value in effect_options.items()
            if name != omitted
        }
        for omitted in effect_options
    )
    option_candidates.append({})
    seen: set[tuple[tuple[str, object], ...]] = set()
    for candidate in option_candidates:
        candidate_key = tuple(sorted(candidate.items()))
        if candidate_key in seen:
            continue
        seen.add(candidate_key)
        expected = _animation_row_for_options(effect, candidate)
        if spec['durationScalable']:
            _scale_animation_row_duration(
                expected,
                base_duration_ms=int(spec['defaultDurationMs']),
                requested_duration_ms=duration_ms,
            )
        _apply_timing_options(expected, target)
        row_id = row.get('id')
        expected.set('id', row_id if row_id and row_id.isdigit() else '1')
        _append_after_effect(
            expected,
            target,
            int(expected.get('id', '1')),
        )
        _append_animation_sound(expected, target)
        if _animation_spec_matches_row(
            row,
            {'rowXml': ET.tostring(expected, encoding='unicode')},
        ):
            return True
    return False


def _animation_rows(
    slide_root: ET.Element,
    errors: list[str],
    *,
    require_behavior_signatures: bool = False,
) -> list[AnimationRowSummary]:
    from pptx_animations import (
        AnimationRowSummary,
        PML_NS,
        _NODE_TYPE_TRIGGERS,
        _PRESET_CLASS_BY_CATEGORY,
        _after_effect_summary,
        _behavior_duration_ms,
        _qn,
        _read_effect_options,
        _resolve_row_effect,
        _row_filter,
        _row_matches_powerpoint_behavior,
        _row_offset_ms,
        _row_shape_id,
        _row_trigger_shape_id,
        _sound_summary,
        _timing_summary,
    )
    parent_map = {
        child: parent
        for parent in slide_root.iter()
        for child in list(parent)
    }
    rows: list[AnimationRowSummary] = []
    for row in slide_root.iter(_qn(PML_NS, 'cTn')):
        preset_class = row.get('presetClass')
        if preset_class not in set(_PRESET_CLASS_BY_CATEGORY.values()):
            continue
        node_type = row.get('nodeType')
        trigger = _NODE_TYPE_TRIGGERS.get(node_type or '')
        if trigger is None:
            errors.append(
                f'unsupported object-animation nodeType {node_type!r}; expected '
                f'{", ".join(_NODE_TYPE_TRIGGERS)}'
            )
            continue
        shape_id = _row_shape_id(row, errors)
        filter_name = _row_filter(row, preset_class, errors)
        supported_effects, resolved_class, preset_id, preset_subtype = (
            _resolve_row_effect(
            row,
            filter_name,
            errors,
        )
        )
        duration_ms = _behavior_duration_ms(row, errors)
        trigger_shape_id = _row_trigger_shape_id(row, parent_map, errors)
        offset_ms = _row_offset_ms(
            row,
            trigger,
            trigger_shape_id,
            parent_map,
            errors,
        )
        resolved_effect = supported_effects[0] if supported_effects else None
        effect_options = _read_effect_options(
            row,
            resolved_effect,
            filter_name,
            errors,
        )
        (
            repeat_count,
            repeat_duration_ms,
            auto_reverse,
            rewind,
            accelerate,
            decelerate,
            bounce_end,
            restart,
            playback_duration_ms,
        ) = _timing_summary(row, duration_ms, errors)
        after_effect, after_effect_color = _after_effect_summary(row, errors)
        sound_relationship_id, sound_name = _sound_summary(row, errors)
        if (
            shape_id is None
            or resolved_class is None
            or preset_id is None
            or preset_subtype is None
        ):
            continue
        if (
            require_behavior_signatures
            and resolved_effect is not None
            and duration_ms is not None
            and not _row_matches_powerpoint_behavior(
                row,
                shape_id=shape_id,
                effect=resolved_effect,
                effect_options=effect_options,
                trigger=trigger,
                duration_ms=duration_ms,
                repeat_count=repeat_count,
                repeat_duration_ms=repeat_duration_ms,
                auto_reverse=auto_reverse,
                rewind=rewind,
                accelerate=accelerate,
                decelerate=decelerate,
                bounce_end=bounce_end,
                restart=restart,
                after_effect=after_effect,
                after_effect_color=after_effect_color,
                sound_relationship_id=sound_relationship_id,
                sound_name=sound_name,
            )
        ):
            errors.append(
                'object-animation PowerPoint-authored behavior tree changed '
                f'for shape {shape_id}'
            )
        rows.append(
            AnimationRowSummary(
                shape_id=shape_id,
                effect=resolved_effect,
                supported_effects=supported_effects,
                preset_class=resolved_class,
                trigger=trigger,
                duration_ms=duration_ms,
                offset_ms=offset_ms,
                preset_id=preset_id,
                preset_subtype=preset_subtype,
                filter_name=filter_name,
                effect_options=effect_options,
                trigger_shape_id=trigger_shape_id,
                repeat_count=repeat_count,
                repeat_duration_ms=repeat_duration_ms,
                auto_reverse=auto_reverse,
                rewind=rewind,
                accelerate=accelerate,
                decelerate=decelerate,
                bounce_end=bounce_end,
                restart=restart,
                after_effect=after_effect,
                after_effect_color=after_effect_color,
                sound_relationship_id=sound_relationship_id,
                sound_name=sound_name,
                playback_duration_ms=playback_duration_ms,
            )
        )
    return rows


def _select_supported_timing_branch(slide_root: ET.Element) -> ET.Element:
    """Project p14 AlternateContent timing onto one effective slide tree."""
    from pptx_animations import (
        PML_NS,
        _MC_NS,
        _qn,
        copy,
    )
    projected = copy.deepcopy(slide_root)
    alternate_tag = _qn(_MC_NS, 'AlternateContent')
    choice_tag = _qn(_MC_NS, 'Choice')
    fallback_tag = _qn(_MC_NS, 'Fallback')
    for index, child in list(enumerate(list(projected))):
        if child.tag != alternate_tag:
            continue
        branches = [
            branch
            for branch in list(child)
            if branch.tag in {choice_tag, fallback_tag}
        ]
        selected_timing = None
        for branch in branches:
            if (
                branch.tag == choice_tag
                and 'p14' in (branch.get('Requires') or '').split()
            ):
                selected_timing = branch.find(_qn(PML_NS, 'timing'))
                if selected_timing is not None:
                    break
        if selected_timing is None:
            for branch in branches:
                selected_timing = branch.find(_qn(PML_NS, 'timing'))
                if selected_timing is not None:
                    break
        if selected_timing is None:
            continue
        projected.remove(child)
        projected.insert(index, copy.deepcopy(selected_timing))
    return projected


