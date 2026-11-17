"""Core PPTX assembly: create_pptx_with_native_svg."""
# Adapted from hugohe3/ppt-master 7e0df6705de0cd24345210d31f07fcb5a32b6900.
# MIT; Copyright (c) 2025-2026 Hugo He. See references/LICENSE-PPT-MASTER.txt and references/upstream.md.

from __future__ import annotations
import posixpath
from pathlib import (
    Path,
)
from xml.etree import ElementTree as ET

from .builder import (
    PptxStructureContext,
)

def _slide_size_type(width_emu: int, height_emu: int) -> str:
    """Return the ``p:sldSz type`` token matching a slide size."""
    from .builder import (
        _SLIDE_SIZE_TYPES,
    )
    for ratio_w, ratio_h, token in _SLIDE_SIZE_TYPES:
        if abs(width_emu * ratio_h - height_emu * ratio_w) <= max(width_emu, height_emu) // 200:
            return token
    return "custom"


def _set_slide_size_type(presentation, width_emu: int, height_emu: int) -> None:
    """Make the ``type`` token agree with the cx/cy the exporter just set."""
    from .builder import (
        PML_NS,
    )
    slide_size = presentation.element.find(f"{{{PML_NS}}}sldSz")
    if slide_size is not None:
        slide_size.set("type", _slide_size_type(width_emu, height_emu))


def _relationship_attrs(elem: ET.Element) -> dict[str, str]:
    return {key.rsplit("}", 1)[-1]: value for key, value in elem.attrib.items()}


def _resolve_package_target(source_part: str, target: str) -> str:
    """Resolve a relationship target relative to a package part path."""
    return posixpath.normpath(posixpath.join(posixpath.dirname(source_part), target))


def _relationships_path_for_part(extract_dir: Path, part_name: str) -> Path:
    """Return the package relationship sidecar path for a part name."""
    path = Path(part_name)
    return extract_dir / path.parent / "_rels" / f"{path.name}.rels"


def _find_relationship_target(
    rels_path: Path,
    rel_type: str,
) -> str | None:
    """Find the first relationship target for a relationship type."""
    if not rels_path.exists():
        return None
    root = ET.parse(rels_path).getroot()
    for elem in root:
        attrs = _relationship_attrs(elem)
        if attrs.get("Type") == rel_type:
            return attrs.get("Target")
    return None


def _read_relationships(rels_path: Path) -> dict[str, dict[str, str]]:
    """Return relationship attributes keyed by rId."""
    if not rels_path.exists():
        return {}
    root = ET.parse(rels_path).getroot()
    rels: dict[str, dict[str, str]] = {}
    for elem in root:
        attrs = _relationship_attrs(elem)
        rel_id = attrs.get("Id")
        if rel_id:
            rels[rel_id] = attrs
    return rels


def _find_relationship_id(
    rels_path: Path,
    rel_type: str,
    target: str,
    target_mode: str | None = None,
) -> str | None:
    """Find an existing relationship by type and target."""
    for rel_id, attrs in _read_relationships(rels_path).items():
        if (
            attrs.get("Type") == rel_type
            and attrs.get("Target") == target
            and attrs.get("TargetMode") == target_mode
        ):
            return rel_id
    return None


def _read_slide_layout_targets(extract_dir: Path, slide_count: int) -> PptxStructureContext:
    """Read the actual layout relationship target for every generated slide."""
    from .builder import (
        PptxStructureContext,
        SLIDE_LAYOUT_REL_TYPE,
        SLIDE_MASTER_REL_TYPE,
    )
    slide_layout_targets: dict[int, str] = {}
    slide_master_parts: dict[int, str] = {}
    rels_dir = extract_dir / "ppt" / "slides" / "_rels"
    for slide_num in range(1, slide_count + 1):
        rels_path = rels_dir / f"slide{slide_num}.xml.rels"
        if not rels_path.exists():
            raise RuntimeError(f"Missing slide relationship file: {rels_path}")
        target = _find_relationship_target(rels_path, SLIDE_LAYOUT_REL_TYPE)
        if not target:
            raise RuntimeError(f"Slide {slide_num} has no slide layout relationship")
        slide_layout_targets[slide_num] = target

        slide_part = f"ppt/slides/slide{slide_num}.xml"
        layout_part = _resolve_package_target(slide_part, target)
        layout_rels_path = _relationships_path_for_part(extract_dir, layout_part)
        master_target = _find_relationship_target(layout_rels_path, SLIDE_MASTER_REL_TYPE)
        if not master_target:
            raise RuntimeError(
                f"Slide {slide_num} layout has no slide master relationship"
            )
        slide_master_parts[slide_num] = _resolve_package_target(layout_part, master_target)
    return PptxStructureContext(
        slide_layout_targets=slide_layout_targets,
        slide_master_parts=slide_master_parts,
    )
