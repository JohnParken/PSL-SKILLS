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

def _effective_placeholder_tx_body(
    tx_body: ET.Element | None,
    inherited_body_properties: tuple[ET.Element, ...],
) -> ET.Element | None:
    """Merge inherited placeholder bodyPr settings into one visible text body."""
    if tx_body is None or not inherited_body_properties:
        return tx_body
    effective = copy.deepcopy(tx_body)
    body_pr = effective.find("a:bodyPr", NS)
    if body_pr is None:
        body_pr = ET.Element(f"{{{NS['a']}}}bodyPr")
        effective.insert(0, body_pr)

    child_groups = (
        {"prstTxWarp"},
        {"noAutofit", "normAutofit", "spAutoFit"},
        {"scene3d"},
        {"sp3d"},
    )
    for inherited in inherited_body_properties:
        for name, value in inherited.attrib.items():
            body_pr.attrib.setdefault(name, value)
        local_names = {
            child.tag.rsplit("}", 1)[-1]
            for child in body_pr
            if isinstance(child.tag, str)
        }
        for group in child_groups:
            if local_names & group:
                continue
            inherited_child = next(
                (
                    child
                    for child in inherited
                    if isinstance(child.tag, str)
                    and child.tag.rsplit("}", 1)[-1] in group
                ),
                None,
            )
            if inherited_child is not None:
                body_pr.append(copy.deepcopy(inherited_child))
                local_names.add(inherited_child.tag.rsplit("}", 1)[-1])
    return effective


def _materialize_inherited_list_styles(
    tx_body: ET.Element | None,
    inherited_lst_styles: tuple[ET.Element, ...],
) -> tuple[ET.Element | None, bool]:
    """Flatten placeholder list-style inheritance into the preserved txBody."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .slide_to_svg import (
        _merge_text_property_element,
    )
    if not inherited_lst_styles:
        return tx_body, True
    if tx_body is None:
        return None, False

    effective = copy.deepcopy(tx_body)
    lst_style = effective.find("a:lstStyle", NS)
    if lst_style is None:
        lst_style = ET.Element(f"{{{NS['a']}}}lstStyle")
        body_pr = effective.find("a:bodyPr", NS)
        insert_at = list(effective).index(body_pr) + 1 if body_pr is not None else 0
        effective.insert(insert_at, lst_style)

    for level in range(1, 10):
        local_level = lst_style.find(f"a:lvl{level}pPr", NS)
        inherited_levels = [
            level_pr
            for inherited in inherited_lst_styles
            if (level_pr := inherited.find(f"a:lvl{level}pPr", NS)) is not None
        ]
        if local_level is None and not inherited_levels:
            continue

        merged = ET.Element(f"{{{NS['a']}}}lvl{level}pPr")
        for source in reversed(inherited_levels):
            _merge_text_property_element(merged, source)
        if local_level is not None:
            _merge_text_property_element(merged, local_level)

        if local_level is None:
            lst_style.append(merged)
        else:
            index = list(lst_style).index(local_level)
            lst_style.remove(local_level)
            lst_style.insert(index, merged)

    return effective, True


def _merge_text_property_element(
    target: ET.Element,
    source: ET.Element,
) -> None:
    """Overlay one DrawingML paragraph/run property node by choice group."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .slide_to_svg import (
        _merge_text_property_element,
        _text_property_child_key,
    )
    target.attrib.update(source.attrib)
    for source_child in source:
        key = _text_property_child_key(source_child)
        target_child = next(
            (
                child
                for child in target
                if _text_property_child_key(child) == key
            ),
            None,
        )
        if (
            source_child.tag == f"{{{NS['a']}}}defRPr"
            and target_child is not None
        ):
            _merge_text_property_element(target_child, source_child)
            continue
        if target_child is not None:
            index = list(target).index(target_child)
            target.remove(target_child)
            target.insert(index, copy.deepcopy(source_child))
        else:
            target.append(copy.deepcopy(source_child))


def _text_property_child_key(child: ET.Element) -> str:
    """Return the OOXML choice-group key for one text-property child."""
    name = child.tag.rsplit("}", 1)[-1]
    groups = (
        ("fill", {
            "noFill", "solidFill", "gradFill", "blipFill", "pattFill", "grpFill",
        }),
        ("effect", {"effectLst", "effectDag"}),
        ("bullet-color", {"buClrTx", "buClr"}),
        ("bullet-size", {"buSzTx", "buSzPct", "buSzPts"}),
        ("bullet-font", {"buFontTx", "buFont"}),
        ("bullet-kind", {"buNone", "buAutoNum", "buChar", "buBlip"}),
        ("underline-line", {"uLnTx", "uLn"}),
        ("underline-fill", {"uFillTx", "uFill"}),
    )
    for key, names in groups:
        if name in names:
            return key
    return name


def _counter_reflected_text_svg(text_svg: str, xfrm: Xfrm) -> str:
    """Keep text upright when DrawingML flips its owning shape geometry."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .slide_to_svg import (
        _xml_escape,
    )
    if not text_svg or xfrm.rot or not (xfrm.flip_h or xfrm.flip_v):
        return text_svg
    transform = xfrm.to_svg_transform()
    if not transform:
        return text_svg
    return (
        '<g data-pptx-text-flip-compensation="true" '
        f'transform="{_xml_escape(transform)}">\n{text_svg}\n</g>'
    )


def _block_formula_zone(tx_body: ET.Element | None) -> ET.Element | None:
    """Return the sole block-math zone from a canonical formula text body."""
    if tx_body is None:
        return None
    paragraphs = tx_body.findall("a:p", NS)
    if len(paragraphs) != 1:
        return None
    paragraph = paragraphs[0]
    formula_zones = [
        child
        for child in paragraph
        if child.tag == f"{{{A14_NS}}}m"
    ]
    allowed = {
        f"{{{NS['a']}}}pPr",
        f"{{{NS['a']}}}endParaRPr",
        f"{{{A14_NS}}}m",
    }
    if len(formula_zones) != 1 or any(
        child.tag not in allowed
        for child in paragraph
        if isinstance(child.tag, str)
    ):
        return None
    root_children = [
        child for child in formula_zones[0] if isinstance(child.tag, str)
    ]
    if (
        len(root_children) != 1
        or root_children[0].tag
        != "{http://schemas.openxmlformats.org/officeDocument/2006/math}oMathPara"
    ):
        return None
    return formula_zones[0]


def _block_formula_carrier_error(
    node: ShapeNode,
    *,
    top_level: bool,
) -> str | None:
    """Reject block carriers whose non-formula state would be discarded."""
    if not top_level:
        return "grouped block formula carrier is not reversible"
    if (
        node.xfrm.rot
        or node.xfrm.flip_h
        or node.xfrm.flip_v
        or node.effective_rotation
    ):
        return "block formula carrier rotation or flip is not reversible"
    if node.placeholder is not None:
        return "block formula carrier cannot retain placeholder ownership"
    if node.hyperlink_rid or node.hyperlink_action:
        return "block formula carrier hyperlink is not reversible"
    if node.xml.find("p:style", NS) is not None:
        return "block formula carrier style reference is not reversible"

    sp_pr = node.xml.find("p:spPr", NS)
    if sp_pr is None:
        return "block formula carrier is missing p:spPr"
    preset = sp_pr.find("a:prstGeom", NS)
    if preset is None or preset.get("prst") != "rect":
        return "block formula carrier must use rectangular geometry"
    if any(
        sp_pr.find(path, NS) is not None
        for path in (
            "a:solidFill",
            "a:gradFill",
            "a:pattFill",
            "a:blipFill",
            "a:grpFill",
            "a:effectLst",
            "a:effectDag",
            "a:scene3d",
            "a:sp3d",
        )
    ):
        return "block formula carrier paint or effect is not reversible"
    line = sp_pr.find("a:ln", NS)
    if line is not None and line.find("a:noFill", NS) is None:
        return "block formula carrier line is not reversible"
    return None


def _prepare_inline_formula_resolver(
    tx_body: ET.Element | None,
    ctx: AssemblyContext,
    *,
    allow_native: bool,
    force_opaque: bool = False,
):
    """Build one all-or-opaque resolver for formula runs in a text body."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .slide_to_svg import (
        _diagnose_formula_fallback,
    )
    if tx_body is None:
        return None
    zones = [
        child
        for paragraph in tx_body.findall("a:p", NS)
        for child in paragraph
        if child.tag == f"{{{A14_NS}}}m"
    ]
    if not zones:
        return None

    imported: dict[int, FormulaImport] = {}
    failed = force_opaque
    if not allow_native:
        failed = True
        _diagnose_formula_fallback(
            ctx,
            FormulaImportError(
                "formula reconstruction is not supported inside vertical text"
            ),
        )
    elif not force_opaque:
        for zone in zones:
            try:
                imported[id(zone)] = import_formula(zone, display=False, palette=ctx.palette)
            except FormulaImportError as exc:
                failed = True
                _diagnose_formula_fallback(ctx, exc)

    def _resolve(zone: ET.Element) -> tuple[FormulaImport | None, str]:
        if failed:
            return None, opaque_formula_preview(zone)
        item = imported.get(id(zone))
        if item is None:
            return None, opaque_formula_preview(zone)
        return item, item.preview

    return _resolve


def _diagnose_formula_fallback(
    ctx: AssemblyContext,
    error: FormulaImportError,
) -> None:
    message = f"Office Math was not reconstructed: {error}"
    if ctx.strict:
        raise ValueError(message) from error
    ctx.diagnose(
        "formula-not-reconstructed",
        message,
        "render a linear text preview and preserve the relationship-free source txBody",
    )


def _render_block_formula(
    node: ShapeNode,
    ctx: AssemblyContext,
    formula: FormulaImport,
    *,
    top_level: bool,
) -> str:
    """Emit one canonical block marker and a dependency-free SVG preview."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .slide_to_svg import (
        _object_metadata,
        _wrap_shape_group,
        _xml_escape,
    )
    if node.xfrm.w <= 0 or node.xfrm.h <= 0:
        ctx.diagnose(
            "formula-degenerate-frame",
            "Source formula has a missing or nonpositive frame: "
            + _object_metadata(node, ctx)["data-pptx-frame"],
            "retain the formula content and exact frame; no positive frame is invented",
        )
    x = fmt_num(node.xfrm.x)
    y = fmt_num(node.xfrm.y)
    width = fmt_num(node.xfrm.w)
    height = fmt_num(node.xfrm.h)
    align = formula.align
    if align == "right":
        preview_x = node.xfrm.x + node.xfrm.w
        anchor = "end"
    elif align == "left":
        preview_x = node.xfrm.x
        anchor = "start"
    else:
        preview_x = node.xfrm.x + node.xfrm.w / 2.0
        anchor = "middle"
    preview_y = node.xfrm.y + node.xfrm.h / 2.0 + formula.font_size_px * 0.35
    payload = {
        "latex": formula.latex,
        "display": "block",
        "font_size": formula.font_size_px,
        "color": formula.color,
        "align": align,
        "language": formula.language,
        "name": node.name or f"Formula {node.spid}",
    }
    metadata = (
        '<metadata type="application/json">'
        + _xml_escape(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
        + "</metadata>"
    )
    preview = (
        f'<text x="{fmt_num(preview_x)}" y="{fmt_num(preview_y)}" '
        f'text-anchor="{anchor}" font-family="Cambria Math" '
        f'font-size="{fmt_num(formula.font_size_px)}" '
        f'fill="{_xml_escape(formula.color)}">'
        f'{_xml_escape(formula.preview)}</text>'
    )
    return _wrap_shape_group(
        metadata + "\n" + preview,
        node,
        ctx,
        top_level=top_level,
        extra_attrs=[
            'data-pptx-replace-with="formula"',
            'data-pptx-import-source="pptx"',
            f'data-pptx-x="{x}"',
            f'data-pptx-y="{y}"',
            f'data-pptx-width="{width}"',
            f'data-pptx-height="{height}"',
            f'data-pptx-bounds="{x} {y} {width} {height}"',
        ],
    )


