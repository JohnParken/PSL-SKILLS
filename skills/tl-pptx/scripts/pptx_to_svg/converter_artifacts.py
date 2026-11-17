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

def _write_artifact_tree(
    output_dir: Path,
    result: ConvertResult,
    options: ConvertOptions,
) -> None:
    """Write a complete converter roster into an empty staging directory.

    Layout:
      - normal conversion uses ``svg/`` and optional ``svg-flat/``
      - round-trip conversion keeps immutable SVG backing under ``analysis/``
        and publishes only ``authoring-svg-flat/`` as the editable page source
      - ``images/``     shared image assets, referenced by both views
      - semantic resource directories for sounds, audio, video, and opaque
        native payloads when the source package contains them
    """
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .converter import (
        _ROUNDTRIP_VECTOR_MIN_BYTES,
        _ROUNDTRIP_VECTOR_MIN_DECORATION_BYTES,
        _ROUNDTRIP_VECTOR_MIN_DRAWABLES,
        _SVG_HREF_ATTRIBUTE_RE,
        _path_lexists,
        _validate_media_filename,
        _write_animation_config,
        _write_conversion_report,
        _write_inheritance_json,
        _write_roundtrip_manifest,
        _write_speaker_notes,
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    svg_dir = output_dir / (
        ROUNDTRIP_LAYERED_SVG_DIR if options.roundtrip else Path("svg")
    )
    svg_dir.mkdir(parents=True, exist_ok=True)
    media_dir = output_dir / options.images_subdir
    sound_dir = output_dir / options.sound_subdir
    media_written: dict[str, bytes] = {}
    sounds_written: dict[str, bytes] = {}

    def _svg_for_target(svg: str, target_dir: Path) -> str:
        """Rebase generated local hrefs from the normal one-level SVG root."""
        source_dir = output_dir / "svg"
        project_root = output_dir.resolve()

        def replace_href(match: re.Match[str]) -> str:
            raw = unescape(match.group("value"))
            parsed = urlsplit(raw)
            if (
                not raw
                or raw.startswith("#")
                or parsed.scheme
                or parsed.netloc
                or not parsed.path
            ):
                return match.group(0)
            resolved = (source_dir / unquote(parsed.path)).resolve()
            try:
                resolved.relative_to(project_root)
            except ValueError as exc:
                raise RuntimeError(
                    f"Generated SVG resource escapes the workspace: {raw!r}"
                ) from exc
            relative = os.path.relpath(resolved, target_dir).replace(os.sep, "/")
            rebased = urlunsplit(("", "", relative, parsed.query, parsed.fragment))
            return f"{match.group('prefix')}{quoteattr(rebased)}"

        return _SVG_HREF_ATTRIBUTE_RE.sub(replace_href, svg)

    def _collect_media(media: dict[str, bytes]) -> None:
        for filename, blob in media.items():
            _validate_media_filename(filename)
            if filename in media_written:
                if media_written[filename] != blob:
                    raise RuntimeError(
                        f"Asset filename collision with different bytes: {filename}"
                    )
                continue
            media_written[filename] = blob

    # Layered mode: write masters and layouts first so they sort ahead of slides.
    for art in result.masters:
        (svg_dir / art.filename).write_text(
            _svg_for_target(art.svg, svg_dir),
            encoding="utf-8",
        )
        _collect_media(art.media_files)
    for art in result.layouts:
        (svg_dir / art.filename).write_text(
            _svg_for_target(art.svg, svg_dir),
            encoding="utf-8",
        )
        _collect_media(art.media_files)

    # Slides (primary view).
    for art in result.slides:
        target = svg_dir / f"slide_{art.index:02d}.svg"
        target.write_text(
            _svg_for_target(art.svg, target.parent),
            encoding="utf-8",
        )
        _collect_media(art.media_files)
    for filename, blob in result.animation_media_files.items():
        _validate_media_filename(filename)
        existing = sounds_written.get(filename)
        if existing is not None and existing != blob:
            raise RuntimeError(
                f"Sound filename collision with different bytes: {filename}"
            )
        sounds_written[filename] = blob

    # Inheritance graph alongside the layered SVGs (only meaningful when we
    # actually emitted a layered view).
    if options.inheritance_mode in {"layered", "both"}:
        _write_inheritance_json(svg_dir, result)

    # Flat companion view (only when result.flat_slides is populated).
    if result.flat_slides:
        flat_dir = output_dir / (
            ROUNDTRIP_FLAT_SVG_DIR if options.roundtrip else Path("svg-flat")
        )
        flat_dir.mkdir(parents=True, exist_ok=True)
        for art in result.flat_slides:
            target = flat_dir / f"slide_{art.index:02d}.svg"
            target.write_text(
                _svg_for_target(art.svg, target.parent),
                encoding="utf-8",
            )
            _collect_media(art.media_files)

    _write_animation_config(output_dir, result)
    _write_speaker_notes(output_dir, result, write_total=not options.roundtrip)
    if result.native_structure is not None:
        if result.source_pptx_path is None:
            raise RuntimeError(
                "Round-trip source structure is missing its source PPTX path"
            )
        source_target = output_dir / SOURCE_TEMPLATE_NAME
        source_target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(result.source_pptx_path, source_target)
        structure_target = output_dir / NATIVE_STRUCTURE_NAME
        structure_target.parent.mkdir(parents=True, exist_ok=True)
        structure_target.write_text(
            json.dumps(
                result.native_structure,
                ensure_ascii=False,
                indent=2,
            ) + "\n",
            encoding="utf-8",
        )
    embedded_fonts_descriptor: dict[str, object] | None = None
    embedded_font_paths: tuple[str, ...] = ()
    if result.embedded_fonts is not None:
        (
            embedded_fonts_descriptor,
            embedded_font_paths,
        ) = write_embedded_font_bundle(output_dir, result.embedded_fonts)
    _write_conversion_report(
        output_dir,
        result,
        options,
        embedded_fonts_descriptor=embedded_fonts_descriptor,
        embedded_font_paths=embedded_font_paths,
    )
    write_workspace_resources(
        output_dir,
        result.resource_inventory,
        include_images=not options.embed_images,
    )
    if media_written:
        media_dir.mkdir(parents=True, exist_ok=True)
    for filename, blob in media_written.items():
        target = media_dir / filename
        if _path_lexists(target):
            if (
                target.is_symlink()
                or not target.is_file()
                or target.read_bytes() != blob
            ):
                raise RuntimeError(
                    f"Asset filename collision with different bytes: {filename}"
                )
            continue
        target.write_bytes(blob)
    if sounds_written:
        sound_dir.mkdir(parents=True, exist_ok=True)
    for filename, blob in sounds_written.items():
        target = sound_dir / filename
        if _path_lexists(target):
            if (
                target.is_symlink()
                or not target.is_file()
                or target.read_bytes() != blob
            ):
                raise RuntimeError(
                    f"Sound filename collision with different bytes: {filename}"
                )
            continue
        target.write_bytes(blob)
    if result.native_structure is not None:
        flat_dir = output_dir / ROUNDTRIP_FLAT_SVG_DIR
        authoring_dir = output_dir / AUTHORING_SVG_FLAT_DIR
        source_proxy_dir = media_dir / "source-object-previews"
        mapping = [
            (source, authoring_dir / source.name)
            for source in sorted(flat_dir.glob("slide_*.svg"))
        ]
        if len(mapping) != len(result.slides):
            raise RuntimeError(
                "Round-trip flat backing roster does not match the slide roster"
            )
        project_svg_batch(
            mapping,
            flat_dir,
            authoring_dir,
            force=False,
            projection_kind="flat",
            source_proxy_dir=source_proxy_dir,
        )
        extract_directory(
            authoring_dir,
            output_dir / "icons",
            "imported",
            min_drawables=_ROUNDTRIP_VECTOR_MIN_DRAWABLES,
            min_bytes=_ROUNDTRIP_VECTOR_MIN_BYTES,
            min_decoration_bytes=_ROUNDTRIP_VECTOR_MIN_DECORATION_BYTES,
            inplace=True,
            id_prefix="flat",
            inventory_path=(
                output_dir
                / f"{authoring_dir.name}_vector_asset_inventory.json"
            ),
        )
        _write_roundtrip_manifest(output_dir, result, options)


