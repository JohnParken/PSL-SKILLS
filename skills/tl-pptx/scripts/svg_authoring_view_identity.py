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
    SourceReference,
)

def _serialize_svg(element: ET.Element) -> bytes:
    """Serialize SVG independently of namespace registrations from other tools."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from svg_authoring_view import (
        SVG_NS,
        XLINK_NS,
    )
    ET.register_namespace("", SVG_NS)
    ET.register_namespace("xlink", XLINK_NS)
    return ET.tostring(element, encoding="utf-8", xml_declaration=False)

# These fields identify source OOXML objects or carry opaque paint/effect
# payloads. They belong in the complete import SVG, not its lightweight view.
def _local_name(name: object) -> str:
    return name.rsplit("}", 1)[-1] if isinstance(name, str) else ""


def semantic_shape_text_component(
    shape: ET.Element,
    *,
    included_text_ids: set[int] | None = None,
) -> ET.Element | None:
    """Validate text compiled into one semantic shape, excluding native restores."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from svg_authoring_view import (
        SVG_NS,
    )
    def included(element: ET.Element) -> bool:
        return (
            element.tag.replace(f'{{{SVG_NS}}}', '') == 'text'
            and (included_text_ids is None or id(element) in included_text_ids)
        )

    texts = [child for child in shape if included(child)]
    nested_texts = [child for child in shape.iter() if included(child)]
    if nested_texts != texts:
        raise ValueError('Semantic shape text must be one direct SVG text component')
    if not texts:
        return None
    if len(texts) != 1:
        raise ValueError('Semantic shape requires at most one paragraph-based text component')
    if shape.get('data-pptx-frame') is None:
        raise ValueError('Semantic shape text requires data-pptx-frame on its owner')
    return texts[0]


def _semantic_text(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    return value


def _stable_tag_name(tag: object) -> str:
    if isinstance(tag, str):
        return tag
    if tag is ET.Comment:
        return "#comment"
    if tag is ET.ProcessingInstruction:
        return "#processing-instruction"
    raise ValueError(f"Unsupported XML node type in source object: {tag!r}")


def semantic_subtree_sha256(
    element: ET.Element,
    *,
    ignored_attributes: frozenset[str] = frozenset(),
) -> str:
    """Hash parsed SVG semantics without attribute order or indentation noise."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from svg_authoring_view import (
        _semantic_text,
        _stable_tag_name,
    )
    digest = hashlib.sha256()

    def visit(item: ET.Element) -> None:
        digest.update(_stable_tag_name(item.tag).encode("utf-8"))
        for name, value in sorted(item.attrib.items()):
            if name in ignored_attributes:
                continue
            digest.update(b"\0a")
            digest.update(name.encode("utf-8"))
            digest.update(b"\0")
            digest.update(value.encode("utf-8"))
        text = _semantic_text(item.text)
        if text is not None:
            digest.update(b"\0t")
            digest.update(text.encode("utf-8"))
        for child in item:
            digest.update(b"\0c")
            visit(child)
            tail = _semantic_text(child.tail)
            if tail is not None:
                digest.update(b"\0l")
                digest.update(tail.encode("utf-8"))
        digest.update(b"\0e")

    visit(element)
    return digest.hexdigest()


def _iter_element_paths(
    root: ET.Element,
) -> list[tuple[tuple[int, ...], ET.Element]]:
    indexed: list[tuple[tuple[int, ...], ET.Element]] = []

    def walk(element: ET.Element, path: tuple[int, ...]) -> None:
        indexed.append((path, element))
        for index, child in enumerate(element):
            walk(child, (*path, index))

    walk(root, ())
    return indexed


def _source_reference(element: ET.Element) -> str | None:
    if not element.get("id") or not element.get("data-pptx-object"):
        return None
    scope = element.get("data-pptx-shape-scope")
    shape_id = element.get("data-pptx-shape-id")
    if not scope or not shape_id:
        return None
    return f"{scope}:{shape_id}"


def _stamp_source_references(root: ET.Element) -> list[SourceReference]:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from svg_authoring_view import (
        SOURCE_REF_ATTRIBUTE,
        SourceReference,
        _iter_element_paths,
        _source_reference,
    )
    existing = [
        element
        for _, element in _iter_element_paths(root)
        if element.get(SOURCE_REF_ATTRIBUTE) is not None
    ]
    if existing:
        raise ValueError(
            f"Input already contains reserved {SOURCE_REF_ATTRIBUTE}; "
            "project from the lossless import SVG instead"
        )

    references: list[SourceReference] = []
    seen: set[str] = set()
    for path, element in _iter_element_paths(root):
        source_ref = _source_reference(element)
        if source_ref is None:
            continue
        if source_ref in seen:
            raise ValueError(f"Duplicate source object identity: {source_ref}")
        seen.add(source_ref)
        references.append(
            SourceReference(
                source_ref=source_ref,
                source_path=path,
            )
        )
        element.set(SOURCE_REF_ATTRIBUTE, source_ref)
    return references


def _index_initial_authoring_references(
    root: ET.Element,
    references: list[SourceReference],
) -> None:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from svg_authoring_view import (
        SOURCE_REF_ATTRIBUTE,
        semantic_subtree_sha256,
    )
    by_ref = {reference.source_ref: reference for reference in references}
    seen: set[str] = set()
    for element in root.iter():
        source_ref = element.get(SOURCE_REF_ATTRIBUTE)
        if source_ref is None:
            continue
        if source_ref in seen:
            raise ValueError(f"Duplicate authoring source reference: {source_ref}")
        reference = by_ref.get(source_ref)
        if reference is None:
            raise ValueError(f"Unknown authoring source reference: {source_ref}")
        seen.add(source_ref)
        reference.initial_authoring_subtree_sha256 = semantic_subtree_sha256(
            element,
            ignored_attributes=frozenset({SOURCE_REF_ATTRIBUTE}),
        )

    missing = sorted(set(by_ref) - seen)
    if missing:
        raise ValueError(
            "Authoring projection dropped source-referenced object(s): "
            + ", ".join(missing[:5])
        )


def _fresh_native_fallback_markers(root: ET.Element) -> set[ET.Element]:
    """Return native markers whose source fallback guard is currently valid."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from svg_authoring_view import (
        _SHA256_RE,
    )
    fresh: set[ET.Element] = set()
    for element in root.iter():
        expected = element.get(NATIVE_FALLBACK_SHA256_ATTR)
        if (
            expected is None
            or expected != expected.strip()
            or _SHA256_RE.fullmatch(expected) is None
        ):
            continue
        if svg_native_fallback_fingerprint(
            element,
            document_root=root,
        ) == expected.lower():
            fresh.add(element)
    return fresh


