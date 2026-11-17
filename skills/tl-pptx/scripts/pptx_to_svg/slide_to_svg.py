"""Per-slide composition: dispatches every ShapeNode through the right
converter, accumulates <defs>, and produces one final SVG string.

The output structure mirrors what svg_to_pptx expects so the deck can be
round-tripped:
    <svg viewBox="0 0 W H">
        <defs>
            <linearGradient id=.../>
            <marker id=.../>
            <filter id=.../>
        </defs>
        <!-- background -->
        <rect ... />        (slide background, if any)
        <g id="shape-1">...</g>
        <g id="shape-2">...</g>
        ...
    </svg>

Each top-level <g> wraps one shape and is treated by svg_to_pptx as an
animation anchor.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import posixpath
from dataclasses import dataclass, field
from xml.etree import ElementTree as ET

from pptx_shapes import (
    CONNECTOR_PRESET_TYPES,
    NATIVE_FALLBACK_SHA256_ATTR,
    has_relationship_attributes,
    svg_native_fallback_markup_fingerprint,
    svg_text_fingerprint,
)
from pptx_effects import (
    EFFECT_REASON_ATTR,
    EFFECT_STATUS_ATTR,
    NATIVE_EFFECT_ATTR,
    NATIVE_EFFECT_SHA256_ATTR,
    txbody_has_run_effects,
    unsupported_effect_metadata,
)
from hyperlink_contract import SHAPE_HYPERLINK_ATTR, SOURCE_HREF_ATTR
from svg_to_pptx.drawingml.paths import (
    PathCommand,
    normalize_path_commands,
    parse_svg_path,
    parse_svg_points,
    svg_path_to_absolute,
    transform_path_commands,
)

from .color_resolver import ColorPalette, find_color_elem, resolve_color
from .chart_to_svg import CHART_URI, CHARTEX_URI, extract_native_chart_payload
from .custgeom_to_svg import convert_custom_geom
from .effect_to_svg import (
    EffectResult,
    convert_effects,
    unsupported_target_effect_metadata,
)
from .emu_units import NS, Xfrm, fmt_num, format_canvas_px_from_emu, parse_xfrm
from .fill_to_svg import FillResult, resolve_fill
from .formula_import import (
    A14_NS,
    FormulaImport,
    FormulaImportError,
    import_formula,
    opaque_formula_preview,
)
from .import_diagnostics import (
    ImportDiagnostic,
    append_diagnostic,
)
from .hyperlinks import resolve_click_hyperlink
from .ln_to_svg import StrokeResult, resolve_stroke
from .ooxml_loader import (
    OoxmlPackage,
    PartRef,
    SlideRef,
    inherited_shape_visibility,
)
from .pic_to_svg import (
    LinkedImageResolutionError,
    MediaResolutionError,
    PictureResult,
    convert_blip_fill,
    convert_picture,
)
from .prstgeom_to_svg import GeomResult, convert_prst_geom
from .preset_svg_markup import serialize_preset_layers
from .shape_walker import (
    CONNECTOR, GRAPHIC, GROUP, PICTURE, SHAPE,
    ShapeNode, get_background, walk_sp_tree,
)
from .tbl_to_svg import convert_tbl
from .txbody_to_svg import (
    TextResult,
    convert_txbody,
    convert_vertical_txbody,
    is_vertical_txbody,
    DEFAULT_FONT_SIZE_PX,
)


# ---------------------------------------------------------------------------
# AssemblyContext
# ---------------------------------------------------------------------------

_SOURCE_PROXY_ATTRIBUTE = "data-pptx-source-proxy"
_SOURCE_PROXY_KIND = "native-restore"
_EXTERNAL_LINKED_IMAGE_PROXY_ATTRIBUTE = (
    "data-pptx-external-linked-image-proxy"
)

@dataclass
class AssemblyContext:
    """Per-slide accumulator for unique IDs + media + defs."""

    palette: ColorPalette | None
    pkg: OoxmlPackage
    slide_part: PartRef
    theme: PartRef | None = None
    slide_number: int | None = None
    theme_fonts: dict[str, str] = field(default_factory=dict)
    media_subdir: str = "images"
    embed_images: bool = False
    keep_hidden: bool = False
    strict: bool = False
    group_id_prefix: str = ""
    render_graphic_previews: bool = True
    preserve_placeholder_inheritance: bool = False
    asset_name_map: dict[str, str] = field(default_factory=dict)
    diagnostics: list[ImportDiagnostic] = field(default_factory=list)
    source_slide_index: int | None = None
    current_node: ShapeNode | None = None

    # Sequence counters (single-element lists so handlers can mutate)
    grad_seq: list[int] = field(default_factory=lambda: [0])
    marker_seq: list[int] = field(default_factory=lambda: [0])
    filter_seq: list[int] = field(default_factory=lambda: [0])
    shape_seq: list[int] = field(default_factory=lambda: [0])
    clip_seq: list[int] = field(default_factory=lambda: [0])

    # Accumulated outputs
    defs: list[str] = field(default_factory=list)
    media: dict[str, bytes] = field(default_factory=dict)
    group_fills: list[FillResult | None] = field(default_factory=list)

    def bind_palette(self) -> None:
        """Route tolerant color diagnostics through the current object context."""
        if self.palette is None:
            return
        self.palette.strict = self.strict
        self.palette.diagnostic_sink = self._diagnose_color

    def diagnose(
        self,
        code: str,
        message: str,
        fallback: str,
        *,
        node: ShapeNode | None = None,
    ) -> None:
        """Record one recoverable source-contract violation."""
        source_node = node or self.current_node
        append_diagnostic(
            self.diagnostics,
            ImportDiagnostic(
                code=code,
                message=message,
                fallback=fallback,
                part_path=self.slide_part.path,
                slide_index=self.source_slide_index,
                shape_id=source_node.spid if source_node is not None else "",
                shape_name=source_node.name if source_node is not None else "",
                shape_kind=source_node.kind if source_node is not None else "",
            ),
        )

    def _diagnose_color(self, code: str, message: str, fallback: str) -> None:
        self.diagnose(code, message, fallback)



# Stable upstream API facade, split by conversion responsibility.
from .slide_to_svg_assembly import (
    _diagnose_picture_result,
    _resolve_svg_hyperlink,
    assemble_slide,
    assemble_part_solo,
)
from .slide_to_svg_shapes import (
    _convert_node,
    _fallback_node_svg,
    _convert_shape,
)
from .slide_to_svg_text import (
    _effective_placeholder_tx_body,
    _materialize_inherited_list_styles,
    _merge_text_property_element,
    _text_property_child_key,
    _counter_reflected_text_svg,
    _block_formula_zone,
    _block_formula_carrier_error,
    _prepare_inline_formula_resolver,
    _diagnose_formula_fallback,
    _render_block_formula,
)
from .slide_to_svg_geometry import (
    _txbody_metadata,
    _placeholder_sp_pr_metadata,
    _resolve_geometry,
    _build_geometry_xml,
    _resolve_shape_style_defaults,
    _effective_shape_stroke_properties,
    _resolve_text_style_default,
    _resolve_ref_color,
    _geom_to_svg,
    _preset_layers_to_svg,
)
from .slide_to_svg_images import (
    _clip_blip_image,
    _flatten_vector_custom_crop,
    _inject_clip_path,
    _project_clip_into_nested_crop,
    _serialize_clip_path,
    _convert_picture,
    _inject_root_svg_attrs,
    _convert_connector,
)
from .slide_to_svg_groups import (
    _convert_group,
    _convert_graphic_fallback,
    _graphic_preview_label,
    _replacement_payload_metadata,
    _package_rels_path,
    _package_content_types,
)
from .slide_to_svg_native_objects import (
    _roundtrip_chart_package,
    _roundtrip_graphic_frame_metadata,
    _render_graphic_table,
    _render_graphic_chart,
    _render_graphic_preview,
)
from .slide_to_svg_inheritance import (
    _emit_background,
    _emit_part_background,
    _emit_background_image,
    _theme_background_fill,
    _emit_inherited_shapes,
    _is_placeholder_node,
    _convert_placeholder_guide,
)
from .slide_to_svg_metadata import (
    _wrap_shape_group,
    _attrs_to_xml,
    _metadata_group_attrs,
    _diagnose_unsupported_effect,
    _geometry_group_attrs,
    _object_metadata,
    _shape_scope,
    _connector_metadata,
    _xml_escape,
    _xml_text_escape,
)

