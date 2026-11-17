#!/usr/bin/env python3
"""
PPT Master - Flat Authoring Round-trip Materializer

Materialize editable ``authoring-svg-flat/`` pages back into layered slide SVG
inputs for the existing source-preserving SVG-to-PPTX exporter. Unchanged
source objects recover their original round-trip metadata; edited objects stay
as authored SVG and are converted normally.

Usage:
    Imported by svg_to_pptx.py for ``--roundtrip``.

Dependencies:
    None (standard library and sibling PPT Master modules only).
"""

from __future__ import annotations

import base64
import binascii
import copy
import hashlib
import json
import math
import os
import re
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit, urlunsplit
from xml.etree import ElementTree as ET

from extract_svg_assets import (
    ASSET_ROLE_ATTRIBUTE,
    DECORATION_ASSET_ROLE,
    VECTOR_INVENTORY_SCHEMA,
    extract_file,
)
from hyperlink_contract import ADOPTED_SOURCE_LINK_ATTR, SHAPE_HYPERLINK_ATTR, SOURCE_HREF_ATTR
from pptx_workspace import (
    AUTHORING_SVG_FLAT_DIR,
    ROUNDTRIP_MANIFEST_PATH,
    ROUNDTRIP_PAGE_PLAN_PATH,
)
from pptx_shapes import svg_text_fingerprint
from slide_roster import discover_slide_svgs
from svg_authoring_view import (
    AUTHORING_OMITTED_SOURCE_ATTRIBUTES,
    AUTHORING_MANIFEST_NAME,
    AUTHORING_SCHEMA,
    EXTERNAL_LINKED_IMAGE_PROXY_ATTRIBUTE,
    SOURCE_REF_ATTRIBUTE,
    SOURCE_PROXY_ATTRIBUTE,
    SOURCE_PROXY_KIND,
    _element_chain,
    _format_transform_number,
    _materialize_adopted_context,
    project_svg_batch,
    semantic_subtree_sha256,
)
from svg_compatibility import normalize_single_child_group_filters
from svg_to_pptx.drawingml.paths import parse_svg_points
from svg_to_pptx.drawingml.utils import INHERITABLE_ATTRS, parse_transform_matrix
from svg_to_pptx.native_objects.chart_patch import chart_edit_is_data_only, patch_chart_package
from svg_to_pptx.native_objects.marker_common import _load_payload


SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
IMPORTED_ICON_NAMESPACE = "imported"
ROUNDTRIP_PAGE_PLAN_SCHEMA = "ppt-master.roundtrip-page-plan.v1"
_URL_ID_RE = re.compile(r"url\(\s*(['\"]?)#([^)'\"\s]+)\1\s*\)")
_PRESERVED_EFFECT_ATTRIBUTES = frozenset({
    "data-pptx-effect-ooxml",
    "data-pptx-effect-ooxml-sha256",
    "data-pptx-effect-reason",
    "data-pptx-effect-status",
})
_ROOT_AUTHORING_ATTRIBUTES = (
    "width",
    "height",
    "viewBox",
    "style",
    *INHERITABLE_ATTRS,
    "opacity",
    "transform",
)

ET.register_namespace("", SVG_NS)
ET.register_namespace("xlink", XLINK_NS)


class AuthoringRoundtripError(RuntimeError):
    """Reject stale, ambiguous, or incomplete authoring round-trip input."""


@dataclass(frozen=True)
class SourceRefRecord:
    source_path: tuple[int, ...]
    initial_authoring_subtree_sha256: str
    representation: str
    proxy_asset: Path | None = None
    proxy_asset_sha256: str | None = None


@dataclass(frozen=True)
class AuthoringDocument:
    name: str
    source_name: str
    source_slide: int
    authoring_path: Path
    flat_source_path: Path
    layered_source_path: Path
    initial_authoring_sha256: str
    source_refs: dict[str, SourceRefRecord]


@dataclass(frozen=True)
class VectorAssetRecord:
    icon: str
    asset_path: Path
    origin_document: str
    source_sha256: str
    expected_asset_sha256: str
    actual_asset_sha256: str

    @property
    def element_prefix(self) -> str:
        return self.icon.rsplit("/", 1)[-1]

    @property
    def baseline_key(self) -> str:
        stem = self.element_prefix
        match = re.search(r"(slide_\d+_ill\d+)$", stem)
        slot = match.group(1) if match else self.source_sha256
        return f"{self.origin_document}:{slot}"

    @property
    def unchanged(self) -> bool:
        return self.actual_asset_sha256 == self.expected_asset_sha256


@dataclass(frozen=True)
class RefOccurrence:
    element: ET.Element
    asset: VectorAssetRecord | None
    root: ET.Element


@dataclass(frozen=True)
class AuthoringRoundtripResult:
    svg_files: tuple[Path, ...]
    authoring_files: tuple[Path, ...]
    pages: tuple["RoundtripPage", ...]
    page_plan_present: bool
    report: dict[str, Any]


@dataclass(frozen=True)
class RoundtripPage:
    """One output page resolved from the optional deck-level plan."""

    output_index: int
    source_slide: int
    svg_name: str
    source_svg_name: str

    @property
    def svg_stem(self) -> str:
        return Path(self.svg_name).stem



# Stable upstream API facade, split by conversion responsibility.
from .identity import (
    roundtrip_source_fingerprint,
    _local_name,
    _parse_svg,
    _load_json,
    _resolve_inside,
    _sha256_file,
    _source_element,
    _source_identity,
    is_flat_authoring_bundle,
    _roundtrip_source_svg_names,
)
from .documents import (
    _load_documents,
    _identity_pages,
)
from .plans import (
    _load_page_plan,
    _output_documents,
    _load_current_assets,
    _baseline_assets,
    _generate_baseline_bundle,
)
from .fingerprints import (
    _collapse_hash_only_groups,
    normalized_authoring_subtree_sha256,
    _contextual_authoring_element,
    _source_jump_links,
    preserve_adopted_source_links,
    authoring_source_ref_is_unchanged,
    _referenced_assets,
    _ref_occurrences,
    _outer_source_ref_elements,
)
from .definitions import (
    _layered_ref_index,
    _direct_defs,
    _definition_changes,
    _referenced_definition_ids,
    _definition_reference_ids,
    _prune_unreferenced_definitions,
    _merge_defs,
    _serialize_svg,
)
from .restore import (
    _rebase_resource_references,
    _restore_preserved_effect_metadata,
    _text_body_frame,
    _text_body_geometry,
    _text_body_state,
    _text_body_formula_loss_warning,
    _restore_unchanged_text_body,
    _apply_authoring_root_attributes,
    _native_restore_placeholder,
    prepare_imported_chart_edit,
    chart_rebuild_warning,
)
from .materialize import (
    _materialize_document,
)
from .entry import (
    materialize_flat_authoring_roundtrip,
)

