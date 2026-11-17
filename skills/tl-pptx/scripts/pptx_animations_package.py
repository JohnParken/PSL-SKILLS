"""PPT Master motion core; responsibility-split, with stable original API.

MIT Copyright (c) 2025-2026 Hugo He.
Upstream commit: 7e0df6705de0cd24345210d31f07fcb5a32b6900.
"""
from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from pptx_animations import AnimationSequenceSummary
import copy
import posixpath
import re
import zipfile
from dataclasses import replace
from pathlib import Path
from typing import Mapping, Sequence
from xml.etree import ElementTree as ET
from pptx_transitions import PML_NS

from pptx_animations_registry import (
    ET,
    Mapping,
    NATIVE_ANIMATIONS,
    PML_NS,
    Path,
    Sequence,
    _AUDIO_REL_TYPE,
    _PACKAGE_REL_NS,
    _PRESET_CLASS_BY_CATEGORY,
    copy,
    posixpath,
    re,
    replace,
    zipfile,
)

def validate_generated_animation_xml(
    slide_xml: str | bytes,
    targets: Sequence[Sequence[object] | Mapping[str, object]],
    *,
    duration: float = 0.3,
    trigger: str = 'after-previous',
) -> AnimationSequenceSummary:
    """Read back one generated sequence and require exact requested semantics."""
    from pptx_animations import (
        ET,
        NATIVE_ANIMATIONS,
        PML_NS,
        _PRESET_CLASS_BY_CATEGORY,
        _animation_row_for_options,
        _animation_spec_matches_row,
        _append_after_effect,
        _append_animation_sound,
        _apply_timing_options,
        _main_target_offsets,
        _normalize_target,
        _qn,
        _read_effect_options,
        _row_filter,
        _scale_animation_row_duration,
        _seconds_to_ms,
        _select_supported_timing_branch,
        _timing_summary,
        copy,
        normalize_animation_trigger,
        read_slide_animation_sequence,
        replace,
    )
    trigger = normalize_animation_trigger(trigger)
    default_duration_ms = _seconds_to_ms(
        duration,
        'animation duration',
        allow_zero=False,
    )
    normalized_expected = tuple(
        _normalize_target(target, default_duration_ms, trigger)
        for target in targets
    )
    # PowerPoint stores ordinary rows in mainSeq and shape-triggered rows in
    # separate interactiveSeq containers. Their relative cross-sequence order
    # has no playback meaning, so read-back follows the native container order.
    expected = tuple(
        target
        for target in normalized_expected
        if target.trigger_shape_id is None
    ) + tuple(
        target
        for target in normalized_expected
        if target.trigger_shape_id is not None
    )
    summary = read_slide_animation_sequence(
        slide_xml,
        require_supported_effects=True,
    )
    data = slide_xml.encode('utf-8') if isinstance(slide_xml, str) else slide_xml
    actual_root = _select_supported_timing_branch(ET.fromstring(data))
    actual_row_elements = [
        row
        for row in actual_root.iter(_qn(PML_NS, 'cTn'))
        if row.get('presetClass') in set(_PRESET_CLASS_BY_CATEGORY.values())
    ]
    errors: list[str] = []
    if len(summary.rows) != len(expected):
        errors.append(
            f'animation read-back row count is {len(summary.rows)}; '
            f'expected {len(expected)}'
        )
    expected_main_targets = tuple(
        target for target in expected if target.trigger_shape_id is None
    )
    expected_main_triggers = {
        target.trigger for target in expected_main_targets
    }
    expected_sequence_trigger = (
        (
            next(iter(expected_main_triggers))
            if len(expected_main_triggers) == 1
            else None
        )
        if expected_main_targets
        else ('on-click' if expected else None)
    )
    if expected and summary.trigger != expected_sequence_trigger:
        errors.append(
            f'animation read-back trigger is {summary.trigger!r}; '
            f'expected {expected_sequence_trigger!r}'
        )

    try:
        main_offsets = iter(_main_target_offsets(expected_main_targets))
    except ValueError as exc:
        errors.append(str(exc))
        main_offsets = iter(())
    expected_offsets = [
        (
            target.delay_ms
            if target.trigger_shape_id is not None
            else next(main_offsets, 0)
        )
        for target in expected
    ]

    for index, (actual, target) in enumerate(zip(summary.rows, expected), 1):
        spec = NATIVE_ANIMATIONS[target.effect]
        expected_row = _animation_row_for_options(
            target.effect,
            target.effect_options,
        )
        if spec['durationScalable']:
            _scale_animation_row_duration(
                expected_row,
                base_duration_ms=int(spec['defaultDurationMs']),
                requested_duration_ms=target.duration_ms,
            )
        _apply_timing_options(expected_row, target)
        option_errors: list[str] = []
        expected_filter = _row_filter(
            expected_row,
            str(spec['presetClass']),
            option_errors,
        )
        expected_options = _read_effect_options(
            expected_row,
            target.effect,
            expected_filter,
            option_errors,
        )
        (
            expected_repeat_count,
            expected_repeat_duration_ms,
            expected_auto_reverse,
            expected_rewind,
            expected_accelerate,
            expected_decelerate,
            expected_bounce_end,
            expected_restart,
            expected_playback_duration_ms,
        ) = _timing_summary(
            expected_row,
            (
                target.duration_ms
                if spec['durationScalable']
                else spec['defaultDurationMs']
            ),
            option_errors,
        )
        if option_errors:
            errors.append(
                f'animation row {index} expected-option model failed: '
                + '; '.join(option_errors)
            )
        if index <= len(actual_row_elements):
            actual_row_element = actual_row_elements[index - 1]
            expected_behavior_row = copy.deepcopy(expected_row)
            actual_row_id = actual_row_element.get('id')
            expected_behavior_row.set(
                'id',
                actual_row_id
                if actual_row_id and actual_row_id.isdigit()
                else '1',
            )
            _append_after_effect(
                expected_behavior_row,
                target,
                int(expected_behavior_row.get('id', '1')),
            )
            _append_animation_sound(expected_behavior_row, target)
            behavior_spec = {
                'rowXml': ET.tostring(
                    expected_behavior_row,
                    encoding='unicode',
                )
            }
            if not _animation_spec_matches_row(
                actual_row_element,
                behavior_spec,
            ):
                errors.append(
                    f'animation row {index} PowerPoint-authored behavior '
                    'tree changed'
                )
        if actual.shape_id != target.shape_id:
            errors.append(
                f'animation row {index} targets shape {actual.shape_id}; '
                f'expected {target.shape_id}'
            )
        expected_row_trigger = (
            target.trigger
        )
        if actual.trigger != expected_row_trigger:
            errors.append(
                f'animation row {index} trigger is {actual.trigger!r}; '
                f'expected {expected_row_trigger!r}'
            )
        if actual.trigger_shape_id != target.trigger_shape_id:
            errors.append(
                f'animation row {index} trigger shape is '
                f'{actual.trigger_shape_id!r}; expected '
                f'{target.trigger_shape_id!r}'
            )
        if target.effect not in actual.supported_effects:
            errors.append(
                f'animation row {index} resolved effects '
                f'{actual.supported_effects!r}; expected {target.effect!r}'
            )
        if actual.preset_class != spec['presetClass']:
            errors.append(f'animation row {index} presetClass changed')
        if actual.preset_id != int(spec['presetID']):
            errors.append(f'animation row {index} presetID changed')
        if actual.preset_subtype != int(expected_row.get('presetSubtype', '-1')):
            errors.append(f'animation row {index} presetSubtype changed')
        if actual.filter_name != expected_filter:
            errors.append(f'animation row {index} filter changed')
        if dict(actual.effect_options) != expected_options:
            errors.append(
                f'animation row {index} effect_options are '
                f'{dict(actual.effect_options)!r}; expected {expected_options!r}'
            )
        expected_duration = (
            target.duration_ms
            if spec['durationScalable']
            else spec['defaultDurationMs']
        )
        if actual.duration_ms != expected_duration:
            errors.append(
                f'animation row {index} duration is {actual.duration_ms}ms; '
                f'expected {expected_duration}ms'
            )
        if actual.offset_ms != expected_offsets[index - 1]:
            errors.append(
                f'animation row {index} offset is {actual.offset_ms}ms; '
                f'expected {expected_offsets[index - 1]}ms'
            )
        timing_pairs = (
            ('repeat_count', actual.repeat_count, expected_repeat_count),
            (
                'repeat_duration_ms',
                actual.repeat_duration_ms,
                expected_repeat_duration_ms,
            ),
            ('auto_reverse', actual.auto_reverse, expected_auto_reverse),
            ('rewind', actual.rewind, expected_rewind),
            ('accelerate', actual.accelerate, expected_accelerate),
            ('decelerate', actual.decelerate, expected_decelerate),
            ('bounce_end', actual.bounce_end, expected_bounce_end),
            ('restart', actual.restart, expected_restart),
            (
                'playback_duration_ms',
                actual.playback_duration_ms,
                expected_playback_duration_ms,
            ),
        )
        for field, actual_value, expected_value in timing_pairs:
            if actual_value != expected_value:
                errors.append(
                    f'animation row {index} {field} is {actual_value!r}; '
                    f'expected {expected_value!r}'
                )
        if actual.after_effect != target.after_effect:
            errors.append(
                f'animation row {index} after_effect is '
                f'{actual.after_effect!r}; expected {target.after_effect!r}'
            )
        if actual.after_effect_color != target.after_effect_color:
            errors.append(
                f'animation row {index} after_effect_color is '
                f'{actual.after_effect_color!r}; '
                f'expected {target.after_effect_color!r}'
            )
        if actual.sound_relationship_id != target.sound_relationship_id:
            errors.append(
                f'animation row {index} sound relationship is '
                f'{actual.sound_relationship_id!r}; '
                f'expected {target.sound_relationship_id!r}'
            )
        if actual.sound_name != target.sound_name:
            errors.append(
                f'animation row {index} sound name is '
                f'{actual.sound_name!r}; expected {target.sound_name!r}'
            )
    if errors:
        raise ValueError('; '.join(errors))
    resolved_rows = tuple(
        replace(actual, effect=target.effect)
        for actual, target in zip(summary.rows, expected)
    )
    return replace(summary, rows=resolved_rows)


def validate_pptx_animation_package(
    pptx_path: str | Path,
    *,
    require_supported_effects: bool = False,
    skip_slide_numbers: set[int] | None = None,
) -> None:
    """Validate generated timing, excluding byte-preserved source slides."""
    from pptx_animations import (
        ET,
        Path,
        _AUDIO_REL_TYPE,
        _PACKAGE_REL_NS,
        _qn,
        posixpath,
        re,
        read_slide_animation_sequence,
        validate_slide_animation_structure,
        zipfile,
    )
    path = Path(pptx_path)
    skipped = skip_slide_numbers or set()
    errors: list[str] = []
    try:
        with zipfile.ZipFile(path) as package:
            names = sorted(
                name
                for name in package.namelist()
                if re.fullmatch(r'ppt/slides/slide\d+\.xml', name)
            )
            for name in names:
                match = re.search(r'slide(\d+)\.xml$', name)
                if match is not None and int(match.group(1)) in skipped:
                    continue
                slide_data = package.read(name)
                try:
                    root = ET.fromstring(slide_data)
                except ET.ParseError as exc:
                    errors.append(f'{name}: invalid XML: {exc}')
                    continue
                for error in validate_slide_animation_structure(
                    root,
                    require_supported_effects=require_supported_effects,
                ):
                    errors.append(f'{name}: {error}')
                try:
                    summary = read_slide_animation_sequence(slide_data)
                except ValueError:
                    continue
                sound_ids = {
                    row.sound_relationship_id
                    for row in summary.rows
                    if row.sound_relationship_id is not None
                }
                if not sound_ids:
                    continue
                slide_leaf = posixpath.basename(name)
                rels_name = (
                    f'ppt/slides/_rels/{slide_leaf}.rels'
                )
                if rels_name not in package.namelist():
                    errors.append(
                        f'{name}: animation sound relationships are missing'
                    )
                    continue
                try:
                    relationships = ET.fromstring(package.read(rels_name))
                except ET.ParseError as exc:
                    errors.append(f'{rels_name}: invalid XML: {exc}')
                    continue
                by_id = {
                    rel.get('Id'): rel
                    for rel in relationships.findall(
                        _qn(_PACKAGE_REL_NS, 'Relationship')
                    )
                }
                for relationship_id in sorted(sound_ids):
                    relationship = by_id.get(relationship_id)
                    if relationship is None:
                        errors.append(
                            f'{name}: animation sound relationship '
                            f'{relationship_id} is missing'
                        )
                        continue
                    if relationship.get('Type') != _AUDIO_REL_TYPE:
                        errors.append(
                            f'{name}: animation sound relationship '
                            f'{relationship_id} is not an audio relationship'
                        )
                        continue
                    target = relationship.get('Target')
                    if not target:
                        errors.append(
                            f'{name}: animation sound relationship '
                            f'{relationship_id} has no target'
                        )
                        continue
                    target_part = posixpath.normpath(
                        posixpath.join(posixpath.dirname(name), target)
                    )
                    if target_part not in package.namelist():
                        errors.append(
                            f'{name}: animation sound target is missing: '
                            f'{target_part}'
                        )
    except (OSError, zipfile.BadZipFile) as exc:
        raise ValueError(f'unable to read PPTX package {path}: {exc}') from exc
    if errors:
        raise ValueError('; '.join(errors))


