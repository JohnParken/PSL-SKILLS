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

def _path_lexists(path: Path) -> bool:
    """Return whether a path or symlink exists without following the symlink."""
    return path.exists() or path.is_symlink()


def _managed_svg_paths(output_dir: Path) -> list[Path]:
    """Return converter-owned SVG files without traversing user directories."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .converter import (
        _MANAGED_FLAT_SVG_RE,
        _MANAGED_PRIMARY_SVG_RE,
        _path_lexists,
    )
    managed: list[Path] = []
    for relative_dir, filename_re, carries_inheritance in (
        (Path("svg"), _MANAGED_PRIMARY_SVG_RE, True),
        (Path("svg-flat"), _MANAGED_FLAT_SVG_RE, False),
        (ROUNDTRIP_LAYERED_SVG_DIR, _MANAGED_PRIMARY_SVG_RE, True),
        (ROUNDTRIP_FLAT_SVG_DIR, _MANAGED_FLAT_SVG_RE, False),
        (AUTHORING_SVG_DIR, _MANAGED_PRIMARY_SVG_RE, False),
        (AUTHORING_SVG_FLAT_DIR, _MANAGED_FLAT_SVG_RE, False),
    ):
        svg_dir = output_dir / relative_dir
        if svg_dir.is_symlink():
            managed.append(svg_dir)
            continue
        if not svg_dir.is_dir():
            continue
        managed.extend(
            path
            for path in svg_dir.iterdir()
            if filename_re.fullmatch(path.name)
            and (path.is_file() or path.is_symlink())
        )
        inheritance = svg_dir / "inheritance.json"
        if carries_inheritance and _path_lexists(inheritance):
            managed.append(inheritance)
        if relative_dir in {AUTHORING_SVG_DIR, AUTHORING_SVG_FLAT_DIR}:
            for filename in (
                "authoring_manifest.json",
                "authoring_summary.json",
            ):
                sidecar = svg_dir / filename
                if _path_lexists(sidecar):
                    managed.append(sidecar)
    return managed


def _managed_vector_asset_paths(output_dir: Path) -> set[Path]:
    """Return the previous converter-owned decoration inventory and assets."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .converter import (
        _path_lexists,
    )
    managed: set[Path] = set()
    for authoring_dir in (AUTHORING_SVG_DIR, AUTHORING_SVG_FLAT_DIR):
        inventory_path = (
            output_dir / f"{authoring_dir.name}_vector_asset_inventory.json"
        )
        if not _path_lexists(inventory_path):
            continue
        managed.add(inventory_path)
        if inventory_path.is_symlink() or not inventory_path.is_file():
            continue
        try:
            payload = json.loads(inventory_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        icons_dir_value = (
            payload.get("icons_dir") if isinstance(payload, dict) else None
        )
        icons_dir = (
            Path(icons_dir_value)
            if isinstance(icons_dir_value, str) and icons_dir_value
            else Path("icons")
        )
        if (
            icons_dir.drive
            or icons_dir.anchor
            or icons_dir.is_absolute()
            or ".." in icons_dir.parts
        ):
            continue
        assets = payload.get("assets") if isinstance(payload, dict) else None
        if not isinstance(assets, list):
            continue
        for item in assets:
            value = item.get("asset") if isinstance(item, dict) else None
            if not isinstance(value, str):
                continue
            asset_path = Path(value)
            if (
                asset_path.drive
                or asset_path.anchor
                or asset_path.is_absolute()
                or ".." in asset_path.parts
                or not asset_path.parts
            ):
                continue
            path = (
                asset_path
                if asset_path.parts[0] == "icons"
                else icons_dir / asset_path
            )
            if not path.parts or path.parts[0] != "icons":
                continue
            target = output_dir / path
            if _path_lexists(target):
                managed.add(target)
    return managed


def _managed_report_artifact_paths(output_dir: Path) -> set[Path]:
    """Return optional artifacts owned by the previous conversion report."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .converter import (
        _MANAGED_TRANSITION_SOUND_RE,
    )
    report_path = conversion_report_path(output_dir)
    if report_path.is_symlink() or not report_path.is_file():
        return set()
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return set()
    artifacts = report.get("artifacts")
    if not isinstance(artifacts, dict):
        return set()
    if artifacts.get("animationConfig") != "animations.json":
        return set()

    managed = {Path("animations.json")}
    source_template = artifacts.get("sourceTemplate")
    if source_template == SOURCE_TEMPLATE_NAME:
        managed.add(Path(str(source_template)))
    native_structure = artifacts.get("nativeStructure")
    if native_structure == NATIVE_STRUCTURE_NAME:
        managed.add(Path(str(native_structure)))
    if artifacts.get("roundtripManifest") == ROUNDTRIP_MANIFEST_PATH.as_posix():
        managed.add(ROUNDTRIP_MANIFEST_PATH)
    embedded_font_paths = [artifacts.get("embeddedFontManifest")]
    raw_font_parts = artifacts.get("embeddedFontParts")
    if isinstance(raw_font_parts, list):
        embedded_font_paths.extend(raw_font_parts)
    font_prefix = FONT_BUNDLE_DIR.parts
    for value in embedded_font_paths:
        if not isinstance(value, str):
            continue
        path = Path(value)
        if (
            path.drive
            or path.anchor
            or path.is_absolute()
            or ".." in path.parts
            or path.parts[:len(font_prefix)] != font_prefix
            or path.suffix.lower() not in {".json", ".fntdata"}
        ):
            continue
        managed.add(path)
    animation_media = artifacts.get("animationMedia")
    managed_lists = [animation_media, artifacts.get("resources"), artifacts.get("notes")]
    managed_resource_roots = {
        "audio",
        "images",
        "native-payloads",
        "notes",
        "sounds",
        "video",
    }
    for values in managed_lists:
        if not isinstance(values, list):
            continue
        for value in values:
            if not isinstance(value, str):
                continue
            path = Path(value)
            if (
                path.drive
                or path.anchor
                or path.is_absolute()
                or not path.parts
                or ".." in path.parts
                or path.parts[0] not in managed_resource_roots
            ):
                continue
            managed.add(path)
    if not isinstance(animation_media, list):
        return managed
    for value in animation_media:
        if not isinstance(value, str):
            continue
        path = Path(value)
        if (
            path.drive
            or path.anchor
            or path.is_absolute()
            or not path.parts
            or ".." in path.parts
            or not _MANAGED_TRANSITION_SOUND_RE.fullmatch(path.name)
        ):
            continue
        managed.add(path)
    return managed


def _referenced_local_paths(
    output_dir: Path,
    svg_paths: list[Path],
) -> set[Path]:
    """Resolve local media referenced by converter-owned SVGs."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .converter import (
        _SVG_HREF_RE,
    )
    referenced: set[Path] = set()
    output_abs = output_dir.absolute()
    for svg_path in svg_paths:
        if svg_path.is_symlink() or not svg_path.is_file():
            continue
        try:
            svg_text = svg_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        for raw_href in _SVG_HREF_RE.findall(svg_text):
            href = unescape(raw_href)
            parsed = urlsplit(href)
            if parsed.scheme or parsed.netloc or not parsed.path:
                continue
            href_path = unquote(parsed.path)
            if Path(href_path).is_absolute():
                continue
            target = Path(os.path.normpath(str(svg_path.parent / href_path)))
            try:
                relative = target.absolute().relative_to(output_abs)
            except ValueError:
                continue
            if relative.parts:
                referenced.add(relative)
    return referenced


def _validated_relative_paths(paths: set[str | Path]) -> set[Path]:
    """Normalize caller-supplied managed paths and reject output escapes."""
    normalized: set[Path] = set()
    for value in paths:
        path = Path(value)
        if (
            path.drive
            or path.anchor
            or path.is_absolute()
            or not path.parts
            or ".." in path.parts
        ):
            raise ValueError(f"Managed artifact path must stay relative: {value}")
        normalized.add(path)
    return normalized


def _reject_symlink_ancestors(
    root: Path,
    relative_paths: set[Path],
) -> None:
    """Reject managed paths that would traverse a preserved user symlink."""
    for relative in relative_paths:
        current = root
        for component in relative.parts[:-1]:
            current /= component
            if current.is_symlink():
                raise RuntimeError(
                    "Managed artifact path crosses an unmanaged symlink: "
                    f"{relative}"
                )


