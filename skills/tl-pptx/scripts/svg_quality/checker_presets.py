"""Pinned PPT Master quality gate component.

MIT Copyright (c) 2025-2026 Hugo He.
Upstream 7e0df6705de0cd24345210d31f07fcb5a32b6900.
"""
from __future__ import annotations
from pathlib import Path
from typing import Dict, List, Tuple
from xml.etree import ElementTree as ET

class PresetsQualityMixin:
    """Upstream presets checker methods."""

    def _check_unsupported_visual_elements(
        self,
        root: ET.Element,
        result: Dict,
    ) -> None:
        """Reject authored visual elements with no native converter dispatch."""
        from .checker import (
            _UseExpansionError,
            _collect_unsupported_visuals,
            _expand_local_use_references,
            copy,
        )
        if _collect_unsupported_visuals is None:
            result['errors'].append(
                "Unable to import native visual-element preflight; "
                "cannot verify SVG element support"
            )
            return
        if _expand_local_use_references is None or _UseExpansionError is None:
            result['errors'].append(
                "Unable to import local <use> expansion; "
                "cannot verify SVG element support"
            )
            return

        expanded_root = copy.deepcopy(root)
        try:
            _expand_local_use_references(expanded_root)
        except _UseExpansionError:
            # _check_forbidden_elements already reports the actionable
            # local-reference validation error.
            return

        unsupported = _collect_unsupported_visuals(
            expanded_root,
            allow_data_icon_use=True,
        )
        if not unsupported:
            return

        preview = '; '.join(unsupported[:8])
        suffix = '' if len(unsupported) <= 8 else f'; +{len(unsupported) - 8} more'
        result['errors'].append(
            f"Unsupported visual SVG element(s) for native PPTX export: "
            f"{preview}{suffix}"
        )

    def _check_preset_geometry_metadata(
        self,
        root: ET.Element,
        result: Dict,
    ) -> None:
        """Validate round-trip preset metadata with the exporter's parser."""
        from .checker import (
            _AUTHORING_ATTR,
            _ConvertContext,
            _authored_preset_encoding,
            _compact_preset_ancestor_paint,
            _local_name,
            _resolve_preset_preview_hash,
            _svg_preset_preview_fingerprint,
            _validate_authored_preset_group,
            _validate_authored_preset_tree,
            _validate_preset_geometry_metadata,
        )
        marked = [
            elem
            for elem in root.iter()
            if (
                elem.get('data-pptx-prst') is not None
                or elem.get('data-pptx-frame') is not None
                or elem.get('data-pptx-geometry-status') is not None
                or elem.get('data-pptx-geometry-reason') is not None
                or elem.get('data-pptx-geometry-kind') is not None
                or elem.get('data-pptx-custgeom') is not None
                or elem.get('data-pptx-preview-sha256') is not None
                or elem.get('data-pptx-shape-id') is not None
                or elem.get('data-pptx-shape-scope') is not None
                or elem.get('data-pptx-shape-style') is not None
                or elem.get(_AUTHORING_ATTR) is not None
                or any(attr.startswith('data-pptx-av-') for attr in elem.attrib)
            )
        ]
        if not marked:
            return
        if _validate_preset_geometry_metadata is None:
            result['errors'].append(
                'Unable to import PPTX preset metadata validator; '
                'cannot verify native shape restoration'
            )
            return

        issues = set()
        geometry_context = _ConvertContext(defs={
            elem.get('id'): elem for elem in root.iter() if elem.get('id')
        })
        for elem in marked:
            tag = _local_name(elem)
            elem_id = elem.get('id')
            label = f'<{tag} id="{elem_id}">' if elem_id else f'<{tag}>'
            for error in _validate_preset_geometry_metadata(elem, geometry_context):
                issues.add(f'{label} has invalid PPTX shape metadata: {error}')
        if _validate_authored_preset_tree is None:
            if any(
                elem.get(_AUTHORING_ATTR) is not None
                for elem in root.iter()
            ):
                issues.add(
                    'Unable to import authored PPTX preset validator'
                )
        else:
            for error in _validate_authored_preset_tree(root):
                issues.add(f'Invalid authored PPTX preset: {error}')
        if (
            _svg_preset_preview_fingerprint is None
            or _resolve_preset_preview_hash is None
        ):
            issues.add('Unable to import PPTX preset preview fingerprint validator')
        else:
            for elem in root.iter():
                if (
                    _local_name(elem) != 'g'
                    or elem.get('data-pptx-object') not in {'shape', 'connector'}
                    or elem.get('data-pptx-prst') is None
                ):
                    continue
                try:
                    expected = _resolve_preset_preview_hash(elem)
                except ValueError as exc:
                    elem_id = elem.get('id') or '(no id)'
                    issues.add(
                        f'<g id="{elem_id}"> has an invalid PPTX preset '
                        f'preview contract: {exc}'
                    )
                    continue
                if expected is None:
                    continue
                actual = _svg_preset_preview_fingerprint(elem)
                if actual != expected:
                    elem_id = elem.get('id') or '(no id)'
                    issues.add(
                        f'<g id="{elem_id}"> has a stale PPTX preset preview; '
                        'update the native carrier or restore the generated detail paths'
                    )
        result['errors'].extend(sorted(issues))
        if (
            _authored_preset_encoding is not None
            and _validate_authored_preset_group is not None
        ):
            expanded = [
                elem.get('id') or '(no id)'
                for elem in root.iter()
                if _authored_preset_encoding(elem) == 'expanded'
                and not _validate_authored_preset_group(elem)
            ]
            if expanded:
                examples = ', '.join(expanded[:3])
                suffix = '' if len(expanded) <= 3 else f', +{len(expanded) - 3} more'
                result['warnings'].append(
                    'Compatible expanded authored-preset fragment(s) detected '
                    f'({len(expanded)}: {examples}{suffix}). New project-authored '
                    'pages and templates use the compact helper form; the '
                    'expanded carrier/preview form remains readable for compatibility. '
                    'No change is required while it remains ordinary Slide-local input.'
                )
        inherited_paint = _compact_preset_ancestor_paint(root)
        if inherited_paint:
            examples = ', '.join(
                f'{element_id} ({"/".join(properties)})'
                for element_id, properties in inherited_paint[:3]
            )
            suffix = (
                ''
                if len(inherited_paint) <= 3
                else f', +{len(inherited_paint) - 3} more'
            )
            result['warnings'].append(
                'Compact authored preset(s) use compatible ancestor paint or '
                f'opacity ({examples}{suffix}). Canonical page/template authoring '
                'keeps preset paint local and reruns the helper with channel alpha; '
                'export remains supported.'
            )

    def _check_preset_geometry_transforms(
        self,
        root: ET.Element,
        result: Dict,
    ) -> None:
        """Reject preset transforms that DrawingML cannot represent exactly."""
        from .checker import (
            ET,
            _CONNECTOR_PRESET_TYPES,
            _IDENTITY_MATRIX,
            _local_name,
            _matrix_multiply,
            _parse_transform_matrix,
            _rect_to_dml_xfrm,
            _validate_dml_shape_matrix,
            re,
        )
        helpers = (
            _IDENTITY_MATRIX,
            _matrix_multiply,
            _parse_transform_matrix,
            _rect_to_dml_xfrm,
            _validate_dml_shape_matrix,
        )
        if any(helper is None for helper in helpers):
            return

        relevant: set[ET.Element] = set()

        def mark_relevant(element: ET.Element) -> bool:
            found = element.get('data-pptx-prst') is not None
            for child in element:
                found = mark_relevant(child) or found
            if found:
                relevant.add(element)
            return found

        mark_relevant(root)
        issues = set()

        def visit(element: ET.Element, parent_matrix) -> None:
            if element not in relevant:
                return
            matrix = parent_matrix
            transform = element.get('transform')
            if transform:
                try:
                    local_matrix = _parse_transform_matrix(transform)
                    matrix = _matrix_multiply(parent_matrix, local_matrix)
                except ValueError as exc:
                    issues.add(
                        f'<{_local_name(element)}> has invalid preset '
                        f'transform: {exc}'
                    )
                    return
            if element.get('data-pptx-prst') is not None:
                try:
                    raw_frame = element.get('data-pptx-frame')
                    if raw_frame:
                        frame = tuple(
                            float(part)
                            for part in re.split(r'[\s,]+', raw_frame.strip())
                        )
                        if len(frame) != 4:
                            raise ValueError(
                                'data-pptx-frame must contain four numbers'
                            )
                        preset = element.get('data-pptx-prst') or ''
                        _rect_to_dml_xfrm(
                            frame[0],
                            frame[1],
                            frame[2],
                            frame[3],
                            matrix,
                            preserve_degenerate_axes=(
                                element.get('data-pptx-object') == 'connector'
                                or preset in _CONNECTOR_PRESET_TYPES
                            ),
                        )
                    else:
                        _validate_dml_shape_matrix(matrix)
                except ValueError as exc:
                    elem_id = element.get('id') or '(no id)'
                    issues.add(
                        f'<{_local_name(element)} id="{elem_id}"> has '
                        f'unsupported preset transform: {exc}'
                    )
            for child in element:
                visit(child, matrix)

        visit(root, _IDENTITY_MATRIX)
        result['errors'].extend(sorted(issues))

    @staticmethod
    def _is_full_canvas_root_rect(
        root: ET.Element,
        element: ET.Element,
    ) -> bool:
        """Return whether one direct rect is the ordinary full-page backdrop."""
        from .checker import (
            _effective_presentation_value,
            _local_name,
            _parse_project_geometry_length,
            _parse_project_opacity,
            _parse_viewbox_values,
        )
        if (
            _local_name(element) != 'rect'
            or _parse_project_geometry_length is None
            or any(
                element.get(attribute)
                for attribute in ('transform', 'filter', 'clip-path')
            )
        ):
            return False
        viewbox = _parse_viewbox_values(root.get('viewBox') or '')
        if viewbox is None:
            return False

        parent_by_id = {id(element): root}

        def inherited(name: str, default: str) -> str:
            return _effective_presentation_value(
                element,
                name,
                parent_by_id,
            ) or default

        try:
            values = {
                name: _parse_project_geometry_length(
                    element.get(name) or '0',
                    name,
                )
                for name in ('x', 'y', 'width', 'height', 'rx', 'ry')
            }
            stroke_width = _parse_project_geometry_length(
                inherited('stroke-width', '1'),
                'stroke-width',
            )
            stroke_opacity = (
                _parse_project_opacity(inherited('stroke-opacity', '1'))
                if _parse_project_opacity is not None else 1.0
            )
        except ValueError:
            return False
        fill = inherited('fill', '#000000').strip().lower()
        stroke = inherited('stroke', 'none').strip().lower()
        if (
            fill == 'none'
            or (
                stroke != 'none'
                and stroke_width > 0
                and stroke_opacity > 0
            )
        ):
            return False

        view_x, view_y, view_width, view_height = viewbox
        tolerance = 0.5
        return (
            values['rx'] == 0
            and values['ry'] == 0
            and abs(values['x'] - view_x) <= tolerance
            and abs(values['y'] - view_y) <= tolerance
            and abs(values['width'] - view_width) <= tolerance
            and abs(values['height'] - view_height) <= tolerance
        )

    def _check_animation_group_ids(
        self,
        root: ET.Element,
        svg_path: Path,
        result: Dict,
    ):
        """Validate top-level animation anchors without policing inner groups."""
        from .checker import (
            _effective_top_level,
            _is_static_page_frame,
            _local_name,
            _usable_animation_group_id,
            defaultdict,
        )
        non_visual = {'defs', 'title', 'desc', 'metadata', 'style'}
        group_indexes: Dict[str, List[int]] = defaultdict(list)
        ungrouped: List[str] = []
        ungrouped_signatures: List[Tuple[object, ...]] = []
        visual_index = 0

        for child in root:
            if _effective_top_level is not None:
                child = _effective_top_level(child)
            tag = _local_name(child)
            if tag in non_visual:
                continue
            visual_index += 1
            is_first_visual = visual_index == 1

            if tag == 'g':
                group_id = _usable_animation_group_id(child.get('id'))
                if group_id is None:
                    result['warnings'].append(
                        f"Top-level visible <g> #{visual_index} has no id; "
                        "object-level animation config cannot reference it"
                    )
                    continue
                group_indexes[group_id].append(visual_index)
                continue

            if svg_path.parent.name != 'svg_output':
                continue
            if child.get('data-pptx-layer') is not None:
                continue
            if (
                _is_static_page_frame is not None
                and _is_static_page_frame(
                    child.get('data-pptx-role'),
                    child.get('data-pptx-placeholder'),
                )
            ):
                continue
            if is_first_visual and self._is_full_canvas_root_rect(root, child):
                continue
            child_id = (child.get('id') or '').strip()
            ungrouped.append(
                f'<{tag} id="{child_id}">'
                if child_id else f'<{tag}> #{visual_index}'
            )
            ungrouped_signatures.append(
                self._prototype_element_signature(child)
            )

        for group_id, indexes in sorted(group_indexes.items()):
            if len(indexes) > 1:
                positions = ', '.join(str(item) for item in indexes)
                result['errors'].append(
                    f'Duplicate top-level group id {group_id!r} at visible '
                    f'positions {positions}; animation target ids must be unique'
                )

        if ungrouped:
            samples = ', '.join(ungrouped[:3])
            if len(ungrouped) > 3:
                samples += ', ...'
            message = (
                f'{len(ungrouped)} ungrouped top-level Slide-local element(s) '
                f'in svg_output ({samples}); group only logical content units '
                'in a top-level <g id="...">. Keep genuine static page framing '
                'as a root primitive and declare a supported data-pptx-role such '
                'as "background" or "decoration"'
            )
            prototype_root = self._active_prototype_root()
            prototype_ungrouped = (
                self._ungrouped_slide_local_facts(prototype_root)
                if prototype_root is not None
                else ([], [])
            )
            if (
                prototype_root is not None
                and ungrouped == prototype_ungrouped[0]
                and ungrouped_signatures == prototype_ungrouped[1]
            ):
                self._append_inherited_info(
                    result,
                    'animation_anchor',
                    message,
                )
            else:
                result['warnings'].append(message)

    @staticmethod
    def _prototype_element_signature(
        element: ET.Element,
    ) -> Tuple[object, ...]:
        """Compare warning-owned topology/style while ignoring visible text."""
        from .checker import (
            SVGQualityChecker,
            _local_name,
        )
        return (
            _local_name(element),
            tuple(sorted(element.attrib.items())),
            tuple(
                SVGQualityChecker._prototype_element_signature(child)
                for child in element
            ),
        )

    def _ungrouped_slide_local_facts(
        self,
        root: ET.Element,
    ) -> Tuple[List[str], List[Tuple[object, ...]]]:
        """Describe and fingerprint top-level non-group Slide-local atoms."""
        from .checker import (
            _effective_top_level,
            _is_static_page_frame,
            _local_name,
        )
        non_visual = {'defs', 'title', 'desc', 'metadata', 'style'}
        descriptors: List[str] = []
        signatures: List[Tuple[object, ...]] = []
        visual_index = 0
        for child in root:
            if _effective_top_level is not None:
                child = _effective_top_level(child)
            tag = _local_name(child)
            if tag in non_visual:
                continue
            visual_index += 1
            if tag == 'g' or child.get('data-pptx-layer') is not None:
                continue
            if (
                _is_static_page_frame is not None
                and _is_static_page_frame(
                    child.get('data-pptx-role'),
                    child.get('data-pptx-placeholder'),
                )
            ):
                continue
            if visual_index == 1 and self._is_full_canvas_root_rect(root, child):
                continue
            child_id = (child.get('id') or '').strip()
            descriptors.append(
                f'<{tag} id="{child_id}">'
                if child_id else f'<{tag}> #{visual_index}'
            )
            signatures.append(self._prototype_element_signature(child))
        return descriptors, signatures

    # OOXML ST_PresetPatternVal enum — anything outside this set produces a
    # PPTX schema violation ("PowerPoint found a problem with the content").
