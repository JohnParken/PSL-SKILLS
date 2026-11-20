#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Standard-library RTF builder; controls are tested, office rendering is not certified."""

from dataclasses import dataclass, field
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


@dataclass
class ParagraphStyle:
    """Formatting configuration for an RTF paragraph."""

    align: str = "justify"
    space_before_pt: float = 0.0
    space_after_pt: float = 0.0
    line_spacing_mult: Optional[float] = None
    line_spacing_exact_pt: Optional[float] = None
    first_line_indent_chars: float = 2.0
    left_indent_pt: float = 0.0
    right_indent_pt: float = 0.0
    keep_with_next: bool = False
    widow_control: Optional[bool] = True
    page_break_before: bool = False
    keep_together: bool = False
    first_line_indent_pt: Optional[float] = None
    tabs_pt: List[float] = field(default_factory=list)
    outline_level: Optional[int] = None
    character_indent: bool = False


class RtfParagraph:
    """A paragraph consisting of formatting properties and runs."""

    def __init__(self, style: Optional[ParagraphStyle] = None, **kwargs):
        resolved = style if style is not None else ParagraphStyle(**kwargs)
        self.align = resolved.align
        self.space_before_pt = resolved.space_before_pt
        self.space_after_pt = resolved.space_after_pt
        self.line_spacing_mult = resolved.line_spacing_mult
        self.line_spacing_exact_pt = resolved.line_spacing_exact_pt
        self.first_line_indent_chars = resolved.first_line_indent_chars
        self.left_indent_pt = resolved.left_indent_pt
        self.right_indent_pt = resolved.right_indent_pt
        self.keep_with_next = resolved.keep_with_next
        self.widow_control = resolved.widow_control
        self.page_break_before = resolved.page_break_before
        self.keep_together = resolved.keep_together
        self.first_line_indent_pt = resolved.first_line_indent_pt
        self.tabs_pt = list(resolved.tabs_pt or [])
        self.outline_level = resolved.outline_level
        self.character_indent = resolved.character_indent
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
    from wps_rtf_doc_builder import build_document_from_spec as _build
    return _build(spec)
