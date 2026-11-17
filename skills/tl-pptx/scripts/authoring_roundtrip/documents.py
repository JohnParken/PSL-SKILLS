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
)

def _load_documents(
    project_path: Path,
    authoring_dir: Path,
) -> tuple[
    Path,
    Path,
    dict[str, AuthoringDocument],
    dict[str, Any],
    dict[str, Any],
]:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from authoring_roundtrip import (
        AuthoringDocument,
        AuthoringRoundtripError,
        SourceRefRecord,
        _load_json,
        _parse_svg,
        _resolve_inside,
        _roundtrip_source_svg_names,
        _sha256_file,
        _source_element,
        _source_identity,
    )
    manifest_path = authoring_dir / AUTHORING_MANIFEST_NAME
    manifest = _load_json(manifest_path, context="flat authoring manifest")
    if manifest.get("schema") != AUTHORING_SCHEMA:
        raise AuthoringRoundtripError(
            f"Unsupported authoring manifest schema: {manifest.get('schema')!r}"
        )
    if manifest.get("projection_kind") != "flat":
        raise AuthoringRoundtripError(
            "Authoring round-trip requires projection_kind='flat'"
        )
    if manifest.get("authoring_root") != ".":
        raise AuthoringRoundtripError(
            "authoring_manifest.json authoring_root must be '.'"
        )
    if manifest.get("source_ref_attribute") != SOURCE_REF_ATTRIBUTE:
        raise AuthoringRoundtripError(
            "authoring_manifest.json uses an unsupported source-ref attribute"
        )
    source_root_raw = manifest.get("source_root")
    if not isinstance(source_root_raw, str) or not source_root_raw.strip():
        raise AuthoringRoundtripError(
            "authoring_manifest.json source_root must be a non-empty string"
        )
    source_root = (authoring_dir / source_root_raw).resolve()
    try:
        source_root.relative_to(project_path)
    except ValueError as exc:
        raise AuthoringRoundtripError(
            "Flat authoring source_root must stay inside the import workspace"
        ) from exc
    if not source_root.is_dir():
        raise AuthoringRoundtripError(
            f"Flat authoring backing SVG directory is missing: {source_root}"
        )

    roundtrip_manifest = _load_json(
        project_path / ROUNDTRIP_MANIFEST_PATH,
        context="round-trip workspace manifest",
    )
    if roundtrip_manifest.get("schema") != "ppt-master.roundtrip-workspace.v1":
        raise AuthoringRoundtripError(
            "Unsupported round-trip workspace manifest schema: "
            f"{roundtrip_manifest.get('schema')!r}"
        )
    directories = roundtrip_manifest.get("directories")
    if not isinstance(directories, dict):
        raise AuthoringRoundtripError(
            "Round-trip workspace manifest directories must be an object"
        )
    flat_root_raw = directories.get("flatSvg")
    layered_root_raw = directories.get("layeredSvg")
    proxy_root_raw = directories.get("sourceObjectPreviews")
    if not isinstance(flat_root_raw, str) or not flat_root_raw:
        raise AuthoringRoundtripError(
            "Round-trip workspace manifest has no flat SVG backing directory"
        )
    if not isinstance(layered_root_raw, str) or not layered_root_raw:
        raise AuthoringRoundtripError(
            "Round-trip workspace manifest has no layered SVG backing directory"
        )
    if not isinstance(proxy_root_raw, str) or not proxy_root_raw:
        raise AuthoringRoundtripError(
            "Round-trip workspace manifest has no source-object preview directory"
        )
    flat_root = _resolve_inside(
        project_path,
        flat_root_raw,
        context="round-trip flat SVG backing directory",
    )
    layered_root = _resolve_inside(
        project_path,
        layered_root_raw,
        context="round-trip layered SVG backing directory",
    )
    proxy_root = _resolve_inside(
        project_path,
        proxy_root_raw,
        context="round-trip source-object preview directory",
    )
    if source_root != flat_root:
        raise AuthoringRoundtripError(
            "Authoring manifest source_root does not match the round-trip flat "
            "SVG backing directory"
        )
    if not layered_root.is_dir():
        raise AuthoringRoundtripError(
            f"Round-trip layered SVG backing directory is missing: {layered_root}"
        )

    source_svg_names = _roundtrip_source_svg_names(roundtrip_manifest)
    source_slide_by_name = {
        name: index
        for index, name in source_svg_names.items()
    }

    documents_raw = manifest.get("documents")
    if not isinstance(documents_raw, list):
        raise AuthoringRoundtripError(
            "authoring_manifest.json documents must be an array"
        )
    documents: dict[str, AuthoringDocument] = {}
    for index, raw in enumerate(documents_raw):
        if not isinstance(raw, dict):
            raise AuthoringRoundtripError(f"documents[{index}] must be an object")
        authoring_name = raw.get("authoring")
        source_name = raw.get("source")
        if not isinstance(authoring_name, str) or not authoring_name:
            raise AuthoringRoundtripError(
                f"documents[{index}].authoring must be a non-empty string"
            )
        if not isinstance(source_name, str) or not source_name:
            raise AuthoringRoundtripError(
                f"documents[{index}].source must be a non-empty string"
            )
        if authoring_name in documents:
            raise AuthoringRoundtripError(
                f"Duplicate authoring manifest document: {authoring_name}"
            )
        source_slide = source_slide_by_name.get(authoring_name)
        if source_slide is None:
            raise AuthoringRoundtripError(
                "Authoring manifest document is not registered by the round-trip "
                f"source roster: {authoring_name}"
            )
        authoring_path = _resolve_inside(
            authoring_dir,
            authoring_name,
            context=f"documents[{index}].authoring",
        )
        flat_source_path = _resolve_inside(
            source_root,
            source_name,
            context=f"documents[{index}].source",
        )
        layered_source_path = _resolve_inside(
            layered_root,
            source_name,
            context=f"documents[{index}] layered source",
        )
        for label, path in (
            ("authoring SVG", authoring_path),
            ("flat backing SVG", flat_source_path),
            ("layered backing SVG", layered_source_path),
        ):
            if not path.is_file() or path.suffix.lower() != ".svg":
                raise AuthoringRoundtripError(f"Missing {label}: {path}")
        expected_source_sha = raw.get("source_sha256")
        if not isinstance(expected_source_sha, str) or not expected_source_sha:
            raise AuthoringRoundtripError(
                f"documents[{index}].source_sha256 must be a non-empty string"
            )
        actual_source_sha = _sha256_file(flat_source_path)
        if actual_source_sha != expected_source_sha:
            raise AuthoringRoundtripError(
                f"Flat backing SVG changed: {flat_source_path.name}; expected "
                f"{expected_source_sha}, found {actual_source_sha}"
            )
        initial_authoring_sha = raw.get("initial_authoring_sha256")
        if not isinstance(initial_authoring_sha, str) or not initial_authoring_sha:
            raise AuthoringRoundtripError(
                f"documents[{index}].initial_authoring_sha256 must be a non-empty string"
            )

        refs_raw = raw.get("source_refs")
        if not isinstance(refs_raw, dict):
            raise AuthoringRoundtripError(
                f"documents[{index}].source_refs must be an object"
            )
        flat_root = _parse_svg(flat_source_path)
        refs: dict[str, SourceRefRecord] = {}
        for source_ref, ref_raw in refs_raw.items():
            if not isinstance(source_ref, str) or not isinstance(ref_raw, dict):
                raise AuthoringRoundtripError(
                    f"Invalid source-ref record in {authoring_name}"
                )
            source_path_raw = ref_raw.get("source_path")
            if not (
                isinstance(source_path_raw, list)
                and all(
                    isinstance(item, int) and not isinstance(item, bool) and item >= 0
                    for item in source_path_raw
                )
            ):
                raise AuthoringRoundtripError(
                    f"{authoring_name} source ref {source_ref!r} has invalid source_path"
                )
            initial_hash = ref_raw.get("initial_authoring_subtree_sha256")
            if not isinstance(initial_hash, str) or not initial_hash:
                raise AuthoringRoundtripError(
                    f"{authoring_name} source ref {source_ref!r} has no initial hash"
                )
            representation = ref_raw.get("representation")
            if representation not in {"inline", "source-proxy"}:
                raise AuthoringRoundtripError(
                    f"{authoring_name} source ref {source_ref!r} has an "
                    "unsupported representation"
                )
            proxy_asset: Path | None = None
            proxy_asset_sha256: str | None = None
            if representation == "source-proxy":
                proxy_asset_raw = ref_raw.get("proxy_asset")
                proxy_asset_sha256_raw = ref_raw.get("proxy_asset_sha256")
                if (
                    not isinstance(proxy_asset_raw, str)
                    or not proxy_asset_raw
                    or not isinstance(proxy_asset_sha256_raw, str)
                    or re.fullmatch(r"[0-9a-f]{64}", proxy_asset_sha256_raw) is None
                ):
                    raise AuthoringRoundtripError(
                        f"{authoring_name} source proxy {source_ref!r} has "
                        "incomplete asset metadata"
                    )
                proxy_asset = (authoring_dir / proxy_asset_raw).resolve()
                try:
                    proxy_asset.relative_to(proxy_root)
                except ValueError as exc:
                    proxy_root_name = proxy_root.relative_to(project_path).as_posix()
                    raise AuthoringRoundtripError(
                        f"{authoring_name} source proxy {source_ref!r} must use "
                        f"{proxy_root_name}/"
                    ) from exc
                if (
                    not proxy_asset.is_file()
                    or proxy_asset.suffix.lower() != ".svg"
                    or _sha256_file(proxy_asset) != proxy_asset_sha256_raw
                ):
                    raise AuthoringRoundtripError(
                        f"{authoring_name} source proxy asset is missing or changed: "
                        f"{proxy_asset}"
                    )
                proxy_asset_sha256 = proxy_asset_sha256_raw
            source_path_tuple = tuple(source_path_raw)
            source_element = _source_element(flat_root, source_path_tuple)
            if _source_identity(source_element) != source_ref:
                raise AuthoringRoundtripError(
                    f"{authoring_name} source ref {source_ref!r} resolves to "
                    f"{_source_identity(source_element)!r}"
                )
            refs[source_ref] = SourceRefRecord(
                source_path=source_path_tuple,
                initial_authoring_subtree_sha256=initial_hash,
                representation=representation,
                proxy_asset=proxy_asset,
                proxy_asset_sha256=proxy_asset_sha256,
            )
        documents[authoring_name] = AuthoringDocument(
            name=authoring_name,
            source_name=authoring_name,
            source_slide=source_slide,
            authoring_path=authoring_path,
            flat_source_path=flat_source_path,
            layered_source_path=layered_source_path,
            initial_authoring_sha256=initial_authoring_sha,
            source_refs=refs,
        )

    if set(documents) != set(source_slide_by_name):
        raise AuthoringRoundtripError(
            "Authoring manifest/source slide roster differs; missing="
            f"{sorted(set(source_slide_by_name) - set(documents))}, "
            f"extra={sorted(set(documents) - set(source_slide_by_name))}"
        )
    if manifest.get("file_count") != len(documents):
        raise AuthoringRoundtripError(
            "authoring_manifest.json file_count does not match documents"
        )
    expected_ref_count = sum(len(item.source_refs) for item in documents.values())
    if manifest.get("source_ref_count") != expected_ref_count:
        raise AuthoringRoundtripError(
            "authoring_manifest.json source_ref_count does not match documents"
        )
    return source_root, proxy_root, documents, manifest, roundtrip_manifest


def _identity_pages(
    documents: dict[str, AuthoringDocument],
) -> tuple[RoundtripPage, ...]:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from authoring_roundtrip import (
        RoundtripPage,
    )
    by_slide = {
        document.source_slide: document
        for document in documents.values()
    }
    return tuple(
        RoundtripPage(
            output_index=index,
            source_slide=index,
            svg_name=by_slide[index].name,
            source_svg_name=by_slide[index].name,
        )
        for index in range(1, len(by_slide) + 1)
    )


