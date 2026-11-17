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



# Upstream MIT, Hugo He 2025-2026; algorithms retained during module split.
from svg_authoring_view import (
    ProjectionReport,
    ProjectionStats,
    SourceReference,
)

def _externalize_large_source_objects(
    root: ET.Element,
    references: list[SourceReference],
    output_dir: Path,
    proxy_dir: Path,
    stats: ProjectionStats,
) -> dict[Path, bytes]:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from svg_authoring_view import (
        SEMANTIC_OBJECT_ATTRIBUTE,
        SEMANTIC_SHAPE_KIND,
        SEMANTIC_TABLE_KIND,
        SOURCE_PROXY_ATTRIBUTE,
        SOURCE_PROXY_KIND,
        SOURCE_PROXY_MIN_BYTES,
        SOURCE_REF_ATTRIBUTE,
        _contains_authoring_semantics,
        _prefer_editable_vector_asset,
        _prune_unreferenced_definitions,
        _serialize_svg,
        _source_proxy_asset_bytes,
        _source_proxy_element,
    )
    reference_by_id = {
        reference.source_ref: reference
        for reference in references
    }
    candidates: list[tuple[ET.Element, ET.Element]] = []

    def visit(parent: ET.Element, inside_source_ref: bool) -> None:
        for child in list(parent):
            source_ref = child.get(SOURCE_REF_ATTRIBUTE)
            forced_proxy = child.get(SOURCE_PROXY_ATTRIBUTE) == SOURCE_PROXY_KIND
            if source_ref and (not inside_source_ref or forced_proxy):
                candidates.append((parent, child))
                if forced_proxy:
                    continue
            visit(child, inside_source_ref or bool(source_ref))

    visit(root, False)
    assets: dict[Path, bytes] = {}
    for parent, element in candidates:
        if element.get(SEMANTIC_OBJECT_ATTRIBUTE) in {
            SEMANTIC_SHAPE_KIND,
            SEMANTIC_TABLE_KIND,
        }:
            continue
        original_bytes = len(_serialize_svg(element))
        forced_source_proxy = (
            element.get(SOURCE_PROXY_ATTRIBUTE) == SOURCE_PROXY_KIND
        )
        if (
            not forced_source_proxy
            and (
                original_bytes < SOURCE_PROXY_MIN_BYTES
                or _contains_authoring_semantics(element)
                or _prefer_editable_vector_asset(element)
            )
        ):
            continue
        source_ref = element.get(SOURCE_REF_ATTRIBUTE)
        if not source_ref or source_ref not in reference_by_id:
            raise ValueError("Large source object has no manifest reference")
        payload = _source_proxy_asset_bytes(
            root,
            element,
            output_dir,
            proxy_dir,
            stats,
        )
        digest = hashlib.sha256(payload).hexdigest()
        asset_path = proxy_dir / f"source-object-{digest}.svg"
        previous = assets.get(asset_path)
        if previous is not None and previous != payload:
            raise ValueError(f"Source proxy hash collision: {asset_path.name}")
        assets[asset_path] = payload

        reference = reference_by_id[source_ref]
        reference.representation = "source-proxy"
        reference.proxy_asset = asset_path
        reference.proxy_asset_sha256 = digest
        proxy = _source_proxy_element(
            root,
            element,
            source_ref,
            asset_path,
            output_dir,
        )
        siblings = list(parent)
        index = siblings.index(element)
        parent.remove(element)
        parent.insert(index, proxy)
        stats.source_object_proxies += 1
        stats.source_object_proxy_bytes += original_bytes

    _prune_unreferenced_definitions(root, stats)
    active_refs = {
        source_ref
        for element in root.iter()
        if (source_ref := element.get(SOURCE_REF_ATTRIBUTE))
    }
    references[:] = [
        reference
        for reference in references
        if reference.source_ref in active_refs
    ]
    return assets


def _render_projection(
    source: Path,
    output: Path,
    source_proxy_dir: Path | None = None,
) -> tuple[ProjectionReport, bytes, dict[Path, bytes]]:
    """Build one projection in memory without changing source or destination."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from svg_authoring_view import (
        ProjectionReport,
        ProjectionStats,
        SEMANTIC_OBJECT_ATTRIBUTE,
        SEMANTIC_TABLE_KIND,
        _compact_semantic_shapes,
        _compact_semantic_tables,
        _compact_text_runs,
        _externalize_large_source_objects,
        _fresh_native_fallback_markers,
        _index_initial_authoring_references,
        _local_name,
        _mark_semantic_objects,
        _project_subtree,
        _rewrite_asset_references,
        _serialize_svg,
        _stamp_source_references,
        _strip_import_attributes,
    )
    original = source.read_bytes()
    parser = ET.XMLParser(
        target=ET.TreeBuilder(insert_comments=True, insert_pis=True),
    )
    root = ET.fromstring(original, parser=parser)
    if _local_name(root.tag) != "svg":
        raise ValueError(f"Root element is not <svg>: {source}")

    fresh_native_markers = _fresh_native_fallback_markers(root)
    source_references = _stamp_source_references(root)
    stats = ProjectionStats()
    _mark_semantic_objects(root, stats)
    _compact_semantic_shapes(root, stats)
    definitions, _duplicates = project_definition_index(root)
    _project_subtree(root, stats, ConvertContext(defs=definitions))
    _compact_text_runs(root, stats)
    stats.compatibility_normalizations += len(
        normalize_single_child_group_filters(root)
    )
    _strip_import_attributes(root, stats)
    _rewrite_asset_references(root, source.parent, output.parent, stats)
    source_proxy_assets: dict[Path, bytes] = {}
    if source_proxy_dir is not None:
        source_proxy_assets.update(_compact_semantic_tables(
            root,
            source_references,
            output.parent,
            source_proxy_dir,
            stats,
        ))
        source_proxy_assets.update(_externalize_large_source_objects(
            root,
            source_references,
            output.parent,
            source_proxy_dir,
            stats,
        ))
    normalization = normalize_compact_authoring_tree(root)
    stats.coordinate_attributes_compacted = (
        normalization.coordinates.changed_attributes
    )
    stats.style_declarations_compacted = (
        normalization.styles.changed_declarations
    )
    live_elements = set(root.iter())
    for marker in fresh_native_markers & live_elements:
        if marker.get(SEMANTIC_OBJECT_ATTRIBUTE) == SEMANTIC_TABLE_KIND:
            marker.attrib.pop(NATIVE_FALLBACK_SHA256_ATTR, None)
            continue
        marker.set(
            NATIVE_FALLBACK_SHA256_ATTR,
            svg_native_fallback_fingerprint(marker, document_root=root),
        )
    _index_initial_authoring_references(root, source_references)

    contract_errors = canonical_authoring_errors(root)
    if contract_errors:
        raise ValueError(
            "Canonical authoring projection failed: "
            + "; ".join(contract_errors)
        )

    projected = _serialize_svg(root)
    if not projected.endswith(b"\n"):
        projected += b"\n"

    report = ProjectionReport(
        source=source,
        output=output,
        original_bytes=len(original),
        projected_bytes=len(projected),
        stats=stats,
        source_sha256=hashlib.sha256(original).hexdigest(),
        initial_authoring_sha256=hashlib.sha256(projected).hexdigest(),
        source_references=source_references,
    )
    return report, projected, source_proxy_assets


