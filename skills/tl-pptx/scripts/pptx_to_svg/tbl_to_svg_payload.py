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
    _CellSlot,
)

def _round_payload_number(value: float) -> int | float:
    rounded = round(float(value), 3)
    return int(rounded) if rounded.is_integer() else rounded


def _native_table_payload(
    tbl: ET.Element,
    xfrm: Xfrm,
    column_widths: list[float],
    row_heights: list[float],
    cells: list[list[_CellSlot | None]],
    palette: ColorPalette | None,
    theme_fonts: dict[str, str],
) -> dict[str, Any]:
    """Build the SVG native Table replacement payload for an unmerged table."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .tbl_to_svg import (
        _native_cell_payload,
        _round_payload_number,
    )
    tbl_pr = tbl.find("a:tblPr", NS)
    payload: dict[str, Any] = {
        "x": _round_payload_number(xfrm.x),
        "y": _round_payload_number(xfrm.y),
        "width": _round_payload_number(xfrm.w),
        "height": _round_payload_number(xfrm.h),
        "strict_grid": True,
        "header_rows": (
            1 if tbl_pr is not None and ooxml_bool(tbl_pr.get("firstRow")) else 0
        ),
        "column_widths": [_round_payload_number(width) for width in column_widths],
        "row_heights": [_round_payload_number(height) for height in row_heights],
        "rows": [],
    }
    style: dict[str, Any] = {
        "band_row": bool(tbl_pr is not None and ooxml_bool(tbl_pr.get("bandRow"))),
    }
    if tbl_pr is not None:
        table_style_id = tbl_pr.findtext("a:tableStyleId", default="", namespaces=NS).strip()
        if table_style_id:
            style["table_style_id"] = table_style_id
    payload["style"] = style

    rows_payload: list[list[Any]] = []
    for row_cells in cells:
        row_payload: list[Any] = []
        for slot in row_cells:
            if slot is None:
                row_payload.append("")
                continue
            cell_payload = _native_cell_payload(
                slot.element,
                palette,
                theme_fonts,
            )
            if slot.is_dropped:
                cell_payload["merge_continuation"] = True
                row_payload.append(cell_payload)
                continue
            if slot.row_span > 1:
                cell_payload["row_span"] = slot.row_span
            if slot.col_span > 1:
                cell_payload["col_span"] = slot.col_span
            row_payload.append(cell_payload)
        rows_payload.append(row_payload)
    payload["rows"] = rows_payload
    return compact_semantic_table_payload(payload)


def _native_cell_payload(
    tc: ET.Element,
    palette: ColorPalette | None,
    theme_fonts: dict[str, str],
) -> dict[str, Any]:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .tbl_to_svg import (
        _cell_align,
        _cell_bold,
        _cell_borders_payload,
        _cell_fill_payload,
        _cell_font_size_px,
        _cell_paragraph_payloads,
        _cell_plain_text,
        _cell_rich_paragraph_payloads,
        _cell_text_color,
        _cell_valign,
        _copy_cell_layout_options,
        _copy_cell_margins,
    )
    tx_body = tc.find("a:txBody", NS)
    tc_pr = tc.find("a:tcPr", NS)
    paragraph_payloads = _cell_paragraph_payloads(tx_body)
    rich_paragraphs = _cell_rich_paragraph_payloads(
        tx_body,
        palette,
        theme_fonts,
    )
    if rich_paragraphs is not None:
        cell: dict[str, Any] = {"paragraphs": rich_paragraphs}
    elif len(paragraph_payloads) > 1:
        cell: dict[str, Any] = {"paragraphs": paragraph_payloads}
    else:
        cell = {"text": _cell_plain_text(tx_body)}

    fill, fill_opacity = _cell_fill_payload(tc_pr, palette)
    if fill:
        cell["fill"] = fill
    if fill_opacity is not None:
        cell["fill_opacity"] = fill_opacity
    if rich_paragraphs is None:
        color = _cell_text_color(tx_body, palette)
        if color:
            cell["color"] = color
        font_size = _cell_font_size_px(tx_body)
        if font_size:
            cell["font_size"] = font_size
    if len(paragraph_payloads) <= 1:
        align = _cell_align(tx_body)
        if align:
            cell["align"] = align
    valign = _cell_valign(tc_pr)
    if valign:
        cell["valign"] = valign
    if rich_paragraphs is None:
        bold = _cell_bold(tx_body)
        if bold is not None:
            cell["bold"] = bold
    borders = _cell_borders_payload(tc_pr, palette)
    if borders:
        cell["borders"] = borders
    _copy_cell_margins(tc_pr, cell)
    _copy_cell_layout_options(tc_pr, cell)
    return cell


def _cell_plain_text(tx_body: ET.Element | None) -> str:
    if tx_body is None:
        return ""
    paragraphs: list[str] = []
    for paragraph in tx_body.findall("a:p", NS):
        text = "".join(node.text or "" for node in paragraph.findall(".//a:t", NS))
        if text:
            paragraphs.append(text)
    return "\n".join(paragraphs)


def _cell_paragraph_payloads(
    tx_body: ET.Element | None,
) -> list[str | dict[str, str]]:
    if tx_body is None:
        return []
    payloads: list[str | dict[str, str]] = []
    for paragraph in tx_body.findall("a:p", NS):
        text = "".join(node.text or "" for node in paragraph.findall(".//a:t", NS))
        p_pr = paragraph.find("a:pPr", NS)
        align = p_pr.get("algn") if p_pr is not None else None
        if align in {"l", "ctr", "r", "just"}:
            payloads.append({"text": text, "align": align})
        else:
            payloads.append(text)
    return payloads


def _effective_run_attr(
    r_pr: ET.Element | None,
    default_r_pr: ET.Element | None,
    name: str,
) -> str | None:
    if r_pr is not None and r_pr.get(name) is not None:
        return r_pr.get(name)
    return default_r_pr.get(name) if default_r_pr is not None else None


def _effective_run_child(
    r_pr: ET.Element | None,
    default_r_pr: ET.Element | None,
    name: str,
) -> ET.Element | None:
    child = r_pr.find(f"a:{name}", NS) if r_pr is not None else None
    if child is not None:
        return child
    return default_r_pr.find(f"a:{name}", NS) if default_r_pr is not None else None


def _native_run_font_family(
    r_pr: ET.Element | None,
    default_r_pr: ET.Element | None,
    theme_fonts: dict[str, str],
) -> str | None:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .tbl_to_svg import (
        _effective_run_child,
    )
    faces: list[str] = []
    for tag in ("latin", "ea"):
        node = _effective_run_child(r_pr, default_r_pr, tag)
        raw_face = node.get("typeface") if node is not None else None
        face = _resolve_theme_typeface(raw_face, theme_fonts)
        if face and face not in faces:
            faces.append(face)
    if len(faces) != 1:
        return None
    face = faces[0].strip()
    return face if face and "," not in face else None


def _native_run_payload(
    run: ET.Element,
    default_r_pr: ET.Element | None,
    palette: ColorPalette | None,
    theme_fonts: dict[str, str],
) -> dict[str, Any]:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .tbl_to_svg import (
        _canonical_source_font_size_px,
        _effective_run_attr,
        _effective_run_child,
        _native_run_font_family,
        _native_run_outline_payload,
        _round_payload_number,
        _solid_fill_is_unsafe,
    )
    r_pr = run.find("a:rPr", NS)
    text = run.findtext("a:t", default="", namespaces=NS)
    payload: dict[str, Any] = {"text": text}

    for source, target in (("b", "bold"), ("i", "italic")):
        raw = _effective_run_attr(r_pr, default_r_pr, source)
        if raw is not None:
            payload[target] = ooxml_bool(raw)

    underline = _effective_run_attr(r_pr, default_r_pr, "u")
    if underline is not None:
        payload["underline"] = underline != "none"
    strike = _effective_run_attr(r_pr, default_r_pr, "strike")
    if strike is not None:
        payload["strike"] = strike != "noStrike"

    font_size = _canonical_source_font_size_px(
        _effective_run_attr(r_pr, default_r_pr, "sz")
    )
    if font_size is not None:
        payload["font_size"] = font_size

    solid_fill = _effective_run_child(r_pr, default_r_pr, "solidFill")
    if solid_fill is not None and not _solid_fill_is_unsafe(solid_fill, palette):
        try:
            color, _alpha = resolve_color(find_color_elem(solid_fill), palette)
        except (AttributeError, OverflowError, TypeError, ValueError):
            color = None
        if color:
            payload["color"] = color

    font_family = _native_run_font_family(
        r_pr,
        default_r_pr,
        theme_fonts,
    )
    if font_family:
        payload["font_family"] = font_family

    for source, target in (("lang", "lang"), ("altLang", "alt_lang")):
        language = _effective_run_attr(r_pr, default_r_pr, source)
        if language and language.strip():
            payload[target] = language.strip()

    raw_baseline = _effective_run_attr(r_pr, default_r_pr, "baseline")
    if raw_baseline is not None:
        try:
            baseline = int(raw_baseline)
        except ValueError:
            baseline = 0
        if baseline:
            payload["baseline_percent"] = _round_payload_number(
                baseline / 1000.0
            )

    outline = _native_run_outline_payload(
        _effective_run_child(r_pr, default_r_pr, "ln"),
        palette,
    )
    if outline is not None:
        payload["outline"] = outline
    return payload


def _paragraph_line_spacing_percent(
    p_pr: ET.Element | None,
) -> int | float | None:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .tbl_to_svg import (
        _round_payload_number,
    )
    if p_pr is None:
        return None
    spacing = p_pr.find("a:lnSpc/a:spcPct", NS)
    raw = spacing.get("val") if spacing is not None else None
    if raw is None or not raw.isdigit():
        return None
    percent = int(raw) / 1000.0
    if percent == 100:
        return None
    return _round_payload_number(percent)


def _run_style_signature(run: dict[str, Any]) -> tuple[tuple[str, Any], ...]:
    """Return one hashable signature for deciding whether runs are required."""
    return tuple(
        sorted(
            (
                key,
                json.dumps(value, sort_keys=True, separators=(",", ":"))
                if isinstance(value, dict)
                else value,
            )
            for key, value in run.items()
            if key != "text"
        )
    )


