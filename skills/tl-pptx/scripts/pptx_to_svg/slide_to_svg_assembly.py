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

def _diagnose_picture_result(
    ctx: AssemblyContext,
    result: PictureResult,
) -> None:
    """Project recoverable picture losses into the import report."""
    for diagnostic in result.diagnostics:
        ctx.diagnose(
            diagnostic.code,
            diagnostic.message,
            diagnostic.fallback,
        )


def _resolve_svg_hyperlink(
    ctx: AssemblyContext,
    relationship_id: str,
    action: str,
) -> str | None:
    """Resolve one source-part click link or record its explicit loss."""
    resolution = resolve_click_hyperlink(
        ctx.slide_part.rels,
        relationship_id,
        action,
        slide_index_by_part=ctx.pkg.slide_index_by_part,
    )
    if resolution.error is None:
        return resolution.href
    if ctx.strict:
        raise ValueError(resolution.error)
    ctx.diagnose(
        "hyperlink-omitted",
        resolution.error,
        "retain the object and omit only its unsupported click link",
    )
    return None


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------

def assemble_slide(
    pkg: OoxmlPackage,
    slide: SlideRef,
    palette: ColorPalette | None,
    *,
    theme_fonts: dict[str, str] | None = None,
    media_subdir: str = "images",
    embed_images: bool = False,
    keep_hidden: bool = False,
    inheritance_mode: str = "flat",
    asset_name_map: dict[str, str] | None = None,
    strict: bool = False,
    diagnostics: list[ImportDiagnostic] | None = None,
    preserve_placeholder_inheritance: bool = False,
) -> tuple[str, dict[str, bytes]]:
    """Convert one slide to a complete SVG string + media files map.

    inheritance_mode controls how master/layout shapes are rendered:
        - "flat" (default): emit the effective visible Master/Layout
          non-placeholder shapes inline inside the slide SVG, honoring both
          source ``showMasterSp`` flags. This view is used for round-trip
          fidelity with svg_to_pptx.
        - "layered": skip inherited shapes entirely. The slide SVG contains
          only its own shapes. Callers (e.g. /create-template's PPTX import)
          render master/layout once each as separate SVGs and record the
          inheritance graph in inheritance.json.
    """
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .slide_to_svg import (
        AssemblyContext,
        _convert_node,
        _emit_background,
        _emit_inherited_shapes,
        _emit_part_background,
    )
    ctx = AssemblyContext(
        palette=palette,
        pkg=pkg,
        slide_part=slide.part,
        theme=pkg.resolve_theme(slide.master),
        slide_number=pkg.first_slide_number + slide.index - 1,
        theme_fonts=theme_fonts or {},
        media_subdir=media_subdir,
        embed_images=embed_images,
        keep_hidden=keep_hidden,
        strict=strict,
        render_graphic_previews=(inheritance_mode == "flat"),
        preserve_placeholder_inheritance=preserve_placeholder_inheritance,
        asset_name_map=asset_name_map or {},
        diagnostics=diagnostics if diagnostics is not None else [],
        source_slide_index=slide.index,
    )
    ctx.bind_palette()

    canvas_w, canvas_h = pkg.slide_size_px
    canvas_w_token, canvas_h_token = (
        format_canvas_px_from_emu(value) for value in pkg.slide_size_emu
    )

    # Background (cSld/bg) — emit as the first body element.
    body_parts: list[str] = []
    try:
        bg_xml = (
            _emit_background(slide, ctx, canvas_w, canvas_h)
            if inheritance_mode == "flat"
            else _emit_part_background(
                SlideRef(index=slide.index, part=slide.part, layout=None, master=slide.master),
                ctx, canvas_w, canvas_h,
            )
        )
    except (ValueError, MediaResolutionError) as exc:
        if strict:
            raise
        ctx.diagnose(
            "background-omitted",
            str(exc),
            "omit the unsupported background and continue the slide",
        )
        bg_xml = ""
    if bg_xml:
        body_parts.append(bg_xml)

    if inheritance_mode == "flat":
        # Inherited layout/master shapes render behind slide-local shapes. Skip
        # placeholders; they define editable regions, not visible background.
        body_parts.extend(_emit_inherited_shapes(slide, ctx))
    elif inheritance_mode != "layered":
        raise ValueError(
            f"inheritance_mode must be 'flat' or 'layered', got {inheritance_mode!r}"
        )

    # Walk shapes — placeholders without their own xfrm inherit geometry from
    # layout, then master.
    nodes = walk_sp_tree(
        slide.part.xml,
        layout_xml=slide.layout.xml if slide.layout else None,
        master_xml=slide.master.xml if slide.master else None,
    )
    for node in nodes:
        chunk = _convert_node(node, ctx, top_level=True)
        if chunk:
            body_parts.append(chunk)

    # Compose final SVG
    defs_xml = "".join(ctx.defs) if ctx.defs else ""
    defs_block = f"<defs>{defs_xml}</defs>" if defs_xml else ""

    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'xmlns:xlink="http://www.w3.org/1999/xlink" version="1.1" '
        f'width="{canvas_w_token}" height="{canvas_h_token}" '
        f'viewBox="0 0 {canvas_w_token} {canvas_h_token}">'
        f"{defs_block}"
        + "\n".join(body_parts)
        + "</svg>"
    )
    return svg, ctx.media


def assemble_part_solo(
    pkg: OoxmlPackage,
    part: PartRef,
    palette: ColorPalette | None,
    *,
    role: str,
    parent_master: PartRef | None = None,
    theme_fonts: dict[str, str] | None = None,
    media_subdir: str = "images",
    embed_images: bool = False,
    keep_hidden: bool = False,
    asset_name_map: dict[str, str] | None = None,
    strict: bool = False,
    diagnostics: list[ImportDiagnostic] | None = None,
) -> tuple[str, dict[str, bytes]]:
    """Render a single slideMaster or slideLayout part as a standalone SVG.

    Used by the layered export path. Skips placeholders the same way
    `_emit_inherited_shapes` does, so the output represents the part's
    decorative / structural shapes only — what the part *contributes* to its
    descendants. The first ancestor's background (if any) is emitted as the
    first body element so the output reads like a real slide.

    Args:
        role: 'master' or 'layout'. Used as the group_id_prefix to keep ids
            unique when the workspace inlines multiple parts in a viewer.
        parent_master: when ``role == "layout"``, pass the parent slide
            master so theme-style background fills (``<p:bgRef idx=...>``)
            can resolve via the theme attached to that master. For
            ``role == "master"`` the master is its own parent and this
            argument is ignored.
    """
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .slide_to_svg import (
        AssemblyContext,
        _convert_node,
        _convert_placeholder_guide,
        _emit_part_background,
        _is_placeholder_node,
    )
    if role not in {"master", "layout"}:
        raise ValueError(f"role must be 'master' or 'layout', got {role!r}")

    ctx = AssemblyContext(
        palette=palette,
        pkg=pkg,
        slide_part=part,
        theme=pkg.resolve_theme(part if role == "master" else parent_master),
        theme_fonts=theme_fonts or {},
        media_subdir=media_subdir,
        embed_images=embed_images,
        keep_hidden=keep_hidden,
        strict=strict,
        group_id_prefix=f"{role}-",
        render_graphic_previews=False,
        asset_name_map=asset_name_map or {},
        diagnostics=diagnostics if diagnostics is not None else [],
    )
    ctx.bind_palette()

    canvas_w, canvas_h = pkg.slide_size_px
    canvas_w_token, canvas_h_token = (
        format_canvas_px_from_emu(value) for value in pkg.slide_size_emu
    )

    body_parts: list[str] = []

    # Layered semantics: each part's standalone SVG must contain only that
    # part's own contribution. The master gets its own bg, the layout gets
    # its own bg only if it overrides the master's, and consumers re-stack
    # the layers when they need a flat view. We therefore inspect <p:bg> on
    # this part alone — never inherited from above. Theme-style fills
    # (<p:bgRef idx=...>) still need the parent master's <a:fmtScheme> to
    # resolve, hence the SlideRef.master plumbing below.
    if role == "master":
        master_for_theme: PartRef | None = part
    else:
        master_for_theme = parent_master
    fake_slide = SlideRef(
        index=0,
        part=part,
        layout=None,
        master=master_for_theme,
    )
    try:
        bg_xml = _emit_part_background(fake_slide, ctx, canvas_w, canvas_h)
    except (ValueError, MediaResolutionError) as exc:
        if strict:
            raise
        ctx.diagnose(
            "background-omitted",
            str(exc),
            "omit the unsupported background and continue the part",
        )
        bg_xml = ""
    if bg_xml:
        body_parts.append(bg_xml)

    # Walk shapes. Layered master/layout SVGs retain each placeholder's source
    # appearance so mirror materialization can recover its editable decoration.
    for node in walk_sp_tree(part.xml):
        if _is_placeholder_node(node):
            chunk = _convert_placeholder_guide(node, ctx, top_level=True)
        else:
            chunk = _convert_node(node, ctx, top_level=True)
        if chunk:
            body_parts.append(chunk)

    defs_xml = "".join(ctx.defs) if ctx.defs else ""
    defs_block = f"<defs>{defs_xml}</defs>" if defs_xml else ""

    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'xmlns:xlink="http://www.w3.org/1999/xlink" version="1.1" '
        f'width="{canvas_w_token}" height="{canvas_h_token}" '
        f'viewBox="0 0 {canvas_w_token} {canvas_h_token}">'
        f"{defs_block}"
        + "\n".join(body_parts)
        + "</svg>"
    )
    return svg, ctx.media


# ---------------------------------------------------------------------------
# Per-node dispatch
# ---------------------------------------------------------------------------

