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
from extract_svg_assets import (
    DEFAULT_MIN_BYTES,
    DEFAULT_MIN_DECORATION_BYTES,
    DEFAULT_MIN_DRAWABLES,
)

def extract_directory(
    svg_dir: Path,
    icons_dir: Path,
    icon_namespace: str,
    *,
    min_drawables: int = DEFAULT_MIN_DRAWABLES,
    min_bytes: int = DEFAULT_MIN_BYTES,
    min_decoration_bytes: int = DEFAULT_MIN_DECORATION_BYTES,
    inplace: bool = False,
    id_prefix: str = "",
    rewritten_dir: Path | None = None,
    inventory_path: Path | None = None,
    reuse_inventory_path: Path | None = None,
    clean_stale: bool = False,
    skip_unparseable: bool = False,
) -> tuple[dict, Path | None, tuple[tuple[Path, str], ...]]:
    """Extract one SVG directory and publish its deterministic inventory.

    Thresholds select vector groups or decoration runs. In-place authoring
    bundles also refresh their model-readable summary. Returns the inventory,
    optional summary path, and any parse failures skipped by the CLI mode.
    """
    # Resolve shared facade state at call time, preserving upstream bindings.
    from extract_svg_assets import (
        DECORATION_ASSET_ROLE,
        ICON_NAMESPACE_RE,
        VECTOR_INVENTORY_SCHEMA,
        _clean_stale_assets,
        _existing_placeholder_entries,
        _load_reusable_assets,
        _referenced_icon_assets,
        _rewritten_path,
        extract_file,
    )
    svg_dir = Path(svg_dir)
    icons_dir = Path(icons_dir)
    if not svg_dir.is_dir():
        raise ValueError(f"svg_dir not found: {svg_dir}")
    if icon_namespace and not ICON_NAMESPACE_RE.fullmatch(icon_namespace):
        raise ValueError(
            "icon_namespace must be one lower-case ASCII directory name "
            "using only letters, digits, '_' or '-'"
        )
    if min(min_drawables, min_bytes, min_decoration_bytes) < 0:
        raise ValueError("vector extraction thresholds must be non-negative")

    icons_dir.mkdir(parents=True, exist_ok=True)
    reusable_assets = (
        _load_reusable_assets(reuse_inventory_path, icons_dir)
        if reuse_inventory_path is not None
        else {}
    )
    inventory_path = (
        Path(inventory_path)
        if inventory_path is not None
        else svg_dir.parent / f"{svg_dir.name}_vector_asset_inventory.json"
    )

    def manifest_path(path: Path | None) -> str | None:
        if path is None:
            return None
        try:
            return path.resolve().relative_to(
                inventory_path.parent.resolve()
            ).as_posix()
        except ValueError:
            return str(path)

    svg_paths = sorted(svg_dir.glob("*.svg"))
    inventory: list[dict] = []
    skipped: list[tuple[Path, str]] = []
    for svg_path in svg_paths:
        try:
            inventory.extend(
                extract_file(
                    svg_path,
                    icons_dir,
                    icon_namespace,
                    min_drawables,
                    min_bytes,
                    min_decoration_bytes,
                    inplace,
                    id_prefix,
                    rewritten_dir,
                    reusable_assets,
                )
            )
        except ET.ParseError as exc:
            if not skip_unparseable:
                raise
            skipped.append((svg_path, str(exc)))

    extracted_count = sum(entry.get("source") == "extracted" for entry in inventory)
    reused_count = sum(
        entry.get("source") == "reused-inventory" for entry in inventory
    )
    known_assets = {str(entry["asset"]) for entry in inventory}
    inventory.extend(
        _existing_placeholder_entries(
            svg_paths,
            icons_dir,
            icon_namespace,
            known_assets,
        )
    )

    stale_removed: list[str] = []
    if clean_stale:
        keep_assets = {str(entry["asset"]) for entry in inventory}
        keep_assets.update(_referenced_icon_assets(svg_paths, icon_namespace))
        stale_removed = _clean_stale_assets(
            icons_dir,
            icon_namespace,
            svg_paths,
            id_prefix,
            keep_assets,
        )

    manifest = {
        "schema": VECTOR_INVENTORY_SCHEMA,
        "svg_dir": manifest_path(svg_dir),
        "icons_dir": manifest_path(icons_dir),
        "icon_namespace": icon_namespace or None,
        "asset_role": DECORATION_ASSET_ROLE,
        "rewritten_dir": (
            None
            if inplace
            else manifest_path(
                _rewritten_path(
                    svg_dir / "_sample.svg",
                    rewritten_dir,
                    False,
                ).parent
            )
        ),
        "reuse_inventory": manifest_path(reuse_inventory_path),
        "min_drawables": min_drawables,
        "min_bytes": min_bytes,
        "min_decoration_bytes": min_decoration_bytes,
        "extracted_count": extracted_count,
        "reused_count": reused_count,
        "asset_count": len(inventory),
        "stale_removed": stale_removed,
        "assets": inventory,
    }
    inventory_path.parent.mkdir(parents=True, exist_ok=True)
    inventory_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    summary_path = (
        write_authoring_summary(svg_dir)
        if inplace and (svg_dir / AUTHORING_MANIFEST_NAME).is_file()
        else None
    )
    return manifest, summary_path, tuple(skipped)


