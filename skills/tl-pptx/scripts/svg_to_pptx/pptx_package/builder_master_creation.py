"""Core PPTX assembly: create_pptx_with_native_svg."""
# Adapted from hugohe3/ppt-master 7e0df6705de0cd24345210d31f07fcb5a32b6900.
# MIT; Copyright (c) 2025-2026 Hugo He. See references/LICENSE-PPT-MASTER.txt and references/upstream.md.

from __future__ import annotations
import posixpath
import re
import shutil
from pathlib import (
    Path,
)
from xml.etree import ElementTree as ET
from .template_structure import (
    OOXML_UINT32_MAX,
    TemplateSlideSpec,
    TemplateStructureError,
)

from .builder import (
    PptxStructureContext,
)

def _clone_master_theme(
    extract_dir: Path,
    source_master_part: str,
    master_part: str,
    master_name: str,
) -> str:
    """Give a cloned Slide Master its own Theme package part."""
    from .builder import (
        THEME_CONTENT_TYPE,
        THEME_REL_TYPE,
        _add_content_type_override,
        _next_theme_part_number,
        _read_relationships,
        _relationships_path_for_part,
        _resolve_package_target,
        _write_xml_tree,
    )
    source_master_rels = _relationships_path_for_part(
        extract_dir,
        source_master_part,
    )
    theme_targets = [
        attrs["Target"]
        for attrs in _read_relationships(source_master_rels).values()
        if attrs.get("Type") == THEME_REL_TYPE and attrs.get("Target")
    ]
    if len(theme_targets) != 1:
        raise RuntimeError(
            "Source Slide Master must have one Theme relationship: "
            f"{source_master_part}"
        )
    source_theme_part = _resolve_package_target(
        source_master_part,
        theme_targets[0],
    )
    source_theme_path = extract_dir / source_theme_part
    if not source_theme_path.exists():
        raise RuntimeError(
            f"Slide Master Theme part is missing: {source_theme_part}"
        )

    theme_num = _next_theme_part_number(extract_dir)
    theme_part = f"ppt/theme/theme{theme_num}.xml"
    theme_path = extract_dir / theme_part
    shutil.copyfile(source_theme_path, theme_path)
    theme_tree = ET.parse(theme_path)
    theme_tree.getroot().set("name", f"{master_name} Theme")
    _write_xml_tree(theme_path, theme_tree)

    source_theme_rels = _relationships_path_for_part(
        extract_dir,
        source_theme_part,
    )
    if source_theme_rels.exists():
        theme_rels = _relationships_path_for_part(extract_dir, theme_part)
        theme_rels.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_theme_rels, theme_rels)

    master_rels = _relationships_path_for_part(extract_dir, master_part)
    theme_relationships = [
        (rel_id, attrs)
        for rel_id, attrs in _read_relationships(master_rels).items()
        if attrs.get("Type") == THEME_REL_TYPE
    ]
    if len(theme_relationships) != 1:
        raise RuntimeError(
            f"Cloned Slide Master must have one Theme relationship: {master_part}"
        )
    theme_rel_id, _attrs = theme_relationships[0]
    theme_target = posixpath.relpath(theme_part, posixpath.dirname(master_part))
    rels_content = master_rels.read_text(encoding="utf-8")
    theme_rel_pattern = re.compile(
        rf'(<Relationship\b[^>]*\bId="{re.escape(theme_rel_id)}"'
        rf'[^>]*\bTarget=")[^"]*(")'
    )
    rels_content, replaced = theme_rel_pattern.subn(
        rf"\g<1>{theme_target}\g<2>",
        rels_content,
        count=1,
    )
    if replaced != 1:
        raise RuntimeError(
            f"Could not retarget cloned Slide Master Theme: {master_part}"
        )
    master_rels.write_text(rels_content, encoding="utf-8")

    content_types_path = extract_dir / "[Content_Types].xml"
    content_types_path.write_text(
        _add_content_type_override(
            content_types_path.read_text(encoding="utf-8"),
            theme_part,
            THEME_CONTENT_TYPE,
        ),
        encoding="utf-8",
    )
    return theme_part


def _clone_structured_master(
    extract_dir: Path,
    source_master_part: str,
    master_name: str,
) -> str:
    """Clone a clean Master part and register it with the Presentation."""
    from .builder import (
        PML_NS,
        SLIDE_LAYOUT_REL_TYPE,
        SLIDE_MASTER_REL_TYPE,
        _add_content_type_override,
        _append_relationship,
        _next_master_part_number,
        _read_relationships,
        _relationships_path_for_part,
        _remove_relationship,
        _reseed_p14_creation_id,
        _write_xml_tree,
    )
    master_num = _next_master_part_number(extract_dir)
    master_part = f"ppt/slideMasters/slideMaster{master_num}.xml"
    master_path = extract_dir / master_part
    source_master_path = extract_dir / source_master_part
    shutil.copyfile(source_master_path, master_path)

    tree = ET.parse(master_path)
    root = tree.getroot()
    _reseed_p14_creation_id(root)
    c_sld = root.find(f"{{{PML_NS}}}cSld")
    if c_sld is None:
        raise RuntimeError(f"Slide master has no p:cSld: {source_master_part}")
    c_sld.set("name", master_name)
    layout_list = root.find(f"{{{PML_NS}}}sldLayoutIdLst")
    if layout_list is None:
        raise RuntimeError(
            f"Slide master has no p:sldLayoutIdLst: {source_master_part}"
        )
    for entry in list(layout_list):
        layout_list.remove(entry)
    _write_xml_tree(master_path, tree)

    source_rels = _relationships_path_for_part(extract_dir, source_master_part)
    master_rels = _relationships_path_for_part(extract_dir, master_part)
    master_rels.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source_rels, master_rels)
    for rel_id, attrs in tuple(_read_relationships(master_rels).items()):
        if attrs.get("Type") == SLIDE_LAYOUT_REL_TYPE:
            _remove_relationship(master_rels, rel_id)
    _clone_master_theme(
        extract_dir,
        source_master_part,
        master_part,
        master_name,
    )

    presentation_rels = extract_dir / "ppt" / "_rels" / "presentation.xml.rels"
    relationship_target = posixpath.relpath(master_part, "ppt")
    relationship_id = _append_relationship(
        presentation_rels,
        SLIDE_MASTER_REL_TYPE,
        relationship_target,
    )
    presentation_path = extract_dir / "ppt" / "presentation.xml"
    presentation_xml = presentation_path.read_text(encoding="utf-8")
    master_ids = [
        int(value)
        for value in re.findall(r'<p:sldMasterId\b[^>]*\bid="(\d+)"', presentation_xml)
    ]
    master_id = max(master_ids, default=(1 << 31) - 1) + 1
    if master_id > OOXML_UINT32_MAX:
        raise TemplateStructureError("Presentation Master id exceeds OOXML UInt32")
    entry = f'<p:sldMasterId id="{master_id}" r:id="{relationship_id}"/>'
    if "</p:sldMasterIdLst>" not in presentation_xml:
        raise RuntimeError("presentation.xml has no p:sldMasterIdLst")
    presentation_path.write_text(
        presentation_xml.replace(
            "</p:sldMasterIdLst>",
            f"{entry}</p:sldMasterIdLst>",
            1,
        ),
        encoding="utf-8",
    )

    content_types_path = extract_dir / "[Content_Types].xml"
    content_types_path.write_text(
        _add_content_type_override(
            content_types_path.read_text(encoding="utf-8"),
            master_part,
            "application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml",
        ),
        encoding="utf-8",
    )
    return master_part


def _assign_structured_masters(
    extract_dir: Path,
    structure: PptxStructureContext,
    specs: list[TemplateSlideSpec],
) -> dict[str, str]:
    """Create one registered package Master for each explicit SVG Master key."""
    from .builder import (
        _set_master_picker_name,
    )
    if not specs:
        raise TemplateStructureError("Structured export requires at least one slide")
    source_master = structure.slide_master_part(specs[0].slide_num)
    masters: dict[str, str] = {}
    for spec in specs:
        existing_part = masters.get(spec.master_key)
        if existing_part is None:
            if not masters:
                existing_part = source_master
                _set_master_picker_name(
                    extract_dir / existing_part,
                    spec.master_name,
                )
            else:
                existing_part = _clone_structured_master(
                    extract_dir,
                    source_master,
                    spec.master_name,
                )
            masters[spec.master_key] = existing_part
        structure.slide_master_parts[spec.slide_num] = existing_part
    return masters


def _clear_master_placeholder_shapes(master_path: Path) -> None:
    """Remove base-package placeholders before installing structured content."""
    from .builder import (
        PML_NS,
        _write_xml_tree,
    )
    tree = ET.parse(master_path)
    root = tree.getroot()
    sp_tree = root.find(f".//{{{PML_NS}}}cSld/{{{PML_NS}}}spTree")
    if sp_tree is None:
        raise RuntimeError(f"Slide master has no p:spTree: {master_path}")
    for child in list(sp_tree):
        if child.find(f".//{{{PML_NS}}}ph") is not None:
            sp_tree.remove(child)
    _write_xml_tree(master_path, tree)


def _set_slide_layout_target(rels_path: Path, target: str) -> None:
    """Point a slide's layout relationship at a different layout part."""
    from .builder import (
        SLIDE_LAYOUT_REL_TYPE,
        _read_relationships,
    )
    content = rels_path.read_text(encoding="utf-8")
    rel_id = None
    for existing_id, attrs in _read_relationships(rels_path).items():
        if attrs.get("Type") == SLIDE_LAYOUT_REL_TYPE:
            rel_id = existing_id
            break
    if rel_id is None:
        raise RuntimeError(f"No slide layout relationship in {rels_path}")
    pattern = re.compile(
        rf'(<Relationship\b[^>]*\bId="{re.escape(rel_id)}"[^>]*\bTarget=")[^"]*(")'
    )
    new_content, replaced = pattern.subn(rf"\g<1>{target}\g<2>", content, count=1)
    if not replaced:
        raise RuntimeError(f"Could not retarget layout relationship in {rels_path}")
    rels_path.write_text(new_content, encoding="utf-8")
