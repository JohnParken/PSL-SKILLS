"""Core PPTX assembly: create_pptx_with_native_svg."""
# Adapted from hugohe3/ppt-master 7e0df6705de0cd24345210d31f07fcb5a32b6900.
# MIT; Copyright (c) 2025-2026 Hugo He. See references/LICENSE-PPT-MASTER.txt and references/upstream.md.

from __future__ import annotations
import re
from pathlib import (
    Path,
)
from typing import Any
from xml.etree import ElementTree as ET
from ..semantic_markers import (
    page_layout_name_from_svg,
)

from .builder import (
    PptxStructureContext,
)

def _baseline_layout_role(svg_path: Path) -> str:
    """Use explicit page semantics, then fall back to conservative filename roles."""
    from .builder import (
        _BASELINE_LAYOUT_ROLE_TOKENS,
    )
    semantic_role = page_layout_name_from_svg(svg_path)
    if semantic_role:
        return semantic_role
    stem = svg_path.stem.casefold()
    tokens = {
        token
        for token in re.split(r"[^0-9a-z]+", stem)
        if token and not token.isdigit()
    }
    for role, english_tokens, cjk_tokens in _BASELINE_LAYOUT_ROLE_TOKENS:
        if tokens.intersection(english_tokens) or any(
            token in stem for token in cjk_tokens
        ):
            return role
    return "Content"


def _layout_identity(layout_path: Path) -> tuple[str, bool]:
    """Return a layout's picker name and master-shape visibility."""
    from .builder import (
        PML_NS,
    )
    root = ET.parse(layout_path).getroot()
    c_sld = root.find(f"{{{PML_NS}}}cSld")
    name = c_sld.attrib.get("name", "") if c_sld is not None else ""
    return name, root.attrib.get("showMasterSp", "1") != "0"


def _shared_explicit_slide_background(
    extract_dir: Path,
    slide_nums: list[int],
) -> str | None:
    """Return one exact background only when every family slide carries it."""
    from .builder import (
        _extract_slide_background_xml,
    )
    backgrounds: list[str] = []
    for slide_num in slide_nums:
        slide_path = extract_dir / "ppt" / "slides" / f"slide{slide_num}.xml"
        background = _extract_slide_background_xml(
            slide_path.read_text(encoding="utf-8")
        )
        if background is None:
            return None
        backgrounds.append(background)
    if not backgrounds or len(set(backgrounds)) != 1:
        return None
    return backgrounds[0]


def _extract_baseline_layout_families(
    extract_dir: Path,
    structure: PptxStructureContext,
    svg_files: list[Path],
    *,
    verbose: bool = False,
) -> int:
    """Build conservative post-generation layout families for a free SVG deck."""
    from .builder import (
        COVER_LAYOUT_NAME,
        SLIDE_LAYOUT_REL_TYPE,
        _create_custom_layout,
        _find_relationship_target,
        _put_background_on_part,
        _remove_slide_background_xml,
        _resolve_package_target,
        _set_slide_layout_target,
    )
    families: dict[tuple[str, str, bool], list[int]] = {}
    base_layouts: dict[tuple[str, str, bool], str] = {}
    for slide_num, svg_path in enumerate(svg_files, 1):
        rels_path = (
            extract_dir
            / "ppt"
            / "slides"
            / "_rels"
            / f"slide{slide_num}.xml.rels"
        )
        layout_target = _find_relationship_target(rels_path, SLIDE_LAYOUT_REL_TYPE)
        if not layout_target:
            raise RuntimeError(f"Slide {slide_num} has no slide layout relationship")
        layout_part = _resolve_package_target(
            f"ppt/slides/slide{slide_num}.xml", layout_target
        )
        layout_name, show_master_shapes = _layout_identity(extract_dir / layout_part)
        role = _baseline_layout_role(svg_path)
        if role == "Content" and layout_name == COVER_LAYOUT_NAME:
            role = COVER_LAYOUT_NAME
        key = (
            structure.slide_master_part(slide_num),
            role,
            show_master_shapes,
        )
        families.setdefault(key, []).append(slide_num)
        base_layouts.setdefault(key, layout_part)

    created = 0
    lifted_backgrounds = 0
    role_counts: dict[tuple[str, str], int] = {}
    for key, slide_nums in families.items():
        master_part, role, show_master_shapes = key
        role_key = (master_part, role)
        role_counts[role_key] = role_counts.get(role_key, 0) + 1
        variant = role_counts[role_key]
        layout_name = role if variant == 1 else f"{role} {variant}"
        layout_target, layout_part = _create_custom_layout(
            extract_dir,
            master_part,
            base_layouts[key],
            layout_name,
            show_master_shapes=show_master_shapes,
        )

        background_xml = _shared_explicit_slide_background(
            extract_dir, slide_nums
        )
        if background_xml is not None:
            layout_path = extract_dir / layout_part
            layout_xml = layout_path.read_text(encoding="utf-8")
            updated_layout_xml = _put_background_on_part(layout_xml, background_xml)
            if updated_layout_xml is not None:
                layout_path.write_text(updated_layout_xml, encoding="utf-8")
                for slide_num in slide_nums:
                    slide_path = (
                        extract_dir / "ppt" / "slides" / f"slide{slide_num}.xml"
                    )
                    slide_path.write_text(
                        _remove_slide_background_xml(
                            slide_path.read_text(encoding="utf-8")
                        ),
                        encoding="utf-8",
                    )
                lifted_backgrounds += len(slide_nums)

        for slide_num in slide_nums:
            rels_path = (
                extract_dir
                / "ppt"
                / "slides"
                / "_rels"
                / f"slide{slide_num}.xml.rels"
            )
            _set_slide_layout_target(rels_path, layout_target)
        created += 1

    if verbose and created:
        print(
            "  Baseline layout families: "
            f"created {created} reusable layout(s), "
            f"lifted {lifted_backgrounds} slide background(s)"
        )
    return created


def _promote_common_chrome_shapes_to_layouts(
    extract_dir: Path,
    slide_count: int,
    conversion_traces: list[dict[str, Any]] | None,
    *,
    verbose: bool = False,
) -> int:
    """Promote exact leading chrome shared by every slide in one layout."""
    from .builder import (
        PML_NS,
        SLIDE_LAYOUT_REL_TYPE,
        _PAGE_NUMBER_TOKENS,
        _append_shape_to_part,
        _canonical_shape_xml,
        _copy_shape_relationships_to_part,
        _find_relationship_target,
        _read_relationships,
        _relationships_path_for_part,
        _resolve_package_target,
        _shape_relationships_supported,
        _timing_shape_ids,
        _top_level_shapes_by_id,
        _trace_chrome_shape_ids,
        _write_xml_tree,
    )
    if not conversion_traces:
        return 0
    trace_by_slide = {
        int(trace.get("slide_num", 0)): trace
        for trace in conversion_traces
        if trace.get("slide_num") is not None
    }
    if len(trace_by_slide) < slide_count:
        return 0

    slides_by_layout: dict[str, list[int]] = {}
    for slide_num in range(1, slide_count + 1):
        rels_path = (
            extract_dir
            / "ppt"
            / "slides"
            / "_rels"
            / f"slide{slide_num}.xml.rels"
        )
        layout_target = _find_relationship_target(rels_path, SLIDE_LAYOUT_REL_TYPE)
        if not layout_target:
            raise RuntimeError(f"Slide {slide_num} has no slide layout relationship")
        layout_part = _resolve_package_target(
            f"ppt/slides/slide{slide_num}.xml",
            layout_target,
        )
        slides_by_layout.setdefault(layout_part, []).append(slide_num)

    promoted = 0
    promoted_roles = 0
    for layout_part, slide_nums in slides_by_layout.items():
        if len(slide_nums) < 2:
            continue
        slide_state: dict[int, dict[str, Any]] = {}
        for slide_num in slide_nums:
            slide_path = extract_dir / "ppt" / "slides" / f"slide{slide_num}.xml"
            rels_path = (
                extract_dir
                / "ppt"
                / "slides"
                / "_rels"
                / f"slide{slide_num}.xml.rels"
            )
            tree = ET.parse(slide_path)
            root = tree.getroot()
            slide_state[slide_num] = {
                "path": slide_path,
                "rels": _read_relationships(rels_path),
                "root": root,
                "shapes": _top_level_shapes_by_id(root),
                "timing_shape_ids": _timing_shape_ids(root),
                "tokens": _trace_chrome_shape_ids(trace_by_slide.get(slide_num)),
                "tree": tree,
            }

        common_tokens = set.intersection(*(
            set(state["tokens"])
            for state in slide_state.values()
        ))
        common_tokens.difference_update(_PAGE_NUMBER_TOKENS)
        candidates: dict[str, dict[int, str]] = {}
        for token in sorted(common_tokens):
            shape_ids_by_slide: dict[int, str] = {}
            canonical_shapes: set[bytes] = set()
            for slide_num in slide_nums:
                state = slide_state[slide_num]
                shape_ids = state["tokens"].get(token, [])
                if len(shape_ids) != 1:
                    break
                shape_id = shape_ids[0]
                shape = state["shapes"].get(shape_id)
                if shape is None or shape_id in state["timing_shape_ids"]:
                    break
                if not _shape_relationships_supported(shape, state["rels"]):
                    break
                shape_ids_by_slide[slide_num] = shape_id
                canonical_shapes.add(_canonical_shape_xml(shape, state["rels"]))
            if len(shape_ids_by_slide) == len(slide_nums) and len(canonical_shapes) == 1:
                candidates[token] = shape_ids_by_slide

        if not candidates:
            continue

        # Layout shapes render behind slide-local shapes. Keep visual z-order by
        # accepting only the identical leading prefix shared by every family page.
        token_by_shape_id = {
            slide_num: {
                shape_ids[slide_num]: token
                for token, shape_ids in candidates.items()
            }
            for slide_num in slide_nums
        }
        leading_orders: list[list[str]] = []
        for slide_num in slide_nums:
            order: list[str] = []
            for shape_id in slide_state[slide_num]["shapes"]:
                token = token_by_shape_id[slide_num].get(shape_id)
                if token is None:
                    break
                order.append(token)
            leading_orders.append(order)
        safe_tokens = list(leading_orders[0])
        for order in leading_orders[1:]:
            common_length = 0
            for expected, actual in zip(safe_tokens, order):
                if expected != actual:
                    break
                common_length += 1
            safe_tokens = safe_tokens[:common_length]
            if not safe_tokens:
                break
        if not safe_tokens:
            continue

        layout_path = extract_dir / layout_part
        layout_rels_path = _relationships_path_for_part(extract_dir, layout_part)
        for token in safe_tokens:
            shape_ids_by_slide = candidates[token]
            first_slide = slide_nums[0]
            first_state = slide_state[first_slide]
            shape = first_state["shapes"][shape_ids_by_slide[first_slide]]
            layout_shape = _copy_shape_relationships_to_part(
                shape,
                first_state["rels"],
                layout_rels_path,
            )
            _append_shape_to_part(layout_path, layout_shape)
            promoted_roles += 1
            for slide_num, shape_id in shape_ids_by_slide.items():
                state = slide_state[slide_num]
                shape_to_remove = state["shapes"].get(shape_id)
                sp_tree = state["root"].find(
                    f".//{{{PML_NS}}}cSld/{{{PML_NS}}}spTree"
                )
                if sp_tree is not None and shape_to_remove is not None:
                    sp_tree.remove(shape_to_remove)
                    promoted += 1

        for slide_num in slide_nums:
            state = slide_state[slide_num]
            _write_xml_tree(state["path"], state["tree"])

    if verbose and promoted:
        print(
            "  Baseline layout chrome: "
            f"promoted {promoted} slide shape(s) across "
            f"{promoted_roles} shared object(s)"
        )
    return promoted
