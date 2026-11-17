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

from .template_structure import (
    PptxStructureLock,
    TemplateSlideSpec,
)

def template_prototype_lock_errors(
    structure_lock: PptxStructureLock,
) -> list[str]:
    """Validate selected input prototypes before generated pages exist.

    ``page_layouts`` records authoring-input provenance. Strict execution keeps
    that prototype's Layout identity, while adaptive execution may declare a
    new Layout under the same Master. Final generated SVGs remain subject to
    :func:`template_lock_errors` and :func:`template_prototype_errors`.
    """
    from .template_structure import (
        TemplateStructureError,
        _validate_template_slide_contracts,
        parse_template_slide,
    )
    if structure_lock.mode != "structured":
        return []

    errors: list[str] = []
    specs: list[TemplateSlideSpec] = []
    for prototype in structure_lock.prototypes:
        try:
            specs.append(
                parse_template_slide(prototype.svg_path, prototype.slide_num)
            )
        except TemplateStructureError as exc:
            errors.append(str(exc))
    if errors:
        return list(dict.fromkeys(errors))

    try:
        _validate_template_slide_contracts(specs)
    except TemplateStructureError as exc:
        errors.append(str(exc))

    assignments = {
        reference.slide_num: reference
        for reference in structure_lock.layouts
    }
    definitions = {
        definition.layout_key: definition
        for definition in structure_lock.layout_definitions
    }
    master_names = {
        master.master_key: master.master_name
        for master in structure_lock.masters
    }
    prototype_pages = {spec.slide_num for spec in specs}
    assignment_pages = set(assignments)
    missing_assignments = sorted(prototype_pages - assignment_pages)
    missing_prototypes = sorted(assignment_pages - prototype_pages)
    if missing_assignments:
        errors.append(
            "spec_lock.md page_pptx_layouts is missing generated page(s): "
            + ", ".join(
                f"P{slide_num:02d}" for slide_num in missing_assignments
            )
        )
    if missing_prototypes:
        errors.append(
            "spec_lock.md page_layouts is missing generated page(s): "
            + ", ".join(f"P{slide_num:02d}" for slide_num in missing_prototypes)
        )

    adherence = structure_lock.template_adherence or "strict"
    for spec in specs:
        assignment = assignments.get(spec.slide_num)
        if assignment is None:
            continue
        definition = definitions.get(assignment.layout_key)
        if definition is None:
            errors.append(
                f"spec_lock.md P{spec.slide_num:02d} references undeclared "
                f"Layout {assignment.layout_key!r}"
            )
            continue
        expected_master_name = master_names.get(definition.master_key)
        if (
            spec.master_key != definition.master_key
            or spec.master_name != expected_master_name
        ):
            errors.append(
                f"{spec.svg_path.name}: input prototype Master "
                f"{spec.master_key!r} / {spec.master_name!r} does not match "
                f"assigned Layout {definition.layout_key!r} Master "
                f"{definition.master_key!r} / {expected_master_name!r}"
            )

        reuses_input_layout = spec.layout_key == definition.layout_key
        must_match_layout = (
            adherence == "strict"
            or reuses_input_layout
            or definition.prototype_svg_path is not None
        )
        if must_match_layout and (
            spec.layout_key != definition.layout_key
            or spec.layout_name != definition.layout_name
        ):
            errors.append(
                f"{spec.svg_path.name}: input prototype Layout "
                f"{spec.layout_key!r} / {spec.layout_name!r} does not match "
                f"assigned Layout {definition.layout_key!r} / "
                f"{definition.layout_name!r}"
            )
        elif (
            adherence == "adaptive"
            and not reuses_input_layout
            and spec.layout_name == definition.layout_name
        ):
            errors.append(
                f"{spec.svg_path.name}: adaptive output Layout "
                f"{definition.layout_key!r} must use a new picker name instead "
                f"of input prototype name {spec.layout_name!r}"
            )

    definition_specs = list(specs)
    next_slide_num = max(prototype_pages, default=0) + 1
    for definition in structure_lock.layout_definitions:
        if definition.prototype_slide_num is not None:
            source_assignment = assignments.get(definition.prototype_slide_num)
            if source_assignment is None:
                errors.append(
                    f"spec_lock.md Layout {definition.layout_key!r} uses missing "
                    f"prototype page P{definition.prototype_slide_num:02d}"
                )
            elif source_assignment.layout_key != definition.layout_key:
                errors.append(
                    f"spec_lock.md Layout {definition.layout_key!r} uses "
                    f"P{definition.prototype_slide_num:02d}, but that page is "
                    f"assigned to Layout {source_assignment.layout_key!r}"
                )
            continue
        if definition.prototype_svg_path is None:
            errors.append(
                f"spec_lock.md Layout {definition.layout_key!r} has no prototype"
            )
            continue
        try:
            definition_spec = parse_template_slide(
                definition.prototype_svg_path,
                next_slide_num,
            )
            next_slide_num += 1
        except TemplateStructureError as exc:
            errors.append(str(exc))
            continue
        definition_specs.append(definition_spec)
        expected_master_name = master_names.get(definition.master_key)
        if (
            definition_spec.layout_key != definition.layout_key
            or definition_spec.layout_name != definition.layout_name
            or definition_spec.master_key != definition.master_key
            or definition_spec.master_name != expected_master_name
        ):
            errors.append(
                f"spec_lock.md Layout {definition.layout_key!r} definition does "
                f"not match prototype {definition_spec.svg_path.name} root identity"
            )

    try:
        _validate_template_slide_contracts(definition_specs)
    except TemplateStructureError as exc:
        errors.append(str(exc))
    return list(dict.fromkeys(errors))


def template_lock_errors(
    specs: list[TemplateSlideSpec],
    structure_lock: PptxStructureLock,
) -> list[str]:
    """Return mismatches between parsed SVG layouts and the project lock."""
    from .template_structure import (
        TemplateStructureError,
        structured_layout_definition_files,
    )
    if structure_lock.mode not in {"structured", "preserve"}:
        return []
    errors: list[str] = []
    references = {
        reference.slide_num: reference
        for reference in structure_lock.layouts
    }
    actual_slides = {spec.slide_num for spec in specs}
    expected_slides = set(references)
    missing = sorted(actual_slides - expected_slides)
    extra = sorted(expected_slides - actual_slides)
    assignment_section = (
        "page_pptx_layouts"
        if structure_lock.mode == "structured"
        else "pptx_layouts"
    )
    if missing:
        pages = ", ".join(f"P{slide_num:02d}" for slide_num in missing)
        errors.append(
            f"spec_lock.md {assignment_section} is missing generated page(s): "
            f"{pages}"
        )
    if extra:
        pages = ", ".join(f"P{slide_num:02d}" for slide_num in extra)
        errors.append(
            f"spec_lock.md {assignment_section} references absent page(s): "
            f"{pages}"
        )
    for spec in specs:
        reference = references.get(spec.slide_num)
        if reference is None:
            continue
        if spec.layout_key != reference.layout_key:
            errors.append(
                f"{spec.svg_path.name}: data-pptx-layout={spec.layout_key!r} "
                f"does not match spec_lock P{spec.slide_num:02d} Layout key "
                f"{reference.layout_key!r}"
            )
        if structure_lock.mode == "preserve":
            if reference.layout_name and spec.layout_name != reference.layout_name:
                errors.append(
                    f"{spec.svg_path.name}: data-pptx-layout-name="
                    f"{spec.layout_name!r} does not match spec_lock "
                    f"P{spec.slide_num:02d} Layout name "
                    f"{reference.layout_name!r}"
                )
    if structure_lock.mode == "structured":
        definitions = {
            definition.layout_key: definition
            for definition in structure_lock.layout_definitions
        }
        master_names = {
            master.master_key: master.master_name for master in structure_lock.masters
        }
        for spec in specs:
            definition = definitions.get(spec.layout_key)
            if definition is not None:
                if spec.layout_name != definition.layout_name:
                    errors.append(
                        f"{spec.svg_path.name}: data-pptx-layout-name="
                        f"{spec.layout_name!r} does not match Layout "
                        f"{spec.layout_key!r} name {definition.layout_name!r}"
                    )
                if spec.master_key != definition.master_key:
                    errors.append(
                        f"{spec.svg_path.name}: data-pptx-master="
                        f"{spec.master_key!r} does not match Layout "
                        f"{spec.layout_key!r} Master {definition.master_key!r}"
                    )
            expected_name = master_names.get(spec.master_key)
            if expected_name is not None and spec.master_name != expected_name:
                errors.append(
                    f"{spec.svg_path.name}: data-pptx-master-name="
                    f"{spec.master_name!r} does not match spec_lock Master "
                    f"{spec.master_key!r} name {expected_name!r}"
                )
        try:
            structured_layout_definition_files(specs, structure_lock)
        except TemplateStructureError as exc:
            errors.append(str(exc))
    return errors
