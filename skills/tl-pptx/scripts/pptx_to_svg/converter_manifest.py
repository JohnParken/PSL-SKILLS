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

def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_speaker_notes(
    output_dir: Path,
    result: ConvertResult,
    *,
    write_total: bool = True,
) -> None:
    """Write imported notes into the standard per-slide Markdown contract."""
    if not result.speaker_notes:
        return
    notes_dir = output_dir / "notes"
    notes_dir.mkdir(parents=True, exist_ok=True)
    combined = ["# Speaker Notes"]
    for note in result.speaker_notes:
        content = note.markdown.strip() + "\n"
        (notes_dir / note.filename).write_text(content, encoding="utf-8")
        combined.extend([
            "",
            f"## Slide {note.slide_index:02d}",
            "",
            note.markdown.strip(),
        ])
    if not write_total:
        # Round-trip notes are keyed by output stem; the Generate-only
        # total.md would only pose as a second source of truth.
        return
    (notes_dir / "total.md").write_text(
        "\n".join(combined).rstrip() + "\n",
        encoding="utf-8",
    )


def _write_roundtrip_manifest(
    output_dir: Path,
    result: ConvertResult,
    options: ConvertOptions,
) -> None:
    """Record immutable source, editable sidecars, and semantic resources."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .converter import (
        _referenced_local_paths,
        _sha256_file,
    )
    source_path = output_dir / SOURCE_PPTX_PATH
    animation_path = output_dir / "animations.json"
    resource_manifest = result.resource_inventory.manifest(
        include_images=not options.embed_images,
    )
    materialized_resource_paths = {
        Path(str(item["workspacePath"]))
        for item in resource_manifest["items"]
        if isinstance(item, dict)
        and item.get("materialized") is True
        and isinstance(item.get("workspacePath"), str)
    }
    notes_by_index = {
        note.slide_index: note
        for note in result.speaker_notes
    }
    flat_by_index = {
        slide.index: slide
        for slide in result.flat_slides
    }
    native_slides = []
    if isinstance(result.native_structure, dict):
        raw_slides = result.native_structure.get("slides")
        if isinstance(raw_slides, list):
            native_slides = raw_slides
    native_by_index = {
        int(item.get("index")): item
        for item in native_slides
        if isinstance(item, dict) and isinstance(item.get("index"), int)
    }
    native_masters = (
        result.native_structure.get("masters")
        if isinstance(result.native_structure, dict)
        else None
    )
    master_parts = {
        str(item.get("key")): str(item.get("packagePart"))
        for item in native_masters or []
        if isinstance(item, dict)
        and item.get("key")
        and item.get("packagePart")
    }
    native_layouts = (
        result.native_structure.get("layouts")
        if isinstance(result.native_structure, dict)
        else None
    )
    layout_parts = {
        str(item.get("key")): str(item.get("packagePart"))
        for item in native_layouts or []
        if isinstance(item, dict)
        and item.get("key")
        and item.get("packagePart")
    }
    slides: list[dict[str, object]] = []
    narration_by_index: dict[int, str | None] = {}
    with zipfile.ZipFile(source_path) as archive:
        for index, native_slide in native_by_index.items():
            part = str(native_slide["packagePart"])
            rels_part = posixpath.join(posixpath.dirname(part), "_rels", posixpath.basename(part) + ".rels")
            parts = narration_audio_parts(archive.read(part), archive.read(rels_part), part)
            narration_by_index[index] = (
                result.resource_inventory.path_map().get(parts[0]) if len(parts) == 1 else None
            )
    for slide in result.slides:
        layered_path = ROUNDTRIP_LAYERED_SVG_DIR / f"slide_{slide.index:02d}.svg"
        native_slide = native_by_index.get(slide.index, {})
        row: dict[str, object] = {
            "index": slide.index,
            "sourcePart": native_slide.get("packagePart"),
            "layoutPart": layout_parts.get(str(native_slide.get("layoutKey"))),
            "masterPart": master_parts.get(str(native_slide.get("masterKey"))),
            "layeredSvg": layered_path.as_posix(),
            "layeredSvgSha256": _sha256_file(output_dir / layered_path),
            "animationSha256": slide_animation_config_sha256(
                result.animation_config,
                f"slide_{slide.index:02d}",
            ),
            "narrationAudio": narration_by_index.get(slide.index),
        }
        referenced_svg_paths = [output_dir / layered_path]
        if slide.index in flat_by_index:
            flat_path = ROUNDTRIP_FLAT_SVG_DIR / f"slide_{slide.index:02d}.svg"
            row["flatSvg"] = flat_path.as_posix()
            row["flatSvgSha256"] = _sha256_file(output_dir / flat_path)
            referenced_svg_paths.append(output_dir / flat_path)
        authoring_path = AUTHORING_SVG_FLAT_DIR / f"slide_{slide.index:02d}.svg"
        if (output_dir / authoring_path).is_file():
            referenced_svg_paths.append(output_dir / authoring_path)
        derived_paths = sorted(
            _referenced_local_paths(output_dir, referenced_svg_paths)
            - materialized_resource_paths,
            key=lambda path: path.as_posix(),
        )
        derived_resources: list[dict[str, str]] = []
        for relative in derived_paths:
            target = output_dir / relative
            if not target.is_file():
                raise RuntimeError(
                    "Round-trip SVG references a missing derived resource: "
                    f"{relative.as_posix()}"
                )
            derived_resources.append({
                "file": relative.as_posix(),
                "sha256": _sha256_file(target),
            })
        row["derivedResources"] = derived_resources
        note = notes_by_index.get(slide.index)
        if note is not None:
            note_path = Path("notes") / note.filename
            row["notes"] = {
                "file": note_path.as_posix(),
                "sha256": _sha256_file(output_dir / note_path),
                "sourcePart": note.source_part,
                "sourceSha256": note.source_sha256,
            }
        slides.append(row)
    payload = {
        "schema": "ppt-master.roundtrip-workspace.v1",
        "source": {
            "file": SOURCE_PPTX_PATH.as_posix(),
            "sha256": _sha256_file(source_path),
            "originalPath": str(result.source_pptx_path.resolve()),
        },
        "structure": NATIVE_STRUCTURE_PATH.as_posix(),
        "conversionReport": CONVERSION_REPORT_PATH.as_posix(),
        "sidecars": {
            "animations": {
                "file": "animations.json",
                "sha256": _sha256_file(animation_path),
                "baseline": result.animation_config,
            },
            "notesTotal": None,
        },
        "directories": {
            "authoringSvg": AUTHORING_SVG_FLAT_DIR.as_posix(),
            "layeredSvg": ROUNDTRIP_LAYERED_SVG_DIR.as_posix(),
            "flatSvg": (
                ROUNDTRIP_FLAT_SVG_DIR.as_posix()
                if result.flat_slides
                else None
            ),
            "images": options.images_subdir,
            "sourceObjectPreviews": (
                Path(options.images_subdir) / "source-object-previews"
            ).as_posix(),
            "sounds": options.sound_subdir,
            "audio": "audio",
            "video": "video",
            "notes": "notes",
            "nativePayloads": "native-payloads",
        },
        "slides": slides,
        "resources": resource_manifest,
    }
    target = output_dir / ROUNDTRIP_MANIFEST_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _write_artifacts(
    output_dir: Path,
    result: ConvertResult,
    options: ConvertOptions,
) -> None:
    """Stage a complete conversion, then atomically publish its exact roster."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .converter import (
        _write_artifact_tree,
        publish_staged_workspace,
    )
    output_dir = output_dir.absolute()
    reject_removed_workspace_layout(output_dir)
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging_root = Path(tempfile.mkdtemp(
        prefix=f".{output_dir.name}.convert-",
        dir=output_dir.parent,
    ))
    staged_dir = staging_root / "generated"
    try:
        _write_artifact_tree(staged_dir, result, options)
        publish_staged_workspace(output_dir, staged_dir)
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)


