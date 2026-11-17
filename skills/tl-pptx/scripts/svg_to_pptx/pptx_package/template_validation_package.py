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
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET
from ..drawingml.utils import EMU_PER_PX
from .template_structure import (
    TemplatePlaceholderBinding,
    TemplateSlideSpec,
    TemplateStructureError,
    is_proxy_placeholder,
    template_placeholder_bindings,
)

from .template_validation import (
    _Placeholder,
)

def validate_pptx_template_package(
    pptx_path: str | Path,
    specs: list[TemplateSlideSpec],
    *,
    layout_specs: list[TemplateSlideSpec] | None = None,
    expected_layout_parts: dict[str, str] | None = None,
    expected_master_parts: dict[str, str] | None = None,
    expected_backgrounds: dict[str, str | None] | None = None,
    expected_shape_rosters: dict[str, tuple[str, ...]] | None = None,
) -> None:
    """Validate a finished structured PPTX against its explicit SVG contract.

    ``specs`` describes published slides. ``layout_specs`` may additionally
    include internal prototypes for unused Layouts. Export supplies exact part,
    background, and shape expectations for deterministic serialization
    read-back. Callers with only published-slide specs retain the original
    portable relationship checks.
    """
    from .template_validation import (
        DML_NS,
        PML_NS,
        SLIDE_CONTENT_TYPE,
        SLIDE_LAYOUT_CONTENT_TYPE,
        SLIDE_LAYOUT_REL_TYPE,
        SLIDE_MASTER_CONTENT_TYPE,
        SLIDE_MASTER_REL_TYPE,
        _PackageReader,
        _background_payload_signatures,
        _content_type_overrides,
        _expected_background_signature,
        _first_run_size,
        _has_explicit_background,
        _level_one_default_size,
        _shape_bounds,
        _single_relationship_target,
        _top_level_shape_by_name,
        _validate_content_type_part_roster,
        _validate_layout_header_footer,
        _validate_master_theme_ownership,
        _validate_named_shape_roster,
        _validate_physical_part_roster,
        _validate_placeholder_roster,
        _validate_presentation_master_registration,
        _validate_presentation_slide_registration,
        _validate_registered_part_roster,
        _validate_unique_creation_ids,
    )
    if not specs:
        raise ValueError("structured package validation requires at least one slide spec")

    structure_specs = layout_specs or specs
    specs_by_layout: dict[str, list[TemplateSlideSpec]] = {}
    public_specs_by_layout: dict[str, list[TemplateSlideSpec]] = {}
    bindings_by_layout: dict[str, tuple[TemplatePlaceholderBinding, ...]] = {}
    try:
        for spec in structure_specs:
            specs_by_layout.setdefault(spec.layout_key, []).append(spec)
        for spec in specs:
            public_specs_by_layout.setdefault(spec.layout_key, []).append(spec)
        for layout_key, layout_specs in specs_by_layout.items():
            bindings_by_layout[layout_key] = template_placeholder_bindings(
                layout_specs[0]
            )
    except TemplateStructureError as exc:
        raise ValueError(str(exc)) from exc

    errors: list[str] = []
    path = Path(pptx_path)
    try:
        with zipfile.ZipFile(path) as package:
            reader = _PackageReader(package, errors)
            overrides = _content_type_overrides(reader)
            if expected_backgrounds is not None:
                for part, background_xml in sorted(expected_backgrounds.items()):
                    root = reader.xml(part)
                    if root is None:
                        continue
                    if background_xml is None:
                        expected_signatures: tuple[tuple[object, ...], ...] = ()
                    else:
                        try:
                            expected_signatures = (
                                _expected_background_signature(background_xml),
                            )
                        except ValueError as exc:
                            errors.append(f"{part} {exc}")
                            continue
                    actual_signatures = _background_payload_signatures(root)
                    if actual_signatures != expected_signatures:
                        errors.append(
                            f"{part} p:bg count/payload differs from the exact "
                            "structured-export expectation"
                        )
            if expected_shape_rosters is not None:
                for part, roster in sorted(expected_shape_rosters.items()):
                    root = reader.xml(part)
                    if root is None:
                        continue
                    _validate_named_shape_roster(
                        root,
                        roster,
                        part,
                        errors,
                        exact=True,
                        ordered=True,
                    )
            layout_parts_by_key = dict(expected_layout_parts or {})
            keys_by_layout_part = {
                part: key for key, part in layout_parts_by_key.items()
            }
            if len(keys_by_layout_part) != len(layout_parts_by_key):
                errors.append(
                    "expected Layout part mapping assigns one part to multiple keys"
                )
            if expected_layout_parts is not None and (
                set(expected_layout_parts) != set(specs_by_layout)
            ):
                errors.append(
                    "expected Layout key roster differs from the SVG contract; "
                    f"missing={sorted(set(specs_by_layout) - set(expected_layout_parts))}, "
                    f"extra={sorted(set(expected_layout_parts) - set(specs_by_layout))}"
                )
            slide_roots: dict[int, ET.Element] = {}

            for spec in specs:
                slide_part = f"ppt/slides/slide{spec.slide_num}.xml"
                slide_root = reader.xml(slide_part)
                if slide_root is not None:
                    slide_roots[spec.slide_num] = slide_root
                    raw_show_inherited = (
                        slide_root.get("showMasterSp", "1").strip().lower()
                    )
                    if raw_show_inherited not in {"0", "1", "false", "true"}:
                        errors.append(
                            f"{slide_part} showMasterSp is not a valid boolean"
                        )
                    elif (
                        (raw_show_inherited in {"1", "true"})
                        != spec.slide_show_inherited_shapes
                    ):
                        errors.append(
                            f"{slide_part} showMasterSp={raw_show_inherited}, "
                            "expected "
                            f"{str(spec.slide_show_inherited_shapes).lower()}"
                        )
                relationship_target = _single_relationship_target(
                    reader,
                    slide_part,
                    SLIDE_LAYOUT_REL_TYPE,
                    f"Slide {spec.slide_num}",
                )
                if relationship_target is None:
                    continue
                _relationship, layout_part = relationship_target
                if not (
                    layout_part.startswith("ppt/slideLayouts/")
                    and layout_part.endswith(".xml")
                ):
                    errors.append(
                        f"Slide {spec.slide_num} targets non-Layout part {layout_part}"
                    )
                    continue
                reader.xml(layout_part)
                previous_part = layout_parts_by_key.setdefault(spec.layout_key, layout_part)
                if previous_part != layout_part:
                    errors.append(
                        f"layout key {spec.layout_key!r} targets both {previous_part} "
                        f"and {layout_part}"
                    )
                previous_key = keys_by_layout_part.setdefault(
                    layout_part,
                    spec.layout_key,
                )
                if previous_key != spec.layout_key:
                    errors.append(
                        f"layout keys {previous_key!r} and {spec.layout_key!r} both "
                        f"target {layout_part}"
                    )

            used_master_parts: set[str] = set()
            layout_parts_by_master: dict[str, set[str]] = {}
            master_specs_by_part: dict[str, TemplateSlideSpec] = {}
            master_parts_by_key = dict(expected_master_parts or {})
            keys_by_master_part = {
                part: key for key, part in master_parts_by_key.items()
            }
            expected_master_keys = {
                spec.master_key for spec in structure_specs
            }
            if len(keys_by_master_part) != len(master_parts_by_key):
                errors.append(
                    "expected Master part mapping assigns one part to multiple keys"
                )
            if expected_master_parts is not None and (
                set(expected_master_parts) != expected_master_keys
            ):
                errors.append(
                    "expected Master key roster differs from the SVG contract; "
                    f"missing={sorted(expected_master_keys - set(expected_master_parts))}, "
                    f"extra={sorted(set(expected_master_parts) - expected_master_keys)}"
                )
            for layout_key, layout_specs in specs_by_layout.items():
                prototype = layout_specs[0]
                layout_part = layout_parts_by_key.get(layout_key)
                if layout_part is None:
                    errors.append(f"layout key {layout_key!r} has no resolved Layout part")
                    continue
                layout_root = reader.xml(layout_part)
                if layout_root is None:
                    continue
                if layout_root.tag != f"{{{PML_NS}}}sldLayout":
                    errors.append(f"{layout_part} is not a p:sldLayout part")
                if layout_root.get("type") != "cust":
                    errors.append(f"{layout_part} must have type='cust'")
                if layout_root.get("preserve") != "1":
                    errors.append(f"{layout_part} must have preserve='1'")
                raw_show_master = (
                    layout_root.get("showMasterSp", "1").strip().lower()
                )
                if raw_show_master not in {"0", "1", "false", "true"}:
                    errors.append(
                        f"{layout_part} showMasterSp is not a valid boolean"
                    )
                elif (
                    (raw_show_master in {"1", "true"})
                    != prototype.layout_show_master_shapes
                ):
                    errors.append(
                        f"{layout_part} showMasterSp={raw_show_master}, "
                        "expected "
                        f"{str(prototype.layout_show_master_shapes).lower()}"
                    )
                c_sld = layout_root.find(f"{{{PML_NS}}}cSld")
                expected_name = layout_specs[0].layout_name
                actual_name = c_sld.get("name") if c_sld is not None else None
                if actual_name != expected_name:
                    errors.append(
                        f"{layout_part} has picker name {actual_name!r}, expected "
                        f"{expected_name!r}"
                    )

                override_values = overrides.get(f"/{layout_part}", [])
                if override_values != [SLIDE_LAYOUT_CONTENT_TYPE]:
                    errors.append(
                        f"[Content_Types].xml must declare {layout_part} exactly once "
                        f"as {SLIDE_LAYOUT_CONTENT_TYPE}"
                    )

                bindings = bindings_by_layout[layout_key]
                layout_placeholders = _validate_placeholder_roster(
                    layout_root,
                    bindings,
                    f"Layout {layout_key!r}",
                    errors,
                    is_layout=True,
                )
                _validate_layout_header_footer(
                    layout_root,
                    bindings,
                    f"Layout {layout_key!r}",
                    errors,
                )
                expected_layout_names = tuple(
                    f"{item.element_id} Layout"
                    if item.layer == "layout"
                    else f"{item.element_id} Placeholder"
                    for item in prototype.elements
                    if (
                        (item.layer == "layout" and not item.is_background)
                        or item.placeholder
                    )
                )
                _validate_named_shape_roster(
                    layout_root,
                    expected_layout_names,
                    f"Layout {layout_key!r}",
                    errors,
                    exact=True,
                    ordered=True,
                )
                expected_layout_background = any(
                    item.is_background for item in prototype.layout_elements
                )
                actual_layout_background = _has_explicit_background(layout_root)
                if actual_layout_background != expected_layout_background:
                    errors.append(
                        f"Layout {layout_key!r} background ownership is "
                        f"{actual_layout_background}, expected "
                        f"{expected_layout_background} from the SVG contract"
                    )
                if (
                    expected_layout_background
                    and expected_backgrounds is not None
                    and layout_part not in expected_backgrounds
                ):
                    errors.append(
                        f"Layout {layout_key!r} has no pre-promotion p:bg payload "
                        "for exact read-back"
                    )
                slide_placeholders: dict[int, dict[int, _Placeholder]] = {}
                for spec in public_specs_by_layout.get(layout_key, []):
                    slide_root = slide_roots.get(spec.slide_num)
                    if slide_root is None:
                        continue
                    expected_slide_bindings = bindings
                    slide_placeholders[spec.slide_num] = _validate_placeholder_roster(
                        slide_root,
                        expected_slide_bindings,
                        f"Slide {spec.slide_num}",
                        errors,
                        is_layout=False,
                    )
                    expected_slide_background = any(
                        item.layer == "slide" and item.is_background
                        for item in spec.elements
                    )
                    actual_slide_background = _has_explicit_background(slide_root)
                    if actual_slide_background != expected_slide_background:
                        errors.append(
                            f"Slide {spec.slide_num} background ownership is "
                            f"{actual_slide_background}, expected "
                            f"{expected_slide_background} from the SVG contract"
                        )
                    slide_part = f"ppt/slides/slide{spec.slide_num}.xml"
                    if (
                        expected_slide_background
                        and expected_backgrounds is not None
                        and slide_part not in expected_backgrounds
                    ):
                        errors.append(
                            f"Slide {spec.slide_num} has no pre-promotion p:bg "
                            "payload for exact read-back"
                        )
                    expected_carrier_names = [
                        (
                            f"{item.element_id} Proxy Content"
                            if is_proxy_placeholder(item)
                            else f"{item.element_id} Placeholder Carrier"
                        )
                        for item in spec.placeholders
                    ]
                    expected_carrier_names.extend(
                        "Placeholder Binding "
                        f"{binding.placeholder_type} {binding.effective_idx}"
                        for binding in bindings
                        if is_proxy_placeholder(binding.element)
                    )
                    _validate_named_shape_roster(
                        slide_root,
                        expected_carrier_names,
                        f"Slide {spec.slide_num}",
                        errors,
                        exact=False,
                        ordered=True,
                    )
                    bindings_by_element = {
                        binding.element.element_id: binding
                        for binding in bindings
                    }
                    for item in spec.placeholders:
                        proxy_binding = is_proxy_placeholder(item)
                        carrier_name = (
                            f"{item.element_id} Proxy Content"
                            if proxy_binding
                            else f"{item.element_id} Placeholder Carrier"
                        )
                        carrier = _top_level_shape_by_name(slide_root, carrier_name)
                        carrier_placeholder = (
                            carrier.find(f".//{{{PML_NS}}}ph")
                            if carrier is not None
                            else None
                        )
                        if proxy_binding:
                            if carrier_placeholder is not None:
                                errors.append(
                                    f"Slide {spec.slide_num} proxy content "
                                    f"{carrier_name!r} must remain ordinary"
                                )
                        elif carrier is not None and carrier_placeholder is None:
                            errors.append(
                                f"Slide {spec.slide_num} carrier "
                                f"{carrier_name!r} must own its p:ph binding"
                            )
                        if not proxy_binding:
                            continue
                        placeholder_binding = bindings_by_element[item.element_id]
                        binding_name = (
                            "Placeholder Binding "
                            f"{placeholder_binding.placeholder_type} "
                            f"{placeholder_binding.effective_idx}"
                        )
                        binding_shape = _top_level_shape_by_name(
                            slide_root,
                            binding_name,
                        )
                        if binding_shape is None:
                            continue
                        c_nv_pr = next(
                            binding_shape.iter(f"{{{PML_NS}}}cNvPr"),
                            None,
                        )
                        if (
                            c_nv_pr is None
                            or c_nv_pr.get("hidden", "0").lower()
                            not in {"1", "true"}
                        ):
                            errors.append(
                                f"Slide {spec.slide_num} binding proxy "
                                f"{binding_name!r} must be hidden"
                            )
                        if binding_shape.find(f".//{{{PML_NS}}}ph") is None:
                            errors.append(
                                f"Slide {spec.slide_num} binding proxy "
                                f"{binding_name!r} has no p:ph"
                            )
                        alpha_values = {
                            alpha.get("val")
                            for alpha in binding_shape.findall(
                                f".//{{{DML_NS}}}srgbClr/{{{DML_NS}}}alpha"
                            )
                        }
                        proxy_text = "".join(
                            node.text or ""
                            for node in binding_shape.findall(
                                f".//{{{DML_NS}}}t"
                            )
                        )
                        if "0" not in alpha_values or proxy_text != "\u200b":
                            errors.append(
                                f"Slide {spec.slide_num} binding proxy "
                                f"{binding_name!r} must contain exactly one fully "
                                "transparent zero-width run"
                            )

                prototype_placeholders = slide_placeholders.get(
                    prototype.slide_num,
                    {},
                )
                for binding in bindings:
                    idx = binding.effective_idx
                    layout_placeholder = layout_placeholders.get(idx)
                    prototype_placeholder = prototype_placeholders.get(idx)
                    if is_proxy_placeholder(binding.element):
                        prototype_placeholder = None
                    if layout_placeholder is None:
                        continue
                    context = (
                        f"Layout {layout_key!r} placeholder "
                        f"{binding.element.element_id!r}"
                    )
                    actual_bounds = _shape_bounds(
                        layout_placeholder.shape,
                        context,
                        errors,
                    )
                    if binding.element.placeholder_bounds is not None:
                        expected_bounds = tuple(
                            round(value * EMU_PER_PX)
                            for value in binding.element.placeholder_bounds
                        )
                    elif prototype_placeholder is not None:
                        expected_bounds = _shape_bounds(
                            prototype_placeholder.shape,
                            f"Slide {prototype.slide_num} placeholder "
                            f"{binding.element.element_id!r}",
                            errors,
                        )
                    else:
                        expected_bounds = None
                        errors.append(
                            f"{context} has neither explicit bounds nor a bound "
                            "prototype Slide placeholder"
                        )
                    if (
                        actual_bounds is not None
                        and expected_bounds is not None
                        and actual_bounds != expected_bounds
                    ):
                        errors.append(
                            f"{context} bounds are {actual_bounds}, expected "
                            f"{expected_bounds}"
                        )

                    prompt_size = _first_run_size(layout_placeholder.shape)
                    default_size = _level_one_default_size(
                        layout_placeholder.shape
                    )
                    if prototype_placeholder is not None:
                        prototype_size = _first_run_size(
                            prototype_placeholder.shape
                        )
                        if prototype_size is None:
                            continue
                        if prompt_size != prototype_size:
                            errors.append(
                                f"{context} prompt size is {prompt_size!r}, expected "
                                f"{prototype_size!r}"
                            )
                        if default_size != prototype_size:
                            errors.append(
                                f"{context} level-1 default size is "
                                f"{default_size!r}, expected {prototype_size!r}"
                            )
                    elif prompt_size is not None and default_size != prompt_size:
                        errors.append(
                            f"{context} level-1 default size is {default_size!r}, "
                            f"expected its prompt size {prompt_size!r}"
                        )

                master_target = _single_relationship_target(
                    reader,
                    layout_part,
                    SLIDE_MASTER_REL_TYPE,
                    f"Layout {layout_key!r}",
                )
                if master_target is None:
                    continue
                _relationship, master_part = master_target
                if not (
                    master_part.startswith("ppt/slideMasters/")
                    and master_part.endswith(".xml")
                ):
                    errors.append(
                        f"Layout {layout_key!r} targets non-Master part {master_part}"
                    )
                    continue
                reader.xml(master_part)
                used_master_parts.add(master_part)
                previous_master_part = master_parts_by_key.setdefault(
                    prototype.master_key,
                    master_part,
                )
                if previous_master_part != master_part:
                    errors.append(
                        f"Master key {prototype.master_key!r} targets both "
                        f"{previous_master_part} and {master_part}"
                    )
                previous_master_key = keys_by_master_part.setdefault(
                    master_part,
                    prototype.master_key,
                )
                if previous_master_key != prototype.master_key:
                    errors.append(
                        f"Master keys {previous_master_key!r} and "
                        f"{prototype.master_key!r} both target {master_part}"
                    )
                previous_master_spec = master_specs_by_part.setdefault(
                    master_part,
                    prototype,
                )
                if previous_master_spec.master_key != prototype.master_key:
                    errors.append(
                        f"{master_part} is shared by conflicting SVG Master contracts"
                    )
                layout_parts_by_master.setdefault(master_part, set()).add(
                    layout_part
                )

            layout_id_owners: dict[int, str] = {}
            for master_part, layout_parts in sorted(layout_parts_by_master.items()):
                master_root = reader.xml(master_part)
                if master_root is None:
                    continue
                master_spec = master_specs_by_part.get(master_part)
                if master_spec is not None:
                    master_c_sld = master_root.find(f"{{{PML_NS}}}cSld")
                    actual_master_name = (
                        master_c_sld.get("name")
                        if master_c_sld is not None
                        else None
                    )
                    if actual_master_name != master_spec.master_name:
                        errors.append(
                            f"Master {master_part} has picker name "
                            f"{actual_master_name!r}, expected "
                            f"{master_spec.master_name!r}"
                        )
                    _validate_named_shape_roster(
                        master_root,
                        tuple(
                            f"{item.element_id} Master"
                            for item in master_spec.master_elements
                            if not item.is_background
                        ),
                        f"Master {master_part}",
                        errors,
                        exact=True,
                        ordered=True,
                    )
                    expected_master_background = any(
                        item.is_background
                        for item in master_spec.master_elements
                    )
                    if (
                        expected_master_background
                        and not _has_explicit_background(master_root)
                    ):
                        errors.append(
                            f"Master {master_part} is missing its explicit background"
                        )
                    if (
                        expected_master_background
                        and expected_backgrounds is not None
                        and master_part not in expected_backgrounds
                    ):
                        errors.append(
                            f"Master {master_part} has no pre-promotion p:bg payload "
                            "for exact read-back"
                        )
                layout_id_entries = master_root.findall(
                    f"{{{PML_NS}}}sldLayoutIdLst/"
                    f"{{{PML_NS}}}sldLayoutId"
                )
                layout_ids = _validate_registered_part_roster(
                    reader,
                    master_part,
                    SLIDE_LAYOUT_REL_TYPE,
                    layout_id_entries,
                    layout_parts,
                    f"Master {master_part} p:sldLayoutIdLst",
                )
                for layout_id in sorted(layout_ids):
                    previous_owner = layout_id_owners.setdefault(
                        layout_id,
                        master_part,
                    )
                    if previous_owner != master_part:
                        errors.append(
                            f"Slide Layout numeric id {layout_id} appears in both "
                            f"{previous_owner} and {master_part}"
                        )

            expected_slide_parts = {
                f"ppt/slides/slide{spec.slide_num}.xml"
                for spec in specs
            }
            expected_layout_parts = set(layout_parts_by_key.values())
            _validate_physical_part_roster(
                reader,
                expected_slide_parts,
                "ppt/slides",
                "slide",
                "Slide",
            )
            _validate_physical_part_roster(
                reader,
                expected_layout_parts,
                "ppt/slideLayouts",
                "slideLayout",
                "Layout",
            )
            _validate_physical_part_roster(
                reader,
                used_master_parts,
                "ppt/slideMasters",
                "slideMaster",
                "Master",
            )
            _validate_content_type_part_roster(
                overrides,
                expected_slide_parts,
                "ppt/slides",
                "slide",
                SLIDE_CONTENT_TYPE,
                errors,
                "Slide",
            )
            _validate_content_type_part_roster(
                overrides,
                expected_layout_parts,
                "ppt/slideLayouts",
                "slideLayout",
                SLIDE_LAYOUT_CONTENT_TYPE,
                errors,
                "Layout",
            )
            _validate_content_type_part_roster(
                overrides,
                used_master_parts,
                "ppt/slideMasters",
                "slideMaster",
                SLIDE_MASTER_CONTENT_TYPE,
                errors,
                "Master",
            )
            _validate_unique_creation_ids(
                reader,
                expected_slide_parts | expected_layout_parts | used_master_parts,
            )
            _validate_presentation_slide_registration(reader, expected_slide_parts)
            _validate_presentation_master_registration(reader, used_master_parts)
            _validate_master_theme_ownership(reader, used_master_parts)
    except (OSError, zipfile.BadZipFile) as exc:
        raise ValueError(f"cannot read template PPTX package {path}: {exc}") from exc

    if errors:
        details = "\n".join(f"  - {error}" for error in errors)
        raise ValueError(f"structured package read-back failed:\n{details}")
