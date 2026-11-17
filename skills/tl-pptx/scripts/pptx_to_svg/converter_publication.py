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

def _remove_managed_paths(candidate_dir: Path, relative_paths: set[Path]) -> None:
    """Remove only the previous converter roster from a candidate workspace."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .converter import (
        _reject_symlink_ancestors,
    )
    _reject_symlink_ancestors(candidate_dir, relative_paths)
    parents: set[Path] = set()
    for relative in sorted(
        relative_paths,
        key=lambda item: len(item.parts),
        reverse=True,
    ):
        target = candidate_dir / relative
        if target.is_symlink() or target.is_file():
            target.unlink()
        elif target.is_dir():
            raise RuntimeError(
                "Managed artifact path collides with a preserved directory: "
                f"{relative}"
            )
        parent = target.parent
        while parent != candidate_dir:
            parents.add(parent)
            parent = parent.parent

    for parent in sorted(parents, key=lambda item: len(item.parts), reverse=True):
        if parent.is_symlink() or not parent.is_dir():
            continue
        try:
            parent.rmdir()
        except OSError:
            pass


def _overlay_staged_tree(staged_dir: Path, candidate_dir: Path) -> None:
    """Overlay generated artifacts without overwriting unmanaged user files."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .converter import (
        _path_lexists,
        _reject_symlink_ancestors,
    )
    for source in sorted(staged_dir.rglob("*")):
        relative = source.relative_to(staged_dir)
        target = candidate_dir / relative
        _reject_symlink_ancestors(candidate_dir, {relative})
        if source.is_symlink():
            raise RuntimeError(
                f"Generated artifact must not be a symlink: {relative}"
            )
        if source.is_dir():
            if (
                target.is_symlink()
                or (_path_lexists(target) and not target.is_dir())
            ):
                raise RuntimeError(
                    f"Generated artifact collides with unmanaged path: {relative}"
                )
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if _path_lexists(target):
            if target.is_dir() or target.is_symlink():
                raise RuntimeError(
                    f"Generated artifact collides with unmanaged path: {relative}"
                )
            if target.read_bytes() != source.read_bytes():
                raise RuntimeError(
                    f"Generated artifact collides with unmanaged file: {relative}"
                )
        shutil.copy2(source, target)


def publish_staged_workspace(
    output_dir: Path,
    staged_dir: Path,
    *,
    managed_root_files: set[str | Path] | None = None,
    managed_relative_paths: set[str | Path] | None = None,
) -> None:
    """Atomically publish generated artifacts while preserving user files.

    Converter-owned SVGs, their local media references, and the named managed
    artifacts are replaced as one roster. Everything else already present in
    the output directory is copied into the candidate unchanged.
    """
    # Resolve shared facade state at call time, preserving upstream bindings.
    from .converter import (
        _managed_report_artifact_paths,
        _managed_svg_paths,
        _managed_vector_asset_paths,
        _overlay_staged_tree,
        _path_lexists,
        _referenced_local_paths,
        _remove_managed_paths,
        _validated_relative_paths,
    )
    output_dir = output_dir.absolute()
    staged_dir = staged_dir.absolute()
    if (
        output_dir == staged_dir
        or output_dir in staged_dir.parents
        or staged_dir in output_dir.parents
    ):
        raise ValueError(
            "Staged and output workspaces must not contain one another"
        )
    output_resolved = output_dir.resolve(strict=False)
    try:
        Path.cwd().resolve().relative_to(output_resolved)
    except ValueError:
        pass
    else:
        raise RuntimeError(
            "Output workspace must not contain the current working directory"
        )
    if not staged_dir.is_dir():
        raise ValueError(f"Staged workspace does not exist: {staged_dir}")
    if (
        output_dir.is_symlink()
        or (_path_lexists(output_dir) and not output_dir.is_dir())
    ):
        raise RuntimeError(f"Output path must be a real directory: {output_dir}")

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    transaction_dir = Path(tempfile.mkdtemp(
        prefix=f".{output_dir.name}.publish-",
        dir=output_dir.parent,
    ))
    candidate_dir = transaction_dir / "candidate"
    backup_dir = transaction_dir / "previous"
    preserve_backup = False

    try:
        if output_dir.is_dir():
            shutil.copytree(output_dir, candidate_dir, symlinks=True)
        else:
            candidate_dir.mkdir()

        managed_svg = _managed_svg_paths(output_dir)
        relative_paths = {
            path.relative_to(output_dir)
            for path in managed_svg
        }
        relative_paths.update(
            path.relative_to(output_dir)
            for path in _managed_vector_asset_paths(output_dir)
        )
        relative_paths.update(_referenced_local_paths(output_dir, managed_svg))
        relative_paths.add(CONVERSION_REPORT_PATH)
        relative_paths.update(_managed_report_artifact_paths(output_dir))
        relative_paths.update(_validated_relative_paths(managed_root_files or set()))
        relative_paths.update(_validated_relative_paths(managed_relative_paths or set()))
        _remove_managed_paths(candidate_dir, relative_paths)
        _overlay_staged_tree(staged_dir, candidate_dir)

        if output_dir.is_dir():
            try:
                os.replace(output_dir, backup_dir)
                os.replace(candidate_dir, output_dir)
            except BaseException as publish_error:
                try:
                    if _path_lexists(backup_dir):
                        if _path_lexists(output_dir):
                            failed_output = transaction_dir / "failed-publish"
                            os.replace(output_dir, failed_output)
                        os.replace(backup_dir, output_dir)
                except BaseException as restore_error:
                    if (
                        not _path_lexists(backup_dir)
                        and _path_lexists(output_dir)
                    ):
                        raise publish_error
                    preserve_backup = _path_lexists(backup_dir)
                    raise RuntimeError(
                        "Failed to publish the new workspace and restore the "
                        "previous workspace; recovery directory: "
                        f"{transaction_dir}"
                    ) from restore_error
                raise
        else:
            os.replace(candidate_dir, output_dir)
    finally:
        if not preserve_backup:
            shutil.rmtree(transaction_dir, ignore_errors=True)


