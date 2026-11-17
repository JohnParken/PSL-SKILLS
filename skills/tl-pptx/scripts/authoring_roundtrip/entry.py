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
    AuthoringRoundtripResult,
)

def materialize_flat_authoring_roundtrip(
    project_path: Path,
    authoring_dir: Path,
    output_dir: Path,
) -> AuthoringRoundtripResult:
    """Materialize a flat authoring bundle into preserve-mode slide SVGs."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from authoring_roundtrip import (
        AuthoringRoundtripError,
        AuthoringRoundtripResult,
        ROUNDTRIP_PAGE_PLAN_SCHEMA,
        _generate_baseline_bundle,
        _load_current_assets,
        _load_documents,
        _load_page_plan,
        _materialize_document,
        _output_documents,
    )
    project_path = project_path.resolve()
    authoring_dir = authoring_dir.resolve()
    output_dir = output_dir.resolve()
    try:
        authoring_dir.relative_to(project_path)
        output_dir.relative_to(project_path)
    except ValueError as exc:
        raise AuthoringRoundtripError(
            "Authoring and materialized directories must stay inside the project"
        ) from exc
    if not authoring_dir.is_dir():
        raise AuthoringRoundtripError(
            f"Authoring directory does not exist: {authoring_dir}"
        )
    if output_dir.exists() and any(output_dir.iterdir()):
        raise AuthoringRoundtripError(
            f"Materialized output directory must be empty: {output_dir}"
        )
    output_dir.mkdir(parents=True, exist_ok=True)

    (
        source_root,
        source_proxy_dir,
        documents,
        manifest,
        _roundtrip_manifest,
    ) = _load_documents(
        project_path,
        authoring_dir,
    )
    pages, page_plan_present = _load_page_plan(
        project_path,
        authoring_dir,
        documents,
    )
    output_documents = _output_documents(pages, documents, authoring_dir)
    current_assets, inventory = _load_current_assets(
        project_path,
        authoring_dir,
        documents,
    )
    baseline_temporary, baseline_root, baseline_assets = _generate_baseline_bundle(
        project_path,
        source_root,
        source_proxy_dir,
        documents,
        inventory,
    )
    try:
        current_keys = sorted(record.baseline_key for record in current_assets.values())
        baseline_keys = sorted(record.baseline_key for record in baseline_assets.values())
        if current_keys != baseline_keys:
            raise AuthoringRoundtripError(
                "Regenerated flat vector extraction differs from its inventory; "
                f"missing={sorted(set(current_keys) - set(baseline_keys))}, "
                f"extra={sorted(set(baseline_keys) - set(current_keys))}"
            )
        document_reports = [
            _materialize_document(
                project_path,
                document,
                baseline_root / document.source_name,
                output_dir / document.name,
                current_assets,
                baseline_assets,
                page=page,
                pages=pages,
            )
            for page, document in zip(pages, output_documents)
        ]
    finally:
        baseline_temporary.cleanup()

    svg_files = (
        tuple(output_dir / page.svg_name for page in pages)
        if page_plan_present
        else tuple(discover_slide_svgs(output_dir))
    )
    if len(svg_files) != len(pages):
        raise AuthoringRoundtripError(
            "Materialized slide roster does not match the output page plan"
        )
    totals = {
        key: sum(int(item[key]) for item in document_reports)
        for key in (
            "source_ref_count",
            "unchanged_refs",
            "edited_refs",
            "deleted_refs",
            "authored_unreferenced",
            "expanded_vector_uses",
        )
    }
    report: dict[str, Any] = {
        "schema": "ppt-master.authoring-roundtrip-materialization.v1",
        "authoring_root": str(authoring_dir),
        "manifest_schema": manifest.get("schema"),
        "projection_kind": manifest.get("projection_kind"),
        "materialized_slide_count": len(svg_files),
        "page_plan": (
            {
                "schema": ROUNDTRIP_PAGE_PLAN_SCHEMA,
                "output_pages": len(pages),
                "pages": [
                    {
                        "output_index": page.output_index,
                        "source_slide": page.source_slide,
                        "svg": page.svg_name,
                    }
                    for page in pages
                ],
            }
            if page_plan_present
            else None
        ),
        "totals": totals,
        "documents": document_reports,
    }
    return AuthoringRoundtripResult(
        svg_files=svg_files,
        authoring_files=tuple(
            authoring_dir / page.svg_name
            for page in pages
        ),
        pages=pages,
        page_plan_present=page_plan_present,
        report=report,
    )
