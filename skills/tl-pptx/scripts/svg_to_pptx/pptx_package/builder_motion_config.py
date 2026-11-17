"""Core PPTX assembly: create_pptx_with_native_svg."""
# Adapted from hugohe3/ppt-master 7e0df6705de0cd24345210d31f07fcb5a32b6900.
# MIT; Copyright (c) 2025-2026 Hugo He. See references/LICENSE-PPT-MASTER.txt and references/upstream.md.

from __future__ import annotations
import json
import random
from typing import Any
from pptx_transitions import (
    normalize_transition_effect_request,
    validate_seconds,
)
from pptx_animations import (
    ANIMATION_TIMING_OPTION_FIELDS,
    animation_seconds_to_milliseconds,
    normalize_animation_effect,
    normalize_animation_effect_request,
    normalize_animation_trigger,
    pick_animation_effect,
)
from ..animation_config import (
    animation_group_effect_entries,
    resolve_slide_animation_config,
)

def _slide_config(animation_config: dict[str, Any] | None, svg_stem: str) -> dict[str, Any]:
    if not animation_config:
        return {}
    slides_value = animation_config.get('slides', {})
    if not isinstance(slides_value, dict):
        raise ValueError('animations.json field "slides" must be an object')
    slide_value = slides_value.get(svg_stem, {})
    if not isinstance(slide_value, dict):
        raise ValueError(
            f'animations.json slide "{svg_stem}" must be an object'
        )
    return slide_value


def _slide_transition_settings(
    default_transition_cfg: dict[str, Any],
    slide_cfg: dict[str, Any],
    transition: str | None,
    transition_effect_options: dict[str, object] | None,
    duration: float,
    auto_advance: float | None,
    transition_sound: str | None,
    cli_overrides: dict[str, bool],
) -> tuple[str | None, dict[str, object], float, float | None, str | None]:
    trans_value = slide_cfg.get('transition', {})
    if not isinstance(trans_value, dict):
        raise ValueError('animations.json slide transition must be an object')
    trans_cfg = trans_value
    effect, effect_options = normalize_transition_effect_request(
        transition,
        transition_effect_options,
    )
    if not cli_overrides.get('transition'):
        if 'effect' in trans_cfg:
            raw_effect = trans_cfg['effect']
            raw_options = trans_cfg.get('effect_options')
            effect, effect_options = normalize_transition_effect_request(
                raw_effect,
                raw_options,
            )
        elif 'effect_options' in trans_cfg:
            raise ValueError(
                'animations.json transition effect_options requires '
                'an explicit effect'
            )
    if not cli_overrides.get('transition_duration'):
        if 'duration' in trans_cfg:
            duration = validate_seconds(
                trans_cfg.get('duration'),
                "transition duration",
                allow_zero=effect is None,
            )
    if not cli_overrides.get('auto_advance') and 'auto_advance' in trans_cfg:
        auto_advance = validate_seconds(
            trans_cfg.get('auto_advance'),
            "transition auto_advance",
            allow_zero=True,
        )
    raw_sound = transition_sound
    if raw_sound is None and not cli_overrides.get('transition_sound'):
        raw_sound = default_transition_cfg.get('sound')
    if 'sound' in trans_cfg:
        raw_sound = trans_cfg['sound']
    if raw_sound is not None and (
        not isinstance(raw_sound, str) or not raw_sound.strip()
    ):
        raise ValueError(
            'animations.json transition sound must be a non-empty '
            'project-relative .wav path or null'
        )
    return effect, effect_options, duration, auto_advance, raw_sound


def _slide_animation_settings(
    slide_cfg: dict[str, Any],
    default_animation_cfg: dict[str, Any],
    animation: str | None,
    duration: float,
    stagger: float,
    trigger: str,
    cli_overrides: dict[str, bool],
) -> tuple[str | None, float, float, str, dict[str, Any]]:
    anim_value = slide_cfg.get('animation', {})
    if not isinstance(anim_value, dict):
        raise ValueError('animations.json slide animation must be an object')
    anim_cfg = anim_value
    resolved_cfg = resolve_slide_animation_config(
        default_animation_cfg,
        anim_cfg,
    )
    if cli_overrides.get('animation'):
        effect, effect_options = normalize_animation_effect_request(
            animation,
            allow_none=True,
            allow_modes=True,
        )
        resolved_cfg['effect'] = effect or 'none'
        if effect_options:
            resolved_cfg['effect_options'] = effect_options
        else:
            resolved_cfg.pop('effect_options', None)
    else:
        raw_effect = resolved_cfg.get('effect', animation)
        effect, effect_options = normalize_animation_effect_request(
            raw_effect,
            resolved_cfg.get('effect_options'),
            allow_none=True,
            allow_modes=True,
        )
        resolved_cfg['effect'] = effect or 'none'
        if effect_options:
            resolved_cfg['effect_options'] = effect_options
        else:
            resolved_cfg.pop('effect_options', None)
    if not cli_overrides.get('animation_duration'):
        duration = validate_seconds(
            anim_cfg.get('duration', duration),
            'animation duration',
            allow_zero=False,
        )
    else:
        resolved_cfg['duration'] = duration
    if not cli_overrides.get('animation_stagger'):
        stagger = validate_seconds(
            anim_cfg.get('stagger', stagger),
            'animation stagger',
            allow_zero=True,
        )
    else:
        resolved_cfg['stagger'] = stagger
    if not cli_overrides.get('animation_trigger') and 'trigger' in anim_cfg:
        trigger = normalize_animation_trigger(anim_cfg.get('trigger'))
    else:
        trigger = normalize_animation_trigger(trigger)
        resolved_cfg['trigger'] = trigger
    animation_seconds_to_milliseconds(
        duration,
        'animation duration',
        allow_zero=False,
    )
    animation_seconds_to_milliseconds(
        stagger,
        'animation stagger',
        allow_zero=True,
    )
    resolved_cfg['effect'] = effect or 'none'
    resolved_cfg['duration'] = duration
    resolved_cfg['stagger'] = stagger
    resolved_cfg['trigger'] = trigger
    return effect, duration, stagger, trigger, resolved_cfg


def _build_sequence_targets(
    anim_targets: list[tuple[int, str]],
    slide_name: str,
    slide_cfg: dict[str, Any],
    animation: str | None,
    animation_cfg: dict[str, Any],
    duration: float,
    stagger: float,
    mixed_animation_offset: int,
    animation_rng: random.Random,
) -> tuple[list[dict[str, Any]], int]:
    groups_value = slide_cfg.get('groups', {})
    if not isinstance(groups_value, dict):
        raise ValueError('animations.json slide groups must be an object')
    groups_cfg = groups_value
    shape_ids_by_group = {
        svg_id: sid for sid, svg_id in anim_targets
    }
    ordered: list[tuple[int, int, int, str, str, dict[str, Any]]] = []
    # A group without a sidecar ``order`` follows the nearest listed group
    # before it in SVG order (0 before the first one). Numbering unlisted
    # groups by raw SVG index collided with explicit orders: a headline at
    # index 2 landed behind the body block the author had numbered 1 and 2.
    inherited_orders: list[int] = []
    current_order = 0
    for _sid, svg_id in anim_targets:
        group_value = groups_cfg.get(svg_id, {})
        explicit = [
            cfg.get('order')
            for _path, cfg in (
                animation_group_effect_entries(group_value, path='')
                if isinstance(group_value, dict) else []
            )
            if isinstance(cfg.get('order'), int)
            and not isinstance(cfg.get('order'), bool)
        ]
        if explicit:
            current_order = max(current_order, max(explicit))
        inherited_orders.append(current_order)
    for idx, (sid, svg_id) in enumerate(anim_targets):
        group_value = groups_cfg.get(svg_id, {})
        if not isinstance(group_value, dict):
            raise ValueError(
                f'animations.json group "{svg_id}" must be an object'
            )
        group_path = (
            f'slides[{json.dumps(slide_name, ensure_ascii=False)}]'
            f'.groups[{json.dumps(svg_id, ensure_ascii=False)}]'
        )
        effect_entries = animation_group_effect_entries(
            group_value,
            path=group_path,
        )
        for effect_idx, (effect_path, effect_cfg) in enumerate(effect_entries):
            raw_effect = effect_cfg.get('effect')
            if raw_effect is not None:
                normalized_effect = normalize_animation_effect(
                    raw_effect,
                    allow_none=True,
                    allow_modes=True,
                )
            else:
                normalized_effect = None
            if 'effect' in effect_cfg and normalized_effect is None:
                continue
            if animation is None and normalized_effect is None:
                continue
            order_value = effect_cfg.get('order')
            order = order_value if order_value is not None else inherited_orders[idx]
            if order_value is not None and (
                isinstance(order, bool)
                or not isinstance(order, int)
                or order <= 0
            ):
                raise ValueError(
                    f'animations.json {effect_path}.order must be '
                    'a positive integer'
                )
            effect_entry = dict(effect_cfg)
            effect_entry['_shape_id'] = sid
            effect_entry['_effect'] = normalized_effect
            effect_entry['_effect_raw'] = raw_effect
            ordered.append(
                (
                    order,
                    idx,
                    effect_idx,
                    svg_id,
                    effect_path,
                    effect_entry,
                )
            )

    ordered.sort(key=lambda item: (item[0], item[1], item[2]))

    seq_targets: list[dict[str, Any]] = []
    resolved_group_modes: list[str | None] = []
    main_sequence_count = 0
    for seq_idx, (
        _order,
        _original_idx,
        _effect_idx,
        _svg_id,
        effect_path,
        group_cfg,
    ) in enumerate(ordered):
        shape_id = int(group_cfg['_shape_id'])
        raw_effect = group_cfg.get('_effect')
        resolved_group_modes.append(
            raw_effect if raw_effect in ('auto', 'mixed', 'random') else None
        )
        if raw_effect in ('auto', 'mixed', 'random'):
            effect = pick_animation_effect(
                str(raw_effect), seq_idx, mixed_animation_offset, group_id=_svg_id,
                rng=animation_rng,
            )
            effect_options: dict[str, object] = {}
        else:
            effect = str(raw_effect or pick_animation_effect(
                animation, seq_idx, mixed_animation_offset, group_id=_svg_id,
                rng=animation_rng,
            ))
            request_effect = (
                group_cfg.get('_effect_raw')
                if group_cfg.get('_effect_raw') is not None
                else effect
            )
            option_value = (
                group_cfg.get('effect_options')
                if group_cfg.get('_effect_raw') is not None
                else animation_cfg.get('effect_options')
            )
            effect, effect_options = normalize_animation_effect_request(
                request_effect,
                option_value,
                allow_none=False,
                allow_modes=False,
            )
        item_duration = validate_seconds(
            group_cfg.get('duration', duration),
            f'animations.json {effect_path}.duration',
            allow_zero=False,
        )
        trigger_shape = group_cfg.get('trigger_shape')
        raw_trigger = group_cfg.get(
            'trigger',
            animation_cfg.get('trigger', 'after-previous'),
        )
        resolved_trigger = normalize_animation_trigger(raw_trigger)
        if trigger_shape is not None:
            if 'trigger' in group_cfg and resolved_trigger != 'on-click':
                raise ValueError(
                    f'animations.json {effect_path}.trigger_shape requires '
                    'trigger "on-click" when trigger is explicit'
                )
            resolved_trigger = 'on-click'
        if trigger_shape is not None:
            default_delay = 0
        elif resolved_trigger == 'after-previous' and main_sequence_count > 0:
            default_delay = stagger
        else:
            default_delay = 0
        delay_seconds = validate_seconds(
            group_cfg.get('delay', default_delay),
            f'animations.json {effect_path}.delay',
            allow_zero=True,
        )
        delay_ms = animation_seconds_to_milliseconds(
            delay_seconds,
            f'animations.json {effect_path}.delay',
            allow_zero=True,
        )
        inherited_fields = {
            field: animation_cfg[field]
            for field in (
                *ANIMATION_TIMING_OPTION_FIELDS,
                'after_effect',
                'sound',
            )
            if field in animation_cfg
        }
        inherited_fields.update(
            {
                field: group_cfg[field]
                for field in (
                    *ANIMATION_TIMING_OPTION_FIELDS,
                    'after_effect',
                    'sound',
                )
                if field in group_cfg
            }
        )
        target_entry: dict[str, Any] = {
            'shape_id': shape_id,
            'delay_ms': delay_ms,
            'effect': effect,
            'effect_options': effect_options,
            'duration': item_duration,
            'trigger': resolved_trigger,
        }
        if trigger_shape is not None:
            if not isinstance(trigger_shape, str) or not trigger_shape.strip():
                raise ValueError(
                    f'animations.json {effect_path}.trigger_shape must '
                    'be a non-empty group id'
                )
            trigger_shape_id = shape_ids_by_group.get(trigger_shape)
            if trigger_shape_id is None:
                raise ValueError(
                    f'animations.json {effect_path}.trigger_shape '
                    f'references a missing or non-triggerable group: '
                    f'{trigger_shape}'
                )
            if trigger_shape_id == shape_id:
                raise ValueError(
                    f'animations.json {effect_path}.trigger_shape must '
                    'reference a different group'
                )
            target_entry['trigger_shape_id'] = trigger_shape_id
        else:
            main_sequence_count += 1
        target_entry.update(inherited_fields)
        if 'sound' in target_entry:
            target_entry['_sound_path'] = target_entry.pop('sound')
        seq_targets.append(target_entry)

    mixed_count = 0
    if animation == 'mixed':
        mixed_count = sum(1 for _target in seq_targets[1:])
    elif animation == 'auto':
        # 'auto' accumulates a cross-slide offset so the image pool and the
        # unmatched-id fallback rotate as the deck advances. Single-effect
        # semantic matches (title→entrance_fade, chart→entrance_wipe, etc.)
        # are unaffected
        # because they ignore the offset.
        mixed_count = len(seq_targets)
    else:
        mixed_count = sum(
            1
            for seq_idx, mode in enumerate(resolved_group_modes)
            if mode == 'auto' or (mode == 'mixed' and seq_idx > 0)
        )
    return seq_targets, mixed_count
