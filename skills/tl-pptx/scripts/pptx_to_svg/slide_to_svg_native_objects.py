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
from typing import Any

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

def _roundtrip_chart_package(
    node: ShapeNode,
    ctx: AssemblyContext,
    graphic_data: ET.Element | None,
) -> dict[str, Any] | None:
    """Collect the closed source chart dependency graph for exact round-trip."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .slide_to_svg import (
        _package_content_types,
        _package_rels_path,
    )
    if not ctx.preserve_placeholder_inheritance or graphic_data is None:
        return None
    chart_ref = graphic_data.find(f"{{{CHART_URI}}}chart")
    if chart_ref is None:
        return None
    rel_id = chart_ref.attrib.get(f"{{{NS['r']}}}id")
    chart_part = ctx.slide_part.resolve_rel(rel_id or "")
    if not chart_part or not chart_part.startswith("ppt/charts/"):
        return None

    try:
        defaults, overrides = _package_content_types(ctx)
        pending = [chart_part]
        seen: set[str] = set()
        parts: list[dict[str, str]] = []
        while pending:
            part_name = pending.pop()
            if part_name in seen:
                continue
            if not part_name.startswith(
                ("ppt/charts/", "ppt/embeddings/", "ppt/theme/")
            ):
                return None
            payload = ctx.pkg.read_part_bytes(part_name)
            if payload is None:
                return None
            seen.add(part_name)
            content_type = overrides.get(part_name) or defaults.get(
                posixpath.splitext(part_name)[1].lstrip(".").lower(),
                "",
            )
            parts.append({
                "content_type": content_type,
                "encoding": "base64",
                "name": part_name,
                "payload": base64.b64encode(payload).decode("ascii"),
                "sha256": hashlib.sha256(payload).hexdigest(),
            })

            rels_name = _package_rels_path(part_name)
            rels_payload = ctx.pkg.read_part_bytes(rels_name)
            if rels_payload is None:
                continue
            try:
                rels_root = ET.fromstring(rels_payload)
            except ET.ParseError:
                return None
            for relationship in rels_root.findall(
                f"{{{NS['rel']}}}Relationship"
            ):
                if relationship.attrib.get("TargetMode") == "External":
                    return None
                target = relationship.attrib.get("Target")
                if not target:
                    return None
                resolved = posixpath.normpath(
                    posixpath.join(posixpath.dirname(part_name), target)
                ).lstrip("/")
                if resolved.startswith("../"):
                    return None
                pending.append(resolved)
            parts.append({
                "content_type": "",
                "encoding": "base64",
                "name": rels_name,
                "payload": base64.b64encode(rels_payload).decode("ascii"),
                "sha256": hashlib.sha256(rels_payload).hexdigest(),
            })
    except RuntimeError:
        return None

    frame = copy.deepcopy(node.xml)
    nv_pr = frame.find("p:nvGraphicFramePr/p:nvPr", NS)
    custom_data = nv_pr.find("p:custDataLst", NS) if nv_pr is not None else None
    if custom_data is not None:
        nv_pr.remove(custom_data)
    relationship_attrs = [
        (owner, name)
        for owner in frame.iter()
        for name in owner.attrib
        if isinstance(name, str) and name.startswith(f"{{{NS['r']}}}")
    ]
    if len(relationship_attrs) != 1 or relationship_attrs[0][0].tag != f"{{{CHART_URI}}}chart":
        return None
    frame_payload = ET.tostring(frame, encoding="utf-8")
    return {
        "chart_part": chart_part,
        "frame": {
            "encoding": "base64",
            "payload": base64.b64encode(frame_payload).decode("ascii"),
            "sha256": hashlib.sha256(frame_payload).hexdigest(),
        },
        "parts": sorted(parts, key=lambda item: item["name"]),
    }


def _roundtrip_graphic_frame_metadata(
    node: ShapeNode,
    ctx: AssemblyContext,
    visible_markup: str,
    *,
    top_level: bool,
) -> tuple[str, list[str]]:
    """Preserve one relationship-free native frame behind its SVG fallback."""
    if not ctx.preserve_placeholder_inheritance or not top_level:
        return "", []
    native_frame = copy.deepcopy(node.xml)
    nv_pr = native_frame.find("p:nvGraphicFramePr/p:nvPr", NS)
    custom_data = (
        nv_pr.find("p:custDataLst", NS)
        if nv_pr is not None else None
    )
    if custom_data is not None:
        # Office tags are non-visual, slide-relationship-bound metadata. The
        # validated source package sidecar retains them; the portable native
        # frame omits them so it stays relationship-free on a regenerated slide.
        nv_pr.remove(custom_data)
    if has_relationship_attributes(native_frame):
        return "", []
    raw = ET.tostring(native_frame, encoding="utf-8")
    transform = node.xfrm.to_svg_transform()
    fallback_hash = svg_native_fallback_markup_fingerprint(
        visible_markup,
        root_transform=transform,
        external_markup="".join(ctx.defs),
    )
    metadata = (
        '<metadata data-pptx-part="roundtrip-graphic-frame" '
        'data-pptx-encoding="base64" '
        f'data-pptx-ooxml-sha256="{hashlib.sha256(raw).hexdigest()}">'
        f'{base64.b64encode(raw).decode("ascii")}</metadata>'
    )
    return metadata, [
        'data-pptx-roundtrip-object="graphic-frame"',
        f'{NATIVE_FALLBACK_SHA256_ATTR}="{fallback_hash}"',
    ]


def _render_graphic_table(
    node: ShapeNode,
    ctx: AssemblyContext,
    graphic_data: ET.Element | None,
) -> tuple[str, list[str], str]:
    """Convert the <a:tbl> child of a graphicFrame to SVG plus metadata."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .slide_to_svg import (
        _diagnose_unsupported_effect,
        _metadata_group_attrs,
        _replacement_payload_metadata,
        _resolve_svg_hyperlink,
        _xml_escape,
    )
    if graphic_data is None:
        return "", [], ""
    tbl = graphic_data.find("a:tbl", NS)
    if tbl is None:
        return "", [], ""
    table_styles_part = ctx.pkg.resolve_table_styles()
    result = convert_tbl(
        tbl, node.xfrm, ctx.palette,
        table_styles=(
            table_styles_part.xml if table_styles_part is not None else None
        ),
        theme_fonts=ctx.theme_fonts,
        slide_number=ctx.slide_number,
        id_prefix=f"tbl{ctx.shape_seq[0]}",
        grad_seq=ctx.grad_seq,
        marker_seq=ctx.marker_seq,
        hyperlink_resolver=lambda rid, action: _resolve_svg_hyperlink(
            ctx,
            rid,
            action,
        ),
        strict=ctx.strict,
        diagnostic_sink=ctx.diagnose,
    )
    if result.defs:
        ctx.defs.extend(result.defs)
    replacement_attrs: list[str] = ['data-pptx-import-source="pptx"']
    payload_metadata = ""
    if result.native_payload:
        if node.name and not result.native_payload.get("name"):
            result.native_payload["name"] = node.name
        payload_metadata = _replacement_payload_metadata(result.native_payload)
        replacement_attrs.append('data-pptx-replace-with="table"')
        replacement_attrs.append('data-pptx-native-authority="json"')
    elif result.native_status:
        replacement_attrs.append(
            'data-pptx-replacement-status="'
            f'{_xml_escape(result.native_status)}"'
        )
    if result.effect_reason:
        effect_metadata = unsupported_effect_metadata(result.effect_reason)
        _diagnose_unsupported_effect(ctx, effect_metadata)
        replacement_attrs.extend(_metadata_group_attrs(effect_metadata))
    return result.svg, replacement_attrs, payload_metadata


def _render_graphic_chart(
    node: ShapeNode,
    ctx: AssemblyContext,
    graphic_data: ET.Element | None,
    preview_svg: str,
) -> tuple[str, list[str], str]:
    """Return a chart fallback plus native Chart replacement metadata."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .slide_to_svg import (
        _SOURCE_PROXY_ATTRIBUTE,
        _replacement_payload_metadata,
        _roundtrip_chart_package,
        _xml_escape,
    )
    result = extract_native_chart_payload(
        graphic_data,
        node.xfrm,
        ctx.slide_part,
        ctx.pkg,
        ctx.palette,
    )
    replacement_attrs: list[str] = ['data-pptx-import-source="pptx"']
    payload_metadata = ""
    source_package = _roundtrip_chart_package(
        node,
        ctx,
        graphic_data,
    )
    if result.native_payload or source_package is not None:
        payload = result.native_payload or {
            "height": round(node.xfrm.h, 3),
            "width": round(node.xfrm.w, 3),
            "x": round(node.xfrm.x, 3),
            "y": round(node.xfrm.y, 3),
        }
        if node.name and not payload.get("name"):
            payload["name"] = node.name
        if source_package is not None:
            payload["source_package"] = source_package
            replacement_attrs.append(
                'data-pptx-roundtrip-object="source-chart-package"'
            )
        payload_metadata = _replacement_payload_metadata(payload)
        replacement_attrs.append('data-pptx-replace-with="chart"')
        replacement_attrs.append('data-pptx-native-authority="json"')
        if not result.native_payload:
            replacement_attrs.append(f'{_SOURCE_PROXY_ATTRIBUTE}="native-restore"')
    elif result.native_status:
        replacement_attrs.append(
            'data-pptx-replacement-status="'
            f'{_xml_escape(result.native_status)}"'
        )

    rendered = preview_svg
    if rendered:
        replacement_attrs.append('data-pptx-fallback-kind="source-preview"')
    elif result.normalized_svg:
        rendered = result.normalized_svg
        replacement_attrs.append('data-pptx-fallback-kind="normalized"')
    else:
        replacement_attrs.append('data-pptx-fallback-kind="placeholder"')
    return rendered, replacement_attrs, payload_metadata


def _render_graphic_preview(node: ShapeNode, ctx: AssemblyContext) -> str:
    """Render a graphicFrame's baked fallback preview bitmap when present.

    PowerPoint stores a static raster preview for many embedded graphics
    inside ``mc:AlternateContent``. The Fallback branch is normally a plain
    ``p:pic`` (sometimes nested), so any conformant viewer that can't speak
    the richer object paints the preview. We do the same for flat preview SVGs.

    Falls back to '' when the deck has no Fallback pic (very old or
    third-party authoring tools sometimes omit it). Caller then emits the
    dashed placeholder.
    """
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .slide_to_svg import (
        _diagnose_picture_result,
    )
    ac = node.xml.find("a:graphic/a:graphicData/mc:AlternateContent", NS)
    if ac is None:
        return ""
    pic = ac.find("mc:Fallback//p:pic", NS)
    if pic is None:
        # Some authoring tools put the preview directly in mc:Choice.
        pic = ac.find("mc:Choice//p:pic", NS)
        if pic is None:
            return ""

    # The inner pic carries its own absolute xfrm in this deck (and in every
    # well-formed PPTX I've seen — PowerPoint copies the graphicFrame xfrm
    # there during save). If it's missing, fall back to the graphicFrame's
    # xfrm so the preview at least lands somewhere visible.
    inner_xfrm = node.xfrm
    pic_xfrm_elem = pic.find("p:spPr/a:xfrm", NS)
    if pic_xfrm_elem is not None:
        from .emu_units import parse_xfrm
        parsed = parse_xfrm(pic_xfrm_elem)
        if parsed.w > 0 and parsed.h > 0:
            inner_xfrm = parsed

    result = convert_picture(
        pic, inner_xfrm, ctx.slide_part, ctx.pkg,
        media_subdir=ctx.media_subdir,
        embed_inline=ctx.embed_images,
        asset_name_map=ctx.asset_name_map,
        strict=ctx.strict,
    )
    _diagnose_picture_result(ctx, result)
    if not result.svg:
        return ""
    ctx.media.update(result.media)
    return result.svg


# ---------------------------------------------------------------------------
# Background
# ---------------------------------------------------------------------------

