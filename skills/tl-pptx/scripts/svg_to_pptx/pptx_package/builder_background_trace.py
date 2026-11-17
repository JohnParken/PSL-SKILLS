"""Core PPTX assembly: create_pptx_with_native_svg."""
# Adapted from hugohe3/ppt-master 7e0df6705de0cd24345210d31f07fcb5a32b6900.
# MIT; Copyright (c) 2025-2026 Hugo He. See references/LICENSE-PPT-MASTER.txt and references/upstream.md.

from __future__ import annotations
import re
from pathlib import (
    Path,
)
from typing import Any
from xml.etree import ElementTree as ET
from ..semantic_markers import (
    chrome_token_from_markers,
)
from .template_structure import (
    TemplateStructureError,
)

from .builder import (
    PptxStructureContext,
)

def _extract_slide_background_xml(slide_xml: str) -> str | None:
    """Return the slide-level p:bg XML when it directly precedes spTree."""
    from .builder import (
        _SLIDE_BACKGROUND_RE,
    )
    match = _SLIDE_BACKGROUND_RE.search(slide_xml)
    return match.group("bg") if match else None


def _remove_slide_background_xml(slide_xml: str) -> str:
    """Remove a promoted slide-level p:bg from cSld."""
    from .builder import (
        _SLIDE_BACKGROUND_RE,
    )
    return _SLIDE_BACKGROUND_RE.sub(r"\g<prefix>\g<suffix>", slide_xml, count=1)


def _put_background_on_part(part_xml: str, background_xml: str) -> str | None:
    """Replace or insert p:bg before a slide/master/layout spTree.

    Returns None when the part carries a p:bg the canonical pattern cannot
    replace; inserting there would leave two p:bg children under p:cSld.
    """
    from .builder import (
        _SLIDE_BACKGROUND_RE,
    )
    match = _SLIDE_BACKGROUND_RE.search(part_xml)
    if match:
        return (
            part_xml[:match.start("bg")]
            + background_xml
            + part_xml[match.end("bg"):]
        )
    if "<p:bg" in part_xml:
        return None

    cslide_match = re.search(r"(<p:cSld\b[^>]*>)", part_xml)
    if not cslide_match:
        raise RuntimeError("PPTX slide/master/layout part has no p:cSld element")
    return (
        part_xml[:cslide_match.end()]
        + background_xml
        + part_xml[cslide_match.end():]
    )


def _dominant_variant(
    values_by_slide: dict[int, Any],
) -> tuple[Any | None, list[int]]:
    """Return the most common value and its slides, or None on a tie."""
    slides_by_value: dict[Any, list[int]] = {}
    for slide_num, value in sorted(values_by_slide.items()):
        slides_by_value.setdefault(value, []).append(slide_num)
    if not slides_by_value:
        return None, []
    best_count = max(len(slides) for slides in slides_by_value.values())
    dominant = [
        (value, slides)
        for value, slides in slides_by_value.items()
        if len(slides) == best_count
    ]
    if len(dominant) != 1:
        return None, []
    return dominant[0]


def _is_strict_majority(subset_size: int, total: int) -> bool:
    return subset_size >= 2 and subset_size * 2 > total


def _promote_common_slide_backgrounds_to_masters(
    extract_dir: Path,
    structure: PptxStructureContext,
    slide_count: int,
    *,
    verbose: bool = False,
) -> int:
    """Promote the majority slide background to its shared slide master.

    Every slide in the master group must carry an explicit background —
    a slide without one would start inheriting the promoted master fill.
    Minority slides keep their own slide-level background, which always
    overrides the master fill.
    """
    slides_by_master: dict[str, list[int]] = {}
    for slide_num in range(1, slide_count + 1):
        master_part = structure.slide_master_part(slide_num)
        slides_by_master.setdefault(master_part, []).append(slide_num)

    promoted = 0
    for master_part, slide_nums in slides_by_master.items():
        slide_backgrounds: dict[int, str] = {}
        for slide_num in slide_nums:
            slide_path = extract_dir / "ppt" / "slides" / f"slide{slide_num}.xml"
            slide_xml = slide_path.read_text(encoding="utf-8")
            background_xml = _extract_slide_background_xml(slide_xml)
            if not background_xml:
                slide_backgrounds = {}
                break
            slide_backgrounds[slide_num] = background_xml

        if not slide_backgrounds:
            continue
        background_xml, dominant_slides = _dominant_variant(slide_backgrounds)
        if background_xml is None:
            continue
        if not _is_strict_majority(len(dominant_slides), len(slide_nums)):
            continue

        master_path = extract_dir / master_part
        master_xml = master_path.read_text(encoding="utf-8")
        promoted_master_xml = _put_background_on_part(master_xml, background_xml)
        if promoted_master_xml is None:
            continue
        master_path.write_text(promoted_master_xml, encoding="utf-8")

        for slide_num in dominant_slides:
            slide_path = extract_dir / "ppt" / "slides" / f"slide{slide_num}.xml"
            slide_xml = slide_path.read_text(encoding="utf-8")
            slide_path.write_text(
                _remove_slide_background_xml(slide_xml),
                encoding="utf-8",
            )
            promoted += 1

    if verbose and promoted:
        print(f"  Baseline master background: promoted {promoted} slide background(s)")
    return promoted


def _chrome_token_from_svg_id(svg_id: str | None) -> str | None:
    """Return the baseline chrome token encoded in a source SVG id."""
    from .builder import (
        _CHROME_TRACE_TOKENS,
    )
    if not svg_id:
        return None
    lower = svg_id.lower()
    compact = re.sub(r"[-_\s]+", "", lower)
    if compact in _CHROME_TRACE_TOKENS:
        return compact
    split_tokens = {token for token in re.split(r"[-_\s]+", lower) if token}
    for token in _CHROME_TRACE_TOKENS:
        if token in split_tokens:
            return token
    return None


def _trace_chrome_shape_ids(
    trace: dict[str, Any] | None,
) -> dict[str, list[str]]:
    """Map chrome token to generated top-level shape ids for one slide."""
    result: dict[str, list[str]] = {}
    if not trace:
        return result
    for event in trace.get("events", []):
        if event.get("decision") != "native":
            continue
        if event.get("animation_override"):
            # Explicitly animated chrome stays slide-local.
            continue
        semantic_role = event.get("data-pptx-role")
        placeholder = event.get("data-pptx-placeholder")
        has_explicit_semantics = (
            semantic_role is not None or placeholder is not None
        )
        token = (
            chrome_token_from_markers(semantic_role, placeholder)
            if has_explicit_semantics
            else _chrome_token_from_svg_id(event.get("id"))
        )
        shape_id = event.get("shape_id")
        if token and shape_id is not None:
            shape_ids = result.setdefault(token, [])
            normalized_shape_id = str(shape_id)
            if normalized_shape_id not in shape_ids:
                shape_ids.append(normalized_shape_id)
    return result


def _trace_native_shape_ids(
    trace: dict[str, Any] | None,
) -> dict[str, list[str]]:
    """Map every traced SVG id to generated top-level shape ids."""
    result: dict[str, list[str]] = {}
    if not trace:
        return result
    for event in trace.get("events", []):
        if event.get("decision") != "native":
            continue
        svg_id = event.get("id")
        shape_id = event.get("shape_id")
        if not svg_id or shape_id is None:
            continue
        shape_ids = result.setdefault(str(svg_id), [])
        normalized = str(shape_id)
        if normalized not in shape_ids:
            shape_ids.append(normalized)
    return result


def _shape_id(elem: ET.Element) -> str | None:
    from .builder import (
        PML_NS,
    )
    for cnv in elem.iter(f"{{{PML_NS}}}cNvPr"):
        return cnv.attrib.get("id")
    return None


def _set_shape_name(elem: ET.Element, name: str) -> None:
    """Give one top-level shape a deterministic read-back identity."""
    from .builder import (
        PML_NS,
    )
    for cnv in elem.iter(f"{{{PML_NS}}}cNvPr"):
        cnv.set("name", name)
        return
    raise TemplateStructureError(
        f"Cannot name structured shape {name!r}: p:cNvPr is missing"
    )


def _roundtrip_morph_shape_ids(svg_path: Path, group_ids: set[str]) -> dict[tuple[str, str], int]:
    """Resolve preserved endpoints from imported identity, never SVG id spelling."""
    if not group_ids:
        return {}
    root = ET.parse(svg_path).getroot()
    result: dict[tuple[str, str], int] = {}
    for group_id in sorted(group_ids):
        groups = [node for node in root if node.tag == '{http://www.w3.org/2000/svg}g'
                  and node.get('id') == group_id]
        shape_id = groups[0].get('data-pptx-shape-id', '') if len(groups) == 1 else ''
        if (not shape_id.isdecimal()
                or groups[0].get('data-pptx-shape-scope') != 'slide'):
            raise TemplateStructureError(
                f'Morph target "{svg_path.stem}/{group_id}" has no unique imported '
                'Slide-local shape identity; edit this endpoint page as well, or '
                'pair groups on two edited pages'
            )
        result[(svg_path.stem, group_id)] = int(shape_id)
    return result
