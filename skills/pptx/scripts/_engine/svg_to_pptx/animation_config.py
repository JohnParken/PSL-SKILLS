# Adapted from PPT Master 5e8856b (MIT); see assets/licenses/ppt_master.txt.
"""Structural group labels only; animation playback is outside this skill."""
from __future__ import annotations
import re
from xml.etree import ElementTree as ET

from .drawingml.utils import SVG_NS, XLINK_NS

_NON_VISUAL_TAGS = frozenset(('defs', 'title', 'desc', 'metadata', 'style'))
_ANCHOR_OWN_ATTRIBUTES = frozenset((
    'href',
    f'{{{XLINK_NS}}}href',
    'xlink:href',
))

_CHROME_ID_TOKENS = frozenset({
    'background', 'bg',
    'decoration', 'decorations', 'decor',
    'header', 'footer',
    'chrome', 'watermark',
    'pagenumber', 'pagenum', 'slidenumber', 'slidenum',
    'logo', 'nav',
})


_RULE_TOKENS = frozenset({'rule', 'rules'})


_RULE_QUALIFIERS = frozenset({
    'hairline', 'thin', 'horizontal', 'vertical', 'h', 'v',
    'top', 'bottom', 'left', 'right', 'mid', 'middle',
    'section', 'page', 'title', 'header', 'footer', 'divider',
})


def is_chrome_id(elem_id: str | None) -> bool:
    """Return whether a group id represents static slide chrome."""
    if not elem_id:
        return False
    lower = elem_id.lower()
    compact = lower.replace('-', '').replace('_', '')
    if compact in _CHROME_ID_TOKENS:
        return True
    tokens = [t for t in re.split(r'[-_]', lower) if t]
    if any(t in _CHROME_ID_TOKENS for t in tokens):
        return True
    if compact in _RULE_TOKENS:
        return True
    if tokens and tokens[0] in _RULE_TOKENS and all(
        t.isdigit() or t in _RULE_QUALIFIERS for t in tokens[1:]
    ):
        return True
    if tokens and tokens[-1] in _RULE_TOKENS and all(
        t in _RULE_QUALIFIERS or t.isdigit() for t in tokens[:-1]
    ):
        return True
    return False


def usable_animation_group_id(raw: str | None) -> str | None:
    """Return one nonblank SVG animation anchor verbatim, else ``None``."""
    return raw if raw and raw.strip() else None


def _tag_name(elem: ET.Element) -> str:
    return elem.tag.replace(f'{{{SVG_NS}}}', '')


def anchor_wrapped_group(elem: ET.Element) -> ET.Element | None:
    """Return the single group wrapped by an otherwise bare hyperlink anchor."""
    if _tag_name(elem) != 'a':
        return None
    if any(attr not in _ANCHOR_OWN_ATTRIBUTES for attr in elem.attrib):
        return None
    visual = [child for child in elem if _tag_name(child) not in _NON_VISUAL_TAGS]
    if len(visual) != 1 or _tag_name(visual[0]) != 'g':
        return None
    return visual[0]


def effective_top_level(elem: ET.Element) -> ET.Element:
    """Return the element a top-level scan should treat ``elem`` as."""
    wrapped = anchor_wrapped_group(elem)
    return elem if wrapped is None else wrapped
