"""Core PPTX assembly: create_pptx_with_native_svg."""
# Adapted from hugohe3/ppt-master 7e0df6705de0cd24345210d31f07fcb5a32b6900.
# MIT; Copyright (c) 2025-2026 Hugo He. See references/LICENSE-PPT-MASTER.txt and references/upstream.md.

from __future__ import annotations
import posixpath
import shutil
from pathlib import (
    Path,
    PurePosixPath,
)
from xml.etree import ElementTree as ET
from .notes import (
    create_notes_master_rels_xml,
    create_notes_master_xml,
)

from .builder import (
    _NotesMasterReference,
)

def _content_type_overrides(extract_dir: Path) -> dict[str, str]:
    """Return package part content-type overrides keyed without a leading slash."""
    content_types_path = extract_dir / '[Content_Types].xml'
    try:
        root = ET.parse(content_types_path).getroot()
    except (OSError, ET.ParseError) as exc:
        raise RuntimeError(
            f'Cannot read package content types: {content_types_path}'
        ) from exc
    overrides: dict[str, str] = {}
    for child in root:
        if child.tag.rsplit('}', 1)[-1] != 'Override':
            continue
        part_name = child.get('PartName', '').lstrip('/')
        content_type = child.get('ContentType', '')
        if part_name:
            overrides[part_name] = content_type
    return overrides


def _notes_master_part_from_target(target: str) -> str | None:
    """Resolve one presentation relationship target to a notes-master part."""
    from .builder import (
        _resolve_package_target,
    )
    part_name = _resolve_package_target(
        'ppt/presentation.xml',
        target,
    ).lstrip('/')
    prefix = 'ppt/notesMasters/'
    if not part_name.startswith(prefix) or part_name == prefix:
        return None
    return part_name


def _validate_notes_master_part(
    extract_dir: Path,
    part_name: str,
    content_types: dict[str, str],
) -> str:
    """Validate one existing notes-master package part and return its name."""
    from .builder import (
        PML_NS,
        _NOTES_MASTER_CONTENT_TYPE,
    )
    normalized = posixpath.normpath(part_name.lstrip('/'))
    if not normalized.startswith('ppt/notesMasters/'):
        raise RuntimeError(
            f'Notes master resolves outside ppt/notesMasters: {part_name}'
        )
    part_path = extract_dir / PurePosixPath(normalized)
    if not part_path.is_file():
        raise RuntimeError(f'Notes master part is missing: {normalized}')
    try:
        root = ET.parse(part_path).getroot()
    except (OSError, ET.ParseError) as exc:
        raise RuntimeError(f'Cannot parse notes master part: {normalized}') from exc
    if root.tag != f'{{{PML_NS}}}notesMaster':
        raise RuntimeError(f'Notes master part has an invalid root: {normalized}')
    declared_type = content_types.get(normalized)
    if declared_type is not None and declared_type != _NOTES_MASTER_CONTENT_TYPE:
        raise RuntimeError(
            'Notes master content type mismatch for '
            f'{normalized}: {declared_type}'
        )
    return normalized


def _register_notes_master_id(
    presentation_path: Path,
    relationship_id: str,
) -> None:
    """Register a notes-master relationship in presentation.xml by XML identity."""
    from .builder import (
        PML_NS,
        REL_NS,
        _write_xml_tree,
    )
    try:
        tree = ET.parse(presentation_path)
    except (OSError, ET.ParseError) as exc:
        raise RuntimeError(
            f'Cannot parse presentation package part: {presentation_path}'
        ) from exc
    root = tree.getroot()
    list_tag = f'{{{PML_NS}}}notesMasterIdLst'
    id_tag = f'{{{PML_NS}}}notesMasterId'
    relationship_attr = f'{{{REL_NS}}}id'
    notes_master_list = root.find(list_tag)
    if notes_master_list is not None:
        if any(
            child.tag == id_tag
            and child.get(relationship_attr) == relationship_id
            for child in notes_master_list
        ):
            return
    else:
        slide_master_list = root.find(f'{{{PML_NS}}}sldMasterIdLst')
        if slide_master_list is None:
            raise RuntimeError(
                'presentation.xml is missing p:sldMasterIdLst'
            )
        notes_master_list = ET.Element(list_tag)
        insert_at = list(root).index(slide_master_list) + 1
        root.insert(insert_at, notes_master_list)
    ET.SubElement(
        notes_master_list,
        id_tag,
        {relationship_attr: relationship_id},
    )
    _write_xml_tree(presentation_path, tree)


def _ensure_notes_master(
    extract_dir: Path,
    primary_language: str | None = None,
) -> _NotesMasterReference:
    """Reuse or create a notes master and wire it into the presentation package."""
    from .builder import (
        PML_NS,
        REL_NS,
        _NOTES_MASTER_CONTENT_TYPE,
        _NOTES_MASTER_REL_TYPE,
        _NotesMasterReference,
        _append_relationship,
        _read_relationships,
        _relationships_path_for_part,
    )
    ppt_dir = extract_dir / 'ppt'
    notes_masters_dir = ppt_dir / 'notesMasters'
    notes_masters_dir.mkdir(exist_ok=True)
    presentation_rels_path = ppt_dir / '_rels' / 'presentation.xml.rels'
    presentation_path = ppt_dir / 'presentation.xml'
    content_types = _content_type_overrides(extract_dir)
    relationships = _read_relationships(presentation_rels_path)

    try:
        presentation_root = ET.parse(presentation_path).getroot()
    except (OSError, ET.ParseError) as exc:
        raise RuntimeError(
            f'Cannot parse presentation package part: {presentation_path}'
        ) from exc
    relationship_attr = f'{{{REL_NS}}}id'
    referenced_ids = [
        elem.get(relationship_attr, '')
        for elem in presentation_root.findall(
            f'./{{{PML_NS}}}notesMasterIdLst/{{{PML_NS}}}notesMasterId'
        )
    ]
    notes_relationship_ids = [
        rel_id
        for rel_id, attrs in relationships.items()
        if attrs.get('Type') == _NOTES_MASTER_REL_TYPE
        and attrs.get('TargetMode') != 'External'
    ]
    ordered_relationship_ids = list(
        dict.fromkeys(referenced_ids + notes_relationship_ids)
    )
    relationship_id_by_part: dict[str, str] = {}
    selected_part: str | None = None
    selected_rid: str | None = None
    for rel_id in ordered_relationship_ids:
        attrs = relationships.get(rel_id)
        if (
            not attrs
            or attrs.get('Type') != _NOTES_MASTER_REL_TYPE
            or attrs.get('TargetMode') == 'External'
        ):
            continue
        target = attrs.get('Target', '')
        part_name = _notes_master_part_from_target(target)
        if part_name is None:
            raise RuntimeError(
                f'Notes master relationship {rel_id} has an invalid target: {target}'
            )
        part_name = _validate_notes_master_part(
            extract_dir,
            part_name,
            content_types,
        )
        relationship_id_by_part.setdefault(part_name, rel_id)
        if selected_part is None:
            selected_part = part_name
            selected_rid = rel_id

    if selected_part is None:
        for part_name, content_type in sorted(content_types.items()):
            if content_type != _NOTES_MASTER_CONTENT_TYPE:
                continue
            selected_part = _validate_notes_master_part(
                extract_dir,
                part_name,
                content_types,
            )
            selected_rid = relationship_id_by_part.get(selected_part)
            break

    created_theme_part: str | None = None
    if selected_part is None:
        occupied_parts = set(content_types)
        occupied_parts.update(
            part_name
            for attrs in relationships.values()
            if attrs.get('Type') == _NOTES_MASTER_REL_TYPE
            and (part_name := _notes_master_part_from_target(
                attrs.get('Target', '')
            )) is not None
        )
        index = 1
        while True:
            selected_part = f'ppt/notesMasters/notesMaster{index}.xml'
            if (
                selected_part not in occupied_parts
                and not (extract_dir / selected_part).exists()
            ):
                break
            index += 1
        notes_master_path = extract_dir / selected_part
        notes_master_path.write_text(
            create_notes_master_xml(primary_language),
            encoding='utf-8',
        )

        theme_dir = ppt_dir / 'theme'
        theme_dir.mkdir(exist_ok=True)
        theme1_path = theme_dir / 'theme1.xml'
        theme2_path = theme_dir / 'theme2.xml'
        if not theme2_path.exists():
            if theme1_path.exists():
                shutil.copy2(theme1_path, theme2_path)
            else:
                raise RuntimeError(
                    'Cannot create notes theme: ppt/theme/theme1.xml is missing'
                )
        created_theme_part = 'ppt/theme/theme2.xml'

        notes_master_rels_path = _relationships_path_for_part(
            extract_dir,
            selected_part,
        )
        notes_master_rels_path.parent.mkdir(parents=True, exist_ok=True)
        notes_master_rels_path.write_text(
            create_notes_master_rels_xml(),
            encoding='utf-8',
        )

    if selected_rid is None:
        selected_rid = _append_relationship(
            presentation_rels_path,
            _NOTES_MASTER_REL_TYPE,
            posixpath.relpath(selected_part, 'ppt'),
        )
    _register_notes_master_id(presentation_path, selected_rid)
    return _NotesMasterReference(
        package_part=selected_part,
        created_theme_part=created_theme_part,
    )
