# Adapted from PPT Master 5e8856b (MIT); see assets/licenses/ppt_master.txt.
"""Chart xml axes implementation."""
from __future__ import annotations

from .chart_xml_shared import (
    Any,
    _axis_title_xml,
    _bool_attr,
    _category_axis_reversed,
    _chart_line_sp_pr_xml,
    _chart_tx_pr_xml,
    _first_present,
    _font_size_hpt,
    _major_gridlines_xml,
    _xml_escape,
)

def _axis_scaling_xml(config: dict[str, Any], *, reverse: bool | None = None) -> str:
    if reverse is None:
        reverse = bool(config.get("reverse"))
    orientation = "maxMin" if reverse else "minMax"
    maximum = (
        f'<c:max val="{config["maximum"]}"/>'
        if config.get("maximum") is not None else ""
    )
    minimum = (
        f'<c:min val="{config["minimum"]}"/>'
        if config.get("minimum") is not None else ""
    )
    return f'<c:scaling><c:orientation val="{orientation}"/>{maximum}{minimum}</c:scaling>'


def _axis_tick_marks(config: dict[str, Any]) -> str:
    """Axis tick marks default to none: generated fallbacks draw labels, not ticks."""
    return str(config.get("tick_marks") or "none")


def _axis_position(config: dict[str, Any], default: str) -> str:
    return {
        "bottom": "b",
        "left": "l",
        "right": "r",
        "top": "t",
    }.get(str(config.get("position") or ""), default)


def _axis_label_position(config: dict[str, Any], default: str) -> str:
    return {
        "high": "high",
        "low": "low",
        "next_to": "nextTo",
        "none": "none",
    }.get(str(config.get("label_position") or ""), default)


def _axis_number_format_xml(
    config: dict[str, Any],
    default: str | None = None,
) -> str:
    number_format = config.get("number_format", default)
    if number_format is None:
        return ""
    return f'<c:numFmt formatCode="{_xml_escape(str(number_format))}" sourceLinked="0"/>'


def _axis_major_gridlines_xml(
    config: dict[str, Any],
    *,
    default: bool,
    color: str | None,
) -> str:
    enabled = config.get("major_gridlines", default)
    return _major_gridlines_xml(color) if enabled else ""


def _axis_tx_pr_xml(
    config: dict[str, Any],
    font_size: int,
    chart_style: dict[str, str | None],
) -> str:
    """Apply per-axis tick-label overrides to the shared chart text defaults."""
    return _chart_tx_pr_xml(
        _font_size_hpt(config["font_size"]) if config.get("font_size") else font_size,
        config.get("color") or chart_style.get("text_color"),
        font_face=config.get("font_family") or chart_style.get("font_face"),
        language=chart_style.get("primary_language"),
    )


def _axis_pair_xml(
    cat_ax_id: str,
    val_ax_id: str,
    *,
    axis_font_size: int,
    axis_title_font_size: int,
    axis_titles: dict[str, Any],
    chart_style: dict[str, str | None],
    chart_type: str,
    grouping: str | None,
    show_value_axis_labels: bool,
    axes: dict[str, dict[str, Any]],
    secondary: bool,
) -> str:
    primary_language = chart_style.get("primary_language")
    category_role = "secondary_category" if secondary else "category"
    value_role = "secondary_value" if secondary else "value"
    category = axes.get(category_role, {})
    value = axes.get(value_role, {})
    category_kind = str(category.get("kind") or ("date" if chart_type == "stock" else "text"))
    category_tag = "dateAx" if category_kind == "date" else "catAx"
    default_cat_pos = "l" if chart_type == "bar" else "b"
    default_val_pos = "r" if secondary else ("b" if chart_type == "bar" else "l")
    cat_pos = _axis_position(category, default_cat_pos)
    val_pos = _axis_position(value, default_val_pos)
    cat_delete = _bool_attr(not category.get("visible", not secondary))
    val_delete = _bool_attr(not value.get("visible", True))
    cat_tick_label_pos = _axis_label_position(category, "nextTo")
    default_val_tick = "nextTo" if show_value_axis_labels else "none"
    val_tick_label_pos = _axis_label_position(value, default_val_tick)
    default_value_format = "0%" if grouping == "percentStacked" else None
    cat_number_format = _axis_number_format_xml(
        category,
        "m/d/yyyy" if category_kind == "date" else None,
    )
    val_number_format = _axis_number_format_xml(value, default_value_format)
    axis_sp_pr = _chart_line_sp_pr_xml(chart_style.get("axis_color"))

    cat_title_xml = "" if secondary else _axis_title_xml(
        _first_present(axis_titles.get("category"), axis_titles.get("x")),
        font_size=axis_title_font_size,
        color=chart_style.get("text_color"),
        font_face=chart_style.get("font_face"),
        primary_language=primary_language,
    )
    value_title_key = "secondary_value" if secondary else "value"
    value_title = axis_titles.get(value_title_key)
    if not secondary:
        value_title = _first_present(value_title, axis_titles.get("y"))
    val_title_xml = _axis_title_xml(
        value_title,
        font_size=axis_title_font_size,
        color=chart_style.get("text_color"),
        font_face=chart_style.get("font_face"),
        primary_language=primary_language,
    )
    cat_gridlines = _axis_major_gridlines_xml(
        category,
        default=False,
        color=chart_style.get("grid_color"),
    )
    val_gridlines = _axis_major_gridlines_xml(
        value,
        default=not secondary,
        color=chart_style.get("grid_color"),
    )
    if category_kind == "date":
        category_tail = '<c:auto val="1"/><c:lblOffset val="100"/><c:baseTimeUnit val="days"/>'
    else:
        category_tail = (
            '<c:auto val="1"/><c:lblAlgn val="ctr"/><c:lblOffset val="100"/>'
            '<c:noMultiLvlLbl val="0"/>'
        )
    # Written explicitly: renderers disagree on the default (LibreOffice
    # starts an unspecified area on the axis edge, PowerPoint between ticks).
    default_cross_between = (
        "midCat" if chart_type == "area" and category_kind == "date" else "between"
    )
    cross_between = (
        f'<c:crossBetween val="{value.get("cross_between") or default_cross_between}"/>'
    )
    cat_tick_marks = _axis_tick_marks(category)
    val_tick_marks = _axis_tick_marks(value)
    major_unit = (
        f'<c:majorUnit val="{value["major_unit"]}"/>'
        if value.get("major_unit") is not None else ""
    )
    # ``crosses`` places an axis at the first (autoZero) or last (max) point of
    # the axis it crosses; a reversed axis swaps those ends, so the crossing
    # follows the requested position XOR the crossing axis orientation. The
    # hidden secondary pair keeps autoZero/max: its crossing sets the area
    # fill baseline, not a visible axis position.
    category_reversed = (
        False if secondary else _category_axis_reversed(category, chart_type)
    )
    value_reversed = False if secondary else bool(value.get("reverse"))
    far_val_pos = "t" if chart_type == "bar" else "r"
    far_cat_pos = "r" if chart_type == "bar" else "t"
    if secondary:
        value_crosses = "max"
        category_crosses = "autoZero"
    else:
        value_crosses = "max" if (val_pos == far_val_pos) != category_reversed else "autoZero"
        category_crosses = "max" if (cat_pos == far_cat_pos) != value_reversed else "autoZero"
    return (
        f"<c:{category_tag}>"
        f'<c:axId val="{cat_ax_id}"/>{_axis_scaling_xml(category, reverse=category_reversed)}'
        f'<c:delete val="{cat_delete}"/><c:axPos val="{cat_pos}"/>'
        f"{cat_gridlines}{cat_title_xml}{cat_number_format}"
        f'<c:majorTickMark val="{cat_tick_marks}"/><c:minorTickMark val="none"/>'
        f'<c:tickLblPos val="{cat_tick_label_pos}"/>'
        f"{axis_sp_pr}{_axis_tx_pr_xml(category, axis_font_size, chart_style)}"
        f'<c:crossAx val="{val_ax_id}"/><c:crosses val="{category_crosses}"/>{category_tail}'
        f"</c:{category_tag}>"
        "<c:valAx>"
        f'<c:axId val="{val_ax_id}"/>{_axis_scaling_xml(value, reverse=value_reversed)}'
        f'<c:delete val="{val_delete}"/><c:axPos val="{val_pos}"/>'
        f"{val_gridlines}{val_title_xml}{val_number_format}"
        f'<c:majorTickMark val="{val_tick_marks}"/><c:minorTickMark val="none"/>'
        f'<c:tickLblPos val="{val_tick_label_pos}"/>'
        f"{axis_sp_pr}{_axis_tx_pr_xml(value, axis_font_size, chart_style)}"
        f'<c:crossAx val="{cat_ax_id}"/><c:crosses val="{value_crosses}"/>'
        f"{cross_between}{major_unit}"
        "</c:valAx>"
    )


def _secondary_axis_xml(
    cat_ax_id: str,
    val_ax_id: str,
    *,
    axis_font_size: int,
    axis_title_font_size: int,
    axis_titles: dict[str, Any],
    chart_style: dict[str, str | None],
    grouping: str | None = None,
    axes: dict[str, dict[str, Any]] | None = None,
) -> str:
    return _axis_pair_xml(
        cat_ax_id,
        val_ax_id,
        axis_font_size=axis_font_size,
        axis_title_font_size=axis_title_font_size,
        axis_titles=axis_titles,
        chart_style=chart_style,
        chart_type="combo",
        grouping=grouping,
        show_value_axis_labels=True,
        axes=axes or {},
        secondary=True,
    )
