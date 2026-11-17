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

def main(argv: Optional[list[str]] = None) -> int:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from extract_svg_assets import (
        build_parser,
        extract_directory,
    )
    args = build_parser().parse_args(argv)
    svg_dir = Path(args.svg_dir)
    icons_dir = Path(args.icons_dir) if args.icons_dir else svg_dir.parent / "icons"
    icon_namespace = args.icon_namespace.strip()
    reuse_inventory_path = Path(args.reuse_inventory) if args.reuse_inventory else None
    rewritten_dir = Path(args.rewritten_dir) if args.rewritten_dir else None
    inventory_path = (
        Path(args.inventory)
        if args.inventory
        else svg_dir.parent / f"{svg_dir.name}_vector_asset_inventory.json"
    )
    try:
        manifest, summary_path, skipped = extract_directory(
            svg_dir,
            icons_dir,
            icon_namespace,
            min_drawables=args.min_drawables,
            min_bytes=args.min_bytes,
            min_decoration_bytes=args.min_decoration_bytes,
            inplace=args.inplace,
            id_prefix=args.id_prefix,
            rewritten_dir=rewritten_dir,
            inventory_path=inventory_path,
            reuse_inventory_path=reuse_inventory_path,
            clean_stale=args.clean_stale,
            skip_unparseable=True,
        )
    except (OSError, ValueError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    for svg_path, reason in skipped:
        print(f"[WARN] skip unparseable {svg_path.name}: {reason}", file=sys.stderr)
    extracted_count = int(manifest["extracted_count"])
    reused_count = int(manifest["reused_count"])
    inventory = list(manifest["assets"])
    stale_removed = list(manifest["stale_removed"])
    print(
        f"[OK] extracted {extracted_count} new asset(s), reused {reused_count} asset(s), "
        f"inventoried {len(inventory)} asset reference(s) -> "
        f"{icons_dir / icon_namespace if icon_namespace else icons_dir}",
        file=sys.stderr,
    )
    if stale_removed:
        print(f"[OK] removed {len(stale_removed)} stale generated asset(s)", file=sys.stderr)
    if summary_path is not None:
        print(f"[OK] refreshed model-readable summary: {summary_path}", file=sys.stderr)
    return 0


