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

def _convert_node(node: ShapeNode, ctx: AssemblyContext, *, top_level: bool) -> str:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .slide_to_svg import (
        _convert_connector,
        _convert_graphic_fallback,
        _convert_group,
        _convert_picture,
        _convert_shape,
        _fallback_node_svg,
    )
    previous_node = ctx.current_node
    ctx.current_node = node
    try:
        if node.hidden and not ctx.keep_hidden:
            ctx.diagnose(
                "author-hidden-omitted",
                "Source shape is marked hidden by its author",
                "exclude the hidden shape and its children from the SVG view",
            )
            return ""
        if node.kind == SHAPE:
            return _convert_shape(node, ctx, top_level=top_level)
        if node.kind == PICTURE:
            return _convert_picture(node, ctx, top_level=top_level)
        if node.kind == CONNECTOR:
            return _convert_connector(node, ctx, top_level=top_level)
        if node.kind == GROUP:
            return _convert_group(node, ctx, top_level=top_level)
        if node.kind == GRAPHIC:
            return _convert_graphic_fallback(node, ctx, top_level=top_level)
        return ""
    except ValueError as exc:
        if ctx.strict:
            raise
        ctx.diagnose(
            "object-replaced",
            str(exc),
            "replace only this object with a visible placeholder",
            node=node,
        )
        return _fallback_node_svg(node, ctx, top_level=top_level)
    finally:
        ctx.current_node = previous_node


def _fallback_node_svg(
    node: ShapeNode,
    ctx: AssemblyContext,
    *,
    top_level: bool,
    source_proxy: bool = False,
) -> str:
    """Keep one unsupported source object visible without aborting its deck."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .slide_to_svg import (
        _EXTERNAL_LINKED_IMAGE_PROXY_ATTRIBUTE,
        _SOURCE_PROXY_ATTRIBUTE,
        _SOURCE_PROXY_KIND,
        _wrap_shape_group,
        _xml_escape,
    )
    if node.xfrm.w <= 0 or node.xfrm.h <= 0:
        return ""
    x = fmt_num(node.xfrm.x)
    y = fmt_num(node.xfrm.y)
    width = fmt_num(node.xfrm.w)
    height = fmt_num(node.xfrm.h)
    label = _xml_escape(node.name or f"Unsupported {node.kind}")
    inner = (
        f'<rect x="{x}" y="{y}" width="{width}" height="{height}" '
        'fill="#F8FAFC" fill-opacity="0.72" stroke="#DC2626" '
        'stroke-width="1" stroke-dasharray="6 4"/>'
        f'<text x="{fmt_num(node.xfrm.x + 8)}" '
        f'y="{fmt_num(node.xfrm.y + min(18, node.xfrm.h / 2))}" '
        f'font-size="12" fill="#991B1B">{label}</text>'
    )
    extra_attrs = (
        [
            f'{_SOURCE_PROXY_ATTRIBUTE}="{_SOURCE_PROXY_KIND}"',
            f'{_EXTERNAL_LINKED_IMAGE_PROXY_ATTRIBUTE}="true"',
        ]
        if source_proxy
        else None
    )
    return _wrap_shape_group(
        inner,
        node,
        ctx,
        top_level=top_level,
        extra_attrs=extra_attrs,
    )


# ---------------------------------------------------------------------------
# Shape (<p:sp>)
# ---------------------------------------------------------------------------

def _convert_shape(node: ShapeNode, ctx: AssemblyContext, *, top_level: bool) -> str:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .slide_to_svg import (
        _block_formula_carrier_error,
        _block_formula_zone,
        _build_geometry_xml,
        _clip_blip_image,
        _counter_reflected_text_svg,
        _diagnose_formula_fallback,
        _diagnose_picture_result,
        _effective_placeholder_tx_body,
        _fallback_node_svg,
        _geometry_group_attrs,
        _materialize_inherited_list_styles,
        _placeholder_sp_pr_metadata,
        _prepare_inline_formula_resolver,
        _render_block_formula,
        _resolve_geometry,
        _resolve_svg_hyperlink,
        _resolve_text_style_default,
        _shape_scope,
        _txbody_metadata,
        _wrap_shape_group,
        _xml_escape,
    )
    sp_pr = node.xml.find("p:spPr", NS)

    # Check for blipFill (image-filled shape, e.g. Canva exports where images
    # are expressed as <p:sp> + <a:blipFill> rather than <p:pic>).
    geom = _resolve_geometry(node, sp_pr)

    blip_fill_elem = sp_pr.find("a:blipFill", NS) if sp_pr is not None else None
    blip_image = ""
    if blip_fill_elem is not None:
        try:
            blip_result = convert_blip_fill(
                blip_fill_elem, node.xfrm, ctx.slide_part, ctx.pkg,
                media_subdir=ctx.media_subdir,
                embed_inline=ctx.embed_images,
                asset_name_map=ctx.asset_name_map,
                strict=ctx.strict,
            )
        except LinkedImageResolutionError as exc:
            if ctx.strict:
                raise
            ctx.diagnose(
                "linked-image-proxy",
                str(exc),
                "retain the complete source object as a non-editable proxy",
            )
            return _fallback_node_svg(
                node,
                ctx,
                top_level=top_level,
                source_proxy=True,
            )
        except (ValueError, MediaResolutionError) as exc:
            if ctx.strict:
                raise
            ctx.diagnose(
                "image-fill-omitted",
                str(exc),
                "omit the image fill and retain shape geometry/text",
            )
        else:
            if blip_result.external_linked:
                ctx.diagnose(
                    "linked-image-proxy",
                    "Externally linked image fills are source-backed",
                    "retain the complete source object as a non-editable proxy",
                )
                return _fallback_node_svg(
                    node,
                    ctx,
                    top_level=top_level,
                    source_proxy=True,
                )
            _diagnose_picture_result(ctx, blip_result)
            if blip_result.svg:
                blip_image = _clip_blip_image(blip_result.svg, geom, ctx)
                ctx.media.update(blip_result.media)

    # Text body (a:txBody)
    source_tx_body = node.xml.find("p:txBody", NS)
    tx_body = _effective_placeholder_tx_body(
        source_tx_body,
        node.inherited_body_properties,
    )
    is_vertical = is_vertical_txbody(tx_body, node.xfrm)
    source_xfrm = sp_pr.find("a:xfrm", NS) if sp_pr is not None else None
    source_frame = (
        parse_xfrm(source_xfrm)
        if source_xfrm is not None and source_xfrm.find("a:ext", NS) is not None
        else node.xfrm
    )
    if (
        (source_frame.w <= 0 or source_frame.h <= 0)
        and tx_body is not None
        and any(element.text for element in tx_body.iter(f"{{{NS['a']}}}t"))
        and (
            node.placeholder is None
            or _shape_scope(ctx) == "slide"
            or (sp_pr is not None and sp_pr.find("a:xfrm", NS) is not None)
        )
    ):
        # Report explicit collapsed frames even when placeholder inheritance
        # recovers their geometry. Standalone guides can await inheritance.
        ctx.diagnose(
            "degenerate-text-frame",
            "Text has a nonpositive source frame: "
            + " ".join(fmt_num(value) for value in (
                source_frame.x, source_frame.y, source_frame.w, source_frame.h,
            )),
            "retain text using available placeholder geometry or source overflow",
        )
    block_formula = _block_formula_zone(tx_body)
    block_formula_failed = False
    if block_formula is not None and not is_vertical:
        try:
            carrier_error = _block_formula_carrier_error(
                node,
                top_level=top_level,
            )
            if carrier_error is not None:
                raise FormulaImportError(carrier_error)
            imported_formula = import_formula(block_formula, display=True, palette=ctx.palette)
        except FormulaImportError as exc:
            _diagnose_formula_fallback(ctx, exc)
            block_formula_failed = True
        else:
            return _render_block_formula(
                node,
                ctx,
                imported_formula,
                top_level=top_level,
            )
    inline_formula_resolver = _prepare_inline_formula_resolver(
        tx_body,
        ctx,
        allow_native=not is_vertical,
        force_opaque=block_formula_failed,
    )
    local_has_run_effects = txbody_has_run_effects(source_tx_body)
    inherited_has_run_effects = txbody_has_run_effects(
        *node.inherited_lst_styles
    )
    metadata_tx_body, inherited_styles_materialized = (
        _materialize_inherited_list_styles(
            tx_body,
            node.inherited_lst_styles,
        )
    )
    export_tx_body = (
        source_tx_body
        if (
            ctx.preserve_placeholder_inheritance
            and node.placeholder is not None
            and source_tx_body is not None
        )
        else metadata_tx_body
    )
    has_run_effects = local_has_run_effects or inherited_has_run_effects
    if geom is not None and has_run_effects:
        if is_vertical:
            geom.attrs.update(unsupported_effect_metadata(
                "unsupported-run-effect-route:vertical-text"
            ))
        elif tx_body is not None and has_relationship_attributes(tx_body):
            geom.attrs.update(unsupported_effect_metadata(
                "unsupported-run-effect-route:relationship-bearing-text"
            ))
        elif inherited_has_run_effects and not inherited_styles_materialized:
            geom.attrs.update(unsupported_effect_metadata(
                "unsupported-run-effect-route:inherited-text-style"
            ))

    # Geometry (fill is "none" when blipFill is present, so only stroke draws)
    geom_xml = _build_geometry_xml(node, sp_pr, ctx, geom=geom)

    try:
        text_default_fill = _resolve_text_style_default(node, ctx)
        if tx_body is not None and is_vertical:
            text_result = convert_vertical_txbody(
                tx_body, node.xfrm, ctx.palette,
                theme_fonts=ctx.theme_fonts,
                slide_number=ctx.slide_number,
                default_fill=text_default_fill,
                default_font_size_px=DEFAULT_FONT_SIZE_PX,
                fallback_lst_styles=node.inherited_lst_styles,
                id_prefix=f"{ctx.group_id_prefix}txt",
                id_seq=ctx.grad_seq,
                hyperlink_resolver=lambda rid, action: _resolve_svg_hyperlink(
                    ctx,
                    rid,
                    action,
                ),
                inline_formula_resolver=inline_formula_resolver,
                strict=ctx.strict,
                diagnostic_sink=ctx.diagnose,
            )
        else:
            text_result = convert_txbody(
                tx_body, node.xfrm, ctx.palette,
                theme_fonts=ctx.theme_fonts,
                slide_number=ctx.slide_number,
                default_fill=text_default_fill,
                default_font_size_px=DEFAULT_FONT_SIZE_PX,
                fallback_lst_styles=node.inherited_lst_styles,
                id_prefix=f"{ctx.group_id_prefix}txt",
                id_seq=ctx.grad_seq,
                hyperlink_resolver=lambda rid, action: _resolve_svg_hyperlink(
                    ctx,
                    rid,
                    action,
                ),
                inline_formula_resolver=inline_formula_resolver,
                strict=ctx.strict,
                diagnostic_sink=ctx.diagnose,
            ) if tx_body is not None else TextResult()
    except ValueError as exc:
        if ctx.strict:
            raise
        ctx.diagnose(
            "text-omitted",
            str(exc),
            "omit this text body and retain the object's other visuals",
        )
        text_result = TextResult()
    if text_result.defs:
        ctx.defs.extend(text_result.defs)
    visible_text_svg = (
        text_result.svg
        if is_vertical
        else _counter_reflected_text_svg(text_result.svg, node.xfrm)
    )

    if is_vertical:
        # Vertical text: geometry + image in one group, text in separate group
        geom_inner = (blip_image + "\n" + geom_xml) if blip_image else geom_xml
        shape_xml = _wrap_shape_group(
            geom_inner,
            node,
            ctx,
            top_level=top_level,
            extra_attrs=_geometry_group_attrs(geom),
        )
        if not visible_text_svg:
            return shape_xml
        text_frame_attr = ""
        if text_result.frame is not None:
            text_frame = " ".join(fmt_num(value, 8) for value in text_result.frame)
            text_frame_attr = f' data-pptx-frame="{text_frame}"'
        text_group = (
            f'<g id="{ctx.group_id_prefix}shape-{node.spid or ctx.shape_seq[0]}-text"'
            f' data-name="{_xml_escape(node.name)} text"{text_frame_attr}>\n'
            f"{visible_text_svg}\n</g>"
        )
        return f"{shape_xml}\n{text_group}"

    # Normal: image (behind) + geometry (stroke) + text (top)
    inner_parts = []
    if blip_image:
        inner_parts.append(blip_image)
    if geom_xml:
        inner_parts.append(geom_xml)
    if (
        export_tx_body is not None
        and geom is not None
        and geom_xml
        and not text_result.contains_inline_formula
    ):
        inner_parts.append(
            _txbody_metadata(
                export_tx_body,
                visible_text_svg,
            )
        )
    placeholder_sp_pr = _placeholder_sp_pr_metadata(node, ctx)
    if placeholder_sp_pr:
        inner_parts.append(placeholder_sp_pr)
    if visible_text_svg:
        inner_parts.append(visible_text_svg)
    inner = "\n".join(inner_parts) if inner_parts else ""
    return _wrap_shape_group(
        inner,
        node,
        ctx,
        top_level=top_level,
        extra_attrs=_geometry_group_attrs(geom),
    )


