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

def convert_pptx_to_svg(
    pptx_path: Path,
    output_dir: Path | None = None,
    options: ConvertOptions | None = None,
) -> ConvertResult:
    """Convert a .pptx file to one SVG per slide.

    Args:
        pptx_path: Source .pptx file.
        output_dir: When given, write svg/<slide_NN>.svg + media files there.
            When None, files are not written; callers can read SlideArtifact.svg.
        options: ConvertOptions; defaults to ConvertOptions().

    Returns:
        ConvertResult with per-slide SVG strings and resolved theme info.
    """
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .converter import (
        ConvertOptions,
        ConvertResult,
        _annotate_roundtrip_slide_roots,
        _convert_inheritance_parts,
        _convert_slide,
        _extract_theme_info,
        _make_palette,
        _read_back_slide_animation,
        _read_back_slide_transition,
        _roundtrip_native_structure,
        _validate_resource_subdir,
        _write_artifacts,
    )
    options = options or ConvertOptions()
    if options.inheritance_mode not in {"flat", "layered", "both"}:
        raise ValueError(
            f"inheritance_mode must be 'flat', 'layered', or 'both', "
            f"got {options.inheritance_mode!r}"
        )
    if options.roundtrip and options.inheritance_mode != "both":
        raise ValueError(
            "roundtrip requires inheritance_mode 'both' so the editable flat "
            "authoring view and layered source backing are both complete"
        )
    if options.roundtrip and (
        options.images_subdir != "images"
        or options.sound_subdir != "sounds"
        or options.embed_images
    ):
        raise ValueError(
            "roundtrip uses fixed images/ and sounds/ resource directories "
            "and does not support inline images"
        )
    if not options.embed_images:
        _validate_resource_subdir(options.images_subdir)
    _validate_resource_subdir(options.sound_subdir)
    emit_layered = options.inheritance_mode in {"layered", "both"}
    emit_flat = options.inheritance_mode in {"flat", "both"}
    result = ConvertResult(
        source_file=pptx_path.name,
        strict=options.strict,
    )

    with OoxmlPackage(pptx_path) as pkg:
        if pkg.zip is not None:
            result.resource_inventory = inventory_package_resources(pkg.zip)
            image_name_map = result.resource_inventory.image_name_map()
            image_name_map.update(options.asset_name_map)
            options = replace(options, asset_name_map=image_name_map)
        result.speaker_notes = import_speaker_notes(pkg, result.diagnostics)
        result.canvas_px = pkg.slide_size_px

        # Default theme summary is kept for compatibility; conversion itself
        # resolves palette/fonts per slide master.
        first_slide = pkg.get_slide(1)
        default_master = first_slide.master if first_slide else None
        default_theme = pkg.resolve_theme(default_master)
        palette = _make_palette(
            default_master,
            default_theme,
            options,
            result,
            part_path=default_theme.path if default_theme is not None else "",
        )
        if default_theme is not None:
            result.theme_colors, result.theme_fonts = _extract_theme_info(default_theme, palette)
            result.theme_xml = ET.tostring(default_theme.xml, encoding="utf-8")
        if pkg.presentation is not None and pkg.zip is not None:
            try:
                result.embedded_fonts = capture_embedded_fonts(
                    pkg.presentation.xml,
                    pkg.presentation.rels,
                    pkg.zip.read,
                )
            except EmbeddedFontError as exc:
                if options.strict:
                    raise
                append_diagnostic(
                    result.diagnostics,
                    ImportDiagnostic(
                        code="embedded-fonts-omitted",
                        message=str(exc),
                        fallback=(
                            "keep editable text and rely on an installed or "
                            "substitute font"
                        ),
                        part_path=pkg.presentation.path,
                    ),
                )

        for master in pkg.iter_all_masters():
            theme = pkg.resolve_theme(master) or default_theme
            pal = _make_palette(
                master,
                theme,
                options,
                result,
                part_path=master.path,
            )
            colors, fonts = _extract_theme_info(theme, pal) if theme is not None else ({}, {})
            result.master_themes[master.path] = {
                "themePath": theme.path if theme is not None else None,
                "colors": colors,
                "fonts": fonts,
            }

        # Per-slide conversion. The primary view is layered when emitted
        # (template designers care most about that one); the flat view is
        # rendered alongside when needed.
        primary_mode = "layered" if emit_layered else "flat"
        for slide in pkg.iter_slides():
            _read_back_slide_transition(
                pkg,
                slide,
                result,
                options,
            )
            slide_theme = pkg.resolve_theme(slide.master) or default_theme
            slide_palette = _make_palette(
                slide.master,
                slide_theme,
                options,
                result,
                part_path=slide.part.path,
                slide_index=slide.index,
            )
            _colors, slide_fonts = (
                _extract_theme_info(slide_theme, slide_palette)
                if slide_theme is not None
                else ({}, result.theme_fonts)
            )
            artifact = _convert_slide(
                pkg,
                slide,
                slide_palette,
                options,
                result.diagnostics,
                slide_fonts,
                inheritance_mode=primary_mode,
            )
            result.slides.append(artifact)
            _read_back_slide_animation(
                pkg,
                slide,
                artifact,
                result,
                options,
            )
        if emit_layered and emit_flat:
            for slide in pkg.iter_slides():
                slide_theme = pkg.resolve_theme(slide.master) or default_theme
                slide_palette = _make_palette(
                    slide.master,
                    slide_theme,
                    options,
                    result,
                    part_path=slide.part.path,
                    slide_index=slide.index,
                )
                _colors, slide_fonts = (
                    _extract_theme_info(slide_theme, slide_palette)
                    if slide_theme is not None
                    else ({}, result.theme_fonts)
                )
                artifact = _convert_slide(
                    pkg,
                    slide,
                    slide_palette,
                    options,
                    result.diagnostics,
                    slide_fonts,
                    inheritance_mode="flat",
                )
                result.flat_slides.append(artifact)

        # Layered mode: also render each master / layout once.
        if emit_layered:
            _convert_inheritance_parts(pkg, default_theme, options, result)
        if options.roundtrip:
            result.native_structure = _roundtrip_native_structure(pkg, pptx_path)
            result.source_pptx_path = pptx_path
            _annotate_roundtrip_slide_roots(
                result.slides,
                result.native_structure,
            )

    if output_dir is not None:
        _write_artifacts(output_dir, result, options)

    return result


