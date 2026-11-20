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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from ..drawingml.utils import EMU_PER_PX
from .template_structure import (
    TemplatePlaceholderBinding,
    TemplateSlideSpec,
    TemplateStructureError,
    is_proxy_placeholder,
    template_placeholder_bindings,
)
from .template_validation import _Placeholder


@dataclass(frozen=True)
class _ValidationDeps:
    """Lazily resolved helpers and constants from template_validation."""

    dml_ns: str
    pml_ns: str
    slide_content_type: str
    slide_layout_content_type: str
    slide_layout_rel_type: str
    slide_master_content_type: str
    slide_master_rel_type: str
    package_reader_cls: Any
    background_payload_signatures: Any
    content_type_overrides: Any
    expected_background_signature: Any
    first_run_size: Any
    has_explicit_background: Any
    level_one_default_size: Any
    shape_bounds: Any
    single_relationship_target: Any
    top_level_shape_by_name: Any
    validate_content_type_part_roster: Any
    validate_layout_header_footer: Any
    validate_master_theme_ownership: Any
    validate_named_shape_roster: Any
    validate_physical_part_roster: Any
    validate_placeholder_roster: Any
    validate_presentation_master_registration: Any
    validate_presentation_slide_registration: Any
    validate_registered_part_roster: Any
    validate_unique_creation_ids: Any


@dataclass
class _PackageValidationState:
    """Mutable state shared across template package validation stages."""

    reader: Any
    overrides: dict[str, list[str]]
    errors: list[str]
    specs_by_layout: dict[str, list[TemplateSlideSpec]]
    public_specs_by_layout: dict[str, list[TemplateSlideSpec]]
    bindings_by_layout: dict[str, tuple[TemplatePlaceholderBinding, ...]]
    layout_parts_by_key: dict[str, str]
    keys_by_layout_part: dict[str, str]
    master_parts_by_key: dict[str, str]
    keys_by_master_part: dict[str, str]
    expected_backgrounds: dict[str, str | None] | None = None
    slide_roots: dict[int, ET.Element] = field(default_factory=dict)
    used_master_parts: set[str] = field(default_factory=set)
    layout_parts_by_master: dict[str, set[str]] = field(default_factory=dict)
    master_specs_by_part: dict[str, TemplateSlideSpec] = field(default_factory=dict)


def _load_validation_deps() -> _ValidationDeps:
    """Load template_validation symbols lazily to avoid circular imports."""
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

    return _ValidationDeps(
        dml_ns=DML_NS,
        pml_ns=PML_NS,
        slide_content_type=SLIDE_CONTENT_TYPE,
        slide_layout_content_type=SLIDE_LAYOUT_CONTENT_TYPE,
        slide_layout_rel_type=SLIDE_LAYOUT_REL_TYPE,
        slide_master_content_type=SLIDE_MASTER_CONTENT_TYPE,
        slide_master_rel_type=SLIDE_MASTER_REL_TYPE,
        package_reader_cls=_PackageReader,
        background_payload_signatures=_background_payload_signatures,
        content_type_overrides=_content_type_overrides,
        expected_background_signature=_expected_background_signature,
        first_run_size=_first_run_size,
        has_explicit_background=_has_explicit_background,
        level_one_default_size=_level_one_default_size,
        shape_bounds=_shape_bounds,
        single_relationship_target=_single_relationship_target,
        top_level_shape_by_name=_top_level_shape_by_name,
        validate_content_type_part_roster=_validate_content_type_part_roster,
        validate_layout_header_footer=_validate_layout_header_footer,
        validate_master_theme_ownership=_validate_master_theme_ownership,
        validate_named_shape_roster=_validate_named_shape_roster,
        validate_physical_part_roster=_validate_physical_part_roster,
        validate_placeholder_roster=_validate_placeholder_roster,
        validate_presentation_slide_registration=(
            _validate_presentation_slide_registration
        ),
        validate_presentation_master_registration=(
            _validate_presentation_master_registration
        ),
        validate_registered_part_roster=_validate_registered_part_roster,
        validate_unique_creation_ids=_validate_unique_creation_ids,
    )


def _group_specs_and_bindings(
    specs: list[TemplateSlideSpec],
    layout_specs: list[TemplateSlideSpec] | None,
) -> tuple[
    list[TemplateSlideSpec],
    dict[str, list[TemplateSlideSpec]],
    dict[str, list[TemplateSlideSpec]],
    dict[str, tuple[TemplatePlaceholderBinding, ...]],
]:
    """Group slide/layout specs by layout key and compute placeholder bindings."""
    structure_specs = layout_specs or specs
    specs_by_layout: dict[str, list[TemplateSlideSpec]] = {}
    public_specs_by_layout: dict[str, list[TemplateSlideSpec]] = {}
    bindings_by_layout: dict[str, tuple[TemplatePlaceholderBinding, ...]] = {}
    try:
        for spec in structure_specs:
            specs_by_layout.setdefault(spec.layout_key, []).append(spec)
        for spec in specs:
            public_specs_by_layout.setdefault(spec.layout_key, []).append(spec)
        for layout_key, group_specs in specs_by_layout.items():
            bindings_by_layout[layout_key] = template_placeholder_bindings(
                group_specs[0]
            )
    except TemplateStructureError as exc:
        raise ValueError(str(exc)) from exc
    return (
        structure_specs,
        specs_by_layout,
        public_specs_by_layout,
        bindings_by_layout,
    )


def _validate_expected_parts_and_payloads(
    state: _PackageValidationState,
    deps: _ValidationDeps,
    expected_shape_rosters: dict[str, tuple[str, ...]] | None,
    expected_layout_parts: dict[str, str] | None,
    expected_master_parts: dict[str, str] | None,
    structure_specs: list[TemplateSlideSpec],
) -> None:
    """Verify deterministic background payloads, shape rosters, and key maps."""
    if state.expected_backgrounds is not None:
        for part, background_xml in sorted(state.expected_backgrounds.items()):
            root = state.reader.xml(part)
            if root is None:
                continue
            if background_xml is None:
                expected_signatures: tuple[tuple[object, ...], ...] = ()
            else:
                try:
                    expected_signatures = (
                        deps.expected_background_signature(background_xml),
                    )
                except ValueError as exc:
                    state.errors.append(f"{part} {exc}")
                    continue
            actual_signatures = deps.background_payload_signatures(root)
            if actual_signatures != expected_signatures:
                state.errors.append(
                    f"{part} p:bg count/payload differs from the exact "
                    "structured-export expectation"
                )
    if expected_shape_rosters is not None:
        for part, roster in sorted(expected_shape_rosters.items()):
            root = state.reader.xml(part)
            if root is None:
                continue
            deps.validate_named_shape_roster(
                root,
                roster,
                part,
                state.errors,
                exact=True,
                ordered=True,
            )
    if len(state.keys_by_layout_part) != len(state.layout_parts_by_key):
        state.errors.append(
            "expected Layout part mapping assigns one part to multiple keys"
        )
    if expected_layout_parts is not None and (
        set(expected_layout_parts) != set(state.specs_by_layout)
    ):
        state.errors.append(
            "expected Layout key roster differs from the SVG contract; "
            f"missing={sorted(set(state.specs_by_layout) - set(expected_layout_parts))}, "
            f"extra={sorted(set(expected_layout_parts) - set(state.specs_by_layout))}"
        )
    expected_master_keys = {spec.master_key for spec in structure_specs}
    if len(state.keys_by_master_part) != len(state.master_parts_by_key):
        state.errors.append(
            "expected Master part mapping assigns one part to multiple keys"
        )
    if expected_master_parts is not None and (
        set(expected_master_parts) != expected_master_keys
    ):
        state.errors.append(
            "expected Master key roster differs from the SVG contract; "
            f"missing={sorted(expected_master_keys - set(expected_master_parts))}, "
            f"extra={sorted(set(expected_master_parts) - expected_master_keys)}"
        )


def _validate_slide_contracts(
    state: _PackageValidationState,
    deps: _ValidationDeps,
    specs: list[TemplateSlideSpec],
) -> None:
    """Validate slide showMasterSp attributes and slide-to-layout links."""
    for spec in specs:
        slide_part = f"ppt/slides/slide{spec.slide_num}.xml"
        slide_root = state.reader.xml(slide_part)
        if slide_root is not None:
            state.slide_roots[spec.slide_num] = slide_root
            raw_show_inherited = slide_root.get("showMasterSp", "1").strip().lower()
            if raw_show_inherited not in {"0", "1", "false", "true"}:
                state.errors.append(f"{slide_part} showMasterSp is not a valid boolean")
            elif (
                (raw_show_inherited in {"1", "true"})
                != spec.slide_show_inherited_shapes
            ):
                state.errors.append(
                    f"{slide_part} showMasterSp={raw_show_inherited}, "
                    "expected "
                    f"{str(spec.slide_show_inherited_shapes).lower()}"
                )
        relationship_target = deps.single_relationship_target(
            state.reader,
            slide_part,
            deps.slide_layout_rel_type,
            f"Slide {spec.slide_num}",
        )
        if relationship_target is None:
            continue
        _relationship, layout_part = relationship_target
        if not (
            layout_part.startswith("ppt/slideLayouts/")
            and layout_part.endswith(".xml")
        ):
            state.errors.append(
                f"Slide {spec.slide_num} targets non-Layout part {layout_part}"
            )
            continue
        state.reader.xml(layout_part)
        previous_part = state.layout_parts_by_key.setdefault(spec.layout_key, layout_part)
        if previous_part != layout_part:
            state.errors.append(
                f"layout key {spec.layout_key!r} targets both {previous_part} "
                f"and {layout_part}"
            )
        previous_key = state.keys_by_layout_part.setdefault(layout_part, spec.layout_key)
        if previous_key != spec.layout_key:
            state.errors.append(
                f"layout keys {previous_key!r} and {spec.layout_key!r} both "
                f"target {layout_part}"
            )


def _validate_slide_proxy_binding(
    state: _PackageValidationState,
    deps: _ValidationDeps,
    spec: TemplateSlideSpec,
    slide_root: ET.Element,
    bindings_by_element: dict[str, TemplatePlaceholderBinding],
    item: Any,
) -> None:
    """Validate ordinary vs carrier binding and hidden proxy placeholder shapes."""
    proxy_binding = is_proxy_placeholder(item)
    carrier_name = (
        f"{item.element_id} Proxy Content"
        if proxy_binding
        else f"{item.element_id} Placeholder Carrier"
    )
    carrier = deps.top_level_shape_by_name(slide_root, carrier_name)
    carrier_placeholder = (
        carrier.find(f".//{{{deps.pml_ns}}}ph") if carrier is not None else None
    )
    if proxy_binding:
        if carrier_placeholder is not None:
            state.errors.append(
                f"Slide {spec.slide_num} proxy content "
                f"{carrier_name!r} must remain ordinary"
            )
    elif carrier is not None and carrier_placeholder is None:
        state.errors.append(
            f"Slide {spec.slide_num} carrier "
            f"{carrier_name!r} must own its p:ph binding"
        )
    if not proxy_binding:
        return
    placeholder_binding = bindings_by_element[item.element_id]
    binding_name = (
        "Placeholder Binding "
        f"{placeholder_binding.placeholder_type} "
        f"{placeholder_binding.effective_idx}"
    )
    binding_shape = deps.top_level_shape_by_name(slide_root, binding_name)
    if binding_shape is None:
        return
    c_nv_pr = next(binding_shape.iter(f"{{{deps.pml_ns}}}cNvPr"), None)
    if c_nv_pr is None or c_nv_pr.get("hidden", "0").lower() not in {"1", "true"}:
        state.errors.append(
            f"Slide {spec.slide_num} binding proxy {binding_name!r} must be hidden"
        )
    if binding_shape.find(f".//{{{deps.pml_ns}}}ph") is None:
        state.errors.append(
            f"Slide {spec.slide_num} binding proxy {binding_name!r} has no p:ph"
        )
    alpha_values = {
        alpha.get("val")
        for alpha in binding_shape.findall(
            f".//{{{deps.dml_ns}}}srgbClr/{{{deps.dml_ns}}}alpha"
        )
    }
    proxy_text = "".join(
        node.text or "" for node in binding_shape.findall(f".//{{{deps.dml_ns}}}t")
    )
    if "0" not in alpha_values or proxy_text != "\u200b":
        state.errors.append(
            f"Slide {spec.slide_num} binding proxy "
            f"{binding_name!r} must contain exactly one fully "
            "transparent zero-width run"
        )


def _validate_slide_for_layout(
    state: _PackageValidationState,
    deps: _ValidationDeps,
    spec: TemplateSlideSpec,
    bindings: tuple[TemplatePlaceholderBinding, ...],
    slide_placeholders: dict[int, dict[int, _Placeholder]],
) -> None:
    """Validate one published slide against its Layout contract."""
    slide_root = state.slide_roots.get(spec.slide_num)
    if slide_root is None:
        return
    slide_placeholders[spec.slide_num] = deps.validate_placeholder_roster(
        slide_root,
        bindings,
        f"Slide {spec.slide_num}",
        state.errors,
        is_layout=False,
    )
    expected_slide_background = any(
        item.layer == "slide" and item.is_background for item in spec.elements
    )
    actual_slide_background = deps.has_explicit_background(slide_root)
    if actual_slide_background != expected_slide_background:
        state.errors.append(
            f"Slide {spec.slide_num} background ownership is "
            f"{actual_slide_background}, expected "
            f"{expected_slide_background} from the SVG contract"
        )
    slide_part = f"ppt/slides/slide{spec.slide_num}.xml"
    if (
        expected_slide_background
        and state.expected_backgrounds is not None
        and slide_part not in state.expected_backgrounds
    ):
        state.errors.append(
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
    deps.validate_named_shape_roster(
        slide_root,
        expected_carrier_names,
        f"Slide {spec.slide_num}",
        state.errors,
        exact=False,
        ordered=True,
    )
    bindings_by_element = {
        binding.element.element_id: binding for binding in bindings
    }
    for item in spec.placeholders:
        _validate_slide_proxy_binding(
            state,
            deps,
            spec,
            slide_root,
            bindings_by_element,
            item,
        )


def _validate_layout_placeholder_geometry(
    state: _PackageValidationState,
    deps: _ValidationDeps,
    layout_key: str,
    prototype: TemplateSlideSpec,
    bindings: tuple[TemplatePlaceholderBinding, ...],
    layout_placeholders: dict[int, _Placeholder],
    slide_placeholders: dict[int, dict[int, _Placeholder]],
) -> None:
    """Validate layout placeholder bounding boxes and prompt/default text sizes."""
    prototype_placeholders = slide_placeholders.get(prototype.slide_num, {})
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
        actual_bounds = deps.shape_bounds(
            layout_placeholder.shape,
            context,
            state.errors,
        )
        if binding.element.placeholder_bounds is not None:
            expected_bounds = tuple(
                round(value * EMU_PER_PX)
                for value in binding.element.placeholder_bounds
            )
        elif prototype_placeholder is not None:
            expected_bounds = deps.shape_bounds(
                prototype_placeholder.shape,
                f"Slide {prototype.slide_num} placeholder "
                f"{binding.element.element_id!r}",
                state.errors,
            )
        else:
            expected_bounds = None
            state.errors.append(
                f"{context} has neither explicit bounds nor a bound "
                "prototype Slide placeholder"
            )
        if (
            actual_bounds is not None
            and expected_bounds is not None
            and actual_bounds != expected_bounds
        ):
            state.errors.append(
                f"{context} bounds are {actual_bounds}, expected "
                f"{expected_bounds}"
            )
        prompt_size = deps.first_run_size(layout_placeholder.shape)
        default_size = deps.level_one_default_size(layout_placeholder.shape)
        if prototype_placeholder is not None:
            prototype_size = deps.first_run_size(prototype_placeholder.shape)
            if prototype_size is None:
                continue
            if prompt_size != prototype_size:
                state.errors.append(
                    f"{context} prompt size is {prompt_size!r}, expected "
                    f"{prototype_size!r}"
                )
            if default_size != prototype_size:
                state.errors.append(
                    f"{context} level-1 default size is "
                    f"{default_size!r}, expected {prototype_size!r}"
                )
        elif prompt_size is not None and default_size != prompt_size:
            state.errors.append(
                f"{context} level-1 default size is {default_size!r}, "
                f"expected its prompt size {prompt_size!r}"
            )


def _validate_layout_master_link(
    state: _PackageValidationState,
    deps: _ValidationDeps,
    layout_key: str,
    prototype: TemplateSlideSpec,
    layout_part: str,
) -> None:
    """Validate layout-to-master relationship and register master ownership."""
    master_target = deps.single_relationship_target(
        state.reader,
        layout_part,
        deps.slide_master_rel_type,
        f"Layout {layout_key!r}",
    )
    if master_target is None:
        return
    _relationship, master_part = master_target
    if not (
        master_part.startswith("ppt/slideMasters/")
        and master_part.endswith(".xml")
    ):
        state.errors.append(
            f"Layout {layout_key!r} targets non-Master part {master_part}"
        )
        return
    state.reader.xml(master_part)
    state.used_master_parts.add(master_part)
    previous_master_part = state.master_parts_by_key.setdefault(
        prototype.master_key,
        master_part,
    )
    if previous_master_part != master_part:
        state.errors.append(
            f"Master key {prototype.master_key!r} targets both "
            f"{previous_master_part} and {master_part}"
        )
    previous_master_key = state.keys_by_master_part.setdefault(
        master_part,
        prototype.master_key,
    )
    if previous_master_key != prototype.master_key:
        state.errors.append(
            f"Master keys {previous_master_key!r} and "
            f"{prototype.master_key!r} both target {master_part}"
        )
    previous_master_spec = state.master_specs_by_part.setdefault(
        master_part,
        prototype,
    )
    if previous_master_spec.master_key != prototype.master_key:
        state.errors.append(
            f"{master_part} is shared by conflicting SVG Master contracts"
        )
    state.layout_parts_by_master.setdefault(master_part, set()).add(layout_part)


def _validate_layout_contracts(
    state: _PackageValidationState,
    deps: _ValidationDeps,
) -> None:
    """Validate every Layout part, its slides, placeholders, and Master links."""
    for layout_key, group_specs in state.specs_by_layout.items():
        prototype = group_specs[0]
        layout_part = state.layout_parts_by_key.get(layout_key)
        if layout_part is None:
            state.errors.append(f"layout key {layout_key!r} has no resolved Layout part")
            continue
        layout_root = state.reader.xml(layout_part)
        if layout_root is None:
            continue
        if layout_root.tag != f"{{{deps.pml_ns}}}sldLayout":
            state.errors.append(f"{layout_part} is not a p:sldLayout part")
        if layout_root.get("type") != "cust":
            state.errors.append(f"{layout_part} must have type='cust'")
        if layout_root.get("preserve") != "1":
            state.errors.append(f"{layout_part} must have preserve='1'")
        raw_show_master = layout_root.get("showMasterSp", "1").strip().lower()
        if raw_show_master not in {"0", "1", "false", "true"}:
            state.errors.append(f"{layout_part} showMasterSp is not a valid boolean")
        elif (raw_show_master in {"1", "true"}) != prototype.layout_show_master_shapes:
            state.errors.append(
                f"{layout_part} showMasterSp={raw_show_master}, "
                "expected "
                f"{str(prototype.layout_show_master_shapes).lower()}"
            )
        c_sld = layout_root.find(f"{{{deps.pml_ns}}}cSld")
        expected_name = prototype.layout_name
        actual_name = c_sld.get("name") if c_sld is not None else None
        if actual_name != expected_name:
            state.errors.append(
                f"{layout_part} has picker name {actual_name!r}, expected "
                f"{expected_name!r}"
            )
        override_values = state.overrides.get(f"/{layout_part}", [])
        if override_values != [deps.slide_layout_content_type]:
            state.errors.append(
                f"[Content_Types].xml must declare {layout_part} exactly once "
                f"as {deps.slide_layout_content_type}"
            )
        bindings = state.bindings_by_layout[layout_key]
        layout_placeholders = deps.validate_placeholder_roster(
            layout_root,
            bindings,
            f"Layout {layout_key!r}",
            state.errors,
            is_layout=True,
        )
        deps.validate_layout_header_footer(
            layout_root,
            bindings,
            f"Layout {layout_key!r}",
            state.errors,
        )
        expected_layout_names = tuple(
            f"{item.element_id} Layout"
            if item.layer == "layout"
            else f"{item.element_id} Placeholder"
            for item in prototype.elements
            if (item.layer == "layout" and not item.is_background) or item.placeholder
        )
        deps.validate_named_shape_roster(
            layout_root,
            expected_layout_names,
            f"Layout {layout_key!r}",
            state.errors,
            exact=True,
            ordered=True,
        )
        expected_layout_background = any(
            item.is_background for item in prototype.layout_elements
        )
        actual_layout_background = deps.has_explicit_background(layout_root)
        if actual_layout_background != expected_layout_background:
            state.errors.append(
                f"Layout {layout_key!r} background ownership is "
                f"{actual_layout_background}, expected "
                f"{expected_layout_background} from the SVG contract"
            )
        if (
            expected_layout_background
            and state.expected_backgrounds is not None
            and layout_part not in state.expected_backgrounds
        ):
            state.errors.append(
                f"Layout {layout_key!r} has no pre-promotion p:bg payload "
                "for exact read-back"
            )
        slide_placeholders: dict[int, dict[int, _Placeholder]] = {}
        for spec in state.public_specs_by_layout.get(layout_key, []):
            _validate_slide_for_layout(
                state,
                deps,
                spec,
                bindings,
                slide_placeholders,
            )
        _validate_layout_placeholder_geometry(
            state,
            deps,
            layout_key,
            prototype,
            bindings,
            layout_placeholders,
            slide_placeholders,
        )
        _validate_layout_master_link(
            state,
            deps,
            layout_key,
            prototype,
            layout_part,
        )


def _validate_master_contracts(
    state: _PackageValidationState,
    deps: _ValidationDeps,
) -> None:
    """Validate every Master part's shape roster, background, and layout IDs."""
    layout_id_owners: dict[int, str] = {}
    for master_part, layout_parts in sorted(state.layout_parts_by_master.items()):
        master_root = state.reader.xml(master_part)
        if master_root is None:
            continue
        master_spec = state.master_specs_by_part.get(master_part)
        if master_spec is not None:
            master_c_sld = master_root.find(f"{{{deps.pml_ns}}}cSld")
            actual_master_name = (
                master_c_sld.get("name") if master_c_sld is not None else None
            )
            if actual_master_name != master_spec.master_name:
                state.errors.append(
                    f"Master {master_part} has picker name "
                    f"{actual_master_name!r}, expected "
                    f"{master_spec.master_name!r}"
                )
            deps.validate_named_shape_roster(
                master_root,
                tuple(
                    f"{item.element_id} Master"
                    for item in master_spec.master_elements
                    if not item.is_background
                ),
                f"Master {master_part}",
                state.errors,
                exact=True,
                ordered=True,
            )
            expected_master_background = any(
                item.is_background for item in master_spec.master_elements
            )
            if (
                expected_master_background
                and not deps.has_explicit_background(master_root)
            ):
                state.errors.append(
                    f"Master {master_part} is missing its explicit background"
                )
            if (
                expected_master_background
                and state.expected_backgrounds is not None
                and master_part not in state.expected_backgrounds
            ):
                state.errors.append(
                    f"Master {master_part} has no pre-promotion p:bg payload "
                    "for exact read-back"
                )
        layout_id_entries = master_root.findall(
            f"{{{deps.pml_ns}}}sldLayoutIdLst/{{{deps.pml_ns}}}sldLayoutId"
        )
        layout_ids = deps.validate_registered_part_roster(
            state.reader,
            master_part,
            deps.slide_layout_rel_type,
            layout_id_entries,
            layout_parts,
            f"Master {master_part} p:sldLayoutIdLst",
        )
        for layout_id in sorted(layout_ids):
            previous_owner = layout_id_owners.setdefault(layout_id, master_part)
            if previous_owner != master_part:
                state.errors.append(
                    f"Slide Layout numeric id {layout_id} appears in both "
                    f"{previous_owner} and {master_part}"
                )


def _validate_package_rosters_and_registrations(
    state: _PackageValidationState,
    deps: _ValidationDeps,
    specs: list[TemplateSlideSpec],
) -> None:
    """Validate physical ZIP parts, Content_Types overrides, and presentation links."""
    expected_slide_parts = {f"ppt/slides/slide{spec.slide_num}.xml" for spec in specs}
    expected_layout_part_set = set(state.layout_parts_by_key.values())
    deps.validate_physical_part_roster(
        state.reader,
        expected_slide_parts,
        "ppt/slides",
        "slide",
        "Slide",
    )
    deps.validate_physical_part_roster(
        state.reader,
        expected_layout_part_set,
        "ppt/slideLayouts",
        "slideLayout",
        "Layout",
    )
    deps.validate_physical_part_roster(
        state.reader,
        state.used_master_parts,
        "ppt/slideMasters",
        "slideMaster",
        "Master",
    )
    deps.validate_content_type_part_roster(
        state.overrides,
        expected_slide_parts,
        "ppt/slides",
        "slide",
        deps.slide_content_type,
        state.errors,
        "Slide",
    )
    deps.validate_content_type_part_roster(
        state.overrides,
        expected_layout_part_set,
        "ppt/slideLayouts",
        "slideLayout",
        deps.slide_layout_content_type,
        state.errors,
        "Layout",
    )
    deps.validate_content_type_part_roster(
        state.overrides,
        state.used_master_parts,
        "ppt/slideMasters",
        "slideMaster",
        deps.slide_master_content_type,
        state.errors,
        "Master",
    )
    deps.validate_unique_creation_ids(
        state.reader,
        expected_slide_parts | expected_layout_part_set | state.used_master_parts,
    )
    deps.validate_presentation_slide_registration(state.reader, expected_slide_parts)
    deps.validate_presentation_master_registration(
        state.reader,
        state.used_master_parts,
    )
    deps.validate_master_theme_ownership(state.reader, state.used_master_parts)


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
    if not specs:
        raise ValueError("structured package validation requires at least one slide spec")
    deps = _load_validation_deps()
    (
        structure_specs,
        specs_by_layout,
        public_specs_by_layout,
        bindings_by_layout,
    ) = _group_specs_and_bindings(specs, layout_specs)
    errors: list[str] = []
    path = Path(pptx_path)
    try:
        with zipfile.ZipFile(path) as package:
            reader = deps.package_reader_cls(package, errors)
            overrides = deps.content_type_overrides(reader)
            layout_parts_by_key = dict(expected_layout_parts or {})
            master_parts_by_key = dict(expected_master_parts or {})
            state = _PackageValidationState(
                reader=reader,
                overrides=overrides,
                errors=errors,
                specs_by_layout=specs_by_layout,
                public_specs_by_layout=public_specs_by_layout,
                bindings_by_layout=bindings_by_layout,
                layout_parts_by_key=layout_parts_by_key,
                keys_by_layout_part={
                    part: key for key, part in layout_parts_by_key.items()
                },
                master_parts_by_key=master_parts_by_key,
                keys_by_master_part={
                    part: key for key, part in master_parts_by_key.items()
                },
                expected_backgrounds=expected_backgrounds,
            )
            _validate_expected_parts_and_payloads(
                state,
                deps,
                expected_shape_rosters,
                expected_layout_parts,
                expected_master_parts,
                structure_specs,
            )
            _validate_slide_contracts(state, deps, specs)
            _validate_layout_contracts(state, deps)
            _validate_master_contracts(state, deps)
            _validate_package_rosters_and_registrations(state, deps, specs)
    except (OSError, zipfile.BadZipFile) as exc:
        raise ValueError(f"cannot read template PPTX package {path}: {exc}") from exc

    if errors:
        details = "\n".join(f"  - {error}" for error in errors)
        raise ValueError(f"structured package read-back failed:\n{details}")
