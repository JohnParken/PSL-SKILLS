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
from ..geometry_properties import (
    materialize_inline_geometry_properties,
)

from .template_structure import (
    TemplateElementSpec,
    TemplateSlideSpec,
)

def _prototype_placeholder_contract(
    spec: TemplateSlideSpec,
) -> tuple[tuple[object, ...], ...]:
    """Return the strict template placeholder contract without slide content."""
    return tuple(item.contract_signature() for item in spec.placeholders)


def _layout_contract_difference(
    actual: tuple[TemplateElementSpec, ...],
    expected: tuple[TemplateElementSpec, ...],
) -> str:
    """Describe the smallest actionable difference in a Layout atom roster."""
    actual_ids = tuple(item.element_id for item in actual)
    expected_ids = tuple(item.element_id for item in expected)
    actual_id_set = set(actual_ids)
    expected_id_set = set(expected_ids)
    missing = tuple(item for item in expected_ids if item not in actual_id_set)
    unexpected = tuple(item for item in actual_ids if item not in expected_id_set)
    details: list[str] = []
    if missing:
        details.append(
            "missing generated Layout element id(s): "
            + ", ".join(repr(item) for item in missing)
        )
    if unexpected:
        details.append(
            "unexpected generated Layout element id(s): "
            + ", ".join(repr(item) for item in unexpected)
        )
    if not details and actual_ids != expected_ids:
        details.append("generated Layout element order differs")
    if not details:
        details.append(
            "shared Layout element metadata (data-pptx-* attributes), geometry, "
            "topology, or content differs; fills, strokes, and gradients may be "
            "repainted"
        )
    return "; ".join(details)


def _mirror_comparable_attributes(
    element: ET.Element,
    svg_path: Path,
    *,
    ignore_structure_attrs: bool,
) -> dict[str, str]:
    """Return literal mirror attributes using the validator's normalization."""
    from .template_structure import (
        _STRUCTURE_ATTRS,
        _signature_attr_value,
    )
    return {
        name: _signature_attr_value(
            name,
            value,
            svg_path=svg_path,
            asset_identity=True,
        )
        for name, value in element.attrib.items()
        if not (
            ignore_structure_attrs
            and name.rsplit("}", 1)[-1] in _STRUCTURE_ATTRS
        )
    }


def _mirror_node_label(element: ET.Element, index: int) -> str:
    """Return a compact stable-enough path segment for one SVG node."""
    from .template_structure import (
        _local_tag,
    )
    tag = _local_tag(element)
    element_id = (element.get("id") or "").strip()
    return f"{tag}#{element_id}" if element_id else f"{tag}[{index}]"


def _mirror_element_difference(
    expected: ET.Element,
    actual: ET.Element,
    *,
    expected_svg: Path,
    actual_svg: Path,
    path: str,
    ignore_structure_attrs: bool,
) -> str | None:
    """Describe the first literal mirror subtree difference."""
    from .template_structure import (
        _local_tag,
        _mirror_comparison_children,
    )
    expected_tag = _local_tag(expected)
    actual_tag = _local_tag(actual)
    if expected_tag != actual_tag:
        return f"{path}: expected <{expected_tag}>, found <{actual_tag}>"
    if (
        expected_tag == "metadata"
        and (expected.text or "") != (actual.text or "")
    ):
        return f"{path}: metadata payload differs"

    expected_attrs = _mirror_comparable_attributes(
        expected,
        expected_svg,
        ignore_structure_attrs=ignore_structure_attrs,
    )
    actual_attrs = _mirror_comparable_attributes(
        actual,
        actual_svg,
        ignore_structure_attrs=ignore_structure_attrs,
    )
    if expected_attrs != actual_attrs:
        for name in sorted(set(expected_attrs) | set(actual_attrs)):
            expected_value = expected_attrs.get(name)
            actual_value = actual_attrs.get(name)
            if expected_value != actual_value:
                return (
                    f"{path}: attribute {name.rsplit('}', 1)[-1]!r} expected "
                    f"{expected_value!r}, found {actual_value!r}"
                )

    expected_children = list(_mirror_comparison_children(expected))
    actual_children = list(_mirror_comparison_children(actual))
    if len(expected_children) != len(actual_children):
        expected_tspans = sum(
            _local_tag(child) == "tspan" for child in expected_children
        )
        actual_tspans = sum(
            _local_tag(child) == "tspan" for child in actual_children
        )
        if expected_tspans != actual_tspans:
            return (
                f"{path}: expected {expected_tspans} direct <tspan> child(ren), "
                f"found {actual_tspans}; mirror text node count/order must stay "
                "unchanged"
            )
        return (
            f"{path}: expected {len(expected_children)} child node(s), found "
            f"{len(actual_children)}"
        )

    for index, (expected_child, actual_child) in enumerate(
        zip(expected_children, actual_children),
        start=1,
    ):
        child_path = f"{path}/{_mirror_node_label(expected_child, index)}"
        difference = _mirror_element_difference(
            expected_child,
            actual_child,
            expected_svg=expected_svg,
            actual_svg=actual_svg,
            path=child_path,
            ignore_structure_attrs=ignore_structure_attrs,
        )
        if difference:
            return difference
    return None


def _mirror_slide_local_difference(
    expected_spec: TemplateSlideSpec,
    actual_spec: TemplateSlideSpec,
    protected_ids: set[str],
) -> str | None:
    """Describe the first Slide-local mirror difference."""
    from .template_structure import (
        _NON_VISUAL_TAGS,
        _local_tag,
        _parse_svg_root,
        _scope_visual_resources_signature,
    )
    expected_root = _parse_svg_root(expected_spec.svg_path)
    actual_root = _parse_svg_root(actual_spec.svg_path)
    materialize_inline_geometry_properties(expected_root)
    materialize_inline_geometry_properties(actual_root)

    def selected(root: ET.Element) -> list[ET.Element]:
        output: list[ET.Element] = []
        for child in root:
            if _local_tag(child) in _NON_VISUAL_TAGS:
                continue
            element_id = (child.get("id") or "").strip()
            if element_id and element_id not in protected_ids:
                continue
            output.append(child)
        return output

    expected_children = selected(expected_root)
    actual_children = selected(actual_root)
    if len(expected_children) != len(actual_children):
        return (
            f"svg: expected {len(expected_children)} Slide-local top-level "
            f"element(s), found {len(actual_children)}"
        )
    for index, (expected, actual) in enumerate(
        zip(expected_children, actual_children),
        start=1,
    ):
        difference = _mirror_element_difference(
            expected,
            actual,
            expected_svg=expected_spec.svg_path,
            actual_svg=actual_spec.svg_path,
            path=f"svg/{_mirror_node_label(expected, index)}",
            ignore_structure_attrs=True,
        )
        if difference:
            return difference
    if _scope_visual_resources_signature(
        expected_root,
        tuple(expected_children),
        expected_spec.svg_path,
        ignore_json_native_preview=True,
    ) != _scope_visual_resources_signature(
        actual_root,
        tuple(actual_children),
        actual_spec.svg_path,
        ignore_json_native_preview=True,
    ):
        return "svg: referenced defs, CSS, root styling, or asset identity differs"
    return None


def _mirror_structure_difference(
    expected_spec: TemplateSlideSpec,
    actual_spec: TemplateSlideSpec,
    elements: tuple[TemplateElementSpec, ...],
) -> str | None:
    """Describe the first mirror difference in named structural elements."""
    from .template_structure import (
        _parse_svg_root,
    )
    expected_root = _parse_svg_root(expected_spec.svg_path)
    actual_root = _parse_svg_root(actual_spec.svg_path)
    materialize_inline_geometry_properties(expected_root)
    materialize_inline_geometry_properties(actual_root)
    expected_by_id = {
        (child.get("id") or "").strip(): child
        for child in expected_root
        if (child.get("id") or "").strip()
    }
    actual_by_id = {
        (child.get("id") or "").strip(): child
        for child in actual_root
        if (child.get("id") or "").strip()
    }
    for item in elements:
        expected = expected_by_id.get(item.element_id)
        actual = actual_by_id.get(item.element_id)
        if expected is None or actual is None:
            return (
                f"svg/{item.element_id}: expected direct structural element is "
                "missing from one side"
            )
        difference = _mirror_element_difference(
            expected,
            actual,
            expected_svg=expected_spec.svg_path,
            actual_svg=actual_spec.svg_path,
            path=f"svg/{_mirror_node_label(expected, 1)}",
            ignore_structure_attrs=False,
        )
        if difference:
            return difference
    return None
