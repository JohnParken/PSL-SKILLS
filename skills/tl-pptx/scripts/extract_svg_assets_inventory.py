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

def _clean_stale_assets(
    icons_dir: Path,
    icon_namespace: str,
    svg_paths: list[Path],
    id_prefix: str,
    keep_assets: set[str],
) -> list[str]:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from extract_svg_assets import (
        _generated_asset_re,
    )
    pattern = _generated_asset_re([path.stem for path in svg_paths], id_prefix)
    if pattern is None:
        return []

    removed: list[str] = []
    asset_dir = icons_dir / icon_namespace if icon_namespace else icons_dir
    for asset_path in sorted(asset_dir.glob("*.svg")):
        relative_asset = asset_path.relative_to(icons_dir).as_posix()
        if relative_asset in keep_assets or not pattern.match(asset_path.name):
            continue
        asset_path.unlink()
        removed.append(relative_asset)
    return removed


def _referenced_icon_assets(svg_paths: list[Path], icon_namespace: str) -> set[str]:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from extract_svg_assets import (
        _icon_asset_for_namespace,
        _local,
    )
    assets: set[str] = set()
    for svg_path in svg_paths:
        try:
            root = ET.parse(svg_path).getroot()
        except ET.ParseError:
            continue
        for elem in root.iter():
            if _local(elem.tag) != "use":
                continue
            icon_name = elem.get("data-icon")
            if icon_name and (asset := _icon_asset_for_namespace(icon_name, icon_namespace)):
                assets.add(asset)
    return assets


def _existing_placeholder_entries(
    svg_paths: list[Path],
    icons_dir: Path,
    icon_namespace: str,
    known_assets: set[str],
) -> list[dict]:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from extract_svg_assets import (
        ASSET_ROLE_ATTRIBUTE,
        DECORATION_ASSET_ROLE,
        _drawable_count,
        _icon_asset_for_namespace,
        _local,
        _source_references,
        _tag_histogram,
        _xml_size,
    )
    by_asset: dict[str, dict] = {}
    for svg_path in svg_paths:
        try:
            root = ET.parse(svg_path).getroot()
        except ET.ParseError:
            continue

        for elem in root.iter():
            if _local(elem.tag) != "use":
                continue
            icon_name = elem.get("data-icon")
            if not icon_name:
                continue

            asset = _icon_asset_for_namespace(icon_name, icon_namespace)
            if asset is None:
                continue
            if elem.get(ASSET_ROLE_ATTRIBUTE) != DECORATION_ASSET_ROLE:
                raise ValueError(
                    f"Imported vector placeholder {icon_name!r} must declare "
                    f"{ASSET_ROLE_ATTRIBUTE}={DECORATION_ASSET_ROLE!r}"
                )
            if asset in known_assets:
                continue

            entry = by_asset.setdefault(
                asset,
                {
                    "svg": svg_path.name,
                    "svgs": [],
                    "id": icon_name,
                    "icon": icon_name,
                    "asset": asset,
                    "source": "existing-placeholder",
                    "role": DECORATION_ASSET_ROLE,
                    "asset_exists": (icons_dir / asset).exists(),
                },
            )
            if svg_path.name not in entry["svgs"]:
                entry["svgs"].append(svg_path.name)

    entries: list[dict] = []
    for asset, entry in sorted(by_asset.items()):
        asset_path = icons_dir / asset
        if asset_path.exists():
            entry["asset_sha256"] = hashlib.sha256(
                asset_path.read_bytes()
            ).hexdigest()
            try:
                root = ET.parse(asset_path).getroot()
            except ET.ParseError as exc:
                raise ValueError(
                    f"Imported vector asset is invalid SVG XML: "
                    f"{asset_path}: {exc}"
                ) from exc
            else:
                role = root.get(ASSET_ROLE_ATTRIBUTE)
                if role != DECORATION_ASSET_ROLE:
                    raise ValueError(
                        f"Imported vector asset must declare "
                        f"{ASSET_ROLE_ATTRIBUTE}={DECORATION_ASSET_ROLE!r}: "
                        f"{asset_path}"
                    )
                entry["drawable_count"] = _drawable_count(root)
                entry["byte_count"] = _xml_size(root)
                entry["elements"] = _tag_histogram(root)
                entry["source_refs"] = _source_references(root)
                entry["dependencies"] = sorted(
                    elem_id
                    for elem in root.iter()
                    if _local(elem.tag) == "defs"
                    for child in elem
                    if (elem_id := child.get("id"))
                )
        entries.append(entry)
    return entries


def _load_reusable_assets(inventory_path: Path, icons_dir: Path) -> dict[str, dict]:
    """Load fingerprinted assets from an earlier extraction inventory."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from extract_svg_assets import (
        ASSET_ROLE_ATTRIBUTE,
        DECORATION_ASSET_ROLE,
        VECTOR_INVENTORY_SCHEMA,
    )
    try:
        payload = json.loads(inventory_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(f"reuse inventory not found: {inventory_path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid reuse inventory JSON: {inventory_path}: {exc}") from exc

    if payload.get("schema") != VECTOR_INVENTORY_SCHEMA:
        raise ValueError(
            f"unsupported reuse inventory schema: {payload.get('schema')!r}"
        )
    if payload.get("asset_role") != DECORATION_ASSET_ROLE:
        raise ValueError(
            "reuse inventory must declare asset_role='decoration'"
        )
    entries = payload.get("assets")
    if not isinstance(entries, list):
        raise ValueError(f"reuse inventory has no assets list: {inventory_path}")

    reusable: dict[str, dict] = {}
    fingerprinted = 0
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        source_sha256 = entry.get("source_sha256")
        asset_sha256 = entry.get("asset_sha256")
        asset = entry.get("asset")
        icon = entry.get("icon")
        role = entry.get("role")
        if not all(
            isinstance(value, str) and value
            for value in (source_sha256, asset_sha256, asset, icon)
        ):
            continue
        if role != DECORATION_ASSET_ROLE:
            raise ValueError(
                f"reusable asset {icon!r} is not a decoration asset"
            )
        fingerprinted += 1
        asset_path = icons_dir / asset
        if not asset_path.is_file():
            raise ValueError(
                f"reusable asset is missing from the target icons directory: {asset_path}"
            )
        actual_asset_sha256 = hashlib.sha256(asset_path.read_bytes()).hexdigest()
        if actual_asset_sha256 != asset_sha256:
            raise ValueError(
                f"reusable asset hash does not match its inventory: {asset_path}"
            )
        try:
            asset_root = ET.parse(asset_path).getroot()
        except ET.ParseError as exc:
            raise ValueError(
                f"reusable asset is not valid SVG XML: {asset_path}: {exc}"
            ) from exc
        if asset_root.get(ASSET_ROLE_ATTRIBUTE) != DECORATION_ASSET_ROLE:
            raise ValueError(
                f"reusable asset lacks its decoration role: {asset_path}"
            )
        current = reusable.get(source_sha256)
        if current is None or asset < str(current["asset"]):
            reusable[source_sha256] = entry

    extracted_count = payload.get("extracted_count", 0)
    if isinstance(extracted_count, int) and extracted_count > fingerprinted:
        raise ValueError(
            "reuse inventory predates source fingerprints; rerun the source extraction "
            f"with the current tool: {inventory_path}"
        )
    return reusable


def _rewritten_path(svg_path: Path, rewritten_dir: Path | None, inplace: bool) -> Path:
    if inplace:
        return svg_path
    if rewritten_dir is None:
        return svg_path.parent.parent / f"{svg_path.parent.name}-rewritten" / svg_path.name
    return rewritten_dir / svg_path.name


def _fresh_native_fallback_markers(root: ET.Element) -> set[ET.Element]:
    """Snapshot native markers whose visible fallback is fresh before extraction."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from extract_svg_assets import (
        _SHA256_RE,
    )
    fresh: set[ET.Element] = set()
    for element in root.iter():
        expected = element.get(NATIVE_FALLBACK_SHA256_ATTR)
        if (
            expected is None
            or expected != expected.strip()
            or not _SHA256_RE.fullmatch(expected)
        ):
            continue
        if svg_native_fallback_fingerprint(
            element,
            document_root=root,
        ) == expected.lower():
            fresh.add(element)
    return fresh


def _normalize_authoring_tree(
    root: ET.Element,
    fresh_native_markers: set[ET.Element],
) -> None:
    """Normalize first, then hash the final visible native fallbacks."""
    normalize_compact_authoring_tree(root)
    live_elements = set(root.iter())
    for marker in fresh_native_markers & live_elements:
        marker.set(
            NATIVE_FALLBACK_SHA256_ATTR,
            svg_native_fallback_fingerprint(marker, document_root=root),
        )


