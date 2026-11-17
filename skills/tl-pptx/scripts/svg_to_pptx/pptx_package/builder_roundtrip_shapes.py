"""Core PPTX assembly: create_pptx_with_native_svg."""
# Adapted from hugohe3/ppt-master 7e0df6705de0cd24345210d31f07fcb5a32b6900.
# MIT; Copyright (c) 2025-2026 Hugo He. See references/LICENSE-PPT-MASTER.txt and references/upstream.md.

from __future__ import annotations
import re
from pathlib import (
    Path,
)
from xml.etree import ElementTree as ET
from pptx_transitions import (
    transition_carriers,
)
from .template_structure import (
    TemplateStructureError,
)

def _roundtrip_synthetic_shape_id(source_order: tuple[int, ...]) -> str:
    """Return the importer's stable identity for a shape with no native id."""
    return "missing-" + "-".join(str(value) for value in source_order)


def _direct_shape_id(element: ET.Element) -> str | None:
    """Return one shape's own cNvPr id without descending into child shapes."""
    from .builder import (
        MC_NS,
        PML_NS,
        _shape_id,
    )
    if element.tag == f"{{{MC_NS}}}AlternateContent":
        return _shape_id(element)
    for child in element:
        c_nv_pr = child.find(f"{{{PML_NS}}}cNvPr")
        if c_nv_pr is not None:
            return c_nv_pr.get("id")
    return None


def _roundtrip_shape_id_closure(
    element: ET.Element,
    source_order: tuple[int, ...],
) -> set[str]:
    """Return native and synthetic identities in one source shape subtree."""
    from .builder import (
        PML_NS,
        _TOP_LEVEL_SHAPE_TAGS,
        _shape_ids_in_element,
    )
    identity = _direct_shape_id(element) or _roundtrip_synthetic_shape_id(
        source_order
    )
    identities = _shape_ids_in_element(element) | {identity}
    if element.tag != f"{{{PML_NS}}}grpSp":
        return identities
    child_order = 0
    for child in element:
        if child.tag not in _TOP_LEVEL_SHAPE_TAGS:
            continue
        child_order += 1
        identities.update(
            _roundtrip_shape_id_closure(
                child,
                (*source_order, child_order),
            )
        )
    return identities


def _index_roundtrip_source_shape(
    element: ET.Element,
    source_order: tuple[int, ...],
    shapes: dict[str, ET.Element],
    key_by_element: dict[int, str],
) -> None:
    """Index one native source shape subtree by stable round-trip identity."""
    from .builder import (
        PML_NS,
        _TOP_LEVEL_SHAPE_TAGS,
    )
    shape_id = _direct_shape_id(element) or _roundtrip_synthetic_shape_id(
        source_order
    )
    if shape_id in shapes:
        raise TemplateStructureError(
            f"Round-trip source slide repeats shape id {shape_id}"
        )
    shapes[shape_id] = element
    key_by_element[id(element)] = shape_id
    if element.tag != f"{{{PML_NS}}}grpSp":
        return
    child_order = 0
    for child in element:
        if child.tag not in _TOP_LEVEL_SHAPE_TAGS:
            continue
        child_order += 1
        _index_roundtrip_source_shape(
            child,
            (*source_order, child_order),
            shapes,
            key_by_element,
        )


def _roundtrip_source_shape_roster(
    source_tree: ET.Element,
) -> tuple[
    dict[str, ET.Element],
    dict[str, set[str]],
    dict[int, str],
    dict[str, ET.Element],
]:
    """Index source top-level shapes without mutating missing native ids."""
    from .builder import (
        _TOP_LEVEL_SHAPE_TAGS,
    )
    shapes: dict[str, ET.Element] = {}
    descendant_ids: dict[str, set[str]] = {}
    key_by_element: dict[int, str] = {}
    all_shapes: dict[str, ET.Element] = {}
    source_order = 0
    for child in source_tree:
        if child.tag not in _TOP_LEVEL_SHAPE_TAGS:
            continue
        source_order += 1
        order_path = (source_order,)
        _index_roundtrip_source_shape(
            child,
            order_path,
            all_shapes,
            key_by_element,
        )
        shape_id = key_by_element[id(child)]
        shapes[shape_id] = child
        descendant_ids[shape_id] = _roundtrip_shape_id_closure(
            child,
            order_path,
        )
    return shapes, descendant_ids, key_by_element, all_shapes


def _roundtrip_nested_restore_ids(
    source_shapes: dict[str, ET.Element],
    source_key_by_element: dict[int, str],
    affected_top_ids: set[str],
    unchanged_ids: set[str],
    deleted_ids: set[str],
) -> set[str]:
    """Return outermost unchanged source shapes inside rebuilt top-level groups."""
    from .builder import (
        PML_NS,
        _TOP_LEVEL_SHAPE_TAGS,
    )
    restore_ids: set[str] = set()

    def visit(element: ET.Element) -> None:
        shape_id = source_key_by_element[id(element)]
        if shape_id in unchanged_ids:
            restore_ids.add(shape_id)
            return
        if shape_id in deleted_ids or element.tag != f"{{{PML_NS}}}grpSp":
            return
        for child in element:
            if child.tag in _TOP_LEVEL_SHAPE_TAGS:
                visit(child)

    for shape_id in affected_top_ids:
        visit(source_shapes[shape_id])
    return restore_ids


def _restore_roundtrip_nested_source_shapes(
    generated_nodes: list[ET.Element],
    source_shapes: dict[str, ET.Element],
    restore_ids: set[str],
    traced_ids: dict[str, list[str]],
) -> list[ET.Element]:
    """Replace converter-safe nested placeholders with exact source shapes."""
    from .builder import (
        PML_NS,
        _TOP_LEVEL_SHAPE_TAGS,
    )
    locations: dict[str, tuple[ET.Element, ET.Element]] = {}

    def index_children(parent: ET.Element) -> None:
        for child in parent:
            if child.tag not in _TOP_LEVEL_SHAPE_TAGS:
                continue
            shape_id = _direct_shape_id(child)
            if shape_id is not None:
                previous = locations.setdefault(shape_id, (parent, child))
                if previous[1] is not child:
                    raise TemplateStructureError(
                        "Generated round-trip slide repeats nested shape id "
                        f"{shape_id}"
                    )
            if child.tag == f"{{{PML_NS}}}grpSp":
                index_children(child)

    for node in generated_nodes:
        if node.tag == f"{{{PML_NS}}}grpSp":
            index_children(node)

    restored_nodes: list[ET.Element] = []
    for source_id in sorted(restore_ids, key=_roundtrip_shape_sort_key):
        candidate_ids = {source_id}
        candidate_ids.update(traced_ids.get(f"shape-{source_id}", []))
        matches = {
            candidate_id: locations[candidate_id]
            for candidate_id in candidate_ids
            if candidate_id in locations
        }
        if len(matches) != 1:
            raise TemplateStructureError(
                "Unchanged nested round-trip source object did not produce "
                f"exactly one restore placeholder: {source_id}"
            )
        parent, placeholder = next(iter(matches.values()))
        source = source_shapes.get(source_id)
        if source is None:
            raise TemplateStructureError(
                f"Round-trip source shape is missing for nested ref {source_id}"
            )
        clone = ET.fromstring(ET.tostring(source, encoding="utf-8"))
        clone.tail = placeholder.tail
        position = list(parent).index(placeholder)
        parent.remove(placeholder)
        parent.insert(position, clone)
        restored_nodes.append(clone)
    return restored_nodes


def _roundtrip_shape_sort_key(value: str) -> tuple[int, int | str]:
    """Sort native numeric ids before stable synthetic ids."""
    return (0, int(value)) if value.isdigit() else (1, value)


def _relationship_key(attrs: dict[str, str]) -> tuple[str, str, str | None]:
    """Return the identity used to reuse one source relationship."""
    return (
        attrs.get("Type", ""),
        attrs.get("Target", ""),
        attrs.get("TargetMode"),
    )


def _merge_roundtrip_relationships(
    source_rels: bytes,
    generated_rels_path: Path,
    generated_slide_root: ET.Element,
    required_generated_ids: set[str],
) -> None:
    """Retarget generated references into the preserved source rel roster."""
    from .builder import (
        _REL_ATTRS,
        _relationship_attrs,
        _write_xml_tree,
    )
    try:
        source_root = ET.fromstring(source_rels)
        generated_root = ET.parse(generated_rels_path).getroot()
    except ET.ParseError as exc:
        raise TemplateStructureError(
            "Round-trip slide relationships are not valid XML"
        ) from exc

    source_by_key: dict[tuple[str, str, str | None], str] = {}
    used_ids: set[str] = set()
    for relationship in source_root:
        attrs = _relationship_attrs(relationship)
        rel_id = attrs.get("Id")
        if not rel_id:
            continue
        used_ids.add(rel_id)
        source_by_key.setdefault(_relationship_key(attrs), rel_id)
    numeric_ids = [
        int(match.group(1))
        for rel_id in used_ids
        if (match := re.fullmatch(r"rId(\d+)", rel_id)) is not None
    ]
    next_id = max(numeric_ids, default=0) + 1

    id_map: dict[str, str] = {}
    added_relationship = False
    for relationship in generated_root:
        attrs = _relationship_attrs(relationship)
        generated_id = attrs.get("Id")
        if not generated_id:
            raise TemplateStructureError(
                "Generated round-trip relationship has no Id"
            )
        if generated_id not in required_generated_ids:
            continue
        existing_id = source_by_key.get(_relationship_key(attrs))
        if existing_id is not None:
            id_map[generated_id] = existing_id
            continue

        assigned_id = generated_id
        if assigned_id in used_ids:
            while f"rId{next_id}" in used_ids:
                next_id += 1
            assigned_id = f"rId{next_id}"
            next_id += 1
        clone = ET.fromstring(ET.tostring(relationship, encoding="utf-8"))
        clone.set("Id", assigned_id)
        source_root.append(clone)
        added_relationship = True
        used_ids.add(assigned_id)
        source_by_key[_relationship_key(attrs)] = assigned_id
        id_map[generated_id] = assigned_id

    missing_relationships = required_generated_ids - set(id_map)
    if missing_relationships:
        raise TemplateStructureError(
            "Generated round-trip slide references missing relationship(s): "
            + ", ".join(sorted(missing_relationships))
        )

    for node in generated_slide_root.iter():
        for attr_name, value in list(node.attrib.items()):
            if attr_name in _REL_ATTRS and value in id_map:
                node.set(attr_name, id_map[value])
    if added_relationship:
        _write_xml_tree(generated_rels_path, ET.ElementTree(source_root))
    else:
        generated_rels_path.write_bytes(source_rels)


def _replace_roundtrip_motion(
    source_root: ET.Element,
    generated_root: ET.Element,
    *,
    transition_changed: bool,
    transition_replaced: bool,
    animation_changed: bool,
    advance_changed: bool = True,
) -> None:
    """Replace only explicitly changed transition and animation state."""
    from .builder import (
        PML_NS,
        _replace_roundtrip_transition_advance,
    )
    if transition_changed and transition_replaced:
        if not advance_changed:
            _replace_roundtrip_transition_advance(generated_root, source_root)
        generated_carriers = transition_carriers(generated_root)
        if len(generated_carriers) > 1:
            raise TemplateStructureError(
                "Generated round-trip slide has multiple transition carriers"
            )
        for carrier in transition_carriers(source_root):
            source_root.remove(carrier)
        if generated_carriers:
            clone = ET.fromstring(
                ET.tostring(generated_carriers[0], encoding="utf-8")
            )
            children = list(source_root)
            insert_at = next(
                (
                    index
                    for index, child in enumerate(children)
                    if child.tag in {
                        f"{{{PML_NS}}}timing",
                        f"{{{PML_NS}}}extLst",
                    }
                ),
                len(children),
            )
            source_root.insert(insert_at, clone)
    elif transition_changed:
        _replace_roundtrip_transition_advance(source_root, generated_root)

    if animation_changed:
        timing_tag = f"{{{PML_NS}}}timing"
        existing_timing = source_root.find(timing_tag)
        generated_timing = generated_root.find(timing_tag)
        if existing_timing is not None:
            source_root.remove(existing_timing)
        if generated_timing is None:
            return
        clone = ET.fromstring(ET.tostring(generated_timing, encoding="utf-8"))
        children = list(source_root)
        insert_at = next(
            (
                index
                for index, child in enumerate(children)
                if child.tag == f"{{{PML_NS}}}extLst"
            ),
            len(children),
        )
        source_root.insert(insert_at, clone)
