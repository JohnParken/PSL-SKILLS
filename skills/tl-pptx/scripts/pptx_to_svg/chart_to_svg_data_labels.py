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

def _source_chart_text_style(
    owner: ET.Element | None,
    palette: ColorPalette | None,
) -> dict[str, Any]:
    """Read uniform chart text properties without requiring canonical OOXML."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .chart_to_svg import (
        C_NS,
    )
    if owner is None:
        return {}
    properties = [owner.find(path, C_NS) for path in (
        "a:lstStyle/a:defPPr/a:defRPr", "a:lstStyle/a:lvl1pPr/a:defRPr",
        "a:p/a:pPr/a:defRPr", "a:p/a:r/a:rPr",
    )]
    if all(node is None for node in properties):
        return {}
    r_pr = ET.Element(f"{{{NS['a']}}}rPr")
    for node in properties:
        if node is None:
            continue
        r_pr.attrib.update(node.attrib)
        for child in node:
            previous = r_pr.find(child.tag)
            if previous is not None:
                r_pr.remove(previous)
            r_pr.append(child)

    style: dict[str, Any] = {}
    raw_size = r_pr.attrib.get("sz")
    if raw_size is not None:
        try:
            size_px = float(raw_size) / 75.0
        except ValueError:
            size_px = 0.0
        if size_px > 0 and math.isfinite(size_px):
            style["font_size"] = (
                int(size_px) if size_px.is_integer() else round(size_px, 3)
            )
    if r_pr.attrib.get("b") is not None:
        style["bold"] = ooxml_bool(r_pr.attrib.get("b"), True)

    solid_fill = r_pr.find("a:solidFill", C_NS)
    color_elem = find_color_elem(solid_fill)
    if color_elem is not None:
        color, alpha = resolve_color(color_elem, palette)
        if alpha > 0:
            style["color"] = color

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


def _point_data_label_payload(
    dlabel: ET.Element,
    *,
    palette: ColorPalette | None,
    point_count: int,
) -> dict[str, Any]:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .chart_to_svg import (
        C_NS,
        _UnsupportedChart,
        _data_label_flag_payload,
        _data_label_position_payload,
        _element_val,
        _local_name,
        _source_chart_text_style,
    )
    allowed_children = {
        "dLblPos", "delete", "extLst", "idx", "numFmt", "showBubbleSize",
        "showCatName", "showLegendKey", "showPercent", "showSerName",
        "showVal", "spPr", "tx", "txPr",
    }
    if any(_local_name(child.tag) not in allowed_children for child in dlabel):
        raise _UnsupportedChart("unsupported-chart-point-labels")
    idx = _element_val(dlabel.find("c:idx", C_NS))
    if idx is None or not idx.isdigit():
        raise _UnsupportedChart("unsupported-chart-point-labels")
    point_index = int(idx)
    if point_index < 0 or point_index >= point_count:
        raise _UnsupportedChart("unsupported-chart-point-labels")

    for tag in ("showLegendKey", "showBubbleSize"):
        elem = dlabel.find(f"c:{tag}", C_NS)
        if elem is not None and ooxml_bool(elem.attrib.get("val"), True):
            raise _UnsupportedChart("unsupported-chart-point-labels")

    item: dict[str, Any] = {"idx": point_index}
    delete = dlabel.find("c:delete", C_NS)
    if delete is not None:
        item["delete"] = ooxml_bool(delete.attrib.get("val"), True)
    item.update(_data_label_flag_payload(dlabel, include_missing=False))
    position = _data_label_position_payload(dlabel)
    if position:
        item["position"] = position
    num_fmt = dlabel.find("c:numFmt", C_NS)
    if num_fmt is not None and num_fmt.attrib.get("formatCode"):
        item["number_format"] = num_fmt.attrib["formatCode"]
    tx_pr = dlabel.find("c:txPr", C_NS)
    if tx_pr is not None:
        item.update(_source_chart_text_style(tx_pr, palette))

    tx = dlabel.find("c:tx", C_NS)
    if tx is not None:
        rich = tx.find("c:rich", C_NS)
        if rich is None or len(list(tx)) != 1:
            raise _UnsupportedChart("unsupported-chart-point-labels")
        paragraphs = rich.findall("a:p", C_NS)
        if not paragraphs:
            raise _UnsupportedChart("unsupported-chart-point-labels")
        lines = ["".join(node.text or "" for node in p.findall(".//a:t", C_NS)) for p in paragraphs]
        text = "\n".join(lines)
        if not text:
            raise _UnsupportedChart("unsupported-chart-point-labels")
        item["text"] = text
        item.update(_source_chart_text_style(rich, palette))
    return item


def _source_data_labels_ooxml(dlabels: ET.Element) -> dict[str, str]:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .chart_to_svg import (
        _UnsupportedChart,
    )
    if any(
        isinstance(name, str) and name.startswith(f"{{{NS['r']}}}")
        for node in dlabels.iter()
        for name in node.attrib
    ):
        raise _UnsupportedChart("unsupported-chart-series-data-labels")
    payload = ET.tostring(dlabels, encoding="utf-8")
    return {
        "encoding": "base64",
        "payload": base64.b64encode(payload).decode("ascii"),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _data_labels_payload(
    dlabels: ET.Element | None,
    *,
    palette: ColorPalette | None = None,
    point_count: int | None = None,
    preserve_source: bool = False,
) -> dict[str, Any] | None:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .chart_to_svg import (
        C_NS,
        _UnsupportedChart,
        _data_label_flag_payload,
        _data_label_position_payload,
        _data_label_text_style,
        _point_data_label_payload,
        _source_chart_text_style,
        _source_data_labels_ooxml,
    )
    if dlabels is None:
        return None
    point_nodes = dlabels.findall("c:dLbl", C_NS)
    if point_nodes and point_count is None:
        raise _UnsupportedChart("unsupported-chart-point-labels")
    allowed_children = {
        "dLbl", "extLst", "numFmt", "spPr", "txPr", "dLblPos", "showLegendKey", "showVal",
        "showCatName", "showSerName", "showPercent", "showBubbleSize",
        "showLeaderLines",
    }
    if any(
        child.tag.rsplit("}", 1)[-1] not in allowed_children
        for child in dlabels
    ):
        raise _UnsupportedChart("unsupported-chart-data-labels")
    for tag in ("showLegendKey", "showBubbleSize"):
        elem = dlabels.find(f"c:{tag}", C_NS)
        if elem is not None and ooxml_bool(elem.attrib.get("val"), True):
            raise _UnsupportedChart("unsupported-chart-data-labels")

    config: dict[str, Any] = _data_label_flag_payload(
        dlabels,
        include_missing=True,
    )

    leader_lines = dlabels.find("c:showLeaderLines", C_NS)
    if leader_lines is not None:
        config["show_leader_lines"] = ooxml_bool(
            leader_lines.attrib.get("val"),
            True,
        )

    position = _data_label_position_payload(dlabels)
    if position:
        config["position"] = position
    num_fmt = dlabels.find("c:numFmt", C_NS)
    if num_fmt is not None and num_fmt.attrib.get("formatCode"):
        config["number_format"] = num_fmt.attrib["formatCode"]
    tx_pr = dlabels.find("c:txPr", C_NS)
    if tx_pr is not None:
        try:
            config.update(_data_label_text_style(tx_pr))
        except _UnsupportedChart:
            config.update(_source_chart_text_style(tx_pr, palette))
    if point_nodes:
        assert point_count is not None
        points = [
            _point_data_label_payload(
                point,
                palette=palette,
                point_count=point_count,
            )
            for point in point_nodes
        ]
        if len({point["idx"] for point in points}) != len(points):
            raise _UnsupportedChart("unsupported-chart-point-labels")
        config["points"] = points
        if preserve_source:
            config["source_ooxml"] = _source_data_labels_ooxml(dlabels)
    if not any(
        config.get(field)
        for field in (
            "points", "show_category", "show_percent", "show_series", "show_value",
        )
    ):
        return None
    return config


