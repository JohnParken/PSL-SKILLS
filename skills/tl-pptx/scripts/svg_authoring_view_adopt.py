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

def adopt_authoring_object(
    authoring_dir: Path,
    source_spec: str,
    target_name: str,
) -> dict[str, object]:
    """Copy one cross-page object as authored SVG with no source identity."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from svg_authoring_view import (
        SOURCE_PROXY_ATTRIBUTE,
        SOURCE_PROXY_KIND,
        SVG_NS,
        _adopted_definition_clones,
        _append_adopted_definitions,
        _authoring_page_path,
        _authoring_source_document_ids,
        _load_authoring_summary_manifest,
        _local_name,
        _materialize_adopted_context,
        _parse_adopt_source,
        _parse_authoring_svg,
        _rewrite_adopted_fragment_ids,
        _strip_adopted_transport,
        _write_svg_atomically,
        write_authoring_summary,
    )
    authoring_dir = authoring_dir.resolve()
    if not authoring_dir.is_dir():
        raise ValueError(f"Authoring directory not found: {authoring_dir}")
    manifest, _, _ = _load_authoring_summary_manifest(authoring_dir)
    source_name, element_id = _parse_adopt_source(source_spec)
    source_path = _authoring_page_path(
        authoring_dir,
        source_name,
        label="--adopt-object source",
    )
    target_path = _authoring_page_path(
        authoring_dir,
        target_name,
        label="--into target",
    )
    if source_path == target_path:
        raise ValueError("--adopt-object source and --into target must differ")

    source_root = _parse_authoring_svg(source_path)
    target_root = _parse_authoring_svg(target_path)
    matches = [
        item
        for item in source_root.iter()
        if item.get("id") == element_id
    ]
    if not matches:
        raise ValueError(f"{source_name} has no element id {element_id!r}")
    if len(matches) > 1:
        raise ValueError(f"{source_name} repeats element id {element_id!r}")
    source_element = matches[0]
    if any(
        item.get(SOURCE_PROXY_ATTRIBUTE) == SOURCE_PROXY_KIND
        for item in source_element.iter()
    ):
        raise ValueError(
            f"Cannot adopt source proxy {source_name}:{element_id}; "
            "source proxies cannot move between pages"
        )

    adopted_definitions = _adopted_definition_clones(
        source_root,
        source_element,
    )
    adopted = copy.deepcopy(source_element)
    adopted.tail = None
    from authoring_roundtrip import preserve_adopted_source_links

    preserve_adopted_source_links(authoring_dir, source_name, source_root, source_element, adopted)
    imported_icons = [
        item
        for item in adopted.iter()
        if _local_name(item.tag) == "use"
        and (item.get("data-icon") or "").startswith("imported/")
    ]
    inlined_icons = 0
    if imported_icons:
        from svg_to_pptx.use_expander import (
            UseExpansionError,
            expand_use_data_icons,
        )

        container = ET.Element(f"{{{SVG_NS}}}g")
        container.append(adopted)
        try:
            inlined_icons = expand_use_data_icons(
                container,
                authoring_dir.parent / "icons",
            )
        except UseExpansionError as exc:
            raise ValueError(
                f"Cannot inline source-page vector asset: {exc}"
            ) from exc
        adopted = container[0]
        if adopted.get("id") is None:
            adopted.set("id", element_id)
    residual_imports = sorted({
        icon
        for item in adopted.iter()
        if (icon := item.get("data-icon"))
        and icon.startswith("imported/")
    })
    if residual_imports:
        raise ValueError(
            "Adopted object retains source-owned imported vector(s): "
            + ", ".join(residual_imports)
        )

    inherited_attributes, ancestor_transform_composed = (
        _materialize_adopted_context(
            source_root,
            source_element,
            target_root,
            adopted,
        )
    )
    stripped, removed_metadata = _strip_adopted_transport(adopted)
    target_ids = {
        item_id
        for item in target_root.iter()
        if (item_id := item.get("id"))
    }
    target_ids.update(
        _authoring_source_document_ids(
            authoring_dir,
            manifest,
            target_name,
        )
    )
    adopted_envelope = ET.Element(f"{{{SVG_NS}}}g")
    adopted_envelope.append(adopted)
    adopted_envelope.extend(adopted_definitions)
    replacements = _rewrite_adopted_fragment_ids(
        adopted_envelope,
        target_ids,
    )
    adopted = adopted_envelope[0]
    adopted_definitions = list(adopted_envelope)[1:]
    adopted_id = adopted.get("id")
    if not adopted_id:
        raise ValueError("Adopted object lost its required id")
    _append_adopted_definitions(target_root, adopted_definitions)
    shape_href = adopted.get("data-pptx-shape-hyperlink")
    if shape_href and not any(_local_name(item.tag) == "a" for item in adopted.iter()):
        adopted.attrib.pop("data-pptx-shape-hyperlink")
        wrapper = ET.Element(f"{{{SVG_NS}}}a", {"href": shape_href})
        source_href = adopted.attrib.pop(SOURCE_HREF_ATTR, None)
        if source_href is not None:
            wrapper.set(SOURCE_HREF_ATTR, source_href)
        adopted_link = adopted.attrib.pop(ADOPTED_SOURCE_LINK_ATTR, None)
        if adopted_link is not None:
            wrapper.set(ADOPTED_SOURCE_LINK_ATTR, adopted_link)
        wrapper.append(adopted)
        target_root.append(wrapper)
    else:
        target_root.append(adopted)

    original_target = target_path.read_bytes()
    try:
        _write_svg_atomically(target_path, target_root)
        summary_path = write_authoring_summary(authoring_dir)
    except (OSError, ValueError):
        with tempfile.NamedTemporaryFile(
            prefix=f".{target_path.name}.rollback.",
            suffix=".tmp",
            dir=target_path.parent,
            delete=False,
        ) as handle:
            rollback_path = Path(handle.name)
            handle.write(original_target)
        rollback_path.chmod(target_path.stat().st_mode)
        rollback_path.replace(target_path)
        raise

    return {
        "authoring_dir": str(authoring_dir),
        "source": f"{source_name}:{element_id}",
        "target": target_name,
        "adopted_id": adopted_id,
        "renamed_ids": dict(sorted(replacements.items())),
        "copied_definitions": len(adopted_definitions),
        "inlined_imported_vectors": inlined_icons,
        "inherited_attributes": list(inherited_attributes),
        "ancestor_transform_composed": ancestor_transform_composed,
        "stripped_attributes": sorted(stripped),
        "removed_native_metadata": removed_metadata,
        "summary": str(summary_path),
    }


