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


SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
DRAWABLE = {"path", "polygon", "polyline", "rect", "circle", "ellipse", "line"}
SEMANTIC_CONTENT = {"text", "tspan", "foreignObject"}
SEMANTIC_MARKER_ATTRIBUTES = {
    "data-pptx-carrier",
    "data-pptx-inline-formula",
    "data-pptx-layer",
    "data-pptx-page-role",
    "data-pptx-placeholder",
    "data-pptx-replace-with",
    "data-pptx-role",
    "data-pptx-shape-hyperlink",
    SEMANTIC_OBJECT_ATTRIBUTE,
}
DEFINITION_CONTAINERS = {"defs"}
DEFAULT_MIN_DRAWABLES = 20
DEFAULT_MIN_BYTES = 3000
DEFAULT_MIN_DECORATION_BYTES = 3000
SOURCE_REF_ATTRIBUTE = "data-pptx-source-ref"
ASSET_ROLE_ATTRIBUTE = "data-pptx-asset-role"
DECORATION_ASSET_ROLE = "decoration"
VECTOR_INVENTORY_SCHEMA = "vector_asset_inventory.v2"
ICON_NAMESPACE_RE = re.compile(r"^[a-z0-9](?:[a-z0-9_-]*[a-z0-9])?$")
URL_REF_RE = re.compile(r"url\(\s*(['\"]?)#([^)'\"]\S*?)\1\s*\)")
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")



# Stable upstream API facade, split by conversion responsibility.
from extract_svg_assets_references import (
    _local,
    _drawable_count,
    _xml_size,
    _large_enough,
    _has_semantic_content,
    _has_semantic_object,
    _is_semantic_owner,
    _is_existing_placeholder,
    _has_icon_placeholder,
    _is_extractable_subtree,
    _is_chart_group,
    _tag_histogram,
    _source_references,
    _is_descendant,
    _id_index,
    _referenced_ids,
    _dependency_elements,
    _collect_id_mapping,
    _rewrite_references,
    _rebase_external_hrefs,
    _rewrite_reference_values,
    _definition_signature,
)
from extract_svg_assets_geometry import (
    _optimize_definitions,
    _find_extractable,
    _find_extractable_runs,
    _asset_svg,
    _source_sha256,
    _asset_group,
    _asset_id,
    _icon_reference,
    _asset_relative_path,
    _icon_asset_for_namespace,
    _has_namespace_placeholder,
    _generated_asset_re,
)
from extract_svg_assets_inventory import (
    _clean_stale_assets,
    _referenced_icon_assets,
    _existing_placeholder_entries,
    _load_reusable_assets,
    _rewritten_path,
    _fresh_native_fallback_markers,
    _normalize_authoring_tree,
)
from extract_svg_assets_file import (
    extract_file,
)
from extract_svg_assets_arguments import (
    build_parser,
)
from extract_svg_assets_directory import (
    extract_directory,
)
from extract_svg_assets_cli import (
    main,
)

