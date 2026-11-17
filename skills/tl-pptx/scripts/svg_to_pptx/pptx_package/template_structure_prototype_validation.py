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

def template_prototype_errors(
    specs: list[TemplateSlideSpec],
    structure_lock: PptxStructureLock,
    *,
    require_complete_roster: bool = True,
) -> list[str]:
    """Compare structured template pages with their selected SVG prototypes."""
    from .template_structure import (
        TemplateStructureError,
        _layout_contract_difference,
        _mirror_ordinary_slide_ids,
        _mirror_slide_local_difference,
        _mirror_slide_local_signature,
        _mirror_structure_difference,
        _prototype_placeholder_contract,
        _structure_subtree_signature,
        parse_template_slide,
    )
    if structure_lock.mode != "structured" or not structure_lock.prototypes:
        return []
    errors: list[str] = []
    prototypes = {
        reference.slide_num: reference
        for reference in structure_lock.prototypes
    }
    adherence = structure_lock.template_adherence or "strict"
    actual_slides = {spec.slide_num for spec in specs}
    prototype_slides = set(prototypes)
    missing_prototypes = sorted(actual_slides - prototype_slides)
    extra_prototypes = sorted(prototype_slides - actual_slides)
    if missing_prototypes:
        errors.append(
            "spec_lock.md page_layouts is missing generated page(s): "
            + ", ".join(f"P{slide_num:02d}" for slide_num in missing_prototypes)
        )
    if require_complete_roster and extra_prototypes:
        errors.append(
            "spec_lock.md page_layouts references absent page(s): "
            + ", ".join(f"P{slide_num:02d}" for slide_num in extra_prototypes)
        )
    for spec in specs:
        reference = prototypes.get(spec.slide_num)
        if reference is None:
            errors.append(
                f"{spec.svg_path.name}: spec_lock.md page_layouts is missing "
                f"prototype P{spec.slide_num:02d}"
            )
            continue
        try:
            prototype = parse_template_slide(reference.svg_path, spec.slide_num)
        except TemplateStructureError as exc:
            errors.append(str(exc))
            continue

        try:
            literal_visual = (
                structure_lock.template_reuse_scope == "mirror"
                if structure_lock.template_reuse_scope is not None
                else reference.replication_mode == "mirror"
            )
            expected_master_structure = _structure_subtree_signature(
                prototype.svg_path,
                prototype.master_elements,
                include_skin=literal_visual,
                asset_identity=literal_visual,
                ignore_json_native_preview=literal_visual,
            )
            actual_master_structure = _structure_subtree_signature(
                spec.svg_path,
                spec.master_elements,
                include_skin=literal_visual,
                asset_identity=literal_visual,
                ignore_json_native_preview=literal_visual,
            )
        except TemplateStructureError as exc:
            errors.append(str(exc))
            continue
        if (
            spec.master_key != prototype.master_key
            or spec.master_name != prototype.master_name
            or tuple(item.contract_signature() for item in spec.master_elements)
            != tuple(
                item.contract_signature() for item in prototype.master_elements
            )
            or actual_master_structure != expected_master_structure
        ):
            master_difference = (
                _mirror_structure_difference(
                    prototype,
                    spec,
                    prototype.master_elements,
                )
                if literal_visual
                else None
            )
            errors.append(
                f"{spec.svg_path.name}: template Master structure differs "
                f"from prototype {reference.svg_path.name}; strict and adaptive "
                "routes must retain its ids, topology, geometry, data-pptx-* "
                "metadata, and content"
                + (
                    " including mirror visual styling"
                    if literal_visual
                    else "; fills, strokes, and gradients may be repainted"
                )
                + (
                    f"; first difference: {master_difference}"
                    if master_difference else ""
                )
            )

        if literal_visual:
            try:
                protected_slide_ids = (
                    _mirror_ordinary_slide_ids(prototype)
                    | _mirror_ordinary_slide_ids(spec)
                )
                expected_slide_visual = _mirror_slide_local_signature(
                    prototype,
                    protected_slide_ids,
                )
                actual_slide_visual = _mirror_slide_local_signature(
                    spec,
                    protected_slide_ids,
                )
            except TemplateStructureError as exc:
                errors.append(str(exc))
                continue
            if actual_slide_visual != expected_slide_visual:
                difference = _mirror_slide_local_difference(
                    prototype,
                    spec,
                    protected_slide_ids,
                )
                errors.append(
                    f"{spec.svg_path.name}: mirror Slide-local non-text visuals "
                    f"differ from prototype {reference.svg_path.name}; preserve "
                    "grouping, geometry, paint, effects, and referenced asset "
                    "identity; only visible text content and JSON-first derived "
                    "preview children may change"
                    + (f"; first difference: {difference}" if difference else "")
                )

        try:
            expected_layout_structure = _structure_subtree_signature(
                prototype.svg_path,
                prototype.layout_elements,
                include_skin=literal_visual,
                asset_identity=literal_visual,
                ignore_json_native_preview=literal_visual,
            )
            actual_layout_structure = _structure_subtree_signature(
                spec.svg_path,
                spec.layout_elements,
                include_skin=literal_visual,
                asset_identity=literal_visual,
                ignore_json_native_preview=literal_visual,
            )
            if literal_visual:
                expected_placeholder_visual = _structure_subtree_signature(
                    prototype.svg_path,
                    prototype.placeholders,
                    include_skin=True,
                    include_text=False,
                    asset_identity=True,
                    ignore_json_native_preview=True,
                )
                actual_placeholder_visual = _structure_subtree_signature(
                    spec.svg_path,
                    spec.placeholders,
                    include_skin=True,
                    include_text=False,
                    asset_identity=True,
                    ignore_json_native_preview=True,
                )
            else:
                expected_placeholder_visual = ()
                actual_placeholder_visual = ()
        except TemplateStructureError as exc:
            errors.append(str(exc))
            continue

        placeholder_contract_same = (
            _prototype_placeholder_contract(spec)
            == _prototype_placeholder_contract(prototype)
        )
        layout_contract_same = (
            spec.layout_show_master_shapes
            == prototype.layout_show_master_shapes
            and tuple(item.contract_signature() for item in spec.layout_elements)
            == tuple(
                item.contract_signature() for item in prototype.layout_elements
            )
            and actual_layout_structure == expected_layout_structure
        )
        placeholder_visual_same = (
            actual_placeholder_visual == expected_placeholder_visual
        )
        reusable_contract_same = (
            placeholder_contract_same
            and layout_contract_same
            and placeholder_visual_same
        )

        reuses_prototype_key = spec.layout_key == prototype.layout_key
        if adherence == "adaptive" and not reuses_prototype_key:
            if spec.layout_name == prototype.layout_name:
                errors.append(
                    f"{spec.svg_path.name}: adaptive template authoring created "
                    f"new layout key {spec.layout_key!r} but reused prototype picker "
                    f"name {prototype.layout_name!r}; assign a new key and name to "
                    "the evolved Layout contract"
                )
            if reusable_contract_same:
                errors.append(
                    f"{spec.svg_path.name}: adaptive template authoring changed "
                    "only the Layout key/name while the reusable static, "
                    "placeholder, and default-bounds contract is unchanged; "
                    f"reuse prototype identity {prototype.layout_key!r} / "
                    f"{prototype.layout_name!r}"
                )
            continue

        missing_bounds = [
            item.element_id
            for item in prototype.placeholders
            if item.placeholder_bounds is None
        ]
        if missing_bounds:
            if adherence == "strict":
                errors.append(
                    f"{reference.svg_path.name}: deferred strict template "
                    "authoring requires explicit data-pptx-bounds "
                    "on every prototype placeholder; missing: "
                    + ", ".join(missing_bounds)
                )
            else:
                errors.append(
                    f"{spec.svg_path.name}: adaptive output reused prototype layout "
                    f"key {prototype.layout_key!r}, but that prototype lacks explicit "
                    "placeholder bounds; assign a new key and name to the evolved "
                    "Layout contract"
                )
            continue
        if spec.layout_key != prototype.layout_key:
            errors.append(
                f"{spec.svg_path.name}: strict template use must keep "
                f"prototype layout key {prototype.layout_key!r}, found "
                f"{spec.layout_key!r}"
            )
        if spec.layout_name != prototype.layout_name:
            if adherence == "strict":
                errors.append(
                    f"{spec.svg_path.name}: strict template use must keep "
                    f"prototype layout name {prototype.layout_name!r}, found "
                    f"{spec.layout_name!r}"
                )
            else:
                errors.append(
                    f"{spec.svg_path.name}: adaptive output reused prototype layout "
                    f"key {prototype.layout_key!r} but changed its picker name from "
                    f"{prototype.layout_name!r} to {spec.layout_name!r}; assign a "
                    "new key and name to the evolved Layout contract"
                )
        if (
            spec.slide_show_inherited_shapes
            != prototype.slide_show_inherited_shapes
        ):
            errors.append(
                f"{spec.svg_path.name}: inherited-shape visibility differs from "
                f"prototype {reference.svg_path.name}; keep root "
                "data-pptx-show-inherited-shapes unchanged"
            )
        if not placeholder_contract_same:
            if adherence == "strict":
                errors.append(
                    f"{spec.svg_path.name}: strict placeholder id/type/index/default-"
                    f"bounds contract differs from prototype "
                    f"{reference.svg_path.name}"
                )
            else:
                errors.append(
                    f"{spec.svg_path.name}: adaptive output reused prototype layout "
                    f"key {prototype.layout_key!r} but changed its placeholder "
                    "contract; assign a new key and name"
                )
        if literal_visual and not placeholder_visual_same:
            difference = _mirror_structure_difference(
                prototype,
                spec,
                prototype.placeholders,
            )
            errors.append(
                f"{spec.svg_path.name}: mirror placeholder geometry or visual "
                f"styling differs from prototype {reference.svg_path.name}; "
                "only visible text content may change under the reused Layout"
                + (f"; first difference: {difference}" if difference else "")
            )
        if not layout_contract_same:
            qualifier = "mirror visual/structural" if literal_visual else "structural"
            difference = _layout_contract_difference(
                spec.layout_elements,
                prototype.layout_elements,
            )
            if literal_visual:
                literal_difference = _mirror_structure_difference(
                    prototype,
                    spec,
                    prototype.layout_elements,
                )
                if literal_difference:
                    difference += f"; first difference: {literal_difference}"
            if adherence == "strict":
                errors.append(
                    f"{spec.svg_path.name}: strict Layout {qualifier} contract "
                    f"differs from prototype {reference.svg_path.name}; {difference}"
                )
            else:
                errors.append(
                    f"{spec.svg_path.name}: adaptive output reused prototype layout "
                    f"key {prototype.layout_key!r} but changed its {qualifier} "
                    f"contract; {difference}; assign a new key and name"
                )
    return errors
