#!/usr/bin/env python3
"""
PPT Master - Large Vector Asset Extractor

Factor large non-semantic vector decorations out of working SVGs into project
icon assets, leaving a compact decoration-marked `<use data-icon>` placeholder
behind. Semantic objects and semantic descendants always remain inline. The
existing icon embedding path re-inlines each asset before export, so the
exported PPTX remains native shapes rather than an embedded picture.

Because re-inlining restores the extracted decoration subtree, the detection
threshold is a readability convenience. It never expands the eligible asset
role beyond decoration.

Usage:
    python3 scripts/extract_svg_assets.py <svg_dir> [options]

Examples:
    python3 scripts/extract_svg_assets.py import_ws/authoring-svg \
        --icons-dir import_ws/icons --icon-namespace imported \
        --inplace --id-prefix layered --clean-stale
    python3 scripts/extract_svg_assets.py import_ws/authoring-svg-flat \
        --icons-dir import_ws/icons --icon-namespace imported \
        --reuse-inventory import_ws/authoring-svg_vector_asset_inventory.json \
        --inplace --id-prefix flat --clean-stale
    python3 scripts/extract_svg_assets.py project/svg_output --inplace --min-drawables 40

Dependencies:
    None (standard library only).

See workflows/create-template.md and svg_finalize/embed_icons.py.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit, urlunsplit
from xml.etree import ElementTree as ET

from console_encoding import configure_utf8_stdio
from pptx_shapes import (
    NATIVE_FALLBACK_SHA256_ATTR,
    svg_native_fallback_fingerprint,
)
from svg_authoring_view import (
    AUTHORING_MANIFEST_NAME,
    SEMANTIC_OBJECT_ATTRIBUTE,
    write_authoring_summary,
)
from svg_authoring_contract import normalize_compact_authoring_tree



# Upstream MIT, Hugo He 2025-2026; algorithms retained during module split.

def _local(tag: object) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _drawable_count(elem: ET.Element) -> int:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from extract_svg_assets import (
        DRAWABLE,
        _local,
    )
    return sum(1 for e in elem.iter() if _local(e.tag) in DRAWABLE)


def _xml_size(elem: ET.Element) -> int:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from extract_svg_assets import (
        SOURCE_REF_ATTRIBUTE,
        SVG_NS,
        XLINK_NS,
    )
    if not any(item.get(SOURCE_REF_ATTRIBUTE) for item in elem.iter()):
        ET.register_namespace("", SVG_NS)
        ET.register_namespace("xlink", XLINK_NS)
        return len(ET.tostring(elem, encoding="utf-8"))
    measured = copy.deepcopy(elem)
    for item in measured.iter():
        item.attrib.pop(SOURCE_REF_ATTRIBUTE, None)
    ET.register_namespace("", SVG_NS)
    ET.register_namespace("xlink", XLINK_NS)
    return len(ET.tostring(measured, encoding="utf-8"))


def _large_enough(elem: ET.Element, min_drawables: int, min_bytes: int) -> bool:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from extract_svg_assets import (
        _drawable_count,
        _xml_size,
    )
    return _drawable_count(elem) >= min_drawables or _xml_size(elem) >= min_bytes


def _has_semantic_content(elem: ET.Element) -> bool:
    """Text-bearing groups must stay readable/editable in the working SVG."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from extract_svg_assets import (
        SEMANTIC_CONTENT,
        _local,
    )
    return any(_local(e.tag) in SEMANTIC_CONTENT for e in elem.iter())


def _has_semantic_object(elem: ET.Element) -> bool:
    """Semantic authoring objects must never move into imported decoration."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from extract_svg_assets import (
        _is_semantic_owner,
    )
    return any(_is_semantic_owner(item) for item in elem.iter())


def _is_semantic_owner(elem: ET.Element) -> bool:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from extract_svg_assets import (
        SEMANTIC_MARKER_ATTRIBUTES,
        _local,
    )
    if any(elem.get(name) is not None for name in SEMANTIC_MARKER_ATTRIBUTES):
        return True
    return (
        _local(elem.tag) == "metadata"
        and elem.get("type") == "application/json"
    )


def _is_existing_placeholder(elem: ET.Element) -> bool:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from extract_svg_assets import (
        _local,
    )
    return _local(elem.tag) == "use" and elem.get("data-icon") is not None


def _has_icon_placeholder(elem: ET.Element) -> bool:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from extract_svg_assets import (
        _is_existing_placeholder,
    )
    return any(_is_existing_placeholder(item) for item in elem.iter())


def _is_extractable_subtree(elem: ET.Element) -> bool:
    """Pure vector subtrees can be moved; semantic content must stay inline."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from extract_svg_assets import (
        DEFINITION_CONTAINERS,
        _drawable_count,
        _has_icon_placeholder,
        _has_semantic_content,
        _has_semantic_object,
        _is_chart_group,
        _local,
    )
    if (
        _local(elem.tag) in DEFINITION_CONTAINERS
        or _has_semantic_object(elem)
        or _has_icon_placeholder(elem)
        or _is_chart_group(elem)
        or _has_semantic_content(elem)
    ):
        return False
    return _drawable_count(elem) > 0


def _is_chart_group(elem: ET.Element) -> bool:
    """Charts are handled separately (data + calibration) — never extract them."""
    gid = (elem.get("id") or "").lower()
    if "chart" in gid:
        return True
    return any("chart" in (e.get("id") or "").lower() for e in elem.iter())


def _tag_histogram(elem: ET.Element) -> dict[str, int]:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from extract_svg_assets import (
        DRAWABLE,
        _local,
    )
    hist: dict[str, int] = {}
    for e in elem.iter():
        name = _local(e.tag)
        if name in DRAWABLE:
            hist[name] = hist.get(name, 0) + 1
    return hist


def _source_references(elem: ET.Element) -> list[str]:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from extract_svg_assets import (
        SOURCE_REF_ATTRIBUTE,
    )
    return sorted({
        source_ref
        for item in elem.iter()
        if (source_ref := item.get(SOURCE_REF_ATTRIBUTE))
    })


def _is_descendant(container: ET.Element, candidate: ET.Element) -> bool:
    return any(elem is candidate for elem in container.iter())


def _id_index(root: ET.Element) -> dict[str, ET.Element]:
    return {elem_id: elem for elem in root.iter() if (elem_id := elem.get("id"))}


def _referenced_ids(elem: ET.Element) -> set[str]:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from extract_svg_assets import (
        URL_REF_RE,
        _local,
    )
    refs: set[str] = set()
    for item in elem.iter():
        for attr_name, value in item.attrib.items():
            refs.update(match.group(2) for match in URL_REF_RE.finditer(value))
            if _local(attr_name) == "href" and value.startswith("#") and len(value) > 1:
                refs.add(value[1:])
    return refs


def _dependency_elements(root: ET.Element, asset_group: ET.Element) -> list[ET.Element]:
    """
    Return external definition elements referenced by the extracted subtree.

    Gradients, patterns, filters, clip paths, and markers often live in the
    source SVG root <defs>. The extracted asset must carry these dependencies
    itself; otherwise the standalone icon and later re-inline can lose styling.
    """
    # Resolve shared facade state at call time, preserving upstream bindings.
    from extract_svg_assets import (
        _id_index,
        _is_descendant,
        _referenced_ids,
    )
    by_id = _id_index(root)
    dependencies: list[ET.Element] = []
    seen: set[str] = set()
    queue = sorted(_referenced_ids(asset_group))

    while queue:
        ref_id = queue.pop(0)
        if ref_id in seen:
            continue
        seen.add(ref_id)

        target = by_id.get(ref_id)
        if target is None or _is_descendant(asset_group, target):
            continue

        dependencies.append(target)
        for nested_ref in sorted(_referenced_ids(target)):
            if nested_ref not in seen:
                queue.append(nested_ref)

    return dependencies


def _collect_id_mapping(asset_id: str, group: ET.Element, dependencies: list[ET.Element]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for root in [group, *dependencies]:
        for elem in root.iter():
            elem_id = elem.get("id")
            if elem_id and elem_id not in mapping:
                mapping[elem_id] = f"{asset_id}_{elem_id}"
    return mapping


def _rewrite_references(elem: ET.Element, id_mapping: dict[str, str]) -> None:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from extract_svg_assets import (
        _rewrite_reference_values,
    )
    _rewrite_reference_values(elem, id_mapping)

    for item in elem.iter():
        elem_id = item.get("id")
        if elem_id in id_mapping:
            item.set("id", id_mapping[elem_id])


def _rebase_external_hrefs(
    elem: ET.Element,
    source_dir: Path,
    target_dir: Path,
) -> int:
    """Keep relative image/use references valid after moving a subtree."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from extract_svg_assets import (
        _local,
    )
    rewritten_count = 0
    for item in elem.iter():
        for attr_name, value in list(item.attrib.items()):
            if _local(attr_name) != "href" or value.startswith(("#", "/")):
                continue
            parsed = urlsplit(value)
            if parsed.scheme or parsed.netloc or not parsed.path:
                continue
            source_target = (source_dir / parsed.path).resolve()
            relative = Path(os.path.relpath(source_target, target_dir)).as_posix()
            rewritten = urlunsplit(("", "", relative, parsed.query, parsed.fragment))
            if rewritten != value:
                item.set(attr_name, rewritten)
                rewritten_count += 1
    return rewritten_count


def _rewrite_reference_values(
    elem: ET.Element,
    id_mapping: dict[str, str],
) -> None:
    """Rewrite local URL/href references without renaming definition ids."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from extract_svg_assets import (
        URL_REF_RE,
        _local,
    )
    def rewrite_url(match: re.Match[str]) -> str:
        quote, ref_id = match.group(1), match.group(2)
        new_id = id_mapping.get(ref_id, ref_id)
        return f"url({quote}#{new_id}{quote})"

    for item in elem.iter():
        for attr_name, value in list(item.attrib.items()):
            rewritten = URL_REF_RE.sub(rewrite_url, value)
            if _local(attr_name) == "href" and value.startswith("#") and value[1:] in id_mapping:
                rewritten = f"#{id_mapping[value[1:]]}"
            if rewritten != value:
                item.set(attr_name, rewritten)


def _definition_signature(elem: ET.Element) -> bytes:
    """Return definition semantics without its document-local id."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from extract_svg_assets import (
        SVG_NS,
        XLINK_NS,
    )
    normalized = copy.deepcopy(elem)
    normalized.attrib.pop("id", None)
    ET.register_namespace("", SVG_NS)
    ET.register_namespace("xlink", XLINK_NS)
    return ET.tostring(normalized, encoding="utf-8")


