"""PPT Master motion core; responsibility-split, with stable original API.

MIT Copyright (c) 2025-2026 Hugo He.
Upstream commit: 7e0df6705de0cd24345210d31f07fcb5a32b6900.
"""
from __future__ import annotations
import json
import math
from typing import Any
from pptx_animations import ANIMATION_MODES, ANIMATION_TIMING_OPTION_FIELDS, animation_effect_supports_bounce_end, animation_seconds_to_milliseconds, normalize_animation_effect_request

def validate_animation_config_errors(config: dict[str, Any]) -> list[str]:
    """Return fatal object-animation errors that must block export."""
    from .animation_config import (
        _animation_group_errors,
        _animation_scope_errors,
        _resolved_animation_parameter_errors,
        _unknown_field_errors,
    )
    errors = _unknown_field_errors(
        config,
        frozenset({'version', 'defaults', 'slides'}),
        'top level',
    )
    defaults = config.get('defaults', {})
    if not isinstance(defaults, dict):
        errors.append('animations.json field "defaults" must be an object')
    else:
        errors.extend(
            _unknown_field_errors(
                defaults,
                frozenset({'transition', 'animation'}),
                'defaults',
            )
        )
        errors.extend(_animation_scope_errors(defaults, 'defaults'))

    slides = config.get('slides', {})
    if not isinstance(slides, dict):
        errors.append('animations.json field "slides" must be an object')
        return list(dict.fromkeys(errors))

    for slide_name, slide_cfg in slides.items():
        if not isinstance(slide_cfg, dict):
            errors.append(f'animations.json slide "{slide_name}" must be an object')
            continue
        errors.extend(
            _unknown_field_errors(
                slide_cfg,
                frozenset({'transition', 'animation', 'groups', 'morph'}),
                f'slide "{slide_name}"',
            )
        )
        errors.extend(
            _animation_scope_errors(slide_cfg, f'slide "{slide_name}"')
        )
        errors.extend(_animation_group_errors(slide_name, slide_cfg))
    errors.extend(_resolved_animation_parameter_errors(config))
    return list(dict.fromkeys(errors))


def _animation_scope_errors(scope: dict[str, Any], label: str) -> list[str]:
    from .animation_config import (
        ANIMATION_TIMING_OPTION_FIELDS,
        _animation_effect_error,
        _animation_parameter_errors,
        _animation_trigger_error,
        _unknown_field_errors,
        animation_seconds_to_milliseconds,
    )
    if 'animation' not in scope:
        return []
    animation = scope['animation']
    if not isinstance(animation, dict):
        return [f'animations.json {label} field "animation" must be an object']

    errors = _unknown_field_errors(
        animation,
        frozenset({
            'effect',
            'effect_options',
            'duration',
            'stagger',
            'trigger',
            *ANIMATION_TIMING_OPTION_FIELDS,
            'after_effect',
            'sound',
        }),
        f'{label} animation',
    )
    if 'effect' in animation:
        effect_error = _animation_effect_error(animation['effect'], label)
        if effect_error:
            errors.append(effect_error)

    for field, allow_zero in (('duration', False), ('stagger', True)):
        if field not in animation:
            continue
        try:
            animation_seconds_to_milliseconds(
                animation[field],
                f'animations.json {label} animation {field}',
                allow_zero=allow_zero,
            )
        except ValueError as exc:
            errors.append(str(exc))

    if 'trigger' in animation:
        trigger_error = _animation_trigger_error(animation['trigger'], label)
        if trigger_error:
            errors.append(trigger_error)
    errors.extend(
        _animation_parameter_errors(
            animation,
            f'{label} animation',
            inherited_effect='auto',
        )
    )
    return errors


def _animation_group_errors(
    slide_name: object,
    slide_cfg: dict[str, Any],
) -> list[str]:
    from .animation_config import (
        _animation_effect_entry_errors,
        json,
    )
    if 'groups' not in slide_cfg:
        return []
    groups = slide_cfg['groups']
    if not isinstance(groups, dict):
        return [
            f'animations.json slide "{slide_name}" field "groups" must be an object'
        ]

    errors: list[str] = []
    for group_id, group_cfg in groups.items():
        path = (
            f'slides[{json.dumps(str(slide_name), ensure_ascii=False)}]'
            f'.groups[{json.dumps(str(group_id), ensure_ascii=False)}]'
        )
        if not isinstance(group_cfg, dict):
            errors.append(f'animations.json {path} must be an object')
            continue

        if 'effects' not in group_cfg:
            errors.extend(
                _animation_effect_entry_errors(
                    group_cfg,
                    path,
                    require_effect=False,
                    target_group_id=str(group_id),
                )
            )
            continue

        extra_fields = sorted(set(group_cfg) - {'effects'})
        if extra_fields:
            rendered = ', '.join(repr(field) for field in extra_fields)
            errors.append(
                f'animations.json {path} cannot combine "effects" with '
                f'other group-level field(s): {rendered}'
            )
        effects = group_cfg['effects']
        if not isinstance(effects, list):
            errors.append(f'animations.json {path}.effects must be an array')
            continue
        if not effects:
            errors.append(
                f'animations.json {path}.effects must contain at least one effect'
            )
            continue
        for index, effect_cfg in enumerate(effects):
            effect_path = f'{path}.effects[{index}]'
            if not isinstance(effect_cfg, dict):
                errors.append(
                    f'animations.json {effect_path} must be an object'
                )
                continue
            errors.extend(
                _animation_effect_entry_errors(
                    effect_cfg,
                    effect_path,
                    require_effect=True,
                    target_group_id=str(group_id),
                )
            )
    return errors


def _animation_effect_entry_errors(
    effect_cfg: dict[str, Any],
    path: str,
    *,
    require_effect: bool,
    target_group_id: str,
) -> list[str]:
    """Validate one legacy group block or one ``effects[]`` row."""
    from .animation_config import (
        _GROUP_EFFECT_FIELDS,
        _animation_effect_error,
        _animation_parameter_errors,
        _animation_trigger_error,
        _unknown_field_errors,
        animation_seconds_to_milliseconds,
    )
    errors = _unknown_field_errors(
        effect_cfg,
        _GROUP_EFFECT_FIELDS,
        path,
    )
    if require_effect and 'effect' not in effect_cfg:
        errors.append(f'animations.json {path}.effect is required')
    elif 'effect' in effect_cfg:
        effect_error = _animation_effect_error(effect_cfg['effect'], path)
        if effect_error:
            errors.append(effect_error)

    for field, allow_zero in (('duration', False), ('delay', True)):
        if field not in effect_cfg:
            continue
        try:
            animation_seconds_to_milliseconds(
                effect_cfg[field],
                f'animations.json {path}.{field}',
                allow_zero=allow_zero,
            )
        except ValueError as exc:
            errors.append(str(exc))

    if 'order' in effect_cfg:
        order = effect_cfg['order']
        if isinstance(order, bool) or not isinstance(order, int) or order <= 0:
            errors.append(
                f'animations.json {path}.order must be a positive integer: '
                f'{order!r}'
            )

    if 'trigger' in effect_cfg:
        trigger_error = _animation_trigger_error(effect_cfg['trigger'], path)
        if trigger_error:
            errors.append(trigger_error)

    if 'trigger_shape' in effect_cfg:
        trigger_shape = effect_cfg['trigger_shape']
        if not isinstance(trigger_shape, str) or not trigger_shape.strip():
            errors.append(
                f'animations.json {path}.trigger_shape must be a '
                f'non-empty group id: {trigger_shape!r}'
            )
        elif trigger_shape == target_group_id:
            errors.append(
                f'animations.json {path}.trigger_shape must reference '
                'a different group'
            )
        if effect_cfg.get('effect') == 'none':
            errors.append(
                f'animations.json {path}.trigger_shape cannot be used '
                'with effect "none"'
            )
        if (
            'trigger' in effect_cfg
            and effect_cfg.get('trigger') != 'on-click'
        ):
            errors.append(
                f'animations.json {path}.trigger_shape requires '
                'trigger "on-click" when trigger is explicit'
            )

    errors.extend(
        _animation_parameter_errors(
            effect_cfg,
            path,
            inherited_effect='auto',
        )
    )
    return errors


def _bounce_support_error(
    animation: dict[str, Any],
    label: str,
) -> str | None:
    """Return a writer-equivalent bounce support error for one resolved scope."""
    from .animation_config import (
        ANIMATION_MODES,
        animation_effect_supports_bounce_end,
        math,
        normalize_animation_effect_request,
    )
    bounce_end = animation.get('bounce_end')
    if (
        isinstance(bounce_end, bool)
        or not isinstance(bounce_end, (int, float))
        or not math.isfinite(float(bounce_end))
        or float(bounce_end) <= 0
    ):
        return None
    try:
        effect, options = normalize_animation_effect_request(
            animation.get('effect', 'auto'),
            animation.get('effect_options'),
            allow_none=True,
            allow_modes=True,
        )
    except ValueError:
        return None
    if effect is None or effect in ANIMATION_MODES:
        return None
    if animation_effect_supports_bounce_end(effect, options):
        return None
    return (
        f'animations.json {label} effect {effect!r} has no behavior that '
        'supports bounce_end'
    )


def _resolved_animation_parameter_errors(config: dict[str, Any]) -> list[str]:
    """Validate effective animation parameters after sidecar inheritance."""
    from .animation_config import (
        ANIMATION_TIMING_OPTION_FIELDS,
        _INHERITANCE_SENSITIVE_ANIMATION_FIELDS,
        _animation_parameter_errors,
        _bounce_support_error,
        animation_group_effect_entries,
        json,
        resolve_slide_animation_config,
    )
    defaults = config.get('defaults', {})
    default_animation: dict[str, Any] = {'effect': 'none'}
    if isinstance(defaults, dict):
        value = defaults.get('animation', {})
        if isinstance(value, dict):
            default_animation = resolve_slide_animation_config(
                default_animation,
                value,
            )

    errors: list[str] = []
    default_error = _bounce_support_error(default_animation, 'defaults animation')
    if default_error:
        errors.append(default_error)

    slides = config.get('slides', {})
    if not isinstance(slides, dict):
        return errors
    for slide_name, slide_cfg in slides.items():
        if not isinstance(slide_cfg, dict):
            continue
        slide_value = slide_cfg.get('animation', {})
        if not isinstance(slide_value, dict):
            continue
        slide_animation = resolve_slide_animation_config(
            default_animation,
            slide_value,
        )
        if _INHERITANCE_SENSITIVE_ANIMATION_FIELDS & set(slide_value):
            errors.extend(
                _animation_parameter_errors(
                    slide_animation,
                    f'slide "{slide_name}" animation',
                    inherited_effect='auto',
                )
            )
            error = _bounce_support_error(
                slide_animation,
                f'slide "{slide_name}" animation',
            )
            if error:
                errors.append(error)

        groups = slide_cfg.get('groups', {})
        if not isinstance(groups, dict):
            continue
        for group_id, group_cfg in groups.items():
            if not isinstance(group_cfg, dict):
                continue
            path = (
                f'slides[{json.dumps(str(slide_name), ensure_ascii=False)}]'
                f'.groups[{json.dumps(str(group_id), ensure_ascii=False)}]'
            )
            try:
                effect_entries = animation_group_effect_entries(
                    group_cfg,
                    path=path,
                )
            except ValueError:
                continue
            for effect_path, effect_cfg in effect_entries:
                if not (
                    _INHERITANCE_SENSITIVE_ANIMATION_FIELDS
                    & set(effect_cfg)
                ):
                    continue
                inherited_group_animation = {
                    field: slide_animation[field]
                    for field in (
                        'effect',
                        'effect_options',
                        'duration',
                        *ANIMATION_TIMING_OPTION_FIELDS,
                        'after_effect',
                        'sound',
                    )
                    if field in slide_animation
                }
                group_animation = resolve_slide_animation_config(
                    inherited_group_animation,
                    effect_cfg,
                )
                errors.extend(
                    _animation_parameter_errors(
                        group_animation,
                        effect_path,
                        inherited_effect='none',
                    )
                )
                error = _bounce_support_error(
                    group_animation,
                    effect_path,
                )
                if error:
                    errors.append(error)
    return errors


