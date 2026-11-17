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

def _parse_adopt_source(value: str) -> tuple[str, str]:
    match = re.fullmatch(r"([^/\\]+\.svg):(.+)", value, flags=re.IGNORECASE)
    if match is None:
        raise ValueError(
            "--adopt-object must use <from.svg>:<element-id>"
        )
    source_name, element_id = match.groups()
    if not element_id.strip() or element_id != element_id.strip():
        raise ValueError("--adopt-object element id must be non-empty and trimmed")
    return source_name, element_id


def _authoring_page_path(
    authoring_dir: Path,
    name: str,
    *,
    label: str,
) -> Path:
    relative = Path(name)
    if (
        "/" in name
        or "\\" in name
        or relative.name != name
        or relative.suffix.lower() != ".svg"
        or name in {".", ".."}
    ):
        raise ValueError(f"{label} must name one SVG directly in {authoring_dir}")
    path = (authoring_dir / name).resolve()
    try:
        path.relative_to(authoring_dir.resolve())
    except ValueError as exc:
        raise ValueError(f"{label} escapes the authoring directory: {name!r}") from exc
    if not path.is_file():
        raise ValueError(f"{label} does not exist: {path}")
    return path


def _parse_authoring_svg(path: Path) -> ET.Element:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from svg_authoring_view import (
        _local_name,
    )
    parser = ET.XMLParser(
        target=ET.TreeBuilder(insert_comments=True, insert_pis=True),
    )
    try:
        root = ET.fromstring(path.read_bytes(), parser=parser)
    except (OSError, ET.ParseError) as exc:
        raise ValueError(f"Cannot parse authoring SVG {path}: {exc}") from exc
    if _local_name(root.tag) != "svg":
        raise ValueError(f"Authoring document root is not <svg>: {path}")
    return root


def _element_chain(
    root: ET.Element,
    element: ET.Element,
) -> tuple[ET.Element, ...]:
    """Return the root-to-element ancestry chain."""
    parent_by_child = {
        child: parent
        for parent in root.iter()
        for child in parent
    }
    chain = [element]
    while chain[-1] is not root:
        parent = parent_by_child.get(chain[-1])
        if parent is None:
            raise ValueError("Adopted element is detached from its source SVG")
        chain.append(parent)
    return tuple(reversed(chain))


def _local_inherited_presentation(element: ET.Element) -> dict[str, str]:
    """Resolve supported local presentation attributes with CSS precedence."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from svg_authoring_view import (
        _ADOPT_INHERITED_ATTRIBUTES,
    )
    values = {
        name: value
        for name in _ADOPT_INHERITED_ATTRIBUTES
        if (value := element.get(name)) is not None
    }
    values.update({
        name: value
        for name, value in parse_inline_style(element.get("style")).items()
        if name in _ADOPT_INHERITED_ATTRIBUTES
    })
    return values


def _local_opacity(element: ET.Element) -> str | None:
    inline = parse_inline_style(element.get("style"))
    return inline.get("opacity") or element.get("opacity")


def _set_explicit_presentation(
    element: ET.Element,
    name: str,
    value: str,
) -> None:
    """Write one resolved style as an attribute, removing its inline shadow."""
    inline = parse_inline_style(element.get("style"))
    if name in inline:
        inline.pop(name)
        if inline:
            element.set(
                "style",
                "; ".join(
                    f"{property_name}: {property_value}"
                    for property_name, property_value in inline.items()
                ),
            )
        else:
            element.attrib.pop("style", None)
    element.set(name, value)


def _format_transform_number(value: float) -> str:
    if math.isclose(value, 0.0, abs_tol=1e-12):
        value = 0.0
    return f"{value:.12g}"


def _materialize_adopted_context(
    source_root: ET.Element,
    source_element: ET.Element,
    target_root: ET.Element,
    adopted: ET.Element,
) -> tuple[tuple[str, ...], bool]:
    """Carry source ancestry style and transforms onto an adopted object."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from svg_authoring_view import (
        _element_chain,
        _format_transform_number,
        _local_inherited_presentation,
        _local_opacity,
        _set_explicit_presentation,
    )
    chain = _element_chain(source_root, source_element)
    effective: dict[str, str] = {}
    for ancestor in chain:
        effective.update(_local_inherited_presentation(ancestor))
    target_defaults = _local_inherited_presentation(target_root)
    materialized: list[str] = []
    for name, value in effective.items():
        if target_defaults.get(name) == value:
            continue
        _set_explicit_presentation(adopted, name, value)
        materialized.append(name)

    source_opacity = 1.0
    source_has_opacity = False
    try:
        for ancestor in chain:
            raw_opacity = _local_opacity(ancestor)
            if raw_opacity is None:
                continue
            source_has_opacity = True
            source_opacity *= parse_opacity(
                raw_opacity,
                allow_percentage=True,
            )
        target_raw_opacity = _local_opacity(target_root)
        target_opacity = parse_opacity(
            target_raw_opacity,
            allow_percentage=True,
        )
    except ValueError as exc:
        raise ValueError(f"Cannot resolve adopted object opacity: {exc}") from exc
    if (
        (source_has_opacity or target_raw_opacity is not None)
        and not math.isclose(source_opacity, target_opacity, abs_tol=1e-12)
    ):
        _set_explicit_presentation(
            adopted,
            "opacity",
            _format_transform_number(source_opacity),
        )
        materialized.append("opacity")

    ancestor_transform_composed = any(
        (ancestor.get("transform") or "").strip()
        for ancestor in chain[:-1]
    )
    if ancestor_transform_composed:
        matrix = IDENTITY_MATRIX
        try:
            for ancestor in chain:
                raw_transform = ancestor.get("transform")
                if raw_transform:
                    matrix = matrix_multiply(
                        matrix,
                        parse_transform_matrix(raw_transform),
                    )
        except ValueError as exc:
            raise ValueError(
                f"Cannot compose adopted object ancestor transform: {exc}"
            ) from exc
        adopted.set(
            "transform",
            "matrix(" + " ".join(
                _format_transform_number(value)
                for value in matrix
            ) + ")",
        )
    return tuple(sorted(materialized)), ancestor_transform_composed


