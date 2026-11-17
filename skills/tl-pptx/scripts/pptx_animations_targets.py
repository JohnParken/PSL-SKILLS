"""PPT Master motion core; responsibility-split, with stable original API.

MIT Copyright (c) 2025-2026 Hugo He.
Upstream commit: 7e0df6705de0cd24345210d31f07fcb5a32b6900.
"""
from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from pptx_animations import AnimationTarget
from typing import Mapping, Sequence

from pptx_animations_registry import ANIMATION_RESTARTS, ANIMATION_TIMING_OPTION_FIELDS, Mapping, Sequence

def _normalize_target_mapping(
    target: Mapping[str, object],
    default_duration_ms: int,
    default_trigger: str,
) -> AnimationTarget:
    from pptx_animations import (
        ANIMATION_RESTARTS,
        ANIMATION_TIMING_OPTION_FIELDS,
        AnimationTarget,
        _non_negative_milliseconds,
        _normalize_after_effect,
        _normalize_repeat_count,
        _normalize_sound,
        _optional_bool,
        _optional_ratio,
        _positive_shape_id,
        _seconds_to_ms,
        normalize_animation_effect_request,
        normalize_animation_trigger,
    )
    allowed = {
        'shape_id',
        'delay_ms',
        'effect',
        'duration',
        'effect_options',
        'trigger',
        'trigger_shape_id',
        *ANIMATION_TIMING_OPTION_FIELDS,
        'after_effect',
        'sound',
    }
    unknown = set(target) - allowed
    if unknown:
        raise ValueError(
            'animation target has unknown field(s): ' + ', '.join(sorted(unknown))
        )
    shape_id = _positive_shape_id(target.get('shape_id'))
    delay_ms = _non_negative_milliseconds(
        target.get('delay_ms', 0),
        'animation target delay_ms',
    )
    effect, effect_options = normalize_animation_effect_request(
        target.get('effect'),
        target.get('effect_options'),
        allow_none=False,
        allow_modes=False,
    )
    duration_ms = default_duration_ms
    if target.get('duration') is not None:
        duration_ms = _seconds_to_ms(
            target.get('duration'),
            'animation target duration',
            allow_zero=False,
        )
    trigger_shape_id = (
        _positive_shape_id(
            target['trigger_shape_id'],
            'animation target trigger_shape_id',
        )
        if 'trigger_shape_id' in target
        else None
    )
    if trigger_shape_id == shape_id:
        raise ValueError(
            'animation trigger_shape_id must target a different shape'
        )
    target_trigger = (
        normalize_animation_trigger(target['trigger'])
        if 'trigger' in target
        else default_trigger
    )
    if trigger_shape_id is not None:
        if 'trigger' in target and target_trigger != 'on-click':
            raise ValueError(
                'animation target with trigger_shape_id must use '
                'trigger "on-click"'
            )
        target_trigger = 'on-click'
    repeat_count = (
        _normalize_repeat_count(target['repeat_count'])
        if 'repeat_count' in target
        else None
    )
    repeat_duration_ms = (
        _seconds_to_ms(
            target['repeat_duration'],
            'animation repeat_duration',
            allow_zero=False,
        )
        if 'repeat_duration' in target
        else None
    )
    if repeat_count is not None and repeat_duration_ms is not None:
        raise ValueError(
            'animation repeat_count and repeat_duration are mutually exclusive'
        )
    auto_reverse = (
        _optional_bool(target['auto_reverse'], 'animation auto_reverse')
        if 'auto_reverse' in target
        else None
    )
    rewind = (
        _optional_bool(target['rewind'], 'animation rewind')
        if 'rewind' in target
        else None
    )
    accelerate = (
        _optional_ratio(target['accelerate'], 'animation accelerate')
        if 'accelerate' in target
        else None
    )
    decelerate = (
        _optional_ratio(target['decelerate'], 'animation decelerate')
        if 'decelerate' in target
        else None
    )
    if (
        accelerate is not None
        and decelerate is not None
        and accelerate + decelerate > 1
    ):
        raise ValueError(
            'animation accelerate + decelerate must not exceed 1'
        )
    bounce_end = (
        _optional_ratio(target['bounce_end'], 'animation bounce_end')
        if 'bounce_end' in target
        else None
    )
    if bounce_end and decelerate:
        raise ValueError(
            'animation bounce_end and decelerate are mutually exclusive '
            'in PowerPoint'
        )
    restart = target.get('restart')
    if restart is not None and restart not in ANIMATION_RESTARTS:
        raise ValueError(
            f'animation restart must be one of '
            f'{", ".join(ANIMATION_RESTARTS)}: {restart!r}'
        )
    after_effect, after_effect_color = _normalize_after_effect(
        target.get('after_effect')
    )
    sound_relationship_id, sound_name = _normalize_sound(target.get('sound'))
    return AnimationTarget(
        shape_id=shape_id,
        delay_ms=delay_ms,
        effect=effect,
        duration_ms=duration_ms,
        effect_options=effect_options,
        trigger=target_trigger,
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
    )


def _normalize_target(
    target: Sequence[object] | Mapping[str, object],
    default_duration_ms: int,
    default_trigger: str = 'after-previous',
) -> AnimationTarget:
    from pptx_animations import (
        AnimationTarget,
        Mapping,
        Sequence,
        _non_negative_milliseconds,
        _normalize_target_mapping,
        _positive_shape_id,
        _seconds_to_ms,
        normalize_animation_effect_request,
    )
    if isinstance(target, Mapping):
        return _normalize_target_mapping(
            target,
            default_duration_ms,
            default_trigger,
        )
    if isinstance(target, (str, bytes)) or not isinstance(target, Sequence):
        raise ValueError(f'animation target must be a 3- or 4-item sequence: {target!r}')
    if len(target) not in (3, 4):
        raise ValueError(f'animation target must contain 3 or 4 items: {target!r}')
    shape_id = _positive_shape_id(target[0])
    delay_ms = _non_negative_milliseconds(target[1], 'animation target delay_ms')
    effect, effect_options = normalize_animation_effect_request(
        target[2],
        allow_none=False,
        allow_modes=False,
    )
    duration_ms = default_duration_ms
    if len(target) == 4 and target[3] is not None:
        duration_ms = _seconds_to_ms(
            target[3],
            'animation target duration',
            allow_zero=False,
        )
    return AnimationTarget(
        shape_id=shape_id,
        delay_ms=delay_ms,
        effect=effect,
        duration_ms=duration_ms,
        effect_options=effect_options,
        trigger=default_trigger,
    )


