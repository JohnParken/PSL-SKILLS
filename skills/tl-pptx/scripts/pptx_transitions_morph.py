"""PPT Master motion core; responsibility-split, with stable original API.

MIT Copyright (c) 2025-2026 Hugo He.
Upstream commit: 7e0df6705de0cd24345210d31f07fcb5a32b6900.
"""
from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from pptx_transitions import TransitionSummary, MorphPairExpectation
import zipfile
from pathlib import Path
from typing import Any, Iterable
from xml.etree import ElementTree as ET

from pptx_transitions_registry import Any, CONTENT_TYPES_PART, ET, Iterable, LET, MC_NS, PML_NS, PRESENTATION_PROPS_CONTENT_TYPE, PRESENTATION_RELS_PART, Path, zipfile

def _top_level_shape_types_by_name(
    slide_xml: bytes,
) -> dict[str, list[str]]:
    """Return top-level Selection Pane names and their OOXML container types."""
    from pptx_transitions import (
        LET,
        PML_NS,
        _local_name,
        _qn,
        parse_source_xml,
    )
    root = LET.fromstring(slide_xml) if LET is not None else parse_source_xml(slide_xml)
    sp_tree = root.find(f".//{{{PML_NS}}}cSld/{{{PML_NS}}}spTree")
    if sp_tree is None:
        raise ValueError("slide has no p:cSld/p:spTree")
    shapes: dict[str, list[str]] = {}
    for child in sp_tree:
        c_nv_pr = next(child.iter(_qn(PML_NS, "cNvPr")), None)
        name = c_nv_pr.get("name") if c_nv_pr is not None else None
        if not name:
            continue
        shapes.setdefault(name, []).append(_local_name(child.tag))
    return shapes


def validate_pptx_morph_pairs(
    pptx_path: Path,
    expectations: Iterable[MorphPairExpectation],
) -> None:
    """Prove that every requested forced-Morph pair survives final packaging."""
    from pptx_transitions import (
        ET,
        _top_level_shape_types_by_name,
        read_slide_transition_xml,
        zipfile,
    )
    expected_pairs = tuple(expectations)
    if not expected_pairs:
        return

    errors: list[str] = []
    slide_shapes: dict[int, dict[str, list[str]]] = {}
    slide_transitions: dict[int, TransitionSummary] = {}
    involved_slides = {
        slide_number
        for pair in expected_pairs
        for slide_number in (
            pair.source_slide_number,
            pair.destination_slide_number,
        )
    }
    try:
        with zipfile.ZipFile(pptx_path, "r") as package:
            names = set(package.namelist())
            for slide_number in sorted(involved_slides):
                part = f"ppt/slides/slide{slide_number}.xml"
                if part not in names:
                    errors.append(f"{part}: Morph slide part is missing")
                    continue
                slide_xml = package.read(part)
                try:
                    slide_shapes[slide_number] = _top_level_shape_types_by_name(
                        slide_xml
                    )
                    slide_transitions[slide_number] = read_slide_transition_xml(
                        slide_xml
                    )
                except Exception as exc:
                    errors.append(f"{part}: Morph read-back failed: {exc}")
    except (OSError, zipfile.BadZipFile, KeyError, ET.ParseError) as exc:
        errors.append(f"unable to read PPTX Morph package: {exc}")

    for slide_number, names_to_types in slide_shapes.items():
        duplicate_names = sorted(
            name
            for name, types in names_to_types.items()
            if name.startswith("!!") and len(types) != 1
        )
        if duplicate_names:
            errors.append(
                f"ppt/slides/slide{slide_number}.xml: duplicate forced-Morph "
                f"name(s): {', '.join(duplicate_names)}"
            )

    for pair in expected_pairs:
        if pair.destination_slide_number != pair.source_slide_number + 1:
            errors.append(
                f'Morph pair "{pair.key}" must connect adjacent generated slides'
            )
            continue
        source_shapes = slide_shapes.get(pair.source_slide_number)
        destination_shapes = slide_shapes.get(pair.destination_slide_number)
        if source_shapes is None or destination_shapes is None:
            continue

        shape_name = pair.shape_name
        source_types = source_shapes.get(shape_name, [])
        destination_types = destination_shapes.get(shape_name, [])
        if len(source_types) != 1:
            errors.append(
                f'Morph pair "{pair.key}" expected exactly one source object '
                f'named "{shape_name}" on slide {pair.source_slide_number}'
            )
        if len(destination_types) != 1:
            errors.append(
                f'Morph pair "{pair.key}" expected exactly one destination '
                f'object named "{shape_name}" on slide '
                f'{pair.destination_slide_number}'
            )
        if (
            len(source_types) == 1
            and len(destination_types) == 1
            and source_types[0] != destination_types[0]
        ):
            errors.append(
                f'Morph pair "{pair.key}" changes OOXML object type from '
                f'{source_types[0]} to {destination_types[0]}'
            )

        transition = slide_transitions.get(pair.destination_slide_number)
        if (
            transition is not None
            and (
                transition.canonical_effect != "morph"
                or transition.effect_options.get("morph_by") != "object"
            )
        ):
            errors.append(
                f'Morph pair "{pair.key}" destination slide '
                f'{pair.destination_slide_number} does not use Morph by object'
            )

    declared_names_by_edge: dict[tuple[int, int], set[str]] = {}
    for pair in expected_pairs:
        declared_names_by_edge.setdefault(
            (
                pair.source_slide_number,
                pair.destination_slide_number,
            ),
            set(),
        ).add(pair.shape_name)
    for source_slide_number in sorted(slide_shapes):
        destination_slide_number = source_slide_number + 1
        if destination_slide_number not in slide_shapes:
            continue
        transition = slide_transitions.get(destination_slide_number)
        if (
            transition is None
            or transition.canonical_effect != "morph"
        ):
            continue
        source_names = {
            name
            for name in slide_shapes[source_slide_number]
            if name.startswith("!!")
        }
        destination_names = {
            name
            for name in slide_shapes[destination_slide_number]
            if name.startswith("!!")
        }
        declared_names = declared_names_by_edge.get(
            (source_slide_number, destination_slide_number),
            set(),
        )
        unexpected_names = sorted(
            (source_names & destination_names) - declared_names
        )
        if unexpected_names:
            errors.append(
                f"Morph edge {source_slide_number}->{destination_slide_number} "
                "contains undeclared forced name(s): "
                + ", ".join(unexpected_names)
            )

    if errors:
        raise ValueError("; ".join(dict.fromkeys(errors)))


def _validate_package_use_timings(
    package: zipfile.ZipFile,
    names: list[str],
) -> list[str]:
    from pptx_transitions import (
        CONTENT_TYPES_PART,
        ET,
        PML_NS,
        PRESENTATION_PROPS_CONTENT_TYPE,
        PRESENTATION_RELS_PART,
        _presentation_props_part,
        _qn,
    )
    errors: list[str] = []
    required_parts = {
        PRESENTATION_RELS_PART,
        CONTENT_TYPES_PART,
    }
    missing = sorted(required_parts - set(names))
    if missing:
        return ["timed advance is missing package parts: " + ", ".join(missing)]

    rels_root = ET.fromstring(package.read(PRESENTATION_RELS_PART))
    props_part = _presentation_props_part(rels_root)
    if props_part is None:
        return ["presentation relationships do not reference presentation properties"]
    if props_part not in names:
        return [f"presentation properties part is missing: {props_part}"]

    props_root = ET.fromstring(package.read(props_part))
    show_properties = props_root.find(_qn(PML_NS, "showPr"))
    if (
        show_properties is None
        or show_properties.get("useTimings") not in {"1", "true", "on"}
    ):
        errors.append(f"{props_part} must set p:showPr@useTimings=1")

    content_root = ET.fromstring(package.read(CONTENT_TYPES_PART))
    package_part_name = "/" + props_part.lstrip("/")
    if not any(
        override.get("PartName") == package_part_name
        and override.get("ContentType") == PRESENTATION_PROPS_CONTENT_TYPE
        for override in content_root
    ):
        errors.append(f"[Content_Types].xml must declare {props_part}")
    return errors


def validate_slide_transition_structure(slide_root: Any) -> list[str]:
    """Return logical-carrier and schema-order errors for one slide root."""
    from pptx_transitions import (
        MC_NS,
        PML_NS,
        _qn,
        _transition_elements,
        transition_carriers,
    )
    errors: list[str] = []
    children = list(slide_root)
    carriers = transition_carriers(slide_root)
    if len(carriers) > 1:
        errors.append(
            f"slide has {len(carriers)} logical transition carriers; expected at most 1"
        )
    if carriers:
        carrier_index = children.index(carriers[0])
        common_slide = next(
            (
                child
                for child in children
                if child.tag == _qn(PML_NS, "cSld")
            ),
            None,
        )
        if common_slide is None:
            errors.append("slide with transition carrier must contain p:cSld")
        elif carrier_index < children.index(common_slide):
            errors.append("transition carrier must follow p:cSld")
        color_map = next(
            (
                child
                for child in children
                if child.tag == _qn(PML_NS, "clrMapOvr")
            ),
            None,
        )
        if color_map is not None and carrier_index < children.index(color_map):
            errors.append("transition carrier must follow p:clrMapOvr")
        for tag in ("timing", "extLst"):
            element = next(
                (
                    child
                    for child in children
                    if child.tag == _qn(PML_NS, tag)
                ),
                None,
            )
            if element is not None and carrier_index > children.index(element):
                errors.append(f"transition carrier must precede p:{tag}")

    for carrier in carriers:
        for transition in _transition_elements(carrier):
            sound_actions = [
                child
                for child in list(transition)
                if child.tag == _qn(PML_NS, "sndAc")
            ]
            if len(sound_actions) > 1:
                errors.append(
                    "p:transition must contain at most one p:sndAc; "
                    f"found {len(sound_actions)}"
                )
            if sound_actions:
                start_sounds = [
                    child
                    for child in list(sound_actions[0])
                    if child.tag == _qn(PML_NS, "stSnd")
                ]
                if len(start_sounds) > 1:
                    errors.append(
                        "p:sndAc must contain at most one p:stSnd; "
                        f"found {len(start_sounds)}"
                    )
                if start_sounds:
                    sounds = [
                        child
                        for child in list(start_sounds[0])
                        if child.tag == _qn(PML_NS, "snd")
                    ]
                    if len(sounds) != 1:
                        errors.append(
                            "p:stSnd must contain exactly one p:snd; "
                            f"found {len(sounds)}"
                        )
        if carrier.tag != _qn(MC_NS, "AlternateContent"):
            continue
        choices = [
            child
            for child in list(carrier)
            if child.tag == _qn(MC_NS, "Choice")
        ]
        if not choices:
            errors.append("mc:AlternateContent transition must contain mc:Choice")
        for branch_name in ("Choice", "Fallback"):
            branches = [
                child
                for child in list(carrier)
                if child.tag == _qn(MC_NS, branch_name)
            ]
            for branch in branches:
                count = len(_transition_elements(branch))
                if count != 1:
                    errors.append(
                        f"mc:{branch_name} must contain exactly one p:transition; "
                        f"found {count}"
                    )
    return errors


def validate_slide_transition_xml(slide_xml: str | bytes) -> list[str]:
    """Validate raw slide transition structure and MCE prefix bindings."""
    from pptx_transitions import (
        LET,
        parse_source_xml,
        validate_mce_prefixes,
        validate_slide_transition_structure,
    )
    data = slide_xml.encode("utf-8") if isinstance(slide_xml, str) else slide_xml
    try:
        if LET is not None:
            root = LET.fromstring(data)
        else:
            root = parse_source_xml(data)
    except Exception as exc:
        return [f"invalid slide XML: {exc}"]
    return validate_slide_transition_structure(root) + validate_mce_prefixes(data)


