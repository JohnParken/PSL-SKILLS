"""Pinned PPT Master quality gate component.

MIT Copyright (c) 2025-2026 Hugo He.
Upstream 7e0df6705de0cd24345210d31f07fcb5a32b6900.
"""
from __future__ import annotations
from pathlib import Path
from typing import Dict, List, Tuple
from xml.etree import ElementTree as ET

class FileQualityMixin:
    """Upstream file checker methods."""

    def __init__(
        self,
        *,
        template_mode: bool = False,
        quick_generate: bool = False,
        canonical_authoring: bool = False,
    ):
        from .checker import (
            Counter,
            defaultdict,
        )
        self.template_mode = template_mode
        self._image_pixel_sizes: Dict[Path, Tuple[int, int]] = {}
        self.scan_banner = True
        self.quick_generate = quick_generate
        self.canonical_authoring = canonical_authoring
        self.results = []
        self._roundtrip_source_fingerprint: Dict[str, object] | None = None
        self.summary = {
            'total': 0,
            'passed': 0,
            'warnings': 0,
            'errors': 0
        }
        self.issue_types = defaultdict(int)
        # spec_lock anchor comparison state (populated only when
        # _parse_spec_lock is available and a spec_lock.md is found near the SVG)
        self._lock_cache: Dict[Path, Dict] = {}
        self._anchor_value_summary: Dict[str, Dict[str, set]] = {
            'colors': defaultdict(set),
            'fonts': defaultdict(set),
            'sizes': defaultdict(set),
        }
        self._undeclared_size_occurrences: Counter[str] = Counter()
        self._undeclared_size_counts_ready = False
        self._lock_seen = False  # True once we locate at least one spec_lock.md
        self._source_manifest_cache: Dict[
            Path,
            Tuple[Dict, str | None],
        ] = {}
        self._source_manifest_errors_reported: set[Path] = set()
        # Template-mode aggregation (populated by check_directory when
        # template_mode=True). Each entry is (severity, kind, message) where
        # severity is 'error' or 'warning'. Printed in print_summary.
        self._template_issues: List[Tuple[str, str, str]] = []
        self._spec_only_template_kind: str | None = None
        self._template_roster_pages = 0
        self._animation_issues: List[Tuple[str, str]] = []
        self._illustration_issues: List[Tuple[str, str, str]] = []
        self._communication_trace_issues: List[Tuple[str, str]] = []
        self._communication_traced_projects: set[Path] = set()
        self._pptx_structure_issues: List[Tuple[str, str]] = []
        self._has_incomplete_page_roster = False
        self._structured_native_slots: list[str] = []
        self._active_slide_count: int | None = None
        # Early/page stages see part of the roster, so a slide jump's upper
        # bound waits for the final gate.
        self.partial_roster = False
        self._prototype_by_output: Dict[Path, Path] = {}
        self._active_prototype_path: Path | None = None
        self._active_template_reuse_scope: str | None = None
        self._prototype_root_cache: Dict[Path, ET.Element | None] = {}
        self._source_import_summary: Dict[str, object] = {
            'warning_count': 0,
            'by_code': {},
        }
        self._aggregate_counts_applied = False

    @staticmethod
    def _append_inherited_info(
        result: Dict,
        kind: str,
        message: str,
    ) -> None:
        """Record prototype-owned diagnostics outside the warning channel."""
        result['info'].setdefault('inherited', []).append({
            'kind': kind,
            'message': message,
        })

    def _active_prototype_root(self) -> ET.Element | None:
        """Parse the selected mirror prototype once for inherited checks."""
        from .checker import (
            ET,
            NativePayloadError,
            hydrate_native_payload_refs,
        )
        if (
            self._active_template_reuse_scope != 'mirror'
            or self._active_prototype_path is None
        ):
            return None
        path = self._active_prototype_path.resolve()
        if path in self._prototype_root_cache:
            return self._prototype_root_cache[path]
        try:
            root = ET.parse(path).getroot()
            hydrate_native_payload_refs(root, path)
        except (OSError, ET.ParseError, NativePayloadError):
            root = None
        self._prototype_root_cache[path] = root
        return root

    def check_file(
        self,
        svg_file: str,
        expected_format: str = None,
        *,
        expected_viewbox: str | None = None,
        expected_viewbox_label: str = "expected canvas",
    ) -> Dict:
        """
        Check a single SVG file

        Args:
            svg_file: SVG file path
            expected_format: Expected canvas format (e.g., 'ppt169')

        Returns:
            Check result dictionary
        """
        from .checker import (
            NativePayloadError,
            Path,
            _CHECK_PPTX_STRUCTURED_PROJECT,
            _template_structure_checks_enabled,
            discover_slide_svgs,
            hashlib,
            hydrate_native_payload_refs,
            svg_contracts,
        )
        svg_path = Path(svg_file)

        if not svg_path.exists():
            return {
                'file': str(svg_file),
                'exists': False,
                'errors': ['File does not exist'],
                'warnings': [],
                'passed': False
            }

        result = {
            'file': svg_path.name,
            'path': str(svg_path),
            'exists': True,
            'errors': [],
            'warnings': [],
            'info': {},
            'passed': True
        }

        try:
            source_bytes = svg_path.read_bytes()
            result['source_sha256'] = hashlib.sha256(source_bytes).hexdigest()
            content = source_bytes.decode('utf-8')

            # 0. Parse XML once — every other check assumes the file is valid
            # XML. Bail early on failure so the regex-based checks below don't
            # produce misleading errors on a broken document.
            root = self._parse_xml_root(content, result)
            if root is not None:
                self._check_canonical_authoring(root, result)
                try:
                    hydrated_payloads = hydrate_native_payload_refs(root, svg_path)
                except NativePayloadError as exc:
                    result['errors'].append(
                        f"Invalid native payload reference: {exc}"
                    )
                else:
                    if hydrated_payloads:
                        result['info']['native_payload_refs'] = hydrated_payloads

                roster = discover_slide_svgs(svg_path.parent) if self.quick_generate else []
                if (
                    roster
                    and svg_path.name == roster[0].name
                    and not (
                        root.get('lang')
                        or root.get('{http://www.w3.org/XML/1998/namespace}lang')
                    )
                ):
                    result['warnings'].append(
                        'Quick roster declares no deck language: put '
                        'lang="<BCP-47>" (vi-VN, he-IL, ...) on the first '
                        "page's root <svg>; export reads it for run proofing "
                        'language, right-to-left defaults, theme script slots '
                        'and docProps (advisory)'
                    )

                # 1. Check viewBox
                self._check_viewbox(
                    root,
                    svg_path,
                    result,
                    expected_format,
                    expected_viewbox=expected_viewbox,
                    expected_viewbox_label=expected_viewbox_label,
                )
                self._check_legacy_pptx_attributes(root, svg_path, result)
                self._record_carrier_receipt(root, result)

                # 1a. Validate exact importer transport before compatible
                # inline geometry is materialized on the shared tree.
                svg_contracts.check_nested_svg_crop_contract(root, result)

                # 2. Check forbidden elements
                svg_contracts.check_forbidden_elements(content, root, result)
                svg_contracts.check_mask_contract(root, result)

                # 2a. Validate direct geometry lengths and stroke widths.
                svg_contracts.check_geometry_length_values(root, result)
                self._check_shape_coordinate_ranges(root, result)

                # 2b. Validate line-presentation grammar and mappings.
                svg_contracts.check_stroke_style_values(root, result)

                # 2c. Validate image fit/crop grammar and mappings.
                self._check_image_contract(root, svg_path, result)
                svg_contracts.check_image_aspect_ratio_values(root, result)

                # 2d. Validate complete path-data and point-list grammar.
                svg_contracts.check_freeform_geometry_values(root, result)

                # 2e. Validate complete transform grammar and native mappings.
                svg_contracts.check_transform_values(root, result)

                # 2f. Validate opacity grammar and native alpha mappings.
                svg_contracts.check_opacity_values(root, result)

                # 2g. Validate the closed authoring-property surface and
                # conditional definition interfaces before export.
                svg_contracts.check_authoring_property_contract(root, result)
                svg_contracts.check_text_property_contract(root, result)
                self._check_preserved_txbody_contract(root, result)
                svg_contracts.check_paint_compatibility(root, result)
                svg_contracts.check_reference_spelling(root, result)
                svg_contracts.check_definition_contract(root, result)
                svg_contracts.check_paint_reference_contract(root, result)
                svg_contracts.check_marker_contract(root, result)
                svg_contracts.check_clip_path_contract(root, result)

                # 2h. Validate the supported shadow/glow filter interface.
                svg_contracts.check_imported_effect_status(root, result)
                svg_contracts.check_filter_effects(root, result)

                # 2i. Validate gradient definitions, stops, and coordinates.
                svg_contracts.check_gradient_interfaces(root, result)

                # 3. Check font-size values
                svg_contracts.check_font_size_values(content, result)

                # 4. Check fonts
                self._check_fonts(content, result)

                # 5. Check text wrapping methods
                self._check_text_elements(content, root, result)
                self._check_semantic_shape_text(root, result)

                # 5b. Validate native hyperlink targets and carrier structure.
                self._check_hyperlinks(root, result)

                # 6. Check image references (file existence and resolution)
                self._check_image_references(root, svg_path, result)

                # 7. Check icon placeholders resolve before post-processing.
                self._check_icon_placeholders(root, svg_path, result)

                # 7b. Reject visual elements the native converter cannot dispatch.
                self._check_unsupported_visual_elements(root, result)
                self._check_hidden_elements(root, result)

                # 7c. Fail closed on invalid PPTX preset/adjustment metadata.
                self._check_preset_geometry_metadata(root, result)
                self._check_preset_geometry_transforms(root, result)

                # 8. Check object-level animation anchor quality.
                self._check_animation_group_ids(root, svg_path, result)

                # 8b. Check <pattern> elements declare a PPTX preset.
                self._check_pattern_fills(root, result)

                # 8c. Check explicit native replacement markers before export.
                self._check_native_object_markers(root, result)

                # 8d. Validate explicit master/layout/placeholder metadata.
                if (
                    _template_structure_checks_enabled(svg_path)
                    if self.template_mode
                    else _CHECK_PPTX_STRUCTURED_PROJECT
                ):
                    self._check_pptx_structure_metadata(root, svg_path, result)

                # 8e. Validate rendering-neutral page/structure compiler hints.
                self._check_semantic_markers(root, svg_path, result)

                # 9. Compare values with spec_lock anchors. Additional colors
                #    and fonts are informational. Generated-page type sizes may
                #    stay sparse twice; the third occurrence is an error. Other
                #    spec-backed SVG locations retain advisory review. Templates
                #    do not ship a spec_lock.md, so skip in template mode.
                if not self.template_mode:
                    self._check_spec_lock_alignment(
                        content,
                        svg_path,
                        result,
                        root=root,
                    )

                # 10. Check web-sourced image attribution. Templates don't carry
                #    image_sources.json; skip in template mode.
                if not self.template_mode:
                    self._check_sourced_image_attribution(
                        root,
                        svg_path,
                        result,
                    )

            # Determine pass/fail
            if self.template_mode:
                self._classify_preserved_mirror_geometry(svg_path, result)
            result['passed'] = len(result['errors']) == 0

        except Exception as e:
            result['errors'].append(f"Failed to read file: {e}")
            result['passed'] = False

        return self._record_result(result)

    def _classify_preserved_mirror_geometry(self, svg_path: Path, result: Dict) -> None:
        """Report immutable source geometry as inherited, never exempt edits.

        The materializer records the exact published SVG hash. Only its text
        width estimates and source-object overlap qualify; XML, assets, native
        payloads and reusable structure remain fully blocking.
        """
        from .checker import (
            hashlib,
            json,
            re,
        )
        manifest_path = svg_path.parent / 'template_execution_manifest.json'
        try:
            manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return
        if (not isinstance(manifest, dict)
                or manifest.get('schema') != 'ppt-master.template-execution-manifest.v1'
                or manifest.get('replication_mode') != 'mirror'
                or manifest.get('source_geometry_unchanged') is not True
                or not re.fullmatch(r'[0-9a-f]{64}', str(manifest.get('source_package_sha256', '')))):
            return
        entries = manifest.get('templates')
        if not isinstance(entries, list):
            return
        if not any(isinstance(entry, dict)
                   and entry.get('prototype') == svg_path.name
                   and entry.get('source_svg_sha256') == result.get('source_sha256')
                   for entry in entries):
            return
        dependencies = manifest.get('source_files_sha256')
        if not isinstance(dependencies, dict) or not dependencies:
            return
        try:
            for relative, digest in dependencies.items():
                path = (svg_path.parent / relative).resolve()
                path.relative_to(svg_path.parent.parent.resolve())
                if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                    return
        except (OSError, ValueError, TypeError):
            return
        result['info']['mirror_source_unchanged'] = True
        source_import = manifest.get('source_import')
        if isinstance(source_import, dict):
            self._source_import_summary = source_import
        for bucket in ('errors', 'warnings'):
            for message in list(result[bucket]):
                if (message.startswith('<text>') and ' exceeds ' in message) or (
                    message.startswith('<g ') and ' data-pptx-bounds overlaps ' in message
                ):
                    result[bucket].remove(message)
                    result['info'].setdefault('inherited', []).append({
                        'kind': 'mirror-source-geometry', 'message': message,
                    })

    def _record_result(self, result: Dict) -> Dict:
        """Append one file result and update aggregate counters."""
        self.summary['total'] += 1
        if result['passed']:
            if result['warnings']:
                self.summary['warnings'] += 1
            else:
                self.summary['passed'] += 1
        else:
            self.summary['errors'] += 1

        # Categorize issue types
        for error in result['errors']:
            self.issue_types[self._categorize_issue(error)] += 1

        self.results.append(result)
        return result

