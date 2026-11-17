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

def extract_file(
    svg_path: Path,
    icons_dir: Path,
    icon_namespace: str,
    min_drawables: int,
    min_bytes: int,
    min_decoration_bytes: int,
    inplace: bool,
    id_prefix: str = "",
    rewritten_dir: Path | None = None,
    reusable_assets: dict[str, dict] | None = None,
) -> list[dict]:
    """Extract qualifying groups from one SVG. Returns inventory entries."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from extract_svg_assets import (
        ASSET_ROLE_ATTRIBUTE,
        DECORATION_ASSET_ROLE,
        SVG_NS,
        XLINK_NS,
        _asset_group,
        _asset_id,
        _asset_relative_path,
        _asset_svg,
        _collect_id_mapping,
        _dependency_elements,
        _drawable_count,
        _find_extractable,
        _find_extractable_runs,
        _fresh_native_fallback_markers,
        _has_namespace_placeholder,
        _icon_reference,
        _local,
        _normalize_authoring_tree,
        _optimize_definitions,
        _rebase_external_hrefs,
        _rewrite_references,
        _rewritten_path,
        _source_references,
        _source_sha256,
        _tag_histogram,
        _xml_size,
    )
    ET.register_namespace("", SVG_NS)
    tree = ET.parse(svg_path)
    root = tree.getroot()
    fresh_native_markers = _fresh_native_fallback_markers(root)
    view_box = root.get("viewBox")
    width = root.get("width")
    height = root.get("height")
    definitions_changed = any(_optimize_definitions(root))

    # A namespaced projection is an all-at-once readability pass. Once it owns
    # an asset reference, reruns inventory the existing placeholders instead of
    # progressively factoring their remaining parent/sibling geometry.
    if _has_namespace_placeholder(root, icon_namespace):
        if definitions_changed or not inplace:
            _normalize_authoring_tree(root, fresh_native_markers)
            rewritten = _rewritten_path(svg_path, rewritten_dir, inplace)
            rewritten.parent.mkdir(parents=True, exist_ok=True)
            ET.register_namespace("", SVG_NS)
            ET.register_namespace("xlink", XLINK_NS)
            tree.write(rewritten, encoding="utf-8", xml_declaration=True)
        return []

    targets: list[tuple[ET.Element, list[ET.Element]]] = []
    parents = {child: parent for parent in root.iter() for child in parent}
    group_targets = _find_extractable(root, min_drawables, min_bytes)
    selected_groups = set(group_targets)

    def inside_selected_group(elem: ET.Element) -> bool:
        current = elem
        while current in parents:
            current = parents[current]
            if current in selected_groups:
                return True
        return False

    for group in group_targets:
        parent = parents.get(group)
        if parent is not None:
            targets.append((parent, [group]))

    for parent, run in _find_extractable_runs(root, min_drawables, min_bytes, min_decoration_bytes):
        if (
            parent not in selected_groups
            and not inside_selected_group(parent)
            and not any(child in selected_groups for child in run)
            and all(child in parent for child in run)
        ):
            targets.append((parent, run))

    if not targets:
        if definitions_changed or not inplace:
            _normalize_authoring_tree(root, fresh_native_markers)
            rewritten = _rewritten_path(svg_path, rewritten_dir, inplace)
            rewritten.parent.mkdir(parents=True, exist_ok=True)
            ET.register_namespace("", SVG_NS)
            ET.register_namespace("xlink", XLINK_NS)
            tree.write(rewritten, encoding="utf-8", xml_declaration=True)
        return []

    asset_dir = icons_dir / icon_namespace if icon_namespace else icons_dir
    asset_dir.mkdir(parents=True, exist_ok=True)
    entries = []

    for index, (parent, nodes) in enumerate(targets, start=1):
        if not nodes or not all(node in parent for node in nodes):
            continue
        asset_id = _asset_id(svg_path, index, id_prefix)
        icon_reference = _icon_reference(icon_namespace, asset_id)
        asset = _asset_relative_path(icon_namespace, asset_id)
        pos = list(parent).index(nodes[0])
        group = nodes[0] if len(nodes) == 1 and _local(nodes[0].tag) == "g" else _asset_group(nodes)

        dependencies = [copy.deepcopy(elem) for elem in _dependency_elements(root, group)]
        dependency_source_ids = sorted({
            elem_id
            for dependency in dependencies
            for elem in dependency.iter()
            if (elem_id := elem.get("id"))
        })
        source_sha256 = _source_sha256(group, dependencies, view_box, width, height)
        source_refs = _source_references(group)
        reusable = (reusable_assets or {}).get(source_sha256)
        if reusable is not None:
            reused_icon = str(reusable["icon"])
            reused_asset = str(reusable["asset"])
            placeholder = ET.Element(f"{{{SVG_NS}}}use")
            placeholder.set("data-icon", reused_icon)
            placeholder.set(ASSET_ROLE_ATTRIBUTE, DECORATION_ASSET_ROLE)
            for node in nodes:
                if node in parent:
                    parent.remove(node)
            parent.insert(pos, placeholder)
            entries.append({
                "svg": svg_path.name,
                "id": reused_icon,
                "icon": reused_icon,
                "asset": reused_asset,
                "source": "reused-inventory",
                "role": DECORATION_ASSET_ROLE,
                "source_sha256": source_sha256,
                "asset_sha256": reusable["asset_sha256"],
                "reused_from_svg": reusable.get("svg"),
                "drawable_count": _drawable_count(group),
                "byte_count": _xml_size(group),
                "source_refs": source_refs,
                "dependencies": dependency_source_ids,
                "elements": _tag_histogram(group),
            })
            continue

        id_mapping = _collect_id_mapping(asset_id, group, dependencies)
        _rewrite_references(group, id_mapping)
        for dependency in dependencies:
            _rewrite_references(dependency, id_mapping)

        # Asset keeps the group in original page coordinates and carries its defs.
        asset_path = icons_dir / asset
        external_hrefs_rewritten = _rebase_external_hrefs(
            group,
            svg_path.parent,
            asset_path.parent,
        )
        for dependency in dependencies:
            external_hrefs_rewritten += _rebase_external_hrefs(
                dependency,
                svg_path.parent,
                asset_path.parent,
            )
        asset_bytes = _asset_svg(group, dependencies, view_box, width, height)
        asset_path.write_bytes(asset_bytes)

        placeholder = ET.Element(f"{{{SVG_NS}}}use")
        placeholder.set("data-icon", icon_reference)
        placeholder.set(ASSET_ROLE_ATTRIBUTE, DECORATION_ASSET_ROLE)
        for node in nodes:
            if node in parent:
                parent.remove(node)
        parent.insert(pos, placeholder)

        entries.append({
            "svg": svg_path.name,
            "id": icon_reference,
            "icon": icon_reference,
            "asset": asset,
            "source": "extracted",
            "role": DECORATION_ASSET_ROLE,
            "source_sha256": source_sha256,
            "asset_sha256": hashlib.sha256(asset_bytes).hexdigest(),
            "drawable_count": _drawable_count(group),
            "byte_count": _xml_size(group),
            "source_refs": source_refs,
            "dependencies": [id_mapping.get(elem_id, elem_id) for elem_id in dependency_source_ids],
            "external_hrefs_rewritten": external_hrefs_rewritten,
            "elements": _tag_histogram(group),
        })

    _optimize_definitions(root)
    _normalize_authoring_tree(root, fresh_native_markers)
    rewritten = _rewritten_path(svg_path, rewritten_dir, inplace)
    rewritten.parent.mkdir(parents=True, exist_ok=True)
    ET.register_namespace("", SVG_NS)
    ET.register_namespace("xlink", XLINK_NS)
    tree.write(rewritten, encoding="utf-8", xml_declaration=True)
    return entries


