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
from pathlib import Path
from xml.etree import ElementTree as ET
from native_payloads import (
    NativePayloadError,
)
from ..canvas_contract import (
    CanvasContractError,
)

from .template_structure import (
    NativeLayoutSpec,
    NativePlaceholderSpec,
    NativeStructureContract,
    PptxStructureLock,
    TemplateElementSpec,
    TemplateSlideSpec,
)

def match_native_placeholders(
    spec: TemplateSlideSpec,
    layout: NativeLayoutSpec,
) -> tuple[tuple[TemplateElementSpec, NativePlaceholderSpec], ...]:
    """Match slide placeholder markers to source layout placeholder identities."""
    from .template_structure import (
        TemplateStructureError,
        _PRESERVE_PLACEHOLDER_TYPE_ORDER,
    )
    available = list(layout.placeholders)
    matches: list[tuple[TemplateElementSpec, NativePlaceholderSpec]] = []
    for item in spec.placeholders:
        allowed_types = _PRESERVE_PLACEHOLDER_TYPE_ORDER.get(
            item.placeholder or "",
            (),
        )
        candidate_index = None
        for placeholder_type in allowed_types:
            for index, candidate in enumerate(available):
                if candidate.placeholder_type != placeholder_type:
                    continue
                if (
                    item.placeholder_idx is not None
                    and candidate.effective_idx != item.placeholder_idx
                ):
                    continue
                candidate_index = index
                break
            if candidate_index is not None:
                break
        if candidate_index is None:
            idx_note = (
                f" idx={item.placeholder_idx}"
                if item.placeholder_idx is not None
                else ""
            )
            raise TemplateStructureError(
                f"{spec.svg_path.name}: placeholder {item.element_id!r} "
                f"({item.placeholder}{idx_note}) has no compatible source placeholder "
                f"in layout {layout.key!r}"
            )
        matches.append((item, available.pop(candidate_index)))
    return tuple(matches)


def native_structure_lock_errors(
    specs: list[TemplateSlideSpec],
    structure_lock: PptxStructureLock,
    contract: NativeStructureContract,
) -> list[str]:
    """Return preserve-mode mismatches against the imported source contract."""
    from .template_structure import (
        TemplateStructureError,
    )
    if structure_lock.mode != "preserve":
        return []
    errors: list[str] = []
    references = {item.slide_num: item for item in structure_lock.layouts}
    contract_layouts = {layout.key: layout for layout in contract.layouts}

    for reference in structure_lock.layouts:
        layout = contract_layouts.get(reference.layout_key)
        if layout is None:
            errors.append(
                f"spec_lock.md P{reference.slide_num:02d} references unknown source "
                f"layout key {reference.layout_key!r}"
            )
            continue
        if reference.layout_name and reference.layout_name != layout.name:
            errors.append(
                f"spec_lock.md P{reference.slide_num:02d} layout name "
                f"{reference.layout_name!r} does not match source name {layout.name!r}"
            )

    master_contracts: dict[str, tuple[tuple[object, ...], ...]] = {}
    layout_contracts: dict[str, tuple[tuple[object, ...], ...]] = {}
    for spec in specs:
        reference = references.get(spec.slide_num)
        if reference is None:
            continue
        layout = contract_layouts.get(reference.layout_key)
        if layout is None:
            continue
        master_contract = tuple(
            item.contract_signature() for item in spec.master_elements
        )
        expected_master = master_contracts.setdefault(
            layout.master_key,
            master_contract,
        )
        if master_contract != expected_master:
            errors.append(
                f"{spec.svg_path.name}: preview master layer differs from another "
                f"page using source master {layout.master_key!r}"
            )
        expected_layout = layout_contracts.setdefault(
            layout.key,
            spec.layout_contract,
        )
        if spec.layout_contract != expected_layout:
            errors.append(
                f"{spec.svg_path.name}: preview layout/placeholder contract differs "
                f"from another page using source layout {layout.key!r}"
            )
        try:
            match_native_placeholders(spec, layout)
        except TemplateStructureError as exc:
            errors.append(str(exc))
    return errors


def _placement_lint_errors(svg_path: Path) -> list[str]:
    """Enumerate every placement/paint-order violation in one pass.

    ``parse_template_slide`` fails fast on the first error, which discloses
    violations one whole fix-cycle at a time. The quality checker runs this
    pre-lint first so a single run reports every offender of the two
    highest-frequency classes: structure metadata below the root, and
    template paint-order violations.
    """
    from .template_structure import (
        _LAYERS,
        _NON_VISUAL_TAGS,
        _is_full_canvas_solid_rect,
        _local_tag,
        _parse_svg_root,
        _structure_attrs,
        _svg_canvas,
    )
    try:
        root = _parse_svg_root(svg_path)
    except (OSError, ET.ParseError, NativePayloadError):
        return []
    if _local_tag(root) != "svg":
        return []
    errors: list[str] = []
    direct_children = set(root)
    allowed_carriers = {
        carrier
        for slot in root
        if (slot.get("data-pptx-placeholder") or "").strip()
        for carrier in slot
        if carrier.get("data-pptx-carrier") is not None
    }
    for elem in root.iter():
        if elem is root or elem in direct_children:
            continue
        attrs = _structure_attrs(elem)
        if elem in allowed_carriers:
            attrs = [
                attr for attr in attrs
                if attr != "data-pptx-carrier"
            ]
        if attrs:
            element_id = elem.get("id") or _local_tag(elem) or "<unnamed>"
            errors.append(
                f"{svg_path.name}: {element_id} uses template metadata below the SVG "
                "root; only a direct slot child may declare its carrier marker"
            )
    try:
        canvas = _svg_canvas(root)
    except CanvasContractError:
        # Root-canvas validation is owned by parse_template_slide and the
        # page Checker; placement lint should not duplicate that diagnosis.
        return errors
    last_order_rank = -1
    for elem in root:
        tag = _local_tag(elem)
        if tag in _NON_VISUAL_TAGS:
            continue
        layer = (elem.get("data-pptx-layer") or "").strip().lower() or None
        if layer not in _LAYERS:
            layer = None
        is_background = _is_full_canvas_solid_rect(elem, canvas, root=root)
        effective_layer = layer or ("slide" if is_background else None)
        if is_background and effective_layer is not None:
            order_rank = {"master": 0, "layout": 1, "slide": 2}[effective_layer]
        elif effective_layer == "master":
            order_rank = 3
        elif effective_layer == "layout":
            order_rank = 4
        else:
            order_rank = 5
        if order_rank < last_order_rank:
            errors.append(
                f"{svg_path.name}: {elem.get('id') or tag} violates template paint "
                "order; use Master background, Layout background, Slide background, "
                "Master shapes, Layout shapes, then Slide content/placeholders. "
                "Full-canvas solid rects are treated as backgrounds; translucent overlays are not backgrounds"
            )
            continue
        last_order_rank = order_rank
    return errors


def validate_template_svg(svg_path: Path) -> list[str]:
    """Return per-file template metadata errors for quality-check integration."""
    from .template_structure import (
        TemplateStructureError,
        parse_template_slide,
    )
    errors = _placement_lint_errors(svg_path)
    try:
        parse_template_slide(svg_path, 1)
    except TemplateStructureError as exc:
        message = str(exc)
        if message not in errors:
            errors.append(message)
    return errors
