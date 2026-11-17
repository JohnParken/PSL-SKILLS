"""PPT Master motion core; responsibility-split, with stable original API.

MIT Copyright (c) 2025-2026 Hugo He.
Upstream commit: 7e0df6705de0cd24345210d31f07fcb5a32b6900.
"""
from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from pptx_transitions import TransitionSummary
from typing import Any, Mapping

from pptx_transitions_registry import Any, MC_NS, Mapping, NATIVE_TRANSITIONS, NATIVE_TRANSITION_KEYS, P14_NS, PML_NS, RELATIONSHIPS_NS

def _transition_elements(carrier: Any) -> list[Any]:
    from pptx_transitions import (
        PML_NS,
        _qn,
    )
    if carrier.tag == _qn(PML_NS, "transition"):
        return [carrier]
    return [
        element
        for element in carrier.iter()
        if element.tag == _qn(PML_NS, "transition")
    ]


def transition_carriers(slide_root: Any) -> list[Any]:
    """Return root-level direct or AlternateContent transition carriers."""
    from pptx_transitions import (
        MC_NS,
        PML_NS,
        _qn,
        _transition_elements,
    )
    carriers: list[Any] = []
    for child in list(slide_root):
        if child.tag == _qn(PML_NS, "transition"):
            carriers.append(child)
            continue
        if child.tag != _qn(MC_NS, "AlternateContent"):
            continue
        if _transition_elements(child):
            carriers.append(child)
    return carriers


def _primary_and_fallback(carrier: Any) -> tuple[Any | None, Any | None]:
    from pptx_transitions import (
        MC_NS,
        PML_NS,
        _qn,
        _transition_elements,
    )
    if carrier.tag == _qn(PML_NS, "transition"):
        return carrier, None

    primary = None
    fallback = None
    for child in list(carrier):
        transitions = _transition_elements(child)
        if not transitions:
            continue
        if child.tag == _qn(MC_NS, "Choice") and primary is None:
            primary = transitions[0]
        elif child.tag == _qn(MC_NS, "Fallback") and fallback is None:
            fallback = transitions[0]
    if primary is None:
        transitions = _transition_elements(carrier)
        primary = transitions[0] if transitions else None
    return primary, fallback


def _effect_identity(
    transition: Any | None,
) -> tuple[str | None, str | None, dict[str, str]]:
    from pptx_transitions import (
        PML_NS,
        _local_name,
        _namespace_name,
        _qn,
    )
    if transition is None:
        return None, None, {}
    for child in list(transition):
        if child.tag == _qn(PML_NS, "sndAc"):
            continue
        return (
            _local_name(child.tag),
            _namespace_name(child.tag),
            {str(name): str(value) for name, value in child.attrib.items()},
        )
    return None, None, {}


def _sound_identity(
    transition: Any | None,
) -> tuple[str | None, str | None]:
    """Return one embedded transition sound relationship and display name."""
    from pptx_transitions import (
        PML_NS,
        RELATIONSHIPS_NS,
        _qn,
    )
    if transition is None:
        return None, None
    sound_action = next(
        (
            child
            for child in list(transition)
            if child.tag == _qn(PML_NS, "sndAc")
        ),
        None,
    )
    if sound_action is None:
        return None, None
    start_sound = next(
        (
            child
            for child in list(sound_action)
            if child.tag == _qn(PML_NS, "stSnd")
        ),
        None,
    )
    if start_sound is None:
        return None, None
    sound = next(
        (
            child
            for child in list(start_sound)
            if child.tag == _qn(PML_NS, "snd")
        ),
        None,
    )
    if sound is None:
        return None, None
    return sound.get(_qn(RELATIONSHIPS_NS, "embed")), sound.get("name")


def _effective_transition_options(
    effect: str,
    options: Mapping[str, object] | None = None,
) -> dict[str, object]:
    from pptx_transitions import (
        NATIVE_TRANSITIONS,
        normalize_transition_effect_options,
    )
    effective = {
        name: spec["default"]
        for name, spec in NATIVE_TRANSITIONS[effect]["effectOptions"].items()
    }
    effective.update(normalize_transition_effect_options(effect, options))
    return effective


def _transition_option_combinations(effect: str) -> list[dict[str, object]]:
    from pptx_transitions import (
        NATIVE_TRANSITIONS,
    )
    combinations: list[dict[str, object]] = [{}]
    for name, spec in NATIVE_TRANSITIONS[effect]["effectOptions"].items():
        combinations = [
            {**combination, name: value}
            for combination in combinations
            for value in spec["values"]
        ]
    return combinations


def _identify_native_transition(
    element: str | None,
    namespace: str | None,
    attributes: Mapping[str, str],
) -> tuple[str | None, dict[str, object]]:
    from pptx_transitions import (
        NATIVE_TRANSITION_KEYS,
        _effect_spec,
        _transition_option_combinations,
    )
    if element is None or namespace is None:
        return None, {}
    for effect in NATIVE_TRANSITION_KEYS:
        for options in _transition_option_combinations(effect):
            _prefix, expected_namespace, expected_element, expected_attrs, _fallback = (
                _effect_spec(effect, options)
            )
            if (
                namespace == expected_namespace
                and element == expected_element
                and dict(attributes) == {
                    str(name): str(value)
                    for name, value in expected_attrs.items()
                }
            ):
                return effect, options
    return None, {}


def _int_attribute(element: Any | None, *names: str) -> int | None:
    if element is None:
        return None
    for name in names:
        raw = element.get(name)
        if raw is None:
            continue
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None
    return None


def _bool_attribute(element: Any, name: str, default: bool) -> bool:
    raw = element.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() not in {"0", "false", "off", "no"}


def read_slide_transition(slide_root: Any) -> TransitionSummary:
    """Read the primary transition without mistaking fallback for success."""
    from pptx_transitions import (
        MC_NS,
        P14_NS,
        TransitionSummary,
        _bool_attribute,
        _effect_identity,
        _identify_native_transition,
        _int_attribute,
        _primary_and_fallback,
        _qn,
        _sound_identity,
        transition_carriers,
    )
    carriers = transition_carriers(slide_root)
    if not carriers:
        return TransitionSummary(carrier="none", logical_count=0)

    carrier = carriers[0]
    primary, fallback = _primary_and_fallback(carrier)
    effect, effect_namespace, effect_attributes = _effect_identity(primary)
    fallback_effect, fallback_namespace, _fallback_attributes = _effect_identity(
        fallback
    )
    sound_relationship_id, sound_name = _sound_identity(primary)
    fallback_sound_relationship_id, fallback_sound_name = _sound_identity(
        fallback
    )
    canonical_effect, effect_options = _identify_native_transition(
        effect,
        effect_namespace,
        effect_attributes,
    )
    carrier_name = (
        "alternate-content"
        if carrier.tag == _qn(MC_NS, "AlternateContent")
        else "direct"
    )
    duration_ms = _int_attribute(
        primary,
        _qn(P14_NS, "dur"),
        "dur",
    )
    advance_after_ms = _int_attribute(primary, "advTm")
    return TransitionSummary(
        carrier=carrier_name if len(carriers) == 1 else "multiple",
        logical_count=len(carriers),
        effect=effect,
        effect_namespace=effect_namespace,
        effect_attributes=effect_attributes,
        canonical_effect=canonical_effect,
        effect_options=effect_options,
        fallback_effect=fallback_effect,
        fallback_effect_namespace=fallback_namespace,
        duration_ms=duration_ms,
        speed=primary.get("spd") if primary is not None else None,
        advance_on_click=(
            _bool_attribute(primary, "advClick", True)
            if primary is not None
            else None
        ),
        advance_after_ms=advance_after_ms,
        sound_relationship_id=sound_relationship_id,
        sound_name=sound_name,
        fallback_sound_relationship_id=fallback_sound_relationship_id,
        fallback_sound_name=fallback_sound_name,
    )


