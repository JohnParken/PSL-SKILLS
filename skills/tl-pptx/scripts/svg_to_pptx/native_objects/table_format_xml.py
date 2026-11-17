"""Native PowerPoint table conversion."""

from __future__ import annotations

from .table import _TableBorderSpec, _TableMergeRegion

from typing import Any



from ..drawingml.theme_colors import ThemeColorSpec, color_node_xml
from .marker_common import (
    _clean_hex,
    _number,
    _powerpoint_line_width_emu,
)



# Adapted from table.py; upstream MIT, Hugo He, 2025-2026.
def _strict_table_border_color(value: Any, side: str) -> str:
    raw = value if isinstance(value, str) else ""
    if len(raw) != 7 or not raw.startswith("#"):
        raise RuntimeError(
            f"Native PPTX table {side} border color must be #RRGGBB"
        )
    try:
        int(raw[1:], 16)
    except ValueError as exc:
        raise RuntimeError(
            f"Native PPTX table {side} border color must be #RRGGBB"
        ) from exc
    return raw[1:].upper()


def _table_border_override(value: Any, side: str) -> _TableBorderSpec:
    # Resolve shared symbols at call time; retain the public module contract.
    from .table import (
        _TableBorderSpec,
    )
    if not isinstance(value, dict):
        raise RuntimeError(
            f"Native PPTX table {side} border must be an object"
        )
    border_style = value.get("style")
    if border_style == "none":
        if set(value) != {"style"}:
            raise RuntimeError(
                f"Native PPTX table {side} border style none accepts no other fields"
            )
        return _TableBorderSpec("none")
    if border_style != "solid":
        raise RuntimeError(
            f"Native PPTX table {side} border style must be solid or none"
        )
    if set(value) != {"style", "color", "width"}:
        raise RuntimeError(
            f"Native PPTX table {side} solid border requires style/color/width only"
        )
    width = _number(value.get("width"), f"table {side} border width")
    if width <= 0:
        raise RuntimeError(
            f"Native PPTX table {side} solid border width must be positive"
        )
    _powerpoint_line_width_emu(width, f"table {side} border width")
    return _TableBorderSpec(
        "solid",
        color=_strict_table_border_color(value.get("color"), side),
        width=width,
    )


def _table_border_specs(
    cell_data: dict[str, Any],
    style: dict[str, Any],
) -> dict[str, _TableBorderSpec | None]:
    # Resolve shared symbols at call time; retain the public module contract.
    from .table import (
        _TABLE_BORDER_SIDES,
        _TableBorderSpec,
        _table_border_width,
    )
    raw_borders = cell_data.get("borders")
    if raw_borders is None:
        border_overrides: dict[str, Any] = {}
    elif not isinstance(raw_borders, dict):
        raise RuntimeError("Native PPTX table cell borders must be an object")
    else:
        unknown = sorted(set(raw_borders) - set(_TABLE_BORDER_SIDES))
        if unknown:
            raise RuntimeError(
                "Native PPTX table cell borders use unsupported side(s): "
                + ", ".join(unknown)
            )
        border_overrides = raw_borders

    legacy_width = _table_border_width(cell_data, style)
    legacy_spec = (
        _TableBorderSpec(
            "solid",
            color=_clean_hex(
                cell_data.get("border_color", style.get("border_color")),
                "#D9DEE7",
            ),
            width=legacy_width,
        )
        if legacy_width > 0
        else None
    )
    return {
        side: (
            _table_border_override(border_overrides[side], side)
            if side in border_overrides
            else legacy_spec if side in {"left", "right", "top", "bottom"}
            else None
        )
        for side in _TABLE_BORDER_SIDES
    }


def _table_border_xml(
    cell_data: dict[str, Any],
    style: dict[str, Any],
    theme_color_spec: ThemeColorSpec | None,
) -> str:
    # Resolve shared symbols at call time; retain the public module contract.
    from .table import (
        _TABLE_BORDER_TAGS,
    )
    border_xml: list[str] = []
    for side, border in _table_border_specs(cell_data, style).items():
        if border is None:
            continue
        tag = _TABLE_BORDER_TAGS[side]
        if border.style == "none":
            border_xml.append(f'<a:{tag}><a:noFill/></a:{tag}>')
            continue
        assert border.color is not None and border.width is not None
        line_width = _powerpoint_line_width_emu(
            border.width, f"table {side} border width",
        )
        border_xml.append(
            f'<a:{tag} w="{line_width}">'
            f'<a:solidFill>{color_node_xml(border.color, theme_color_spec, "stroke")}'
            '</a:solidFill>'
            '<a:prstDash val="solid"/>'
            f'</a:{tag}>'
        )
    return "".join(border_xml)


def _table_fill_xml(
    fill: str | None,
    opacity: float | None,
    theme_color_spec: ThemeColorSpec | None,
) -> str:
    if fill is None:
        return ""
    alpha_xml = (
        f'<a:alpha val="{round(opacity * 100000)}"/>'
        if opacity is not None and opacity < 1
        else ""
    )
    return (
        '<a:solidFill>'
        f'{color_node_xml(fill, theme_color_spec, "fill", alpha_xml)}'
        '</a:solidFill>'
    )


def _table_merge_attrs(
    region: _TableMergeRegion | None,
    row_idx: int,
    col_idx: int,
) -> str:
    if region is None:
        return ""
    attrs: list[str] = []
    if row_idx == region.row and region.row_span > 1:
        attrs.append(f'rowSpan="{region.row_span}"')
    if col_idx == region.col and region.col_span > 1:
        attrs.append(f'gridSpan="{region.col_span}"')
    if col_idx > region.col:
        attrs.append('hMerge="1"')
    if row_idx > region.row:
        attrs.append('vMerge="1"')
    return (" " + " ".join(attrs)) if attrs else ""
