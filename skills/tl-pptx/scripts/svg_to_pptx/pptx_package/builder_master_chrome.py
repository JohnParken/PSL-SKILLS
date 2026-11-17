"""Core PPTX assembly: create_pptx_with_native_svg."""
# Adapted from hugohe3/ppt-master 7e0df6705de0cd24345210d31f07fcb5a32b6900.
# MIT; Copyright (c) 2025-2026 Hugo He. See references/LICENSE-PPT-MASTER.txt and references/upstream.md.

from __future__ import annotations
import uuid
from pathlib import (
    Path,
)
from typing import Any
from xml.etree import ElementTree as ET

from .builder import (
    PptxStructureContext,
)

def _promote_common_chrome_shapes_to_masters(
    extract_dir: Path,
    structure: PptxStructureContext,
    slide_count: int,
    conversion_traces: list[dict[str, Any]] | None,
    *,
    verbose: bool = False,
) -> int:
    """Promote explicit repeated chrome SVG ids to their shared master."""
    from .builder import (
        PML_NS,
        SLIDE_LAYOUT_REL_TYPE,
        _append_shape_to_master,
        _canonical_shape_xml,
        _copy_shape_relationships_to_master,
        _create_cover_layout,
        _dominant_variant,
        _find_relationship_target,
        _is_strict_majority,
        _read_relationships,
        _relationships_path_for_part,
        _resolve_package_target,
        _set_slide_layout_target,
        _shape_relationships_supported,
        _timing_shape_ids,
        _top_level_shapes_by_id,
        _trace_chrome_shape_ids,
        _write_xml_tree,
    )
    if not conversion_traces:
        return 0
    trace_by_slide = {
        int(trace.get("slide_num", 0)): trace
        for trace in conversion_traces
        if trace.get("slide_num") is not None
    }
    if len(trace_by_slide) < slide_count:
        return 0

    slides_by_master: dict[str, list[int]] = {}
    for slide_num in range(1, slide_count + 1):
        master_part = structure.slide_master_part(slide_num)
        slides_by_master.setdefault(master_part, []).append(slide_num)

    promoted = 0
    promoted_roles = 0
    for master_part, slide_nums in slides_by_master.items():
        if len(slide_nums) < 2:
            continue
        slide_state: dict[int, dict[str, Any]] = {}
        for slide_num in slide_nums:
            slide_path = extract_dir / "ppt" / "slides" / f"slide{slide_num}.xml"
            rels_path = extract_dir / "ppt" / "slides" / "_rels" / f"slide{slide_num}.xml.rels"
            tree = ET.parse(slide_path)
            root = tree.getroot()
            slide_state[slide_num] = {
                "path": slide_path,
                "rels": _read_relationships(rels_path),
                "root": root,
                "shapes": _top_level_shapes_by_id(root),
                "timing_shape_ids": _timing_shape_ids(root),
                "tokens": _trace_chrome_shape_ids(trace_by_slide.get(slide_num)),
                "tree": tree,
            }

        # Per token, find the strict-majority identical variant. Slides
        # outside every dominant set become cover-layout minority slides.
        candidate_sets: dict[str, dict[int, str]] = {}
        all_tokens = sorted({
            token
            for state in slide_state.values()
            for token in state["tokens"]
        })
        for token in all_tokens:
            carriers: dict[int, str] = {}
            canonical_by_slide: dict[int, bytes] = {}
            for slide_num in slide_nums:
                state = slide_state[slide_num]
                shape_ids = state["tokens"].get(token, [])
                if len(shape_ids) != 1:
                    continue
                shape_id = shape_ids[0]
                shape = state["shapes"].get(shape_id)
                if shape is None:
                    continue
                if shape_id in state["timing_shape_ids"]:
                    continue
                if not _shape_relationships_supported(shape, state["rels"]):
                    continue
                carriers[slide_num] = shape_id
                canonical_by_slide[slide_num] = _canonical_shape_xml(
                    shape,
                    state["rels"],
                )
            dominant_xml, dominant_slides = _dominant_variant(canonical_by_slide)
            if dominant_xml is None:
                continue
            if not _is_strict_majority(len(dominant_slides), len(slide_nums)):
                continue
            candidate_sets[token] = {
                slide_num: carriers[slide_num] for slide_num in dominant_slides
            }

        if not candidate_sets:
            continue
        content_slides = sorted(set.intersection(
            *(set(slides) for slides in candidate_sets.values())
        ))
        if not _is_strict_majority(len(content_slides), len(slide_nums)):
            continue

        promotions: list[tuple[str, dict[int, str]]] = []
        claimed_shape_ids: dict[int, set[str]] = {
            slide_num: set() for slide_num in content_slides
        }
        for token in sorted(candidate_sets):
            shape_ids_by_slide = {
                slide_num: candidate_sets[token][slide_num]
                for slide_num in content_slides
            }
            # A flattened nested chrome group can emit several semantic trace
            # ids for the same generated DrawingML shape. Claim it once.
            if any(
                shape_ids_by_slide[slide_num] in claimed_shape_ids[slide_num]
                for slide_num in content_slides
            ):
                continue
            for slide_num, shape_id in shape_ids_by_slide.items():
                claimed_shape_ids[slide_num].add(shape_id)
            promotions.append((token, shape_ids_by_slide))

        if not promotions:
            continue

        # Master shapes always render behind slide-local shapes. Preserve the
        # original z-order by promoting only a common leading chrome prefix;
        # overlay headers/footers remain slide-local.
        token_by_shape_id = {
            slide_num: {
                shape_ids[slide_num]: token
                for token, shape_ids in promotions
            }
            for slide_num in content_slides
        }
        leading_token_orders: list[list[str]] = []
        for slide_num in content_slides:
            order: list[str] = []
            for shape_id in slide_state[slide_num]["shapes"]:
                token = token_by_shape_id[slide_num].get(shape_id)
                if token is None:
                    break
                order.append(token)
            leading_token_orders.append(order)

        safe_tokens = list(leading_token_orders[0])
        for order in leading_token_orders[1:]:
            common_length = 0
            for expected, actual in zip(safe_tokens, order):
                if expected != actual:
                    break
                common_length += 1
            safe_tokens = safe_tokens[:common_length]
            if not safe_tokens:
                break
        promotion_by_token = {token: shape_ids for token, shape_ids in promotions}
        promotions = [
            (token, promotion_by_token[token])
            for token in safe_tokens
        ]

        if not promotions:
            continue

        master_path = extract_dir / master_part
        master_rels_path = _relationships_path_for_part(extract_dir, master_part)
        for _token, shape_ids_by_slide in promotions:
            first_slide = content_slides[0]
            first_state = slide_state[first_slide]
            shape = first_state["shapes"][shape_ids_by_slide[first_slide]]
            master_shape = _copy_shape_relationships_to_master(
                shape,
                first_state["rels"],
                master_rels_path,
            )
            _append_shape_to_master(master_path, master_shape)
            promoted_roles += 1

            for slide_num, shape_id in shape_ids_by_slide.items():
                state = slide_state[slide_num]
                shape_to_remove = state["shapes"].get(shape_id)
                sp_tree = state["root"].find(f".//{{{PML_NS}}}cSld/{{{PML_NS}}}spTree")
                if sp_tree is not None and shape_to_remove is not None:
                    sp_tree.remove(shape_to_remove)
                    promoted += 1

        for slide_num in content_slides:
            state = slide_state[slide_num]
            _write_xml_tree(state["path"], state["tree"])

        # Minority slides (covers, section pages) keep every shape
        # slide-local and move to a Cover layout that hides the newly
        # promoted master chrome, so their rendering never changes.
        minority_slides = [
            slide_num for slide_num in slide_nums
            if slide_num not in set(content_slides)
        ]
        if minority_slides:
            first_minority_rels = (
                extract_dir / "ppt" / "slides" / "_rels"
                / f"slide{minority_slides[0]}.xml.rels"
            )
            base_target = _find_relationship_target(
                first_minority_rels, SLIDE_LAYOUT_REL_TYPE
            )
            if not base_target:
                raise RuntimeError(
                    f"Slide {minority_slides[0]} has no slide layout relationship"
                )
            base_layout_part = _resolve_package_target(
                f"ppt/slides/slide{minority_slides[0]}.xml", base_target
            )
            cover_target = _create_cover_layout(
                extract_dir, master_part, base_layout_part
            )
            for slide_num in minority_slides:
                rels_path = (
                    extract_dir / "ppt" / "slides" / "_rels"
                    / f"slide{slide_num}.xml.rels"
                )
                _set_slide_layout_target(rels_path, cover_target)
            if verbose:
                print(
                    "  Baseline cover layout: "
                    f"{len(minority_slides)} slide(s) keep slide-local chrome"
                )

    if verbose and promoted:
        print(
            "  Baseline master chrome: "
            f"promoted {promoted} slide shape(s) across {promoted_roles} shared object(s)"
        )
    return promoted


def _first_slide_number(extract_dir: Path) -> int:
    """Read firstSlideNum from presentation.xml (defaults to 1)."""
    presentation_path = extract_dir / "ppt" / "presentation.xml"
    try:
        root = ET.parse(presentation_path).getroot()
    except (OSError, ET.ParseError):
        return 1
    raw = root.attrib.get("firstSlideNum")
    if raw is None:
        return 1
    try:
        return int(raw)
    except ValueError:
        return 1


def _shape_with_id(root: ET.Element, shape_id: str) -> ET.Element | None:
    """Find a p:sp anywhere in the slide tree by its cNvPr id."""
    from .builder import (
        PML_NS,
    )
    for shape in root.iter(f"{{{PML_NS}}}sp"):
        cnv = shape.find(f"{{{PML_NS}}}nvSpPr/{{{PML_NS}}}cNvPr")
        if cnv is not None and cnv.attrib.get("id") == shape_id:
            return shape
    return None


def _replace_literal_run_with_slidenum_field(
    shape: ET.Element,
    expected_text: str,
    field_guid: str,
) -> bool:
    """Swap a single literal page-number run for an a:fld slidenum field."""
    from .builder import (
        DML_NS,
        PML_NS,
    )
    tx_body = shape.find(f"{{{PML_NS}}}txBody")
    if tx_body is None:
        return False
    a_t = f"{{{DML_NS}}}t"

    def _is_expected(text: str) -> bool:
        # A zero-padded literal ("02") names the same slide as "2".
        stripped = text.strip()
        return stripped == expected_text or (
            stripped.isdigit() and str(int(stripped)) == expected_text
        )

    total_text = "".join(t.text or "" for t in tx_body.iter(a_t))
    if not _is_expected(total_text):
        return False
    text_runs = [
        (paragraph, run)
        for paragraph in tx_body.iter(f"{{{DML_NS}}}p")
        for run in paragraph.findall(f"{{{DML_NS}}}r")
        if (run.findtext(a_t) or "").strip()
    ]
    if len(text_runs) != 1:
        return False
    paragraph, run = text_runs[0]
    if not _is_expected(run.findtext(a_t) or ""):
        return False

    fld = ET.Element(f"{{{DML_NS}}}fld", {"id": field_guid, "type": "slidenum"})
    r_pr = run.find(f"{{{DML_NS}}}rPr")
    if r_pr is not None:
        fld.append(ET.fromstring(ET.tostring(r_pr, encoding="utf-8")))
    fld_text = ET.SubElement(fld, a_t)
    fld_text.text = expected_text
    index = list(paragraph).index(run)
    paragraph.remove(run)
    paragraph.insert(index, fld)
    return True


def _convert_page_number_texts_to_fields(
    extract_dir: Path,
    slide_count: int,
    conversion_traces: list[dict[str, Any]] | None,
    *,
    context: str = "Baseline",
    verbose: bool = False,
) -> int:
    """Replace literal page-number chrome text with auto-updating fields.

    Only converts when the traced pageNumber/slideNumber shape's whole text
    equals the slide's expected display number (honoring firstSlideNum), so
    schemes like content-only numbering keep their literal text untouched.
    """
    from .builder import (
        _PAGE_NUMBER_TOKENS,
        _trace_chrome_shape_ids,
        _write_xml_tree,
    )
    if not conversion_traces:
        return 0
    trace_by_slide = {
        int(trace.get("slide_num", 0)): trace
        for trace in conversion_traces
        if trace.get("slide_num") is not None
    }
    first_slide_number = _first_slide_number(extract_dir)
    field_guid = f"{{{str(uuid.uuid4()).upper()}}}"

    converted = 0
    for slide_num in range(1, slide_count + 1):
        tokens = _trace_chrome_shape_ids(trace_by_slide.get(slide_num))
        shape_ids = sorted({
            shape_id
            for token, ids in tokens.items()
            if token in _PAGE_NUMBER_TOKENS
            for shape_id in ids
        })
        if len(shape_ids) != 1:
            continue
        slide_path = extract_dir / "ppt" / "slides" / f"slide{slide_num}.xml"
        tree = ET.parse(slide_path)
        shape = _shape_with_id(tree.getroot(), shape_ids[0])
        if shape is None:
            continue
        expected_text = str(first_slide_number + slide_num - 1)
        if _replace_literal_run_with_slidenum_field(shape, expected_text, field_guid):
            _write_xml_tree(slide_path, tree)
            converted += 1

    if verbose and converted:
        print(
            f"  {context} slide-number fields: "
            f"converted {converted} page number(s)"
        )
    return converted
