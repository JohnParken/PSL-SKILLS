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
    DEFAULT_FILL_HEX,
    DEFAULT_FONT_SIZE_PX,
    HyperlinkResolver,
    InlineFormulaResolver,
    TextDiagnosticSink,
    TextParagraph,
    TextRun,
)

def _read_emu_attr(elem: ET.Element | None, attr: str, default_emu: int) -> float:
    """Read an EMU integer attribute and return px."""
    if elem is None:
        return emu_to_px(default_emu)
    val = elem.attrib.get(attr)
    if val is None:
        return emu_to_px(default_emu)
    try:
        return emu_to_px(int(val))
    except ValueError:
        return emu_to_px(default_emu)


def _parse_paragraphs(
    tx_body: ET.Element,
    palette: ColorPalette | None,
    theme_fonts: dict[str, str],
    *,
    default_fill: str = DEFAULT_FILL_HEX,
    default_font_size_px: float = DEFAULT_FONT_SIZE_PX,
    fallback_lst_styles: tuple[ET.Element, ...] = (),
    fallback_run_props: tuple[ET.Element, ...] = (),
    slide_number: int | None = None,
    id_prefix: str = "txt",
    id_seq: list[int] | None = None,
    hyperlink_resolver: HyperlinkResolver | None = None,
    inline_formula_resolver: InlineFormulaResolver | None = None,
    strict: bool = False,
    diagnostic_sink: TextDiagnosticSink | None = None,
) -> list[TextParagraph]:
    """Walk <a:p> children producing TextParagraph objects."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .txbody_to_svg import (
        _parse_paragraph,
    )
    paragraphs: list[TextParagraph] = []
    autonum_state: dict[int, int] = {}
    lst_style = tx_body.find("a:lstStyle", NS)
    lst_styles = (
        (lst_style,) + fallback_lst_styles
        if lst_style is not None else fallback_lst_styles
    )

    for p_elem in tx_body.findall("a:p", NS):
        para = _parse_paragraph(
            p_elem, palette, theme_fonts, autonum_state,
            lst_styles=lst_styles,
            fallback_run_props=fallback_run_props,
            default_fill=default_fill,
            default_font_size_px=default_font_size_px,
            slide_number=slide_number,
            id_prefix=id_prefix, id_seq=id_seq,
            hyperlink_resolver=hyperlink_resolver,
            inline_formula_resolver=inline_formula_resolver,
            strict=strict,
            diagnostic_sink=diagnostic_sink,
        )
        paragraphs.append(para)

    return paragraphs


def _parse_paragraph(
    p_elem: ET.Element,
    palette: ColorPalette | None,
    theme_fonts: dict[str, str],
    autonum_state: dict[int, int],
    *,
    lst_styles: tuple[ET.Element, ...] = (),
    fallback_run_props: tuple[ET.Element, ...] = (),
    default_fill: str = DEFAULT_FILL_HEX,
    default_font_size_px: float = DEFAULT_FONT_SIZE_PX,
    slide_number: int | None = None,
    id_prefix: str = "txt",
    id_seq: list[int] | None = None,
    hyperlink_resolver: HyperlinkResolver | None = None,
    inline_formula_resolver: InlineFormulaResolver | None = None,
    strict: bool = False,
    diagnostic_sink: TextDiagnosticSink | None = None,
) -> TextParagraph:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .txbody_to_svg import (
        TextParagraph,
        TextRun,
        _attr_chain,
        _build_run,
        _child_chain,
        _emu_px_attr_chain,
        _font_size_px,
        _line_height_ratio,
        _lst_style_level_prs,
        _resolve_bullet_fill,
        _resolve_bullet_prefix,
        _spacing_points_px,
    )
    para = TextParagraph()

    p_pr = p_elem.find("a:pPr", NS)
    if p_pr is not None:
        try:
            para.level = int(p_pr.attrib.get("lvl", "0"))
        except ValueError:
            para.level = 0

    para_style_chain = (p_pr,) + _lst_style_level_prs(lst_styles, para.level)
    para.align = _attr_chain(para_style_chain, "algn") or "l"
    para.margin_left_px = _emu_px_attr_chain(para_style_chain, "marL", 0.0)
    para.indent_px = _emu_px_attr_chain(para_style_chain, "indent", 0.0)
    para.line_height_ratio = _line_height_ratio(para_style_chain)
    line_spacing = _child_chain(para_style_chain, "a:lnSpc")
    if line_spacing is not None and line_spacing.find("a:spcPts", NS) is not None:
        para.line_spacing_px = _spacing_points_px((line_spacing,), "a:spcPts")
    para.space_before_px = _spacing_points_px(para_style_chain, "a:spcBef/a:spcPts")
    para.space_after_px = _spacing_points_px(para_style_chain, "a:spcAft/a:spcPts")
    para.bullet_prefix = _resolve_bullet_prefix(
        para_style_chain, para.level, autonum_state,
    )
    para.bullet_fill, para.bullet_fill_opacity = _resolve_bullet_fill(
        para_style_chain, palette,
    )

    # Default endParaRPr style (applies if a run has no rPr)
    end_rpr = p_elem.find("a:endParaRPr", NS)
    # defRPr from pPr and txBody/lstStyle, both optional.
    def_rpr = p_pr.find("a:defRPr", NS) if p_pr is not None else None
    list_def_rpr = _child_chain(para_style_chain[1:], "a:defRPr")
    para.empty_line_font_size_px = _font_size_px(
        (end_rpr, def_rpr, list_def_rpr) + fallback_run_props,
        default_font_size_px,
    )

    def resolved_run(text: str, rpr: ET.Element | None) -> TextRun:
        return _build_run(
            text, rpr, end_rpr, palette, theme_fonts,
            def_rpr=def_rpr,
            list_def_rpr=list_def_rpr,
            fallback_run_props=fallback_run_props,
            default_fill=default_fill,
            default_font_size_px=default_font_size_px,
            id_prefix=id_prefix, id_seq=id_seq,
            hyperlink_resolver=hyperlink_resolver,
            strict=strict,
            diagnostic_sink=diagnostic_sink,
        )

    def append_resolved_text(text: str, rpr: ET.Element | None) -> None:
        """Preserve literal newlines inside a:t as explicit DrawingML breaks."""
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        segments = normalized.split("\n")
        for index, segment in enumerate(segments):
            if segment or len(segments) == 1:
                para.runs.append(resolved_run(segment, rpr))
            if index < len(segments) - 1:
                line_break = resolved_run("", rpr)
                line_break.is_break = True
                para.runs.append(line_break)

    for child in list(p_elem):
        if not isinstance(child.tag, str):
            continue
        local = child.tag.split("}", 1)[-1]
        if local == "r":
            rpr = child.find("a:rPr", NS)
            text_elem = child.find("a:t", NS)
            text = text_elem.text or "" if text_elem is not None else ""
            append_resolved_text(text, rpr)
        elif local == "br":
            break_rpr = child.find("a:rPr", NS)
            para.runs.append(TextRun(
                text="",
                font_size_px=_font_size_px(
                    (break_rpr, def_rpr, list_def_rpr, end_rpr)
                    + fallback_run_props,
                    default_font_size_px,
                ),
                font_family="sans-serif",
                fill=default_fill,
                is_break=True,
            ))
        elif local == "fld":
            # Slide SVGs have a concrete page context, so resolve slide-number
            # fields there. Standalone master/layout renders keep the literal
            # fallback because one shared part can serve many slide numbers.
            rpr = child.find("a:rPr", NS)
            text_elem = child.find("a:t", NS)
            text = text_elem.text or "" if text_elem is not None else ""
            field_type = child.attrib.get("type", "").strip().lower()
            if field_type == "slidenum" and slide_number is not None:
                text = str(slide_number)
            if text:
                append_resolved_text(text, rpr)
        elif (
            child.tag
            == "{http://schemas.microsoft.com/office/drawing/2010/main}m"
            and inline_formula_resolver is not None
        ):
            formula, preview = inline_formula_resolver(child)
            if preview:
                formula_rpr = next(
                    child.iter(f"{{{NS['a']}}}rPr"),
                    None,
                )
                run = resolved_run(preview, formula_rpr)
                if formula is not None:
                    run.formula_latex = formula.latex
                    run.fill = formula.color
                    run.fill_opacity = formula.opacity
                para.runs.append(run)

    return para


def _apply_norm_autofit(
    body_pr: ET.Element | None,
    paragraphs: list[TextParagraph],
) -> None:
    """Scale runs by the stored shrink-on-overflow result PowerPoint renders with."""
    autofit = body_pr.find("a:normAutofit", NS) if body_pr is not None else None
    if autofit is None:
        return

    def fraction(attr: str, default: float) -> float:
        try:
            value = int(autofit.attrib.get(attr, "")) / 100000.0
        except ValueError:
            return default
        return value if 0.0 < value <= 1.0 else default

    font_scale = fraction("fontScale", 1.0)
    line_scale = 1.0 - fraction("lnSpcReduction", 0.0)
    if font_scale == 1.0 and line_scale == 1.0:
        return
    for para in paragraphs:
        para.empty_line_font_size_px *= font_scale
        para.line_height_ratio *= line_scale
        if para.line_spacing_px is not None:
            para.line_spacing_px *= line_scale
        for run in para.runs:
            run.font_size_px *= font_scale


def _font_size_px(
    sources: tuple[ET.Element | None, ...],
    default_font_size_px: float,
) -> float:
    """Resolve one effective DrawingML run size into SVG pixels."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .txbody_to_svg import (
        _attr_chain,
    )
    return hundredths_pt_to_px(
        _attr_chain(sources, "sz"),
        default_font_size_px,
    )


def _build_run(
    text: str,
    rpr: ET.Element | None,
    end_rpr: ET.Element | None,
    palette: ColorPalette | None,
    theme_fonts: dict[str, str],
    *,
    def_rpr: ET.Element | None = None,
    list_def_rpr: ET.Element | None = None,
    fallback_run_props: tuple[ET.Element, ...] = (),
    default_fill: str = DEFAULT_FILL_HEX,
    default_font_size_px: float = DEFAULT_FONT_SIZE_PX,
    id_prefix: str = "txt",
    id_seq: list[int] | None = None,
    hyperlink_resolver: HyperlinkResolver | None = None,
    strict: bool = False,
    diagnostic_sink: TextDiagnosticSink | None = None,
) -> TextRun:
    """Resolve a single <a:r> run from its rPr and fallback run properties."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .txbody_to_svg import (
        TextImportError,
        TextRun,
        _attr_chain,
        _build_font_stack,
        _font_size_px,
        _resolve_theme_typeface,
        _typeface_chain,
    )
    style_chain = (
        rpr, def_rpr, list_def_rpr, end_rpr,
    ) + fallback_run_props
    # font-size: rPr > pPr/defRPr > lstStyle/lvlNpPr/defRPr > endParaRPr > default
    font_size_px = _font_size_px(style_chain, default_font_size_px)
    # Bold / italic
    bold = ooxml_bool(_attr_chain(style_chain, "b"))
    italic = ooxml_bool(_attr_chain(style_chain, "i"))
    # Underline / strike
    u_val = _attr_chain(style_chain, "u")
    underline = u_val not in (None, "", "none")
    strike_val = _attr_chain(style_chain, "strike")
    strikethrough = strike_val in ("sngStrike", "dblStrike")

    # Letter spacing (rPr@spc, in 1/100 pt)
    spc = _attr_chain(style_chain, "spc")
    letter_spacing_px = 0.0
    if spc is not None:
        try:
            letter_spacing_px = float(spc) / 100.0 * 4.0 / 3.0  # pt -> px
        except ValueError as exc:
            message = (
                f"Invalid DrawingML a:rPr@spc value {spc!r}; expected a numeric "
                "hundredths-of-a-point value"
            )
            if strict:
                raise TextImportError(message) from exc
            if diagnostic_sink is not None:
                diagnostic_sink(
                    "text-letter-spacing-normalized",
                    message,
                    "use zero letter spacing for this run",
                )

    # Color
    fill = default_fill
    fill_opacity = 1.0
    defs: list[str] = []
    color_source = None
    for src in style_chain:
        if src is None:
            continue
        grad = src.find("a:gradFill", NS)
        if grad is not None:
            grad_fill = resolve_fill(
                grad, palette,
                id_prefix=id_prefix,
                id_seq=id_seq,
            )
            if grad_fill.attrs.get("fill"):
                fill = grad_fill.attrs["fill"]
                fill_opacity = 1.0
                defs.extend(grad_fill.defs)
                color_source = None
                break
        solid = src.find("a:solidFill", NS)
        if solid is not None:
            color_source = solid
            break
    if color_source is not None:
        color_elem = find_color_elem(color_source)
        hex_, alpha = resolve_color(color_elem, palette)
        if hex_:
            fill = hex_
            fill_opacity = alpha

    # Font typeface
    latin_face = _typeface_chain(style_chain, "latin")
    ea_face = _typeface_chain(style_chain, "ea")
    cs_face = _typeface_chain(style_chain, "cs")
    lang = _attr_chain(style_chain, "lang")
    alt_lang = _attr_chain(style_chain, "altLang")

    # Resolve theme refs (e.g. typeface="+mn-lt" / "+mj-ea")
    latin_face = _resolve_theme_typeface(
        latin_face,
        theme_fonts,
        text=text,
        lang=lang,
        alt_lang=alt_lang,
    )
    ea_face = _resolve_theme_typeface(
        ea_face,
        theme_fonts,
        text=text,
        lang=lang,
        alt_lang=alt_lang,
    )
    cs_face = _resolve_theme_typeface(
        cs_face,
        theme_fonts,
        text=text,
        lang=lang,
        alt_lang=alt_lang,
    )

    # No typeface anywhere in the chain means the theme's minor font pair.
    latin_face = latin_face or theme_fonts.get("minorLatin") or None
    ea_face = ea_face or theme_fonts.get("minorEastAsia") or None
    font_family = _build_font_stack(latin_face, ea_face, cs_face)
    hyperlink_href: str | None = None
    if rpr is not None and hyperlink_resolver is not None:
        hyperlink = rpr.find("a:hlinkClick", NS)
        if hyperlink is not None:
            hyperlink_href = hyperlink_resolver(
                hyperlink.attrib.get(f"{{{NS['r']}}}id", ""),
                hyperlink.attrib.get("action", ""),
            )

    return TextRun(
        text=text,
        font_size_px=font_size_px,
        font_family=font_family,
        fill=fill,
        fill_opacity=fill_opacity,
        defs=defs,
        bold=bold,
        italic=italic,
        underline=underline,
        strikethrough=strikethrough,
        letter_spacing_px=letter_spacing_px,
        hyperlink_href=hyperlink_href,
        font_faces={
            "latin": latin_face or "Segoe UI",
            "ea": ea_face or latin_face or "Microsoft YaHei",
        },
        baseline_shifted=_attr_chain(style_chain, "baseline") not in {None, "0"},
    )


