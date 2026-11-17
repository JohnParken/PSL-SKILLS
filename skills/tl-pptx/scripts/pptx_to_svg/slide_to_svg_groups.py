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

def _convert_group(node: ShapeNode, ctx: AssemblyContext, *, top_level: bool) -> str:
    """Render group contents flat (children already remapped to slide space)."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .slide_to_svg import (
        _convert_node,
        _diagnose_unsupported_effect,
        _metadata_group_attrs,
        _wrap_shape_group,
    )
    if node.children and (
        (node.xfrm.w == 0 and node.xfrm.ch_w is not None and node.xfrm.ch_w > 0)
        or (node.xfrm.h == 0 and node.xfrm.ch_h is not None and node.xfrm.ch_h > 0)
    ):
        ctx.diagnose(
            "group-collapsed-axis",
            "The source group maps a positive child-coordinate extent to a zero frame axis",
            "retain the source coordinate mapping; child geometry may collapse or be omitted",
        )
    parent_fill = ctx.group_fills[-1] if ctx.group_fills else None
    group_properties = node.xml.find("p:grpSpPr", NS)
    try:
        resolved_fill = resolve_fill(
            group_properties,
            ctx.palette,
            id_prefix="g",
            id_seq=ctx.grad_seq,
            group_fill=parent_fill,
        )
    except ValueError as exc:
        if ctx.strict:
            raise
        ctx.diagnose(
            "group-fill-omitted",
            str(exc),
            "inherit the nearest resolved ancestor group fill when available",
        )
        group_fill = parent_fill
    else:
        ctx.defs.extend(resolved_fill.defs)
        group_fill = (
            FillResult(attrs=dict(resolved_fill.attrs))
            if resolved_fill.attrs
            else parent_fill
        )

    inner_parts: list[str] = []
    ctx.group_fills.append(group_fill)
    try:
        for child in node.children:
            chunk = _convert_node(child, ctx, top_level=False)
            if chunk:
                inner_parts.append(chunk)
    finally:
        ctx.group_fills.pop()
    if not inner_parts:
        return ""
    inner = "\n".join(inner_parts)
    effect_metadata = unsupported_target_effect_metadata(
        node.xml.find("p:grpSpPr", NS),
        "group",
    )
    _diagnose_unsupported_effect(ctx, effect_metadata)
    return _wrap_shape_group(
        inner,
        node,
        ctx,
        top_level=top_level,
        extra_attrs=_metadata_group_attrs(effect_metadata),
    )


# ---------------------------------------------------------------------------
# Graphic frame fallback (<p:graphicFrame>)
# ---------------------------------------------------------------------------

def _convert_graphic_fallback(node: ShapeNode, ctx: AssemblyContext,
                              *, top_level: bool) -> str:
    """Render a <p:graphicFrame> by dispatching on its graphicData uri.

    Currently:
    - ``...drawingml/2006/table`` → real table renderer (`convert_tbl`)
    - ``...presentationml/2006/ole`` → render the ``mc:Fallback`` preview
      bitmap that PowerPoint bakes alongside every embedded OLE object.
      Visually identical to what PowerPoint shows for an unedited embed.
    - supported classic charts → baked preview plus native chart metadata.
    - everything else (SmartArt / diagram / unsupported chart) → labelled
      preview or bounding rectangle plus transparent unsupported metadata.
    """
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .slide_to_svg import (
        _SOURCE_PROXY_ATTRIBUTE,
        _graphic_preview_label,
        _object_metadata,
        _render_graphic_chart,
        _render_graphic_preview,
        _render_graphic_table,
        _roundtrip_graphic_frame_metadata,
        _wrap_shape_group,
        _xml_escape,
    )
    graphic_data = node.xml.find("a:graphic/a:graphicData", NS)
    uri = graphic_data.attrib.get("uri", "graphicFrame") if graphic_data is not None else "graphicFrame"
    if node.xfrm.w <= 0 or node.xfrm.h <= 0:
        ctx.diagnose(
            "graphic-degenerate-frame",
            "Source graphic has a missing or nonpositive frame: "
            + _object_metadata(node, ctx)["data-pptx-frame"],
            "retain recoverable content and the exact frame; no positive frame is invented",
        )

    if uri == "http://schemas.openxmlformats.org/drawingml/2006/table":
        rendered, replacement_attrs, payload_metadata = _render_graphic_table(
            node,
            ctx,
            graphic_data,
        )
        if rendered:
            inner = (
                f"{payload_metadata}\n{rendered}"
                if payload_metadata
                else rendered
            )
            roundtrip_metadata, roundtrip_attrs = (
                _roundtrip_graphic_frame_metadata(
                    node,
                    ctx,
                    inner,
                    top_level=top_level,
                )
            )
            if roundtrip_metadata:
                inner = f"{roundtrip_metadata}\n{inner}"
            return _wrap_shape_group(
                inner,
                node,
                ctx,
                top_level=top_level,
                extra_attrs=replacement_attrs + roundtrip_attrs,
            )

    preview_svg = ""
    if ctx.render_graphic_previews:
        try:
            preview_svg = _render_graphic_preview(node, ctx)
        except MediaResolutionError as exc:
            if ctx.strict:
                raise
            ctx.diagnose(
                "preview-omitted",
                str(exc),
                "omit the missing baked preview and retain the native, "
                "normalized, or placeholder fallback",
            )

    chart_replacement_attrs: list[str] = []
    chart_payload_metadata = ""
    if uri in {CHART_URI, CHARTEX_URI}:
        rendered, chart_replacement_attrs, chart_payload_metadata = (
            _render_graphic_chart(
                node,
                ctx,
                graphic_data,
                preview_svg,
            )
        )
        if rendered:
            inner = (
                f"{chart_payload_metadata}\n{rendered}"
                if chart_payload_metadata
                else rendered
            )
            return _wrap_shape_group(
                inner,
                node,
                ctx,
                top_level=top_level,
                extra_attrs=chart_replacement_attrs,
            )

    if uri == "http://schemas.openxmlformats.org/presentationml/2006/ole":
        if preview_svg:
            labelled = (
                preview_svg
                + "\n"
                + _graphic_preview_label(node, "ole preview")
            )
            return _wrap_shape_group(
                labelled, node, ctx, top_level=top_level,
                extra_attrs=[f'{_SOURCE_PROXY_ATTRIBUTE}="native-restore"'],
            )

    if preview_svg:
        labelled = (
            preview_svg
            + "\n"
            + _graphic_preview_label(
                node,
                f"{uri.rsplit('/', 1)[-1]} preview",
            )
        )
        return _wrap_shape_group(
            labelled, node, ctx, top_level=top_level,
            extra_attrs=[f'{_SOURCE_PROXY_ATTRIBUTE}="native-restore"'],
        )

    if ctx.render_graphic_previews:
        ctx.diagnose(
            "graphic-preview-unavailable",
            f"Source graphic {uri} has no recoverable visual preview",
            "retain native metadata when available and display the labelled source-frame placeholder",
        )
    label = uri.rsplit("/", 1)[-1]
    placeholder = (
        f'<rect x="{fmt_num(node.xfrm.x)}" y="{fmt_num(node.xfrm.y)}" '
        f'width="{fmt_num(node.xfrm.w)}" height="{fmt_num(node.xfrm.h)}" '
        f'fill="none" stroke="#999999" stroke-dasharray="4 4"/>'
        f'<text x="{fmt_num(node.xfrm.x + node.xfrm.w / 2)}" '
        f'y="{fmt_num(node.xfrm.y + node.xfrm.h / 2)}" '
        f'text-anchor="middle" font-family="Arial" font-size="14" fill="#999999">'
        f"[{_xml_escape(label)}]</text>"
    )
    if chart_payload_metadata:
        placeholder = f"{chart_payload_metadata}\n{placeholder}"
    return _wrap_shape_group(
        placeholder,
        node,
        ctx,
        top_level=top_level,
        extra_attrs=chart_replacement_attrs + (
            [] if 'data-pptx-replace-with="chart"' in chart_replacement_attrs
            else [f'{_SOURCE_PROXY_ATTRIBUTE}="native-restore"']
        ),
    )


def _graphic_preview_label(node: ShapeNode, label: str) -> str:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .slide_to_svg import (
        _xml_escape,
    )
    return (
        f'<rect x="{fmt_num(node.xfrm.x)}" y="{fmt_num(node.xfrm.y)}" '
        f'width="{fmt_num(node.xfrm.w)}" height="22" '
        f'fill="#FFFFFF" fill-opacity="0.82" stroke="#999999" stroke-width="0.5"/>'
        f'<text x="{fmt_num(node.xfrm.x + 6)}" y="{fmt_num(node.xfrm.y + 15)}" '
        f'font-family="Arial" font-size="11" fill="#666666">[{_xml_escape(label)}]</text>'
    )


def _replacement_payload_metadata(payload: object) -> str:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .slide_to_svg import (
        _xml_text_escape,
    )
    payload_json = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return (
        '<metadata type="application/json">'
        f'{_xml_text_escape(payload_json)}</metadata>'
    )


def _package_rels_path(part_name: str) -> str:
    parent, name = posixpath.split(part_name)
    return f"{parent}/_rels/{name}.rels" if parent else f"_rels/{name}.rels"


def _package_content_types(ctx: AssemblyContext) -> tuple[dict[str, str], dict[str, str]]:
    payload = ctx.pkg.read_part_bytes("[Content_Types].xml")
    if payload is None:
        raise RuntimeError("source package has no [Content_Types].xml")
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise RuntimeError("source package content types are malformed") from exc
    defaults = {
        item.attrib.get("Extension", "").lower(): item.attrib.get("ContentType", "")
        for item in root.findall(f"{{{NS['ct']}}}Default")
    }
    overrides = {
        item.attrib.get("PartName", "").lstrip("/"): item.attrib.get("ContentType", "")
        for item in root.findall(f"{{{NS['ct']}}}Override")
    }
    return defaults, overrides


