"""Top-level orchestrator for PPTX -> SVG conversion.

Public API: convert_pptx_to_svg(pptx_path, output_dir, options).

Composes the per-slide pipeline:
    OoxmlPackage -> shape_walker.walk_sp_tree
                 -> per-shape dispatch (prstgeom / txbody / pic / ...)
                 -> assembled SVG text + extracted media files

Stages B-F will fill in the per-shape dispatch. For Stage A this entry just
loads the package and reports basic per-slide structure to verify wiring.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import posixpath
import re
import shutil
import tempfile
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from html import unescape
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit, urlunsplit
from xml.etree import ElementTree as ET
from xml.sax.saxutils import quoteattr

from extract_svg_assets import extract_directory
from pptx_embedded_fonts import (
    FONT_BUNDLE_DIR,
    EmbeddedFontBundle,
    EmbeddedFontError,
    capture_embedded_fonts,
    write_embedded_font_bundle,
)
from pptx_workspace import (
    AUTHORING_SVG_DIR,
    AUTHORING_SVG_FLAT_DIR,
    CONVERSION_REPORT_PATH,
    NATIVE_STRUCTURE_PATH,
    ROUNDTRIP_FLAT_SVG_DIR,
    ROUNDTRIP_LAYERED_SVG_DIR,
    ROUNDTRIP_MANIFEST_PATH,
    SOURCE_PPTX_PATH,
    PackageResourceInventory,
    conversion_report_path,
    inventory_package_resources,
    reject_removed_workspace_layout,
    slide_animation_config_sha256,
    write_workspace_resources,
)
from svg_authoring_view import project_svg_batch
from svg_to_pptx.animation_config import (
    validate_animation_config_errors,
    validate_transition_config,
)
from svg_to_pptx.pptx_package.narration import narration_audio_parts
from template_import.manifest import (
    count_drawable_shapes,
    extract_placeholders,
    part_display_name,
)
from template_import.native_structure import (
    CONTRACT_NAME as NATIVE_STRUCTURE_NAME,
    SOURCE_TEMPLATE_NAME,
    build_native_structure,
)

from .animation_import import (
    AnimationImportError,
    import_slide_animation,
)
from .color_resolver import ColorPalette
from .emu_units import NS
from .import_diagnostics import ImportDiagnostic, append_diagnostic
from .ooxml_loader import (
    OoxmlPackage,
    PartRef,
    SlideRef,
    part_show_master_sp,
)
from .notes_import import ImportedSpeakerNote, import_speaker_notes
from .slide_to_svg import assemble_part_solo, assemble_slide
from .transition_import import (
    TransitionImportError,
    import_slide_transition,
)


_CJK_THEME_SCRIPTS = frozenset({"Hans", "Hant", "Jpan", "Hang"})
_MANAGED_PRIMARY_SVG_RE = re.compile(
    r"(?:slide_\d+|master_\d+_[A-Za-z0-9_-]+|layout_\d+_[A-Za-z0-9_-]+)\.svg"
)
_MANAGED_FLAT_SVG_RE = re.compile(r"slide_\d+\.svg")
_MANAGED_TRANSITION_SOUND_RE = re.compile(
    r"transition_sound_[0-9a-f]{16}\.wav"
)
_ROUNDTRIP_VECTOR_MIN_DRAWABLES = 2
_ROUNDTRIP_VECTOR_MIN_BYTES = 512
_ROUNDTRIP_VECTOR_MIN_DECORATION_BYTES = 512
_SVG_HREF_RE = re.compile(
    r"\b(?:href|xlink:href)\s*=\s*[\"']([^\"']+)[\"']"
)
_SVG_HREF_ATTRIBUTE_RE = re.compile(
    r"(?P<prefix>\b(?:href|xlink:href)\s*=\s*)"
    r"(?P<quote>[\"'])(?P<value>[^\"']+)(?P=quote)"
)


@dataclass
class ConvertOptions:
    """Convert behavior knobs.

    images_subdir: where to write image files relative to output_dir. SVG image
        href will use './<images_subdir>/<filename>'.
    sound_subdir: where to write transition/object cue audio relative to
        output_dir. New workspaces use ``sounds``.
    embed_images: when True, base64-encode images inline instead of writing
        files. Default False (matches svg_to_pptx default of external images).
    keep_hidden: include shapes marked hidden="1". Default False.
    inheritance_mode: how to render master/layout shapes per slide SVG.
        - "both" (default): emit both views — layered under svg/ for template
          designers (master/layout/slide as separate files) and flat under
          svg-flat/ for previewers (each slide self-contained). Costs roughly
          1.3-1.5× converter time and ~1.6-2× disk vs. either single mode.
        - "layered": skip inherited shapes inside the slide. The orchestrator
          renders every master and layout to its own SVG, plus
          svg/inheritance.json describing the reuse graph. Optimised for
          template authors who need to see "what is shared vs. unique".
        - "flat": inline the inherited shapes visible under the source
          ``showMasterSp`` flags for preview pages and screenshot pipelines.
    strict: stop on the first unsupported or malformed source construct.
        Default False keeps usable content and records structured diagnostics.
    roundtrip: create the fixed semantic workspace and source-preserving
        package contracts used by the editable ``authoring-svg-flat/`` route.
    """

    images_subdir: str = "images"
    sound_subdir: str = "sounds"
    embed_images: bool = False
    keep_hidden: bool = False
    inheritance_mode: str = "both"
    asset_name_map: dict[str, str] = field(default_factory=dict)
    strict: bool = False
    roundtrip: bool = False


@dataclass
class PartArtifact:
    """Result of converting a master or layout part to SVG (layered mode only)."""

    role: str  # "master" | "layout"
    part_path: str  # OOXML part path, e.g. "ppt/slideLayouts/slideLayout3.xml"
    filename: str  # output svg filename, e.g. "layout_03_title.xml.svg"
    svg: str
    media_files: dict[str, bytes] = field(default_factory=dict)
    parent_master_part_path: str | None = None
    theme_part_path: str | None = None
    show_master_shapes: bool = True


@dataclass
class SlideArtifact:
    """Result of converting a single slide."""

    index: int  # 1-based
    svg: str
    media_files: dict[str, bytes] = field(default_factory=dict)
    layout_part_path: str | None = None
    master_part_path: str | None = None
    show_inherited_shapes: bool = True


@dataclass
class ConvertResult:
    """Result of converting an entire .pptx.

    ``slides`` holds the layered/primary view (or, in pure flat mode, the flat
    view). ``flat_slides`` is populated only in ``"both"`` mode and contains
    self-contained renderings of every slide; callers that don't care about
    the flat view can ignore it.
    """

    slides: list[SlideArtifact] = field(default_factory=list)
    canvas_px: tuple[float, float] = (1280.0, 720.0)
    theme_colors: dict[str, str] = field(default_factory=dict)
    theme_fonts: dict[str, str] = field(default_factory=dict)
    theme_xml: bytes | None = None
    embedded_fonts: EmbeddedFontBundle | None = None
    resource_inventory: PackageResourceInventory = field(
        default_factory=PackageResourceInventory
    )
    speaker_notes: tuple[ImportedSpeakerNote, ...] = ()
    native_structure: dict[str, object] | None = None
    source_pptx_path: Path | None = None
    layouts: list[PartArtifact] = field(default_factory=list)
    masters: list[PartArtifact] = field(default_factory=list)
    flat_slides: list[SlideArtifact] = field(default_factory=list)
    master_themes: dict[str, dict[str, object]] = field(default_factory=dict)
    diagnostics: list[ImportDiagnostic] = field(default_factory=list)
    animation_config: dict[str, object] = field(
        default_factory=lambda: {
            "version": 1,
            "defaults": {
                "transition": {
                    "effect": "none",
                    "duration": 0.0,
                },
            },
            "slides": {},
        }
    )
    animation_media_files: dict[str, bytes] = field(default_factory=dict)
    source_file: str = ""
    strict: bool = False



# Stable upstream API facade, split by conversion responsibility.
from .converter_contract import (
    _validate_resource_subdir,
    _validate_media_filename,
    _extract_theme_info,
    _palette_diagnostic_sink,
    _roundtrip_native_structure,
    _annotate_roundtrip_slide_roots,
    _make_palette,
)
from .converter_entry import (
    convert_pptx_to_svg,
)
from .converter_slides import (
    _read_back_slide_transition,
    _read_back_slide_animation,
    _convert_slide,
    _convert_inheritance_parts,
    _render_part,
)
from .converter_managed_paths import (
    _path_lexists,
    _managed_svg_paths,
    _managed_vector_asset_paths,
    _managed_report_artifact_paths,
    _referenced_local_paths,
    _validated_relative_paths,
    _reject_symlink_ancestors,
)
from .converter_publication import (
    _remove_managed_paths,
    _overlay_staged_tree,
    publish_staged_workspace,
)
from .converter_artifacts import (
    _write_artifact_tree,
)
from .converter_manifest import (
    _sha256_file,
    _write_speaker_notes,
    _write_roundtrip_manifest,
    _write_artifacts,
)
from .converter_reports import (
    _write_conversion_report,
    _write_animation_config,
    _write_inheritance_json,
)

