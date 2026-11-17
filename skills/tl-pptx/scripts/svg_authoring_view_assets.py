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
    ProjectionStats,
    SourceReference,
)

def _definition_references(element: ET.Element) -> set[str]:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from svg_authoring_view import (
        _URL_ID_RE,
        _local_name,
    )
    references: set[str] = set()
    for item in element.iter():
        for name, value in item.attrib.items():
            references.update(match.group(2) for match in _URL_ID_RE.finditer(value))
            if _local_name(name) == "href" and value.startswith("#"):
                references.add(value[1:])
        if item.text:
            references.update(
                match.group(2) for match in _URL_ID_RE.finditer(item.text)
            )
    return references


def _definition_closure(
    root: ET.Element,
    content: list[ET.Element],
) -> tuple[list[ET.Element], set[int]]:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from svg_authoring_view import (
        _definition_references,
        _local_name,
    )
    definitions = [
        child for child in root
        if _local_name(child.tag) == "defs"
    ]
    owner_by_id: dict[str, ET.Element] = {}
    for definitions_root in definitions:
        for child in definitions_root:
            for item in child.iter():
                definition_id = item.get("id")
                if definition_id:
                    owner_by_id.setdefault(definition_id, child)

    required_ids: set[str] = set()
    pending = [
        reference
        for element in content
        for reference in _definition_references(element)
    ]
    selected_owners: set[int] = set()
    while pending:
        definition_id = pending.pop()
        if definition_id in required_ids:
            continue
        required_ids.add(definition_id)
        owner = owner_by_id.get(definition_id)
        if owner is None or id(owner) in selected_owners:
            continue
        selected_owners.add(id(owner))
        pending.extend(_definition_references(owner) - required_ids)
    return definitions, selected_owners


def _selected_definitions(
    root: ET.Element,
    content: list[ET.Element],
) -> list[ET.Element]:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from svg_authoring_view import (
        _definition_closure,
        _local_name,
    )
    definitions, selected_owners = _definition_closure(root, content)
    selected: list[ET.Element] = []
    for definitions_root in definitions:
        clone = ET.Element(definitions_root.tag, dict(definitions_root.attrib))
        clone.text = definitions_root.text
        for child in definitions_root:
            always_keep = _local_name(child.tag) == "style" or not child.get("id")
            if always_keep or id(child) in selected_owners:
                clone.append(copy.deepcopy(child))
        if list(clone) or (clone.text and clone.text.strip()):
            selected.append(clone)
    return selected


def _prune_unreferenced_definitions(
    root: ET.Element,
    stats: ProjectionStats,
) -> None:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from svg_authoring_view import (
        _definition_closure,
        _local_name,
    )
    definitions = [
        child for child in root
        if _local_name(child.tag) == "defs"
    ]
    if not definitions:
        return
    visible_content = [child for child in root if child not in definitions]
    _, selected_owners = _definition_closure(root, visible_content)
    for definitions_root in definitions:
        for child in list(definitions_root):
            always_keep = _local_name(child.tag) == "style" or not child.get("id")
            if always_keep or id(child) in selected_owners:
                continue
            definitions_root.remove(child)
            stats.unused_definitions_pruned += 1
        if not list(definitions_root) and not (
            definitions_root.text and definitions_root.text.strip()
        ):
            root.remove(definitions_root)


def _proxy_canvas_attributes(root: ET.Element) -> dict[str, str]:
    view_box = (root.get("viewBox") or "").split()
    if len(view_box) == 4:
        x, y, width, height = view_box
    else:
        x = y = "0"
        width = root.get("width") or "1"
        height = root.get("height") or "1"
    return {
        "x": x,
        "y": y,
        "width": width,
        "height": height,
        "preserveAspectRatio": "none",
    }


def _source_proxy_asset_bytes(
    root: ET.Element,
    element: ET.Element,
    authoring_dir: Path,
    proxy_dir: Path,
    stats: ProjectionStats,
    *,
    strip_element_transform: bool = False,
) -> bytes:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from svg_authoring_view import (
        SOURCE_PROXY_ATTRIBUTE,
        SOURCE_REF_ATTRIBUTE,
        _rewrite_asset_references,
        _selected_definitions,
        _serialize_svg,
    )
    asset_root = ET.Element(root.tag, dict(root.attrib))
    for definitions in _selected_definitions(root, [element]):
        asset_root.append(definitions)
    asset_element = copy.deepcopy(element)
    if strip_element_transform:
        asset_element.attrib.pop("transform", None)
    asset_root.append(asset_element)
    for item in asset_root.iter():
        item.attrib.pop(SOURCE_REF_ATTRIBUTE, None)
        item.attrib.pop(SOURCE_PROXY_ATTRIBUTE, None)
    _rewrite_asset_references(
        asset_root,
        authoring_dir,
        proxy_dir,
        stats,
    )
    stats.coordinate_attributes_compacted += compact_svg_tree(
        asset_root,
    ).changed_attributes
    payload = _serialize_svg(asset_root)
    return payload if payload.endswith(b"\n") else payload + b"\n"


def _source_proxy_element(
    root: ET.Element,
    source: ET.Element,
    source_ref: str,
    asset_path: Path,
    output_dir: Path,
) -> ET.Element:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from svg_authoring_view import (
        EXTERNAL_LINKED_IMAGE_PROXY_ATTRIBUTE,
        SOURCE_PROXY_ATTRIBUTE,
        SOURCE_PROXY_KIND,
        SOURCE_REF_ATTRIBUTE,
        SVG_NS,
        _proxy_canvas_attributes,
    )
    proxy = ET.Element(f"{{{SVG_NS}}}image")
    for name in (
        "id",
        "data-pptx-object",
        "data-pptx-frame",
        EXTERNAL_LINKED_IMAGE_PROXY_ATTRIBUTE,
        "data-pptx-prst",
        "data-pptx-av-adj",
        "data-pptx-av-adj1",
        "data-pptx-av-adj2",
        "data-pptx-import-source",
        "data-pptx-native-authority",
        "data-pptx-replacement-status",
        "data-pptx-replace-with",
        "data-pptx-placeholder",
        "data-pptx-placeholder-type",
        "data-pptx-placeholder-idx",
        "data-pptx-structure-role",
    ):
        value = source.get(name)
        if value is not None:
            proxy.set(name, value)
    proxy.attrib.update(_proxy_canvas_attributes(root))
    proxy.set(
        "href",
        os.path.relpath(asset_path, output_dir).replace(os.sep, "/"),
    )
    proxy.set(SOURCE_REF_ATTRIBUTE, source_ref)
    proxy.set(SOURCE_PROXY_ATTRIBUTE, SOURCE_PROXY_KIND)
    proxy.tail = source.tail
    return proxy


def _compact_semantic_tables(
    root: ET.Element,
    references: list[SourceReference],
    output_dir: Path,
    preview_dir: Path,
    stats: ProjectionStats,
) -> dict[Path, bytes]:
    """Keep table data inline while moving only its rendered preview aside."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from svg_authoring_view import (
        SEMANTIC_OBJECT_ATTRIBUTE,
        SEMANTIC_TABLE_KIND,
        SOURCE_REF_ATTRIBUTE,
        SVG_NS,
        _local_name,
        _proxy_canvas_attributes,
        _source_proxy_asset_bytes,
    )
    assets: dict[Path, bytes] = {}
    reference_by_id = {
        reference.source_ref: reference
        for reference in references
    }
    for element in list(root.iter()):
        if element.get(SEMANTIC_OBJECT_ATTRIBUTE) != SEMANTIC_TABLE_KIND:
            continue
        source_ref = element.get(SOURCE_REF_ATTRIBUTE)
        if not source_ref or source_ref not in reference_by_id:
            raise ValueError("Semantic table has no source reference")
        payload_metadata = [
            child
            for child in element
            if _local_name(child.tag) == "metadata"
            and child.get("type") == "application/json"
        ]
        if len(payload_metadata) != 1:
            raise ValueError(
                f"Semantic table {source_ref} requires exactly one JSON payload"
            )

        preview_source = copy.deepcopy(element)
        for child in list(preview_source):
            if _local_name(child.tag) == "metadata":
                preview_source.remove(child)
        payload = _source_proxy_asset_bytes(
            root,
            preview_source,
            output_dir,
            preview_dir,
            stats,
            strip_element_transform=True,
        )
        digest = hashlib.sha256(payload).hexdigest()
        asset_path = preview_dir / f"semantic-table-preview-{digest}.svg"
        previous = assets.get(asset_path)
        if previous is not None and previous != payload:
            raise ValueError(f"Semantic table preview hash collision: {asset_path.name}")
        assets[asset_path] = payload

        metadata = payload_metadata[0]
        metadata.tail = None
        for child in list(element):
            element.remove(child)
        element.append(metadata)
        preview = ET.Element(f"{{{SVG_NS}}}image")
        preview.attrib.update(_proxy_canvas_attributes(root))
        preview.set(
            "href",
            os.path.relpath(asset_path, output_dir).replace(os.sep, "/"),
        )
        preview.set("data-pptx-part", "authoring-preview")
        element.append(preview)
        stats.semantic_table_preview_bytes += len(payload)
    return assets


def _prefer_editable_vector_asset(element: ET.Element) -> bool:
    """Keep pure vector decorations available for later `<use>` extraction."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from svg_authoring_view import (
        _DRAWABLE_TAGS,
        _SEMANTIC_CONTENT_TAGS,
        _local_name,
    )
    if any(
        _local_name(item.tag) in _SEMANTIC_CONTENT_TAGS
        for item in element.iter()
    ):
        return False
    if any(
        "chart" in (item.get("id") or "").lower()
        for item in element.iter()
    ):
        return False
    return any(
        _local_name(item.tag) in _DRAWABLE_TAGS
        for item in element.iter()
    )


def _contains_authoring_semantics(element: ET.Element) -> bool:
    """Keep meaning-bearing content inline and editable for the model."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from svg_authoring_view import (
        _SEMANTIC_AUTHORING_ATTRIBUTES,
        _SEMANTIC_CONTENT_TAGS,
        _local_name,
    )
    for item in element.iter():
        if _local_name(item.tag) in _SEMANTIC_CONTENT_TAGS:
            return True
        if any(item.get(name) is not None for name in _SEMANTIC_AUTHORING_ATTRIBUTES):
            return True
        if (
            _local_name(item.tag) == "metadata"
            and item.get("type") == "application/json"
        ):
            return True
    return False


