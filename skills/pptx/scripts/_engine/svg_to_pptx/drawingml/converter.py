# Adapted from PPT Master 5e8856b (MIT); see assets/licenses/ppt_master.txt.
"""Core SVG -> DrawingML dispatcher, group handling, and main entry point."""

from __future__ import annotations

import base64
import binascii
import copy
import hashlib
import math
import re
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from native_payloads import NativePayloadError, hydrate_native_payload_refs
from pptx_ooxml.package import PackagePartRegistry
from hyperlink_contract import (
    SHAPE_HYPERLINK_ATTR,
    project_hyperlink_errors,
    svg_hyperlink_href,
)
from pptx_shapes import (
    DRAWINGML_NS,
    UnknownPresetShapeError,
    has_relationship_attributes,
    resolve_preset_preview_hash,
    svg_preset_preview_fingerprint,
    svg_text_fingerprint,
    validate_ooxml_xfrm,
)
from pptx_effects import project_effect_status_errors, txbody_has_run_effects
from pptx_to_svg.preset_authoring import (
    materialize_compact_authored_preset_tree,
    validate_authored_preset_tree,
)
from resource_paths import icon_dir_for_project
from svg_authoring_view import (
    SEMANTIC_OBJECT_ATTRIBUTE,
    SEMANTIC_SHAPE_KIND,
    semantic_shape_text_component,
)
from svg_compatibility import normalize_single_child_group_filters

from .context import (
    TEXT_FLOW_PRESERVE,
    TEXT_FLOW_SPLIT,
    ConvertContext,
    ShapeResult,
    resolve_text_flow,
)
from .hyperlinks import apply_shape_hyperlink
from .paths import (
    project_freeform_geometry_errors,
    project_gradient_geometry_errors,
)
from .theme_colors import ThemeColorSpec
from .theme_fonts import ThemeFontSpec
from .text_properties import (
    materialize_project_text_metrics,
    project_text_property_errors,
    resolve_project_font_sizes,
    resolve_project_letter_spacings,
)
from .utils import (
    EMU_PER_PX,
    SVG_NS,
    _extract_inheritable_styles,
    _get_attr,
    _is_unit_axis_reflection,
    _xml_escape,
    is_picture_effect_carrier,
    parse_svg_length,
    parse_transform_operations,
    parse_transform_matrix,
    project_definition_errors,
    project_definition_index,
    project_filter_errors,
    project_geometry_length_errors,
    project_gradient_errors,
    project_image_aspect_ratio_errors,
    project_mask_errors,
    project_marker_errors,
    project_opacity_errors,
    project_paint_errors,
    project_paint_reference_errors,
    project_stroke_style_errors,
    project_transform_errors,
    resolve_url_id,
    svg_hidden_reason,
    supports_full_project_transform,
    validate_dml_shape_matrix,
)
from .styles import (
    build_effect_xml, build_fill_xml,
    get_element_opacity, get_fill_opacity, get_stroke_opacity,
)
from .elements import (
    complete_preset_adjustments,
    shape_display_name,
    empty_clip_path_reason,
    convert_rect, convert_circle, convert_ellipse,
    convert_line, convert_path,
    convert_polygon, convert_polyline,
    convert_text, convert_image, convert_nested_svg,
    project_clip_path_errors,
    project_image_errors,
    project_nested_svg_crop_errors,
)
from ..animation_config import (
    anchor_wrapped_group,
    effective_top_level,
    is_chrome_id,
    usable_animation_group_id,
)
from ..canvas_contract import (
    CanvasContractError,
    parse_project_svg_root,
    parse_project_viewbox,
)
from ..native_objects import (
    INLINE_FORMULA_ATTR,
    NativeMarkerAttributeError,
    convert_native_object,
    inline_formula_marker_errors,
    native_metadata_payload_matches,
    native_replacement_kind,
    native_marker_transform,
    require_fresh_native_fallback,
    reserve_source_chart_parts,
    snapshot_native_fallback_freshness,
)
from ..native_objects.formula import formula_fallback_xml
from ..native_objects.inline_formula import wrap_inline_formula_shape
from ..native_objects.marker_status import native_marker_status_errors
from ..semantic_markers import is_static_page_frame



# Compatibility facade retaining public and private entry points.
from .converter_validation import SvgNativeConversionError
from .converter_validation import _hydrate_native_payloads
from .converter_validation import _require_native_marker_attributes
from .converter_validation import _require_inline_formula_markers
from .converter_validation import _require_project_hyperlinks
from .converter_validation import _native_replacement_enabled
from .converter_validation import _contains_enabled_native_replacement
from .converter_validation import _require_project_freeform_geometry
from .converter_validation import _require_project_nested_svg_crops
from .converter_validation import _require_project_clip_paths
from .converter_validation import _require_project_images
from .converter_validation import _require_project_transforms
from .converter_validation import _require_project_stroke_styles
from .converter_validation import _require_project_image_aspect_ratios
from .converter_validation import _require_project_opacities
from .converter_validation import _require_project_paints
from .converter_validation import _require_project_masks
from .converter_validation import _require_project_definitions
from .converter_validation import _require_project_paint_references
from .converter_validation import _require_project_line_end_markers
from .converter_validation import _require_project_gradients
from .converter_validation import _require_project_filters
from .converter_validation import _require_project_effect_status
from .converter_validation import _require_project_text_properties
from .converter_text_metadata import parse_transform
from .converter_text_metadata import _root_viewport_size
from .converter_text_metadata import _extract_rotate_pivot
from .converter_text_metadata import _txbody_metadata
from .converter_text_metadata import _placeholder_sp_pr_metadata
from .converter_text_metadata import _TXBODY_UNCHANGED_ATTR
from .converter_text_metadata import _PREVIEW_UNCHANGED_ATTR
from .converter_text_metadata import _mark_unchanged_txbody_groups
from .converter_text_metadata import _mark_unchanged_preset_previews
from .converter_text_metadata import _require_unchanged_preset_preview
from .converter_text_metadata import _decode_unchanged_txbody
from .converter_text_metadata import _append_shape_text
from .converter_text_metadata import _A14_NS
from .converter_text_metadata import _A14_MATH_TAG
from .converter_text_metadata import _TXBODY_PREFIXES
from .converter_text_metadata import _txbody_has_office_math
from .converter_text_metadata import _with_office_math_fallback
from .converter_text_metadata import _make_semantic_geometry_carrier_visible
from .converter_text_metadata import _semantic_shape_text_body
from .converter_text_metadata import _convert_semantic_shape
from .converter_text_metadata import _restore_placeholder_sp_pr
from .converter_text_metadata import _complete_placeholder_preset_adjustments
from .converter_text_metadata import preserved_native_text_body
from .converter_groups import _is_import_text_flip_compensation
from .converter_groups import _roundtrip_graphic_frame
from .converter_groups import convert_g
from .converter_groups import convert_a
from .converter_dispatch import _NON_VISUAL_TAGS
from .converter_dispatch import _CONVERTERS
from .converter_dispatch import _SUPPORTED_VISUAL_CHILD_TAGS
from .converter_dispatch import _parse_svg_canvas
from .converter_dispatch import _is_full_canvas_rect
from .converter_dispatch import _background_xml_from_rect
from .converter_dispatch import _extract_background_candidate
from .converter_dispatch import collect_defs
from .converter_dispatch import _build_source_shape_id_map
from .converter_dispatch import _geometry_trace_metadata
from .converter_dispatch import collect_hidden_visuals
from .converter_dispatch import convert_element
from .converter_dispatch import _local_tag
from .converter_dispatch import collect_unsupported_visuals
from .converter_slide import convert_svg_to_slide_shapes
