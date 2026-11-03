#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Pure Python RTF document builder tailored for seamless rendering in Kingsoft WPS Office."""

from typing import List, Dict, Optional, Tuple, Any

FONT_SIMSUN = 0
FONT_SIMHEI = 1
FONT_FANGSONG = 2
FONT_KAITI = 3
FONT_ARIAL = 4
FONT_TIMES = 5
FONT_CALIBRI = 6
FONT_YAHEI = 7
FONT_FANGSONG_GB2312 = 8

FONT_MAP = {
    "simsun": FONT_SIMSUN,
    "宋体": FONT_SIMSUN,
    "simhei": FONT_SIMHEI,
    "黑体": FONT_SIMHEI,
    "fangsong": FONT_FANGSONG,
    "仿宋": FONT_FANGSONG,
    "fangsong_gb2312": FONT_FANGSONG_GB2312,
    "仿宋_gb2312": FONT_FANGSONG_GB2312,
    "kaiti": FONT_KAITI,
    "楷体": FONT_KAITI,
    "arial": FONT_ARIAL,
    "times": FONT_TIMES,
    "times new roman": FONT_TIMES,
    "calibri": FONT_CALIBRI,
    "microsoft yahei": FONT_YAHEI,
    "微软雅黑": FONT_YAHEI,
}

# Standard A4 paper in twips (1 inch = 1440 twips = 25.4mm)
A4_WIDTH_TWIPS = 11906  # 210mm
A4_HEIGHT_TWIPS = 16838  # 297mm


def escape_rtf_text(text: str) -> str:
    """Escape text for RTF, converting Unicode code points to \\uN? syntax."""
    if not text:
        return ""
    out = []
    for ch in text:
        code = ord(ch)
        if code < 128:
            if ch in "\\{}":
                out.append("\\" + ch)
            elif ch == "\r":
                continue
            elif ch == "\n":
                out.append("\\par ")
            elif ch == "\t":
                out.append("\\tab ")
            else:
                out.append(ch)
        else:
            # RTF 16-bit signed integer representation for Unicode code point
            signed_code = code if code < 32768 else code - 65536
            out.append(f"\\u{signed_code}?")
    return "".join(out)


def resolve_font_index(font_name: Optional[str]) -> int:
    """Map common font family names to defined font table index."""
    if not font_name:
        return FONT_FANGSONG
    key = font_name.strip().lower()
    return FONT_MAP.get(key, FONT_SIMSUN)


class RtfRun:
    """A formatted span of text within a paragraph."""

    def __init__(
        self,
        text: str,
        font: int = FONT_FANGSONG,
        size_pt: float = 14.0,
        bold: bool = False,
        italic: bool = False,
        underline: bool = False,
        color_idx: int = 0,
        highlight_idx: int = 0,
    ):
        self.text = text
        self.font = font
        self.size_pt = size_pt
        self.bold = bold
        self.italic = italic
        self.underline = underline
        self.color_idx = color_idx
        self.highlight_idx = highlight_idx

    def to_rtf(self) -> str:
        """Render run to RTF control sequence."""
        half_pts = int(round(self.size_pt * 2))
        ctrls = [f"\\f{self.font}", f"\\fs{half_pts}"]
        if self.bold:
            ctrls.append("\\b")
        if self.italic:
            ctrls.append("\\i")
        if self.underline:
            ctrls.append("\\ul")
        if self.color_idx > 0:
            ctrls.append(f"\\cf{self.color_idx}")
        if self.highlight_idx > 0:
            ctrls.append(f"\\chcbpat{self.highlight_idx}")

        prefix = "".join(ctrls) + " " if ctrls else ""
        escaped_text = escape_rtf_text(self.text)
        suffix = []
        if self.bold:
            suffix.append("\\b0")
        if self.italic:
            suffix.append("\\i0")
        if self.underline:
            suffix.append("\\ulnone")
        if self.color_idx > 0:
            suffix.append("\\cf0")
        return prefix + escaped_text + "".join(suffix)


class RtfParagraph:
    """A paragraph consisting of formatting properties and runs."""

    def __init__(
        self,
        align: str = "justify",
        space_before_pt: float = 0.0,
        space_after_pt: float = 0.0,
        line_spacing_mult: Optional[float] = None,
        line_spacing_exact_pt: Optional[float] = None,
        first_line_indent_chars: float = 2.0,
        left_indent_pt: float = 0.0,
        right_indent_pt: float = 0.0,
        keep_with_next: bool = False,
        widow_control: bool = True,
    ):
        self.align = align
        self.space_before_pt = space_before_pt
        self.space_after_pt = space_after_pt
        self.line_spacing_mult = line_spacing_mult
        self.line_spacing_exact_pt = line_spacing_exact_pt
        self.first_line_indent_chars = first_line_indent_chars
        self.left_indent_pt = left_indent_pt
        self.right_indent_pt = right_indent_pt
        self.keep_with_next = keep_with_next
        self.widow_control = widow_control
        self.runs: List[RtfRun] = []

    def add_run(self, run: RtfRun) -> "RtfParagraph":
        self.runs.append(run)
        return self

    def add_text(
        self,
        text: str,
        font: int = FONT_FANGSONG,
        size_pt: float = 14.0,
        bold: bool = False,
        italic: bool = False,
        underline: bool = False,
        color_idx: int = 0,
    ) -> "RtfParagraph":
        self.runs.append(RtfRun(text, font, size_pt, bold, italic, underline, color_idx))
        return self

    def to_rtf(self) -> str:
        """Render paragraph to RTF."""
        ctrls = ["\\pard"]
        # Alignment
        if self.align == "center":
            ctrls.append("\\qc")
        elif self.align == "right":
            ctrls.append("\\qr")
        elif self.align == "left":
            ctrls.append("\\ql")
        else:
            ctrls.append("\\qj")

        # Spacing before/after (twips = pt * 20)
        if self.space_before_pt > 0:
            ctrls.append(f"\\sb{int(self.space_before_pt * 20)}")
        if self.space_after_pt > 0:
            ctrls.append(f"\\sa{int(self.space_after_pt * 20)}")

        # Line spacing
        if self.line_spacing_exact_pt is not None:
            # Exact line spacing in twips with \slmult0
            ctrls.append(f"\\sl{int(self.line_spacing_exact_pt * 20)}\\slmult0")
        elif self.line_spacing_mult is not None:
            # Multiple of 240 twips (1.0 = 240)
            ctrls.append(f"\\sl{int(self.line_spacing_mult * 240)}\\slmult1")

        # Indentation (2 chars approx 28pt = 560 twips at 14pt, or relative)
        if self.first_line_indent_chars > 0:
            base_size = self.runs[0].size_pt if self.runs else 14.0
            indent_twips = int(self.first_line_indent_chars * base_size * 20)
            ctrls.append(f"\\fi{indent_twips}")
        elif self.first_line_indent_chars < 0:
            ctrls.append(f"\\fi{int(self.first_line_indent_chars * 20)}")

        if self.left_indent_pt > 0:
            ctrls.append(f"\\li{int(self.left_indent_pt * 20)}")
        if self.right_indent_pt > 0:
            ctrls.append(f"\\ri{int(self.right_indent_pt * 20)}")

        if self.keep_with_next:
            ctrls.append("\\keepn")
        if self.widow_control:
            ctrls.append("\\widowctrl")

        runs_rtf = "".join(run.to_rtf() for run in self.runs)
        return "".join(ctrls) + " " + runs_rtf + "\\par"


class RtfTableCell:
    """A cell in an RTF table row."""

    def __init__(
        self,
        paragraphs: Optional[List[RtfParagraph]] = None,
        width_twips: int = 2000,
        borders: bool = True,
        bg_color_idx: int = 0,
        valign: str = "center",
        hmerge: Optional[str] = None,  # "first" or "continue"
        vmerge: Optional[str] = None,  # "first" or "continue"
    ):
        self.paragraphs = paragraphs or []
        self.width_twips = width_twips
        self.borders = borders
        self.bg_color_idx = bg_color_idx
        self.valign = valign
        self.hmerge = hmerge
        self.vmerge = vmerge

    def add_text(
        self,
        text: str,
        font: int = FONT_SIMSUN,
        size_pt: float = 10.5,
        align: str = "center",
        bold: bool = False,
    ):
        p = RtfParagraph(align=align, first_line_indent_chars=0.0)
        p.add_text(text, font=font, size_pt=size_pt, bold=bold)
        self.paragraphs.append(p)


class RtfTableRow:
    """A row in an RTF table."""

    def __init__(self, is_header: bool = False, keep_with_next: bool = False):
        self.is_header = is_header
        self.keep_with_next = keep_with_next
        self.cells: List[RtfTableCell] = []

    def add_cell(self, cell: RtfTableCell) -> "RtfTableRow":
        self.cells.append(cell)
        return self

    def to_rtf(self) -> str:
        """Render table row definition and cell contents."""
        out = ["\\trowd\\trgaph108\\trleft0"]
        if self.is_header:
            out.append("\\trhdr")
        if self.keep_with_next:
            out.append("\\trkeep")

        # Cell definitions with accumulated width
        current_x = 0
        for cell in self.cells:
            current_x += cell.width_twips

            # Borders
            if cell.borders:
                out.append("\\clbrdrt\\brdrs\\brdrw10")
                out.append("\\clbrdrl\\brdrs\\brdrw10")
                out.append("\\clbrdrb\\brdrs\\brdrw10")
                out.append("\\clbrdrr\\brdrs\\brdrw10")

            # Background color
            if cell.bg_color_idx > 0:
                out.append(f"\\clcbpat{cell.bg_color_idx}")

            # Vertical alignment
            if cell.valign == "top":
                out.append("\\clvertalt")
            elif cell.valign == "bottom":
                out.append("\\clvertalb")
            else:
                out.append("\\clvertalc")

            # Horizontal merge
            if cell.hmerge == "first":
                out.append("\\clmgf")
            elif cell.hmerge == "continue":
                out.append("\\clmrg")

            # Vertical merge
            if cell.vmerge == "first":
                out.append("\\clvmgf")
            elif cell.vmerge == "continue":
                out.append("\\clvmrg")

            out.append(f"\\cellx{current_x}")

        # Cell contents
        for cell in self.cells:
            if cell.paragraphs:
                for idx, p in enumerate(cell.paragraphs):
                    p_rtf = p.to_rtf()
                    # Strip leading \pard and trailing \par if needed, wrap in \intbl
                    clean_p = p_rtf.replace("\\pard", "\\pard\\intbl")
                    if idx == len(cell.paragraphs) - 1 and clean_p.endswith("\\par"):
                        clean_p = clean_p[:-4]
                    out.append(clean_p)
            else:
                out.append("\\pard\\intbl")
            out.append("\\cell")

        out.append("\\row")
        return "".join(out)


class RtfTable:
    """A full table containing rows."""

    def __init__(self):
        self.rows: List[RtfTableRow] = []

    def add_row(self, row: RtfTableRow) -> "RtfTable":
        self.rows.append(row)
        return self

    def to_rtf(self) -> str:
        return "\n".join(row.to_rtf() for row in self.rows)


class RtfDocument:
    """Top-level document orchestrator generating compliant RTF for WPS."""

    def __init__(
        self,
        margin_top_pt: float = 72.0,
        margin_bottom_pt: float = 72.0,
        margin_left_pt: float = 72.0,
        margin_right_pt: float = 72.0,
        header_text: Optional[str] = None,
        footer_text: Optional[str] = None,
        page_numbering: bool = False,
    ):
        self.margin_top_twips = int(margin_top_pt * 20)
        self.margin_bottom_twips = int(margin_bottom_pt * 20)
        self.margin_left_twips = int(margin_left_pt * 20)
        self.margin_right_twips = int(margin_right_pt * 20)
        self.header_text = header_text
        self.footer_text = footer_text
        self.page_numbering = page_numbering
        self.elements: List[Any] = []

    def add_element(self, element: Any):
        self.elements.append(element)

    def add_paragraph(self, paragraph: RtfParagraph):
        self.elements.append(paragraph)

    def add_table(self, table: RtfTable):
        self.elements.append(table)

    def add_page_break(self):
        self.elements.append("\\page")

    def add_title(self, text: str, font: int = FONT_SIMHEI, size_pt: float = 22.0):
        """Standard title (二号/一号, centered, bold)."""
        p = RtfParagraph(align="center", space_before_pt=12.0, space_after_pt=12.0, first_line_indent_chars=0.0)
        p.add_text(text, font=font, size_pt=size_pt, bold=True)
        self.elements.append(p)

    def add_heading1(self, text: str, font: int = FONT_SIMHEI, size_pt: float = 16.0):
        """Level 1 heading (三号黑体, left-aligned/indented)."""
        p = RtfParagraph(align="left", space_before_pt=8.0, space_after_pt=4.0, first_line_indent_chars=0.0)
        p.add_text(text, font=font, size_pt=size_pt, bold=True)
        self.elements.append(p)

    def add_heading2(self, text: str, font: int = FONT_KAITI, size_pt: float = 14.0):
        """Level 2 heading (四号楷体, left-aligned/indented)."""
        p = RtfParagraph(align="left", space_before_pt=6.0, space_after_pt=2.0, first_line_indent_chars=0.0)
        p.add_text(text, font=font, size_pt=size_pt, bold=True)
        self.elements.append(p)

    def add_body_paragraph(self, text: str, font: int = FONT_FANGSONG, size_pt: float = 14.0):
        """Standard official body text (四号/三号仿宋, 2-character indent, 1.25x line spacing)."""
        p = RtfParagraph(align="justify", line_spacing_mult=1.25, first_line_indent_chars=2.0)
        p.add_text(text, font=font, size_pt=size_pt)
        self.elements.append(p)

    def render(self) -> str:
        """Render complete RTF document string."""
        parts = [
            "{\\rtf1\\ansi\\ansicpg936\\deff0",
            # Font table
            "{\\fonttbl",
            "{\\f0\\fnil\\fcharset134 SimSun;}",
            "{\\f1\\fnil\\fcharset134 SimHei;}",
            "{\\f2\\fnil\\fcharset134 FangSong;}",
            "{\\f3\\fnil\\fcharset134 KaiTi;}",
            "{\\f4\\fnil\\fcharset0 Arial;}",
            "{\\f5\\fnil\\fcharset0 Times New Roman;}",
            "{\\f6\\fnil\\fcharset0 Calibri;}",
            "{\\f7\\fnil\\fcharset134 Microsoft YaHei;}",
            "{\\f8\\fnil\\fcharset134 FangSong_GB2312;}",
            "}",
            # Color table
            "{\\colortbl ;\\red0\\green0\\blue0;\\red255\\green0\\blue0;\\red0\\green112\\blue192;"
            "\\red112\\green48\\blue160;\\red128\\green128\\blue128;\\red240\\green240\\blue240;}",
            # View & Unicode control
            "\\viewkind4\\uc1",
            # Page dimension & margins
            f"\\paperw{A4_WIDTH_TWIPS}\\paperh{A4_HEIGHT_TWIPS}",
            f"\\margl{self.margin_left_twips}\\margr{self.margin_right_twips}",
            f"\\margt{self.margin_top_twips}\\margb{self.margin_bottom_twips}",
        ]

        if self.header_text:
            escaped_header = escape_rtf_text(self.header_text)
            parts.append(f"{{\\header \\pard\\qr\\fs18 {escaped_header}\\par}}")
        if self.footer_text or self.page_numbering:
            footer_content = []
            if self.footer_text:
                footer_content.append(escape_rtf_text(self.footer_text) + " ")
            if self.page_numbering:
                footer_content.append("{\\field{\\*\\fldinst PAGE}}")
            footer_body = "".join(footer_content)
            parts.append(f"{{\\footer \\pard\\qc\\fs18 {footer_body}\\par}}")

        for el in self.elements:
            if isinstance(el, str):
                parts.append(el)
            elif hasattr(el, "to_rtf"):
                parts.append(el.to_rtf())

        parts.append("}")
        return "\n".join(parts)

    def save(self, file_path: str):
        """Save the document as .wps file (with ASCII encoding of RTF stream)."""
        content = self.render()
        with open(file_path, "w", encoding="ascii") as fp:
            fp.write(content)


def build_document_from_spec(spec: Dict[str, Any]) -> RtfDocument:
    """Build an RtfDocument instance from a declarative document specification."""
    theme = spec.get("theme", "official")
    header_text = spec.get("header_text") or spec.get("header")
    footer_text = spec.get("footer_text") or spec.get("footer")
    page_numbering = bool(spec.get("page_numbering", True))

    if theme == "official":
        # GB/T 9704-2012: top 37mm, bottom 35mm, left 28mm, right 26mm
        doc = RtfDocument(
            margin_top_pt=104.9,
            margin_bottom_pt=99.2,
            margin_left_pt=79.4,
            margin_right_pt=73.7,
            header_text=header_text,
            footer_text=footer_text,
            page_numbering=page_numbering,
        )
        title_font = FONT_SIMHEI
        title_size = 22.0
        h1_font, h1_size = FONT_SIMHEI, 16.0
        h2_font, h2_size = FONT_KAITI, 15.0
        h3_font, h3_size = FONT_FANGSONG, 14.0
        body_font, body_size = FONT_FANGSONG, 14.0
        body_indent = 2.0
        line_spacing_exact = 28.8
        line_spacing_mult = None
    elif theme == "business":
        doc = RtfDocument(
            margin_top_pt=72.0,
            margin_bottom_pt=72.0,
            margin_left_pt=72.0,
            margin_right_pt=72.0,
            header_text=header_text,
            footer_text=footer_text,
            page_numbering=page_numbering,
        )
        title_font = FONT_YAHEI
        title_size = 22.0
        h1_font, h1_size = FONT_YAHEI, 16.0
        h2_font, h2_size = FONT_YAHEI, 14.0
        h3_font, h3_size = FONT_YAHEI, 12.0
        body_font, body_size = FONT_YAHEI, 11.0
        body_indent = 0.0
        line_spacing_exact = None
        line_spacing_mult = 1.25
    else:  # general
        doc = RtfDocument(
            margin_top_pt=56.7,
            margin_bottom_pt=56.7,
            margin_left_pt=56.7,
            margin_right_pt=56.7,
            header_text=header_text,
            footer_text=footer_text,
            page_numbering=page_numbering,
        )
        title_font = FONT_SIMHEI
        title_size = 20.0
        h1_font, h1_size = FONT_SIMHEI, 15.0
        h2_font, h2_size = FONT_KAITI, 13.0
        h3_font, h3_size = FONT_SIMSUN, 12.0
        body_font, body_size = FONT_SIMSUN, 12.0
        body_indent = 2.0
        line_spacing_exact = None
        line_spacing_mult = 1.2

    blocks = spec.get("blocks", [])
    for block in blocks:
        b_type = block.get("type", "paragraph")
        if b_type == "title":
            text = block.get("text", "")
            p = RtfParagraph(
                align="center",
                space_before_pt=14.0,
                space_after_pt=12.0,
                first_line_indent_chars=0.0,
                keep_with_next=True,
            )
            p.add_text(text, font=title_font, size_pt=title_size, bold=True)
            doc.add_paragraph(p)
        elif b_type == "subtitle":
            text = block.get("text", "")
            p = RtfParagraph(
                align="center",
                space_before_pt=0.0,
                space_after_pt=12.0,
                first_line_indent_chars=0.0,
                keep_with_next=True,
            )
            p.add_text(text, font=FONT_KAITI, size_pt=16.0, bold=False)
            doc.add_paragraph(p)
        elif b_type == "heading":
            level = int(block.get("level", 1))
            text = block.get("text", "")
            if level == 1:
                p = RtfParagraph(
                    align="left",
                    space_before_pt=10.0,
                    space_after_pt=4.0,
                    first_line_indent_chars=0.0,
                    keep_with_next=True,
                )
                p.add_text(text, font=h1_font, size_pt=h1_size, bold=True)
            elif level == 2:
                p = RtfParagraph(
                    align="left",
                    space_before_pt=8.0,
                    space_after_pt=3.0,
                    first_line_indent_chars=0.0,
                    keep_with_next=True,
                )
                p.add_text(text, font=h2_font, size_pt=h2_size, bold=True)
            else:
                p = RtfParagraph(
                    align="left",
                    space_before_pt=6.0,
                    space_after_pt=2.0,
                    first_line_indent_chars=0.0,
                    keep_with_next=True,
                )
                p.add_text(text, font=h3_font, size_pt=h3_size, bold=True)
            doc.add_paragraph(p)
        elif b_type == "paragraph":
            text = block.get("text", "")
            runs_data = block.get("runs")
            p = RtfParagraph(
                align=block.get("align", "justify"),
                space_before_pt=block.get("space_before_pt", 0.0),
                space_after_pt=block.get("space_after_pt", 2.0 if theme == "official" else 4.0),
                first_line_indent_chars=block.get("indent_chars", body_indent),
                line_spacing_exact_pt=line_spacing_exact,
                line_spacing_mult=line_spacing_mult,
                widow_control=True,
            )
            if runs_data and isinstance(runs_data, list):
                for r in runs_data:
                    p.add_text(
                        r.get("text", ""),
                        font=resolve_font_index(r.get("font")),
                        size_pt=r.get("size_pt", body_size),
                        bold=bool(r.get("bold", False)),
                        italic=bool(r.get("italic", False)),
                        underline=bool(r.get("underline", False)),
                        color_idx=int(r.get("color_idx", 0)),
                    )
            else:
                p.add_text(text, font=body_font, size_pt=body_size)
            doc.add_paragraph(p)
        elif b_type == "list":
            items = block.get("items", [])
            ordered = bool(block.get("ordered", False))
            for idx, item_text in enumerate(items, start=1):
                p = RtfParagraph(
                    align="left",
                    space_before_pt=1.0,
                    space_after_pt=2.0,
                    first_line_indent_chars=0.0,
                    left_indent_pt=18.0,
                    line_spacing_mult=1.2,
                    widow_control=True,
                )
                prefix = f"{idx}. " if ordered else "• "
                p.add_text(prefix, font=body_font, size_pt=body_size, bold=ordered)
                p.add_text(str(item_text), font=body_font, size_pt=body_size)
                doc.add_paragraph(p)
        elif b_type == "table":
            rows_data = block.get("rows", [])
            has_header = bool(block.get("header", True))
            col_widths = block.get("col_widths")
            table = RtfTable()
            num_cols = max(len(r) for r in rows_data) if rows_data else 1
            available_width = A4_WIDTH_TWIPS - doc.margin_left_twips - doc.margin_right_twips
            default_cell_w = max(1000, available_width // num_cols)

            for r_idx, row_values in enumerate(rows_data):
                is_hdr = has_header and (r_idx == 0)
                row = RtfTableRow(is_header=is_hdr, keep_with_next=is_hdr)
                for c_idx, val in enumerate(row_values):
                    c_width = int(col_widths[c_idx] * 20) if col_widths and c_idx < len(col_widths) else default_cell_w
                    cell = RtfTableCell(
                        width_twips=c_width,
                        borders=True,
                        bg_color_idx=6 if is_hdr else 0,
                        valign="center",
                    )
                    cell.add_text(
                        str(val),
                        font=FONT_SIMHEI if is_hdr else FONT_SIMSUN,
                        size_pt=10.5,
                        align="center" if is_hdr else "left",
                        bold=is_hdr,
                    )
                    row.add_cell(cell)
                table.add_row(row)
            doc.add_table(table)
        elif b_type == "callout":
            text = block.get("text", "")
            p = RtfParagraph(
                align="left",
                space_before_pt=4.0,
                space_after_pt=4.0,
                first_line_indent_chars=0.0,
                left_indent_pt=14.0,
                right_indent_pt=14.0,
                line_spacing_mult=1.2,
            )
            p.add_text(text, font=FONT_KAITI, size_pt=11.0, italic=True)
            doc.add_paragraph(p)
        elif b_type == "page_break":
            doc.add_page_break()

    return doc
