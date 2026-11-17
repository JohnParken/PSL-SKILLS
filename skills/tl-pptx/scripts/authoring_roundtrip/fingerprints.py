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
    RefOccurrence,
    VectorAssetRecord,
)

def _collapse_hash_only_groups(root: ET.Element) -> None:
    """Canonicalize attribute-free one-child wrappers for edit comparison."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from authoring_roundtrip import (
        _local_name,
    )
    for parent in list(root.iter()):
        changed = True
        while changed:
            changed = False
            for index, child in enumerate(list(parent)):
                if (
                    _local_name(child.tag) != "g"
                    or child.attrib
                    or len(child) != 1
                    or (child.text and child.text.strip())
                ):
                    continue
                replacement = child[0]
                replacement.tail = child.tail
                parent.remove(child)
                parent.insert(index, replacement)
                changed = True
                break


def normalized_authoring_subtree_sha256(
    element: ET.Element,
    assets: dict[str, VectorAssetRecord],
    *,
    asset: VectorAssetRecord | None = None,
) -> str:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from authoring_roundtrip import (
        _URL_ID_RE,
        _collapse_hash_only_groups,
        _local_name,
    )
    normalized = copy.deepcopy(element)
    if asset is not None and not asset.unchanged:
        normalized.set(
            "data-pptx-edited-vector-asset-sha256",
            asset.actual_asset_sha256,
        )
    prefix = f"{asset.element_prefix}_" if asset is not None else None

    def rewrite_url(match: re.Match[str]) -> str:
        quote, ref_id = match.group(1), match.group(2)
        if prefix and ref_id.startswith(prefix):
            ref_id = ref_id[len(prefix):]
        return f"url({quote}#{ref_id}{quote})"

    for item in normalized.iter():
        if prefix:
            item_id = item.get("id")
            if item_id and item_id.startswith(prefix):
                item.set("id", item_id[len(prefix):])
            for name, value in list(item.attrib.items()):
                rewritten = _URL_ID_RE.sub(rewrite_url, value)
                if _local_name(name) == "href" and rewritten.startswith(f"#{prefix}"):
                    rewritten = "#" + rewritten[len(prefix) + 1:]
                if rewritten != value:
                    item.set(name, rewritten)
        if asset is not None:
            for name, value in list(item.attrib.items()):
                if _local_name(name) != "href" or value.startswith(("#", "data:")):
                    continue
                parsed = urlsplit(value)
                if parsed.scheme or parsed.netloc or not parsed.path:
                    continue
                resolved = (
                    asset.asset_path.parent / unquote(parsed.path)
                ).resolve()
                normalized_ref = f"asset-resource:{resolved.as_posix()}"
                if parsed.query:
                    normalized_ref += f"?{parsed.query}"
                if parsed.fragment:
                    normalized_ref += f"#{parsed.fragment}"
                item.set(name, normalized_ref)
        icon = item.get("data-icon")
        record = assets.get(icon or "")
        if record is not None:
            token = f"asset-slot:{record.baseline_key}"
            if not record.unchanged:
                token += f":edited:{record.actual_asset_sha256}"
            item.set("data-icon", token)
    normalize_single_child_group_filters(normalized)
    _collapse_hash_only_groups(normalized)
    return semantic_subtree_sha256(
        normalized,
        ignored_attributes=frozenset({SOURCE_REF_ATTRIBUTE}),
    )


def _contextual_authoring_element(element: ET.Element, root: ET.Element | None) -> ET.Element:
    """Resolve the same effective context used when moving an authored object."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from authoring_roundtrip import (
        SVG_NS,
        _local_name,
    )
    if root is None:
        return element
    resolved = copy.deepcopy(element)
    _materialize_adopted_context(root, element, ET.Element(f"{{{SVG_NS}}}svg"), resolved)
    links = [
        dict(ancestor.attrib)
        for ancestor in _element_chain(root, element)[:-1]
        if _local_name(ancestor.tag) == "a"
    ]
    if links:
        resolved.set("data-pptx-runtime-ancestor-links", json.dumps(links, sort_keys=True))
    return resolved


def _source_jump_links(current_root: ET.Element, baseline_root: ET.Element) -> dict[ET.Element, tuple[str, str]]:
    """Identify surviving links by their source owner and position in that owner."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from authoring_roundtrip import (
        XLINK_NS,
        _local_name,
    )
    def links(root: ET.Element) -> dict[tuple, tuple[ET.Element, str, str]]:
        found = {}

        def visit(node: ET.Element, owner: str | None = None, path: tuple = ()) -> None:
            source_ref = node.get(SOURCE_REF_ATTRIBUTE)
            if source_ref:
                owner, path = source_ref, ()
            attrs = [SHAPE_HYPERLINK_ATTR]
            if _local_name(node.tag) == "a":
                attrs.extend(("href", f"{{{XLINK_NS}}}href"))
            for name in attrs:
                href = node.get(name, "")
                if not re.fullmatch(r"#slide-[1-9]\d*", href):
                    continue
                if owner:
                    key = (owner, path, name)
                else:
                    descendants = tuple(item.get(SOURCE_REF_ATTRIBUTE) for item in node.iter()
                                        if item.get(SOURCE_REF_ATTRIBUTE))
                    if not descendants:
                        continue
                    key = (descendants, name)
                found[key] = (node, name, href)
            for index, child in enumerate(node):
                visit(child, owner, path + ((child.tag, index),))

        visit(root)
        return found

    baseline = links(baseline_root)
    return {
        node: (name, href)
        for key, (node, name, href) in links(current_root).items()
        if key in baseline and baseline[key][2] == href
        and (baseline[key][0].get(SOURCE_HREF_ATTR) is None or node.get(SOURCE_HREF_ATTR) == href)
    }


def preserve_adopted_source_links(
    authoring_dir: Path, source_name: str, source_root: ET.Element,
    source_element: ET.Element, adopted: ET.Element,
) -> None:
    """Carry source jump provenance across adoption after native identity is removed."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from authoring_roundtrip import (
        AuthoringRoundtripError,
        XLINK_NS,
        _generate_baseline_bundle,
        _load_current_assets,
        _load_documents,
        _load_page_plan,
        _local_name,
        _output_documents,
        _parse_svg,
        _source_jump_links,
    )
    project_path = authoring_dir.parent
    if not (project_path / ROUNDTRIP_MANIFEST_PATH).is_file():
        return
    source_dir, proxy_dir, documents, _, _ = _load_documents(project_path, authoring_dir)
    pages, _ = _load_page_plan(project_path, authoring_dir, documents)
    output_docs = {doc.name: doc for doc in _output_documents(pages, documents, authoring_dir)}
    document = output_docs.get(source_name) or documents.get(source_name)
    if document is None:
        raise AuthoringRoundtripError(f"Cannot resolve adopted source page {source_name}")
    _, inventory = _load_current_assets(project_path, authoring_dir, documents)
    temporary, baseline_dir, _ = _generate_baseline_bundle(
        project_path, source_dir, proxy_dir, documents, inventory,
    )
    try:
        inherited = _source_jump_links(source_root, _parse_svg(baseline_dir / document.source_name))
    finally:
        temporary.cleanup()
    for original, clone in zip(source_element.iter(), adopted.iter()):
        if original in inherited:
            clone.set(SOURCE_HREF_ATTR, inherited[original][1])
        if clone.get(SOURCE_HREF_ATTR):
            clone.set(ADOPTED_SOURCE_LINK_ATTR, "true")
    for ancestor in _element_chain(source_root, source_element)[:-1]:
        if _local_name(ancestor.tag) != "a":
            continue
        href = ancestor.get("href") or ancestor.get(f"{{{XLINK_NS}}}href")
        if href:
            adopted.set(SHAPE_HYPERLINK_ATTR, href)
            if ancestor in inherited or ancestor.get(SOURCE_HREF_ATTR) == href:
                adopted.set(SOURCE_HREF_ATTR, href)
                adopted.set(ADOPTED_SOURCE_LINK_ATTR, "true")


def authoring_source_ref_is_unchanged(
    current_element: ET.Element,
    baseline_element: ET.Element,
    current_assets: dict[str, VectorAssetRecord],
    baseline_assets: dict[str, VectorAssetRecord],
    *,
    changed_definition_ids: set[str],
    current_asset: VectorAssetRecord | None = None,
    baseline_asset: VectorAssetRecord | None = None,
    current_root: ET.Element | None = None,
    baseline_root: ET.Element | None = None,
) -> bool:
    """Compare one source-ref occurrence exactly as round-trip export does."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from authoring_roundtrip import (
        _contextual_authoring_element,
        _referenced_definition_ids,
        normalized_authoring_subtree_sha256,
    )
    current_element = _contextual_authoring_element(current_element, current_root)
    baseline_element = _contextual_authoring_element(baseline_element, baseline_root)
    return (
        normalized_authoring_subtree_sha256(
            current_element,
            current_assets,
            asset=current_asset,
        )
        == normalized_authoring_subtree_sha256(
            baseline_element,
            baseline_assets,
            asset=baseline_asset,
        )
        and not (
            changed_definition_ids
            & _referenced_definition_ids(current_element)
        )
    )


def _referenced_assets(
    root: ET.Element,
    assets: dict[str, VectorAssetRecord],
    *,
    document_name: str,
    origin_document: str | None = None,
) -> dict[str, tuple[VectorAssetRecord, ET.Element]]:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from authoring_roundtrip import (
        AuthoringRoundtripError,
        IMPORTED_ICON_NAMESPACE,
        _parse_svg,
    )
    referenced: dict[str, tuple[VectorAssetRecord, ET.Element]] = {}
    for element in root.iter():
        icon = (element.get("data-icon") or "").strip()
        if not icon or icon in referenced:
            continue
        if (
            icon.startswith(f"{IMPORTED_ICON_NAMESPACE}/")
            and element.get(ASSET_ROLE_ATTRIBUTE) != DECORATION_ASSET_ROLE
        ):
            raise AuthoringRoundtripError(
                f"{document_name} imported vector {icon!r} is not marked "
                "as decoration"
            )
        record = assets.get(icon)
        if record is None:
            if icon.startswith(f"{IMPORTED_ICON_NAMESPACE}/"):
                raise AuthoringRoundtripError(
                    f"{document_name} references imported vector {icon!r} "
                    "without an inventory record"
                )
            continue
        expected_origin = origin_document or document_name
        if record.origin_document != expected_origin:
            raise AuthoringRoundtripError(
                f"{document_name} references {icon!r} owned by "
                f"{record.origin_document}"
            )
        referenced[icon] = (record, _parse_svg(record.asset_path))
    return referenced


def _ref_occurrences(
    root: ET.Element,
    referenced_assets: dict[str, tuple[VectorAssetRecord, ET.Element]],
) -> dict[str, list[RefOccurrence]]:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from authoring_roundtrip import (
        RefOccurrence,
    )
    occurrences: dict[str, list[RefOccurrence]] = {}
    for element in root.iter():
        source_ref = element.get(SOURCE_REF_ATTRIBUTE)
        if source_ref:
            occurrences.setdefault(source_ref, []).append(
                RefOccurrence(element=element, asset=None, root=root)
            )
    for record, asset_root in referenced_assets.values():
        context_root = copy.deepcopy(root)
        use = next(item for item in context_root.iter() if item.get("data-icon") == record.icon)
        use.append(asset_root)
        for element in asset_root.iter():
            source_ref = element.get(SOURCE_REF_ATTRIBUTE)
            if source_ref:
                occurrences.setdefault(source_ref, []).append(
                    RefOccurrence(element=element, asset=record, root=context_root)
                )
    return occurrences


def _outer_source_ref_elements(root: ET.Element) -> list[ET.Element]:
    elements: list[ET.Element] = []

    def visit(element: ET.Element, inside_ref: bool) -> None:
        source_ref = element.get(SOURCE_REF_ATTRIBUTE)
        if source_ref and not inside_ref:
            elements.append(element)
            return
        for child in element:
            visit(child, inside_ref or bool(source_ref))

    visit(root, False)
    return elements


