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

def _load_authoring_summary_manifest(
    authoring_dir: Path,
) -> tuple[dict[str, object], list[str], dict[str, int] | None]:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from svg_authoring_view import (
        AUTHORING_MANIFEST_NAME,
        AUTHORING_SCHEMA,
    )
    manifest_path = authoring_dir / AUTHORING_MANIFEST_NAME
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValueError(
            f"Authoring manifest not found: {manifest_path}"
        ) from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"Cannot decode authoring manifest {manifest_path}: {exc}"
        ) from exc
    if not isinstance(manifest, dict) or manifest.get("schema") != AUTHORING_SCHEMA:
        raise ValueError(
            f"Unsupported authoring manifest schema in {manifest_path}"
        )
    documents = manifest.get("documents")
    if not isinstance(documents, list):
        raise ValueError(
            f"Authoring manifest documents must be an array: {manifest_path}"
        )

    names: list[str] = []
    for index, document in enumerate(documents):
        if not isinstance(document, dict):
            raise ValueError(
                f"Authoring manifest documents[{index}] must be an object"
            )
        name = document.get("authoring")
        relative = Path(name) if isinstance(name, str) else Path()
        if (
            not isinstance(name, str)
            or not name
            or relative.is_absolute()
            or any(part in {"", ".", ".."} for part in relative.parts)
            or relative.suffix.lower() != ".svg"
        ):
            raise ValueError(
                f"Authoring manifest documents[{index}].authoring is invalid"
            )
        names.append(name)
    if len(names) != len(set(names)):
        raise ValueError("Authoring manifest contains duplicate document names")

    from pptx_workspace import ROUNDTRIP_PAGE_PLAN_PATH

    project_path = authoring_dir.parent
    if (project_path / ROUNDTRIP_PAGE_PLAN_PATH).exists():
        from authoring_roundtrip import (
            AuthoringRoundtripError,
            _load_documents,
            _load_page_plan,
        )

        try:
            _, _, roundtrip_documents, _, _ = _load_documents(
                project_path,
                authoring_dir,
            )
            pages, _ = _load_page_plan(
                project_path,
                authoring_dir,
                roundtrip_documents,
            )
        except AuthoringRoundtripError as exc:
            raise ValueError(
                f"Cannot build page-plan-aware authoring summary: {exc}"
            ) from exc
        source_slides = {
            name: document.source_slide
            for name, document in roundtrip_documents.items()
        }
        source_slides.update({
            page.svg_name: page.source_slide
            for page in pages
        })
        return manifest, sorted(source_slides), source_slides

    actual_names = sorted(
        path.relative_to(authoring_dir).as_posix()
        for path in authoring_dir.rglob("*.svg")
        if path.is_file()
    )
    if sorted(names) != actual_names:
        raise ValueError(
            "Authoring manifest/file roster differs while building summary"
        )
    return manifest, sorted(names), None


def _authoring_summary_bytes(authoring_dir: Path) -> bytes:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from svg_authoring_view import (
        AUTHORING_MANIFEST_NAME,
        AUTHORING_SUMMARY_SCHEMA,
        SOURCE_PROXY_ATTRIBUTE,
        SOURCE_PROXY_KIND,
        _authoring_summary_document,
        _load_authoring_summary_manifest,
    )
    (
        manifest,
        document_names,
        source_slides,
    ) = _load_authoring_summary_manifest(authoring_dir)
    documents = [
        _authoring_summary_document(
            authoring_dir / name,
            name,
            source_slide=(
                source_slides[name]
                if source_slides is not None
                else None
            ),
        )
        for name in document_names
    ]
    total_icon_assets = {
        icon_name
        for document in documents
        for icon_name in document["icon_refs"]
    }
    totals = {
        "svg_bytes": sum(int(document["bytes"]) for document in documents),
        "elements": sum(int(document["elements"]) for document in documents),
        "top_level_elements": sum(
            int(document["top_level_elements"])
            for document in documents
        ),
        "drawables": sum(int(document["drawables"]) for document in documents),
        "text_elements": sum(
            int(document["text_elements"])
            for document in documents
        ),
        "text_characters": sum(
            int(document["text_characters"])
            for document in documents
        ),
        "images": sum(int(document["images"]) for document in documents),
        "icon_uses": sum(int(document["icon_uses"]) for document in documents),
        "unique_icon_assets": len(total_icon_assets),
        "placeholders": sum(
            int(document["placeholders"])
            for document in documents
        ),
        "inline_source_refs": sum(
            int(document["inline_source_refs"])
            for document in documents
        ),
        "source_proxies": sum(
            int(document["source_proxies"])
            for document in documents
        ),
        "semantic_tables": sum(
            int(document["semantic_tables"])
            for document in documents
        ),
        "semantic_shapes": sum(
            int(document["semantic_shapes"])
            for document in documents
        ),
        "machine_source_refs": manifest.get("source_ref_count"),
    }
    payload = {
        "schema": AUTHORING_SUMMARY_SCHEMA,
        "projection_kind": manifest.get("projection_kind"),
        "authoring_root": ".",
        "machine_manifest": AUTHORING_MANIFEST_NAME,
        "machine_manifest_policy": "tool-only; do not load into model context",
        "file_count": len(documents),
        "totals": totals,
        "documents": documents,
    }
    if totals["source_proxies"]:
        payload["source_proxy_policy"] = {
            "marker": f'{SOURCE_PROXY_ATTRIBUTE}="{SOURCE_PROXY_KIND}"',
            "purpose": (
                "Atomic visual preview of an immutable source-backed "
                "PowerPoint object"
            ),
            "editing": (
                "Keep unchanged to restore the native object; a Slide-local "
                "proxy may be removed to delete it; inherited proxies must "
                "remain; do not edit the proxy or its asset"
            ),
        }
    return (
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")


def write_authoring_summary(authoring_dir: Path) -> Path:
    """Regenerate the model-readable summary from the current authoring SVGs."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from svg_authoring_view import (
        AUTHORING_SUMMARY_NAME,
        _authoring_summary_bytes,
    )
    authoring_dir = Path(authoring_dir).resolve()
    if not authoring_dir.is_dir():
        raise ValueError(f"Authoring directory not found: {authoring_dir}")
    payload = _authoring_summary_bytes(authoring_dir)
    summary_path = authoring_dir / AUTHORING_SUMMARY_NAME
    with tempfile.NamedTemporaryFile(
        prefix=f".{AUTHORING_SUMMARY_NAME}.",
        suffix=".tmp",
        dir=authoring_dir,
        delete=False,
    ) as handle:
        temporary_path = Path(handle.name)
        handle.write(payload)
    try:
        temporary_path.chmod(0o644)
        temporary_path.replace(summary_path)
    except OSError:
        temporary_path.unlink(missing_ok=True)
        raise
    return summary_path


