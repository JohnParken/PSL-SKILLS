"""Classic native chart XML emitters."""

from __future__ import annotations

import base64
import binascii
import hashlib
from typing import Any
from xml.etree import ElementTree as ET

from ..drawingml.utils import (
    _xml_escape,
    detect_text_lang,
    text_has_rtl_characters,
    text_uses_rtl,
)
from .chart_data import (
    _DEFAULT_CHART_COLORS,
    _category_axis_is_date,
    _category_axis_reversed,
    _chart_language,
    _chart_list,
    _chart_plot_area_layout,
    _data_label_position,
    _data_label_point_items,
    _data_labels_config,
    data_labels_show_unlisted,
)
from .chart_style import (
    _alpha_xml,
    _axis_title_xml,
    _axis_titles,
    _chart_area_sp_pr_xml,
    _chart_line_sp_pr_xml,
    _chart_text_entry,
    _chart_text_entry_color,
    _chart_text_entry_font_face,
    _chart_text_entry_font_size,
    _chart_text_sizes,
    _chart_title_is_bounded,
    _chart_tx_pr_xml,
    _classic_chart_style,
    _font_face_xml,
    _inferred_bar_gap_width,
    _inferred_bar_overlap,
    _inferred_cross_between,
    _major_gridlines_xml,
)
from .marker_attributes import native_json_is_authoritative
from .marker_common import (
    PACKAGE_REL_TYPE,
    _bool_attr,
    _chart_bool,
    _clean_hex,
    _compact_key,
    _excel_col,
    _first_present,
    _font_size_hpt,
    _hex_or_none,
    _powerpoint_line_width_emu,
)


_DEFAULT_GAP_WIDTH = 150



# Stable public facade; implementation is split by responsibility.
from .chart_xml_labels import (
    _string_cache,
    _number_cache,
    _category_reference_xml,
    _series_color_xml,
    _data_label_flags_xml,
    _source_data_labels_xml,
    _data_label_custom_text_xml,
    _data_labels_xml,
    _series_scoped_data_labels,
    _chart_color,
    _data_point_colors_xml,
    _marker_xml,
    _marker_size_pt,
    _line_point_markers_xml,
)
from .chart_xml_series import (
    _series_xml,
    _chart_title_paragraph_xml,
    _chart_title_xml,
    _plot_area_layout_xml,
    _chart_legend_xml,
    _scatter_series_style_xml,
    _xy_series_xml,
    _bar_overlap_xml,
    _bar_chart_group_xml,
    _line_area_chart_group_xml,
)
from .chart_xml_axes import (
    _axis_scaling_xml,
    _axis_tick_marks,
    _axis_position,
    _axis_label_position,
    _axis_number_format_xml,
    _axis_major_gridlines_xml,
    _axis_tx_pr_xml,
    _axis_pair_xml,
    _secondary_axis_xml,
    _combo_axis_grouping,
    _combo_plot_layer,
)
from .chart_xml_plots import (
    _combo_plot_xml,
    _chart_plot_xml,
)
from .chart_xml_extended import (
    _axis_xml,
    _xy_axis_xml,
    _stock_series_xml,
    _stock_axis_xml,
    _chart_data_with_fallback_geometry,
)
from .chart_xml_document import (
    _chart_xml,
    _chart_rels_xml,
)
