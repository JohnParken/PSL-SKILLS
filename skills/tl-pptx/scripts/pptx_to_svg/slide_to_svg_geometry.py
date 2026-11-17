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

def _txbody_metadata(
    tx_body: ET.Element,
    visible_text_svg: str,
) -> str:
    """Preserve the native text body while its visible SVG remains authoritative."""
    if has_relationship_attributes(tx_body):
        # Relationship ids are part-local and cannot be copied into a newly
        # generated slide without rebuilding the relationship target.
        return ""
    raw = ET.tostring(tx_body, encoding="utf-8")
    encoded = base64.b64encode(raw).decode("ascii")
    wrapper = ET.fromstring(
        f'<svg xmlns="http://www.w3.org/2000/svg">{visible_text_svg}</svg>'
    )
    digest = svg_text_fingerprint(wrapper)
    return (
        '<metadata data-pptx-part="txbody" data-pptx-encoding="base64" '
        f'data-pptx-text-sha256="{digest}">{encoded}</metadata>'
    )


def _placeholder_sp_pr_metadata(
    node: ShapeNode,
    ctx: AssemblyContext,
) -> str:
    """Preserve relationship-free local placeholder geometry for inheritance."""
    if (
        not ctx.preserve_placeholder_inheritance
        or node.placeholder is None
        or node.kind != SHAPE
    ):
        return ""
    sp_pr = node.xml.find("p:spPr", NS)
    if sp_pr is None or has_relationship_attributes(sp_pr):
        return ""
    raw = ET.tostring(sp_pr, encoding="utf-8")
    return (
        '<metadata data-pptx-part="placeholder-sppr" '
        'data-pptx-encoding="base64" '
        f'data-pptx-ooxml-sha256="{hashlib.sha256(raw).hexdigest()}">'
        f'{base64.b64encode(raw).decode("ascii")}</metadata>'
    )


def _resolve_geometry(node: ShapeNode, sp_pr: ET.Element | None) -> GeomResult | None:
    """Resolve a DrawingML shape geometry into an absolute SVG geometry model."""
    prst_geom = sp_pr.find("a:prstGeom", NS) if sp_pr is not None else None
    cust_geom = sp_pr.find("a:custGeom", NS) if sp_pr is not None else None
    prst = prst_geom.attrib.get("prst", "rect") if prst_geom is not None else None

    geom: GeomResult | None = None
    if prst_geom is not None:
        geom = convert_prst_geom(prst, node.xfrm, prst_geom)
    elif cust_geom is not None:
        d = convert_custom_geom(cust_geom, node.xfrm)
        if d:
            raw = ET.tostring(cust_geom, encoding="utf-8")
            geom = GeomResult(
                tag="path",
                path_d=d,
                attrs={
                    "data-pptx-part": "geometry",
                    "data-pptx-geometry-kind": "custom",
                    "data-pptx-custgeom": base64.b64encode(raw).decode("ascii"),
                    "data-pptx-geometry-sha256": hashlib.sha256(
                        d.strip().encode("utf-8")
                    ).hexdigest(),
                },
            )
    else:
        # No geometry hint at all — render bounding rect
        geom = convert_prst_geom("rect", node.xfrm, None)

    if geom is None:
        return None
    # An evaluated path can retain a collapsed axis without inventing a frame.
    # Mirror/native validation still decides whether that source is exportable.
    permits_degenerate_axis = (
        node.kind == CONNECTOR
        or prst in CONNECTOR_PRESET_TYPES
        or (node.xfrm.w >= 0 and node.xfrm.h >= 0
            and (node.xfrm.w > 0 or node.xfrm.h > 0))
    )
    if (
        not permits_degenerate_axis
        and (node.xfrm.w <= 0 or node.xfrm.h <= 0)
    ):
        return None
    return geom


def _build_geometry_xml(node: ShapeNode, sp_pr: ET.Element | None,
                        ctx: AssemblyContext,
                        geom: GeomResult | None = None) -> str:
    """Build the SVG geometry element with fill/stroke/effect attributes."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .slide_to_svg import (
        _attrs_to_xml,
        _diagnose_unsupported_effect,
        _effective_shape_stroke_properties,
        _geom_to_svg,
        _object_metadata,
        _preset_layers_to_svg,
        _resolve_geometry,
        _resolve_shape_style_defaults,
    )
    if geom is None:
        geom = _resolve_geometry(node, sp_pr)
    if geom is None:
        if sp_pr is not None and any(
            sp_pr.find(path, NS) is not None
            for path in ("a:prstGeom", "a:custGeom")
        ):
            ctx.diagnose(
                "geometry-omitted",
                "Source geometry could not be recovered at frame "
                + _object_metadata(node, ctx)["data-pptx-frame"],
                "retain any text/image content; no replacement geometry is invented",
            )
        return ""
    # Resolve style defaults early so markers can adopt the theme stroke color
    # when <a:ln> doesn't carry an explicit solidFill.
    try:
        style_defaults = _resolve_shape_style_defaults(node, ctx)
    except ValueError as exc:
        if ctx.strict:
            raise
        ctx.diagnose(
            "shape-style-omitted",
            str(exc),
            "omit unresolved theme style defaults",
        )
        style_defaults = {}

    # Fill / stroke / effect
    try:
        fill = resolve_fill(
            sp_pr,
            ctx.palette,
            id_prefix="g",
            id_seq=ctx.grad_seq,
            group_fill=ctx.group_fills[-1] if ctx.group_fills else None,
        )
    except ValueError as exc:
        if ctx.strict:
            raise
        ctx.diagnose(
            "fill-omitted",
            str(exc),
            "omit only the unsupported fill",
        )
        fill = FillResult.none_fill()
    try:
        stroke = resolve_stroke(
            _effective_shape_stroke_properties(node, ctx, sp_pr),
            ctx.palette,
            id_prefix="m",
            id_seq=ctx.marker_seq,
            style_stroke_default=style_defaults.get("stroke"),
            gradient_frame=(
                node.xfrm.x,
                node.xfrm.y,
                node.xfrm.w,
                node.xfrm.h,
            ),
        )
    except ValueError as exc:
        if ctx.strict:
            raise
        ctx.diagnose(
            "stroke-omitted",
            str(exc),
            "omit only the unsupported outline",
        )
        stroke = StrokeResult(attrs={"stroke": "none"})
    try:
        effect = convert_effects(
            sp_pr,
            ctx.palette,
            id_prefix="fx",
            id_seq=ctx.filter_seq,
            target_rotation_degrees=node.effective_rotation,
        )
    except ValueError as exc:
        if ctx.strict:
            raise
        ctx.diagnose(
            "effect-omitted",
            str(exc),
            "omit only the unsupported visual effect",
        )
        effect = EffectResult()

    ctx.defs.extend(fill.defs)
    ctx.defs.extend(stroke.defs)
    ctx.defs.extend(effect.defs)
    effect_attrs = dict(effect.metadata)
    effect_reason = effect_attrs.get(EFFECT_REASON_ATTR)
    existing_reason = geom.attrs.get(EFFECT_REASON_ATTR)
    if effect_reason is not None and existing_reason is not None:
        effect_attrs.update(unsupported_effect_metadata(
            existing_reason,
            effect_reason,
        ))
    geom.attrs.update(effect_attrs)
    _diagnose_unsupported_effect(ctx, geom.attrs)

    attrs = {**fill.attrs, **stroke.attrs}
    for key, value in style_defaults.items():
        attrs.setdefault(key, value)
    if effect.filter_id is not None:
        attrs["filter"] = f"url(#{effect.filter_id})"

    # Default fill / stroke when not specified by spPr (matches PowerPoint
    # behavior: a:noFill on shape-level fill if there's a txBody, else any
    # explicit fill present in spPr should already have been captured).
    if "fill" not in attrs:
        attrs["fill"] = "none"
    if "stroke" not in attrs:
        # Spec default for shapes is no stroke unless ln says otherwise.
        # Skip emitting stroke="none" to keep markup tight.
        pass

    if (
        (node.xfrm.w <= 0 or node.xfrm.h <= 0)
        and not (
            (node.kind == CONNECTOR or geom.attrs.get("data-pptx-prst") in CONNECTOR_PRESET_TYPES)
            and node.xfrm.w >= 0 and node.xfrm.h >= 0
            and (node.xfrm.w > 0 or node.xfrm.h > 0)
        )
    ):
        # Restoring evaluated zero-axis paths must not resurrect empty,
        # explicitly unpainted text-box outlines as exportable source content.
        if (
            node.kind == SHAPE
            and attrs.get("fill", "none") == "none"
            and attrs.get("stroke", "none") == "none"
            and not any(key in attrs for key in ("filter", "marker-start", "marker-end"))
        ):
            return ""
        ctx.diagnose(
            "geometry-degenerate-frame",
            "Source geometry has a missing or degenerate frame: "
            + _object_metadata(node, ctx)["data-pptx-frame"],
            "retain evaluated geometry and the exact source frame",
        )

    semantic_attrs = {
        **geom.attrs,
        **_object_metadata(node, ctx),
    }
    shape_style = node.xml.find("p:style", NS)
    if shape_style is not None:
        semantic_attrs["data-pptx-shape-style"] = base64.b64encode(
            ET.tostring(shape_style, encoding="utf-8")
        ).decode("ascii")
    if geom.layers:
        return _preset_layers_to_svg(geom, semantic_attrs, attrs)
    return _geom_to_svg(
        geom,
        _attrs_to_xml({**semantic_attrs, **attrs}),
    )


def _resolve_shape_style_defaults(node: ShapeNode, ctx: AssemblyContext) -> dict[str, str]:
    """Resolve minimal p:style defaults used when spPr omits explicit style.

    Full theme style matrix reproduction is intentionally out of scope here;
    this only prevents common theme-styled placeholders/shapes from becoming
    transparent or unstroked when their visible color lives in p:style.
    """
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .slide_to_svg import (
        _resolve_ref_color,
    )
    style = node.xml.find("p:style", NS)
    if style is None:
        return {}

    defaults: dict[str, str] = {}

    fill_ref = style.find("a:fillRef", NS)
    if fill_ref is not None and fill_ref.attrib.get("idx", "").strip() != "0":
        fill_color = _resolve_ref_color(fill_ref, ctx)
        if fill_color:
            defaults["fill"] = fill_color

    return defaults


def _effective_shape_stroke_properties(
    node: ShapeNode,
    ctx: AssemblyContext,
    sp_pr: ET.Element | None,
) -> ET.Element | None:
    """Resolve the referenced theme line before applying local line overrides."""
    reference = node.xml.find("p:style/a:lnRef", NS)
    if reference is None or reference.get("idx") == "0":
        return sp_pr
    try:
        index = int(reference.get("idx", ""))
    except ValueError:
        raise ValueError("Invalid theme line style index") from None
    styles = (
        ctx.theme.xml.findall("a:themeElements/a:fmtScheme/a:lnStyleLst/a:ln", NS)
        if ctx.theme is not None else []
    )
    if not 1 <= index <= len(styles):
        ctx.diagnose(
            "theme-line-reference-omitted",
            f"Theme line style {index} cannot be resolved",
            "retain the local outline without inventing a theme stroke",
        )
        return sp_pr

    line = copy.deepcopy(styles[index - 1])
    reference_color = find_color_elem(reference)
    if reference_color is not None:
        for owner in line.iter():
            for child in list(owner):
                if child.tag == f"{{{NS['a']}}}schemeClr" and child.get("val") == "phClr":
                    color = copy.deepcopy(reference_color)
                    color.extend(copy.deepcopy(list(child)))
                    owner[list(owner).index(child)] = color

    local_line = sp_pr.find("a:ln", NS) if sp_pr is not None else None
    if local_line is not None:
        line.attrib.update(local_line.attrib)
        exclusive_children = (
            {"noFill", "solidFill", "gradFill", "pattFill", "blipFill", "grpFill"},
            {"prstDash", "custDash"},
            {"round", "bevel", "miter"},
        )
        for local in local_line:
            name = local.tag.rsplit("}", 1)[-1]
            family = next((names for names in exclusive_children if name in names), {name})
            for inherited in list(line):
                if inherited.tag.rsplit("}", 1)[-1] in family:
                    line.remove(inherited)
            line.append(copy.deepcopy(local))
    properties = ET.Element(f"{{{NS['p']}}}spPr")
    properties.append(line)
    return properties


def _resolve_text_style_default(node: ShapeNode, ctx: AssemblyContext) -> str:
    """Resolve p:style fontRef color used by runs without explicit fill."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .slide_to_svg import (
        _resolve_ref_color,
    )
    style = node.xml.find("p:style", NS)
    if style is None:
        return "#000000"
    font_ref = style.find("a:fontRef", NS)
    font_color = _resolve_ref_color(font_ref, ctx)
    return font_color or "#000000"


def _resolve_ref_color(ref_elem: ET.Element | None, ctx: AssemblyContext) -> str | None:
    color_elem = find_color_elem(ref_elem)
    hex_, _alpha = resolve_color(color_elem, ctx.palette)
    return hex_


def _geom_to_svg(geom: GeomResult, attrs_xml: str | None = None) -> str:
    """Serialize a resolved geometry with optional SVG attributes."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .slide_to_svg import (
        _attrs_to_xml,
    )
    if attrs_xml is None:
        attrs_xml = _attrs_to_xml(geom.attrs)
    if geom.tag == "path":
        return f'<path d="{geom.path_d}"{attrs_xml}/>'
    if geom.tag in ("polygon", "polyline"):
        return f'<{geom.tag} points="{geom.points}"{attrs_xml}/>'
    return f"<{geom.tag}{attrs_xml}/>"


def _preset_layers_to_svg(
    geom: GeomResult,
    semantic_attrs: dict[str, str],
    style_attrs: dict[str, str],
) -> str:
    """Serialize one semantic carrier plus every visible preset path layer.

    DrawingML applies shape-level fill/line first, then each preset path can
    override whether and how that paint is used.  A hidden carrier retains the
    unmodified shape-level style for native round-trip; visible detail paths
    reproduce the preset's independent paint behavior without being exported
    as duplicate PowerPoint shapes.
    """
    markup = serialize_preset_layers(
        geom.layers,
        semantic_attrs,
        style_attrs,
    )
    geom.attrs["data-pptx-preview-sha256"] = markup.preview_hash
    semantic_attrs["data-pptx-preview-sha256"] = markup.preview_hash
    return markup.markup


