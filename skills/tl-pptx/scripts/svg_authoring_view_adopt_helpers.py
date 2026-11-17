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

def _rewrite_adopted_fragment_ids(
    element: ET.Element,
    target_ids: set[str],
) -> dict[str, str]:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from svg_authoring_view import (
        _URL_ID_RE,
        _local_name,
    )
    subtree_ids = [
        item_id
        for item in element.iter()
        if (item_id := item.get("id"))
    ]
    duplicates = sorted(
        item_id
        for item_id, count in Counter(subtree_ids).items()
        if count > 1
    )
    if duplicates:
        raise ValueError(
            "Adopted subtree contains duplicate id(s): "
            + ", ".join(duplicates[:8])
        )

    reserved = set(target_ids) | set(subtree_ids)
    replacements: dict[str, str] = {}
    for item_id in subtree_ids:
        if item_id not in target_ids:
            continue
        base = f"{item_id}-adopted"
        candidate = base
        suffix = 2
        while candidate in reserved:
            candidate = f"{base}-{suffix}"
            suffix += 1
        replacements[item_id] = candidate
        reserved.add(candidate)

    if not replacements:
        return replacements

    def rewrite_url(match: re.Match[str]) -> str:
        quote, ref_id = match.group(1), match.group(2)
        return f"url({quote}#{replacements.get(ref_id, ref_id)}{quote})"

    for item in element.iter():
        item_id = item.get("id")
        if item_id in replacements:
            item.set("id", replacements[item_id])
        for name, value in list(item.attrib.items()):
            rewritten = _URL_ID_RE.sub(rewrite_url, value)
            if _local_name(name) == "href" and value.startswith("#"):
                rewritten = "#" + replacements.get(value[1:], value[1:])
            if rewritten != value:
                item.set(name, rewritten)
    return replacements


def _adopted_definition_clones(
    source_root: ET.Element,
    source_element: ET.Element,
) -> list[ET.Element]:
    """Copy the transitive source definitions referenced by one adopted object."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from svg_authoring_view import (
        _ADOPT_INHERITED_ATTRIBUTES,
        _definition_closure,
        _element_chain,
    )
    context_elements = [source_element]
    for ancestor in _element_chain(source_root, source_element)[:-1]:
        context = ET.Element(ancestor.tag)
        for name in (*_ADOPT_INHERITED_ATTRIBUTES, "opacity", "style"):
            value = ancestor.get(name)
            if value is not None:
                context.set(name, value)
        context_elements.append(context)
    definitions, selected_owners = _definition_closure(
        source_root,
        context_elements,
    )
    return [
        copy.deepcopy(child)
        for definitions_root in definitions
        for child in definitions_root
        if id(child) in selected_owners
    ]


def _append_adopted_definitions(
    target_root: ET.Element,
    definitions: list[ET.Element],
) -> None:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from svg_authoring_view import (
        SVG_NS,
        _local_name,
    )
    if not definitions:
        return
    target_defs = next(
        (
            child
            for child in target_root
            if _local_name(child.tag) == "defs"
        ),
        None,
    )
    if target_defs is None:
        target_defs = ET.Element(f"{{{SVG_NS}}}defs")
        target_root.insert(0, target_defs)
    target_defs.extend(definitions)


def _authoring_source_document_ids(
    authoring_dir: Path,
    manifest: dict[str, object],
    authoring_name: str,
) -> set[str]:
    """Return ids hidden behind source refs in one authoring document."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from svg_authoring_view import (
        _authoring_page_path,
        _parse_authoring_svg,
    )
    documents = manifest.get("documents")
    if not isinstance(documents, list):
        return set()
    document = next(
        (
            item
            for item in documents
            if isinstance(item, dict)
            and item.get("authoring") == authoring_name
        ),
        None,
    )
    source_root_value = manifest.get("source_root")
    source_name = document.get("source") if document is not None else None
    if not isinstance(source_root_value, str) or not isinstance(source_name, str):
        return set()
    source_dir = (authoring_dir / source_root_value).resolve()
    source_path = _authoring_page_path(
        source_dir,
        source_name,
        label="--adopt-object target source",
    )
    source_root = _parse_authoring_svg(source_path)
    return {
        item_id
        for item in source_root.iter()
        if (item_id := item.get("id"))
    }


def _strip_adopted_transport(element: ET.Element) -> tuple[set[str], int]:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from svg_authoring_view import (
        ADOPT_OBJECT_STRIPPED_ATTRIBUTES,
        SEMANTIC_OBJECT_ATTRIBUTE,
        SEMANTIC_SHAPE_KIND,
        _local_name,
    )
    stripped: set[str] = set()
    removed_metadata = 0

    def visit(parent: ET.Element) -> None:
        nonlocal removed_metadata
        for child in list(parent):
            original_part = child.get("data-pptx-part")
            has_transport = any(
                name in ADOPT_OBJECT_STRIPPED_ATTRIBUTES
                for name in child.attrib
            )
            is_json_metadata = (
                _local_name(child.tag) == "metadata"
                and child.get("type") == "application/json"
            )
            if (
                _local_name(child.tag) == "metadata"
                and not is_json_metadata
                and (original_part is not None or has_transport)
            ):
                stripped.update(
                    name
                    for name in child.attrib
                    if name in ADOPT_OBJECT_STRIPPED_ATTRIBUTES
                )
                parent.remove(child)
                removed_metadata += 1
                continue
            visit(child)

    visit(element)
    for item in element.iter():
        for name in list(item.attrib):
            if name not in ADOPT_OBJECT_STRIPPED_ATTRIBUTES:
                continue
            stripped.add(name)
            item.attrib.pop(name, None)
        if item.get(SEMANTIC_OBJECT_ATTRIBUTE) == SEMANTIC_SHAPE_KIND:
            stripped.add(SEMANTIC_OBJECT_ATTRIBUTE)
            item.attrib.pop(SEMANTIC_OBJECT_ATTRIBUTE, None)
    return stripped, removed_metadata


def _write_svg_atomically(path: Path, root: ET.Element) -> None:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from svg_authoring_view import (
        _serialize_svg,
    )
    payload = _serialize_svg(root)
    if not payload.endswith(b"\n"):
        payload += b"\n"
    with tempfile.NamedTemporaryFile(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
        delete=False,
    ) as handle:
        temporary_path = Path(handle.name)
        handle.write(payload)
    try:
        temporary_path.chmod(path.stat().st_mode)
        temporary_path.replace(path)
    except OSError:
        temporary_path.unlink(missing_ok=True)
        raise


