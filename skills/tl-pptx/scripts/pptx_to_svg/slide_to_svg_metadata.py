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

def _wrap_shape_group(
    inner: str,
    node: ShapeNode,
    ctx: AssemblyContext,
    *,
    top_level: bool,
    extra_attrs: list[str] | None = None,
) -> str:
    """Wrap a shape's body in a <g> that carries the transform (rotation /
    flip) and an id for animation anchoring."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .slide_to_svg import (
        _object_metadata,
        _resolve_svg_hyperlink,
        _xml_escape,
    )
    if not inner.strip():
        return ""

    transform = node.xfrm.to_svg_transform()
    ctx.shape_seq[0] += 1
    seq = ctx.shape_seq[0]
    sid = node.spid or str(seq)
    g_id = f"{ctx.group_id_prefix}shape-{sid}"

    attrs: list[str] = [f'id="{g_id}"']
    attrs.extend(
        f'{key}="{_xml_escape(value)}"'
        for key, value in _object_metadata(
            node,
            ctx,
            fallback_shape_id=sid,
        ).items()
    )
    if node.name:
        attrs.append(f'data-name="{_xml_escape(node.name)}"')
    if node.placeholder is not None and node.kind == SHAPE:
        sp_pr = node.xml.find("p:spPr", NS)
        if sp_pr is not None and any(
            sp_pr.find(path, NS) is not None
            for path in ("a:prstGeom", "a:custGeom")
        ):
            attrs.append('data-pptx-placeholder-local-geometry="true"')
    if extra_attrs:
        attrs.extend(extra_attrs)
        if any(
            attribute.split("=", 1)[0] == "data-pptx-replace-with"
            for attribute in extra_attrs
        ) and not any(
            attribute.split("=", 1)[0] == NATIVE_FALLBACK_SHA256_ATTR
            for attribute in extra_attrs
        ):
            fallback_hash = svg_native_fallback_markup_fingerprint(
                inner,
                root_transform=transform,
                external_markup="".join(ctx.defs),
            )
            attrs.append(
                f'{NATIVE_FALLBACK_SHA256_ATTR}="{fallback_hash}"'
            )
    if transform:
        attrs.append(f'transform="{transform}"')
    group_xml = f"<g {' '.join(attrs)}>\n{inner}\n</g>"
    if node.hyperlink_rid or node.hyperlink_action:
        href = _resolve_svg_hyperlink(
            ctx,
            node.hyperlink_rid,
            node.hyperlink_action,
        )
        if href is not None and '<a href=' in inner:
            attrs.append(
                f'{SHAPE_HYPERLINK_ATTR}="{_xml_escape(href)}"'
            )
            if href.startswith('#slide-'):
                attrs.append(f'{SOURCE_HREF_ATTR}="{_xml_escape(href)}"')
            return f"<g {' '.join(attrs)}>\n{inner}\n</g>"
        if href is not None:
            provenance = f' {SOURCE_HREF_ATTR}="{_xml_escape(href)}"' if href.startswith('#slide-') else ''
            return f'<a href="{_xml_escape(href)}"{provenance}>{group_xml}</a>'
    return group_xml


def _attrs_to_xml(attrs: dict[str, str]) -> str:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .slide_to_svg import (
        _xml_escape,
    )
    if not attrs:
        return ""
    return "".join(f' {key}="{_xml_escape(value)}"' for key, value in attrs.items())


def _metadata_group_attrs(attrs: dict[str, str]) -> list[str]:
    """Serialize import metadata for a logical object wrapper."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .slide_to_svg import (
        _xml_escape,
    )
    return [
        f'{key}="{_xml_escape(value)}"'
        for key, value in attrs.items()
    ]


def _diagnose_unsupported_effect(
    ctx: AssemblyContext,
    metadata: dict[str, str],
) -> None:
    """Copy an import-only blocking effect marker into the conversion report."""
    reason = metadata.get(EFFECT_REASON_ATTR)
    if reason is None:
        return
    ctx.diagnose(
        "effect-unsupported",
        reason,
        "retain the base object and record blocking effect metadata",
    )


def _geometry_group_attrs(geom: GeomResult | None) -> list[str]:
    """Mirror native geometry semantics onto the logical shape container."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .slide_to_svg import (
        _xml_escape,
    )
    if geom is None:
        return []
    keys = (
        "data-pptx-prst",
        "data-pptx-geometry-kind",
        "data-pptx-geometry-sha256",
        "data-pptx-preview-sha256",
        "data-pptx-geometry-status",
        "data-pptx-geometry-reason",
        EFFECT_STATUS_ATTR,
        EFFECT_REASON_ATTR,
        NATIVE_EFFECT_ATTR,
        NATIVE_EFFECT_SHA256_ATTR,
    )
    attrs: list[str] = []
    for key, value in geom.attrs.items():
        if key in keys or key.startswith("data-pptx-av-"):
            attrs.append(f'{key}="{_xml_escape(value)}"')
    return attrs


def _object_metadata(
    node: ShapeNode,
    ctx: AssemblyContext,
    *,
    fallback_shape_id: str = "",
) -> dict[str, str]:
    """Describe the source object without coupling geometry to its SVG bounds."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .slide_to_svg import (
        _connector_metadata,
        _shape_scope,
    )
    object_kind = {
        SHAPE: "shape",
        PICTURE: "picture",
        CONNECTOR: "connector",
        GROUP: "group",
        GRAPHIC: "graphic-frame",
    }.get(node.kind, node.kind)
    shape_id = node.spid or fallback_shape_id
    frame = " ".join((
        fmt_num(node.xfrm.x, 8),
        fmt_num(node.xfrm.y, 8),
        fmt_num(node.xfrm.w, 8),
        fmt_num(node.xfrm.h, 8),
    ))
    attrs = {
        "data-pptx-object": object_kind,
        "data-pptx-shape-id": shape_id,
        "data-pptx-shape-scope": _shape_scope(ctx),
        "data-pptx-frame": frame,
    }
    if node.name:
        attrs["data-pptx-shape-name"] = node.name
    if node.placeholder is not None:
        if node.placeholder.type:
            attrs["data-ph-type"] = node.placeholder.type
        if node.placeholder.idx is not None:
            attrs["data-pptx-placeholder-index"] = node.placeholder.idx
        if node.placeholder.sz is not None:
            attrs["data-pptx-placeholder-size"] = node.placeholder.sz
        if node.placeholder.orient is not None:
            attrs["data-pptx-placeholder-orientation"] = node.placeholder.orient
    if node.kind == CONNECTOR:
        attrs.update(_connector_metadata(node, _shape_scope(ctx)))
    return attrs


def _shape_scope(ctx: AssemblyContext) -> str:
    if ctx.group_id_prefix.startswith("master-"):
        return "master"
    if ctx.group_id_prefix.startswith("layout-"):
        return "layout"
    return "slide"


def _connector_metadata(node: ShapeNode, scope: str) -> dict[str, str]:
    """Preserve connector endpoint references when PowerPoint declares them."""
    attrs: dict[str, str] = {}
    cnv = node.xml.find("p:nvCxnSpPr/p:cNvCxnSpPr", NS)
    if cnv is None:
        return attrs

    for endpoint, prefix in (("stCxn", "start"), ("endCxn", "end")):
        connection = cnv.find(f"a:{endpoint}", NS)
        if connection is None:
            continue
        shape_id = connection.attrib.get("id")
        site = connection.attrib.get("idx")
        if shape_id is not None:
            attrs[f"data-pptx-{prefix}-shape-id"] = shape_id
            attrs[f"data-pptx-{prefix}-shape-scope"] = scope
        if site is not None:
            attrs[f"data-pptx-{prefix}-site"] = site
    return attrs


def _xml_escape(text: str) -> str:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .slide_to_svg import (
        _xml_text_escape,
    )
    return _xml_text_escape(text).replace('"', "&quot;")


def _xml_text_escape(text: str) -> str:
    return (text.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;"))
