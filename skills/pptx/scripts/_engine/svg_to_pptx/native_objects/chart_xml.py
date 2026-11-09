# Adapted from PPT Master 5e8856b (MIT); see assets/licenses/ppt_master.txt.
"""Compatibility facade for chart xml helpers.

Implementation is separated by responsibility; public and private import names
remain available here for existing callers.
"""
from __future__ import annotations

from .chart_xml_shared import (
    Any,
    ET,
    PACKAGE_REL_TYPE,
    _DEFAULT_CHART_COLORS,
    _DEFAULT_GAP_WIDTH,
    _alpha_xml,
    _axis_title_xml,
    _axis_titles,
    _bool_attr,
    _category_axis_is_date,
    _category_axis_reversed,
    _chart_area_sp_pr_xml,
    _chart_bool,
    _chart_language,
    _chart_line_sp_pr_xml,
    _chart_list,
    _chart_plot_area_layout,
    _chart_text_entry,
    _chart_text_entry_color,
    _chart_text_entry_font_face,
    _chart_text_entry_font_size,
    _chart_text_sizes,
    _chart_title_is_bounded,
    _chart_tx_pr_xml,
    _classic_chart_style,
    _clean_hex,
    _compact_key,
    _data_label_point_items,
    _data_label_position,
    _data_labels_config,
    _excel_col,
    _first_present,
    _font_face_xml,
    _font_size_hpt,
    _hex_or_none,
    _inferred_bar_gap_width,
    _inferred_bar_overlap,
    _inferred_cross_between,
    _major_gridlines_xml,
    _powerpoint_line_width_emu,
    _xml_escape,
    base64,
    binascii,
    data_labels_show_unlisted,
    detect_text_lang,
    hashlib,
    native_json_is_authoritative,
    text_has_rtl_characters,
    text_uses_rtl,
)
from .chart_xml_labels import (
    _category_reference_xml,
    _data_label_custom_text_xml,
    _data_label_flags_xml,
    _data_labels_xml,
    _number_cache,
    _series_color_xml,
    _series_scoped_data_labels,
    _source_data_labels_xml,
    _string_cache,
)
from .chart_xml_series import (
    _chart_color,
    _data_point_colors_xml,
    _line_point_markers_xml,
    _marker_size_pt,
    _marker_xml,
    _series_xml,
)
from .chart_xml_chrome import (
    _bar_chart_group_xml,
    _bar_overlap_xml,
    _chart_legend_xml,
    _chart_title_paragraph_xml,
    _chart_title_xml,
    _line_area_chart_group_xml,
    _plot_area_layout_xml,
    _scatter_series_style_xml,
    _xy_series_xml,
)
from .chart_xml_axes import (
    _axis_label_position,
    _axis_major_gridlines_xml,
    _axis_number_format_xml,
    _axis_pair_xml,
    _axis_position,
    _axis_scaling_xml,
    _axis_tick_marks,
    _axis_tx_pr_xml,
    _secondary_axis_xml,
)
from .chart_xml_plots import (
    _chart_plot_xml,
    _combo_axis_grouping,
    _combo_plot_layer,
    _combo_plot_xml,
)
from .chart_xml_special import (
    _axis_xml,
    _stock_axis_xml,
    _stock_series_xml,
    _xy_axis_xml,
)
from .chart_xml_document import (
    _chart_data_with_fallback_geometry,
    _chart_rels_xml,
    _chart_xml,
)
