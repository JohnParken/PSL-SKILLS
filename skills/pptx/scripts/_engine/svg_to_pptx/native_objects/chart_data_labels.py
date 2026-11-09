# Adapted from PPT Master 5e8856b (MIT); see assets/licenses/ppt_master.txt.
"""Chart data labels implementation."""
from __future__ import annotations

from .chart_data_shared import (
    Any,
    _chart_bool,
    _compact_key,
    _first_present,
    _hex_or_none,
    _number,
    math,
)

def _chart_number(value: Any) -> int | float:
    if isinstance(value, bool):
        raise RuntimeError("Native PPTX chart values must be numeric")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise RuntimeError("Native PPTX chart value is not numeric") from exc
    if not math.isfinite(number):
        raise RuntimeError(f"Native PPTX chart value must be finite: {value}")
    return int(number) if number.is_integer() else number


def _chart_point_value(value: Any) -> int | float | None:
    """A series point: a finite number, or ``null`` for a gap in the series."""
    if value is None:
        return None
    return _chart_number(value)


def _chart_list(value: Any, field_name: str) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise RuntimeError(f"Native PPTX chart {field_name} must be a list")
    return value


def _data_labels_config(payload: dict[str, Any]) -> dict[str, Any] | None:
    raw = _first_present(payload.get("data_labels"), payload.get("dataLabels"))
    if raw is None:
        return None
    if isinstance(raw, bool):
        return {} if raw else None
    if not isinstance(raw, dict):
        raise RuntimeError("Native PPTX chart data_labels must be a boolean or object")
    return raw


def _data_label_position(value: Any, chart_type: str, grouping: str | None) -> str | None:
    """Normalize and validate a label position for its chart plot."""
    if chart_type == "area":
        if value is not None:
            raise RuntimeError("Native PPTX area data labels do not support label position")
        return None
    is_stacked = chart_type in {"bar", "column"} and grouping in {
        "percentStacked", "stacked",
    }
    default = "ctr" if is_stacked else (
        "outEnd" if chart_type in {"bar", "column"} else "t"
    )
    if value is None:
        return default
    aliases = {
        "above": "t",
        "bestfit": "bestFit",
        "center": "ctr",
        "inbase": "inBase",
        "insidebase": "inBase",
        "insideend": "inEnd",
        "inend": "inEnd",
        "outend": "outEnd",
        "outsideend": "outEnd",
    }
    position = aliases.get(_compact_key(value))
    if not position:
        raise RuntimeError(
            "Native PPTX chart data label position must be one of: "
            "above, best_fit, center, inside_base, inside_end, outside_end"
        )
    if chart_type in {"bar", "column"}:
        if position not in {"ctr", "inBase", "inEnd", "outEnd"}:
            raise RuntimeError(
                "Native PPTX bar/column data label position must be one of: "
                "center, inside_base, inside_end, outside_end"
            )
        if is_stacked and position == "outEnd":
            raise RuntimeError(
                "Native PPTX stacked bar/column data labels do not support outside_end"
            )
    elif chart_type == "line" and position not in {"bestFit", "ctr", "t"}:
        raise RuntimeError(
            "Native PPTX line data label position must be one of: above, best_fit, center"
        )
    return position


def _chart_data_labels(
    payload: dict[str, Any],
    chart_type: str,
    grouping: str | None,
    point_count: int,
) -> dict[str, Any] | None:
    config = _data_labels_config(payload)
    if config is None:
        return None
    if chart_type not in {"area", "bar", "column", "line"}:
        raise RuntimeError(
            f"Native PPTX {chart_type} chart data labels are outside current support"
        )
    _data_label_position(config.get("position"), chart_type, grouping)
    if config.get("color") is not None and _hex_or_none(config["color"]) is None:
        raise RuntimeError("Native PPTX chart data_labels.color must be a color")
    point_items = _data_label_point_items(
        config,
        chart_type,
        grouping,
        point_count,
    )
    for item in point_items:
        if item.get("color") is not None and _hex_or_none(item["color"]) is None:
            raise RuntimeError(
                "Native PPTX chart data_labels.points color must be a color"
            )
    if (
        point_items
        and not data_labels_show_unlisted(config)
        and all(item.get("delete") is True for item in point_items)
    ):
        raise RuntimeError(
            "Native PPTX chart data_labels.points lists the labels to show; "
            "every listed point is delete: true, so no label remains. List the "
            "points to label, or set show_value: true so unlisted points keep "
            "their labels and delete entries hide single points"
        )
    colors = _chart_list(
        _first_present(
            config.get("colors"),
            config.get("label_colors"),
            config.get("labelColors"),
        ),
        "data_labels.colors",
    )
    if colors and len(colors) != point_count:
        raise RuntimeError("Native PPTX chart data_labels.colors must match point count")
    if any(_hex_or_none(color) is None for color in colors):
        raise RuntimeError("Native PPTX chart data_labels.colors entries must be colors")
    source_ooxml = config.get("source_ooxml")
    if source_ooxml is not None:
        if not isinstance(source_ooxml, dict):
            raise RuntimeError(
                "Native PPTX chart data_labels.source_ooxml must be an object"
            )
        if source_ooxml.get("encoding") != "base64":
            raise RuntimeError(
                "Native PPTX chart data_labels.source_ooxml encoding must be base64"
            )
        payload_text = source_ooxml.get("payload")
        checksum = source_ooxml.get("sha256")
        if not isinstance(payload_text, str) or not payload_text:
            raise RuntimeError(
                "Native PPTX chart data_labels.source_ooxml payload must be non-empty"
            )
        if (
            not isinstance(checksum, str)
            or len(checksum) != 64
            or any(char not in "0123456789abcdef" for char in checksum.lower())
        ):
            raise RuntimeError(
                "Native PPTX chart data_labels.source_ooxml sha256 is invalid"
            )
    return config


def data_labels_show_unlisted(config: dict[str, Any]) -> bool:
    """Return whether points absent from ``points`` keep the series label.

    An explicit truthy ``show_*`` flag makes ``points`` a list of overrides,
    as in OOXML and the PPTX importer; without one ``points`` lists the only
    labels shown.
    """
    return any(
        _chart_bool(_first_present(*(config.get(key) for key in aliases)), False)
        for aliases in (
            ("show_value", "showValue", "value"),
            ("show_category", "showCategory", "category"),
            ("show_series", "showSeries", "series"),
            ("show_percent", "showPercent", "percent"),
        )
    )


def _data_label_point_items(
    config: dict[str, Any],
    chart_type: str,
    grouping: str | None,
    point_count: int,
) -> list[dict[str, Any]]:
    """Normalize selected point labels and validate their plot semantics."""
    raw_points = config.get("points")
    if raw_points is None:
        return []
    items: list[dict[str, Any]] = []
    seen: set[int] = set()
    for item in _chart_list(raw_points, "data_labels.points"):
        if isinstance(item, dict):
            raw_index = item.get("idx")
            data = dict(item)
        else:
            raw_index = item
            data = {}
        if isinstance(raw_index, bool):
            raise RuntimeError("Native PPTX chart data_labels.points idx must be an integer")
        index_value = _number(raw_index, "data_labels.points idx")
        if not index_value.is_integer():
            raise RuntimeError("Native PPTX chart data_labels.points idx must be an integer")
        index = int(index_value)
        if index < 0 or index >= point_count:
            raise RuntimeError("Native PPTX chart data_labels.points idx is outside point range")
        if index in seen:
            raise RuntimeError("Native PPTX chart data_labels.points idx values must be unique")
        _data_label_position(
            _first_present(data.get("position"), config.get("position")),
            chart_type,
            grouping,
        )
        if data.get("delete") is not None and not isinstance(data["delete"], bool):
            raise RuntimeError(
                "Native PPTX chart data_labels.points delete must be a boolean"
            )
        if data.get("text") is not None and (
            not isinstance(data["text"], str) or not data["text"]
        ):
            raise RuntimeError(
                "Native PPTX chart data_labels.points text must be a non-empty string"
            )
        seen.add(index)
        data["idx"] = index
        items.append(data)
    return items
