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
    TableResult,
    _TableStyleContext,
)

def _normalize_table_style_id(value: str | None) -> str:
    return (value or "").strip().strip("{}").upper()


def _resolve_table_style(
    tbl: ET.Element,
    table_styles: ET.Element | None,
) -> _TableStyleContext:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .tbl_to_svg import (
        BUILTIN_MEDIUM_STYLE_2_ACCENT_1,
        _BUILTIN_MEDIUM_STYLE_2_ACCENT_1_XML,
        _TableStyleContext,
        _normalize_table_style_id,
    )
    tbl_pr = tbl.find("a:tblPr", NS)
    style_id = (
        tbl_pr.findtext("a:tableStyleId", default="", namespaces=NS).strip()
        if tbl_pr is not None else ""
    )
    if not style_id and table_styles is not None:
        style_id = table_styles.get("def", "").strip()
    normalized_id = _normalize_table_style_id(style_id)
    supported_id = _normalize_table_style_id(BUILTIN_MEDIUM_STYLE_2_ACCENT_1)

    # P1 deliberately supports one built-in family.  Consuming an arbitrary
    # custom definition here would be asymmetric: native reconstruction keeps
    # only the style id and does not copy custom tableStyles.xml definitions.
    if normalized_id != supported_id:
        return _TableStyleContext(None, tbl_pr)

    if table_styles is not None and normalized_id:
        for candidate in table_styles.findall("a:tblStyle", NS):
            if _normalize_table_style_id(candidate.get("styleId")) == normalized_id:
                return _TableStyleContext(candidate, tbl_pr)

    return _TableStyleContext(_BUILTIN_MEDIUM_STYLE_2_ACCENT_1_XML, tbl_pr)


def _effective_cell_fill(
    tc_pr: ET.Element | None,
    table_style: _TableStyleContext,
    row_index: int,
    palette: ColorPalette | None,
    *,
    id_prefix: str,
    id_seq: list[int],
) -> FillResult:
    """Resolve direct cell fill before row-region and whole-table defaults."""
    direct = resolve_fill(
        tc_pr, palette, id_prefix=id_prefix, id_seq=id_seq,
    )
    if direct.attrs or direct.defs:
        return direct

    for _name, region in table_style.regions_for_row(row_index):
        fill_parent = region.find("a:tcStyle/a:fill", NS)
        if fill_parent is None:
            continue
        inherited = resolve_fill(
            fill_parent, palette, id_prefix=id_prefix, id_seq=id_seq,
        )
        if inherited.attrs or inherited.defs:
            return inherited
    return direct


def _table_text_run_props(
    table_style: _TableStyleContext,
    row_index: int,
    theme_fonts: dict[str, str],
) -> tuple[ET.Element, ...]:
    """Materialize table text-style regions as lowest-priority run defaults."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .tbl_to_svg import (
        _table_tx_style_run_props,
    )
    props: list[ET.Element] = []
    for _name, region in table_style.regions_for_row(row_index):
        tx_style = region.find("a:tcTxStyle", NS)
        if tx_style is None:
            continue
        run_props = _table_tx_style_run_props(tx_style, theme_fonts)
        if run_props is not None:
            props.append(run_props)
    return tuple(props)


def _table_tx_style_run_props(
    tx_style: ET.Element,
    theme_fonts: dict[str, str],
) -> ET.Element | None:
    run_props = ET.Element(f"{{{NS['a']}}}rPr")
    for attr in ("b", "i"):
        value = tx_style.get(attr)
        if value is not None:
            run_props.set(attr, "1" if ooxml_bool(value) else "0")

    font_ref = tx_style.find("a:fontRef", NS)
    font_role = font_ref.get("idx") if font_ref is not None else None
    if font_role in {"major", "minor"}:
        prefix = "major" if font_role == "major" else "minor"
        latin = theme_fonts.get(f"{prefix}Latin")
        east_asia = theme_fonts.get(f"{prefix}EastAsia") or latin
        complex_script = theme_fonts.get(f"{prefix}ComplexScript") or latin
        for tag, typeface in (
            ("latin", latin),
            ("ea", east_asia),
            ("cs", complex_script),
        ):
            if typeface:
                ET.SubElement(
                    run_props, f"{{{NS['a']}}}{tag}",
                    {"typeface": typeface},
                )

    color = find_color_elem(tx_style)
    if color is not None:
        solid_fill = ET.SubElement(run_props, f"{{{NS['a']}}}solidFill")
        solid_fill.append(copy.deepcopy(color))

    if not run_props.attrib and not list(run_props):
        return None
    return run_props


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------

def convert_tbl(
    tbl: ET.Element,
    xfrm: Xfrm,
    palette: ColorPalette | None,
    *,
    table_styles: ET.Element | None = None,
    theme_fonts: dict[str, str] | None = None,
    slide_number: int | None = None,
    id_prefix: str = "tbl",
    grad_seq: list[int] | None = None,
    marker_seq: list[int] | None = None,
    hyperlink_resolver: HyperlinkResolver | None = None,
    strict: bool = False,
    diagnostic_sink: TextDiagnosticSink | None = None,
) -> TableResult:
    """Render an <a:tbl> at the given absolute xfrm into SVG markup."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .tbl_to_svg import (
        TableResult,
        _border_line,
        _build_cell_grid,
        _canonical_native_merge_status,
        _column_widths_px,
        _convert_cell_text,
        _cumulative_starts,
        _effective_cell_fill,
        _native_table_payload,
        _resolve_table_style,
        _row_height_px,
        _table_has_unsupported_direct_formatting,
        _table_has_unsupported_style,
        _table_text_run_props,
    )
    grad_seq = grad_seq if grad_seq is not None else [0]
    marker_seq = marker_seq if marker_seq is not None else [0]

    col_widths_px = _column_widths_px(tbl)
    if not col_widths_px:
        message = "Source table has no column grid; cell geometry cannot be recovered"
        if strict:
            raise ValueError(message)
        if diagnostic_sink is not None:
            diagnostic_sink("table-geometry-omitted", message, "retain the source table fallback without inventing columns")
        return TableResult()
    rows = tbl.findall("a:tr", NS)
    if not rows:
        message = "Source table has no rows; cell geometry cannot be recovered"
        if strict:
            raise ValueError(message)
        if diagnostic_sink is not None:
            diagnostic_sink("table-geometry-omitted", message, "retain the source table fallback without inventing rows")
        return TableResult()
    table_style = _resolve_table_style(tbl, table_styles)
    row_heights_px = [_row_height_px(r) for r in rows]
    grid_topology_invalid = any(
        len(row.findall("a:tc", NS)) != len(col_widths_px)
        for row in rows
    )
    source_geometry_invalid = (
        grid_topology_invalid
        or any(width <= 0 for width in col_widths_px)
        or any(height <= 0 for height in row_heights_px)
        or sum(col_widths_px) <= 0
        or sum(row_heights_px) <= 0
    )
    if source_geometry_invalid:
        message = "Source table has inconsistent cell counts or nonpositive row/column dimensions"
        if strict:
            raise ValueError(message)
        if diagnostic_sink is not None:
            diagnostic_sink(
                "table-geometry-unsupported",
                message,
                "render recoverable cells at source coordinates; cells outside the source grid cannot be placed",
            )

    # PowerPoint's tblGrid widths and tr heights together describe the
    # *intrinsic* table size. The graphicFrame xfrm width/height may differ;
    # if it does, scale rows/columns proportionally so the table fills the
    # frame the way PowerPoint renders it.
    intrinsic_w = sum(col_widths_px) or xfrm.w
    intrinsic_h = sum(row_heights_px) or xfrm.h
    sx = (xfrm.w / intrinsic_w) if intrinsic_w else 1.0
    sy = (xfrm.h / intrinsic_h) if intrinsic_h else 1.0
    col_widths = [w * sx for w in col_widths_px]
    row_heights = [h * sy for h in row_heights_px]

    col_lefts = _cumulative_starts(xfrm.x, col_widths)
    row_tops = _cumulative_starts(xfrm.y, row_heights)

    # First pass: resolve merge state so spanned cells get the union geometry
    # and dropped cells don't render anything. PowerPoint expresses merges via
    # gridSpan/rowSpan on the anchor cell + hMerge/vMerge on the dropped cells.
    cells = _build_cell_grid(rows, len(col_widths))
    merge_status = _canonical_native_merge_status(rows, len(col_widths))
    effect_reason = (
        "unsupported-run-effect-route:table-cell-text"
        if any(
            txbody_has_run_effects(tc.find("a:txBody", NS))
            for tc in tbl.findall(".//a:tc", NS)
        )
        else None
    )
    if xfrm.rot or xfrm.flip_h or xfrm.flip_v:
        native_status = "unsupported-native-transform"
    elif merge_status:
        native_status = merge_status
    elif len(rows) > 1000 or len(col_widths) > 1000:
        native_status = "unsupported-table-size"
    elif source_geometry_invalid or (
        any(width <= 0 for width in col_widths)
        or any(height <= 0 for height in row_heights)
        or sum(col_widths) <= 0
        or sum(row_heights) <= 0
    ):
        native_status = "unsupported-table-geometry"
    elif _table_has_unsupported_style(tbl):
        native_status = "unsupported-table-style"
    elif (
        effect_reason
        or _table_has_unsupported_direct_formatting(tbl, palette)
    ):
        native_status = "unsupported-table-direct-formatting"
    else:
        native_status = None
    native_payload = (
        None if native_status else _native_table_payload(
            tbl,
            xfrm,
            col_widths,
            row_heights,
            cells,
            palette,
            theme_fonts or {},
        )
    )

    body_parts: list[str] = []
    defs: list[str] = []

    # Pass A: cell backgrounds.
    for r, row_cells in enumerate(cells):
        for c, cell in enumerate(row_cells):
            if cell is None or cell.is_dropped:
                continue
            rect_x = col_lefts[c]
            rect_y = row_tops[r]
            rect_w = sum(col_widths[c:c + cell.col_span])
            rect_h = sum(row_heights[r:r + cell.row_span])
            tcPr = cell.element.find("a:tcPr", NS)
            fill = _effective_cell_fill(
                tcPr, table_style, r, palette,
                id_prefix=f"{id_prefix}fill",
                id_seq=grad_seq,
            )
            defs.extend(fill.defs)
            attrs = fill.attrs or {"fill": "none"}
            attr_str = "".join(f' {k}="{v}"' for k, v in attrs.items())
            body_parts.append(
                f'<rect x="{fmt_num(rect_x)}" y="{fmt_num(rect_y)}" '
                f'width="{fmt_num(rect_w)}" height="{fmt_num(rect_h)}"'
                f'{attr_str}/>'
            )

    # Pass B: cell text. Cell xfrm uses default tcPr insets if none specified.
    for r, row_cells in enumerate(cells):
        for c, cell in enumerate(row_cells):
            if cell is None or cell.is_dropped:
                continue
            tx_body = cell.element.find("a:txBody", NS)
            if tx_body is None:
                continue
            tcPr = cell.element.find("a:tcPr", NS)
            cell_x = col_lefts[c]
            cell_y = row_tops[r]
            cell_w = sum(col_widths[c:c + cell.col_span])
            cell_h = sum(row_heights[r:r + cell.row_span])
            cell_xfrm = Xfrm(x=cell_x, y=cell_y, w=cell_w, h=cell_h)
            text_result = _convert_cell_text(
                tx_body, tcPr, cell_xfrm, palette, theme_fonts,
                fallback_run_props=_table_text_run_props(
                    table_style, r, theme_fonts or {},
                ),
                slide_number=slide_number,
                id_prefix=f"{id_prefix}txt",
                id_seq=grad_seq,
                hyperlink_resolver=hyperlink_resolver,
                strict=strict,
                diagnostic_sink=diagnostic_sink,
            )
            defs.extend(text_result.defs)
            if text_result.svg:
                body_parts.append(text_result.svg)

    # Pass C: cell borders. Drawn last so they appear on top of fills/text.
    for r, row_cells in enumerate(cells):
        for c, cell in enumerate(row_cells):
            if cell is None or cell.is_dropped:
                continue
            cell_x = col_lefts[c]
            cell_y = row_tops[r]
            cell_w = sum(col_widths[c:c + cell.col_span])
            cell_h = sum(row_heights[r:r + cell.row_span])
            tcPr = cell.element.find("a:tcPr", NS)
            for tag, x1, y1, x2, y2 in (
                ("a:lnT", cell_x, cell_y, cell_x + cell_w, cell_y),
                ("a:lnR", cell_x + cell_w, cell_y, cell_x + cell_w, cell_y + cell_h),
                ("a:lnB", cell_x, cell_y + cell_h, cell_x + cell_w, cell_y + cell_h),
                ("a:lnL", cell_x, cell_y, cell_x, cell_y + cell_h),
            ):
                line_xml = _border_line(
                    tcPr, table_style, r, c, len(rows), len(col_widths), tag,
                    x1, y1, x2, y2, palette,
                    id_prefix=f"{id_prefix}stk", id_seq=marker_seq, defs=defs,
                )
                if line_xml:
                    body_parts.append(line_xml)

    return TableResult(
        svg="\n".join(body_parts),
        defs=defs,
        native_payload=native_payload,
        native_status=native_status,
        effect_reason=effect_reason,
    )


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

