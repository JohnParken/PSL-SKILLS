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
)

def _compact_semantic_shapes(root: ET.Element, stats: ProjectionStats) -> None:
    """Collapse each semantic shape to one geometry carrier and one text body."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from svg_authoring_view import (
        SEMANTIC_OBJECT_ATTRIBUTE,
        SEMANTIC_SHAPE_KIND,
        _DRAWABLE_TAGS,
        _SEMANTIC_CARRIER_OWNER_ATTRIBUTES,
        _local_name,
        _make_geometry_carrier_visible,
        _merge_semantic_shape_texts,
        _normalize_semantic_text_lines,
        _remove_child,
    )
    for shape in root.iter():
        if shape.get(SEMANTIC_OBJECT_ATTRIBUTE) != SEMANTIC_SHAPE_KIND:
            continue
        carriers = [
            child
            for child in shape
            if child.get("data-pptx-part") == "geometry"
            and _local_name(child.tag) in _DRAWABLE_TAGS
        ]
        if len(carriers) != 1:
            raise ValueError("Semantic shape requires exactly one geometry carrier")
        carrier = carriers[0]
        stats.compatibility_normalizations += _make_geometry_carrier_visible(carrier)
        for name in list(carrier.attrib):
            if (
                name not in _SEMANTIC_CARRIER_OWNER_ATTRIBUTES
                and not name.startswith("data-pptx-av-")
            ):
                continue
            owner_value = shape.get(name)
            if owner_value is None or owner_value != carrier.get(name):
                raise ValueError(
                    f"Semantic shape owner/carrier values differ for {name}"
                )
            carrier.attrib.pop(name, None)
            stats.compatibility_normalizations += 1

        direct_texts = [
            child for child in shape
            if _local_name(child.tag) == "text"
        ]
        all_texts = [
            item for item in shape.iter()
            if _local_name(item.tag) == "text"
        ]
        for text in direct_texts:
            if _normalize_semantic_text_lines(text):
                stats.compatibility_normalizations += 1
        if len(all_texts) > 1:
            if direct_texts != all_texts:
                raise ValueError(
                    "Multi-paragraph semantic shape text must be direct children"
                )
            _merge_semantic_shape_texts(shape, direct_texts)

        for child in list(shape):
            if child is carrier:
                continue
            tag = _local_name(child.tag)
            part = child.get("data-pptx-part")
            if tag in _DRAWABLE_TAGS or part in {
                "geometry-detail",
                "geometry-preview",
            }:
                _remove_child(shape, child)
                stats.compatibility_normalizations += 1


def _append_tail(parent: ET.Element, index: int, tail: str | None) -> None:
    if not tail:
        return
    if index > 0:
        previous = list(parent)[index - 1]
        previous.tail = (previous.tail or "") + tail
    else:
        parent.text = (parent.text or "") + tail


def _remove_child(parent: ET.Element, child: ET.Element) -> None:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from svg_authoring_view import (
        _append_tail,
    )
    children = list(parent)
    index = children.index(child)
    tail = child.tail
    parent.remove(child)
    _append_tail(parent, index, tail)


def _unwrap_preview(parent: ET.Element, wrapper: ET.Element) -> bool:
    """Promote a marker-only preview wrapper without changing its geometry."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from svg_authoring_view import (
        _append_tail,
    )
    if wrapper.attrib or (wrapper.text and wrapper.text.strip()):
        return False

    siblings = list(parent)
    index = siblings.index(wrapper)
    promoted = list(wrapper)
    wrapper_tail = wrapper.tail
    for child in promoted:
        wrapper.remove(child)
    parent.remove(wrapper)

    for offset, child in enumerate(promoted):
        parent.insert(index + offset, child)

    if promoted:
        promoted[-1].tail = (promoted[-1].tail or "") + (wrapper_tail or "")
    else:
        _append_tail(parent, index, wrapper_tail)
    return True


def _strip_import_attributes(
    element: ET.Element,
    stats: ProjectionStats,
    *,
    geometry_owner: ET.Element | None = None,
    ctx: ConvertContext | None = None,
) -> None:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from svg_authoring_view import (
        AUTHORING_OMITTED_SOURCE_ATTRIBUTES,
        _local_name,
    )
    gradient_attributes = {
        NATIVE_GRADIENT_ATTR,
        NATIVE_GRADIENT_SHA256_ATTR,
        NATIVE_GRADIENT_PREVIEW_SHA256_ATTR,
    }
    preserve_gradient = False
    if _local_name(element.tag) in {"linearGradient", "radialGradient"}:
        preview = copy.deepcopy(element)
        for name in gradient_attributes:
            preview.attrib.pop(name, None)
        # Source gradients can need a preview outside the authored interface
        # (for example userSpaceOnUse for a collapsed outline). Keep the native
        # payload needed to export those previews without weakening that interface.
        preserve_gradient = bool(project_gradient_errors(preview))
    for name in list(element.attrib):
        if name not in AUTHORING_OMITTED_SOURCE_ATTRIBUTES:
            continue
        if preserve_gradient and name in gradient_attributes:
            continue
        if (
            geometry_owner is not None
            and (
                geometry_owner.get("data-pptx-object") == "connector"
                or is_stroked_zero_axis_shape(geometry_owner, ctx)
            )
            and element.get("data-pptx-part") == "geometry"
            and name in {"data-pptx-custgeom", "data-pptx-geometry-sha256"}
        ):
            # Collapsed stroked shapes and connectors need the source geometry
            # because the compact projection no longer rehydrates source refs.
            continue
        stats.source_attributes[name] += 1
        del element.attrib[name]


def _compact_text_runs(root: ET.Element, stats: ProjectionStats) -> None:
    """Remove redundant run paint while preserving directly editable text."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from svg_authoring_view import (
        _TSPAN_INHERITED_ATTRIBUTES,
        _local_name,
    )
    for text in root.iter():
        if _local_name(text.tag) != "text":
            continue
        for span in list(text):
            if _local_name(span.tag) != "tspan":
                continue
            for name in list(span.attrib):
                if (
                    name in _TSPAN_INHERITED_ATTRIBUTES
                    and span.get(name) == text.get(name)
                ):
                    del span.attrib[name]
                    stats.inherited_text_attributes_compacted += 1

        if len(text) != 1:
            continue
        span = text[0]
        if (
            _local_name(span.tag) != "tspan"
            or span.attrib
            or len(span)
            or text.text not in {None, ""}
            or span.tail not in {None, ""}
        ):
            continue
        text.text = span.text
        text.remove(span)
        stats.single_tspans_collapsed += 1


def _project_subtree(
    parent: ET.Element,
    stats: ProjectionStats,
    ctx: ConvertContext,
) -> None:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from svg_authoring_view import (
        SEMANTIC_OBJECT_ATTRIBUTE,
        SEMANTIC_SHAPE_KIND,
        _is_hidden_geometry_carrier,
        _local_name,
        _project_subtree,
        _remove_child,
        _strip_import_attributes,
        _unwrap_preview,
    )
    for child in list(parent):
        part = child.get("data-pptx-part")
        tag = _local_name(child.tag)

        if tag == "metadata" and part == "txbody":
            stats.txbody_metadata += 1
            _remove_child(parent, child)
            continue

        if (
            _is_hidden_geometry_carrier(child)
            and parent.get(SEMANTIC_OBJECT_ATTRIBUTE) != SEMANTIC_SHAPE_KIND
        ):
            stats.hidden_geometry_carriers += 1
            _remove_child(parent, child)
            continue

        _project_subtree(child, stats, ctx)
        _strip_import_attributes(child, stats, geometry_owner=parent, ctx=ctx)

        if part == "geometry-preview":
            child.attrib.pop("data-pptx-part", None)
            stats.compatibility_normalizations += len(
                normalize_single_child_group_filters(child)
            )
            if _unwrap_preview(parent, child):
                stats.geometry_preview_wrappers += 1
        elif part == "geometry-detail":
            child.attrib.pop("data-pptx-part", None)
            stats.geometry_detail_markers += 1


def _rewrite_asset_reference(value: str, source_dir: Path, output_dir: Path) -> str:
    if not value or value.startswith("#"):
        return value
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc or not parsed.path:
        return value

    resolved = (source_dir / parsed.path).resolve()
    try:
        relative = os.path.relpath(resolved, output_dir).replace(os.sep, "/")
    except ValueError:
        relative = resolved.as_uri()
    return urlunsplit(("", "", relative, parsed.query, parsed.fragment))


def _rewrite_asset_references(
    root: ET.Element,
    source_dir: Path,
    output_dir: Path,
    stats: ProjectionStats,
) -> None:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from svg_authoring_view import (
        XLINK_NS,
        _rewrite_asset_reference,
    )
    for element in root.iter():
        for name in ("href", f"{{{XLINK_NS}}}href"):
            current = element.get(name)
            if current is None:
                continue
            rewritten = _rewrite_asset_reference(current, source_dir, output_dir)
            if rewritten != current:
                element.set(name, rewritten)
                stats.asset_references_rewritten += 1


