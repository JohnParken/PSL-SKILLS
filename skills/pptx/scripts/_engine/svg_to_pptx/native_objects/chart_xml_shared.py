# Adapted from PPT Master 5e8856b (MIT); see assets/licenses/ppt_master.txt.
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
