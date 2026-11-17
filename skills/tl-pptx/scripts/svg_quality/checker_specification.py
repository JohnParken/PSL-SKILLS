"""Pinned PPT Master quality gate component.

MIT Copyright (c) 2025-2026 Hugo He.
Upstream 7e0df6705de0cd24345210d31f07fcb5a32b6900.
"""
from __future__ import annotations
from pathlib import Path
from collections import Counter
from typing import Dict, List, Tuple
from xml.etree import ElementTree as ET

class SpecificationQualityMixin:
    """Upstream specification checker methods."""

    def _get_spec_lock(self, svg_path: Path):
        """Locate and parse spec_lock.md near the SVG. Returns dict or None.

        Looks in svg_path.parent and svg_path.parent.parent (covers the two
        common layouts: SVG directly under <project>/ or under
        <project>/svg_output/). Results are cached per lock path.
        """
        from .checker import (
            _parse_spec_lock,
        )
        if self.quick_generate:
            return None
        if _parse_spec_lock is None:
            return None
        for candidate in (svg_path.parent / 'spec_lock.md',
                          svg_path.parent.parent / 'spec_lock.md'):
            if candidate in self._lock_cache:
                return self._lock_cache[candidate]
            if candidate.exists():
                try:
                    data = _parse_spec_lock(candidate)
                except Exception:
                    data = None
                self._lock_cache[candidate] = data
                if data is not None:
                    self._lock_seen = True
                return data
        return None

    def _prototype_drift_allowances(
        self,
    ) -> Tuple[set[str], set[str], set[str]]:
        """Return color/font/size values owned by the selected mirror page."""
        from .checker import (
            _PAINT_PROPERTIES,
            _normalize_hex_rgb,
            _parse_export_color,
            re,
        )
        prototype_root = self._active_prototype_root()
        if prototype_root is None:
            return set(), set(), set()
        try:
            content = self._active_prototype_path.read_text(encoding='utf-8')
        except (AttributeError, OSError):
            return set(), set(), set()

        colors: set[str] = set()
        for attribute in _PAINT_PROPERTIES or ():
            for raw_value in self._svg_property_values(content, attribute):
                normalized = raw_value.strip()
                if normalized.lower() in {'none', 'transparent'} or re.fullmatch(
                    r'url\(#[^)]+\)', normalized
                ):
                    continue
                if _parse_export_color is not None:
                    color, _alpha = _parse_export_color(normalized)
                else:
                    color = _normalize_hex_rgb(normalized)
                if color:
                    colors.add(color)
        fonts = {
            self._normalize_font_stack(value)
            for value in self._font_family_values(content)
            if self._normalize_font_stack(value)
        }
        sizes = set(self._effective_text_size_counts(prototype_root))
        return colors, fonts, sizes

    def _declared_typography_size_anchors(
        self,
        lock: Dict,
    ) -> Tuple[Dict, set[str], List[float], List[str]]:
        """Return valid declared size anchors and malformed lock rows."""
        from .checker import (
            math,
            re,
        )
        typography = lock.get('typography', {})
        positive_numeric_re = re.compile(
            r'^(?=.*[1-9])(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)$'
        )
        locked_sizes: set[str] = set()
        anchor_sizes: List[float] = []
        invalid_sizes: List[str] = []
        for key, raw_value in typography.items():
            if key == 'font_family' or key.endswith('_family'):
                continue
            value = raw_value.strip()
            if positive_numeric_re.fullmatch(value) is None:
                invalid_sizes.append(f"{key}: {raw_value}")
                continue
            try:
                anchor = float(value)
            except (TypeError, ValueError):
                invalid_sizes.append(f"{key}: {raw_value}")
                continue
            if not math.isfinite(anchor) or anchor <= 0:
                invalid_sizes.append(f"{key}: {raw_value}")
                continue
            locked_sizes.add(self._canonical_font_size_key(anchor))
            anchor_sizes.append(anchor)
        return typography, locked_sizes, anchor_sizes, invalid_sizes

    def _count_undeclared_size_occurrences(
        self,
        root: ET.Element,
        *,
        locked_sizes: set[str],
        anchor_sizes: List[float],
        prototype_sizes: set[str],
    ) -> Counter[str]:
        """Count text objects using valid sizes outside all declared bands."""
        from .checker import (
            Counter,
            FONT_SIZE_ANCHOR_TOLERANCE_PX,
            math,
        )
        counts: Counter[str] = Counter()
        if not locked_sizes:
            return counts
        for value, occurrence_count in self._effective_text_size_counts(root).items():
            if value in prototype_sizes and value not in locked_sizes:
                continue
            if value in locked_sizes:
                continue
            try:
                used_px = float(value)
            except (TypeError, ValueError):
                continue
            if not math.isfinite(used_px) or used_px < 0:
                continue
            if any(
                abs(used_px - anchor_px) <= FONT_SIZE_ANCHOR_TOLERANCE_PX
                for anchor_px in anchor_sizes
            ):
                continue
            counts[value] += occurrence_count
        return counts

    def _effective_text_size_counts(self, root: ET.Element) -> Counter[str]:
        """Count each effective size once per non-empty SVG text object."""
        from .checker import (
            Counter,
            ET,
            _UseExpansionError,
            _expand_local_use_references,
            _local_name,
            _resolve_project_font_sizes,
            copy,
        )
        counts: Counter[str] = Counter()
        if _resolve_project_font_sizes is None:
            return counts
        working_root = root
        if (
            _expand_local_use_references is not None
            and _UseExpansionError is not None
        ):
            expanded_root = copy.deepcopy(root)
            try:
                _expand_local_use_references(expanded_root)
            except _UseExpansionError:
                pass
            else:
                working_root = expanded_root
        try:
            effective_sizes = _resolve_project_font_sizes(working_root)
        except ValueError:
            return counts

        def collect_text_object_sizes(element: ET.Element) -> set[str]:
            values: set[str] = set()

            def visit(node: ET.Element) -> None:
                if (node.text or '').strip():
                    values.add(
                        self._canonical_font_size_key(effective_sizes[id(node)])
                    )
                for child in node:
                    visit(child)
                    if (child.tail or '').strip():
                        values.add(
                            self._canonical_font_size_key(
                                effective_sizes[id(node)]
                            )
                        )

            visit(element)
            return values

        definition_containers = {
            'clippath',
            'defs',
            'marker',
            'mask',
            'pattern',
            'symbol',
        }

        def visit_visible(element: ET.Element) -> None:
            local_name = _local_name(element).casefold()
            if local_name in definition_containers:
                return
            if local_name == 'text':
                if element.get('data-pptx-layer') in {'master', 'layout'}:
                    return
                counts.update(collect_text_object_sizes(element))
                return
            for child in element:
                visit_visible(child)

        visit_visible(working_root)
        return counts

    @staticmethod
    def _canonical_font_size_key(value: float) -> str:
        """Canonicalize equivalent numeric spellings for deck-wide counting."""
        return format(value, '.12g')

    def _prepare_undeclared_size_occurrences(
        self,
        svg_files: List[Path],
    ) -> None:
        """Pre-count sparse undeclared sizes before per-file diagnostics."""
        from .checker import (
            ET,
        )
        previous_prototype = self._active_prototype_path
        try:
            for svg_path in svg_files:
                lock = self._get_spec_lock(svg_path)
                if lock is None:
                    continue
                _typography, locked_sizes, anchor_sizes, _invalid = (
                    self._declared_typography_size_anchors(lock)
                )
                self._active_prototype_path = self._prototype_by_output.get(
                    svg_path.resolve()
                )
                _colors, _fonts, prototype_sizes = (
                    self._prototype_drift_allowances()
                )
                try:
                    content = svg_path.read_text(encoding='utf-8')
                except OSError:
                    continue
                try:
                    root = ET.fromstring(content)
                except ET.ParseError:
                    continue
                self._undeclared_size_occurrences.update(
                    self._count_undeclared_size_occurrences(
                        root,
                        locked_sizes=locked_sizes,
                        anchor_sizes=anchor_sizes,
                        prototype_sizes=prototype_sizes,
                    )
                )
        finally:
            self._active_prototype_path = previous_prototype
            self._undeclared_size_counts_ready = True

    def _check_spec_lock_alignment(
        self,
        content: str,
        svg_path: Path,
        result: Dict,
        *,
        root: ET.Element,
    ):
        """Compare SVG values with reusable anchors in spec_lock.md.

        Covers colors (fill / stroke / stop-color / flood-color / pattern
        metadata), font-family, and font-size.
        Additional colors and font families are valid contextual authoring and
        are recorded as information. A valid undeclared display size may occur
        at most twice across generated pages; its third occurrence makes it a
        recurring role and blocks ``svg_output`` until the role is declared.
        Structural text still maps to declared role bands. Exact mirror-
        prototype values remain inherited information. Exact values are
        accumulated in self._anchor_value_summary for end-of-run aggregation.
        When spec_lock.md is missing, silently skip this local comparison; the
        Generate route's required-artifact gate owns whether execution may begin.
        """
        from .checker import (
            SPARSE_UNDECLARED_FONT_SIZE_MAX_OCCURRENCES,
            _BARE_HEX_VALUE_RE,
            _PAINT_PROPERTIES,
            _authored_preset_encoding,
            _normalize_hex_rgb,
            _parse_export_color,
            _validate_authored_preset_group,
            re,
        )
        lock = self._get_spec_lock(svg_path)
        if lock is None:
            return
        prototype_colors, prototype_fonts, prototype_sizes = (
            self._prototype_drift_allowances()
        )

        # Build allow-sets from the lock
        allowed_colors = set()
        for v in lock.get('colors', {}).values():
            if _parse_export_color is not None:
                color, _alpha = _parse_export_color(v)
                if color:
                    allowed_colors.add(color)
            else:
                color = _normalize_hex_rgb(v)
                if color:
                    allowed_colors.add(color)

        # A validated compact preset may contain registry-derived darken/lighten
        # layer colors.  Their base paint still comes from spec_lock; the exact
        # child HEX values are deterministic compiler evidence, not color drift.
        if (
            _authored_preset_encoding is not None
            and _validate_authored_preset_group is not None
        ):
            for group in root.iter():
                if (
                    _authored_preset_encoding(group) != 'compact'
                    or _validate_authored_preset_group(group)
                ):
                    continue
                for child in group:
                    for attribute in ('fill', 'stroke'):
                        raw_value = child.get(attribute)
                        if raw_value is None:
                            continue
                        if _parse_export_color is not None:
                            color, _alpha = _parse_export_color(raw_value)
                        else:
                            color = _normalize_hex_rgb(raw_value)
                        if color:
                            allowed_colors.add(color)
        locked_colors = set(allowed_colors)
        allowed_colors.update(prototype_colors)

        typo, locked_sizes, anchor_sizes, invalid_lock_sizes = (
            self._declared_typography_size_anchors(lock)
        )
        if invalid_lock_sizes:
            shown = ', '.join(invalid_lock_sizes[:5])
            more = len(invalid_lock_sizes) - 5
            suffix = f" (+{more} more)" if more > 0 else ""
            result['errors'].append(
                f"spec_lock typography sizes must be positive finite unitless px values; "
                f"found {shown}{suffix}."
            )

        # Font families: default `font_family` plus any per-role `*_family`
        # override (title_family / body_family / emphasis_family / code_family,
        # per templates/schemas/spec_lock.schema.json). Any of these is a legitimate declared
        # value; an SVG that uses any one of them is not drifting.
        allowed_fonts = set()
        if typo:
            default_font = typo.get('font_family', '').strip()
            if default_font:
                allowed_fonts.add(self._normalize_font_stack(default_font))
            for k, v in typo.items():
                if k == 'font_family' or not k.endswith('_family'):
                    continue
                v_clean = v.strip()
                # Skip placeholder text like "same as body (omit if identical)"
                if not v_clean or v_clean.lower().startswith('same as'):
                    continue
                allowed_fonts.add(self._normalize_font_stack(v_clean))
        locked_fonts = set(allowed_fonts)
        allowed_fonts.update(prototype_fonts)

        # Sizes: declared slots are anchors. Checker cannot infer which role a
        # text node carries, so it uses the union of their ±2px bands as a cheap
        # numeric safety net; prompt rules own semantic role mapping.
        # Scan SVG for used values
        color_drifts = set()
        inherited_colors = set()
        for attr in _PAINT_PROPERTIES or ():
            for raw_value in self._svg_property_values(content, attr):
                normalized = raw_value.strip()
                if normalized.lower() in {'none', 'transparent'} or re.fullmatch(
                    r'url\(#[^)]+\)', normalized
                ):
                    continue
                if _BARE_HEX_VALUE_RE.fullmatch(normalized):
                    continue
                if _parse_export_color is not None:
                    val, _alpha = _parse_export_color(normalized)
                    if val is None:
                        continue
                else:
                    val = _normalize_hex_rgb(normalized)
                    if val is None:
                        continue
                if val not in allowed_colors:
                    color_drifts.add(f'#{val}')
                elif val in prototype_colors and val not in locked_colors:
                    inherited_colors.add(f'#{val}')

        font_drifts = set()
        inherited_fonts = set()
        for val in self._font_family_values(content):
            normalized_font = self._normalize_font_stack(val)
            if allowed_fonts and normalized_font not in allowed_fonts:
                font_drifts.add(val)
            elif (
                normalized_font in prototype_fonts
                and normalized_font not in locked_fonts
            ):
                inherited_fonts.add(val)

        size_drift_counts = self._count_undeclared_size_occurrences(
            root,
            locked_sizes=locked_sizes,
            anchor_sizes=anchor_sizes,
            prototype_sizes=prototype_sizes,
        )
        size_drifts = set(size_drift_counts)
        inherited_sizes = set()
        for val in self._effective_text_size_counts(root):
            if val in prototype_sizes and val not in locked_sizes:
                inherited_sizes.add(val)

        # Record in run-wide aggregation. Colors/fonts beyond the anchor set are
        # contextual values, not release issues. Generated-page sizes enforce
        # role-anchor ownership; other spec-backed locations retain review.
        fname = svg_path.name
        for v in color_drifts:
            self._anchor_value_summary['colors'][v].add(fname)
        for v in font_drifts:
            self._anchor_value_summary['fonts'][v].add(fname)
        for v in size_drifts:
            self._anchor_value_summary['sizes'][v].add(fname)

        contextual_values = {}
        if color_drifts:
            contextual_values['colors'] = sorted(color_drifts)
        if font_drifts:
            contextual_values['font_families'] = sorted(font_drifts)
        if contextual_values:
            result['info']['contextual_values'] = contextual_values

        sparse_sizes = {}
        recurring_sizes = {}
        for value, local_count in size_drift_counts.items():
            total_count = (
                self._undeclared_size_occurrences.get(value, local_count)
                if self._undeclared_size_counts_ready
                else local_count
            )
            target = (
                sparse_sizes
                if total_count <= SPARSE_UNDECLARED_FONT_SIZE_MAX_OCCURRENCES
                else recurring_sizes
            )
            target[value] = total_count

        if sparse_sizes:
            result['info']['sparse_typography_sizes'] = {
                value: count for value, count in sorted(sparse_sizes.items())
            }

        if recurring_sizes:
            shown = ', '.join(
                f"{value} ({count} occurrences)"
                for value, count in sorted(recurring_sizes.items())
            )
            size_issue = (
                f"undeclared font-size {shown} exceeds the sparse-display limit "
                f"of {SPARSE_UNDECLARED_FONT_SIZE_MAX_OCCURRENCES} occurrences"
            )
            if svg_path.parent.name == 'svg_output':
                result['errors'].append(
                    "spec_lock typography-size recurrence: "
                    f"{size_issue}. Structural text must return to its declared "
                    "role band; a genuinely recurring display treatment needs a "
                    "justified named role in the Design Spec and spec_lock."
                )
            else:
                result['warnings'].append(
                    f"spec_lock typography-size recurrence review: {size_issue}"
                )
        inherited_parts = []
        if inherited_colors:
            inherited_parts.append(f"{len(inherited_colors)} color(s)")
        if inherited_fonts:
            inherited_parts.append(f"{len(inherited_fonts)} font-family value(s)")
        if inherited_sizes:
            inherited_parts.append(f"{len(inherited_sizes)} font-size value(s)")
        if inherited_parts:
            self._append_inherited_info(
                result,
                'spec_lock_alignment',
                f"{', '.join(inherited_parts)} come unchanged from mirror "
                "prototype and are accepted without expanding spec_lock.md",
            )

