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

def _materialize_document(
    project_path: Path,
    document: AuthoringDocument,
    baseline_path: Path,
    output_path: Path,
    current_assets: dict[str, VectorAssetRecord],
    baseline_assets: dict[str, VectorAssetRecord],
    *,
    page: RoundtripPage | None = None,
    pages: tuple[RoundtripPage, ...] = (),
) -> dict[str, Any]:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from authoring_roundtrip import (
        AuthoringRoundtripError,
        SVG_NS,
        XLINK_NS,
        _apply_authoring_root_attributes,
        _definition_changes,
        _direct_defs,
        _layered_ref_index,
        _local_name,
        _merge_defs,
        _native_restore_placeholder,
        _outer_source_ref_elements,
        _parse_svg,
        _prune_unreferenced_definitions,
        _rebase_resource_references,
        _ref_occurrences,
        _referenced_assets,
        _restore_preserved_effect_metadata,
        _restore_unchanged_text_body,
        _serialize_svg,
        _source_jump_links,
        authoring_source_ref_is_unchanged,
        chart_rebuild_warning,
        normalized_authoring_subtree_sha256,
        prepare_imported_chart_edit,
    )
    current_root = _parse_svg(document.authoring_path)
    baseline_root = _parse_svg(baseline_path)
    layered_root = _parse_svg(document.layered_source_path)
    inherited_links = _source_jump_links(current_root, baseline_root)

    def remap_link(clone: ET.Element, original: ET.Element) -> None:
        inherited = inherited_links.get(original)
        source_href = original.get(SOURCE_HREF_ATTR)
        if source_href and re.fullmatch(r"#slide-[1-9]\d*", source_href):
            for name in (SHAPE_HYPERLINK_ATTR, "href", f"{{{XLINK_NS}}}href"):
                if original.get(name) == source_href:
                    inherited = (name, source_href)
                    break
        clone.attrib.pop(SOURCE_HREF_ATTR, None)
        clone.attrib.pop(ADOPTED_SOURCE_LINK_ATTR, None)
        if inherited is None or page is None:
            return
        name, href = inherited
        target = int(href.removeprefix("#slide-"))
        targets = [candidate.output_index for candidate in pages if candidate.source_slide == target]
        if original.get(ADOPTED_SOURCE_LINK_ATTR) != "true" and target == page.source_slide:
            targets = [page.output_index]
        if len(targets) != 1:
            reason = "omitted" if not targets else "repeated"
            raise AuthoringRoundtripError(
                f"{document.name} inherited link {href!r} targets {reason} source slide {target}; "
                "include the target exactly once or remove/change the link"
            )
        clone.set(name, f"#slide-{targets[0]}")
    document_unchanged = (
        normalized_authoring_subtree_sha256(current_root, current_assets)
        == normalized_authoring_subtree_sha256(
            baseline_root,
            baseline_assets,
        )
    )
    layered_index = _layered_ref_index(layered_root)
    current_referenced_assets = _referenced_assets(
        current_root,
        current_assets,
        document_name=document.name,
        origin_document=document.source_name,
    )
    baseline_referenced_assets = _referenced_assets(
        baseline_root,
        baseline_assets,
        document_name=document.name,
        origin_document=document.source_name,
    )
    current_occurrences = _ref_occurrences(
        current_root,
        current_referenced_assets,
    )
    baseline_occurrences = _ref_occurrences(
        baseline_root,
        baseline_referenced_assets,
    )
    expected_refs = set(document.source_refs)
    baseline_refs = set(baseline_occurrences)
    if baseline_refs != expected_refs:
        raise AuthoringRoundtripError(
            f"{document.name} regenerated baseline source-ref closure differs; "
            f"missing={sorted(expected_refs - baseline_refs)}, "
            f"extra={sorted(baseline_refs - expected_refs)}"
        )
    baseline_duplicates = sorted(
        ref for ref, items in baseline_occurrences.items() if len(items) != 1
    )
    if baseline_duplicates:
        raise AuthoringRoundtripError(
            f"{document.name} regenerated baseline has duplicate source refs: "
            + ", ".join(baseline_duplicates)
        )
    manifest_proxy_refs = {
        source_ref
        for source_ref, record in document.source_refs.items()
        if record.representation == "source-proxy"
    }
    baseline_proxy_refs = {
        source_ref
        for source_ref, occurrences in baseline_occurrences.items()
        if occurrences[0].element.get(SOURCE_PROXY_ATTRIBUTE) == SOURCE_PROXY_KIND
    }
    if baseline_proxy_refs != manifest_proxy_refs:
        raise AuthoringRoundtripError(
            f"{document.name} regenerated source-proxy roster differs; "
            f"missing={sorted(manifest_proxy_refs - baseline_proxy_refs)}, "
            f"extra={sorted(baseline_proxy_refs - manifest_proxy_refs)}"
        )
    current_unknown = sorted(set(current_occurrences) - expected_refs)
    if current_unknown:
        raise AuthoringRoundtripError(
            f"{document.name} contains unknown source refs: "
            + ", ".join(current_unknown)
        )
    current_duplicates = sorted(
        ref for ref, items in current_occurrences.items() if len(items) != 1
    )
    if current_duplicates:
        raise AuthoringRoundtripError(
            f"{document.name} contains duplicate source refs: "
            + ", ".join(current_duplicates)
        )

    defs_changed, changed_definition_ids = _definition_changes(
        current_root,
        baseline_root,
        current_assets,
        baseline_assets,
    )
    unchanged_refs: set[str] = set()
    edited_refs: set[str] = set()
    for source_ref, occurrences in current_occurrences.items():
        current = occurrences[0]
        baseline = baseline_occurrences[source_ref][0]
        if authoring_source_ref_is_unchanged(
            current.element,
            baseline.element,
            current_assets,
            baseline_assets,
            changed_definition_ids=changed_definition_ids,
            current_asset=current.asset,
            baseline_asset=baseline.asset,
            current_root=current.root,
            baseline_root=baseline.root,
        ):
            unchanged_refs.add(source_ref)
        else:
            edited_refs.add(source_ref)
    deleted_refs = expected_refs - set(current_occurrences)
    changed_inherited_refs = sorted(
        source_ref for source_ref in edited_refs | deleted_refs
        if source_ref.startswith(("master:", "layout:"))
    )
    if changed_inherited_refs:
        raise AuthoringRoundtripError(
            f"{document.name} edits or deletes inherited object(s): "
            + ", ".join(changed_inherited_refs)
            + "; Master/Layout objects must remain unchanged because a flat "
            "slide cannot edit shared structure; edit Slide-local objects instead"
        )
    edited_proxy_refs = sorted(edited_refs & manifest_proxy_refs)
    if edited_proxy_refs:
        raise AuthoringRoundtripError(
            f"{document.name} edits source-backed proxy object(s): "
            + ", ".join(edited_proxy_refs)
            + "; keep each proxy unchanged to restore its native PowerPoint "
            "object; only a complete Slide-local proxy may be removed to "
            "delete that object"
        )
    deleted_external_linked_image_refs = sorted(
        source_ref
        for source_ref in deleted_refs & manifest_proxy_refs
        if baseline_occurrences[source_ref][0].element.get(
            EXTERNAL_LINKED_IMAGE_PROXY_ATTRIBUTE
        ) == "true"
    )
    if deleted_external_linked_image_refs:
        raise AuthoringRoundtripError(
            f"{document.name} deletes externally linked image proxy object(s): "
            + ", ".join(deleted_external_linked_image_refs)
            + "; keep each proxy unchanged to restore its original external "
            "picture relationship"
        )

    warnings: list[dict[str, Any]] = []

    def restore_nodes(
        element: ET.Element,
        source_dir: Path | None = None,
    ) -> list[ET.Element]:
        source_dir = source_dir or document.authoring_path.parent
        source_ref = element.get(SOURCE_REF_ATTRIBUTE)
        if source_ref in unchanged_refs:
            scope = source_ref.split(":", 1)[0]
            if scope != "slide":
                return []
            source = layered_index.get(source_ref)
            if source is None:
                raise AuthoringRoundtripError(
                    f"{document.name} cannot find {source_ref!r} in layered source"
                )
            return [_native_restore_placeholder(source, source_ref)]

        if _local_name(element.tag) == "use":
            icon = (element.get("data-icon") or "").strip()
            referenced = current_referenced_assets.get(icon)
            if referenced is not None:
                record, asset_root = referenced
                outer_refs = _outer_source_ref_elements(asset_root)
                if outer_refs:
                    restored: list[ET.Element] = []
                    for outer in outer_refs:
                        restored.extend(
                            restore_nodes(outer, record.asset_path.parent)
                        )
                    context_attrs = {
                        name: value for name, value in element.attrib.items()
                        if name in {*INHERITABLE_ATTRS, "style", "opacity", "transform"}
                    }
                    if context_attrs:
                        wrapper = ET.Element(f"{{{SVG_NS}}}g", context_attrs)
                        wrapper.extend(restored)
                        return [wrapper]
                    return restored

        clone = copy.deepcopy(element)
        for child in list(clone):
            clone.remove(child)
        for original_child in element:
            replacements = restore_nodes(original_child, source_dir)
            if replacements:
                replacements[-1].tail = original_child.tail
            for replacement in replacements:
                clone.append(replacement)
        if _local_name(clone.tag) == "a" and not len(clone) and any(
            item.get(SOURCE_REF_ATTRIBUTE) in unchanged_refs for item in element.iter()
        ):
            return []  # Its inherited native objects stay on the Master/Layout.
        clone.attrib.pop(SOURCE_REF_ATTRIBUTE, None)
        remap_link(clone, element)
        _rebase_resource_references(
            clone,
            source_dir,
            output_path.parent,
            project_path,
            recursive=False,
        )
        if source_ref:
            source = layered_index.get(source_ref)
            if source is not None:
                if source_ref.startswith("slide:"):
                    for name in (
                        "data-pptx-shape-id",
                        "data-pptx-shape-scope",
                        "data-pptx-shape-name",
                        "data-name",
                    ):
                        if clone.get(name) is None and source.get(name) is not None:
                            clone.set(name, str(source.get(name)))
                _restore_preserved_effect_metadata(clone, source)
                package, reason = prepare_imported_chart_edit(
                    element, baseline_occurrences[source_ref][0].element, source,
                    current_root=current_occurrences[source_ref][0].root,
                    baseline_root=baseline_occurrences[source_ref][0].root,
                )
                if package is not None:
                    metadata = clone.find(f'{{{SVG_NS}}}metadata[@type="application/json"]')
                    payload = _load_payload(clone, "chart")
                    payload["source_package"] = package
                    metadata.text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
                    clone.set("data-pptx-roundtrip-object", "source-chart-package")
                    # Older normalized previews emitted invisible one-point
                    # polylines at gaps. The closed native package retains
                    # their data; markers and labels remain in the preview.
                    removed = 0
                    if clone.get("data-pptx-fallback-kind") == "normalized":
                        for parent in clone.iter():
                            for child in list(parent):
                                if child.tag != f"{{{SVG_NS}}}polyline":
                                    continue
                                try:
                                    points = parse_svg_points(child.get("points", ""), min_points=1)
                                except ValueError:
                                    continue  # The geometry validator still owns malformed points.
                                if len(points) == 1:
                                    parent.remove(child)
                                    removed += 1
                    if removed:
                        message = (f"Omitted {removed} invisible isolated line preview segment(s); "
                                   "chart values, gaps, markers and labels are preserved. "
                                   "Reimport the source deck to refresh the stored preview.")
                        warnings.append({
                            "code": "native-chart-empty-line-segment", "source_ref": source_ref,
                            "message": message,
                        })
                        print(f"[ROUNDTRIP][WARNING] {source_ref}: {message}", file=sys.stderr)
                if reason:
                    message = chart_rebuild_warning(project_path, document.source_slide, source_ref, reason)
                    object_id = element.get("id") or source_ref
                    output_index = page.output_index if page else document.source_slide
                    warnings.append({
                        "code": "native-chart-rebuilt", "output_page": output_index,
                        "source_slide": document.source_slide, "object_id": object_id,
                        "source_ref": source_ref, "message": message,
                    })
                    print(f"[ROUNDTRIP][WARNING] page={output_index} object={object_id} "
                          f"source_ref={source_ref}: {message}", file=sys.stderr)
                warning = _restore_unchanged_text_body(
                    clone, source, current_occurrences[source_ref][0], baseline_occurrences[source_ref][0],
                )
                if warning:
                    object_id = element.get("id") or source_ref
                    output_index = page.output_index if page else document.source_slide
                    warnings.append({
                        "code": "native-formula-to-text",
                        "output_page": output_index,
                        "source_slide": document.source_slide,
                        "object_id": object_id,
                        "source_ref": source_ref,
                        "message": warning,
                    })
                    print(
                        f"[ROUNDTRIP][WARNING] page={output_index} ({document.name}) "
                        f"object={object_id} source_ref={source_ref}: {warning}",
                        file=sys.stderr,
                    )
        if (
            _local_name(clone.tag) == "g" and source_ref is None
            and set(clone.attrib) <= {*INHERITABLE_ATTRS, "style", "opacity", "transform"}
            and len(clone) > 1
            and all(_local_name(child.tag) not in {"defs", "metadata"} for child in clone)
        ):
            # A context-only wrapper must not merge separate source objects
            # into one new DrawingML group, which would lose their ownership.
            resolved = []
            for child in clone:
                contextual = ET.Element(clone.tag, clone.attrib)
                contextual.append(child)
                resolved.append(contextual)
            return resolved
        return [clone]

    baseline_unreferenced = Counter(
        normalized_authoring_subtree_sha256(child, baseline_assets)
        for child in baseline_root
        if _local_name(child.tag) != "defs"
        and child.get(SOURCE_REF_ATTRIBUTE) is None
        and _local_name(child.tag) != "use"
    )
    output_root = copy.deepcopy(layered_root)
    for child in list(output_root):
        output_root.remove(child)
    _apply_authoring_root_attributes(output_root, current_root)
    layered_defs_root = copy.deepcopy(layered_root)
    current_defs_root = copy.deepcopy(current_root)
    _rebase_resource_references(
        layered_defs_root,
        document.layered_source_path.parent,
        output_path.parent,
        project_path,
        recursive=True,
    )
    _rebase_resource_references(
        current_defs_root,
        document.authoring_path.parent,
        output_path.parent,
        project_path,
        recursive=True,
    )
    merged_defs = _merge_defs(
        layered_defs_root,
        current_defs_root,
        include_authoring_changes=defs_changed or bool(edited_refs),
    )
    merged_definition_ids = {
        definition_id
        for definition in (list(merged_defs) if merged_defs is not None else [])
        if (definition_id := definition.get("id"))
    }
    merged_definition_hashes = {
        semantic_subtree_sha256(definition)
        for definition in (list(merged_defs) if merged_defs is not None else [])
    }
    for record, asset_root in current_referenced_assets.values():
        asset_defs = _direct_defs(asset_root)
        if (
            record.unchanged
            or asset_defs is None
            or not _outer_source_ref_elements(asset_root)
        ):
            continue
        if merged_defs is None:
            merged_defs = ET.Element(f"{{{SVG_NS}}}defs")
        for definition in asset_defs:
            clone = copy.deepcopy(definition)
            _rebase_resource_references(
                clone,
                record.asset_path.parent,
                output_path.parent,
                project_path,
                recursive=True,
            )
            definition_id = clone.get("id")
            definition_hash = semantic_subtree_sha256(clone)
            if definition_hash in merged_definition_hashes:
                continue
            if definition_id and definition_id in merged_definition_ids:
                raise AuthoringRoundtripError(
                    f"{document.name} edited vector asset definition id "
                    f"collides with page definitions: {definition_id!r}"
                )
            merged_defs.append(clone)
            merged_definition_hashes.add(definition_hash)
            if definition_id:
                merged_definition_ids.add(definition_id)
    if merged_defs is not None and list(merged_defs):
        output_root.append(merged_defs)

    authored_unreferenced = 0
    expanded_vector_uses = 0
    for child in current_root:
        if _local_name(child.tag) == "defs":
            continue
        if (
            child.get(SOURCE_REF_ATTRIBUTE) is None
            and _local_name(child.tag) != "use"
        ):
            child_hash = normalized_authoring_subtree_sha256(
                child,
                current_assets,
            )
            if baseline_unreferenced[child_hash] > 0:
                baseline_unreferenced[child_hash] -= 1
                if not any(
                    item.get(SOURCE_REF_ATTRIBUTE) or item.get("data-icon") in current_assets
                    for item in child.iter()
                ):
                    continue
            else:
                authored_unreferenced += 1
        replacements = restore_nodes(child)
        if _local_name(child.tag) == "use" and len(replacements) > 1:
            expanded_vector_uses += 1
        for replacement in replacements:
            output_root.append(replacement)

    _prune_unreferenced_definitions(output_root)
    residual_refs = sorted({
        source_ref
        for element in output_root.iter()
        if (source_ref := element.get(SOURCE_REF_ATTRIBUTE))
    })
    if residual_refs:
        raise AuthoringRoundtripError(
            f"{document.name} materialization left source refs: {residual_refs[:5]}"
        )
    output_path.write_bytes(_serialize_svg(output_root))
    return {
        "file": document.name,
        "source_file": document.source_name,
        "source_slide": document.source_slide,
        "document_unchanged": document_unchanged,
        "source_ref_count": len(expected_refs),
        "source_ref_ids": sorted(expected_refs),
        "unchanged_refs": len(unchanged_refs),
        "unchanged_ref_ids": sorted(unchanged_refs),
        "edited_refs": len(edited_refs),
        "edited_ref_ids": sorted(edited_refs),
        "deleted_refs": len(deleted_refs),
        "deleted_ref_ids": sorted(deleted_refs),
        "authored_unreferenced": authored_unreferenced,
        "expanded_vector_uses": expanded_vector_uses,
        "defs_changed": defs_changed,
        "warnings": warnings,
    }


