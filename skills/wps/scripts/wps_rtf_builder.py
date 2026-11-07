#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Standard-library RTF builder; controls are tested, office rendering is not certified."""

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
FONT_XIAOBIAOSONG = 9

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
    "方正小标宋简体": FONT_XIAOBIAOSONG,
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
        if code < 32 and ch not in "\r\n\t":
            raise ValueError("Unsupported text control character")
        if code < 128:
            if ch in "\\{}":
                out.append("\\" + ch)
            elif ch == "\r":
                continue
            elif ch == "\n":
                out.append("\\line ")
            elif ch == "\t":
                out.append("\\tab ")
            else:
                out.append(ch)
        else:
            # RTF 16-bit signed integer representation for Unicode code point
            for offset in range(0, len(ch.encode("utf-16le")), 2):
                unit = int.from_bytes(ch.encode("utf-16le")[offset:offset + 2], "little")
                signed_code = unit if unit < 32768 else unit - 65536
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
        latin_font: Optional[int] = None,
    ):
        self.text = text
        self.font = font
        self.size_pt = size_pt
        self.bold = bold
        self.italic = italic
        self.underline = underline
        self.color_idx = color_idx
        self.highlight_idx = highlight_idx
        self.latin_font = latin_font

    def to_rtf(self) -> str:
        """Render run to RTF control sequence."""
        half_pts = int(round(self.size_pt * 2))
        ctrls = [f"\\f{self.font}", f"\\fs{half_pts}"]
        if self.latin_font is not None:
            # RTF 1.9.1 composite fonts: associate East Asian and Latin families.
            ctrls = [f"\\hich\\af{self.latin_font}\\dbch\\af{self.font}\\loch\\f{self.latin_font}", f"\\fs{half_pts}"]
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
        rendered = prefix + escaped_text + "".join(suffix)
        # Composite font associations must not leak into an explicitly formatted next run.
        return "{" + rendered + "}" if self.latin_font is not None else rendered


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
        widow_control: Optional[bool] = True,
        page_break_before: bool = False,
        keep_together: bool = False,
        first_line_indent_pt: Optional[float] = None,
        tabs_pt: Optional[List[float]] = None,
        outline_level: Optional[int] = None,
        character_indent: bool = False,
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
        self.page_break_before = page_break_before
        self.keep_together = keep_together
        self.first_line_indent_pt = first_line_indent_pt
        self.tabs_pt = tabs_pt or []
        self.outline_level = outline_level
        self.character_indent = character_indent
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
        latin_font: Optional[int] = None,
    ) -> "RtfParagraph":
        self.runs.append(RtfRun(text, font, size_pt, bold, italic, underline, color_idx, latin_font=latin_font))
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
            ctrls.append(f"\\sl{-int(self.line_spacing_exact_pt * 20)}\\slmult0")
        elif self.line_spacing_mult is not None:
            # Multiple of 240 twips (1.0 = 240)
            ctrls.append(f"\\sl{int(self.line_spacing_mult * 240)}\\slmult1")

        # Indentation (2 chars approx 28pt = 560 twips at 14pt, or relative)
        if self.first_line_indent_pt is not None:
            ctrls.append(f"\\fi{int(self.first_line_indent_pt * 20)}")
        elif self.first_line_indent_chars > 0:
            base_size = self.runs[0].size_pt if self.runs else 14.0
            indent_twips = int(self.first_line_indent_chars * base_size * 20)
            ctrls.append(f"\\fi{indent_twips}")
        elif self.first_line_indent_chars < 0:
            base_size = self.runs[0].size_pt if self.runs else 14.0
            ctrls.append(f"\\fi{int(self.first_line_indent_chars * base_size * 20)}")
        if self.character_indent and self.first_line_indent_pt is None:
            # Keep the equivalent twip fallback; explicit point indents take precedence.
            ctrls.append(f"\\cufi{round(self.first_line_indent_chars * 100)}")

        if self.left_indent_pt > 0:
            ctrls.append(f"\\li{int(self.left_indent_pt * 20)}")
        if self.right_indent_pt > 0:
            ctrls.append(f"\\ri{int(self.right_indent_pt * 20)}")

        if self.outline_level is not None:
            ctrls.append(f"\\outlinelevel{self.outline_level}")
        for position in self.tabs_pt:
            ctrls.append(f"\\tx{int(position * 20)}")
        if self.page_break_before:
            ctrls.append("\\pagebb")
        if self.keep_together:
            ctrls.append("\\keep")
        if self.keep_with_next:
            ctrls.append("\\keepn")
        if self.widow_control:
            ctrls.append("\\widctlpar")
        elif self.widow_control is False:
            ctrls.append("\\nowidctlpar")

        runs_rtf = "".join(run.to_rtf() for run in self.runs)
        return "".join(ctrls) + " " + runs_rtf + "\\par"


def normalized_merges(rows, merges, repeat_header):
    """Validate bounded merge rectangles without deleting covered user content."""
    if not isinstance(merges, list) or len(merges) > 256:
        raise ValueError("merges must contain at most 256 inclusive [r0,c0,r1,c1] ranges")
    cells = {}
    for span in merges:
        if not isinstance(span, list) or len(span) != 4 or any(type(value) is not int for value in span):
            raise ValueError("Merge coordinates must be four integers")
        r0, c0, r1, c1 = span
        if not (0 <= r0 <= r1 < len(rows) and 0 <= c0 <= c1 < len(rows[0])) or (r0 == r1 and c0 == c1):
            raise ValueError("Merge is out of bounds, reversed or contains only one cell")
        if repeat_header and r0 == 0 and r1 > 0:
            raise ValueError("Vertical merge across repeating header boundary is unsupported; disable repetition explicitly")
        for row in range(r0, r1 + 1):
            for col in range(c0, c1 + 1):
                if len(cells) >= 100000:
                    raise ValueError("Merge coverage exceeds 100000 cells")
                if (row, col) in cells:
                    raise ValueError("Overlapping merge ranges")
                if (row, col) != (r0, c0) and rows[row][col] != "":
                    raise ValueError("Covered merge cells must be empty; refusing to discard content")
                cells[row, col] = (("first" if col == c0 else "continue") if c0 != c1 else None,
                                   ("first" if row == r0 else "continue") if r0 != r1 else None)
    return cells


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
        border_width_pt: float = 0.5,
    ):
        self.paragraphs = paragraphs or []
        self.width_twips = width_twips
        self.borders = borders
        self.border_width_pt = border_width_pt
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

    def __init__(self, is_header: bool = False, keep_with_next: bool = False, keep_together: bool = False):
        self.is_header = is_header
        self.keep_with_next = keep_with_next
        self.keep_together = keep_together
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
            out.append("\\trkeepfollow")
        if self.keep_together:
            out.append("\\trkeep")

        # Cell definitions with accumulated width
        current_x = 0
        for cell in self.cells:
            current_x += cell.width_twips

            # Borders
            edges = ("top", "left", "bottom", "right") if cell.borders is True else cell.borders or ()
            for edge in ("top", "left", "bottom", "right"):
                code = {"top": "t", "left": "l", "bottom": "b", "right": "r"}[edge]
                style = "\\brdrs\\brdrw" + str(int(cell.border_width_pt * 20)) if edge in edges else "\\brdrnil"
                out.append("\\clbrdr" + code + style)

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
        official_typography: bool = False,
    ):
        self.margin_top_twips = int(margin_top_pt * 20)
        self.margin_bottom_twips = int(margin_bottom_pt * 20)
        self.margin_left_twips = int(margin_left_pt * 20)
        self.margin_right_twips = int(margin_right_pt * 20)
        self.header_text = header_text
        self.footer_text = footer_text
        self.page_numbering = page_numbering
        self.official_typography = official_typography
        self.page_width_twips = A4_WIDTH_TWIPS
        self.page_height_twips = A4_HEIGHT_TWIPS
        self.spec_warnings = []
        self.spec_warning_count = 0
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
            "{\\f9\\fnil\\fcharset134 " + escape_rtf_text("方正小标宋简体") + ";}",
            "}",
            # Color table
            "{\\colortbl ;\\red0\\green0\\blue0;\\red255\\green0\\blue0;\\red0\\green112\\blue192;"
            "\\red112\\green48\\blue160;\\red128\\green128\\blue128;\\red240\\green240\\blue240;}",
            # View & Unicode control
            "\\viewkind4\\uc1",
            # Page dimension & margins
            f"\\paperw{self.page_width_twips}\\paperh{self.page_height_twips}",
            f"\\margl{self.margin_left_twips}\\margr{self.margin_right_twips}",
            f"\\margt{self.margin_top_twips}\\margb{self.margin_bottom_twips}",
        ]

        if self.official_typography:
            for kind, text, page_number in (("header", self.header_text, False), ("footer", self.footer_text, self.page_numbering)):
                if text is None and not page_number:
                    continue
                paragraph = RtfParagraph(align="left", first_line_indent_chars=0, line_spacing_mult=1.15, widow_control=None)
                paragraph.add_text((text or "") + (" " if text and page_number else ""), font=FONT_FANGSONG, size_pt=16, latin_font=FONT_TIMES)
                content = paragraph.to_rtf()[:-4]
                if page_number:
                    page_run = RtfRun("", font=FONT_FANGSONG, size_pt=16, latin_font=FONT_TIMES).to_rtf()
                    content += page_run[:-1] + "{\\field{\\*\\fldinst PAGE}{\\fldrslt 1}}}"
                parts.append("{\\" + kind + " " + content + "\\par}")
        elif self.header_text:
            escaped_header = escape_rtf_text(self.header_text)
            parts.append(f"{{\\header \\pard\\qr\\fs18 {escaped_header}\\par}}")
        if not self.official_typography and (self.footer_text or self.page_numbering):
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
        """Save validated ASCII RTF as .rtf/.wps and return readback diagnostics."""
        from wps_output_contract import require_output_path, require_output_bytes
        from wps_task_paths import delivery_filename
        file_path = delivery_filename(file_path)
        require_output_path(file_path, 'rtf')
        content = self.render()
        inspection = require_output_bytes(content.encode('ascii'), file_path, 'rtf')
        from wps_task_paths import output_candidate, write_candidate
        with output_candidate(file_path, exclusive=True) as (candidate, publish):
            write_candidate(candidate, content.encode("ascii"))
            publish()
        return inspection


def build_document_from_spec(spec: Dict[str, Any]) -> RtfDocument:
    """Build an RtfDocument instance from a declarative document specification."""
    import math
    if not isinstance(spec, dict) or not isinstance(spec.get("blocks"), list) or not spec["blocks"]:
        raise ValueError("A nonempty blocks array is required")
    if spec.get("theme", "official") not in ("official", "business", "general"):
        raise ValueError("Unsupported theme")
    if "page_numbering" in spec and type(spec["page_numbering"]) is not bool:
        raise ValueError("page_numbering must be boolean")
    for block in spec["blocks"]:
        if not isinstance(block, dict) or block.get("type") not in ("title", "subtitle", "heading", "paragraph", "list", "table", "callout", "page_break"):
            raise ValueError("Unsupported block")
        if "text" in block and not isinstance(block["text"], str):
            raise ValueError("Block text must be a string")
        if block.get("type") == "table":
            rows = block.get("rows")
            if not isinstance(rows, list) or not rows or not all(isinstance(row, list) and row for row in rows) or len({len(row) for row in rows}) != 1:
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
        for key in ("space_before_pt", "space_after_pt", "indent_chars", "line_spacing_exact_pt", "line_spacing_mult", "left_indent_pt", "right_indent_pt", "first_line_indent_pt"):
            if key in ("line_spacing_exact_pt", "line_spacing_mult") and block.get(key, 0) is None:
                continue
            if key in block and (type(block[key]) not in (int, float) or not math.isfinite(block[key]) or abs(block[key]) > 1000):
                raise ValueError("Invalid paragraph measurement")
            if key in block and key not in ("indent_chars", "first_line_indent_pt") and block[key] < 0:
                raise ValueError("Spacing must be nonnegative")
    theme = spec.get("theme", "official")
    header_text = spec.get("header_text") or spec.get("header")
    footer_text = spec.get("footer_text") or spec.get("footer")
    page_numbering = spec.get("page_numbering", theme != "official")
    latin_font = FONT_TIMES if theme == "official" else None

    if theme == "official":
        # Match the DOCX official preset (not a claim of national-standard compliance).
        # 31.8 mm rounded to whole twips is 1803; top/bottom are 25.4 mm.
        doc = RtfDocument(
            margin_top_pt=72.0,
            margin_bottom_pt=72.0,
            margin_left_pt=90.15,
            margin_right_pt=90.15,
            header_text=spec.get("header_text") if spec.get("header_text") is not None else spec.get("header"),
            footer_text=spec.get("footer_text") if spec.get("footer_text") is not None else spec.get("footer"),
            page_numbering=page_numbering,
            official_typography=True,
        )
        title_font = FONT_XIAOBIAOSONG
        title_size = 22.0
        h1_font, h1_size = FONT_SIMHEI, 16.0
        h2_font, h2_size = FONT_KAITI, 16.0
        h3_font, h3_size = FONT_FANGSONG, 16.0
        body_font, body_size = FONT_FANGSONG, 16.0
        body_indent = 2.0
        line_spacing_exact = 28.0
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

    def warn_unknown(value, supported, path):
        for key in value:
            if key not in supported:
                doc.spec_warning_count += 1
                if len(doc.spec_warnings) < 100:
                    doc.spec_warnings.append({"code": "unsupported_option_ignored", "path": path + str(key)})
    warn_unknown(spec, {"theme", "header_text", "header", "footer_text", "footer", "page_numbering", "blocks", "page"}, "")
    page = spec.get("page", {})
    if not isinstance(page, dict):
        raise ValueError("page must be an object")
    warn_unknown(page, {"width_pt", "height_pt", "margin_left_pt", "margin_right_pt", "margin_top_pt", "margin_bottom_pt"}, "page.")
    for key, attribute in {"width_pt": "page_width_twips", "height_pt": "page_height_twips",
                           "margin_left_pt": "margin_left_twips", "margin_right_pt": "margin_right_twips",
                           "margin_top_pt": "margin_top_twips", "margin_bottom_pt": "margin_bottom_twips"}.items():
        if key in page:
            value = page[key]
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 2000:
                raise ValueError("Invalid page measurement")
            setattr(doc, attribute, int(value * 20))
    if (doc.page_width_twips <= doc.margin_left_twips + doc.margin_right_twips
            or doc.page_height_twips <= doc.margin_top_twips + doc.margin_bottom_twips):
        raise ValueError("Page margins leave no printable area")
    blocks = spec.get("blocks", [])
    paragraph_options = {
        "align": "align", "space_before_pt": "space_before_pt", "space_after_pt": "space_after_pt",
        "indent_chars": "first_line_indent_chars", "line_spacing_exact_pt": "line_spacing_exact_pt",
        "line_spacing_mult": "line_spacing_mult", "widow_control": "widow_control", "keep_with_next": "keep_with_next",
        "keep_together": "keep_together", "page_break_before": "page_break_before", "left_indent_pt": "left_indent_pt",
        "right_indent_pt": "right_indent_pt", "first_line_indent_pt": "first_line_indent_pt", "tabs_pt": "tabs_pt",
    }

    def apply_heading_options(paragraph, block):
        for option, attribute in paragraph_options.items():
            if option in block:
                setattr(paragraph, attribute, block[option])

    def resolve_run_font(name, path):
        if name is not None and not isinstance(name, str):
            raise ValueError("Run font must be a string")
        if name and name.strip().lower() not in FONT_MAP:
            doc.spec_warning_count += 1
            if len(doc.spec_warnings) < 100:
                doc.spec_warnings.append({"code": "unsupported_font_substituted", "path": path, "replacement": "SimSun"})
        return resolve_font_index(name)

    block_options = {
        "title": {"type", "text"} | set(paragraph_options), "subtitle": {"type", "text"} | set(paragraph_options),
        "heading": {"type", "text", "level"} | set(paragraph_options),
        "paragraph": {"type", "text", "runs", "align", "space_before_pt", "space_after_pt", "indent_chars",
                      "line_spacing_exact_pt", "line_spacing_mult", "widow_control", "keep_with_next", "keep_together",
                      "page_break_before", "left_indent_pt", "right_indent_pt", "first_line_indent_pt", "tabs_pt"},
        "list": {"type", "items", "ordered", "start"},
        "table": {"type", "rows", "header", "col_widths", "col_alignments", "repeat_header", "row_keep_together", "merges", "borders", "border_width_pt"},
        "callout": {"type", "text"}, "page_break": {"type"},
    }
    for block_index, block in enumerate(blocks):
        warn_unknown(block, block_options[block["type"]], "blocks[{}].".format(block_index))
        b_type = block.get("type", "paragraph")
        if b_type == "title":
            text = block.get("text", "")
            p = RtfParagraph(
                align="center",
                space_before_pt=0.0 if theme == "official" else 14.0,
                space_after_pt=12.0,
                first_line_indent_chars=0.0,
                keep_with_next=True,
                line_spacing_mult=1.15 if theme == "official" else None,
                widow_control=None if theme == "official" else True,
            )
            apply_heading_options(p, block)
            p.add_text(text, font=title_font, size_pt=title_size, bold=theme != "official", latin_font=latin_font)
            doc.add_paragraph(p)
        elif b_type == "subtitle":
            text = block.get("text", "")
            p = RtfParagraph(
                align="center",
                space_before_pt=0.0,
                space_after_pt=8.0 if theme == "official" else 12.0,
                first_line_indent_chars=0.0,
                keep_with_next=True,
                line_spacing_mult=1.15 if theme == "official" else None,
                widow_control=None if theme == "official" else True,
            )
            apply_heading_options(p, block)
            p.add_text(text, font=FONT_XIAOBIAOSONG if theme == "official" else FONT_KAITI,
                       size_pt=14.0 if theme == "official" else 16.0, bold=False, latin_font=latin_font)
            doc.add_paragraph(p)
        elif b_type == "heading":
            level = int(block.get("level", 1))
            text = block.get("text", "")
            if theme == "official":
                font, size = {1: (h1_font, h1_size), 2: (h2_font, h2_size), 3: (h3_font, h3_size)}.get(level, (FONT_FANGSONG, 16.0))
                p = RtfParagraph(align="left", first_line_indent_chars=0,
                                 line_spacing_mult=1.15, keep_with_next=True, widow_control=None)
                p.add_text(text, font=font, size_pt=size, bold=False, latin_font=latin_font)
            elif level == 1:
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
            p.outline_level = level - 1
            apply_heading_options(p, block)
            doc.add_paragraph(p)
        elif b_type == "paragraph":
            text = block.get("text", "")
            runs_data = block.get("runs")
            p = RtfParagraph(
                align=block.get("align", "left" if theme == "official" else "justify"),
                space_before_pt=block.get("space_before_pt", 0.0),
                space_after_pt=block.get("space_after_pt", 0.0 if theme == "official" else 4.0),
                first_line_indent_chars=block.get("indent_chars", body_indent),
                line_spacing_exact_pt=block.get("line_spacing_exact_pt", None if "line_spacing_mult" in block else line_spacing_exact),
                line_spacing_mult=block.get("line_spacing_mult", line_spacing_mult),
                widow_control=block.get("widow_control", None if theme == "official" else True),
                keep_with_next=block.get("keep_with_next", False),
                keep_together=block.get("keep_together", False),
                page_break_before=block.get("page_break_before", False),
                left_indent_pt=block.get("left_indent_pt", 0),
                right_indent_pt=block.get("right_indent_pt", 0),
                first_line_indent_pt=block.get("first_line_indent_pt"),
                tabs_pt=block.get("tabs_pt", []),
                character_indent=theme == "official",
            )
            if runs_data and isinstance(runs_data, list):
                for run_index, r in enumerate(runs_data):
                    p.add_text(
                        r.get("text", ""),
                        font=resolve_run_font(r.get("font"), f"blocks[{block_index}].runs[{run_index}].font"),
                        size_pt=r.get("size_pt", body_size),
                        bold=bool(r.get("bold", False)),
                        italic=bool(r.get("italic", False)),
                        underline=bool(r.get("underline", False)),
                        color_idx=int(r.get("color_idx", 0)),
                        latin_font=latin_font if not r.get("font") else None,
                    )
            else:
                p.add_text(text, font=body_font, size_pt=body_size, latin_font=latin_font)
            doc.add_paragraph(p)
        elif b_type == "list":
            items = block.get("items", [])
            ordered = bool(block.get("ordered", False))
            for idx, item_text in enumerate(items, start=block.get("start", 1)):
                p = RtfParagraph(
                    align="left",
                    space_before_pt=0.0 if theme == "official" else 1.0,
                    space_after_pt=0.0 if theme == "official" else 2.0,
                    first_line_indent_chars=0.0,
                    left_indent_pt=36.0 if theme == "official" else 18.0,
                    first_line_indent_pt=-18.0 if theme == "official" else None,
                    tabs_pt=[36.0] if theme == "official" else [],
                    line_spacing_mult=1.15 if theme == "official" else 1.2,
                    widow_control=None if theme == "official" else True,
                )
                prefix = f"{idx}. " if ordered else "• "
                p.add_text(prefix, font=body_font, size_pt=body_size, bold=ordered and theme != "official", latin_font=latin_font)
                p.add_text(str(item_text), font=body_font, size_pt=body_size, latin_font=latin_font)
                doc.add_paragraph(p)
        elif b_type == "table":
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
                        width_twips=c_width,
                        borders=edges,
                        border_width_pt=border_width,
                        hmerge=merge[0] if merge else None,
                        vmerge=merge[1] if merge else None,
                        bg_color_idx=6 if is_hdr and theme != "official" else 0,
                        valign="center",
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
