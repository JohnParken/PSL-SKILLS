#!/usr/bin/env python3
"""
PPT Master - SVG Authoring View

Create a lightweight, non-destructive editable IR from PPTX-imported SVG
files. The source SVG remains the native-payload authority; the authoring copy
translates recognized shapes and tables into compact semantic components,
keeps stable source references, and omits bulky import-only/render-duplicate
payloads.

Usage:
    python3 scripts/svg_authoring_view.py <svg-file-or-directory> \
        -o <output-dir> --projection-kind <kind>

Examples:
    python3 scripts/svg_authoring_view.py analysis/source_svg_import/svg \
        -o analysis/authoring-svg --projection-kind layered
    python3 scripts/svg_authoring_view.py imported/slide_06.svg \
        -o /tmp/slide-authoring-view --projection-kind generic

Dependencies:
    None (standard library only).

The output directory is an authoring bundle: editable SVGs plus one
model-readable `authoring_summary.json` and one tool-only
`authoring_manifest.json` provenance sidecar. Layered IR remains the
template-creation input; final templates are materialized from it. A flat
authoring bundle at ``authoring-svg-flat/`` is the sole editable source for
``svg_to_pptx.py --roundtrip``. Directory runs prepare and stage the
complete batch before publishing it, so a failed page leaves the existing
destination set unchanged.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import re
import shutil
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit, urlunsplit
from xml.etree import ElementTree as ET

from compact_svg_coordinates import compact_svg_tree
from console_encoding import configure_utf8_stdio
from hyperlink_contract import ADOPTED_SOURCE_LINK_ATTR, SOURCE_HREF_ATTR
from native_payloads import (
    CUSTOM_GEOMETRY_ATTRIBUTE,
    CUSTOM_GEOMETRY_REF_ATTRIBUTE,
    NATIVE_RECORD_REF_ATTRIBUTE,
    SHAPE_STYLE_ATTRIBUTE,
    SHAPE_STYLE_REF_ATTRIBUTE,
    TXBODY_REF_ATTRIBUTE,
)
from pptx_shapes import (
    NATIVE_FALLBACK_SHA256_ATTR,
    svg_native_fallback_fingerprint,
)
from pptx_gradients import (
    NATIVE_GRADIENT_ATTR,
    NATIVE_GRADIENT_PREVIEW_SHA256_ATTR,
    NATIVE_GRADIENT_SHA256_ATTR,
)
from svg_compatibility import normalize_single_child_group_filters
from svg_authoring_contract import (
    canonical_authoring_errors,
    normalize_compact_authoring_tree,
)
from svg_to_pptx.drawingml.context import ConvertContext, IDENTITY_MATRIX
from svg_to_pptx.drawingml.utils import (
    INHERITABLE_ATTRS,
    is_stroked_zero_axis_shape,
    matrix_multiply,
    parse_inline_style,
    parse_opacity,
    parse_transform_matrix,
    project_definition_index,
    project_gradient_errors,
)



# Upstream MIT, Hugo He 2025-2026; algorithms retained during module split.
from svg_authoring_view import (
    ProjectionReport,
)

def _nearest_existing_directory(path: Path) -> Path:
    candidate = path
    while not os.path.lexists(candidate):
        parent = candidate.parent
        if parent == candidate:
            break
        candidate = parent
    if not candidate.is_dir():
        raise NotADirectoryError(f"Output parent is not a directory: {candidate}")
    return candidate


def _ensure_directory(path: Path, created: list[Path]) -> None:
    missing: list[Path] = []
    candidate = path
    while not candidate.exists():
        if os.path.lexists(candidate):
            raise NotADirectoryError(f"Output parent is not a directory: {candidate}")
        missing.append(candidate)
        parent = candidate.parent
        if parent == candidate:
            raise NotADirectoryError(f"Cannot resolve output parent: {path}")
        candidate = parent
    if not candidate.is_dir():
        raise NotADirectoryError(f"Output parent is not a directory: {candidate}")

    for directory in reversed(missing):
        directory.mkdir()
        created.append(directory)


def _remove_created_directories(created: list[Path]) -> list[str]:
    errors: list[str] = []
    for directory in reversed(created):
        try:
            directory.rmdir()
        except FileNotFoundError:
            continue
        except OSError as exc:
            errors.append(f"could not remove {directory}: {exc}")
    return errors


def _rollback_published_files(
    published: list[tuple[Path, Path | None]],
    created: list[Path],
) -> list[str]:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from svg_authoring_view import (
        _remove_created_directories,
    )
    errors: list[str] = []
    for target, backup in reversed(published):
        try:
            if backup is None:
                target.unlink(missing_ok=True)
            else:
                backup.replace(target)
        except OSError as exc:
            errors.append(f"could not restore {target}: {exc}")
    errors.extend(_remove_created_directories(created))
    return errors


def _publish_existing_directory(
    staged: list[tuple[Path, Path]],
    staging_root: Path,
    *,
    force: bool,
) -> None:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from svg_authoring_view import (
        _ensure_directory,
        _rollback_published_files,
    )
    backup_root = staging_root / "previous"
    backups: dict[Path, Path | None] = {}

    for index, (target, _) in enumerate(staged):
        if not os.path.lexists(target):
            backups[target] = None
            continue
        if not force:
            raise FileExistsError(f"Output file already exists: {target}")
        if target.is_dir() and not target.is_symlink():
            raise IsADirectoryError(f"Output target is a directory: {target}")

        backup = backup_root / f"{index:06d}.bak"
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target, backup, follow_symlinks=False)
        backups[target] = backup

    created: list[Path] = []
    published: list[tuple[Path, Path | None]] = []
    try:
        for target, _ in staged:
            _ensure_directory(target.parent, created)

        staging_device = staging_root.stat().st_dev
        for target, _ in staged:
            if target.parent.stat().st_dev != staging_device:
                raise OSError(
                    f"Cannot atomically publish across filesystems: {target}"
                )
            if backups[target] is None and os.path.lexists(target):
                raise FileExistsError(
                    f"Output appeared while projections were staged: {target}"
                )

        for target, staged_file in staged:
            staged_file.replace(target)
            published.append((target, backups[target]))
    except OSError as exc:
        rollback_errors = _rollback_published_files(published, created)
        if rollback_errors:
            details = "; ".join(rollback_errors)
            raise RuntimeError(
                f"Batch publish failed ({exc}); rollback was incomplete: {details}"
            ) from exc
        raise


def project_svg_batch(
    mapping: list[tuple[Path, Path]],
    source_root: Path,
    output_dir: Path,
    *,
    force: bool,
    projection_kind: str,
    source_proxy_dir: Path | None = None,
    publish_source_proxies: bool = True,
) -> list[ProjectionReport]:
    """Build and publish one complete authoring bundle transactionally."""
    # Resolve shared facade state at call time, preserving upstream bindings.
    from svg_authoring_view import (
        AUTHORING_MANIFEST_NAME,
        AUTHORING_SUMMARY_NAME,
        _authoring_manifest_bytes,
        _ensure_directory,
        _is_within,
        _nearest_existing_directory,
        _publish_existing_directory,
        _remove_created_directories,
        _render_projection,
        write_authoring_summary,
    )
    if source_proxy_dir is not None and _is_within(
        source_proxy_dir.resolve(),
        output_dir.resolve(),
    ):
        raise ValueError("Source proxy assets must live outside the authoring bundle")
    rendered = [
        _render_projection(source, output, source_proxy_dir)
        for source, output in mapping
    ]
    proxy_assets: dict[Path, bytes] = {}
    for _, _, rendered_assets in rendered:
        for target, payload in rendered_assets.items():
            previous = proxy_assets.get(target)
            if previous is not None and previous != payload:
                raise ValueError(f"Source proxy collision: {target}")
            proxy_assets[target] = payload
    if source_proxy_dir is not None and not publish_source_proxies:
        for target, payload in proxy_assets.items():
            if not target.is_file() or target.read_bytes() != payload:
                raise ValueError(
                    f"Source proxy asset is missing or stale: {target}"
                )
    staging_parent = _nearest_existing_directory(output_dir.parent)

    with tempfile.TemporaryDirectory(
        prefix=".svg-authoring-view-",
        dir=staging_parent,
    ) as temporary:
        staging_root = Path(temporary)
        new_root = staging_root / "projected"
        staged: list[tuple[Path, Path]] = []

        for report, projected, _ in rendered:
            relative = report.output.relative_to(output_dir)
            staged_file = new_root / relative
            staged_file.parent.mkdir(parents=True, exist_ok=True)
            staged_file.write_bytes(projected)
            staged.append((report.output, staged_file))

        manifest_path = output_dir / AUTHORING_MANIFEST_NAME
        staged_manifest = new_root / AUTHORING_MANIFEST_NAME
        staged_manifest.write_bytes(
            _authoring_manifest_bytes(
                [report for report, _, _ in rendered],
                source_root,
                output_dir,
                projection_kind,
            )
        )
        staged.append((manifest_path, staged_manifest))
        staged_summary = write_authoring_summary(new_root)
        staged.append(
            (
                output_dir / AUTHORING_SUMMARY_NAME,
                staged_summary,
            )
        )

        if publish_source_proxies:
            proxy_staging = staging_root / "source-proxies"
            for index, (target, payload) in enumerate(sorted(
                proxy_assets.items(),
                key=lambda item: item[0].as_posix(),
            )):
                if os.path.lexists(target):
                    if (
                        target.is_symlink()
                        or not target.is_file()
                        or target.read_bytes() != payload
                    ):
                        raise FileExistsError(
                            f"Source proxy target has different content: {target}"
                        )
                    continue
                staged_proxy = proxy_staging / f"{index:06d}.svg"
                staged_proxy.parent.mkdir(parents=True, exist_ok=True)
                staged_proxy.write_bytes(payload)
                staged.append((target, staged_proxy))

        has_external_targets = any(
            not _is_within(target.resolve(), output_dir.resolve())
            for target, _ in staged
        )
        if not output_dir.exists() and not has_external_targets:
            created: list[Path] = []
            try:
                _ensure_directory(output_dir.parent, created)
                if os.path.lexists(output_dir):
                    raise FileExistsError(
                        f"Output directory appeared while projections were staged: {output_dir}"
                    )
                if output_dir.parent.stat().st_dev != staging_root.stat().st_dev:
                    raise OSError(
                        f"Cannot atomically publish across filesystems: {output_dir}"
                    )
                new_root.replace(output_dir)
            except OSError as exc:
                cleanup_errors = _remove_created_directories(created)
                if cleanup_errors:
                    details = "; ".join(cleanup_errors)
                    raise RuntimeError(
                        f"Batch publish failed ({exc}); cleanup was incomplete: {details}"
                    ) from exc
                raise
        else:
            _publish_existing_directory(
                staged,
                staging_root,
                force=force,
            )

    return [report for report, _, _ in rendered]


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _source_mapping(input_path: Path, output_dir: Path) -> list[tuple[Path, Path]]:
    if input_path.is_file():
        if input_path.suffix.lower() != ".svg":
            raise ValueError(f"Input file must use the .svg extension: {input_path}")
        return [(input_path, output_dir / input_path.name)]

    sources = sorted(
        path for path in input_path.rglob("*")
        if path.is_file() and path.suffix.lower() == ".svg"
    )
    if not sources:
        raise ValueError(f"No SVG files found under: {input_path}")
    return [(source, output_dir / source.relative_to(input_path)) for source in sources]


