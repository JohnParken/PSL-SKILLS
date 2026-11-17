"""PPT Master motion core; responsibility-split, with stable original API.

MIT Copyright (c) 2025-2026 Hugo He.
Upstream commit: 7e0df6705de0cd24345210d31f07fcb5a32b6900.
"""
from __future__ import annotations
from dataclasses import field
from typing import Any, Mapping
from xml.etree import ElementTree as ET
from xml.sax.saxutils import quoteattr

from pptx_transitions_registry import Any, DEFAULT_TRANSITION, ET, LET, MC_NS, Mapping, NATIVE_TRANSITIONS, P14_NS, PML_NS, TRANSITION_NAMESPACES, field, quoteattr

def _effect_spec(
    effect: str,
    effect_options: object = None,
) -> tuple[str, str, str, dict[str, Any], str | None]:
    from pptx_transitions import (
        NATIVE_TRANSITIONS,
        TRANSITION_NAMESPACES,
        normalize_transition_effect_options,
    )
    info = NATIVE_TRANSITIONS[effect]
    prefix = str(info.get("prefix", "p"))
    namespace = TRANSITION_NAMESPACES[prefix]
    element = str(info["element"])
    attrs = dict(info.get("attrs", {}))
    fallback = info.get("fallback")
    options = {
        name: option_spec["default"]
        for name, option_spec in info["effectOptions"].items()
    }
    options.update(
        normalize_transition_effect_options(effect, effect_options)
    )
    option_specs = info["effectOptions"]
    for name, value in options.items():
        override = option_specs[name]["values"][value]
        if "prefix" in override:
            prefix = str(override["prefix"])
            namespace = TRANSITION_NAMESPACES[prefix]
        if "element" in override:
            element = str(override["element"])
        for attribute in override.get("remove_attrs", ()):
            attrs.pop(str(attribute), None)
        attrs.update(
            {
                str(attribute): str(attribute_value)
                for attribute, attribute_value in override.get("attrs", {}).items()
            }
        )
        if "fallback" in override:
            fallback = override["fallback"]
    return prefix, namespace, element, attrs, str(fallback) if fallback else None


def _effect_xml(
    effect: str,
    effect_options: object = None,
) -> tuple[str, str, str]:
    from pptx_transitions import (
        _effect_spec,
    )
    prefix, _namespace, element, effect_attrs, _fallback = _effect_spec(
        effect,
        effect_options,
    )
    attrs = " ".join(
        f'{key}="{value}"'
        for key, value in effect_attrs.items()
    )
    suffix = f" {attrs}" if attrs else ""
    return prefix, element, suffix


def _transition_attributes(
    *,
    duration_ms: int | None,
    advance_after_ms: int | None,
    advance_on_click: bool | None,
    declare_p14: bool,
) -> str:
    from pptx_transitions import (
        P14_NS,
    )
    attrs: list[str] = []
    if duration_ms is not None:
        attrs.append(f'p14:dur="{duration_ms}"')
    if declare_p14:
        attrs.append(f'xmlns:p14="{P14_NS}"')
    if advance_on_click is not None:
        attrs.append(f'advClick="{1 if advance_on_click else 0}"')
    if advance_after_ms is not None:
        attrs.append(f'advTm="{advance_after_ms}"')
    return " " + " ".join(attrs) if attrs else ""


def _normalize_transition_sound(
    sound: Mapping[str, object] | None,
) -> dict[str, str] | None:
    """Validate one packaged transition-sound relationship descriptor."""
    from pptx_transitions import (
        Mapping,
    )
    if sound is None:
        return None
    if not isinstance(sound, Mapping):
        raise ValueError("transition sound must be a relationship descriptor")
    unknown = set(sound) - {"relationship_id", "name"}
    if unknown:
        raise ValueError(
            "transition sound has unknown field(s): "
            + ", ".join(sorted(str(field) for field in unknown))
        )
    relationship_id = sound.get("relationship_id")
    name = sound.get("name")
    if not isinstance(relationship_id, str) or not relationship_id.strip():
        raise ValueError(
            "transition sound relationship_id must be a non-empty string"
        )
    if not isinstance(name, str) or not name.strip():
        raise ValueError("transition sound name must be a non-empty string")
    return {
        "relationship_id": relationship_id,
        "name": name,
    }


def _transition_sound_xml(
    sound: Mapping[str, str] | None,
    *,
    indent: str,
) -> str:
    """Return the optional p:sndAc payload at one indentation level."""
    from pptx_transitions import (
        quoteattr,
    )
    if sound is None:
        return ""
    relationship_id = quoteattr(sound["relationship_id"])
    name = quoteattr(sound["name"])
    return (
        f"{indent}<p:sndAc>\n"
        f"{indent}  <p:stSnd>\n"
        f"{indent}    <p:snd r:embed={relationship_id} name={name}/>\n"
        f"{indent}  </p:stSnd>\n"
        f"{indent}</p:sndAc>\n"
    )


def create_transition_xml(
    effect: str | None = DEFAULT_TRANSITION,
    duration: float = 0.5,
    advance_after: float | None = None,
    advance_on_click: bool | None = None,
    effect_options: Mapping[str, object] | None = None,
    sound: Mapping[str, object] | None = None,
) -> str:
    """Build a direct or MCE-backed p:transition XML fragment."""
    from pptx_transitions import (
        MC_NS,
        TRANSITION_NAMESPACES,
        _effect_spec,
        _effect_xml,
        _normalize_transition_sound,
        _seconds_to_ms,
        _transition_attributes,
        _transition_sound_xml,
        normalize_transition_effect_request,
    )
    normalized_effect, normalized_options = normalize_transition_effect_request(
        effect,
        effect_options,
    )
    normalized_sound = _normalize_transition_sound(sound)
    duration_ms = None
    if normalized_effect is not None:
        duration_ms = _seconds_to_ms(
            duration,
            "transition duration",
            allow_zero=False,
        )
    if advance_on_click is not None:
        if not isinstance(advance_on_click, bool):
            raise ValueError(
                "transition advance_on_click must be a boolean or None"
            )
    advance_ms = None
    if advance_after is not None:
        advance_ms = _seconds_to_ms(
            advance_after,
            "transition advance_after",
            allow_zero=True,
        )

    if (
        normalized_effect is None
        and advance_ms is None
        and advance_on_click is None
        and normalized_sound is None
    ):
        return ""

    attr_text = _transition_attributes(
        duration_ms=duration_ms,
        advance_after_ms=advance_ms,
        advance_on_click=advance_on_click,
        declare_p14=normalized_effect is not None,
    )
    if normalized_effect is None:
        if normalized_sound is None:
            return f"  <p:transition{attr_text}/>"
        return (
            f"  <p:transition{attr_text}>\n"
            + _transition_sound_xml(normalized_sound, indent="    ")
            + "  </p:transition>"
        )

    prefix, element_name, effect_attrs = _effect_xml(
        normalized_effect,
        normalized_options,
    )
    if prefix != "p":
        _effect_prefix, _namespace, _element, _attrs, fallback = _effect_spec(
            normalized_effect,
            normalized_options,
        )
        fallback_effect = fallback or "fade"
        fallback_prefix, fallback_name, fallback_attrs = _effect_xml(
            fallback_effect
        )
        fallback_attr_text = _transition_attributes(
            duration_ms=None,
            advance_after_ms=advance_ms,
            advance_on_click=advance_on_click,
            declare_p14=False,
        )
        choice_sound_xml = _transition_sound_xml(
            normalized_sound,
            indent="        ",
        )
        fallback_sound_xml = _transition_sound_xml(
            normalized_sound,
            indent="        ",
        )
        return (
            f'  <mc:AlternateContent xmlns:mc="{MC_NS}">\n'
            f'    <mc:Choice xmlns:{prefix}="{TRANSITION_NAMESPACES[prefix]}" '
            f'Requires="{prefix}">\n'
            f"      <p:transition{attr_text}>\n"
            f"        <{prefix}:{element_name}{effect_attrs}/>\n"
            f"{choice_sound_xml}"
            "      </p:transition>\n"
            "    </mc:Choice>\n"
            "    <mc:Fallback>\n"
            f"      <p:transition{fallback_attr_text}>\n"
            f"        <{fallback_prefix}:{fallback_name}{fallback_attrs}/>\n"
            f"{fallback_sound_xml}"
            "      </p:transition>\n"
            "    </mc:Fallback>\n"
            "  </mc:AlternateContent>"
        )
    sound_xml = _transition_sound_xml(normalized_sound, indent="    ")
    return (
        f"  <p:transition{attr_text}>\n"
        f"    <{prefix}:{element_name}{effect_attrs}/>\n"
        f"{sound_xml}"
        "  </p:transition>"
    )


def _is_lxml_element(element: object) -> bool:
    from pptx_transitions import (
        LET,
    )
    return LET is not None and isinstance(element, LET._Element)


def _new_element(
    context: Any,
    tag: str,
    *,
    nsmap: dict[str, str] | None = None,
) -> Any:
    from pptx_transitions import (
        ET,
        LET,
        _is_lxml_element,
    )
    if _is_lxml_element(context):
        return LET.Element(tag, nsmap=nsmap)
    return ET.Element(tag)


def _build_transition_element(
    context: Any,
    *,
    effect: str | None,
    duration: float,
    advance_after: float | None,
    advance_on_click: bool | None,
    effect_options: Mapping[str, object] | None = None,
) -> Any | None:
    from pptx_transitions import (
        MC_NS,
        P14_NS,
        PML_NS,
        TRANSITION_NAMESPACES,
        _effect_spec,
        _new_element,
        _qn,
        _seconds_to_ms,
        normalize_transition_effect_request,
    )
    normalized_effect, normalized_options = normalize_transition_effect_request(
        effect,
        effect_options,
    )
    if (
        normalized_effect is None
        and advance_after is None
        and advance_on_click is not False
    ):
        return None

    if advance_on_click is not None:
        if not isinstance(advance_on_click, bool):
            raise ValueError(
                "transition advance_on_click must be a boolean or None"
            )
    duration_ms = (
        _seconds_to_ms(
            duration,
            "transition duration",
            allow_zero=False,
        )
        if normalized_effect is not None
        else None
    )
    advance_ms = (
        _seconds_to_ms(
            advance_after,
            "transition advance_after",
            allow_zero=True,
        )
        if advance_after is not None
        else None
    )

    def build_transition(
        effect_name: str | None,
        *,
        include_duration: bool,
        options: Mapping[str, object] | None = None,
    ) -> Any:
        nsmap = {"p": PML_NS}
        if include_duration:
            nsmap["p14"] = P14_NS
        transition = _new_element(
            context,
            _qn(PML_NS, "transition"),
            nsmap=nsmap,
        )
        if include_duration and duration_ms is not None:
            transition.set(_qn(P14_NS, "dur"), str(duration_ms))
        if advance_on_click is not None:
            transition.set("advClick", "1" if advance_on_click else "0")
        if advance_ms is not None:
            transition.set("advTm", str(advance_ms))
        if effect_name is not None:
            _prefix, namespace, element_name, effect_attrs, _fallback = (
                _effect_spec(effect_name, options)
            )
            child = _new_element(context, _qn(namespace, element_name))
            for key, value in effect_attrs.items():
                child.set(key, str(value))
            transition.append(child)
        return transition

    if normalized_effect is None:
        return build_transition(None, include_duration=False)

    prefix, _namespace, _element, _attrs, fallback = _effect_spec(
        normalized_effect,
        normalized_options,
    )
    if prefix == "p":
        return build_transition(
            normalized_effect,
            include_duration=True,
            options=normalized_options,
        )

    carrier = _new_element(
        context,
        _qn(MC_NS, "AlternateContent"),
        nsmap={"mc": MC_NS},
    )
    choice = _new_element(
        context,
        _qn(MC_NS, "Choice"),
        nsmap={prefix: TRANSITION_NAMESPACES[prefix]},
    )
    choice.set("Requires", prefix)
    choice.append(
        build_transition(
            normalized_effect,
            include_duration=True,
            options=normalized_options,
        )
    )
    fallback_node = _new_element(context, _qn(MC_NS, "Fallback"))
    fallback_node.append(
        build_transition(fallback or "fade", include_duration=False)
    )
    carrier.extend((choice, fallback_node))
    return carrier


