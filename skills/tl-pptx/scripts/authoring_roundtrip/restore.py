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
    RefOccurrence,
)

def _rebase_resource_references(
    element: ET.Element,
    source_dir: Path,
    target_dir: Path,
    project_path: Path,
    *,
    recursive: bool,
) -> None:
    """Rebase exact local SVG resource hrefs without path guessing."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from authoring_roundtrip import (
        AuthoringRoundtripError,
        XLINK_NS,
        _local_name,
    )
    nodes = element.iter() if recursive else (element,)
    project_root = project_path.resolve()
    for node in nodes:
        if _local_name(node.tag) == "a":
            continue
        for name in ("href", f"{{{XLINK_NS}}}href"):
            raw = node.get(name)
            if raw is None or not raw or raw.startswith("#") or raw.startswith("data:"):
                continue
            parsed = urlsplit(raw)
            if (
                parsed.scheme
                or parsed.netloc
                or parsed.query
                or parsed.fragment
                or not parsed.path
                or Path(unquote(parsed.path)).is_absolute()
            ):
                raise AuthoringRoundtripError(
                    f"Unsupported SVG resource reference in {element.tag}: {raw!r}"
                )
            resolved = (source_dir / unquote(parsed.path)).resolve()
            try:
                resolved.relative_to(project_root)
            except ValueError as exc:
                raise AuthoringRoundtripError(
                    f"SVG resource reference escapes the project: {raw!r}"
                ) from exc
            relative = os.path.relpath(resolved, target_dir).replace(os.sep, "/")
            node.set(name, urlunsplit(("", "", relative, "", "")))


def _restore_preserved_effect_metadata(
    target: ET.Element,
    source: ET.Element,
) -> None:
    """Keep source-native effects when an author edits other object semantics."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from authoring_roundtrip import (
        _PRESERVED_EFFECT_ATTRIBUTES,
    )
    source_by_id = {
        item_id: item
        for item in source.iter()
        if (item_id := item.get("id"))
    }
    for item in target.iter():
        source_item = (
            source
            if item is target
            else source_by_id.get(item.get("id") or "")
        )
        if source_item is None:
            continue
        for name in _PRESERVED_EFFECT_ATTRIBUTES:
            value = source_item.get(name)
            if value is not None and item.get(name) is None:
                item.set(name, value)


def _text_body_frame(element: ET.Element) -> tuple[float, ...] | None:
    """Read a finite carrier frame for geometry and relative text comparisons."""
    try:
        frame = tuple(float(value) for value in re.split(
            r"[\s,]+", (element.get("data-pptx-frame") or "").strip(),
        ))
    except ValueError:
        return None
    if len(frame) != 4 or not all(math.isfinite(value) for value in frame):
        return None
    return frame


def _text_body_geometry(occurrence: RefOccurrence) -> tuple | None:
    """Compare carrier size and effective axes, allowing only position changes."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from authoring_roundtrip import (
        _contextual_authoring_element,
        _text_body_frame,
    )
    frame = _text_body_frame(occurrence.element)
    if frame is None:
        return None
    contextual = _contextual_authoring_element(occurrence.element, occurrence.root)
    # Match the precision used when the shared context helper composes ancestry.
    axes = tuple(f"{value:.12g}" for value in parse_transform_matrix(contextual.get("transform", ""))[:4])
    return frame[2:], axes


def _text_body_state(occurrence: RefOccurrence, *, frame_relative: bool = False) -> list[str] | None:
    """Fingerprint text with carrier translations removed, retaining local edits."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from authoring_roundtrip import (
        SVG_NS,
        _contextual_authoring_element,
        _text_body_frame,
    )
    carrier = copy.deepcopy(occurrence.element)
    root = carrier
    context = [carrier]
    for ancestor in reversed(_element_chain(occurrence.root, occurrence.element)[:-1]):
        wrapper = ET.Element(ancestor.tag, ancestor.attrib)
        wrapper.append(root)
        root = wrapper
        context.append(wrapper)
    # Normalize only the carrier and its ancestors. Text-local coordinates and
    # transforms (including those on wrappers inside the carrier) still count.
    for node in context:
        axes = parse_transform_matrix(node.get("transform", ""))[:4]
        node.attrib.pop("transform", None)
        if axes != (1.0, 0.0, 0.0, 1.0):
            node.set("transform", "matrix(" + " ".join(
                f"{value:.12g}" for value in (*axes, 0.0, 0.0)
            ) + ")")
    if frame_relative:
        frame = _text_body_frame(carrier)
        if frame is None:
            return None
        for text in carrier.iter(f"{{{SVG_NS}}}text"):
            for node in text.iter():
                if node.tag not in {f"{{{SVG_NS}}}text", f"{{{SVG_NS}}}tspan"}:
                    continue
                for axis, origin in zip(("x", "y"), frame[:2]):
                    raw = node.get(axis)
                    if raw is None:
                        continue
                    try:
                        values = [float(value) - origin for value in re.split(r"[\s,]+", raw.strip())]
                    except ValueError:
                        return None
                    if not all(math.isfinite(value) for value in values):
                        return None
                    node.set(axis, " ".join(_format_transform_number(value) for value in values))
    return [
        semantic_subtree_sha256(_contextual_authoring_element(text, root))
        for text in carrier.iter(f"{{{SVG_NS}}}text")
    ]


def _text_body_formula_loss_warning(metadata: ET.Element, target: ET.Element, reason: str) -> str | None:
    """Diagnose lost native math without taking over payload validation."""
    try:
        body = ET.fromstring(base64.b64decode((metadata.text or "").strip(), validate=True))
    except (ValueError, binascii.Error, UnicodeDecodeError, ET.ParseError):
        # An unreadable payload has an unknown formula count. Keep validation
        # with the converter when the original restoration path consumes it.
        return None
    math_count = sum(1 for _ in body.iter("{http://schemas.microsoft.com/office/drawing/2010/main}m"))
    marker_count = sum(
        1 for node in target.iter()
        if node.get("data-pptx-inline-formula") or node.get("data-pptx-replace-with") == "formula"
    )
    if math_count > marker_count:
        return f"native formulas will become plain text; reason={reason}; source txBody cannot be restored"
    return None


def _restore_unchanged_text_body(
    target: ET.Element,
    source: ET.Element,
    current: RefOccurrence,
    baseline: RefOccurrence,
) -> str | None:
    """Recover unchanged native text after translation, or diagnose lost math."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from authoring_roundtrip import (
        _text_body_formula_loss_warning,
        _text_body_geometry,
        _text_body_state,
    )
    metadata = next((child for child in source if child.get("data-pptx-part") == "txbody"), None)
    if metadata is None:
        return
    geometry = _text_body_geometry(current)
    if geometry is None or geometry != _text_body_geometry(baseline):
        return _text_body_formula_loss_warning(metadata, target, "size/transform-changed")
    if _text_body_state(current) != _text_body_state(baseline):
        relative_state = _text_body_state(current, frame_relative=True)
        if relative_state is None or relative_state != _text_body_state(baseline, frame_relative=True):
            return _text_body_formula_loss_warning(metadata, target, "text-edited")
    restored = copy.deepcopy(metadata)
    # Projection compacts text runs and paragraphs. Validate that projection
    # above, then let the existing decoder validate the recovered payload.
    restored.set("data-pptx-text-sha256", svg_text_fingerprint(target))
    target.append(restored)


def _apply_authoring_root_attributes(
    target: ET.Element,
    authoring_root: ET.Element,
) -> None:
    """Make authoring-root geometry and inherited presentation authoritative."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from authoring_roundtrip import (
        _ROOT_AUTHORING_ATTRIBUTES,
    )
    for name in _ROOT_AUTHORING_ATTRIBUTES:
        target.attrib.pop(name, None)
        value = authoring_root.get(name)
        if value is not None:
            target.set(name, value)


def _native_restore_placeholder(
    source: ET.Element,
    source_ref: str,
) -> ET.Element:
    """Keep one natively restored source shape ordered without converting it."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from authoring_roundtrip import (
        SVG_NS,
    )
    shape_key = source_ref.split(":", 1)[1]
    placeholder = ET.Element(
        f"{{{SVG_NS}}}g",
        {
            "id": f"shape-{shape_key}",
            "data-pptx-object": "group",
            "data-pptx-roundtrip-object": "native-restore-placeholder",
        },
    )
    if shape_key.isdigit():
        placeholder.set("data-pptx-shape-id", shape_key)
        placeholder.set("data-pptx-shape-scope", "slide")
    for name in (
        "data-pptx-layer",
        "data-pptx-role",
        "data-pptx-placeholder",
    ):
        value = source.get(name)
        if value is not None:
            placeholder.set(name, value)
    transform = source.get("transform")
    if transform:
        placeholder.set("transform", transform)
    frame = (source.get("data-pptx-frame") or "").split()
    try:
        x, y, width, height = (float(value) for value in frame)
    except (TypeError, ValueError):
        x, y, width, height = 0.0, 0.0, 1.0, 1.0
    ET.SubElement(
        placeholder,
        f"{{{SVG_NS}}}rect",
        {
            "x": f"{x:.15g}",
            "y": f"{y:.15g}",
            "width": f"{max(width, 0.001):.15g}",
            "height": f"{max(height, 0.001):.15g}",
            "fill": "#000000",
        },
    )
    return placeholder


def prepare_imported_chart_edit(
    current: ET.Element,
    baseline: ET.Element,
    source: ET.Element,
    *,
    current_root: ET.Element | None = None,
    baseline_root: ET.Element | None = None,
) -> tuple[dict | None, str | None]:
    """Reuse import-time JSON and source parts for a data-only chart change."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from authoring_roundtrip import (
        AuthoringRoundtripError,
        SVG_NS,
        _contextual_authoring_element,
    )
    if any(element.get("data-pptx-replace-with") != "chart"
           or element.get("data-pptx-native-authority") != "json"
           for element in (current, baseline)):
        return None, None
    try:
        edited = _load_payload(current, "chart")
        original = _load_payload(baseline, "chart")
    except RuntimeError as exc:
        marker_id = current.get("id") or current.get(SOURCE_REF_ATTRIBUTE) or "<unnamed>"
        raise AuthoringRoundtripError(f"Invalid chart {marker_id}: {exc}") from exc
    unchanged = edited == original
    if not unchanged and not chart_edit_is_data_only(original, edited):
        return None, "series/point topology or non-data JSON fields changed"
    comparison = copy.deepcopy(_contextual_authoring_element(current, current_root))
    reference = _contextual_authoring_element(baseline, baseline_root)
    comparison.find(f'{{{SVG_NS}}}metadata[@type="application/json"]').text = (
        reference.find(f'{{{SVG_NS}}}metadata[@type="application/json"]').text
    )
    ignored = frozenset({SOURCE_REF_ATTRIBUTE, "data-pptx-fallback-sha256"})
    if semantic_subtree_sha256(comparison, ignored_attributes=ignored) != semantic_subtree_sha256(
        reference, ignored_attributes=ignored,
    ):
        return None, "chart SVG geometry or presentation changed alongside its data"
    package = _load_payload(source, "chart").get("source_package")
    if unchanged:
        return package, None
    if package is None:
        raise AuthoringRoundtripError("Data-only chart edit has no closed source package; reimport the source deck")
    try:
        return patch_chart_package(package, original, edited), None
    except RuntimeError as exc:
        raise AuthoringRoundtripError(f"Cannot preserve data-only chart edit: {exc}") from exc


def chart_rebuild_warning(project_path: Path, source_slide: int, source_ref: str, reason: str) -> str:
    """Report the object's recorded import losses when a chart must rebuild."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from authoring_roundtrip import (
        _load_json,
    )
    report_path = project_path / "validation" / "conversion-report.json"
    diagnostics = []
    if report_path.is_file():
        report = _load_json(report_path, context="import conversion report")
        diagnostics = [item for item in report.get("diagnostics", [])
                       if item.get("slide_index") == source_slide
                       and str(item.get("shape_id")) == source_ref.split(":", 1)[-1]]
    details = "; ".join(dict.fromkeys(
        f"{item.get('code')}: {item.get('message')}" for item in diagnostics
    )) or "no object-specific import diagnostics were recorded"
    return (f"native-chart-rebuilt: {reason}; JSON rebuild may omit source-only formatting. "
            f"Import diagnostics: {details}")


