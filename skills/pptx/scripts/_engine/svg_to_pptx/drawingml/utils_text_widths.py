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


# Responsibility: utils_text_widths; adapted from the original module.

_NON_LATIN_SCRIPT_BASES = frozenset({
    'zh', 'ja', 'ko',
    'ar', 'fa', 'ps', 'sd', 'ug', 'ur',
    'he', 'yi',
    'hi', 'mr', 'ne', 'sa',
    'th',
    'be', 'bg', 'kk', 'ky', 'mk', 'mn', 'ru', 'sr', 'uk',
    'el',
})


def _is_grapheme_extend(ch: str) -> bool:
    """Return whether ``ch`` extends the preceding rendered character."""
    cp = ord(ch)
    return (
        unicodedata.category(ch) in {'Mn', 'Mc', 'Me'}
        or 0xFE00 <= cp <= 0xFE0F
        or 0xE0100 <= cp <= 0xE01EF
        or 0x1F3FB <= cp <= 0x1F3FF
        or 0xE0020 <= cp <= 0xE007F
    )


def _is_regional_indicator(ch: str) -> bool:
    return 0x1F1E6 <= ord(ch) <= 0x1F1FF


def _is_virama(ch: str) -> bool:
    name = unicodedata.name(ch, '')
    return (
        unicodedata.combining(ch) == 9
        or 'VIRAMA' in name
        or name.endswith(' SIGN HALANT')
    )


def _is_emoji_base(ch: str) -> bool:
    cp = ord(ch)
    return 0x2600 <= cp <= 0x27BF or 0x1F000 <= cp <= 0x1FAFF


def _unicode_script_key(ch: str) -> str | None:
    """Return the stable Unicode-name prefix used for project script joins."""
    name = unicodedata.name(ch, '')
    if not name:
        return None
    tokens = name.split()
    boundary_tokens = {
        'CONSONANT',
        'LETTER',
        'SIGN',
        'SYLLABLE',
        'VOWEL',
    }
    for index, token in enumerate(tokens):
        if index > 0 and token in boundary_tokens:
            return ' '.join(tokens[:index])
    if tokens[0] in {'MEETEI', 'OL', 'TAI'} and len(tokens) > 1:
        return ' '.join(tokens[:2])
    return tokens[0]


def _virama_script_key(cluster: str, virama: str) -> str | None:
    virama_script = _unicode_script_key(virama)
    for ch in reversed(cluster):
        if not unicodedata.category(ch).startswith('L'):
            continue
        base_script = _unicode_script_key(ch)
        return base_script if base_script == virama_script else None
    return None


def split_project_text_clusters(text: str) -> list[str]:
    """Split text into the rendered units used by project width estimates.

    This intentionally implements only the Unicode joins that affect SVG to
    DrawingML tracking: combining marks, variation selectors, emoji modifiers,
    ZWJ sequences, regional-indicator pairs, and common virama conjuncts.
    """
    clusters: list[str] = []
    virama_script: str | None = None
    emoji_join = False
    for ch in text:
        if not clusters:
            clusters.append(ch)
            continue

        cluster = clusters[-1]
        previous = cluster[-1]
        if ch == '\n' and previous == '\r':
            clusters[-1] += ch
            virama_script = None
            emoji_join = False
        elif _is_grapheme_extend(ch):
            if _is_virama(ch):
                virama_script = _virama_script_key(cluster, ch)
            clusters[-1] += ch
        elif ch == '\u200d':
            clusters[-1] += ch
            emoji_join = any(_is_emoji_base(item) for item in cluster)
        elif ch == '\u200c':
            clusters[-1] += ch
            virama_script = None
            emoji_join = False
        elif (
            virama_script is not None
            and unicodedata.category(ch).startswith('L')
            and _unicode_script_key(ch) == virama_script
        ):
            clusters[-1] += ch
            virama_script = None
            emoji_join = False
        elif emoji_join and _is_emoji_base(ch):
            clusters[-1] += ch
            emoji_join = False
        elif (
            len(cluster) == 1
            and _is_regional_indicator(cluster)
            and _is_regional_indicator(ch)
        ):
            clusters[-1] += ch
        else:
            clusters.append(ch)
            virama_script = None
            emoji_join = False
    return clusters


def resolve_text_run_fonts(text: str, fonts: dict[str, str]) -> dict[str, str]:
    """Return DrawingML latin/ea/cs typefaces for one text run."""
    from .utils_fonts import is_cjk_char
    latin = fonts['latin']
    if any(is_cjk_char(ch) for ch in text):
        ea = fonts['ea']
    else:
        ea = latin
    return {'latin': latin, 'ea': ea, 'cs': latin}


# Unicode fixed-width spaces carry their width in their definition; bundled
# advance tables omit them, and the generic 0.55em fallback triples the thin
# spaces German and French typography sets before '%', units, and '§'.
_FIXED_SPACE_EMS = {
    ' ': 0.5,    # en space
    ' ': 1.0,    # em space
    ' ': 1 / 3,  # three-per-em space
    ' ': 0.25,   # four-per-em space
    ' ': 1 / 6,  # six-per-em space
    ' ': 0.55,   # figure space
    ' ': 0.3,    # punctuation space
    ' ': 0.2,    # thin space
    ' ': 0.1,    # hair space
    ' ': 0.2,    # narrow no-break space
}


_UNDECOMPOSED_BASE_LETTERS = str.maketrans('đĐłŁøØħĦ', 'dDlLoOhH')


def _base_letter(ch: str) -> str:
    """Return the unaccented letter a precomposed character advances like."""
    decomposed = unicodedata.normalize('NFD', ch)
    if len(decomposed) > 1 and all(
        unicodedata.category(mark) == 'Mn' for mark in decomposed[1:]
    ):
        ch = decomposed[0]
    return ch.translate(_UNDECOMPOSED_BASE_LETTERS)


def _estimate_character_width(ch: str, font_size: float) -> float:
    from .utils_fonts import is_cjk_char
    if (
        0xFF00 <= ord(ch) <= 0xFFEF
        and unicodedata.east_asian_width(ch) == 'H'
    ):
        return font_size * 0.5
    if is_cjk_char(ch):
        return font_size
    if ch == ' ':
        return font_size * 0.3
    if ch in _FIXED_SPACE_EMS:
        return font_size * _FIXED_SPACE_EMS[ch]
    ch = _base_letter(ch)
    if ch in 'mMwWOQ%':
        return font_size * 0.75
    if ch in 'iIlj!|':
        return font_size * 0.3
    if ch.isdigit():
        # digits are tabular (uniform ~0.55em) in most UI fonts, including
        # '1' — classing it with 'il|' under-sizes the box and makes
        # renderers that ignore wrap="none" (LibreOffice) wrap the line
        return font_size * 0.55
    return font_size * 0.55


def _estimate_grapheme_width(cluster: str, font_size: float) -> float:
    bases = [
        ch for ch in cluster
        if ch not in {'\u200c', '\u200d'} and not _is_grapheme_extend(ch)
    ]
    if not bases:
        return font_size * 0.55
    if (
        len(bases) > 1
        and all(_is_regional_indicator(ch) for ch in bases)
    ) or '\u20e3' in cluster or any(_is_emoji_base(ch) for ch in bases):
        return font_size
    # Spacing combining marks (Indic vowel signs such as Devanagari aa/ii/o)
    # sit beside the base and advance the pen; non-spacing marks do not.
    spacing_marks = sum(1 for ch in cluster if unicodedata.category(ch) == 'Mc')
    return (
        max(_estimate_character_width(ch, font_size) for ch in bases)
        + font_size * 0.3 * spacing_marks
    )


_FONT_ADVANCES_CACHE = None


def primary_font_family(font_family: str | None) -> str:
    """Normalize the first family in a font stack."""
    return str(font_family or '').split(',')[0].strip().strip('\'"').lower()


def get_font_advances(
    font_family: str | None,
    font_weight: str = '400',
    font_style: str = 'normal',
) -> dict[str, float] | None:
    """Return bundled glyph advances for the primary family and style."""
    family = primary_font_family(font_family)
    if not family:
        return None

    global _FONT_ADVANCES_CACHE
    if _FONT_ADVANCES_CACHE is None:
        with Path(__file__).with_name('font_advances.json').open(encoding='utf-8') as handle:
            _FONT_ADVANCES_CACHE = json.load(handle)['families']

    bold = font_weight in ('bold', '600', '700', '800', '900')
    italic = font_style in ('italic', 'oblique')
    style = 'bold' if bold else 'regular'
    if italic:
        style = 'bold-italic' if bold else 'italic'
    entry = _FONT_ADVANCES_CACHE.get(family, {}).get(style)
    return entry['advances'] if entry is not None else None


def estimate_text_cluster_widths(
    text: str,
    font_size: float,
    font_weight: str = '400',
    *,
    font_family: str | None = None,
    font_style: str = 'normal',
) -> list[float]:
    """Estimate each project text cluster without inserting tracking."""
    from .utils_fonts import is_cjk_char
    advances = get_font_advances(font_family, font_weight, font_style)
    bold = font_weight in ('bold', '600', '700', '800', '900')
    widths = []
    for cluster in split_project_text_clusters(text):
        cjk = any(is_cjk_char(ch) for ch in cluster)
        if advances is not None and not cjk and all(
            ch in advances and not _is_emoji_base(ch) and not _is_grapheme_extend(ch)
            for ch in cluster
        ):
            widths.append(sum(advances[ch] for ch in cluster) * font_size)
            continue
        # A precomposed letter the table lacks advances like its base letter.
        bases = [
            _base_letter(ch) for ch in cluster if not _is_grapheme_extend(ch)
        ]
        if advances is not None and not cjk and bases and all(
            ch in advances and not _is_emoji_base(ch) for ch in bases
        ) and all(
            unicodedata.category(ch) == 'Mn' and not 0xFE00 <= ord(ch) <= 0xFE0F
            for ch in cluster if _is_grapheme_extend(ch)
        ):
            widths.append(sum(advances[ch] for ch in bases) * font_size)
            continue
        width = _estimate_grapheme_width(cluster, font_size)
        widths.append(width * 1.05 if bold and not cjk else width)
    return widths


def estimate_text_width(text: str, font_size: float, font_weight: str = '400') -> float:
    """Estimate text width in SVG pixels."""
    return sum(estimate_text_cluster_widths(text, font_size, font_weight))


def _xml_escape(text: str) -> str:
    """Escape XML special characters."""
    return (text.replace('&', '&amp;')
                .replace('<', '&lt;')
                .replace('>', '&gt;')
                .replace('"', '&quot;'))
