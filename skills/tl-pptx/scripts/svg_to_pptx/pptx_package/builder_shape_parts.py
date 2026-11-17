"""Core PPTX assembly: create_pptx_with_native_svg."""
# Adapted from hugohe3/ppt-master 7e0df6705de0cd24345210d31f07fcb5a32b6900.
# MIT; Copyright (c) 2025-2026 Hugo He. See references/LICENSE-PPT-MASTER.txt and references/upstream.md.

from __future__ import annotations
import posixpath
import re
from pathlib import (
    Path,
    PurePosixPath,
)
from xml.etree import ElementTree as ET
from hyperlink_contract import (
    HYPERLINK_REL_TYPE,
)
from .narration import (
    IMAGE_REL_TYPE,
)

def _shape_relationships_supported(
    elem: ET.Element,
    rels: dict[str, dict[str, str]],
) -> bool:
    """Return whether every shape relation can move to Master/Layout parts."""
    from .builder import (
        SLIDE_REL_TYPE,
        _relationship_ids_in_shape,
    )
    for rel_id in _relationship_ids_in_shape(elem):
        attrs = rels.get(rel_id)
        if not attrs:
            return False
        rel_type = attrs.get("Type")
        target_mode = attrs.get("TargetMode")
        if rel_type == IMAGE_REL_TYPE and not target_mode:
            continue
        if rel_type == HYPERLINK_REL_TYPE and target_mode == "External":
            continue
        if rel_type == SLIDE_REL_TYPE and not target_mode:
            continue
        return False
    return True


def _canonical_shape_xml(
    elem: ET.Element,
    rels: dict[str, dict[str, str]],
) -> bytes:
    """Canonicalize ids and relationship ids for cross-slide equality."""
    from .builder import (
        DML_NS,
        PML_NS,
        _REL_ATTRS,
    )
    clone = ET.fromstring(ET.tostring(elem, encoding="utf-8"))
    for cnv in clone.iter(f"{{{PML_NS}}}cNvPr"):
        cnv.set("id", "ID")
        # Generated names include the slide-local shape id (for example,
        # ``Image 2`` versus ``Image 8``) but do not affect rendering.
        if "name" in cnv.attrib:
            cnv.set("name", "NAME")
    for fld in clone.iter(f"{{{DML_NS}}}fld"):
        # The literal inside a slide-number field is a per-slide render
        # cache that PowerPoint recomputes from the slide position.
        if fld.attrib.get("type") == "slidenum":
            cached = fld.find(f"{{{DML_NS}}}t")
            if cached is not None:
                cached.text = ""
    for node in clone.iter():
        for attr_name, value in list(node.attrib.items()):
            if attr_name not in _REL_ATTRS:
                continue
            attrs = rels.get(value, {})
            node.set(
                attr_name,
                f"{attrs.get('Type', '')}|{attrs.get('Target', '')}|"
                f"{attrs.get('TargetMode', '')}",
            )
    return ET.tostring(clone, encoding="utf-8")


def _ensure_relationship(
    rels_path: Path,
    rel_type: str,
    target: str,
    target_mode: str | None = None,
) -> str:
    from .builder import (
        _append_relationship,
        _find_relationship_id,
    )
    existing = _find_relationship_id(
        rels_path,
        rel_type,
        target,
        target_mode,
    )
    if existing:
        return existing
    return _append_relationship(
        rels_path,
        rel_type,
        target,
        target_mode=target_mode,
    )


def _part_name_for_relationships_path(rels_path: Path) -> str:
    """Recover one ``ppt/...`` package part from its relationship sidecar."""
    if rels_path.parent.name != "_rels" or not rels_path.name.endswith(".rels"):
        raise RuntimeError(f"Invalid PPTX relationship path: {rels_path}")
    part_path = rels_path.parent.parent / rels_path.name.removesuffix(".rels")
    parts = part_path.parts
    try:
        ppt_index = len(parts) - 1 - tuple(reversed(parts)).index("ppt")
    except ValueError as exc:
        raise RuntimeError(
            f"Relationship path is not under a ppt package: {rels_path}"
        ) from exc
    return PurePosixPath(*parts[ppt_index:]).as_posix()


def _copy_shape_relationships_to_part(
    elem: ET.Element,
    slide_rels: dict[str, dict[str, str]],
    target_rels_path: Path,
) -> ET.Element:
    """Clone a shape and retarget supported relationship ids to another part."""
    from .builder import (
        _REL_ATTRS,
        _resolve_package_target,
    )
    clone = ET.fromstring(ET.tostring(elem, encoding="utf-8"))
    target_part = _part_name_for_relationships_path(target_rels_path)
    for node in clone.iter():
        for attr_name, value in list(node.attrib.items()):
            if attr_name not in _REL_ATTRS:
                continue
            rel = slide_rels.get(value)
            if not rel:
                raise RuntimeError(f"Missing slide relationship for {value}")
            target_mode = rel.get("TargetMode")
            relationship_target = rel["Target"]
            if target_mode != "External":
                resolved_target = _resolve_package_target(
                    "ppt/slides/source.xml",
                    relationship_target,
                )
                relationship_target = posixpath.relpath(
                    resolved_target,
                    posixpath.dirname(target_part),
                )
            new_rid = _ensure_relationship(
                target_rels_path,
                rel["Type"],
                relationship_target,
                target_mode,
            )
            node.set(attr_name, new_rid)
    return clone


def _copy_shape_relationships_to_master(
    elem: ET.Element,
    slide_rels: dict[str, dict[str, str]],
    master_rels_path: Path,
) -> ET.Element:
    """Clone a shape and retarget supported relationship ids to the master."""
    return _copy_shape_relationships_to_part(elem, slide_rels, master_rels_path)


def _next_master_shape_id(master_xml: str) -> int:
    ids = [
        int(match)
        for match in re.findall(r"<p:cNvPr\b[^>]*\bid=\"(\d+)\"", master_xml)
    ]
    return max(ids, default=1) + 1


def _renumber_shape_ids(elem: ET.Element, start_id: int) -> None:
    from .builder import (
        PML_NS,
    )
    next_id = start_id
    for cnv in elem.iter(f"{{{PML_NS}}}cNvPr"):
        cnv.set("id", str(next_id))
        next_id += 1


def _append_shape_to_master(master_path: Path, elem: ET.Element) -> None:
    master_xml = master_path.read_text(encoding="utf-8")
    _renumber_shape_ids(elem, _next_master_shape_id(master_xml))
    shape_xml = ET.tostring(elem, encoding="unicode")
    if "</p:spTree>" not in master_xml:
        raise RuntimeError(f"Slide master has no p:spTree: {master_path}")
    master_path.write_text(
        master_xml.replace("</p:spTree>", f"{shape_xml}\n</p:spTree>", 1),
        encoding="utf-8",
    )


def _append_shape_to_part(part_path: Path, elem: ET.Element) -> None:
    """Append a top-level shape to a master/layout spTree with fresh ids."""
    from .builder import (
        PML_NS,
    )
    tree = ET.parse(part_path)
    root = tree.getroot()
    sp_tree = root.find(f".//{{{PML_NS}}}cSld/{{{PML_NS}}}spTree")
    if sp_tree is None:
        raise RuntimeError(f"PPTX part has no p:spTree: {part_path}")
    existing_ids = [
        int(cnv.attrib["id"])
        for cnv in root.iter(f"{{{PML_NS}}}cNvPr")
        if cnv.attrib.get("id", "").isdigit()
    ]
    clone = ET.fromstring(ET.tostring(elem, encoding="utf-8"))
    _renumber_shape_ids(clone, max(existing_ids, default=1) + 1)
    sp_tree.append(clone)
    _write_xml_tree(part_path, tree)


def _write_xml_tree(path: Path, tree: ET.ElementTree) -> None:
    from .builder import (
        PACKAGE_REL_NS,
    )
    root = tree.getroot()
    if root.tag == f"{{{PACKAGE_REL_NS}}}Relationships":
        ET.register_namespace("", PACKAGE_REL_NS)
    tree.write(path, encoding="utf-8", xml_declaration=True)


def _slide_ref_shape_ids(values: frozenset[str]) -> set[str]:
    """Return Slide-local ids from authoring source references."""
    result: set[str] = set()
    for value in values:
        if value.startswith("slide:") and len(value) > len("slide:"):
            result.add(value.split(":", 1)[1])
    return result
