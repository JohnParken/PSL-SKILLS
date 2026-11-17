"""Convert a DrawingML <a:tbl> into SVG.

Tables in PowerPoint are stored under <p:graphicFrame> with
graphicData uri="...drawingml/2006/table" wrapping a single <a:tbl>:

    <p:graphicFrame>
      <p:xfrm>...</p:xfrm>
      <a:graphic><a:graphicData uri="...table">
        <a:tbl>
          <a:tblPr/>
          <a:tblGrid>
            <a:gridCol w="..."/>...
          </a:tblGrid>
          <a:tr h="...">
            <a:tc [gridSpan=N] [rowSpan=N] [hMerge=1] [vMerge=1]>
              <a:txBody>...</a:txBody>
              <a:tcPr>
                <a:lnL/><a:lnR/><a:lnT/><a:lnB/>
                <a:solidFill/>... or <a:gradFill/> ...
              </a:tcPr>
            </a:tc>
          </a:tr>
        </a:tbl>
      </a:graphicData></a:graphic>
    </p:graphicFrame>

The graphicFrame's <p:xfrm> gives the table's slide-space position and total
size; <a:tblGrid> + <a:tr> heights distribute that size across columns/rows.

Cell painting order:
1. background fill (rect at cell box)
2. text body (re-uses convert_txbody)
3. cell borders (lnT / lnR / lnB / lnL — stroked as separate <line>s so
   neighbouring cells with different border styles render correctly)
"""

from __future__ import annotations

import copy
import json
import math
from dataclasses import dataclass
from typing import Any
from xml.etree import ElementTree as ET

from pptx_effects import txbody_has_run_effects
from semantic_table import compact_semantic_table_payload

from .color_resolver import ColorPalette, find_color_elem, resolve_color
from .emu_units import (
    NS,
    Xfrm,
    emu_to_px,
    fmt_num,
    hundredths_pt_to_px,
    ooxml_bool,
)
from .fill_to_svg import FillResult, resolve_fill
from .ln_to_svg import resolve_stroke
from .txbody_to_svg import (
    HyperlinkResolver,
    TextDiagnosticSink,
    TextImportError,
    _resolve_theme_typeface,
    convert_txbody,
)



# Upstream MIT, Hugo He 2025-2026; algorithms retained during module split.

def _direct_border_payload(
    ln: ET.Element,
    palette: ColorPalette | None,
) -> dict[str, Any]:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .tbl_to_svg import (
        _DIRECT_BORDER_WIDTH_MAX,
        _round_payload_number,
    )
    if set(ln.attrib) - {"w", "cap", "cmpd", "algn"}:
        raise ValueError("unsupported border line attribute")
    if ln.get("cap") not in {None, "flat"}:
        raise ValueError("unsupported border cap")
    if ln.get("cmpd") not in {None, "sng"}:
        raise ValueError("unsupported border compound style")
    if ln.get("algn") not in {None, "ctr"}:
        raise ValueError("unsupported border alignment")
    width_emu: int | None = None
    if "w" in ln.attrib:
        raw_width = ln.get("w", "")
        if not raw_width.isdigit():
            raise ValueError("invalid border width")
        width_emu = int(raw_width)
        if not 0 < width_emu <= _DIRECT_BORDER_WIDTH_MAX:
            raise ValueError("invalid border width")

    children = list(ln)
    child_names = [child.tag.rsplit("}", 1)[-1] for child in children]
    if child_names.count("noFill") == 1:
        no_fill = next(
            child for child in children
            if child.tag.rsplit("}", 1)[-1] == "noFill"
        )
        if (
            no_fill.attrib
            or list(no_fill)
            or any(
                name not in {
                    "noFill", "prstDash", "round", "headEnd", "tailEnd",
                }
                for name in child_names
            )
        ):
            raise ValueError("invalid noFill border")
        return {"style": "none"}

    decoration_names = {"round", "headEnd", "tailEnd"}
    if child_names.count("solidFill") != 1 or any(
        name not in {"solidFill", "prstDash", *decoration_names}
        for name in child_names
    ):
        raise ValueError("unsupported border paint")
    if (
        child_names.count("prstDash") > 1
        or any(child_names.count(name) > 1 for name in decoration_names)
        or width_emu is None
    ):
        raise ValueError("invalid solid border")
    dash = next(
        (child for child in children if child.tag.rsplit("}", 1)[-1] == "prstDash"),
        None,
    )
    if dash is not None and (
        dash.attrib != {"val": "solid"} or list(dash)
    ):
        raise ValueError("unsupported border dash")
    line_join = next(
        (child for child in children if child.tag.rsplit("}", 1)[-1] == "round"),
        None,
    )
    if line_join is not None and (line_join.attrib or list(line_join)):
        raise ValueError("unsupported border line join")
    for endpoint_name in ("headEnd", "tailEnd"):
        endpoint = next(
            (
                child for child in children
                if child.tag.rsplit("}", 1)[-1] == endpoint_name
            ),
            None,
        )
        if endpoint is None:
            continue
        if (
            set(endpoint.attrib) - {"type", "w", "len"}
            or endpoint.get("type") not in {None, "none"}
            or endpoint.get("w") not in {None, "med"}
            or endpoint.get("len") not in {None, "med"}
            or list(endpoint)
        ):
            raise ValueError("unsupported border endpoint")

    solid_fill = next(
        child for child in children
        if child.tag.rsplit("}", 1)[-1] == "solidFill"
    )
    if solid_fill.attrib or len(list(solid_fill)) != 1:
        raise ValueError("invalid solid border fill")
    color_elem = find_color_elem(solid_fill)
    try:
        color, alpha = resolve_color(color_elem, palette, strict=True)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("invalid solid border color") from exc
    if color is None or alpha != 1.0:
        raise ValueError("border color must resolve to opaque RGB")

    width = _round_payload_number(emu_to_px(str(width_emu)))
    if width <= 0:
        raise ValueError("border width is too small")
    return {
        "style": "solid",
        "color": color,
        "width": width,
    }


def _table_has_unsupported_direct_formatting(
    tbl: ET.Element,
    palette: ColorPalette | None,
) -> bool:
    """Reject direct cell features the compact native schema cannot retain."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .tbl_to_svg import (
        _DIRECT_BORDER_TAGS,
        _direct_border_payload,
        _table_cell_has_unsupported_topology,
        _table_text_has_unsupported_outline,
        _text_body_has_unsupported_formatting,
    )
    for tc in tbl.findall(".//a:tc", NS):
        if _table_cell_has_unsupported_topology(tc):
            return True
        tc_pr = tc.find("a:tcPr", NS)
        if tc_pr is not None:
            allowed_attrs = {
                "marL", "marR", "marT", "marB", "anchor",
                "anchorCtr", "horzOverflow",
            }
            if any(name not in allowed_attrs for name in tc_pr.attrib):
                return True
            if tc_pr.get("anchor") not in {None, "t", "ctr", "b"}:
                return True
            if tc_pr.get("anchorCtr") not in {None, "0", "1", "false", "true"}:
                return True
            if tc_pr.get("horzOverflow") not in {None, "clip", "overflow"}:
                return True
            if any(
                child.tag.rsplit("}", 1)[-1]
                not in {"solidFill", "noFill", *_DIRECT_BORDER_TAGS}
                for child in tc_pr
            ):
                return True
            fills = [
                child for child in tc_pr
                if child.tag.rsplit("}", 1)[-1] in {"solidFill", "noFill"}
            ]
            if len(fills) > 1:
                return True
            no_fill = tc_pr.find("a:noFill", NS)
            if no_fill is not None and (no_fill.attrib or list(no_fill)):
                return True
            for border_tag in _DIRECT_BORDER_TAGS:
                borders = tc_pr.findall(f"a:{border_tag}", NS)
                if len(borders) > 1:
                    return True
                if borders:
                    try:
                        _direct_border_payload(borders[0], palette)
                    except ValueError:
                        return True
            solid_fill = tc_pr.find("a:solidFill", NS)
            if solid_fill is not None:
                fill = resolve_fill(tc_pr, palette)
                if (
                    not fill.attrs
                    or not fill.attrs.get("fill", "").startswith("#")
                    or set(fill.attrs) - {"fill", "fill-opacity"}
                ):
                    return True
        tx_body = tc.find("a:txBody", NS)
        if (
            _text_body_has_unsupported_formatting(tx_body)
            or _table_text_has_unsupported_outline(tx_body, palette)
        ):
            return True
    return False


def _table_cell_has_unsupported_topology(tc: ET.Element) -> bool:
    """Accept only the closed optional txBody -> optional tcPr cell sequence."""
    if any(
        name not in {"gridSpan", "rowSpan", "hMerge", "vMerge"}
        for name in tc.attrib
    ):
        return True
    tx_body_tag = f"{{{NS['a']}}}txBody"
    tc_pr_tag = f"{{{NS['a']}}}tcPr"
    child_tags = [child.tag for child in tc]
    child_index = 0
    if child_tags[:1] == [tx_body_tag]:
        child_index += 1
    if child_tags[child_index:child_index + 1] == [tc_pr_tag]:
        child_index += 1
    return child_index != len(child_tags)


def _text_body_has_unsupported_formatting(tx_body: ET.Element | None) -> bool:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .tbl_to_svg import (
        _table_run_props_have_unsupported_formatting,
    )
    if tx_body is None:
        return False
    if tx_body.attrib:
        return True

    body_pr_tag = f"{{{NS['a']}}}bodyPr"
    list_style_tag = f"{{{NS['a']}}}lstStyle"
    paragraph_tag = f"{{{NS['a']}}}p"
    body_tags = [child.tag for child in tx_body]
    body_index = 0
    if not body_tags or body_tags[0] != body_pr_tag:
        return True
    body_index += 1
    if body_index < len(body_tags) and body_tags[body_index] == list_style_tag:
        body_index += 1
    if body_index == len(body_tags) or any(
        tag != paragraph_tag for tag in body_tags[body_index:]
    ):
        return True

    relationship_prefix = f"{{{NS['r']}}}"
    forbidden_run_children = {
        f"{{{NS['a']}}}extLst",
        f"{{{NS['a']}}}hlinkClick",
        f"{{{NS['a']}}}hlinkMouseOver",
    }
    if any(
        node.tag in forbidden_run_children
        or any(name.startswith(relationship_prefix) for name in node.attrib)
        for node in tx_body.iter()
    ):
        return True

    body_pr = tx_body.find("a:bodyPr", NS)
    if body_pr is None or body_pr.attrib or list(body_pr):
        return True
    list_style = tx_body.find("a:lstStyle", NS)
    if list_style is not None and (list_style.attrib or list(list_style)):
        return True
    for paragraph in tx_body.findall("a:p", NS):
        if paragraph.attrib:
            return True
        p_pr_tag = f"{{{NS['a']}}}pPr"
        run_tag = f"{{{NS['a']}}}r"
        end_r_pr_tag = f"{{{NS['a']}}}endParaRPr"
        direct_tags = [child.tag for child in paragraph]
        paragraph_index = 0
        if direct_tags[:1] == [p_pr_tag]:
            paragraph_index += 1
        while (
            paragraph_index < len(direct_tags)
            and direct_tags[paragraph_index] == run_tag
        ):
            paragraph_index += 1
        if (
            paragraph_index < len(direct_tags)
            and direct_tags[paragraph_index] == end_r_pr_tag
        ):
            paragraph_index += 1
        if paragraph_index != len(direct_tags):
            return True

        p_pr = paragraph.find("a:pPr", NS)
        if p_pr is not None:
            alignment = p_pr.get("algn")
            if alignment not in {None, "l", "ctr", "r", "just"}:
                return True
            p_pr_tags = [child.tag.rsplit("}", 1)[-1] for child in p_pr]
            if p_pr_tags.count("defRPr") > 1 or p_pr_tags.count("buNone") > 1:
                return True
            bullet_tags = [tag for tag in p_pr_tags if tag.startswith("bu")]
            if bullet_tags and "buNone" not in bullet_tags:
                return True

        for run in paragraph.findall("a:r", NS):
            if run.attrib:
                return True
            r_pr_tag = f"{{{NS['a']}}}rPr"
            text_tag = f"{{{NS['a']}}}t"
            run_tags = [child.tag for child in run]
            if run_tags not in ([text_tag], [r_pr_tag, text_tag]):
                return True
            text_node = run.find("a:t", NS)
            if text_node is None or list(text_node):
                return True
            allowed_text_attrs = {"{http://www.w3.org/XML/1998/namespace}space"}
            if any(name not in allowed_text_attrs for name in text_node.attrib):
                return True
            r_pr = run.find("a:rPr", NS)
            if _table_run_props_have_unsupported_formatting(r_pr):
                return True
        end_r_pr = paragraph.find("a:endParaRPr", NS)
        if _table_run_props_have_unsupported_formatting(end_r_pr):
            return True
    return False


def _table_run_props_have_unsupported_formatting(
    run_props: ET.Element | None,
) -> bool:
    """Return whether one run uses semantics outside the normalized schema."""
    if run_props is None:
        return False
    allowed_children = {
        "effectLst", "latin", "ea", "cs", "sym", "ln", "solidFill",
    }
    for child in run_props:
        name = child.tag.rsplit("}", 1)[-1]
        if name not in allowed_children:
            return True
        if name == "effectLst" and (child.attrib or list(child)):
            return True
    return False


def _native_run_outline_payload(
    line: ET.Element | None,
    palette: ColorPalette | None,
) -> dict[str, Any] | None:
    """Normalize one visible text outline into the shared line schema."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .tbl_to_svg import (
        _direct_border_payload,
    )
    if line is None:
        return None
    if line.find("a:noFill", NS) is not None:
        return None
    raw_width = line.get("w")
    if raw_width is None:
        return None
    if not raw_width.isdigit() or int(raw_width) == 0:
        return None
    return _direct_border_payload(line, palette)


def _table_text_has_unsupported_outline(
    tx_body: ET.Element | None,
    palette: ColorPalette | None,
) -> bool:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .tbl_to_svg import (
        _native_run_outline_payload,
    )
    if tx_body is None:
        return False
    for run_props in tx_body.findall(".//a:rPr", NS):
        line = run_props.find("a:ln", NS)
        if line is None:
            continue
        try:
            _native_run_outline_payload(line, palette)
        except ValueError:
            return True
    return False


def _legacy_text_body_has_unsupported_formatting(
    tx_body: ET.Element | None,
) -> bool:
    """Return the pre-P2-T4 gate so active plain payloads stay unchanged."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .tbl_to_svg import (
        _legacy_effective_run_signature,
        _legacy_run_props_have_unsupported_formatting,
    )
    if tx_body is None:
        return False
    body_pr = tx_body.find("a:bodyPr", NS)
    if body_pr is not None and (body_pr.attrib or list(body_pr)):
        return True
    list_style = tx_body.find("a:lstStyle", NS)
    if list_style is not None and (list_style.attrib or list(list_style)):
        return True
    if (
        tx_body.find(".//a:br", NS) is not None
        or tx_body.find(".//a:fld", NS) is not None
        or tx_body.find(".//a:tab", NS) is not None
    ):
        return True

    run_signatures: set[tuple[str | None, str | None, bytes | None]] = set()
    for paragraph in tx_body.findall("a:p", NS):
        p_pr = paragraph.find("a:pPr", NS)
        alignment = p_pr.get("algn") if p_pr is not None else None
        if alignment not in {None, "l", "ctr", "r"}:
            return True
        if p_pr is not None:
            if any(name != "algn" for name in p_pr.attrib):
                return True
            if any(
                child.tag.rsplit("}", 1)[-1] not in {"defRPr", "buNone"}
                for child in p_pr
            ):
                return True

        default_r_pr = p_pr.find("a:defRPr", NS) if p_pr is not None else None
        if _legacy_run_props_have_unsupported_formatting(default_r_pr):
            return True
        for run in paragraph.findall("a:r", NS):
            r_pr = run.find("a:rPr", NS)
            if _legacy_run_props_have_unsupported_formatting(r_pr):
                return True
            run_signatures.add(
                _legacy_effective_run_signature(r_pr, default_r_pr)
            )
        end_r_pr = paragraph.find("a:endParaRPr", NS)
        if _legacy_run_props_have_unsupported_formatting(end_r_pr):
            return True
        if not paragraph.findall("a:r", NS) and end_r_pr is not None:
            run_signatures.add(
                _legacy_effective_run_signature(end_r_pr, default_r_pr)
            )

    return len(run_signatures) > 1


def _legacy_run_props_have_unsupported_formatting(
    r_pr: ET.Element | None,
) -> bool:
    if r_pr is None:
        return False
    if ooxml_bool(r_pr.get("i")):
        return True
    if r_pr.get("u") not in {None, "none"}:
        return True
    if r_pr.get("strike") not in {None, "noStrike"}:
        return True
    if r_pr.get("baseline") not in {None, "0"}:
        return True
    if r_pr.get("cap") not in {None, "none"}:
        return True
    if r_pr.get("spc") not in {None, "0"}:
        return True
    allowed_attrs = {
        "lang", "altLang", "sz", "b", "i", "u", "strike", "dirty",
        "baseline", "cap", "spc",
    }
    if any(name not in allowed_attrs for name in r_pr.attrib):
        return True
    solid_fill = r_pr.find("a:solidFill", NS)
    if solid_fill is not None and solid_fill.find(".//a:alpha", NS) is not None:
        return True
    return any(
        child.tag.rsplit("}", 1)[-1] != "solidFill"
        for child in r_pr
    )


def _legacy_effective_run_signature(
    r_pr: ET.Element | None,
    default_r_pr: ET.Element | None,
) -> tuple[str | None, str | None, bytes | None]:
    def attr(name: str) -> str | None:
        if r_pr is not None and r_pr.get(name) is not None:
            return r_pr.get(name)
        return default_r_pr.get(name) if default_r_pr is not None else None

    solid_fill = r_pr.find("a:solidFill", NS) if r_pr is not None else None
    if solid_fill is None and default_r_pr is not None:
        solid_fill = default_r_pr.find("a:solidFill", NS)
    fill_xml = (
        ET.tostring(solid_fill, encoding="utf-8")
        if solid_fill is not None else None
    )
    return attr("b"), attr("sz"), fill_xml


