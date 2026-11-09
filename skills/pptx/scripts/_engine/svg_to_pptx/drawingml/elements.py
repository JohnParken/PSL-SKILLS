# Adapted from PPT Master 5e8856b (MIT); see assets/licenses/ppt_master.txt.
"""SVG element converters: rect, circle, line, path, polygon, polyline, text, image, ellipse."""

from __future__ import annotations

import base64
import binascii
import hashlib
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote_to_bytes
from xml.etree import ElementTree as ET

from pptx_core.images import ImageValidationError, MAX_IMAGE_BYTES, inspect_image

from pptx_shapes import (
    CONNECTOR_PRESET_TYPES,
    OOXML_COORDINATE_MAX,
    OOXML_COORDINATE_MIN,
    get_preset_registry,
    has_relationship_attributes,
    load_shape_type_values,
    validate_ooxml_xfrm,
)
from pptx_effects import (
    EFFECT_REASON_ATTR,
    EFFECT_STATUS_ATTR,
    NATIVE_EFFECT_ATTR,
    NATIVE_EFFECT_SHA256_ATTR,
    preserved_native_effect_xml,
)
from hyperlink_contract import svg_hyperlink_href
from pptx_to_svg.preset_authoring import AUTHORING_ATTR, AUTHORING_VALUE
from resource_paths import (
    resolve_external_image_reference,
)

from .context import (
    TEXT_FLOW_PRESERVE,
    TEXT_FLOW_SPLIT,
    ConvertContext,
    ShapeResult,
)
from .hyperlinks import (
    HYPERLINK_ACTION_KEY,
    HYPERLINK_RID_KEY,
    hyperlink_click_xml,
    hyperlink_run_metadata,
)
from .theme_colors import color_node_xml
from .theme_fonts import theme_font_tokens
from .text_baseline import drawingml_text_baseline_offset
from .text_properties import (
    drawingml_letter_spacing,
    normalize_project_text_segments,
    parse_project_baseline_shift,
    parse_project_font_style,
    parse_project_font_weight,
    parse_project_letter_spacing,
    parse_project_text_anchor,
    parse_project_text_decoration,
    resolve_project_xml_space,
)
from .utils import (
    SVG_NS, XLINK_NS, ANGLE_UNIT, FONT_PX_TO_HUNDREDTHS_PT,
    PROJECT_IMAGE_ASPECT_RATIO_ANCHORS,
    px_to_emu, _f, _get_attr, parse_svg_length,
    svg_length_x, svg_length_y, svg_length_size,
    ctx_x, ctx_y, ctx_w, ctx_h,
    rect_to_dml_xfrm,
    combine_opacity, parse_hex_color, parse_svg_color,
    resolve_project_text_image_fill, resolve_url_id, get_effective_filter_id,
    parse_inline_style, parse_font_family, is_cjk_char,
    detect_text_lang, estimate_text_cluster_widths, font_px_to_hpt,
    get_font_advances, primary_font_family,
    resolve_text_run_fonts, split_project_text_clusters,
    text_has_rtl_characters, text_uses_rtl,
    is_thick_circle_shorthand, parse_project_geometry_length,
    is_canonical_project_geometry_length,
    parse_project_image_aspect_ratio,
    parse_project_opacity,
    parse_project_stroke_dasharray,
    quantize_ooxml_alpha,
    project_definition_index,
    svg_hidden_reason,
    is_stroked_zero_axis_shape,
    matrix_multiply, parse_transform_matrix, parse_transform_operations,
    transform_point, _xml_escape,
)
from .styles import (
    build_solid_fill, build_gradient_fill,
    build_fill_xml, build_stroke_xml, build_effect_xml, classify_filter_effect,
    get_element_opacity, get_fill_opacity, get_stroke_opacity,
)
from .paths import (
    PathCommand, parse_svg_path, parse_svg_points, svg_path_to_absolute,
    normalize_path_commands, path_commands_to_drawingml,
    transform_path_commands,
)



# Compatibility facade retaining public and private entry points.
from .elements_image_sources import _resolve_external_image
from .elements_image_sources import _PROJECT_IMAGE_FORMATS
from .elements_image_sources import _normalize_project_image_format
from .elements_image_sources import _valid_project_image_payload
from .elements_image_sources import _decode_data_image_uri
from .elements_image_sources import ProjectImageSource
from .elements_image_sources import _project_image_href
from .elements_image_sources import load_project_image_source
from .elements_image_sources import project_image_errors
from .elements_shape_xml import shape_display_name
from .elements_shape_xml import _wrap_shape
from .elements_shape_xml import _wrap_connector
from .elements_shape_xml import _wrap_geometry_object
from .elements_shape_xml import _imported_placeholder_xml
from .elements_shape_xml import _element_effect_xml
from .elements_shape_xml import _decode_shape_style
from .elements_shape_xml import _connector_connection_xml
from .elements_shape_xml import _claim_element_shape_id
from .elements_shape_xml import _context_transform_matrix
from .elements_shape_xml import _combined_transform_matrix
from .elements_shape_xml import _uses_full_transform
from .elements_shape_xml import _transformed_point
from .elements_shape_xml import _shape_xfrm_from_svg_rect
from .elements_shape_xml import _BEZIER_QUARTER_K
from .elements_shape_xml import PPTX_PRESET_SHAPE_TYPES
from .elements_shape_xml import _PPTX_AV_PREFIX
from .elements_shape_xml import _PPTX_GUIDE_NAME_RE
from .elements_shape_xml import _PPTX_VAL_FORMULA_RE
from .elements_preset_geometry import _parse_preset_geometry_metadata
from .elements_preset_geometry import validate_preset_geometry_metadata
from .elements_preset_geometry import complete_preset_adjustments
from .elements_preset_geometry import _build_preset_geom_from_meta
from .elements_preset_geometry import _build_preserved_custom_geom
from .elements_preset_geometry import _shape_xfrm_from_preset_frame
from .elements_preset_geometry import _pathlike_preset_xfrm
from .elements_basic_shapes import _build_round_rect_custgeom
from .elements_basic_shapes import convert_rect
from .elements_basic_shapes import _build_arc_ring_path
from .elements_basic_shapes import _is_donut_circle
from .elements_basic_shapes import convert_circle
from .elements_basic_shapes import convert_line
from .elements_path_shapes import convert_path
from .elements_path_shapes import convert_polygon
from .elements_path_shapes import convert_polyline
from .elements_path_shapes import _SERIF_WIDTH_FAMILIES
from .elements_path_shapes import _TEXTBOX_PADDING_MIN_PX
from .elements_path_shapes import _TEXTBOX_PADDING_MAX_PX
from .elements_path_shapes import _TEXTBOX_PADDING_RATIO
from .elements_path_shapes import _TEXT_WIDTH_HEADROOM_BASE
from .elements_path_shapes import _TEXT_WIDTH_HEADROOM_CAPS
from .elements_path_shapes import _SERIF_TEXT_WIDTH_HEADROOM_BASE
from .elements_path_shapes import _SERIF_TEXT_WIDTH_HEADROOM_CAPS
from .elements_path_shapes import _TEXT_BULLET_MARKERS
from .elements_path_shapes import _TEXT_BULLET_RE
from .elements_path_shapes import _INLINE_FORMULA_ATTR
from .elements_path_shapes import _INLINE_FORMULA_KEY
from .elements_text_metrics import _text_line_vertical_extent
from .elements_text_metrics import _native_text_line_frame_height
from .elements_text_metrics import _normalize_text_run_whitespace
from .elements_text_metrics import _letter_spacing_to_drawingml_spc
from .elements_text_metrics import _is_serif_face
from .elements_text_metrics import _run_script_segments
from .elements_text_metrics import _estimate_run_width_with_headroom
from .elements_text_metrics import _WIDE_FAMILY_WIDTH_FACTORS
from .elements_text_metrics import _MONOSPACE_ADVANCE_EM
from .elements_text_metrics import _run_primary_family
from .elements_text_metrics import _MONOSPACE_NAME_HINT_RE
from .elements_text_metrics import _MONOSPACE_DEFAULT_ADVANCE_EM
from .elements_text_metrics import _monospace_advance_em
from .elements_text_metrics import _family_width_factor
from .elements_text_metrics import _estimate_run_text_width
from .elements_text_metrics import validate_text_run_advances
from .elements_text_metrics import _uppercase_fraction
from .elements_text_metrics import _estimate_text_runs_width
from .elements_text_metrics import estimate_single_line_text_frame_width
from .elements_text_metrics import validate_single_line_text_run_advances
from .elements_text_metrics import _first_nonspace_run
from .elements_text_metrics import _strip_leading_chars_from_runs
from .elements_text_metrics import _take_leading_chars_from_runs
from .elements_text_metrics import _extract_text_bullet
from .elements_text_metrics import _bullet_margin_px
from .elements_text_metrics import _bullet_indent_px
from .elements_text_metrics import _build_bullet_xml
from .elements_text_metrics import _paragraph_pr_xml
from .elements_text_metrics import _estimate_bullet_line_width
from .elements_text_metrics import _textbox_padding
from .elements_text_metrics import drawingml_text_frame_width_emu
from .elements_text_metrics import _text_opacity_ratio
from .elements_text_runs import _override_run_attrs
from .elements_text_runs import _collect_tspan_runs
from .elements_text_runs import _collect_inline_runs
from .elements_text_runs import _build_text_runs
from .elements_text_runs import _build_text_fill_xml
from .elements_text_runs import _build_text_outline_xml
from .elements_text_runs import _build_run_properties_xml
from .elements_text_runs import _coalesce_text_runs
from .elements_text_runs import _text_axis_transform
from .elements_text_runs import _inline_formula_preview_run
from .elements_text_runs import _build_run_xml
from .elements_text_conversion import convert_text
from .elements_clip_geometry import _clip_commands_to_geom
from .elements_clip_geometry import _CLIP_SHAPE_TAGS
from .elements_clip_geometry import _CLIP_NON_VISUAL_ELEMENTS
from .elements_clip_geometry import _element_contract_label
from .elements_clip_geometry import _unsupported_clip_rule_properties
from .elements_clip_geometry import _effective_clip_geometry_length
from .elements_clip_geometry import _clip_preset_geometry_error
from .elements_clip_geometry import _nested_crop_clip_preset_geometry_error
from .elements_clip_geometry import _visible_clip_shapes
from .elements_clip_geometry import empty_clip_path_reason
from .elements_clip_geometry import project_clip_path_errors
from .elements_clip_geometry import _resolve_clip_geometry
from .elements_pictures import _picture_xfrm_from_rect
from .elements_pictures import _picture_xfrm_from_svg_rect
from .elements_pictures import _picture_rendered_frame_size
from .elements_pictures import _read_image_size
from .elements_pictures import _optimize_image_for_pptx
from .elements_pictures import _compute_slice_src_rect
from .elements_pictures import _resolve_image_src_rect_values
from .elements_pictures import _src_rect_xml
from .elements_pictures import _resolve_image_meet_fit
from .elements_pictures import _build_image_blip_xml
from .elements_pictures import _register_image_media
from .elements_pictures import convert_image
from .elements_pictures import convert_ellipse
from .elements_nested_crops import NestedSvgCropSpec
from .elements_nested_crops import _NESTED_CROP_OUTER_ATTRIBUTES
from .elements_nested_crops import _NESTED_CROP_IMAGE_ATTRIBUTES
from .elements_nested_crops import _DRAWINGML_PERCENTAGE_MIN
from .elements_nested_crops import _DRAWINGML_PERCENTAGE_MAX
from .elements_nested_crops import _unsupported_nested_crop_attributes
from .elements_nested_crops import parse_project_nested_svg_crop
from .elements_nested_crops import project_nested_svg_crop_errors
from .elements_nested_crops import _resolve_nested_svg_clip_geometry
from .elements_nested_crops import convert_nested_svg
