"""Pinned PPT Master quality gate component.

MIT Copyright (c) 2025-2026 Hugo He.
Upstream 7e0df6705de0cd24345210d31f07fcb5a32b6900.
"""
from __future__ import annotations
from pathlib import Path
from typing import Dict, List, Tuple
from xml.etree import ElementTree as ET

class DirectoryQualityMixin:
    """Upstream directory checker methods."""

    def check_directory(self, directory: str, expected_format: str = None) -> List[Dict]:
        """
        Check all SVG files in a directory

        Args:
            directory: Directory path
            expected_format: Expected canvas format

        Returns:
            List of check results
        """
        from .checker import (
            CanvasContractError,
            Counter,
            ET,
            Path,
            _CHECK_PPTX_STRUCTURED_PROJECT,
            _declared_template_canvas_viewbox,
            _roster_spec_path,
            _roster_spec_paths,
            _spec_declared_kind,
            _template_spec_paths,
            _template_structure_checks_enabled,
            discover_slide_svgs,
            parse_project_viewbox,
            validate_communication_trace,
            validate_outline_hyperlinks,
            validate_outline_roster,
        )
        dir_path = Path(directory)
        self._has_incomplete_page_roster = False
        self._structured_native_slots: list[str] = []
        self._undeclared_size_occurrences = Counter()
        self._undeclared_size_counts_ready = False

        if not dir_path.exists():
            print(f"[ERROR] Directory does not exist: {directory}")
            self.summary['errors'] += 1
            self.issue_types['Input issues'] += 1
            return []

        # Brand and Style workspaces have no SVG roster. Validate their
        # portable contracts through the same authority used by library
        # registration, while keeping project scope independent of global
        # indexes and directory names.
        if self.template_mode and dir_path.is_dir():
            nested = dir_path / 'templates'
            spec_dir = nested if _template_spec_paths(nested) else dir_path
            specs = _template_spec_paths(spec_dir)
            bare = [spec for spec in specs if spec.name == 'design_spec.md']
            qualified = [spec for spec in specs if spec.name != 'design_spec.md']
            if bare and qualified:
                self._template_issues.append((
                    'error',
                    'spec_naming',
                    'design_spec.md and design_spec.<kind>.<id>.md cannot share '
                    f'{spec_dir}; rename the bare spec to its kind-qualified name',
                ))
                return self.results
            try:
                from register_template import (
                    SpecParseError,
                    validate_qualified_spec_identity,
                )
                for spec in qualified:
                    validate_qualified_spec_identity(spec)
            except ImportError as exc:
                self._template_issues.append((
                    'error',
                    'spec_naming',
                    f'Qualified Design Spec validator could not be imported: {exc}',
                ))
                return self.results
            except (OSError, SpecParseError) as exc:
                self._template_issues.append((
                    'error',
                    'spec_naming',
                    str(exc),
                ))
                return self.results
            declared_kinds = [
                kind
                for spec in qualified
                for kind in [_spec_declared_kind(spec)]
                if kind is not None
            ]
            duplicate_kinds = sorted({
                kind for kind in declared_kinds
                if declared_kinds.count(kind) > 1
            })
            if duplicate_kinds:
                self._template_issues.append((
                    'error',
                    'spec_naming',
                    f'{spec_dir} declares the same kind more than once: '
                    + ', '.join(duplicate_kinds),
                ))
                return self.results
            active_roster_spec = _roster_spec_path(spec_dir)
            shadowed_deck_specs = [
                spec
                for spec in _roster_spec_paths(spec_dir)
                if spec != active_roster_spec
                and _spec_declared_kind(spec) == 'deck'
            ]
            for spec in shadowed_deck_specs:
                try:
                    from register_template import (
                        SpecParseError,
                        validate_shadowed_deck_spec,
                    )
                    declared_pages = self._extract_spec_roster(
                        spec.read_text(encoding='utf-8')
                    )
                    validate_shadowed_deck_spec(spec, declared_pages)
                except ImportError as exc:
                    self._template_issues.append((
                        'error',
                        'deck_contract',
                        f'Shadowed Deck validator could not be imported: {exc}',
                    ))
                    return self.results
                except (OSError, SpecParseError) as exc:
                    self._template_issues.append((
                        'error',
                        'deck_contract',
                        str(exc),
                    ))
                    return self.results
            roster_free = [
                (spec, kind)
                for spec in _template_spec_paths(spec_dir)
                for kind in [_spec_declared_kind(spec)]
                if kind in {'brand', 'style'}
            ]
            for spec, spec_kind in roster_free:
                self._spec_only_template_kind = spec_kind
                self.summary['total'] += 1
                spec_valid = True
                pretty_kind = spec_kind.title()
                print(
                    f"[INFO] {pretty_kind} spec detected "
                    f"({spec.name}) — "
                    f"validating its portable workspace contract."
                )
                workspace_root = (
                    spec.parent.parent
                    if spec.parent.name == 'templates'
                    else spec.parent
                )
                try:
                    from register_template import (
                        SpecParseError,
                        validate_brand_workspace,
                        validate_style_workspace,
                    )
                    validator = {
                        'brand': validate_brand_workspace,
                        'style': validate_style_workspace,
                    }[spec_kind]
                    validator(workspace_root)
                except ImportError as exc:
                    spec_valid = False
                    self._template_issues.append((
                        'error',
                        f'{spec_kind}_contract',
                        f"{pretty_kind} schema validator could not be imported: {exc}",
                    ))
                except (OSError, SpecParseError) as exc:
                    spec_valid = False
                    self._template_issues.append((
                        'error',
                        f'{spec_kind}_contract',
                        str(exc),
                    ))
                if spec_valid:
                    self.summary['passed'] += 1
            # A roster-bearing Layout/Deck spec may sit beside those in one
            # project workspace; only then does SVG validation still apply.
            if roster_free and _roster_spec_path(spec_dir) is None:
                return self.results

        # Find all SVG files
        if dir_path.is_file():
            svg_files = [dir_path]
        else:
            if self.template_mode:
                # Template directories live at templates/{layouts,decks}/<id>/.
                svg_files = discover_slide_svgs(dir_path)
            else:
                svg_output = dir_path / \
                    'svg_output' if (
                        dir_path / 'svg_output').exists() else dir_path
                svg_files = discover_slide_svgs(svg_output)

        if not svg_files:
            print(f"[ERROR] No SVG files found in: {directory}")
            self.summary['errors'] += 1
            self.issue_types['Input issues'] += 1
            return []

        self._active_slide_count = len(svg_files)

        self._configure_prototype_context(dir_path, svg_files)
        if not self.template_mode:
            self._prepare_undeclared_size_occurrences(svg_files)

        directory_expected_viewbox: str | None = None
        directory_expected_label = "the first SVG canvas"
        directory_lock_has_canvas = False
        if self.template_mode:
            template_viewbox = _declared_template_canvas_viewbox(dir_path)
            if template_viewbox:
                directory_expected_viewbox = template_viewbox
                directory_expected_label = "design_spec canvas_viewbox"
            else:
                directory_expected_viewbox = ""
                directory_expected_label = "design_spec canvas_viewbox"
        if expected_format is None and directory_expected_viewbox is None:
            lock = (
                None
                if self.template_mode
                else self._get_spec_lock(svg_files[0])
            )
            if lock is not None:
                if 'canvas' in lock:
                    directory_lock_has_canvas = True
                    locked_viewbox = lock.get('canvas', {}).get('viewBox')
                    if locked_viewbox:
                        directory_expected_viewbox = locked_viewbox
                        directory_expected_label = "spec_lock canvas"
                else:
                    directory_expected_viewbox = ""
                    directory_expected_label = "spec_lock canvas"
            if (
                directory_expected_viewbox is None
                and not directory_lock_has_canvas
            ):
                for svg_file in svg_files:
                    try:
                        root = ET.parse(svg_file).getroot()
                        first_canvas = parse_project_viewbox(
                            root.get('viewBox'),
                            context=f"{svg_file.name} root viewBox",
                        )
                    except (OSError, ET.ParseError, CanvasContractError):
                        continue
                    directory_expected_viewbox = first_canvas.canonical
                    directory_expected_label = f"first SVG {svg_file.name}"
                    break

        if self.scan_banner:
            print(f"\n[SCAN] Checking {len(svg_files)} SVG file(s)...\n")

        for svg_file in svg_files:
            self._active_prototype_path = self._prototype_by_output.get(
                svg_file.resolve()
            )
            result = self.check_file(
                str(svg_file),
                expected_format,
                expected_viewbox=directory_expected_viewbox,
                expected_viewbox_label=directory_expected_label,
            )
            self._print_result(result)

        if self.template_mode:
            check_structure = _template_structure_checks_enabled(dir_path)
            if check_structure:
                self._check_pptx_structure_contract(dir_path, svg_files)
            if dir_path.is_dir():
                self._check_template_contract(
                    dir_path,
                    svg_files,
                    check_structure=check_structure,
                )
        elif _CHECK_PPTX_STRUCTURED_PROJECT:
            self._check_pptx_structure_contract(dir_path, svg_files)
        if (
            not self.template_mode
            and not self.quick_generate
            and dir_path.is_dir()
        ):
            self._check_animation_config_contract(dir_path)
            self._check_illustration_resource_contract(dir_path)
        if (
            not self.template_mode
            and not self.quick_generate
            and validate_communication_trace is not None
        ):
            project_path = self._resolve_project_path(dir_path)
            if project_path not in self._communication_traced_projects:
                self._communication_traced_projects.add(project_path)
                self._communication_trace_issues.extend(
                    ('error', message)
                    for message in validate_communication_trace(project_path)
                )
                if not self.partial_roster and validate_outline_roster is not None:
                    self._communication_trace_issues.extend(
                        ('error', message)
                        for message in validate_outline_roster(project_path)
                    )
                if not self.partial_roster and validate_outline_hyperlinks is not None:
                    self._communication_trace_issues.extend(
                        ('error', message)
                        for message in validate_outline_hyperlinks(project_path)
                    )
        return self.results

    def _check_pptx_structure_contract(
        self,
        target_path: Path,
        svg_files: List[Path],
    ) -> None:
        """Validate the all-page structured lock and reusable contracts."""
        from .checker import (
            ET,
            _PPTX_STRUCTURE_ATTRS,
            _TemplateStructureError,
            _declared_pptx_structure_mode,
            _generated_theme_contract_errors,
            _load_pptx_structure_lock,
            _parse_optional_layout_slides,
            _parse_template_structure_slide,
            _parse_template_structure_slides,
            _structure_subtree_signature,
            _structured_layout_definition_files,
            _template_lock_errors,
            _template_prototype_errors,
            discover_slide_svgs,
        )
        if self.quick_generate:
            if (
                _parse_optional_layout_slides is None
                or _TemplateStructureError is None
            ):
                self._pptx_structure_issues.append((
                    'error',
                    'Quick PPTX structure inference is unavailable because '
                    'the template_structure module could not be imported.',
                ))
                return
            try:
                specs = _parse_optional_layout_slides(svg_files)
            except _TemplateStructureError as exc:
                self._pptx_structure_issues.append(('error', str(exc)))
                return
            if specs is None:
                return
            self._structured_native_slots = sorted({
                str(item.placeholder) for spec in specs for item in spec.placeholders
                if item.placeholder in {'chart', 'table'}
            })
            self._pptx_structure_issues.extend(
                ('error', message)
                for message in self._shared_fixed_layer_errors(specs)
            )
            self._pptx_structure_issues.extend(
                ('warning', message)
                for message in self._duplicate_layout_key_warnings(specs)
            )
            return
        project_path = self._resolve_project_path(target_path)
        standard_project = bool(
            not self.template_mode
            and (project_path / 'svg_output').is_dir()
        )
        declared_mode = (
            _declared_pptx_structure_mode(project_path)
            if standard_project
            else None
        )
        implicit_flat = bool(standard_project) and not declared_mode
        if implicit_flat:
            self._pptx_structure_issues.append((
                'warning',
                'spec_lock.md declares no pptx_structure.mode; the project is '
                'treated as mode: flat, matching the exporter default. Declare '
                'mode: flat explicitly, or mode: structured for a deck/layout '
                'template.',
            ))
            declared_mode = 'flat'
        if standard_project and declared_mode in {'flat', 'structured'}:
            self._pptx_structure_issues.extend(
                ('error', message)
                for message in _generated_theme_contract_errors(project_path)
            )
        if standard_project and declared_mode == 'flat':
            if (
                _load_pptx_structure_lock is None
                or _TemplateStructureError is None
            ):
                self._pptx_structure_issues.append((
                    'error',
                    'Flat PPTX project validation is unavailable because the '
                    'template_structure module could not be imported.',
                ))
                return
            try:
                structure_lock = _load_pptx_structure_lock(project_path)
            except _TemplateStructureError as exc:
                self._pptx_structure_issues.append(('error', str(exc)))
                return
            if structure_lock is None and implicit_flat:
                return
            if structure_lock is None or structure_lock.mode != 'flat':
                self._pptx_structure_issues.append((
                    'error',
                    'spec_lock.md must contain one complete '
                    'pptx_structure.mode: flat contract.',
                ))
            return
        has_metadata = False
        for svg_path in svg_files:
            try:
                root = ET.parse(svg_path).getroot()
            except (OSError, ET.ParseError):
                continue
            if any(
                elem.get(attr) is not None
                for elem in root.iter()
                for attr in _PPTX_STRUCTURE_ATTRS
            ):
                has_metadata = True
                break

        if not standard_project and not self.template_mode and not has_metadata:
            return
        if (
            _load_pptx_structure_lock is None
            or _parse_template_structure_slide is None
            or _parse_template_structure_slides is None
            or _structure_subtree_signature is None
            or _template_lock_errors is None
            or _TemplateStructureError is None
        ):
            self._pptx_structure_issues.append((
                'error',
                'Structured PPTX project validation is unavailable because the '
                'template_structure module could not be imported.',
            ))
            return

        if self.template_mode:
            try:
                specs = _parse_template_structure_slides(svg_files)
            except _TemplateStructureError as exc:
                self._pptx_structure_issues.append(('error', str(exc)))
                return
            self._pptx_structure_issues.extend(
                ('error', message)
                for message in self._shared_fixed_layer_errors(specs)
            )
            self._pptx_structure_issues.extend(
                ('warning', message)
                for message in self._duplicate_layout_key_warnings(specs)
            )
            return

        if standard_project and declared_mode != 'structured':
            self._pptx_structure_issues.append((
                'error',
                'release SVG projects require spec_lock.md pptx_structure.mode: '
                'flat (free design / brand-only) or structured (deck/layout '
                f'template); found {declared_mode!r}. Create a template '
                'workspace through skills/ppt-master/workflows/create-template.md '
                'before generating structured SVG pages. Existing PPTX/SVG files '
                'are not upgraded in place.',
            ))
            return

        try:
            structure_lock = _load_pptx_structure_lock(project_path)
        except _TemplateStructureError as exc:
            self._pptx_structure_issues.append(('error', str(exc)))
            return
        if structure_lock is None or structure_lock.mode != 'structured':
            self._pptx_structure_issues.append((
                'error',
                'spec_lock.md must contain one complete '
                'pptx_structure.mode: structured contract.',
            ))
            return
        complete_roster = target_path.is_dir()
        try:
            if not complete_roster and target_path.is_file():
                sibling_files = discover_slide_svgs(target_path.parent)
                resolved_target = target_path.resolve()
                slide_num = next(
                    (
                        index
                        for index, sibling in enumerate(sibling_files, start=1)
                        if sibling.resolve() == resolved_target
                    ),
                    1,
                )
                specs = [
                    _parse_template_structure_slide(target_path, slide_num)
                ]
            else:
                specs = _parse_template_structure_slides(svg_files)
        except _TemplateStructureError as exc:
            self._pptx_structure_issues.append(('error', str(exc)))
            return

        dependency_specs = list(specs)
        try:
            # Use the exporter's registered dependencies, including explicitly
            # retained prototypes which have no public page in this deck.
            definition_files = _structured_layout_definition_files(specs, structure_lock)
            dependency_specs.extend(
                _parse_template_structure_slide(path, len(specs) + index)
                for index, path in enumerate(definition_files, start=1)
            )
        except _TemplateStructureError as exc:
            if complete_roster:
                self._pptx_structure_issues.append(('error', str(exc)))
        self._structured_native_slots = sorted({
            str(item.placeholder)
            for spec in dependency_specs
            for item in spec.placeholders
            if item.placeholder in {'chart', 'table'}
        })
        if complete_roster:
            actual_slides = {spec.slide_num for spec in specs}
            expected_slides = {
                reference.slide_num
                for reference in structure_lock.layouts
            }
            expected_slides.update(
                reference.slide_num
                for reference in structure_lock.prototypes
            )
            self._has_incomplete_page_roster = bool(
                expected_slides - actual_slides
            )
            self._pptx_structure_issues.extend(
                ('error', message)
                for message in _template_lock_errors(specs, structure_lock)
            )
        else:
            self._pptx_structure_issues.extend(
                ('error', message)
                for message in self._partial_structure_lock_errors(
                    specs,
                    structure_lock,
                )
            )
        if _template_prototype_errors is not None:
            self._pptx_structure_issues.extend(
                ('error', message)
                for message in _template_prototype_errors(
                    specs,
                    structure_lock,
                    require_complete_roster=complete_roster,
                )
            )
        self._pptx_structure_issues.extend(
            ('error', message)
            for message in self._shared_fixed_layer_errors(specs)
        )
        self._pptx_structure_issues.extend(
            ('warning', message)
            for message in self._duplicate_layout_key_warnings(specs)
        )

