"""Pinned PPT Master quality gate component.

MIT Copyright (c) 2025-2026 Hugo He.
Upstream 7e0df6705de0cd24345210d31f07fcb5a32b6900.
"""
from __future__ import annotations
from pathlib import Path
from typing import Dict, List, Tuple
from xml.etree import ElementTree as ET

class SummaryQualityMixin:
    """Upstream summary checker methods."""

    def _print_result(self, result: Dict):
        """Print check result for a single file"""
        if result['passed']:
            if result['warnings']:
                icon = "[WARN]"
                status = "Passed (with warnings)"
            else:
                icon = "[OK]"
                status = "Passed"
        else:
            icon = "[ERROR]"
            status = "Failed"

        print(f"{icon} {result['file']} - {status}")

        # Display basic info
        if result['info']:
            info_items = []
            if 'viewbox' in result['info']:
                info_items.append(f"viewBox: {result['info']['viewbox']}")
            calibration = result['info'].get(
                'roundtrip_text_calibration'
            )
            if isinstance(calibration, dict):
                factor = calibration.get('factor')
                measured = calibration.get('measured_unchanged')
                positive = calibration.get('positive_unchanged')
                if (
                    isinstance(factor, (int, float))
                    and isinstance(measured, int)
                    and isinstance(positive, int)
                ):
                    info_items.append(
                        f'text calibration: {factor:.1%} '
                        f'({positive}/{measured} unchanged source texts)'
                    )
            if info_items:
                print(f"   {' | '.join(info_items)}")

        # Display errors
        if result['errors']:
            for error in result['errors']:
                print(f"   [ERROR] {error}")

        # Display the complete warning set from this run. The generation
        # workflow reviews all findings before one consolidated repair pass.
        if result['warnings']:
            for warning in result['warnings']:
                print(f"   [WARN] {warning}")

        print()

    def print_summary(self):
        """Print check summary"""
        self._apply_aggregated_issue_counts()

        print("=" * 80)
        print("[SUMMARY] Check Summary")
        print("=" * 80)

        print(f"\nTotal files: {self.summary['total']}")
        print(
            f"  [OK] Fully passed: {self.summary['passed']} ({self._percentage(self.summary['passed'])}%)")
        print(
            f"  [WARN] With warnings: {self.summary['warnings']} ({self._percentage(self.summary['warnings'])}%)")
        print(
            f"  [ERROR] With errors: {self.summary['errors']} ({self._percentage(self.summary['errors'])}%)")

        self._print_provenance_category_summary()
        self._print_carrier_receipt_summary()

        if self.issue_types:
            print(f"\nIssue categories:")
            for issue_type, count in sorted(self.issue_types.items(), key=lambda x: x[1], reverse=True):
                print(f"  {issue_type}: {count}")

        # spec_lock anchor comparison (only printed when a lock was found)
        self._print_anchor_value_summary()

        # Template-mode aggregation (orphan/missing roster + placeholder hints)
        self._print_template_summary()

        # Animation config aggregation.
        self._print_animation_summary()

        # Illustration strategy aggregation.
        self._print_illustration_summary()

        # Communication contract and per-page audience movement.
        self._print_communication_trace_summary()

        # Explicit PowerPoint master/layout structure aggregation.
        self._print_pptx_structure_summary()

        # Source-owned import recovery belongs to the template, not this run.
        self._print_source_import_summary()

        # Fix suggestions
        if self.summary['errors'] > 0 or self.summary['warnings'] > 0:
            print(f"\n[TIP] Common fixes:")
            print(
                f"  1. XML well-formedness: write typography as raw Unicode (—, ©, →, NBSP); "
                f"escape XML reserved chars as &amp; &lt; &gt; &quot; &apos; — "
                f"never use HTML named entities like &nbsp; &mdash; &copy;"
            )
            print(f"  2. viewBox issues: root viewBox is the canvas authority (see references/canvas-formats.md)")
            print(
                "  3. Paint recommendation: generated SVG prefers uppercase "
                "#RRGGBB plus channel-specific opacity; compatible alternatives "
                "remain non-blocking"
            )
            print(f"  4. foreignObject: Use <text> + <tspan> for manual line breaks")
            print(f"  5. Font issues: use PPT-safe exported typefaces (e.g. Arial / Consolas, with the CJK face of the deck language)")

    def _carrier_receipt_summary(self) -> Dict:
        """Aggregate factual per-page carrier receipts for compact review."""
        from .checker import (
            Counter,
        )
        receipts = [
            result.get('info', {}).get('carrier_receipt')
            for result in self.results
            if result.get('info', {}).get('carrier_receipt')
        ]
        totals = Counter({
            'text_elements': 0,
            'image_placements': 0,
            'icons': 0,
            'svg_geometry_elements': 0,
            'preset_shapes': 0,
            'page_frame_elements': 0,
            'marker_uses': 0,
            'inline_emphasis_runs': 0,
            'gradient_uses': 0,
            'filter_uses': 0,
            'text_effects': 0,
        })
        pages_with = Counter({
            'images': 0,
            'icons': 0,
            'presets': 0,
            'charts': 0,
            'tables': 0,
            'formulas': 0,
            'inline_emphasis_runs': 0,
            'gradient_uses': 0,
            'filter_uses': 0,
            'text_effects': 0,
        })
        geometry_counts: Counter[str] = Counter()
        preset_names: Counter[str] = Counter()
        native_objects: Counter[str] = Counter()
        image_frame_shares: List[float] = []

        for receipt in receipts:
            images = receipt['images']
            geometry = receipt['geometry']
            native = receipt['native_objects']
            effects = receipt.get('effects', {})
            totals['text_elements'] += receipt['text_elements']
            totals['image_placements'] += images['placements']
            totals['icons'] += receipt['icons']
            totals['preset_shapes'] += geometry['preset_shapes']
            totals['page_frame_elements'] += geometry['page_frame_elements']
            totals['marker_uses'] += sum(geometry['marker_uses'].values())
            geometry_counts.update(geometry['svg_elements'])
            preset_names.update(geometry['preset_names'])
            native_objects.update(native)

            if images['placements']:
                pages_with['images'] += 1
                image_frame_shares.append(images['max_frame_share'])
            if receipt['icons']:
                pages_with['icons'] += 1
            if geometry['preset_shapes']:
                pages_with['presets'] += 1
            if native.get('chart'):
                pages_with['charts'] += 1
            if native.get('table'):
                pages_with['tables'] += 1
            if native.get('formula_block') or native.get('formula_inline'):
                pages_with['formulas'] += 1
            for name in (
                'inline_emphasis_runs',
                'gradient_uses',
                'filter_uses',
                'text_effects',
            ):
                count = effects.get(name, 0)
                totals[name] += count
                if count > 0:
                    pages_with[name] += 1

        totals['svg_geometry_elements'] = sum(geometry_counts.values())
        frame_share_range = (
            [round(min(image_frame_shares), 4), round(max(image_frame_shares), 4)]
            if image_frame_shares
            else []
        )
        return {
            'scope': 'informational-not-a-quota',
            'pages': len(receipts),
            'totals': dict(totals),
            'pages_with': dict(pages_with),
            'relationship_pages': self._relationship_page_count(),
            'geometry_elements': dict(sorted(geometry_counts.items())),
            'preset_names': dict(sorted(preset_names.items())),
            'native_objects': dict(sorted(native_objects.items())),
            'image_page_max_frame_share_range': frame_share_range,
        }

    def _relationship_page_count(self) -> int | None:
        """Count design_spec §IX pages whose Relationships line names a carried relation.

        The executor's receipt review compares pages carrying a preset or
        connector against pages whose ``Relationships`` line names ``order``,
        ``link``, ``parent``, or ``membership``; this reads that count from the
        project's ``design_spec.md`` so the comparison is on the receipt. None
        when no exact ``design_spec.md`` is present (Quick, templates).
        """
        from .checker import (
            Path,
            count_carried_relationship_pages,
        )
        paths = [
            Path(result['path'])
            for result in self.results
            if result.get('path') and result.get('info', {}).get('carrier_receipt')
        ]
        if not paths:
            return None
        spec_path = self._resolve_project_path(paths[0]) / 'design_spec.md'
        if not spec_path.is_file():
            return None
        try:
            text = spec_path.read_text(encoding='utf-8')
        except OSError:
            return None
        return count_carried_relationship_pages(text)

    def _print_carrier_receipt_summary(self) -> None:
        """Print a compact actual-use receipt without a score or threshold."""
        if self.template_mode:
            return
        receipt = self._carrier_receipt_summary()
        if not receipt['pages']:
            return
        totals = receipt['totals']
        native = receipt['native_objects']
        print("\n[CARRIERS] Actual-use receipt (informational; not a quota)")
        print(
            f"  Pages: {receipt['pages']} | text: {totals['text_elements']} | "
            f"images: {totals['image_placements']} | icons: {totals['icons']}"
        )
        print(
            f"  Geometry: SVG elements {totals['svg_geometry_elements']} | "
            f"native presets {totals['preset_shapes']} | "
            f"page-frame elements {totals['page_frame_elements']} | "
            f"marker uses {totals['marker_uses']}"
        )
        pages_with = receipt['pages_with']
        print(
            f"  Effects: inline emphasis {totals['inline_emphasis_runs']} "
            f"(pages {pages_with['inline_emphasis_runs']}) | "
            f"gradients {totals['gradient_uses']} "
            f"(pages {pages_with['gradient_uses']}) | "
            f"filters {totals['filter_uses']} "
            f"(pages {pages_with['filter_uses']}) | "
            f"text effects {totals['text_effects']} "
            f"(pages {pages_with['text_effects']})"
        )
        print(
            f"  Native objects: charts {native.get('chart', 0)} | "
            f"tables {native.get('table', 0)} | formulas "
            f"{native.get('formula_block', 0) + native.get('formula_inline', 0)}"
        )
        presets = receipt['preset_names']
        preset_text = (
            ', '.join(f'{name} x{count}' for name, count in presets.items())
            if presets
            else '(none)'
        )
        print(f"  Presets: {preset_text}")
        relationship_pages = receipt.get('relationship_pages')
        preset_pages = pages_with['presets']
        if relationship_pages is None:
            print(f"  Pages carrying a preset or connector: {preset_pages}")
        else:
            print(
                f"  Relationship pages: {relationship_pages} "
                "(§IX names order / link / parent / membership) | "
                f"pages carrying a preset or connector: {preset_pages}"
            )
        image_range = receipt['image_page_max_frame_share_range']
        if image_range:
            print(
                "  Largest image-frame share on image pages: "
                f"{image_range[0] * 100:.1f}%–{image_range[1] * 100:.1f}%"
            )

    def _print_provenance_category_summary(self):
        """Print compact JSON-equivalent counts for token-safe gate handling."""
        from .checker import (
            _source_import_warning_count,
        )
        categories = self._provenance_categories()
        unchanged_mirror = self.template_mode and self.results and all(
            result.get('info', {}).get('mirror_source_unchanged')
            for result in self.results
        )
        rows = (
            (
                'blocking',
                len(categories['blocking']),
                'hard findings; gate also requires exit 0',
            ),
            (
                'introduced',
                len(categories['introduced']),
                ('advisory; mirror: source-authored spellings, workspace unmodified'
                 if unchanged_mirror else 'advisory; new or changed'),
            ),
            (
                'inherited',
                len(categories['inherited']),
                'informational; prototype-identical',
            ),
            (
                'source-import',
                _source_import_warning_count(categories['source_import']),
                'informational; source-conversion loss',
            ),
        )

        print("\nProvenance categories:")
        for name, count, note in rows:
            print(f"  {f'{name}: {count}':<20} {note}")

    def _print_animation_summary(self):
        """Print animations.json validation issues if present."""
        if not self._animation_issues:
            return

        errors = [item for item in self._animation_issues if item[0] == 'error']
        warnings = [item for item in self._animation_issues if item[0] == 'warning']

        print("\n[ANIMATION] animations.json checks")
        for _severity, msg in errors:
            print(f"  [ERROR] {msg}")
        for _severity, msg in warnings:
            print(f"  [WARN] {msg}")

    def _print_illustration_summary(self):
        """Print project-level illustration strategy issues if present."""
        if not self._illustration_issues:
            return

        errors = [item for item in self._illustration_issues if item[0] == 'error']
        warnings = [item for item in self._illustration_issues if item[0] == 'warning']

        print("\n[IMAGES] Image resource checks")
        if errors:
            print(f"  Errors ({len(errors)}):")
            for _severity, kind, msg in errors:
                print(f"    [{kind}] {msg}")
        if warnings:
            print(f"  Warnings ({len(warnings)}):")
            for _severity, kind, msg in warnings:
                print(f"    [{kind}] {msg}")

    def _print_pptx_structure_summary(self):
        """Print project-level PowerPoint structure contract issues."""
        if not self._pptx_structure_issues:
            return
        print("\n[PPTX STRUCTURE] Master/layout contract checks")
        for severity, message in self._pptx_structure_issues:
            print(f"  [{severity.upper()}] {message}")

    def _print_communication_trace_summary(self):
        """Print project-level communication trace issues."""
        if not self._communication_trace_issues:
            return
        print("\n[COMMUNICATION TRACE] Contract, Audience move, and outline roster checks")
        for severity, message in self._communication_trace_issues:
            print(f"  [{severity.upper()}] {message}")

    def _print_source_import_summary(self):
        """Print source-owned tolerant-import diagnostics as information."""
        from .checker import (
            _source_import_warning_count,
        )
        warning_count = _source_import_warning_count(
            self._source_import_summary
        )
        if warning_count <= 0:
            return
        print("\n[SOURCE IMPORT] Template-owned compatibility diagnostics")
        print(
            f"  [INFO] {warning_count} source-import warning(s); unchanged "
            "template recovery is not attributed to generated content."
        )
        by_code = self._source_import_summary.get('by_code')
        if isinstance(by_code, dict):
            for code, count in sorted(by_code.items()):
                print(f"    {code}: {count}")

    def _print_template_summary(self):
        """Aggregate template-mode roster / placeholder issues at the bottom.

        Errors land under the ``errors`` summary count (so the exit signal
        from ``main`` agrees), warnings under ``warnings``. Both are listed
        per file so the user can act on them directly.
        """
        if not self._template_issues and self._spec_only_template_kind is None:
            return

        errors = [item for item in self._template_issues if item[0] == 'error']
        warnings = [item for item in self._template_issues if item[0] == 'warning']

        print("\n[TEMPLATE] Template mode checks")
        if errors:
            print(f"  Errors ({len(errors)}):")
            for _sev, kind, msg in errors:
                print(f"    [{kind}] {msg}")
        if warnings:
            print(f"  Warnings ({len(warnings)}):")
            for _sev, kind, msg in warnings:
                print(f"    [{kind}] {msg}")
        if self._spec_only_template_kind is not None and not errors:
            pretty_kind = self._spec_only_template_kind.title()
            print(f"  {pretty_kind} design_spec.md contract passed.")
        if not errors:
            if self._template_roster_pages:
                print(
                    f"  Roster contract passed "
                    f"({self._template_roster_pages} declared page(s) matched)."
                )
            if self._spec_only_template_kind is None:
                print("  No structural roster issues.")
                print("  Conventional placeholder-name hints may be declared through "
                      "'placeholders:' frontmatter. Placeholder bounds are mandatory "
                      "design-zone metadata.")

