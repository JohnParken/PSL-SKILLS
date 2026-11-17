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

def _optimize_definitions(root: ET.Element) -> tuple[int, int]:
    """Deduplicate equivalent defs and remove definitions with no live refs."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from extract_svg_assets import (
        DEFINITION_CONTAINERS,
        URL_REF_RE,
        _definition_signature,
        _local,
        _referenced_ids,
        _rewrite_reference_values,
    )
    deduplicated = 0
    pruned = 0
    definition_containers = [
        child for child in root
        if _local(child.tag) in DEFINITION_CONTAINERS
    ]

    for definitions in definition_containers:
        canonical_by_signature: dict[bytes, str] = {}
        duplicate_ids: dict[str, str] = {}
        duplicate_elements: list[ET.Element] = []
        for definition in list(definitions):
            definition_id = definition.get("id")
            if not definition_id:
                continue
            signature = _definition_signature(definition)
            canonical_id = canonical_by_signature.get(signature)
            if canonical_id is None:
                canonical_by_signature[signature] = definition_id
                continue
            duplicate_ids[definition_id] = canonical_id
            duplicate_elements.append(definition)

        if duplicate_ids:
            _rewrite_reference_values(root, duplicate_ids)
            for definition in duplicate_elements:
                definitions.remove(definition)
            deduplicated += len(duplicate_elements)

        owner_by_id: dict[str, ET.Element] = {}
        for definition in definitions:
            for item in definition.iter():
                if item_id := item.get("id"):
                    owner_by_id[item_id] = definition

        live_refs: set[str] = set()
        for attr_name, value in root.attrib.items():
            live_refs.update(match.group(2) for match in URL_REF_RE.finditer(value))
            if _local(attr_name) == "href" and value.startswith("#"):
                live_refs.add(value[1:])
        for child in root:
            if child is definitions:
                continue
            live_refs.update(_referenced_ids(child))

        reachable: set[ET.Element] = set()
        pending = sorted(live_refs)
        while pending:
            ref_id = pending.pop(0)
            owner = owner_by_id.get(ref_id)
            if owner is None or owner in reachable:
                continue
            reachable.add(owner)
            for nested_ref in sorted(_referenced_ids(owner)):
                if nested_ref not in live_refs:
                    live_refs.add(nested_ref)
                    pending.append(nested_ref)

        for definition in list(definitions):
            has_id = any(item.get("id") for item in definition.iter())
            if has_id and definition not in reachable:
                definitions.remove(definition)
                pruned += 1

        if not list(definitions):
            root.remove(definitions)

    return deduplicated, pruned


def _find_extractable(root: ET.Element, min_drawables: int, min_bytes: int) -> list[ET.Element]:
    """Outermost <g> groups whose drawable count clears the threshold (no nesting)."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from extract_svg_assets import (
        DEFINITION_CONTAINERS,
        _has_icon_placeholder,
        _has_semantic_content,
        _has_semantic_object,
        _is_chart_group,
        _is_semantic_owner,
        _large_enough,
        _local,
    )
    found: list[ET.Element] = []

    def walk(elem: ET.Element) -> None:
        if (
            _local(elem.tag) in DEFINITION_CONTAINERS
            or _is_semantic_owner(elem)
        ):
            return
        for child in list(elem):
            if _local(child.tag) != "g":
                walk(child)
                continue
            if (
                not _is_chart_group(child)
                and not _has_semantic_object(child)
                and not _has_semantic_content(child)
                and not _has_icon_placeholder(child)
                and _large_enough(child, min_drawables, min_bytes)
            ):
                found.append(child)  # outermost qualifying — do not descend
            else:
                walk(child)

    walk(root)
    return found


def _find_extractable_runs(
    root: ET.Element,
    min_drawables: int,
    min_bytes: int,
    min_decoration_bytes: int,
) -> list[tuple[ET.Element, list[ET.Element]]]:
    """
    Consecutive pure-vector children inside mixed groups.

    PPT exports often flatten text and large vector decorations as siblings
    under the same parent. Whole-group extraction would hide text, so only the
    contiguous vector runs are factored out.
    """
    # Resolve shared facade state at call time, preserving upstream bindings.
    from extract_svg_assets import (
        DEFINITION_CONTAINERS,
        _drawable_count,
        _has_semantic_content,
        _is_extractable_subtree,
        _is_semantic_owner,
        _local,
        _xml_size,
    )
    found: list[tuple[ET.Element, list[ET.Element]]] = []

    def flush(parent: ET.Element, run: list[ET.Element]) -> None:
        byte_threshold = min_decoration_bytes if _has_semantic_content(parent) else min_bytes
        if run and (
            sum(_drawable_count(child) for child in run) >= min_drawables
            or sum(_xml_size(child) for child in run) >= byte_threshold
        ):
            found.append((parent, list(run)))

    def walk(elem: ET.Element) -> None:
        if (
            _local(elem.tag) in DEFINITION_CONTAINERS
            or _is_semantic_owner(elem)
        ):
            return
        run: list[ET.Element] = []
        for child in list(elem):
            if _is_extractable_subtree(child):
                run.append(child)
                continue
            flush(elem, run)
            run = []
            walk(child)
        flush(elem, run)

    walk(root)
    return found


def _asset_svg(
    group: ET.Element,
    dependencies: list[ET.Element],
    view_box: str | None,
    width: str | None,
    height: str | None,
) -> bytes:
    """Standalone, independently-viewable SVG carrying the group in page coords."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from extract_svg_assets import (
        ASSET_ROLE_ATTRIBUTE,
        DECORATION_ASSET_ROLE,
        SVG_NS,
        XLINK_NS,
        _has_semantic_content,
        _has_semantic_object,
        _is_chart_group,
    )
    semantic_inputs = [group, *dependencies]
    if any(
        _has_semantic_object(item)
        or _has_semantic_content(item)
        or _is_chart_group(item)
        for item in semantic_inputs
    ):
        raise ValueError(
            "Semantic authoring content must remain inline and cannot become "
            "imported decoration assets"
        )
    svg = ET.Element(f"{{{SVG_NS}}}svg")
    svg.set("data-icon-style", "preserve-color")
    svg.set(ASSET_ROLE_ATTRIBUTE, DECORATION_ASSET_ROLE)
    if view_box:
        svg.set("viewBox", view_box)
    if width:
        svg.set("width", width)
    if height:
        svg.set("height", height)
    if dependencies:
        defs = ET.SubElement(svg, f"{{{SVG_NS}}}defs")
        for dependency in dependencies:
            defs.append(dependency)
    svg.append(group)
    normalize_compact_authoring_tree(svg)
    ET.register_namespace("", SVG_NS)
    ET.register_namespace("xlink", XLINK_NS)
    return ET.tostring(svg, encoding="utf-8", xml_declaration=True)


def _source_sha256(
    group: ET.Element,
    dependencies: list[ET.Element],
    view_box: str | None,
    width: str | None,
    height: str | None,
) -> str:
    """Fingerprint an extracted subtree before asset-id namespacing."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from extract_svg_assets import (
        _asset_svg,
    )
    payload = _asset_svg(
        copy.deepcopy(group),
        [copy.deepcopy(dependency) for dependency in dependencies],
        view_box,
        width,
        height,
    )
    return hashlib.sha256(payload).hexdigest()


def _asset_group(nodes: list[ET.Element]) -> ET.Element:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from extract_svg_assets import (
        SVG_NS,
    )
    group = ET.Element(f"{{{SVG_NS}}}g")
    for node in nodes:
        group.append(node)
    return group


def _asset_id(svg_path: Path, index: int, id_prefix: str) -> str:
    prefix = f"{id_prefix}_" if id_prefix else ""
    return f"{prefix}{svg_path.stem}_ill{index:02d}"


def _icon_reference(icon_namespace: str, asset_id: str) -> str:
    return f"{icon_namespace}/{asset_id}" if icon_namespace else asset_id


def _asset_relative_path(icon_namespace: str, asset_id: str) -> str:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from extract_svg_assets import (
        _icon_reference,
    )
    return f"{_icon_reference(icon_namespace, asset_id)}.svg"


def _icon_asset_for_namespace(icon_name: str, icon_namespace: str) -> str | None:
    """Map one local placeholder to its asset path, excluding other libraries."""
    if icon_namespace:
        prefix = f"{icon_namespace}/"
        if not icon_name.startswith(prefix):
            return None
        asset_id = icon_name[len(prefix):]
        if not asset_id or "/" in asset_id:
            return None
        return f"{icon_name}.svg"
    if "/" in icon_name:
        return None
    return f"{icon_name}.svg"


def _has_namespace_placeholder(root: ET.Element, icon_namespace: str) -> bool:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from extract_svg_assets import (
        _icon_asset_for_namespace,
        _local,
    )
    if not icon_namespace:
        return False
    return any(
        _local(elem.tag) == "use"
        and (icon_name := elem.get("data-icon")) is not None
        and _icon_asset_for_namespace(icon_name, icon_namespace) is not None
        for elem in root.iter()
    )


def _generated_asset_re(svg_stems: list[str], id_prefix: str) -> re.Pattern[str] | None:
    if not svg_stems:
        return None
    prefix = f"{re.escape(id_prefix)}_" if id_prefix else ""
    stems = "|".join(re.escape(stem) for stem in svg_stems)
    return re.compile(rf"^{prefix}(?:{stems})_ill\d+\.svg$")


