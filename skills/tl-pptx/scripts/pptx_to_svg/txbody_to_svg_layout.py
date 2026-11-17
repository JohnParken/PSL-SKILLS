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
from .txbody_to_svg import TextRun

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
    DEFAULT_FILL_HEX,
    DEFAULT_FONT_SIZE_PX,
    HyperlinkResolver,
    InlineFormulaResolver,
    TextDiagnosticSink,
    TextResult,
)

def convert_txbody(
    tx_body: ET.Element | None,
    xfrm: Xfrm,
    palette: ColorPalette | None,
    *,
    theme_fonts: dict[str, str] | None = None,
    slide_number: int | None = None,
    default_fill: str = DEFAULT_FILL_HEX,
    default_font_size_px: float = DEFAULT_FONT_SIZE_PX,
    fallback_lst_styles: tuple[ET.Element, ...] = (),
    fallback_run_props: tuple[ET.Element, ...] = (),
    id_prefix: str = "txt",
    id_seq: list[int] | None = None,
    hyperlink_resolver: HyperlinkResolver | None = None,
    inline_formula_resolver: InlineFormulaResolver | None = None,
    strict: bool = False,
    diagnostic_sink: TextDiagnosticSink | None = None,
) -> TextResult:
    """Convert <p:txBody> under the given shape geometry to SVG <text>(s)."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .txbody_to_svg import (
        DEFAULT_INSETS_EMU,
        TextResult,
        _apply_norm_autofit,
        _clip_lines_to_bottom,
        _collect_text_defs,
        _emit_paragraph,
        _has_visible_text,
        _paragraph_height_from_lines,
        _parse_paragraphs,
        _read_emu_attr,
        _wrap_paragraph_into_lines,
    )
    if tx_body is None:
        return TextResult()

    body_pr = tx_body.find("a:bodyPr", NS)
    paragraphs = _parse_paragraphs(
        tx_body, palette, theme_fonts or {}, default_fill=default_fill,
        default_font_size_px=default_font_size_px,
        fallback_lst_styles=fallback_lst_styles,
        fallback_run_props=fallback_run_props,
        slide_number=slide_number, id_prefix=id_prefix, id_seq=id_seq,
        hyperlink_resolver=hyperlink_resolver,
        inline_formula_resolver=inline_formula_resolver,
        strict=strict,
        diagnostic_sink=diagnostic_sink,
    )
    if not paragraphs or not _has_visible_text(paragraphs):
        return TextResult()
    _apply_norm_autofit(body_pr, paragraphs)

    # Insets + anchor + wrap
    lins = _read_emu_attr(body_pr, "lIns", DEFAULT_INSETS_EMU["l"])
    tins = _read_emu_attr(body_pr, "tIns", DEFAULT_INSETS_EMU["t"])
    rins = _read_emu_attr(body_pr, "rIns", DEFAULT_INSETS_EMU["r"])
    bins = _read_emu_attr(body_pr, "bIns", DEFAULT_INSETS_EMU["b"])
    anchor = body_pr.attrib.get("anchor", "t") if body_pr is not None else "t"
    wrap_mode = body_pr.attrib.get("wrap", "square") if body_pr is not None else "square"
    # DrawingML defaults to overflow: a fixed frame does not hide later lines.
    clip_vertical = (
        body_pr is not None
        and body_pr.attrib.get("vertOverflow") in {"clip", "ellipsis"}
    )
    respect_edge_spacing = (
        body_pr is not None
        and body_pr.attrib.get("spcFirstLastPara") in {"1", "true"}
    )

    inner_x = xfrm.x + lins
    inner_y = xfrm.y + tins
    inner_w = max(xfrm.w - lins - rins, 1.0)
    inner_h = max(xfrm.h - tins - bins, 1.0)

    # Pre-wrap each paragraph into concrete display lines.
    wrap_width = inner_w if wrap_mode == "square" else float("inf")
    para_lines: list[list[list[TextRun]]] = [
        _wrap_paragraph_into_lines(p, wrap_width) for p in paragraphs
    ]

    # Pre-compute heights to support anchor=ctr / b
    para_heights = [
        _paragraph_height_from_lines(p, lines)
        for p, lines in zip(paragraphs, para_lines)
    ]
    space_before = [paragraph.space_before_px for paragraph in paragraphs]
    space_after = [paragraph.space_after_px for paragraph in paragraphs]
    if not respect_edge_spacing:
        space_before[0] = 0.0
        space_after[-1] = 0.0
    total_h = sum(
        before + height + after
        for before, height, after in zip(
            space_before,
            para_heights,
            space_after,
        )
    )
    anchor_space = inner_h - total_h
    # Overflow stays anchored: centered text grows both ways, bottom text up.
    # Explicit clipping keeps the leading lines inside the frame instead.
    if clip_vertical:
        anchor_space = max(0.0, anchor_space)
    if anchor == "ctr":
        cursor_y = inner_y + anchor_space / 2.0
    elif anchor == "b":
        cursor_y = inner_y + anchor_space
    else:
        cursor_y = inner_y

    bottom_y = inner_y + inner_h
    text_blocks: list[str] = []
    # Semantic rebuilding can merge paragraphs back into one native body.
    # Keep one baseline convention throughout that body when math or shifted
    # runs require the exporter's separate vertical-extent model.
    metrics_baseline = not any(
        run.formula_latex is not None or run.baseline_shifted
        for para in paragraphs for run in para.runs
    )
    for para, lines, height, before, after in zip(
        paragraphs,
        para_lines,
        para_heights,
        space_before,
        space_after,
    ):
        cursor_y += before
        visible_lines = (
            _clip_lines_to_bottom(para, lines, cursor_y, bottom_y)
            if clip_vertical else lines
        )
        if visible_lines:
            text_blocks.append(
                _emit_paragraph(
                    para, visible_lines, inner_x, inner_w, cursor_y,
                    metrics_baseline=metrics_baseline,
                )
            )
        cursor_y += height + after
        if clip_vertical and cursor_y >= bottom_y:
            break

    svg = "\n".join(text_blocks)
    return TextResult(
        svg=svg,
        defs=_collect_text_defs(paragraphs),
        contains_inline_formula="data-pptx-inline-formula=" in svg,
    )


def is_vertical_txbody(tx_body: ET.Element | None, xfrm: Xfrm | None = None) -> bool:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .txbody_to_svg import (
        VERTICAL_TEXT_MODES,
        _looks_like_auto_stacked_cjk,
    )
    if tx_body is None:
        return False
    body_pr = tx_body.find("a:bodyPr", NS)
    if body_pr is None:
        return False
    if body_pr.attrib.get("vert") in VERTICAL_TEXT_MODES:
        return True
    return _looks_like_auto_stacked_cjk(tx_body, body_pr, xfrm)


def convert_vertical_txbody(
    tx_body: ET.Element | None,
    xfrm: Xfrm,
    palette: ColorPalette | None,
    *,
    theme_fonts: dict[str, str] | None = None,
    slide_number: int | None = None,
    default_fill: str = DEFAULT_FILL_HEX,
    default_font_size_px: float = DEFAULT_FONT_SIZE_PX,
    fallback_lst_styles: tuple[ET.Element, ...] = (),
    fallback_run_props: tuple[ET.Element, ...] = (),
    id_prefix: str = "txt",
    id_seq: list[int] | None = None,
    hyperlink_resolver: HyperlinkResolver | None = None,
    inline_formula_resolver: InlineFormulaResolver | None = None,
    strict: bool = False,
    diagnostic_sink: TextDiagnosticSink | None = None,
) -> TextResult:
    """Render East Asian vertical text as upright stacked glyphs.

    PowerPoint often combines ``bodyPr@vert=eaVert`` with a rotated text box.
    Rendering the text inside the rotated shape group makes Chinese glyphs lie
    sideways. This helper computes the final rotated box and places glyphs
    upright in slide coordinates.
    """
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .txbody_to_svg import (
        TextResult,
        _apply_norm_autofit,
        _collect_text_defs,
        _convert_east_asian_vertical,
        _parse_paragraphs,
        _rotated_bbox,
        _run_tspan_attrs,
        _text_base_attrs,
        _wrap_run_hyperlink,
        _xml_escape,
    )
    if tx_body is None:
        return TextResult()

    body_pr = tx_body.find("a:bodyPr", NS)
    paragraphs = _parse_paragraphs(
        tx_body, palette, theme_fonts or {}, default_fill=default_fill,
        default_font_size_px=default_font_size_px,
        fallback_lst_styles=fallback_lst_styles,
        fallback_run_props=fallback_run_props,
        slide_number=slide_number, id_prefix=id_prefix, id_seq=id_seq,
        hyperlink_resolver=hyperlink_resolver,
        inline_formula_resolver=inline_formula_resolver,
        strict=strict,
        diagnostic_sink=diagnostic_sink,
    )
    _apply_norm_autofit(body_pr, paragraphs)
    if body_pr is not None and body_pr.attrib.get("vert") == "eaVert":
        return _convert_east_asian_vertical(
            paragraphs,
            xfrm,
            body_pr,
        )

    runs = [
        run
        for para in paragraphs
        for run in para.runs
        if not run.is_break and run.text
    ]
    if not runs:
        return TextResult()

    box_x, box_y, box_w, box_h = _rotated_bbox(xfrm)
    center_x = box_x + box_w / 2.0
    clip_vertical = (
        body_pr is not None
        and body_pr.attrib.get("vertOverflow") in {"clip", "ellipsis"}
    )

    glyphs: list[tuple[str, TextRun]] = []
    for run in runs:
        for char in run.text:
            glyphs.append((" " if char in "\t\r\n" else char, run))

    if not glyphs:
        return TextResult()

    advances = [glyph_run.font_size_px * 1.05 for _, glyph_run in glyphs]
    total_h = sum(advances)
    top_y = box_y + max(0.0, (box_h - total_h) / 2.0)

    bottom_y = box_y + box_h
    spans: list[str] = []
    cursor_y = top_y
    first_run: TextRun | None = None
    first_baseline: float | None = None
    previous_baseline: float | None = None
    for (char, run), advance in zip(glyphs, advances):
        if clip_vertical and cursor_y + advance > bottom_y:
            break
        baseline_y = cursor_y + run.font_size_px * 0.85
        tspan_attrs = _run_tspan_attrs(run)
        if first_run is None:
            first_run = run
            first_baseline = baseline_y
            run_span = f"<tspan{tspan_attrs}>{_xml_escape(char)}</tspan>"
            spans.append(_wrap_run_hyperlink(run_span, run))
        else:
            dy = baseline_y - (previous_baseline or baseline_y)
            run_span = f"<tspan{tspan_attrs}>{_xml_escape(char)}</tspan>"
            linked_span = _wrap_run_hyperlink(run_span, run)
            spans.append(
                f'<tspan x="{fmt_num(center_x)}" dy="{fmt_num(dy)}">'
                f"{linked_span}</tspan>"
            )
        previous_baseline = baseline_y
        cursor_y += advance

    if first_run is None or first_baseline is None:
        return TextResult()

    attrs = _text_base_attrs(first_run, center_x, first_baseline, "middle")
    return TextResult(
        svg=f"<text{attrs}>{''.join(spans)}</text>",
        defs=_collect_text_defs(paragraphs),
        contains_inline_formula=False,
        frame=(box_x, box_y, box_w, box_h),
    )


