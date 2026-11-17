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



# Upstream MIT, Hugo He 2025-2026; algorithms retained during module split.
from .converter import (
    ConvertOptions,
    ConvertResult,
)

def _write_conversion_report(
    output_dir: Path,
    result: ConvertResult,
    options: ConvertOptions,
    *,
    embedded_fonts_descriptor: dict[str, object] | None = None,
    embedded_font_paths: tuple[str, ...] = (),
) -> None:
    """Write the user-visible tolerant-import report."""
    animation_media = [
        (PurePosixPath(options.sound_subdir) / filename).as_posix()
        for filename in sorted(result.animation_media_files)
    ]
    source_theme: dict[str, object] = {
        "colors": result.theme_colors,
        "fonts": result.theme_fonts,
    }
    if result.theme_xml is not None:
        source_theme["ooxml"] = {
            "encoding": "base64",
            "sha256": hashlib.sha256(result.theme_xml).hexdigest(),
            "payload": base64.b64encode(result.theme_xml).decode("ascii"),
        }
    source_document: dict[str, object] = {
        "canvasPx": {
            "width": result.canvas_px[0],
            "height": result.canvas_px[1],
        },
        "theme": source_theme,
    }
    if embedded_fonts_descriptor is not None:
        source_document["embeddedFonts"] = embedded_fonts_descriptor
    artifacts: dict[str, object] = {
        "animationConfig": "animations.json",
        "animationMedia": animation_media,
        "resources": [
            resource.workspace_path
            for resource in result.resource_inventory.resources
            if not (options.embed_images and resource.kind == "image")
        ],
        "notes": [
            (Path("notes") / note.filename).as_posix()
            for note in result.speaker_notes
        ] + (
            ["notes/total.md"]
            if result.speaker_notes and not options.roundtrip
            else []
        ),
    }
    if embedded_font_paths:
        artifacts["embeddedFontManifest"] = embedded_font_paths[-1]
        artifacts["embeddedFontParts"] = list(embedded_font_paths[:-1])
    if result.native_structure is not None:
        artifacts["sourceTemplate"] = SOURCE_TEMPLATE_NAME
        artifacts["nativeStructure"] = NATIVE_STRUCTURE_NAME
        artifacts["roundtripManifest"] = ROUNDTRIP_MANIFEST_PATH.as_posix()
    report = {
        "schemaVersion": 1,
        "source": result.source_file,
        "mode": "strict" if result.strict else "tolerant",
        "summary": {
            "slides": len(result.slides),
            "warnings": len(result.diagnostics),
        },
        "artifacts": artifacts,
        "sourceDocument": source_document,
        "diagnostics": [item.to_dict() for item in result.diagnostics],
    }
    report_path = output_dir / CONVERSION_REPORT_PATH
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _write_animation_config(output_dir: Path, result: ConvertResult) -> None:
    """Write the canonical transition/object-motion sidecar."""
    errors = list(
        dict.fromkeys(
            validate_transition_config(result.animation_config)
            + validate_animation_config_errors(result.animation_config)
        )
    )
    if errors:
        raise RuntimeError(
            "Generated animations.json is invalid: " + "; ".join(errors)
        )
    (output_dir / "animations.json").write_text(
        json.dumps(
            result.animation_config,
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )


def _write_inheritance_json(svg_dir: Path, result: ConvertResult) -> None:
    """Record layered parentage plus source-owned shape-visibility booleans."""
    layout_by_path = {art.part_path: art.filename for art in result.layouts}
    master_by_path = {art.part_path: art.filename for art in result.masters}

    inheritance = {
        "masters": [
            {
                "file": art.filename,
                "partPath": art.part_path,
                "themePath": art.theme_part_path,
            }
            for art in result.masters
        ],
        "layouts": [
            {
                "file": art.filename,
                "partPath": art.part_path,
                "master": master_by_path.get(art.parent_master_part_path or ""),
                "parentPartPath": art.parent_master_part_path,
                "themePath": art.theme_part_path,
                "showMasterShapes": art.show_master_shapes,
            }
            for art in result.layouts
        ],
        "slides": [
            {
                "file": f"slide_{slide.index:02d}.svg",
                "index": slide.index,
                "layout": layout_by_path.get(slide.layout_part_path or ""),
                "master": master_by_path.get(slide.master_part_path or ""),
                "showInheritedShapes": slide.show_inherited_shapes,
            }
            for slide in result.slides
        ],
    }
    (svg_dir / "inheritance.json").write_text(
        json.dumps(inheritance, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
