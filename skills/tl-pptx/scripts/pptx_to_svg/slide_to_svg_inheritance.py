"""Per-slide composition: dispatches every ShapeNode through the right
converter, accumulates <defs>, and produces one final SVG string.

The output structure mirrors what svg_to_pptx expects so the deck can be
round-tripped:
    <svg viewBox="0 0 W H">
        <defs>
            <linearGradient id=.../>
            <marker id=.../>
            <filter id=.../>
        </defs>
        <!-- background -->
        <rect ... />        (slide background, if any)
        <g id="shape-1">...</g>
        <g id="shape-2">...</g>
        ...
    </svg>

Each top-level <g> wraps one shape and is treated by svg_to_pptx as an
animation anchor.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import posixpath
from dataclasses import dataclass, field
from xml.etree import ElementTree as ET

from pptx_shapes import (
    CONNECTOR_PRESET_TYPES,
    NATIVE_FALLBACK_SHA256_ATTR,
    has_relationship_attributes,
    svg_native_fallback_markup_fingerprint,
    svg_text_fingerprint,
)
from pptx_effects import (
    EFFECT_REASON_ATTR,
    EFFECT_STATUS_ATTR,
    NATIVE_EFFECT_ATTR,
    NATIVE_EFFECT_SHA256_ATTR,
    txbody_has_run_effects,
    unsupported_effect_metadata,
)
from hyperlink_contract import SHAPE_HYPERLINK_ATTR, SOURCE_HREF_ATTR
from svg_to_pptx.drawingml.paths import (
    PathCommand,
    normalize_path_commands,
    parse_svg_path,
    parse_svg_points,
    svg_path_to_absolute,
    transform_path_commands,
)

from .color_resolver import ColorPalette, find_color_elem, resolve_color
from .chart_to_svg import CHART_URI, CHARTEX_URI, extract_native_chart_payload
from .custgeom_to_svg import convert_custom_geom
from .effect_to_svg import (
    EffectResult,
    convert_effects,
    unsupported_target_effect_metadata,
)
from .emu_units import NS, Xfrm, fmt_num, format_canvas_px_from_emu, parse_xfrm
from .fill_to_svg import FillResult, resolve_fill
from .formula_import import (
    A14_NS,
    FormulaImport,
    FormulaImportError,
    import_formula,
    opaque_formula_preview,
)
from .import_diagnostics import (
    ImportDiagnostic,
    append_diagnostic,
)
from .hyperlinks import resolve_click_hyperlink
from .ln_to_svg import StrokeResult, resolve_stroke
from .ooxml_loader import (
    OoxmlPackage,
    PartRef,
    SlideRef,
    inherited_shape_visibility,
)
from .pic_to_svg import (
    LinkedImageResolutionError,
    MediaResolutionError,
    PictureResult,
    convert_blip_fill,
    convert_picture,
)
from .prstgeom_to_svg import GeomResult, convert_prst_geom
from .preset_svg_markup import serialize_preset_layers
from .shape_walker import (
    CONNECTOR, GRAPHIC, GROUP, PICTURE, SHAPE,
    ShapeNode, get_background, walk_sp_tree,
)
from .tbl_to_svg import convert_tbl
from .txbody_to_svg import (
    TextResult,
    convert_txbody,
    convert_vertical_txbody,
    is_vertical_txbody,
    DEFAULT_FONT_SIZE_PX,
)


# ---------------------------------------------------------------------------
# AssemblyContext
# ---------------------------------------------------------------------------


# Upstream MIT, Hugo He 2025-2026; algorithms retained during module split.
from .slide_to_svg import (
    AssemblyContext,
)

def _emit_background(slide: SlideRef, ctx: AssemblyContext,
                     w: float, h: float) -> str:
    """Inspect <p:bg> on slide / layout / master in inheritance order."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .slide_to_svg import (
        _attrs_to_xml,
        _emit_background_image,
        _theme_background_fill,
    )
    for part in (slide.part, slide.layout, slide.master):
        if part is None:
            continue
        bg = get_background(part.xml)
        if bg is None:
            continue
        bg_pr = bg.find("p:bgPr", NS)
        bg_ref = bg.find("p:bgRef", NS)
        placeholder_hex = None

        if bg_pr is None and bg_ref is not None:
            bg_pr = _theme_background_fill(slide, ctx, bg_ref)
            color_elem = find_color_elem(bg_ref)
            placeholder_hex, _ = resolve_color(color_elem, ctx.palette)
        if bg_pr is None:
            continue

        bg_image = _emit_background_image(bg_pr, part, ctx, w, h)
        if bg_image:
            return bg_image

        fill = resolve_fill(
            bg_pr, ctx.palette,
            id_prefix="bg", id_seq=ctx.grad_seq,
            placeholder_hex=placeholder_hex,
        )
        ctx.defs.extend(fill.defs)
        if not fill.attrs:
            return ""
        # Convert dict to attributes
        attrs_xml = _attrs_to_xml(fill.attrs)
        return (f'<rect x="0" y="0" width="{fmt_num(w)}" height="{fmt_num(h)}"'
                f"{attrs_xml}/>")
    return ""


def _emit_part_background(slide: SlideRef, ctx: AssemblyContext,
                          w: float, h: float) -> str:
    """Render the background declared on the part itself only.

    Distinct from `_emit_background`, which walks the slide → layout →
    master inheritance chain. Used by the layered solo renderer so each
    standalone master / layout SVG carries only its own ``<p:bg>`` — the
    inheritance is rebuilt by consumers re-stacking the layers, and we'd
    rather output nothing than have master decoration leak into a layout
    file.
    """
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .slide_to_svg import (
        _attrs_to_xml,
        _emit_background_image,
        _theme_background_fill,
    )
    bg = get_background(slide.part.xml)
    if bg is None:
        return ""
    bg_pr = bg.find("p:bgPr", NS)
    bg_ref = bg.find("p:bgRef", NS)
    placeholder_hex = None

    if bg_pr is None and bg_ref is not None:
        bg_pr = _theme_background_fill(slide, ctx, bg_ref)
        color_elem = find_color_elem(bg_ref)
        placeholder_hex, _ = resolve_color(color_elem, ctx.palette)
    if bg_pr is None:
        return ""

    bg_image = _emit_background_image(bg_pr, slide.part, ctx, w, h)
    if bg_image:
        return bg_image

    fill = resolve_fill(
        bg_pr, ctx.palette,
        id_prefix="bg", id_seq=ctx.grad_seq,
        placeholder_hex=placeholder_hex,
    )
    ctx.defs.extend(fill.defs)
    if not fill.attrs:
        return ""
    attrs_xml = _attrs_to_xml(fill.attrs)
    return (f'<rect x="0" y="0" width="{fmt_num(w)}" height="{fmt_num(h)}"'
            f"{attrs_xml}/>")


def _emit_background_image(
    bg_pr: ET.Element,
    source_part: PartRef,
    ctx: AssemblyContext,
    w: float,
    h: float,
) -> str:
    """Render a slide/layout/master background image fill as a full-canvas image."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .slide_to_svg import (
        _diagnose_picture_result,
    )
    blip_fill = bg_pr.find("a:blipFill", NS)
    if blip_fill is None:
        return ""

    result = convert_blip_fill(
        blip_fill,
        Xfrm(0.0, 0.0, w, h),
        source_part,
        ctx.pkg,
        media_subdir=ctx.media_subdir,
        embed_inline=ctx.embed_images,
        asset_name_map=ctx.asset_name_map,
        strict=ctx.strict,
    )
    _diagnose_picture_result(ctx, result)
    if result.media:
        ctx.media.update(result.media)
    return result.svg


def _theme_background_fill(
    slide: SlideRef,
    ctx: AssemblyContext,
    bg_ref: ET.Element,
) -> ET.Element | None:
    """Resolve p:bgRef idx into the theme background fill style list."""
    def reject_invalid_idx(message: str) -> None:
        if ctx.strict:
            raise ValueError(message)
        ctx.diagnose(
            "theme-background-reference-omitted",
            message,
            "omit this part's theme background fill",
        )

    idx_raw = bg_ref.attrib.get("idx")
    if not idx_raw:
        reject_invalid_idx(
            "Invalid p:bgRef@idx: expected a 1001-based theme fill index"
        )
        return None
    try:
        idx = int(idx_raw)
    except ValueError:
        reject_invalid_idx(
            f"Invalid p:bgRef@idx value {idx_raw!r}; expected a 1001-based "
            "theme fill index"
        )
        return None
    # ECMA style matrix background fill references are 1001-based.
    bg_fill_index = idx - 1001
    if bg_fill_index < 0:
        reject_invalid_idx(
            f"Invalid p:bgRef@idx value {idx_raw!r}; expected a value of 1001 "
            "or greater"
        )
        return None

    theme = ctx.pkg.resolve_theme(slide.master)
    if theme is None:
        return None
    fill_list = theme.xml.find(".//a:fmtScheme/a:bgFillStyleLst", NS)
    if fill_list is None:
        return None
    fills = [child for child in list(fill_list) if isinstance(child.tag, str)]
    if bg_fill_index >= len(fills):
        reject_invalid_idx(
            f"Invalid p:bgRef@idx value {idx_raw!r}; theme background fill list "
            f"contains {len(fills)} entries"
        )
        return None
    return fills[bg_fill_index]


def _emit_inherited_shapes(slide: SlideRef, ctx: AssemblyContext) -> list[str]:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .slide_to_svg import (
        _convert_node,
        _is_placeholder_node,
    )
    parts: list[str] = []
    show_layout_shapes, show_master_shapes = inherited_shape_visibility(slide)
    inherited_parts = (
        ("master-", slide.master, show_master_shapes),
        ("layout-", slide.layout, show_layout_shapes),
    )
    for prefix, part, visible in inherited_parts:
        if part is None or not visible:
            continue
        original_part = ctx.slide_part
        original_prefix = ctx.group_id_prefix
        ctx.slide_part = part
        ctx.group_id_prefix = prefix
        try:
            for node in walk_sp_tree(part.xml):
                if _is_placeholder_node(node):
                    continue
                chunk = _convert_node(node, ctx, top_level=True)
                if chunk:
                    parts.append(chunk)
        finally:
            ctx.slide_part = original_part
            ctx.group_id_prefix = original_prefix
    return parts


def _is_placeholder_node(node: ShapeNode) -> bool:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .slide_to_svg import (
        _is_placeholder_node,
    )
    if node.placeholder is not None:
        return True
    if node.kind == GROUP:
        return all(_is_placeholder_node(child) for child in node.children)
    return False


def _convert_placeholder_guide(node: ShapeNode, ctx: AssemblyContext,
                               *, top_level: bool) -> str:
    """Emit the source-authored appearance of one template placeholder."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .slide_to_svg import (
        _convert_node,
    )
    return _convert_node(node, ctx, top_level=top_level)


# ---------------------------------------------------------------------------
# Wrap / utilities
# ---------------------------------------------------------------------------

