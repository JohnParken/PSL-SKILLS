"""Native chart metadata normalization."""

from __future__ import annotations

import math
from typing import Any

from language_tags import LanguageTagError, normalize_language_tag

from .marker_common import (
    _chart_bool,
    _clean_hex,
    _compact_key,
    _first_present,
    _hex_or_none,
    _number,
    _powerpoint_emu,
    _powerpoint_line_width_emu,
)


_CATEGORY_CHART_TYPES = {
    "area",
    "bar",
    "column",
    "doughnut",
    "line",
    "of_pie",
    "pie",
    "radar",
}
_XY_CHART_TYPES = {"scatter", "bubble"}
_CHARTEX_CHART_TYPES = {
    "box_whisker",
    "funnel",
    "histogram",
    "pareto",
    "sunburst",
    "treemap",
    "waterfall",
}
_DEFERRED_CHART_TYPES = {
    "bullet",
    "gantt",
    "heatmap",
    "map",
}
_UNSUPPORTED_3D_CHART_TYPES = {
    "area3d",
    "bar3d",
    "column3d",
    "line3d",
    "pie3d",
    "surface",
}
_DEFAULT_CHART_COLORS = [
    "4472C4",
    "ED7D31",
    "A5A5A5",
    "FFC000",
    "5B9BD5",
    "70AD47",
    "264478",
    "9E480E",
]

_AXIS_ROLE_DEFAULTS = {
    "category": ("text", "bottom"),
    "secondary_category": ("text", "bottom"),
    "secondary_value": ("value", "right"),
    "value": ("value", "left"),
    "x": ("value", "bottom"),
    "y": ("value", "left"),
}



# Stable public facade; implementation is split by responsibility.
from .chart_data_labels import (
    _chart_number,
    _chart_point_value,
    _chart_list,
    _data_labels_config,
    _data_label_position,
    _chart_data_labels,
    data_labels_show_unlisted,
    _data_label_point_items,
)
from .chart_data_axes import (
    _chart_axes,
    _category_axis_reversed,
    _category_axis_is_date,
    _chart_plot_area,
    _chart_plot_area_layout,
    _doughnut_hole_size,
)
from .chart_data_kinds import (
    _chart_kind,
    _chart_grouping,
    _line_style,
    _radar_style,
    _point_color,
    _marker_size,
    _gap_width,
    _overlap,
)
from .chart_data_category import (
    _category_series,
    _category_chart_data,
)
from .chart_data_combo import (
    _combo_axis_name,
    _combo_plot_type,
    _plot_series_area_style,
    _combo_series_indices,
    _combo_plot_entry,
    _combo_chart_data,
)
from .chart_data_extended import (
    _chart_values,
    _chart_categories,
    _hierarchy_levels,
    _treemap_parent_labels,
    _chartex_chart_data,
    _stock_chart_data,
    _point_values,
    _xy_chart_data,
)
from .chart_data_dispatch import (
    _chart_language,
    _chart_data,
    validate_chart_payload,
    validate_data_label_position,
)
