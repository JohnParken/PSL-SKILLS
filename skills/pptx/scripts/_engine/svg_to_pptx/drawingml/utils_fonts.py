# Adapted from PPT Master 5e8856b (MIT); see assets/licenses/ppt_master.txt.
"""Coordinate, transform, color, and font helpers for DrawingML conversion.

See references/shared-standards-core.md §2.1 for project geometry and
references/svg-effects.md §§6.2–6.8 for paint, image-fit, line-presentation,
and transform authoring contracts.
"""

from __future__ import annotations

import colorsys
import json
import math
import re
import unicodedata
from collections import Counter
from collections.abc import Iterator
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from xml.etree import ElementTree as ET

from pptx_gradients import (
    NATIVE_GRADIENT_ATTR,
    NATIVE_GRADIENT_PREVIEW_SHA256_ATTR,
    NATIVE_GRADIENT_SHA256_ATTR,
    preserved_native_gradient_xml,
)

from pptx_shapes import (
    OOXML_COORDINATE_MAX,
    resolve_preset_preview_hash,
    svg_preset_preview_fingerprint,
    validate_ooxml_xfrm,
)
from language_tags import language_base, language_uses_rtl, normalize_language_tag

from .context import AffineMatrix, ConvertContext, IDENTITY_MATRIX

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


# Responsibility: utils_fonts; adapted from the original module.

def parse_hex_color(color_str: str) -> str | None:
    """Parse SVG color values to ``RRGGBB``, ignoring any alpha channel."""
    from .utils_paint_values import parse_svg_color
    if color_str and color_str.strip().lower() == 'transparent':
        return None
    color, _alpha = parse_svg_color(color_str)
    return color


def combine_opacity(*values: float | None) -> float | None:
    """Multiply opacity components, returning ``None`` when fully opaque."""
    combined = 1.0
    for value in values:
        if value is not None:
            combined *= max(0.0, min(1.0, value))
    return combined if combined < 1.0 else None


def parse_stop_style(style_str: str) -> tuple[str | None, float]:
    """Parse a gradient stop's style attribute.

    Args:
        style_str: Style string like 'stop-color:#XXX;stop-opacity:N'.

    Returns:
        (color, opacity) tuple.
    """
    from .utils_paint_values import parse_inline_style
    from .utils_paint_values import parse_opacity
    from .utils_paint_values import parse_svg_color
    color = None
    color_alpha = 1.0
    stop_opacity = 1.0
    style_values = parse_inline_style(style_str)
    if not style_values:
        return color, stop_opacity

    if 'stop-color' in style_values:
        color, color_alpha = parse_svg_color(style_values['stop-color'])
    if 'stop-opacity' in style_values:
        stop_opacity = parse_opacity(
            style_values['stop-opacity'],
            allow_percentage=True,
        )

    return color, color_alpha * stop_opacity


def resolve_url_id(url_str: str) -> str | None:
    """Extract ID from 'url(#someId)' reference."""
    if not url_str:
        return None
    m = re.match(r'url\(#([^)]+)\)', url_str.strip())
    return m.group(1) if m else None


def get_effective_filter_id(elem: ET.Element, ctx: ConvertContext) -> str | None:
    """Get the effective filter ID for an element, including inherited context."""
    filt = elem.get('filter')
    if filt:
        return resolve_url_id(filt)
    return ctx.filter_id


# ---------------------------------------------------------------------------
# Font parsing
# ---------------------------------------------------------------------------

# Windows EA faces for decks whose primary language is not Simplified Chinese:
# (sans, serif) by BCP-47 language/script prefix.
_EA_DEFAULTS_BY_LANGUAGE = (
    (('ja',), ('Yu Gothic', 'Yu Mincho')),
    (('ko',), ('Malgun Gothic', 'Batang')),
    (('zh-hant', 'zh-tw', 'zh-hk', 'zh-mo'), ('Microsoft JhengHei', 'PMingLiU')),
)
# macOS Japanese faces map to Japanese Windows faces, not to Chinese ones.
_JA_FONT_FALLBACK_WIN = {
    'Hiragino Sans': 'Yu Gothic',
    'Hiragino Kaku Gothic ProN': 'Yu Gothic',
    'Hiragino Kaku Gothic Pro': 'Yu Gothic',
    'Hiragino Mincho ProN': 'Yu Mincho',
    'Hiragino Mincho Pro': 'Yu Mincho',
}


def _language_is(language: str | None, prefix: str) -> bool:
    """Return whether a BCP-47 tag equals or starts with one subtag prefix."""
    tag = (language or '').lower()
    return tag == prefix or tag.startswith(prefix + '-')


def _ea_default(language: str | None, serif: bool) -> str:
    """Return the Windows EA fallback face for one deck language."""
    for prefixes, faces in _EA_DEFAULTS_BY_LANGUAGE:
        if any(_language_is(language, prefix) for prefix in prefixes):
            return faces[1] if serif else faces[0]
    return 'SimSun' if serif else 'Microsoft YaHei'


def parse_font_family(
    font_family_str: str,
    language: str | None = None,
) -> dict[str, str]:
    """Parse CSS font-family into latin/ea typeface names.

    Prioritizes Windows-available fonts since PPTX is primarily opened on
    Windows. macOS/Linux-only fonts are mapped via FONT_FALLBACK_WIN. The
    first named Latin face fills ``latin`` and the first named CJK face fills
    ``ea``; a CJK face also serves ``latin`` when no named Latin face exists,
    and a generic family fills ``latin`` only when it precedes every named face.
    ``language`` (the deck's BCP-47 primary language) picks the EA fallback
    when the stack names no CJK face, so Japanese text never lands on a
    Chinese face.
    """
    from .utils_constants import EA_FONTS
    from .utils_constants import FONT_FALLBACK_WIN
    from .utils_constants import GENERIC_FONT_MAP
    from .utils_constants import SYSTEM_FONTS
    from .utils_constants import _FONT_CANONICAL_NAMES
    from .utils_constants import _SERIF_LATIN
    is_japanese = _language_is(language, 'ja')
    if not font_family_str:
        return {'latin': 'Segoe UI', 'ea': _ea_default(language, False)}

    fonts = [f.strip().strip("'\"") for f in font_family_str.split(',')]
    latin_font = None
    ea_font = None

    for font in fonts:
        font = _FONT_CANONICAL_NAMES.get(font.casefold(), font)
        if font in SYSTEM_FONTS:
            continue
        if font in GENERIC_FONT_MAP:
            # A generic family only fills the Latin slot when it precedes
            # every named face: a trailing ``sans-serif`` after a named CJK
            # face must not pull that run's Latin glyphs onto another face.
            if latin_font is None and ea_font is None:
                latin_font = GENERIC_FONT_MAP[font]
            continue

        win_font = (
            _JA_FONT_FALLBACK_WIN.get(font) if is_japanese else None
        ) or FONT_FALLBACK_WIN.get(font, font)
        if font in EA_FONTS or win_font in EA_FONTS:
            ea_font = ea_font or win_font
        else:
            latin_font = latin_font or win_font

    # PPT renders CJK text via latin typeface when ea doesn't match
    if not latin_font and ea_font:
        latin_font = ea_font

    final_latin = latin_font or 'Segoe UI'

    # EA must always be a CJK-capable font
    if not ea_font:
        ea_font = _ea_default(language, final_latin in _SERIF_LATIN)

    return {'latin': final_latin, 'ea': ea_font}


def unsafe_exported_font_faces(font_family_str: str) -> dict[str, str]:
    """Return resolved PPTX typefaces that require a custom installation."""
    from .utils_constants import PPT_SAFE_FONTS
    return {
        role: family
        for role, family in parse_font_family(font_family_str).items()
        if family.strip().lower() not in PPT_SAFE_FONTS
    }


def _is_han_char(ch: str) -> bool:
    """Return whether one character belongs to a Han ideograph block."""
    cp = ord(ch)
    return (
        0x3400 <= cp <= 0x4DBF
        or 0x4E00 <= cp <= 0x9FFF
        or 0xF900 <= cp <= 0xFAFF
        or 0x20000 <= cp <= 0x2EE5F
        or 0x30000 <= cp <= 0x323AF
    )


def _is_hiragana_char(ch: str) -> bool:
    cp = ord(ch)
    return (
        0x3040 <= cp <= 0x309F
        or 0x1B001 <= cp <= 0x1B11F
    )


def _is_katakana_char(ch: str) -> bool:
    cp = ord(ch)
    return (
        0x30A0 <= cp <= 0x30FF
        or 0x31F0 <= cp <= 0x31FF
        or 0xFF65 <= cp <= 0xFF9F
        or 0x1AFF0 <= cp <= 0x1AFFF
        or cp == 0x1B000
        or 0x1B120 <= cp <= 0x1B16F
    )


def _is_hangul_char(ch: str) -> bool:
    cp = ord(ch)
    return (
        0x1100 <= cp <= 0x11FF
        or 0x3130 <= cp <= 0x318F
        or 0xA960 <= cp <= 0xA97F
        or 0xAC00 <= cp <= 0xD7AF
        or 0xD7B0 <= cp <= 0xD7FF
        or 0xFFA0 <= cp <= 0xFFDC
    )


def is_cjk_char(ch: str) -> bool:
    """Return whether one character uses the project East Asian width model."""
    cp = ord(ch)
    return (
        _is_han_char(ch)
        or _is_hiragana_char(ch)
        or _is_katakana_char(ch)
        or _is_hangul_char(ch)
        or 0x2E80 <= cp <= 0x2FFF
        or 0x3000 <= cp <= 0x303F
        or 0x3100 <= cp <= 0x312F
        or 0x31A0 <= cp <= 0x31BF
        or 0x31C0 <= cp <= 0x31EF
        or 0xFF00 <= cp <= 0xFFEF
    )


def _contains_codepoint_range(
    text: str,
    ranges: tuple[tuple[int, int], ...],
) -> bool:
    """Return whether text contains a code point in one of the ranges."""
    return any(
        start <= ord(ch) <= end
        for ch in text
        for start, end in ranges
    )


def _explicit_language_script(language: str) -> str | None:
    """Return the explicit script before any region, variant, or extension."""
    parts = normalize_language_tag(language).split('-')
    index = 1
    if len(parts[0]) <= 3:
        while index < len(parts) and len(parts[index]) == 3 and parts[index].isalpha():
            index += 1
    if index < len(parts) and len(parts[index]) == 4 and parts[index].isalpha():
        return parts[index]
    return None


def _default_language_for_script(
    default_language: str | None,
    bases: frozenset[str],
    fallback: str,
) -> str:
    """Prefer the project language when it belongs to the detected script."""
    if default_language and language_base(default_language) in bases:
        return default_language
    return fallback


def text_has_rtl_characters(text: str) -> bool:
    """Return whether text contains a strong right-to-left character."""
    return any(unicodedata.bidirectional(ch) in {'R', 'AL'} for ch in text)


def text_uses_rtl(text: str, default_language: str | None = None) -> bool:
    """Resolve paragraph direction from its first strong character or project."""
    for char in text:
        direction = unicodedata.bidirectional(char)
        if direction in {'R', 'AL'}:
            return True
        if direction == 'L':
            return False
    return bool(default_language and language_uses_rtl(default_language))


def detect_text_lang(
    text: str,
    default_language: str | None = None,
) -> str:
    """Return a DrawingML language tag, preferring the project contract."""
    from .utils_text_widths import _NON_LATIN_SCRIPT_BASES
    has_hangul = False
    has_kana = False
    has_east_asian_text = False
    for ch in text:
        has_hangul = has_hangul or _is_hangul_char(ch)
        has_kana = (
            has_kana
            or _is_hiragana_char(ch)
            or _is_katakana_char(ch)
        )
        has_east_asian_text = has_east_asian_text or is_cjk_char(ch)
    if has_hangul:
        return _default_language_for_script(
            default_language,
            frozenset({'ko'}),
            'ko-KR',
        )
    if has_kana:
        return _default_language_for_script(
            default_language,
            frozenset({'ja'}),
            'ja-JP',
        )
    if has_east_asian_text:
        return _default_language_for_script(
            default_language,
            frozenset({'zh', 'ja', 'ko'}),
            'zh-CN',
        )
    if _contains_codepoint_range(text, (
        (0x0600, 0x06FF),
        (0x0750, 0x077F),
        (0x08A0, 0x08FF),
        (0xFB50, 0xFDFF),
        (0xFE70, 0xFEFF),
        (0x1EE00, 0x1EEFF),
    )):
        return _default_language_for_script(
            default_language,
            frozenset({'ar', 'fa', 'ps', 'sd', 'ug', 'ur'}),
            'ar-SA',
        )
    if _contains_codepoint_range(text, (
        (0x0590, 0x05FF),
        (0xFB1D, 0xFB4F),
    )):
        return _default_language_for_script(
            default_language,
            frozenset({'he', 'yi'}),
            'he-IL',
        )
    if _contains_codepoint_range(text, (
        (0x0900, 0x097F),
        (0xA8E0, 0xA8FF),
    )):
        return _default_language_for_script(
            default_language,
            frozenset({'hi', 'mr', 'ne', 'sa'}),
            'hi-IN',
        )
    if _contains_codepoint_range(text, ((0x0E00, 0x0E7F),)):
        return _default_language_for_script(
            default_language,
            frozenset({'th'}),
            'th-TH',
        )
    if _contains_codepoint_range(text, (
        (0x0400, 0x052F),
        (0x1C80, 0x1C8F),
        (0x2DE0, 0x2DFF),
        (0xA640, 0xA69F),
    )):
        return _default_language_for_script(
            default_language,
            frozenset({'be', 'bg', 'kk', 'ky', 'mk', 'mn', 'ru', 'sr', 'uk'}),
            'ru-RU',
        )
    if _contains_codepoint_range(text, (
        (0x0370, 0x03FF),
        (0x1F00, 0x1FFF),
    )):
        return _default_language_for_script(
            default_language,
            frozenset({'el'}),
            'el-GR',
        )
    if (
        default_language
        and language_base(default_language) in _NON_LATIN_SCRIPT_BASES
        and _explicit_language_script(default_language) != 'Latn'
        and any(ch.isalpha() for ch in text)
    ):
        # A run of Latin letters inside a CJK/Arabic/... deck (an English
        # subtitle, a source line) proofs and reads as English, not as the
        # deck language; digits and punctuation alone keep the deck tag.
        return 'en-US'
    return default_language or 'en-US'


# Language bases whose script the detector recognises above; a Latin-letter
# run under one of these deck languages is not written in that language.
