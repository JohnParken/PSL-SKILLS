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

def _column_widths_px(tbl: ET.Element) -> list[float]:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .tbl_to_svg import (
        _safe_emu_integer,
    )
    grid = tbl.find("a:tblGrid", NS)
    if grid is None:
        return []
    widths: list[float] = []
    for col in grid.findall("a:gridCol", NS):
        w_emu = col.attrib.get("w")
        if w_emu is None:
            widths.append(0.0)
            continue
        value = _safe_emu_integer(w_emu)
        widths.append(emu_to_px(value) if value is not None else 0.0)
    return widths


def _row_height_px(row: ET.Element) -> float:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .tbl_to_svg import (
        _safe_emu_integer,
    )
    h_emu = row.attrib.get("h")
    if h_emu is None:
        return 0.0
    value = _safe_emu_integer(h_emu)
    return emu_to_px(value) if value is not None else 0.0


def _safe_emu_integer(raw_value: str) -> int | None:
    """Return a bounded ASCII DrawingML coordinate without float overflow."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .tbl_to_svg import (
        _POWERPOINT_COORD_MAX,
        _POWERPOINT_COORD_MIN,
    )
    token = raw_value.strip(" \t\r\n")
    digits = token[1:] if token.startswith("-") else token
    if (
        not digits
        or not digits.isascii()
        or not digits.isdigit()
        or len(digits) > 10
    ):
        return None
    value = int(token)
    if not _POWERPOINT_COORD_MIN <= value <= _POWERPOINT_COORD_MAX:
        return None
    return value


def _cumulative_starts(origin: float, sizes: list[float]) -> list[float]:
    out = [origin]
    acc = origin
    for size in sizes[:-1]:
        acc += size
        out.append(acc)
    return out


# ---------------------------------------------------------------------------
# Cell grid
# ---------------------------------------------------------------------------

def _build_cell_grid(rows: list[ET.Element], col_count: int) -> list[list[_CellSlot | None]]:
    """Map each (row, col) to the <a:tc> that owns it.

    Anchor cells (the top-left of a merge) carry col_span/row_span; merged
    slaves are marked is_dropped so the renderer skips them. Cells not part
    of any merge get span 1×1.
    """
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .tbl_to_svg import (
        _CellSlot,
        _safe_int,
    )
    grid: list[list[_CellSlot | None]] = [[None] * col_count for _ in rows]

    for r, row in enumerate(rows):
        row_cells = row.findall("a:tc", NS)
        # PowerPoint writes one physical <a:tc> for every grid column, including
        # explicit hMerge/vMerge continuation cells. In that canonical form the
        # physical index is the grid column; advancing by gridSpan would consume
        # the continuation cell twice and shift every following cell left.
        explicit_grid = len(row_cells) >= col_count
        c = 0
        for physical_col, tc in enumerate(row_cells):
            if explicit_grid:
                c = physical_col
            else:
                # Retain best-effort support for compact/non-canonical rows that
                # omit explicit merge continuation cells.
                while c < col_count and grid[r][c] is not None:
                    c += 1
            if c >= col_count:
                break

            grid_span = _safe_int(tc.attrib.get("gridSpan"), 1)
            row_span = _safe_int(tc.attrib.get("rowSpan"), 1)
            h_merge = ooxml_bool(tc.attrib.get("hMerge"))
            v_merge = ooxml_bool(tc.attrib.get("vMerge"))

            if h_merge or v_merge:
                # Merge slaves are physical cells but have no independent paint.
                grid[r][c] = _CellSlot(element=tc, is_dropped=True)
                c += 1
                continue

            slot = _CellSlot(
                element=tc,
                col_span=max(grid_span, 1),
                row_span=max(row_span, 1),
            )
            for dr in range(slot.row_span):
                for dc in range(slot.col_span):
                    rr = r + dr
                    cc = c + dc
                    if rr >= len(rows) or cc >= col_count:
                        continue
                    if dr == 0 and dc == 0:
                        grid[rr][cc] = slot
                    else:
                        grid[rr][cc] = _CellSlot(
                            element=tc, is_dropped=True,
                        )
            c += 1 if explicit_grid else slot.col_span

    return grid


def _safe_int(value: str | None, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _strict_merge_bool(value: str | None) -> bool:
    if value is None:
        return False
    normalized = value.strip().lower()
    if normalized in {"1", "on", "true"}:
        return True
    if normalized in {"0", "false", "off"}:
        return False
    raise ValueError("invalid OOXML boolean")


def _strict_merge_span(value: str | None) -> int:
    if value is None:
        return 1
    normalized = value.strip()
    if not normalized.isdigit():
        raise ValueError("invalid OOXML span")
    span = int(normalized)
    if span <= 0:
        raise ValueError("invalid OOXML span")
    return span


def _canonical_native_merge_status(
    rows: list[ET.Element],
    col_count: int,
) -> str | None:
    """Accept only explicit rectangular merge topology safe for regeneration."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .tbl_to_svg import (
        _CanonicalMergeRegion,
        _strict_merge_bool,
        _strict_merge_span,
    )
    physical_rows = [row.findall("a:tc", NS) for row in rows]
    merge_attrs = {"gridSpan", "rowSpan", "hMerge", "vMerge"}
    if not any(
        any(name in tc.attrib for name in merge_attrs)
        for row_cells in physical_rows
        for tc in row_cells
    ):
        return None
    if col_count <= 0 or any(
        len(row_cells) != col_count for row_cells in physical_rows
    ):
        return "unsupported-merge-topology"

    states: dict[tuple[int, int], tuple[ET.Element, int, int, bool, bool]] = {}
    try:
        for row_idx, row_cells in enumerate(physical_rows):
            for col_idx, tc in enumerate(row_cells):
                states[(row_idx, col_idx)] = (
                    tc,
                    _strict_merge_span(tc.get("rowSpan")),
                    _strict_merge_span(tc.get("gridSpan")),
                    _strict_merge_bool(tc.get("hMerge")),
                    _strict_merge_bool(tc.get("vMerge")),
                )
    except ValueError:
        return "unsupported-merge-topology"

    anchors: list[_CanonicalMergeRegion] = []
    for (
        (row_idx, col_idx),
        (_tc, row_span, col_span, h_merge, v_merge),
    ) in states.items():
        if not h_merge and not v_merge and (row_span > 1 or col_span > 1):
            anchors.append(
                _CanonicalMergeRegion(row_idx, col_idx, row_span, col_span)
            )

    owners: dict[tuple[int, int], _CanonicalMergeRegion] = {}
    for region in anchors:
        if (
            region.row + region.row_span > len(rows)
            or region.col + region.col_span > col_count
        ):
            return "unsupported-merge-topology"
        for covered_row in range(region.row, region.row + region.row_span):
            for covered_col in range(region.col, region.col + region.col_span):
                position = (covered_row, covered_col)
                if position in owners:
                    return "unsupported-merge-topology"
                owners[position] = region

    for (
        (row_idx, col_idx),
        (tc, row_span, col_span, h_merge, v_merge),
    ) in states.items():
        region = owners.get((row_idx, col_idx))
        if region is None:
            if h_merge or v_merge or row_span != 1 or col_span != 1:
                return "unsupported-merge-topology"
            continue

        is_anchor = row_idx == region.row and col_idx == region.col
        expected_row_span = region.row_span if row_idx == region.row else 1
        expected_col_span = region.col_span if col_idx == region.col else 1
        if (
            row_span != expected_row_span
            or col_span != expected_col_span
            or h_merge != (col_idx > region.col)
            or v_merge != (row_idx > region.row)
        ):
            return "unsupported-merge-topology"
    return None


def _table_has_unsupported_style(tbl: ET.Element) -> bool:
    tbl_pr = tbl.find("a:tblPr", NS)
    if tbl_pr is None:
        return False
    allowed_attrs = {
        "firstRow", "bandRow", "firstCol", "lastCol", "lastRow",
        "bandCol", "rtl",
    }
    if any(name not in allowed_attrs for name in tbl_pr.attrib):
        return True
    if any(
        ooxml_bool(tbl_pr.attrib.get(name))
        for name in ("firstCol", "lastCol", "lastRow", "bandCol", "rtl")
    ):
        return True
    for child in tbl_pr:
        name = child.tag.rsplit("}", 1)[-1]
        if name == "tableStyleId":
            continue
        if name == "effectLst" and not child.attrib and not list(child):
            continue
        return True
    return False


