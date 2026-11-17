"""PPT Master motion core; responsibility-split, with stable original API.

MIT Copyright (c) 2025-2026 Hugo He.
Upstream commit: 7e0df6705de0cd24345210d31f07fcb5a32b6900.
"""
from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .animation_config import GroupTarget
import re
import statistics
from pathlib import Path
from xml.etree import ElementTree as ET
from hyperlink_contract import SHAPE_HYPERLINK_ATTR
from .drawingml.utils import SVG_NS
from .semantic_markers import is_static_page_frame

def _tag_name(elem: ET.Element) -> str:
    from .animation_config import (
        SVG_NS,
    )
    return elem.tag.replace(f'{{{SVG_NS}}}', '')


def is_chrome_id(elem_id: str | None) -> bool:
    """Return whether a group id represents static slide chrome."""
    from .animation_config import (
        _CHROME_ID_TOKENS,
        _RULE_QUALIFIERS,
        _RULE_TOKENS,
        re,
    )
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


def _font_size_px(elem: ET.Element) -> float | None:
    """Return an element's own font-size in px from its attribute or style."""
    from .animation_config import (
        _FONT_SIZE_RE,
        re,
    )
    raw = elem.get('font-size')
    if raw is None:
        match = _FONT_SIZE_RE.search(elem.get('style') or '')
        raw = match.group(1) if match else None
    if raw is None:
        return None
    try:
        return float(re.match(r'[0-9.]+', raw.strip()).group(0))
    except (AttributeError, ValueError):
        return None


def _text_sizes(scope: ET.Element) -> list[float]:
    """Return every declared font-size among text runs under ``scope``."""
    from .animation_config import (
        _font_size_px,
        _tag_name,
    )
    return [
        size
        for elem in scope.iter()
        if _tag_name(elem) in {'text', 'tspan'}
        for size in (_font_size_px(elem),)
        if size is not None
    ]


def _max_text_size(scope: ET.Element) -> float | None:
    from .animation_config import (
        _text_sizes,
    )
    sizes = _text_sizes(scope)
    return max(sizes) if sizes else None


def _page_reference_size(root: ET.Element) -> float | None:
    """Return the median of the page's distinct text sizes.

    Running labels and page numbers sit below it; a title block sits at or
    above it even when a hero numeral is the page's largest text.
    """
    from .animation_config import (
        _text_sizes,
        statistics,
    )
    distinct = sorted(set(_text_sizes(root)))
    return statistics.median(distinct) if distinct else None


def _holds_page_title(group: ET.Element, reference_size: float | None) -> bool:
    """A header/footer-named group whose text reaches the page's median size
    is the title block, not chrome."""
    from .animation_config import (
        _max_text_size,
    )
    if reference_size is None:
        return False
    group_max = _max_text_size(group)
    return group_max is not None and group_max >= reference_size - 1e-6


def _names_title_block(group_id: str) -> bool:
    from .animation_config import (
        _TITLE_BLOCK_TOKENS,
        re,
    )
    tokens = re.split(r'[-_]', group_id.lower())
    return any(t in _TITLE_BLOCK_TOKENS for t in tokens if t)


def scan_root_primitives(svg_path: Path) -> dict[str, str]:
    """Return id -> description for visible direct-root non-group elements."""
    from .animation_config import (
        ET,
        _NON_VISUAL_TAGS,
        _tag_name,
        effective_top_level,
        usable_animation_group_id,
    )
    root = ET.parse(str(svg_path)).getroot()
    primitives: dict[str, str] = {}
    for child in root:
        child = effective_top_level(child)
        tag = _tag_name(child)
        if tag in _NON_VISUAL_TAGS or tag == 'g':
            continue
        elem_id = usable_animation_group_id(child.get('id'))
        if elem_id is None:
            continue
        role = child.get('data-pptx-role')
        description = f'<{tag}>'
        if role:
            description += f' with data-pptx-role="{role}"'
        primitives[elem_id] = description
    return primitives


def anchor_wrapped_group(elem: ET.Element) -> ET.Element | None:
    """Return the single ``<g>`` a bare hyperlink anchor wraps, else ``None``.

    ``<a href><g id>…</g></a>`` is the canonical whole-object link
    (native-hyperlinks.md §2). The anchor carries only its target, so the
    group inside is the page's real top-level unit: it stays the animation
    anchor and the checker's grouping unit, and export attaches the click to
    its leaves. An anchor with other attributes or several children is an
    ordinary container.
    """
    from .animation_config import (
        _ANCHOR_OWN_ATTRIBUTES,
        _NON_VISUAL_TAGS,
        _tag_name,
    )
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
    from .animation_config import (
        anchor_wrapped_group,
    )
    wrapped = anchor_wrapped_group(elem)
    return elem if wrapped is None else wrapped


def usable_animation_group_id(raw: str | None) -> str | None:
    """Return one nonblank SVG animation anchor verbatim, else ``None``."""
    return raw if raw and raw.strip() else None


def scan_svg_targets(
    svg_path: Path,
    *,
    include_hidden: bool = False,
) -> tuple[list[GroupTarget], list[str]]:
    """Scan one SVG for top-level visible group ids and anonymous groups."""
    from .animation_config import (
        ET,
        GroupTarget,
        SHAPE_HYPERLINK_ATTR,
        _NON_VISUAL_TAGS,
        _holds_page_title,
        _names_title_block,
        _page_reference_size,
        _tag_name,
        effective_top_level,
        is_chrome_id,
        is_static_page_frame,
        usable_animation_group_id,
    )
    # The converter imports animation policy; defer this shared visibility scan.
    from .drawingml.converter import collect_hidden_visuals

    root = ET.parse(str(svg_path)).getroot()
    hidden_by_id = {id(element): reason for element, reason in collect_hidden_visuals(root)}
    targets: list[GroupTarget] = []
    anonymous_groups: list[str] = []
    visual_index = 0
    page_reference_size = _page_reference_size(root)

    for child in root:
        child = effective_top_level(child)
        tag = _tag_name(child)
        if tag in _NON_VISUAL_TAGS:
            continue
        visual_index += 1
        if tag != 'g':
            continue
        hidden_reason = hidden_by_id.get(id(child))
        if hidden_reason and not include_hidden:
            continue
        group_id = usable_animation_group_id(child.get('id'))
        if group_id is None:
            if hidden_reason is None:
                anonymous_groups.append(f'{svg_path.stem}: top-level group #{visual_index}')
            continue
        role = child.get('data-pptx-role')
        placeholder = child.get('data-pptx-placeholder')
        has_explicit_semantics = role is not None or placeholder is not None
        has_structural_layer = child.get('data-pptx-layer') is not None
        semantic_static = (
            has_explicit_semantics
            and is_static_page_frame(role, placeholder)
        )
        # A static role/placeholder marker makes a group chrome (kept out of
        # scaffold, listing, and automatic animation) but not structural: an
        # explicit sidecar entry may still animate or Morph-pair it. Only a
        # ``data-pptx-layer`` group is structural.
        structurally_static = has_structural_layer
        if has_structural_layer:
            chrome = True
        elif has_explicit_semantics:
            chrome = semantic_static
        else:
            chrome = is_chrome_id(group_id)
            if (
                chrome
                and _names_title_block(group_id)
                and _holds_page_title(child, page_reference_size)
            ):
                chrome = False
        targets.append(
            GroupTarget(
                slide=svg_path.stem,
                group_id=group_id,
                order=visual_index,
                chrome=chrome,
                structurally_static=structurally_static,
                hidden_reason=hidden_reason,
                placeholder=placeholder,
                on_structured_page=root.get('data-pptx-layout') is not None,
                has_hyperlink=any(
                    _tag_name(descendant) == 'a'
                    or descendant.get(SHAPE_HYPERLINK_ATTR) is not None
                    for descendant in child.iter()
                ),
            )
        )

    return targets, anonymous_groups


def _duplicate_target_ids(targets: list[GroupTarget]) -> tuple[str, ...]:
    """Return duplicate top-level animation anchors in deterministic order."""
    counts: dict[str, int] = {}
    for target in targets:
        counts[target.group_id] = counts.get(target.group_id, 0) + 1
    return tuple(sorted(group_id for group_id, count in counts.items() if count > 1))


def _duplicate_target_error(slide_name: str, duplicates: tuple[str, ...]) -> str:
    rendered = ', '.join(repr(group_id) for group_id in duplicates)
    return (
        f'SVG slide "{slide_name}" has duplicate top-level group id(s): '
        f'{rendered}; animation target ids must be unique'
    )


def _require_unique_target_ids(
    slide_name: str,
    targets: list[GroupTarget],
) -> None:
    from .animation_config import (
        _duplicate_target_error,
        _duplicate_target_ids,
    )
    duplicates = _duplicate_target_ids(targets)
    if duplicates:
        raise ValueError(_duplicate_target_error(slide_name, duplicates))


