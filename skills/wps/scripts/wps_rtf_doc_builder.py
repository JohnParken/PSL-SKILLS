#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build validated RtfDocument instances from declarative JSON specifications."""

import math
from typing import Any, Dict

from wps_rtf_builder import (
    FONT_FANGSONG,
    FONT_KAITI,
    FONT_MAP,
    FONT_SIMHEI,
    FONT_SIMSUN,
    FONT_TIMES,
    FONT_XIAOBIAOSONG,
    FONT_YAHEI,
    RtfDocument,
    RtfParagraph,
    RtfTable,
    RtfTableCell,
    RtfTableRow,
    normalized_merges,
    resolve_font_index,
)

_PARAGRAPH_OPTIONS = {
    "align": "align",
    "space_before_pt": "space_before_pt",
    "space_after_pt": "space_after_pt",
    "indent_chars": "first_line_indent_chars",
    "line_spacing_exact_pt": "line_spacing_exact_pt",
    "line_spacing_mult": "line_spacing_mult",
    "widow_control": "widow_control",
    "keep_with_next": "keep_with_next",
    "keep_together": "keep_together",
    "page_break_before": "page_break_before",
    "left_indent_pt": "left_indent_pt",
    "right_indent_pt": "right_indent_pt",
    "first_line_indent_pt": "first_line_indent_pt",
    "tabs_pt": "tabs_pt",
}
_BLOCK_OPTIONS = {
    "title": {"type", "text"} | set(_PARAGRAPH_OPTIONS),
    "subtitle": {"type", "text"} | set(_PARAGRAPH_OPTIONS),
    "heading": {"type", "text", "level"} | set(_PARAGRAPH_OPTIONS),
    "paragraph": {"type", "text", "runs", *set(_PARAGRAPH_OPTIONS)},
    "list": {"type", "items", "ordered", "start"},
    "table": {"type", "rows", "header", "col_widths", "col_alignments", "repeat_header",
              "row_keep_together", "merges", "borders", "border_width_pt"},
    "callout": {"type", "text"},
    "page_break": {"type"},
}


def _warn_unknown(doc: RtfDocument, value: dict, supported: set, path: str) -> None:
    for key in value:
        if key not in supported:
            doc.spec_warning_count += 1
            if len(doc.spec_warnings) < 100:
                doc.spec_warnings.append({"code": "unsupported_option_ignored", "path": path + str(key)})


def _validate_spec(spec: Dict[str, Any]) -> None:
    if not isinstance(spec, dict) or not isinstance(spec.get("blocks"), list) or not spec["blocks"]:
        raise ValueError("A nonempty blocks array is required")
    if spec.get("theme", "official") not in ("official", "business", "general"):
        raise ValueError("Unsupported theme")
    if "page_numbering" in spec and type(spec["page_numbering"]) is not bool:
        raise ValueError("page_numbering must be boolean")
    for block in spec["blocks"]:
        if not isinstance(block, dict) or block.get("type") not in _BLOCK_OPTIONS:
            raise ValueError("Unsupported block")
        if "text" in block and not isinstance(block["text"], str):
            raise ValueError("Block text must be a string")
        if block.get("type") == "table":
            rows = block.get("rows")
            if (not isinstance(rows, list) or not rows
                    or not all(isinstance(row, list) and row for row in rows)
                    or len({len(row) for row in rows}) != 1):
                raise ValueError("Tables require nonempty rectangular rows")
        if "align" in block and block["align"] not in ("left", "right", "center", "justify"):
            raise ValueError("Unsupported alignment")
        if block.get("type") == "heading" and (type(block.get("level", 1)) is not int or not 1 <= block.get("level", 1) <= 9):
            raise ValueError("heading level must be 1..9")
        tabs = block.get("tabs_pt", [])
        if not isinstance(tabs, list) or len(tabs) > 64:
            raise ValueError("tabs_pt must be a list of at most 64 left-tab positions")
        if any(type(t) not in (int, float) or not math.isfinite(t) or not 0 < t <= 1000 for t in tabs):
            raise ValueError("Tab positions must be finite positive points")
        if any(a >= b for a, b in zip(tabs, tabs[1:])):
            raise ValueError("Tab positions must be strictly increasing")
        if any(key in block for key in ("list_style", "list_level", "numbering")):
            raise ValueError("Native multilevel/automatic list numbering is unsupported; use literal lists")
        if block.get("type") == "list" and (type(block.get("start", 1)) is not int or not 1 <= block.get("start", 1) <= 1000000):
            raise ValueError("List start must be an integer from 1 to 1000000")
        for key in ("row_keep_together", "repeat_header", "header", "ordered", "widow_control", "keep_with_next", "keep_together", "page_break_before"):
            if key in block and type(block[key]) is not bool:
                raise ValueError("Expected boolean paragraph/table option")
        for run in block.get("runs", []):
            if not isinstance(run, dict) or not isinstance(run.get("text", ""), str):
                raise ValueError("Invalid text run")
            size = run.get("size_pt", 14)
            if type(size) not in (int, float) or not math.isfinite(size) or not 1 <= size <= 200:
                raise ValueError("Invalid run font size")
        for key in ("space_before_pt", "space_after_pt", "indent_chars", "line_spacing_exact_pt",
                    "line_spacing_mult", "left_indent_pt", "right_indent_pt", "first_line_indent_pt"):
            if key in ("line_spacing_exact_pt", "line_spacing_mult") and block.get(key, 0) is None:
                continue
            if key in block and (type(block[key]) not in (int, float) or not math.isfinite(block[key]) or abs(block[key]) > 1000):
                raise ValueError("Invalid paragraph measurement")
            if key in block and key not in ("indent_chars", "first_line_indent_pt") and block[key] < 0:
                raise ValueError("Spacing must be nonnegative")


def _create_themed_document(spec: Dict[str, Any]):
    theme = spec.get("theme", "official")
    header_text = spec.get("header_text") or spec.get("header")
    footer_text = spec.get("footer_text") or spec.get("footer")
    page_numbering = spec.get("page_numbering", theme != "official")
    latin_font = FONT_TIMES if theme == "official" else None
    if theme == "official":
        doc = RtfDocument(
            margin_top_pt=72.0, margin_bottom_pt=72.0, margin_left_pt=90.15, margin_right_pt=90.15,
            header_text=spec.get("header_text") if spec.get("header_text") is not None else spec.get("header"),
            footer_text=spec.get("footer_text") if spec.get("footer_text") is not None else spec.get("footer"),
            page_numbering=page_numbering, official_typography=True,
        )
        style = {
            "theme": theme, "latin_font": latin_font,
            "title_font": FONT_XIAOBIAOSONG, "title_size": 22.0,
            "h1": (FONT_SIMHEI, 16.0), "h2": (FONT_KAITI, 16.0), "h3": (FONT_FANGSONG, 16.0),
            "body_font": FONT_FANGSONG, "body_size": 16.0, "body_indent": 2.0,
            "line_spacing_exact": 28.0, "line_spacing_mult": None,
        }
    elif theme == "business":
        doc = RtfDocument(
            margin_top_pt=72.0, margin_bottom_pt=72.0, margin_left_pt=72.0, margin_right_pt=72.0,
            header_text=header_text, footer_text=footer_text, page_numbering=page_numbering,
        )
        style = {
            "theme": theme, "latin_font": latin_font,
            "title_font": FONT_YAHEI, "title_size": 22.0,
            "h1": (FONT_YAHEI, 16.0), "h2": (FONT_YAHEI, 14.0), "h3": (FONT_YAHEI, 12.0),
            "body_font": FONT_YAHEI, "body_size": 11.0, "body_indent": 0.0,
            "line_spacing_exact": None, "line_spacing_mult": 1.25,
        }
    else:
        doc = RtfDocument(
            margin_top_pt=56.7, margin_bottom_pt=56.7, margin_left_pt=56.7, margin_right_pt=56.7,
            header_text=header_text, footer_text=footer_text, page_numbering=page_numbering,
        )
        style = {
            "theme": theme, "latin_font": latin_font,
            "title_font": FONT_SIMHEI, "title_size": 20.0,
            "h1": (FONT_SIMHEI, 15.0), "h2": (FONT_KAITI, 13.0), "h3": (FONT_SIMSUN, 12.0),
            "body_font": FONT_SIMSUN, "body_size": 12.0, "body_indent": 2.0,
            "line_spacing_exact": None, "line_spacing_mult": 1.2,
        }
    return doc, style


def _apply_page_spec(doc: RtfDocument, spec: Dict[str, Any]) -> None:
    _warn_unknown(doc, spec, {"theme", "header_text", "header", "footer_text", "footer", "page_numbering", "blocks", "page"}, "")
    page = spec.get("page", {})
    if not isinstance(page, dict):
        raise ValueError("page must be an object")
    _warn_unknown(doc, page, {"width_pt", "height_pt", "margin_left_pt", "margin_right_pt", "margin_top_pt", "margin_bottom_pt"}, "page.")
    for key, attribute in {
        "width_pt": "page_width_twips", "height_pt": "page_height_twips",
        "margin_left_pt": "margin_left_twips", "margin_right_pt": "margin_right_twips",
        "margin_top_pt": "margin_top_twips", "margin_bottom_pt": "margin_bottom_twips",
    }.items():
        if key in page:
            value = page[key]
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 2000:
                raise ValueError("Invalid page measurement")
            setattr(doc, attribute, int(value * 20))
    if (doc.page_width_twips <= doc.margin_left_twips + doc.margin_right_twips
            or doc.page_height_twips <= doc.margin_top_twips + doc.margin_bottom_twips):
        raise ValueError("Page margins leave no printable area")


def _apply_heading_options(paragraph: RtfParagraph, block: dict) -> None:
    for option, attribute in _PARAGRAPH_OPTIONS.items():
        if option in block:
            setattr(paragraph, attribute, block[option])


def _resolve_run_font(doc: RtfDocument, name, path: str) -> int:
    if name is not None and not isinstance(name, str):
        raise ValueError("Run font must be a string")
    if name and name.strip().lower() not in FONT_MAP:
        doc.spec_warning_count += 1
        if len(doc.spec_warnings) < 100:
            doc.spec_warnings.append({"code": "unsupported_font_substituted", "path": path, "replacement": "SimSun"})
    return resolve_font_index(name)


def _append_table_block(doc: RtfDocument, block: dict, style: dict) -> None:
    theme = style["theme"]
    latin_font = style["latin_font"]
    rows_data = block.get("rows", [])
    has_header = bool(block.get("header", True))
    col_widths = block.get("col_widths")
    table = RtfTable()
    num_cols = max(len(r) for r in rows_data) if rows_data else 1
    available_width = doc.page_width_twips - doc.margin_left_twips - doc.margin_right_twips
    default_cell_w = available_width // num_cols
    if default_cell_w < 1:
        raise ValueError("Too many table columns for the printable width")
    if col_widths and (len(col_widths) != num_cols or any(type(w) not in (int, float) or not math.isfinite(w) or w < 0.05 for w in col_widths)
                       or sum(int(w * 20) for w in col_widths) > available_width):
        raise ValueError("Column widths must fit the page and match the table")
    col_alignments = block.get("col_alignments", ["left"] * num_cols)
    if (not isinstance(col_alignments, list) or len(col_alignments) != num_cols
            or any(a not in ("left", "center", "right", "justify") for a in col_alignments)):
        raise ValueError("col_alignments must match columns and use supported paragraph alignments")
    border_mode = block.get("borders", "all")
    if type(border_mode) is bool:
        border_mode = "all" if border_mode else "none"
    if border_mode not in ("all", "none", "outer"):
        raise ValueError("borders must be all, none or outer")
    border_width = block.get("border_width_pt", 0.5)
    if type(border_width) not in (int, float) or not math.isfinite(border_width) or not 0.05 <= border_width <= 12:
        raise ValueError("border_width_pt must be finite and between 0.05 and 12")
    merge_cells = normalized_merges(rows_data, block.get("merges", []), has_header and block.get("repeat_header", True))
    for r_idx, row_values in enumerate(rows_data):
        is_hdr = has_header and (r_idx == 0)
        row = RtfTableRow(is_header=is_hdr and block.get("repeat_header", True), keep_with_next=is_hdr,
                          keep_together=block.get("row_keep_together", True if theme == "official" else is_hdr))
        for c_idx, val in enumerate(row_values):
            c_width = int(col_widths[c_idx] * 20) if col_widths and c_idx < len(col_widths) else default_cell_w
            edges = True if border_mode == "all" else []
            if border_mode == "outer":
                edges = (["top"] if r_idx == 0 else []) + (["bottom"] if r_idx == len(rows_data) - 1 else [])
                edges += (["left"] if c_idx == 0 else []) + (["right"] if c_idx == num_cols - 1 else [])
            merge = merge_cells.get((r_idx, c_idx))
            cell = RtfTableCell(
                width_twips=c_width, borders=edges, border_width_pt=border_width,
                hmerge=merge[0] if merge else None, vmerge=merge[1] if merge else None,
                bg_color_idx=6 if is_hdr and theme != "official" else 0, valign="center",
            )
            if theme == "official":
                paragraph = RtfParagraph(align="center" if is_hdr else col_alignments[c_idx], first_line_indent_chars=0,
                                         line_spacing_mult=1.15, widow_control=None)
                paragraph.add_text(str(val), font=FONT_FANGSONG, size_pt=16, bold=is_hdr, latin_font=latin_font)
                cell.paragraphs.append(paragraph)
            else:
                cell.add_text(str(val), font=FONT_SIMHEI if is_hdr else FONT_SIMSUN, size_pt=10.5,
                              align="center" if is_hdr else col_alignments[c_idx], bold=is_hdr)
            row.add_cell(cell)
        table.add_row(row)
    doc.add_table(table)


def _append_block(doc: RtfDocument, block_index: int, block: dict, style: dict) -> None:
    theme = style["theme"]
    latin_font = style["latin_font"]
    b_type = block.get("type", "paragraph")
    if b_type == "title":
        p = RtfParagraph(
            align="center", space_before_pt=0.0 if theme == "official" else 14.0, space_after_pt=12.0,
            first_line_indent_chars=0.0, keep_with_next=True,
            line_spacing_mult=1.15 if theme == "official" else None, widow_control=None if theme == "official" else True,
        )
        _apply_heading_options(p, block)
        p.add_text(block.get("text", ""), font=style["title_font"], size_pt=style["title_size"],
                   bold=theme != "official", latin_font=latin_font)
        doc.add_paragraph(p)
    elif b_type == "subtitle":
        p = RtfParagraph(
            align="center", space_before_pt=0.0, space_after_pt=8.0 if theme == "official" else 12.0,
            first_line_indent_chars=0.0, keep_with_next=True,
            line_spacing_mult=1.15 if theme == "official" else None, widow_control=None if theme == "official" else True,
        )
        _apply_heading_options(p, block)
        p.add_text(block.get("text", ""), font=FONT_XIAOBIAOSONG if theme == "official" else FONT_KAITI,
                   size_pt=14.0 if theme == "official" else 16.0, bold=False, latin_font=latin_font)
        doc.add_paragraph(p)
    elif b_type == "heading":
        level = int(block.get("level", 1))
        text = block.get("text", "")
        if theme == "official":
            font, size = {1: style["h1"], 2: style["h2"], 3: style["h3"]}.get(level, (FONT_FANGSONG, 16.0))
            p = RtfParagraph(align="left", first_line_indent_chars=0, line_spacing_mult=1.15, keep_with_next=True, widow_control=None)
            p.add_text(text, font=font, size_pt=size, bold=False, latin_font=latin_font)
        else:
            spec_by_level = {1: (10.0, 4.0, style["h1"]), 2: (8.0, 3.0, style["h2"])}
            before, after, (font, size) = spec_by_level.get(level, (6.0, 2.0, style["h3"]))
            p = RtfParagraph(align="left", space_before_pt=before, space_after_pt=after, first_line_indent_chars=0.0, keep_with_next=True)
            p.add_text(text, font=font, size_pt=size, bold=True)
        p.outline_level = level - 1
        _apply_heading_options(p, block)
        doc.add_paragraph(p)
    elif b_type == "paragraph":
        p = RtfParagraph(
            align=block.get("align", "left" if theme == "official" else "justify"),
            space_before_pt=block.get("space_before_pt", 0.0),
            space_after_pt=block.get("space_after_pt", 0.0 if theme == "official" else 4.0),
            first_line_indent_chars=block.get("indent_chars", style["body_indent"]),
            line_spacing_exact_pt=block.get("line_spacing_exact_pt", None if "line_spacing_mult" in block else style["line_spacing_exact"]),
            line_spacing_mult=block.get("line_spacing_mult", style["line_spacing_mult"]),
            widow_control=block.get("widow_control", None if theme == "official" else True),
            keep_with_next=block.get("keep_with_next", False), keep_together=block.get("keep_together", False),
            page_break_before=block.get("page_break_before", False), left_indent_pt=block.get("left_indent_pt", 0),
            right_indent_pt=block.get("right_indent_pt", 0), first_line_indent_pt=block.get("first_line_indent_pt"),
            tabs_pt=block.get("tabs_pt", []), character_indent=theme == "official",
        )
        runs_data = block.get("runs")
        if runs_data and isinstance(runs_data, list):
            for run_index, r in enumerate(runs_data):
                p.add_text(
                    r.get("text", ""),
                    font=_resolve_run_font(doc, r.get("font"), f"blocks[{block_index}].runs[{run_index}].font"),
                    size_pt=r.get("size_pt", style["body_size"]),
                    bold=bool(r.get("bold", False)), italic=bool(r.get("italic", False)),
                    underline=bool(r.get("underline", False)), color_idx=int(r.get("color_idx", 0)),
                    latin_font=latin_font if not r.get("font") else None,
                )
        else:
            p.add_text(block.get("text", ""), font=style["body_font"], size_pt=style["body_size"], latin_font=latin_font)
        doc.add_paragraph(p)
    elif b_type == "list":
        ordered = bool(block.get("ordered", False))
        for idx, item_text in enumerate(block.get("items", []), start=block.get("start", 1)):
            p = RtfParagraph(
                align="left", space_before_pt=0.0 if theme == "official" else 1.0, space_after_pt=0.0 if theme == "official" else 2.0,
                first_line_indent_chars=0.0, left_indent_pt=36.0 if theme == "official" else 18.0,
                first_line_indent_pt=-18.0 if theme == "official" else None, tabs_pt=[36.0] if theme == "official" else [],
                line_spacing_mult=1.15 if theme == "official" else 1.2, widow_control=None if theme == "official" else True,
            )
            prefix = f"{idx}. " if ordered else "• "
            p.add_text(prefix, font=style["body_font"], size_pt=style["body_size"], bold=ordered and theme != "official", latin_font=latin_font)
            p.add_text(str(item_text), font=style["body_font"], size_pt=style["body_size"], latin_font=latin_font)
            doc.add_paragraph(p)
    elif b_type == "table":
        _append_table_block(doc, block, style)
    elif b_type == "callout":
        p = RtfParagraph(align="left", space_before_pt=4.0, space_after_pt=4.0, first_line_indent_chars=0.0,
                         left_indent_pt=14.0, right_indent_pt=14.0, line_spacing_mult=1.2)
        p.add_text(block.get("text", ""), font=FONT_KAITI, size_pt=11.0, italic=True)
        doc.add_paragraph(p)
    elif b_type == "page_break":
        doc.add_page_break()


def build_document_from_spec(spec: Dict[str, Any]) -> RtfDocument:
    """Build an RtfDocument instance from a declarative document specification."""
    _validate_spec(spec)
    doc, style = _create_themed_document(spec)
    _apply_page_spec(doc, spec)
    for block_index, block in enumerate(spec.get("blocks", [])):
        _warn_unknown(doc, block, _BLOCK_OPTIONS[block["type"]], f"blocks[{block_index}].")
        _append_block(doc, block_index, block, style)
    return doc
