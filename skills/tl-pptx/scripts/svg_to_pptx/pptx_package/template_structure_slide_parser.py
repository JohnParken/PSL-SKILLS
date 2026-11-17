"""
PPT Master - Template Structure Metadata

Parse and validate explicit SVG metadata consumed by structured PPTX export.

Usage:
    Imported by svg_to_pptx.pptx_package.builder and svg_quality_checker.py.

Examples:
    parse_template_slides([Path("projects/demo/svg_output/01_cover.svg")])

Dependencies:
    None (only uses standard library)
"""
# Adapted from hugohe3/ppt-master 7e0df6705de0cd24345210d31f07fcb5a32b6900.
# MIT; Copyright (c) 2025-2026 Hugo He. See references/LICENSE-PPT-MASTER.txt and references/upstream.md.

from __future__ import annotations
import re
from pathlib import Path
from xml.etree import ElementTree as ET
from native_payloads import (
    NativePayloadError,
)
from ..drawingml.utils import (
    is_picture_effect_carrier,
    project_geometry_length_errors,
)
from ..canvas_contract import CanvasContractError, parse_project_viewbox
from ..geometry_properties import (
    GeometryStyleError,
    materialize_inline_geometry_properties,
)

from .template_structure import (
    TemplateElementSpec,
    TemplateSlideSpec,
)

def parse_template_slide(
    svg_path: Path,
    slide_num: int,
    *,
    structured: bool = True,
) -> TemplateSlideSpec:
    """Parse one SVG's explicit template layout and structure elements."""
    from .template_structure import (
        PLACEHOLDER_BINDING_MODES,
        TemplateElementSpec,
        TemplateSlideSpec,
        TemplateStructureError,
        _LAYERS,
        _LAYOUT_KEY_RE,
        _MASTER_KEY_RE,
        _NON_VISUAL_TAGS,
        _PLACEHOLDERS,
        _STRUCTURE_ATTRS,
        _is_authored_preset_atom,
        _is_full_canvas_solid_rect,
        _local_tag,
        _parse_placeholder_bounds,
        _parse_placeholder_idx,
        _parse_root_boolean,
        _parse_svg_root,
        _structure_attrs,
        _svg_canvas,
        _validate_placeholder_carrier,
    )
    try:
        root = _parse_svg_root(svg_path)
    except (OSError, ET.ParseError, NativePayloadError) as exc:
        raise TemplateStructureError(
            f"{svg_path.name}: unable to parse SVG structure metadata: {exc}"
        ) from exc

    try:
        materialize_inline_geometry_properties(root)
    except GeometryStyleError as exc:
        raise TemplateStructureError(
            f"{svg_path.name}: invalid inline geometry: {exc}"
        ) from exc

    if _local_tag(root) != "svg":
        raise TemplateStructureError(f"{svg_path.name}: root element must be <svg>")
    try:
        parse_project_viewbox(
            root.get("viewBox"),
            context=f"{svg_path.name} root viewBox",
        )
    except CanvasContractError as exc:
        raise TemplateStructureError(str(exc)) from exc

    geometry_errors = project_geometry_length_errors(root)
    if geometry_errors:
        preview = "; ".join(geometry_errors[:8])
        suffix = (
            "" if len(geometry_errors) <= 8
            else f"; +{len(geometry_errors) - 8} more"
        )
        raise TemplateStructureError(
            f"{svg_path.name}: invalid project geometry length(s): "
            f"{preview}{suffix}"
        )

    master_key = (root.get("data-pptx-master") or "").strip()
    master_name = (root.get("data-pptx-master-name") or "").strip()
    if structured and not master_key:
        raise TemplateStructureError(
            f"{svg_path.name}: structured export requires root data-pptx-master"
        )
    if structured and not master_name:
        raise TemplateStructureError(
            f"{svg_path.name}: structured export requires root data-pptx-master-name"
        )
    if not master_key:
        master_key = "preserved-source"
    if not master_name:
        master_name = "Preserved Source Master"
    if not _MASTER_KEY_RE.fullmatch(master_key):
        raise TemplateStructureError(
            f"{svg_path.name}: invalid data-pptx-master {master_key!r}; use 1-64 "
            "ASCII letters, digits, dots, underscores, or hyphens"
        )

    layout_key = (root.get("data-pptx-layout") or "").strip()
    if not layout_key:
        raise TemplateStructureError(
            f"{svg_path.name}: explicit Layout export requires root data-pptx-layout"
        )
    if not _LAYOUT_KEY_RE.fullmatch(layout_key):
        raise TemplateStructureError(
            f"{svg_path.name}: invalid data-pptx-layout {layout_key!r}; use 1-64 "
            "ASCII letters, digits, dots, underscores, or hyphens"
        )
    layout_name = (root.get("data-pptx-layout-name") or "").strip()
    if structured and not layout_name:
        raise TemplateStructureError(
            f"{svg_path.name}: structured export requires root data-pptx-layout-name"
        )
    if not layout_name:
        layout_name = re.sub(r"[-_.]+", " ", layout_key).strip().title() or layout_key
    layout_show_master_shapes = _parse_root_boolean(
        root,
        "data-pptx-show-master-shapes",
        svg_path=svg_path,
    )
    slide_show_inherited_shapes = _parse_root_boolean(
        root,
        "data-pptx-show-inherited-shapes",
        svg_path=svg_path,
    )
    if structured and root.get("data-pptx-layout-kind") is not None:
        raise TemplateStructureError(
            f"{svg_path.name}: data-pptx-layout-kind is obsolete; the root "
            "Master/Layout identity is already final"
        )

    illegal_root_attrs = sorted(
        attr for attr in _STRUCTURE_ATTRS
        if (
            attr not in {
                "data-pptx-layout",
                "data-pptx-layout-name",
                "data-pptx-master",
                "data-pptx-master-name",
                "data-pptx-show-inherited-shapes",
                "data-pptx-show-master-shapes",
            }
            and root.get(attr) is not None
        )
    )
    if illegal_root_attrs:
        raise TemplateStructureError(
            f"{svg_path.name}: root <svg> cannot use {', '.join(illegal_root_attrs)}"
        )

    id_counts: dict[str, int] = {}
    for elem in root.iter():
        element_id = elem.get("id")
        if element_id:
            id_counts[element_id] = id_counts.get(element_id, 0) + 1
    duplicate_ids = sorted(element_id for element_id, count in id_counts.items() if count > 1)
    if duplicate_ids:
        raise TemplateStructureError(
            f"{svg_path.name}: duplicate SVG id(s) are not allowed in explicit Layout mode: "
            + ", ".join(duplicate_ids)
        )

    elements: list[TemplateElementSpec] = []
    canvas = _svg_canvas(root)
    last_order_rank = -1
    visual_order = 0
    for elem in root:
        tag = _local_tag(elem)
        if tag in _NON_VISUAL_TAGS:
            continue

        element_id = (elem.get("id") or "").strip()
        layer_raw = elem.get("data-pptx-layer")
        layer = (layer_raw or "").strip().lower() or None
        placeholder_raw = elem.get("data-pptx-placeholder")
        placeholder = (
            (placeholder_raw or "").strip().lower() or None
        )
        bounds_raw = elem.get("data-pptx-bounds")
        placeholder_idx_raw = elem.get("data-pptx-idx")
        binding_raw = elem.get("data-pptx-binding")
        carrier_raw = elem.get("data-pptx-carrier")
        editable_raw = elem.get("data-pptx-editable")
        is_background = _is_full_canvas_solid_rect(elem, canvas, root=root)
        effective_layer = layer or ("slide" if is_background else None)

        if (
            elem.get("data-pptx-layout") is not None
            or elem.get("data-pptx-layout-name") is not None
            or elem.get("data-pptx-master") is not None
            or elem.get("data-pptx-master-name") is not None
            or elem.get("data-pptx-show-inherited-shapes") is not None
            or elem.get("data-pptx-show-master-shapes") is not None
        ):
            raise TemplateStructureError(
                f"{svg_path.name}: Master/Layout identity and visibility "
                "attributes belong on the root <svg> only"
            )
        if layer and layer not in _LAYERS:
            raise TemplateStructureError(
                f"{svg_path.name}: {element_id or tag} has unsupported "
                f"data-pptx-layer={layer!r}"
            )
        if layer_raw is not None and layer is None:
            raise TemplateStructureError(
                f"{svg_path.name}: {element_id or tag} has empty data-pptx-layer"
            )
        if placeholder and placeholder not in _PLACEHOLDERS:
            raise TemplateStructureError(
                f"{svg_path.name}: {element_id or tag} has unsupported "
                f"data-pptx-placeholder={placeholder!r}"
            )
        if placeholder_raw is not None and placeholder is None:
            raise TemplateStructureError(
                f"{svg_path.name}: {element_id or tag} has empty "
                "data-pptx-placeholder"
            )
        if effective_layer and placeholder:
            raise TemplateStructureError(
                f"{svg_path.name}: {element_id or tag} cannot be both a static "
                "structure/background layer and a content placeholder"
            )
        if layer == "slide" and not is_background:
            raise TemplateStructureError(
                f"{svg_path.name}: data-pptx-layer='slide' is allowed only on a "
                "direct full-canvas solid background rect"
            )
        if (
            structured
            and layer in {"master", "layout"}
            and tag == "g"
            and not _is_authored_preset_atom(elem)
            and not is_picture_effect_carrier(elem)
        ):
            raise TemplateStructureError(
                f"{svg_path.name}: {element_id or tag} is a <g> on the {layer} "
                "layer; Master/Layout fixed elements must be root-level atoms"
            )
        if placeholder_idx_raw is not None and not placeholder:
            raise TemplateStructureError(
                f"{svg_path.name}: {element_id or tag} has placeholder idx without "
                "data-pptx-placeholder"
            )
        if binding_raw is not None and not placeholder:
            raise TemplateStructureError(
                f"{svg_path.name}: {element_id or tag} has placeholder binding "
                "without data-pptx-placeholder"
            )
        if carrier_raw is not None:
            raise TemplateStructureError(
                f"{svg_path.name}: {element_id or tag} declares "
                "data-pptx-carrier on a root child; the marker belongs "
                "on the direct child inside a placeholder <g>"
            )
        if (effective_layer or placeholder) and not element_id:
            raise TemplateStructureError(
                f"{svg_path.name}: direct <{tag}> with Layout metadata requires an id"
            )
        if editable_raw is not None:
            if not effective_layer or editable_raw.strip().lower() != "false":
                raise TemplateStructureError(
                    f"{svg_path.name}: data-pptx-editable currently supports only "
                    "'false' on master/layout elements or slide backgrounds"
                )

        if is_background:
            order_rank = {"master": 0, "layout": 1, "slide": 2}[effective_layer]
        elif effective_layer == "master":
            order_rank = 3
        elif effective_layer == "layout":
            order_rank = 4
        else:
            order_rank = 5
        if order_rank < last_order_rank:
            raise TemplateStructureError(
                f"{svg_path.name}: {element_id or tag} violates template paint order; "
                "use Master background, Layout background, Slide background, "
                "Master shapes, Layout shapes, then Slide content/placeholders. "
                "Full-canvas solid rects are treated as backgrounds; translucent overlays are not backgrounds"
            )
        last_order_rank = order_rank

        placeholder_bounds = _parse_placeholder_bounds(
            bounds_raw if placeholder else None,
            svg_path=svg_path,
            element_id=element_id or tag,
        )
        if structured and placeholder and placeholder_bounds is None:
            raise TemplateStructureError(
                f"{svg_path.name}: Layout placeholder {element_id!r} requires "
                "explicit data-pptx-bounds; define the "
                "reusable frame from the design zone, not the current text bounds"
            )
        placeholder_idx = _parse_placeholder_idx(
            placeholder_idx_raw,
            svg_path=svg_path,
            element_id=element_id or tag,
        )

        placeholder_binding: str | None = None
        placeholder_carrier_tag: str | None = None
        if placeholder and structured:
            if tag != "g":
                raise TemplateStructureError(
                    f"{svg_path.name}: placeholder {element_id!r} must be declared "
                    "on a root-level <g> authoring boundary"
                )
            wrapper_visual_attrs = sorted(
                name
                for name in elem.attrib
                if name != "id" and not name.startswith("data-pptx-")
            )
            if wrapper_visual_attrs:
                raise TemplateStructureError(
                    f"{svg_path.name}: placeholder group {element_id!r} must be a "
                    "render-neutral authoring boundary; move these attributes to "
                    "its content: " + ", ".join(wrapper_visual_attrs)
                )
            placeholder_binding = (binding_raw or "carrier").strip().lower()
            if binding_raw is not None and not binding_raw.strip():
                raise TemplateStructureError(
                    f"{svg_path.name}: placeholder {element_id!r} has an empty "
                    "data-pptx-binding"
                )
            if placeholder_binding not in PLACEHOLDER_BINDING_MODES:
                allowed = ", ".join(sorted(PLACEHOLDER_BINDING_MODES))
                raise TemplateStructureError(
                    f"{svg_path.name}: placeholder {element_id!r} binding must be "
                    f"one of: {allowed}"
                )
            visual_children = [
                child for child in elem if _local_tag(child) not in _NON_VISUAL_TAGS
            ]
            carrier_children = [
                child
                for child in visual_children
                if (child.get("data-pptx-carrier") or "")
                .strip()
                .lower()
                == "true"
            ]
            for child in elem:
                marker = child.get("data-pptx-carrier")
                if marker is not None and marker.strip().lower() != "true":
                    raise TemplateStructureError(
                        f"{svg_path.name}: placeholder {element_id!r} carrier marker "
                        "must be exactly 'true'"
                    )
                illegal_child_attrs = [
                    attr
                    for attr in _structure_attrs(child)
                    if attr != "data-pptx-carrier"
                ]
                if illegal_child_attrs:
                    raise TemplateStructureError(
                        f"{svg_path.name}: placeholder {element_id!r} child uses "
                        "nested structure metadata: " + ", ".join(illegal_child_attrs)
                    )
                for descendant in child.iter():
                    if descendant is child:
                        continue
                    nested_attrs = _structure_attrs(descendant)
                    if nested_attrs:
                        nested_id = descendant.get("id") or _local_tag(descendant)
                        raise TemplateStructureError(
                            f"{svg_path.name}: {nested_id} uses nested structure "
                            "metadata: " + ", ".join(nested_attrs)
                        )
            if placeholder_binding == "proxy":
                if placeholder != "object":
                    raise TemplateStructureError(
                        f"{svg_path.name}: placeholder {element_id!r} may use proxy "
                        "binding only with data-pptx-placeholder='object'"
                    )
                if carrier_children:
                    raise TemplateStructureError(
                        f"{svg_path.name}: proxy placeholder {element_id!r} must not "
                        "declare a carrier child"
                    )
                if not visual_children:
                    raise TemplateStructureError(
                        f"{svg_path.name}: proxy placeholder {element_id!r} must "
                        "contain visible Slide-local content"
                    )
            else:
                if len(visual_children) != 1 or len(carrier_children) != 1:
                    composite_hint = (
                        " For composite object content, declare "
                        "data-pptx-binding='proxy' in the prototype "
                        "and page, or create an adaptive Layout; never add a tiny "
                        "or transparent dummy carrier."
                        if placeholder == "object" else ""
                    )
                    raise TemplateStructureError(
                        f"{svg_path.name}: carrier placeholder {element_id!r} must "
                        "contain exactly one visual direct child and mark it "
                        "data-pptx-carrier='true'."
                        f"{composite_hint}"
                    )
                carrier = carrier_children[0]
                placeholder_carrier_tag = _local_tag(carrier)
                _validate_placeholder_carrier(
                    carrier,
                    placeholder,
                    svg_path=svg_path,
                    element_id=element_id,
                )
        elif placeholder:
            placeholder_binding = "carrier"
            placeholder_carrier_tag = tag
            _validate_placeholder_carrier(
                elem,
                placeholder,
                svg_path=svg_path,
                element_id=element_id,
            )
        else:
            for descendant in elem.iter():
                if descendant is elem:
                    continue
                nested_attrs = _structure_attrs(descendant)
                if nested_attrs:
                    nested_id = descendant.get("id") or _local_tag(descendant)
                    raise TemplateStructureError(
                        f"{svg_path.name}: {nested_id} uses structure metadata below "
                        "the SVG root: " + ", ".join(nested_attrs)
                    )

        if effective_layer or placeholder:
            elements.append(TemplateElementSpec(
                element_id=element_id,
                order=visual_order,
                tag=tag,
                layer=effective_layer,
                placeholder=placeholder,
                placeholder_bounds=placeholder_bounds,
                placeholder_idx=placeholder_idx,
                placeholder_binding=placeholder_binding,
                placeholder_carrier_tag=placeholder_carrier_tag,
                is_background=is_background,
            ))
        visual_order += 1

    for scope in ("master", "layout", "slide"):
        backgrounds = [
            item for item in elements
            if item.layer == scope and item.is_background
        ]
        if len(backgrounds) > 1:
            raise TemplateStructureError(
                f"{svg_path.name}: explicit Layout mode allows at most one {scope} "
                "solid background"
            )

    spec = TemplateSlideSpec(
        slide_num=slide_num,
        svg_path=svg_path,
        master_key=master_key,
        master_name=master_name,
        layout_key=layout_key,
        layout_name=layout_name,
        layout_show_master_shapes=layout_show_master_shapes,
        slide_show_inherited_shapes=slide_show_inherited_shapes,
        elements=tuple(elements),
    )
    return spec
