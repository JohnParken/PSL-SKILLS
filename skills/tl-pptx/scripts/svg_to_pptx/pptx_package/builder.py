"""Core PPTX assembly: create_pptx_with_native_svg."""
# Adapted from hugohe3/ppt-master 7e0df6705de0cd24345210d31f07fcb5a32b6900.
# MIT; Copyright (c) 2025-2026 Hugo He. See references/LICENSE-PPT-MASTER.txt and references/upstream.md.

from __future__ import annotations
import hashlib
import json
import mimetypes
import os
import posixpath
import random
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import secrets
import zipfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any
from xml.etree import ElementTree as ET
from xml.parsers import expat
from xml.sax.saxutils import escape, quoteattr
from pptx import Presentation
from pptx_embedded_fonts import (
    FONT_CONTENT_TYPE,
    FONT_REL_TYPE,
    PML_NS as EMBEDDED_FONT_PML_NS,
    REL_NS as EMBEDDED_FONT_REL_NS,
    EmbeddedFontBundle,
    EmbeddedFontError,
    embedded_font_typefaces,
)
from pptx_transitions import (
    AdvanceUpdate,
    DEFAULT_TRANSITION_DURATION,
    EnterUpdate,
    MorphPairExpectation,
    NATIVE_TRANSITIONS,
    apply_slide_motion_xml,
    create_transition_xml,
    normalize_transition_effect_request,
    read_slide_transition_xml,
    serialize_source_xml,
    set_directory_use_timings,
    transition_carriers,
    validate_generated_transition_xml,
    validate_pptx_morph_pairs,
    validate_pptx_transition_package,
    validate_seconds,
)
from pptx_animations import (
    ANIMATION_TIMING_OPTION_FIELDS,
    animation_seconds_to_milliseconds,
    create_sequence_timing_xml,
    normalize_animation_effect,
    normalize_animation_effect_request,
    normalize_animation_trigger,
    object_animation_fingerprint,
    pick_animation_effect,
    validate_generated_animation_xml,
    validate_pptx_animation_package,
)
from pptx_opc_validation import (
    canonical_opc_part_path as _canonical_opc_part_path,
    resolve_internal_opc_target as _resolve_internal_opc_target,
    verify_internal_relationships,
)
from pptx_workspace import WorkspaceResourceSpec
from pptx_ooxml.clone import clone_presentation_slides
from pptx_ooxml.package import PackagePartRegistry, prune_unreferenced_directory_parts
from language_tags import language_uses_rtl, office_language_tag
from hyperlink_contract import (
    HYPERLINK_REL_TYPE,
    trigger_shape_hyperlink_errors,
)
from ..animation_config import (
    MorphPair,
    animation_group_effect_entries,
    resolve_morph_pairs,
    resolve_slide_animation_config,
)
from ..drawingml.context import resolve_text_flow
from ..drawingml.converter import convert_svg_to_slide_shapes
from ..drawingml.theme_colors import (
    ThemeColorError,
    ThemeColorSpec,
    apply_theme_color_spec,
    rewrite_chart_accent_colors,
)
from ..drawingml.theme_fonts import (
    MasterTextStyleSpec,
    ThemeFontSpec,
    apply_master_text_style_spec,
    apply_theme_font_spec,
)
from ..drawingml.utils import EMU_PER_PX, detect_text_lang
from ..semantic_markers import (
    chrome_token_from_markers,
    page_layout_name_from_svg,
)
from .dimensions import (
    CANVAS_FORMATS,
    resolve_svg_canvas,
)
from .media import (
    PNG_RENDERER,
    get_png_renderer_info, convert_svg_to_png, convert_svg_to_png_cached,
)
from .notes import (
    markdown_to_plain_text,
    create_notes_master_rels_xml,
    create_notes_master_xml,
    create_notes_slide_xml,
    create_notes_slide_rels_xml,
)
from .narration import (
    AUDIO_CONTENT_TYPES,
    AUDIO_REL_TYPE,
    AUDIO_MARKER_PNG_BYTES,
    DEFAULT_NARRATION_START_FLOOR,
    IMAGE_REL_TYPE,
    MEDIA_REL_TYPE,
    apply_recorded_timing,
    inject_narration,
    narration_lead_in_seconds,
    next_shape_id,
    probe_audio_duration,
    remove_narration,
)
from .slide_xml import (
    create_slide_xml_with_svg, create_slide_rels_xml,
)
from .template_structure import (
    NativeStructureContract,
    OOXML_UINT32_MAX,
    TEMPLATE_PLACEHOLDER_TYPES,
    TemplateElementSpec,
    TemplateSlideSpec,
    TemplateStructureError,
    flat_structure_metadata_errors,
    is_proxy_placeholder,
    match_native_placeholders,
    parse_preserve_slides,
    parse_template_slides,
    template_placeholder_bindings,
)
from .template_validation import validate_pptx_template_package

SLIDE_LAYOUT_REL_TYPE = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout"
)


SLIDE_REL_TYPE = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide"
)


SLIDE_MASTER_REL_TYPE = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideMaster"
)


NOTES_SLIDE_REL_TYPE = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/notesSlide"
)


THEME_REL_TYPE = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/theme"
)


THEME_CONTENT_TYPE = "application/vnd.openxmlformats-officedocument.theme+xml"


def _new_field_guid() -> str:
    """Generate a braced RFC 4122 version-4 identifier for DrawingML fields."""
    value = secrets.token_hex(16).upper()
    variant = "89AB"[int(value[16], 16) & 3]
    return f"{{{value[:8]}-{value[8:12]}-4{value[13:16]}-{variant}{value[17:20]}-{value[20:]}}}"


PML_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"


_SLIDE_SIZE_TYPES = ((4, 3, "screen4x3"), (16, 9, "screen16x9"), (16, 10, "screen16x10"))


DML_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"


REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


P14_NS = "http://schemas.microsoft.com/office/powerpoint/2010/main"


MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"


A14_NS = "http://schemas.microsoft.com/office/drawing/2010/main"


MATH_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"


PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


for _prefix, _uri in (
    ("p", PML_NS),
    ("a", DML_NS),
    ("r", REL_NS),
    ("p14", P14_NS),
    ("mc", MC_NS),
    ("a14", A14_NS),
    ("m", MATH_NS),
):
    try:
        ET.register_namespace(_prefix, _uri)
    except (ValueError, AttributeError):
        pass


@dataclass(frozen=True)
class PptxStructureContext:
    """Resolved base package structure reused when slide XML is regenerated."""

    slide_layout_targets: dict[int, str]
    slide_master_parts: dict[int, str]

    def slide_layout_target(self, slide_num: int) -> str:
        """Return the slide layout target for a generated slide."""
        try:
            return self.slide_layout_targets[slide_num]
        except KeyError as exc:
            raise RuntimeError(
                f"Missing slide layout relationship for generated slide {slide_num}"
            ) from exc

    def slide_master_part(self, slide_num: int) -> str:
        """Return the slide master package part for a generated slide."""
        try:
            return self.slide_master_parts[slide_num]
        except KeyError as exc:
            raise RuntimeError(
                f"Missing slide master relationship for generated slide {slide_num}"
            ) from exc


@dataclass(frozen=True)
class RoundtripSlidePatch:
    """One authoring slide overlay applied onto its preserved source part."""

    source_ref_ids: frozenset[str]
    edited_ref_ids: frozenset[str]
    deleted_ref_ids: frozenset[str]
    visual_changed: bool
    authoring_visual_changed: bool
    motion_changed: bool
    transition_changed: bool
    transition_replaced: bool
    animation_changed: bool
    notes_changed: bool
    advance_changed: bool = False
    # Shared with this page's authoring-report document.
    warnings: list[dict[str, Any]] | None = None


@dataclass(frozen=True)
class _NotesMasterReference:
    """Notes master selected for one generated notes slide."""

    package_part: str
    created_theme_part: str | None = None


@dataclass
class _TemplateRuntimeSlide:
    """Parsed slide package state used by explicit Layout structure export."""

    spec: TemplateSlideSpec
    slide_path: Path
    rels_path: Path
    tree: ET.ElementTree
    root: ET.Element
    rels: dict[str, dict[str, str]]
    shapes: dict[str, ET.Element]
    shape_ids_by_svg_id: dict[str, list[str]]


_SLIDE_BACKGROUND_RE = re.compile(
    r"(?P<prefix><p:cSld\b[^>]*>\s*)"
    r"(?P<bg><p:bg\b.*?</p:bg>)"
    r"(?P<suffix>\s*<p:spTree\b)",
    re.DOTALL,
)


_CHROME_TRACE_TOKENS = (
    "logo",
    "footer",
    "header",
    "watermark",
    "chrome",
    "pagenumber",
    "slidenumber",
    "pagenum",
    "slidenum",
)


_TOP_LEVEL_SHAPE_TAGS = {
    f"{{{PML_NS}}}sp",
    f"{{{PML_NS}}}grpSp",
    f"{{{PML_NS}}}pic",
    f"{{{PML_NS}}}cxnSp",
    f"{{{PML_NS}}}graphicFrame",
    f"{{{MC_NS}}}AlternateContent",
}


_FLAT_SYSTEM_PLACEHOLDER_TYPES = frozenset({"dt", "ftr", "sldNum"})


_REL_ATTRS = {
    f"{{{REL_NS}}}embed",
    f"{{{REL_NS}}}link",
    f"{{{REL_NS}}}id",
}


_XML_START_TAG_RE = re.compile(r'''<(?:"[^"]*"|'[^']*'|[^'">])*>''')


_XML_ATTRIBUTE_RE = re.compile(r'''\s+([^\s=/>]+)\s*=\s*(["'])(.*?)\2''', re.DOTALL)


_RAW_ALTERNATE_CONTENT_RE = re.compile(
    rb"<mc:AlternateContent\b[^>]*>.*?</mc:AlternateContent\s*>",
    re.DOTALL,
)


_RAW_DIRECT_TRANSITION_RE = re.compile(
    rb"<p:transition\b(?:[^>]*/>|[^>]*>.*?</p:transition\s*>)",
    re.DOTALL,
)


_RAW_TIMING_RE = re.compile(
    rb"<p:timing\b(?:[^>]*/>|[^>]*>.*?</p:timing\s*>)",
    re.DOTALL,
)


COVER_LAYOUT_NAME = "Cover"


_BASELINE_LAYOUT_ROLE_TOKENS = (
    ("Cover", frozenset({"cover", "frontcover"}), ("封面",)),
    (
        "Agenda",
        frozenset({"agenda", "contents", "outline", "toc"}),
        ("目录", "议程"),
    ),
    (
        "Section",
        frozenset({"chapter", "divider", "section", "transition"}),
        ("章节", "过渡页"),
    ),
    (
        "Closing",
        frozenset({"closing", "end", "ending", "qa", "thankyou", "thanks"}),
        ("封底", "结束", "结尾", "结语", "致谢", "谢谢"),
    ),
)


_TEMPLATE_PLACEHOLDER_PROMPTS = {
    "title": "Click to add title",
    "subtitle": "Click to add subtitle",
    "body": "Click to add text",
    "picture": "Click to add picture",
    "chart": "Click to add chart",
    "table": "Click to add table",
    "object": "Click to add content",
    "media": "Click to add media",
    "date": "Date",
    "footer": "Footer",
}


_PARAGRAPH_BULLET_CHOICE_TAGS = {
    f"{{{DML_NS}}}buNone",
    f"{{{DML_NS}}}buAutoNum",
    f"{{{DML_NS}}}buChar",
    f"{{{DML_NS}}}buBlip",
}


_PARAGRAPH_PROPERTIES_TRAILING_TAGS = {
    f"{{{DML_NS}}}tabLst",
    f"{{{DML_NS}}}defRPr",
    f"{{{DML_NS}}}extLst",
}


_PAGE_NUMBER_TOKENS = {"pagenumber", "pagenum", "slidenumber"}


_IMAGE_CONTENT_TYPES = {
    'png': 'image/png',
    'jpg': 'image/jpeg',
    'jpeg': 'image/jpeg',
    'gif': 'image/gif',
    'webp': 'image/webp',
    'svg': 'image/svg+xml',
    'bmp': 'image/bmp',
    'emf': 'image/x-emf',
    'tif': 'image/tiff',
    'tiff': 'image/tiff',
    'wmf': 'image/x-wmf',
}


_NOTES_MASTER_REL_TYPE = (
    'http://schemas.openxmlformats.org/officeDocument/2006/relationships/notesMaster'
)


_NOTES_MASTER_CONTENT_TYPE = (
    'application/vnd.openxmlformats-officedocument.presentationml.notesMaster+xml'
)



# Stable explicit facade; algorithms retain their upstream signatures.
from .builder_relationships import (
    _slide_size_type,
    _set_slide_size_type,
    _relationship_attrs,
    _resolve_package_target,
    _relationships_path_for_part,
    _find_relationship_target,
    _read_relationships,
    _find_relationship_id,
    _read_slide_layout_targets,
)
from .builder_background_trace import (
    _extract_slide_background_xml,
    _remove_slide_background_xml,
    _put_background_on_part,
    _dominant_variant,
    _is_strict_majority,
    _promote_common_slide_backgrounds_to_masters,
    _chrome_token_from_svg_id,
    _trace_chrome_shape_ids,
    _trace_native_shape_ids,
    _shape_id,
    _set_shape_name,
    _roundtrip_morph_shape_ids,
)
from .builder_morph import (
    _patch_morph_name_bytes,
    _apply_morph_shape_names,
    _top_level_shape_name_roster,
    _top_level_shapes_by_id,
    _timing_shape_ids,
    _relationship_ids_in_shape,
)
from .builder_shape_parts import (
    _shape_relationships_supported,
    _canonical_shape_xml,
    _ensure_relationship,
    _part_name_for_relationships_path,
    _copy_shape_relationships_to_part,
    _copy_shape_relationships_to_master,
    _next_master_shape_id,
    _renumber_shape_ids,
    _append_shape_to_master,
    _append_shape_to_part,
    _write_xml_tree,
    _slide_ref_shape_ids,
)
from .builder_roundtrip_shapes import (
    _roundtrip_synthetic_shape_id,
    _direct_shape_id,
    _roundtrip_shape_id_closure,
    _index_roundtrip_source_shape,
    _roundtrip_source_shape_roster,
    _roundtrip_nested_restore_ids,
    _restore_roundtrip_nested_source_shapes,
    _roundtrip_shape_sort_key,
    _relationship_key,
    _merge_roundtrip_relationships,
    _replace_roundtrip_motion,
)
from .builder_roundtrip_motion import (
    _replace_roundtrip_transition_advance,
    _raw_transition_carrier_span,
    _preserve_roundtrip_transition_markup,
    _raw_timing_span,
    _preserve_roundtrip_timing_markup,
    _apply_roundtrip_transition_overlay,
    _shape_ids_in_element,
    _rewrite_roundtrip_timing_shape_ids,
    _renumber_roundtrip_generated_shapes,
)
from .builder_roundtrip_overlay import (
    _apply_roundtrip_slide_overlay,
)
from .builder_roundtrip_notes import (
    _remove_relationships_by_type,
    _part_has_relationship_references,
    _unused_part_path,
    _remove_all_notes_parts,
    _apply_slide_notes,
    _roundtrip_resource_payloads,
    _reinject_roundtrip_resources,
)
from .builder_layout_creation import (
    _next_layout_part_number,
    _next_slide_layout_id,
    _create_cover_layout,
    _create_custom_layout,
    _set_master_picker_name,
    _reseed_p14_creation_id,
    _next_master_part_number,
    _next_theme_part_number,
)
from .builder_master_creation import (
    _clone_master_theme,
    _clone_structured_master,
    _assign_structured_masters,
    _clear_master_placeholder_shapes,
    _set_slide_layout_target,
)
from .builder_baseline_layouts import (
    _baseline_layout_role,
    _layout_identity,
    _shared_explicit_slide_background,
    _extract_baseline_layout_families,
    _promote_common_chrome_shapes_to_layouts,
)
from .builder_template_shapes import (
    _template_runtime_slides,
    _template_shape_for_item,
    _shape_transform,
    _int_attr,
    _flatten_group_transform,
    _unwrap_placeholder_carrier,
    _slide_sp_tree,
    _append_shape_to_runtime_slide,
    _presentation_slide_size_emu,
    _solid_background_xml_from_shape,
    _remove_template_shape,
    _template_reference_indices,
    _move_template_background,
    _move_template_solid_background_shapes,
    _set_slide_tree_background,
)
from .builder_template_frames import (
    _apply_template_slide_backgrounds,
    _move_template_static_shape,
    _shape_bounds_emu,
    _replace_shape_xfrm,
    _placeholder_vertical_anchor,
    _normalize_placeholder_body_properties,
    _apply_layout_frame_to_placeholder_carrier,
    _layout_level_one_paragraph_properties,
    _set_layout_level_one_default_size,
    _set_no_bullet_paragraph_properties,
)
from .builder_placeholders import (
    _placeholder_text_body,
    _set_placeholder_no_inherited_bullets,
    _set_placeholder_theme_font_role,
    _layout_placeholder_shape,
    _placeholder_binding_proxy,
    _patch_slide_placeholder,
    _set_template_layout_header_footer,
)
from .builder_explicit_structure import (
    _apply_explicit_layout_structure,
    _apply_preserved_structure,
)
from .builder_master_chrome import (
    _promote_common_chrome_shapes_to_masters,
    _first_slide_number,
    _shape_with_id,
    _replace_literal_run_with_slidenum_field,
    _convert_page_number_texts_to_fields,
)
from .builder_pruning import (
    _remove_relationship,
    _remove_content_type_override,
    _remove_trailing_layout_definition_slides,
    _prune_unreferenced_definition_payload_parts,
    _prune_unused_slide_layouts,
    _flat_structure_name,
    _flat_placeholder_type,
    _clean_flat_structure_part,
    _name_flat_themes,
)
from .builder_flat_package import (
    _prepare_flat_structure,
    _append_relationship,
    _add_default_content_type,
    _add_content_type_override,
    _content_type_contract,
    _content_type_for_extension,
    _as_dict,
    _create_writable_work_dir,
    _relax_output_permissions,
)
from .builder_notes_master import (
    _content_type_overrides,
    _notes_master_part_from_target,
    _validate_notes_master_part,
    _register_notes_master_id,
    _ensure_notes_master,
)
from .builder_motion_config import (
    _slide_config,
    _slide_transition_settings,
    _slide_animation_settings,
    _build_sequence_targets,
)
from .builder_sounds import (
    _next_relationship_id,
    _materialize_slide_sound,
    _materialize_transition_sound,
    _materialize_animation_sounds,
    _prerender_legacy_pngs,
)
from .builder_metadata import (
    _rtl_text_levels,
    _apply_template_text_language,
    _presentation_format,
    _stamp_docprops,
)
from .builder_source_package import (
    _create_preserved_base_pptx,
    _clear_preserved_slide_collections,
    _validate_source_theme_xml,
    _install_source_theme_xml,
    _install_source_themes_by_master,
    _install_source_embedded_fonts,
)

from .builder_export import create_pptx_with_native_svg
