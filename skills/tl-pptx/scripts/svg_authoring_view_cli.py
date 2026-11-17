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
from svg_authoring_view import ProjectionReport

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

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Create lightweight editable IR bundles from PPTX-imported SVG files."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("input", type=Path, help="SVG file or directory to project")
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        help="Explicit destination directory for projected SVG copies",
    )
    parser.add_argument(
        "--refresh-summary",
        action="store_true",
        help=(
            "Regenerate authoring_summary.json for an existing authoring "
            "bundle; input must be that bundle directory and -o is omitted"
        ),
    )
    parser.add_argument(
        "--adopt-object",
        metavar="<from.svg>:<element-id>",
        help=(
            "Copy one object from another page as authored SVG, strip its "
            "source/native transport, and refresh the bundle summary"
        ),
    )
    parser.add_argument(
        "--into",
        metavar="<target.svg>",
        help="Target page for --adopt-object",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Replace authoring files/manifest that already exist "
            "(never changes source files)"
        ),
    )
    parser.add_argument(
        "--projection-kind",
        choices=("layered", "flat", "generic"),
        default="generic",
        help="Record the IR representation kind in bundle metadata",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    # Resolve shared facade state at call time, preserving upstream bindings.
    from svg_authoring_view import (
        AUTHORING_MANIFEST_NAME,
        AUTHORING_SUMMARY_NAME,
        ProjectionStats,
        _is_within,
        _source_mapping,
        adopt_authoring_object,
        build_parser,
        project_svg_batch,
        write_authoring_summary,
    )
    parser = build_parser()
    args = parser.parse_args(argv)
    input_path = args.input.resolve()

    if not input_path.exists():
        print(f"Error: input does not exist: {input_path}", file=sys.stderr)
        return 1
    if args.refresh_summary and args.adopt_object is not None:
        parser.error("--refresh-summary and --adopt-object are mutually exclusive")
    if args.adopt_object is not None:
        if args.into is None:
            parser.error("--adopt-object requires --into <target.svg>")
        if args.output_dir is not None:
            parser.error("--adopt-object does not accept -o/--output-dir")
        if args.force:
            parser.error("--adopt-object does not accept --force")
        if args.projection_kind != "generic":
            parser.error("--adopt-object does not accept --projection-kind")
        try:
            result = adopt_authoring_object(
                input_path,
                args.adopt_object,
                args.into,
            )
        except (OSError, ValueError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.into is not None:
        parser.error("--into requires --adopt-object")
    if args.refresh_summary:
        if args.output_dir is not None:
            print(
                "Error: --refresh-summary does not accept -o/--output-dir",
                file=sys.stderr,
            )
            return 1
        try:
            summary_path = write_authoring_summary(input_path)
        except (OSError, ValueError) as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1
        print(json.dumps({
            "authoring_dir": str(input_path),
            "summary": str(summary_path),
            "summary_bytes": summary_path.stat().st_size,
        }, ensure_ascii=False, indent=2))
        return 0
    if args.output_dir is None:
        parser.error("-o/--output-dir is required unless --refresh-summary is used")
    output_dir = args.output_dir.resolve()

    if output_dir.exists() and not output_dir.is_dir():
        print(f"Error: output path is not a directory: {output_dir}", file=sys.stderr)
        return 1
    if input_path.is_dir() and _is_within(output_dir, input_path):
        print("Error: output directory must not be inside the input directory", file=sys.stderr)
        return 1

    try:
        mapping = _source_mapping(input_path, output_dir)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    same_file = [source for source, target in mapping if source.resolve() == target.resolve()]
    if same_file:
        print(f"Error: output would overwrite source SVG: {same_file[0]}", file=sys.stderr)
        return 1

    collisions = [target for _, target in mapping if os.path.lexists(target)]
    manifest_path = output_dir / AUTHORING_MANIFEST_NAME
    if os.path.lexists(manifest_path):
        collisions.append(manifest_path)
    summary_path = output_dir / AUTHORING_SUMMARY_NAME
    if os.path.lexists(summary_path):
        collisions.append(summary_path)
    if collisions and not args.force:
        print(
            f"Error: {len(collisions)} output file(s) already exist; "
            "use --force to replace the authoring bundle. "
            f"First collision: {collisions[0]}",
            file=sys.stderr,
        )
        return 1

    reports: list[ProjectionReport] = []
    try:
        source_root = input_path if input_path.is_dir() else input_path.parent
        reports = project_svg_batch(
            mapping,
            source_root,
            output_dir,
            force=args.force,
            projection_kind=args.projection_kind,
        )
    except (ET.ParseError, OSError, RuntimeError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    total_stats = ProjectionStats()
    original_bytes = 0
    projected_bytes = 0
    for report in reports:
        original_bytes += report.original_bytes
        projected_bytes += report.projected_bytes
        total_stats.merge(report.stats)

    bytes_saved = original_bytes - projected_bytes
    reduction = (bytes_saved / original_bytes * 100) if original_bytes else 0.0
    result = {
        "input": str(input_path),
        "output_dir": str(output_dir),
        "manifest": str(output_dir / AUTHORING_MANIFEST_NAME),
        "summary": str(output_dir / AUTHORING_SUMMARY_NAME),
        "projection_kind": args.projection_kind,
        "file_count": len(reports),
        "files": [report.as_dict() for report in reports],
        "totals": {
            "original_bytes": original_bytes,
            "projected_bytes": projected_bytes,
            "bytes_saved": bytes_saved,
            "reduction_percent": round(reduction, 2),
            "removed": total_stats.as_dict(),
        },
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


