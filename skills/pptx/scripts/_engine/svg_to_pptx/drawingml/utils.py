# Adapted from PPT Master 5e8856b (MIT); see assets/licenses/ppt_master.txt.
"""Coordinate, transform, color, and font helpers for DrawingML conversion.

See references/shared-standards-core.md §2.1 for project geometry and
references/svg-effects.md §§6.2–6.8 for paint, image-fit, line-presentation,
and transform authoring contracts.
"""

from __future__ import annotations

import colorsys
import json
import math
import re
import unicodedata
from collections import Counter
from collections.abc import Iterator
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from xml.etree import ElementTree as ET

from pptx_gradients import (
    NATIVE_GRADIENT_ATTR,
    NATIVE_GRADIENT_PREVIEW_SHA256_ATTR,
    NATIVE_GRADIENT_SHA256_ATTR,
    preserved_native_gradient_xml,
)

from pptx_shapes import (
    OOXML_COORDINATE_MAX,
    resolve_preset_preview_hash,
    svg_preset_preview_fingerprint,
    validate_ooxml_xfrm,
)
from language_tags import language_base, language_uses_rtl, normalize_language_tag

from .context import AffineMatrix, ConvertContext, IDENTITY_MATRIX

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


# Compatibility facade retaining public and private entry points.
from .utils_constants import SVG_NS
from .utils_constants import XLINK_NS
from .utils_constants import EMU_PER_PX
from .utils_constants import FONT_PX_TO_HUNDREDTHS_PT
from .utils_constants import DRAWINGML_TEXT_FONT_SIZE_MIN
from .utils_constants import DRAWINGML_TEXT_FONT_SIZE_MAX
from .utils_constants import ANGLE_UNIT
from .utils_constants import INHERITABLE_ATTRS
from .utils_constants import EA_FONTS
from .utils_constants import SYSTEM_FONTS
from .utils_constants import FONT_FALLBACK_WIN
from .utils_constants import GENERIC_FONT_MAP
from .utils_constants import _FONT_CANONICAL_NAMES
from .utils_constants import _SERIF_LATIN
from .utils_constants import PPT_SAFE_FONTS
from .utils_constants import DASH_PRESETS
from .utils_constants import PROJECT_STROKE_ENUM_VALUES
from .utils_constants import PROJECT_IMAGE_ASPECT_RATIO_ANCHORS
from .utils_constants import PROJECT_IMAGE_ASPECT_RATIO_MODES
from .utils_constants import PROJECT_OPACITY_PROPERTIES
from .utils_constants import PROJECT_PERCENTAGE_OPACITY_PROPERTIES
from .utils_constants import PROJECT_PAINT_PROPERTIES
from .utils_constants import PROJECT_REFERENCE_PAINT_PROPERTIES
from .utils_constants import PROJECT_DEFINITION_TAGS
from .utils_constants import PROJECT_GRADIENT_TAGS
from .utils_constants import PROJECT_TEXT_IMAGE_FILL_ATTR
from .utils_constants import PROJECT_TEXT_IMAGE_FILL_MODES
from .utils_constants import PROJECT_LINEAR_GRADIENT_COORDINATE_MIN
from .utils_constants import PROJECT_LINEAR_GRADIENT_COORDINATE_MAX
from .utils_constants import PROJECT_RADIAL_FOCUS_TOLERANCE
from .utils_constants import PROJECT_FILTER_PRIMITIVES
from .utils_constants import PROJECT_FILTER_EFFECT_PRIMITIVES
from .utils_constants import PROJECT_FILTER_PUBLIC_TARGETS
from .utils_constants import _PROJECT_MARKER_NUMBER_TOKEN
from .utils_constants import _PROJECT_MARKER_POINT_TOKEN
from .utils_constants import _PROJECT_MARKER_TRIANGLE_PATH_RE
from .utils_constants import _PROJECT_MARKER_DIAMOND_PATH_RE
from .utils_constants import _PROJECT_MARKER_ARROW_PATH_RE
from .utils_constants import _PROJECT_MARKER_COMMAND_POINT_RE
from .utils_constants import PROJECT_NON_VISUAL_DEFINITION_CHILD_TAGS
from .utils_constants import THICK_CIRCLE_COVERAGE_TOLERANCE
from .utils_lengths import px_to_emu
from .utils_lengths import font_px_to_hpt
from .utils_lengths import _f
from .utils_lengths import _LENGTH_RE
from .utils_lengths import _CANONICAL_PROJECT_GEOMETRY_LENGTH_RE
from .utils_lengths import _PROJECT_STROKE_DASH_NUMBER_PATTERN
from .utils_lengths import _PROJECT_STROKE_DASH_NUMBER_RE
from .utils_lengths import _PROJECT_STROKE_DASHARRAY_RE
from .utils_lengths import PROJECT_GEOMETRY_LENGTH_ATTRIBUTES
from .utils_lengths import PROJECT_NON_NEGATIVE_LENGTH_ATTRIBUTES
from .utils_lengths import _parse_svg_length_parts
from .utils_lengths import parse_svg_length
from .utils_lengths import parse_project_geometry_length
from .utils_lengths import is_canonical_project_geometry_length
from .utils_lengths import format_project_geometry_length
from .utils_lengths import parse_project_opacity
from .utils_lengths import is_project_opacity_default_form
from .utils_lengths import format_project_opacity
from .utils_lengths import parse_project_image_aspect_ratio
from .utils_lengths import format_project_image_aspect_ratio
from .utils_lengths import _parse_project_stroke_dasharray
from .utils_lengths import parse_project_stroke_dasharray
from .utils_lengths import noncanonical_stroke_dash_numbers
from .utils_lengths import parse_project_stroke_enum
from .utils_lengths import is_thick_circle_shorthand
from .utils_lengths import svg_length_x
from .utils_lengths import svg_length_y
from .utils_lengths import svg_length_size
from .utils_transforms import _TRANSFORM_NUMBER_PATTERN
from .utils_transforms import _TRANSFORM_NUMBER_RE
from .utils_transforms import _CANONICAL_TRANSFORM_NUMBER_RE
from .utils_transforms import _TRANSFORM_OPERATION_RE
from .utils_transforms import _TRANSFORM_WHITESPACE_RE
from .utils_transforms import _TRANSFORM_SEPARATOR_PATTERN
from .utils_transforms import _TRANSFORM_SEPARATOR_RE
from .utils_transforms import _TRANSFORM_ARGUMENTS_RE
from .utils_transforms import _TRANSFORM_ARITIES
from .utils_transforms import _FULL_TRANSFORM_TAGS
from .utils_transforms import _TRANSFORM_CONTAINER_TAGS
from .utils_transforms import _TRANSFORM_DEFINITION_BOUNDARIES
from .utils_transforms import _NON_VISUAL_TRANSFORM_CHILD_TAGS
from .utils_transforms import matrix_multiply
from .utils_transforms import _translate_matrix
from .utils_transforms import _scale_matrix
from .utils_transforms import _rotate_matrix
from .utils_transforms import _parse_transform_operations
from .utils_transforms import parse_transform_operations
from .utils_transforms import noncanonical_transform_numbers
from .utils_transforms import _transform_operations_matrix
from .utils_transforms import parse_transform_matrix
from .utils_transforms import transform_point
from .utils_transforms import validate_dml_shape_matrix
from .utils_transforms import _svg_element_tag
from .utils_transforms import _transform_element_label
from .utils_transforms import _visual_transform_children
from .utils_transforms import _iter_visual_transform_tree
from .utils_transforms import _TRANSFORM_ARC_STYLE_ATTRS
from .utils_transforms import _transform_arc_styles
from .utils_transforms import _is_project_thick_circle
from .utils_transforms import supports_full_project_transform
from .utils_transforms import iter_project_transforms
from .utils_transforms import _has_positive_rounding
from .utils_transforms import _contains_rounded_rect
from .utils_transforms import _contains_native_marker
from .utils_transforms import _project_thick_circle_ids
from .utils_stroke_transforms import _PROJECT_STROKE_STYLE_ATTRIBUTES
from .utils_stroke_transforms import iter_project_stroke_styles
from .utils_stroke_transforms import project_stroke_style_errors
from .utils_stroke_transforms import _contains_thick_circle
from .utils_stroke_transforms import _is_unit_axis_reflection
from .utils_stroke_transforms import _transform_semantic_error
from .utils_stroke_transforms import project_transform_errors
from .utils_stroke_transforms import rect_to_dml_xfrm
from .utils_stroke_transforms import _extract_inheritable_styles
from .utils_stroke_transforms import _get_attr
from .utils_stroke_transforms import ctx_x
from .utils_stroke_transforms import ctx_y
from .utils_stroke_transforms import ctx_w
from .utils_stroke_transforms import ctx_h
from .utils_paint_values import _CSS_NAMED_COLORS
from .utils_paint_values import parse_inline_style
from .utils_paint_values import svg_hidden_reason
from .utils_paint_values import is_stroked_zero_axis_shape
from .utils_paint_values import iter_project_geometry_lengths
from .utils_paint_values import project_geometry_length_errors
from .utils_paint_values import iter_project_image_aspect_ratios
from .utils_paint_values import project_image_aspect_ratio_errors
from .utils_paint_values import iter_project_opacities
from .utils_paint_values import project_opacity_errors
from .utils_paint_values import _finite_float
from .utils_paint_values import _parse_color_channel
from .utils_paint_values import _parse_alpha_channel
from .utils_paint_values import parse_opacity
from .utils_paint_values import quantize_ooxml_unit_ratio
from .utils_paint_values import quantize_ooxml_alpha
from .utils_paint_values import _functional_color_parts
from .utils_paint_values import _parse_hue_degrees
from .utils_paint_values import _parse_percentage
from .utils_paint_values import parse_svg_color
from .utils_paint_values import parse_project_paint
from .utils_paint_values import is_project_paint_default_form
from .utils_paint_values import iter_project_paints
from .utils_paint_values import project_paint_errors
from .utils_markers import project_definition_index
from .utils_markers import project_definition_errors
from .utils_markers import _project_marker_polygon_points
from .utils_markers import _project_marker_path_points
from .utils_markers import _project_marker_cross
from .utils_markers import _project_marker_segments_cross
from .utils_markers import _project_marker_quadrilateral_type
from .utils_markers import classify_project_marker_shape
from .utils_markers import _project_effective_presentation_value
from .utils_markers import project_marker_errors
from .utils_gradients import resolve_project_text_image_fill
from .utils_gradients import project_paint_reference_errors
from .utils_gradients import project_mask_errors
from .utils_gradients import parse_project_gradient_ratio
from .utils_gradients import parse_project_linear_gradient_coordinate
from .utils_gradients import is_project_radial_focus_point
from .utils_gradients import project_gradient_errors
from .utils_filters import parse_project_filter_params
from .utils_filters import project_filter_drawingml_coordinates
from .utils_filters import project_filter_errors
from .utils_filters import _is_compact_authored_preset_filter_target
from .utils_filters import _is_imported_preset_preview_filter_target
from .utils_filters import is_picture_effect_carrier
from .utils_fonts import parse_hex_color
from .utils_fonts import combine_opacity
from .utils_fonts import parse_stop_style
from .utils_fonts import resolve_url_id
from .utils_fonts import get_effective_filter_id
from .utils_fonts import _EA_DEFAULTS_BY_LANGUAGE
from .utils_fonts import _JA_FONT_FALLBACK_WIN
from .utils_fonts import _language_is
from .utils_fonts import _ea_default
from .utils_fonts import parse_font_family
from .utils_fonts import unsafe_exported_font_faces
from .utils_fonts import _is_han_char
from .utils_fonts import _is_hiragana_char
from .utils_fonts import _is_katakana_char
from .utils_fonts import _is_hangul_char
from .utils_fonts import is_cjk_char
from .utils_fonts import _contains_codepoint_range
from .utils_fonts import _explicit_language_script
from .utils_fonts import _default_language_for_script
from .utils_fonts import text_has_rtl_characters
from .utils_fonts import text_uses_rtl
from .utils_fonts import detect_text_lang
from .utils_text_widths import _NON_LATIN_SCRIPT_BASES
from .utils_text_widths import _is_grapheme_extend
from .utils_text_widths import _is_regional_indicator
from .utils_text_widths import _is_virama
from .utils_text_widths import _is_emoji_base
from .utils_text_widths import _unicode_script_key
from .utils_text_widths import _virama_script_key
from .utils_text_widths import split_project_text_clusters
from .utils_text_widths import resolve_text_run_fonts
from .utils_text_widths import _FIXED_SPACE_EMS
from .utils_text_widths import _UNDECOMPOSED_BASE_LETTERS
from .utils_text_widths import _base_letter
from .utils_text_widths import _estimate_character_width
from .utils_text_widths import _estimate_grapheme_width
from .utils_text_widths import _FONT_ADVANCES_CACHE
from .utils_text_widths import primary_font_family
from .utils_text_widths import get_font_advances
from .utils_text_widths import estimate_text_cluster_widths
from .utils_text_widths import estimate_text_width
from .utils_text_widths import _xml_escape
