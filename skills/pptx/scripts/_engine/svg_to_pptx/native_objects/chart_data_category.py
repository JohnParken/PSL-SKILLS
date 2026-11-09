# Adapted from PPT Master 5e8856b (MIT); see assets/licenses/ppt_master.txt.
"""Chart data category implementation."""
from __future__ import annotations

from .chart_data_shared import (
    Any,
    _CATEGORY_CHART_TYPES,
    _CHARTEX_CHART_TYPES,
    _DEFERRED_CHART_TYPES,
    _UNSUPPORTED_3D_CHART_TYPES,
    _XY_CHART_TYPES,
    _chart_bool,
    _clean_hex,
    _compact_key,
    _first_present,
    _number,
    _powerpoint_line_width_emu,
)

def _chart_kind(payload: dict[str, Any]) -> tuple[str, str | None, str | None]:
    raw_type = payload.get("type") or payload.get("chart_type") or "column"
    key = _compact_key(raw_type)
    aliases: dict[str, tuple[str, str | None, str | None]] = {
        "area": ("area", "standard", None),
        "areastacked": ("area", "stacked", None),
        "areastacked100": ("area", "percentStacked", None),
        "area100": ("area", "percentStacked", None),
        "bar": ("bar", "clustered", None),
        "barofpie": ("of_pie", None, "bar"),
        "barclustered": ("bar", "clustered", None),
        "barstacked": ("bar", "stacked", None),
        "barstacked100": ("bar", "percentStacked", None),
        "boxandwhisker": ("box_whisker", None, None),
        "boxplot": ("box_whisker", None, None),
        "boxwhisker": ("box_whisker", None, None),
        "bubble": ("bubble", None, None),
        "bullet": ("bullet", None, None),
        "bulletchart": ("bullet", None, None),
        "combo": ("combo", None, None),
        "combochart": ("combo", None, None),
        "choropleth": ("map", None, None),
        "conebarclustered": ("bar3d", "clustered", "cone"),
        "conebarstacked": ("bar3d", "stacked", "cone"),
        "conebarstacked100": ("bar3d", "percentStacked", "cone"),
        "conecol": ("column3d", "clustered", "cone"),
        "conecolclustered": ("column3d", "clustered", "cone"),
        "conecolstacked": ("column3d", "stacked", "cone"),
        "conecolstacked100": ("column3d", "percentStacked", "cone"),
        "col": ("column", "clustered", None),
        "column": ("column", "clustered", None),
        "columnclustered": ("column", "clustered", None),
        "columnstacked": ("column", "stacked", None),
        "columnstacked100": ("column", "percentStacked", None),
        "contour": ("surface", None, "topView"),
        "contourwireframe": ("surface", None, "topViewWireframe"),
        "cylinderbarclustered": ("bar3d", "clustered", "cylinder"),
        "cylinderbarstacked": ("bar3d", "stacked", "cylinder"),
        "cylinderbarstacked100": ("bar3d", "percentStacked", "cylinder"),
        "cylindercol": ("column3d", "clustered", "cylinder"),
        "cylindercolclustered": ("column3d", "clustered", "cylinder"),
        "cylindercolstacked": ("column3d", "stacked", "cylinder"),
        "cylindercolstacked100": ("column3d", "percentStacked", "cylinder"),
        "doughnut": ("doughnut", None, None),
        "doughnutexploded": ("doughnut", None, "exploded"),
        "donut": ("doughnut", None, None),
        "donutexploded": ("doughnut", None, "exploded"),
        "filledmap": ("map", None, None),
        "funnel": ("funnel", None, None),
        "funnelchart": ("funnel", None, None),
        "gantt": ("gantt", None, None),
        "ganttchart": ("gantt", None, None),
        "geo": ("map", None, None),
        "geomap": ("map", None, None),
        "heatmap": ("heatmap", None, None),
        "heatmapchart": ("heatmap", None, None),
        "histogram": ("histogram", None, None),
        "histogramchart": ("histogram", None, None),
        "line": ("line", "standard", "line"),
        "linemarkers": ("line", "standard", "lineMarker"),
        "linemarkersstacked": ("line", "stacked", "lineMarker"),
        "linemarkersstacked100": ("line", "percentStacked", "lineMarker"),
        "linestacked": ("line", "stacked", "line"),
        "linestacked100": ("line", "percentStacked", "line"),
        "linestackedmarkers": ("line", "stacked", "lineMarker"),
        "linestackedmarkers100": ("line", "percentStacked", "lineMarker"),
        "pie": ("pie", None, None),
        "pieexploded": ("pie", None, "exploded"),
        "ofpie": ("of_pie", None, "pie"),
        "pieofpie": ("of_pie", None, "pie"),
        "pareto": ("pareto", None, None),
        "paretochart": ("pareto", None, None),
        "pyramidbarclustered": ("bar3d", "clustered", "pyramid"),
        "pyramidbarstacked": ("bar3d", "stacked", "pyramid"),
        "pyramidbarstacked100": ("bar3d", "percentStacked", "pyramid"),
        "pyramidcol": ("column3d", "clustered", "pyramid"),
        "pyramidcolclustered": ("column3d", "clustered", "pyramid"),
        "pyramidcolstacked": ("column3d", "stacked", "pyramid"),
        "pyramidcolstacked100": ("column3d", "percentStacked", "pyramid"),
        "radar": ("radar", None, "line"),
        "radarfilled": ("radar", None, "filled"),
        "radarmarkers": ("radar", None, "lineMarker"),
        "scatter": ("scatter", None, "marker"),
        "stock": ("stock", None, "hlc"),
        "stockhlc": ("stock", None, "hlc"),
        "stockohlc": ("stock", None, "ohlc"),
        "stockvhlc": ("stock", None, "vhlc"),
        "stockvohlc": ("stock", None, "vohlc"),
        "surface": ("surface", None, "surface3D"),
        "surface3d": ("surface", None, "surface3D"),
        "surfacewireframe": ("surface", None, "surface3DWireframe"),
        "surfacetopview": ("surface", None, "topView"),
        "surfacetopviewwireframe": ("surface", None, "topViewWireframe"),
        "sunburst": ("sunburst", None, None),
        "sunburstchart": ("sunburst", None, None),
        "map": ("map", None, None),
        "mapchart": ("map", None, None),
        "threedarea": ("area3d", "standard", None),
        "threedareastacked": ("area3d", "stacked", None),
        "threedareastacked100": ("area3d", "percentStacked", None),
        "threedbar": ("bar3d", "clustered", "box"),
        "threedbarclustered": ("bar3d", "clustered", "box"),
        "threedbarstacked": ("bar3d", "stacked", "box"),
        "threedbarstacked100": ("bar3d", "percentStacked", "box"),
        "threedcolumn": ("column3d", "clustered", "box"),
        "threedcolumnclustered": ("column3d", "clustered", "box"),
        "threedcolumnstacked": ("column3d", "stacked", "box"),
        "threedcolumnstacked100": ("column3d", "percentStacked", "box"),
        "threedline": ("line3d", "standard", None),
        "threedpie": ("pie3d", None, None),
        "threedpieexploded": ("pie3d", None, "exploded"),
        "treemap": ("treemap", None, None),
        "treemapchart": ("treemap", None, None),
        "waterfall": ("waterfall", None, None),
        "waterfallchart": ("waterfall", None, None),
        "xy": ("scatter", None, "marker"),
        "xyscatter": ("scatter", None, "marker"),
        "xyscatterlines": ("scatter", None, "lineMarker"),
        "xyscatterlinesnomarkers": ("scatter", None, "line"),
        "xyscattersmooth": ("scatter", None, "smoothMarker"),
        "xyscattersmoothnomarkers": ("scatter", None, "smooth"),
    }
    if key.startswith("100percentstacked"):
        key = key.replace("100percentstacked", "", 1) + "stacked100"
    if key.startswith("percentstacked"):
        key = key.replace("percentstacked", "", 1) + "stacked100"
    if key.startswith("3d"):
        key = "threed" + key[2:]
    chart_type, grouping, style = aliases.get(key, (key, None, None))
    if chart_type in _UNSUPPORTED_3D_CHART_TYPES:
        raise RuntimeError("Native PPTX 3D charts are intentionally unsupported")
    if chart_type in _DEFERRED_CHART_TYPES:
        raise RuntimeError(
            f"Native PPTX {chart_type} chart is outside current basic chart support"
        )

    supported = sorted(_CATEGORY_CHART_TYPES | _XY_CHART_TYPES | _CHARTEX_CHART_TYPES | {"combo", "stock"})
    if chart_type not in supported:
        raise RuntimeError(f"Native PPTX chart type must be one of: {', '.join(supported)}")
    return chart_type, grouping, style


def _chart_grouping(
    chart_type: str,
    payload: dict[str, Any],
    alias_grouping: str | None,
) -> str | None:
    grouping = payload.get("grouping") or payload.get("chart_grouping") or alias_grouping
    if not grouping and payload.get("stacked"):
        grouping = "stacked"
    if not grouping:
        return "clustered" if chart_type in {"bar", "column"} else "standard"

    aliases = {
        "100": "percentStacked",
        "100percent": "percentStacked",
        "100percentstacked": "percentStacked",
        "clustered": "clustered",
        "percent": "percentStacked",
        "percentstacked": "percentStacked",
        "stacked": "stacked",
        "standard": "standard",
    }
    normalized = aliases.get(_compact_key(grouping))
    if chart_type in {"bar", "column"}:
        allowed = {"clustered", "stacked", "percentStacked"}
    elif chart_type in {"area", "line"}:
        allowed = {"standard", "stacked", "percentStacked"}
    else:
        allowed = {"standard"}
    if normalized not in allowed:
        if normalized in {"clustered", "standard"}:
            allowed_text = ", ".join(sorted(allowed))
            raise RuntimeError(f"Native PPTX {chart_type} chart grouping must be one of: {allowed_text}")
        raise RuntimeError(
            f"Native PPTX {grouping} grouping is outside current basic chart support"
        )
    return normalized


def _line_style(payload: dict[str, Any], alias_style: str | None) -> str:
    raw_style = payload.get("line_style") or payload.get("lineStyle") or alias_style
    if raw_style is None:
        raw_style = "lineMarker" if payload.get("markers") else "line"
    aliases = {
        "line": "line",
        "linemarker": "lineMarker",
        "marker": "lineMarker",
        "markers": "lineMarker",
        "none": "line",
        "nomarker": "line",
        "nomarkers": "line",
    }
    style = aliases.get(_compact_key(raw_style))
    if not style:
        raise RuntimeError("Native PPTX line_style must be one of: line, lineMarker")
    return style


def _radar_style(payload: dict[str, Any], alias_style: str | None) -> tuple[str, str | None]:
    raw_style = payload.get("radar_style") or payload.get("radarStyle") or alias_style or "line"
    aliases = {
        "filled": ("filled", None),
        "line": ("marker", "none"),
        "linemarker": ("marker", "circle"),
        "marker": ("marker", "none"),
        "markers": ("marker", "circle"),
        "standard": ("marker", "none"),
    }
    style = aliases.get(_compact_key(raw_style))
    if not style:
        raise RuntimeError(
            f"Native PPTX radar_style {raw_style} is outside current basic chart support"
        )
    return style


def _point_color(color: Any, chart_type: str) -> str | None:
    """Hex per-point colour; line series may leave a point unmarked with ``null``."""
    if chart_type == "line" and (color is None or _compact_key(color) == "none"):
        return None
    if color is None:
        raise RuntimeError(
            f"Native PPTX {chart_type} chart series point_colors entries must be "
            "colours (only a line series may leave a point null)"
        )
    return _clean_hex(color, "#4472C4")


def _marker_size(source: dict[str, Any], chart_type: str) -> float | None:
    """Line marker diameter in SVG px (``marker_size``), validated for the 2..72pt range."""
    raw = _first_present(source.get("marker_size"), source.get("markerSize"))
    if raw is None:
        return None
    if chart_type != "line":
        raise RuntimeError("Native PPTX chart marker_size applies to line series only")
    size = _number(raw, "chart marker_size")
    if not 2 <= size * 0.75 <= 72:
        raise RuntimeError("Native PPTX chart marker_size must resolve to 2..72pt")
    return size


def _gap_width(source: dict[str, Any], chart_type: str) -> int | None:
    """Bar/column gap width as a percentage of one bar (``gap_width``, 0..500)."""
    raw = _first_present(source.get("gap_width"), source.get("gapWidth"))
    if raw is None:
        return None
    if chart_type not in {"bar", "column"}:
        raise RuntimeError("Native PPTX chart gap_width applies to bar and column charts only")
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not 0 <= raw <= 500:
        raise RuntimeError("Native PPTX chart gap_width must be a number between 0 and 500")
    return int(round(raw))


def _overlap(source: dict[str, Any], chart_type: str) -> int | None:
    """Bar/column series overlap as a percentage of one bar (``overlap``, -100..100)."""
    raw = source.get("overlap")
    if raw is None:
        return None
    if chart_type not in {"bar", "column"}:
        raise RuntimeError("Native PPTX chart overlap applies to bar and column charts only")
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not -100 <= raw <= 100:
        raise RuntimeError("Native PPTX chart overlap must be a number between -100 and 100")
    return int(round(raw))


def _category_series(
    payload: dict[str, Any],
    categories: list[Any],
    *,
    chart_type: str,
    grouping: str | None,
    typed_combo: bool = False,
) -> list[dict[str, Any]]:
    from .chart_data_labels import _chart_data_labels, _chart_list, _chart_point_value
    raw_series = payload.get("series", [])
    if not categories or not isinstance(raw_series, list) or not raw_series:
        raise RuntimeError("Native PPTX chart requires non-empty categories and series")
    root_point_colors = _first_present(
        payload.get("point_colors"),
        payload.get("pointColors"),
    )
    if root_point_colors is not None and len(raw_series) != 1:
        raise RuntimeError("Native PPTX chart root point_colors is only valid for one series")

    series: list[dict[str, Any]] = []
    for idx, item in enumerate(raw_series, start=1):
        if not isinstance(item, dict):
            raise RuntimeError("Native PPTX chart series entries must be objects")
        if (
            _first_present(item.get("line_style"), item.get("lineStyle")) is not None
            and not typed_combo
        ):
            raise RuntimeError(
                "Native PPTX chart series[].line_style is not a series option; "
                "set line_style on the chart root (combo: on the plot or typed series)"
            )
        values = [
            _chart_point_value(value)
            for value in _chart_list(item.get("values", []), "series[].values")
        ]
        if len(values) != len(categories):
            raise RuntimeError("Native PPTX chart series values must match categories length")
        if all(value is None for value in values):
            raise RuntimeError("Native PPTX chart series values must contain a number")
        if chart_type not in {"area", "bar", "column", "line"} and None in values:
            raise RuntimeError(
                f"Native PPTX {chart_type} chart series values cannot contain null gaps"
            )
        raw_point_colors = _first_present(
            item.get("point_colors"),
            item.get("pointColors"),
            root_point_colors if idx == 1 else None,
        )
        point_colors = [
            _point_color(color, chart_type)
            for color in _chart_list(raw_point_colors, "series[].point_colors")
        ]
        if point_colors and len(point_colors) != len(values):
            raise RuntimeError("Native PPTX chart series point_colors must match values length")
        series_item = {"name": str(item.get("name") or f"Series {idx}"), "values": values}
        if point_colors:
            series_item["point_colors"] = point_colors
        marker_size = _marker_size(item, chart_type)
        if marker_size is not None:
            series_item["marker_size"] = marker_size
        fill_opacity = _first_present(
            item.get("fill_opacity"),
            item.get("fillOpacity"),
        )
        if fill_opacity is not None:
            fill_opacity = _number(fill_opacity, "series fill_opacity")
            if not 0 <= fill_opacity <= 1:
                raise RuntimeError(
                    "Native PPTX chart series fill_opacity must be between 0 and 1"
                )
            series_item["fill_opacity"] = fill_opacity
        line_width = _first_present(
            item.get("line_width"),
            item.get("lineWidth"),
        )
        if line_width is not None:
            line_width = _number(line_width, "series line_width")
            if line_width <= 0:
                raise RuntimeError("Native PPTX chart series line_width must be positive")
            _powerpoint_line_width_emu(line_width, "series line_width")
            series_item["line_width"] = line_width
        data_labels = _chart_data_labels(
            item,
            chart_type,
            grouping,
            len(categories),
        )
        if data_labels is not None:
            series_item["data_labels"] = data_labels
        series.append(series_item)
    return series


def _category_chart_data(
    payload: dict[str, Any],
    chart_type: str,
    alias_grouping: str | None,
    alias_style: str | None,
) -> dict[str, Any]:
    from .chart_data_labels import _chart_data_labels, _chart_list, _chart_number
    from .chart_data_axes import _category_axis_is_date, _chart_axes, _doughnut_hole_size
    axes = _chart_axes(
        payload,
        {"category", "value"},
        bar_orientation=chart_type == "bar",
    )
    if axes and chart_type in {"doughnut", "of_pie", "pie"}:
        raise RuntimeError(
            f"Native PPTX {chart_type} chart axes are outside current support"
        )
    if _category_axis_is_date(axes) and chart_type != "area":
        raise RuntimeError(
            "Native PPTX date category axes are currently supported for area charts only"
        )
    raw_categories = _chart_list(payload.get("categories", []), "categories")
    categories = (
        [_chart_number(item) for item in raw_categories]
        if _category_axis_is_date(axes)
        else [str(item) for item in raw_categories]
    )
    style = payload.get("style") if isinstance(payload.get("style"), dict) else {}

    grouping = (
        _chart_grouping(chart_type, payload, alias_grouping)
        if chart_type in {"bar", "column", "line", "area"}
        else None
    )
    series = _category_series(
        payload,
        categories,
        chart_type=chart_type,
        grouping=grouping,
    )
    if chart_type in {"doughnut", "of_pie", "pie"}:
        if len(series) != 1:
            raise RuntimeError("Native PPTX pie-family charts support exactly one series")

    of_pie_type = None
    if chart_type == "of_pie":
        raw_of_pie_type = (
            payload.get("of_pie_type")
            or payload.get("ofPieType")
            or payload.get("secondary_type")
            or alias_style
            or "pie"
        )
        of_pie_aliases = {
            "bar": "bar",
            "barofpie": "bar",
            "pie": "pie",
            "pieofpie": "pie",
        }
        of_pie_type = of_pie_aliases.get(_compact_key(raw_of_pie_type))
        if not of_pie_type:
            raise RuntimeError("Native PPTX of_pie_type must be one of: bar, pie")

    line_style = _line_style(payload, alias_style) if chart_type == "line" else None
    radar_style = None
    radar_marker_style = None
    if chart_type == "radar":
        radar_style, radar_marker_style = _radar_style(payload, alias_style)

    if alias_style == "exploded" or payload.get("exploded"):
        raise RuntimeError("Native PPTX exploded pie/doughnut is outside current basic chart support")

    return {
        "kind": "category",
        "type": chart_type,
        "categories": categories,
        "grouping": grouping,
        "of_pie_type": of_pie_type,
        "hole_size": _doughnut_hole_size(payload, chart_type),
        "line_style": line_style,
        "marker_size": _marker_size(payload, chart_type),
        "gap_width": _gap_width(payload, chart_type),
        "overlap": _overlap(payload, chart_type),
        "radar_marker_style": radar_marker_style,
        "radar_style": radar_style,
        "show_value_axis_labels": _chart_bool(
            _first_present(
                payload.get("show_value_axis_labels"),
                payload.get("showValueAxisLabels"),
                style.get("show_value_axis_labels"),
                style.get("showValueAxisLabels"),
            ),
            True,
        ),
        "data_labels": _chart_data_labels(
            payload,
            chart_type,
            grouping,
            len(categories),
        ),
        "axes": axes,
        "series": series,
    }
