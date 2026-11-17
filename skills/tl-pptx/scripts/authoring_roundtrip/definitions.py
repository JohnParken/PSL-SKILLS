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



# Upstream MIT, Hugo He 2025-2026; algorithms retained during module split.
from authoring_roundtrip import (
    VectorAssetRecord,
)

def _layered_ref_index(root: ET.Element) -> dict[str, ET.Element]:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from authoring_roundtrip import (
        AuthoringRoundtripError,
        _source_identity,
    )
    index: dict[str, ET.Element] = {}
    for element in root.iter():
        if not element.get("id") or not element.get("data-pptx-object"):
            continue
        source_ref = _source_identity(element)
        if source_ref is None:
            continue
        if source_ref in index:
            raise AuthoringRoundtripError(
                f"Layered SVG contains duplicate source identity {source_ref!r}"
            )
        index[source_ref] = element
    return index


def _direct_defs(root: ET.Element) -> ET.Element | None:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from authoring_roundtrip import (
        _local_name,
    )
    return next(
        (child for child in root if _local_name(child.tag) == "defs"),
        None,
    )


def _definition_changes(
    current_root: ET.Element,
    baseline_root: ET.Element,
    current_assets: dict[str, VectorAssetRecord],
    baseline_assets: dict[str, VectorAssetRecord],
) -> tuple[bool, set[str]]:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from authoring_roundtrip import (
        _direct_defs,
        normalized_authoring_subtree_sha256,
    )
    current_defs = _direct_defs(current_root)
    baseline_defs = _direct_defs(baseline_root)
    current_hash = (
        normalized_authoring_subtree_sha256(current_defs, current_assets)
        if current_defs is not None
        else None
    )
    baseline_hash = (
        normalized_authoring_subtree_sha256(baseline_defs, baseline_assets)
        if baseline_defs is not None
        else None
    )
    if current_hash == baseline_hash:
        return False, set()
    current_children = list(current_defs) if current_defs is not None else []
    baseline_children = list(baseline_defs) if baseline_defs is not None else []
    current_by_id = {
        child.get("id"): child
        for child in current_children
        if child.get("id")
    }
    baseline_by_id = {
        child.get("id"): child
        for child in baseline_children
        if child.get("id")
    }
    changed_ids = {
        definition_id
        for definition_id in set(current_by_id) | set(baseline_by_id)
        if definition_id not in current_by_id
        or definition_id not in baseline_by_id
        or normalized_authoring_subtree_sha256(
            current_by_id[definition_id],
            current_assets,
        )
        != normalized_authoring_subtree_sha256(
            baseline_by_id[definition_id],
            baseline_assets,
        )
    }
    return True, changed_ids


def _referenced_definition_ids(element: ET.Element) -> set[str]:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from authoring_roundtrip import (
        _URL_ID_RE,
        _local_name,
    )
    references: set[str] = set()
    for item in element.iter():
        for name, value in item.attrib.items():
            references.update(match.group(2) for match in _URL_ID_RE.finditer(value))
            if _local_name(name) == "href" and value.startswith("#"):
                references.add(value[1:])
    return references


def _definition_reference_ids(element: ET.Element) -> set[str]:
    """Return local definition ids referenced by attributes or style text."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from authoring_roundtrip import (
        _URL_ID_RE,
        _referenced_definition_ids,
    )
    references = _referenced_definition_ids(element)
    for item in element.iter():
        if item.text:
            references.update(
                match.group(2) for match in _URL_ID_RE.finditer(item.text)
            )
    return references


def _prune_unreferenced_definitions(root: ET.Element) -> None:
    """Keep only the definition closure reachable by converted page content."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from authoring_roundtrip import (
        _definition_reference_ids,
        _direct_defs,
        _local_name,
    )
    definitions = _direct_defs(root)
    if definitions is None:
        return
    owner_by_id: dict[str, ET.Element] = {}
    for child in definitions:
        for item in child.iter():
            if definition_id := item.get("id"):
                owner_by_id.setdefault(definition_id, child)

    always_kept = [
        child
        for child in definitions
        if _local_name(child.tag) == "style" or not child.get("id")
    ]
    pending = [
        reference
        for child in root
        if child is not definitions
        for reference in _definition_reference_ids(child)
    ]
    pending.extend(
        reference
        for child in always_kept
        for reference in _definition_reference_ids(child)
    )
    selected_owners: set[int] = set()
    required_ids: set[str] = set()
    while pending:
        definition_id = pending.pop()
        if definition_id in required_ids:
            continue
        required_ids.add(definition_id)
        owner = owner_by_id.get(definition_id)
        if owner is None or id(owner) in selected_owners:
            continue
        selected_owners.add(id(owner))
        pending.extend(_definition_reference_ids(owner) - required_ids)

    for child in list(definitions):
        if child in always_kept or id(child) in selected_owners:
            continue
        definitions.remove(child)
    if not list(definitions) and not (
        definitions.text and definitions.text.strip()
    ):
        root.remove(definitions)


def _merge_defs(
    layered_root: ET.Element,
    authoring_root: ET.Element,
    *,
    include_authoring_changes: bool,
) -> ET.Element | None:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from authoring_roundtrip import (
        SVG_NS,
        _direct_defs,
    )
    layered_defs = _direct_defs(layered_root)
    authoring_defs = _direct_defs(authoring_root)
    if layered_defs is None and (
        authoring_defs is None or not include_authoring_changes
    ):
        return None
    merged = (
        copy.deepcopy(layered_defs)
        if layered_defs is not None
        else ET.Element(f"{{{SVG_NS}}}defs")
    )
    if not include_authoring_changes or authoring_defs is None:
        return merged
    by_id = {
        child.get("id"): (index, child)
        for index, child in enumerate(merged)
        if child.get("id")
    }
    existing_hashes = {
        semantic_subtree_sha256(child)
        for child in merged
    }
    for child in authoring_defs:
        clone = copy.deepcopy(child)
        definition_id = clone.get("id")
        if definition_id and definition_id in by_id:
            index, previous = by_id[definition_id]
            projected_previous = copy.deepcopy(previous)
            for item in projected_previous.iter():
                for name in AUTHORING_OMITTED_SOURCE_ATTRIBUTES:
                    item.attrib.pop(name, None)
            if (
                semantic_subtree_sha256(projected_previous)
                == semantic_subtree_sha256(clone)
            ):
                continue
            merged.remove(previous)
            merged.insert(index, clone)
            by_id[definition_id] = (index, clone)
            continue
        child_hash = semantic_subtree_sha256(clone)
        if child_hash not in existing_hashes:
            merged.append(clone)
            existing_hashes.add(child_hash)
    return merged


def _serialize_svg(root: ET.Element) -> bytes:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from authoring_roundtrip import (
        SVG_NS,
        XLINK_NS,
    )
    ET.register_namespace("", SVG_NS)
    ET.register_namespace("xlink", XLINK_NS)
    payload = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    return payload if payload.endswith(b"\n") else payload + b"\n"


