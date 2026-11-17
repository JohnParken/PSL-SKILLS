"""Core PPTX assembly: create_pptx_with_native_svg."""
# Adapted from hugohe3/ppt-master 7e0df6705de0cd24345210d31f07fcb5a32b6900.
# MIT; Copyright (c) 2025-2026 Hugo He. See references/LICENSE-PPT-MASTER.txt and references/upstream.md.

from __future__ import annotations
import sys
from pathlib import (
    Path,
)
from typing import Any
from xml.etree import ElementTree as ET
from pptx_transitions import (
    serialize_source_xml,
    transition_carriers,
)
from .narration import (
    next_shape_id,
)
from .template_structure import (
    TemplateStructureError,
)

from .builder import (
    RoundtripSlidePatch,
)

def _apply_roundtrip_slide_overlay(
    source_slide: bytes,
    source_rels: bytes,
    generated_slide_path: Path,
    generated_rels_path: Path,
    patch: RoundtripSlidePatch,
    conversion_trace: dict[str, Any] | None,
) -> dict[int, int]:
    """Overlay authored Slide objects onto the exact source slide package part."""
    from .builder import (
        DML_NS,
        PML_NS,
        _TOP_LEVEL_SHAPE_TAGS,
        _direct_shape_id,
        _merge_roundtrip_relationships,
        _preserve_roundtrip_timing_markup,
        _preserve_roundtrip_transition_markup,
        _relationship_ids_in_shape,
        _renumber_roundtrip_generated_shapes,
        _replace_roundtrip_motion,
        _restore_roundtrip_nested_source_shapes,
        _rewrite_roundtrip_timing_shape_ids,
        _roundtrip_nested_restore_ids,
        _roundtrip_shape_sort_key,
        _roundtrip_source_shape_roster,
        _shape_id,
        _shape_ids_in_element,
        _slide_ref_shape_ids,
        _timing_shape_ids,
        _trace_native_shape_ids,
    )
    try:
        source_root = ET.fromstring(source_slide)
        generated_tree = ET.parse(generated_slide_path)
    except ET.ParseError as exc:
        raise TemplateStructureError(
            "Round-trip source or generated slide is not valid XML"
        ) from exc
    generated_root = generated_tree.getroot()

    source_tree = source_root.find(
        f".//{{{PML_NS}}}cSld/{{{PML_NS}}}spTree"
    )
    generated_sp_tree = generated_root.find(
        f".//{{{PML_NS}}}cSld/{{{PML_NS}}}spTree"
    )
    if source_tree is None or generated_sp_tree is None:
        raise TemplateStructureError(
            "Round-trip slide overlay requires source and generated p:spTree"
        )

    (
        source_shapes,
        source_descendant_ids,
        source_key_by_element,
        all_source_shapes,
    ) = _roundtrip_source_shape_roster(source_tree)

    generated_order: list[str] = []
    generated_shapes: dict[str, ET.Element] = {}
    generated_top_id_by_descendant: dict[str, str] = {}
    for child in generated_sp_tree:
        if child.tag not in _TOP_LEVEL_SHAPE_TAGS:
            continue
        shape_id = _shape_id(child)
        if not shape_id:
            raise TemplateStructureError(
                "Generated round-trip slide contains a top-level shape without id"
            )
        if shape_id in generated_shapes:
            raise TemplateStructureError(
                f"Generated round-trip slide repeats top-level shape id {shape_id}"
            )
        generated_order.append(shape_id)
        generated_shapes[shape_id] = child
        for descendant_id in _shape_ids_in_element(child):
            previous = generated_top_id_by_descendant.setdefault(
                descendant_id,
                shape_id,
            )
            if previous != shape_id:
                raise TemplateStructureError(
                    "Generated round-trip slide repeats a shape id across "
                    f"top-level objects: {descendant_id}"
                )

    source_ref_ids = _slide_ref_shape_ids(patch.source_ref_ids)
    edited_ids = _slide_ref_shape_ids(patch.edited_ref_ids)
    deleted_ids = _slide_ref_shape_ids(patch.deleted_ref_ids)
    unchanged_ids = source_ref_ids - edited_ids - deleted_ids
    affected_top_ids = {
        shape_id
        for shape_id, descendant_ids in source_descendant_ids.items()
        if descendant_ids & (edited_ids | deleted_ids)
    }
    nested_restore_ids = _roundtrip_nested_restore_ids(
        source_shapes,
        source_key_by_element,
        affected_top_ids,
        unchanged_ids,
        deleted_ids,
    )
    authored_top_ids = {
        shape_id
        for shape_id in source_shapes
        if shape_id in source_ref_ids or shape_id in affected_top_ids
    }
    deleted_top_ids = authored_top_ids & deleted_ids
    traced_ids = _trace_native_shape_ids(conversion_trace)
    generated_by_source_id: dict[str, ET.Element] = {}
    source_id_by_generated_top_id: dict[str, str] = {}
    for source_id in authored_top_ids - deleted_top_ids:
        candidate_ids = [source_id]
        candidate_ids.extend(traced_ids.get(f"shape-{source_id}", []))
        generated_top_ids = {
            generated_top_id_by_descendant[candidate_id]
            for candidate_id in candidate_ids
            if candidate_id in generated_top_id_by_descendant
        }
        if len(generated_top_ids) > 1:
            raise TemplateStructureError(
                "Generated round-trip source object maps to multiple top-level "
                f"shapes: {source_id}"
            )
        if not generated_top_ids:
            continue
        generated_top_id = next(iter(generated_top_ids))
        previous_source_id = source_id_by_generated_top_id.setdefault(
            generated_top_id,
            source_id,
        )
        if previous_source_id != source_id:
            raise TemplateStructureError(
                "Generated round-trip top-level shape maps to multiple source "
                f"objects: {previous_source_id}, {source_id}"
            )
        generated_by_source_id[source_id] = generated_shapes[generated_top_id]

    authored_order = [
        source_id_by_generated_top_id[generated_top_id]
        for generated_top_id in generated_order
        if generated_top_id in source_id_by_generated_top_id
    ]
    if len(authored_order) != len(set(authored_order)):
        raise TemplateStructureError(
            "Generated round-trip slide has an ambiguous authored shape order"
        )
    missing_authored = (
        authored_top_ids - deleted_top_ids - set(authored_order)
    )
    if missing_authored:
        raise TemplateStructureError(
            "Edited round-trip source object did not produce a DrawingML shape: "
            + ", ".join(sorted(missing_authored, key=_roundtrip_shape_sort_key))
        )

    selected_by_id: dict[str, tuple[ET.Element, bool]] = {}
    for shape_id in authored_order:
        if shape_id in affected_top_ids:
            selected_by_id[shape_id] = (
                generated_by_source_id[shape_id],
                True,
            )
        else:
            selected_by_id[shape_id] = (source_shapes[shape_id], False)
    # New objects keep their SVG paint position: each one follows the nearest
    # preceding authored object, and leading ones precede the first slot.
    extra_generated: dict[str | None, list[ET.Element]] = {}
    preceding_authored_id: str | None = None
    for shape_id in generated_order:
        if shape_id in source_id_by_generated_top_id:
            preceding_authored_id = source_id_by_generated_top_id[shape_id]
        else:
            extra_generated.setdefault(preceding_authored_id, []).append(
                generated_shapes[shape_id]
            )

    authored_iterator = iter(authored_order)
    merged_children: list[tuple[ET.Element, bool]] = []
    final_authored_slot = 0
    anchor_slots: dict[str | None, int] = {}
    for child in list(source_tree):
        shape_id = (
            source_key_by_element.get(id(child))
            if child.tag in _TOP_LEVEL_SHAPE_TAGS
            else None
        )
        if shape_id not in authored_top_ids:
            merged_children.append((child, False))
            continue
        anchor_slots.setdefault(None, len(merged_children))
        next_shape_id = next(authored_iterator, None)
        if next_shape_id is not None:
            merged_children.append(selected_by_id[next_shape_id])
            anchor_slots[next_shape_id] = len(merged_children)
        final_authored_slot = len(merged_children)
    if next(authored_iterator, None) is not None:
        raise TemplateStructureError(
            "Generated authored shape roster exceeds the source overlay slots"
        )
    if not authored_top_ids:
        anchor_slots[None] = next(
            (
                index
                for index, (element, _generated) in enumerate(merged_children)
                if element.tag in _TOP_LEVEL_SHAPE_TAGS
            ),
            len(merged_children),
        )
    if authored_order:
        # Trailing additions stay after every source authored slot, so unlisted
        # source shapes past the last filled slot keep painting below them.
        anchor_slots[authored_order[-1]] = final_authored_slot
    # Anchors ascend in authored order; splice from the back to keep indexes valid.
    for anchor, elements in reversed(list(extra_generated.items())):
        insert_at = anchor_slots[anchor]
        merged_children[insert_at:insert_at] = [
            (element, True) for element in elements
        ]

    generated_nodes = [
        element for element, generated in merged_children if generated
    ]
    retained_source_nodes = [
        element for element, generated in merged_children if not generated
    ]
    required_generated_relationship_ids = {
        relationship_id
        for element in generated_nodes
        for relationship_id in _relationship_ids_in_shape(element)
    }
    if patch.transition_changed:
        for carrier in transition_carriers(generated_root):
            required_generated_relationship_ids.update(
                _relationship_ids_in_shape(carrier)
            )
    if patch.animation_changed:
        motion_node = generated_root.find(f"{{{PML_NS}}}timing")
        if motion_node is not None:
            required_generated_relationship_ids.update(
                _relationship_ids_in_shape(motion_node)
            )
    _merge_roundtrip_relationships(
        source_rels,
        generated_rels_path,
        generated_root,
        required_generated_relationship_ids,
    )
    restored_nested_nodes = _restore_roundtrip_nested_source_shapes(
        generated_nodes,
        all_source_shapes,
        nested_restore_ids,
        traced_ids,
    )
    # Keep source attachments in their original id space while generated shapes
    # and their attachments are renumbered. Restored nested connectors are source
    # objects too, even though their outer group was rebuilt.
    source_connections = [
        (connector, properties, connection, connection.get("id"))
        for element in [*retained_source_nodes, *restored_nested_nodes]
        for connector in element.iter(f"{{{PML_NS}}}cxnSp")
        for properties in connector.iter(f"{{{PML_NS}}}cNvCxnSpPr")
        for connection in properties
        if connection.tag in {f"{{{DML_NS}}}stCxn", f"{{{DML_NS}}}endCxn"}
    ]
    if patch.transition_changed or patch.animation_changed:
        _replace_roundtrip_motion(
            source_root,
            generated_root,
            transition_changed=patch.transition_changed,
            transition_replaced=patch.transition_replaced,
            animation_changed=patch.animation_changed,
            advance_changed=patch.advance_changed,
        )
    restored_shape_id_map = {
        generated_top_id: source_id
        for generated_top_id, source_id in source_id_by_generated_top_id.items()
        if source_id not in affected_top_ids
        and generated_top_id != source_id
    }
    if patch.animation_changed:
        _rewrite_roundtrip_timing_shape_ids(
            source_root,
            restored_shape_id_map,
        )
    renumbered_shape_id_map = _renumber_roundtrip_generated_shapes(
        generated_nodes,
        retained_source_nodes,
        source_root,
        rewrite_timing=patch.animation_changed,
    )

    for child in list(source_tree):
        source_tree.remove(child)
    for child, _generated in merged_children:
        source_tree.append(child)

    visible_shape_ids = {
        value
        for element in source_tree
        for value in _shape_ids_in_element(element)
    }
    final_shapes = {
        _direct_shape_id(element): element
        for element in source_tree.iter()
        if element.tag in _TOP_LEVEL_SHAPE_TAGS
    }
    for connector, properties, connection, source_target in source_connections:
        target = source_target
        if source_target in edited_ids:
            candidates = {
                renumbered_shape_id_map.get(candidate, candidate)
                for candidate in traced_ids.get(f"shape-{source_target}", [])
            } & visible_shape_ids
            if len(candidates) > 1:
                connector_id = _direct_shape_id(connector)
                endpoint = connection.tag.rsplit("}", 1)[-1]
                message = (
                    f"{generated_slide_path.name}: connector {connector_id} {endpoint} "
                    f"endpoint {source_target} maps to multiple edited objects; "
                    "removed this attachment and preserved connector geometry. "
                    "Reconnect this end to the intended object in PowerPoint, or keep "
                    "one endpoint object in the authoring SVG before exporting again."
                )
                if patch.warnings is not None:
                    patch.warnings.append({
                        "code": "roundtrip-connector-attachment-detached",
                        "connector_id": connector_id,
                        "endpoint": endpoint,
                        "source_target_id": source_target,
                        "candidate_ids": sorted(candidates, key=_roundtrip_shape_sort_key),
                        "message": message,
                    })
                print(f"[ROUNDTRIP][WARNING] {message}", file=sys.stderr)
                target = None
            elif candidates:
                target = next(iter(candidates))
                generated_endpoint = final_shapes[target]
                source_endpoint = all_source_shapes.get(source_target)
                if source_endpoint is not None and generated_endpoint.tag != source_endpoint.tag:
                    # A top-level picture may acquire an animation wrapper. Its
                    # connection sites still belong to the picture inside it.
                    leaves = list(generated_endpoint.iter(source_endpoint.tag))
                    if len(leaves) == 1:
                        target = _direct_shape_id(leaves[0])
        if source_target in deleted_ids or target not in visible_shape_ids:
            properties.remove(connection)
        elif target != connection.get("id"):
            connection.set("id", target)
    if not patch.animation_changed:
        missing_timing_targets = _timing_shape_ids(source_root) - visible_shape_ids
        if missing_timing_targets:
            raise TemplateStructureError(
                "Edited slide removed source animation target(s); update "
                "animations.json explicitly: "
                + ", ".join(sorted(missing_timing_targets, key=int))
            )
    serialized_slide = serialize_source_xml(source_root, source_slide)
    if not patch.transition_changed:
        serialized_slide = _preserve_roundtrip_transition_markup(
            serialized_slide,
            source_slide,
        )
    if not patch.animation_changed:
        serialized_slide = _preserve_roundtrip_timing_markup(
            serialized_slide,
            source_slide,
        )
    generated_slide_path.write_bytes(serialized_slide)
    combined_id_map = {
        **restored_shape_id_map,
        **renumbered_shape_id_map,
    }
    return {
        int(old_id): int(new_id)
        for old_id, new_id in combined_id_map.items()
        if old_id.isdigit() and new_id.isdigit()
    }
