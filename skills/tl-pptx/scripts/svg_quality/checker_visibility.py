"""Pinned PPT Master quality gate component.

MIT Copyright (c) 2025-2026 Hugo He.
Upstream 7e0df6705de0cd24345210d31f07fcb5a32b6900.
"""
from __future__ import annotations
from pathlib import Path
from typing import Dict, List, Tuple
from xml.etree import ElementTree as ET

class VisibilityQualityMixin:
    """Upstream visibility checker methods."""

    @staticmethod
    def _is_hidden_element(
        element: ET.Element,
        parent_by_id: Dict[int, ET.Element],
    ) -> bool:
        """Resolve ordinary suppression and empty clips before measuring visuals."""
        from .checker import (
            _empty_clip_path_reason,
            _project_definition_index,
            _svg_hidden_reason,
        )
        if _svg_hidden_reason is not None and _svg_hidden_reason(element, parent_by_id) is not None:
            return True
        if _empty_clip_path_reason is None or _project_definition_index is None:
            return False
        clipped = []
        root = element
        current = element
        while current is not None:
            if current.get('clip-path') is not None:
                clipped.append(current)
            root = current
            current = parent_by_id.get(id(current))
        if not clipped:
            return False
        definitions, _duplicates = _project_definition_index(root)
        return any(_empty_clip_path_reason(item, definitions, parent_by_id) for item in clipped)

    def _check_hidden_elements(self, root: ET.Element, result: Dict) -> None:
        """Advise which hidden SVG objects will be omitted during native export."""
        from .checker import (
            _collect_hidden_visuals,
            _element_label,
        )
        if _collect_hidden_visuals is None:
            return
        for element, reason in _collect_hidden_visuals(root):
            result['warnings'].append(
                f'Hidden element {_element_label(element)} will not be exported '
                f'({reason}, including inherited state); advisory only'
            )

    def _check_shape_coordinate_ranges(self, root: ET.Element, result: Dict) -> None:
        """Check ordinary shape frames with the exporter's OOXML range validator."""
        from .checker import (
            ET,
            _element_label,
            _local_name,
            _parse_inline_style,
            _parse_project_geometry_length,
            _rect_to_dml_xfrm,
        )
        if _rect_to_dml_xfrm is None or _parse_project_geometry_length is None:
            return
        parents = {id(child): parent for parent in root.iter() for child in parent}

        def visit(element: ET.Element) -> None:
            tag = _local_name(element)
            if tag in {'defs', 'metadata', 'title', 'desc', 'style'}:
                return
            if (
                tag in {'rect', 'image', 'circle', 'ellipse'}
                and element.get('data-pptx-frame') is None
                and not self._is_hidden_element(element, parents)
            ):
                styles = _parse_inline_style(element.get('style')) if _parse_inline_style else {}

                def length(name: str) -> float:
                    return _parse_project_geometry_length(styles.get(name, element.get(name, '0')), name)

                try:
                    if tag in {'rect', 'image'}:
                        frame = (length('x'), length('y'), length('width'), length('height'))
                    else:
                        rx = length('r' if tag == 'circle' else 'rx')
                        ry = rx if tag == 'circle' else length('ry')
                        frame = (length('cx') - rx, length('cy') - ry, rx * 2, ry * 2)
                    matrix = self._accumulated_transform_matrix(element, parents)
                    if matrix is not None and frame[2] > 0 and frame[3] > 0:
                        _rect_to_dml_xfrm(*frame, matrix)
                except ValueError as exc:
                    # Other geometry/transform grammar errors have their own checks.
                    if 'OOXML' in str(exc):
                        result['errors'].append(
                            f'{_element_label(element)}: {exc}; reduce the shape '
                            'coordinates or dimensions before export'
                        )
            for child in element:
                visit(child)

        visit(root)

    @staticmethod
    def _has_zero_opacity(
        element: ET.Element,
        parent_by_id: Dict[int, ET.Element],
    ) -> bool:
        """Return whether an element or ancestor has zero effective opacity."""
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
            raw = style_values.get('opacity')
            if raw is None:
                raw = current.get('opacity')
            if raw is not None:
                value = raw.strip()
                try:
                    opacity = (
                        float(value[:-1]) / 100
                        if value.endswith('%')
                        else float(value)
                    )
                except ValueError:
                    pass
                else:
                    if opacity <= 0:
                        return True
            current = parent_by_id.get(id(current))
        return False

    @classmethod
    def _visible_image_elements(
        cls,
        root: ET.Element,
    ) -> Tuple[ET.Element, Dict[int, ET.Element], List[ET.Element]]:
        """Return rendered image instances after expanding static local uses."""
        from .checker import (
            SVG_NS,
            _UseExpansionError,
            _expand_local_use_references,
            copy,
        )
        working_root = copy.deepcopy(root)
        if (
            _expand_local_use_references is not None
            and _UseExpansionError is not None
        ):
            try:
                _expand_local_use_references(working_root)
            except _UseExpansionError:
                # The local-reference validator owns the actionable failure.
                working_root = copy.deepcopy(root)

        parent_by_id = {
            id(child): parent
            for parent in working_root.iter()
            for child in list(parent)
        }
        images = [
            element
            for element in working_root.iter(f'{{{SVG_NS}}}image')
            if not cls._is_hidden_element(element, parent_by_id)
            and not cls._has_non_visual_ancestor(
                element,
                working_root,
                parent_by_id,
            )
            and not cls._has_zero_opacity(element, parent_by_id)
        ]
        return working_root, parent_by_id, images

    @staticmethod
    def _has_non_visual_ancestor(
        element: ET.Element,
        module: ET.Element,
        parent_by_id: Dict[int, ET.Element],
    ) -> bool:
        """Return whether an element lives in a non-rendered module subtree."""
        from .checker import (
            _NON_VISUAL_SVG_TAGS,
            _local_name,
        )
        current: ET.Element | None = element
        while current is not None and current is not module:
            if _local_name(current) in _NON_VISUAL_SVG_TAGS:
                return True
            current = parent_by_id.get(id(current))
        return False

    def _check_module_bounds_contract(
        self,
        root: ET.Element,
        result: Dict,
    ) -> None:
        """Validate ordinary direct-root module boundaries in the SVG canvas."""
        from .checker import (
            _BOUNDS_ATTR,
            _BOUNDS_OVERFLOW_TOLERANCE,
            _MORPH_STAGING_ATTR,
            _PPTX_ROOT_STRUCTURE_ATTRS,
            _authored_preset_encoding,
            _element_label,
            _local_name,
            _parse_positive_bounds,
            _parse_viewbox_values,
        )
        parent_by_id = {
            id(child): parent
            for parent in root.iter()
            for child in list(parent)
        }
        viewbox = _parse_viewbox_values(root.get('viewBox') or '')
        canvas = None
        if viewbox is not None:
            x, y, width, height = viewbox
            canvas = (x, y, x + width, y + height)

        for element in root.iter():
            if element.get(_BOUNDS_ATTR) is None:
                continue
            if _local_name(element) != 'g':
                result['errors'].append(
                    f'{_element_label(element)} {_BOUNDS_ATTR} is valid '
                    'only on <g> layout modules'
                )

        for element in root.iter():
            raw_staging = element.get(_MORPH_STAGING_ATTR)
            if raw_staging is None:
                continue
            label = _element_label(element)
            if raw_staging != 'true':
                result['errors'].append(
                    f'{label} {_MORPH_STAGING_ATTR} must equal "true"; '
                    'set the exact value or remove the marker'
                )
                continue
            if _local_name(element) != 'g':
                result['errors'].append(
                    f'{label} {_MORPH_STAGING_ATTR} is valid only on <g>; '
                    'move it to the enclosing ordinary direct-root group'
                )
                continue
            if parent_by_id.get(id(element)) is not root:
                result['errors'].append(
                    f'{label} {_MORPH_STAGING_ATTR} requires a direct-root '
                    '<g>; move the marked group directly under <svg> or remove '
                    'the marker'
                )
                continue
            if not (element.get('id') or '').strip():
                result['errors'].append(
                    f'{label} {_MORPH_STAGING_ATTR} requires a stable non-empty '
                    'id; add an id to the marked direct-root group'
                )
                continue
            incompatible = [
                attribute
                for attribute in (
                    'data-pptx-layer',
                    'data-pptx-placeholder',
                )
                if element.get(attribute) is not None
            ]
            if incompatible:
                result['errors'].append(
                    f'{label} {_MORPH_STAGING_ATTR} cannot be combined with '
                    f'{", ".join(incompatible)}; use an ordinary Slide-local '
                    'group or remove the marker'
                )
                continue
            resolved = self._resolved_root_module_bounds(element)
            if resolved is None:
                result['errors'].append(
                    f'{label} {_MORPH_STAGING_ATTR} requires valid '
                    f'{_BOUNDS_ATTR}; add or fix positive root-coordinate '
                    'x y width height bounds'
                )
                continue
            if canvas is None:
                result['errors'].append(
                    f'{label} {_MORPH_STAGING_ATTR} cannot verify an off-canvas '
                    'endpoint without a valid root viewBox; fix the root viewBox'
                )
                continue
            if not self._bounds_are_disjoint(resolved[1], canvas):
                result['errors'].append(
                    f'{label} {_MORPH_STAGING_ATTR} requires wholly off-canvas '
                    f'{_BOUNDS_ATTR}; move the full bounds outside the root '
                    'viewBox or remove the marker from partially visible content'
                )

        missing: List[str] = []
        bounded_root_groups: List[
            Tuple[ET.Element, Tuple[float, float, float, float]]
        ] = []
        root_groups = [
            child
            for child in list(root)
            if _local_name(child) == 'g'
        ]
        require_bounds = (
            self.template_mode
            or root.get('data-pptx-page-role') is not None
            or any(
                root.get(attribute) is not None
                for attribute in _PPTX_ROOT_STRUCTURE_ATTRS
            )
        )
        for group in root_groups:
            if self._is_hidden_element(group, parent_by_id):
                continue
            if (
                _authored_preset_encoding is not None
                and _authored_preset_encoding(group) == 'compact'
            ):
                continue
            raw_bounds = group.get(_BOUNDS_ATTR)
            if raw_bounds is None:
                missing.append(_element_label(group))
                continue
            try:
                _parse_positive_bounds(raw_bounds)
            except ValueError as exc:
                result['errors'].append(
                    f'{_element_label(group)} {_BOUNDS_ATTR} {exc}'
                )
                continue

            resolved = self._resolved_root_module_bounds(group)
            if resolved is None:
                continue
            bounded_root_groups.append((group, resolved[1]))
            if canvas is None:
                continue
            if self._is_off_canvas_morph_group(group, canvas):
                continue
            attribute, bounds = resolved
            self._record_bounds_overflow(
                result,
                subject=f'{_element_label(group)} {attribute}',
                inner=bounds,
                container='canvas viewBox',
                outer=canvas,
                repair=(
                    'keep the root module subcanvas inside the SVG viewBox'
                ),
            )

        structured_page = all(
            (root.get(attribute) or '').strip()
            for attribute in _PPTX_ROOT_STRUCTURE_ATTRS
        )
        for index, (first_group, first_bounds) in enumerate(
            bounded_root_groups,
        ):
            if self._root_module_overlap_exempt(
                first_group,
                structured_page=structured_page,
                canvas=canvas,
            ):
                continue
            for second_group, second_bounds in bounded_root_groups[index + 1:]:
                if self._root_module_overlap_exempt(
                    second_group,
                    structured_page=structured_page,
                    canvas=canvas,
                ):
                    continue
                overlap_width, overlap_height = self._bounds_overlap_dimensions(
                    first_bounds,
                    second_bounds,
                )
                if (
                    overlap_width <= _BOUNDS_OVERFLOW_TOLERANCE
                    or overlap_height <= _BOUNDS_OVERFLOW_TOLERANCE
                ):
                    continue
                result['errors'].append(
                    f'{_element_label(first_group)} {_BOUNDS_ATTR} overlaps '
                    f'{_element_label(second_group)} {_BOUNDS_ATTR} by '
                    f'{overlap_width:.1f}px x {overlap_height:.1f}px; keep '
                    'ordinary direct-root module zones disjoint beyond the '
                    f'{_BOUNDS_OVERFLOW_TOLERANCE:.0f}px tolerance'
                )

        if missing:
            sample = '; '.join(missing[:3])
            suffix = '' if len(missing) <= 3 else f'; +{len(missing) - 3} more'
            bucket = result['errors'] if require_bounds else result['warnings']
            prefix = 'Detected' if require_bounds else 'Reference SVG: detected'
            bucket.append(
                f'{prefix} {len(missing)} visible root-level <g> '
                f'module(s) without explicit {_BOUNDS_ATTR} '
                f'({sample}{suffix}); every final-page/template root <g> other '
                'than a compact authored-preset atom declares its root-coordinate '
                'layout subcanvas even when it also carries native coordinates'
            )

    def _check_text_bounds(
        self,
        root: ET.Element,
        result: Dict,
        *,
        included_text_ids: set[int] | None = None,
    ) -> None:
        """Validate visible text against page and root-module bounds."""
        from .checker import (
            SVG_NS,
            _element_label,
            _estimate_single_line_text_frame_width,
            _local_name,
            _parse_project_font_weight,
            _parse_project_geometry_length,
            _parse_project_text_anchor,
            _parse_viewbox_values,
            _resolve_project_font_sizes,
            _resolve_project_letter_spacings,
        )
        helpers = (
            _estimate_single_line_text_frame_width,
            _parse_project_font_weight,
            _parse_project_geometry_length,
            _parse_project_text_anchor,
            _resolve_project_font_sizes,
            _resolve_project_letter_spacings,
        )
        if any(helper is None for helper in helpers):
            return
        try:
            font_sizes = _resolve_project_font_sizes(root)
            letter_spacings = _resolve_project_letter_spacings(
                root,
                font_sizes,
            )
        except ValueError:
            return

        parent_by_id = {
            id(child): parent
            for parent in root.iter()
            for child in list(parent)
        }
        unchanged_groups = self._unchanged_txbody_group_ids(root)
        viewbox = _parse_viewbox_values(root.get('viewBox') or '')
        canvas = None
        if viewbox is not None:
            x, y, width, height = viewbox
            canvas = (x, y, x + width, y + height)

        estimated_by_id: Dict[
            int,
            Tuple[float, float, float, float],
        ] = {}
        page_overflow_text_ids: set[int] = set()
        unverified: List[str] = []
        for text_element in root.iter(f'{{{SVG_NS}}}text'):
            if (
                included_text_ids is not None
                and id(text_element) not in included_text_ids
            ):
                continue
            if self._has_ancestor_id(
                text_element,
                parent_by_id,
                unchanged_groups,
            ):
                continue
            if self._has_non_visual_ancestor(
                text_element,
                root,
                parent_by_id,
            ):
                continue
            if self._is_hidden_element(text_element, parent_by_id):
                continue
            visible_text = ''.join(text_element.itertext())
            marker_text = '{{' in visible_text and '}}' in visible_text
            slot = parent_by_id.get(id(text_element))
            template_carrier = (
                self.template_mode
                and text_element.get('data-pptx-carrier') == 'true'
                and slot is not None
                and slot.get('data-pptx-placeholder') is not None
            )
            if not visible_text.strip() or (marker_text and not template_carrier):
                continue
            estimated = self._estimated_text_bounds(
                text_element,
                parent_by_id,
                font_sizes,
                letter_spacings,
                include_headroom=True,
            )
            if estimated is not None and marker_text:
                # A token has no content-width contract, but its carrier still
                # has the same baseline/line-box contract as a generated page.
                resolved_slot = self._resolved_root_module_bounds(slot)
                if resolved_slot is not None:
                    boundary = resolved_slot[1]
                    estimated = (boundary[0], estimated[1], boundary[2], estimated[3])
            if estimated is not None:
                estimated_by_id[id(text_element)] = estimated

            if (
                marker_text
                or canvas is None
                or self._has_zero_opacity(text_element, parent_by_id)
            ):
                continue

            page_estimated = self._estimated_text_bounds(
                text_element,
                parent_by_id,
                font_sizes,
                letter_spacings,
                include_headroom=False,
            )
            if page_estimated is None:
                unverified.append(self._text_diagnostic_label(text_element))
                continue
            direct_child = text_element
            parent = parent_by_id.get(id(direct_child))
            while parent is not None and parent is not root:
                direct_child = parent
                parent = parent_by_id.get(id(direct_child))
            morph_staging = (
                parent is root
                and _local_name(direct_child) == 'g'
                and self._is_off_canvas_morph_group(
                    direct_child,
                    canvas,
                )
                and self._bounds_are_disjoint(page_estimated, canvas)
            )
            if (
                not morph_staging
                and self._record_canvas_text_overflow(
                    result,
                    subject=self._text_diagnostic_label(text_element),
                    inner=page_estimated,
                    canvas=canvas,
                    width_diagnostic=self._text_width_diagnostic(
                        text_element,
                        parent_by_id,
                        font_sizes,
                        letter_spacings,
                        container_width=canvas[2] - canvas[0],
                        include_headroom=False,
                    ),
                )
            ):
                page_overflow_text_ids.add(id(text_element))

        if unverified:
            sample = ', '.join(unverified[:3])
            suffix = (
                ''
                if len(unverified) <= 3
                else f', +{len(unverified) - 3} more'
            )
            result['warnings'].append(
                'Cannot verify root viewBox bounds for visible text with '
                f'unsupported or unresolved geometry: {sample}{suffix}; use '
                'supported explicit text positioning when page fit matters '
                '(continuation <tspan> lines repeat the parent x; a hanging '
                'indent needs its own <text>)'
            )

        root_groups = [
            child
            for child in list(root)
            if _local_name(child) == 'g'
        ]
        for module in root_groups:
            if self._is_hidden_element(module, parent_by_id):
                continue
            resolved_module = self._resolved_root_module_bounds(module)
            if resolved_module is None:
                continue
            boundary_attribute, boundary = resolved_module
            for text_element in module.iter(f'{{{SVG_NS}}}text'):
                if id(text_element) in page_overflow_text_ids:
                    continue
                estimated = estimated_by_id.get(id(text_element))
                if estimated is None:
                    continue
                self._record_bounds_overflow(
                    result,
                    subject=self._text_diagnostic_label(text_element),
                    inner=estimated,
                    container=(
                        f'{_element_label(module)} {boundary_attribute}'
                    ),
                    outer=boundary,
                    repair=(
                        # A Layout slot's bounds are its contract on every page.
                        'reflow the text, or map the page to a Layout whose '
                        'slot is larger; slot bounds stay as the prototype '
                        'declares them'
                        if module.get('data-pptx-placeholder') is not None
                        else 'expand the root module bounds into available '
                        'non-overlapping space; otherwise reflow the text'
                    ),
                    width_diagnostic=self._text_width_diagnostic(
                        text_element,
                        parent_by_id,
                        font_sizes,
                        letter_spacings,
                        container_width=boundary[2] - boundary[0],
                        include_headroom=True,
                    ),
                )

