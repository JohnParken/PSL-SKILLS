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

def _cell_rich_paragraph_payloads(
    tx_body: ET.Element | None,
    palette: ColorPalette | None,
    theme_fonts: dict[str, str],
) -> list[dict[str, Any]] | None:
    """Materialize runs only when the legacy plain contract was insufficient."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .tbl_to_svg import (
        _legacy_text_body_has_unsupported_formatting,
        _native_run_payload,
        _paragraph_line_spacing_percent,
        _run_style_signature,
    )
    if tx_body is None or not _legacy_text_body_has_unsupported_formatting(tx_body):
        return None

    paragraphs: list[
        tuple[str | None, int | float | None, list[dict[str, Any]]]
    ] = []
    style_signatures: set[tuple[tuple[str, Any], ...]] = set()
    needs_runs = False
    run_only_fields = {
        "italic", "underline", "strike", "font_family", "lang", "alt_lang",
        "baseline_percent", "outline",
    }
    for paragraph in tx_body.findall("a:p", NS):
        p_pr = paragraph.find("a:pPr", NS)
        align = p_pr.get("algn") if p_pr is not None else None
        if align not in {"l", "ctr", "r", "just"}:
            align = None
        line_spacing_percent = _paragraph_line_spacing_percent(p_pr)
        default_r_pr = p_pr.find("a:defRPr", NS) if p_pr is not None else None
        runs = [
            _native_run_payload(run, default_r_pr, palette, theme_fonts)
            for run in paragraph.findall("a:r", NS)
        ]
        paragraphs.append((align, line_spacing_percent, runs))
        if line_spacing_percent is not None:
            needs_runs = True
        for run in runs:
            style = _run_style_signature(run)
            style_signatures.add(style)
            if run_only_fields.intersection(run):
                needs_runs = True

    if len(style_signatures) > 1:
        needs_runs = True
    if not needs_runs:
        return None

    payloads: list[dict[str, Any]] = []
    for align, line_spacing_percent, runs in paragraphs:
        paragraph_payload: dict[str, Any]
        if runs:
            paragraph_payload = {"runs": runs}
        else:
            paragraph_payload = {"text": ""}
        if align is not None:
            paragraph_payload["align"] = align
        if line_spacing_percent is not None:
            paragraph_payload["line_spacing_percent"] = line_spacing_percent
        payloads.append(paragraph_payload)
    return payloads


def _cell_fill_payload(
    tc_pr: ET.Element | None,
    palette: ColorPalette | None,
) -> tuple[str | None, int | float | None]:
    """Return one normalized solid cell fill and optional opacity."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .tbl_to_svg import (
        _round_payload_number,
    )
    fill = resolve_fill(tc_pr, palette)
    color = fill.attrs.get("fill") if fill.attrs else None
    if not color or not color.startswith("#"):
        return None, None
    opacity_raw = fill.attrs.get("fill-opacity")
    opacity = (
        _round_payload_number(float(opacity_raw))
        if opacity_raw is not None
        else None
    )
    return color, opacity


def _cell_text_color(tx_body: ET.Element | None, palette: ColorPalette | None) -> str | None:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .tbl_to_svg import (
        _solid_fill_is_unsafe,
        _text_run_props_in_priority,
    )
    for r_pr in _text_run_props_in_priority(tx_body):
        solid_fill = r_pr.find("a:solidFill", NS)
        if solid_fill is not None and not _solid_fill_is_unsafe(solid_fill, palette):
            try:
                color, _alpha = resolve_color(find_color_elem(solid_fill), palette)
            except (AttributeError, OverflowError, TypeError, ValueError):
                continue
            if color:
                return color
    return None


def _cell_font_size_px(tx_body: ET.Element | None) -> int | float | None:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .tbl_to_svg import (
        _canonical_source_font_size_px,
        _text_run_props_in_priority,
    )
    for r_pr in _text_run_props_in_priority(tx_body):
        size = _canonical_source_font_size_px(r_pr.get("sz"))
        if size is not None:
            return size
    return None


def _canonical_source_font_size_px(raw_size: str | None) -> int | float | None:
    """Return a writer-stable font size for one bounded DrawingML token."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .tbl_to_svg import (
        _round_payload_number,
    )
    if (
        raw_size is None
        or not raw_size.isascii()
        or not raw_size.isdigit()
        or len(raw_size) > 6
    ):
        return None
    size_hpt = int(raw_size)
    if not 100 <= size_hpt <= 400000:
        return None
    # The writer emits sizes at 0.1pt precision; canonicalize on first import
    # so source and native reimport payloads stay stable.
    canonical_hpt = round(size_hpt / 10) * 10
    return _round_payload_number(hundredths_pt_to_px(canonical_hpt))


def _cell_align(tx_body: ET.Element | None) -> str | None:
    if tx_body is None:
        return None
    p_pr = tx_body.find("a:p/a:pPr", NS)
    align = p_pr.get("algn") if p_pr is not None else None
    if align in {"l", "ctr", "r", "just"}:
        return align
    return None


def _cell_valign(tc_pr: ET.Element | None) -> str | None:
    anchor = tc_pr.get("anchor") if tc_pr is not None else None
    return {
        "t": "top",
        "ctr": "middle",
        "b": "bottom",
    }.get(anchor)


def _cell_bold(tx_body: ET.Element | None) -> bool | None:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .tbl_to_svg import (
        _text_run_props_in_priority,
    )
    for r_pr in _text_run_props_in_priority(tx_body):
        if r_pr.get("b") is not None:
            return ooxml_bool(r_pr.get("b"))
    return None


def _cell_borders_payload(
    tc_pr: ET.Element | None,
    palette: ColorPalette | None,
) -> dict[str, dict[str, Any]]:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .tbl_to_svg import (
        _DIRECT_BORDER_TAGS,
        _direct_border_payload,
    )
    if tc_pr is None:
        return {}
    borders: dict[str, dict[str, Any]] = {}
    for border_tag, side in _DIRECT_BORDER_TAGS.items():
        ln = tc_pr.find(f"a:{border_tag}", NS)
        if ln is not None:
            borders[side] = _direct_border_payload(ln, palette)
    return borders


def _text_run_props_in_priority(tx_body: ET.Element | None) -> list[ET.Element]:
    if tx_body is None:
        return []
    props: list[ET.Element] = []
    for path in (".//a:r/a:rPr", ".//a:pPr/a:defRPr", ".//a:endParaRPr"):
        r_pr = tx_body.find(path, NS)
        if r_pr is not None:
            props.append(r_pr)
    return props


def _copy_cell_margins(tc_pr: ET.Element | None, cell: dict[str, Any]) -> None:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .tbl_to_svg import (
        _round_payload_number,
        _safe_emu_integer,
    )
    if tc_pr is None:
        return
    for source, target in (
        ("marL", "padding_left"),
        ("marR", "padding_right"),
        ("marT", "padding_top"),
        ("marB", "padding_bottom"),
    ):
        if source not in tc_pr.attrib:
            continue
        value = _safe_emu_integer(tc_pr.attrib[source])
        if value is None or value < 0:
            continue
        cell[target] = _round_payload_number(emu_to_px(value))


def _copy_cell_layout_options(
    tc_pr: ET.Element | None,
    cell: dict[str, Any],
) -> None:
    """Copy native cell layout semantics not represented by SVG text."""
    if tc_pr is None:
        return
    if tc_pr.get("anchorCtr") is not None:
        cell["anchor_center"] = ooxml_bool(tc_pr.get("anchorCtr"))
    horizontal_overflow = tc_pr.get("horzOverflow")
    if horizontal_overflow is not None:
        cell["horizontal_overflow"] = horizontal_overflow


# ---------------------------------------------------------------------------
# Cell text & borders
# ---------------------------------------------------------------------------

