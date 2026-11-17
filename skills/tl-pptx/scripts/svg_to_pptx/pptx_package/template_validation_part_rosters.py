"""
PPT Master - Template Package Validation

Read a generated structured PPTX back and verify its reusable Master/Layout graph.

Usage:
    Imported by svg_to_pptx.pptx_package.builder.

Examples:
    validate_pptx_template_package(Path("output.pptx"), template_specs)

Dependencies:
    None (only uses standard library and local modules)
"""
# Adapted from hugohe3/ppt-master 7e0df6705de0cd24345210d31f07fcb5a32b6900.
# MIT; Copyright (c) 2025-2026 Hugo He. See references/LICENSE-PPT-MASTER.txt and references/upstream.md.

from __future__ import annotations
import posixpath
from xml.etree import ElementTree as ET
from .template_structure import (
    OOXML_UINT32_MAX,
)

from .template_validation import (
    PRESENTATION_COLLECTION_ID_MIN,
    _PackageReader,
)

def _content_type_overrides(
    reader: _PackageReader,
) -> dict[str, list[str]]:
    root = reader.xml("[Content_Types].xml")
    overrides: dict[str, list[str]] = {}
    if root is None:
        return overrides
    for elem in root:
        if elem.tag.rsplit("}", 1)[-1] != "Override":
            continue
        part_name = elem.get("PartName", "")
        content_type = elem.get("ContentType", "")
        if part_name and content_type:
            overrides.setdefault(part_name, []).append(content_type)
    return overrides


def _validate_uint32_id_roster(
    elements: list[ET.Element],
    context: str,
    errors: list[str],
    *,
    min_value: int = 0,
) -> set[int]:
    values: set[int] = set()
    for element in elements:
        raw_value = element.get("id")
        try:
            value = int(raw_value) if raw_value is not None else -1
        except ValueError:
            value = -1
        if not min_value <= value <= OOXML_UINT32_MAX:
            errors.append(
                f"{context} contains id {raw_value!r} outside the OOXML range "
                f"{min_value}..{OOXML_UINT32_MAX}"
            )
            continue
        if value in values:
            errors.append(f"{context} repeats numeric id {value}")
            continue
        values.add(value)
    return values


def _validate_registered_part_roster(
    reader: _PackageReader,
    source_part: str,
    rel_type: str,
    entries: list[ET.Element],
    expected_targets: set[str],
    context: str,
    *,
    min_id: int = PRESENTATION_COLLECTION_ID_MIN,
) -> set[int]:
    from .template_validation import (
        REL_NS,
        _resolve_relationship_target,
    )
    relationships = [
        relationship
        for relationship in reader.relationships(source_part)
        if relationship.rel_type == rel_type
    ]
    targets_by_id: dict[str, str] = {}
    ids_by_target: dict[str, list[str]] = {}
    for relationship in relationships:
        target = _resolve_relationship_target(
            source_part,
            relationship,
            reader.errors,
            context,
        )
        if target is None:
            continue
        targets_by_id[relationship.rel_id] = target
        ids_by_target.setdefault(target, []).append(relationship.rel_id)
    for target, rel_ids in sorted(ids_by_target.items()):
        if len(rel_ids) > 1:
            reader.errors.append(
                f"{context} targets {target} through multiple relationships: "
                + ", ".join(rel_ids)
            )

    actual_targets = set(targets_by_id.values())
    if actual_targets != expected_targets:
        missing = sorted(expected_targets - actual_targets)
        extra = sorted(actual_targets - expected_targets)
        reader.errors.append(
            f"{context} registered target roster differs; missing={missing}, "
            f"extra={extra}"
        )

    entry_rel_ids: list[str] = []
    for entry in entries:
        rel_id = entry.get(f"{{{REL_NS}}}id")
        if not rel_id:
            reader.errors.append(f"{context} contains an entry without r:id")
            continue
        entry_rel_ids.append(rel_id)
    if len(entry_rel_ids) != len(set(entry_rel_ids)):
        reader.errors.append(f"{context} repeats an entry r:id")
    if set(entry_rel_ids) != {relationship.rel_id for relationship in relationships}:
        reader.errors.append(
            f"{context} entry r:id roster does not match its relationships"
        )
    return _validate_uint32_id_roster(
        entries,
        context,
        reader.errors,
        min_value=min_id,
    )


def _numbered_part_family(
    names: frozenset[str],
    directory: str,
    stem: str,
) -> set[str]:
    """Return exact numbered XML parts such as slide12.xml in one directory."""
    prefix = f"{directory}/{stem}"
    parts: set[str] = set()
    for name in names:
        if not name.startswith(prefix) or not name.endswith(".xml"):
            continue
        number = name[len(prefix):-4]
        if (
            number.isdigit()
            and int(number) > 0
            and posixpath.dirname(name) == directory
        ):
            parts.add(name)
    return parts


def _validate_physical_part_roster(
    reader: _PackageReader,
    expected_parts: set[str],
    directory: str,
    stem: str,
    context: str,
) -> None:
    actual_parts = _numbered_part_family(reader.names, directory, stem)
    if actual_parts != expected_parts:
        reader.errors.append(
            f"{context} physical part roster differs; "
            f"missing={sorted(expected_parts - actual_parts)}, "
            f"extra={sorted(actual_parts - expected_parts)}"
        )


def _validate_content_type_part_roster(
    overrides: dict[str, list[str]],
    expected_parts: set[str],
    directory: str,
    stem: str,
    content_type: str,
    errors: list[str],
    context: str,
) -> None:
    family = _numbered_part_family(
        frozenset(name.lstrip("/") for name in overrides),
        directory,
        stem,
    )
    if family != expected_parts:
        errors.append(
            f"[Content_Types].xml {context} Override roster differs; "
            f"missing={sorted(expected_parts - family)}, "
            f"extra={sorted(family - expected_parts)}"
        )
    for part in sorted(expected_parts):
        values = overrides.get(f"/{part}", [])
        if values != [content_type]:
            errors.append(
                f"[Content_Types].xml must declare {part} exactly once as "
                f"{content_type}"
            )


def _validate_presentation_master_registration(
    reader: _PackageReader,
    master_parts: set[str],
) -> None:
    from .template_validation import (
        PML_NS,
        SLIDE_MASTER_REL_TYPE,
    )
    presentation_part = "ppt/presentation.xml"
    presentation_root = reader.xml(presentation_part)
    if presentation_root is None:
        return
    master_id_entries = presentation_root.findall(
        f"{{{PML_NS}}}sldMasterIdLst/{{{PML_NS}}}sldMasterId"
    )
    _validate_registered_part_roster(
        reader,
        presentation_part,
        SLIDE_MASTER_REL_TYPE,
        master_id_entries,
        master_parts,
        "Presentation p:sldMasterIdLst",
    )


def _validate_master_theme_ownership(
    reader: _PackageReader,
    master_parts: set[str],
) -> None:
    """Require a separate Theme part for every structured Slide Master."""
    from .template_validation import (
        THEME_REL_TYPE,
        _single_relationship_target,
    )
    owners: dict[str, str] = {}
    for master_part in sorted(master_parts):
        resolved = _single_relationship_target(
            reader,
            master_part,
            THEME_REL_TYPE,
            f"Master {master_part}",
        )
        if resolved is None:
            continue
        _relationship, theme_part = resolved
        if not (
            theme_part.startswith("ppt/theme/")
            and theme_part.endswith(".xml")
        ):
            reader.errors.append(
                f"Master {master_part} targets non-Theme part {theme_part}"
            )
            continue
        reader.xml(theme_part)
        previous_owner = owners.setdefault(theme_part, master_part)
        if previous_owner != master_part:
            reader.errors.append(
                f"Theme {theme_part} is shared by structured Masters "
                f"{previous_owner} and {master_part}"
            )


def _validate_presentation_slide_registration(
    reader: _PackageReader,
    slide_parts: set[str],
) -> None:
    from .template_validation import (
        PML_NS,
        PRESENTATION_SLIDE_ID_MIN,
        SLIDE_REL_TYPE,
    )
    presentation_part = "ppt/presentation.xml"
    presentation_root = reader.xml(presentation_part)
    if presentation_root is None:
        return
    slide_id_entries = presentation_root.findall(
        f"{{{PML_NS}}}sldIdLst/{{{PML_NS}}}sldId"
    )
    _validate_registered_part_roster(
        reader,
        presentation_part,
        SLIDE_REL_TYPE,
        slide_id_entries,
        slide_parts,
        "Presentation p:sldIdLst",
        min_id=PRESENTATION_SLIDE_ID_MIN,
    )
