"""Core PPTX assembly: create_pptx_with_native_svg."""
# Adapted from hugohe3/ppt-master 7e0df6705de0cd24345210d31f07fcb5a32b6900.
# MIT; Copyright (c) 2025-2026 Hugo He. See references/LICENSE-PPT-MASTER.txt and references/upstream.md.

from __future__ import annotations
import re
from pathlib import (
    Path,
)
from xml.etree import ElementTree as ET
from pptx_opc_validation import (
    canonical_opc_part_path as _canonical_opc_part_path,
    resolve_internal_opc_target as _resolve_internal_opc_target,
)

from .builder import (
    PptxStructureContext,
)

def _remove_relationship(rels_path: Path, rel_id: str) -> None:
    """Remove one relationship entry by rId."""
    rels_content = rels_path.read_text(encoding="utf-8")
    pattern = re.compile(
        rf'[ \t]*<Relationship\b[^>]*\bId="{re.escape(rel_id)}"[^>]*/>[ \t]*\n?'
    )
    new_content, removed = pattern.subn("", rels_content, count=1)
    if not removed:
        raise RuntimeError(f"Relationship {rel_id} not found in {rels_path}")
    rels_path.write_text(new_content, encoding="utf-8")


def _remove_content_type_override(content_types_path: Path, part_name: str) -> None:
    """Remove the Override content-type entry for a deleted package part."""
    normalized = "/" + part_name.lstrip("/")
    content = content_types_path.read_text(encoding="utf-8")
    pattern = re.compile(
        rf'[ \t]*<Override\b[^>]*\bPartName="{re.escape(normalized)}"[^>]*/>[ \t]*\n?'
    )
    new_content, removed = pattern.subn("", content, count=1)
    if removed:
        content_types_path.write_text(new_content, encoding="utf-8")


def _remove_trailing_layout_definition_slides(
    extract_dir: Path,
    public_slide_count: int,
    total_slide_count: int,
) -> int:
    """Remove internal carrier slides after their Layouts are registered."""
    from .builder import (
        PML_NS,
        REL_NS,
        SLIDE_REL_TYPE,
        _read_relationships,
        _relationships_path_for_part,
        _resolve_package_target,
        _write_xml_tree,
    )
    if total_slide_count <= public_slide_count:
        return 0
    presentation_part = "ppt/presentation.xml"
    presentation_path = extract_dir / presentation_part
    presentation_rels = _relationships_path_for_part(
        extract_dir,
        presentation_part,
    )
    tree = ET.parse(presentation_path)
    root = tree.getroot()
    slide_list = root.find(f"{{{PML_NS}}}sldIdLst")
    if slide_list is None:
        raise RuntimeError("presentation.xml has no p:sldIdLst")
    entries_by_rel_id = {
        entry.get(f"{{{REL_NS}}}id", ""): entry
        for entry in slide_list.findall(f"{{{PML_NS}}}sldId")
    }
    relationships = _read_relationships(presentation_rels)
    content_types_path = extract_dir / "[Content_Types].xml"
    removed = 0
    for slide_num in range(public_slide_count + 1, total_slide_count + 1):
        slide_part = f"ppt/slides/slide{slide_num}.xml"
        rel_ids = [
            rel_id
            for rel_id, attrs in relationships.items()
            if attrs.get("Type") == SLIDE_REL_TYPE
            and _resolve_package_target(
                presentation_part,
                attrs.get("Target", ""),
            ) == slide_part
        ]
        if len(rel_ids) != 1:
            raise RuntimeError(
                f"Internal Layout carrier {slide_part} must have exactly one "
                "Presentation relationship"
            )
        rel_id = rel_ids[0]
        entry = entries_by_rel_id.get(rel_id)
        if entry is None:
            raise RuntimeError(
                f"presentation.xml has no p:sldId entry for {slide_part}"
            )
        slide_list.remove(entry)
        _remove_relationship(presentation_rels, rel_id)
        slide_path = extract_dir / slide_part
        slide_rels = _relationships_path_for_part(extract_dir, slide_part)
        if not slide_path.is_file() or not slide_rels.is_file():
            raise RuntimeError(
                f"Internal Layout carrier package parts are incomplete: {slide_part}"
            )
        slide_path.unlink()
        slide_rels.unlink()
        _remove_content_type_override(content_types_path, slide_part)
        removed += 1
    _write_xml_tree(presentation_path, tree)
    return removed


def _prune_unreferenced_definition_payload_parts(
    extract_dir: Path,
    *,
    preserved_parts: frozenset[str] = frozenset(),
) -> int:
    """Remove generated native/media payload left by deleted carrier slides.

    Unselected complete Slide prototypes are first converted as carrier slides
    so their reusable Layout can be registered. Removing those carriers may
    leave chart, workbook, or media parts with no remaining relationship. Run
    an iterative incoming-reference sweep so chart-owned workbooks/styles are
    removed after their orphan chart part and relationship sidecar disappear.
    """
    from .builder import (
        _relationship_attrs,
        _relationships_path_for_part,
    )
    candidate_prefixes = (
        "ppt/charts/",
        "ppt/embeddings/",
        "ppt/media/",
    )
    content_types_path = extract_dir / "[Content_Types].xml"
    removed = 0
    while True:
        referenced_parts: set[str] = set()
        for rels_path in extract_dir.rglob("*.rels"):
            rels_rel = rels_path.relative_to(extract_dir).as_posix()
            try:
                root = ET.parse(rels_path).getroot()
            except ET.ParseError as exc:
                raise RuntimeError(
                    f"Invalid relationships XML while pruning {rels_rel}: {exc}"
                ) from exc
            for elem in root:
                attrs = _relationship_attrs(elem)
                if attrs.get("TargetMode", "").lower() == "external":
                    continue
                target = attrs.get("Target")
                if not target:
                    continue
                resolved = _resolve_internal_opc_target(rels_rel, target)
                if resolved is not None:
                    referenced_parts.add(resolved)

        orphan_paths: list[tuple[Path, str]] = []
        for path in extract_dir.rglob("*"):
            if not path.is_file():
                continue
            part_name = path.relative_to(extract_dir).as_posix()
            if "/_rels/" in part_name:
                continue
            if not part_name.startswith(candidate_prefixes):
                continue
            if part_name in preserved_parts:
                continue
            canonical = _canonical_opc_part_path(part_name)
            if canonical is not None and canonical not in referenced_parts:
                orphan_paths.append((path, part_name))
        if not orphan_paths:
            break

        for path, part_name in orphan_paths:
            rels_path = _relationships_path_for_part(extract_dir, part_name)
            if rels_path.is_file():
                rels_path.unlink()
            path.unlink()
            _remove_content_type_override(content_types_path, part_name)
            removed += 1
    return removed


def _prune_unused_slide_layouts(
    extract_dir: Path,
    structure: PptxStructureContext,
    slide_count: int,
    *,
    verbose: bool = False,
) -> int:
    """Remove base-template slide layouts no generated slide references.

    The python-pptx base package ships the full Office layout set; unused
    entries only pollute the PowerPoint new-slide picker. Layouts referenced
    by any generated slide are always kept, and a master keeps its layout
    list untouched unless at least one referenced layout remains in it.
    """
    from .builder import (
        SLIDE_LAYOUT_REL_TYPE,
        _find_relationship_target,
        _read_relationships,
        _relationships_path_for_part,
        _resolve_package_target,
    )
    # Read layout references live: earlier baseline passes may have rebound
    # minority slides to a Cover layout that the initial structure context
    # does not know about.
    referenced_layouts: set[str] = set()
    for slide_num in range(1, slide_count + 1):
        rels_path = (
            extract_dir / "ppt" / "slides" / "_rels" / f"slide{slide_num}.xml.rels"
        )
        target = _find_relationship_target(rels_path, SLIDE_LAYOUT_REL_TYPE)
        if not target:
            raise RuntimeError(f"Slide {slide_num} has no slide layout relationship")
        referenced_layouts.add(
            _resolve_package_target(f"ppt/slides/slide{slide_num}.xml", target)
        )

    pruned = 0
    content_types_path = extract_dir / "[Content_Types].xml"
    for master_part in sorted(set(structure.slide_master_parts.values())):
        master_path = extract_dir / master_part
        master_rels_path = _relationships_path_for_part(extract_dir, master_part)
        layout_rels = {
            rel_id: _resolve_package_target(master_part, attrs.get("Target", ""))
            for rel_id, attrs in _read_relationships(master_rels_path).items()
            if attrs.get("Type") == SLIDE_LAYOUT_REL_TYPE
        }
        if not any(part in referenced_layouts for part in layout_rels.values()):
            continue

        master_xml = master_path.read_text(encoding="utf-8")
        for rel_id, layout_part in sorted(layout_rels.items()):
            if layout_part in referenced_layouts:
                continue
            entry_re = re.compile(
                rf'[ \t]*<p:sldLayoutId\b[^>]*\br:id="{re.escape(rel_id)}"[^>]*/>[ \t]*\n?'
            )
            master_xml, removed = entry_re.subn("", master_xml, count=1)
            if not removed:
                raise RuntimeError(
                    f"Slide master {master_part} has no sldLayoutId entry for {rel_id}"
                )
            _remove_relationship(master_rels_path, rel_id)
            (extract_dir / layout_part).unlink()
            layout_rels_path = _relationships_path_for_part(extract_dir, layout_part)
            if layout_rels_path.exists():
                layout_rels_path.unlink()
            _remove_content_type_override(content_types_path, layout_part)
            pruned += 1
        master_path.write_text(master_xml, encoding="utf-8")

    if verbose and pruned:
        print(f"  Layout prune: removed {pruned} unused base layout(s)")
    return pruned


def _flat_structure_name(value: str | None) -> str:
    """Return one compact package identity for a free-design deck."""
    normalized = " ".join((value or "").split()).strip()
    return (normalized or "Free Design")[:120]


def _flat_placeholder_type(shape: ET.Element) -> str | None:
    """Return one system placeholder type carried by a top-level shape."""
    from .builder import (
        PML_NS,
    )
    if shape.tag != f"{{{PML_NS}}}sp":
        return None
    placeholder = shape.find(
        f"{{{PML_NS}}}nvSpPr/{{{PML_NS}}}nvPr/{{{PML_NS}}}ph"
    )
    return placeholder.get("type") if placeholder is not None else None


def _clean_flat_structure_part(
    part_path: Path,
    name: str,
    *,
    is_layout: bool,
) -> tuple[int, tuple[str, ...]]:
    """Keep only standard footer hooks in one project-owned flat shell."""
    from .builder import (
        PML_NS,
        _FLAT_SYSTEM_PLACEHOLDER_TYPES,
        _TOP_LEVEL_SHAPE_TAGS,
        _write_xml_tree,
    )
    try:
        tree = ET.parse(part_path)
    except (OSError, ET.ParseError) as exc:
        raise RuntimeError(
            f"Cannot parse flat structure part {part_path}: {exc}"
        ) from exc
    root = tree.getroot()
    common_slide = root.find(f"{{{PML_NS}}}cSld")
    if common_slide is None:
        raise RuntimeError(f"Flat structure part has no p:cSld: {part_path}")
    shape_tree = common_slide.find(f"{{{PML_NS}}}spTree")
    if shape_tree is None:
        raise RuntimeError(f"Flat structure part has no p:spTree: {part_path}")

    removed = 0
    retained: list[str] = []
    for child in list(shape_tree):
        if child.tag not in _TOP_LEVEL_SHAPE_TAGS:
            continue
        placeholder_type = _flat_placeholder_type(child)
        if placeholder_type in _FLAT_SYSTEM_PLACEHOLDER_TYPES:
            retained.append(placeholder_type)
            continue
        shape_tree.remove(child)
        removed += 1
    for parent in (common_slide, root):
        for extension_list in parent.findall(f"{{{PML_NS}}}extLst"):
            parent.remove(extension_list)

    common_slide.set("name", name)
    if is_layout:
        root.set("type", "blank")
        root.set("preserve", "1")
    _write_xml_tree(part_path, tree)
    return removed, tuple(retained)


def _name_flat_themes(extract_dir: Path, name: str) -> int:
    """Replace stock Office theme identities with the current deck identity."""
    from .builder import (
        DML_NS,
        _write_xml_tree,
    )
    theme_paths = sorted((extract_dir / "ppt" / "theme").glob("theme*.xml"))
    if not theme_paths:
        raise RuntimeError("Flat PPTX package has no theme part")
    for theme_path in theme_paths:
        try:
            tree = ET.parse(theme_path)
        except (OSError, ET.ParseError) as exc:
            raise RuntimeError(
                f"Cannot parse flat theme {theme_path}: {exc}"
            ) from exc
        root = tree.getroot()
        root.set("name", name)
        for tag in ("clrScheme", "fontScheme", "fmtScheme"):
            scheme = root.find(f".//{{{DML_NS}}}{tag}")
            if scheme is not None:
                scheme.set("name", name)
        _write_xml_tree(theme_path, tree)
    return len(theme_paths)
