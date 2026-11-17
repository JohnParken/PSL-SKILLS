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


BUILTIN_MEDIUM_STYLE_2_ACCENT_1 = "{5C22544A-7EE6-4342-B048-85BDC9FD1C3A}"
_POWERPOINT_COORD_MIN = -(2**31)
_POWERPOINT_COORD_MAX = 2**31 - 1

_BUILTIN_MEDIUM_STYLE_2_ACCENT_1_XML = ET.fromstring(
    f'''<a:tblStyle xmlns:a="{NS["a"]}"
        styleId="{BUILTIN_MEDIUM_STYLE_2_ACCENT_1}">
      <a:wholeTbl>
        <a:tcTxStyle>
          <a:fontRef idx="minor"><a:prstClr val="black"/></a:fontRef>
          <a:schemeClr val="dk1"/>
        </a:tcTxStyle>
        <a:tcStyle>
          <a:tcBdr>
            <a:left><a:ln w="12700"><a:solidFill><a:schemeClr val="lt1"/></a:solidFill></a:ln></a:left>
            <a:right><a:ln w="12700"><a:solidFill><a:schemeClr val="lt1"/></a:solidFill></a:ln></a:right>
            <a:top><a:ln w="12700"><a:solidFill><a:schemeClr val="lt1"/></a:solidFill></a:ln></a:top>
            <a:bottom><a:ln w="12700"><a:solidFill><a:schemeClr val="lt1"/></a:solidFill></a:ln></a:bottom>
            <a:insideH><a:ln w="12700"><a:solidFill><a:schemeClr val="lt1"/></a:solidFill></a:ln></a:insideH>
            <a:insideV><a:ln w="12700"><a:solidFill><a:schemeClr val="lt1"/></a:solidFill></a:ln></a:insideV>
          </a:tcBdr>
          <a:fill><a:solidFill><a:schemeClr val="accent1"><a:tint val="20000"/></a:schemeClr></a:solidFill></a:fill>
        </a:tcStyle>
      </a:wholeTbl>
      <a:band1H>
        <a:tcStyle><a:fill><a:solidFill><a:schemeClr val="accent1"><a:tint val="40000"/></a:schemeClr></a:solidFill></a:fill></a:tcStyle>
      </a:band1H>
      <a:band2H><a:tcStyle/></a:band2H>
      <a:firstRow>
        <a:tcTxStyle b="on">
          <a:fontRef idx="minor"><a:prstClr val="black"/></a:fontRef>
          <a:schemeClr val="lt1"/>
        </a:tcTxStyle>
        <a:tcStyle>
          <a:tcBdr><a:bottom><a:ln w="38100"><a:solidFill><a:schemeClr val="lt1"/></a:solidFill></a:ln></a:bottom></a:tcBdr>
          <a:fill><a:solidFill><a:schemeClr val="accent1"/></a:solidFill></a:fill>
        </a:tcStyle>
      </a:firstRow>
    </a:tblStyle>'''
)


@dataclass
class TableResult:
    """Composite render output, replacement metadata, and effect diagnostics."""

    svg: str = ""
    defs: list[str] = None
    native_payload: dict[str, Any] | None = None
    native_status: str | None = None
    effect_reason: str | None = None

    def __post_init__(self) -> None:
        if self.defs is None:
            self.defs = []


@dataclass(frozen=True)
class _TableStyleContext:
    """Small, best-effort view of the table style regions we render."""

    style: ET.Element | None
    table_properties: ET.Element | None

    def regions_for_row(self, row_index: int) -> tuple[tuple[str, ET.Element], ...]:
        if self.style is None:
            return ()

        names: list[str] = []
        first_row = bool(
            self.table_properties is not None
            and ooxml_bool(self.table_properties.get("firstRow"))
        )
        if first_row and row_index == 0:
            names.append("firstRow")
        elif (
            self.table_properties is not None
            and ooxml_bool(self.table_properties.get("bandRow"))
        ):
            band_index = row_index - (1 if first_row else 0)
            names.append("band1H" if band_index % 2 == 0 else "band2H")
        names.append("wholeTbl")

        regions: list[tuple[str, ET.Element]] = []
        for name in names:
            region = self.style.find(f"a:{name}", NS)
            if region is not None:
                regions.append((name, region))
        return tuple(regions)


@dataclass
class _CellSlot:
    """Per-grid-position resolution of <a:tc> attributes."""

    element: ET.Element
    col_span: int = 1
    row_span: int = 1
    is_dropped: bool = False  # True for h/vMerge slaves: don't paint anything


@dataclass(frozen=True)
class _CanonicalMergeRegion:
    row: int
    col: int
    row_span: int
    col_span: int


_DIRECT_BORDER_TAGS = {
    "lnL": "left",
    "lnR": "right",
    "lnT": "top",
    "lnB": "bottom",
    "lnTlToBr": "diagonal_down",
    "lnBlToTr": "diagonal_up",
}
_DIRECT_BORDER_WIDTH_MAX = 20116800

# Stable upstream API facade, split by conversion responsibility.
from .tbl_to_svg_layout import (
    _normalize_table_style_id,
    _resolve_table_style,
    _effective_cell_fill,
    _table_text_run_props,
    _table_tx_style_run_props,
    convert_tbl,
)
from .tbl_to_svg_grid import (
    _column_widths_px,
    _row_height_px,
    _safe_emu_integer,
    _cumulative_starts,
    _build_cell_grid,
    _safe_int,
    _strict_merge_bool,
    _strict_merge_span,
    _canonical_native_merge_status,
    _table_has_unsupported_style,
)
from .tbl_to_svg_validation import (
    _direct_border_payload,
    _table_has_unsupported_direct_formatting,
    _table_cell_has_unsupported_topology,
    _text_body_has_unsupported_formatting,
    _table_run_props_have_unsupported_formatting,
    _native_run_outline_payload,
    _table_text_has_unsupported_outline,
    _legacy_text_body_has_unsupported_formatting,
    _legacy_run_props_have_unsupported_formatting,
    _legacy_effective_run_signature,
)
from .tbl_to_svg_payload import (
    _round_payload_number,
    _native_table_payload,
    _native_cell_payload,
    _cell_plain_text,
    _cell_paragraph_payloads,
    _effective_run_attr,
    _effective_run_child,
    _native_run_font_family,
    _native_run_payload,
    _paragraph_line_spacing_percent,
    _run_style_signature,
)
from .tbl_to_svg_cells import (
    _cell_rich_paragraph_payloads,
    _cell_fill_payload,
    _cell_text_color,
    _cell_font_size_px,
    _canonical_source_font_size_px,
    _cell_align,
    _cell_valign,
    _cell_bold,
    _cell_borders_payload,
    _text_run_props_in_priority,
    _copy_cell_margins,
    _copy_cell_layout_options,
)
from .tbl_to_svg_render import (
    _convert_cell_text,
    _run_props_need_render_normalization,
    _solid_fill_is_unsafe,
    _color_numeric_tokens_are_finite,
    _plain_table_text_body,
    _tcPr_inset_overrides,
    _border_line,
    _table_style_border_names,
    _line_element_to_svg,
    _make_ln_wrapper,
)

