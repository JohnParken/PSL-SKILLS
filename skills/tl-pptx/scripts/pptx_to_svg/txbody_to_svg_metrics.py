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

# Upstream MIT, Hugo He 2025-2026; algorithms retained during module split.
from .txbody_to_svg import (
    TextParagraph,
    TextRun,
)

def _has_visible_text(paragraphs: list[TextParagraph]) -> bool:
    for p in paragraphs:
        for r in p.runs:
            # Structured export writes U+200B only to keep a visually blank
            # placeholder carrier alive in DrawingML. Restore that transport
            # sentinel to an empty SVG carrier on re-import.
            if r.text.replace("\u200b", "").strip():
                return True
    return False


def _collect_text_defs(paragraphs: list[TextParagraph]) -> list[str]:
    """Return unique text fill defs referenced by parsed runs."""
    defs: list[str] = []
    seen: set[str] = set()
    for para in paragraphs:
        for run in para.runs:
            for item in run.defs:
                if item not in seen:
                    defs.append(item)
                    seen.add(item)
    return defs


# ---------------------------------------------------------------------------
# Word-wrap / text measurement
# ---------------------------------------------------------------------------

def _is_cjk(ch: str) -> bool:
    """Check if a character is CJK (Chinese/Japanese/Korean) or full-width."""
    return is_cjk_char(ch)


def _char_width(ch: str, font_size: float, bold: bool) -> float:
    """Estimate a single character's rendered width in pixels.

    Mirrors svg_to_pptx/drawingml/utils.py estimate_text_width so wrapping breaks
    align with the same heuristic used to estimate text-box sizes elsewhere.
    """
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .txbody_to_svg import (
        _is_cjk,
    )
    if _is_cjk(ch):
        w = font_size  # CJK is approximately 1em per glyph
    elif ch == ' ':
        w = font_size * 0.3
    elif ch in 'mMwWOQ%':
        w = font_size * 0.75
    elif ch in 'iIlj!|':
        w = font_size * 0.3
    elif ch.isdigit():
        # digits are tabular (uniform ~0.55em) in most UI fonts, including
        # '1' — classing it with 'il|' under-sizes the width and makes
        # renderers that ignore wrap="none" (LibreOffice) wrap the line
        w = font_size * 0.55
    else:
        w = font_size * 0.55
    # Bold Latin generally expands a little. CJK glyphs keep their em advance
    # in common PPT fonts; applying the bold multiplier causes short Chinese
    # titles such as "少年强国说" to wrap even though PowerPoint keeps them on
    # one line.
    if bold and not _is_cjk(ch):
        w *= 1.05
    return w


def _estimate_run_width(text: str, run: TextRun) -> float:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .txbody_to_svg import (
        _char_width,
    )
    glyph_width = sum(_char_width(c, run.font_size_px, run.bold) for c in text)
    tracking_width = run.letter_spacing_px * max(len(text) - 1, 0)
    return (glyph_width + tracking_width) * 1.05


def _advance_width(ch: str, index_in_segment: int, run: TextRun) -> float:
    """Return the width added by one character inside a measured line segment."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .txbody_to_svg import (
        _char_width,
    )
    tracking = run.letter_spacing_px if index_in_segment > 0 else 0.0
    return _char_width(ch, run.font_size_px, run.bold) + tracking


def _find_break_point(
    text: str, start: int, max_width: float, run: TextRun,
) -> tuple[int, float]:
    """Find the longest prefix of text[start:] that fits in max_width.

    Returns (end_index, used_width). Prefers breaking after whitespace, after
    CJK characters, or after hyphens. If even the first character doesn't fit,
    returns (start, 0.0) — the caller should flush the current line first.
    """
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .txbody_to_svg import (
        _advance_width,
        _is_cjk,
    )
    cur_w = 0.0
    last_break = start
    last_break_w = 0.0

    for i in range(start, len(text)):
        ch = text[i]
        ch_w = _advance_width(ch, i - start, run)
        if cur_w + ch_w > max_width:
            if last_break > start:
                return last_break, last_break_w
            return start, 0.0
        cur_w += ch_w
        # Update last_break point
        if ch.isspace() or _is_cjk(ch) or ch in "-—、，。！？：；":
            last_break = i + 1
            last_break_w = cur_w
    # Whole rest fits
    return len(text), cur_w


def _wrap_paragraph_into_lines(
    para: TextParagraph,
    max_width: float,
) -> list[list[TextRun]]:
    """Split a paragraph's runs into display lines respecting `max_width`.

    Each line is a list of (possibly truncated) TextRuns. Explicit a:br runs
    force a new line. When max_width is +inf the original runs are returned
    unchanged (one logical line per a:br segment).

    Bullet prefix is prepended to the first non-empty run if present.
    """
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .txbody_to_svg import (
        _advance_width,
        _copy_run,
        _estimate_run_width,
        _find_break_point,
        _should_keep_single_line,
    )
    lines: list[list[TextRun]] = [[]]
    cur_w = 0.0

    if _should_keep_single_line(para, max_width):
        return [[_copy_run(run, text=run.text) for run in para.runs if not run.is_break and run.text]]

    if para.bullet_prefix and para.runs:
        first_run = next((r for r in para.runs if not r.is_break), None)
        if first_run is not None:
            bullet_run = _copy_run(first_run, text=para.bullet_prefix)
            if para.bullet_fill is not None:
                bullet_run.fill = para.bullet_fill
                bullet_run.fill_opacity = para.bullet_fill_opacity
            hanging_width = max(-para.indent_px, 0.0)
            measured_width = _estimate_run_width(para.bullet_prefix, bullet_run)
            if hanging_width > measured_width:
                space_width = _estimate_run_width(" ", bullet_run)
                extra_spaces = round(
                    (hanging_width - measured_width) / space_width
                )
                bullet_run.text += " " * max(extra_spaces, 0)
            lines[-1].append(bullet_run)
            cur_w = _estimate_run_width(bullet_run.text, bullet_run)

    for run in para.runs:
        if run.is_break:
            # Keep the break on the line it terminates so consecutive breaks
            # form a break-only empty line. Visible text owns a non-empty
            # line's height; the break rPr owns only that empty line.
            lines[-1].append(run)
            lines.append([])
            cur_w = 0.0
            continue
        if not run.text:
            continue
        if run.formula_latex is not None:
            width = _estimate_run_width(run.text, run)
            if lines[-1] and cur_w + width > max_width:
                lines.append([])
                cur_w = 0.0
            lines[-1].append(_copy_run(run, text=run.text))
            cur_w += width
            continue
        text = run.text
        i = 0
        while i < len(text):
            avail = max_width - cur_w
            if avail <= 0 and lines[-1]:
                # Line is full; start a new one
                lines.append([])
                cur_w = 0.0
                avail = max_width

            end, used = _find_break_point(text, i, avail, run)
            if end == i:
                # Nothing fits even from a fresh line — force one char to avoid
                # an infinite loop.
                if lines[-1]:
                    lines.append([])
                    cur_w = 0.0
                    continue
                end = i + 1
                used = _advance_width(text[i], 0, run)

            chunk = text[i:end]
            lines[-1].append(_copy_run(run, text=chunk))
            cur_w += used
            i = end

            if i < len(text):
                # More to render — wrap to next line
                lines.append([])
                cur_w = 0.0

    return lines


def _should_keep_single_line(para: TextParagraph, max_width: float) -> bool:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .txbody_to_svg import (
        _estimate_run_width,
    )
    if max_width == float("inf") or para.bullet_prefix:
        return False
    if any(run.is_break for run in para.runs):
        return False

    text_runs = [run for run in para.runs if run.text]
    if not text_runs:
        return False
    text = "".join(run.text for run in text_runs)
    non_space_count = sum(1 for ch in text if not ch.isspace())

    # Short labels/titles are usually intentionally single-line in PPT. Let
    # them overflow slightly rather than inventing a line break from imperfect
    # font metrics or alignment spaces.
    if non_space_count <= 18:
        return True

    estimated = sum(_estimate_run_width(run.text, run) for run in text_runs)
    return estimated <= max_width * 1.12


def _copy_run(run: TextRun, *, text: str) -> TextRun:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .txbody_to_svg import (
        TextRun,
    )
    return TextRun(
        text=text,
        font_size_px=run.font_size_px,
        font_family=run.font_family,
        fill=run.fill,
        fill_opacity=run.fill_opacity,
        defs=list(run.defs),
        bold=run.bold,
        italic=run.italic,
        underline=run.underline,
        strikethrough=run.strikethrough,
        letter_spacing_px=run.letter_spacing_px,
        hyperlink_href=run.hyperlink_href,
        formula_latex=run.formula_latex,
        font_faces=dict(run.font_faces),
        baseline_shifted=run.baseline_shifted,
    )


def _paragraph_height_from_lines(p: TextParagraph,
                                 lines: list[list[TextRun]]) -> float:
    """Total px height after wrapping. Each line uses its own max font size."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .txbody_to_svg import (
        _line_height,
    )
    if not lines:
        return _line_height(p, [])
    height = 0.0
    for line in lines:
        height += _line_height(p, line)
    return height


def _line_height(p: TextParagraph, line: list[TextRun]) -> float:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .txbody_to_svg import (
        _line_font_size,
    )
    if p.line_spacing_px is not None:
        return p.line_spacing_px
    return _line_font_size(p, line) * p.line_height_ratio


def _line_font_size(p: TextParagraph, line: list[TextRun]) -> float:
    visible_sizes = [
        run.font_size_px
        for run in line
        if not run.is_break and run.text
    ]
    if visible_sizes:
        return max(visible_sizes)
    break_sizes = [run.font_size_px for run in line if run.is_break]
    return max(break_sizes, default=p.empty_line_font_size_px)


def _clip_lines_to_bottom(
    para: TextParagraph,
    lines: list[list[TextRun]],
    top_y: float,
    bottom_y: float,
) -> list[list[TextRun]]:
    """Return the leading display lines whose line boxes fit in the text frame."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .txbody_to_svg import (
        _line_height,
    )
    visible: list[list[TextRun]] = []
    cursor_y = top_y
    for line in lines:
        line_h = _line_height(para, line)
        # Approximate explicit clip/ellipsis at line boundaries. Default
        # overflow bypasses this filter; it must not discard source text.
        if cursor_y >= bottom_y:
            break
        visible.append(line)
        cursor_y += line_h
    return visible


def _paragraph_height(p: TextParagraph) -> float:
    """Legacy helper kept for callers that don't pre-wrap (currently unused)."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .txbody_to_svg import (
        _paragraph_height_from_lines,
        _wrap_paragraph_into_lines,
    )
    lines = _wrap_paragraph_into_lines(p, float("inf"))
    return _paragraph_height_from_lines(p, lines)


