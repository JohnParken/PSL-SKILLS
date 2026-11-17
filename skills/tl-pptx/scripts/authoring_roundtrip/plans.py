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
    AuthoringDocument,
    RoundtripPage,
    VectorAssetRecord,
)

def _load_page_plan(
    project_path: Path,
    authoring_dir: Path,
    documents: dict[str, AuthoringDocument],
) -> tuple[tuple[RoundtripPage, ...], bool]:
    """Load the optional strict page plan or return the identity page order."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from authoring_roundtrip import (
        AuthoringRoundtripError,
        ROUNDTRIP_PAGE_PLAN_SCHEMA,
        RoundtripPage,
        _identity_pages,
        _load_json,
        _resolve_inside,
    )
    plan_path = project_path / ROUNDTRIP_PAGE_PLAN_PATH
    if not plan_path.exists():
        actual_files = {
            path.relative_to(authoring_dir).as_posix()
            for path in authoring_dir.rglob("*.svg")
            if path.is_file()
        }
        if actual_files != set(documents):
            raise AuthoringRoundtripError(
                "Authoring manifest/file roster differs; missing="
                f"{sorted(set(documents) - actual_files)}, "
                f"extra={sorted(actual_files - set(documents))}"
            )
        return _identity_pages(documents), False
    if not plan_path.is_file():
        raise AuthoringRoundtripError(
            f"Round-trip page plan must be a file: {plan_path}"
        )
    payload = _load_json(plan_path, context="round-trip page plan")
    unknown_root_fields = sorted(set(payload) - {"schema", "pages"})
    if unknown_root_fields:
        raise AuthoringRoundtripError(
            "Round-trip page plan has unsupported field(s): "
            + ", ".join(unknown_root_fields)
        )
    if payload.get("schema") != ROUNDTRIP_PAGE_PLAN_SCHEMA:
        raise AuthoringRoundtripError(
            "Unsupported round-trip page plan schema: "
            f"{payload.get('schema')!r}; expected {ROUNDTRIP_PAGE_PLAN_SCHEMA!r}"
        )
    raw_pages = payload.get("pages")
    if not isinstance(raw_pages, list) or not raw_pages:
        raise AuthoringRoundtripError(
            "Round-trip page plan pages must be a non-empty array"
        )

    documents_by_slide = {
        document.source_slide: document
        for document in documents.values()
    }
    source_count = len(documents_by_slide)
    pages: list[RoundtripPage] = []
    seen_svg_names: dict[str, int] = {}
    for output_index, raw in enumerate(raw_pages, start=1):
        context = f"page_plan.json pages[{output_index - 1}]"
        if not isinstance(raw, dict):
            raise AuthoringRoundtripError(f"{context} must be an object")
        unknown_fields = sorted(set(raw) - {"source_slide", "svg"})
        if unknown_fields:
            raise AuthoringRoundtripError(
                f"{context} has unsupported field(s): "
                + ", ".join(unknown_fields)
            )
        source_slide = raw.get("source_slide")
        if (
            not isinstance(source_slide, int)
            or isinstance(source_slide, bool)
            or source_slide < 1
            or source_slide > source_count
        ):
            raise AuthoringRoundtripError(
                f"{context}.source_slide must be between 1 and {source_count}"
            )
        source_document = documents_by_slide[source_slide]
        svg_value = raw.get("svg", source_document.name)
        if not isinstance(svg_value, str) or not svg_value:
            raise AuthoringRoundtripError(
                f"{context}.svg must be a non-empty authoring SVG filename"
            )
        if "\\" in svg_value:
            raise AuthoringRoundtripError(
                f"{context}.svg must not contain backslashes: {svg_value!r}"
            )
        svg_path_value = Path(svg_value)
        if (
            svg_path_value.name != svg_value
            or svg_path_value.suffix.lower() != ".svg"
            or svg_value in {".", ".."}
        ):
            raise AuthoringRoundtripError(
                f"{context}.svg must be one authoring-svg-flat SVG filename"
            )
        svg_key = svg_value.casefold()
        if svg_key in seen_svg_names:
            raise AuthoringRoundtripError(
                f"{context}.svg repeats {svg_value!r}; already used by output "
                f"page {seen_svg_names[svg_key]}"
            )
        authoring_path = _resolve_inside(
            authoring_dir,
            svg_value,
            context=f"{context}.svg",
        )
        if not authoring_path.is_file():
            raise AuthoringRoundtripError(
                f"{context}.svg names an unknown authoring SVG file: {svg_value}"
            )
        canonical_owner = documents.get(svg_value)
        if (
            canonical_owner is not None
            and canonical_owner.source_slide != source_slide
        ):
            raise AuthoringRoundtripError(
                f"{context}.svg {svg_value!r} belongs to source slide "
                f"{canonical_owner.source_slide}, not {source_slide}"
            )

        pages.append(RoundtripPage(
            output_index=output_index,
            source_slide=source_slide,
            svg_name=svg_value,
            source_svg_name=source_document.name,
        ))
        seen_svg_names[svg_key] = output_index

    expected_files = set(documents) | {page.svg_name for page in pages}
    actual_files = {
        path.relative_to(authoring_dir).as_posix()
        for path in authoring_dir.rglob("*.svg")
        if path.is_file()
    }
    if actual_files != expected_files:
        raise AuthoringRoundtripError(
            "Authoring manifest/page-plan file roster differs; missing="
            f"{sorted(expected_files - actual_files)}, "
            f"extra={sorted(actual_files - expected_files)}"
        )
    return tuple(pages), True


def _output_documents(
    pages: tuple[RoundtripPage, ...],
    documents: dict[str, AuthoringDocument],
    authoring_dir: Path,
) -> tuple[AuthoringDocument, ...]:
    """Bind every output authoring file to its declared source document."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from authoring_roundtrip import (
        AuthoringDocument,
    )
    documents_by_slide = {
        document.source_slide: document
        for document in documents.values()
    }
    output: list[AuthoringDocument] = []
    for page in pages:
        source = documents_by_slide[page.source_slide]
        output.append(AuthoringDocument(
            name=page.svg_name,
            source_name=source.name,
            source_slide=page.source_slide,
            authoring_path=(authoring_dir / page.svg_name).resolve(),
            flat_source_path=source.flat_source_path,
            layered_source_path=source.layered_source_path,
            initial_authoring_sha256=source.initial_authoring_sha256,
            source_refs=source.source_refs,
        ))
    return tuple(output)


def _load_current_assets(
    project_path: Path,
    authoring_dir: Path,
    documents: dict[str, AuthoringDocument],
) -> tuple[dict[str, VectorAssetRecord], dict[str, Any] | None]:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from authoring_roundtrip import (
        AuthoringRoundtripError,
        IMPORTED_ICON_NAMESPACE,
        VectorAssetRecord,
        _load_json,
        _resolve_inside,
        _sha256_file,
    )
    inventory_path = project_path / f"{authoring_dir.name}_vector_asset_inventory.json"
    if not inventory_path.is_file():
        return {}, None
    inventory = _load_json(inventory_path, context="flat vector asset inventory")
    if inventory.get("schema") != VECTOR_INVENTORY_SCHEMA:
        raise AuthoringRoundtripError(
            f"Unsupported vector inventory schema: {inventory.get('schema')!r}"
        )
    if inventory.get("icon_namespace") != IMPORTED_ICON_NAMESPACE:
        raise AuthoringRoundtripError(
            "Flat authoring vector inventory must use icon_namespace='imported'"
        )
    if inventory.get("asset_role") != DECORATION_ASSET_ROLE:
        raise AuthoringRoundtripError(
            "Flat authoring vector inventory must use asset_role='decoration'"
        )
    assets_raw = inventory.get("assets")
    if not isinstance(assets_raw, list):
        raise AuthoringRoundtripError("Vector inventory assets must be an array")
    icons_root = project_path / "icons"
    records: dict[str, VectorAssetRecord] = {}
    for index, raw in enumerate(assets_raw):
        if not isinstance(raw, dict):
            raise AuthoringRoundtripError(f"vector assets[{index}] must be an object")
        values = {
            name: raw.get(name)
            for name in (
                "icon",
                "asset",
                "svg",
                "source_sha256",
                "asset_sha256",
            )
        }
        if not all(isinstance(value, str) and value for value in values.values()):
            raise AuthoringRoundtripError(
                f"vector assets[{index}] is missing a required string field"
            )
        if raw.get("role") != DECORATION_ASSET_ROLE:
            raise AuthoringRoundtripError(
                f"vector assets[{index}] is not a decoration asset"
            )
        icon = str(values["icon"])
        origin = str(values["svg"])
        if icon in records:
            raise AuthoringRoundtripError(f"Duplicate vector asset id: {icon}")
        if origin not in documents:
            raise AuthoringRoundtripError(
                f"Vector asset {icon!r} names unknown document {origin!r}"
            )
        asset_path = _resolve_inside(
            icons_root,
            str(values["asset"]),
            context=f"vector asset {icon!r}",
        )
        if not asset_path.is_file() or asset_path.suffix.lower() != ".svg":
            raise AuthoringRoundtripError(f"Vector asset is missing: {asset_path}")
        try:
            asset_root = ET.parse(asset_path).getroot()
        except ET.ParseError as exc:
            raise AuthoringRoundtripError(
                f"Vector asset is invalid SVG XML: {asset_path}: {exc}"
            ) from exc
        if asset_root.get(ASSET_ROLE_ATTRIBUTE) != DECORATION_ASSET_ROLE:
            raise AuthoringRoundtripError(
                f"Vector asset is not declared as decoration: {asset_path}"
            )
        records[icon] = VectorAssetRecord(
            icon=icon,
            asset_path=asset_path,
            origin_document=origin,
            source_sha256=str(values["source_sha256"]),
            expected_asset_sha256=str(values["asset_sha256"]),
            actual_asset_sha256=_sha256_file(asset_path),
        )
    if inventory.get("asset_count") != len(records):
        raise AuthoringRoundtripError("Vector inventory asset_count is stale")
    return records, inventory


def _baseline_assets(
    entries: list[dict[str, Any]],
    icons_root: Path,
) -> dict[str, VectorAssetRecord]:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from authoring_roundtrip import (
        AuthoringRoundtripError,
        VectorAssetRecord,
        _sha256_file,
    )
    records: dict[str, VectorAssetRecord] = {}
    for index, raw in enumerate(entries):
        values = {
            name: raw.get(name)
            for name in (
                "icon",
                "asset",
                "svg",
                "source_sha256",
                "asset_sha256",
            )
        }
        if not all(isinstance(value, str) and value for value in values.values()):
            raise AuthoringRoundtripError(
                f"Generated baseline vector asset {index} is incomplete"
            )
        if raw.get("role") != DECORATION_ASSET_ROLE:
            raise AuthoringRoundtripError(
                f"Generated baseline vector asset {index} is not decoration"
            )
        icon = str(values["icon"])
        asset_path = icons_root / str(values["asset"])
        try:
            asset_root = ET.parse(asset_path).getroot()
        except ET.ParseError as exc:
            raise AuthoringRoundtripError(
                f"Generated baseline vector asset is invalid: {asset_path}: {exc}"
            ) from exc
        if asset_root.get(ASSET_ROLE_ATTRIBUTE) != DECORATION_ASSET_ROLE:
            raise AuthoringRoundtripError(
                f"Generated baseline vector asset lacks decoration role: {asset_path}"
            )
        actual_sha = _sha256_file(asset_path)
        records[icon] = VectorAssetRecord(
            icon=icon,
            asset_path=asset_path,
            origin_document=str(values["svg"]),
            source_sha256=str(values["source_sha256"]),
            expected_asset_sha256=actual_sha,
            actual_asset_sha256=actual_sha,
        )
    return records


def _generate_baseline_bundle(
    project_path: Path,
    source_root: Path,
    source_proxy_dir: Path,
    documents: dict[str, AuthoringDocument],
    inventory: dict[str, Any] | None,
) -> tuple[tempfile.TemporaryDirectory[str], Path, dict[str, VectorAssetRecord]]:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from authoring_roundtrip import (
        AuthoringRoundtripError,
        IMPORTED_ICON_NAMESPACE,
        _baseline_assets,
    )
    temporary = tempfile.TemporaryDirectory(
        prefix=".authoring-baseline-",
        dir=project_path,
    )
    baseline_root = Path(temporary.name)
    mapping = [
        (document.flat_source_path, baseline_root / document.name)
        for document in sorted(documents.values(), key=lambda item: item.name)
    ]
    try:
        project_svg_batch(
            mapping,
            source_root,
            baseline_root,
            force=False,
            projection_kind="flat",
            source_proxy_dir=source_proxy_dir,
            publish_source_proxies=False,
        )
        if inventory is None:
            return temporary, baseline_root, {}
        min_drawables = inventory.get("min_drawables")
        min_bytes = inventory.get("min_bytes")
        min_decoration_bytes = inventory.get("min_decoration_bytes")
        if not all(
            isinstance(value, int) and not isinstance(value, bool) and value >= 0
            for value in (min_drawables, min_bytes, min_decoration_bytes)
        ):
            raise AuthoringRoundtripError(
                "Flat vector inventory extraction thresholds are invalid"
            )
        icon_namespace = inventory.get("icon_namespace")
        if icon_namespace != IMPORTED_ICON_NAMESPACE:
            raise AuthoringRoundtripError(
                "Flat vector inventory must use icon_namespace='imported'"
            )
        if inventory.get("asset_role") != DECORATION_ASSET_ROLE:
            raise AuthoringRoundtripError(
                "Flat vector inventory must use asset_role='decoration'"
            )
        baseline_icons = baseline_root / "_baseline_icons"
        entries: list[dict[str, Any]] = []
        for document in sorted(documents.values(), key=lambda item: item.name):
            entries.extend(
                extract_file(
                    baseline_root / document.name,
                    baseline_icons,
                    IMPORTED_ICON_NAMESPACE,
                    int(min_drawables),
                    int(min_bytes),
                    int(min_decoration_bytes),
                    True,
                    "baseline",
                    None,
                    None,
                )
            )
        return temporary, baseline_root, _baseline_assets(entries, baseline_icons)
    except Exception:
        temporary.cleanup()
        raise


