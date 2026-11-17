"""Coordinate, transform, color, and font helpers for DrawingML conversion.

See references/shared-standards-core.md §2.1 for project geometry and
references/svg-effects.md §§6.2–6.8 for paint, image-fit, line-presentation,
and transform authoring contracts.
"""

from __future__ import annotations

import unicodedata




# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


# Adapted from utils.py; upstream MIT, Hugo He, 2025-2026.
def _base_letter(ch: str) -> str:
    """Return the unaccented letter a precomposed character advances like."""
    # Resolve shared symbols at call time; retain the public module contract.
    from .utils import (
        _UNDECOMPOSED_BASE_LETTERS,
    )
    decomposed = unicodedata.normalize('NFD', ch)
    if len(decomposed) > 1 and all(
        unicodedata.category(mark) == 'Mn' for mark in decomposed[1:]
    ):
        ch = decomposed[0]
    return ch.translate(_UNDECOMPOSED_BASE_LETTERS)


def _estimate_character_width(ch: str, font_size: float) -> float:
    # Resolve shared symbols at call time; retain the public module contract.
    from .utils import (
        _FIXED_SPACE_EMS,
        _base_letter,
        is_cjk_char,
    )
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
    # Resolve shared symbols at call time; retain the public module contract.
    from .utils import (
        _estimate_character_width,
        _is_emoji_base,
        _is_grapheme_extend,
        _is_regional_indicator,
    )
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


def primary_font_family(font_family: str | None) -> str:
    """Normalize the first family in a font stack."""
    return str(font_family or '').split(',')[0].strip().strip('\'"').lower()


def estimate_text_cluster_widths(
    text: str,
    font_size: float,
    font_weight: str = '400',
    *,
    font_family: str | None = None,
    font_style: str = 'normal',
) -> list[float]:
    """Estimate each project text cluster without inserting tracking."""
    # Resolve shared symbols at call time; retain the public module contract.
    from .utils import (
        _base_letter,
        _estimate_grapheme_width,
        _is_emoji_base,
        _is_grapheme_extend,
        get_font_advances,
        is_cjk_char,
        split_project_text_clusters,
    )
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
    # Resolve shared symbols at call time; retain the public module contract.
    from .utils import (
        estimate_text_cluster_widths,
    )
    return sum(estimate_text_cluster_widths(text, font_size, font_weight))


def _xml_escape(text: str) -> str:
    """Escape XML special characters."""
    return (text.replace('&', '&amp;')
                .replace('<', '&lt;')
                .replace('>', '&gt;')
                .replace('"', '&quot;'))
