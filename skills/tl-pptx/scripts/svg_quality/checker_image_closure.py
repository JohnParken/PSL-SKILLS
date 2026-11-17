"""Pinned PPT Master quality gate component.

MIT Copyright (c) 2025-2026 Hugo He.
Upstream 7e0df6705de0cd24345210d31f07fcb5a32b6900.
"""
from __future__ import annotations
from pathlib import Path
from typing import Dict, List, Tuple
from xml.etree import ElementTree as ET

class ImageClosureQualityMixin:
    """Upstream image closure checker methods."""

    def _check_planned_image_closure(
        self,
        rows: List[Dict[str, str]],
        project_path: Path,
        lock_entries: Dict[str, List[Dict[str, str]]],
        lock_error: str | None,
        svg_references: Dict[Path, Dict[str, set[Path]]],
        inline_image_counts: Dict[Path, int],
        image_placements: Dict[
            str,
            List[Tuple[Path, str, Tuple[str, ...]]],
        ],
    ) -> None:
        """Close Design Spec, execution lock, files, SVGs, and provenance."""
        from .checker import (
            Path,
            _parse_project_image_aspect_ratio,
            defaultdict,
        )
        project_root = project_path.resolve()
        if inline_image_counts:
            total = sum(inline_image_counts.values())
            shown = ', '.join(
                f"{path.name} ({count})"
                for path, count in sorted(inline_image_counts.items())
            )
            self._illustration_issues.append((
                'error',
                'svg_inline_image_untracked',
                f"svg_output contains {total} inline data-URI image(s): "
                f"{shown}. Current projects must keep external project-local "
                "image hrefs so every placement closes through Design Spec "
                "§VIII and spec_lock.md.",
            ))
        valid_acquisitions = {
            'ai',
            'web',
            'user',
            'formula',
            'placeholder',
            'slice',
        }
        valid_statuses = {
            'pending',
            'failed',
            'generated',
            'sourced',
            'rendered',
            'needs-manual',
            'existing',
            'placeholder',
        }
        terminal_by_acquisition = {
            'ai': {'generated', 'needs-manual'},
            'web': {'sourced', 'needs-manual'},
            'user': {'existing', 'needs-manual'},
            'formula': {'rendered', 'needs-manual'},
            'placeholder': {'placeholder'},
            'slice': {'generated', 'needs-manual'},
        }
        current_image_contract = (
            any('Crop Policy' in row for row in rows)
            or any(
                entry.get('legacy') == 'false'
                for entries in lock_entries.values()
                for entry in entries
            )
        )
        seen_filenames: set[str] = set()
        for row in rows:
            raw_filename = self._row_raw_filename(row)
            filename = self._row_filename(row)
            acquire = self._row_acquire(row)
            status = self._row_status(row)
            layout = self._row_layout(row)
            crop = self._row_crop(row)
            filename_is_bare = bool(filename) and (
                filename not in {'.', '..'}
                and '/' not in filename
                and '\\' not in filename
                and ':' not in filename
            )
            filename_is_canonical = raw_filename in {
                filename,
                f"images/{filename}",
            }
            if not filename_is_bare or not filename_is_canonical:
                self._illustration_issues.append((
                    'error',
                    'planned_image_invalid_filename',
                    f"Design Spec §VIII Filename {raw_filename!r} must be "
                    "a non-empty bare filename or canonical "
                    "images/<filename> path.",
                ))
            elif filename in seen_filenames:
                self._illustration_issues.append((
                    'error',
                    'planned_image_duplicate_filename',
                    f"Design Spec §VIII repeats Filename {filename!r}; "
                    "one resource must have one authoritative row.",
                ))
            else:
                seen_filenames.add(filename)

            if current_image_contract and not layout:
                self._illustration_issues.append((
                    'error',
                    'planned_image_missing_pattern',
                    f"{filename or '(missing filename)'} has an empty Design "
                    "Spec §VIII Image pattern; preserve one non-empty "
                    "Strategist recommendation without locking SVG geometry.",
                ))
            if current_image_contract and crop not in {'adaptive', 'no-crop'}:
                self._illustration_issues.append((
                    'error',
                    'planned_image_invalid_crop_policy',
                    f"{filename or '(missing filename)'} has invalid Design "
                    f"Spec §VIII Crop Policy "
                    f"{row.get('Crop Policy', '').strip()!r}; use adaptive "
                    "or no-crop.",
                ))

            if acquire not in valid_acquisitions:
                self._illustration_issues.append((
                    'error',
                    'planned_image_invalid_acquisition',
                    f"{filename or '(missing filename)'} has invalid "
                    f"Acquire Via {row.get('Acquire Via', '').strip()!r}.",
                ))
                continue
            if status not in valid_statuses:
                self._illustration_issues.append((
                    'error',
                    'planned_image_invalid_status',
                    f"{filename or '(missing filename)'} has invalid "
                    f"Status {row.get('Status', '').strip()!r}.",
                ))
                continue
            if status in {'pending', 'failed'}:
                self._illustration_issues.append((
                    'error',
                    'planned_image_not_terminal',
                    f"{filename or '(missing filename)'} has non-terminal "
                    f"Status {row.get('Status', '').strip()!r}; finish the "
                    "owning acquisition or mark it Needs-Manual before export.",
                ))
            elif status not in terminal_by_acquisition[acquire]:
                expected = ', '.join(sorted(terminal_by_acquisition[acquire]))
                self._illustration_issues.append((
                    'error',
                    'planned_image_status_mismatch',
                    f"{filename or '(missing filename)'} uses Acquire Via "
                    f"{acquire!r} but Status {status!r}; terminal status must "
                    f"be one of: {expected}.",
                ))

        if lock_error:
            self._illustration_issues.append((
                'error',
                'image_lock_unreadable',
                lock_error,
            ))
            return

        placed_rows = [
            row for row in rows
            if self._row_type(row).lower() not in {'illustration sheet', 'source'}
            and self._row_acquire(row)
            in {'ai', 'web', 'user', 'formula', 'placeholder', 'slice'}
        ]
        rows_by_filename = {
            self._row_filename(row): row
            for row in placed_rows
            if self._row_filename(row)
        }
        referenced_paths: Dict[str, set[Path]] = defaultdict(set)
        for references in svg_references.values():
            for filename, paths in references.items():
                referenced_paths[filename].update(paths)
        referenced = set(referenced_paths)

        for filename, row in rows_by_filename.items():
            if filename not in lock_entries:
                self._illustration_issues.append((
                    'error',
                    'planned_image_missing_lock',
                    f"{filename} is a placed Design Spec image row but is "
                    "absent from spec_lock.md images.",
                ))
                continue
            if current_image_contract and any(
                entry.get('legacy') != 'false'
                for entry in lock_entries[filename]
            ):
                self._illustration_issues.append((
                    'error',
                    'planned_image_legacy_lock_projection',
                    f"{filename} uses the current Design Spec image contract "
                    "but its spec_lock.md row does not provide complete "
                    "source=... and crop=... metadata.",
                ))

        for filename in sorted(referenced - set(rows_by_filename)):
            lock_note = (
                ""
                if filename in lock_entries
                else " and is absent from spec_lock.md images"
            )
            self._illustration_issues.append((
                'error',
                'svg_image_missing_spec',
                f"svg_output references {filename}, but it has no placed "
                f"Design Spec §VIII row{lock_note}.",
            ))

        for filename, entries in lock_entries.items():
            row = rows_by_filename.get(filename)
            if row is None:
                self._illustration_issues.append((
                    'error',
                    'locked_image_missing_spec',
                    f"{filename} is listed in spec_lock.md images but has no "
                    "placed row in Design Spec §VIII.",
                ))
                continue

            acquire = self._row_acquire(row)
            status = self._row_status(row)
            layout_pattern = self._row_layout(row)
            crop_policy = self._row_crop(row)
            if len(entries) > 1:
                keys = ', '.join(repr(entry.get('key', '')) for entry in entries)
                self._illustration_issues.append((
                    'error',
                    'locked_image_duplicate_entries',
                    f"{filename} appears in multiple spec_lock.md image rows "
                    f"({keys}); one resource must have one authoritative row.",
                ))
            for entry in entries:
                if entry.get('legacy') != 'false':
                    continue
                if entry.get('source') != acquire:
                    self._illustration_issues.append((
                        'error',
                        'locked_image_source_mismatch',
                        f"{filename} spec_lock source={entry.get('source')!r} "
                        f"does not match Design Spec §VIII Acquire Via "
                        f"{acquire!r}.",
                    ))
                if entry.get('crop') != crop_policy:
                    self._illustration_issues.append((
                        'error',
                        'locked_image_crop_mismatch',
                        f"{filename} spec_lock crop={entry.get('crop')!r} "
                        f"does not match Design Spec §VIII Crop Policy "
                        f"{crop_policy!r}.",
                    ))
                locked_pattern = entry.get('pattern', '')
                if locked_pattern and not self._layout_projection_matches(
                    locked_pattern,
                    layout_pattern,
                ):
                    self._illustration_issues.append((
                        'error',
                        'locked_image_pattern_mismatch',
                        f"{filename} spec_lock pattern="
                        f"{entry.get('pattern')!r} does not preserve the "
                        "Design Spec §VIII Image pattern recommendation "
                        f"{layout_pattern!r}. This Design Spec-to-spec_lock "
                        "projection check compares ordered catalog ids when "
                        "present, otherwise normalized text; it does not "
                        "compare SVG geometry or restrict the Executor's "
                        "realization.",
                    ))
            candidate_paths: List[Path] = []
            for entry in entries:
                raw_path = entry.get('path', '')
                if not raw_path:
                    continue
                lock_path = Path(raw_path)
                legacy_bare_filename = (
                    not lock_path.is_absolute()
                    and raw_path not in {'.', '..'}
                    and '/' not in raw_path
                    and '\\' not in raw_path
                    and ':' not in raw_path
                )
                if lock_path.is_absolute():
                    path = lock_path
                elif legacy_bare_filename:
                    path = project_path / 'images' / raw_path
                else:
                    path = project_path / raw_path
                resolved_path = path.resolve()
                try:
                    resolved_path.relative_to(project_root)
                except ValueError:
                    self._illustration_issues.append((
                        'error',
                        'locked_image_path_outside_project',
                        f"{filename} lock path {entry['path']!r} resolves "
                        "outside the project workspace.",
                    ))
                    continue
                candidate_paths.append(resolved_path)

            distinct_candidate_paths = set(candidate_paths)
            if len(distinct_candidate_paths) > 1:
                shown = ', '.join(
                    str(path.relative_to(project_root))
                    for path in sorted(distinct_candidate_paths)
                )
                self._illustration_issues.append((
                    'error',
                    'locked_image_ambiguous_paths',
                    f"{filename} resolves to multiple locked project paths: "
                    f"{shown}. One resource must have one authoritative asset.",
                ))

            expected_paths = {
                path
                for path in distinct_candidate_paths
                if path.is_file()
            }
            asset_exists = bool(expected_paths)
            file_required = status in {
                'existing',
                'generated',
                'sourced',
                'rendered',
            }
            if not asset_exists and file_required:
                expected = entries[0].get('path') or f"images/{filename}"
                self._illustration_issues.append((
                    'error',
                    'locked_image_file_missing',
                    f"{filename} is locked and has terminal Status "
                    f"{row.get('Status', '').strip()!r}, but {expected} "
                    "does not exist.",
                ))

            actual_paths = referenced_paths.get(filename, set())
            unexpected_paths = actual_paths - expected_paths
            if unexpected_paths:
                shown = ', '.join(
                    str(path.relative_to(project_root))
                    if path.is_relative_to(project_root)
                    else str(path)
                    for path in sorted(unexpected_paths)
                )
                self._illustration_issues.append((
                    'error',
                    'locked_image_reference_mismatch',
                    f"{filename} is referenced from {shown}, not exclusively "
                    "from its locked project path.",
                ))

            should_be_referenced = (
                acquire != 'placeholder'
                and asset_exists
                and status
                in {
                    'existing',
                    'generated',
                    'sourced',
                    'rendered',
                    'needs-manual',
                }
            )
            if should_be_referenced and not (actual_paths & expected_paths):
                self._illustration_issues.append((
                    'error',
                    'locked_image_unreferenced',
                    f"{filename} has usable terminal content but its locked "
                    "file is not referenced by any svg_output <image> element.",
                ))

            effective_no_crop = (
                crop_policy == 'no-crop'
                or acquire == 'formula'
                or any(entry.get('crop') == 'no-crop' for entry in entries)
            )
            if effective_no_crop:
                placements_by_svg: Dict[
                    Path,
                    List[Tuple[
                        str,
                        Tuple[str, ...],
                        Tuple[float, float, float, float] | None,
                    ]],
                ] = defaultdict(list)
                for svg_path, raw_aspect, mechanisms, geometry in (
                    image_placements.get(filename, [])
                ):
                    placements_by_svg[svg_path].append((
                        raw_aspect,
                        mechanisms,
                        geometry,
                    ))
                source_size = self._measure_image_pixels(
                    referenced_paths.get(filename),
                )

                for svg_path, placements in placements_by_svg.items():
                    parsed_placements = []
                    for raw_aspect, mechanisms, geometry in placements:
                        try:
                            align, mode = (
                                _parse_project_image_aspect_ratio(raw_aspect or None)
                                if _parse_project_image_aspect_ratio is not None
                                else ('', '')
                            )
                        except ValueError:
                            # The per-SVG aspect-ratio validator owns malformed syntax.
                            continue
                        parsed_placements.append((
                            raw_aspect,
                            mechanisms,
                            align,
                            mode,
                            geometry,
                        ))

                    has_complete_placement = any(
                        align != 'none'
                        and mode == 'meet'
                        and not mechanisms
                        for _raw_aspect, mechanisms, align, mode, _geometry
                        in parsed_placements
                    )

                    for raw_aspect, _mechanisms, align, _mode, geometry in (
                        parsed_placements
                    ):
                        if align != 'none':
                            continue
                        # ``none`` is the mandated form inside the nested crop
                        # transport and is harmless on a frame that keeps the
                        # source aspect; only measured distortion is a stretch.
                        deviation = self._stretch_deviation(geometry, source_size)
                        if deviation is not None and deviation <= 0.02:
                            continue
                        actual = raw_aspect or '(implicit xMidYMid meet)'
                        if deviation is None:
                            detail = (
                                "its frame cannot be checked against the source "
                                "pixel aspect"
                            )
                        else:
                            detail = (
                                f"its frame distorts the source aspect by "
                                f"{deviation * 100:.1f}%"
                            )
                        self._illustration_issues.append((
                            'error',
                            'no_crop_image_fit_mismatch',
                            f"{svg_path.name}: {filename} is no-crop but its "
                            f"rendered placement uses "
                            f"preserveAspectRatio={actual!r} and {detail}; "
                            "stretching is not a detail crop and remains "
                            "forbidden.",
                        ))

                    if has_complete_placement:
                        continue

                    for raw_aspect, mechanisms, align, mode, _geometry in (
                        parsed_placements
                    ):
                        if align != 'none' and mode != 'meet':
                            actual = raw_aspect or '(implicit xMidYMid meet)'
                            self._illustration_issues.append((
                                'error',
                                'no_crop_image_fit_mismatch',
                                f"{svg_path.name}: {filename} is no-crop but "
                                "this page has no complete placement and uses "
                                f"preserveAspectRatio={actual!r}; keep at least "
                                "one unclipped placement with a legal alignment "
                                "anchor and meet.",
                            ))
                        if mechanisms:
                            self._illustration_issues.append((
                                'error',
                                'no_crop_image_clipped',
                                f"{svg_path.name}: {filename} is no-crop but "
                                "this page has no complete placement; its "
                                "rendered placement is affected by "
                                f"{', '.join(mechanisms)}. Keep at least one "
                                "unclipped meet placement so every source pixel "
                                "remains visible.",
                            ))

        self._check_sourced_image_provenance(
            rows_by_filename,
            project_path,
        )

