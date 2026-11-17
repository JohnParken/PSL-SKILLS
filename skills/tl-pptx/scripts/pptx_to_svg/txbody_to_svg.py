"""DrawingML <p:txBody> -> SVG <text> conversion.

Reverse of svg_to_pptx/drawingml/elements.py convert_text.

Strategy (v1):
- Each <a:p> paragraph emits one <text> element (one line of baseline).
  Multiple <a:r> runs in one paragraph become <tspan>s sharing the text
  element's x.
- Vertical layout: y of first paragraph is determined by anchor (t/ctr/b)
  and tIns/bIns. Subsequent paragraphs stack downward with line height
  derived from the largest font in the paragraph * 1.2 (default leading).
- Horizontal layout: text-anchor follows pPr@algn. x is computed from the
  text frame plus lIns/rIns and the alignment.
- No automatic word wrap (PPT's wrap is layout-time; v1 trusts the existing
  text frame width and emits text as-is). a:br produces an explicit linebreak.
- Bullet points (a:buChar / a:buAutoNum) are rendered as literal prefixes
  so the visual lands without relying on PowerPoint list semantics.

Color / font / size attributes propagate from a:rPr; missing attributes fall
back to paragraph/list defaults, endParaRPr, or spec-default values.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable
from unicodedata import east_asian_width
from xml.etree import ElementTree as ET

from hyperlink_contract import SOURCE_HREF_ATTR
from svg_to_pptx.drawingml.text_baseline import drawingml_text_baseline_offset
from svg_to_pptx.drawingml.utils import detect_text_lang, is_cjk_char

from .color_resolver import ColorPalette, find_color_elem, resolve_color
from .emu_units import (
    NS, Xfrm, fmt_num, emu_to_px, format_ooxml_alpha,
    hundredths_pt_to_px, ooxml_bool,
)
from .fill_to_svg import resolve_fill
from .formula_import import FormulaImport


# ---------------------------------------------------------------------------
# Defaults (matches DrawingML spec)
# ---------------------------------------------------------------------------

# Default body insets when bodyPr omits them: 0.1 inch left/right, 0.05 top/bot.
DEFAULT_INSETS_EMU = {"l": 91440, "t": 45720, "r": 91440, "b": 45720}

# Default font size = 1800 (= 18 pt = 24 px). Spec is actually 1800 (18pt).
DEFAULT_FONT_SIZE_PX = 24.0
DEFAULT_LINE_HEIGHT_RATIO = 1.2  # leading multiplier
DEFAULT_FILL_HEX = "#000000"


@dataclass
class TextRun:
    """A single run with resolved style + text."""

    text: str
    font_size_px: float
    font_family: str  # full font-family stack (latin, ea fallback joined)
    fill: str
    fill_opacity: float = 1.0
    defs: list[str] = field(default_factory=list)
    bold: bool = False
    italic: bool = False
    underline: bool = False
    strikethrough: bool = False
    letter_spacing_px: float = 0.0
    is_break: bool = False  # marks an a:br within a paragraph
    hyperlink_href: str | None = None
    formula_latex: str | None = None
    font_faces: dict[str, str] = field(default_factory=dict)
    baseline_shifted: bool = False


HyperlinkResolver = Callable[[str, str], str | None]
InlineFormulaResolver = Callable[[ET.Element], tuple[FormulaImport | None, str]]
TextDiagnosticSink = Callable[[str, str, str], None]


class TextImportError(ValueError):
    """Report malformed DrawingML text in strict import mode."""


@dataclass
class TextParagraph:
    """One <a:p>: a list of runs sharing alignment + level."""

    runs: list[TextRun] = field(default_factory=list)
    align: str = "l"  # l / ctr / r / just / dist
    level: int = 0
    indent_px: float = 0.0
    margin_left_px: float = 0.0
    line_height_ratio: float = DEFAULT_LINE_HEIGHT_RATIO
    line_spacing_px: float | None = None
    space_before_px: float = 0.0
    space_after_px: float = 0.0
    empty_line_font_size_px: float = DEFAULT_FONT_SIZE_PX
    bullet_prefix: str = ""  # rendered prefix like '• ' or '1. '
    bullet_fill: str | None = None
    bullet_fill_opacity: float = 1.0


@dataclass
class TextResult:
    """Resolved text body ready for SVG emission.

    `svg` is one or more <text> elements, already absolutely positioned
    inside the slide coordinate system. `defs` holds text gradient fills.
    `frame` carries the source box used by separately positioned vertical text.
    """

    svg: str = ""
    defs: list[str] = field(default_factory=list)
    contains_inline_formula: bool = False
    frame: tuple[float, float, float, float] | None = None


VERTICAL_TEXT_MODES = {"eaVert", "vert", "wordArtVert", "wordArtVertRtl"}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


# Stable upstream API facade, split by conversion responsibility.
from .txbody_to_svg_layout import (
    convert_txbody,
    is_vertical_txbody,
    convert_vertical_txbody,
)
from .txbody_to_svg_vertical import (
    _convert_east_asian_vertical,
    _is_east_asian_vertical_upright,
    _east_asian_vertical_segment_height,
    _fit_sideways_vertical_text,
    _emit_upright_vertical_segment,
    _emit_sideways_vertical_segment,
    _rotated_bbox,
    _looks_like_auto_stacked_cjk,
    _plain_text,
)
from .txbody_to_svg_parse import (
    _read_emu_attr,
    _parse_paragraphs,
    _parse_paragraph,
    _apply_norm_autofit,
    _font_size_px,
    _build_run,
)
from .txbody_to_svg_properties import (
    _lst_style_level_prs,
    _child_chain,
    _emu_px_attr_chain,
    _line_height_ratio,
    _spacing_points_px,
    _attr_chain,
    _typeface,
    _typeface_chain,
    _theme_script_from_lang,
    _theme_script_from_text,
    _run_theme_script,
    _resolve_theme_typeface,
    _build_font_stack,
    _quote_font,
    _resolve_bullet_prefix,
    _resolve_bullet_fill,
    _format_auto_number,
    _alpha_number,
    _roman_number,
)
from .txbody_to_svg_metrics import (
    _has_visible_text,
    _collect_text_defs,
    _is_cjk,
    _char_width,
    _estimate_run_width,
    _advance_width,
    _find_break_point,
    _wrap_paragraph_into_lines,
    _should_keep_single_line,
    _copy_run,
    _paragraph_height_from_lines,
    _line_height,
    _line_font_size,
    _clip_lines_to_bottom,
    _paragraph_height,
)
from .txbody_to_svg_emit import (
    _emit_paragraph,
    _text_base_attrs,
    _run_tspan_attrs,
    _run_tspan_markup,
    _wrap_run_hyperlink,
    _xml_escape,
)

