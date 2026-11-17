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

def _is_hidden_geometry_carrier(element: ET.Element) -> bool:
    if element.get("data-pptx-part") != "geometry":
        return False
    visibility = (element.get("visibility") or "").strip().lower()
    display = (element.get("display") or "").strip().lower()
    style = (element.get("style") or "").replace(" ", "").lower()
    return (
        visibility == "hidden"
        or display == "none"
        or "visibility:hidden" in style
        or "display:none" in style
    )


def _is_semantic_shape_candidate(element: ET.Element) -> bool:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from svg_authoring_view import (
        _DRAWABLE_TAGS,
        _local_name,
        _normalize_semantic_text_lines,
        _semantic_text_baseline,
    )
    if (
        _local_name(element.tag) != "g"
        or element.get("data-pptx-object") not in {"shape", "connector"}
        or (
            element.get("data-pptx-prst") is None
            and element.get("data-pptx-geometry-kind") != "custom"
        )
    ):
        return False
    if any(
        name in element.attrib
        for name in (
            "data-pptx-placeholder",
            "data-pptx-placeholder-type",
            "data-pptx-placeholder-idx",
        )
    ):
        return False
    if any(
        item.get("data-pptx-effect-status") == "unsupported"
        or item.get("data-pptx-effect-ooxml") is not None
        for item in element.iter()
    ):
        return False
    carriers = [
        child
        for child in element
        if child.get("data-pptx-part") == "geometry"
        and _local_name(child.tag) in _DRAWABLE_TAGS
    ]
    if len(carriers) != 1:
        return False
    visible_texts = [
        item
        for item in element.iter()
        if _local_name(item.tag) == "text"
    ]
    if any(text not in list(element) for text in visible_texts):
        return False
    if any(
        item.get("data-pptx-inline-formula") is not None
        for text in visible_texts
        for item in text.iter()
    ):
        return False
    positional_texts = [
        text
        for text in visible_texts
        if any(
            _local_name(item.tag) == "tspan"
            and any(item.get(name) is not None for name in ("x", "y", "dy"))
            for item in text.iter()
        )
    ]
    if len(visible_texts) > 1 and positional_texts:
        return False
    for text in positional_texts:
        try:
            if not _normalize_semantic_text_lines(copy.deepcopy(text)):
                return False
        except ValueError:
            return False
    if element.get("data-pptx-object") == "connector" and visible_texts:
        return False
    if len(visible_texts) > 1:
        if any(text.get("transform") is not None for text in visible_texts):
            return False
        anchors = {
            (item.get("x"), item.get("text-anchor", "start"))
            for item in visible_texts
        }
        if len(anchors) != 1:
            return False
        try:
            baselines = [_semantic_text_baseline(text) for text in visible_texts]
        except ValueError:
            return False
        if any(current <= previous for previous, current in zip(
            baselines,
            baselines[1:],
        )):
            return False
    for child in element:
        tag = _local_name(child.tag)
        part = child.get("data-pptx-part")
        if child is carriers[0] or tag in {"metadata", "text", "a"}:
            continue
        if part in {"geometry-preview", "placeholder-sppr"}:
            continue
        if tag == "g" and all(
            _local_name(item.tag) in {"g", "text", "tspan", "a"}
            for item in child.iter()
        ):
            continue
        return False
    return True


def _mark_semantic_objects(root: ET.Element, stats: ProjectionStats) -> None:
    """Mark source objects whose authoring truth has a closed semantic IR."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from svg_authoring_view import (
        SEMANTIC_OBJECT_ATTRIBUTE,
        SEMANTIC_SHAPE_KIND,
        SEMANTIC_TABLE_KIND,
        _is_semantic_shape_candidate,
    )
    for element in root.iter():
        if element.get("data-pptx-replace-with") == SEMANTIC_TABLE_KIND:
            element.set(SEMANTIC_OBJECT_ATTRIBUTE, SEMANTIC_TABLE_KIND)
            stats.semantic_tables += 1
            continue
        if _is_semantic_shape_candidate(element):
            element.set(SEMANTIC_OBJECT_ATTRIBUTE, SEMANTIC_SHAPE_KIND)
            stats.semantic_shapes += 1


def _make_geometry_carrier_visible(element: ET.Element) -> int:
    """Turn one import-only hidden carrier into the authored geometry surface."""
    changed = 0
    for name in ("visibility", "display", "pointer-events"):
        if name in element.attrib:
            del element.attrib[name]
            changed += 1

    style = element.get("style")
    if style is not None:
        declarations = [
            declaration.strip()
            for declaration in style.split(";")
            if declaration.strip()
        ]
        retained = [
            declaration
            for declaration in declarations
            if declaration.split(":", 1)[0].strip().lower()
            not in {"display", "pointer-events", "visibility"}
        ]
        normalized = "; ".join(retained)
        if normalized:
            if normalized != style:
                element.set("style", normalized)
                changed += 1
        else:
            del element.attrib["style"]
            changed += 1
    return changed


def _semantic_text_baseline(element: ET.Element) -> float:
    raw = element.get("y")
    if raw is None:
        raise ValueError("Semantic shape text requires an explicit y baseline")
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(
            f"Semantic shape text has a non-numeric y baseline: {raw!r}"
        ) from exc
    if not math.isfinite(value):
        raise ValueError(
            f"Semantic shape text has a non-finite y baseline: {raw!r}"
        )
    return value


def _semantic_text_font_size(element: ET.Element) -> float:
    raw = element.get("font-size")
    if raw is None:
        return 16.0
    try:
        value = float(raw)
    except ValueError:
        return 16.0
    return value if math.isfinite(value) and value > 0 else 16.0


def _semantic_text_has_content(element: ET.Element) -> bool:
    return bool("".join(element.itertext()))


def _normalize_semantic_text_lines(text: ET.Element) -> bool:
    """Lower compatible positioned tspans into one semantic line model."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from svg_authoring_view import (
        SVG_NS,
        _local_name,
        _semantic_text_has_content,
    )
    positional = [
        child
        for child in text
        if _local_name(child.tag) == "tspan"
        and any(child.get(name) is not None for name in ("x", "y", "dy"))
    ]
    if not positional:
        return False

    parent_x = text.get("x")
    advances: list[float] = []
    for child in positional:
        if child.get("y") is not None:
            raise ValueError(
                "Semantic shape text cannot normalize an absolute tspan y"
            )
        child_x = child.get("x")
        if child_x is not None and child_x != parent_x:
            raise ValueError(
                "Semantic shape text cannot normalize a tspan x jump"
            )
        raw_dy = child.get("dy")
        try:
            advance = float(raw_dy or "0")
        except ValueError as exc:
            raise ValueError(
                f"Semantic shape text has a non-numeric tspan dy: {raw_dy!r}"
            ) from exc
        if not math.isfinite(advance) or advance <= 0:
            raise ValueError(
                f"Semantic shape text requires a positive finite tspan dy: {raw_dy!r}"
            )
        advances.append(advance)

    first_line = ET.Element(f"{{{SVG_NS}}}tspan")
    first_line.text = text.text
    lines: list[ET.Element] = [first_line]
    current = first_line
    for child in list(text):
        clone = copy.deepcopy(child)
        if child in positional:
            for name in ("x", "y", "dy"):
                clone.attrib.pop(name, None)
            clone.set("data-paragraph-line-break", "1")
            lines.append(clone)
            current = clone
            continue
        current.append(clone)

    lines = [line for line in lines if _semantic_text_has_content(line)]
    if len(lines) < 2:
        return False
    for child in list(text):
        text.remove(child)
    text.text = None
    text.set("data-pptx-text-model", "lines")
    line_height = min(advances)
    text.set(
        "data-paragraph-line-height",
        f"{line_height:.6f}".rstrip("0").rstrip("."),
    )
    for line in lines:
        text.append(line)
    return True


def _semantic_paragraph_from_text(
    source: ET.Element,
    parent: ET.Element,
    *,
    space_before: float,
) -> ET.Element:
    """Wrap one rendered SVG text line as one semantic paragraph run tree."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from svg_authoring_view import (
        SVG_NS,
        _TSPAN_INHERITED_ATTRIBUTES,
    )
    paragraph = ET.Element(f"{{{SVG_NS}}}tspan")
    presentation_attributes = _TSPAN_INHERITED_ATTRIBUTES | {
        "baseline-shift",
        "opacity",
        "stroke-linecap",
        "stroke-linejoin",
        "stroke-width",
        "style",
    }
    for name in presentation_attributes:
        value = source.get(name)
        if value is not None and value != parent.get(name):
            paragraph.set(name, value)
    if space_before > 0:
        paragraph.set(
            "data-paragraph-space-before",
            f"{space_before:.6f}".rstrip("0").rstrip("."),
        )
    paragraph.text = source.text
    for child in source:
        paragraph.append(copy.deepcopy(child))
    return paragraph


def _merge_semantic_shape_texts(
    shape: ET.Element,
    texts: list[ET.Element],
) -> ET.Element:
    """Represent multiple rendered text rows as one paragraph-based text body."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from svg_authoring_view import (
        _semantic_paragraph_from_text,
        _semantic_text_baseline,
        _semantic_text_font_size,
    )
    baselines = [_semantic_text_baseline(text) for text in texts]
    positive_advances = [
        current - previous
        for previous, current in zip(baselines, baselines[1:])
        if current > previous
    ]
    line_height = _semantic_text_font_size(texts[0]) * 1.5
    if positive_advances:
        line_height = min(line_height, min(positive_advances))
    line_height = max(line_height, 1.0)

    combined = copy.deepcopy(texts[0])
    for child in list(combined):
        combined.remove(child)
    combined.text = None
    combined.set("data-pptx-text-model", "paragraphs")
    combined.set(
        "data-paragraph-line-height",
        f"{line_height:.6f}".rstrip("0").rstrip("."),
    )
    previous_baseline = baselines[0]
    for index, (source, baseline) in enumerate(zip(texts, baselines)):
        space_before = (
            0.0
            if index == 0
            else max(0.0, baseline - previous_baseline - line_height)
        )
        combined.append(
            _semantic_paragraph_from_text(
                source,
                combined,
                space_before=space_before,
            )
        )
        previous_baseline = baseline

    siblings = list(shape)
    first_index = siblings.index(texts[0])
    for text in texts:
        shape.remove(text)
    shape.insert(first_index, combined)
    return combined


