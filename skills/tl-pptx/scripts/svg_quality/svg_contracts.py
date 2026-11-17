#!/usr/bin/env python3
"""PPT Master stateless SVG contract checks.

Validates the SVG property surface shared with the native DrawingML exporter.
Each check receives an explicit XML root or source string and appends findings
to the supplied result dictionary.

Usage:
    Import checks from ``svg_quality.svg_contracts``.

Examples:
    from svg_quality.svg_contracts import check_paint_compatibility

Dependencies:
    Standard library plus local PPT Master SVG-to-PPTX modules.
"""

import copy
import math
import re
from collections import Counter, defaultdict
from typing import Dict, List
from xml.etree import ElementTree as ET

from .xml_support import (
    XLINK_NS,
    element_label as _element_label,
    local_name as _local_name,
)

try:
    from pptx_effects import (
        EFFECT_REASON_ATTR as _EFFECT_REASON_ATTR,
        EFFECT_STATUS_ATTR as _EFFECT_STATUS_ATTR,
        project_effect_status_errors as _project_effect_status_errors,
    )
except ImportError:
    _EFFECT_REASON_ATTR = "data-pptx-effect-reason"
    _EFFECT_STATUS_ATTR = "data-pptx-effect-status"
    _project_effect_status_errors = None

try:
    from svg_to_pptx.drawingml.utils import (
        DRAWINGML_TEXT_FONT_SIZE_MAX as _DRAWINGML_TEXT_FONT_SIZE_MAX,
        DRAWINGML_TEXT_FONT_SIZE_MIN as _DRAWINGML_TEXT_FONT_SIZE_MIN,
        PROJECT_OPACITY_PROPERTIES as _OPACITY_PROPERTIES,
        PROJECT_PAINT_PROPERTIES as _PAINT_PROPERTIES,
        PROJECT_PERCENTAGE_OPACITY_PROPERTIES as _PERCENTAGE_OPACITY_PROPERTIES,
        format_project_geometry_length as _format_project_geometry_length,
        format_project_image_aspect_ratio as _format_project_image_aspect_ratio,
        format_project_opacity as _format_project_opacity,
        font_px_to_hpt as _font_px_to_hpt,
        is_canonical_project_geometry_length as _is_canonical_project_geometry_length,
        is_project_opacity_default_form as _is_project_opacity_default_form,
        is_project_paint_default_form as _is_project_paint_default_form,
        iter_project_geometry_lengths as _iter_project_geometry_lengths,
        iter_project_image_aspect_ratios as _iter_project_image_aspect_ratios,
        iter_project_opacities as _iter_project_opacities,
        iter_project_paints as _iter_project_paints,
        iter_project_stroke_styles as _iter_project_stroke_styles,
        iter_project_transforms as _iter_project_transforms,
        noncanonical_stroke_dash_numbers as _noncanonical_stroke_dash_numbers,
        noncanonical_transform_numbers as _noncanonical_transform_numbers,
        parse_inline_style as _parse_inline_style,
        parse_project_geometry_length as _parse_project_geometry_length,
        parse_project_image_aspect_ratio as _parse_project_image_aspect_ratio,
        parse_project_opacity as _parse_project_opacity,
        parse_project_paint as _parse_project_paint,
        parse_project_stroke_dasharray as _parse_project_stroke_dasharray,
        parse_project_stroke_enum as _parse_project_stroke_enum,
        parse_svg_length as _parse_export_length,
        project_definition_errors as _project_definition_errors,
        project_filter_errors as _project_filter_errors,
        project_gradient_errors as _project_gradient_errors,
        project_image_aspect_ratio_errors as _project_image_aspect_ratio_errors,
        project_mask_errors as _project_mask_errors,
        project_marker_errors as _project_marker_errors,
        project_opacity_errors as _project_opacity_errors,
        project_paint_errors as _project_paint_errors,
        project_paint_reference_errors as _project_paint_reference_errors,
        project_stroke_style_errors as _project_stroke_style_errors,
        project_transform_errors as _project_transform_errors,
    )
except ImportError:
    _DRAWINGML_TEXT_FONT_SIZE_MAX = None
    _DRAWINGML_TEXT_FONT_SIZE_MIN = None
    _OPACITY_PROPERTIES = None
    _PAINT_PROPERTIES = None
    _PERCENTAGE_OPACITY_PROPERTIES = None
    _format_project_geometry_length = None
    _format_project_image_aspect_ratio = None
    _format_project_opacity = None
    _font_px_to_hpt = None
    _is_canonical_project_geometry_length = None
    _is_project_opacity_default_form = None
    _is_project_paint_default_form = None
    _iter_project_geometry_lengths = None
    _iter_project_image_aspect_ratios = None
    _iter_project_opacities = None
    _iter_project_paints = None
    _iter_project_stroke_styles = None
    _iter_project_transforms = None
    _noncanonical_stroke_dash_numbers = None
    _noncanonical_transform_numbers = None
    _parse_inline_style = None
    _parse_project_geometry_length = None
    _parse_project_image_aspect_ratio = None
    _parse_project_opacity = None
    _parse_project_paint = None
    _parse_project_stroke_dasharray = None
    _parse_project_stroke_enum = None
    _parse_export_length = None
    _project_definition_errors = None
    _project_filter_errors = None
    _project_gradient_errors = None
    _project_image_aspect_ratio_errors = None
    _project_mask_errors = None
    _project_marker_errors = None
    _project_opacity_errors = None
    _project_paint_errors = None
    _project_paint_reference_errors = None
    _project_stroke_style_errors = None
    _project_transform_errors = None

try:
    from svg_to_pptx.drawingml.paths import (
        iter_project_freeform_geometry as _iter_project_freeform_geometry,
        noncanonical_path_numbers as _noncanonical_path_numbers,
        noncanonical_points_numbers as _noncanonical_points_numbers,
        project_gradient_geometry_errors as _project_gradient_geometry_errors,
    )
except ImportError:
    _iter_project_freeform_geometry = None
    _noncanonical_path_numbers = None
    _noncanonical_points_numbers = None
    _project_gradient_geometry_errors = None

try:
    from svg_to_pptx.drawingml.elements import (
        project_clip_path_errors as _project_clip_path_errors,
        project_nested_svg_crop_errors as _project_nested_svg_crop_errors,
    )
except ImportError:
    _project_clip_path_errors = None
    _project_nested_svg_crop_errors = None

try:
    from svg_to_pptx.drawingml.text_properties import (
        project_text_property_diagnostics as _project_text_property_diagnostics,
    )
except ImportError:
    _project_text_property_diagnostics = None

try:
    from svg_to_pptx.geometry_properties import (
        materialize_inline_geometry_properties as _materialize_inline_geometry_properties,
        validate_inline_geometry_properties as _validate_inline_geometry_properties,
    )
except ImportError:
    _materialize_inline_geometry_properties = None
    _validate_inline_geometry_properties = None

try:
    from svg_to_pptx.use_expander import (
        UseExpansionError as _UseExpansionError,
        expand_local_use_references as _expand_local_use_references,
        validate_local_use_references as _validate_local_use_references,
    )
except ImportError:
    _UseExpansionError = None
    _expand_local_use_references = None
    _validate_local_use_references = None

_CANONICAL_PAINT_ALPHA_PROPERTY = {
    "fill": "fill-opacity",
    "stroke": "stroke-opacity",
    "stop-color": "stop-opacity",
    "flood-color": "flood-opacity",
}
_SUPPORTED_INLINE_STYLE_PROPERTIES = frozenset({
    "cx", "cy", "display", "fill", "fill-opacity", "filter", "flood-color",
    "flood-opacity", "font-family", "font-size", "font-style", "font-weight",
    "height", "letter-spacing", "opacity", "r", "rx", "ry",
    "shape-rendering", "stop-color", "stop-opacity", "stroke",
    "stroke-dasharray", "stroke-linecap", "stroke-linejoin", "stroke-opacity",
    "stroke-width", "text-anchor", "text-decoration", "vector-effect",
    "visibility", "width", "x", "y",
})
_BAKE_REQUIRED_VISUAL_PROPERTIES = frozenset({
    "backdrop-filter",
    "isolation",
    "mix-blend-mode",
})
_SHARED_FAIL_CLOSED_STYLE_PROPERTIES = frozenset({"mask"})


from .svg_contracts_properties import (
    check_forbidden_elements,
    check_paint_compatibility,
    check_reference_spelling,
    check_opacity_values,
    check_authoring_property_contract,
    check_text_property_contract,
)
from .svg_contracts_geometry import (
    check_definition_contract,
    check_paint_reference_contract,
    check_marker_contract,
    check_clip_path_contract,
    check_mask_contract,
    check_filter_effects,
    check_imported_effect_status,
    check_gradient_interfaces,
    check_geometry_length_values,
    check_stroke_style_values,
    check_image_aspect_ratio_values,
    check_nested_svg_crop_contract,
    check_freeform_geometry_values,
    check_transform_values,
    check_font_size_values,
)
