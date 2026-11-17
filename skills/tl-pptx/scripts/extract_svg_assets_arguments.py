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

def build_parser() -> argparse.ArgumentParser:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from extract_svg_assets import (
        DEFAULT_MIN_BYTES,
        DEFAULT_MIN_DECORATION_BYTES,
        DEFAULT_MIN_DRAWABLES,
    )
    parser = argparse.ArgumentParser(
        description=(
            "Factor large non-semantic SVG decorations into reusable assets."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("svg_dir", help="Directory of working SVGs (e.g. import_ws/svg or project/svg_output)")
    parser.add_argument("-o", "--output", dest="icons_dir", help="Project icon dir (default: <svg_dir>/../icons)")
    parser.add_argument("--icons-dir", dest="icons_dir", help="Project icon dir (default: <svg_dir>/../icons)")
    parser.add_argument(
        "--icon-namespace",
        default="",
        help=(
            "Optional lower-case subdirectory and data-icon prefix for extracted "
            "assets (create-template uses: imported)"
        ),
    )
    parser.add_argument(
        "--rewritten-dir",
        help="Directory for rewritten SVGs when not using --inplace (default: <svg_dir>/../<svg_dir-name>-rewritten)",
    )
    parser.add_argument(
        "--inventory",
        help="Inventory JSON path (default: <svg_dir>/../<svg_dir-name>_vector_asset_inventory.json)",
    )
    parser.add_argument(
        "--reuse-inventory",
        help=(
            "Reuse fingerprint-matched assets from an earlier extraction inventory; "
            "only unmatched vector subtrees create new assets"
        ),
    )
    parser.add_argument(
        "--id-prefix",
        default="",
        help=(
            "Optional prefix for generated asset IDs, useful when processing "
            "layered and flat SVG dirs into one icons dir"
        ),
    )
    parser.add_argument(
        "--min-drawables", type=int, default=DEFAULT_MIN_DRAWABLES,
        help=f"Min drawable elements for a group to be extracted (default: {DEFAULT_MIN_DRAWABLES})",
    )
    parser.add_argument(
        "--min-bytes", type=int, default=DEFAULT_MIN_BYTES,
        help=f"Min XML bytes for a pure-vector group/run to be extracted (default: {DEFAULT_MIN_BYTES})",
    )
    parser.add_argument(
        "--min-decoration-bytes", type=int, default=DEFAULT_MIN_DECORATION_BYTES,
        help=(
            "Min XML bytes for pure-vector decoration runs inside text-bearing groups "
            f"(default: {DEFAULT_MIN_DECORATION_BYTES})"
        ),
    )
    parser.add_argument(
        "--inplace", action="store_true",
        help="Rewrite the source SVGs in place instead of writing to --rewritten-dir",
    )
    parser.add_argument(
        "--clean-stale",
        action="store_true",
        help=(
            "Remove stale generated assets for the current svg filenames/id prefix "
            "that are not referenced by this run's inventory"
        ),
    )
    return parser


