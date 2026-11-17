"""PPT Master motion core; responsibility-split, with stable original API.

MIT Copyright (c) 2025-2026 Hugo He.
Upstream commit: 7e0df6705de0cd24345210d31f07fcb5a32b6900.
"""
from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from pptx_transitions import EnterUpdate, AdvanceUpdate, TransitionSummary
from typing import Any, Mapping

from pptx_transitions_registry import Any, Mapping, PML_NS

def _captured_advance(carriers: list[Any]) -> tuple[bool | None, float | None]:
    from pptx_transitions import (
        _bool_attribute,
        _int_attribute,
        _primary_and_fallback,
    )
    if not carriers:
        return None, None
    primary, _fallback = _primary_and_fallback(carriers[0])
    if primary is None:
        return None, None
    click = (
        _bool_attribute(primary, "advClick", True)
        if primary.get("advClick") is not None
        else None
    )
    advance_ms = _int_attribute(primary, "advTm")
    after = advance_ms / 1000 if advance_ms is not None else None
    return click, after


def _resolve_advance(
    update: AdvanceUpdate,
    *,
    preserved_click: bool | None,
    preserved_after: float | None,
) -> tuple[bool | None, float | None]:
    from pptx_transitions import (
        validate_seconds,
    )
    valid_modes = {"preserve", "click", "after", "both", "narration"}
    if update.mode not in valid_modes:
        raise ValueError(
            f"unknown slide advance mode {update.mode!r}; "
            f"valid modes: {', '.join(sorted(valid_modes))}"
        )
    if update.mode == "preserve":
        return preserved_click, preserved_after
    if update.mode == "click":
        return True, None
    if update.after is None:
        raise ValueError(f"slide advance mode {update.mode!r} requires 'after'")
    after = validate_seconds(
        update.after,
        "slide advance after",
        allow_zero=True,
    )
    if update.mode == "both":
        return True, after
    return False, after


def _set_advance_attributes(
    transition: Any,
    *,
    advance_on_click: bool | None,
    advance_after: float | None,
) -> None:
    from pptx_transitions import (
        _seconds_to_ms,
    )
    if advance_on_click is None:
        transition.attrib.pop("advClick", None)
    else:
        transition.set("advClick", "1" if advance_on_click else "0")
    if advance_after is None:
        transition.attrib.pop("advTm", None)
    else:
        transition.set(
            "advTm",
            str(
                _seconds_to_ms(
                    advance_after,
                    "slide advance after",
                    allow_zero=True,
                )
            ),
        )


def _insert_transition_carrier(slide_root: Any, carrier: Any) -> None:
    from pptx_transitions import (
        PML_NS,
        _qn,
    )
    children = list(slide_root)
    for index, child in enumerate(children):
        if child.tag in {
            _qn(PML_NS, "timing"),
            _qn(PML_NS, "extLst"),
        }:
            slide_root.insert(index, carrier)
            return

    clr_map = None
    for child in children:
        if child.tag == _qn(PML_NS, "clrMapOvr"):
            clr_map = child
    if clr_map is not None:
        slide_root.insert(list(slide_root).index(clr_map) + 1, carrier)
        return
    slide_root.append(carrier)


def _apply_slide_motion_unchecked(
    slide_root: Any,
    *,
    enter: EnterUpdate,
    advance: AdvanceUpdate,
) -> bool:
    """Apply one resolved enter/advance update and return whether advTm remains."""
    from pptx_transitions import (
        _build_transition_element,
        _captured_advance,
        _insert_transition_carrier,
        _resolve_advance,
        _set_advance_attributes,
        _transition_elements,
        normalize_transition_effect_request,
        transition_carriers,
        validate_seconds,
    )
    valid_policies = {"preserve", "replace", "none"}
    if enter.policy not in valid_policies:
        raise ValueError(
            f"unknown transition enter policy {enter.policy!r}; "
            f"valid policies: {', '.join(sorted(valid_policies))}"
        )
    if (
        enter.policy != "replace"
        and enter.effect_options not in (None, {})
    ):
        raise ValueError(
            "transition effect_options require enter policy 'replace'"
        )

    carriers = transition_carriers(slide_root)
    if len(carriers) > 1:
        raise ValueError(
            "slide contains multiple logical transition carriers; "
            "refusing to preserve or replace an ambiguous source"
        )

    preserved_click, preserved_after = _captured_advance(carriers)
    advance_on_click, advance_after = _resolve_advance(
        advance,
        preserved_click=preserved_click,
        preserved_after=preserved_after,
    )

    if enter.policy == "preserve":
        if advance.mode == "preserve":
            return any(
                transition.get("advTm") is not None
                for carrier in carriers
                for transition in _transition_elements(carrier)
            )
        if carriers:
            for transition in _transition_elements(carriers[0]):
                _set_advance_attributes(
                    transition,
                    advance_on_click=advance_on_click,
                    advance_after=advance_after,
                )
            return advance_after is not None

        transition = _build_transition_element(
            slide_root,
            effect=None,
            duration=enter.duration,
            advance_after=advance_after,
            advance_on_click=advance_on_click,
            effect_options=None,
        )
        if transition is not None:
            _insert_transition_carrier(slide_root, transition)
        return advance_after is not None

    effect, effect_options = (
        normalize_transition_effect_request(
            enter.effect,
            enter.effect_options,
            allow_none=False,
        )
        if enter.policy == "replace"
        else (None, {})
    )
    duration = validate_seconds(
        enter.duration,
        "transition duration",
        allow_zero=False,
    )

    if carriers:
        slide_root.remove(carriers[0])

    transition = _build_transition_element(
        slide_root,
        effect=effect,
        duration=duration,
        advance_after=advance_after,
        advance_on_click=advance_on_click,
        effect_options=effect_options,
    )
    if transition is not None:
        _insert_transition_carrier(slide_root, transition)
    return advance_after is not None


def _visual_identity(summary: TransitionSummary) -> tuple[Any, ...]:
    return (
        summary.carrier,
        summary.logical_count,
        summary.effect,
        summary.effect_namespace,
        tuple(sorted(summary.effect_attributes.items())),
        summary.canonical_effect,
        tuple(sorted(summary.effect_options.items())),
        summary.fallback_effect,
        summary.fallback_effect_namespace,
        summary.duration_ms,
        summary.speed,
        summary.sound_relationship_id,
        summary.sound_name,
        summary.fallback_sound_relationship_id,
        summary.fallback_sound_name,
    )


def _expected_visual_identity(
    effect: str,
    effect_options: Mapping[str, object] | None = None,
) -> tuple[str, str, str, str | None, str | None, dict[str, Any]]:
    from pptx_transitions import (
        _effect_spec,
    )
    prefix, namespace, element, attrs, fallback = _effect_spec(
        effect,
        effect_options,
    )
    carrier = "direct" if prefix == "p" else "alternate-content"
    fallback_element = None
    fallback_namespace = None
    if carrier == "alternate-content":
        (
            _fallback_prefix,
            fallback_namespace,
            fallback_element,
            _fallback_attrs,
            _nested_fallback,
        ) = _effect_spec(fallback or "fade")
    return (
        carrier,
        element,
        namespace,
        fallback_element,
        fallback_namespace,
        attrs,
    )


def _validate_applied_motion(
    slide_root: Any,
    *,
    before: TransitionSummary,
    enter: EnterUpdate,
    advance: AdvanceUpdate,
) -> None:
    from pptx_transitions import (
        PML_NS,
        _bool_attribute,
        _effective_transition_options,
        _expected_visual_identity,
        _int_attribute,
        _primary_and_fallback,
        _qn,
        _resolve_advance,
        _seconds_to_ms,
        _transition_elements,
        _visual_identity,
        normalize_transition_effect_request,
        read_slide_transition,
        transition_carriers,
        validate_slide_transition_structure,
    )
    errors = validate_slide_transition_structure(slide_root)
    after = read_slide_transition(slide_root)

    if enter.policy == "preserve":
        if before.logical_count and _visual_identity(after) != _visual_identity(before):
            errors.append("preserve policy changed the source visual transition")
        elif not before.logical_count and after.effect is not None:
            errors.append("preserve policy added a visual transition")
    elif enter.policy == "replace":
        effect, effect_options = normalize_transition_effect_request(
            enter.effect,
            enter.effect_options,
            allow_none=False,
        )
        expected_effect_options = _effective_transition_options(
            effect,
            effect_options,
        )
        expected_duration = _seconds_to_ms(
            enter.duration,
            "transition duration",
            allow_zero=False,
        )
        (
            expected_carrier,
            expected_effect,
            expected_namespace,
            expected_fallback,
            expected_fallback_namespace,
            expected_attrs,
        ) = _expected_visual_identity(effect, effect_options)
        if (
            after.carrier != expected_carrier
            or after.logical_count != 1
            or after.effect != expected_effect
            or after.effect_namespace != expected_namespace
            or after.canonical_effect != effect
            or dict(after.effect_options) != expected_effect_options
            or after.fallback_effect != expected_fallback
            or after.fallback_effect_namespace != expected_fallback_namespace
            or after.duration_ms != expected_duration
        ):
            errors.append(
                f"replace policy read-back does not match effect {effect!r}"
            )
        carriers = transition_carriers(slide_root)
        if carriers:
            primary, _fallback = _primary_and_fallback(carriers[0])
            effect_children = [
                child
                for child in (list(primary) if primary is not None else [])
                if child.tag != _qn(PML_NS, "sndAc")
            ]
            if effect_children:
                actual_attrs = effect_children[0].attrib
                for name, value in expected_attrs.items():
                    if actual_attrs.get(name) != str(value):
                        errors.append(
                            f"replace policy wrote invalid {effect} {name} attribute"
                        )
    elif enter.policy == "none":
        if after.effect is not None or after.fallback_effect is not None:
            errors.append("none policy retained a visual transition")
        if (
            after.sound_relationship_id is not None
            or after.fallback_sound_relationship_id is not None
        ):
            errors.append("none policy retained a transition sound")

    preserved_click = (
        before.advance_on_click
        if before.advance_on_click is not None
        else True
    )
    preserved_after = (
        before.advance_after_ms / 1000
        if before.advance_after_ms is not None
        else None
    )
    expected_click, expected_after = _resolve_advance(
        advance,
        preserved_click=preserved_click,
        preserved_after=preserved_after,
    )
    actual_click = (
        after.advance_on_click
        if after.advance_on_click is not None
        else True
    )
    actual_after_ms = after.advance_after_ms
    expected_after_ms = (
        _seconds_to_ms(
            expected_after,
            "transition advance_after",
            allow_zero=True,
        )
        if expected_after is not None
        else None
    )
    if actual_click != expected_click or actual_after_ms != expected_after_ms:
        errors.append("transition advance read-back does not match the requested mode")
    if advance.mode != "preserve":
        for carrier in transition_carriers(slide_root):
            for transition in _transition_elements(carrier):
                branch_click = _bool_attribute(transition, "advClick", True)
                branch_after_ms = _int_attribute(transition, "advTm")
                if (
                    branch_click != expected_click
                    or branch_after_ms != expected_after_ms
                ):
                    errors.append(
                        "transition fallback advance does not match the requested mode"
                    )
                    break

    if errors:
        raise ValueError("; ".join(errors))


def apply_slide_motion(
    slide_root: Any,
    *,
    enter: EnterUpdate,
    advance: AdvanceUpdate,
) -> bool:
    """Apply and immediately read back one resolved transition update."""
    from pptx_transitions import (
        _apply_slide_motion_unchecked,
        _validate_applied_motion,
        read_slide_transition,
    )
    before = read_slide_transition(slide_root)
    uses_timings = _apply_slide_motion_unchecked(
        slide_root,
        enter=enter,
        advance=advance,
    )
    _validate_applied_motion(
        slide_root,
        before=before,
        enter=enter,
        advance=advance,
    )
    return uses_timings


