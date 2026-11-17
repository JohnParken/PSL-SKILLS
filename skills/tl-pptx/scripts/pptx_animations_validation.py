"""PPT Master motion core; responsibility-split, with stable original API.

MIT Copyright (c) 2025-2026 Hugo He.
Upstream commit: 7e0df6705de0cd24345210d31f07fcb5a32b6900.
"""
from __future__ import annotations
import math
import re
from typing import Mapping
from pptx_transitions import MAX_OOXML_MILLISECONDS, MAX_OOXML_UNSIGNED_INT, validate_seconds

from pptx_animations_registry import (
    ANIMATIONS,
    ANIMATION_AFTER_EFFECTS,
    ANIMATION_ALIASES,
    ANIMATION_ALIAS_OPTIONS,
    ANIMATION_MODES,
    ANIMATION_TRIGGERS,
    MAX_OOXML_MILLISECONDS,
    MAX_OOXML_UNSIGNED_INT,
    Mapping,
    NATIVE_ANIMATIONS,
    _NON_CONCRETE_FONT_NAMES,
    math,
    re,
    validate_seconds,
)

def _qn(namespace: str, tag: str) -> str:
    return f'{{{namespace}}}{tag}'


def _local_name(tag: str) -> str:
    return tag.rsplit('}', 1)[-1]


def normalize_animation_effect(
    effect: object,
    *,
    allow_none: bool = True,
    allow_modes: bool = True,
) -> str | None:
    """Return a supported effect/mode without silently substituting another."""
    from pptx_animations import (
        ANIMATIONS,
        ANIMATION_ALIASES,
        ANIMATION_MODES,
        NATIVE_ANIMATIONS,
    )
    if effect is None or effect == 'none':
        if allow_none:
            return None
        raise ValueError('animation effect is required')
    if not isinstance(effect, str):
        raise ValueError(f'animation effect must be a string: {effect!r}')
    if effect in ANIMATION_ALIASES:
        return ANIMATION_ALIASES[effect]
    if effect in NATIVE_ANIMATIONS:
        return effect
    if allow_modes and effect in ANIMATION_MODES:
        return effect
    valid = list(ANIMATIONS)
    if allow_modes:
        valid.extend(ANIMATION_MODES)
    if allow_none:
        valid.append('none')
    raise ValueError(
        f'unknown animation effect {effect!r}; valid effects: {", ".join(valid)}'
    )


def _finite_number(value: object, field: str) -> float:
    from pptx_animations import (
        math,
    )
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f'{field} must be a finite number: {value!r}')
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f'{field} must be a finite number: {value!r}')
    return number


def _normalize_animation_color(value: object, field: str) -> str:
    from pptx_animations import (
        re,
    )
    if not isinstance(value, str):
        raise ValueError(
            f'{field} must be #RRGGBB or theme:<scheme-color>: {value!r}'
        )
    if re.fullmatch(r'#[0-9A-Fa-f]{6}', value):
        return value.upper()
    if re.fullmatch(
        r'theme:(?:dk1|lt1|dk2|lt2|tx1|tx2|bg1|bg2|accent[1-6]|'
        r'hlink|folHlink)',
        value,
    ):
        return value
    raise ValueError(
        f'{field} must be #RRGGBB or theme:<scheme-color>: {value!r}'
    )


def _normalize_powerpoint_font_name(value: object, field: str) -> str:
    """Return one concrete PowerPoint font name without checking installation."""
    from pptx_animations import (
        _NON_CONCRETE_FONT_NAMES,
    )
    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            f'{field} must be one concrete PowerPoint font name: {value!r}'
        )
    normalized = value.strip()
    if len(normalized) > 255:
        raise ValueError(f'{field} exceeds 255 characters')
    if ',' in normalized:
        raise ValueError(
            f'{field} must be one concrete PowerPoint font name, '
            f'not a CSS font stack: {value!r}'
        )
    if normalized.casefold() in _NON_CONCRETE_FONT_NAMES:
        raise ValueError(
            f'{field} must be one concrete PowerPoint font name, '
            f'not a generic family or CSS-wide keyword: {value!r}'
        )
    return normalized


def normalize_animation_effect_options(
    effect: str,
    options: object = None,
) -> dict[str, object]:
    """Validate effect-specific PowerPoint EffectParameters values."""
    from pptx_animations import (
        Mapping,
        NATIVE_ANIMATIONS,
        _finite_number,
        _normalize_animation_color,
        _normalize_powerpoint_font_name,
        re,
    )
    if effect not in NATIVE_ANIMATIONS:
        if options in (None, {}):
            return {}
        raise ValueError(
            'animation effect_options require one explicit canonical effect; '
            f'found {effect!r}'
        )
    if options is None:
        options = {}
    if not isinstance(options, Mapping):
        raise ValueError(f'animation effect_options must be an object: {options!r}')

    option_specs = NATIVE_ANIMATIONS[effect]['effectOptions']
    unknown = set(options) - set(option_specs)
    if unknown:
        unsupported = ', '.join(sorted(unknown))
        supported = ', '.join(option_specs) or '(none)'
        raise ValueError(
            f'animation effect {effect!r} does not support effect option(s): '
            f'{unsupported}; supported options: {supported}'
        )
    missing_required = sorted(
        name
        for name, spec in option_specs.items()
        if spec.get('required') and name not in options
    )
    if missing_required:
        required_fields = ', '.join(
            f'effect_options.{name}' for name in missing_required
        )
        raise ValueError(
            f'animation effect {effect!r} requires {required_fields}'
        )

    normalized: dict[str, object] = {}
    for name, value in options.items():
        spec = option_specs[name]
        option_type = spec['type']
        field = f'animation effect_options.{name}'
        if option_type == 'enum':
            key = str(value)
            if isinstance(value, bool) or key not in spec['values']:
                valid = ', '.join(spec['values'])
                raise ValueError(
                    f'{field} for {effect!r} must be one of {valid}: {value!r}'
                )
            normalized[name] = (
                int(key)
                if name == 'amount' and re.fullmatch(r'\d+', key)
                else key
            )
        elif option_type == 'number':
            number = _finite_number(value, field)
            minimum = spec.get('minimum')
            maximum = spec.get('maximum')
            if minimum is not None and number < float(minimum):
                raise ValueError(
                    f'{field} for {effect!r} must be at least {minimum}: {value!r}'
                )
            if maximum is not None and number > float(maximum):
                raise ValueError(
                    f'{field} for {effect!r} must be at most {maximum}: {value!r}'
                )
            normalized[name] = number
        elif option_type == 'string':
            if name == 'font_name':
                normalized[name] = _normalize_powerpoint_font_name(value, field)
            else:
                if not isinstance(value, str) or not value.strip():
                    raise ValueError(
                        f'{field} must be a non-empty string: {value!r}'
                    )
                normalized_value = value.strip()
                if len(normalized_value) > 255:
                    raise ValueError(f'{field} exceeds 255 characters')
                normalized[name] = normalized_value
        elif option_type == 'boolean':
            if not isinstance(value, bool):
                raise ValueError(f'{field} must be a boolean: {value!r}')
            normalized[name] = value
        elif option_type == 'color':
            normalized[name] = _normalize_animation_color(value, field)
        else:
            raise AssertionError(f'unhandled animation effect option type: {option_type}')
    return normalized


def normalize_animation_effect_request(
    effect: object,
    options: object = None,
    *,
    allow_none: bool = True,
    allow_modes: bool = True,
) -> tuple[str | None, dict[str, object]]:
    """Normalize one effect plus options, including legacy semantic aliases."""
    from pptx_animations import (
        ANIMATION_ALIAS_OPTIONS,
        ANIMATION_MODES,
        Mapping,
        normalize_animation_effect,
        normalize_animation_effect_options,
    )
    raw_effect = effect
    canonical = normalize_animation_effect(
        effect,
        allow_none=allow_none,
        allow_modes=allow_modes,
    )
    alias_options = (
        ANIMATION_ALIAS_OPTIONS.get(raw_effect, {})
        if isinstance(raw_effect, str)
        else {}
    )
    explicit_options: Mapping[str, object]
    if options is None:
        explicit_options = {}
    elif isinstance(options, Mapping):
        explicit_options = options
    else:
        raise ValueError(f'animation effect_options must be an object: {options!r}')
    for name, alias_value in alias_options.items():
        if name in explicit_options and explicit_options[name] != alias_value:
            raise ValueError(
                f'legacy animation effect {raw_effect!r} implies '
                f'effect_options.{name}={alias_value!r}, which conflicts with '
                f'{explicit_options[name]!r}'
            )
    merged = {**alias_options, **explicit_options}
    if canonical is None or canonical in ANIMATION_MODES:
        if merged:
            raise ValueError(
                'animation effect_options require one explicit canonical effect; '
                f'found {canonical or "none"!r}'
            )
        return canonical, {}
    return canonical, normalize_animation_effect_options(canonical, merged)


def normalize_animation_trigger(trigger: object) -> str:
    """Return a supported PowerPoint Start mode or raise a precise error."""
    from pptx_animations import (
        ANIMATION_TRIGGERS,
    )
    if not isinstance(trigger, str):
        raise ValueError(f'animation trigger must be a string: {trigger!r}')
    if trigger not in ANIMATION_TRIGGERS:
        raise ValueError(
            f'unknown animation trigger {trigger!r}; valid triggers: '
            f'{", ".join(ANIMATION_TRIGGERS)}'
        )
    return trigger


def _seconds_to_ms(value: object, field: str, *, allow_zero: bool) -> int:
    from pptx_animations import (
        MAX_OOXML_MILLISECONDS,
        math,
        validate_seconds,
    )
    seconds = validate_seconds(value, field, allow_zero=allow_zero)
    raw_milliseconds = seconds * 1000
    if (
        not math.isfinite(raw_milliseconds)
        or raw_milliseconds > MAX_OOXML_MILLISECONDS
    ):
        raise ValueError(f'{field} exceeds the OOXML millisecond limit: {value!r}')
    milliseconds = int(raw_milliseconds)
    return milliseconds if allow_zero else max(1, milliseconds)


def animation_seconds_to_milliseconds(
    value: object,
    field: str,
    *,
    allow_zero: bool,
) -> int:
    """Convert validated animation seconds to the OOXML millisecond range."""
    from pptx_animations import (
        _seconds_to_ms,
    )
    return _seconds_to_ms(value, field, allow_zero=allow_zero)


def _positive_shape_id(value: object, field: str = 'animation shape_id') -> int:
    from pptx_animations import (
        MAX_OOXML_UNSIGNED_INT,
        re,
    )
    if isinstance(value, bool):
        raise ValueError(f'{field} must be a positive integer: {value!r}')
    if isinstance(value, int):
        shape_id = value
    elif isinstance(value, str) and re.fullmatch(r'[1-9]\d*', value):
        shape_id = int(value)
    else:
        raise ValueError(f'{field} must be a positive integer: {value!r}')
    if shape_id <= 0 or shape_id > MAX_OOXML_UNSIGNED_INT:
        raise ValueError(f'{field} must be a positive integer: {value!r}')
    return shape_id


def _non_negative_milliseconds(value: object, field: str) -> int:
    from pptx_animations import (
        MAX_OOXML_MILLISECONDS,
        re,
    )
    if isinstance(value, bool):
        raise ValueError(f'{field} must be a non-negative integer: {value!r}')
    if isinstance(value, int):
        milliseconds = value
    elif isinstance(value, str) and re.fullmatch(r'\d+', value):
        milliseconds = int(value)
    else:
        raise ValueError(f'{field} must be a non-negative integer: {value!r}')
    if milliseconds < 0 or milliseconds > MAX_OOXML_MILLISECONDS:
        raise ValueError(
            f'{field} must be between 0 and {MAX_OOXML_MILLISECONDS}: {value!r}'
        )
    return milliseconds


def _optional_bool(value: object, field: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f'{field} must be a boolean: {value!r}')
    return value


def _optional_ratio(value: object, field: str) -> float:
    from pptx_animations import (
        _finite_number,
    )
    ratio = _finite_number(value, field)
    if ratio < 0 or ratio > 1:
        raise ValueError(f'{field} must be between 0 and 1: {value!r}')
    return ratio


def _normalize_repeat_count(value: object) -> float:
    from pptx_animations import (
        MAX_OOXML_UNSIGNED_INT,
        _finite_number,
    )
    count = _finite_number(value, 'animation repeat_count')
    if count <= 0 or count * 1000 > MAX_OOXML_UNSIGNED_INT:
        raise ValueError(
            'animation repeat_count must be positive and fit the OOXML range: '
            f'{value!r}'
        )
    return count


def _normalize_after_effect(value: object) -> tuple[str, str | None]:
    from pptx_animations import (
        ANIMATION_AFTER_EFFECTS,
        Mapping,
        _normalize_animation_color,
    )
    if value is None:
        return 'none', None
    if isinstance(value, str):
        effect_type = value
        color = None
    elif isinstance(value, Mapping):
        unknown = set(value) - {'type', 'color'}
        if unknown:
            raise ValueError(
                'animation after_effect has unknown field(s): '
                + ', '.join(sorted(unknown))
            )
        effect_type = value.get('type', 'none')
        color = value.get('color')
    else:
        raise ValueError(
            f'animation after_effect must be a string or object: {value!r}'
        )
    if effect_type not in ANIMATION_AFTER_EFFECTS:
        raise ValueError(
            f'animation after_effect.type must be one of '
            f'{", ".join(ANIMATION_AFTER_EFFECTS)}: {effect_type!r}'
        )
    if effect_type == 'dim':
        if color is None:
            raise ValueError('animation dim after_effect requires color')
        return effect_type, _normalize_animation_color(
            color,
            'animation after_effect.color',
        )
    if color is not None:
        raise ValueError(
            f'animation after_effect.color is valid only with type "dim": {color!r}'
        )
    return effect_type, None


def _normalize_sound(value: object) -> tuple[str | None, str | None]:
    from pptx_animations import (
        Mapping,
        re,
    )
    if value is None:
        return None, None
    if not isinstance(value, Mapping):
        raise ValueError(
            'low-level animation sound must be an object with '
            'relationship_id and name'
        )
    unknown = set(value) - {'relationship_id', 'name'}
    if unknown:
        raise ValueError(
            'low-level animation sound has unknown field(s): '
            + ', '.join(sorted(unknown))
        )
    relationship_id = value.get('relationship_id')
    name = value.get('name')
    if not isinstance(relationship_id, str) or not re.fullmatch(
        r'rId[1-9]\d*',
        relationship_id,
    ):
        raise ValueError(
            'low-level animation sound relationship_id must match rIdN: '
            f'{relationship_id!r}'
        )
    if not isinstance(name, str) or not name.strip():
        raise ValueError(
            f'low-level animation sound name must be non-empty: {name!r}'
        )
    return relationship_id, name


