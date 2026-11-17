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
    TextResult,
    TextRun,
)

def _convert_east_asian_vertical(
    paragraphs: list[TextParagraph],
    xfrm: Xfrm,
    body_pr: ET.Element,
) -> TextResult:
    """Render eaVert columns with upright CJK and sideways Latin runs."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .txbody_to_svg import (
        DEFAULT_INSETS_EMU,
        TextResult,
        _collect_text_defs,
        _east_asian_vertical_segment_height,
        _emit_sideways_vertical_segment,
        _emit_upright_vertical_segment,
        _estimate_run_width,
        _fit_sideways_vertical_text,
        _is_east_asian_vertical_upright,
        _read_emu_attr,
        _rotated_bbox,
    )
    columns: list[list[tuple[bool, str, TextRun]]] = []
    for paragraph in paragraphs:
        column: list[tuple[bool, str, TextRun]] = []
        for run in paragraph.runs:
            if run.is_break:
                if column:
                    columns.append(column)
                    column = []
                continue
            start = 0
            while start < len(run.text):
                upright = _is_east_asian_vertical_upright(run.text[start])
                end = start + 1
                while (
                    end < len(run.text)
                    and _is_east_asian_vertical_upright(run.text[end]) == upright
                ):
                    end += 1
                column.append((upright, run.text[start:end], run))
                start = end
        if column:
            columns.append(column)

    if not columns:
        return TextResult()

    box_x, box_y, box_w, box_h = _rotated_bbox(xfrm)
    lins = _read_emu_attr(body_pr, "lIns", DEFAULT_INSETS_EMU["l"])
    tins = _read_emu_attr(body_pr, "tIns", DEFAULT_INSETS_EMU["t"])
    rins = _read_emu_attr(body_pr, "rIns", DEFAULT_INSETS_EMU["r"])
    bins = _read_emu_attr(body_pr, "bIns", DEFAULT_INSETS_EMU["b"])
    inner_x = box_x + lins
    inner_y = box_y + tins
    inner_w = max(box_w - lins - rins, 1.0)
    inner_h = max(box_h - tins - bins, 1.0)

    column_widths = [
        max(run.font_size_px * 1.05 for _upright, _text, run in column)
        for column in columns
    ]
    total_width = sum(column_widths)
    right_x = inner_x + min(inner_w, (inner_w + total_width) / 2.0)
    column_centers: list[float] = []
    for width in column_widths:
        column_centers.append(right_x - width / 2.0)
        right_x -= width

    anchor = body_pr.attrib.get("anchor", "t")
    clip_vertical = body_pr.attrib.get("vertOverflow") in {"clip", "ellipsis"}
    bottom_y = inner_y + inner_h
    text_blocks: list[str] = []
    for column, center_x in zip(columns, column_centers):
        content_height = sum(
            _east_asian_vertical_segment_height(upright, text, run)
            for upright, text, run in column
        )
        if anchor == "ctr":
            cursor_y = inner_y + max(0.0, (inner_h - content_height) / 2.0)
        elif anchor == "b":
            cursor_y = inner_y + max(0.0, inner_h - content_height)
        else:
            cursor_y = inner_y

        for upright, text, run in column:
            available = bottom_y - cursor_y
            if clip_vertical and available <= 0:
                break
            if upright:
                advance = run.font_size_px * 1.05
                visible_count = (
                    min(len(text), int(available // advance))
                    if clip_vertical else len(text)
                )
                visible = text[:visible_count]
                if not visible:
                    break
                text_blocks.append(
                    _emit_upright_vertical_segment(
                        visible,
                        run,
                        center_x,
                        cursor_y,
                        advance,
                    )
                )
                cursor_y += advance * visible_count
                if visible_count < len(text):
                    break
                continue

            visible = (
                _fit_sideways_vertical_text(text, run, available)
                if clip_vertical else text
            )
            if not visible:
                break
            text_blocks.append(
                _emit_sideways_vertical_segment(
                    visible,
                    run,
                    center_x,
                    cursor_y,
                )
            )
            cursor_y += _estimate_run_width(visible, run)
            if len(visible) < len(text):
                break

    return TextResult(
        svg="\n".join(text_blocks),
        defs=_collect_text_defs(paragraphs),
        contains_inline_formula=False,
        frame=(box_x, box_y, box_w, box_h),
    )


def _is_east_asian_vertical_upright(char: str) -> bool:
    """Keep East Asian wide/full-width glyphs upright in eaVert text."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .txbody_to_svg import (
        _is_cjk,
    )
    return _is_cjk(char) or east_asian_width(char) in {"W", "F"}


def _east_asian_vertical_segment_height(
    upright: bool,
    text: str,
    run: TextRun,
) -> float:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .txbody_to_svg import (
        _estimate_run_width,
    )
    if upright:
        return len(text) * run.font_size_px * 1.05
    return _estimate_run_width(text, run)


def _fit_sideways_vertical_text(
    text: str,
    run: TextRun,
    available: float,
) -> str:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .txbody_to_svg import (
        _estimate_run_width,
    )
    end = 0
    for index in range(1, len(text) + 1):
        if _estimate_run_width(text[:index], run) > available:
            break
        end = index
    return text[:end]


def _emit_upright_vertical_segment(
    text: str,
    run: TextRun,
    center_x: float,
    top_y: float,
    advance: float,
) -> str:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .txbody_to_svg import (
        _text_base_attrs,
        _wrap_run_hyperlink,
        _xml_escape,
    )
    first_baseline = top_y + run.font_size_px * 0.85
    attrs = _text_base_attrs(run, center_x, first_baseline, "middle")
    spans = [_xml_escape(text[0])]
    for char in text[1:]:
        spans.append(
            f'<tspan x="{fmt_num(center_x)}" dy="{fmt_num(advance)}">'
            f"{_xml_escape(char)}</tspan>"
        )
    markup = f"<text{attrs}>{''.join(spans)}</text>"
    return _wrap_run_hyperlink(markup, run)


def _emit_sideways_vertical_segment(
    text: str,
    run: TextRun,
    center_x: float,
    top_y: float,
) -> str:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .txbody_to_svg import (
        _text_base_attrs,
        _wrap_run_hyperlink,
        _xml_escape,
    )
    baseline_x = center_x - run.font_size_px * 0.3
    start_y = top_y + run.font_size_px * 0.1
    attrs = _text_base_attrs(run, baseline_x, start_y, "start")
    transform = (
        f' transform="rotate(90 {fmt_num(baseline_x)} {fmt_num(start_y)})"'
    )
    markup = f"<text{attrs}{transform}>{_xml_escape(text)}</text>"
    return _wrap_run_hyperlink(markup, run)


def _rotated_bbox(xfrm: Xfrm) -> tuple[float, float, float, float]:
    rot = round(xfrm.rot) % 360
    cx = xfrm.x + xfrm.w / 2.0
    cy = xfrm.y + xfrm.h / 2.0
    if rot in (90, 270):
        return cx - xfrm.h / 2.0, cy - xfrm.w / 2.0, xfrm.h, xfrm.w
    return xfrm.x, xfrm.y, xfrm.w, xfrm.h


def _looks_like_auto_stacked_cjk(
    tx_body: ET.Element,
    body_pr: ET.Element,
    xfrm: Xfrm | None,
) -> bool:
    """Detect PowerPoint's narrow-box CJK vertical layout without vert=eaVert."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .txbody_to_svg import (
        DEFAULT_FONT_SIZE_PX,
        DEFAULT_INSETS_EMU,
        _is_cjk,
        _plain_text,
        _read_emu_attr,
    )
    if xfrm is None or xfrm.w <= 0 or xfrm.h <= 0:
        return False
    if body_pr.attrib.get("wrap", "square") != "square":
        return False
    if xfrm.w > 64 or xfrm.h < xfrm.w * 2.4:
        return False

    text = _plain_text(tx_body)
    chars = [ch for ch in text if not ch.isspace()]
    if len(chars) < 3 or len(chars) > 16:
        return False
    cjk_count = sum(1 for ch in chars if _is_cjk(ch))
    if cjk_count / len(chars) < 0.8:
        return False

    lins = _read_emu_attr(body_pr, "lIns", DEFAULT_INSETS_EMU["l"])
    rins = _read_emu_attr(body_pr, "rIns", DEFAULT_INSETS_EMU["r"])
    inner_w = max(xfrm.w - lins - rins, 1.0)
    return inner_w <= DEFAULT_FONT_SIZE_PX


def _plain_text(tx_body: ET.Element) -> str:
    """Return concatenated literal text for layout heuristics."""
    parts: list[str] = []
    for text_elem in tx_body.findall(".//a:t", NS):
        if text_elem.text:
            parts.append(text_elem.text)
    return "".join(parts)


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

