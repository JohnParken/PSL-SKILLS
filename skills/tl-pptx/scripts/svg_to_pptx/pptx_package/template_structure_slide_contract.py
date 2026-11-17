"""
PPT Master - Template Structure Metadata

Parse and validate explicit SVG metadata consumed by structured PPTX export.

Usage:
    Imported by svg_to_pptx.pptx_package.builder and svg_quality_checker.py.

Examples:
    parse_template_slides([Path("projects/demo/svg_output/01_cover.svg")])

Dependencies:
    None (only uses standard library)
"""
# Adapted from hugohe3/ppt-master 7e0df6705de0cd24345210d31f07fcb5a32b6900.
# MIT; Copyright (c) 2025-2026 Hugo He. See references/LICENSE-PPT-MASTER.txt and references/upstream.md.

from __future__ import annotations
from pathlib import Path
from xml.etree import ElementTree as ET
from native_payloads import (
    NativePayloadError,
)

from .template_structure import (
    PptxStructureLock,
    TemplateSlideSpec,
)

def _validate_template_slide_contracts(
    specs: list[TemplateSlideSpec],
) -> None:
    """Enforce cross-prototype Master and Layout structure identity."""
    from .template_structure import (
        TemplateStructureError,
        template_placeholder_bindings,
    )
    by_master: dict[str, list[TemplateSlideSpec]] = {}
    for spec in specs:
        by_master.setdefault(spec.master_key, []).append(spec)
    for master_key, master_specs in by_master.items():
        prototype = master_specs[0]
        expected_master = tuple(
            item.contract_signature() for item in prototype.master_elements
        )
        for spec in master_specs[1:]:
            if spec.master_name != prototype.master_name:
                raise TemplateStructureError(
                    f"{spec.svg_path.name}: Master {master_key!r} uses name "
                    f"{spec.master_name!r}, expected {prototype.master_name!r}"
                )
            actual_master = tuple(
                item.contract_signature() for item in spec.master_elements
            )
            if actual_master != expected_master:
                raise TemplateStructureError(
                    f"{spec.svg_path.name}: Master {master_key!r} contract differs "
                    f"from {prototype.svg_path.name}; slides sharing one Master must "
                    "repeat the same root-level atoms in the same order"
                )

    by_layout: dict[str, list[TemplateSlideSpec]] = {}
    for spec in specs:
        by_layout.setdefault(spec.layout_key, []).append(spec)
    for layout_key, layout_specs in by_layout.items():
        prototype = layout_specs[0]
        template_placeholder_bindings(prototype)
        for spec in layout_specs[1:]:
            if spec.layout_name != prototype.layout_name:
                raise TemplateStructureError(
                    f"{spec.svg_path.name}: layout {layout_key!r} uses name "
                    f"{spec.layout_name!r}, expected {prototype.layout_name!r}"
                )
            if spec.master_key != prototype.master_key:
                raise TemplateStructureError(
                    f"{spec.svg_path.name}: globally unique layout {layout_key!r} "
                    f"belongs to Master {spec.master_key!r}, expected "
                    f"{prototype.master_key!r}"
                )
            if (
                spec.layout_show_master_shapes
                != prototype.layout_show_master_shapes
            ):
                raise TemplateStructureError(
                    f"{spec.svg_path.name}: layout {layout_key!r} uses "
                    "data-pptx-show-master-shapes="
                    f"{str(spec.layout_show_master_shapes).lower()}, expected "
                    f"{str(prototype.layout_show_master_shapes).lower()}"
                )
            if spec.layout_contract != prototype.layout_contract:
                raise TemplateStructureError(
                    f"{spec.svg_path.name}: layout {layout_key!r} structure differs "
                    f"from prototype {prototype.svg_path.name}; repeat the same layout "
                    "layers and placeholder ids/types in the same order"
                )


def parse_template_slides(svg_files: list[Path]) -> list[TemplateSlideSpec]:
    """Parse a deck and enforce cross-slide master/layout contracts."""
    from .template_structure import (
        TemplateStructureError,
        parse_template_slide,
    )
    specs = [
        parse_template_slide(svg_path, slide_num)
        for slide_num, svg_path in enumerate(svg_files, start=1)
    ]
    if not specs:
        raise TemplateStructureError(
            "Explicit Layout export requires at least one SVG slide"
        )
    _validate_template_slide_contracts(specs)
    return specs


def parse_optional_layout_slides(
    svg_files: list[Path],
) -> list[TemplateSlideSpec] | None:
    """Parse an all-or-none structured Layout contract, or return no metadata."""
    from .template_structure import (
        TemplateStructureError,
        _STRUCTURE_ATTRS,
        _parse_svg_root,
    )
    roots: list[tuple[Path, ET.Element]] = []
    has_structure_metadata = False
    for svg_path in svg_files:
        try:
            root = _parse_svg_root(svg_path)
        except (OSError, ET.ParseError, NativePayloadError) as exc:
            raise TemplateStructureError(
                f"{svg_path.name}: unable to inspect SVG Layout metadata: {exc}"
            ) from exc
        roots.append((svg_path, root))
        has_structure_metadata = has_structure_metadata or any(
            elem.get(attr) is not None
            for elem in root.iter()
            for attr in _STRUCTURE_ATTRS
        )

    if not has_structure_metadata:
        return None

    missing_master_keys = [
        svg_path.name
        for svg_path, root in roots
        if not (root.get("data-pptx-master") or "").strip()
    ]
    missing_master_names = [
        svg_path.name
        for svg_path, root in roots
        if not (root.get("data-pptx-master-name") or "").strip()
    ]
    missing_keys = [
        svg_path.name
        for svg_path, root in roots
        if not (root.get("data-pptx-layout") or "").strip()
    ]
    missing_names = [
        svg_path.name
        for svg_path, root in roots
        if not (root.get("data-pptx-layout-name") or "").strip()
    ]
    if missing_master_keys or missing_master_names or missing_keys or missing_names:
        missing_fields: list[str] = []
        if missing_master_keys:
            missing_fields.append(
                "data-pptx-master: " + ", ".join(missing_master_keys)
            )
        if missing_master_names:
            missing_fields.append(
                "data-pptx-master-name: " + ", ".join(missing_master_names)
            )
        if missing_keys:
            missing_fields.append(
                "data-pptx-layout: " + ", ".join(missing_keys)
            )
        if missing_names:
            missing_fields.append(
                "data-pptx-layout-name: " + ", ".join(missing_names)
            )
        raise TemplateStructureError(
            "Explicit Layout metadata is all-or-none: once any SVG uses PPTX "
            "structure metadata, every generated page root must declare "
            "Master/Layout keys and names with non-empty values; "
            "missing "
            + "; ".join(missing_fields)
        )
    return parse_template_slides(svg_files)


def flat_structure_metadata_errors(svg_files: list[Path]) -> list[str]:
    """Return every Master/Layout marker forbidden by flat export."""
    from .template_structure import (
        _FLAT_FORBIDDEN_STRUCTURE_ATTRS,
        _local_tag,
        _parse_svg_root,
    )
    errors: list[str] = []
    for svg_path in svg_files:
        try:
            root = _parse_svg_root(svg_path)
        except (OSError, ET.ParseError, NativePayloadError) as exc:
            errors.append(
                f"{svg_path.name}: unable to inspect flat-mode metadata: {exc}"
            )
            continue
        for elem in root.iter():
            attrs = sorted(
                attr
                for attr in _FLAT_FORBIDDEN_STRUCTURE_ATTRS
                if elem.get(attr) is not None
            )
            if not attrs:
                continue
            element_id = (elem.get("id") or _local_tag(elem)).strip()
            errors.append(
                f"{svg_path.name}: flat mode forbids Master/Layout structure "
                f"metadata on {element_id!r}: " + ", ".join(attrs)
            )
    return errors


def parse_preserve_slides(svg_files: list[Path]) -> list[TemplateSlideSpec]:
    """Parse preserve-mode slides before source master grouping is known."""
    from .template_structure import (
        TemplateStructureError,
        parse_template_slide,
    )
    specs = [
        parse_template_slide(svg_path, slide_num, structured=False)
        for slide_num, svg_path in enumerate(svg_files, start=1)
    ]
    if not specs:
        raise TemplateStructureError("Preserve export requires at least one SVG slide")
    return specs


def structured_layout_definition_files(
    specs: list[TemplateSlideSpec],
    structure_lock: PptxStructureLock,
) -> list[Path]:
    """Validate the explicit registered Layout set and return unused carriers.

    A generated page can be the carrier for a used Layout definition. A Layout
    with no generated page must point at one installed template SVG; the builder
    converts that SVG on an internal trailing slide and removes the carrier slide
    after registering the reusable Layout.
    Installed prototypes absent from ``pptx_layouts`` are never compiled.
    Lockless Quick export compiles only its public pages' Layouts.
    """
    from .template_structure import (
        TemplateStructureError,
        parse_template_slide,
    )
    if structure_lock.mode != "structured":
        return []
    definitions = {
        definition.layout_key: definition
        for definition in structure_lock.layout_definitions
    }
    specs_by_slide = {spec.slide_num: spec for spec in specs}
    used_layout_keys = {spec.layout_key for spec in specs}
    master_names = {
        master.master_key: master.master_name
        for master in structure_lock.masters
    }
    combined_specs = list(specs)
    definition_files: list[Path] = []
    next_slide_num = max(specs_by_slide, default=0) + 1
    for definition in structure_lock.layout_definitions:
        if definition.prototype_slide_num is not None:
            prototype = specs_by_slide.get(definition.prototype_slide_num)
            if prototype is None:
                raise TemplateStructureError(
                    f"spec_lock.md Layout {definition.layout_key!r} uses missing "
                    f"prototype page P{definition.prototype_slide_num:02d}"
                )
        elif definition.prototype_svg_path is not None:
            prototype = parse_template_slide(
                definition.prototype_svg_path,
                next_slide_num,
            )
            next_slide_num += 1
            combined_specs.append(prototype)
            if definition.layout_key not in used_layout_keys:
                definition_files.append(definition.prototype_svg_path)
        else:
            raise TemplateStructureError(
                f"spec_lock.md Layout {definition.layout_key!r} has no prototype"
            )
        expected_master_name = master_names.get(definition.master_key)
        if (
            prototype.layout_key != definition.layout_key
            or prototype.layout_name != definition.layout_name
            or prototype.master_key != definition.master_key
            or prototype.master_name != expected_master_name
        ):
            raise TemplateStructureError(
                f"spec_lock.md Layout {definition.layout_key!r} definition does "
                f"not match prototype {prototype.svg_path.name} root identity"
            )
    missing_definitions = sorted(used_layout_keys - set(definitions))
    if missing_definitions:
        raise TemplateStructureError(
            "spec_lock.md pptx_layouts is missing generated Layout key(s): "
            + ", ".join(missing_definitions)
        )
    _validate_template_slide_contracts(combined_specs)
    return definition_files
