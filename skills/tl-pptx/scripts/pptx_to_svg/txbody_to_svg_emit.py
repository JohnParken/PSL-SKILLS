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

def _emit_paragraph(
    para: TextParagraph,
    lines: list[list[TextRun]],
    inner_x: float, inner_w: float,
    top_y: float,
    *,
    metrics_baseline: bool,
) -> str:
    """Render a paragraph (already split into lines) as one <text> element.

    Each pre-wrapped display line becomes a sequence of <tspan>s: the first
    tspan on a line carries the explicit x and dy (line-height advance);
    subsequent tspans on the same line inherit x.
    """
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .txbody_to_svg import (
        DEFAULT_LINE_HEIGHT_RATIO,
        _line_height,
        _run_tspan_attrs,
        _run_tspan_markup,
        _text_base_attrs,
        _wrap_run_hyperlink,
        _xml_escape,
    )
    align = para.align
    if align == "ctr":
        anchor_x = inner_x + inner_w / 2.0
        text_anchor = "middle"
    elif align == "r":
        anchor_x = inner_x + inner_w
        text_anchor = "end"
    else:  # 'l' / 'just' / 'dist' / unknown
        anchor_x = inner_x + para.indent_px + para.margin_left_px
        text_anchor = "start"

    if not lines:
        return ""

    visible_lines = [
        [run for run in line if not run.is_break and run.text]
        for line in lines
    ]
    first_line_idx = next(
        (index for index, line in enumerate(visible_lines) if line),
        None,
    )
    if first_line_idx is None:
        return ""

    first_run = visible_lines[first_line_idx][0]
    baseline_offset = 0.85 * first_run.font_size_px
    if metrics_baseline:
        baseline_offset = drawingml_text_baseline_offset(
            [
                {
                    'text': run.text,
                    'font_size': run.font_size_px,
                    'font_family': run.font_family,
                    'font_faces': run.font_faces,
                    'font_weight': '700' if run.bold else '400',
                    'font_style': 'italic' if run.italic else 'normal',
                }
                for run in visible_lines[first_line_idx]
            ],
            {},
            default_size=first_run.font_size_px,
            line_spacing_px=para.line_spacing_px,
            line_spacing_ratio=para.line_height_ratio / DEFAULT_LINE_HEIGHT_RATIO,
        )
    first_baseline = top_y + baseline_offset

    spans: list[str] = []
    for line_idx, line in enumerate(visible_lines):
        line_advance = None
        if line_idx > 0:
            height_line = (
                lines[line_idx - 1]
                if line_idx <= first_line_idx else lines[line_idx]
            )
            line_advance = _line_height(para, height_line)
        if not line:
            if line_advance is not None:
                spans.append(
                    f'<tspan x="{fmt_num(anchor_x)}" '
                    f'dy="{fmt_num(line_advance, 8)}"></tspan>'
                )
            continue
        line_has_hyperlink = any(run.hyperlink_href for run in line)
        if line_has_hyperlink:
            run_spans = ''.join(
                _wrap_run_hyperlink(
                    _run_tspan_markup(run),
                    run,
                )
                for run in line
            )
            position_attrs = (
                f' x="{fmt_num(anchor_x)}" dy="{fmt_num(line_advance, 8)}"'
                if line_advance is not None
                else ''
            )
            spans.append(f'<tspan{position_attrs}>{run_spans}</tspan>')
            continue
        for run_idx, run in enumerate(line):
            attrs = _run_tspan_attrs(run)
            if run_idx == 0 and line_advance is not None:
                if run.formula_latex is not None:
                    spans.append(
                        f'<tspan x="{fmt_num(anchor_x)}" '
                        f'dy="{fmt_num(line_advance, 8)}">'
                        f'{_run_tspan_markup(run)}</tspan>'
                    )
                else:
                    spans.append(
                        f'<tspan x="{fmt_num(anchor_x)}" '
                        f'dy="{fmt_num(line_advance, 8)}"'
                        f'{attrs}>{_xml_escape(run.text)}</tspan>'
                    )
            else:
                spans.append(_run_tspan_markup(run))

    # Formula paragraphs carry alpha per run, so a translucent first run must
    # not make opaque formulas or surrounding text inherit that alpha.
    base_attrs = _text_base_attrs(
        first_run, anchor_x, first_baseline, text_anchor,
        inherit_opacity=not any(run.formula_latex is not None for line in visible_lines for run in line),
    )
    if metrics_baseline:
        base_attrs += ' data-pptx-text-baseline="metrics-v1"'
    return f"<text{base_attrs}>{''.join(spans)}</text>"


def _text_base_attrs(run: TextRun | None, x: float, y: float,
                     text_anchor: str, *, inherit_opacity: bool = True) -> str:
    parts = [
        f'x="{fmt_num(x)}"',
        f'y="{fmt_num(y, 8)}"',
        f'text-anchor="{text_anchor}"',
        'xml:space="preserve"',
    ]
    if run is None:
        return " " + " ".join(parts)
    parts.append(f'font-family="{run.font_family}"')
    parts.append(f'font-size="{fmt_num(run.font_size_px)}"')
    parts.append(f'fill="{run.fill}"')
    if inherit_opacity and run.fill_opacity < 1.0:
        parts.append(
            f'fill-opacity="{format_ooxml_alpha(run.fill_opacity)}"'
        )
    if run.bold:
        parts.append('font-weight="bold"')
    if run.italic:
        parts.append('font-style="italic"')
    if run.underline and run.strikethrough:
        parts.append('text-decoration="underline line-through"')
    elif run.underline:
        parts.append('text-decoration="underline"')
    elif run.strikethrough:
        parts.append('text-decoration="line-through"')
    if run.letter_spacing_px:
        parts.append(f'letter-spacing="{fmt_num(run.letter_spacing_px)}"')
    return " " + " ".join(parts)


def _run_tspan_attrs(run: TextRun) -> str:
    """Emit resolved run styles, including resets of the parent text style."""
    parts = [
        f'fill="{run.fill}"',
        f'font-family="{run.font_family}"',
        f'font-size="{fmt_num(run.font_size_px)}"',
        f'fill-opacity="{format_ooxml_alpha(run.fill_opacity)}"',
        f'font-weight="{"bold" if run.bold else "normal"}"',
        f'font-style="{"italic" if run.italic else "normal"}"',
    ]
    if run.underline and run.strikethrough:
        parts.append('text-decoration="underline line-through"')
    elif run.underline:
        parts.append('text-decoration="underline"')
    elif run.strikethrough:
        parts.append('text-decoration="line-through"')
    else:
        parts.append('text-decoration="none"')
    if run.letter_spacing_px:
        parts.append(f'letter-spacing="{fmt_num(run.letter_spacing_px)}"')
    return " " + " ".join(parts)


def _run_tspan_markup(run: TextRun) -> str:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .txbody_to_svg import (
        _run_tspan_attrs,
        _xml_escape,
    )
    formula_attr = ""
    if run.formula_latex is not None:
        formula_attr = (
            ' data-pptx-inline-formula="'
            + _xml_escape(run.formula_latex)
            + '"'
        )
    return (
        f"<tspan{_run_tspan_attrs(run)}{formula_attr}>"
        f"{_xml_escape(run.text)}</tspan>"
    )


def _wrap_run_hyperlink(markup: str, run: TextRun) -> str:
    """Wrap one visible SVG run in the canonical hyperlink carrier."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .txbody_to_svg import (
        _xml_escape,
    )
    if not run.hyperlink_href:
        return markup
    provenance = (
        f' {SOURCE_HREF_ATTR}="{_xml_escape(run.hyperlink_href)}"'
        if run.hyperlink_href.startswith('#slide-') else ''
    )
    return f'<a href="{_xml_escape(run.hyperlink_href)}"{provenance}>{markup}</a>'


def _xml_escape(text: str) -> str:
    return (text.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
                .replace('"', "&quot;"))
