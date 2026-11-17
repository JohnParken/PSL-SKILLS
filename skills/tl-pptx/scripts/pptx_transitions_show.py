"""PPT Master motion core; responsibility-split, with stable original API.

MIT Copyright (c) 2025-2026 Hugo He.
Upstream commit: 7e0df6705de0cd24345210d31f07fcb5a32b6900.
"""
from __future__ import annotations
import posixpath
import re
from pathlib import Path
from typing import Any, MutableMapping
from xml.etree import ElementTree as ET

from pptx_transitions_registry import (
    Any,
    CONTENT_TYPES_NS,
    CONTENT_TYPES_PART,
    ET,
    MutableMapping,
    PACKAGE_REL_NS,
    PML_NS,
    PRESENTATION_PROPS_CONTENT_TYPE,
    PRESENTATION_PROPS_PART,
    PRESENTATION_PROPS_REL_TYPE,
    PRESENTATION_RELS_PART,
    Path,
    posixpath,
    re,
)

def _insert_show_properties(root: Any, element: Any) -> None:
    from pptx_transitions import (
        PML_NS,
        _qn,
    )
    for index, child in enumerate(list(root)):
        if child.tag in {
            _qn(PML_NS, "clrMru"),
            _qn(PML_NS, "extLst"),
        }:
            root.insert(index, element)
            return
    root.append(element)


def _next_relationship_id(rels_xml: str) -> str:
    from pptx_transitions import (
        ET,
        re,
    )
    root = ET.fromstring(rels_xml)
    numbers: list[int] = []
    for relationship in root:
        match = re.fullmatch(r"rId(\d+)", relationship.get("Id", ""))
        if match is not None:
            numbers.append(int(match.group(1)))
    return f"rId{max(numbers, default=0) + 1}"


def _presentation_props_part(rels_root: Any) -> str | None:
    from pptx_transitions import (
        PRESENTATION_PROPS_REL_TYPE,
        posixpath,
    )
    for relationship in rels_root:
        if relationship.get("Type") != PRESENTATION_PROPS_REL_TYPE:
            continue
        if relationship.get("TargetMode") == "External":
            raise ValueError("presentation properties relationship must be internal")
        target = str(relationship.get("Target") or "").replace("\\", "/")
        if not target:
            raise ValueError("presentation properties relationship has no target")
        if target.startswith("/"):
            part_name = target.lstrip("/")
        else:
            part_name = posixpath.normpath(posixpath.join("ppt", target))
        if part_name == ".." or part_name.startswith("../"):
            raise ValueError(
                "presentation properties relationship escapes the PPTX package"
            )
        return part_name
    return None


def _ensure_presentation_props_references(
    parts: MutableMapping[str, bytes],
    *,
    props_part: str,
) -> None:
    from pptx_transitions import (
        CONTENT_TYPES_NS,
        CONTENT_TYPES_PART,
        ET,
        PACKAGE_REL_NS,
        PRESENTATION_PROPS_CONTENT_TYPE,
        PRESENTATION_PROPS_REL_TYPE,
        PRESENTATION_RELS_PART,
        _next_relationship_id,
        _presentation_props_part,
        _qn,
        parse_source_xml,
        serialize_source_xml,
    )
    rels_source = parts[PRESENTATION_RELS_PART]
    rels_root = parse_source_xml(rels_source)
    if _presentation_props_part(rels_root) is None:
        ET.SubElement(
            rels_root,
            _qn(PACKAGE_REL_NS, "Relationship"),
            {
                "Id": _next_relationship_id(rels_source.decode("utf-8")),
                "Type": PRESENTATION_PROPS_REL_TYPE,
                "Target": "presProps.xml",
            },
        )
        parts[PRESENTATION_RELS_PART] = serialize_source_xml(
            rels_root,
            rels_source,
        )

    content_source = parts[CONTENT_TYPES_PART]
    content_root = parse_source_xml(content_source)
    part_name = "/" + props_part.lstrip("/")
    if not any(
        override.get("PartName") == part_name
        and override.get("ContentType") == PRESENTATION_PROPS_CONTENT_TYPE
        for override in content_root
    ):
        ET.SubElement(
            content_root,
            _qn(CONTENT_TYPES_NS, "Override"),
            {
                "PartName": part_name,
                "ContentType": PRESENTATION_PROPS_CONTENT_TYPE,
            },
        )
        parts[CONTENT_TYPES_PART] = serialize_source_xml(
            content_root,
            content_source,
        )


def set_package_use_timings(
    parts: MutableMapping[str, bytes],
    *,
    enabled: bool = True,
) -> None:
    """Set presentation-wide timing playback in ppt/presProps.xml."""
    from pptx_transitions import (
        ET,
        PML_NS,
        PRESENTATION_PROPS_PART,
        PRESENTATION_RELS_PART,
        _ensure_presentation_props_references,
        _insert_show_properties,
        _presentation_props_part,
        _qn,
        parse_source_xml,
        serialize_source_xml,
    )
    rels_root = parse_source_xml(parts[PRESENTATION_RELS_PART])
    existing_props_part = _presentation_props_part(rels_root)
    props_part = existing_props_part or PRESENTATION_PROPS_PART
    if props_part in parts:
        source = parts[props_part]
        root = parse_source_xml(source)
    else:
        if existing_props_part is not None:
            raise ValueError(
                f"presentation properties part is missing: {props_part}"
            )
        source = (
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<p:presentationPr xmlns:p="{PML_NS}"/>'
        ).encode("utf-8")
        root = parse_source_xml(source)

    show_properties = root.find(_qn(PML_NS, "showPr"))
    if show_properties is None:
        show_properties = ET.Element(_qn(PML_NS, "showPr"))
        _insert_show_properties(root, show_properties)
    show_properties.set("useTimings", "1" if enabled else "0")
    parts[props_part] = serialize_source_xml(root, source)
    _ensure_presentation_props_references(parts, props_part=props_part)


def set_package_kiosk_show(parts: MutableMapping[str, bytes]) -> None:
    """Make ppt/presProps.xml a looping kiosk show (browsed at a kiosk).

    PowerPoint's "Browsed at a kiosk (full screen)" setting: the show loops
    until Escape and ignores click and keyboard advance, so only timings and
    hyperlinks move between slides — what a touch-screen guide or a signage
    loop needs. `useTimings` is left as the timing pass set it.
    """
    from pptx_transitions import (
        ET,
        PML_NS,
        PRESENTATION_PROPS_PART,
        PRESENTATION_RELS_PART,
        _ensure_presentation_props_references,
        _insert_show_properties,
        _presentation_props_part,
        _qn,
        parse_source_xml,
        serialize_source_xml,
    )
    rels_root = parse_source_xml(parts[PRESENTATION_RELS_PART])
    existing_props_part = _presentation_props_part(rels_root)
    props_part = existing_props_part or PRESENTATION_PROPS_PART
    if props_part in parts:
        source = parts[props_part]
        root = parse_source_xml(source)
    else:
        if existing_props_part is not None:
            raise ValueError(
                f"presentation properties part is missing: {props_part}"
            )
        source = (
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<p:presentationPr xmlns:p="{PML_NS}"/>'
        ).encode("utf-8")
        root = parse_source_xml(source)

    show_properties = root.find(_qn(PML_NS, "showPr"))
    if show_properties is None:
        show_properties = ET.Element(_qn(PML_NS, "showPr"))
        _insert_show_properties(root, show_properties)
    show_properties.set("loop", "1")
    # CT_ShowProperties opens with one of present / browse / kiosk.
    for child in list(show_properties):
        if child.tag in {_qn(PML_NS, kind) for kind in ("present", "browse", "kiosk")}:
            show_properties.remove(child)
    show_properties.insert(0, ET.Element(_qn(PML_NS, "kiosk")))
    parts[props_part] = serialize_source_xml(root, source)
    _ensure_presentation_props_references(parts, props_part=props_part)


def set_directory_use_timings(
    extract_dir: Path,
    *,
    enabled: bool | None = True,
    kiosk: bool = False,
) -> None:
    """Set presentation timing playback (and optionally kiosk looping) in an extracted PPTX directory.

    ``enabled=None`` leaves ``useTimings`` untouched so a kiosk show can be
    written without claiming timed advance.
    """
    from pptx_transitions import (
        CONTENT_TYPES_PART,
        PRESENTATION_PROPS_PART,
        PRESENTATION_RELS_PART,
        _presentation_props_part,
        parse_source_xml,
        set_package_kiosk_show,
        set_package_use_timings,
    )
    part_names = {
        PRESENTATION_RELS_PART,
        CONTENT_TYPES_PART,
    }
    rels_source = (extract_dir / PRESENTATION_RELS_PART).read_bytes()
    rels_root = parse_source_xml(rels_source)
    props_part = _presentation_props_part(rels_root) or PRESENTATION_PROPS_PART
    if (extract_dir / props_part).is_file():
        part_names.add(props_part)
    parts = {
        name: (extract_dir / name).read_bytes()
        for name in part_names
    }
    if enabled is not None:
        set_package_use_timings(parts, enabled=enabled)
    if kiosk:
        set_package_kiosk_show(parts)
    for name, payload in parts.items():
        path = extract_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)


