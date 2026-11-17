"""PPT Master motion core; responsibility-split, with stable original API.

MIT Copyright (c) 2025-2026 Hugo He.
Upstream commit: 7e0df6705de0cd24345210d31f07fcb5a32b6900.
"""
from __future__ import annotations
import re
from typing import Mapping
from xml.etree import ElementTree as ET
from pptx_transitions import MAX_OOXML_MILLISECONDS, MAX_OOXML_UNSIGNED_INT, PML_NS

from pptx_animations_registry import ET, MAX_OOXML_MILLISECONDS, MAX_OOXML_UNSIGNED_INT, Mapping, PML_NS, _P14_NS, _REL_NS, re

def _timing_summary(
    row: ET.Element,
    duration_ms: int | None,
    errors: list[str],
) -> tuple[
    float | None,
    int | None,
    bool,
    bool,
    float,
    float,
    float,
    str,
    int | None,
]:
    from pptx_animations import (
        _P14_NS,
        _qn,
        re,
    )
    raw_repeat_count = row.get('repeatCount')
    repeat_count = None
    if raw_repeat_count is not None:
        if not re.fullmatch(r'\d+', raw_repeat_count):
            errors.append(
                f'object-animation repeatCount must be numeric; found '
                f'{raw_repeat_count!r}'
            )
        else:
            repeat_count = int(raw_repeat_count) / 1000
    raw_repeat_duration = row.get('repeatDur')
    repeat_duration_ms = None
    if raw_repeat_duration is not None:
        if not re.fullmatch(r'\d+', raw_repeat_duration):
            errors.append(
                f'object-animation repeatDur must be numeric; found '
                f'{raw_repeat_duration!r}'
            )
        else:
            repeat_duration_ms = int(raw_repeat_duration)
    if repeat_count is not None and repeat_duration_ms is not None:
        errors.append('object-animation row sets both repeatCount and repeatDur')

    def ratio(attribute: str) -> float:
        raw = row.get(attribute)
        if raw is None:
            return 0.0
        if not re.fullmatch(r'\d+', raw):
            errors.append(
                f'object-animation {attribute} must be numeric; found {raw!r}'
            )
            return 0.0
        number = int(raw)
        if number > 100000:
            errors.append(
                f'object-animation {attribute} exceeds 100000; found {number}'
            )
        return number / 100000

    accelerate = ratio('accel')
    decelerate = ratio('decel')
    if accelerate + decelerate > 1:
        errors.append('object-animation accel + decel exceeds 100000')
    raw_bounce_values = {
        node.get(_qn(_P14_NS, 'bounceEnd'))
        for node in row.iter()
        if node.get(_qn(_P14_NS, 'bounceEnd')) is not None
    }
    preset_bounce = row.get(_qn(_P14_NS, 'presetBounceEnd'))
    if preset_bounce is not None:
        raw_bounce_values.add(preset_bounce)
    bounce_end = 0.0
    if len(raw_bounce_values) > 1:
        errors.append(
            'object-animation behaviors disagree on p14:bounceEnd'
        )
    elif raw_bounce_values:
        raw_bounce = next(iter(raw_bounce_values))
        if raw_bounce is None or not re.fullmatch(r'\d+', raw_bounce):
            errors.append(
                f'object-animation p14:bounceEnd must be numeric; '
                f'found {raw_bounce!r}'
            )
        else:
            bounce_value = int(raw_bounce)
            if bounce_value > 100000:
                errors.append(
                    'object-animation p14:bounceEnd exceeds 100000; '
                    f'found {bounce_value}'
                )
            bounce_end = bounce_value / 100000
    auto_reverse = row.get('autoRev') == '1'
    rewind = row.get('fill') == 'remove'
    restart = {
        None: 'never',
        'always': 'always',
        'whenNotActive': 'when-not-active',
        'never': 'never',
    }.get(row.get('restart'))
    if restart is None:
        errors.append(
            f'object-animation restart has unknown value: {row.get("restart")!r}'
        )
        restart = 'never'

    playback_duration_ms = None
    if duration_ms is not None:
        one_play = duration_ms * (2 if auto_reverse else 1)
        if repeat_duration_ms is not None:
            playback_duration_ms = repeat_duration_ms
        elif repeat_count is not None:
            playback_duration_ms = max(1, round(one_play * repeat_count))
        else:
            playback_duration_ms = one_play
    return (
        repeat_count,
        repeat_duration_ms,
        auto_reverse,
        rewind,
        accelerate,
        decelerate,
        bounce_end,
        restart,
        playback_duration_ms,
    )


def _after_effect_summary(
    row: ET.Element,
    errors: list[str],
) -> tuple[str, str | None]:
    from pptx_animations import (
        PML_NS,
        _qn,
        _read_animation_color,
    )
    sub_timing = row.find(_qn(PML_NS, 'subTnLst'))
    if sub_timing is None:
        return 'none', None
    after_nodes = [
        node
        for node in list(sub_timing)
        if any(
            ctn.get('afterEffect') == '1'
            for ctn in node.iter(_qn(PML_NS, 'cTn'))
        )
    ]
    if not after_nodes:
        return 'none', None
    if len(after_nodes) != 1:
        errors.append(
            f'object-animation row contains {len(after_nodes)} after effects'
        )
        return 'none', None
    node = after_nodes[0]
    if node.tag == _qn(PML_NS, 'animClr'):
        return (
            'dim',
            _read_animation_color(node, errors, 'animation dim after effect'),
        )
    if node.tag == _qn(PML_NS, 'set'):
        ctns = list(node.iter(_qn(PML_NS, 'cTn')))
        master_relation = ctns[0].get('masterRel') if ctns else None
        if master_relation == 'sameClick':
            return 'hide', None
        if master_relation == 'nextClick':
            return 'hide-on-next-click', None
    errors.append('object-animation row contains an unknown after effect')
    return 'none', None


def _sound_summary(
    row: ET.Element,
    errors: list[str],
) -> tuple[str | None, str | None]:
    from pptx_animations import (
        PML_NS,
        _REL_NS,
        _qn,
    )
    sounds = list(row.iter(_qn(PML_NS, 'sndTgt')))
    if not sounds:
        return None, None
    if len(sounds) != 1:
        errors.append(f'object-animation row contains {len(sounds)} sounds')
        return None, None
    relationship_id = sounds[0].get(_qn(_REL_NS, 'embed'))
    name = sounds[0].get('name')
    if relationship_id is None or name is None:
        errors.append('object-animation sound is missing relationship id or name')
    return relationship_id, name


def _row_trigger_shape_id(
    row: ET.Element,
    parent_map: Mapping[ET.Element, ET.Element],
    errors: list[str],
) -> int | None:
    from pptx_animations import (
        MAX_OOXML_UNSIGNED_INT,
        PML_NS,
        _direct_conditions,
        _int_attribute,
        _qn,
    )
    current = parent_map.get(row)
    while current is not None:
        if (
            current.tag == _qn(PML_NS, 'cTn')
            and current.get('nodeType') == 'interactiveSeq'
        ):
            shape_targets = [
                target
                for condition in _direct_conditions(current)
                if condition.get('evt') == 'onClick'
                for target in condition.iter(_qn(PML_NS, 'spTgt'))
            ]
            if len(shape_targets) != 1:
                errors.append(
                    'interactive animation sequence must have one trigger shape'
                )
                return None
            return _int_attribute(
                shape_targets[0],
                'spid',
                'interactive animation trigger shape id',
                errors,
                minimum=1,
                maximum=MAX_OOXML_UNSIGNED_INT,
            )
        current = parent_map.get(current)
    return None


def _row_offset_ms(
    row: ET.Element,
    trigger: str,
    trigger_shape_id: int | None,
    parent_map: Mapping[ET.Element, ET.Element],
    errors: list[str],
) -> int:
    from pptx_animations import (
        MAX_OOXML_MILLISECONDS,
        PML_NS,
        _direct_conditions,
        _qn,
        re,
    )
    leaf_conditions = _direct_conditions(row)
    leaf_delay = None
    if (
        len(leaf_conditions) == 1
        and re.fullmatch(r'\d+', leaf_conditions[0].get('delay') or '')
    ):
        leaf_delay = int(leaf_conditions[0].get('delay', '0'))
    else:
        errors.append(
            'object-animation row must have one numeric leaf start condition'
        )
    current = parent_map.get(row)
    saw_indefinite = False
    numeric_offset: int | None = None
    while current is not None:
        if current.tag == _qn(PML_NS, 'cTn'):
            conditions = _direct_conditions(current)
            if any(condition.get('delay') == 'indefinite' for condition in conditions):
                saw_indefinite = True
            if trigger in {'with-previous', 'after-previous'}:
                numeric = [
                    condition.get('delay')
                    for condition in conditions
                    if condition.get('evt') is None
                    and re.fullmatch(r'\d+', condition.get('delay') or '')
                ]
                if numeric and numeric_offset is None:
                    numeric_offset = int(numeric[0])
                    if numeric_offset > MAX_OOXML_MILLISECONDS:
                        errors.append(
                            'animation row offset exceeds the OOXML '
                            f'millisecond limit: {numeric_offset}'
                        )
        current = parent_map.get(current)

    if (
        trigger == 'on-click'
        and trigger_shape_id is None
        and not saw_indefinite
    ):
        errors.append(
            'on-click object-animation row is missing an indefinite click wrapper'
        )
    if trigger in {'with-previous', 'after-previous'} and not saw_indefinite:
        errors.append(f'{trigger} sequence is missing its sequence anchor')
    if trigger in {'with-previous', 'after-previous'} and numeric_offset is None:
        errors.append(
            f'{trigger} object-animation row is missing its numeric offset wrapper'
        )
    if trigger in {'with-previous', 'after-previous'}:
        absolute_offset = (numeric_offset or 0) + (leaf_delay or 0)
        if absolute_offset > MAX_OOXML_MILLISECONDS:
            errors.append(
                'animation row absolute offset exceeds the OOXML '
                f'millisecond limit: {absolute_offset}'
            )
        return absolute_offset
    if trigger_shape_id is not None:
        return leaf_delay or 0
    return leaf_delay or 0


