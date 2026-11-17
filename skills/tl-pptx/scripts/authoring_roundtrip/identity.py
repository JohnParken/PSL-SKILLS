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

def roundtrip_source_fingerprint(project_path: Path) -> dict[str, object]:
    """Bind validation and export to the authoring SVGs and optional page plan."""
    paths = list((project_path / AUTHORING_SVG_FLAT_DIR).glob('*.svg'))
    plan_path = project_path / ROUNDTRIP_PAGE_PLAN_PATH
    if plan_path.is_file():
        paths.append(plan_path)
    files: list[dict[str, str]] = []
    aggregate = hashlib.sha256()
    for path in sorted(paths, key=lambda item: item.relative_to(project_path).as_posix()):
        name = path.relative_to(project_path).as_posix()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        files.append({'file': name, 'sha256': digest})
        aggregate.update(name.encode('utf-8') + b'\0' + digest.encode('ascii') + b'\n')
    return {
        'algorithm': 'sha256',
        'digest': aggregate.hexdigest(),
        'file_count': len(files),
        'files': files,
    }


def _local_name(name: object) -> str:
    return name.rsplit("}", 1)[-1] if isinstance(name, str) else ""


def _parse_svg(path: Path) -> ET.Element:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from authoring_roundtrip import (
        AuthoringRoundtripError,
    )
    parser = ET.XMLParser(
        target=ET.TreeBuilder(insert_comments=True, insert_pis=True),
    )
    try:
        return ET.fromstring(path.read_bytes(), parser=parser)
    except (OSError, ET.ParseError) as exc:
        raise AuthoringRoundtripError(f"Cannot parse SVG {path}: {exc}") from exc


def _load_json(path: Path, *, context: str) -> dict[str, Any]:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from authoring_roundtrip import (
        AuthoringRoundtripError,
    )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise AuthoringRoundtripError(f"Missing {context}: {path}") from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AuthoringRoundtripError(f"Cannot read {context} {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise AuthoringRoundtripError(f"{context} must be a JSON object: {path}")
    return payload


def _resolve_inside(root: Path, value: str, *, context: str) -> Path:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from authoring_roundtrip import (
        AuthoringRoundtripError,
    )
    try:
        resolved_root = root.resolve()
        path = (root / value).resolve()
    except ValueError as exc:
        raise AuthoringRoundtripError(
            f"{context} contains an invalid path value: {value!r}"
        ) from exc
    try:
        path.relative_to(resolved_root)
    except ValueError as exc:
        raise AuthoringRoundtripError(
            f"{context} resolves outside {root}: {value!r}"
        ) from exc
    return path


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _source_element(root: ET.Element, path: tuple[int, ...]) -> ET.Element:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from authoring_roundtrip import (
        AuthoringRoundtripError,
    )
    element = root
    try:
        for index in path:
            element = list(element)[index]
    except (IndexError, TypeError) as exc:
        raise AuthoringRoundtripError(
            f"Source-ref path no longer resolves: {list(path)}"
        ) from exc
    return element


def _source_identity(element: ET.Element) -> str | None:
    scope = element.get("data-pptx-shape-scope")
    shape_id = element.get("data-pptx-shape-id")
    if not scope or not shape_id:
        return None
    return f"{scope}:{shape_id}"


def is_flat_authoring_bundle(authoring_dir: Path) -> bool:
    """Return whether a directory declares the supported flat authoring IR."""
    manifest_path = authoring_dir / AUTHORING_MANIFEST_NAME
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    return (
        isinstance(manifest, dict)
        and manifest.get("schema") == AUTHORING_SCHEMA
        and manifest.get("projection_kind") == "flat"
        and manifest.get("authoring_root") == "."
        and manifest.get("source_ref_attribute") == SOURCE_REF_ATTRIBUTE
    )


def _roundtrip_source_svg_names(
    manifest: dict[str, Any],
) -> dict[int, str]:
    """Map every source slide index to its canonical flat SVG basename."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from authoring_roundtrip import (
        AuthoringRoundtripError,
    )
    raw_slides = manifest.get("slides")
    if not isinstance(raw_slides, list) or not raw_slides:
        raise AuthoringRoundtripError(
            "Round-trip workspace manifest slides must be a non-empty array"
        )
    names: dict[int, str] = {}
    for offset, raw in enumerate(raw_slides):
        context = f"round-trip slides[{offset}]"
        if not isinstance(raw, dict):
            raise AuthoringRoundtripError(f"{context} must be an object")
        index = raw.get("index")
        flat_svg = raw.get("flatSvg")
        if (
            not isinstance(index, int)
            or isinstance(index, bool)
            or index < 1
        ):
            raise AuthoringRoundtripError(
                f"{context}.index must be a positive integer"
            )
        if not isinstance(flat_svg, str) or not flat_svg:
            raise AuthoringRoundtripError(
                f"{context}.flatSvg must be a non-empty string"
            )
        name = Path(flat_svg).name
        if Path(name).suffix.lower() != ".svg":
            raise AuthoringRoundtripError(
                f"{context}.flatSvg must name an SVG file"
            )
        if index in names:
            raise AuthoringRoundtripError(
                f"Round-trip workspace repeats source slide {index}"
            )
        names[index] = name
    expected = set(range(1, len(names) + 1))
    if set(names) != expected:
        raise AuthoringRoundtripError(
            "Round-trip source slide indices must be contiguous from 1"
        )
    return names


