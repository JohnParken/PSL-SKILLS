"""
PPT Master - Template Structure Metadata

Parse and validate explicit SVG metadata consumed by structured PPTX export.

Usage:
    Imported by svg_to_pptx.pptx_package.builder and svg_quality_checker.py.

Examples:
    parse_template_slides([Path("projects/demo/svg_output/01_cover.svg")])

Dependencies:
    None (only uses standard library)
"""
# Adapted from hugohe3/ppt-master 7e0df6705de0cd24345210d31f07fcb5a32b6900.
# MIT; Copyright (c) 2025-2026 Hugo He. See references/LICENSE-PPT-MASTER.txt and references/upstream.md.

from __future__ import annotations
import base64
import binascii
import copy
import hashlib
import json
import math
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET
from native_payloads import NativePayloadError, hydrate_native_payload_refs
from pptx_to_svg.preset_authoring import (
    authored_preset_encoding,
    validate_authored_preset_group,
)
from ..drawingml.utils import (
    is_picture_effect_carrier,
    parse_inline_style,
    parse_opacity,
    parse_project_geometry_length,
    project_geometry_length_errors,
)
from ..canvas_contract import CanvasContractError, parse_project_viewbox
from ..geometry_properties import (
    GeometryStyleError,
    materialize_inline_geometry_properties,
)
from ..native_objects import (
    NativeMarkerAttributeError,
    native_json_is_authoritative,
    native_replacement_kind,
)

_NON_VISUAL_TAGS = frozenset({"defs", "title", "desc", "metadata", "style"})


_STRUCTURE_ATTRS = frozenset({
    "data-pptx-layer",
    "data-pptx-layout",
    "data-pptx-layout-kind",
    "data-pptx-layout-name",
    "data-pptx-master",
    "data-pptx-master-name",
    "data-pptx-show-inherited-shapes",
    "data-pptx-show-master-shapes",
    "data-pptx-placeholder",
    "data-pptx-binding",
    "data-pptx-carrier",
    "data-pptx-idx",
    "data-pptx-editable",
})


_FLAT_FORBIDDEN_STRUCTURE_ATTRS = frozenset(
    _STRUCTURE_ATTRS - {"data-pptx-editable"}
)


_LAYERS = frozenset({"master", "layout", "slide"})


_PLACEHOLDERS = frozenset({
    "title",
    "subtitle",
    "body",
    "picture",
    "chart",
    "table",
    "object",
    "media",
    "date",
    "footer",
    "slide-number",
})


TEMPLATE_PLACEHOLDER_TYPES = {
    "title": "title",
    "subtitle": "subTitle",
    "body": "body",
    "picture": "pic",
    "chart": "chart",
    "table": "tbl",
    "object": "obj",
    "media": "media",
    "date": "dt",
    "footer": "ftr",
    "slide-number": "sldNum",
}


_TEXT_PLACEHOLDERS = frozenset({
    "title",
    "subtitle",
    "body",
    "date",
    "footer",
    "slide-number",
})


_OBJECT_PLACEHOLDER_TAGS = frozenset({
    "rect",
    "circle",
    "ellipse",
    "line",
    "path",
    "polygon",
    "polyline",
    "text",
    "image",
    "svg",
    "use",
})


_LAYOUT_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


_MASTER_KEY_RE = _LAYOUT_KEY_RE


_LOCK_ROW_RE = re.compile(r"^-\s+([^:]+?)\s*:\s*(.*?)\s*$")


_LOCK_PAGE_RE = re.compile(r"^P(\d+)$")


PPTX_STRUCTURE_MODES = frozenset({"structured", "preserve", "flat"})


TEMPLATE_ADHERENCE_MODES = frozenset({"strict", "adaptive"})


TEMPLATE_REUSE_SCOPES = frozenset({"mirror", "layout", "style"})


PLACEHOLDER_BINDING_MODES = frozenset({"carrier", "proxy"})


SOURCE_THEMES_FILENAME = "source_themes.json"


SOURCE_THEMES_SCHEMA = "ppt-master.source-themes.v1"


_DML_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"


_REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


_TEMPLATE_SKIN_ATTRS = frozenset({
    "baseline-shift",
    "color",
    "fill",
    "fill-opacity",
    "filter",
    "font-family",
    "font-size",
    "font-style",
    "font-weight",
    "letter-spacing",
    "opacity",
    "paint-order",
    "stop-color",
    "stop-opacity",
    "stroke",
    "stroke-dasharray",
    "stroke-dashoffset",
    "stroke-linecap",
    "stroke-linejoin",
    "stroke-miterlimit",
    "stroke-opacity",
    "stroke-width",
    "style",
    "text-decoration",
    "word-spacing",
})


_CSS_RULE_RE = re.compile(r"(?s)([^{}]+)\{([^{}]*)\}")


_CSS_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)


_CSS_ID_RE = re.compile(r"#([A-Za-z_][A-Za-z0-9_-]*)")


_CSS_CLASS_RE = re.compile(r"\.([A-Za-z_][A-Za-z0-9_-]*)")


_CSS_ATTR_RE = re.compile(
    r"\[\s*([A-Za-z_:][A-Za-z0-9_.:-]*)"
    r"(?:\s*(?:[~|^$*]?=)\s*(['\"]?)([^\]'\"]+)\2)?\s*\]"
)


_CSS_URL_RE = re.compile(r"url\(\s*(['\"]?)(.*?)\1\s*\)", re.IGNORECASE)


_CSS_TAG_RE = re.compile(r"(?:^|[\s>+~])([A-Za-z_][A-Za-z0-9_-]*|\*)")


NATIVE_STRUCTURE_SCHEMA = "ppt-master.native-structure.v1"


OOXML_UINT32_MAX = (1 << 32) - 1


class TemplateStructureError(RuntimeError):
    """Reject invalid or ambiguous template-structure metadata."""


@dataclass(frozen=True)
class PptxLayoutReference:
    """One spec_lock page-to-PowerPoint-layout assignment."""

    slide_num: int
    layout_key: str
    layout_name: str | None = None
    master_key: str | None = None


@dataclass(frozen=True)
class PptxLayoutDefinition:
    """One reusable PowerPoint Layout declared by a structured project lock."""

    layout_key: str
    master_key: str
    layout_name: str
    prototype_slide_num: int | None = None
    prototype_svg_path: Path | None = None


@dataclass(frozen=True)
class PptxMasterReference:
    """One named Master declared by the structured project lock."""

    master_key: str
    master_name: str


@dataclass(frozen=True)
class PptxPrototypeReference:
    """One spec_lock page-to-template-SVG prototype declaration."""

    slide_num: int
    template_basename: str
    svg_path: Path
    replication_mode: str | None = None


@dataclass(frozen=True)
class PptxStructureLock:
    """Optional project-level PPTX structure export policy."""

    mode: str
    template_reuse_scope: str | None = None
    template_adherence: str | None = None
    masters: tuple[PptxMasterReference, ...] = ()
    layout_definitions: tuple[PptxLayoutDefinition, ...] = ()
    layouts: tuple[PptxLayoutReference, ...] = ()
    prototypes: tuple[PptxPrototypeReference, ...] = ()
    source_template: Path | None = None
    native_structure: Path | None = None


@dataclass(frozen=True)
class NativePlaceholderSpec:
    """One placeholder exposed by a preserved source layout."""

    semantic_role: str
    placeholder_type: str
    idx: int | None
    geometry: tuple[float, float, float, float] | None = None

    @property
    def effective_idx(self) -> int:
        """Return the OOXML index after applying the omitted-value default."""
        return self.idx if self.idx is not None else 0


@dataclass(frozen=True)
class NativeLayoutSpec:
    """One named layout retained from the source PPTX package."""

    key: str
    name: str
    package_part: str
    master_key: str
    placeholders: tuple[NativePlaceholderSpec, ...] = ()


@dataclass(frozen=True)
class NativeSlideSpec:
    """One source slide retained by index for exact round-trip passthrough."""

    index: int
    package_part: str
    layout_key: str


@dataclass(frozen=True)
class NativeStructureContract:
    """Validated portable contract for a preserved source PPTX package."""

    source_template: Path
    contract_path: Path
    source_sha256: str
    slide_size_emu: tuple[int, int]
    layouts: tuple[NativeLayoutSpec, ...]
    slides: tuple[NativeSlideSpec, ...]

    def layout(self, key: str) -> NativeLayoutSpec:
        for layout in self.layouts:
            if layout.key == key:
                return layout
        raise TemplateStructureError(
            f"native_structure.json has no layout key {key!r}"
        )

    def slide(self, index: int) -> NativeSlideSpec:
        """Return one source slide contract by presentation-order index."""
        for slide in self.slides:
            if slide.index == index:
                return slide
        raise TemplateStructureError(
            f"native_structure.json has no source slide {index}"
        )


@dataclass(frozen=True)
class TemplateElementSpec:
    """One direct SVG child carrying explicit PPTX structure metadata."""

    element_id: str
    order: int
    tag: str
    layer: str | None = None
    placeholder: str | None = None
    placeholder_bounds: tuple[float, float, float, float] | None = None
    placeholder_idx: int | None = None
    placeholder_binding: str | None = None
    placeholder_carrier_tag: str | None = None
    is_background: bool = False

    def contract_signature(self) -> tuple[object, ...]:
        """Return metadata that must agree across slides sharing a structure."""
        return (
            self.element_id,
            self.tag,
            self.layer,
            self.placeholder,
            self.placeholder_bounds,
            self.placeholder_idx,
            self.placeholder_binding,
            self.placeholder_carrier_tag,
            self.is_background,
        )


@dataclass(frozen=True)
class TemplateSlideSpec:
    """Explicit structure contract parsed from one SVG slide."""

    slide_num: int
    svg_path: Path
    master_key: str
    master_name: str
    layout_key: str
    layout_name: str
    layout_show_master_shapes: bool
    slide_show_inherited_shapes: bool
    elements: tuple[TemplateElementSpec, ...]

    @property
    def master_elements(self) -> tuple[TemplateElementSpec, ...]:
        return tuple(item for item in self.elements if item.layer == "master")

    @property
    def layout_elements(self) -> tuple[TemplateElementSpec, ...]:
        return tuple(item for item in self.elements if item.layer == "layout")

    @property
    def placeholders(self) -> tuple[TemplateElementSpec, ...]:
        return tuple(item for item in self.elements if item.placeholder)

    @property
    def layout_contract(self) -> tuple[tuple[object, ...], ...]:
        return tuple(
            item.contract_signature()
            for item in self.elements
            if item.layer == "layout" or item.placeholder
        )


@dataclass(frozen=True)
class TemplatePlaceholderBinding:
    """Resolved PowerPoint identity for one template placeholder."""

    element: TemplateElementSpec
    placeholder_type: str
    assigned_idx: int | None

    @property
    def effective_idx(self) -> int:
        """Return the OOXML idx value after applying its default of zero."""
        return self.assigned_idx if self.assigned_idx is not None else 0


_PRESERVE_PLACEHOLDER_TYPE_ORDER = {
    "title": ("title", "ctrTitle"),
    "subtitle": ("subTitle", "body", "obj"),
    "body": ("body", "obj", "subTitle"),
    "picture": ("pic", "obj"),
    "chart": ("chart", "obj"),
    "table": ("tbl", "obj"),
    "object": ("obj",),
    "media": ("media", "obj", "pic"),
    "date": ("dt",),
    "footer": ("ftr",),
    "slide-number": ("sldNum",),
}



# Stable explicit facade; algorithms retain their upstream signatures.
from .template_structure_metadata import (
    is_proxy_placeholder,
    template_placeholder_bindings,
    _local_tag,
    _parse_svg_root,
    _is_authored_preset_atom,
    _svg_canvas,
    _is_full_canvas_solid_rect,
    _portable_project_file,
    _template_replication_mode,
    load_template_source_themes,
    _template_svg_path,
)
from .template_structure_structure_lock import (
    load_pptx_structure_lock,
)
from .template_structure_native_contract import (
    _file_sha256,
    _native_geometry,
    load_native_structure_contract,
    _parse_placeholder_bounds,
    _parse_placeholder_idx,
    _validate_placeholder_carrier,
    _structure_attrs,
    _parse_root_boolean,
)
from .template_structure_slide_parser import (
    parse_template_slide,
)
from .template_structure_slide_contract import (
    _validate_template_slide_contracts,
    parse_template_slides,
    parse_optional_layout_slides,
    flat_structure_metadata_errors,
    parse_preserve_slides,
    structured_layout_definition_files,
)
from .template_structure_lock_validation import (
    template_prototype_lock_errors,
    template_lock_errors,
)
from .template_structure_resource_signatures import (
    _signature_attr_value,
    _element_tree_signature,
    _is_json_first_native_marker,
    _mirror_comparison_children,
    _mirror_resource_projection,
    _svg_reference_ids,
    _font_family_names,
    _font_families_from_declarations,
    _scope_selector_tokens,
    _css_selector_matches_scope,
    _css_asset_signature,
    _normalize_css_declarations,
)
from .template_structure_visual_signatures import (
    _scope_css_signature,
    _scope_visual_resources_signature,
    _structure_subtree_signature,
    _mirror_ordinary_slide_ids,
    _mirror_slide_local_signature,
)
from .template_structure_mirror_comparison import (
    _prototype_placeholder_contract,
    _layout_contract_difference,
    _mirror_comparable_attributes,
    _mirror_node_label,
    _mirror_element_difference,
    _mirror_slide_local_difference,
    _mirror_structure_difference,
)
from .template_structure_prototype_validation import (
    template_prototype_errors,
)
from .template_structure_native_validation import (
    match_native_placeholders,
    native_structure_lock_errors,
    _placement_lint_errors,
    validate_template_svg,
)
