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
from collections import Counter
from xml.etree import ElementTree as ET
from .template_structure import (
    OOXML_UINT32_MAX,
)

from .template_validation import (
    _PackageReader,
)

def _validate_unique_creation_ids(
    reader: _PackageReader,
    parts: set[str],
) -> None:
    """Reject cloned PowerPoint parts that retain the same p14:creationId."""
    from .template_validation import (
        P14_NS,
        PML_NS,
    )
    owners: dict[int, str] = {}
    for part in sorted(parts):
        root = reader.xml(part)
        if root is None:
            continue
        c_sld = root.find(f"{{{PML_NS}}}cSld")
        if c_sld is None:
            continue
        seen_in_part: set[int] = set()
        for creation_id in c_sld.findall(
            f"{{{PML_NS}}}extLst/{{{PML_NS}}}ext/"
            f"{{{P14_NS}}}creationId"
        ):
            raw_value = creation_id.get("val")
            try:
                value = int(raw_value or "")
            except ValueError:
                reader.errors.append(
                    f"{part} has invalid p14:creationId value {raw_value!r}"
                )
                continue
            if value < 0 or value > OOXML_UINT32_MAX:
                reader.errors.append(
                    f"{part} p14:creationId {value} is outside OOXML UInt32"
                )
                continue
            if value in seen_in_part:
                reader.errors.append(
                    f"{part} repeats p14:creationId {value}"
                )
                continue
            seen_in_part.add(value)
            previous_owner = owners.setdefault(value, part)
            if previous_owner != part:
                reader.errors.append(
                    f"p14:creationId {value} is shared by "
                    f"{previous_owner} and {part}"
                )


def _top_level_shape_names(
    root: ET.Element,
    context: str,
    errors: list[str],
) -> tuple[str, ...]:
    """Return deterministic names for every visible top-level OOXML shape."""
    from .template_validation import (
        PML_NS,
        _TOP_LEVEL_VISIBLE_TAGS,
    )
    c_sld = root.find(f"{{{PML_NS}}}cSld")
    shape_tree = (
        c_sld.find(f"{{{PML_NS}}}spTree") if c_sld is not None else None
    )
    if shape_tree is None:
        errors.append(f"{context} has no p:cSld/p:spTree")
        return ()
    names: list[str] = []
    for child in shape_tree:
        if child.tag not in _TOP_LEVEL_VISIBLE_TAGS:
            continue
        c_nv_pr = next(child.iter(f"{{{PML_NS}}}cNvPr"), None)
        name = c_nv_pr.get("name") if c_nv_pr is not None else None
        if not name:
            errors.append(f"{context} contains a top-level shape without a name")
            continue
        names.append(name)
    return tuple(names)


def _validate_named_shape_roster(
    root: ET.Element,
    expected_names: set[str] | tuple[str, ...] | list[str],
    context: str,
    errors: list[str],
    *,
    exact: bool,
    ordered: bool = False,
) -> None:
    """Validate reusable structure and ordinary Slide carriers by stable name."""
    expected_sequence = tuple(expected_names)
    expected_set = set(expected_sequence)
    actual_names = _top_level_shape_names(root, context, errors)
    counts = Counter(actual_names)
    duplicates = sorted(name for name, count in counts.items() if count > 1)
    if duplicates:
        errors.append(
            f"{context} repeats structured shape name(s): " + ", ".join(duplicates)
        )
    actual_set = set(actual_names)
    missing = sorted(expected_set - actual_set)
    if missing:
        errors.append(
            f"{context} is missing structured shape(s): " + ", ".join(missing)
        )
    if exact:
        unexpected = sorted(actual_set - expected_set)
        if unexpected:
            errors.append(
                f"{context} contains unexpected shape(s): "
                + ", ".join(unexpected)
            )
    if ordered and not missing:
        actual_sequence = (
            actual_names
            if exact
            else tuple(name for name in actual_names if name in expected_set)
        )
        if actual_sequence != expected_sequence:
            errors.append(
                f"{context} structured shape order is {actual_sequence}, "
                f"expected {expected_sequence}"
            )


def _top_level_shape_by_name(
    root: ET.Element,
    name: str,
) -> ET.Element | None:
    from .template_validation import (
        PML_NS,
        _TOP_LEVEL_VISIBLE_TAGS,
    )
    c_sld = root.find(f"{{{PML_NS}}}cSld")
    shape_tree = (
        c_sld.find(f"{{{PML_NS}}}spTree") if c_sld is not None else None
    )
    if shape_tree is None:
        return None
    for child in shape_tree:
        if child.tag not in _TOP_LEVEL_VISIBLE_TAGS:
            continue
        c_nv_pr = next(child.iter(f"{{{PML_NS}}}cNvPr"), None)
        if c_nv_pr is not None and c_nv_pr.get("name") == name:
            return child
    return None


def _has_explicit_background(root: ET.Element) -> bool:
    from .template_validation import (
        PML_NS,
    )
    c_sld = root.find(f"{{{PML_NS}}}cSld")
    return c_sld is not None and c_sld.find(f"{{{PML_NS}}}bg") is not None


def _xml_payload_signature(elem: ET.Element) -> tuple[object, ...]:
    """Return a namespace-stable exact XML payload signature."""
    return (
        elem.tag,
        tuple(sorted(elem.attrib.items())),
        (elem.text or "").strip(),
        tuple(_xml_payload_signature(child) for child in elem),
    )


def _background_payload_signatures(
    root: ET.Element,
) -> tuple[tuple[object, ...], ...]:
    from .template_validation import (
        PML_NS,
    )
    c_sld = root.find(f"{{{PML_NS}}}cSld")
    backgrounds = (
        c_sld.findall(f"{{{PML_NS}}}bg")
        if c_sld is not None
        else []
    )
    return tuple(_xml_payload_signature(background) for background in backgrounds)


def _expected_background_signature(
    background_xml: str,
) -> tuple[object, ...]:
    """Parse a standalone p:bg fragment regardless of its serialized prefix."""
    from .template_validation import (
        DML_NS,
        PML_NS,
    )
    try:
        wrapper = ET.fromstring(
            f'<root xmlns:p="{PML_NS}" xmlns:a="{DML_NS}">'
            f"{background_xml}</root>"
        )
    except ET.ParseError as exc:
        raise ValueError(f"invalid expected p:bg payload: {exc}") from exc
    children = list(wrapper)
    if len(children) != 1 or children[0].tag != f"{{{PML_NS}}}bg":
        raise ValueError("expected background payload must contain exactly one p:bg")
    return _xml_payload_signature(children[0])
