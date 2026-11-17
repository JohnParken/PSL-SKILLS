"""Core PPTX assembly: create_pptx_with_native_svg."""
# Adapted from hugohe3/ppt-master 7e0df6705de0cd24345210d31f07fcb5a32b6900.
# MIT; Copyright (c) 2025-2026 Hugo He. See references/LICENSE-PPT-MASTER.txt and references/upstream.md.

from __future__ import annotations
from pathlib import (
    Path,
)
from xml.etree import ElementTree as ET
from ..drawingml.utils import (
    EMU_PER_PX,
)
from .template_structure import (
    TemplateElementSpec,
    TemplateStructureError,
)

from .builder import (
    _TemplateRuntimeSlide,
)

def _apply_template_slide_backgrounds(
    states: list[_TemplateRuntimeSlide],
    slide_size_emu: tuple[int, int],
) -> dict[str, str]:
    """Compile one-page solid backgrounds into slide-level p:bg."""
    from .builder import (
        PML_NS,
        _remove_template_shape,
        _set_slide_tree_background,
        _solid_background_xml_from_shape,
        _template_shape_for_item,
    )
    applied: dict[str, str] = {}
    for state in states:
        items = [
            item for item in state.spec.elements
            if item.layer == "slide" and item.is_background
        ]
        if not items:
            continue
        item = items[0]
        shape = _template_shape_for_item(state, item)
        if shape is None:
            c_sld = state.root.find(f"{{{PML_NS}}}cSld")
            background = (
                c_sld.find(f"{{{PML_NS}}}bg")
                if c_sld is not None
                else None
            )
            if background is None:
                raise TemplateStructureError(
                    f"{state.spec.svg_path.name}: slide background disappeared "
                    "during explicit Layout structure assembly"
                )
            applied[f"ppt/slides/slide{state.spec.slide_num}.xml"] = ET.tostring(
                background,
                encoding="unicode",
            )
            continue
        background_xml = _solid_background_xml_from_shape(shape, slide_size_emu)
        if background_xml is None:
            raise TemplateStructureError(
                f"{state.spec.svg_path.name}: {item.element_id!r} must remain an "
                "exact full-slide solid rectangle"
            )
        _remove_template_shape(state, shape)
        _set_slide_tree_background(state, background_xml)
        applied[f"ppt/slides/slide{state.spec.slide_num}.xml"] = background_xml
    return applied


def _move_template_static_shape(
    states: list[_TemplateRuntimeSlide],
    item: TemplateElementSpec,
    target_path: Path,
    target_rels_path: Path,
    slide_size_emu: tuple[int, int],
    public_slide_count: int | None = None,
) -> str | None:
    from .builder import (
        _append_shape_to_part,
        _canonical_shape_xml,
        _copy_shape_relationships_to_part,
        _move_template_background,
        _move_template_solid_background_shapes,
        _remove_template_shape,
        _set_shape_name,
        _shape_id,
        _shape_relationships_supported,
        _template_reference_indices,
        _template_shape_for_item,
        _timing_shape_ids,
    )
    shapes = [_template_shape_for_item(state, item) for state in states]
    if any(shape is None for shape in shapes):
        if not all(shape is None for shape in shapes):
            raise TemplateStructureError(
                f"{item.element_id}: structure item is a background on only some slides"
            )
        return _move_template_background(states, target_path, public_slide_count)

    resolved_shapes = [shape for shape in shapes if shape is not None]
    if item.is_background:
        background_xml = _move_template_solid_background_shapes(
            states,
            resolved_shapes,
            target_path,
            slide_size_emu,
            public_slide_count,
        )
        if background_xml is None:
            raise TemplateStructureError(
                f"{item.element_id!r} must compile to one exact p:bg payload"
            )
        return background_xml
    # Internal Layout carriers compile unused prototypes as authored; their
    # copy of a master atom leaves with the carrier slide, so the published
    # pages alone decide the atom (a re-skinned rule must not be refused
    # because an unused prototype still carries the original paint).
    reference = [
        (states[index], resolved_shapes[index])
        for index in _template_reference_indices(states, public_slide_count)
    ]
    canonical = {
        _canonical_shape_xml(shape, state.rels)
        for state, shape in reference
    }
    if len(canonical) != 1:
        slide_names = ", ".join(state.spec.svg_path.name for state, _shape in reference)
        raise TemplateStructureError(
            f"Explicit structure element {item.element_id!r} differs across slides: "
            f"{slide_names}"
        )
    for state, shape in zip(states, resolved_shapes):
        shape_id = _shape_id(shape)
        if shape_id and shape_id in _timing_shape_ids(state.root):
            raise TemplateStructureError(
                f"{state.spec.svg_path.name}: structure element {item.element_id!r} "
                "is referenced by slide timing"
            )
        if not _shape_relationships_supported(shape, state.rels):
            raise TemplateStructureError(
                f"{state.spec.svg_path.name}: structure element {item.element_id!r} "
                "uses a relationship that cannot move to a template part"
            )

    prototype_state, prototype_shape = reference[0]
    target_shape = _copy_shape_relationships_to_part(
        prototype_shape,
        prototype_state.rels,
        target_rels_path,
    )
    _set_shape_name(target_shape, f"{item.element_id} {item.layer.title()}")
    _append_shape_to_part(target_path, target_shape)
    for state, shape in zip(states, resolved_shapes):
        _remove_template_shape(state, shape)
    return None


def _shape_bounds_emu(
    shape: ET.Element,
    override_px: tuple[float, float, float, float] | None,
) -> tuple[int, int, int, int]:
    from .builder import (
        DML_NS,
        PML_NS,
    )
    if override_px is not None:
        x, y, width, height = override_px
        return tuple(
            round(value * EMU_PER_PX)
            for value in (x, y, width, height)
        )

    xfrm = shape.find(f"{{{PML_NS}}}spPr/{{{DML_NS}}}xfrm")
    if xfrm is None:
        xfrm = shape.find(f"{{{PML_NS}}}xfrm")
    if xfrm is None:
        raise TemplateStructureError(
            "Placeholder shape has no directly readable DrawingML transform; "
            "set data-pptx-bounds"
        )
    off = xfrm.find(f"{{{DML_NS}}}off")
    ext = xfrm.find(f"{{{DML_NS}}}ext")
    if off is None or ext is None:
        raise TemplateStructureError("Placeholder transform has no a:off/a:ext")
    try:
        return (
            int(off.attrib["x"]),
            int(off.attrib["y"]),
            int(ext.attrib["cx"]),
            int(ext.attrib["cy"]),
        )
    except (KeyError, ValueError) as exc:
        raise TemplateStructureError("Placeholder transform is invalid") from exc


def _replace_shape_xfrm(
    sp_pr: ET.Element,
    bounds: tuple[int, int, int, int],
) -> None:
    from .builder import (
        DML_NS,
    )
    for existing in list(sp_pr):
        if existing.tag == f"{{{DML_NS}}}xfrm":
            sp_pr.remove(existing)
    x, y, width, height = bounds
    xfrm = ET.Element(f"{{{DML_NS}}}xfrm")
    ET.SubElement(xfrm, f"{{{DML_NS}}}off", {"x": str(x), "y": str(y)})
    ET.SubElement(
        xfrm,
        f"{{{DML_NS}}}ext",
        {"cx": str(width), "cy": str(height)},
    )
    sp_pr.insert(0, xfrm)


def _placeholder_vertical_anchor(
    source_bounds: tuple[int, int, int, int],
    target_bounds: tuple[int, int, int, int],
) -> str:
    """Preserve an intentionally centered carrier inside its full slot frame."""
    _, source_y, _, source_height = source_bounds
    _, target_y, _, target_height = target_bounds
    source_center = source_y + source_height / 2
    target_center = target_y + target_height / 2
    return (
        "ctr"
        if abs(source_center - target_center) <= target_height * 0.2
        else "t"
    )


def _normalize_placeholder_body_properties(
    body_pr: ET.Element,
    source_bounds: tuple[int, int, int, int],
    target_bounds: tuple[int, int, int, int],
) -> None:
    """Make a full-frame placeholder wrap text while preserving vertical intent."""
    from .builder import (
        DML_NS,
    )
    body_pr.set("wrap", "square")
    body_pr.set("anchor", _placeholder_vertical_anchor(source_bounds, target_bounds))
    body_pr.set("anchorCtr", "0")
    autofit_tags = {
        f"{{{DML_NS}}}noAutofit",
        f"{{{DML_NS}}}normAutofit",
        f"{{{DML_NS}}}spAutoFit",
    }
    for child in list(body_pr):
        if child.tag in autofit_tags:
            body_pr.remove(child)
    body_pr.append(ET.Element(f"{{{DML_NS}}}noAutofit"))


def _apply_layout_frame_to_placeholder_carrier(
    shape: ET.Element,
    item: TemplateElementSpec,
) -> None:
    """Use the reusable Layout bounds on one template-review Slide carrier."""
    from .builder import (
        DML_NS,
        PML_NS,
    )
    if item.placeholder_bounds is None:
        raise TemplateStructureError(
            f"Placeholder {item.element_id!r} has no reusable Layout bounds"
        )
    source_bounds = _shape_bounds_emu(shape, None)
    target_bounds = _shape_bounds_emu(shape, item.placeholder_bounds)
    if shape.tag in {f"{{{PML_NS}}}sp", f"{{{PML_NS}}}pic"}:
        sp_pr = shape.find(f"{{{PML_NS}}}spPr")
        if sp_pr is None:
            raise TemplateStructureError(
                f"Placeholder {item.element_id!r} has no p:spPr"
            )
        _replace_shape_xfrm(sp_pr, target_bounds)
    elif shape.tag == f"{{{PML_NS}}}graphicFrame":
        xfrm = shape.find(f"{{{PML_NS}}}xfrm")
        if xfrm is None:
            xfrm = ET.Element(f"{{{PML_NS}}}xfrm")
            shape.insert(1, xfrm)
        for child in list(xfrm):
            if child.tag in {f"{{{DML_NS}}}off", f"{{{DML_NS}}}ext"}:
                xfrm.remove(child)
        x, y, width, height = target_bounds
        ET.SubElement(xfrm, f"{{{DML_NS}}}off", {"x": str(x), "y": str(y)})
        ET.SubElement(
            xfrm,
            f"{{{DML_NS}}}ext",
            {"cx": str(width), "cy": str(height)},
        )
    else:
        raise TemplateStructureError(
            f"Placeholder {item.element_id!r} cannot use Layout bounds on "
            f"DrawingML element {shape.tag.rsplit('}', 1)[-1]!r}"
        )

    tx_body = shape.find(f"{{{PML_NS}}}txBody")
    if tx_body is None:
        return
    body_pr = tx_body.find(f"{{{DML_NS}}}bodyPr")
    if body_pr is None:
        body_pr = ET.Element(f"{{{DML_NS}}}bodyPr")
        tx_body.insert(0, body_pr)
    _normalize_placeholder_body_properties(
        body_pr,
        source_bounds,
        target_bounds,
    )


def _layout_level_one_paragraph_properties(
    list_style: ET.Element,
) -> ET.Element:
    """Return the Layout list style's level-one paragraph properties."""
    from .builder import (
        DML_NS,
    )
    level_tag = f"{{{DML_NS}}}lvl1pPr"
    level_props = list_style.find(level_tag)
    if level_props is None:
        level_props = ET.Element(level_tag)
        trailing_tags = {
            f"{{{DML_NS}}}lvl{level}pPr" for level in range(2, 10)
        }
        trailing_tags.add(f"{{{DML_NS}}}extLst")
        insert_at = next(
            (
                index
                for index, child in enumerate(list_style)
                if child.tag in trailing_tags
            ),
            len(list_style),
        )
        list_style.insert(insert_at, level_props)
    return level_props


def _set_layout_level_one_default_size(
    list_style: ET.Element,
    source_run_pr: ET.Element | None,
) -> None:
    """Persist the prototype run size as the Layout's level-one text default."""
    from .builder import (
        DML_NS,
    )
    if source_run_pr is None or source_run_pr.get("sz") is None:
        return
    level_props = _layout_level_one_paragraph_properties(list_style)
    default_props = level_props.find(f"{{{DML_NS}}}defRPr")
    if default_props is None:
        default_props = ET.Element(f"{{{DML_NS}}}defRPr")
        ext_tag = f"{{{DML_NS}}}extLst"
        insert_at = next(
            (
                index
                for index, child in enumerate(level_props)
                if child.tag == ext_tag
            ),
            len(level_props),
        )
        level_props.insert(insert_at, default_props)
    default_props.set("sz", source_run_pr.get("sz", ""))


def _set_no_bullet_paragraph_properties(
    paragraph_props: ET.Element,
    *,
    replace_existing: bool = False,
) -> None:
    """Disable inherited bullets and hanging indent for a prose paragraph."""
    from .builder import (
        DML_NS,
        _PARAGRAPH_BULLET_CHOICE_TAGS,
        _PARAGRAPH_PROPERTIES_TRAILING_TAGS,
    )
    if replace_existing:
        for child in list(paragraph_props):
            if child.tag in _PARAGRAPH_BULLET_CHOICE_TAGS:
                paragraph_props.remove(child)

    bullet_choice = next(
        (
            child
            for child in paragraph_props
            if child.tag in _PARAGRAPH_BULLET_CHOICE_TAGS
        ),
        None,
    )
    if bullet_choice is not None and bullet_choice.tag != f"{{{DML_NS}}}buNone":
        return
    if bullet_choice is None:
        insert_at = next(
            (
                index
                for index, child in enumerate(paragraph_props)
                if child.tag in _PARAGRAPH_PROPERTIES_TRAILING_TAGS
            ),
            len(paragraph_props),
        )
        paragraph_props.insert(insert_at, ET.Element(f"{{{DML_NS}}}buNone"))

    paragraph_props.set("marL", "0")
    paragraph_props.set("indent", "0")
