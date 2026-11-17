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
    PartArtifact,
    SlideArtifact,
)

def _read_back_slide_transition(
    pkg: OoxmlPackage,
    slide: SlideRef,
    result: ConvertResult,
    options: ConvertOptions,
) -> None:
    """Recover one supported slide transition into the sidecar."""
    try:
        transition = import_slide_transition(
            pkg,
            slide,
            media_subdir=options.sound_subdir,
            resource_path_map=result.resource_inventory.path_map(),
        )
    except TransitionImportError as exc:
        message = f"Slide transition was not reconstructed: {exc}"
        if options.strict:
            raise ValueError(message) from exc
        append_diagnostic(
            result.diagnostics,
            ImportDiagnostic(
                code="transition-not-reconstructed",
                message=message,
                fallback=(
                    "keep this transition in the source PPTX through direct "
                    "native preservation"
                ),
                part_path=slide.part.path,
                slide_index=slide.index,
            ),
        )
    else:
        if transition is not None:
            slides = result.animation_config["slides"]
            if not isinstance(slides, dict):
                raise RuntimeError("internal animations.json slides must be an object")
            slides[f"slide_{slide.index:02d}"] = {
                "transition": transition.config,
            }
            for filename, payload in transition.media_files.items():
                existing = result.animation_media_files.get(filename)
                if existing is not None and existing != payload:
                    raise RuntimeError(
                        "Transition sound filename collision with different bytes: "
                        f"{filename}"
                    )
                result.animation_media_files[filename] = payload



def _read_back_slide_animation(
    pkg: OoxmlPackage,
    slide: SlideRef,
    artifact: SlideArtifact,
    result: ConvertResult,
    options: ConvertOptions,
) -> None:
    """Recover one finite object-animation sequence into the sidecar."""
    try:
        animation = import_slide_animation(
            pkg,
            slide,
            slide_svg=artifact.svg,
        )
    except AnimationImportError as exc:
        message = f"Object animation timing was not reconstructed: {exc}"
        if options.strict:
            raise ValueError(message) from exc
        append_diagnostic(
            result.diagnostics,
            ImportDiagnostic(
                code="animation-not-reconstructed",
                message=message,
                fallback=(
                    "keep this timing in the source PPTX through direct "
                    "native preservation"
                ),
                part_path=slide.part.path,
                slide_index=slide.index,
            ),
        )
        return
    if animation is None:
        return

    slides = result.animation_config["slides"]
    if not isinstance(slides, dict):
        raise RuntimeError("internal animations.json slides must be an object")
    slide_config = slides.setdefault(f"slide_{slide.index:02d}", {})
    if not isinstance(slide_config, dict):
        raise RuntimeError("internal animations.json slide row must be an object")
    slide_config["groups"] = animation.groups


def _convert_slide(
    pkg: OoxmlPackage,
    slide: SlideRef,
    palette: ColorPalette,
    options: ConvertOptions,
    diagnostics: list[ImportDiagnostic],
    theme_fonts: dict[str, str] | None = None,
    *,
    inheritance_mode: str | None = None,
) -> SlideArtifact:
    """Convert a single slide via the full shape pipeline.

    ``inheritance_mode`` overrides ``options.inheritance_mode`` so the
    orchestrator can render the same slide twice (once layered, once flat)
    when the user asked for ``"both"``. Pass ``"flat"`` or ``"layered"``;
    ``None`` falls back to ``options.inheritance_mode`` (used by direct
    callers that want a single mode).
    """
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .converter import (
        SlideArtifact,
    )
    mode = inheritance_mode or options.inheritance_mode
    if mode == "both":
        mode = "layered"  # primary view in both-mode
    show_inherited_shapes = part_show_master_sp(slide.part)
    svg, media = assemble_slide(
        pkg, slide, palette,
        theme_fonts=theme_fonts,
        media_subdir=options.images_subdir,
        embed_images=options.embed_images,
        keep_hidden=options.keep_hidden,
        inheritance_mode=mode,
        asset_name_map=options.asset_name_map,
        strict=options.strict,
        diagnostics=diagnostics,
        preserve_placeholder_inheritance=(
            options.roundtrip and mode == "layered"
        ),
    )
    return SlideArtifact(
        index=slide.index,
        svg=svg,
        media_files=media,
        layout_part_path=slide.layout.path if slide.layout else None,
        master_part_path=slide.master.path if slide.master else None,
        show_inherited_shapes=show_inherited_shapes,
    )


def _convert_inheritance_parts(
    pkg: OoxmlPackage,
    default_theme: PartRef | None,
    options: ConvertOptions,
    result: ConvertResult,
) -> None:
    """Render every master and layout in the deck to its own SVG (layered mode).

    We deliberately render *all* masters / layouts, not only the ones a slide
    references. Multi-style template packages routinely ship more design
    surfaces than the embedded sample slides exercise, and dropping unused
    ones discards the bulk of the template's design intent.
    """
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .converter import (
        _extract_theme_info,
        _make_palette,
        _render_part,
    )
    # Collect unique parts in document order so output filenames are
    # deterministic for a given .pptx.
    seen_masters: dict[str, PartRef] = {}
    for master in pkg.iter_all_masters():
        if master.path not in seen_masters:
            seen_masters[master.path] = master

    layouts_with_parent: list[tuple[PartRef, PartRef]] = []
    seen_layout_paths: set[str] = set()
    for layout, parent_master in pkg.iter_all_layouts_with_parent():
        if layout.path in seen_layout_paths:
            continue
        seen_layout_paths.add(layout.path)
        layouts_with_parent.append((layout, parent_master))

    for seq, part in enumerate(seen_masters.values(), start=1):
        theme = pkg.resolve_theme(part) or default_theme
        palette = _make_palette(
            part,
            theme,
            options,
            result,
            part_path=part.path,
        )
        _colors, fonts = _extract_theme_info(theme, palette) if theme is not None else ({}, result.theme_fonts)
        result.masters.append(_render_part(
            pkg, part, palette, options, result.diagnostics, fonts,
            role="master", seq=seq, theme_part=theme,
        ))
    for seq, (layout, parent_master) in enumerate(layouts_with_parent, start=1):
        theme = pkg.resolve_theme(parent_master) or default_theme
        palette = _make_palette(
            parent_master,
            theme,
            options,
            result,
            part_path=layout.path,
        )
        _colors, fonts = _extract_theme_info(theme, palette) if theme is not None else ({}, result.theme_fonts)
        result.layouts.append(_render_part(
            pkg, layout, palette, options, result.diagnostics, fonts,
            role="layout", seq=seq, parent_master=parent_master,
            theme_part=theme,
        ))


def _render_part(
    pkg: OoxmlPackage,
    part: PartRef,
    palette: ColorPalette,
    options: ConvertOptions,
    diagnostics: list[ImportDiagnostic],
    theme_fonts: dict[str, str],
    *,
    role: str,
    seq: int,
    parent_master: PartRef | None = None,
    theme_part: PartRef | None = None,
) -> PartArtifact:
    """Render a master/layout part, returning a PartArtifact with output filename."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .converter import (
        PartArtifact,
    )
    svg, media = assemble_part_solo(
        pkg, part, palette,
        role=role,
        parent_master=parent_master,
        theme_fonts=theme_fonts,
        media_subdir=options.images_subdir,
        embed_images=options.embed_images,
        keep_hidden=options.keep_hidden,
        asset_name_map=options.asset_name_map,
        strict=options.strict,
        diagnostics=diagnostics,
    )
    stem = PurePosixPath(part.path).stem  # e.g. "slideLayout3"
    safe_stem = re.sub(r"[^A-Za-z0-9_-]+", "_", stem).strip("_") or role
    filename = f"{role}_{seq:02d}_{safe_stem}.svg"
    return PartArtifact(
        role=role,
        part_path=part.path,
        filename=filename,
        svg=svg,
        media_files=media,
        parent_master_part_path=parent_master.path if parent_master is not None else None,
        theme_part_path=theme_part.path if theme_part is not None else None,
        show_master_shapes=(
            part_show_master_sp(part) if role == "layout" else True
        ),
    )


