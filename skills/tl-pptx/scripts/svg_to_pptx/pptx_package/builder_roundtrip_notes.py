"""Core PPTX assembly: create_pptx_with_native_svg."""
# Adapted from hugohe3/ppt-master 7e0df6705de0cd24345210d31f07fcb5a32b6900.
# MIT; Copyright (c) 2025-2026 Hugo He. See references/LICENSE-PPT-MASTER.txt and references/upstream.md.

from __future__ import annotations
import hashlib
import posixpath
from pathlib import (
    Path,
    PurePosixPath,
)
from xml.etree import ElementTree as ET
from pptx_opc_validation import (
    canonical_opc_part_path as _canonical_opc_part_path,
    resolve_internal_opc_target as _resolve_internal_opc_target,
)
from pptx_workspace import WorkspaceResourceSpec
from .notes import (
    markdown_to_plain_text,
    create_notes_slide_xml,
    create_notes_slide_rels_xml,
)
from .template_structure import (
    TemplateStructureError,
)

from .builder import (
    _NotesMasterReference,
)

def _remove_relationships_by_type(rels_path: Path, rel_type: str) -> int:
    """Remove every relationship of one type and return the removed count."""
    from .builder import (
        _relationship_attrs,
        _write_xml_tree,
    )
    tree = ET.parse(rels_path)
    root = tree.getroot()
    removed = 0
    for relationship in list(root):
        if _relationship_attrs(relationship).get("Type") != rel_type:
            continue
        root.remove(relationship)
        removed += 1
    if removed:
        _write_xml_tree(rels_path, tree)
    return removed


def _part_has_relationship_references(extract_dir: Path, part_name: str) -> bool:
    """Return whether any relationships file in the package still targets a part."""
    from .builder import (
        _part_name_for_relationships_path,
        _read_relationships,
        _resolve_package_target,
    )
    for rels_path in (extract_dir / 'ppt').rglob('*.rels'):
        source_part = _part_name_for_relationships_path(rels_path)
        for attrs in _read_relationships(rels_path).values():
            if attrs.get('TargetMode') == 'External':
                continue
            target = attrs.get('Target')
            if target and _resolve_package_target(source_part, target) == part_name:
                return True
    return False


def _unused_part_path(directory: Path, filename: str) -> Path:
    """Allocate a part name without overwriting any existing package member."""
    requested = Path(filename)
    occupied = {path.name.casefold() for path in directory.iterdir()}
    candidate = directory / requested.name
    suffix = 1
    while candidate.name.casefold() in occupied:
        candidate = directory / f"{requested.stem}_{suffix}{requested.suffix}"
        suffix += 1
    return candidate


def _remove_all_notes_parts(extract_dir: Path) -> None:
    """Remove notes slides, masters, their relationships, and type overrides."""
    from .builder import (
        NOTES_SLIDE_REL_TYPE,
        PML_NS,
        _NOTES_MASTER_CONTENT_TYPE,
        _NOTES_MASTER_REL_TYPE,
        _relationships_path_for_part,
        _write_xml_tree,
    )
    content_types_path = extract_dir / '[Content_Types].xml'
    content_tree = ET.parse(content_types_path)
    content_root = content_tree.getroot()
    notes_content_types = {
        'application/vnd.openxmlformats-officedocument.presentationml.notesSlide+xml',
        _NOTES_MASTER_CONTENT_TYPE,
    }
    notes_parts = {
        _canonical_opc_part_path(node.get('PartName', '').lstrip('/'))
        for node in content_root
        if node.get('ContentType') in notes_content_types
    } - {None}
    notes_files = [
        path for path in extract_dir.rglob('*')
        if path.is_file() and (
            _canonical_opc_part_path(path.relative_to(extract_dir).as_posix()) in notes_parts
            or path.relative_to(extract_dir).as_posix().startswith((
                'ppt/notesSlides/', 'ppt/notesMasters/',
            ))
        )
    ]
    notes_parts.update(
        _canonical_opc_part_path(path.relative_to(extract_dir).as_posix())
        for path in notes_files
    )
    for rels_path in extract_dir.rglob('*.rels'):
        tree = ET.parse(rels_path)
        root = tree.getroot()
        removed = False
        rels_name = rels_path.relative_to(extract_dir).as_posix()
        for relationship in list(root):
            if (
                relationship.get('Type') in {NOTES_SLIDE_REL_TYPE, _NOTES_MASTER_REL_TYPE}
                or (
                    relationship.get('TargetMode', '').lower() != 'external'
                    and _resolve_internal_opc_target(
                        rels_name, relationship.get('Target', ''),
                    ) in notes_parts
                )
            ):
                root.remove(relationship)
                removed = True
        if removed:
            _write_xml_tree(rels_path, tree)
    presentation = extract_dir / 'ppt/presentation.xml'
    tree = ET.parse(presentation)
    notes_masters = tree.getroot().find(f'{{{PML_NS}}}notesMasterIdLst')
    if notes_masters is not None:
        tree.getroot().remove(notes_masters)
        _write_xml_tree(presentation, tree)
    for path in notes_files:
        rels_path = _relationships_path_for_part(extract_dir, path.relative_to(extract_dir).as_posix())
        rels_path.unlink(missing_ok=True)
        path.unlink(missing_ok=True)
    removed = False
    for node in list(content_root):
        if _canonical_opc_part_path(node.get('PartName', '').lstrip('/')) in notes_parts:
            content_root.remove(node)
            removed = True
    if removed:
        _write_xml_tree(content_types_path, content_tree)


def _apply_slide_notes(
    extract_dir: Path,
    rels_path: Path,
    slide_num: int,
    notes_content: str,
    primary_language: str | None,
    *,
    enable_notes: bool,
) -> _NotesMasterReference | None:
    """Replace one slide's notes relationship and return its notes master."""
    from .builder import (
        NOTES_SLIDE_REL_TYPE,
        _ensure_notes_master,
        _ensure_relationship,
        _find_relationship_target,
        _part_name_for_relationships_path,
        _read_relationships,
        _resolve_package_target,
    )
    existing_target = _find_relationship_target(rels_path, NOTES_SLIDE_REL_TYPE)
    source_part = _part_name_for_relationships_path(rels_path)
    existing_part = (
        _resolve_package_target(source_part, existing_target) if existing_target else None
    )
    exclusive = existing_part is not None
    if exclusive:
        for other_rels in rels_path.parent.glob('*.rels'):
            if other_rels == rels_path:
                continue
            other_part = _part_name_for_relationships_path(other_rels)
            if any(
                attrs.get('Type') == NOTES_SLIDE_REL_TYPE
                and _resolve_package_target(other_part, attrs.get('Target', '')) == existing_part
                for attrs in _read_relationships(other_rels).values()
            ):
                exclusive = False
                break
    _remove_relationships_by_type(rels_path, NOTES_SLIDE_REL_TYPE)
    if not enable_notes:
        return None
    notes_text = markdown_to_plain_text(notes_content) if notes_content else ''
    if not notes_text:
        return None

    notes_master = _ensure_notes_master(extract_dir, primary_language)
    notes_slides_dir = extract_dir / 'ppt' / 'notesSlides'
    notes_slides_dir.mkdir(exist_ok=True)
    notes_xml_path = (
        extract_dir / existing_part if exclusive
        else _unused_part_path(notes_slides_dir, f'notesSlide{slide_num}.xml')
    )
    notes_part = notes_xml_path.relative_to(extract_dir).as_posix()
    notes_xml_path.write_text(
        create_notes_slide_xml(slide_num, notes_text, primary_language),
        encoding='utf-8',
    )
    notes_rels_dir = notes_xml_path.parent / '_rels'
    notes_rels_dir.mkdir(exist_ok=True)
    notes_rels_path = notes_rels_dir / f'{notes_xml_path.name}.rels'
    notes_rels_path.write_text(
        create_notes_slide_rels_xml(
            slide_num,
            posixpath.relpath(notes_master.package_part, posixpath.dirname(notes_part)),
        ),
        encoding='utf-8',
    )
    _ensure_relationship(
        rels_path,
        NOTES_SLIDE_REL_TYPE,
        posixpath.relpath(notes_part, posixpath.dirname(source_part)),
    )
    return notes_master


def _roundtrip_resource_payloads(
    resource_root: Path | None,
    resources: tuple[WorkspaceResourceSpec, ...],
) -> dict[str, bytes]:
    """Read changed, validated round-trip resources by package part."""
    changed = [resource for resource in resources if resource.changed]
    if not changed:
        return {}
    if resource_root is None:
        raise TemplateStructureError(
            "Round-trip resource reinjection requires an explicit project root"
        )
    workspace = resource_root.resolve()
    payloads: dict[str, bytes] = {}
    for resource in changed:
        source = (workspace / resource.workspace_path).resolve()
        try:
            source.relative_to(workspace)
        except ValueError as exc:
            raise TemplateStructureError(
                "Round-trip resource resolves outside the project: "
                f"{resource.workspace_path.as_posix()}"
            ) from exc
        if not source.is_file():
            raise TemplateStructureError(
                "Materialized round-trip resource is missing: "
                f"{resource.workspace_path.as_posix()}"
            )
        payload = source.read_bytes()
        current_sha256 = hashlib.sha256(payload).hexdigest()
        if current_sha256 != resource.current_sha256:
            raise TemplateStructureError(
                "Round-trip resource changed after validation: "
                f"{resource.workspace_path.as_posix()}"
            )
        payloads[resource.package_part] = payload
    return payloads


def _reinject_roundtrip_resources(
    extract_dir: Path,
    resource_root: Path | None,
    resources: tuple[WorkspaceResourceSpec, ...],
) -> int:
    """Write changed resource bytes back to their exact source package parts."""
    payloads = _roundtrip_resource_payloads(resource_root, resources)
    written = 0
    for package_part, payload in payloads.items():
        target = extract_dir.joinpath(*PurePosixPath(package_part).parts)
        if not target.is_file():
            raise TemplateStructureError(
                "Round-trip source package part is missing: "
                f"{package_part}"
            )
        target.write_bytes(payload)
        written += 1
    return written
