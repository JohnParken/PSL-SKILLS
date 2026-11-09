# Adapted from PPT Master 5e8856b (MIT); see assets/licenses/ppt_master.txt.
"""Compatibility facade for chart data helpers.

Implementation is separated by responsibility; public and private import names
remain available here for existing callers.
"""
from __future__ import annotations

from .chart_data_shared import (
    Any,
    LanguageTagError,
    _AXIS_ROLE_DEFAULTS,
    _CATEGORY_CHART_TYPES,
    _CHARTEX_CHART_TYPES,
    _DEFAULT_CHART_COLORS,
    _DEFERRED_CHART_TYPES,
    _UNSUPPORTED_3D_CHART_TYPES,
    _XY_CHART_TYPES,
    _chart_bool,
    _clean_hex,
    _compact_key,
    _first_present,
    _hex_or_none,
    _number,
    _powerpoint_emu,
    _powerpoint_line_width_emu,
    math,
    normalize_language_tag,
)
from .chart_data_labels import (
    _chart_data_labels,
    _chart_list,
    _chart_number,
    _chart_point_value,
    _data_label_point_items,
    _data_label_position,
    _data_labels_config,
    data_labels_show_unlisted,
)
from .chart_data_axes import (
    _category_axis_is_date,
    _category_axis_reversed,
    _chart_axes,
    _chart_plot_area,
    _chart_plot_area_layout,
    _doughnut_hole_size,
)
from .chart_data_category import (
    _category_chart_data,
    _category_series,
    _chart_grouping,
    _chart_kind,
    _gap_width,
    _line_style,
    _marker_size,
    _overlap,
    _point_color,
    _radar_style,
)
from .chart_data_combo import (
    _combo_axis_name,
    _combo_chart_data,
    _combo_plot_entry,
    _combo_plot_type,
    _combo_series_indices,
    _plot_series_area_style,
)
from .chart_data_special import (
    _chart_categories,
    _chart_language,
    _chart_values,
    _chartex_chart_data,
    _hierarchy_levels,
    _point_values,
    _stock_chart_data,
    _treemap_parent_labels,
    _xy_chart_data,
)
from .chart_data_dispatch import (
    _chart_data,
    validate_chart_payload,
    validate_data_label_position,
)
