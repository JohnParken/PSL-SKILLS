"""PPT Master motion core; responsibility-split, with stable original API.

MIT Copyright (c) 2025-2026 Hugo He.
Upstream commit: 7e0df6705de0cd24345210d31f07fcb5a32b6900.
"""
from __future__ import annotations
import math
from pathlib import Path, PureWindowsPath
from typing import Any
from pptx_animations import (
    ANIMATION_AFTER_EFFECTS,
    ANIMATION_RESTARTS,
    ANIMATION_TRIGGERS,
    animation_seconds_to_milliseconds,
    normalize_animation_effect_options,
    normalize_animation_effect_request,
    normalize_animation_trigger,
)
from pptx_transitions import normalize_transition_effect, normalize_transition_effect_request, validate_seconds
from .pptx_package.narration import AUDIO_CONTENT_TYPES

def _animation_parameter_errors(
    value: dict[str, Any],
    label: str,
    *,
    inherited_effect: object,
    sound_is_path: bool = True,
) -> list[str]:
    """Validate PowerPoint effect/timing parameters shared by all scopes."""
    from .animation_config import (
        ANIMATION_AFTER_EFFECTS,
        ANIMATION_RESTARTS,
        AUDIO_CONTENT_TYPES,
        Path,
        animation_seconds_to_milliseconds,
        math,
        normalize_animation_effect_options,
        normalize_animation_effect_request,
    )
    errors: list[str] = []
    effect = value.get('effect', inherited_effect)
    effect_options = value.get('effect_options')
    if effect_options is not None and 'effect' not in value:
        errors.append(
            f'animations.json {label} effect_options requires an explicit effect'
        )
    else:
        try:
            normalize_animation_effect_request(
                effect,
                effect_options,
                allow_none=True,
                allow_modes=True,
            )
        except ValueError as exc:
            errors.append(f'animations.json {label}: {exc}')

    repeat_count = value.get('repeat_count')
    repeat_duration = value.get('repeat_duration')
    if repeat_count is not None:
        if (
            isinstance(repeat_count, bool)
            or not isinstance(repeat_count, (int, float))
            or not math.isfinite(float(repeat_count))
            or float(repeat_count) <= 0
            or float(repeat_count) * 1000 > 4_294_967_295
        ):
            errors.append(
                f'animations.json {label} repeat_count must be a positive number: '
                f'{repeat_count!r}'
            )
    if repeat_duration is not None:
        try:
            animation_seconds_to_milliseconds(
                repeat_duration,
                f'animations.json {label} repeat_duration',
                allow_zero=False,
            )
        except ValueError as exc:
            errors.append(str(exc))
    if repeat_count is not None and repeat_duration is not None:
        errors.append(
            f'animations.json {label} repeat_count and repeat_duration '
            'are mutually exclusive'
        )

    for field in ('auto_reverse', 'rewind'):
        if field in value and not isinstance(value[field], bool):
            errors.append(
                f'animations.json {label} {field} must be a boolean: '
                f'{value[field]!r}'
            )
    ratios: dict[str, float] = {}
    for field in ('accelerate', 'decelerate', 'bounce_end'):
        if field not in value:
            continue
        raw_ratio = value[field]
        if (
            isinstance(raw_ratio, bool)
            or not isinstance(raw_ratio, (int, float))
            or not math.isfinite(float(raw_ratio))
            or not 0 <= float(raw_ratio) <= 1
        ):
            errors.append(
                f'animations.json {label} {field} must be between 0 and 1: '
                f'{raw_ratio!r}'
            )
        else:
            ratios[field] = float(raw_ratio)
    if ratios.get('accelerate', 0) + ratios.get('decelerate', 0) > 1:
        errors.append(
            f'animations.json {label} accelerate + decelerate must not exceed 1'
        )
    if ratios.get('bounce_end', 0) and ratios.get('decelerate', 0):
        errors.append(
            f'animations.json {label} bounce_end and decelerate are '
            'mutually exclusive in PowerPoint'
        )

    if 'restart' in value and value['restart'] not in ANIMATION_RESTARTS:
        errors.append(
            f'animations.json {label} restart must be one of '
            f'{", ".join(ANIMATION_RESTARTS)}: {value["restart"]!r}'
        )

    if 'after_effect' in value:
        after_effect = value['after_effect']
        if isinstance(after_effect, str):
            after_type = after_effect
            after_color = None
        elif isinstance(after_effect, dict):
            unknown = set(after_effect) - {'type', 'color'}
            for field in sorted(unknown):
                errors.append(
                    f'animations.json {label} after_effect has unknown field: {field}'
                )
            after_type = after_effect.get('type', 'none')
            after_color = after_effect.get('color')
        else:
            after_type = None
            after_color = None
            errors.append(
                f'animations.json {label} after_effect must be a string or object'
            )
        if after_type is not None and after_type not in ANIMATION_AFTER_EFFECTS:
            errors.append(
                f'animations.json {label} after_effect.type must be one of '
                f'{", ".join(ANIMATION_AFTER_EFFECTS)}: {after_type!r}'
            )
        elif after_type == 'dim':
            if after_color is None:
                errors.append(
                    f'animations.json {label} dim after_effect requires color'
                )
            else:
                try:
                    normalize_animation_effect_options(
                        'emphasis_change_fill_color',
                        {'color': after_color},
                    )
                except ValueError as exc:
                    errors.append(f'animations.json {label}: {exc}')
        elif after_color is not None:
            errors.append(
                f'animations.json {label} after_effect.color is valid only '
                'with type "dim"'
            )

    if 'sound' in value:
        sound = value['sound']
        if sound_is_path and (
            not isinstance(sound, str) or not sound.strip()
        ):
            errors.append(
                f'animations.json {label} sound must be a non-empty path string'
            )
        elif sound_is_path and Path(sound).suffix.lower() not in AUDIO_CONTENT_TYPES:
            errors.append(
                f'animations.json {label} sound must use .m4a, .mp3, or .wav'
            )
    return errors


def _animation_trigger_error(trigger: object, label: str) -> str | None:
    from .animation_config import (
        ANIMATION_TRIGGERS,
        normalize_animation_trigger,
    )
    if not isinstance(trigger, str):
        return f'animations.json {label} animation trigger must be a string'
    try:
        normalize_animation_trigger(trigger)
    except ValueError:
        valid = ', '.join(ANIMATION_TRIGGERS)
        return (
            f'animations.json {label} has unknown animation trigger: {trigger}; '
            f'valid triggers: {valid}'
        )
    return None


def _unknown_field_errors(
    value: dict[str, Any],
    allowed: frozenset[str],
    label: str,
) -> list[str]:
    return [
        f'animations.json {label} has unknown field: {field}'
        for field in sorted(set(value) - allowed)
    ]


def validate_transition_config(config: dict[str, Any]) -> list[str]:
    """Return fatal transition-sidecar errors that must block export."""
    from .animation_config import (
        _morph_scope_errors,
        _transition_scope_errors,
        _valid_transition_effect,
    )
    errors: list[str] = []
    defaults = config.get('defaults', {})
    default_effect = 'fade'
    if not isinstance(defaults, dict):
        errors.append('animations.json field "defaults" must be an object')
    else:
        errors.extend(
            _transition_scope_errors(
                defaults,
                'defaults',
                inherited_effect='fade',
            )
        )
        transition_defaults = defaults.get('transition', {})
        if isinstance(transition_defaults, dict):
            value = transition_defaults.get('effect', default_effect)
            if isinstance(value, str) and _valid_transition_effect(value):
                default_effect = value

    slides = config.get('slides', {})
    if not isinstance(slides, dict):
        errors.append('animations.json field "slides" must be an object')
        return errors
    for slide_name, slide_cfg in slides.items():
        if not isinstance(slide_cfg, dict):
            errors.append(f'animations.json slide "{slide_name}" must be an object')
            continue
        errors.extend(
            _transition_scope_errors(
                slide_cfg,
                f'slide "{slide_name}"',
                inherited_effect=default_effect,
            )
        )
        errors.extend(_morph_scope_errors(slide_name, slide_cfg))
    return errors


def _transition_scope_errors(
    scope: dict[str, Any],
    label: str,
    *,
    inherited_effect: str,
) -> list[str]:
    from .animation_config import (
        Path,
        PureWindowsPath,
        _unknown_field_errors,
        normalize_transition_effect,
        normalize_transition_effect_request,
        validate_seconds,
    )
    if 'transition' not in scope:
        return []
    transition = scope['transition']
    if not isinstance(transition, dict):
        return [f'animations.json {label} field "transition" must be an object']

    errors = _unknown_field_errors(
        transition,
        frozenset({
            'effect',
            'effect_options',
            'duration',
            'auto_advance',
            'sound',
        }),
        f'{label} transition',
    )
    effect = transition.get('effect', inherited_effect)
    effect_options = transition.get('effect_options')
    if effect_options is not None and 'effect' not in transition:
        errors.append(
            f'animations.json {label} transition effect_options requires '
            'an explicit effect'
        )
    else:
        try:
            normalize_transition_effect_request(effect, effect_options)
        except ValueError as exc:
            errors.append(f'animations.json {label} transition: {exc}')
    try:
        duration_allows_zero = (
            normalize_transition_effect(effect) is None
        )
    except ValueError:
        duration_allows_zero = False
    for field, allow_zero in (
        ('duration', duration_allows_zero),
        ('auto_advance', True),
    ):
        if field not in transition:
            continue
        try:
            validate_seconds(
                transition[field],
                f'animations.json {label} transition {field}',
                allow_zero=allow_zero,
            )
        except ValueError as exc:
            errors.append(str(exc))
    if 'sound' in transition:
        sound = transition['sound']
        if sound is None:
            return errors
        if not isinstance(sound, str) or not sound.strip():
            errors.append(
                f'animations.json {label} transition sound must be a '
                'non-empty project-relative .wav path or null'
            )
        elif Path(sound).is_absolute() or PureWindowsPath(sound).drive:
            errors.append(
                f'animations.json {label} transition sound must be '
                f'project-relative: {sound!r}'
            )
        elif Path(sound).suffix.lower() != '.wav':
            errors.append(
                f'animations.json {label} transition sound must use .wav'
            )
    return errors


