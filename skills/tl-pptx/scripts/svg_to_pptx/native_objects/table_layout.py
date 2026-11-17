"""Native PowerPoint table conversion."""

from __future__ import annotations

import re
from typing import Any



from ..drawingml.utils import (
    px_to_emu,
)
from .marker_common import (
    _bool_attr,
    _first_present,
    _number,
    _powerpoint_emu,
)



# Adapted from table.py; upstream MIT, Hugo He, 2025-2026.
def _weighted_lengths(
    total: int,
    count: int,
    weights: list[Any] | None,
    *,
    field_name: str,
) -> list[int]:
    # Resolve shared symbols at call time; retain the public module contract.
    if total < count:
        raise RuntimeError(
            f"Native PPTX table {field_name} cannot fit {count} positive grid lengths"
        )
    if weights is None:
        base, remainder = divmod(total, count)
        return [base + (1 if idx < remainder else 0) for idx in range(count)]

    numeric = _table_weights(weights, field_name)
    largest = max(numeric)
    normalized = [weight / largest for weight in numeric]
    normalized_total = sum(normalized)
    distributable = total - count
    quotas = [distributable * weight / normalized_total for weight in normalized]
    extras = [int(quota) for quota in quotas]
    remainder = distributable - sum(extras)
    if remainder < 0 or remainder > count:
        raise RuntimeError(f"Native PPTX table {field_name} allocation overflowed")
    order = sorted(
        range(count),
        key=lambda idx: (quotas[idx] - extras[idx], normalized[idx], -idx),
        reverse=True,
    )
    for idx in order[:remainder]:
        extras[idx] += 1
    return [extra + 1 for extra in extras]


def _table_weights(weights: list[Any], field_name: str) -> list[float]:
    numeric = [
        _number(weight, f"{field_name}[{idx}]")
        for idx, weight in enumerate(weights, start=1)
    ]
    if any(weight < 0 for weight in numeric):
        raise RuntimeError(f"Native PPTX table {field_name} values must be non-negative")
    if max(numeric, default=0.0) <= 0:
        raise RuntimeError(
            f"Native PPTX table {field_name} values must sum to a positive number"
        )
    return numeric


def _table_padding_value(
    cell_data: dict[str, Any],
    style: dict[str, Any],
    side: str,
) -> int | None:
    direct_key = f"padding_{side}"

    def from_source(source: dict[str, Any]) -> Any:
        if direct_key in source:
            return source[direct_key]
        padding = source.get("padding")
        if isinstance(padding, dict):
            if side in padding:
                return padding[side]
        elif padding is not None:
            return padding
        return None

    value = from_source(cell_data)
    if value is None:
        value = from_source(style)
    if value is None:
        return None
    pixels = max(_number(value, f"table {side} padding"), 0.0)
    return _powerpoint_emu(pixels, f"table {side} padding")


def _table_text_height_emu(paragraphs_xml: str) -> int:
    """Estimate the vertical space the cell's paragraphs need in PowerPoint."""
    # Resolve shared symbols at call time; retain the public module contract.
    from .table import (
        _TEXT_LINE_FACTOR,
    )
    sizes = [int(value) for value in re.findall(r' sz="(\d+)"', paragraphs_xml)]
    paragraph_count = max(1, paragraphs_xml.count("<a:p>"))
    if not sizes:
        return 0
    line_px = max(sizes) / 100 / 0.75 * _TEXT_LINE_FACTOR
    return px_to_emu(line_px * paragraph_count)


def _table_padding_attrs(
    cell_data: dict[str, Any],
    style: dict[str, Any],
    *,
    row_height: int | None = None,
    paragraphs_xml: str | None = None,
) -> str:
    # Resolve shared symbols at call time; retain the public module contract.
    values = {
        side: _table_padding_value(cell_data, style, side)
        for side in ("left", "right", "top", "bottom")
    }
    # An authored row height is geometry; vertical padding is style. When the
    # text line plus both margins would exceed the row, PowerPoint grows the
    # row instead, so shrink the margins to what the row can hold.
    if row_height is not None and paragraphs_xml is not None:
        top = values["top"] or 0
        bottom = values["bottom"] or 0
        room = row_height - _table_text_height_emu(paragraphs_xml)
        if top + bottom > room > 0:
            scale = room / (top + bottom)
            values["top"] = int(top * scale) if values["top"] is not None else None
            values["bottom"] = int(bottom * scale) if values["bottom"] is not None else None
        elif top + bottom > room:
            values["top"] = 0 if values["top"] is not None else None
            values["bottom"] = 0 if values["bottom"] is not None else None
    attrs = []
    for attr, side in (
        ("marL", "left"),
        ("marR", "right"),
        ("marT", "top"),
        ("marB", "bottom"),
    ):
        value = values[side]
        if value is not None:
            attrs.append(f'{attr}="{value}"')
    return (" " + " ".join(attrs)) if attrs else ""


def _table_anchor(cell_data: dict[str, Any], style: dict[str, Any]) -> str:
    raw = _first_present(
        cell_data.get("valign"),
        style.get("valign"),
        "middle",
    )
    values = {
        "bottom": "b",
        "middle": "ctr",
        "top": "t",
    }
    anchor = values.get(raw) if isinstance(raw, str) else None
    if not anchor:
        raise RuntimeError("Native PPTX table valign must be one of: top, middle, bottom")
    return anchor


def _table_cell_extra_attrs(cell_data: dict[str, Any]) -> str:
    # Resolve shared symbols at call time; retain the public module contract.
    from .table import (
        _table_bool,
    )
    attrs: list[str] = []
    if "anchor_center" in cell_data:
        anchor_center = _table_bool(
            cell_data["anchor_center"],
            "cell anchor_center",
            default=False,
        )
        attrs.append(f'anchorCtr="{_bool_attr(anchor_center)}"')
    horizontal_overflow = cell_data.get("horizontal_overflow")
    if horizontal_overflow is not None:
        if horizontal_overflow not in {"clip", "overflow"}:
            raise RuntimeError(
                "Native PPTX table horizontal_overflow must be clip or overflow"
            )
        attrs.append(f'horzOverflow="{horizontal_overflow}"')
    return (" " + " ".join(attrs)) if attrs else ""


def _table_fill_opacity(cell_data: dict[str, Any]) -> float | None:
    raw = cell_data.get("fill_opacity")
    if raw is None:
        return None
    if cell_data.get("fill") is None:
        raise RuntimeError(
            "Native PPTX table fill_opacity requires an explicit cell fill"
        )
    opacity = _number(raw, "table cell fill_opacity")
    if not 0 <= opacity <= 1:
        raise RuntimeError(
            "Native PPTX table fill_opacity must be between zero and one"
        )
    return opacity


def _table_border_width(cell_data: dict[str, Any], style: dict[str, Any]) -> float:
    width_raw = cell_data.get("border_width", style.get("border_width"))
    color_raw = cell_data.get("border_color", style.get("border_color"))
    if width_raw is None and color_raw is None:
        return 0.0
    return _number(1 if width_raw is None else width_raw, "table border_width")
