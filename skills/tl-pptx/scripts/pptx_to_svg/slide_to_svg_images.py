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

def _clip_blip_image(image_xml: str, geom: GeomResult | None,
                     ctx: AssemblyContext) -> str:
    """Clip image fills to the owning shape geometry when it is not a plain rect."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .slide_to_svg import (
        _flatten_vector_custom_crop,
        _geom_to_svg,
        _inject_clip_path,
        _project_clip_into_nested_crop,
    )
    if geom is None or geom.tag == "line":
        return image_xml
    if geom.attrs.get("data-pptx-prst") == "rect":
        return image_xml
    if geom.tag == "rect" and not geom.attrs.get("rx") and not geom.attrs.get("ry"):
        return image_xml

    clip_geom = geom
    if image_xml.startswith("<svg"):
        flattened = None
        if (
            not ctx.strict
            and geom.attrs.get("data-pptx-geometry-kind") == "custom"
        ):
            flattened = _flatten_vector_custom_crop(image_xml)
        if flattened is not None:
            image_xml = flattened
            ctx.diagnose(
                "vector-custom-geometry-crop-normalized",
                "SVG-only picture combines srcRect with custom geometry; "
                "Office renders the complete vector as the custom-shape fill",
                "ignore srcRect and clip the complete vector to the custom geometry",
            )
        else:
            try:
                clip_geom = _project_clip_into_nested_crop(geom, image_xml)
            except ValueError as exc:
                if ctx.strict:
                    raise ValueError(
                        f"Cannot project picture geometry into its crop viewBox: {exc}"
                    ) from exc
                ctx.diagnose(
                    "nested-crop-shape-clip-omitted",
                    f"Cannot project picture geometry into its crop viewBox: {exc}",
                    "source image crop retained without the additional shape clip",
                )
                return image_xml

    ctx.clip_seq[0] += 1
    clip_id = f"{ctx.group_id_prefix}clip{ctx.clip_seq[0]}"
    clip_shape = _geom_to_svg(clip_geom, "")
    ctx.defs.append(
        f'<clipPath id="{clip_id}" clipPathUnits="userSpaceOnUse">'
        f'{clip_shape}</clipPath>'
    )
    return _inject_clip_path(image_xml, clip_id)


def _flatten_vector_custom_crop(image_xml: str) -> str | None:
    """Normalize an SVG-only custom-shape crop to a plain full-vector image."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .slide_to_svg import (
        _attrs_to_xml,
    )
    wrapper = ET.fromstring(image_xml)
    if wrapper.tag != "svg" or len(wrapper) != 1 or wrapper[0].tag != "image":
        return None
    image = wrapper[0]
    href = image.attrib.get("href", "")
    href_path = href.split("#", 1)[0].split("?", 1)[0].casefold()
    if not (
        href_path.endswith(".svg")
        or href_path.startswith("data:image/svg+xml")
    ):
        return None

    attrs = {
        key: value
        for key, value in image.attrib.items()
        if key not in {"x", "y", "width", "height", "preserveAspectRatio"}
    }
    for key in ("x", "y", "width", "height"):
        value = wrapper.attrib.get(key)
        if value is None:
            return None
        attrs[key] = value
    attrs["preserveAspectRatio"] = "none"
    return f"<image{_attrs_to_xml(attrs)}/>"


def _inject_clip_path(image_xml: str, clip_id: str) -> str:
    clip_attr = f' clip-path="url(#{clip_id})"'
    if image_xml.startswith("<image"):
        return image_xml.replace("<image", f"<image{clip_attr}", 1)
    if image_xml.startswith("<svg"):
        marked = image_xml.replace("<svg", '<svg data-pptx-crop="1"', 1)
        return marked.replace("<image", f"<image{clip_attr}", 1)
    return image_xml


def _project_clip_into_nested_crop(
    geom: GeomResult,
    image_xml: str,
) -> GeomResult:
    """Map absolute slide geometry into one nested crop's viewBox space."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .slide_to_svg import (
        _serialize_clip_path,
    )
    wrapper = ET.fromstring(image_xml)
    if wrapper.tag != "svg":
        raise ValueError("expected a nested <svg> crop wrapper")

    try:
        frame_x = float(wrapper.attrib["x"])
        frame_y = float(wrapper.attrib["y"])
        frame_w = float(wrapper.attrib["width"])
        frame_h = float(wrapper.attrib["height"])
        view_box = [float(token) for token in wrapper.attrib["viewBox"].split()]
    except (KeyError, ValueError) as exc:
        raise ValueError("crop wrapper has incomplete numeric geometry") from exc
    if frame_w <= 0 or frame_h <= 0 or len(view_box) != 4:
        raise ValueError(
            "crop wrapper requires positive dimensions and four viewBox values"
        )
    vb_x, vb_y, vb_w, vb_h = view_box
    if vb_w <= 0 or vb_h <= 0:
        raise ValueError("crop viewBox dimensions must be positive")

    scale_x = vb_w / frame_w
    scale_y = vb_h / frame_h
    matrix = (
        scale_x,
        0.0,
        0.0,
        scale_y,
        vb_x - frame_x * scale_x,
        vb_y - frame_y * scale_y,
    )

    if geom.tag == "path" and geom.path_d:
        commands = normalize_path_commands(
            svg_path_to_absolute(parse_svg_path(geom.path_d))
        )
        transformed = transform_path_commands(commands, matrix)
        return GeomResult(
            tag="path",
            path_d=_serialize_clip_path(transformed),
        )
    if geom.tag in {"polygon", "polyline"} and geom.points:
        points = parse_svg_points(
            geom.points,
            min_points=3 if geom.tag == "polygon" else 2,
        )
        transformed = [
            (
                x * scale_x + matrix[4],
                y * scale_y + matrix[5],
            )
            for x, y in points
        ]
        return GeomResult(
            tag=geom.tag,
            points=" ".join(
                f"{fmt_num(x, 5)},{fmt_num(y, 5)}"
                for x, y in transformed
            ),
        )
    raise ValueError(f"unsupported clip geometry <{geom.tag}>")


def _serialize_clip_path(commands: list[PathCommand]) -> str:
    """Serialize normalized M/L/C/Z commands with crop-safe precision."""
    parts: list[str] = []
    for command in commands:
        parts.append(command.cmd)
        if command.args:
            parts.append(" ".join(fmt_num(value, 5) for value in command.args))
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Picture (<p:pic>)
# ---------------------------------------------------------------------------

def _convert_picture(node: ShapeNode, ctx: AssemblyContext, *, top_level: bool) -> str:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .slide_to_svg import (
        _EXTERNAL_LINKED_IMAGE_PROXY_ATTRIBUTE,
        _SOURCE_PROXY_ATTRIBUTE,
        _SOURCE_PROXY_KIND,
        _clip_blip_image,
        _diagnose_picture_result,
        _diagnose_unsupported_effect,
        _fallback_node_svg,
        _inject_root_svg_attrs,
        _metadata_group_attrs,
        _object_metadata,
        _resolve_geometry,
        _wrap_shape_group,
    )
    sp_pr = node.xml.find("p:spPr", NS)
    geom = _resolve_geometry(node, sp_pr)
    try:
        result = convert_picture(
            node.xml, node.xfrm, ctx.slide_part, ctx.pkg,
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
            "retain the complete source picture as a non-editable proxy",
        )
        return _fallback_node_svg(
            node,
            ctx,
            top_level=top_level,
            source_proxy=True,
        )
    except MediaResolutionError as exc:
        if ctx.strict:
            raise
        ctx.diagnose(
            "object-replaced",
            str(exc),
            "replace only this picture with a visible placeholder",
        )
        return _fallback_node_svg(node, ctx, top_level=top_level)
    _diagnose_picture_result(ctx, result)
    if not result.svg:
        return ""
    ctx.media.update(result.media)
    effect = convert_effects(
        sp_pr,
        ctx.palette,
        id_prefix="fx",
        id_seq=ctx.filter_seq,
        target_rotation_degrees=node.effective_rotation,
    )
    ctx.defs.extend(effect.defs)
    effect_metadata = dict(effect.metadata)
    _diagnose_unsupported_effect(ctx, effect_metadata)
    clipped_svg = _clip_blip_image(result.svg, geom, ctx)
    picture_attrs = {**_object_metadata(node, ctx), **effect_metadata}
    group_attrs = _metadata_group_attrs(effect_metadata)
    if result.external_linked:
        group_attrs.append(
            f'{_SOURCE_PROXY_ATTRIBUTE}="{_SOURCE_PROXY_KIND}"'
        )
        group_attrs.append(
            f'{_EXTERNAL_LINKED_IMAGE_PROXY_ATTRIBUTE}="true"'
        )
    if effect.filter_id is not None:
        filter_attr = f"url(#{effect.filter_id})"
        if (
            clipped_svg.startswith("<svg")
            or clipped_svg.startswith("<image clip-path=")
        ):
            # Keep the effect outside the crop viewport so shadows and glows
            # remain visible beyond the picture geometry in SVG previews.
            group_attrs.append(f'filter="{filter_attr}"')
        else:
            picture_attrs["filter"] = filter_attr
    picture_svg = _inject_root_svg_attrs(
        clipped_svg,
        picture_attrs,
    )
    return _wrap_shape_group(
        picture_svg,
        node,
        ctx,
        top_level=top_level,
        extra_attrs=group_attrs,
    )


def _inject_root_svg_attrs(markup: str, attrs: dict[str, str]) -> str:
    """Attach source-object identity to a picture's root SVG element."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .slide_to_svg import (
        _attrs_to_xml,
    )
    attrs_xml = _attrs_to_xml(attrs)
    for tag in ("image", "svg"):
        prefix = f"<{tag}"
        if markup.startswith(prefix):
            return markup.replace(prefix, f"{prefix}{attrs_xml}", 1)
    return markup


# ---------------------------------------------------------------------------
# Connector (<p:cxnSp>)
# ---------------------------------------------------------------------------

def _convert_connector(node: ShapeNode, ctx: AssemblyContext, *, top_level: bool) -> str:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .slide_to_svg import (
        _build_geometry_xml,
        _geometry_group_attrs,
        _resolve_geometry,
        _wrap_shape_group,
    )
    sp_pr = node.xml.find("p:spPr", NS)
    geom = _resolve_geometry(node, sp_pr)
    geom_xml = _build_geometry_xml(node, sp_pr, ctx, geom=geom)
    return _wrap_shape_group(
        geom_xml,
        node,
        ctx,
        top_level=top_level,
        extra_attrs=_geometry_group_attrs(geom),
    )


# ---------------------------------------------------------------------------
# Group (<p:grpSp>)
# ---------------------------------------------------------------------------

