# Adapted from PPT Master 5e8856b (MIT); see assets/licenses/ppt_master.txt.
"""Chart data axes implementation."""
from __future__ import annotations

from .chart_data_shared import (
    Any,
    _AXIS_ROLE_DEFAULTS,
    _compact_key,
    _hex_or_none,
    _number,
    _powerpoint_emu,
)

def _chart_axes(
    payload: dict[str, Any],
    allowed_roles: set[str],
    *,
    bar_orientation: bool = False,
) -> dict[str, dict[str, Any]]:
    """Normalize the narrow classic-chart axis contract."""
    from .chart_data_labels import _chart_number
    raw_axes = payload.get("axes")
    if raw_axes is None:
        return {}
    if not isinstance(raw_axes, dict):
        raise RuntimeError("Native PPTX chart axes must be an object")

    unknown_roles = set(raw_axes) - allowed_roles
    if unknown_roles:
        roles = ", ".join(sorted(unknown_roles))
        raise RuntimeError(f"Native PPTX chart axes contains unsupported role(s): {roles}")

    axes: dict[str, dict[str, Any]] = {}
    for role, raw_config in raw_axes.items():
        if not isinstance(raw_config, dict):
            raise RuntimeError(f"Native PPTX chart axes.{role} must be an object")
        allowed_fields = {
            "color", "cross_between", "font_family", "font_size", "kind",
            "label_position", "major_gridlines", "major_unit", "maximum",
            "minimum", "number_format", "position", "reverse", "tick_marks",
            "visible",
        }
        unknown_fields = set(raw_config) - allowed_fields
        if unknown_fields:
            fields = ", ".join(sorted(unknown_fields))
            raise RuntimeError(
                f"Native PPTX chart axes.{role} contains unsupported field(s): {fields}"
            )
        default_kind, default_position = _AXIS_ROLE_DEFAULTS[role]
        if bar_orientation and role == "category":
            default_position = "left"
        elif bar_orientation and role == "value":
            default_position = "bottom"
        kind = _compact_key(raw_config.get("kind") or default_kind)
        if kind not in {"date", "text", "value"}:
            raise RuntimeError(
                f"Native PPTX chart axes.{role}.kind must be date, text, or value"
            )
        if role in {"category", "secondary_category"} and kind not in {"date", "text"}:
            raise RuntimeError(f"Native PPTX chart axes.{role}.kind must be date or text")
        if role in {"value", "secondary_value", "x", "y"} and kind != "value":
            raise RuntimeError(f"Native PPTX chart axes.{role}.kind must be value")

        position_aliases = {
            "b": "bottom",
            "bottom": "bottom",
            "l": "left",
            "left": "left",
            "r": "right",
            "right": "right",
            "t": "top",
            "top": "top",
        }
        position = position_aliases.get(
            _compact_key(raw_config.get("position") or default_position)
        )
        if position is None:
            raise RuntimeError(
                f"Native PPTX chart axes.{role}.position must be bottom, left, right, or top"
            )
        if bar_orientation and role == "category":
            allowed_positions = {"left", "right"}
        elif bar_orientation and role == "value":
            allowed_positions = {"bottom", "top"}
        else:
            allowed_positions = (
                {"bottom", "top"}
                if role in {"category", "secondary_category", "x"}
                else {"left", "right"}
            )
        if position not in allowed_positions:
            choices = ", ".join(sorted(allowed_positions))
            raise RuntimeError(
                f"Native PPTX chart axes.{role}.position must be one of: {choices}"
            )

        config: dict[str, Any] = {"kind": kind, "position": position}
        for field in ("visible", "reverse", "major_gridlines"):
            value = raw_config.get(field)
            if value is None:
                continue
            if not isinstance(value, bool):
                raise RuntimeError(
                    f"Native PPTX chart axes.{role}.{field} must be a boolean"
                )
            config[field] = value

        raw_label_position = raw_config.get("label_position")
        if raw_label_position is not None:
            label_aliases = {
                "high": "high",
                "low": "low",
                "nextto": "next_to",
                "none": "none",
            }
            label_position = label_aliases.get(_compact_key(raw_label_position))
            if label_position is None:
                raise RuntimeError(
                    f"Native PPTX chart axes.{role}.label_position must be one of: "
                    "high, low, next_to, none"
                )
            config["label_position"] = label_position

        raw_color = raw_config.get("color")
        if raw_color is not None:
            color = _hex_or_none(raw_color)
            if color is None:
                raise RuntimeError(f"Native PPTX chart axes.{role}.color must be a colour")
            config["color"] = color
        raw_font_family = raw_config.get("font_family")
        if raw_font_family is not None:
            if not isinstance(raw_font_family, str) or not raw_font_family.strip():
                raise RuntimeError(
                    f"Native PPTX chart axes.{role}.font_family must be a non-empty string"
                )
            config["font_family"] = raw_font_family.strip()
        raw_font_size = raw_config.get("font_size")
        if raw_font_size is not None:
            font_size = _number(raw_font_size, f"chart axes.{role}.font_size")
            if font_size <= 0:
                raise RuntimeError(f"Native PPTX chart axes.{role}.font_size must be positive")
            config["font_size"] = font_size

        raw_tick_marks = raw_config.get("tick_marks")
        if raw_tick_marks is not None:
            tick_marks = _compact_key(raw_tick_marks)
            if tick_marks not in {"cross", "in", "none", "out"}:
                raise RuntimeError(
                    f"Native PPTX chart axes.{role}.tick_marks must be one of: "
                    "cross, in, none, out"
                )
            config["tick_marks"] = tick_marks

        raw_cross_between = raw_config.get("cross_between")
        if raw_cross_between is not None:
            if role not in {"value", "secondary_value"}:
                raise RuntimeError(
                    f"Native PPTX chart axes.{role}.cross_between is unsupported"
                )
            cross_between = {
                "between": "between",
                "midcat": "midCat",
                "midcategory": "midCat",
                "oncategories": "midCat",
                "onticks": "midCat",
            }.get(_compact_key(raw_cross_between))
            if cross_between is None:
                raise RuntimeError(
                    f"Native PPTX chart axes.{role}.cross_between must be between or mid_category"
                )
            config["cross_between"] = cross_between

        raw_number_format = raw_config.get("number_format")
        if raw_number_format is not None:
            if not isinstance(raw_number_format, str):
                raise RuntimeError(
                    f"Native PPTX chart axes.{role}.number_format must be a string"
                )
            if not raw_number_format.strip():
                raise RuntimeError(
                    f"Native PPTX chart axes.{role}.number_format must be non-empty"
                )
            config["number_format"] = raw_number_format

        for field in ("minimum", "maximum", "major_unit"):
            value = raw_config.get(field)
            if value is None:
                continue
            number = _chart_number(value)
            if field == "major_unit":
                if role not in {"value", "secondary_value", "x", "y"}:
                    raise RuntimeError(
                        f"Native PPTX chart axes.{role}.major_unit is unsupported"
                    )
                if number <= 0:
                    raise RuntimeError(
                        f"Native PPTX chart axes.{role}.major_unit must be positive"
                    )
            config[field] = number
        if (
            config.get("minimum") is not None
            and config.get("maximum") is not None
            and config["minimum"] >= config["maximum"]
        ):
            raise RuntimeError(
                f"Native PPTX chart axes.{role}.minimum must be less than maximum"
            )
        axes[role] = config
    return axes


def _category_axis_reversed(config: dict[str, Any], chart_type: str) -> bool:
    """Bar categories read top-down in payload order unless ``reverse`` says otherwise."""
    reverse = config.get("reverse")
    if reverse is None:
        return chart_type == "bar"
    return bool(reverse)


def _category_axis_is_date(axes: dict[str, dict[str, Any]]) -> bool:
    return axes.get("category", {}).get("kind") == "date"


def _chart_plot_area(payload: dict[str, Any]) -> dict[str, float] | None:
    """Normalize an optional absolute slide-local plot-area box."""
    raw = payload.get("plot_area")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise RuntimeError("Native PPTX chart plot_area must be an object")

    box_keys = {"x", "y", "width", "height"}
    unknown_keys = set(raw) - box_keys
    if unknown_keys:
        fields = ", ".join(sorted(unknown_keys))
        raise RuntimeError(
            f"Native PPTX chart plot_area contains unsupported field(s): {fields}"
        )
    missing_keys = box_keys - set(raw)
    if missing_keys:
        fields = ", ".join(sorted(missing_keys))
        raise RuntimeError(
            "Native PPTX chart plot_area requires x/y/width/height together; "
            f"missing: {fields}"
        )

    plot_area = {
        key: _number(raw[key], f"chart plot_area.{key}")
        for key in ("x", "y", "width", "height")
    }
    if plot_area["width"] <= 0 or plot_area["height"] <= 0:
        raise RuntimeError("Native PPTX chart plot_area width/height must be positive")
    return plot_area


def _chart_plot_area_layout(
    chart_data: dict[str, Any],
    chart_bounds: tuple[int, int, int, int],
) -> tuple[float, float, float, float] | None:
    """Resolve an absolute plot-area box to chart-relative manual-layout factors."""
    plot_area = chart_data.get("plot_area")
    if plot_area is None:
        return None

    chart_x, chart_y, chart_width, chart_height = chart_bounds
    plot_x = _powerpoint_emu(plot_area["x"], "chart plot_area.x")
    plot_y = _powerpoint_emu(plot_area["y"], "chart plot_area.y")
    plot_width = _powerpoint_emu(
        plot_area["width"],
        "chart plot_area.width",
        positive=True,
    )
    plot_height = _powerpoint_emu(
        plot_area["height"],
        "chart plot_area.height",
        positive=True,
    )
    if (
        plot_x < chart_x
        or plot_y < chart_y
        or plot_x + plot_width > chart_x + chart_width
        or plot_y + plot_height > chart_y + chart_height
    ):
        raise RuntimeError(
            "Native PPTX chart plot_area must be fully contained within the chart frame"
        )
    return (
        (plot_x - chart_x) / chart_width,
        (plot_y - chart_y) / chart_height,
        plot_width / chart_width,
        plot_height / chart_height,
    )


def _doughnut_hole_size(payload: dict[str, Any], chart_type: str) -> int | None:
    """Normalize the closed doughnut-hole percentage contract."""
    from .chart_data_labels import _chart_number
    raw = payload.get("hole_size")
    if chart_type != "doughnut":
        if raw is not None:
            raise RuntimeError(
                "Native PPTX chart hole_size is supported for doughnut charts only"
            )
        return None
    if raw is None:
        return 75
    if not isinstance(raw, (int, float)) or isinstance(raw, bool):
        raise RuntimeError("Native PPTX doughnut hole_size must be a numeric integer")
    value = _chart_number(raw)
    if not float(value).is_integer():
        raise RuntimeError("Native PPTX doughnut hole_size must be an integer")
    hole_size = int(value)
    if not 10 <= hole_size <= 90:
        raise RuntimeError(
            "Native PPTX doughnut hole_size must be between 10 and 90"
        )
    return hole_size
