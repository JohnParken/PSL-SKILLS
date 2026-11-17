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
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree as ET
from .template_structure import (
    OOXML_UINT32_MAX,
    TemplatePlaceholderBinding,
)

from .template_validation import (
    _PackageReader,
    _Placeholder,
    _Relationship,
)

def _relationships_part_for(source_part: str) -> str:
    directory = posixpath.dirname(source_part)
    filename = posixpath.basename(source_part)
    return posixpath.join(directory, "_rels", f"{filename}.rels")


def _resolve_relationship_target(
    source_part: str,
    relationship: _Relationship,
    errors: list[str],
    context: str,
) -> str | None:
    target_mode = (relationship.target_mode or "Internal").lower()
    if target_mode != "internal":
        errors.append(f"{context} relationship must be internal")
        return None
    raw_target = relationship.target
    if "\\" in raw_target or any(ord(char) <= 0x20 for char in raw_target):
        errors.append(f"{context} has invalid target {raw_target!r}")
        return None
    try:
        parsed = urlsplit(raw_target)
    except ValueError:
        errors.append(f"{context} has invalid target {raw_target!r}")
        return None
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
        errors.append(f"{context} has invalid target {raw_target!r}")
        return None
    decoded = unquote(parsed.path)
    if not decoded:
        errors.append(f"{context} has an empty target")
        return None
    if decoded.startswith("/"):
        candidate = decoded[1:]
    else:
        candidate = posixpath.join(posixpath.dirname(source_part), decoded)
    normalized = posixpath.normpath(candidate)
    if (
        normalized in {"", ".", ".."}
        or normalized.startswith("../")
        or normalized.startswith("/")
    ):
        errors.append(f"{context} target escapes the package: {raw_target!r}")
        return None
    return normalized


def _single_relationship_target(
    reader: _PackageReader,
    source_part: str,
    rel_type: str,
    context: str,
) -> tuple[_Relationship, str] | None:
    matches = [
        relationship
        for relationship in reader.relationships(source_part)
        if relationship.rel_type == rel_type
    ]
    if len(matches) != 1:
        reader.errors.append(
            f"{context} must have exactly one {rel_type.rsplit('/', 1)[-1]} "
            f"relationship, found {len(matches)}"
        )
        return None
    target = _resolve_relationship_target(
        source_part,
        matches[0],
        reader.errors,
        context,
    )
    if target is None:
        return None
    return matches[0], target


def _placeholder_for_shape(shape: ET.Element) -> ET.Element | None:
    from .template_validation import (
        PML_NS,
    )
    paths = {
        f"{{{PML_NS}}}sp": (
            f"{{{PML_NS}}}nvSpPr/{{{PML_NS}}}nvPr/{{{PML_NS}}}ph"
        ),
        f"{{{PML_NS}}}pic": (
            f"{{{PML_NS}}}nvPicPr/{{{PML_NS}}}nvPr/{{{PML_NS}}}ph"
        ),
        f"{{{PML_NS}}}graphicFrame": (
            f"{{{PML_NS}}}nvGraphicFramePr/{{{PML_NS}}}nvPr/{{{PML_NS}}}ph"
        ),
    }
    path = paths.get(shape.tag)
    return shape.find(path) if path else None


def _read_placeholders(
    root: ET.Element,
    context: str,
    errors: list[str],
) -> dict[int, _Placeholder]:
    from .template_validation import (
        PML_NS,
        _Placeholder,
    )
    sp_tree = root.find(f"{{{PML_NS}}}cSld/{{{PML_NS}}}spTree")
    if sp_tree is None:
        errors.append(f"{context} has no p:cSld/p:spTree")
        return {}
    placeholders: dict[int, _Placeholder] = {}
    for shape in sp_tree:
        ph = _placeholder_for_shape(shape)
        if ph is None:
            continue
        raw_idx_value = ph.get("idx")
        try:
            raw_idx = int(raw_idx_value) if raw_idx_value is not None else None
        except ValueError:
            errors.append(f"{context} contains invalid placeholder idx {raw_idx_value!r}")
            continue
        idx = raw_idx if raw_idx is not None else 0
        if not 0 <= idx <= OOXML_UINT32_MAX:
            errors.append(
                f"{context} placeholder idx {idx} is outside the OOXML UInt32 range"
            )
            continue
        if idx in placeholders:
            errors.append(f"{context} repeats effective placeholder idx {idx}")
            continue
        raw_type = ph.get("type")
        placeholders[idx] = _Placeholder(
            shape=shape,
            placeholder_type=raw_type or "obj",
            raw_type=raw_type,
            idx=idx,
            raw_idx=raw_idx,
        )
    return placeholders


def _validate_placeholder_roster(
    root: ET.Element,
    bindings: tuple[TemplatePlaceholderBinding, ...],
    context: str,
    errors: list[str],
    *,
    is_layout: bool,
) -> dict[int, _Placeholder]:
    actual = _read_placeholders(root, context, errors)
    expected = {binding.effective_idx: binding for binding in bindings}
    if set(actual) != set(expected):
        errors.append(
            f"{context} placeholder idx roster is {sorted(actual)}, "
            f"expected {sorted(expected)}"
        )
    for idx in sorted(set(actual).intersection(expected)):
        placeholder = actual[idx]
        binding = expected[idx]
        if placeholder.placeholder_type != binding.placeholder_type:
            errors.append(
                f"{context} placeholder idx {idx} has type "
                f"{placeholder.placeholder_type!r}, expected "
                f"{binding.placeholder_type!r}"
            )
        if binding.assigned_idx is None:
            if placeholder.raw_idx is not None:
                errors.append(
                    f"{context} title placeholder must use the omitted idx=0 form"
                )
        elif placeholder.raw_idx != binding.assigned_idx:
            errors.append(
                f"{context} placeholder idx {idx} must serialize explicit idx "
                f"{binding.assigned_idx}"
            )
        if is_layout and placeholder.raw_type is None:
            errors.append(
                f"{context} Layout placeholder idx {idx} must serialize its type"
            )
        if (
            not is_layout
            and binding.placeholder_type != "obj"
            and placeholder.raw_type is None
        ):
            errors.append(
                f"{context} Slide placeholder idx {idx} must serialize type "
                f"{binding.placeholder_type!r}"
            )
    return actual


def _validate_layout_header_footer(
    root: ET.Element,
    bindings: tuple[TemplatePlaceholderBinding, ...],
    context: str,
    errors: list[str],
) -> None:
    from .template_validation import (
        PML_NS,
    )
    placeholder_roles = {
        binding.element.placeholder for binding in bindings
    }
    expected = {
        "hdr": False,
        "dt": "date" in placeholder_roles,
        "ftr": "footer" in placeholder_roles,
        "sldNum": "slide-number" in placeholder_roles,
    }
    header_footer = root.find(f"{{{PML_NS}}}hf")
    if header_footer is None:
        if any(expected.values()):
            errors.append(
                f"{context} has date/footer/slide-number placeholders but no p:hf"
            )
        return
    for attr, expected_value in expected.items():
        # CT_HeaderFooter boolean attributes default to true when omitted.
        raw_value = header_footer.get(attr, "1").lower()
        if raw_value not in {"0", "1", "false", "true"}:
            errors.append(f"{context} p:hf@{attr} is not a valid boolean")
            continue
        actual_value = raw_value in {"1", "true"}
        if actual_value != expected_value:
            errors.append(
                f"{context} p:hf@{attr} is {raw_value!r}, expected "
                f"{'1' if expected_value else '0'}"
            )


def _shape_bounds(
    shape: ET.Element,
    context: str,
    errors: list[str],
) -> tuple[int, int, int, int] | None:
    from .template_validation import (
        DML_NS,
        PML_NS,
    )
    if shape.tag == f"{{{PML_NS}}}graphicFrame":
        xfrm = shape.find(f"{{{PML_NS}}}xfrm")
    else:
        xfrm = shape.find(f"{{{PML_NS}}}spPr/{{{DML_NS}}}xfrm")
    if xfrm is None:
        errors.append(f"{context} has no direct placeholder transform")
        return None
    off = xfrm.find(f"{{{DML_NS}}}off")
    ext = xfrm.find(f"{{{DML_NS}}}ext")
    if off is None or ext is None:
        errors.append(f"{context} placeholder transform has no a:off/a:ext")
        return None
    try:
        bounds = (
            int(off.attrib["x"]),
            int(off.attrib["y"]),
            int(ext.attrib["cx"]),
            int(ext.attrib["cy"]),
        )
    except (KeyError, ValueError):
        errors.append(f"{context} placeholder transform is invalid")
        return None
    if bounds[2] <= 0 or bounds[3] <= 0:
        errors.append(f"{context} placeholder width/height must be positive")
    return bounds


def _direct_text_body(shape: ET.Element) -> ET.Element | None:
    from .template_validation import (
        PML_NS,
    )
    return shape.find(f"{{{PML_NS}}}txBody")


def _first_run_size(shape: ET.Element) -> str | None:
    from .template_validation import (
        DML_NS,
    )
    text_body = _direct_text_body(shape)
    if text_body is None:
        return None
    run_props = text_body.find(f".//{{{DML_NS}}}rPr")
    return run_props.get("sz") if run_props is not None else None


def _level_one_default_size(shape: ET.Element) -> str | None:
    from .template_validation import (
        DML_NS,
    )
    text_body = _direct_text_body(shape)
    if text_body is None:
        return None
    default_props = text_body.find(
        f"{{{DML_NS}}}lstStyle/"
        f"{{{DML_NS}}}lvl1pPr/"
        f"{{{DML_NS}}}defRPr"
    )
    return default_props.get("sz") if default_props is not None else None
