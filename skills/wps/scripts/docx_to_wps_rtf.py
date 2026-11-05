#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Map supported OOXML Word content to RTF .rtf/.wps using Python standard library."""

import os
from pathlib import Path
import sys
from typing import Optional, Dict, Any, List, Tuple
import xml.etree.ElementTree as ET
import zipfile

from wps_rtf_builder import (
    RtfDocument,
    RtfParagraph,
    RtfRun,
    RtfTable,
    RtfTableRow,
    RtfTableCell,
    resolve_font_index,
    FONT_SIMSUN,
    FONT_SIMHEI,
    FONT_FANGSONG,
    FONT_KAITI,
)

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS = {"w": W_NS}


def get_tag_name(element: ET.Element) -> str:
    """Extract local tag name without XML namespace."""
    tag = element.tag
    return tag.split("}")[-1] if "}" in tag else tag


def parse_run(r_elem: ET.Element, default_font_idx: int = FONT_FANGSONG, default_size_pt: float = 14.0) -> Optional[RtfRun]:
    """Parse a w:r element into an RtfRun."""
    r_pr = r_elem.find("w:rPr", NS)
    font_idx = default_font_idx
    size_pt = default_size_pt
    bold = False
    italic = False
    underline = False
    color_idx = 0

    if r_pr is not None:
        # Font resolution
        r_fonts = r_pr.find("w:rFonts", NS)
        if r_fonts is not None:
            font_name = r_fonts.get(f"{{{W_NS}}}eastAsia") or r_fonts.get(f"{{{W_NS}}}ascii")
            if font_name:
                font_idx = resolve_font_index(font_name)

        # Size resolution (w:sz in half-points)
        sz_elem = r_pr.find("w:sz", NS)
        if sz_elem is not None:
            val = sz_elem.get(f"{{{W_NS}}}val")
            if val and val.isdigit():
                size_pt = float(val) / 2.0

        # Bold
        b_elem = r_pr.find("w:b", NS)
        if b_elem is not None:
            val = b_elem.get(f"{{{W_NS}}}val")
            bold = val not in ("0", "false")

        # Italic
        i_elem = r_pr.find("w:i", NS)
        if i_elem is not None:
            val = i_elem.get(f"{{{W_NS}}}val")
            italic = val not in ("0", "false")

        # Underline
        u_elem = r_pr.find("w:u", NS)
        if u_elem is not None:
            val = u_elem.get(f"{{{W_NS}}}val")
            underline = val not in ("none", "0", "false")

        # Color
        color_elem = r_pr.find("w:color", NS)
        if color_elem is not None:
            c_val = (color_elem.get(f"{{{W_NS}}}val") or "").upper()
            if c_val == "FF0000":
                color_idx = 2  # red
            elif c_val in ("0070C0", "0000FF"):
                color_idx = 3  # blue

    # Collect text content
    text_parts = []
    for child in r_elem:
        tag = get_tag_name(child)
        if tag == "t":
            text_parts.append(child.text or "")
        elif tag == "tab":
            text_parts.append("\t")
        elif tag == "br":
            br_type = child.get(f"{{{W_NS}}}type")
            if br_type != "page":
                text_parts.append("\n")

    full_text = "".join(text_parts)
    if not full_text:
        return None

    return RtfRun(
        text=full_text,
        font=font_idx,
        size_pt=size_pt,
        bold=bold,
        italic=italic,
        underline=underline,
        color_idx=color_idx,
    )


def parse_paragraph(p_elem: ET.Element) -> Tuple[Optional[RtfParagraph], bool]:
    """Parse a w:p element into an RtfParagraph. Returns (paragraph, has_page_break)."""
    p_pr = p_elem.find("w:pPr", NS)
    align = "justify"
    space_before_pt = 0.0
    space_after_pt = 0.0
    line_spacing_mult = None
    line_spacing_exact_pt = None
    first_line_indent_chars = 0.0
    left_indent_pt = 0.0
    right_indent_pt = 0.0
    default_font = FONT_FANGSONG
    default_size = 14.0

    has_page_break = False

    if p_pr is not None:
        # Style hint
        p_style = p_pr.find("w:pStyle", NS)
        if p_style is not None:
            s_val = (p_style.get(f"{{{W_NS}}}val") or "").lower()
            if "title" in s_val:
                align = "center"
                default_font = FONT_SIMHEI
                default_size = 22.0
            elif "heading1" in s_val or s_val == "1":
                align = "left"
                default_font = FONT_SIMHEI
                default_size = 16.0
                space_before_pt = 8.0
            elif "heading2" in s_val or s_val == "2":
                align = "left"
                default_font = FONT_KAITI
                default_size = 14.0
                space_before_pt = 6.0

        # Alignment
        jc = p_pr.find("w:jc", NS)
        if jc is not None:
            jc_val = jc.get(f"{{{W_NS}}}val")
            if jc_val in ("center", "right", "left"):
                align = jc_val
            elif jc_val in ("both", "justify"):
                align = "justify"

        # Spacing
        sp = p_pr.find("w:spacing", NS)
        if sp is not None:
            before = sp.get(f"{{{W_NS}}}before")
            if before and before.isdigit():
                space_before_pt = float(before) / 20.0
            after = sp.get(f"{{{W_NS}}}after")
            if after and after.isdigit():
                space_after_pt = float(after) / 20.0

            line = sp.get(f"{{{W_NS}}}line")
            line_rule = sp.get(f"{{{W_NS}}}lineRule") or "auto"
            if line and line.isdigit():
                val_int = int(line)
                if line_rule == "exact":
                    line_spacing_exact_pt = float(val_int) / 20.0
                elif line_rule == "auto":
                    line_spacing_mult = float(val_int) / 240.0

        # Indentation
        ind = p_pr.find("w:ind", NS)
        if ind is not None:
            fl = ind.get(f"{{{W_NS}}}firstLine")
            flc = ind.get(f"{{{W_NS}}}firstLineChars")
            left = ind.get(f"{{{W_NS}}}left")
            right = ind.get(f"{{{W_NS}}}right")
            if flc and flc.isdigit():
                first_line_indent_chars = float(flc) / 100.0
            elif fl and fl.isdigit():
                first_line_indent_chars = (float(fl) / 20.0) / default_size
            if left and left.isdigit():
                left_indent_pt = float(left) / 20.0
            if right and right.isdigit():
                right_indent_pt = float(right) / 20.0

    para = RtfParagraph(
        align=align,
        space_before_pt=space_before_pt,
        space_after_pt=space_after_pt,
        line_spacing_mult=line_spacing_mult,
        line_spacing_exact_pt=line_spacing_exact_pt,
        first_line_indent_chars=first_line_indent_chars,
        left_indent_pt=left_indent_pt,
        right_indent_pt=right_indent_pt,
    )

    for child in p_elem:
        tag = get_tag_name(child)
        if tag == "r":
            # Check for page break inside run
            for br in child.findall("w:br", NS):
                if br.get(f"{{{W_NS}}}type") == "page":
                    has_page_break = True
            run = parse_run(child, default_font_idx=default_font, default_size_pt=default_size)
            if run:
                para.add_run(run)

    # Return empty paragraph if no text but keeps spacing
    if not para.runs and not has_page_break:
        # Preserve empty paragraph for blank line spacing
        para.add_text("", font=default_font, size_pt=default_size)

    return para, has_page_break


def parse_table(tbl_elem: ET.Element) -> RtfTable:
    """Parse a w:tbl element into an RtfTable."""
    table = RtfTable()

    # Pre-calculate column widths from gridCol if available
    grid_cols = []
    tbl_grid = tbl_elem.find("w:tblGrid", NS)
    if tbl_grid is not None:
        for col in tbl_grid.findall("w:gridCol", NS):
            w_val = col.get(f"{{{W_NS}}}w")
            grid_cols.append(int(w_val) if (w_val and w_val.isdigit()) else 2000)

    for tr_elem in tbl_elem.findall("w:tr", NS):
        is_header = False
        tr_pr = tr_elem.find("w:trPr", NS)
        if tr_pr is not None:
            if tr_pr.find("w:tblHeader", NS) is not None:
                is_header = True

        row = RtfTableRow(is_header=is_header)

        for col_idx, tc_elem in enumerate(tc_elem for tc_elem in tr_elem if get_tag_name(tc_elem) == "tc"):
            tc_pr = tc_elem.find("w:tcPr", NS)
            width_twips = 2000
            hmerge = None
            vmerge = None
            bg_color_idx = 0
            valign = "center"

            if tc_pr is not None:
                # Cell width
                tc_w = tc_pr.find("w:tcW", NS)
                if tc_w is not None:
                    w_val = tc_w.get(f"{{{W_NS}}}w")
                    if w_val and w_val.isdigit():
                        width_twips = int(w_val)
                elif col_idx < len(grid_cols):
                    width_twips = grid_cols[col_idx]

                # Horizontal merge / gridSpan
                grid_span = tc_pr.find("w:gridSpan", NS)
                if grid_span is not None:
                    span_val = grid_span.get(f"{{{W_NS}}}val")
                    if span_val and span_val.isdigit() and col_idx < len(grid_cols):
                        span_count = int(span_val)
                        width_twips = sum(grid_cols[col_idx : col_idx + span_count])

                h_merge_elem = tc_pr.find("w:hMerge", NS)
                if h_merge_elem is not None:
                    m_val = h_merge_elem.get(f"{{{W_NS}}}val") or "continue"
                    hmerge = "first" if m_val == "restart" else "continue"

                # Vertical merge
                v_merge_elem = tc_pr.find("w:vMerge", NS)
                if v_merge_elem is not None:
                    m_val = v_merge_elem.get(f"{{{W_NS}}}val") or "continue"
                    vmerge = "first" if m_val == "restart" else "continue"

                # Shading / background
                shd = tc_pr.find("w:shd", NS)
                if shd is not None:
                    fill = (shd.get(f"{{{W_NS}}}fill") or "").upper()
                    if fill in ("F2F2F2", "EAEAEA", "E0E0E0"):
                        bg_color_idx = 6  # light gray
                    elif fill != "AUTO" and fill:
                        bg_color_idx = 6

                # Vertical alignment
                v_align_elem = tc_pr.find("w:vAlign", NS)
                if v_align_elem is not None:
                    va_val = v_align_elem.get(f"{{{W_NS}}}val")
                    if va_val in ("top", "bottom", "center"):
                        valign = va_val

            cell_paras = []
            for p in tc_elem.findall("w:p", NS):
                p_obj, _ = parse_paragraph(p)
                if p_obj:
                    # Inside tables, zero first line indent by default unless specified
                    p_obj.first_line_indent_chars = 0.0
                    cell_paras.append(p_obj)

            cell = RtfTableCell(
                paragraphs=cell_paras,
                width_twips=width_twips,
                borders=True,
                bg_color_idx=bg_color_idx,
                valign=valign,
                hmerge=hmerge,
                vmerge=vmerge,
            )
            row.add_cell(cell)

        table.add_row(row)

    return table


def parse_docx_to_rtf_doc(docx_path: str) -> RtfDocument:
    """Parse a DOCX file into an RtfDocument instance."""
    input_file = Path(docx_path).resolve()
    if not input_file.is_file():
        raise FileNotFoundError(f"DOCX file not found: {docx_path}")

    with zipfile.ZipFile(input_file) as zf:
        if "word/document.xml" not in zf.namelist():
            raise ValueError(f"Invalid DOCX archive (missing word/document.xml): {docx_path}")
        doc_xml = zf.read("word/document.xml")

    root = ET.fromstring(doc_xml)
    body = root.find("w:body", NS)
    if body is None:
        raise ValueError(f"Missing w:body in {docx_path}")

    # Margins and page geometry from sectPr
    sect_pr = body.find("w:sectPr", NS)
    margin_top = 72.0
    margin_bottom = 72.0
    margin_left = 72.0
    margin_right = 72.0
    if sect_pr is not None:
        pg_mar = sect_pr.find("w:pgMar", NS)
        if pg_mar is not None:
            t = pg_mar.get(f"{{{W_NS}}}top")
            b = pg_mar.get(f"{{{W_NS}}}bottom")
            l = pg_mar.get(f"{{{W_NS}}}left")
            r = pg_mar.get(f"{{{W_NS}}}right")
            if t and t.isdigit():
                margin_top = float(t) / 20.0
            if b and b.isdigit():
                margin_bottom = float(b) / 20.0
            if l and l.isdigit():
                margin_left = float(l) / 20.0
            if r and r.isdigit():
                margin_right = float(r) / 20.0

    rtf_doc = RtfDocument(
        margin_top_pt=margin_top,
        margin_bottom_pt=margin_bottom,
        margin_left_pt=margin_left,
        margin_right_pt=margin_right,
    )

    for elem in body:
        tag = get_tag_name(elem)
        if tag == "p":
            para, has_break = parse_paragraph(elem)
            if para:
                rtf_doc.add_paragraph(para)
            if has_break:
                rtf_doc.add_page_break()
        elif tag == "tbl":
            table = parse_table(elem)
            rtf_doc.add_table(table)

    return rtf_doc


def convert_docx_to_rtf_wps(docx_path: str, output_path: str) -> Dict[str, Any]:
    """Convert .docx file to .wps file using pure Python RTF stream."""
    from read_wps import read_bytes
    from wps_access import read_input
    from wps_output_contract import require_output_path, readback_summary
    in_path = Path(docx_path).resolve()
    out_path = Path(output_path).resolve()
    require_output_path(out_path, 'rtf')
    if in_path == out_path or (out_path.exists() and in_path.samefile(out_path)):
        raise ValueError('Output must not overwrite the source document')
    source_bytes = read_input(in_path)
    source = read_bytes(source_bytes)
    if source.get('format') != 'ooxml-word' or source.get('status') not in ('ok', 'partial'):
        raise ValueError('DOCX conversion requires a readable OOXML Word package, regardless of its suffix')

    rtf_doc = parse_docx_to_rtf_doc(str(in_path))
    if read_input(in_path) != source_bytes:
        raise ValueError('Source changed during conversion')
    out_path.parent.mkdir(parents=True, exist_ok=True)
    inspection = rtf_doc.save(str(out_path))
    partial = source.get('status') == 'partial' or inspection['partial']

    return {
        "status": "partial" if partial else "success",
        "partial": partial,
        "backend": "rtf_stream",
        "output_format": "rtf",
        "binary_doc": False,
        "source_readback": readback_summary(source),
        "readback": inspection['readback'],
        "output_file": str(out_path),
        "file_size": out_path.stat().st_size,
        "elements_count": len(rtf_doc.elements),
    }
