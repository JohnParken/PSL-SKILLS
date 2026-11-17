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
import copy
import hashlib
import re
from pathlib import Path
from xml.etree import ElementTree as ET
from ..native_objects import (
    NativeMarkerAttributeError,
    native_json_is_authoritative,
    native_replacement_kind,
)

def _signature_attr_value(
    name: str,
    value: str,
    *,
    svg_path: Path | None,
    asset_identity: bool,
) -> str:
    """Normalize portable asset references without weakening visual identity."""
    from .template_structure import (
        TemplateStructureError,
        _file_sha256,
    )
    if name.rsplit("}", 1)[-1] != "href":
        return value
    if asset_identity:
        if value.startswith("#") or "://" in value:
            return value
        if value.startswith("data:"):
            return "data-sha256:" + hashlib.sha256(
                value.encode("utf-8")
            ).hexdigest()
        if svg_path is None:
            raise TemplateStructureError(
                "literal asset comparison requires the source SVG path"
            )
        asset_path = (svg_path.parent / value).resolve()
        if not asset_path.is_file():
            raise TemplateStructureError(
                f"{svg_path.name}: mirror asset reference does not resolve: "
                f"{value!r}"
            )
        return "file-sha256:" + _file_sha256(asset_path)
    if value.startswith("data:") or "://" in value:
        return value
    return value.replace("\\", "/").rsplit("/", 1)[-1]


def _element_tree_signature(
    elem: ET.Element,
    *,
    include_skin: bool = False,
    include_text: bool = True,
    svg_path: Path | None = None,
    asset_identity: bool = False,
    ignore_structure_attrs: bool = False,
    ignore_json_native_preview: bool = False,
) -> tuple[object, ...]:
    """Return a stable structural or literal-visual SVG subtree signature."""
    from .template_structure import (
        _STRUCTURE_ATTRS,
        _TEMPLATE_SKIN_ATTRS,
        _local_tag,
    )
    text = (elem.text or "") if include_text else ""
    if _local_tag(elem) not in {"text", "tspan"} and not text.strip():
        text = ""
    attrs = tuple(sorted(
        (
            name,
            _signature_attr_value(
                name,
                value,
                svg_path=svg_path,
                asset_identity=asset_identity,
            ),
        )
        for name, value in elem.attrib.items()
        if (
            not (
                ignore_structure_attrs
                and name.rsplit("}", 1)[-1] in _STRUCTURE_ATTRS
            )
            and (
                include_skin
                or name.rsplit("}", 1)[-1] not in _TEMPLATE_SKIN_ATTRS
            )
        )
    ))
    children = (
        _mirror_comparison_children(elem)
        if ignore_json_native_preview
        else tuple(elem)
    )
    json_native_marker = (
        ignore_json_native_preview and _is_json_first_native_marker(elem)
    )
    return (
        elem.tag,
        attrs,
        text,
        tuple(
            _element_tree_signature(
                child,
                include_skin=include_skin,
                include_text=(
                    include_text
                    or (
                        json_native_marker
                        and _local_tag(child) == "metadata"
                    )
                ),
                svg_path=svg_path,
                asset_identity=asset_identity,
                ignore_structure_attrs=ignore_structure_attrs,
                ignore_json_native_preview=ignore_json_native_preview,
            )
            for child in children
        ),
    )


def _is_json_first_native_marker(elem: ET.Element) -> bool:
    """Return whether this element directly owns JSON-first Chart/Table data."""
    try:
        replacement_kind = native_replacement_kind(elem)
    except NativeMarkerAttributeError:
        return False
    return (
        native_json_is_authoritative(elem)
        and replacement_kind in {"chart", "table"}
    )


def _mirror_comparison_children(elem: ET.Element) -> tuple[ET.Element, ...]:
    """Exclude only the derived preview below a JSON-first native marker."""
    from .template_structure import (
        _local_tag,
    )
    if not _is_json_first_native_marker(elem):
        return tuple(elem)
    return tuple(child for child in elem if _local_tag(child) == "metadata")


def _mirror_resource_projection(elem: ET.Element) -> ET.Element:
    """Copy one comparison subtree without JSON-first derived previews."""
    from .template_structure import (
        _local_tag,
    )
    projected = copy.deepcopy(elem)

    def prune(node: ET.Element) -> None:
        if _is_json_first_native_marker(node):
            for child in list(node):
                if _local_tag(child) != "metadata":
                    node.remove(child)
            return
        for child in node:
            prune(child)

    prune(projected)
    return projected


def _svg_reference_ids(elem: ET.Element) -> set[str]:
    """Return fragment ids referenced anywhere in one SVG subtree."""
    from .template_structure import (
        _CSS_URL_RE,
    )
    references: set[str] = set()
    for node in elem.iter():
        for name, value in node.attrib.items():
            if name.rsplit("}", 1)[-1] == "href" and value.startswith("#"):
                references.add(value[1:])
            references.update(
                match.group(2)[1:]
                for match in _CSS_URL_RE.finditer(value)
                if match.group(2).startswith("#")
            )
    return references


def _font_family_names(raw_value: str) -> set[str]:
    """Return normalized CSS font-family names from one declaration value."""
    return {
        value.strip().strip("'\"")
        for value in raw_value.split(",")
        if value.strip().strip("'\"")
    }


def _font_families_from_declarations(raw: str) -> set[str]:
    """Return font families assigned by one inline or stylesheet declaration."""
    families: set[str] = set()
    for match in re.finditer(
        r"font-family\s*:\s*([^;{}]+)",
        raw,
        flags=re.IGNORECASE,
    ):
        families.update(_font_family_names(match.group(1)))
    return families


def _scope_selector_tokens(
    root: ET.Element,
    elements: tuple[ET.Element, ...],
) -> tuple[set[str], set[str], set[str], list[dict[str, str]], set[str]]:
    """Collect the small selector vocabulary needed to filter SVG CSS rules."""
    from .template_structure import (
        _local_tag,
    )
    ids: set[str] = set()
    classes: set[str] = set()
    tags: set[str] = set()
    attributes: list[dict[str, str]] = []
    font_families: set[str] = set()
    nodes = [root]
    for element in elements:
        nodes.extend(element.iter())
    for node in nodes:
        tags.add(_local_tag(node))
        node_id = (node.get("id") or "").strip()
        if node_id:
            ids.add(node_id)
        classes.update((node.get("class") or "").split())
        local_attrs = {
            name.rsplit("}", 1)[-1]: value
            for name, value in node.attrib.items()
        }
        attributes.append(local_attrs)
        if local_attrs.get("font-family"):
            font_families.update(
                _font_family_names(local_attrs["font-family"])
            )
        if local_attrs.get("style"):
            font_families.update(
                _font_families_from_declarations(local_attrs["style"])
            )
    return ids, classes, tags, attributes, font_families


def _css_selector_matches_scope(
    selector: str,
    *,
    ids: set[str],
    classes: set[str],
    tags: set[str],
    attributes: list[dict[str, str]],
) -> bool:
    """Conservatively decide whether one simple SVG selector can affect scope."""
    from .template_structure import (
        _CSS_ATTR_RE,
        _CSS_CLASS_RE,
        _CSS_ID_RE,
        _CSS_TAG_RE,
    )
    selector_ids = set(_CSS_ID_RE.findall(selector))
    if selector_ids and not selector_ids.issubset(ids):
        return False
    selector_classes = set(_CSS_CLASS_RE.findall(selector))
    if selector_classes and not selector_classes.issubset(classes):
        return False
    for match in _CSS_ATTR_RE.finditer(selector):
        attr_name = match.group(1).rsplit(":", 1)[-1]
        expected = (match.group(3) or "").strip()
        if not any(
            attr_name in attrs
            and (not expected or attrs[attr_name].strip() == expected)
            for attrs in attributes
        ):
            return False
    selector_tags = {
        tag.lower()
        for tag in _CSS_TAG_RE.findall(selector)
        if tag != "*"
    }
    if selector_tags and not selector_tags.issubset(
        {tag.lower() for tag in tags}
    ):
        return False
    return True


def _css_asset_signature(value: str, svg_path: Path) -> str:
    """Replace CSS URL assets with byte identities while retaining fragments."""
    from .template_structure import (
        _CSS_URL_RE,
    )
    def replace(match: re.Match[str]) -> str:
        target = match.group(2).strip()
        if target.startswith("#"):
            return f"url({target})"
        identity = _signature_attr_value(
            "href",
            target,
            svg_path=svg_path,
            asset_identity=True,
        )
        return f"url({identity})"

    return _CSS_URL_RE.sub(replace, value)


def _normalize_css_declarations(raw: str, svg_path: Path) -> str:
    """Normalize formatting-only CSS differences without changing cascade order."""
    declarations: list[str] = []
    for raw_declaration in raw.split(";"):
        declaration = raw_declaration.strip()
        if not declaration:
            continue
        if ":" not in declaration:
            declarations.append(" ".join(declaration.split()))
            continue
        name, value = declaration.split(":", 1)
        normalized_value = " ".join(
            _css_asset_signature(value.strip(), svg_path).split()
        )
        declarations.append(f"{name.strip().lower()}:{normalized_value}")
    return ";".join(declarations)
