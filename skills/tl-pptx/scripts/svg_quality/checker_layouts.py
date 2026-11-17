"""Pinned PPT Master quality gate component.

MIT Copyright (c) 2025-2026 Hugo He.
Upstream 7e0df6705de0cd24345210d31f07fcb5a32b6900.
"""
from __future__ import annotations
from pathlib import Path
from typing import Dict, List, Tuple
from xml.etree import ElementTree as ET

class LayoutsQualityMixin:
    """Upstream layouts checker methods."""

    @staticmethod
    def _partial_structure_lock_errors(specs, structure_lock) -> List[str]:
        """Compare explicitly checked pages without requiring the full roster."""
        references = {
            reference.slide_num: reference
            for reference in structure_lock.layouts
        }
        master_names = {
            master.master_key: master.master_name
            for master in structure_lock.masters
        }
        definitions = {
            definition.layout_key: definition
            for definition in structure_lock.layout_definitions
        }
        errors: List[str] = []
        for spec in specs:
            page = f"P{spec.slide_num:02d}"
            reference = references.get(spec.slide_num)
            if reference is None:
                errors.append(
                    f"spec_lock.md page_pptx_layouts is missing {page}"
                )
                continue
            definition = definitions.get(reference.layout_key)
            if definition is None:
                errors.append(
                    f"spec_lock.md pptx_layouts is missing Layout "
                    f"{reference.layout_key!r}"
                )
                continue
            if spec.master_key != definition.master_key:
                errors.append(
                    f"{spec.svg_path.name}: data-pptx-master={spec.master_key!r} "
                    f"does not match spec_lock Layout {reference.layout_key!r} "
                    f"Master key {definition.master_key!r}"
                )
            if spec.layout_key != reference.layout_key:
                errors.append(
                    f"{spec.svg_path.name}: data-pptx-layout={spec.layout_key!r} "
                    f"does not match spec_lock {page} layout key "
                    f"{reference.layout_key!r}"
                )
            if spec.layout_name != definition.layout_name:
                errors.append(
                    f"{spec.svg_path.name}: data-pptx-layout-name="
                    f"{spec.layout_name!r} does not match spec_lock Layout "
                    f"{reference.layout_key!r} name {definition.layout_name!r}"
                )
            expected_master_name = master_names.get(spec.master_key)
            if expected_master_name != spec.master_name:
                errors.append(
                    f"{spec.svg_path.name}: data-pptx-master-name="
                    f"{spec.master_name!r} does not match spec_lock Master "
                    f"{spec.master_key!r} name {expected_master_name!r}"
                )
        return errors

    def _duplicate_layout_key_warnings(self, specs) -> List[str]:
        """Flag distinct layout keys whose static contracts are identical.

        Keys split by page topic over one shared skeleton compile into
        duplicate PowerPoint Layouts; the fingerprint compares the
        id-insensitive layout-layer drawing plus the placeholder contract.
        """
        prototypes: Dict[Tuple[str, str], Path] = {}
        for spec in specs:
            prototypes.setdefault(
                (getattr(spec, 'master_key', ''), spec.layout_key),
                spec.svg_path,
            )
        if len(prototypes) < 2:
            return []
        fingerprint_keys: Dict[tuple, List[str]] = {}
        for (master_key, layout_key), svg_path in prototypes.items():
            fingerprint = self._layout_contract_fingerprint(svg_path)
            if fingerprint is None:
                continue
            fingerprint_keys.setdefault(
                (master_key, fingerprint),
                [],
            ).append(layout_key)
        messages = []
        for keys in fingerprint_keys.values():
            if len(keys) < 2:
                continue
            joined = ', '.join(sorted(keys))
            messages.append(
                f"layout keys {joined} declare identical static Layout framing "
                "and placeholder contracts; they compile to duplicate Layouts. "
                "Either merge them into one reusable key (spec_lock.md "
                "pptx_layouts + each SVG root), or — when their reusable "
                "contracts genuinely differ — assign distinct explicit default "
                "placeholder bounds and/or mark only truly stable framing as "
                'data-pptx-layer="layout". Slide-local content geometry does not '
                "define a Layout. This recommendation is advisory; no change or "
                "disposition is required."
            )
        return messages

    @classmethod
    def _shared_fixed_layer_errors(cls, specs) -> List[str]:
        """Reject fixed atoms whose payload varies inside one reuse scope."""
        from .checker import (
            _TemplateStructureError,
            defaultdict,
        )
        master_groups = defaultdict(list)
        layout_groups = defaultdict(list)
        for spec in specs:
            master_groups[spec.master_key].append(spec)
            layout_groups[(spec.master_key, spec.layout_key)].append(spec)

        try:
            errors = cls._fixed_layer_group_errors(master_groups, 'master')
            errors.extend(cls._fixed_layer_group_errors(layout_groups, 'layout'))
        except _TemplateStructureError as exc:
            return [str(exc)]
        return errors

    @classmethod
    def _fixed_layer_group_errors(cls, groups, layer: str) -> List[str]:
        """Compare fixed atom payloads across grouped slide specifications."""
        from .checker import (
            defaultdict,
        )
        errors = []
        for scope_key, group_specs in groups.items():
            if len(group_specs) < 2:
                continue
            variants = defaultdict(lambda: defaultdict(list))
            for spec in group_specs:
                payloads = cls._fixed_layer_payloads(spec, layer)
                for element_id, payload in payloads.items():
                    variants[element_id][payload].append(spec)
            for element_id, payload_specs in variants.items():
                if len(payload_specs) < 2:
                    continue
                slide_names = ', '.join(
                    spec.svg_path.name
                    for spec in sorted(group_specs, key=lambda item: item.slide_num)
                )
                if layer == 'master':
                    scope = f"Master {scope_key!r}"
                else:
                    master_key, layout_key = scope_key
                    scope = (
                        f"Layout {layout_key!r} under Master {master_key!r}"
                    )
                if element_id is None:
                    subject = "fixed visual resources"
                    verb = "differ"
                else:
                    subject = f"fixed element {element_id!r}"
                    verb = "differs"
                errors.append(
                    f"{scope} {subject} {verb} across slides: "
                    f"{slide_names}. Values marked data-pptx-layer={layer!r} must "
                    "remain identical throughout their reuse scope; move variable "
                    "text or images into a placeholder slot or keep them Slide-local."
                )
        return errors

    @staticmethod
    def _fixed_layer_payloads(spec, layer: str) -> Dict[object, tuple]:
        """Return resolved fixed-layer visual payloads keyed by SVG id."""
        from .checker import (
            _structure_subtree_signature,
        )
        elements = (
            spec.master_elements if layer == 'master' else spec.layout_elements
        )
        if not elements:
            return {}
        signature = _structure_subtree_signature(
            spec.svg_path,
            elements,
            include_skin=True,
            include_text=True,
            asset_identity=True,
        )
        return {
            None if element_id == '__visual_resources__' else element_id: payload
            for element_id, payload in signature
        }

    @staticmethod
    def _layout_contract_fingerprint(svg_path: Path):
        """Id-insensitive static contract: layout-layer XML + placeholder slots."""
        from .checker import (
            ET,
            copy,
            re,
        )
        try:
            root = ET.parse(str(svg_path)).getroot()
        except (OSError, ET.ParseError):
            return None
        layout_parts = []
        placeholder_parts = []
        for child in list(root):
            if child.get('data-pptx-layer') == 'layout':
                clone = copy.deepcopy(child)
                for elem in clone.iter():
                    elem.attrib.pop('id', None)
                xml = ET.tostring(clone, encoding='unicode')
                layout_parts.append(re.sub(r'\s+', ' ', xml).strip())
            placeholder = child.get('data-pptx-placeholder')
            if placeholder is not None:
                carrier_tags = tuple(
                    grandchild.tag.rsplit('}', 1)[-1]
                    for grandchild in list(child)
                    if (
                        grandchild.get('data-pptx-carrier') or ''
                    ).strip().lower() == 'true'
                )
                placeholder_parts.append((
                    placeholder,
                    child.tag.rsplit('}', 1)[-1],
                    child.get('data-pptx-bounds') or '',
                    child.get('data-pptx-idx') or '',
                    (
                        child.get('data-pptx-binding') or 'carrier'
                    ).strip().lower(),
                    carrier_tags,
                ))
        return (
            tuple(layout_parts),
            tuple(sorted(placeholder_parts)),
        )

    def _check_illustration_resource_contract(self, dir_path: Path) -> None:
        """Project-level planned-image and illustration resource checks."""
        from .checker import (
            Path,
            re,
        )
        project_path = self._resolve_project_path(dir_path)
        spec_path = project_path / 'design_spec.md'
        if not spec_path.exists():
            return

        try:
            spec_text = spec_path.read_text(encoding='utf-8')
        except OSError as exc:
            self._illustration_issues.append((
                'warning',
                'spec_unreadable',
                f"could not read {spec_path}: {exc}",
            ))
            return

        current_contract = (
            '<!-- ppt-master-schema: design-spec/v1 -->' in spec_text
        )
        rows = self._extract_image_resource_rows(spec_text)
        if not rows and not current_contract:
            return

        lock_entries, lock_error = self._load_project_lock_image_entries(
            project_path
        )
        lock_images = set(lock_entries)
        svg_references, inline_image_counts, image_placements = (
            self._load_project_svg_image_references(project_path)
        )
        all_svg_references = (
            set().union(*(
                set(references)
                for references in svg_references.values()
            ))
            if svg_references
            else set()
        )

        sheet_rows = [
            row
            for row in rows
            if self._row_type(row).lower() == 'illustration sheet'
        ]
        slice_rows = [row for row in rows if self._row_acquire(row) == 'slice']
        for row in sheet_rows:
            filename = self._row_filename(row)
            if not filename:
                continue
            if filename in lock_images:
                self._illustration_issues.append((
                    'error',
                    'sheet_in_lock',
                    f"{filename} is an Illustration Sheet but is listed in spec_lock.md images; "
                    "only sliced element rows may be listed.",
                ))
            if filename in all_svg_references:
                self._illustration_issues.append((
                    'error',
                    'sheet_referenced',
                    f"{filename} is an Illustration Sheet but is referenced by an SVG; "
                    "generate it only as a slice source, never place it.",
                ))
            if (
                self._row_status(row) == 'generated'
                and not (project_path / 'images' / filename).is_file()
            ):
                self._illustration_issues.append((
                    'error',
                    'sheet_file_missing',
                    f"{filename} is a Generated Illustration Sheet but "
                    f"images/{filename} does not exist.",
                ))

        # ``Type: Source`` marks a web/user/ai original that only feeds
        # prepared derivatives; like a sheet it never enters the lock or a page.
        derived_parents = set()
        for row in rows:
            match = re.search(
                r'derived\s+from\s+`?([^;|`]+?)`?\s*(?:;|$)',
                row.get('Reference', ''),
                re.IGNORECASE,
            )
            if match:
                derived_parents.add(Path(match.group(1).strip()).name)
        for row in rows:
            if self._row_type(row).lower() != 'source':
                continue
            filename = self._row_filename(row)
            if not filename:
                continue
            if filename in lock_images:
                self._illustration_issues.append((
                    'error',
                    'source_in_lock',
                    f"{filename} is a Source row (unplaced derivation parent) "
                    "but is listed in spec_lock.md images; lock only its "
                    "placed derivatives.",
                ))
            if filename in all_svg_references:
                self._illustration_issues.append((
                    'error',
                    'source_referenced',
                    f"{filename} is a Source row but is referenced by an SVG; "
                    "give the row a placed Type or place a derivative instead.",
                ))
            if filename not in derived_parents:
                self._illustration_issues.append((
                    'error',
                    'source_without_derivative',
                    f"{filename} is a Source row but no row is `Derived from "
                    f"{filename}`; a Source row exists only to feed prepared "
                    "derivatives.",
                ))

        if current_contract:
            self._check_planned_image_closure(
                rows,
                project_path,
                lock_entries,
                lock_error,
                svg_references,
                inline_image_counts,
                image_placements,
            )
        else:
            for row in slice_rows:
                filename = self._row_filename(row)
                if not filename:
                    continue
                if filename not in lock_images:
                    self._illustration_issues.append((
                        'error',
                        'slice_missing_lock',
                        f"{filename} is a slice row but is absent from spec_lock.md images.",
                    ))
                if (
                    self._row_status(row) == 'generated'
                    and not (project_path / 'images' / filename).exists()
                ):
                    self._illustration_issues.append((
                        'error',
                        'slice_file_missing',
                        f"{filename} is a Generated slice row but "
                        f"images/{filename} does not exist.",
                    ))

    @staticmethod
    def _resolve_project_path(dir_path: Path) -> Path:
        """Resolve a checker target directory to its project root."""
        from .checker import (
            _SVG_WORK_DIR_NAMES,
            _project_root_for_svg_path,
        )
        candidate = dir_path.parent if dir_path.is_file() else dir_path
        if (
            _project_root_for_svg_path is not None
            and candidate.name in _SVG_WORK_DIR_NAMES
        ):
            return _project_root_for_svg_path(candidate)
        if (
            (candidate / 'svg_output').exists()
            or (candidate / 'design_spec.md').exists()
        ):
            return candidate
        return candidate.parent

