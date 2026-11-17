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
    SlideArtifact,
)

def _validate_resource_subdir(value: str) -> None:
    """Reject media output paths that can escape the conversion workspace."""
    path = Path(value)
    if path.drive or path.anchor or path.is_absolute() or ".." in path.parts:
        raise ValueError(
            f"resource subdirectory must stay within the output workspace: {value!r}"
        )


def _validate_media_filename(filename: str) -> None:
    """Require one media basename so asset maps cannot redirect writes."""
    path = Path(filename)
    if (
        not filename
        or filename in {".", ".."}
        or path.drive
        or path.anchor
        or path.name != filename
        or "/" in filename
        or "\\" in filename
    ):
        raise ValueError(f"Media filename must be a basename: {filename!r}")


def _extract_theme_info(
    theme: PartRef,
    palette: ColorPalette,
) -> tuple[dict[str, str], dict[str, str]]:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .converter import (
        _CJK_THEME_SCRIPTS,
    )
    from .color_resolver import find_color_elem, resolve_color

    colors: dict[str, str] = {}
    fonts: dict[str, str] = {}

    scheme = theme.xml.find(".//a:clrScheme", NS)
    if scheme is not None:
        for child in list(scheme):
            if not isinstance(child.tag, str):
                continue
            name = child.tag.split("}", 1)[-1]
            try:
                color_elem = find_color_elem(child)
                hex_, _ = resolve_color(color_elem, palette)
            except ValueError as exc:
                if palette.strict:
                    raise
                palette._diagnose(
                    "theme-summary-color-omitted",
                    str(exc),
                    "omit only this malformed theme-summary color",
                )
                continue
            if hex_:
                colors[name] = hex_

    font_scheme = theme.xml.find(".//a:fontScheme", NS)
    if font_scheme is not None:
        for slot in ("majorFont", "minorFont"):
            fnt = font_scheme.find(f"a:{slot}", NS)
            if fnt is None:
                continue
            role_prefix = "major" if slot == "majorFont" else "minor"
            latin = fnt.find("a:latin", NS)
            if latin is not None and latin.attrib.get("typeface"):
                fonts[f"{role_prefix}Latin"] = latin.attrib["typeface"]
            ea = fnt.find("a:ea", NS)
            if ea is not None and ea.attrib.get("typeface"):
                fonts[f"{role_prefix}EastAsia"] = ea.attrib["typeface"]
            cs = fnt.find("a:cs", NS)
            if cs is not None and cs.attrib.get("typeface"):
                fonts[f"{role_prefix}ComplexScript"] = cs.attrib["typeface"]
            for supplemental in fnt.findall("a:font", NS):
                script = supplemental.attrib.get("script", "")
                typeface = supplemental.attrib.get("typeface", "")
                if script in _CJK_THEME_SCRIPTS and typeface:
                    fonts[f"{role_prefix}Script{script}"] = typeface

    return colors, fonts


def _palette_diagnostic_sink(
    result: ConvertResult,
    *,
    part_path: str,
    slide_index: int | None = None,
) -> Callable[[str, str, str], None]:
    """Build a package-level diagnostic sink for palette initialization."""
    def _record(code: str, message: str, fallback: str) -> None:
        append_diagnostic(
            result.diagnostics,
            ImportDiagnostic(
                code=code,
                message=message,
                fallback=fallback,
                part_path=part_path,
                slide_index=slide_index,
            ),
        )

    return _record


def _roundtrip_native_structure(
    pkg: OoxmlPackage,
    pptx_path: Path,
) -> dict[str, object]:
    """Build the existing validated source-structure contract without assets."""
    masters = list(pkg.iter_all_masters())
    layouts_with_parents = list(pkg.iter_all_layouts_with_parent())
    slides = list(pkg.iter_slides())
    used_layouts: dict[str, list[int]] = {}
    used_masters: dict[str, list[int]] = {}
    for slide in slides:
        if slide.layout is not None:
            used_layouts.setdefault(slide.layout.path, []).append(slide.index)
        if slide.master is not None:
            used_masters.setdefault(slide.master.path, []).append(slide.index)

    manifest: dict[str, object] = {
        "slideSize": {
            "width_emu": pkg.slide_size_emu[0],
            "height_emu": pkg.slide_size_emu[1],
            "width_px": pkg.slide_size_px[0],
            "height_px": pkg.slide_size_px[1],
        },
        "masters": [
            {
                "path": master.path,
                "displayName": part_display_name(master.xml, master.path),
                "drawableShapeCount": count_drawable_shapes(master.xml),
                "usedBySlides": used_masters.get(master.path, []),
            }
            for master in masters
        ],
        "layouts": [
            {
                "path": layout.path,
                "displayName": part_display_name(layout.xml, layout.path),
                "parentPath": master.path,
                "showMasterShapes": part_show_master_sp(layout),
                "drawableShapeCount": count_drawable_shapes(layout.xml),
                "placeholders": extract_placeholders(layout.xml),
                "usedBySlides": used_layouts.get(layout.path, []),
            }
            for layout, master in layouts_with_parents
        ],
        "slides": [
            {
                "index": slide.index,
                "slidePath": slide.part.path,
                "layoutPath": slide.layout.path if slide.layout else None,
                "masterPath": slide.master.path if slide.master else None,
                "showInheritedShapes": part_show_master_sp(slide.part),
                "placeholders": extract_placeholders(slide.part.xml),
                "svgFile": f"slide_{slide.index:02d}.svg",
            }
            for slide in slides
        ],
    }
    contract = build_native_structure(pptx_path, manifest)
    if not contract["strategy"]["preservationEligible"]:
        raise RuntimeError(
            "Round-trip mode requires a complete source master/layout graph"
        )
    return contract


def _annotate_roundtrip_slide_roots(
    slides: list[SlideArtifact],
    contract: dict[str, object],
) -> None:
    """Attach exact Layout identity to layered SVG roots for reverse export."""
    raw_layouts = contract.get("layouts")
    raw_masters = contract.get("masters")
    raw_slides = contract.get("slides")
    if not all(isinstance(value, list) for value in (
        raw_layouts,
        raw_masters,
        raw_slides,
    )):
        raise RuntimeError("Generated round-trip source structure is incomplete")
    layouts = {
        str(item.get("key")): item
        for item in raw_layouts
        if isinstance(item, dict)
    }
    masters = {
        str(item.get("key")): item
        for item in raw_masters
        if isinstance(item, dict)
    }
    slide_rows = {
        int(item["index"]): item
        for item in raw_slides
        if isinstance(item, dict) and isinstance(item.get("index"), int)
    }
    for slide in slides:
        row = slide_rows.get(slide.index)
        if row is None:
            raise RuntimeError(
                f"Round-trip source structure has no slide {slide.index}"
            )
        layout_key = str(row.get("layoutKey") or "")
        master_key = str(row.get("masterKey") or "")
        layout = layouts.get(layout_key)
        master = masters.get(master_key)
        if layout is None or master is None:
            raise RuntimeError(
                f"Round-trip slide {slide.index} has an unresolved Layout/Master"
            )
        attrs = {
            "data-pptx-layout": layout_key,
            "data-pptx-layout-name": str(layout.get("name") or layout_key),
            "data-pptx-master": master_key,
            "data-pptx-master-name": str(master.get("name") or master_key),
            "data-pptx-show-master-shapes": (
                "true" if layout.get("showMasterShapes", True) else "false"
            ),
            "data-pptx-show-inherited-shapes": (
                "true" if row.get("showInheritedShapes", True) else "false"
            ),
        }
        marker = slide.svg.find(">")
        if not slide.svg.startswith("<svg ") or marker < 0:
            raise RuntimeError(
                f"Round-trip slide {slide.index} does not have a canonical SVG root"
            )
        serialized = "".join(
            f" {name}={quoteattr(value)}"
            for name, value in attrs.items()
        )
        slide.svg = slide.svg[:marker] + serialized + slide.svg[marker:]


def _make_palette(
    master: PartRef | None,
    theme: PartRef | None,
    options: ConvertOptions,
    result: ConvertResult,
    *,
    part_path: str,
    slide_index: int | None = None,
) -> ColorPalette:
    """Create one strict or tolerant palette with structured diagnostics."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .converter import (
        _palette_diagnostic_sink,
    )
    return ColorPalette(
        master,
        theme,
        strict=options.strict,
        diagnostic_sink=_palette_diagnostic_sink(
            result,
            part_path=part_path,
            slide_index=slide_index,
        ),
    )


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------

