"""Pinned PPT Master quality gate component.

MIT Copyright (c) 2025-2026 Hugo He.
Upstream 7e0df6705de0cd24345210d31f07fcb5a32b6900.
"""
from __future__ import annotations
from pathlib import Path
from typing import Dict, List, Tuple
from xml.etree import ElementTree as ET

class RoundtripQualityMixin:
    """Upstream roundtrip checker methods."""

    def check_roundtrip_workspace(self, workspace: str) -> List[Dict]:
        """Check edited text on the resolved round-trip output page roster."""
        from .checker import (
            Path,
        )
        from authoring_roundtrip import (
            AuthoringRoundtripError,
            _generate_baseline_bundle,
            _load_current_assets,
            _load_documents,
            _load_page_plan,
            _parse_svg,
            roundtrip_source_fingerprint,
        )

        project_path = Path(workspace).resolve()
        authoring_dir = project_path / 'authoring-svg-flat'
        self._has_incomplete_page_roster = False
        self._structured_native_slots: list[str] = []
        if not project_path.is_dir():
            print(f"[ERROR] Round-trip workspace does not exist: {project_path}")
            self.summary['errors'] += 1
            self.issue_types['Input issues'] += 1
            return []
        if not authoring_dir.is_dir():
            print(
                "[ERROR] Round-trip workspace has no authoring-svg-flat/: "
                f"{project_path}"
            )
            self.summary['errors'] += 1
            self.issue_types['Input issues'] += 1
            return []

        try:
            self._roundtrip_source_fingerprint = roundtrip_source_fingerprint(project_path)
            source_root, source_proxy_dir, documents, _, _ = _load_documents(
                project_path,
                authoring_dir,
            )
            pages, _ = _load_page_plan(
                project_path,
                authoring_dir,
                documents,
            )
            current_assets, inventory = _load_current_assets(
                project_path,
                authoring_dir,
                documents,
            )
            baseline_temporary, baseline_dir, baseline_assets = (
                _generate_baseline_bundle(
                    project_path,
                    source_root,
                    source_proxy_dir,
                    documents,
                    inventory,
                )
            )
        except (AuthoringRoundtripError, OSError, ValueError) as exc:
            print(f"[ERROR] Invalid round-trip workspace: {exc}")
            self.summary['errors'] += 1
            self.issue_types['Input issues'] += 1
            return []

        documents_by_slide = {
            document.source_slide: document
            for document in documents.values()
        }
        self._active_slide_count = len(pages)
        print(
            f"\n[SCAN] Checking {len(pages)} round-trip output SVG page(s) "
            "for edited-text horizontal capacity (single-line width per "
            "positioned line; vertical wrapping is not modeled)...\n"
        )
        try:
            baseline_roots = {
                source_slide: _parse_svg(
                    baseline_dir / document.name,
                )
                for source_slide, document in documents_by_slide.items()
            }
            for page in pages:
                result = self._check_roundtrip_file(
                    authoring_dir / page.svg_name,
                    documents_by_slide[page.source_slide],
                    baseline_roots[page.source_slide],
                    current_assets,
                    baseline_assets,
                    output_index=page.output_index,
                    source_slide=page.source_slide,
                )
                self._print_result(result)
        finally:
            baseline_temporary.cleanup()
        return self.results

    def _check_roundtrip_file(
        self,
        svg_path: Path,
        source_document,
        baseline_root: ET.Element,
        current_assets,
        baseline_assets,
        *,
        output_index: int,
        source_slide: int,
    ) -> Dict:
        """Run only edited-text checks for one resolved round-trip page."""
        from .checker import (
            hashlib,
            svg_contracts,
        )
        result = {
            'file': svg_path.name,
            'path': str(svg_path),
            'exists': svg_path.is_file(),
            'errors': [],
            'warnings': [],
            'info': {
                'output_page': output_index,
                'source_slide': source_slide,
            },
            'passed': True,
        }
        if not svg_path.is_file():
            result['errors'].append('File does not exist')
            result['passed'] = False
            return self._record_result(result)

        try:
            source_bytes = svg_path.read_bytes()
            result['source_sha256'] = hashlib.sha256(source_bytes).hexdigest()
            content = source_bytes.decode('utf-8')
            root = self._parse_xml_root(content, result)
            if root is not None:
                self._check_viewbox(root, svg_path, result, None)
                included_text_ids, unchanged_text_ids = (
                    self._roundtrip_text_diff_ids(
                        root,
                        source_document,
                        baseline_root,
                        current_assets,
                        baseline_assets,
                    )
                )
                result['info']['edited_text_elements'] = len(included_text_ids)
                self._check_semantic_shape_text(
                    root, result, included_text_ids=included_text_ids,
                )
                if included_text_ids:
                    scoped_content = self._roundtrip_text_scope_content(
                        root,
                        included_text_ids,
                    )
                    changed_font_families = (
                        self._roundtrip_changed_font_families(
                            root,
                            baseline_root,
                            included_text_ids,
                        )
                    )
                    svg_contracts.check_font_size_values(scoped_content, result)
                    self._check_fonts(
                        scoped_content,
                        result,
                        font_families=changed_font_families,
                    )
                    self._check_text_output_geometry(
                        root,
                        result,
                        included_text_ids=included_text_ids,
                    )
                    self._check_text_bounds(
                        root,
                        result,
                        included_text_ids=included_text_ids,
                    )
                self._check_roundtrip_text_frames(
                    root,
                    result,
                    included_text_ids,
                    unchanged_text_ids,
                    baseline_root=baseline_root,
                )
                self._check_roundtrip_unsupported_table_edits(
                    root,
                    result,
                    included_text_ids,
                )
                self._check_roundtrip_chart_edits(root, baseline_root, source_document, result)
            result['passed'] = len(result['errors']) == 0
        except Exception as exc:
            result['errors'].append(f"Failed to read file: {exc}")
            result['passed'] = False
        return self._record_result(result)

    @staticmethod
    def _check_roundtrip_chart_edits(root, baseline_root, source_document, result: Dict) -> None:
        """Surface source-only chart losses before the round-trip export."""
        from .checker import (
            _element_label,
        )
        from authoring_roundtrip import (
            _layered_ref_index,
            _parse_svg,
            chart_rebuild_warning,
            prepare_imported_chart_edit,
        )
        baseline_by_ref = {node.get('data-pptx-source-ref'): node for node in baseline_root.iter()
                           if node.get('data-pptx-source-ref')}
        candidates = [node for node in root.iter() if node.get('data-pptx-replace-with') == 'chart'
                      and node.get('data-pptx-source-ref') in baseline_by_ref]
        if not candidates:
            return
        sources = _layered_ref_index(_parse_svg(source_document.layered_source_path))
        project_path = source_document.authoring_path.parent.parent
        for node in candidates:
            source_ref = node.get('data-pptx-source-ref')
            source = sources.get(source_ref)
            if source is None:
                continue
            try:
                _package, reason = prepare_imported_chart_edit(
                    node, baseline_by_ref[source_ref], source,
                    current_root=root, baseline_root=baseline_root,
                )
            except RuntimeError as exc:
                result['errors'].append(f"{_element_label(node)}: {exc}")
                continue
            if reason:
                result['warnings'].append(
                    f"{_element_label(node)} source_ref={source_ref}: "
                    + chart_rebuild_warning(project_path, source_document.source_slide, source_ref, reason)
                )

    @staticmethod
    def _check_roundtrip_unsupported_table_edits(
        root: ET.Element,
        result: Dict,
        included_text_ids: set[int],
    ) -> None:
        """Warn when an edited page rewrites a table the importer could not
        carry natively: it exports as shapes and a baked grid, not ``a:tbl``."""
        from .checker import (
            SVG_NS,
            _element_label,
        )
        if not included_text_ids:
            return
        for frame in root.iter(f'{{{SVG_NS}}}g'):
            status = frame.get('data-pptx-replacement-status') or ''
            if not status.startswith('unsupported-table'):
                continue
            if not any(
                id(text) in included_text_ids
                for text in frame.iter(f'{{{SVG_NS}}}text')
            ):
                continue
            result['warnings'].append(
                f"{_element_label(frame)}: edited text belongs to a source table "
                f"the importer marked {status}; this page exports the table as "
                "positioned text over a baked grid, not a native a:tbl. Leave the "
                "table unchanged to restore the original, or accept shape output"
            )

    @staticmethod
    def _check_semantic_shape_text(
        root: ET.Element,
        result: Dict,
        *,
        included_text_ids: set[int] | None = None,
    ) -> None:
        """Report the exporter's semantic-shape text contract before conversion."""
        from .checker import (
            SEMANTIC_OBJECT_ATTRIBUTE,
            SEMANTIC_SHAPE_KIND,
            SVG_NS,
            _classify_paragraph_block,
            _element_label,
            _local_name,
            _svg_hidden_reason,
            semantic_shape_text_component,
        )
        parents = {id(child): parent for parent in root.iter() for child in parent}
        for shape in root.iter(f'{{{SVG_NS}}}g'):
            if shape.get(SEMANTIC_OBJECT_ATTRIBUTE) != SEMANTIC_SHAPE_KIND:
                continue
            if _svg_hidden_reason is not None and _svg_hidden_reason(
                shape, parents, preserve_native_carriers=True,
            ) is not None:
                continue
            try:
                component = semantic_shape_text_component(
                    shape, included_text_ids=included_text_ids,
                )
            except ValueError as exc:
                result['errors'].append(f"{_element_label(shape)}: {exc}")
                continue
            if (
                component is None
                or _classify_paragraph_block is None
                or not any(
                    _local_name(child) == 'tspan'
                    and any(child.get(name) for name in ('x', 'y', 'dy'))
                    for child in component
                )
                or _classify_paragraph_block(
                    component, preserve_line_breaks=True,
                ) is not None
            ):
                continue
            result['errors'].append(
                f"{_element_label(shape)}: multi-line semantic shape text must "
                "stay one paragraph block, which export otherwise splits into "
                "several text components; write the first line on <text x y> "
                "and each later line as <tspan x dy>, never an absolute tspan y"
            )

    @staticmethod
    def _roundtrip_text_diff_ids(
        root: ET.Element,
        source_document,
        baseline_root: ET.Element,
        current_assets,
        baseline_assets,
    ) -> tuple[set[int], set[int]]:
        """Return edited and hash-unchanged source text carrier identities."""
        from .checker import (
            Counter,
            SVGQualityChecker,
            SVG_NS,
        )
        from authoring_roundtrip import (
            _definition_changes,
            authoring_source_ref_is_unchanged,
        )
        from svg_authoring_view import (
            SOURCE_PROXY_ATTRIBUTE,
            SOURCE_PROXY_KIND,
            SOURCE_REF_ATTRIBUTE,
        )

        baseline_by_ref: Dict[str, ET.Element] = {}
        for element in baseline_root.iter():
            source_ref = element.get(SOURCE_REF_ATTRIBUTE)
            if source_ref is None:
                continue
            if source_ref in baseline_by_ref:
                raise ValueError(
                    f"Regenerated baseline repeats source ref {source_ref!r}"
                )
            baseline_by_ref[source_ref] = element
        _, changed_definition_ids = _definition_changes(
            root,
            baseline_root,
            current_assets,
            baseline_assets,
        )
        parent_by_id = {
            id(child): parent
            for parent in root.iter()
            for child in list(parent)
        }
        unchanged_by_owner: Dict[int, bool] = {}
        baseline_signatures: Dict[int, Counter] = {}
        baseline_parent_by_id = {
            id(child): parent
            for parent in baseline_root.iter()
            for child in list(parent)
        }
        included: set[int] = set()
        unchanged_text: set[int] = set()
        for text_element in root.iter(f'{{{SVG_NS}}}text'):
            current: ET.Element | None = text_element
            source_owner: ET.Element | None = None
            source_ref: str | None = None
            source_proxy = False
            while current is not None:
                if (
                    current.get(SOURCE_PROXY_ATTRIBUTE)
                    == SOURCE_PROXY_KIND
                ):
                    source_proxy = True
                if source_owner is None:
                    candidate = current.get(SOURCE_REF_ATTRIBUTE)
                    if candidate:
                        source_owner = current
                        source_ref = candidate
                current = parent_by_id.get(id(current))
            if source_proxy:
                continue
            if source_owner is not None and source_ref is not None:
                record = source_document.source_refs.get(source_ref)
                if record is not None and record.representation == 'source-proxy':
                    continue
                owner_key = id(source_owner)
                unchanged = unchanged_by_owner.get(owner_key)
                if unchanged is None:
                    baseline_owner = baseline_by_ref.get(source_ref)
                    unchanged = bool(
                        record is not None
                        and baseline_owner is not None
                        and authoring_source_ref_is_unchanged(
                            source_owner,
                            baseline_owner,
                            current_assets,
                            baseline_assets,
                            changed_definition_ids=changed_definition_ids,
                            current_root=root,
                            baseline_root=baseline_root,
                        )
                    )
                    unchanged_by_owner[owner_key] = unchanged
                if unchanged:
                    unchanged_text.add(id(text_element))
                    continue
                # The owner changed, but this text may be one of the owner's
                # source texts moved vertically or left alone while a sibling
                # was edited: same runs, same x, same anchor. The source deck
                # already proved that it fits its frame, so it calibrates the
                # estimator instead of being re-estimated against it.
                baseline_owner = baseline_by_ref.get(source_ref)
                if baseline_owner is not None:
                    pool = baseline_signatures.get(owner_key)
                    if pool is None:
                        pool = Counter(
                            SVGQualityChecker._roundtrip_text_signature(
                                baseline_text,
                                baseline_parent_by_id,
                            )
                            for baseline_text in baseline_owner.iter(
                                f'{{{SVG_NS}}}text'
                            )
                        )
                        baseline_signatures[owner_key] = pool
                    signature = SVGQualityChecker._roundtrip_text_signature(
                        text_element,
                        parent_by_id,
                    )
                    if pool[signature] > 0:
                        pool[signature] -= 1
                        unchanged_text.add(id(text_element))
                        continue
            included.add(id(text_element))
        return included, unchanged_text

    @staticmethod
    def _roundtrip_text_signature(
        text_element: ET.Element,
        parent_by_id: Dict[int, ET.Element],
    ) -> Tuple:
        """Width-relevant identity of one text: runs, style, x, anchor."""
        from .checker import (
            ET,
            _effective_presentation_value,
            copy,
        )
        clone = copy.deepcopy(text_element)
        for node in clone.iter():
            for name in ('y', 'dy', 'id'):
                node.attrib.pop(name, None)
            if node is not clone:
                node.attrib.pop('x', None)
        clone.attrib.pop('x', None)
        effective = tuple(
            (
                name,
                _effective_presentation_value(text_element, name, parent_by_id)
                if _effective_presentation_value is not None
                else text_element.get(name),
            )
            for name in (
                'font-family',
                'font-size',
                'font-weight',
                'font-style',
                'letter-spacing',
                'text-anchor',
            )
        )
        return (
            text_element.get('x'),
            effective,
            ET.tostring(clone, encoding='unicode'),
        )

    @staticmethod
    def _roundtrip_edited_text_ids(
        root: ET.Element,
        source_document,
        baseline_root: ET.Element,
        current_assets,
        baseline_assets,
    ) -> set[int]:
        """Return edited text carrier identities for compatibility callers."""
        from .checker import (
            SVGQualityChecker,
        )
        included, _unchanged = SVGQualityChecker._roundtrip_text_diff_ids(
            root,
            source_document,
            baseline_root,
            current_assets,
            baseline_assets,
        )
        return included

    @staticmethod
    def _roundtrip_text_scope_content(
        root: ET.Element,
        included_text_ids: set[int],
    ) -> str:
        """Serialize only edited text and its inherited presentation chain."""
        from .checker import (
            ET,
            _local_name,
            copy,
        )
        def clone_relevant(element: ET.Element) -> ET.Element | None:
            if (
                _local_name(element) == 'text'
                and id(element) in included_text_ids
            ):
                return copy.deepcopy(element)
            children = [
                clone
                for child in list(element)
                if (clone := clone_relevant(child)) is not None
            ]
            if element is not root and not children:
                return None
            clone = ET.Element(element.tag, dict(element.attrib))
            for child in children:
                clone.append(child)
            return clone

        scoped_root = clone_relevant(root)
        if scoped_root is None:
            return ''
        return ET.tostring(scoped_root, encoding='unicode')

    @staticmethod
    def _roundtrip_resolved_font_family(
        element: ET.Element,
        parent_by_id: Dict[int, ET.Element],
    ) -> str | None:
        """Resolve inherited SVG font-family at one text carrier."""
        from .checker import (
            _parse_inline_style,
        )
        current: ET.Element | None = element
        while current is not None:
            style_values = (
                _parse_inline_style(current.get('style'))
                if _parse_inline_style is not None
                else {}
            )
            value = style_values.get('font-family')
            if value is None:
                value = current.get('font-family')
            if isinstance(value, str) and value.strip():
                normalized = value.strip()
                if normalized.casefold() not in {'inherit', 'unset'}:
                    return normalized
            current = parent_by_id.get(id(current))
        return None

    @staticmethod
    def _roundtrip_relative_path(
        element: ET.Element,
        ancestor: ET.Element,
        parent_by_id: Dict[int, ET.Element],
    ) -> tuple[int, ...] | None:
        """Return child indices from one source-ref owner to a descendant."""
        reverse_path: List[int] = []
        current = element
        while current is not ancestor:
            parent = parent_by_id.get(id(current))
            if parent is None:
                return None
            reverse_path.append(
                next(
                    index
                    for index, child in enumerate(list(parent))
                    if child is current
                )
            )
            current = parent
        return tuple(reversed(reverse_path))

    @staticmethod
    def _roundtrip_baseline_context(
        current_owner: ET.Element,
        baseline_owner: ET.Element,
        path: tuple[int, ...],
    ) -> ET.Element:
        """Follow a matching relative path, stopping at the closest context."""
        from .checker import (
            _local_name,
        )
        current = current_owner
        baseline = baseline_owner
        for index in path:
            current_children = list(current)
            baseline_children = list(baseline)
            if index >= len(current_children) or index >= len(baseline_children):
                break
            current_child = current_children[index]
            baseline_child = baseline_children[index]
            if _local_name(current_child) != _local_name(baseline_child):
                break
            current = current_child
            baseline = baseline_child
        return baseline

