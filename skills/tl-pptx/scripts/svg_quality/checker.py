#!/usr/bin/env python3
"""PPT Master SVG quality-check implementation.

Owns SVG, project-contract, template, and report validation. The stable CLI and
compatibility import surface remain in ``scripts/svg_quality_checker.py``.

Usage:
    Import through ``svg_quality_checker`` or invoke the stable script.

Examples:
    from svg_quality_checker import SVGQualityChecker

Dependencies:
    Standard library plus local PPT Master validation modules.
"""

from __future__ import annotations

import copy
import hashlib
import html
import json
import math
import re
from pathlib import Path
from collections import Counter, defaultdict
from typing import Dict, List, Tuple
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree as ET

from native_payloads import NativePayloadError, hydrate_native_payload_refs
from pptx_workspace import (
    NATIVE_STRUCTURE_PATH,
    SOURCE_PPTX_PATH,
)
from slide_roster import discover_slide_svgs
from svg_authoring_contract import canonical_authoring_errors
from svg_authoring_view import (
    SEMANTIC_OBJECT_ATTRIBUTE,
    SEMANTIC_SHAPE_KIND,
    semantic_shape_text_component,
)

from . import svg_contracts
from .xml_support import (
    SVG_NS,
    XLINK_NS,
    element_label as _element_label,
    local_name as _local_name,
)

try:
    from project_utils import (
        CANVAS_FORMATS,
        validate_communication_trace,
        validate_outline_hyperlinks,
        validate_outline_roster,
    )
except ImportError:
    print("Warning: Unable to import project_utils")
    CANVAS_FORMATS = {}
    validate_communication_trace = None
    validate_outline_hyperlinks = None
    validate_outline_roster = None

from svg_to_pptx.canvas_contract import (
    CanvasContractError,
    parse_project_svg_root,
    parse_project_viewbox,
)

try:
    from project_management.project_specs import (
        parse_spec_lock as _parse_spec_lock,
        parse_spec_lock_image_value as _parse_spec_lock_image_value,
    )
except ImportError:
    _parse_spec_lock = None  # spec_lock anchor comparison will be skipped
    _parse_spec_lock_image_value = None

try:
    from svg_to_pptx.animation_config import (
        effective_top_level as _effective_top_level,
        load_animation_config as _load_animation_config,
        usable_animation_group_id as _usable_animation_group_id,
        validate_animation_config as _validate_animation_config,
        validate_animation_config_errors as _validate_animation_config_errors,
        validate_transition_config as _validate_transition_config,
    )
except ImportError as exc:
    _effective_top_level = None
    _load_animation_config = None
    _validate_animation_config = None
    _validate_animation_config_errors = None
    _validate_transition_config = None
    _animation_config_import_error = str(exc)

    def _usable_animation_group_id(raw: str | None) -> str | None:
        return raw if raw and raw.strip() else None
else:
    _animation_config_import_error = None

try:
    from svg_to_pptx.drawingml.utils import (
        IDENTITY_MATRIX as _IDENTITY_MATRIX,
        PROJECT_PAINT_PROPERTIES as _PAINT_PROPERTIES,
        PROJECT_TEXT_IMAGE_FILL_ATTR as _TEXT_IMAGE_FILL_ATTR,
        detect_text_lang as _detect_text_lang,
        is_cjk_char as _is_cjk_char,
        matrix_multiply as _matrix_multiply,
        parse_inline_style as _parse_inline_style,
        parse_project_geometry_length as _parse_project_geometry_length,
        parse_project_image_aspect_ratio as _parse_project_image_aspect_ratio,
        parse_project_opacity as _parse_project_opacity,
        parse_svg_color as _parse_export_color,
        parse_svg_length as _parse_svg_length,
        parse_transform_matrix as _parse_transform_matrix,
        project_definition_index as _project_definition_index,
        project_mask_errors as _project_mask_errors,
        rect_to_dml_xfrm as _rect_to_dml_xfrm,
        split_project_text_clusters as _split_project_text_clusters,
        svg_hidden_reason as _svg_hidden_reason,
        transform_point as _transform_point,
        unsafe_exported_font_faces as _unsafe_exported_font_faces,
        validate_dml_shape_matrix as _validate_dml_shape_matrix,
    )
except ImportError:
    _IDENTITY_MATRIX = None
    _PAINT_PROPERTIES = None
    _TEXT_IMAGE_FILL_ATTR = 'data-pptx-text-image-fill'
    _detect_text_lang = None
    _is_cjk_char = None
    _matrix_multiply = None
    _parse_inline_style = None
    _parse_project_geometry_length = None
    _parse_project_image_aspect_ratio = None
    _parse_project_opacity = None
    _parse_export_color = None
    _parse_svg_length = None
    _parse_transform_matrix = None
    _project_definition_index = None
    _project_mask_errors = None
    _rect_to_dml_xfrm = None
    _split_project_text_clusters = None
    _svg_hidden_reason = None
    _transform_point = None
    _unsafe_exported_font_faces = None
    _validate_dml_shape_matrix = None

try:
    from hyperlink_contract import (
        SHAPE_HYPERLINK_ATTR as _SHAPE_HYPERLINK_ATTR,
        project_hyperlink_errors as _project_hyperlink_errors,
    )
except ImportError:
    _SHAPE_HYPERLINK_ATTR = 'data-pptx-shape-hyperlink'
    _project_hyperlink_errors = None

try:
    from svg_to_pptx.drawingml.converter import (
        SvgNativeConversionError as _SvgNativeConversionError,
        collect_hidden_visuals as _collect_hidden_visuals,
        collect_unsupported_visuals as _collect_unsupported_visuals,
        preserved_native_text_body as _preserved_native_text_body,
    )
except ImportError:
    _SvgNativeConversionError = None
    _collect_hidden_visuals = None
    _collect_unsupported_visuals = None
    _preserved_native_text_body = None

try:
    from svg_to_pptx.drawingml.styles import parse_pattern_colors as _parse_pattern_colors
except ImportError:
    _parse_pattern_colors = None

try:
    from svg_to_pptx.drawingml.context import ConvertContext as _ConvertContext
    from svg_to_pptx.drawingml.elements import (
        drawingml_text_frame_width_emu as _drawingml_text_frame_width_emu,
        empty_clip_path_reason as _empty_clip_path_reason,
        estimate_single_line_text_frame_width as _estimate_single_line_text_frame_width,
        project_image_errors as _project_image_errors,
        validate_single_line_text_run_advances as _validate_single_line_text_run_advances,
        validate_preset_geometry_metadata as _validate_preset_geometry_metadata,
    )
except ImportError:
    _ConvertContext = None
    _drawingml_text_frame_width_emu = None
    _empty_clip_path_reason = None
    _estimate_single_line_text_frame_width = None
    _project_image_errors = None
    _validate_single_line_text_run_advances = None
    _validate_preset_geometry_metadata = None

try:
    from svg_to_pptx.drawingml.text_properties import (
        normalize_project_text_segments as _normalize_project_text_segments,
        parse_project_font_weight as _parse_project_font_weight,
        parse_project_text_anchor as _parse_project_text_anchor,
        resolve_project_xml_space as _resolve_project_xml_space,
        resolve_project_font_sizes as _resolve_project_font_sizes,
        resolve_project_letter_spacings as _resolve_project_letter_spacings,
    )
except ImportError:
    _normalize_project_text_segments = None
    _parse_project_font_weight = None
    _parse_project_text_anchor = None
    _resolve_project_xml_space = None
    _resolve_project_font_sizes = None
    _resolve_project_letter_spacings = None

try:
    from pptx_to_svg.preset_authoring import (
        AUTHORING_ATTR as _AUTHORING_ATTR,
        authored_preset_encoding as _authored_preset_encoding,
        validate_authored_preset_group as _validate_authored_preset_group,
        validate_authored_preset_tree as _validate_authored_preset_tree,
    )
except ImportError:
    _AUTHORING_ATTR = 'data-pptx-authoring'
    _authored_preset_encoding = None
    _validate_authored_preset_group = None
    _validate_authored_preset_tree = None

try:
    from pptx_shapes import (
        CONNECTOR_PRESET_TYPES as _CONNECTOR_PRESET_TYPES,
        resolve_preset_preview_hash as _resolve_preset_preview_hash,
        svg_preset_preview_fingerprint as _svg_preset_preview_fingerprint,
    )
except ImportError:
    _CONNECTOR_PRESET_TYPES = frozenset()
    _resolve_preset_preview_hash = None
    _svg_preset_preview_fingerprint = None

try:
    from svg_to_pptx.native_objects import (
        validate_native_object_marker as _validate_native_object_marker,
    )
except ImportError:
    _validate_native_object_marker = None

try:
    from svg_to_pptx.native_objects import (
        validate_native_object_marker_with_warnings as _validate_native_object_marker_with_warnings,
    )
except ImportError:
    _validate_native_object_marker_with_warnings = None

try:
    from svg_to_pptx.native_objects import (
        native_object_marker_warnings as _native_object_marker_warnings,
    )
except ImportError:
    _native_object_marker_warnings = None

try:
    from svg_to_pptx.native_objects import (
        INLINE_FORMULA_ATTR as _INLINE_FORMULA_ATTR,
        estimate_inline_formula_vertical_extent as _estimate_inline_formula_vertical_extent,
        native_fallback_kind as _native_fallback_kind,
        inline_formula_marker_errors as _inline_formula_marker_errors,
        native_marker_legacy_warnings as _native_marker_legacy_warnings,
        native_replacement_kind as _native_replacement_kind,
        native_replacement_status as _native_replacement_status,
        require_fresh_native_fallback as _require_fresh_native_fallback,
    )
except ImportError:
    _INLINE_FORMULA_ATTR = 'data-pptx-inline-formula'
    _estimate_inline_formula_vertical_extent = None
    _native_fallback_kind = None
    _inline_formula_marker_errors = None
    _native_marker_legacy_warnings = None
    _native_replacement_kind = None
    _native_replacement_status = None
    _require_fresh_native_fallback = None

try:
    from svg_to_pptx.native_objects.marker_status import (
        native_marker_release_block_reason as _native_marker_release_block_reason,
        native_marker_status_errors as _native_marker_status_errors,
    )
except ImportError:
    _native_marker_release_block_reason = None
    _native_marker_status_errors = None

try:
    from svg_to_pptx.semantic_markers import (
        SEMANTIC_ATTRS as _SEMANTIC_ATTRS,
        STRUCTURAL_ROLES as _STRUCTURAL_ROLES,
        is_static_page_frame as _is_static_page_frame,
        validate_semantic_markers as _validate_semantic_markers,
    )
except ImportError:
    _SEMANTIC_ATTRS = frozenset({
        'data-pptx-page-role',
        'data-pptx-role',
    })
    _STRUCTURAL_ROLES = frozenset({
        'background',
        'chrome',
        'decoration',
        'footer',
        'header',
        'logo',
        'page-number',
        'watermark',
    })
    _is_static_page_frame = None
    _validate_semantic_markers = None

try:
    from svg_to_pptx.use_expander import (
        UseExpansionError as _UseExpansionError,
        expand_local_use_references as _expand_local_use_references,
    )
except ImportError:
    _UseExpansionError = None
    _expand_local_use_references = None

try:
    from svg_to_pptx.tspan_flattener import (
        classify_paragraph_block as _classify_paragraph_block,
        flatten_positional_tspans as _flatten_positional_tspans,
        nested_positional_tspan_errors as _nested_positional_tspan_errors,
    )
except ImportError:
    _classify_paragraph_block = None
    _flatten_positional_tspans = None
    _nested_positional_tspan_errors = None

try:
    from svg_to_pptx.pptx_package.template_structure import (
        TemplateStructureError as _TemplateStructureError,
        _is_authored_preset_atom as _is_authored_preset_atom,
        load_pptx_structure_lock as _load_pptx_structure_lock,
        parse_optional_layout_slides as _parse_optional_layout_slides,
        parse_template_slide as _parse_template_structure_slide,
        parse_template_slides as _parse_template_structure_slides,
        structured_layout_definition_files as _structured_layout_definition_files,
        _structure_subtree_signature as _structure_subtree_signature,
        template_lock_errors as _template_lock_errors,
        template_prototype_errors as _template_prototype_errors,
        validate_template_svg as _validate_template_structure_svg,
    )
except ImportError:
    _TemplateStructureError = None
    _is_authored_preset_atom = None
    _load_pptx_structure_lock = None
    _parse_optional_layout_slides = None
    _parse_template_structure_slide = None
    _parse_template_structure_slides = None
    _structured_layout_definition_files = None
    _structure_subtree_signature = None
    _template_lock_errors = None
    _template_prototype_errors = None
    _validate_template_structure_svg = None

try:
    from svg_to_pptx.drawingml.theme_colors import (
        ThemeColorError as _ThemeColorError,
        load_theme_color_spec as _load_theme_color_spec,
    )
    from svg_to_pptx.drawingml.theme_fonts import (
        ThemeFontError as _ThemeFontError,
        load_master_text_style_spec as _load_master_text_style_spec,
        load_theme_font_spec as _load_theme_font_spec,
    )
except ImportError:
    _ThemeColorError = None
    _ThemeFontError = None
    _load_theme_color_spec = None
    _load_master_text_style_spec = None
    _load_theme_font_spec = None

try:
    from svg_finalize.embed_icons import (
        resolve_icon_path as _resolve_icon_path,
        suggest_icon_name as _suggest_icon_name,
    )
except ImportError:
    _resolve_icon_path = None
    _suggest_icon_name = None

try:
    from resource_paths import (
        SVG_WORK_DIR_NAMES as _SVG_WORK_DIR_NAMES,
        icon_dir_for_svg as _icon_dir_for_svg,
        project_root_for_svg_path as _project_root_for_svg_path,
        resolve_external_image_reference as _resolve_external_image_reference,
    )
except ImportError:
    _SVG_WORK_DIR_NAMES = frozenset()
    _icon_dir_for_svg = None
    _project_root_for_svg_path = None
    _resolve_external_image_reference = None


HEX_VALUE_RE = re.compile(
    r"#(?:[0-9A-Fa-f]{3}|[0-9A-Fa-f]{4}|[0-9A-Fa-f]{6}|[0-9A-Fa-f]{8})"
)

# Master/Layout preflight validation. Structured deck/layout-template projects
# are checked at authoring time; the exporter remains the final OOXML/package
# authority. Flat projects only receive the negative guard that rejects authored
# structure metadata. Template roster/placeholder checks always run. Current
# bundled templates opt in to complete structure validation through their
# native_structure_mode: structured declaration. Legacy template-mode packages
# fail closed; Create Template must author a new current-contract workspace.
_CHECK_PPTX_STRUCTURED_PROJECT = True

_SPEC_RELATIONSHIPS_LINE_RE = re.compile(
    r'^\s*-\s*\*\*Relationships(?:\s*\([^)]*\))?\*\*\s*[:：]\s*(?P<value>.*)$'
)
_CARRIED_RELATION_WORD_RE = re.compile(
    r'\b(?:order|link|parent|membership)\b', re.IGNORECASE
)


_BARE_HEX_VALUE_RE = re.compile(
    r"(?:[0-9A-Fa-f]{3}|[0-9A-Fa-f]{4}|[0-9A-Fa-f]{6}|[0-9A-Fa-f]{8})"
)
_NON_VISUAL_SVG_TAGS = frozenset({
    'defs',
    'desc',
    'metadata',
    'style',
    'title',
})
_BOUNDS_ATTR = 'data-pptx-bounds'
_MORPH_STAGING_ATTR = 'data-pptx-morph-staging'
_BOUNDS_OVERFLOW_TOLERANCE = 1.0
_BOUNDS_OVERFLOW_ERROR_RATIO = 0.05
_ROUNDTRIP_TEXT_CALIBRATION_CAP = 0.10
_PARAGRAPH_LINE_GAP_MIN_RATIO = 0.9
_PARAGRAPH_LINE_GAP_MAX_RATIO = 2.05
_PARAGRAPH_LINE_X_TOLERANCE = 0.5
_PARAGRAPH_LINE_MIN_TOTAL_CHARS = 12
_PARAGRAPH_LINE_MIN_LONGEST_CHARS = 8
_PARAGRAPH_LINE_TERMINATOR_RE = re.compile(r'[.!?。！？;；]["\'”’）)]*$')
_PARAGRAPH_LIST_MARKER_RE = re.compile(
    r'^\s*(?:[•·・▪◦‣]\s*|[-–—*]\s+|\d+[.)、]\s+|[（(]\d+[）)]\s*)\S+'
)
_LEGACY_PPTX_ATTRIBUTE_RENAMES = {
    'data-pptx-module-bounds': _BOUNDS_ATTR,
    'data-pptx-placeholder-bounds': _BOUNDS_ATTR,
    'data-pptx-placeholder-carrier': 'data-pptx-carrier',
    'data-pptx-placeholder-binding': 'data-pptx-binding',
    'data-pptx-placeholder-idx': 'data-pptx-idx',
}
_PPTX_ROOT_STRUCTURE_ATTRS = (
    'data-pptx-master',
    'data-pptx-master-name',
    'data-pptx-layout',
    'data-pptx-layout-name',
)
_PPTX_ROOT_VISIBILITY_ATTRS = (
    'data-pptx-show-master-shapes',
    'data-pptx-show-inherited-shapes',
)
_PPTX_STRUCTURE_ATTRS = frozenset({
    *_PPTX_ROOT_STRUCTURE_ATTRS,
    *_PPTX_ROOT_VISIBILITY_ATTRS,
    'data-pptx-layer',
    'data-pptx-layout-kind',
    'data-pptx-placeholder',
    'data-pptx-binding',
    'data-pptx-carrier',
    'data-pptx-idx',
})
_PPTX_PLACEHOLDER_DETAIL_ATTRS = frozenset({
    'data-pptx-binding',
    'data-pptx-idx',
})
_PPTX_STRUCTURE_SECTION_RE = re.compile(
    r"(?ms)^##[ \t]+pptx_structure[ \t]*\r?\n(.*?)(?=^##[ \t]+|\Z)"
)
_PPTX_STRUCTURE_MODE_RE = re.compile(
    r"(?m)^-[ \t]+mode[ \t]*:[ \t]*([^\s#]+)[ \t]*(?:#.*)?$"
)
FONT_SIZE_ANCHOR_TOLERANCE_PX = 2.0
SPARSE_UNDECLARED_FONT_SIZE_MAX_OCCURRENCES = 2

# Oversampling alone does not imply distortion and is often harmless for small
# logos. Warn about downscaling only when the source also has material on-disk
# weight, because PPTX embeds the compressed source asset rather than raw pixels.
# 1280px=96 SVG px/in; at 1.5 device px/SVG px on 1080p, 2x becomes ~3x on-screen—visibly soft; smaller is not warned.
IMAGE_UPSCALE_WARN_RATIO = 2.0
IMAGE_DOWNSIZE_WARN_RATIO = 4.0
IMAGE_DOWNSIZE_WARN_MIN_BYTES = 1024 * 1024

_TEMPLATE_SPEC_NAME_RE = re.compile(
    r'design_spec\.(?P<kind>brand|style|layout|deck)\.(?P<id>[^/\\]+)\.md'
)


from .checker_structure_helpers import (
    count_carried_relationship_pages,
    _compact_preset_ancestor_paint,
    _declared_pptx_structure_mode,
    _generated_theme_contract_errors,
    _parse_positive_bounds,
    _placeholder_bounds_error,
    _local_pptx_structure_errors,
    _normalize_hex_rgb,
)
from .checker_template_helpers import (
    _template_spec_paths,
    _spec_declared_kind,
    _roster_spec_paths,
    _roster_spec_path,
    _design_spec_kind,
    _declared_template_structure_mode,
    _declared_template_canvas_viewbox,
    _template_structure_checks_enabled,
    _direct_defs_index,
    _effective_presentation_value,
    _parse_viewbox_values,
    _parse_placeholders_fallback,
    _source_import_warning_count,
    _quality_source_fingerprint,
)
from .checker_file import FileQualityMixin
from .checker_roundtrip import RoundtripQualityMixin
from .checker_roundtrip_text import RoundtripTextQualityMixin
from .checker_carriers import CarriersQualityMixin
from .checker_text_runs import TextRunsQualityMixin
from .checker_text_geometry import TextGeometryQualityMixin
from .checker_bounds import BoundsQualityMixin
from .checker_visibility import VisibilityQualityMixin
from .checker_text_images import TextImagesQualityMixin
from .checker_presets import PresetsQualityMixin
from .checker_native_structure import NativeStructureQualityMixin
from .checker_specification import SpecificationQualityMixin
from .checker_attribution import AttributionQualityMixin
from .checker_directory import DirectoryQualityMixin
from .checker_layouts import LayoutsQualityMixin
from .checker_image_plan import ImagePlanQualityMixin
from .checker_image_closure import ImageClosureQualityMixin
from .checker_resources import ResourcesQualityMixin
from .checker_summary import SummaryQualityMixin
from .checker_report import ReportQualityMixin


class SVGQualityChecker(
    FileQualityMixin,
    RoundtripQualityMixin,
    RoundtripTextQualityMixin,
    CarriersQualityMixin,
    TextRunsQualityMixin,
    TextGeometryQualityMixin,
    BoundsQualityMixin,
    VisibilityQualityMixin,
    TextImagesQualityMixin,
    PresetsQualityMixin,
    NativeStructureQualityMixin,
    SpecificationQualityMixin,
    AttributionQualityMixin,
    DirectoryQualityMixin,
    LayoutsQualityMixin,
    ImagePlanQualityMixin,
    ImageClosureQualityMixin,
    ResourcesQualityMixin,
    SummaryQualityMixin,
    ReportQualityMixin,
):
    """SVG quality checker"""

    # Default placeholder convention per page-type prefix. This is a *hint*,
    # not a hard contract: templates may define their own placeholder vocabulary
    # via `placeholders:` in design_spec.md frontmatter (see
    # references/template-designer.md §4). Missing default placeholders surface
    # as warnings, never errors — designers may legitimately swap
    # `{{THANK_YOU}}` for `{{CLOSING_MESSAGE}}`, omit `{{DATE}}` when irrelevant,
    # or build content variants with bespoke slot vocabularies.
    #
    # Variants reuse the parent type's expectation (`03a_content_two_col.svg`
    # is matched by the same `content` rules as `03_content.svg`).
    #
    # Keys are page-type tokens, not numbered stems: template numbering is
    # presentation order within one template and shifts when the optional
    # TOC page is present (`02_chapter` in a four-page roster, `03_chapter`
    # in a five-page roster with `02_toc`), so the defaults must apply to
    # both spellings.
    DEFAULT_PLACEHOLDER_CONVENTION = {
        "cover": ("{{TITLE}}",),  # only the title is universally expected
        "chapter": ("{{CHAPTER_TITLE}}",),
        "toc": (),  # TOC layouts vary too widely to assert anything
        "content": ("{{PAGE_TITLE}}",),
        "ending": (),  # ending pages legitimately use varied vocabularies
    }

    _OOXML_PATTERN_PRESETS = frozenset({
        'pct5', 'pct10', 'pct20', 'pct25', 'pct30', 'pct40', 'pct50', 'pct60',
        'pct70', 'pct75', 'pct80', 'pct90',
        'horz', 'vert', 'ltHorz', 'ltVert', 'dkHorz', 'dkVert',
        'narHorz', 'narVert', 'dashHorz', 'dashVert',
        'cross', 'dnDiag', 'upDiag', 'ltDnDiag', 'ltUpDiag', 'dkDnDiag',
        'dkUpDiag', 'wdDnDiag', 'wdUpDiag',
        'dashDnDiag', 'dashUpDiag', 'diagCross',
        'smCheck', 'lgCheck', 'smGrid', 'lgGrid', 'dotGrid', 'smConfetti',
        'lgConfetti', 'horzBrick', 'diagBrick', 'solidDmnd', 'openDmnd',
        'dotDmnd', 'plaid', 'sphere', 'weave', 'wave', 'trellis', 'zigZag',
        'divot', 'shingle',
    })

