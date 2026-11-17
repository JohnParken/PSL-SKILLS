"""Pinned PPT Master quality gate component.

MIT Copyright (c) 2025-2026 Hugo He.
Upstream 7e0df6705de0cd24345210d31f07fcb5a32b6900.
"""
from __future__ import annotations
from pathlib import Path
from typing import Dict, List, Tuple
from xml.etree import ElementTree as ET

class ReportQualityMixin:
    """Upstream report checker methods."""

    def _apply_aggregated_issue_counts(self):
        """Mirror project-level aggregate issues into summary counters once."""
        if self._aggregate_counts_applied:
            return
        self._aggregate_counts_applied = True

        animation_errors = [item for item in self._animation_issues if item[0] == 'error']
        animation_warnings = [item for item in self._animation_issues if item[0] == 'warning']
        self.summary['errors'] += len(animation_errors)
        self.summary['warnings'] += len(animation_warnings)
        for severity, _msg in self._animation_issues:
            self.issue_types[f'animation_config_{severity}'] += 1

        template_errors = [item for item in self._template_issues if item[0] == 'error']
        template_warnings = [item for item in self._template_issues if item[0] == 'warning']
        self.summary['errors'] += len(template_errors)
        self.summary['warnings'] += len(template_warnings)
        for severity, kind, _msg in self._template_issues:
            self.issue_types[f'template_{kind}_{severity}'] += 1

        illustration_errors = [item for item in self._illustration_issues if item[0] == 'error']
        illustration_warnings = [item for item in self._illustration_issues if item[0] == 'warning']
        self.summary['errors'] += len(illustration_errors)
        self.summary['warnings'] += len(illustration_warnings)
        for severity, kind, _msg in self._illustration_issues:
            self.issue_types[f'illustration_{kind}_{severity}'] += 1

        communication_errors = [
            item for item in self._communication_trace_issues
            if item[0] == 'error'
        ]
        communication_warnings = [
            item for item in self._communication_trace_issues
            if item[0] == 'warning'
        ]
        self.summary['errors'] += len(communication_errors)
        self.summary['warnings'] += len(communication_warnings)
        for severity, _msg in self._communication_trace_issues:
            self.issue_types[f'communication_trace_{severity}'] += 1

        structure_errors = [item for item in self._pptx_structure_issues if item[0] == 'error']
        structure_warnings = [item for item in self._pptx_structure_issues if item[0] == 'warning']
        self.summary['errors'] += len(structure_errors)
        self.summary['warnings'] += len(structure_warnings)
        for severity, _msg in self._pptx_structure_issues:
            self.issue_types[f'pptx_structure_{severity}'] += 1

    def _print_anchor_value_summary(self):
        """Print anchor comparisons without treating contextual paint/type as drift."""
        from .checker import (
            SPARSE_UNDECLARED_FONT_SIZE_MAX_OCCURRENCES,
        )
        if not self._lock_seen:
            return
        has_contextual = any(
            self._anchor_value_summary[category]
            for category in ('colors', 'fonts')
        )
        has_undeclared_sizes = bool(self._anchor_value_summary['sizes'])
        if not has_contextual and not has_undeclared_sizes:
            print(
                "\n[OK] spec_lock anchor comparison: no additional contextual "
                "colors/fonts or out-of-band font sizes"
            )
            return

        if has_contextual:
            print("\nContextual values beyond spec_lock anchors (informational):")
            for category, label in (
                ('colors', 'Colors'),
                ('fonts', 'Font families'),
            ):
                items = self._anchor_value_summary.get(category, {})
                if not items:
                    continue
                entries = sorted(
                    items.items(), key=lambda item: (-len(item[1]), item[0])
                )
                print(f"  {label}:")
                for val, files in entries:
                    count = len(files)
                    suffix = "file" if count == 1 else "files"
                    print(f"    {val}  ({count} {suffix})")
            print(
                "Note: contextual page paint, gradient/effect colors, and "
                "export-safe typefaces are allowed.\n"
                "      Add a spec_lock row only when a value becomes a "
                "recurring named semantic role."
            )

        if has_undeclared_sizes:
            print(
                "\nTypography sizes outside every declared role anchor ±2px "
                "(up to 2 occurrences are sparse; the 3rd is recurring):"
            )
            entries = sorted(
                self._anchor_value_summary['sizes'].items(),
                key=lambda item: (-len(item[1]), item[0]),
            )
            for val, files in entries:
                occurrences = self._undeclared_size_occurrences.get(
                    val,
                    len(files),
                )
                file_count = len(files)
                file_suffix = "file" if file_count == 1 else "files"
                policy = (
                    "sparse"
                    if occurrences <= SPARSE_UNDECLARED_FONT_SIZE_MAX_OCCURRENCES
                    else "recurring — declare a role"
                )
                print(
                    f"  {val}  ({occurrences} occurrences in {file_count} "
                    f"{file_suffix}; {policy})"
                )

    def _percentage(self, count: int) -> int:
        """Calculate percentage"""
        if self.summary['total'] == 0:
            return 0
        return min(100, int(count / self.summary['total'] * 100))

    def export_report(self, output_file: str = 'svg_quality_report.txt'):
        """Export check report"""
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write("PPT Master SVG Quality Check Report\n")
            f.write("=" * 80 + "\n\n")

            for result in self.results:
                status = "[OK] Passed" if result['passed'] else "[ERROR] Failed"
                f.write(f"{status} - {result['file']}\n")
                f.write(f"Path: {result.get('path', 'N/A')}\n")

                if result['info']:
                    f.write(f"Info: {result['info']}\n")

                if result['errors']:
                    f.write(f"\nErrors:\n")
                    for error in result['errors']:
                        f.write(f"  - {error}\n")

                if result['warnings']:
                    f.write(f"\nWarnings:\n")
                    for warning in result['warnings']:
                        f.write(f"  - {warning}\n")

                f.write("\n" + "-" * 80 + "\n\n")

            # Write summary
            f.write("\n" + "=" * 80 + "\n")
            f.write("Check Summary\n")
            f.write("=" * 80 + "\n\n")
            f.write(f"Total files: {self.summary['total']}\n")
            f.write(f"Fully passed: {self.summary['passed']}\n")
            f.write(f"With warnings: {self.summary['warnings']}\n")
            f.write(f"With errors: {self.summary['errors']}\n")

        print(f"\n[REPORT] Check report exported: {output_file}")

    def _provenance_categories(self) -> Dict[str, object]:
        """Classify every issue by provenance.

        Single source for the JSON report's ``categories`` block and the
        terminal summary, so the console and the report never disagree about
        what blocks a release export.
        """
        self._apply_aggregated_issue_counts()
        introduced: List[Dict[str, str]] = []
        blocking: List[Dict[str, str]] = []
        inherited: List[Dict[str, str]] = []
        for result in self.results:
            filename = str(result.get('file') or '')
            introduced.extend({
                'file': filename,
                'message': warning,
            } for warning in result.get('warnings', []))
            blocking.extend({
                'file': filename,
                'message': error,
            } for error in result.get('errors', []))
            info = result.get('info') or {}
            for item in info.get('inherited', []):
                if isinstance(item, dict):
                    inherited.append({
                        'file': filename,
                        'kind': str(item.get('kind') or 'prototype'),
                        'message': str(item.get('message') or ''),
                    })

        project_issues = {
            'template': [
                {'severity': severity, 'kind': kind, 'message': message}
                for severity, kind, message in self._template_issues
            ],
            'animation': [
                {'severity': severity, 'message': message}
                for severity, message in self._animation_issues
            ],
            'illustration': [
                {'severity': severity, 'kind': kind, 'message': message}
                for severity, kind, message in self._illustration_issues
            ],
            'communication_trace': [
                {'severity': severity, 'message': message}
                for severity, message in self._communication_trace_issues
            ],
            'pptx_structure': [
                {'severity': severity, 'message': message}
                for severity, message in self._pptx_structure_issues
            ],
        }
        for group, issues in project_issues.items():
            for issue in issues:
                item = {
                    'scope': group,
                    'message': issue['message'],
                }
                if issue['severity'] == 'error':
                    blocking.append(item)
                else:
                    introduced.append(item)

        return {
            'blocking': blocking,
            'introduced': introduced,
            'inherited': inherited,
            'project_issues': project_issues,
            'source_import': dict(self._source_import_summary),
        }

    def export_json_report(
        self,
        output_file: str,
        *,
        target: str,
        stage: str,
    ) -> None:
        """Write a machine-readable quality report with provenance classes."""
        from .checker import (
            Path,
            _quality_source_fingerprint,
            _source_import_warning_count,
            json,
        )
        categories = self._provenance_categories()
        blocking = categories['blocking']
        introduced = categories['introduced']
        inherited = categories['inherited']
        project_issues = categories['project_issues']

        # Keep the legacy `drift` JSON field for report compatibility. Its
        # colors/fonts entries are informational anchor comparisons; sparse
        # size entries are informational until their third occurrence.
        drift = {
            category: {
                value: sorted(files)
                for value, files in sorted(values.items())
            }
            for category, values in self._anchor_value_summary.items()
        }
        source_import = categories['source_import']
        payload = {
            'schema': 'ppt-master.svg-quality-report.v1',
            'stage': stage,
            'target': str(Path(target).resolve()),
            'source_fingerprint': (
                self._roundtrip_source_fingerprint
                if self._roundtrip_source_fingerprint is not None
                else _quality_source_fingerprint(self.results)
            ),
            'summary': dict(self.summary),
            'issue_types': dict(sorted(self.issue_types.items())),
            'categories': {
                'blocking': {
                    'count': len(blocking),
                    'issues': blocking,
                },
                'introduced': {
                    'count': len(introduced),
                    'issues': introduced,
                },
                'inherited': {
                    'count': len(inherited),
                    'issues': inherited,
                },
                'source-import': {
                    'count': _source_import_warning_count(source_import),
                    'summary': source_import,
                },
            },
            'drift': drift,
            'carrier_receipt': self._carrier_receipt_summary(),
            'project_issues': project_issues,
            'files': self.results,
        }
        report_path = Path(output_file)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + '\n',
            encoding='utf-8',
        )
        print(f"\n[REPORT] JSON quality report exported: {report_path}")
