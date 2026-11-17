"""Pinned PPT Master quality gate component.

MIT Copyright (c) 2025-2026 Hugo He.
Upstream 7e0df6705de0cd24345210d31f07fcb5a32b6900.
"""
from __future__ import annotations
from pathlib import Path
from typing import Dict, List, Tuple
from xml.etree import ElementTree as ET

class TextGeometryQualityMixin:
    """Upstream text geometry checker methods."""

    def _check_text_output_geometry(
        self,
        root: ET.Element,
        result: Dict,
        *,
        included_text_ids: set[int] | None = None,
    ) -> None:
        """Reject measurable run advances or frames with non-positive geometry."""
        from .checker import (
            SVG_NS,
            _NON_VISUAL_SVG_TAGS,
            _drawingml_text_frame_width_emu,
            _element_label,
            _estimate_single_line_text_frame_width,
            _local_name,
            _parse_project_font_weight,
            _resolve_project_font_sizes,
            _resolve_project_letter_spacings,
            _validate_single_line_text_run_advances,
        )
        helpers = (
            _drawingml_text_frame_width_emu,
            _estimate_single_line_text_frame_width,
            _parse_project_font_weight,
            _resolve_project_font_sizes,
            _resolve_project_letter_spacings,
            _validate_single_line_text_run_advances,
        )
        if any(helper is None for helper in helpers):
            return
        try:
            font_sizes = _resolve_project_font_sizes(root)
            letter_spacings = _resolve_project_letter_spacings(root, font_sizes)
        except ValueError:
            return

        parent_by_id = {
            id(child): parent
            for parent in root.iter()
            for child in list(parent)
        }
        unchanged_groups = self._unchanged_txbody_group_ids(root)
        errors: List[str] = []
        for text_el in root.iter(f'{{{SVG_NS}}}text'):
            if (
                included_text_ids is not None
                and id(text_el) not in included_text_ids
            ):
                continue
            chain: List[ET.Element] = []
            current: ET.Element | None = text_el
            while current is not None:
                chain.append(current)
                current = parent_by_id.get(id(current))
            if any(
                _local_name(current) in _NON_VISUAL_SVG_TAGS
                for current in chain
            ):
                continue
            if self._has_ancestor_id(text_el, parent_by_id, unchanged_groups):
                continue
            try:
                runs = self._resolved_single_line_text_runs(
                    text_el,
                    parent_by_id,
                    font_sizes,
                    letter_spacings,
                )
                if not runs:
                    continue
                if not ''.join(str(run['text']) for run in runs).strip():
                    continue
                text_width = _estimate_single_line_text_frame_width(runs)
                ext_cx = _drawingml_text_frame_width_emu(
                    text_width,
                    font_sizes[id(text_el)],
                )
            except (KeyError, TypeError, ValueError):
                continue
            if ext_cx < 1:
                errors.append(
                    f'{_element_label(text_el)} negative letter-spacing '
                    'produces a non-positive DrawingML text-frame extent '
                    f'(cx={ext_cx})'
                )
                continue
            try:
                _validate_single_line_text_run_advances(runs)
            except ValueError as exc:
                errors.append(f'{_element_label(text_el)} {exc}')
        result['errors'].extend(errors)

    @classmethod
    def _positioned_text_lines(
        cls,
        text_el: ET.Element,
        parent_by_id: Dict[int, ET.Element],
        font_sizes: Dict[int, float],
        letter_spacings: Dict[int, float],
    ) -> List[Tuple[ET.Element, float, float, List[Dict], float]] | None:
        """Resolve direct positioned tspans into estimable visual lines."""
        from .checker import (
            _classify_paragraph_block,
            _local_name,
            _parse_project_geometry_length,
        )
        if _parse_project_geometry_length is None:
            return None
        children = list(text_el)
        if not children:
            return None
        line_groups = None
        synthetic_first = None
        leads_with_inline_run = (
            cls._is_tspan(children[0]) and not cls._is_line_tspan(children[0])
        )
        if (text_el.text or '').strip() or leads_with_inline_run:
            if _classify_paragraph_block is None:
                return None
            paragraph = _classify_paragraph_block(
                text_el,
                preserve_line_breaks=True,
            )
            if paragraph is None:
                return None
            _base, _extras, _breaks, line_groups, synthetic_first = paragraph
        elif any(
            descendant.get('dx') is not None
            for child in children
            if not cls._is_line_tspan(child)
            for descendant in child.iter()
        ):
            line_groups = []
            for child in children:
                if not (cls._is_tspan(child) or _local_name(child) == 'a'):
                    return None
                if cls._is_line_tspan(child):
                    line_groups.append([child])
                elif line_groups:
                    line_groups[-1].append(child)
                else:
                    return None
        if line_groups is not None:
            try:
                current_y = _parse_project_geometry_length(
                    text_el.get('y') or '0',
                    'y',
                )
                parent_x = _parse_project_geometry_length(
                    text_el.get('x') or '0',
                    'x',
                )
            except ValueError:
                return None

            lines: List[
                Tuple[ET.Element, float, float, List[Dict], float]
            ] = []
            for line_group in line_groups:
                starter = line_group[0]
                if starter is synthetic_first:
                    line_element = text_el
                    line_x = parent_x
                    line_y = current_y
                else:
                    if starter.get('x') is None:
                        return None
                    line_element = starter
                    try:
                        line_x = _parse_project_geometry_length(
                            starter.get('x'),
                            'x',
                        )
                        line_y = (
                            _parse_project_geometry_length(
                                starter.get('y'),
                                'y',
                            )
                            if starter.get('y') is not None
                            else current_y
                        )
                        if starter.get('dx') is not None:
                            line_x += _parse_project_geometry_length(
                                starter.get('dx'),
                                'dx',
                            )
                        if starter.get('dy') is not None:
                            line_y += _parse_project_geometry_length(
                                starter.get('dy'),
                                'dy',
                            )
                    except ValueError:
                        return None

                current_y = line_y
                source_runs = cls._paragraph_line_text_runs(
                    text_el,
                    line_group,
                    synthetic_first,
                )
                if source_runs is None:
                    return None
                try:
                    runs = cls._resolved_text_runs(
                        source_runs,
                        parent_by_id,
                        font_sizes,
                        letter_spacings,
                    )
                except (KeyError, TypeError, ValueError):
                    return None
                if not runs:
                    continue
                try:
                    font_size = max(float(run['font_size']) for run in runs)
                except (KeyError, TypeError, ValueError):
                    return None
                lines.append((
                    line_element,
                    line_x,
                    line_y,
                    runs,
                    font_size,
                ))
            return lines or None

        if any(
            not cls._is_tspan(child)
            or not cls._is_line_tspan(child)
            or child.get('x') is None
            or (child.tail or '').strip()
            for child in children
        ):
            return None

        try:
            current_y = _parse_project_geometry_length(
                text_el.get('y') or '0',
                'y',
            )
        except ValueError:
            return None

        lines: List[Tuple[ET.Element, float, float, List[Dict], float]] = []
        for child in children:
            try:
                line_x = _parse_project_geometry_length(child.get('x'), 'x')
                line_y = (
                    _parse_project_geometry_length(child.get('y'), 'y')
                    if child.get('y') is not None
                    else current_y
                )
                if child.get('dx') is not None:
                    line_x += _parse_project_geometry_length(
                        child.get('dx'),
                        'dx',
                    )
                if child.get('dy') is not None:
                    line_y += _parse_project_geometry_length(
                        child.get('dy'),
                        'dy',
                    )
                runs = cls._resolved_single_line_text_runs(
                    child,
                    parent_by_id,
                    font_sizes,
                    letter_spacings,
                )
            except (KeyError, TypeError, ValueError):
                return None
            current_y = line_y
            if not runs:
                continue
            try:
                font_size = max(float(run['font_size']) for run in runs)
            except (KeyError, TypeError, ValueError):
                return None
            lines.append((child, line_x, line_y, runs, font_size))
        return lines or None

    @classmethod
    def _estimated_text_line_bounds(
        cls,
        line_el: ET.Element,
        x: float,
        y: float,
        runs: List[Dict],
        font_size: float,
        parent_by_id: Dict[int, ET.Element],
        *,
        include_headroom: bool = True,
    ) -> Tuple[float, float, float, float] | None:
        """Estimate one line's transformed visible bounds in SVG coordinates."""
        from .checker import (
            _IDENTITY_MATRIX,
            _effective_presentation_value,
            _estimate_single_line_text_frame_width,
            _matrix_multiply,
            _parse_project_text_anchor,
            _parse_transform_matrix,
            _transform_point,
            math,
        )
        if any(helper is None for helper in (
            _estimate_single_line_text_frame_width,
            _IDENTITY_MATRIX,
            _matrix_multiply,
            _parse_project_text_anchor,
            _parse_transform_matrix,
            _transform_point,
        )):
            return None
        try:
            width = float(_estimate_single_line_text_frame_width(
                runs,
                include_headroom=include_headroom,
            ))
            raw_anchor = (
                _effective_presentation_value(
                    line_el,
                    'text-anchor',
                    parent_by_id,
                )
                or 'start'
            ).strip().lower()
            anchor = _parse_project_text_anchor(raw_anchor).value
        except (TypeError, ValueError):
            return None
        if not all(math.isfinite(value) for value in (x, y, width, font_size)):
            return None
        if width <= 0 or font_size <= 0:
            return None

        if anchor == 'middle':
            left = x - width / 2
            right = x + width / 2
        elif anchor == 'end':
            left = x - width
            right = x
        elif anchor == 'start':
            left = x
            right = x + width
        else:
            return None
        ascent, descent = cls._text_line_vertical_extent(runs, font_size)
        top = y - ascent
        bottom = y + descent

        return cls._transformed_rect_bounds(
            line_el,
            (left, top, right - left, bottom - top),
            parent_by_id,
        )

    @classmethod
    def _imported_model_text_lines(
        cls,
        text_el: ET.Element,
        parent_by_id: Dict[int, ET.Element],
        font_sizes: Dict[int, float],
        letter_spacings: Dict[int, float],
    ) -> List[Tuple[ET.Element, float, float, List[Dict], float]] | None:
        """Measure an imported ``lines`` / ``paragraphs`` text one row per tspan.

        ``pptx_to_svg.py --roundtrip`` projects multi-line source text as
        nested ``<tspan>`` rows without ``x`` / ``dy`` and records the row
        advance in ``data-paragraph-line-height``; the exporter reads that
        model, so the estimator must not measure the rows as one line.
        """
        from .checker import (
            ET,
            _local_name,
            _parse_project_geometry_length,
            re,
        )
        if text_el.get('data-pptx-text-model') not in {'lines', 'paragraphs'}:
            return None
        if _parse_project_geometry_length is None:
            return None
        try:
            base_x = _parse_project_geometry_length(text_el.get('x') or '0', 'x')
            base_y = _parse_project_geometry_length(text_el.get('y') or '0', 'y')
        except ValueError:
            return None
        source_runs: List[Tuple[ET.Element, str]] = []

        def collect(element: ET.Element) -> None:
            if element.text and element.text.strip():
                source_runs.append((element, re.sub(r'\s+', ' ', element.text)))
            for child in list(element):
                if _local_name(child) not in {'tspan', 'a'}:
                    continue
                collect(child)
                if child.tail and child.tail.strip():
                    source_runs.append((element, re.sub(r'\s+', ' ', child.tail)))

        collect(text_el)
        if not source_runs:
            return None

        def row_of(owner: ET.Element) -> ET.Element | None:
            current: ET.Element | None = owner
            while current is not None and current is not text_el:
                parent = parent_by_id.get(id(current))
                if parent is text_el:
                    return current
                current = parent
            return None

        rows: List[Tuple[ET.Element | None, List[Tuple[ET.Element, str]]]] = []
        for owner, text in source_runs:
            row = row_of(owner)
            if rows and rows[-1][0] is row:
                rows[-1][1].append((owner, text))
            else:
                rows.append((row, [(owner, text)]))
        try:
            line_height = float(text_el.get('data-paragraph-line-height') or 0)
        except ValueError:
            line_height = 0.0
        lines: List[Tuple[ET.Element, float, float, List[Dict], float]] = []
        y = base_y
        try:
            for index, (row, row_runs) in enumerate(rows):
                resolved = cls._resolved_text_runs(
                    row_runs,
                    parent_by_id,
                    font_sizes,
                    letter_spacings,
                )
                if not resolved:
                    continue
                size = max(float(run['font_size']) for run in resolved)
                if index > 0:
                    y += line_height if line_height > 0 else size * 1.2
                    if row is not None:
                        y += float(row.get('data-paragraph-space-before') or 0)
                lines.append((row if row is not None else text_el, base_x, y, resolved, size))
        except (KeyError, TypeError, ValueError):
            return None
        return lines or None

    @classmethod
    def _resolved_text_lines(
        cls,
        text_el: ET.Element,
        parent_by_id: Dict[int, ET.Element],
        font_sizes: Dict[int, float],
        letter_spacings: Dict[int, float],
    ) -> List[Tuple[ET.Element, float, float, List[Dict], float]] | None:
        """Resolve one text carrier into the lines used by width estimation."""
        from .checker import (
            _parse_project_geometry_length,
        )
        lines: List[Tuple[ET.Element, float, float, List[Dict], float]] | None
        model_lines = cls._imported_model_text_lines(
            text_el,
            parent_by_id,
            font_sizes,
            letter_spacings,
        )
        if model_lines:
            return model_lines
        try:
            runs = cls._resolved_single_line_text_runs(
                text_el,
                parent_by_id,
                font_sizes,
                letter_spacings,
            )
        except (KeyError, TypeError, ValueError):
            return None
        if runs:
            try:
                lines = [(
                    text_el,
                    _parse_project_geometry_length(text_el.get('x') or '0', 'x'),
                    _parse_project_geometry_length(text_el.get('y') or '0', 'y'),
                    runs,
                    max(float(run['font_size']) for run in runs),
                )]
            except (KeyError, TypeError, ValueError):
                return None
        else:
            lines = cls._positioned_text_lines(
                text_el,
                parent_by_id,
                font_sizes,
                letter_spacings,
            )
        return lines or None

    @classmethod
    def _estimated_text_bounds(
        cls,
        text_el: ET.Element,
        parent_by_id: Dict[int, ET.Element],
        font_sizes: Dict[int, float],
        letter_spacings: Dict[int, float],
        *,
        include_headroom: bool = True,
    ) -> Tuple[float, float, float, float] | None:
        """Estimate one single- or multi-line text carrier's visual bounds."""
        lines = cls._resolved_text_lines(
            text_el,
            parent_by_id,
            font_sizes,
            letter_spacings,
        )
        if lines is None:
            return None

        bounds = [
            cls._estimated_text_line_bounds(
                line_el,
                x,
                y,
                line_runs,
                font_size,
                parent_by_id,
                include_headroom=include_headroom,
            )
            for line_el, x, y, line_runs, font_size in lines
        ]
        resolved = [item for item in bounds if item is not None]
        if not resolved:
            return None
        return (
            min(item[0] for item in resolved),
            min(item[1] for item in resolved),
            max(item[2] for item in resolved),
            max(item[3] for item in resolved),
        )

