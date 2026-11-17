"""SVG element converters: rect, circle, line, path, polygon, polyline, text, image, ellipse."""

from __future__ import annotations

import base64
import binascii
import hashlib
import io
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote_to_bytes
from xml.etree import ElementTree as ET

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
    svg_image_payload_error,
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


_PROJECT_IMAGE_FORMATS = {
    'bmp': 'bmp',
    'emf': 'emf',
    'gif': 'gif',
    'jpeg': 'jpg',
    'jpg': 'jpg',
    'png': 'png',
    'svg': 'svg',
    'svg+xml': 'svg',
    'tif': 'tif',
    'tiff': 'tiff',
    'webp': 'webp',
    'wmf': 'wmf',
    'x-emf': 'emf',
    'x-wmf': 'wmf',
}
_PIL_IMAGE_FORMATS = {
    'bmp': 'BMP',
    'gif': 'GIF',
    'jpg': 'JPEG',
    'png': 'PNG',
    'tif': 'TIFF',
    'tiff': 'TIFF',
    'webp': 'WEBP',
}


@dataclass(frozen=True)
class ProjectImageSource:
    """Validated bytes and package extension for one SVG image reference."""

    img_format: str
    img_data: bytes


_BEZIER_QUARTER_K = 0.5522847498


# The hash-locked shared catalog is the single source of truth for the 187
# ECMA-376 ``ST_ShapeType`` values. Loading it here makes exporter validation
# fail closed if the catalog is missing, corrupt, or incomplete.
PPTX_PRESET_SHAPE_TYPES = frozenset(load_shape_type_values())

_PPTX_AV_PREFIX = 'data-pptx-av-'
_PPTX_GUIDE_NAME_RE = re.compile(r'[A-Za-z_][A-Za-z0-9_.-]{0,63}')
_PPTX_VAL_FORMULA_RE = re.compile(r'val[\t ]+([+-]?\d+)')
_SERIF_WIDTH_FAMILIES = {
    'book antiqua',
    'cambria',
    'fangsong',
    'garamond',
    'georgia',
    'kaiti',
    'palatino',
    'palatino linotype',
    'serif',
    'simsun',
    'songti',
    'times',
    'times new roman',
}

_TEXTBOX_PADDING_MIN_PX = 0.5
_TEXTBOX_PADDING_MAX_PX = 2.0
_TEXTBOX_PADDING_RATIO = 0.04
# Single-line auto-fit headroom interpolates between a low-caps base and an
# all-caps ceiling for each script segment of a run. The crude per-char width
# estimate undercounts capitals most, so all-caps Latin segments need the
# ceiling to keep wrap-ignoring renderers (LibreOffice) from folding. The
# serif tier follows the typeface that draws the segment (``latin`` or ``ea``
# as resolved by parse_font_family), so a CJK segment drawn by Microsoft YaHei
# never takes the Times New Roman tier of the same stack, and a short serif
# label cannot force its multiplier onto an otherwise sans-serif line. Values
# are calibrated against LibreOffice renders of all-caps bold lines, with bases
# left above mixed-case and CJK render ratios; exact ratios shift with font
# substitution, so these carry deliberate margin rather than tracking one
# environment's numbers.
_TEXT_WIDTH_HEADROOM_BASE = 1.06
_TEXT_WIDTH_HEADROOM_CAPS = 1.12
_SERIF_TEXT_WIDTH_HEADROOM_BASE = 1.12
_SERIF_TEXT_WIDTH_HEADROOM_CAPS = 1.36
_TEXT_BULLET_MARKERS = {
    '·': '•',
    '•': '•',
    '●': '●',
    '▪': '▪',
    '■': '■',
    '◆': '◆',
    '◇': '◇',
    '◦': '◦',
    '‣': '‣',
}
_TEXT_BULLET_RE = re.compile(
    r'^(?P<prefix>\s*)(?P<marker>[·•●▪■◆◇◦‣])(?P<space>\s*)'
)
_INLINE_FORMULA_ATTR = 'data-pptx-inline-formula'
_INLINE_FORMULA_KEY = '_inline_formula_latex'


_WIDE_FAMILY_WIDTH_FACTORS = {
    'arial black': (1.06, 1.25),
    'verdana': (1.02, 1.08),
}

# Monospaced faces advance every Latin glyph by one fixed em fraction, so the
# per-glyph table (tuned for proportional sans faces) undershoots them by
# 14–22%: measured 2026-09-05 at 20px, Courier New, DejaVu Sans Mono and Noto
# Sans Mono all render 0.600 em per character where the generic estimate gives
# 0.46–0.50. A monospaced run is therefore measured as characters × advance
# instead of scaled by a factor; CJK glyphs in these faces stay full-width and
# keep the generic estimate. Advances are the faces' published hmtx values.
_MONOSPACE_ADVANCE_EM = {
    'andale mono': 0.60,
    'cascadia code': 0.586,
    'cascadia mono': 0.586,
    'consolas': 0.55,
    'courier': 0.60,
    'courier new': 0.60,
    'dejavu sans mono': 0.602,
    'fira code': 0.60,
    'fira mono': 0.60,
    'hack': 0.602,
    'ibm plex mono': 0.60,
    'inconsolata': 0.50,
    'jetbrains mono': 0.60,
    'liberation mono': 0.60,
    'lucida console': 0.60,
    'menlo': 0.602,
    'monaco': 0.60,
    'monospace': 0.60,
    'noto sans mono': 0.60,
    'pt mono': 0.60,
    'roboto mono': 0.60,
    'sf mono': 0.602,
    'source code pro': 0.60,
    'ubuntu mono': 0.50,
}


_MONOSPACE_NAME_HINT_RE = re.compile(r'mono(?!type)|(?<!uni)code|courier|consol|typewriter')
_MONOSPACE_DEFAULT_ADVANCE_EM = 0.60


_CLIP_SHAPE_TAGS = frozenset({'circle', 'ellipse', 'rect', 'path', 'polygon'})
_CLIP_NON_VISUAL_ELEMENTS = frozenset({
    f'{{{SVG_NS}}}{tag}' for tag in ('desc', 'metadata', 'style', 'title')
})


@dataclass(frozen=True)
class NestedSvgCropSpec:
    """Validated transport fields for one imported cropped picture."""

    image: ET.Element
    x: float
    y: float
    width: float
    height: float
    view_box_x: float
    view_box_y: float
    view_box_width: float
    view_box_height: float
    src_l: int
    src_t: int
    src_r: int
    src_b: int


_NESTED_CROP_OUTER_ATTRIBUTES = frozenset({
    'clip-path',
    'data-pptx-crop',
    'data-pptx-editable',
    EFFECT_REASON_ATTR,
    EFFECT_STATUS_ATTR,
    NATIVE_EFFECT_ATTR,
    NATIVE_EFFECT_SHA256_ATTR,
    'data-pptx-frame',
    'data-pptx-layer',
    'data-pptx-object',
    'data-pptx-carrier',
    'data-pptx-prst',
    'data-pptx-shape-id',
    'data-pptx-shape-name',
    'data-pptx-shape-scope',
    'id',
    'overflow',
    'preserveAspectRatio',
    'transform',
    'viewBox',
    'x',
    'y',
    'width',
    'height',
})
_NESTED_CROP_IMAGE_ATTRIBUTES = frozenset({
    'clip-path',
    'href',
    f'{{{XLINK_NS}}}href',
    'opacity',
    'preserveAspectRatio',
    'x',
    'y',
    'width',
    'height',
})
_DRAWINGML_PERCENTAGE_MIN = -(2 ** 31)
_DRAWINGML_PERCENTAGE_MAX = 2 ** 31 - 1



# Stable public facade; implementation is split by conversion responsibility.
from .elements_image_sources import (
    _resolve_external_image,
    _normalize_project_image_format,
    _little_uint,
    _valid_emf_payload,
    _valid_wmf_payload,
    _valid_project_image_payload,
    _decode_data_image_uri,
    _project_image_href,
    load_project_image_source,
    project_image_errors,
)
from .elements_frames import (
    shape_display_name,
    _wrap_shape,
    _wrap_connector,
    _wrap_geometry_object,
    _imported_placeholder_xml,
    _element_effect_xml,
    _decode_shape_style,
    _connector_connection_xml,
    _claim_element_shape_id,
    _context_transform_matrix,
    _combined_transform_matrix,
    _uses_full_transform,
    _transformed_point,
    _shape_xfrm_from_svg_rect,
)
from .elements_presets import (
    _parse_preset_geometry_metadata,
    validate_preset_geometry_metadata,
    complete_preset_adjustments,
    _build_preset_geom_from_meta,
    _build_preserved_custom_geom,
    _shape_xfrm_from_preset_frame,
    _pathlike_preset_xfrm,
)
from .elements_basic_shapes import (
    _build_round_rect_custgeom,
    convert_rect,
    _build_arc_ring_path,
    _is_donut_circle,
    convert_circle,
)
from .elements_vectors import (
    convert_line,
    convert_path,
    convert_polygon,
    convert_polyline,
)
from .elements_text_measure import (
    _text_line_vertical_extent,
    _native_text_line_frame_height,
    _normalize_text_run_whitespace,
    _letter_spacing_to_drawingml_spc,
    _is_serif_face,
    _run_script_segments,
    _estimate_run_width_with_headroom,
    _run_primary_family,
    _monospace_advance_em,
    _family_width_factor,
    _estimate_run_text_width,
    validate_text_run_advances,
    _uppercase_fraction,
    _estimate_text_runs_width,
    estimate_single_line_text_frame_width,
    validate_single_line_text_run_advances,
    _first_nonspace_run,
    _strip_leading_chars_from_runs,
    _take_leading_chars_from_runs,
    _extract_text_bullet,
    _bullet_margin_px,
    _bullet_indent_px,
    _build_bullet_xml,
    _paragraph_pr_xml,
    _estimate_bullet_line_width,
    _textbox_padding,
    drawingml_text_frame_width_emu,
    _text_opacity_ratio,
)
from .elements_text_runs import (
    _override_run_attrs,
    _collect_tspan_runs,
    _collect_inline_runs,
    _build_text_runs,
    _build_text_fill_xml,
    _build_text_outline_xml,
    _build_run_properties_xml,
    _coalesce_text_runs,
    _text_axis_transform,
    _inline_formula_preview_run,
    _build_run_xml,
)
from .elements_text import (
    convert_text,
)
from .elements_clip import (
    _clip_commands_to_geom,
    _element_contract_label,
    _unsupported_clip_rule_properties,
    _effective_clip_geometry_length,
    _clip_preset_geometry_error,
    _nested_crop_clip_preset_geometry_error,
    _visible_clip_shapes,
    empty_clip_path_reason,
    project_clip_path_errors,
    _resolve_clip_geometry,
)
from .elements_image_geometry import (
    _picture_xfrm_from_rect,
    _picture_xfrm_from_svg_rect,
    _picture_rendered_frame_size,
    _read_svg_image_size,
    _read_image_size,
    _image_has_alpha,
    _prepare_raster_for_geometry,
    _has_exif_geometry_transform,
    _image_target_size,
    _visible_source_scale_floor,
    _fit_full_image_target,
    _resize_for_target,
    _encode_optimized_image,
    _optimize_image_for_pptx,
    _compute_slice_src_rect,
    _resolve_image_src_rect_values,
    _src_rect_xml,
    _resolve_image_meet_fit,
    _build_image_blip_xml,
    _register_image_media,
)
from .elements_images import (
    convert_image,
    convert_ellipse,
)
from .elements_nested_svg import (
    _unsupported_nested_crop_attributes,
    parse_project_nested_svg_crop,
    project_nested_svg_crop_errors,
    _resolve_nested_svg_clip_geometry,
    convert_nested_svg,
)

