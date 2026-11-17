"""Extract native Chart replacement metadata from PPTX chart parts.

The visual chart preview still comes from the existing graphicFrame fallback.
This module only builds a conservative ``data-pptx-replace-with="chart"``
payload when the chart XML cache can be mapped to the current chart schema.
"""

from __future__ import annotations

import base64
import hashlib
import math
import re
from dataclasses import dataclass
from typing import Any
from xml.etree import ElementTree as ET

from svg_to_pptx.drawingml.utils import parse_font_family
from svg_to_pptx.native_objects.chart_data import (
    validate_chart_payload,
    validate_data_label_position,
)

from .chartex_to_svg import UnsupportedChartEx, extract_native_chartex_payload
from .color_resolver import ColorPalette, find_color_elem, resolve_color
from .emu_units import NS, Xfrm, ooxml_bool
from .normalized_chart_svg import SeriesVisualStyle, render_normalized_chart_svg
from .ooxml_loader import OoxmlPackage, PartRef



# Upstream MIT, Hugo He 2025-2026; algorithms retained during module split.

def _chart_text(container: ET.Element | None) -> str:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .chart_to_svg import (
        C_NS,
    )
    if container is None:
        return ""
    paragraphs: list[str] = []
    for paragraph in container.findall(".//a:p", C_NS):
        text = "".join(node.text or "" for node in paragraph.findall(".//a:t", C_NS))
        if text:
            paragraphs.append(text)
    if paragraphs:
        return "\n".join(paragraphs)
    values = [node.text or "" for node in container.findall(".//c:v", C_NS)]
    return "".join(values)


def _canonical_title_paragraph(paragraph: ET.Element) -> dict[str, Any] | None:
    """Return one exporter-canonical or basic Office title paragraph."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .chart_to_svg import (
        C_NS,
        _UnsupportedChart,
        _canonical_srgb_color,
        _local_name,
        _round_payload_number,
    )
    if paragraph.attrib or [_local_name(child.tag) for child in paragraph] != ["r"]:
        return None
    run = paragraph.find("a:r", C_NS)
    if run is None or run.attrib:
        return None
    run_child_names = [_local_name(child.tag) for child in run]
    if run_child_names == ["t"]:
        text = run.find("a:t", C_NS)
        if (
            text is None
            or text.attrib
            or list(text)
            or not (text.text or "")
            or text.text != text.text.strip()
        ):
            return None
        return {"text": text.text}
    if run_child_names != ["rPr", "t"]:
        return None
    run_props = run.find("a:rPr", C_NS)
    text = run.find("a:t", C_NS)
    if (
        run_props is None
        or set(run_props.attrib) != {"lang", "sz"}
        or text is None
        or text.attrib
        or list(text)
        or not (text.text or "")
        or text.text != text.text.strip()
    ):
        return None
    size_token = run_props.attrib["sz"]
    if re.fullmatch(r"[0-9]+", size_token) is None:
        return None
    size = int(size_token)
    if size % 10 != 0 or not 100 <= size <= 400000:
        return None
    child_names = [_local_name(child.tag) for child in run_props]
    if child_names not in ([], ["solidFill"], ["latin", "ea"], ["solidFill", "latin", "ea"]):
        return None
    solid_fill = run_props.find("a:solidFill", C_NS)
    color = None
    if solid_fill is not None:
        try:
            color = _canonical_srgb_color(solid_fill)
        except _UnsupportedChart:
            return None
    latin = run_props.find("a:latin", C_NS)
    east_asian = run_props.find("a:ea", C_NS)
    if (latin is None) != (east_asian is None):
        return None
    for font in (latin, east_asian):
        if font is not None and (
            set(font.attrib) != {"typeface"}
            or not font.attrib["typeface"].strip()
            or list(font)
        ):
            return None
    entry: dict[str, Any] = {
        "text": text.text,
        "font_size": _round_payload_number(size / 75.0),
    }
    if color is not None:
        entry["color"] = f"#{color}"
    if latin is not None and east_asian is not None:
        latin_name = latin.attrib["typeface"]
        east_asian_name = east_asian.attrib["typeface"]
        font_family = (
            latin_name
            if latin_name == east_asian_name
            else f"{latin_name}, {east_asian_name}"
        )
        resolved_fonts = parse_font_family(font_family)
        if (
            resolved_fonts["latin"] != latin_name
            or resolved_fonts["ea"] != east_asian_name
        ):
            return None
        entry["font_family"] = font_family
    return entry


def _canonical_title_entries(title: ET.Element | None) -> list[dict[str, Any]] | None:
    """Recognize exporter-canonical or basic Office rich title structure."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .chart_to_svg import (
        C_NS,
        _canonical_title_paragraph,
        _local_name,
    )
    if title is None:
        return None
    title_child_names = [_local_name(child.tag) for child in title]
    if title_child_names not in (["tx", "layout"], ["tx", "layout", "overlay"]):
        return None
    overlay = title.find("c:overlay", C_NS)
    if overlay is not None and (
        set(overlay.attrib) != {"val"}
        or list(overlay)
        or ooxml_bool(overlay.attrib.get("val"), True)
    ):
        return None
    tx = title.find("c:tx", C_NS)
    layout = title.find("c:layout", C_NS)
    rich = title.find("c:tx/c:rich", C_NS)
    if (
        tx is None
        or tx.attrib
        or [_local_name(child.tag) for child in tx] != ["rich"]
        or layout is None
        or layout.attrib
        or list(layout)
        or rich is None
        or rich.attrib
    ):
        return None
    children = list(rich)
    child_names = [_local_name(child.tag) for child in children]
    if child_names not in (
        ["bodyPr", "lstStyle", "p"],
        ["bodyPr", "lstStyle", "p", "p"],
    ):
        return None
    if children[0].attrib or list(children[0]) or children[1].attrib or list(children[1]):
        return None
    entries = [_canonical_title_paragraph(paragraph) for paragraph in children[2:]]
    if any(entry is None for entry in entries):
        return None
    return [entry for entry in entries if entry is not None]


def _data_label_text_style(tx_pr: ET.Element) -> dict[str, Any]:
    """Extract the subset of label text properties emitted by this exporter."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .chart_to_svg import (
        C_NS,
        _UnsupportedChart,
    )
    body_pr = tx_pr.find("a:bodyPr", C_NS)
    list_style = tx_pr.find("a:lstStyle", C_NS)
    if body_pr is None or body_pr.attrib or list(body_pr):
        raise _UnsupportedChart("unsupported-chart-data-labels")
    if list_style is None or list_style.attrib or list(list_style):
        raise _UnsupportedChart("unsupported-chart-data-labels")
    paragraphs = tx_pr.findall("a:p", C_NS)
    if len(paragraphs) != 1:
        raise _UnsupportedChart("unsupported-chart-data-labels")
    paragraph = paragraphs[0]
    if any(
        child.tag.rsplit("}", 1)[-1] not in {"pPr", "endParaRPr"}
        for child in paragraph
    ):
        raise _UnsupportedChart("unsupported-chart-data-labels")
    p_pr = paragraph.find("a:pPr", C_NS)
    if p_pr is None or p_pr.attrib:
        raise _UnsupportedChart("unsupported-chart-data-labels")
    if any(
        child.tag.rsplit("}", 1)[-1] != "defRPr"
        for child in p_pr
    ):
        raise _UnsupportedChart("unsupported-chart-data-labels")
    end_r_pr = paragraph.find("a:endParaRPr", C_NS)
    if end_r_pr is not None and (
        any(name not in {"lang", "altLang"} for name in end_r_pr.attrib)
        or list(end_r_pr)
    ):
        raise _UnsupportedChart("unsupported-chart-data-labels")

    r_pr = p_pr.find("a:defRPr", C_NS)
    if r_pr is None:
        return {}
    allowed_attrs = {"sz", "b"}
    if any(name not in allowed_attrs for name in r_pr.attrib):
        raise _UnsupportedChart("unsupported-chart-data-labels")
    allowed_children = {"solidFill", "latin", "ea"}
    if any(
        child.tag.rsplit("}", 1)[-1] not in allowed_children
        for child in r_pr
    ):
        raise _UnsupportedChart("unsupported-chart-data-labels")

    style: dict[str, Any] = {}
    raw_size = r_pr.attrib.get("sz")
    if raw_size is not None:
        try:
            size_px = float(raw_size) / 75.0
        except ValueError:
            raise _UnsupportedChart("unsupported-chart-data-labels") from None
        if size_px <= 0 or not math.isfinite(size_px):
            raise _UnsupportedChart("unsupported-chart-data-labels")
        style["font_size"] = int(size_px) if size_px.is_integer() else round(size_px, 3)
    if r_pr.attrib.get("b") is not None:
        style["bold"] = ooxml_bool(r_pr.attrib.get("b"), True)

    solid_fill = r_pr.find("a:solidFill", C_NS)
    if solid_fill is not None:
        color_children = list(solid_fill)
        if (
            len(color_children) != 1
            or color_children[0].tag.rsplit("}", 1)[-1] != "srgbClr"
            or list(color_children[0])
        ):
            raise _UnsupportedChart("unsupported-chart-data-labels")
        color = color_children[0].attrib.get("val", "").strip()
        if len(color) != 6 or any(char not in "0123456789abcdefABCDEF" for char in color):
            raise _UnsupportedChart("unsupported-chart-data-labels")
        style["color"] = f"#{color.upper()}"

    latin = r_pr.find("a:latin", C_NS)
    east_asian = r_pr.find("a:ea", C_NS)
    latin_face = latin.attrib.get("typeface", "").strip() if latin is not None else ""
    east_asian_face = (
        east_asian.attrib.get("typeface", "").strip()
        if east_asian is not None else ""
    )
    font_face = (
        f"{latin_face}, {east_asian_face}"
        if latin_face and east_asian_face and latin_face != east_asian_face
        else latin_face or east_asian_face
    )
    if font_face:
        style["font_family"] = font_face
    return style


def _data_label_position_payload(owner: ET.Element) -> str | None:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .chart_to_svg import (
        C_NS,
        _DATA_LABEL_POSITION_ALIASES,
        _UnsupportedChart,
        _element_val,
    )
    position = _element_val(owner.find("c:dLblPos", C_NS))
    if not position:
        return None
    normalized = _DATA_LABEL_POSITION_ALIASES.get(position)
    if normalized is None:
        raise _UnsupportedChart("unsupported-chart-data-labels")
    return normalized


def _data_label_flag_payload(
    owner: ET.Element,
    *,
    include_missing: bool,
) -> dict[str, bool]:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .chart_to_svg import (
        C_NS,
    )
    config: dict[str, bool] = {}
    for tag, field in (
        ("showVal", "show_value"),
        ("showCatName", "show_category"),
        ("showSerName", "show_series"),
        ("showPercent", "show_percent"),
    ):
        elem = owner.find(f"c:{tag}", C_NS)
        if elem is not None:
            config[field] = ooxml_bool(elem.attrib.get("val"), True)
        elif include_missing:
            config[field] = False
    return config


