"""Pinned PPT Master quality gate component.

MIT Copyright (c) 2025-2026 Hugo He.
Upstream 7e0df6705de0cd24345210d31f07fcb5a32b6900.
"""
from __future__ import annotations
from pathlib import Path
from typing import Dict, List, Tuple
from xml.etree import ElementTree as ET

class NativeStructureQualityMixin:
    """Upstream native structure checker methods."""

    def _check_pattern_fills(self, root: ET.Element, result: Dict):
        """Audit <pattern> defs that drive PPTX <a:pattFill> output.

        svg_to_pptx maps <pattern fill> to native <a:pattFill prst="...">. The
        preset name comes from `data-pptx-pattern` (e.g. `lgGrid` / `smGrid` /
        `dkUpDiag`). Patterns marked with `data-pptx-text-image-fill` instead
        map to run-level <a:blipFill> and are validated by converter preflight.
        Two preset-pattern failure modes are worth catching pre-export:

        1. Missing annotation → the converter compatibility fallback chooses
           `ltUpDiag` (diagonal stripes), which is not an authoring contract.
        2. Invalid preset name → PPTX schema rejects the file; PowerPoint
           opens it with "needs to be repaired". OOXML
           `ST_PresetPatternVal` is a closed enum — only the names in
           `_OOXML_PATTERN_PRESETS` are legal. Inventing `ltGrid` (no such
           value) is the canonical mistake; the only grids are `smGrid` /
           `lgGrid` / `dotGrid`.
        """
        from .checker import (
            _TEXT_IMAGE_FILL_ATTR,
            _direct_defs_index,
            _local_name,
            _parse_inline_style,
            _parse_pattern_colors,
            re,
        )
        definitions, _duplicates = _direct_defs_index(root)
        referenced_patterns: set[str] = set()
        for elem in root.iter():
            style_values = (
                _parse_inline_style(elem.get('style'))
                if _parse_inline_style is not None else {}
            )
            fill = style_values.get('fill') or elem.get('fill')
            match = re.fullmatch(r'url\(#([^)]+)\)', (fill or '').strip())
            if match is None:
                continue
            definition = definitions.get(match.group(1))
            if definition is not None and _local_name(definition) == 'pattern':
                referenced_patterns.add(match.group(1))

        for pattern in (
            elem for elem in root.iter()
            if _local_name(elem) == 'pattern'
        ):
            pat_id = pattern.get('id', '<unnamed>')
            prst = pattern.get('data-pptx-pattern')
            if pattern.get(_TEXT_IMAGE_FILL_ATTR) is not None:
                continue
            if pat_id in referenced_patterns and not prst:
                result['warnings'].append(
                    f"Fidelity warning: <pattern id=\"{pat_id}\"> has no "
                    "data-pptx-pattern attribute, so the converter will use its "
                    "compatible `ltUpDiag` fallback. Generated SVG should declare a valid "
                    "data-pptx-pattern to make the intended preset explicit; "
                    "set data-pptx-fg/data-pptx-bg or matching child paints "
                    "when explicit pattern colors are required. No change is "
                    "required for export."
                )
            if pat_id in referenced_patterns and pattern.get('patternTransform'):
                result['errors'].append(
                    f"<pattern id=\"{pat_id}\"> cannot use patternTransform; "
                    "the native preset mapping does not preserve custom tile transforms"
                )
            if prst not in self._OOXML_PATTERN_PRESETS:
                if not prst:
                    continue
                result['errors'].append(
                    f"<pattern id=\"{pat_id}\"> uses data-pptx-pattern=\"{prst}\" "
                    "which is not in OOXML ST_PresetPatternVal — exported PPTX "
                    "will fail schema validation ('needs to be repaired'). "
                    "Use one of: smGrid / lgGrid / dotGrid (grids), "
                    "ltUpDiag / dkUpDiag / cross / diagCross / weave / plaid / "
                    "horzBrick (others); see references/native-data-interface.md §1 "
                    "for the full authoring enum."
                )
            elif prst and _parse_pattern_colors is not None:
                try:
                    _parse_pattern_colors(pattern)
                except ValueError as exc:
                    result['errors'].append(str(exc))

    def _check_native_object_markers(self, root: ET.Element, result: Dict) -> None:
        """Validate explicit native replacement markers before PPTX export."""
        from .checker import (
            ET,
            _INLINE_FORMULA_ATTR,
            _inline_formula_marker_errors,
            _native_fallback_kind,
            _native_marker_legacy_warnings,
            _native_marker_release_block_reason,
            _native_marker_status_errors,
            _native_object_marker_warnings,
            _native_replacement_kind,
            _native_replacement_status,
            _require_fresh_native_fallback,
            _validate_native_object_marker,
            _validate_native_object_marker_with_warnings,
        )
        inline_formula_markers = [
            elem for elem in root.iter()
            if elem.get(_INLINE_FORMULA_ATTR) is not None
        ]
        if inline_formula_markers and _inline_formula_marker_errors is None:
            result['errors'].append(
                "Unable to import inline-formula validator; cannot verify "
                f"{_INLINE_FORMULA_ATTR} markers"
            )
        elif _inline_formula_marker_errors is not None:
            for error in _inline_formula_marker_errors(root):
                result['errors'].append(f"Invalid inline formula marker: {error}")

        invalid_status_elements: set[ET.Element] = set()
        for elem in root.iter():
            marker_id = elem.get('id') or elem.get('data-name') or '<unnamed>'
            if elem.tag.rsplit('}', 1)[-1] == 'metadata':
                continue
            has_status = any(
                elem.get(name) is not None
                for name in (
                    'data-pptx-replace-with',
                    'data-pptx-native',
                    'data-pptx-fallback-kind',
                    'data-pptx-visual-status',
                    'data-pptx-route-status',
                    'data-pptx-replacement-status',
                    'data-pptx-native-status',
                    'data-pptx-native-authority',
                    'data-pptx-import-source',
                    'data-pptx-native-source',
                )
            )
            if not has_status:
                continue
            if (
                _native_marker_status_errors is None
                or _native_marker_release_block_reason is None
            ):
                result['errors'].append(
                    "Unable to import native-object status validator; "
                    f"cannot verify PPTX graphic {marker_id}"
                )
                continue
            status_errors = _native_marker_status_errors(elem)
            for error in status_errors:
                result['errors'].append(
                    f"PPTX graphic {marker_id} has invalid status metadata: {error}"
                )
            if status_errors:
                invalid_status_elements.add(elem)
                continue
            if _native_marker_legacy_warnings is not None:
                for warning in _native_marker_legacy_warnings(elem):
                    result['warnings'].append(
                        f"PPTX replacement marker {marker_id}: {warning}"
                    )
            try:
                fallback_kind = (
                    _native_fallback_kind(elem)
                    if _native_fallback_kind is not None else None
                )
                replacement_kind = (
                    _native_replacement_kind(elem)
                    if _native_replacement_kind is not None else ''
                )
            except ValueError:
                # The shared status validator reported the alias conflict.
                continue
            if fallback_kind == 'placeholder':
                route = (
                    "the native Chart/Table route may reconstruct its active marker"
                    if replacement_kind
                    else "default export keeps the visible placeholder"
                )
                result['warnings'].append(
                    f"PPTX graphic {marker_id} is a reconstruction-only placeholder; "
                    f"it has no baked preview and {route}"
                )

        for elem in root.iter():
            if elem.tag.rsplit('}', 1)[-1] == 'metadata':
                continue
            if _native_replacement_status is None or _native_replacement_kind is None:
                continue
            try:
                status = _native_replacement_status(elem)
                replacement_kind = _native_replacement_kind(elem)
            except ValueError:
                continue
            if not status or replacement_kind:
                continue
            marker_id = elem.get('id') or elem.get('data-name') or '<unnamed>'
            result['warnings'].append(
                f"Native PPTX object {marker_id} is fallback-only: {status}"
            )

        markers = [
            elem for elem in root.iter()
            if (
                _native_replacement_kind is not None
                and elem.tag.rsplit('}', 1)[-1] != 'metadata'
                and elem not in invalid_status_elements
                and _native_replacement_kind(elem)
            )
        ]
        if not markers:
            return
        if _validate_native_object_marker is None:
            result['warnings'].append(
                "Detected data-pptx-replace-with markers, but replacement validator "
                "could not be imported; export-time validation will still run."
            )
            return

        parent_map = {
            child: parent
            for parent in root.iter()
            for child in parent
        }

        def append_metadata_legacy_warnings(marker: ET.Element) -> None:
            if _native_marker_legacy_warnings is None:
                return
            marker_id = marker.get('id') or '<unnamed>'
            for child in marker:
                if child.tag.rsplit('}', 1)[-1] != 'metadata':
                    continue
                for warning in _native_marker_legacy_warnings(child):
                    result['warnings'].append(
                        f"PPTX replacement marker {marker_id}: {warning}"
                    )

        for marker in markers:
            marker_id = marker.get('id') or '<unnamed>'
            replacement_kind = _native_replacement_kind(marker)
            if (
                self.canonical_authoring
                and replacement_kind in {'chart', 'table'}
            ):
                if _require_fresh_native_fallback is None:
                    result['errors'].append(
                        "Unable to import native fallback freshness validator; "
                        f"cannot verify canonical marker {marker_id}"
                    )
                else:
                    try:
                        _require_fresh_native_fallback(
                            marker,
                            document_root=root,
                        )
                    except RuntimeError as exc:
                        result['errors'].append(
                            f"Canonical SVG-first native marker {marker_id}: {exc}"
                        )
            ancestors = []
            parent = parent_map.get(marker)
            while parent is not None and parent is not root:
                if parent.tag.rsplit('}', 1)[-1] == 'g':
                    ancestors.append(parent)
                parent = parent_map.get(parent)
            ancestors_tuple = tuple(reversed(ancestors))
            if _validate_native_object_marker_with_warnings is not None:
                try:
                    warnings = _validate_native_object_marker_with_warnings(
                        marker,
                        ancestors=ancestors_tuple,
                        document_root=root,
                    )
                except RuntimeError as exc:
                    result['errors'].append(
                        f"Invalid data-pptx-replace-with marker {marker_id}: {exc}"
                    )
                    continue
                for warning in warnings:
                    result['warnings'].append(
                        f"data-pptx-replace-with marker {marker_id}: {warning}"
                    )
                append_metadata_legacy_warnings(marker)
                continue

            try:
                _validate_native_object_marker(marker, ancestors=ancestors_tuple)
            except RuntimeError as exc:
                result['errors'].append(
                    f"Invalid data-pptx-replace-with marker {marker_id}: {exc}"
                )
                continue
            append_metadata_legacy_warnings(marker)
            if _native_object_marker_warnings is None:
                continue
            for warning in _native_object_marker_warnings(
                marker,
                ancestors=ancestors_tuple,
                document_root=root,
            ):
                result['warnings'].append(
                    f"data-pptx-replace-with marker {marker_id}: {warning}"
                )

    def _check_pptx_structure_metadata(
        self,
        root: ET.Element,
        svg_path: Path,
        result: Dict,
    ) -> None:
        """Validate the intrinsic structured Master/Layout SVG contract."""
        from .checker import (
            _PPTX_STRUCTURE_ATTRS,
            _declared_pptx_structure_mode,
            _local_pptx_structure_errors,
            _validate_template_structure_svg,
        )
        has_structure_metadata = any(
            elem.get(attr) is not None
            for elem in root.iter()
            for attr in _PPTX_STRUCTURE_ATTRS
        )
        if self.quick_generate and not has_structure_metadata:
            return
        if (
            not self.quick_generate
            and not self.template_mode
            and svg_path.parent.name == 'svg_output'
        ):
            declared_mode = _declared_pptx_structure_mode(
                self._resolve_project_path(svg_path)
            )
            if declared_mode == 'flat':
                forbidden_attrs = sorted({
                    attr
                    for elem in root.iter()
                    for attr in _PPTX_STRUCTURE_ATTRS
                    if elem.get(attr) is not None
                })
                if forbidden_attrs:
                    result['errors'].append(
                        f"{svg_path.name}: pptx_structure.mode: flat forbids "
                        "Master/Layout/layer/placeholder metadata; remove "
                        + ', '.join(forbidden_attrs)
                    )
                return
            if declared_mode != 'structured':
                # The project-level gate emits one actionable migration error.
                # Avoid burying it under repeated per-page structure failures.
                return
        require_structure = bool(
            self.template_mode
            or svg_path.parent.name == 'svg_output'
        )
        if not has_structure_metadata and not require_structure:
            return
        result['errors'].extend(_local_pptx_structure_errors(
            root,
            svg_path,
            require_structure=require_structure,
        ))
        self._check_placeholder_carrier_flattening(root, svg_path, result)
        if svg_path.parent.name == 'svg_output':
            self._append_structure_coverage_warnings(root, result)
        if _validate_template_structure_svg is None:
            result['errors'].append(
                "Structured PPTX metadata validator could not be imported; "
                "the quality gate cannot verify this SVG"
            )
            return
        result['errors'].extend(_validate_template_structure_svg(svg_path))
        result['errors'] = list(dict.fromkeys(result['errors']))

    @staticmethod
    def _check_placeholder_carrier_flattening(
        root: ET.Element,
        svg_path: Path,
        result: Dict,
    ) -> None:
        """Reject slot carriers that export as multiple native children.

        Default export flattens non-mergeable positional ``<tspan>`` lines
        before converting the surrounding slot group to DrawingML. Reuse that
        exact transform here so the quality gate fails before the later
        placeholder-unwrapping step does.
        """
        from .checker import (
            ET,
            SVG_NS,
            _NON_VISUAL_SVG_TAGS,
            _flatten_positional_tspans,
            _local_name,
            copy,
        )
        if _flatten_positional_tspans is None:
            return

        candidate_ids: List[str] = []
        for slot in root.iter(f'{{{SVG_NS}}}g'):
            if not (slot.get('data-pptx-placeholder') or '').strip():
                continue
            binding = (
                slot.get('data-pptx-binding') or 'carrier'
            ).strip().lower()
            if binding != 'carrier':
                continue
            visual_children = [
                child for child in list(slot)
                if _local_name(child) not in _NON_VISUAL_SVG_TAGS
            ]
            carriers = [
                child for child in visual_children
                if (child.get('data-pptx-carrier') or '')
                .strip()
                .lower()
                == 'true'
            ]
            slot_id = (slot.get('id') or '').strip()
            if not slot_id or len(visual_children) != 1 or len(carriers) != 1:
                continue
            if not any(
                _local_name(descendant) == 'tspan'
                and any(
                    descendant.get(name) is not None
                    for name in ('x', 'y', 'dy')
                )
                for descendant in carriers[0].iter()
            ):
                continue
            candidate_ids.append(slot_id)

        if not candidate_ids:
            return

        flattened_root = copy.deepcopy(root)
        try:
            _flatten_positional_tspans(
                ET.ElementTree(flattened_root),
                merge_paragraphs=True,
                preserve_line_breaks=True,
            )
        except ValueError:
            # The shared text check reports the unsupported nested-position
            # contract; avoid turning a quality result into a checker crash.
            return
        slots_by_id = {
            (slot.get('id') or '').strip(): slot
            for slot in flattened_root.iter(f'{{{SVG_NS}}}g')
            if (slot.get('id') or '').strip()
        }
        for slot_id in candidate_ids:
            slot = slots_by_id.get(slot_id)
            if slot is None:
                continue
            native_children = [
                child for child in list(slot)
                if _local_name(child) not in _NON_VISUAL_SVG_TAGS
            ]
            if len(native_children) == 1:
                continue
            result['errors'].append(
                f"{svg_path.name}: placeholder slot {slot_id} becomes "
                f"{len(native_children)} native children after positional "
                "<tspan> flattening; a carrier-bound slot must export as one "
                "text or picture carrier. Use one single-frame dy-stacked text "
                "frame, or move independently positioned lines outside the slot"
            )

    def _append_structure_coverage_warnings(
        self,
        root: ET.Element,
        result: Dict,
    ) -> None:
        """Warn on mapped pages that compile to bare Masters / empty Layouts.

        Zero-slot and framing-only Layouts are legal contracts, so these stay
        advisory warnings. They neither fail the workflow gate nor require a
        per-warning disposition.
        """
        messages = self._structure_coverage_messages(root)
        if not messages:
            return
        prototype_root = self._active_prototype_root()
        if (
            prototype_root is not None
            and messages == self._structure_coverage_messages(prototype_root)
        ):
            for message in messages:
                self._append_inherited_info(
                    result,
                    'structure_coverage',
                    message,
                )
            return
        result['warnings'].extend(messages)

    @staticmethod
    def _structure_coverage_messages(root: ET.Element) -> List[str]:
        """Return advisory coverage messages for one structured page."""
        if not (root.get('data-pptx-layout') or '').strip():
            return []
        messages: List[str] = []
        has_layer_mark = any(
            elem.get('data-pptx-layer') is not None
            for elem in root.iter()
        )
        has_layout_atom = any(
            child.get('data-pptx-layer') == 'layout'
            for child in list(root)
        )
        has_placeholder = any(
            elem.get('data-pptx-placeholder') is not None
            for elem in root.iter()
        )
        if not has_layer_mark:
            messages.append(
                'Mapped page declares data-pptx-layout but no data-pptx-layer '
                'mark; the exported Master gets no shared background/chrome '
                'and the Layout gets no static framing. Generated templates '
                'should mark the deck-wide '
                'background data-pptx-layer="master" and this layout key\'s '
                'framing data-pptx-layer="layout". No change or disposition '
                'is required.'
            )
        if not has_placeholder and not has_layout_atom:
            messages.append(
                'Mapped page has no placeholder slot and no '
                'data-pptx-layer="layout" atom; its Layout exports empty. '
                'Generated templates should declare the slots the page actually '
                'has (title / subtitle / '
                'body / picture / slide-number / footer) and mark the layout '
                'key\'s static framing unless this is intentionally a fixed '
                'zero-slot composition. No change or disposition is required.'
            )
        elif not has_placeholder:
            messages.append(
                'Mapped Layout has static framing but no insertable '
                'placeholder slot. Generated templates should declare the '
                'slots the page actually has (title / subtitle / body / '
                'picture / slide-number / footer) unless zero-slot is the '
                'intended reusable contract. No change or disposition is required.'
            )
        return messages

    @staticmethod
    def _check_legacy_pptx_attributes(
        root: ET.Element,
        svg_path: Path,
        result: Dict,
    ) -> None:
        """Reject superseded long-form authoring attributes."""
        from .checker import (
            _LEGACY_PPTX_ATTRIBUTE_RENAMES,
            _element_label,
        )
        for element in root.iter():
            for legacy, canonical in _LEGACY_PPTX_ATTRIBUTE_RENAMES.items():
                if element.get(legacy) is None:
                    continue
                result['errors'].append(
                    f'{svg_path.name}: {_element_label(element)} uses legacy '
                    f'{legacy}; rename it to {canonical}'
                )

    def _check_semantic_markers(
        self,
        root: ET.Element,
        svg_path: Path,
        result: Dict,
    ) -> None:
        """Validate minimal compiler hints without changing SVG rendering."""
        from .checker import (
            _SEMANTIC_ATTRS,
            _validate_semantic_markers,
        )
        has_semantics = any(
            elem.get(attr) is not None
            for elem in root.iter()
            for attr in _SEMANTIC_ATTRS
        )
        require_page_role = (
            svg_path.parent.name in {'svg_output', 'svg_final'}
            and root.get('data-pptx-layout') is None
        )
        if _validate_semantic_markers is None:
            if has_semantics:
                result['warnings'].append(
                    "Detected Semantic SVG markers, but their validator could "
                    "not be imported."
                )
            return
        for issue in _validate_semantic_markers(
            root,
            require_page_role=require_page_role,
        ):
            if issue.severity == 'error':
                result['errors'].append(issue.message)
            else:
                result['warnings'].append(issue.message)

