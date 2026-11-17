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
from ..geometry_properties import (
    GeometryStyleError,
    materialize_inline_geometry_properties,
)

from .template_structure import (
    TemplateElementSpec,
    TemplateSlideSpec,
)

def _scope_css_signature(
    root: ET.Element,
    elements: tuple[ET.Element, ...],
    svg_path: Path,
) -> tuple[tuple[str, str], ...]:
    """Return only stylesheet rules that can affect the selected visual scope."""
    from .template_structure import (
        _CSS_COMMENT_RE,
        _CSS_RULE_RE,
        _css_selector_matches_scope,
        _font_families_from_declarations,
        _local_tag,
        _normalize_css_declarations,
        _scope_selector_tokens,
    )
    ids, classes, tags, attributes, font_families = _scope_selector_tokens(
        root,
        elements,
    )
    parsed_rules: list[tuple[str, str]] = []
    for style in root.iter():
        if _local_tag(style) != "style":
            continue
        css = _CSS_COMMENT_RE.sub("", style.text or "")
        for match in _CSS_RULE_RE.finditer(css):
            parsed_rules.append((match.group(1).strip(), match.group(2)))

    matched_selectors: dict[int, tuple[str, ...]] = {}
    for index, (raw_selector, body) in enumerate(parsed_rules):
        if raw_selector.startswith("@"):
            continue
        selectors = tuple(
            " ".join(selector.split())
            for selector in raw_selector.split(",")
            if _css_selector_matches_scope(
                selector,
                ids=ids,
                classes=classes,
                tags=tags,
                attributes=attributes,
            )
        )
        if selectors:
            matched_selectors[index] = selectors
            font_families.update(_font_families_from_declarations(body))

    rules: list[tuple[str, str]] = []
    for index, (raw_selector, body) in enumerate(parsed_rules):
        selectors = matched_selectors.get(index)
        if selectors is None:
            if not raw_selector.lower().startswith("@font-face"):
                continue
            declared_families = _font_families_from_declarations(body)
            if not declared_families.intersection(font_families):
                continue
            selectors = ("@font-face",)
        rules.append((
            ",".join(selectors),
            _normalize_css_declarations(body, svg_path),
        ))
    return tuple(rules)


def _scope_visual_resources_signature(
    root: ET.Element,
    elements: tuple[ET.Element, ...],
    svg_path: Path,
    *,
    ignore_json_native_preview: bool = False,
) -> tuple[object, ...]:
    """Capture root inheritance, relevant CSS, and the referenced defs closure."""
    from .template_structure import (
        _CSS_URL_RE,
        _STRUCTURE_ATTRS,
        _element_tree_signature,
        _local_tag,
        _mirror_resource_projection,
        _signature_attr_value,
        _svg_reference_ids,
    )
    if not elements:
        return ()
    comparison_elements = (
        tuple(_mirror_resource_projection(element) for element in elements)
        if ignore_json_native_preview
        else elements
    )
    root_attrs = tuple(sorted(
        (
            name,
            _signature_attr_value(
                name,
                value,
                svg_path=svg_path,
                asset_identity=True,
            ),
        )
        for name, value in root.attrib.items()
        if (
            name.rsplit("}", 1)[-1] not in _STRUCTURE_ATTRS
            and not name.rsplit("}", 1)[-1].startswith("data-")
        )
    ))
    css_rules = _scope_css_signature(root, comparison_elements, svg_path)
    references: set[str] = set()
    for element in comparison_elements:
        references.update(_svg_reference_ids(element))
    for _selector, declarations in css_rules:
        references.update(
            match.group(2)[1:]
            for match in _CSS_URL_RE.finditer(declarations)
            if match.group(2).startswith("#")
        )

    definitions_by_id: dict[str, ET.Element] = {}
    for definitions in root.iter():
        if _local_tag(definitions) != "defs":
            continue
        for definition in definitions.iter():
            definition_id = (definition.get("id") or "").strip()
            if definition_id:
                definitions_by_id[definition_id] = definition

    pending = list(references)
    resolved: set[str] = set()
    while pending:
        reference = pending.pop()
        if reference in resolved:
            continue
        resolved.add(reference)
        definition = definitions_by_id.get(reference)
        if definition is not None:
            pending.extend(_svg_reference_ids(definition) - resolved)
    definition_signatures = tuple(
        (
            reference,
            _element_tree_signature(
                definitions_by_id[reference],
                include_skin=True,
                include_text=True,
                svg_path=svg_path,
                asset_identity=True,
            ) if reference in definitions_by_id else ("missing", reference),
        )
        for reference in sorted(resolved)
    )
    return root_attrs, css_rules, definition_signatures


def _structure_subtree_signature(
    svg_path: Path,
    elements: tuple[TemplateElementSpec, ...],
    *,
    include_skin: bool = False,
    include_text: bool = True,
    asset_identity: bool = False,
    ignore_json_native_preview: bool = False,
) -> tuple[tuple[str, tuple[object, ...]], ...]:
    """Read structural or literal-visual signatures for direct SVG children."""
    from .template_structure import (
        TemplateStructureError,
        _element_tree_signature,
        _parse_svg_root,
    )
    try:
        root = _parse_svg_root(svg_path)
        materialize_inline_geometry_properties(root)
    except (OSError, ET.ParseError, NativePayloadError, GeometryStyleError) as exc:
        raise TemplateStructureError(
            f"{svg_path.name}: unable to compare template prototype structure: {exc}"
        ) from exc
    direct_by_id = {
        (child.get("id") or "").strip(): child
        for child in root
        if (child.get("id") or "").strip()
    }
    signatures: list[tuple[str, tuple[object, ...]]] = []
    for item in elements:
        child = direct_by_id.get(item.element_id)
        if child is None:
            raise TemplateStructureError(
                f"{svg_path.name}: structure element {item.element_id!r} is no "
                "longer a direct SVG child"
            )
        signatures.append((
            item.element_id,
            _element_tree_signature(
                child,
                include_skin=include_skin,
                include_text=include_text,
                svg_path=svg_path,
                asset_identity=asset_identity,
                ignore_json_native_preview=ignore_json_native_preview,
            ),
        ))
    if include_skin:
        selected = tuple(
            direct_by_id[item.element_id]
            for item in elements
            if item.element_id in direct_by_id
        )
        signatures.append((
            "__visual_resources__",
            _scope_visual_resources_signature(
                root,
                selected,
                svg_path,
                ignore_json_native_preview=ignore_json_native_preview,
            ),
        ))
    return tuple(signatures)


def _mirror_ordinary_slide_ids(spec: TemplateSlideSpec) -> set[str]:
    """Return stable ids that are ordinary Slide content in one mirror page."""
    from .template_structure import (
        TemplateStructureError,
        _NON_VISUAL_TAGS,
        _local_tag,
        _parse_svg_root,
    )
    try:
        root = _parse_svg_root(spec.svg_path)
    except (OSError, ET.ParseError, NativePayloadError) as exc:
        raise TemplateStructureError(
            f"{spec.svg_path.name}: unable to inspect mirror page ownership: {exc}"
        ) from exc
    inherited_ids = {
        item.element_id
        for item in (
            *spec.master_elements,
            *spec.layout_elements,
            *spec.placeholders,
        )
    }
    return {
        element_id
        for child in root
        if _local_tag(child) not in _NON_VISUAL_TAGS
        if (element_id := (child.get("id") or "").strip())
        if element_id not in inherited_ids
    }


def _mirror_slide_local_signature(
    spec: TemplateSlideSpec,
    protected_ids: set[str],
) -> tuple[tuple[object, ...], tuple[object, ...]]:
    """Capture literal mirror visuals that are Slide-local on either page.

    Visible text values and JSON-first native preview children may change, but
    other element topology, attributes, grouping, paint, geometry, and
    referenced asset bytes remain literal.
    Structure metadata may change when adaptive template authoring assigns an
    evolved Layout identity to the same stable SVG id.
    """
    from .template_structure import (
        TemplateStructureError,
        _NON_VISUAL_TAGS,
        _element_tree_signature,
        _local_tag,
        _parse_svg_root,
    )
    try:
        root = _parse_svg_root(spec.svg_path)
        materialize_inline_geometry_properties(root)
    except (OSError, ET.ParseError, NativePayloadError, GeometryStyleError) as exc:
        raise TemplateStructureError(
            f"{spec.svg_path.name}: unable to compare mirror page visuals: {exc}"
        ) from exc
    slide_elements: list[ET.Element] = []
    slide_visuals: list[tuple[object, ...]] = []
    for child in root:
        tag = _local_tag(child)
        if tag in _NON_VISUAL_TAGS:
            continue
        element_id = (child.get("id") or "").strip()
        if element_id and element_id not in protected_ids:
            continue
        slide_elements.append(child)
        slide_visuals.append(
            _element_tree_signature(
                child,
                include_skin=True,
                include_text=False,
                svg_path=spec.svg_path,
                asset_identity=True,
                ignore_structure_attrs=True,
                ignore_json_native_preview=True,
            )
        )
    resources = _scope_visual_resources_signature(
        root,
        tuple(slide_elements),
        spec.svg_path,
        ignore_json_native_preview=True,
    )
    return resources, tuple(slide_visuals)
