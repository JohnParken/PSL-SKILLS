"""PPT Master motion core; responsibility-split, with stable original API.

MIT Copyright (c) 2025-2026 Hugo He.
Upstream commit: 7e0df6705de0cd24345210d31f07fcb5a32b6900.
"""
from __future__ import annotations
import math
from dataclasses import field
from typing import Any, Mapping

from pptx_transitions_registry import (
    Any,
    LEGACY_TRANSITION_KEYS,
    MAX_OOXML_MILLISECONDS,
    Mapping,
    NATIVE_TRANSITIONS,
    NATIVE_TRANSITION_KEYS,
    TRANSITION_ALIASES,
    TRANSITION_ALIAS_OPTIONS,
    field,
    math,
)

def _qn(namespace: str, tag: str) -> str:
    return f"{{{namespace}}}{tag}"


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _namespace_name(tag: str) -> str | None:
    if tag.startswith("{") and "}" in tag:
        return tag[1:].split("}", 1)[0]
    return None


def _value_repr(value: object) -> str:
    try:
        return repr(value)
    except Exception:
        return f"<{type(value).__name__}>"


def validate_seconds(
    value: object,
    field: str,
    *,
    allow_zero: bool,
) -> float:
    """Return a finite seconds value or raise a field-specific error."""
    from pptx_transitions import (
        _value_repr,
        math,
    )
    display_value = _value_repr(value)
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a finite number, not {display_value}")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(
            f"{field} must be a finite number: {display_value}"
        ) from exc
    if not math.isfinite(number):
        raise ValueError(f"{field} must be finite: {display_value}")
    if allow_zero:
        if number < 0:
            raise ValueError(f"{field} must be non-negative: {display_value}")
    elif number <= 0:
        raise ValueError(f"{field} must be greater than zero: {display_value}")
    return number


def normalize_transition_effect(effect: object, *, allow_none: bool = True) -> str | None:
    """Return one canonical PowerPoint transition or explicit no-effect."""
    from pptx_transitions import (
        LEGACY_TRANSITION_KEYS,
        NATIVE_TRANSITIONS,
        NATIVE_TRANSITION_KEYS,
        TRANSITION_ALIASES,
    )
    if effect is None or effect == "none":
        if allow_none:
            return None
        raise ValueError("transition effect is required")
    if not isinstance(effect, str):
        raise ValueError(f"transition effect must be a string: {effect!r}")
    if effect in TRANSITION_ALIASES:
        return TRANSITION_ALIASES[effect]
    if effect in NATIVE_TRANSITIONS:
        return effect
    valid = ", ".join((*NATIVE_TRANSITION_KEYS, *LEGACY_TRANSITION_KEYS))
    raise ValueError(
        f"unknown transition effect {effect!r}; valid effects: {valid}, none"
    )


def normalize_transition_effect_options(
    effect: str,
    options: object = None,
) -> dict[str, object]:
    """Validate PowerPoint Effect Options for one native transition."""
    from pptx_transitions import (
        Mapping,
        NATIVE_TRANSITIONS,
    )
    if effect not in NATIVE_TRANSITIONS:
        if options in (None, {}):
            return {}
        raise ValueError(
            "transition effect_options require one explicit native effect; "
            f"found {effect!r}"
        )
    if options is None:
        options = {}
    if not isinstance(options, Mapping):
        raise ValueError(
            f"transition effect_options must be an object: {options!r}"
        )

    option_specs = NATIVE_TRANSITIONS[effect]["effectOptions"]
    unknown = set(options) - set(option_specs)
    if unknown:
        unsupported = ", ".join(sorted(unknown))
        supported = ", ".join(option_specs) or "(none)"
        raise ValueError(
            f"transition effect {effect!r} does not support effect option(s): "
            f"{unsupported}; supported options: {supported}"
        )

    normalized: dict[str, object] = {}
    for name, value in options.items():
        spec = option_specs[name]
        field = f"transition effect_options.{name}"
        if spec["type"] == "enum":
            if not isinstance(value, str) or value not in spec["values"]:
                valid = ", ".join(spec["values"])
                raise ValueError(
                    f"{field} for {effect!r} must be one of {valid}: {value!r}"
                )
            normalized[name] = value
        elif spec["type"] == "boolean":
            if not isinstance(value, bool):
                raise ValueError(f"{field} must be a boolean: {value!r}")
            normalized[name] = value
        else:
            raise AssertionError(
                f"unhandled transition option type: {spec['type']!r}"
            )
    return normalized


def normalize_transition_effect_request(
    effect: object,
    options: object = None,
    *,
    allow_none: bool = True,
) -> tuple[str | None, dict[str, object]]:
    """Normalize one native effect plus options and legacy semantic aliases."""
    from pptx_transitions import (
        Mapping,
        TRANSITION_ALIAS_OPTIONS,
        normalize_transition_effect,
        normalize_transition_effect_options,
    )
    raw_effect = effect
    canonical = normalize_transition_effect(effect, allow_none=allow_none)
    alias_options = (
        TRANSITION_ALIAS_OPTIONS.get(raw_effect, {})
        if isinstance(raw_effect, str)
        else {}
    )
    explicit_options: Mapping[str, object]
    if options is None:
        explicit_options = {}
    elif isinstance(options, Mapping):
        explicit_options = options
    else:
        raise ValueError(
            f"transition effect_options must be an object: {options!r}"
        )
    for name, alias_value in alias_options.items():
        if name in explicit_options and explicit_options[name] != alias_value:
            raise ValueError(
                f"legacy transition effect {raw_effect!r} implies "
                f"effect_options.{name}={alias_value!r}, which conflicts with "
                f"{explicit_options[name]!r}"
            )
    merged = {**alias_options, **explicit_options}
    if canonical is None:
        if merged:
            raise ValueError(
                "transition effect_options require one explicit native effect; "
                "found 'none'"
            )
        return None, {}
    return canonical, normalize_transition_effect_options(canonical, merged)


def describe_transition_effect(effect: object) -> dict[str, Any]:
    """Return the author-facing parameter contract for one transition."""
    from pptx_transitions import (
        NATIVE_TRANSITIONS,
        TRANSITION_ALIASES,
        normalize_transition_effect_request,
    )
    canonical, implied_options = normalize_transition_effect_request(
        effect,
        allow_none=False,
    )
    option_contract: dict[str, Any] = {}
    for name, raw_spec in NATIVE_TRANSITIONS[canonical]["effectOptions"].items():
        option_contract[name] = {
            "type": raw_spec["type"],
            "default": raw_spec["default"],
            "values": list(raw_spec["values"]),
        }
    return {
        "input": effect,
        "effect": canonical,
        "name": NATIVE_TRANSITIONS[canonical]["name"],
        "category": NATIVE_TRANSITIONS[canonical]["category"],
        "compatibility_alias": (
            effect
            if isinstance(effect, str) and effect in TRANSITION_ALIASES
            else None
        ),
        "implied_effect_options": implied_options,
        "effect_options": option_contract,
        "timing": {
            "duration": "positive seconds",
            "auto_advance": "non-negative seconds",
        },
        "sound": "project-relative .wav path or null in animations.json",
    }


def _seconds_to_ms(value: object, field: str, *, allow_zero: bool) -> int:
    from pptx_transitions import (
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
        raise ValueError(
            f"{field} exceeds the OOXML millisecond limit: {value!r}"
        )
    milliseconds = int(raw_milliseconds)
    return milliseconds if allow_zero else max(1, milliseconds)


