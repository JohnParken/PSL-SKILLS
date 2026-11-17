"""PPT Master motion core; responsibility-split, with stable original API.

MIT Copyright (c) 2025-2026 Hugo He.
Upstream commit: 7e0df6705de0cd24345210d31f07fcb5a32b6900.
"""
from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from pptx_transitions import EnterUpdate, AdvanceUpdate, TransitionSummary
import io
import re
from typing import Any, Mapping
from xml.etree import ElementTree as ET
from xml.sax.saxutils import quoteattr

from pptx_transitions_registry import Any, ET, LET, MC_NS, Mapping, PML_NS, TRANSITION_NAMESPACES, io, quoteattr, re

def _xml_declaration(source: str) -> str:
    from pptx_transitions import (
        re,
    )
    match = re.match(r"\s*(<\?xml[^?]*\?>)", source)
    return match.group(1) if match else ""


def _serialize_lxml_like(root: Any, source: str) -> str:
    from pptx_transitions import (
        LET,
        _xml_declaration,
    )
    body = LET.tostring(root, encoding="unicode", pretty_print=False)
    declaration = _xml_declaration(source)
    return f"{declaration}\n{body}" if declaration else body


def namespace_bindings(xml_data: str | bytes) -> dict[str, str]:
    """Return source prefix bindings without changing the document."""
    from pptx_transitions import (
        ET,
        io,
    )
    data = xml_data.encode("utf-8") if isinstance(xml_data, str) else xml_data
    bindings: dict[str, str] = {}
    for _event, (prefix, uri) in ET.iterparse(
        io.BytesIO(data),
        events=("start-ns",),
    ):
        bindings[prefix or ""] = uri
    return bindings


def register_source_namespaces(xml_data: str | bytes) -> dict[str, str]:
    """Register source prefixes before stdlib ElementTree re-serialization."""
    from pptx_transitions import (
        ET,
        namespace_bindings,
    )
    bindings = namespace_bindings(xml_data)
    for prefix, uri in bindings.items():
        if prefix == "xml":
            continue
        try:
            ET.register_namespace(prefix, uri)
        except (AttributeError, ValueError):
            continue
    return bindings


def parse_source_xml(xml_data: str | bytes) -> ET.Element:
    """Parse XML after registering its original namespace prefixes."""
    from pptx_transitions import (
        ET,
        register_source_namespaces,
    )
    data = xml_data.encode("utf-8") if isinstance(xml_data, str) else xml_data
    register_source_namespaces(data)
    return ET.fromstring(data)


def _required_mce_prefixes(root: Any) -> set[str]:
    from pptx_transitions import (
        MC_NS,
        _qn,
    )
    prefixes = set(str(root.get(_qn(MC_NS, "Ignorable")) or "").split())
    for element in root.iter():
        if element.tag == _qn(MC_NS, "Choice"):
            prefixes.update(str(element.get("Requires") or "").split())
    return {prefix for prefix in prefixes if prefix}


def _inject_root_namespace_declarations(
    xml_data: bytes,
    *,
    bindings: dict[str, str],
    prefixes: set[str],
) -> bytes:
    from pptx_transitions import (
        quoteattr,
        re,
    )
    text = xml_data.decode("utf-8")
    declaration_end = text.find("?>")
    root_start = text.find("<", declaration_end + 2 if declaration_end >= 0 else 0)
    root_end = text.find(">", root_start)
    if root_start < 0 or root_end < 0:
        raise ValueError("unable to locate serialized XML root element")

    opening = text[root_start:root_end]
    declarations: list[str] = []
    for prefix in sorted(prefixes):
        uri = bindings.get(prefix)
        if not uri:
            raise ValueError(
                f"MCE prefix {prefix!r} has no namespace binding in source XML"
            )
        if re.search(rf"\bxmlns:{re.escape(prefix)}\s*=", opening):
            continue
        declarations.append(f" xmlns:{prefix}={quoteattr(uri)}")
    if declarations:
        text = text[:root_end] + "".join(declarations) + text[root_end:]
    return text.encode("utf-8")


def validate_mce_prefixes(xml_data: str | bytes) -> list[str]:
    """Return unresolved MCE Requires/Ignorable prefix errors."""
    from pptx_transitions import (
        LET,
        MC_NS,
        _qn,
    )
    if LET is None:
        return []
    data = xml_data.encode("utf-8") if isinstance(xml_data, str) else xml_data
    try:
        root = LET.fromstring(data)
    except LET.XMLSyntaxError as exc:
        return [f"invalid XML: {exc}"]

    errors: list[str] = []
    ignorable = str(root.get(_qn(MC_NS, "Ignorable")) or "").split()
    for prefix in ignorable:
        if prefix not in root.nsmap:
            errors.append(f"mc:Ignorable prefix is not bound: {prefix}")
    for choice in root.iter(_qn(MC_NS, "Choice")):
        for prefix in str(choice.get("Requires") or "").split():
            if prefix not in choice.nsmap:
                errors.append(f"mc:Choice Requires prefix is not bound: {prefix}")
    return errors


def serialize_source_xml(root: ET.Element, source_xml: str | bytes) -> bytes:
    """Serialize stdlib XML while retaining MCE prefix bindings."""
    from pptx_transitions import (
        ET,
        PML_NS,
        TRANSITION_NAMESPACES,
        _inject_root_namespace_declarations,
        _qn,
        _required_mce_prefixes,
        read_slide_transition,
        read_slide_transition_xml,
        register_source_namespaces,
        validate_mce_prefixes,
        validate_slide_transition_xml,
    )
    expected_transition = (
        read_slide_transition(root)
        if root.tag == _qn(PML_NS, "sld")
        else None
    )
    source = (
        source_xml.encode("utf-8")
        if isinstance(source_xml, str)
        else source_xml
    )
    bindings = register_source_namespaces(source)
    prefixes = _required_mce_prefixes(root)
    for prefix in prefixes:
        namespace = TRANSITION_NAMESPACES.get(prefix)
        if namespace is None:
            continue
        bindings[prefix] = namespace
        ET.register_namespace(prefix, namespace)
    serialized = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    serialized = _inject_root_namespace_declarations(
        serialized,
        bindings=bindings,
        prefixes=prefixes,
    )
    errors = validate_mce_prefixes(serialized)
    if expected_transition is not None:
        errors.extend(validate_slide_transition_xml(serialized))
        actual_transition = read_slide_transition_xml(serialized)
        if actual_transition != expected_transition:
            errors.append("slide transition changed during XML serialization")
    if errors:
        raise ValueError("; ".join(errors))
    return serialized


def apply_slide_motion_xml(
    slide_xml: str,
    *,
    enter: EnterUpdate,
    advance: AdvanceUpdate,
) -> tuple[str, bool]:
    """Apply motion to source XML while preserving MCE namespace prefixes."""
    from pptx_transitions import (
        LET,
        _serialize_lxml_like,
        apply_slide_motion,
        parse_source_xml,
        read_slide_transition,
        read_slide_transition_xml,
        serialize_source_xml,
        validate_slide_transition_xml,
    )
    source_bytes = slide_xml.encode("utf-8")
    if LET is not None:
        parser = LET.XMLParser(
            remove_blank_text=False,
            resolve_entities=False,
            no_network=True,
        )
        root = LET.fromstring(source_bytes, parser)
        uses_timings = apply_slide_motion(
            root,
            enter=enter,
            advance=advance,
        )
        output = _serialize_lxml_like(root, slide_xml)
    else:
        root = parse_source_xml(source_bytes)
        uses_timings = apply_slide_motion(
            root,
            enter=enter,
            advance=advance,
        )
        output = serialize_source_xml(root, source_bytes).decode("utf-8")

    expected_transition = read_slide_transition(root)
    errors = validate_slide_transition_xml(output)
    if read_slide_transition_xml(output) != expected_transition:
        errors.append("slide transition changed during XML serialization")
    if errors:
        raise ValueError("; ".join(errors))
    return output, uses_timings


def read_slide_transition_xml(slide_xml: str | bytes) -> TransitionSummary:
    """Read a transition summary from raw slide XML."""
    from pptx_transitions import (
        LET,
        parse_source_xml,
        read_slide_transition,
    )
    data = (
        slide_xml.encode("utf-8")
        if isinstance(slide_xml, str)
        else slide_xml
    )
    if LET is not None:
        root = LET.fromstring(data)
    else:
        root = parse_source_xml(data)
    return read_slide_transition(root)


def validate_generated_transition_xml(
    slide_xml: str | bytes,
    *,
    effect: str | None,
    duration: object,
    advance_on_click: bool | None,
    advance_after: object | None,
    effect_options: Mapping[str, object] | None = None,
    sound: Mapping[str, object] | None = None,
) -> TransitionSummary:
    """Validate a generated transition against its resolved settings."""
    from pptx_transitions import (
        LET,
        PML_NS,
        _effective_transition_options,
        _expected_visual_identity,
        _normalize_transition_sound,
        _primary_and_fallback,
        _qn,
        _seconds_to_ms,
        normalize_transition_effect_request,
        parse_source_xml,
        read_slide_transition,
        transition_carriers,
        validate_mce_prefixes,
        validate_slide_transition_structure,
    )
    data = slide_xml.encode("utf-8") if isinstance(slide_xml, str) else slide_xml
    root = LET.fromstring(data) if LET is not None else parse_source_xml(data)
    errors = validate_slide_transition_structure(root) + validate_mce_prefixes(data)
    summary = read_slide_transition(root)
    normalized_effect, normalized_options = normalize_transition_effect_request(
        effect,
        effect_options,
    )
    normalized_sound = _normalize_transition_sound(sound)
    expected_click = True if advance_on_click is None else advance_on_click
    if not isinstance(expected_click, bool):
        errors.append("transition advance_on_click must be a boolean or None")
    expected_after_ms = (
        _seconds_to_ms(
            advance_after,
            "transition advance_after",
            allow_zero=True,
        )
        if advance_after is not None
        else None
    )
    expects_carrier = (
        normalized_effect is not None
        or expected_after_ms is not None
        or expected_click is False
        or normalized_sound is not None
    )

    if not expects_carrier:
        if summary.logical_count != 0:
            errors.append("generated slide unexpectedly contains a transition carrier")
    else:
        expected_duration_ms = (
            _seconds_to_ms(
                duration,
                "transition duration",
                allow_zero=False,
            )
            if normalized_effect is not None
            else None
        )
        expected_carrier = "direct"
        expected_effect = None
        expected_namespace = None
        expected_fallback = None
        expected_fallback_namespace = None
        expected_attrs: dict[str, Any] = {}
        expected_sound_relationship_id = (
            normalized_sound["relationship_id"]
            if normalized_sound is not None
            else None
        )
        expected_sound_name = (
            normalized_sound["name"]
            if normalized_sound is not None
            else None
        )
        expected_fallback_sound_relationship_id = None
        expected_fallback_sound_name = None
        if normalized_effect is not None:
            (
                expected_carrier,
                expected_effect,
                expected_namespace,
                expected_fallback,
                expected_fallback_namespace,
                expected_attrs,
            ) = _expected_visual_identity(
                normalized_effect,
                normalized_options,
            )
            expected_effect_options = _effective_transition_options(
                normalized_effect,
                normalized_options,
            )
            if expected_carrier == "alternate-content":
                expected_fallback_sound_relationship_id = (
                    expected_sound_relationship_id
                )
                expected_fallback_sound_name = expected_sound_name
        else:
            expected_effect_options = {}
        if (
            summary.carrier != expected_carrier
            or summary.logical_count != 1
            or summary.effect != expected_effect
            or summary.effect_namespace != expected_namespace
            or summary.canonical_effect != normalized_effect
            or dict(summary.effect_options) != expected_effect_options
            or summary.fallback_effect != expected_fallback
            or summary.fallback_effect_namespace != expected_fallback_namespace
            or summary.duration_ms != expected_duration_ms
            or summary.advance_on_click != expected_click
            or summary.advance_after_ms != expected_after_ms
            or summary.sound_relationship_id != expected_sound_relationship_id
            or summary.sound_name != expected_sound_name
            or summary.fallback_sound_relationship_id
            != expected_fallback_sound_relationship_id
            or summary.fallback_sound_name != expected_fallback_sound_name
        ):
            errors.append("generated transition read-back does not match its settings")
        if normalized_effect is not None:
            carriers = transition_carriers(root)
            if carriers:
                primary, _fallback = _primary_and_fallback(carriers[0])
                effect_children = [
                    child
                    for child in (list(primary) if primary is not None else [])
                    if child.tag != _qn(PML_NS, "sndAc")
                ]
                if len(effect_children) != 1:
                    errors.append(
                        "generated transition must contain exactly one visual "
                        f"effect child; found {len(effect_children)}"
                    )
                else:
                    for name, value in expected_attrs.items():
                        if effect_children[0].get(name) != str(value):
                            errors.append(
                                f"generated {normalized_effect} transition has "
                                f"invalid {name} attribute"
                            )

    if errors:
        raise ValueError("; ".join(errors))
    return summary


