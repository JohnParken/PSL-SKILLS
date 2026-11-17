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
)

def _portable_path(path: Path, base: Path) -> str:
    try:
        return os.path.relpath(path, base).replace(os.sep, "/")
    except ValueError:
        return path.resolve().as_uri()


def _authoring_manifest_bytes(
    reports: list[ProjectionReport],
    source_root: Path,
    output_dir: Path,
    projection_kind: str,
) -> bytes:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from svg_authoring_view import (
        AUTHORING_SCHEMA,
        SOURCE_REF_ATTRIBUTE,
        _portable_path,
    )
    documents = []
    for report in sorted(reports, key=lambda item: item.output.as_posix()):
        documents.append({
            "source": report.source.relative_to(source_root).as_posix(),
            "authoring": report.output.relative_to(output_dir).as_posix(),
            "source_sha256": report.source_sha256,
            "initial_authoring_sha256": report.initial_authoring_sha256,
            "source_refs": {
                reference.source_ref: reference.as_dict(output_dir)
                for reference in sorted(
                    report.source_references,
                    key=lambda item: item.source_ref,
                )
            },
        })

    payload = {
        "schema": AUTHORING_SCHEMA,
        "projection_kind": projection_kind,
        "source_root": _portable_path(source_root, output_dir),
        "authoring_root": ".",
        "source_ref_attribute": SOURCE_REF_ATTRIBUTE,
        "file_count": len(documents),
        "source_ref_count": sum(len(report.source_references) for report in reports),
        "documents": documents,
    }
    return (
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")


def _authoring_document_kind(path: Path) -> str:
    name = path.name
    if name.startswith("master_"):
        return "master"
    if name.startswith("layout_"):
        return "layout"
    if name.startswith("slide_"):
        return "slide"
    return "generic"


def _authoring_summary_document(
    path: Path,
    relative_name: str,
    *,
    source_slide: int | None = None,
) -> dict[str, object]:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from svg_authoring_view import (
        SEMANTIC_OBJECT_ATTRIBUTE,
        SEMANTIC_SHAPE_KIND,
        SEMANTIC_TABLE_KIND,
        SOURCE_PROXY_ATTRIBUTE,
        SOURCE_PROXY_KIND,
        SOURCE_REF_ATTRIBUTE,
        _DRAWABLE_TAGS,
        _authoring_document_kind,
        _local_name,
    )
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError) as exc:
        raise ValueError(f"Cannot summarize authoring SVG {path}: {exc}") from exc
    if _local_name(root.tag) != "svg":
        raise ValueError(f"Authoring document root is not <svg>: {path}")

    elements = list(root.iter())
    icon_references = sorted({
        icon_name
        for element in elements
        if (icon_name := element.get("data-icon"))
    })
    text_elements = [
        element for element in elements
        if _local_name(element.tag) == "text"
    ]
    summary: dict[str, object] = {
        "file": relative_name,
        "kind": _authoring_document_kind(path),
    }
    if source_slide is not None:
        summary["source_slide"] = source_slide
    summary.update({
        "bytes": path.stat().st_size,
        "viewBox": root.get("viewBox"),
        "elements": len(elements),
        "top_level_elements": len(root),
        "drawables": sum(
            _local_name(element.tag) in _DRAWABLE_TAGS
            for element in elements
        ),
        "text_elements": len(text_elements),
        "text_characters": sum(
            len("".join(element.itertext()))
            for element in text_elements
        ),
        "images": sum(
            _local_name(element.tag) == "image"
            for element in elements
        ),
        "icon_uses": sum(
            element.get("data-icon") is not None
            for element in elements
        ),
        "icon_refs": icon_references,
        "placeholders": sum(
            element.get("data-pptx-placeholder") is not None
            for element in elements
        ),
        "inline_source_refs": sum(
            element.get(SOURCE_REF_ATTRIBUTE) is not None
            for element in elements
        ),
        "source_proxies": sum(
            element.get(SOURCE_PROXY_ATTRIBUTE) == SOURCE_PROXY_KIND
            for element in elements
        ),
        "semantic_tables": sum(
            element.get(SEMANTIC_OBJECT_ATTRIBUTE) == SEMANTIC_TABLE_KIND
            for element in elements
        ),
        "semantic_shapes": sum(
            element.get(SEMANTIC_OBJECT_ATTRIBUTE) == SEMANTIC_SHAPE_KIND
            for element in elements
        ),
    })
    return summary


