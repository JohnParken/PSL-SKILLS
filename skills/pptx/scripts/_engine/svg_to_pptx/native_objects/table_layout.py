# Adapted from PPT Master 5e8856b (MIT); see assets/licenses/ppt_master.txt.
"""Table layout implementation."""
from __future__ import annotations

from .table_shared import (
    Any,
    ThemeColorSpec,
    _TABLE_BORDER_SIDES,
    _TABLE_BORDER_TAGS,
    _TEXT_LINE_FACTOR,
    _TableBorderSpec,
    _TableMergeRegion,
    _bool_attr,
    _clean_hex,
    _first_present,
    _number,
    _powerpoint_emu,
    _powerpoint_line_width_emu,
    color_node_xml,
    px_to_emu,
    re,
)

def _weighted_lengths(
    total: int,
    count: int,
    weights: list[Any] | None,
    *,
    field_name: str,
) -> list[int]:
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
    from .table_payload import _table_bool
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
