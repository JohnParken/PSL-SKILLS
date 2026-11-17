"""Core PPTX assembly: create_pptx_with_native_svg."""
# Adapted from hugohe3/ppt-master 7e0df6705de0cd24345210d31f07fcb5a32b6900.
# MIT; Copyright (c) 2025-2026 Hugo He. See references/LICENSE-PPT-MASTER.txt and references/upstream.md.

from __future__ import annotations
from pathlib import (
    Path,
)
from typing import Any
from xml.etree import ElementTree as ET
from .template_structure import (
    TemplateElementSpec,
    TemplateSlideSpec,
    TemplateStructureError,
)

from .builder import (
    _TemplateRuntimeSlide,
)

def _template_runtime_slides(
    extract_dir: Path,
    specs: list[TemplateSlideSpec],
    conversion_traces: list[dict[str, Any]] | None,
) -> list[_TemplateRuntimeSlide]:
    """Load slide XML state and join it with SVG-to-shape trace ids."""
    from .builder import (
        _TemplateRuntimeSlide,
        _read_relationships,
        _top_level_shapes_by_id,
        _trace_native_shape_ids,
    )
    if not conversion_traces:
        raise TemplateStructureError(
            "Explicit Layout export requires native conversion traces for every slide"
        )
    trace_by_slide = {
        int(trace.get("slide_num", 0)): trace
        for trace in conversion_traces
        if trace.get("slide_num") is not None
    }
    states: list[_TemplateRuntimeSlide] = []
    for spec in specs:
        trace = trace_by_slide.get(spec.slide_num)
        if trace is None:
            raise TemplateStructureError(
                f"{spec.svg_path.name}: missing native conversion trace"
            )
        slide_path = extract_dir / "ppt" / "slides" / f"slide{spec.slide_num}.xml"
        rels_path = (
            extract_dir
            / "ppt"
            / "slides"
            / "_rels"
            / f"slide{spec.slide_num}.xml.rels"
        )
        tree = ET.parse(slide_path)
        root = tree.getroot()
        states.append(_TemplateRuntimeSlide(
            spec=spec,
            slide_path=slide_path,
            rels_path=rels_path,
            tree=tree,
            root=root,
            rels=_read_relationships(rels_path),
            shapes=_top_level_shapes_by_id(root),
            shape_ids_by_svg_id=_trace_native_shape_ids(trace),
        ))
    return states


def _template_shape_for_item(
    state: _TemplateRuntimeSlide,
    item: TemplateElementSpec,
) -> ET.Element | None:
    """Resolve one metadata item to its generated top-level DrawingML shape."""
    from .builder import (
        _extract_slide_background_xml,
    )
    shape_ids = [
        shape_id
        for shape_id in state.shape_ids_by_svg_id.get(item.element_id, [])
        if shape_id in state.shapes
    ]
    if len(shape_ids) == 1:
        return state.shapes[shape_ids[0]]
    if not shape_ids and item.layer and item.order == 0 and item.tag in {"rect", "g"}:
        if _extract_slide_background_xml(
            state.slide_path.read_text(encoding="utf-8")
        ):
            return None
    if not shape_ids:
        text_hint = (
            "; multiline text placeholders require a single-frame text mode "
            "and cannot use --no-merge"
            if item.placeholder and item.placeholder_carrier_tag == "text"
            else ""
        )
        raise TemplateStructureError(
            f"{state.spec.svg_path.name}: metadata element {item.element_id!r} "
            f"did not produce one top-level native shape{text_hint}"
        )
    raise TemplateStructureError(
        f"{state.spec.svg_path.name}: metadata element {item.element_id!r} "
        f"resolved to {len(shape_ids)} top-level shapes; use one direct SVG element"
    )


def _shape_transform(shape: ET.Element) -> ET.Element | None:
    """Return the direct DrawingML transform for one top-level shape."""
    from .builder import (
        DML_NS,
        PML_NS,
    )
    paths = {
        f"{{{PML_NS}}}sp": f"{{{PML_NS}}}spPr/{{{DML_NS}}}xfrm",
        f"{{{PML_NS}}}pic": f"{{{PML_NS}}}spPr/{{{DML_NS}}}xfrm",
        f"{{{PML_NS}}}cxnSp": f"{{{PML_NS}}}spPr/{{{DML_NS}}}xfrm",
        f"{{{PML_NS}}}graphicFrame": f"{{{PML_NS}}}xfrm",
        f"{{{PML_NS}}}grpSp": f"{{{PML_NS}}}grpSpPr/{{{DML_NS}}}xfrm",
    }
    path = paths.get(shape.tag)
    return shape.find(path) if path is not None else None


def _int_attr(elem: ET.Element, name: str, context: str) -> int:
    try:
        return int(elem.attrib[name])
    except (KeyError, ValueError) as exc:
        raise TemplateStructureError(f"{context} has invalid {name!r}") from exc


def _flatten_group_transform(
    group: ET.Element,
    carrier: ET.Element,
    *,
    context: str,
) -> None:
    """Map a single group's child transform into slide coordinates."""
    from .builder import (
        DML_NS,
    )
    group_xfrm = _shape_transform(group)
    carrier_xfrm = _shape_transform(carrier)
    if group_xfrm is None or carrier_xfrm is None:
        raise TemplateStructureError(
            f"{context} cannot be unwrapped because its DrawingML transform is missing"
        )
    if any(group_xfrm.get(name) is not None for name in ("rot", "flipH", "flipV")):
        raise TemplateStructureError(
            f"{context} wrapper carries an unsupported group rotation or flip"
        )
    group_off = group_xfrm.find(f"{{{DML_NS}}}off")
    group_ext = group_xfrm.find(f"{{{DML_NS}}}ext")
    child_off = group_xfrm.find(f"{{{DML_NS}}}chOff")
    child_ext = group_xfrm.find(f"{{{DML_NS}}}chExt")
    carrier_off = carrier_xfrm.find(f"{{{DML_NS}}}off")
    carrier_ext = carrier_xfrm.find(f"{{{DML_NS}}}ext")
    if any(value is None for value in (
        group_off,
        group_ext,
        child_off,
        child_ext,
        carrier_off,
        carrier_ext,
    )):
        raise TemplateStructureError(
            f"{context} cannot be unwrapped because its group transform is incomplete"
        )
    child_width = _int_attr(child_ext, "cx", context)
    child_height = _int_attr(child_ext, "cy", context)
    if child_width <= 0 or child_height <= 0:
        raise TemplateStructureError(f"{context} group child extent must be positive")
    scale_x = _int_attr(group_ext, "cx", context) / child_width
    scale_y = _int_attr(group_ext, "cy", context) / child_height
    mapped_x = _int_attr(group_off, "x", context) + round(
        (_int_attr(carrier_off, "x", context) - _int_attr(child_off, "x", context))
        * scale_x
    )
    mapped_y = _int_attr(group_off, "y", context) + round(
        (_int_attr(carrier_off, "y", context) - _int_attr(child_off, "y", context))
        * scale_y
    )
    carrier_off.set("x", str(mapped_x))
    carrier_off.set("y", str(mapped_y))
    carrier_ext.set(
        "cx",
        str(round(_int_attr(carrier_ext, "cx", context) * scale_x)),
    )
    carrier_ext.set(
        "cy",
        str(round(_int_attr(carrier_ext, "cy", context) * scale_y)),
    )


def _unwrap_placeholder_carrier(
    state: _TemplateRuntimeSlide,
    item: TemplateElementSpec,
) -> ET.Element:
    """Remove one SVG-only slot wrapper and return its top-level carrier."""
    from .builder import (
        PML_NS,
        _TOP_LEVEL_SHAPE_TAGS,
        _rewrite_roundtrip_timing_shape_ids,
        _shape_id,
    )
    wrapper = _template_shape_for_item(state, item)
    if wrapper is None:
        raise TemplateStructureError(
            f"{state.spec.svg_path.name}: placeholder {item.element_id!r} cannot "
            "be a slide background"
        )
    if item.tag != "g" or wrapper.tag != f"{{{PML_NS}}}grpSp":
        return wrapper
    carriers = [
        child for child in wrapper if child.tag in _TOP_LEVEL_SHAPE_TAGS
    ]
    if len(carriers) != 1:
        raise TemplateStructureError(
            f"{state.spec.svg_path.name}: placeholder group {item.element_id!r} "
            f"converted to {len(carriers)} native children; expected one carrier"
        )
    carrier = carriers[0]
    _flatten_group_transform(
        wrapper,
        carrier,
        context=f"{state.spec.svg_path.name} placeholder {item.element_id!r}",
    )
    sp_tree = _slide_sp_tree(state)
    try:
        wrapper_index = list(sp_tree).index(wrapper)
    except ValueError as exc:
        raise TemplateStructureError(
            f"{state.spec.svg_path.name}: placeholder wrapper "
            f"{item.element_id!r} is not top-level"
        ) from exc
    wrapper.remove(carrier)
    sp_tree.remove(wrapper)
    sp_tree.insert(wrapper_index, carrier)
    wrapper_id = _shape_id(wrapper)
    carrier_id = _shape_id(carrier)
    if wrapper_id:
        state.shapes.pop(wrapper_id, None)
    if carrier_id:
        state.shapes[carrier_id] = carrier
    if wrapper_id and carrier_id:
        # An animation that names the slot group now targets its carrier.
        _rewrite_roundtrip_timing_shape_ids(state.root, {wrapper_id: carrier_id})
    return carrier


def _slide_sp_tree(state: _TemplateRuntimeSlide) -> ET.Element:
    from .builder import (
        PML_NS,
    )
    sp_tree = state.root.find(f".//{{{PML_NS}}}cSld/{{{PML_NS}}}spTree")
    if sp_tree is None:
        raise RuntimeError(f"Slide has no p:spTree: {state.slide_path}")
    return sp_tree


def _append_shape_to_runtime_slide(
    state: _TemplateRuntimeSlide,
    elem: ET.Element,
) -> None:
    """Append a generated helper shape to one Slide with fresh object ids."""
    from .builder import (
        PML_NS,
        _renumber_shape_ids,
    )
    existing_ids = [
        int(cnv.attrib["id"])
        for cnv in state.root.iter(f"{{{PML_NS}}}cNvPr")
        if cnv.attrib.get("id", "").isdigit()
    ]
    clone = ET.fromstring(ET.tostring(elem, encoding="utf-8"))
    _renumber_shape_ids(clone, max(existing_ids, default=1) + 1)
    _slide_sp_tree(state).append(clone)


def _presentation_slide_size_emu(extract_dir: Path) -> tuple[int, int]:
    """Return the package slide size in EMU."""
    from .builder import (
        PML_NS,
    )
    presentation_path = extract_dir / "ppt" / "presentation.xml"
    root = ET.parse(presentation_path).getroot()
    slide_size = root.find(f"{{{PML_NS}}}sldSz")
    if slide_size is None:
        raise TemplateStructureError("presentation.xml has no p:sldSz")
    try:
        width = int(slide_size.attrib["cx"])
        height = int(slide_size.attrib["cy"])
    except (KeyError, ValueError) as exc:
        raise TemplateStructureError("presentation.xml has an invalid p:sldSz") from exc
    if width <= 0 or height <= 0:
        raise TemplateStructureError("presentation.xml p:sldSz must be positive")
    return width, height


def _solid_background_xml_from_shape(
    shape: ET.Element,
    slide_size_emu: tuple[int, int],
) -> str | None:
    """Convert one exact full-slide solid rectangle shape into p:bg XML."""
    from .builder import (
        DML_NS,
        PML_NS,
    )
    if shape.tag != f"{{{PML_NS}}}sp":
        return None
    if shape.find(f"{{{PML_NS}}}txBody") is not None:
        return None
    sp_pr = shape.find(f"{{{PML_NS}}}spPr")
    if sp_pr is None:
        return None
    xfrm = sp_pr.find(f"{{{DML_NS}}}xfrm")
    if xfrm is None or xfrm.attrib:
        return None
    off = xfrm.find(f"{{{DML_NS}}}off")
    ext = xfrm.find(f"{{{DML_NS}}}ext")
    if off is None or ext is None:
        return None
    try:
        bounds = (
            int(off.attrib["x"]),
            int(off.attrib["y"]),
            int(ext.attrib["cx"]),
            int(ext.attrib["cy"]),
        )
    except (KeyError, ValueError):
        return None
    if bounds != (0, 0, *slide_size_emu):
        return None

    geometry = sp_pr.find(f"{{{DML_NS}}}prstGeom")
    if geometry is None or geometry.attrib.get("prst") != "rect":
        return None
    solid_fill = sp_pr.find(f"{{{DML_NS}}}solidFill")
    if solid_fill is None:
        return None
    competing_fills = {
        f"{{{DML_NS}}}noFill",
        f"{{{DML_NS}}}gradFill",
        f"{{{DML_NS}}}blipFill",
        f"{{{DML_NS}}}pattFill",
        f"{{{DML_NS}}}grpFill",
    }
    if any(child.tag in competing_fills for child in sp_pr):
        return None
    line = sp_pr.find(f"{{{DML_NS}}}ln")
    if line is not None and line.find(f"{{{DML_NS}}}noFill") is None:
        return None
    for effect_tag in (f"{{{DML_NS}}}effectLst", f"{{{DML_NS}}}effectDag"):
        effect = sp_pr.find(effect_tag)
        if effect is not None and (effect.attrib or list(effect)):
            return None

    background = ET.Element(f"{{{PML_NS}}}bg")
    background_props = ET.SubElement(background, f"{{{PML_NS}}}bgPr")
    background_props.append(
        ET.fromstring(ET.tostring(solid_fill, encoding="utf-8"))
    )
    ET.SubElement(background_props, f"{{{DML_NS}}}effectLst")
    return ET.tostring(background, encoding="unicode")


def _remove_template_shape(
    state: _TemplateRuntimeSlide,
    shape: ET.Element,
) -> None:
    sp_tree = _slide_sp_tree(state)
    if shape not in list(sp_tree):
        raise TemplateStructureError(
            f"{state.spec.svg_path.name}: structure shape is not slide-local"
        )
    sp_tree.remove(shape)


def _template_reference_indices(
    states: list[_TemplateRuntimeSlide],
    public_slide_count: int | None,
) -> list[int]:
    """Use public pages as truth, or prototypes for an otherwise unused Master."""
    return [
        index for index, state in enumerate(states)
        if public_slide_count is None or state.spec.slide_num <= public_slide_count
    ] or list(range(len(states)))


def _move_template_background(
    states: list[_TemplateRuntimeSlide],
    target_path: Path,
    public_slide_count: int | None = None,
) -> str:
    from .builder import (
        DML_NS,
        PML_NS,
        _extract_slide_background_xml,
        _put_background_on_part,
    )
    backgrounds = [
        _extract_slide_background_xml(state.slide_path.read_text(encoding="utf-8"))
        for state in states
    ]
    if not backgrounds or any(background is None for background in backgrounds):
        raise TemplateStructureError(
            "Template background metadata must resolve to an explicit background "
            "on every affected slide"
        )
    reference_indices = _template_reference_indices(states, public_slide_count)
    canonical_backgrounds = set()
    for index in reference_indices:
        background = backgrounds[index]
        if background is None:
            continue
        wrapper = ET.fromstring(
            f'<root xmlns:p="{PML_NS}" xmlns:a="{DML_NS}">{background}</root>'
        )
        canonical_backgrounds.add(
            ET.tostring(list(wrapper)[0], encoding="utf-8")
        )
    if len(canonical_backgrounds) != 1:
        slide_names = ", ".join(states[index].spec.svg_path.name for index in reference_indices)
        raise TemplateStructureError(
            f"Explicit template background differs across slides: {slide_names}"
        )
    background_xml = backgrounds[reference_indices[0]]
    if background_xml is None:
        raise TemplateStructureError("Template background is unexpectedly empty")
    target_xml = target_path.read_text(encoding="utf-8")
    updated = _put_background_on_part(target_xml, background_xml)
    if updated is None:
        raise TemplateStructureError(
            f"Cannot install explicit background on {target_path.name}"
        )
    target_path.write_text(updated, encoding="utf-8")
    for state in states:
        c_sld = state.root.find(f"{{{PML_NS}}}cSld")
        background = (
            c_sld.find(f"{{{PML_NS}}}bg") if c_sld is not None else None
        )
        if c_sld is None or background is None:
            raise TemplateStructureError(
                f"{state.spec.svg_path.name}: explicit background disappeared "
                "during explicit Layout structure assembly"
            )
        c_sld.remove(background)
    return background_xml


def _move_template_solid_background_shapes(
    states: list[_TemplateRuntimeSlide],
    shapes: list[ET.Element],
    target_path: Path,
    slide_size_emu: tuple[int, int],
    public_slide_count: int | None = None,
) -> str | None:
    """Move repeated full-slide solid rects into a master/layout p:bg."""
    from .builder import (
        _put_background_on_part,
    )
    backgrounds = [
        _solid_background_xml_from_shape(shape, slide_size_emu)
        for shape in shapes
    ]
    if not any(backgrounds):
        return None
    if any(background is None for background in backgrounds):
        raise TemplateStructureError(
            "A template background resolves to a full-slide solid rect on only "
            "some slides sharing the structure"
        )
    reference_indices = _template_reference_indices(states, public_slide_count)
    canonical = {backgrounds[index] for index in reference_indices}
    if len(canonical) != 1:
        slide_names = ", ".join(states[index].spec.svg_path.name for index in reference_indices)
        raise TemplateStructureError(
            f"Explicit template solid background differs across slides: {slide_names}"
        )
    background_xml = backgrounds[reference_indices[0]]
    if background_xml is None:
        return None
    target_xml = target_path.read_text(encoding="utf-8")
    updated = _put_background_on_part(target_xml, background_xml)
    if updated is None:
        raise TemplateStructureError(
            f"Cannot install explicit solid background on {target_path.name}"
        )
    target_path.write_text(updated, encoding="utf-8")
    for state, shape in zip(states, shapes):
        _remove_template_shape(state, shape)
    return background_xml


def _set_slide_tree_background(
    state: _TemplateRuntimeSlide,
    background_xml: str,
) -> None:
    """Replace the slide tree's p:bg with explicit background XML."""
    from .builder import (
        PML_NS,
    )
    c_sld = state.root.find(f"{{{PML_NS}}}cSld")
    if c_sld is None:
        raise TemplateStructureError(
            f"{state.spec.svg_path.name}: slide has no p:cSld"
        )
    for existing in list(c_sld):
        if existing.tag == f"{{{PML_NS}}}bg":
            c_sld.remove(existing)
    background = ET.fromstring(background_xml)
    sp_tree_tag = f"{{{PML_NS}}}spTree"
    insert_at = next(
        (index for index, child in enumerate(c_sld) if child.tag == sp_tree_tag),
        0,
    )
    c_sld.insert(insert_at, background)
