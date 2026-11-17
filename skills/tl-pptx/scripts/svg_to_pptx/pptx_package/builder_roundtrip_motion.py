"""Core PPTX assembly: create_pptx_with_native_svg."""
# Adapted from hugohe3/ppt-master 7e0df6705de0cd24345210d31f07fcb5a32b6900.
# MIT; Copyright (c) 2025-2026 Hugo He. See references/LICENSE-PPT-MASTER.txt and references/upstream.md.

from __future__ import annotations
from pathlib import (
    Path,
)
from xml.etree import ElementTree as ET
from pptx_transitions import (
    AdvanceUpdate,
    DEFAULT_TRANSITION_DURATION,
    EnterUpdate,
    apply_slide_motion_xml,
    read_slide_transition_xml,
    transition_carriers,
    validate_generated_transition_xml,
)
from pptx_animations import (
    object_animation_fingerprint,
)
from .template_structure import (
    TemplateStructureError,
)

def _replace_roundtrip_transition_advance(
    source_root: ET.Element,
    generated_root: ET.Element,
) -> None:
    """Copy only advance attributes while preserving the source visual effect."""
    from .builder import (
        PML_NS,
    )
    source_carriers = transition_carriers(source_root)
    generated_carriers = transition_carriers(generated_root)
    if len(source_carriers) > 1 or len(generated_carriers) > 1:
        raise TemplateStructureError(
            "Round-trip transition advance patch requires at most one carrier"
        )

    transition_tag = f"{{{PML_NS}}}transition"

    def _elements(carrier: ET.Element) -> list[ET.Element]:
        if carrier.tag == transition_tag:
            return [carrier]
        return list(carrier.iter(transition_tag))

    generated_transitions = (
        _elements(generated_carriers[0]) if generated_carriers else []
    )
    advance_values = {
        (transition.get("advClick"), transition.get("advTm"))
        for transition in generated_transitions
    }
    if len(advance_values) > 1:
        raise TemplateStructureError(
            "Generated transition branches disagree on advance attributes"
        )
    advance_click, advance_after = (
        next(iter(advance_values)) if advance_values else (None, None)
    )

    if not source_carriers:
        if advance_click is not None or advance_after is not None:
            clone = ET.Element(transition_tag)
            if advance_click is not None:
                clone.set("advClick", advance_click)
            if advance_after is not None:
                clone.set("advTm", advance_after)
            children = list(source_root)
            insert_at = next(
                (
                    index
                    for index, child in enumerate(children)
                    if child.tag in {
                        f"{{{PML_NS}}}timing",
                        f"{{{PML_NS}}}extLst",
                    }
                ),
                len(children),
            )
            source_root.insert(insert_at, clone)
        return

    source_transitions = _elements(source_carriers[0])
    if not source_transitions:
        raise TemplateStructureError(
            "Source logical transition carrier contains no p:transition"
        )
    for transition in source_transitions:
        for attribute, value in (
            ("advClick", advance_click),
            ("advTm", advance_after),
        ):
            if value is None:
                transition.attrib.pop(attribute, None)
            else:
                transition.set(attribute, value)


def _raw_transition_carrier_span(xml_data: bytes) -> tuple[int, int] | None:
    """Locate one raw root transition carrier in PowerPoint slide XML."""
    from .builder import (
        _RAW_ALTERNATE_CONTENT_RE,
        _RAW_DIRECT_TRANSITION_RE,
    )
    alternate_spans = [
        match.span()
        for match in _RAW_ALTERNATE_CONTENT_RE.finditer(xml_data)
    ]
    carriers = [
        span
        for span in alternate_spans
        if _RAW_DIRECT_TRANSITION_RE.search(xml_data[span[0]:span[1]])
    ]
    for match in _RAW_DIRECT_TRANSITION_RE.finditer(xml_data):
        if any(start <= match.start() < end for start, end in alternate_spans):
            continue
        carriers.append(match.span())
    if len(carriers) > 1:
        raise TemplateStructureError(
            "Round-trip slide has multiple raw transition carriers"
        )
    return carriers[0] if carriers else None


def _preserve_roundtrip_transition_markup(
    serialized_slide: bytes,
    source_slide: bytes,
) -> bytes:
    """Restore an unchanged source transition carrier byte-for-byte."""
    source_span = _raw_transition_carrier_span(source_slide)
    serialized_span = _raw_transition_carrier_span(serialized_slide)
    if source_span is None:
        if serialized_span is not None:
            raise TemplateStructureError(
                "Round-trip serialization introduced a transition carrier"
            )
        return serialized_slide
    if serialized_span is None:
        raise TemplateStructureError(
            "Round-trip serialization removed the preserved transition carrier"
        )
    return (
        serialized_slide[:serialized_span[0]]
        + source_slide[source_span[0]:source_span[1]]
        + serialized_slide[serialized_span[1]:]
    )


def _raw_timing_span(xml_data: bytes) -> tuple[int, int] | None:
    """Locate one raw logical object-animation timing carrier in slide XML."""
    from .builder import (
        _RAW_ALTERNATE_CONTENT_RE,
        _RAW_TIMING_RE,
    )
    alternate_spans = [
        match.span()
        for match in _RAW_ALTERNATE_CONTENT_RE.finditer(xml_data)
    ]
    timings = [
        span
        for span in alternate_spans
        if _RAW_TIMING_RE.search(xml_data[span[0]:span[1]])
    ]
    for match in _RAW_TIMING_RE.finditer(xml_data):
        if any(start <= match.start() < end for start, end in alternate_spans):
            continue
        timings.append(match.span())
    if len(timings) > 1:
        raise TemplateStructureError(
            "Round-trip slide has multiple raw object-animation timing carriers"
        )
    return timings[0] if timings else None


def _preserve_roundtrip_timing_markup(
    serialized_slide: bytes,
    source_slide: bytes,
) -> bytes:
    """Restore unchanged source object-animation timing byte-for-byte."""
    source_span = _raw_timing_span(source_slide)
    serialized_span = _raw_timing_span(serialized_slide)
    if source_span is None:
        if serialized_span is not None:
            raise TemplateStructureError(
                "Round-trip serialization introduced object-animation timing"
            )
        return serialized_slide
    if serialized_span is None:
        raise TemplateStructureError(
            "Round-trip serialization removed preserved object-animation timing"
        )
    return (
        serialized_slide[:serialized_span[0]]
        + source_slide[source_span[0]:source_span[1]]
        + serialized_slide[serialized_span[1]:]
    )


def _apply_roundtrip_transition_overlay(
    slide_path: Path,
    *,
    effect: str | None,
    effect_options: dict[str, object],
    duration: float,
    auto_advance: float | None,
    replace_transition: bool,
    advance_changed: bool = True,
) -> bool:
    """Patch only transition/advance state while preserving source timing."""
    source_bytes = slide_path.read_bytes()
    source_xml = source_bytes.decode("utf-8")
    source_animation = object_animation_fingerprint(source_xml)
    if not replace_transition:
        enter = EnterUpdate(policy="preserve", duration=duration)
    elif effect is None:
        enter = EnterUpdate(
            policy="none",
            effect=None,
            duration=(duration or DEFAULT_TRANSITION_DURATION),
        )
    else:
        enter = EnterUpdate(
            policy="replace",
            effect=effect,
            duration=duration,
            effect_options=effect_options,
        )
    advance = (
        AdvanceUpdate(mode="preserve")
        if not advance_changed else (
            AdvanceUpdate(mode="click")
            if auto_advance is None
            else AdvanceUpdate(mode="both", after=auto_advance)
        )
    )
    updated_xml, uses_timings = apply_slide_motion_xml(
        source_xml,
        enter=enter,
        advance=advance,
    )
    if object_animation_fingerprint(updated_xml) != source_animation:
        raise RuntimeError(
            "Round-trip transition overlay changed source object animations"
        )
    if replace_transition:
        original = read_slide_transition_xml(source_xml)
        validate_generated_transition_xml(
            updated_xml,
            effect=effect,
            effect_options=effect_options,
            duration=duration,
            advance_on_click=True if advance_changed else original.advance_on_click,
            advance_after=auto_advance if advance_changed else (
                original.advance_after_ms / 1000 if original.advance_after_ms is not None else None
            ),
        )
    slide_path.write_bytes(
        _preserve_roundtrip_timing_markup(
            updated_xml.encode("utf-8"),
            source_bytes,
        )
    )
    return uses_timings


def _shape_ids_in_element(element: ET.Element) -> set[str]:
    """Return every non-visual DrawingML id owned by one shape subtree."""
    from .builder import (
        PML_NS,
    )
    return {
        value
        for node in element.iter(f"{{{PML_NS}}}cNvPr")
        if (value := node.get("id"))
    }


def _rewrite_roundtrip_timing_shape_ids(
    slide_root: ET.Element,
    id_map: dict[str, str],
) -> None:
    """Rewrite every timing reference to a remapped Slide-local shape id."""
    from .builder import (
        PML_NS,
    )
    if not id_map:
        return
    timing = slide_root.find(f"{{{PML_NS}}}timing")
    if timing is None:
        return
    for node in timing.iter():
        old_id = node.get("spid")
        if old_id in id_map:
            node.set("spid", id_map[old_id])


def _renumber_roundtrip_generated_shapes(
    generated_shapes: list[ET.Element],
    retained_source_nodes: list[ET.Element],
    slide_root: ET.Element,
    *,
    rewrite_timing: bool,
) -> dict[str, str]:
    """Keep generated additions disjoint from retained source shape ids."""
    from .builder import (
        DML_NS,
        PML_NS,
    )
    used_ids = {
        value
        for element in retained_source_nodes
        for value in _shape_ids_in_element(element)
    }
    all_ids = set(used_ids)
    for element in generated_shapes:
        all_ids.update(_shape_ids_in_element(element))
    numeric_ids = [int(value) for value in all_ids if value.isdigit()]
    next_id = max(numeric_ids, default=1) + 1
    id_map: dict[str, str] = {}
    for element in generated_shapes:
        for node in element.iter(f"{{{PML_NS}}}cNvPr"):
            old_id = node.get("id")
            if not old_id:
                continue
            if old_id in used_ids:
                while str(next_id) in all_ids:
                    next_id += 1
                new_id = str(next_id)
                next_id += 1
                node.set("id", new_id)
                id_map[old_id] = new_id
                all_ids.add(new_id)
                used_ids.add(new_id)
            else:
                used_ids.add(old_id)

    if not id_map:
        return {}
    for element in generated_shapes:
        for connector_tag in ("stCxn", "endCxn"):
            for connector in element.iter(f"{{{DML_NS}}}{connector_tag}"):
                old_id = connector.get("id")
                if old_id in id_map:
                    connector.set("id", id_map[old_id])
    if rewrite_timing:
        _rewrite_roundtrip_timing_shape_ids(slide_root, id_map)
    return id_map
