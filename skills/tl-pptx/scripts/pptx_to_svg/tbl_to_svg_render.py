"""Convert a DrawingML <a:tbl> into SVG.

Tables in PowerPoint are stored under <p:graphicFrame> with
graphicData uri="...drawingml/2006/table" wrapping a single <a:tbl>:

    <p:graphicFrame>
      <p:xfrm>...</p:xfrm>
      <a:graphic><a:graphicData uri="...table">
        <a:tbl>
          <a:tblPr/>
          <a:tblGrid>
            <a:gridCol w="..."/>...
          </a:tblGrid>
          <a:tr h="...">
            <a:tc [gridSpan=N] [rowSpan=N] [hMerge=1] [vMerge=1]>
              <a:txBody>...</a:txBody>
              <a:tcPr>
                <a:lnL/><a:lnR/><a:lnT/><a:lnB/>
                <a:solidFill/>... or <a:gradFill/> ...
              </a:tcPr>
            </a:tc>
          </a:tr>
        </a:tbl>
      </a:graphicData></a:graphic>
    </p:graphicFrame>

The graphicFrame's <p:xfrm> gives the table's slide-space position and total
size; <a:tblGrid> + <a:tr> heights distribute that size across columns/rows.

Cell painting order:
1. background fill (rect at cell box)
2. text body (re-uses convert_txbody)
3. cell borders (lnT / lnR / lnB / lnL — stroked as separate <line>s so
   neighbouring cells with different border styles render correctly)
"""

from __future__ import annotations

import copy
import json
import math
from dataclasses import dataclass
from typing import Any
from xml.etree import ElementTree as ET

from pptx_effects import txbody_has_run_effects
from semantic_table import compact_semantic_table_payload

from .color_resolver import ColorPalette, find_color_elem, resolve_color
from .emu_units import (
    NS,
    Xfrm,
    emu_to_px,
    fmt_num,
    hundredths_pt_to_px,
    ooxml_bool,
)
from .fill_to_svg import FillResult, resolve_fill
from .ln_to_svg import resolve_stroke
from .txbody_to_svg import (
    HyperlinkResolver,
    TextDiagnosticSink,
    TextImportError,
    _resolve_theme_typeface,
    convert_txbody,
)



# Upstream MIT, Hugo He 2025-2026; algorithms retained during module split.
from .tbl_to_svg import (
    _TableStyleContext,
)

def _convert_cell_text(
    tx_body: ET.Element,
    tcPr: ET.Element | None,
    cell_xfrm: Xfrm,
    palette: ColorPalette | None,
    theme_fonts: dict[str, str] | None,
    *,
    fallback_run_props: tuple[ET.Element, ...],
    slide_number: int | None,
    id_prefix: str,
    id_seq: list[int] | None,
    hyperlink_resolver: HyperlinkResolver | None,
    strict: bool,
    diagnostic_sink: TextDiagnosticSink | None,
):
    """Render cell text. PowerPoint's <a:tcPr> can override txBody insets via
    its own marL/marR/marT/marB attrs; convert_txbody reads from <a:bodyPr>,
    so we materialise a synthetic bodyPr when tcPr has its own insets. Invalid
    malformed source font-size and run-color values are removed from a private
    render copy; they have already been omitted from the native payload and
    must not crash fallback SVG generation."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .tbl_to_svg import (
        _canonical_source_font_size_px,
        _plain_table_text_body,
        _run_props_need_render_normalization,
        _solid_fill_is_unsafe,
        _tcPr_inset_overrides,
    )
    render_tx_body = tx_body
    run_props = [
        node
        for node in tx_body.iter()
        if node.tag in {
            f"{{{NS['a']}}}defRPr",
            f"{{{NS['a']}}}endParaRPr",
            f"{{{NS['a']}}}rPr",
        }
    ]
    if any(
        _run_props_need_render_normalization(node, palette)
        for node in run_props
    ):
        render_tx_body = copy.deepcopy(tx_body)
        for node in render_tx_body.iter():
            if node.tag not in {
                f"{{{NS['a']}}}defRPr",
                f"{{{NS['a']}}}endParaRPr",
                f"{{{NS['a']}}}rPr",
            }:
                continue
            if (
                node.get("sz") is not None
                and _canonical_source_font_size_px(node.get("sz")) is None
            ):
                node.attrib.pop("sz", None)
            solid_fill = node.find("a:solidFill", NS)
            if solid_fill is not None and _solid_fill_is_unsafe(solid_fill, palette):
                node.remove(solid_fill)

    body_pr = render_tx_body.find("a:bodyPr", NS)
    overrides = _tcPr_inset_overrides(tcPr)
    saved: dict[str, str | None] = {}
    if overrides and body_pr is not None:
        for key, val in overrides.items():
            saved[key] = body_pr.attrib.get(key)
            body_pr.set(key, val)
    try:
        try:
            return convert_txbody(
                render_tx_body, cell_xfrm, palette, theme_fonts=theme_fonts,
                fallback_run_props=fallback_run_props,
                slide_number=slide_number,
                id_prefix=id_prefix,
                id_seq=id_seq,
                hyperlink_resolver=hyperlink_resolver,
                strict=strict,
                diagnostic_sink=diagnostic_sink,
            )
        except TextImportError:
            raise
        except (AttributeError, OverflowError, TypeError, ValueError):
            plain_tx_body = _plain_table_text_body(render_tx_body, overrides)
            return convert_txbody(
                plain_tx_body, cell_xfrm, palette, theme_fonts=theme_fonts,
                fallback_run_props=(),
                slide_number=slide_number,
                id_prefix=id_prefix,
                id_seq=id_seq,
                hyperlink_resolver=hyperlink_resolver,
                strict=strict,
                diagnostic_sink=diagnostic_sink,
            )
    finally:
        if overrides and body_pr is not None:
            for key, prior in saved.items():
                if prior is None:
                    body_pr.attrib.pop(key, None)
                else:
                    body_pr.set(key, prior)


def _run_props_need_render_normalization(
    run_props: ET.Element,
    palette: ColorPalette | None,
) -> bool:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .tbl_to_svg import (
        _canonical_source_font_size_px,
        _solid_fill_is_unsafe,
    )
    size = run_props.get("sz")
    if size is not None and _canonical_source_font_size_px(size) is None:
        return True
    solid_fill = run_props.find("a:solidFill", NS)
    return solid_fill is not None and _solid_fill_is_unsafe(solid_fill, palette)


def _solid_fill_is_unsafe(
    solid_fill: ET.Element,
    palette: ColorPalette | None,
) -> bool:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .tbl_to_svg import (
        _color_numeric_tokens_are_finite,
    )
    color = find_color_elem(solid_fill)
    if color is not None and not _color_numeric_tokens_are_finite(color):
        return True
    try:
        resolve_color(color, palette)
    except (AttributeError, OverflowError, TypeError, ValueError):
        return True
    return False


def _color_numeric_tokens_are_finite(color: ET.Element) -> bool:
    """Reject non-finite numeric tokens before the resolver can clamp them."""
    base_numeric_attrs = {
        "hslClr": ("hue", "sat", "lum"),
        "scrgbClr": ("r", "g", "b"),
    }
    modifier_tags = {
        "alpha", "alphaMod", "alphaOff", "hueMod", "hueOff",
        "lumMod", "lumOff", "satMod", "satOff", "shade", "tint",
    }
    for node in color.iter():
        name = node.tag.rsplit("}", 1)[-1]
        attrs = base_numeric_attrs.get(name, ())
        if name in modifier_tags:
            attrs = ("val",)
        for attr in attrs:
            raw = node.get(attr)
            if raw is None:
                return False
            try:
                value = float(raw)
            except (OverflowError, TypeError, ValueError):
                return False
            if not math.isfinite(value):
                return False
    return True


def _plain_table_text_body(
    tx_body: ET.Element,
    overrides: dict[str, str],
) -> ET.Element:
    """Return a text-preserving style-free fallback after malformed styling."""
    plain = copy.deepcopy(tx_body)
    body_pr = plain.find("a:bodyPr", NS)
    if body_pr is None:
        body_pr = ET.Element(f"{{{NS['a']}}}bodyPr")
        plain.insert(0, body_pr)
    body_pr.clear()
    for key, value in overrides.items():
        body_pr.set(key, value)
    list_style = plain.find("a:lstStyle", NS)
    if list_style is not None:
        list_style.clear()
    for paragraph in plain.findall("a:p", NS):
        p_pr = paragraph.find("a:pPr", NS)
        if p_pr is not None:
            align = p_pr.get("algn")
            p_pr.clear()
            if align in {"l", "ctr", "r"}:
                p_pr.set("algn", align)
    for path in (".//a:rPr", ".//a:defRPr", ".//a:endParaRPr"):
        for run_props in plain.findall(path, NS):
            run_props.clear()
    return plain


def _tcPr_inset_overrides(tcPr: ET.Element | None) -> dict[str, str]:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .tbl_to_svg import (
        _safe_emu_integer,
    )
    if tcPr is None:
        return {}
    out: dict[str, str] = {}
    for src, dst in (("marL", "lIns"), ("marR", "rIns"),
                     ("marT", "tIns"), ("marB", "bIns")):
        if src not in tcPr.attrib:
            continue
        value = _safe_emu_integer(tcPr.attrib[src])
        if value is not None and value >= 0:
            out[dst] = str(value)
    return out


def _border_line(
    tcPr: ET.Element | None,
    table_style: _TableStyleContext,
    row_index: int,
    col_index: int,
    row_count: int,
    col_count: int,
    tag: str,
    x1: float, y1: float, x2: float, y2: float,
    palette: ColorPalette | None,
    *,
    id_prefix: str,
    id_seq: list[int],
    defs: list[str],
) -> str:
    """Emit a single border <line> for a given cell side, or empty string when
    that side is explicitly noFill / not specified."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .tbl_to_svg import (
        _line_element_to_svg,
        _table_style_border_names,
    )
    ln = tcPr.find(tag, NS) if tcPr is not None else None
    if ln is not None:
        return _line_element_to_svg(
            ln, x1, y1, x2, y2, palette,
            id_prefix=id_prefix, id_seq=id_seq, defs=defs,
        )

    # Draw inherited shared edges once, from the upper/left cell.  This keeps
    # a specific firstRow bottom border from being painted over by the next
    # row's whole-table top border.  A direct border above still wins because
    # it is handled before this de-duplication gate.
    if (tag == "a:lnT" and row_index > 0) or (
        tag == "a:lnL" and col_index > 0
    ):
        return ""

    for region_name, region in table_style.regions_for_row(row_index):
        for border_name in _table_style_border_names(
            region_name, row_index, col_index, row_count, col_count, tag,
        ):
            ln = region.find(
                f"a:tcStyle/a:tcBdr/a:{border_name}/a:ln", NS,
            )
            if ln is not None:
                return _line_element_to_svg(
                    ln, x1, y1, x2, y2, palette,
                    id_prefix=id_prefix, id_seq=id_seq, defs=defs,
                )
    return ""


def _table_style_border_names(
    region_name: str,
    row_index: int,
    col_index: int,
    row_count: int,
    col_count: int,
    tag: str,
) -> tuple[str, ...]:
    side_names = {
        "a:lnT": ("top", "insideH", row_index > 0),
        "a:lnR": ("right", "insideV", col_index < col_count - 1),
        "a:lnB": ("bottom", "insideH", row_index < row_count - 1),
        "a:lnL": ("left", "insideV", col_index > 0),
    }
    side, inside, is_internal = side_names[tag]
    if region_name == "wholeTbl" and is_internal:
        return inside, side
    return side, inside


def _line_element_to_svg(
    ln: ET.Element,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    palette: ColorPalette | None,
    *,
    id_prefix: str,
    id_seq: list[int],
    defs: list[str],
) -> str:
    # Skip explicit no-line.
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .tbl_to_svg import (
        _make_ln_wrapper,
    )
    if ln.find("a:noFill", NS) is not None:
        return ""

    stroke = resolve_stroke(
        # resolve_stroke expects a parent that contains <a:ln>; wrap so it
        # finds our tag's own children as the line spec.
        _make_ln_wrapper(ln),
        palette,
        id_prefix=id_prefix,
        id_seq=id_seq,
        gradient_frame=(
            min(x1, x2),
            min(y1, y2),
            abs(x2 - x1),
            abs(y2 - y1),
        ),
    )
    defs.extend(stroke.defs)
    attrs = stroke.attrs
    if not attrs.get("stroke"):
        return ""
    attr_str = "".join(f' {k}="{v}"' for k, v in attrs.items())
    return (
        f'<line x1="{fmt_num(x1)}" y1="{fmt_num(y1)}" '
        f'x2="{fmt_num(x2)}" y2="{fmt_num(y2)}"{attr_str}/>'
    )


def _make_ln_wrapper(ln: ET.Element) -> ET.Element:
    """resolve_stroke walks for ``parent.find('a:ln')``; tcPr borders ARE the
    <a:ln> already, so wrap them in a synthetic parent that points back at
    the original element under the expected tag.
    """
    wrapper = ET.Element(f"{{{NS['a']}}}wrapper")
    proxy = ET.SubElement(wrapper, f"{{{NS['a']}}}ln")
    # Carry attributes (e.g. w="...") and children (solidFill, prstDash, ...).
    for k, v in ln.attrib.items():
        proxy.set(k, v)
    for child in list(ln):
        proxy.append(child)
    return wrapper
