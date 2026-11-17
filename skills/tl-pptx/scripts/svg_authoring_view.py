#!/usr/bin/env python3
"""
PPT Master - SVG Authoring View

Create a lightweight, non-destructive editable IR from PPTX-imported SVG
files. The source SVG remains the native-payload authority; the authoring copy
translates recognized shapes and tables into compact semantic components,
keeps stable source references, and omits bulky import-only/render-duplicate
payloads.

Usage:
    python3 scripts/svg_authoring_view.py <svg-file-or-directory> \
        -o <output-dir> --projection-kind <kind>

Examples:
    python3 scripts/svg_authoring_view.py analysis/source_svg_import/svg \
        -o analysis/authoring-svg --projection-kind layered
    python3 scripts/svg_authoring_view.py imported/slide_06.svg \
        -o /tmp/slide-authoring-view --projection-kind generic

Dependencies:
    None (standard library only).

The output directory is an authoring bundle: editable SVGs plus one
model-readable `authoring_summary.json` and one tool-only
`authoring_manifest.json` provenance sidecar. Layered IR remains the
template-creation input; final templates are materialized from it. A flat
authoring bundle at ``authoring-svg-flat/`` is the sole editable source for
``svg_to_pptx.py --roundtrip``. Directory runs prepare and stage the
complete batch before publishing it, so a failed page leaves the existing
destination set unchanged.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import re
import shutil
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit, urlunsplit
from xml.etree import ElementTree as ET

from compact_svg_coordinates import compact_svg_tree
from console_encoding import configure_utf8_stdio
from hyperlink_contract import ADOPTED_SOURCE_LINK_ATTR, SOURCE_HREF_ATTR
from native_payloads import (
    CUSTOM_GEOMETRY_ATTRIBUTE,
    CUSTOM_GEOMETRY_REF_ATTRIBUTE,
    NATIVE_RECORD_REF_ATTRIBUTE,
    SHAPE_STYLE_ATTRIBUTE,
    SHAPE_STYLE_REF_ATTRIBUTE,
    TXBODY_REF_ATTRIBUTE,
)
from pptx_shapes import (
    NATIVE_FALLBACK_SHA256_ATTR,
    svg_native_fallback_fingerprint,
)
from pptx_gradients import (
    NATIVE_GRADIENT_ATTR,
    NATIVE_GRADIENT_PREVIEW_SHA256_ATTR,
    NATIVE_GRADIENT_SHA256_ATTR,
)
from svg_compatibility import normalize_single_child_group_filters
from svg_authoring_contract import (
    canonical_authoring_errors,
    normalize_compact_authoring_tree,
)
from svg_to_pptx.drawingml.context import ConvertContext, IDENTITY_MATRIX
from svg_to_pptx.drawingml.utils import (
    INHERITABLE_ATTRS,
    is_stroked_zero_axis_shape,
    matrix_multiply,
    parse_inline_style,
    parse_opacity,
    parse_transform_matrix,
    project_definition_index,
    project_gradient_errors,
)


SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
AUTHORING_MANIFEST_NAME = "authoring_manifest.json"
AUTHORING_SUMMARY_NAME = "authoring_summary.json"
AUTHORING_SCHEMA = "ppt-master.svg-authoring-ir.v1"
AUTHORING_SUMMARY_SCHEMA = "ppt-master.svg-authoring-summary.v1"
SOURCE_REF_ATTRIBUTE = "data-pptx-source-ref"
SOURCE_PROXY_ATTRIBUTE = "data-pptx-source-proxy"
SOURCE_PROXY_KIND = "native-restore"
EXTERNAL_LINKED_IMAGE_PROXY_ATTRIBUTE = (
    "data-pptx-external-linked-image-proxy"
)
SEMANTIC_OBJECT_ATTRIBUTE = "data-pptx-semantic-object"
SEMANTIC_SHAPE_KIND = "shape"
SEMANTIC_TABLE_KIND = "table"
# Keep ordinary text and vectors inline. Large semantic/native payload objects
# that cannot move to an editable vector asset become atomic source proxies.
SOURCE_PROXY_MIN_BYTES = 4096
_SEMANTIC_CONTENT_TAGS = frozenset({"foreignObject", "text", "tspan"})
_SEMANTIC_AUTHORING_ATTRIBUTES = frozenset({
    "data-pptx-inline-formula",
    "data-pptx-page-role",
    "data-pptx-placeholder",
    "data-pptx-replace-with",
    "data-pptx-role",
    "data-pptx-shape-hyperlink",
    SEMANTIC_OBJECT_ATTRIBUTE,
})
_DRAWABLE_TAGS = frozenset({
    "circle",
    "ellipse",
    "line",
    "path",
    "polygon",
    "polyline",
    "rect",
})
_SEMANTIC_CARRIER_OWNER_ATTRIBUTES = frozenset({
    "data-pptx-frame",
    "data-pptx-geometry-kind",
    "data-pptx-object",
    "data-pptx-prst",
})
_URL_ID_RE = re.compile(r"url\(\s*(['\"]?)#([^)'\"\s]+)\1\s*\)")
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")

ET.register_namespace("", SVG_NS)
ET.register_namespace("xlink", XLINK_NS)


AUTHORING_OMITTED_SOURCE_ATTRIBUTES = {
    "data-name",
    "data-pptx-effect-ooxml",
    "data-pptx-effect-ooxml-sha256",
    "data-pptx-effect-reason",
    "data-pptx-effect-status",
    "data-pptx-gradient-ooxml",
    "data-pptx-gradient-ooxml-sha256",
    "data-pptx-gradient-preview-sha256",
    "data-pptx-custgeom",
    "data-pptx-geometry-sha256",
    "data-pptx-preview-sha256",
    NATIVE_FALLBACK_SHA256_ATTR,
    "data-pptx-roundtrip-object",
    "data-pptx-shape-id",
    "data-pptx-shape-name",
    "data-pptx-shape-scope",
    "data-pptx-shape-style",
}

ADOPT_OBJECT_STRIPPED_ATTRIBUTES = frozenset(
    AUTHORING_OMITTED_SOURCE_ATTRIBUTES
    | {
        SOURCE_REF_ATTRIBUTE,
        SOURCE_PROXY_ATTRIBUTE,
        NATIVE_RECORD_REF_ATTRIBUTE,
        TXBODY_REF_ATTRIBUTE,
        SHAPE_STYLE_ATTRIBUTE,
        SHAPE_STYLE_REF_ATTRIBUTE,
        CUSTOM_GEOMETRY_ATTRIBUTE,
        CUSTOM_GEOMETRY_REF_ATTRIBUTE,
        "data-pptx-encoding",
        "data-pptx-ooxml-sha256",
        "data-pptx-part",
        "data-pptx-text-sha256",
    }
)

_TSPAN_INHERITED_ATTRIBUTES = frozenset({
    "fill",
    "fill-opacity",
    "font-family",
    "font-size",
    "font-style",
    "font-weight",
    "stroke",
    "stroke-opacity",
    "text-anchor",
    "text-decoration",
})
_ADOPT_INHERITED_ATTRIBUTES = tuple(INHERITABLE_ATTRS)

# Compact native-shape intent is intentionally not in the removal set:
# data-pptx-object, data-pptx-prst, and data-pptx-frame remain useful while
# reviewing the visible fallback. Structural markers also pass through
# unchanged; the IR records identity but never decides payload-restoration
# policy.


@dataclass
class SourceReference:
    source_ref: str
    source_path: tuple[int, ...]
    initial_authoring_subtree_sha256: str | None = None
    representation: str = "inline"
    proxy_asset: Path | None = None
    proxy_asset_sha256: str | None = None

    def as_dict(self, output_dir: Path) -> dict[str, object]:
        payload: dict[str, object] = {
            "source_path": list(self.source_path),
            "initial_authoring_subtree_sha256": self.initial_authoring_subtree_sha256,
            "representation": self.representation,
        }
        if self.representation == "source-proxy":
            if self.proxy_asset is None or self.proxy_asset_sha256 is None:
                raise ValueError(
                    f"Source proxy {self.source_ref} has no immutable asset record"
                )
            payload["proxy_asset"] = _portable_path(self.proxy_asset, output_dir)
            payload["proxy_asset_sha256"] = self.proxy_asset_sha256
        return payload


@dataclass
class ProjectionStats:
    txbody_metadata: int = 0
    hidden_geometry_carriers: int = 0
    geometry_preview_wrappers: int = 0
    geometry_detail_markers: int = 0
    compatibility_normalizations: int = 0
    asset_references_rewritten: int = 0
    coordinate_attributes_compacted: int = 0
    style_declarations_compacted: int = 0
    inherited_text_attributes_compacted: int = 0
    single_tspans_collapsed: int = 0
    source_object_proxies: int = 0
    source_object_proxy_bytes: int = 0
    semantic_shapes: int = 0
    semantic_tables: int = 0
    semantic_table_preview_bytes: int = 0
    unused_definitions_pruned: int = 0
    source_attributes: Counter[str] = field(default_factory=Counter)

    def as_dict(self) -> dict[str, object]:
        return {
            "txbody_metadata": self.txbody_metadata,
            "hidden_geometry_carriers": self.hidden_geometry_carriers,
            "geometry_preview_wrappers": self.geometry_preview_wrappers,
            "geometry_detail_markers": self.geometry_detail_markers,
            "compatibility_normalizations": self.compatibility_normalizations,
            "source_attributes": dict(sorted(self.source_attributes.items())),
            "asset_references_rewritten": self.asset_references_rewritten,
            "coordinate_attributes_compacted": self.coordinate_attributes_compacted,
            "style_declarations_compacted": self.style_declarations_compacted,
            "inherited_text_attributes_compacted": (
                self.inherited_text_attributes_compacted
            ),
            "single_tspans_collapsed": self.single_tspans_collapsed,
            "source_object_proxies": self.source_object_proxies,
            "source_object_proxy_bytes": self.source_object_proxy_bytes,
            "semantic_shapes": self.semantic_shapes,
            "semantic_tables": self.semantic_tables,
            "semantic_table_preview_bytes": self.semantic_table_preview_bytes,
            "unused_definitions_pruned": self.unused_definitions_pruned,
        }

    def merge(self, other: "ProjectionStats") -> None:
        self.txbody_metadata += other.txbody_metadata
        self.hidden_geometry_carriers += other.hidden_geometry_carriers
        self.geometry_preview_wrappers += other.geometry_preview_wrappers
        self.geometry_detail_markers += other.geometry_detail_markers
        self.compatibility_normalizations += other.compatibility_normalizations
        self.asset_references_rewritten += other.asset_references_rewritten
        self.coordinate_attributes_compacted += (
            other.coordinate_attributes_compacted
        )
        self.style_declarations_compacted += other.style_declarations_compacted
        self.inherited_text_attributes_compacted += (
            other.inherited_text_attributes_compacted
        )
        self.single_tspans_collapsed += other.single_tspans_collapsed
        self.source_object_proxies += other.source_object_proxies
        self.source_object_proxy_bytes += other.source_object_proxy_bytes
        self.semantic_shapes += other.semantic_shapes
        self.semantic_tables += other.semantic_tables
        self.semantic_table_preview_bytes += other.semantic_table_preview_bytes
        self.unused_definitions_pruned += other.unused_definitions_pruned
        self.source_attributes.update(other.source_attributes)


@dataclass
class ProjectionReport:
    source: Path
    output: Path
    original_bytes: int
    projected_bytes: int
    stats: ProjectionStats
    source_sha256: str
    initial_authoring_sha256: str
    source_references: list[SourceReference]

    def as_dict(self) -> dict[str, object]:
        saved = self.original_bytes - self.projected_bytes
        reduction = (saved / self.original_bytes * 100) if self.original_bytes else 0.0
        return {
            "source": str(self.source),
            "output": str(self.output),
            "original_bytes": self.original_bytes,
            "projected_bytes": self.projected_bytes,
            "bytes_saved": saved,
            "reduction_percent": round(reduction, 2),
            "source_sha256": self.source_sha256,
            "initial_authoring_sha256": self.initial_authoring_sha256,
            "source_ref_count": len(self.source_references),
            "removed": self.stats.as_dict(),
        }



# Stable upstream API facade, split by conversion responsibility.
from svg_authoring_view_identity import (
    _serialize_svg,
    _local_name,
    semantic_shape_text_component,
    _semantic_text,
    _stable_tag_name,
    semantic_subtree_sha256,
    _iter_element_paths,
    _source_reference,
    _stamp_source_references,
    _index_initial_authoring_references,
    _fresh_native_fallback_markers,
)
from svg_authoring_view_semantic_text import (
    _is_hidden_geometry_carrier,
    _is_semantic_shape_candidate,
    _mark_semantic_objects,
    _make_geometry_carrier_visible,
    _semantic_text_baseline,
    _semantic_text_font_size,
    _semantic_text_has_content,
    _normalize_semantic_text_lines,
    _semantic_paragraph_from_text,
    _merge_semantic_shape_texts,
)
from svg_authoring_view_projection import (
    _compact_semantic_shapes,
    _append_tail,
    _remove_child,
    _unwrap_preview,
    _strip_import_attributes,
    _compact_text_runs,
    _project_subtree,
    _rewrite_asset_reference,
    _rewrite_asset_references,
)
from svg_authoring_view_assets import (
    _definition_references,
    _definition_closure,
    _selected_definitions,
    _prune_unreferenced_definitions,
    _proxy_canvas_attributes,
    _source_proxy_asset_bytes,
    _source_proxy_element,
    _compact_semantic_tables,
    _prefer_editable_vector_asset,
    _contains_authoring_semantics,
)
from svg_authoring_view_render import (
    _externalize_large_source_objects,
    _render_projection,
)
from svg_authoring_view_manifest import (
    _portable_path,
    _authoring_manifest_bytes,
    _authoring_document_kind,
    _authoring_summary_document,
)
from svg_authoring_view_summary import (
    _load_authoring_summary_manifest,
    _authoring_summary_bytes,
    write_authoring_summary,
)
from svg_authoring_view_context import (
    _parse_adopt_source,
    _authoring_page_path,
    _parse_authoring_svg,
    _element_chain,
    _local_inherited_presentation,
    _local_opacity,
    _set_explicit_presentation,
    _format_transform_number,
    _materialize_adopted_context,
)
from svg_authoring_view_adopt_helpers import (
    _rewrite_adopted_fragment_ids,
    _adopted_definition_clones,
    _append_adopted_definitions,
    _authoring_source_document_ids,
    _strip_adopted_transport,
    _write_svg_atomically,
)
from svg_authoring_view_adopt import (
    adopt_authoring_object,
)
from svg_authoring_view_publication import (
    _nearest_existing_directory,
    _ensure_directory,
    _remove_created_directories,
    _rollback_published_files,
    _publish_existing_directory,
    project_svg_batch,
    _is_within,
    _source_mapping,
)
from svg_authoring_view_cli import (
    build_parser,
    main,
)

