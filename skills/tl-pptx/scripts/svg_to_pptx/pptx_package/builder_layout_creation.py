"""Core PPTX assembly: create_pptx_with_native_svg."""
# Adapted from hugohe3/ppt-master 7e0df6705de0cd24345210d31f07fcb5a32b6900.
# MIT; Copyright (c) 2025-2026 Hugo He. See references/LICENSE-PPT-MASTER.txt and references/upstream.md.

from __future__ import annotations
import posixpath
import re
import uuid
from pathlib import (
    Path,
)
from xml.etree import ElementTree as ET
from .template_structure import (
    OOXML_UINT32_MAX,
    TemplateStructureError,
)

def _next_layout_part_number(extract_dir: Path) -> int:
    layouts_dir = extract_dir / "ppt" / "slideLayouts"
    numbers = [
        int(match.group(1))
        for path in layouts_dir.glob("slideLayout*.xml")
        if (match := re.fullmatch(r"slideLayout(\d+)\.xml", path.name))
    ]
    return max(numbers, default=0) + 1


def _next_slide_layout_id(extract_dir: Path) -> int:
    """Return a package-wide unused id for a new sldLayoutId entry."""
    from .builder import (
        PML_NS,
    )
    ids: list[int] = []
    masters_dir = extract_dir / "ppt" / "slideMasters"
    for master_path in sorted(masters_dir.glob("slideMaster*.xml")):
        master_root = ET.parse(master_path).getroot()
        ids.extend(
            int(entry.attrib["id"])
            for entry in master_root.findall(
                f"{{{PML_NS}}}sldLayoutIdLst/{{{PML_NS}}}sldLayoutId"
            )
            if entry.attrib.get("id", "").isdigit()
        )
    presentation_path = extract_dir / "ppt" / "presentation.xml"
    if presentation_path.exists():
        ids.extend(
            int(value)
            for value in re.findall(
                r'\bid="(\d{9,})"', presentation_path.read_text(encoding="utf-8")
            )
        )
    next_id = max([*ids, 2147483648]) + 1
    if next_id > OOXML_UINT32_MAX:
        raise TemplateStructureError(
            "Cannot register another Slide Layout because the OOXML UInt32 "
            "identifier range is exhausted"
        )
    return next_id


def _create_cover_layout(extract_dir: Path, master_part: str, base_layout_part: str) -> str:
    """Clone a layout into a Cover layout that hides master shapes.

    Returns the new layout target relative to slide parts.
    """
    from .builder import (
        COVER_LAYOUT_NAME,
        SLIDE_LAYOUT_REL_TYPE,
        _add_content_type_override,
        _append_relationship,
        _relationships_path_for_part,
    )
    base_layout_path = extract_dir / base_layout_part
    layout_xml = base_layout_path.read_text(encoding="utf-8")

    root_match = re.search(r"<p:sldLayout\b[^>]*>", layout_xml)
    if not root_match:
        raise RuntimeError(f"Slide layout has no p:sldLayout root: {base_layout_part}")
    root_tag = root_match.group(0)
    if "showMasterSp=" in root_tag:
        new_root_tag = re.sub(r'showMasterSp="[^"]*"', 'showMasterSp="0"', root_tag)
    else:
        new_root_tag = root_tag[:-1] + ' showMasterSp="0">'
    layout_xml = layout_xml.replace(root_tag, new_root_tag, 1)
    layout_xml = re.sub(
        r"(<p:cSld\b[^>]*?)\s+name=\"[^\"]*\"",
        rf'\g<1> name="{COVER_LAYOUT_NAME}"',
        layout_xml,
        count=1,
    )

    layout_num = _next_layout_part_number(extract_dir)
    new_layout_part = f"ppt/slideLayouts/slideLayout{layout_num}.xml"
    new_layout_path = extract_dir / new_layout_part
    new_layout_path.write_text(layout_xml, encoding="utf-8")

    base_rels_path = _relationships_path_for_part(extract_dir, base_layout_part)
    new_rels_path = _relationships_path_for_part(extract_dir, new_layout_part)
    new_rels_path.parent.mkdir(exist_ok=True)
    new_rels_path.write_text(
        base_rels_path.read_text(encoding="utf-8"), encoding="utf-8"
    )

    master_path = extract_dir / master_part
    master_rels_path = _relationships_path_for_part(extract_dir, master_part)
    layout_target = posixpath.relpath(
        new_layout_part, posixpath.dirname(master_part)
    )
    rel_id = _append_relationship(master_rels_path, SLIDE_LAYOUT_REL_TYPE, layout_target)

    master_xml = master_path.read_text(encoding="utf-8")
    layout_id = _next_slide_layout_id(extract_dir)
    entry = f'<p:sldLayoutId id="{layout_id}" r:id="{rel_id}"/>'
    if "</p:sldLayoutIdLst>" not in master_xml:
        raise RuntimeError(f"Slide master has no sldLayoutIdLst: {master_part}")
    master_path.write_text(
        master_xml.replace("</p:sldLayoutIdLst>", f"{entry}</p:sldLayoutIdLst>", 1),
        encoding="utf-8",
    )

    content_types_path = extract_dir / "[Content_Types].xml"
    content_types_path.write_text(
        _add_content_type_override(
            content_types_path.read_text(encoding="utf-8"),
            new_layout_part,
            "application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml",
        ),
        encoding="utf-8",
    )
    return posixpath.relpath(new_layout_part, "ppt/slides")


def _create_custom_layout(
    extract_dir: Path,
    master_part: str,
    base_layout_part: str,
    layout_name: str,
    *,
    show_master_shapes: bool = True,
) -> tuple[str, str]:
    """Clone a clean custom layout and register it under its slide master.

    Returns ``(slide_relationship_target, package_part)``.
    """
    from .builder import (
        PML_NS,
        REL_NS,
        SLIDE_LAYOUT_REL_TYPE,
        SLIDE_MASTER_REL_TYPE,
        _TOP_LEVEL_SHAPE_TAGS,
        _add_content_type_override,
        _append_relationship,
        _read_relationships,
        _relationships_path_for_part,
        _write_xml_tree,
    )
    base_layout_path = extract_dir / base_layout_part
    tree = ET.parse(base_layout_path)
    root = tree.getroot()
    _reseed_p14_creation_id(root)
    root.set("type", "cust")
    root.set("preserve", "1")
    root.set("showMasterSp", "1" if show_master_shapes else "0")

    c_sld = root.find(f"{{{PML_NS}}}cSld")
    if c_sld is None:
        raise RuntimeError(f"Slide layout has no p:cSld: {base_layout_part}")
    c_sld.set("name", layout_name)
    sp_tree = c_sld.find(f"{{{PML_NS}}}spTree")
    if sp_tree is None:
        raise RuntimeError(f"Slide layout has no p:spTree: {base_layout_part}")
    for child in list(sp_tree):
        if child.tag in _TOP_LEVEL_SHAPE_TAGS:
            sp_tree.remove(child)

    layout_num = _next_layout_part_number(extract_dir)
    new_layout_part = f"ppt/slideLayouts/slideLayout{layout_num}.xml"
    new_layout_path = extract_dir / new_layout_part
    _write_xml_tree(new_layout_path, tree)

    base_rels_path = _relationships_path_for_part(extract_dir, base_layout_part)
    new_rels_path = _relationships_path_for_part(extract_dir, new_layout_part)
    new_rels_path.parent.mkdir(exist_ok=True)
    new_rels_path.write_text(
        base_rels_path.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    master_target = posixpath.relpath(
        master_part,
        posixpath.dirname(new_layout_part),
    )
    rels_content = new_rels_path.read_text(encoding="utf-8")
    master_rel_ids = [
        rel_id
        for rel_id, attrs in _read_relationships(new_rels_path).items()
        if attrs.get("Type") == SLIDE_MASTER_REL_TYPE
    ]
    if len(master_rel_ids) != 1:
        raise RuntimeError(
            f"Cloned slide layout must have one Master relationship: {new_layout_part}"
        )
    master_rel_id = master_rel_ids[0]
    master_rel_pattern = re.compile(
        rf'(<Relationship\b[^>]*\bId="{re.escape(master_rel_id)}"'
        rf'[^>]*\bTarget=")[^"]*(")'
    )
    rels_content, replaced = master_rel_pattern.subn(
        rf"\g<1>{master_target}\g<2>",
        rels_content,
        count=1,
    )
    if replaced != 1:
        raise RuntimeError(
            f"Could not retarget cloned Layout to Master {master_part}"
        )
    new_rels_path.write_text(rels_content, encoding="utf-8")

    master_path = extract_dir / master_part
    master_rels_path = _relationships_path_for_part(extract_dir, master_part)
    layout_target = posixpath.relpath(new_layout_part, posixpath.dirname(master_part))
    rel_id = _append_relationship(master_rels_path, SLIDE_LAYOUT_REL_TYPE, layout_target)
    master_tree = ET.parse(master_path)
    master_root = master_tree.getroot()
    layout_list = master_root.find(f"{{{PML_NS}}}sldLayoutIdLst")
    if layout_list is None:
        raise RuntimeError(f"Slide master has no sldLayoutIdLst: {master_part}")
    layout_id = _next_slide_layout_id(extract_dir)
    ET.SubElement(
        layout_list,
        f"{{{PML_NS}}}sldLayoutId",
        {"id": str(layout_id), f"{{{REL_NS}}}id": rel_id},
    )
    _write_xml_tree(master_path, master_tree)

    content_types_path = extract_dir / "[Content_Types].xml"
    content_types_path.write_text(
        _add_content_type_override(
            content_types_path.read_text(encoding="utf-8"),
            new_layout_part,
            "application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml",
        ),
        encoding="utf-8",
    )
    return posixpath.relpath(new_layout_part, "ppt/slides"), new_layout_part


def _set_master_picker_name(master_path: Path, master_name: str) -> None:
    """Set the visible PowerPoint Master name on its common slide data."""
    from .builder import (
        PML_NS,
        _write_xml_tree,
    )
    tree = ET.parse(master_path)
    c_sld = tree.getroot().find(f"{{{PML_NS}}}cSld")
    if c_sld is None:
        raise RuntimeError(f"Slide master has no p:cSld: {master_path}")
    c_sld.set("name", master_name)
    _write_xml_tree(master_path, tree)


def _reseed_p14_creation_id(root: ET.Element) -> None:
    """Give a cloned Slide/Master/Layout part a fresh PowerPoint creation id."""
    from .builder import (
        P14_NS,
        PML_NS,
    )
    c_sld = root.find(f"{{{PML_NS}}}cSld")
    if c_sld is None:
        return
    creation_ids = c_sld.findall(
        f"{{{PML_NS}}}extLst/{{{PML_NS}}}ext/"
        f"{{{P14_NS}}}creationId"
    )
    for creation_id in creation_ids:
        value = 0
        while value == 0:
            value = uuid.uuid4().int & OOXML_UINT32_MAX
        creation_id.set("val", str(value))


def _next_master_part_number(extract_dir: Path) -> int:
    numbers = [
        int(match.group(1))
        for path in (extract_dir / "ppt" / "slideMasters").glob("slideMaster*.xml")
        if (match := re.fullmatch(r"slideMaster(\d+)\.xml", path.name))
    ]
    return max(numbers, default=0) + 1


def _next_theme_part_number(extract_dir: Path) -> int:
    numbers = [
        int(match.group(1))
        for path in (extract_dir / "ppt" / "theme").glob("theme*.xml")
        if (match := re.fullmatch(r"theme(\d+)\.xml", path.name))
    ]
    return max(numbers, default=0) + 1
