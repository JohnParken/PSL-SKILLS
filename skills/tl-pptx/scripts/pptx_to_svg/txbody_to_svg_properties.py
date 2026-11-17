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

def _lst_style_level_prs(
    lst_styles: tuple[ET.Element, ...],
    level: int,
) -> tuple[ET.Element, ...]:
    """Return txBody/lstStyle paragraph properties for a paragraph level."""
    level_idx = min(max(level, 0), 8) + 1
    level_prs: list[ET.Element] = []
    for lst_style in lst_styles:
        lvl_pr = lst_style.find(f"a:lvl{level_idx}pPr", NS)
        if lvl_pr is not None:
            level_prs.append(lvl_pr)
    return tuple(level_prs)


def _child_chain(
    sources: tuple[ET.Element | None, ...],
    path: str,
) -> ET.Element | None:
    for src in sources:
        if src is None:
            continue
        child = src.find(path, NS)
        if child is not None:
            return child
    return None


def _emu_px_attr_chain(
    sources: tuple[ET.Element | None, ...],
    attr: str,
    default: float,
) -> float:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .txbody_to_svg import (
        _attr_chain,
    )
    value = _attr_chain(sources, attr)
    if value is None:
        return default
    try:
        return emu_to_px(int(value))
    except ValueError:
        return default


def _line_height_ratio(sources: tuple[ET.Element | None, ...]) -> float:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .txbody_to_svg import (
        DEFAULT_LINE_HEIGHT_RATIO,
        _child_chain,
    )
    ln_spc = _child_chain(sources, "a:lnSpc")
    if ln_spc is None:
        return DEFAULT_LINE_HEIGHT_RATIO
    spc_pct = ln_spc.find("a:spcPct", NS)
    if spc_pct is None:
        return DEFAULT_LINE_HEIGHT_RATIO
    try:
        return DEFAULT_LINE_HEIGHT_RATIO * float(spc_pct.attrib.get("val", "100000")) / 100000.0
    except ValueError:
        return DEFAULT_LINE_HEIGHT_RATIO


def _spacing_points_px(
    sources: tuple[ET.Element | None, ...],
    path: str,
) -> float:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .txbody_to_svg import (
        _child_chain,
    )
    spacing = _child_chain(sources, path)
    if spacing is None:
        return 0.0
    try:
        return hundredths_pt_to_px(int(spacing.attrib.get("val", "0")))
    except ValueError:
        return 0.0


def _attr_chain(sources: tuple[ET.Element | None, ...], attr: str) -> str | None:
    """Return the first non-empty value of `attr` from any source element."""
    for src in sources:
        if src is None:
            continue
        v = src.attrib.get(attr)
        if v is not None:
            return v
    return None


def _typeface(rpr: ET.Element | None, child_tag: str) -> str | None:
    if rpr is None:
        return None
    elem = rpr.find(f"a:{child_tag}", NS)
    if elem is None:
        return None
    val = elem.attrib.get("typeface")
    return val or None


def _typeface_chain(
    sources: tuple[ET.Element | None, ...],
    child_tag: str,
) -> str | None:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .txbody_to_svg import (
        _typeface,
    )
    for src in sources:
        face = _typeface(src, child_tag)
        if face:
            return face
    return None


def _theme_script_from_lang(lang: str | None) -> str | None:
    """Map a DrawingML language tag to one theme supplemental-script key."""
    if not lang:
        return None
    normalized = lang.strip().replace("_", "-").lower()
    if not normalized:
        return None
    parts = normalized.split("-")
    primary = parts[0]
    if primary == "ja":
        return "Jpan"
    if primary == "ko":
        return "Hang"
    if primary != "zh":
        return None
    if any(part in {"hant", "cht", "tw", "hk", "mo"} for part in parts[1:]):
        return "Hant"
    return "Hans"


def _theme_script_from_text(text: str) -> str | None:
    """Infer a CJK theme script from glyph ranges, defaulting plain Han to Hans."""
    if any(
        0x3100 <= ord(char) <= 0x312F
        or 0x31A0 <= ord(char) <= 0x31BF
        for char in text
    ):
        return "Hant"
    return {
        "ko-KR": "Hang",
        "ja-JP": "Jpan",
        "zh-CN": "Hans",
    }.get(detect_text_lang(text))


def _run_theme_script(
    text: str,
    lang: str | None,
    alt_lang: str | None,
) -> str | None:
    """Resolve EA script from run language first, then alternate language/text."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .txbody_to_svg import (
        _theme_script_from_lang,
        _theme_script_from_text,
    )
    return (
        _theme_script_from_lang(lang)
        or _theme_script_from_lang(alt_lang)
        or _theme_script_from_text(text)
    )


def _resolve_theme_typeface(
    face: str | None,
    theme_fonts: dict[str, str],
    *,
    text: str = "",
    lang: str | None = None,
    alt_lang: str | None = None,
) -> str | None:
    """Resolve DrawingML major/minor Latin, EA, and complex-script tokens."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .txbody_to_svg import (
        _run_theme_script,
    )
    if not face or not face.startswith("+"):
        return face
    code = face[1:]
    if code == "mj-lt":
        return theme_fonts.get("majorLatin") or face
    if code == "mn-lt":
        return theme_fonts.get("minorLatin") or face
    if code in {"mj-ea", "mn-ea"}:
        prefix = "major" if code.startswith("mj") else "minor"
        script = _run_theme_script(text, lang, alt_lang)
        script_face = (
            theme_fonts.get(f"{prefix}Script{script}")
            if script is not None else None
        )
        return (
            theme_fonts.get(f"{prefix}EastAsia")
            or script_face
            or theme_fonts.get(f"{prefix}Latin")
            or face
        )
    if code == "mj-cs":
        return (
            theme_fonts.get("majorComplexScript")
            or theme_fonts.get("majorLatin")
            or face
        )
    if code == "mn-cs":
        return (
            theme_fonts.get("minorComplexScript")
            or theme_fonts.get("minorLatin")
            or face
        )
    return face


def _build_font_stack(latin: str | None, ea: str | None, cs: str | None) -> str:
    """Build a CSS font-family stack: original PPT names first, then fallbacks."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .txbody_to_svg import (
        _quote_font,
    )
    parts: list[str] = []
    seen: set[str] = set()
    for face in (latin, ea, cs):
        if face and face not in seen:
            parts.append(_quote_font(face))
            seen.add(face)
    # Generic fallback so the browser can render even if PPT fonts are absent.
    parts.append("sans-serif")
    return ", ".join(parts)


def _quote_font(name: str) -> str:
    """Quote a font name if it contains spaces or non-ASCII chars.

    Uses XML entity-escaped double quotes (&quot;) so the resulting CSS string
    survives being embedded inside an SVG attribute that itself uses double
    quotes. CSS parsers accept the unescaped form after attribute parsing.
    """
    if any(c.isspace() or ord(c) > 127 for c in name):
        return f"&quot;{name}&quot;"
    return name


def _resolve_bullet_prefix(
    sources: tuple[ET.Element | None, ...],
    level: int,
    autonum_state: dict[int, int],
) -> str:
    """Render bullet glyphs / numbering as a literal text prefix."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .txbody_to_svg import (
        _child_chain,
        _format_auto_number,
    )
    bu_none = _child_chain(sources, "a:buNone")
    if bu_none is not None:
        autonum_state.pop(level, None)
        return ""
    bu_char = _child_chain(sources, "a:buChar")
    if bu_char is not None:
        ch = bu_char.attrib.get("char", "•")
        bu_font = _child_chain(sources, "a:buFont")
        typeface = bu_font.attrib.get("typeface", "") if bu_font is not None else ""
        if typeface.casefold() == "wingdings" and ch == "l":
            ch = "●"
        return f"{ch} "
    bu_auto = _child_chain(sources, "a:buAutoNum")
    if bu_auto is not None:
        start_at = bu_auto.attrib.get("startAt")
        if start_at is not None:
            try:
                autonum_state[level] = int(start_at)
            except ValueError:
                autonum_state[level] = 1
        else:
            autonum_state[level] = autonum_state.get(level, 0) + 1
        return _format_auto_number(
            autonum_state[level],
            bu_auto.attrib.get("type", "arabicPeriod"),
        )
    return ""


def _resolve_bullet_fill(
    sources: tuple[ET.Element | None, ...],
    palette: ColorPalette | None,
) -> tuple[str | None, float]:
    """Resolve an explicit DrawingML bullet color independently of text."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .txbody_to_svg import (
        _child_chain,
    )
    bu_clr = _child_chain(sources, "a:buClr")
    if bu_clr is None:
        return None, 1.0
    color_elem = find_color_elem(bu_clr)
    color, opacity = resolve_color(color_elem, palette)
    return color, opacity


def _format_auto_number(value: int, kind: str) -> str:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .txbody_to_svg import (
        _alpha_number,
        _roman_number,
    )
    lower = kind.lower()
    if "alphalc" in lower:
        token = _alpha_number(value, uppercase=False)
    elif "alphauc" in lower:
        token = _alpha_number(value, uppercase=True)
    elif "romanlc" in lower:
        token = _roman_number(value).lower()
    elif "romanuc" in lower:
        token = _roman_number(value).upper()
    else:
        token = str(value)

    if "parenboth" in lower:
        return f"({token}) "
    if "parenr" in lower:
        return f"{token}) "
    if "period" in lower:
        return f"{token}. "
    return f"{token} "


def _alpha_number(value: int, *, uppercase: bool) -> str:
    value = max(1, value)
    chars: list[str] = []
    while value:
        value -= 1
        chars.append(chr(ord("A") + (value % 26)))
        value //= 26
    text = "".join(reversed(chars))
    return text if uppercase else text.lower()


def _roman_number(value: int) -> str:
    value = max(1, min(value, 3999))
    parts: list[str] = []
    for n, token in (
        (1000, "M"), (900, "CM"), (500, "D"), (400, "CD"),
        (100, "C"), (90, "XC"), (50, "L"), (40, "XL"),
        (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"),
    ):
        while value >= n:
            parts.append(token)
            value -= n
    return "".join(parts)


# ---------------------------------------------------------------------------
# Layout / emission
# ---------------------------------------------------------------------------

