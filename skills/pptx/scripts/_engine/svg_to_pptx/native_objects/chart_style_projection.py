# Adapted from PPT Master 5e8856b (MIT); see assets/licenses/ppt_master.txt.
"""Chart style projection implementation."""
from __future__ import annotations

from .chart_style_shared import (
    Any,
    ET,
    _NUMBER_FORMAT_RE,
    _chart_data,
    _fallback_text_records,
    _maybe_number,
    _normalized_fallback_text,
    _visible_fallback_texts,
    re,
)

def _chart_projection_text_variants(value: Any) -> set[str]:
    text = _normalized_fallback_text(value)
    variants = {text} if text else set()
    numeric = _maybe_number(value)
    if numeric is not None and numeric.is_integer():
        variants.add(str(int(numeric)))
    return variants


def _format_number_like_excel(number: float, number_format: str) -> str | None:
    """Render ``number`` the way PowerPoint shows a plain Excel format code.

    Covers the codes a chart payload realistically writes — ``0``, ``0.0``,
    ``0.00``, ``#,##0``, ``#,##0.0``, their ``%`` forms, and a quoted literal
    prefix or suffix such as ``"$"#,##0`` or ``0.0"%"`` (a literal percent
    sign does not scale the value). Anything else returns ``None`` so the
    caller keeps only the literal variants.
    """
    match = _NUMBER_FORMAT_RE.match(number_format.strip())
    if match is None:
        return None
    prefix = match.group("prefix") or ""
    suffix = match.group("suffix") or ""
    grouping, _integers, decimals, percent = match.groups()[1:5]
    value = number * 100 if percent else number
    digits = len(decimals or "")
    text = f"{value:,.{digits}f}" if grouping == "#,##" else f"{value:.{digits}f}"
    return f"{prefix}{text}{'%' if percent else ''}{suffix}"


def _chart_number_formats(payload: dict[str, Any]) -> list[str]:
    """Collect every ``number_format`` the payload applies to visible numbers."""
    formats: list[str] = []

    def add(container: Any) -> None:
        if not isinstance(container, dict):
            return
        value = container.get("number_format")
        if value is None:
            value = container.get("numberFormat")
        if isinstance(value, str) and value.strip() and value not in formats:
            formats.append(value)

    for labels in (payload.get("data_labels"), payload.get("dataLabels")):
        add(labels)
        if isinstance(labels, dict):
            for point in labels.get("points") or []:
                add(point)
    for plot in payload.get("plots") or []:
        if isinstance(plot, dict):
            add(plot.get("data_labels"))
            add(plot.get("dataLabels"))
    for series in payload.get("series") or []:
        if isinstance(series, dict):
            add(series.get("data_labels"))
            add(series.get("dataLabels"))
    axes = payload.get("axes")
    if isinstance(axes, dict):
        for config in axes.values():
            add(config)
    return formats


def _chart_projected_texts(
    payload: dict[str, Any],
    chart_data: dict[str, Any],
) -> set[str]:
    from .chart_style_text import _axis_titles
    from .chart_style_warnings import _metadata_text
    from .chart_style_companions import _chart_companion_entries
    projected: set[str] = set()
    number_formats = _chart_number_formats(payload)

    def add(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {
                    "categories", "levels", "name", "series", "sizes",
                    "values", "x", "y",
                }:
                    add(item)
            return
        if isinstance(value, list):
            for item in value:
                add(item)
            return
        projected.update(_chart_projection_text_variants(value))
        numeric = _maybe_number(value)
        if numeric is None:
            return
        for number_format in number_formats:
            formatted = _format_number_like_excel(numeric, number_format)
            if formatted:
                projected.add(formatted)

    for key in ("categories", "levels", "plots", "series", "values"):
        add(chart_data.get(key))
    for field_name in ("title", "subtitle"):
        text = _metadata_text(payload.get(field_name))
        if text:
            projected.add(text)
    for value in _axis_titles(payload).values():
        text = _metadata_text(value)
        if text:
            projected.add(text)
    for item in _chart_companion_entries(
        payload,
        include_title=True,
        include_subtitle_as_caption=True,
    ):
        projected.update(_chart_projection_text_variants(item.get("text")))

    axes = chart_data.get("axes") if isinstance(chart_data.get("axes"), dict) else {}
    for config in axes.values():
        if not isinstance(config, dict):
            continue
        minimum = _maybe_number(config.get("minimum"))
        maximum = _maybe_number(config.get("maximum"))
        major_unit = _maybe_number(config.get("major_unit"))
        if (
            minimum is None
            or maximum is None
            or major_unit is None
            or major_unit <= 0
        ):
            continue
        # Build each tick from the index, not by accumulating ``major_unit``:
        # repeated float addition turns the top tick 5.6 into
        # 5.6000000000000005 and the fallback's "5.6" is then reported missing.
        for index in range(1000):
            value = round(minimum + index * major_unit, 10)
            if value > maximum + major_unit * 1e-6:
                break
            add(value)
    return projected


def _native_chart_reverse_text_warnings(
    elem: ET.Element,
    payload: dict[str, Any],
    chart_data: dict[str, Any],
) -> list[str]:
    from .chart_style_text import _record_has_label
    projected = _chart_projected_texts(payload, chart_data)
    missing: list[str] = []
    for record in _fallback_text_records(elem):
        if _record_has_label(record, "legend"):
            continue
        if record.text not in projected and record.text not in missing:
            missing.append(record.text)
    if not missing:
        return []
    sample = ", ".join(repr(text) for text in missing[:8])
    suffix = "" if len(missing) <= 8 else f", and {len(missing) - 8} more"
    message = (
        "Native PPTX chart visible text not projected: "
        f"{sample}{suffix}. Use categories/data labels/axis labels/legend or companion text."
        " For formatted value labels, set data_labels.number_format to match the fallback."
    )
    # ``73.0`` in the fallback while the payload value is 73: General format
    # renders ``73``, so the decimals need an explicit number_format.
    if any(
        re.fullmatch(r"-?\d+\.\d+", text)
        and str(int(float(text))) in projected
        and float(text).is_integer()
        for text in missing
    ):
        message += (
            " A value that ends in .0 renders without the decimals under the "
            "General format; set data_labels.number_format (for example "
            '"0.0") when the fallback shows them.'
        )
    return [message]


def _native_chart_chrome_warnings(elem: ET.Element, payload: dict[str, Any]) -> list[str]:
    from .chart_style_warnings import (
        _legend_candidate_texts, _native_chart_area_warnings,
        _native_chart_category_order_warnings, _native_chart_line_marker_warnings,
        _native_chart_point_color_warnings, _native_chart_style_color_warnings,
    )
    from .chart_style_geometry import _native_chart_radial_warnings, _native_chart_tile_color_warnings
    from .chart_style_companions import _chart_companion_entries
    fallback_texts = set(_visible_fallback_texts(elem))
    warnings: list[str] = []
    style = payload.get("style") if isinstance(payload.get("style"), dict) else {}
    show_legend = payload.get("show_legend", style.get("show_legend", False))
    if show_legend:
        legend_texts = _legend_candidate_texts(payload)
        missing_legend = [
            text for text in legend_texts if text not in fallback_texts
        ]
        for text in missing_legend:
            warnings.append(
                "Native PPTX chart legend label is not visible inside the fallback "
                f"marker: {text!r}. Add the expected label or remove show_legend."
            )

    companion_entries = _chart_companion_entries(
        payload,
        include_title=False,
        include_subtitle_as_caption=False,
    )
    missing_companion: list[str] = []
    for item in companion_entries:
        text = _normalized_fallback_text(item.get("text"))
        if text and text not in fallback_texts:
            missing_companion.append(text)
    if missing_companion:
        sample = ", ".join(repr(text) for text in missing_companion[:5])
        suffix = "" if len(missing_companion) <= 5 else f", and {len(missing_companion) - 5} more"
        warnings.append(
            "Native PPTX chart companion text is not visible inside the fallback "
            "marker and may appear only after --native-charts-and-tables: "
            f"{sample}{suffix}. "
            "Keep companion metadata aligned with visible chart annotations."
        )
    warnings.extend(_native_chart_style_color_warnings(elem, payload))
    chart_data = _chart_data(payload)
    warnings.extend(_native_chart_point_color_warnings(elem, payload, chart_data))
    warnings.extend(_native_chart_radial_warnings(elem, payload, chart_data))
    warnings.extend(_native_chart_tile_color_warnings(elem, payload, chart_data))
    warnings.extend(_native_chart_reverse_text_warnings(elem, payload, chart_data))
    warnings.extend(_native_chart_line_marker_warnings(elem, payload, chart_data))
    warnings.extend(_native_chart_area_warnings(elem, payload, chart_data))
    warnings.extend(_native_chart_category_order_warnings(elem, payload, chart_data))
    return warnings
