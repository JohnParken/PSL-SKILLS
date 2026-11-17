"""Pinned PPT Master quality gate component.

MIT Copyright (c) 2025-2026 Hugo He.
Upstream 7e0df6705de0cd24345210d31f07fcb5a32b6900.
"""
from __future__ import annotations
from pathlib import Path
from typing import Dict, List, Tuple
from xml.etree import ElementTree as ET

from .checker import (
    _BOUNDS_OVERFLOW_TOLERANCE,
)


class BoundsQualityMixin:
    """Upstream bounds checker methods."""

    @staticmethod
    def _accumulated_transform_matrix(
        element: ET.Element,
        parent_by_id: Dict[int, ET.Element],
    ):
        """Return the element-to-root transform matrix when available."""
        from .checker import (
            _IDENTITY_MATRIX,
            _matrix_multiply,
            _parse_transform_matrix,
        )
        if any(helper is None for helper in (
            _IDENTITY_MATRIX,
            _matrix_multiply,
            _parse_transform_matrix,
        )):
            return None
        chain: List[ET.Element] = []
        current: ET.Element | None = element
        while current is not None:
            chain.append(current)
            current = parent_by_id.get(id(current))
        matrix = _IDENTITY_MATRIX
        try:
            for current in reversed(chain):
                raw_transform = current.get('transform')
                if raw_transform:
                    matrix = _matrix_multiply(
                        matrix,
                        _parse_transform_matrix(raw_transform),
                    )
        except (TypeError, ValueError):
            return None
        return matrix

    @classmethod
    def _transformed_rect_bounds(
        cls,
        element: ET.Element,
        bounds: Tuple[float, float, float, float],
        parent_by_id: Dict[int, ET.Element],
    ) -> Tuple[float, float, float, float] | None:
        """Transform one local rectangle into root SVG coordinates."""
        from .checker import (
            _transform_point,
        )
        if _transform_point is None:
            return None
        matrix = cls._accumulated_transform_matrix(element, parent_by_id)
        if matrix is None:
            return None
        x, y, width, height = bounds
        try:
            corners = [
                _transform_point(matrix, corner_x, corner_y)
                for corner_x, corner_y in (
                    (x, y),
                    (x + width, y),
                    (x + width, y + height),
                    (x, y + height),
                )
            ]
        except (TypeError, ValueError):
            return None
        xs = [point[0] for point in corners]
        ys = [point[1] for point in corners]
        return min(xs), min(ys), max(xs), max(ys)

    @classmethod
    def _transformed_rect_edge_lengths(
        cls,
        element: ET.Element,
        bounds: Tuple[float, float, float, float],
        parent_by_id: Dict[int, ET.Element],
    ) -> Tuple[float, float] | None:
        """Return frame-axis lengths after accumulated SVG transforms."""
        from .checker import (
            _transform_point,
            math,
        )
        if _transform_point is None:
            return None
        matrix = cls._accumulated_transform_matrix(element, parent_by_id)
        if matrix is None:
            return None
        x, y, width, height = bounds
        try:
            origin = _transform_point(matrix, x, y)
            width_end = _transform_point(matrix, x + width, y)
            height_end = _transform_point(matrix, x, y + height)
        except (TypeError, ValueError):
            return None
        rendered_w = math.hypot(
            width_end[0] - origin[0],
            width_end[1] - origin[1],
        )
        rendered_h = math.hypot(
            height_end[0] - origin[0],
            height_end[1] - origin[1],
        )
        if rendered_w <= 0 or rendered_h <= 0:
            return None
        return rendered_w, rendered_h

    @classmethod
    def _text_width_diagnostic(
        cls,
        text_element: ET.Element,
        parent_by_id: Dict[int, ET.Element],
        font_sizes: Dict[int, float],
        letter_spacings: Dict[int, float],
        *,
        container_width: float,
        include_headroom: bool,
    ) -> str | None:
        """Summarize the overflowing line's effective per-cluster width."""
        from .checker import (
            _estimate_single_line_text_frame_width,
            _is_cjk_char,
            _split_project_text_clusters,
            math,
        )
        if (
            _estimate_single_line_text_frame_width is None
            or _is_cjk_char is None
            or _split_project_text_clusters is None
            or not math.isfinite(container_width)
            or container_width <= 0
        ):
            return None
        lines = cls._resolved_text_lines(
            text_element,
            parent_by_id,
            font_sizes,
            letter_spacings,
        )
        if lines is None:
            return None

        widest: Tuple[float, List[str], float] | None = None
        for line_element, _x, _y, runs, _font_size in lines:
            clusters: List[str] = []
            weighted_font_size = 0.0
            try:
                for run in runs:
                    run_clusters = [
                        cluster
                        for cluster in _split_project_text_clusters(
                            str(run.get('text', ''))
                        )
                        if not cluster.isspace()
                    ]
                    run_font_size = float(run['font_size'])
                    clusters.extend(run_clusters)
                    weighted_font_size += run_font_size * len(run_clusters)
                raw_width = float(_estimate_single_line_text_frame_width(
                    runs,
                    include_headroom=include_headroom,
                ))
            except (KeyError, TypeError, ValueError):
                continue
            if not clusters or not math.isfinite(raw_width) or raw_width <= 0:
                continue
            transformed = cls._transformed_rect_edge_lengths(
                line_element,
                (0.0, 0.0, raw_width, 1.0),
                parent_by_id,
            )
            rendered_width = transformed[0] if transformed is not None else raw_width
            if not math.isfinite(rendered_width) or rendered_width <= 0:
                continue
            rendered_font_size = (
                weighted_font_size
                / len(clusters)
                * rendered_width
                / raw_width
            )
            if widest is None or rendered_width > widest[0]:
                widest = rendered_width, clusters, rendered_font_size

        if widest is None:
            return None
        rendered_width, clusters, rendered_font_size = widest
        per_cluster = rendered_width / len(clusters)
        if not math.isfinite(per_cluster) or per_cluster <= 0:
            return None

        cjk_clusters = [
            any(_is_cjk_char(ch) for ch in cluster)
            for cluster in clusters
        ]
        if all(cjk_clusters):
            cluster_label = 'CJK char'
        elif not any(cjk_clusters) and all(
            all(ord(ch) < 128 for ch in cluster)
            for cluster in clusters
        ):
            cluster_label = 'Latin char'
        else:
            cluster_label = 'mixed char'

        font_size = (
            f'{rendered_font_size:.0f}'
            if math.isclose(rendered_font_size, round(rendered_font_size), abs_tol=0.05)
            else f'{rendered_font_size:.1f}'
        )
        width = (
            f'{container_width:.0f}'
            if math.isclose(container_width, round(container_width), abs_tol=0.05)
            else f'{container_width:.1f}'
        )
        headroom = 'incl. headroom' if include_headroom else 'without headroom'
        fits = max(0, math.floor(container_width / per_cluster))
        return (
            f'≈{per_cluster:.1f} px per {cluster_label} at {font_size}px '
            f'{headroom}; ≈{fits} chars fit in {width} px'
        )

    @staticmethod
    def _resolved_root_module_bounds(
        group: ET.Element,
    ) -> Tuple[str, Tuple[float, float, float, float]] | None:
        """Return one root module's explicit boundary in root coordinates."""
        from .checker import (
            _BOUNDS_ATTR,
            _parse_positive_bounds,
        )
        raw = group.get(_BOUNDS_ATTR)
        if raw is None:
            return None
        try:
            x, y, width, height = _parse_positive_bounds(raw)
        except ValueError:
            return None
        return _BOUNDS_ATTR, (x, y, x + width, y + height)

    @staticmethod
    def _bounds_overflow_metrics(
        inner: Tuple[float, float, float, float],
        outer: Tuple[float, float, float, float],
        *,
        tolerance: float = _BOUNDS_OVERFLOW_TOLERANCE,
    ) -> Tuple[str, float, float] | None:
        """Return overflow axes and ratios relative to the outer dimensions."""
        left, top, right, bottom = inner
        outer_left, outer_top, outer_right, outer_bottom = outer
        left_overflow = max(outer_left - left, 0.0)
        right_overflow = max(right - outer_right, 0.0)
        top_overflow = max(outer_top - top, 0.0)
        bottom_overflow = max(bottom - outer_bottom, 0.0)
        horizontal = (
            left_overflow > tolerance
            or right_overflow > tolerance
        )
        vertical = (
            top_overflow > tolerance
            or bottom_overflow > tolerance
        )
        if not horizontal and not vertical:
            return None

        outer_width = outer_right - outer_left
        outer_height = outer_bottom - outer_top
        if outer_width <= 0.0 or outer_height <= 0.0:
            return None
        horizontal_ratio = (
            max(left_overflow, right_overflow) / outer_width
            if horizontal else 0.0
        )
        vertical_ratio = (
            max(top_overflow, bottom_overflow) / outer_height
            if vertical else 0.0
        )
        if horizontal and vertical:
            axes = 'horizontal and vertical'
        elif horizontal:
            axes = 'horizontal'
        else:
            axes = 'vertical'
        return axes, horizontal_ratio, vertical_ratio

    @staticmethod
    def _bounds_are_disjoint(
        first: Tuple[float, float, float, float],
        second: Tuple[float, float, float, float],
    ) -> bool:
        """Return whether two root-coordinate rectangles do not intersect."""
        left, top, right, bottom = first
        other_left, other_top, other_right, other_bottom = second
        return (
            right <= other_left
            or left >= other_right
            or bottom <= other_top
            or top >= other_bottom
        )

    @staticmethod
    def _bounds_overlap_dimensions(
        first: Tuple[float, float, float, float],
        second: Tuple[float, float, float, float],
    ) -> Tuple[float, float]:
        """Return positive intersection width and height for two bounds."""
        left, top, right, bottom = first
        other_left, other_top, other_right, other_bottom = second
        return (
            max(min(right, other_right) - max(left, other_left), 0.0),
            max(min(bottom, other_bottom) - max(top, other_top), 0.0),
        )

    @classmethod
    def _is_off_canvas_morph_group(
        cls,
        group: ET.Element,
        canvas: Tuple[float, float, float, float],
    ) -> bool:
        """Return whether a group declares one wholly off-canvas Morph state."""
        from .checker import (
            _MORPH_STAGING_ATTR,
        )
        if group.get(_MORPH_STAGING_ATTR) != 'true':
            return False
        resolved = cls._resolved_root_module_bounds(group)
        return (
            resolved is not None
            and cls._bounds_are_disjoint(resolved[1], canvas)
        )

    @classmethod
    def _root_module_overlap_exempt(
        cls,
        group: ET.Element,
        *,
        structured_page: bool,
        canvas: Tuple[float, float, float, float] | None,
    ) -> bool:
        """Return whether one root group is not an ordinary module zone."""
        from .checker import (
            _STRUCTURAL_ROLES,
        )
        role = (group.get('data-pptx-role') or '').strip().lower()
        if role in _STRUCTURAL_ROLES:
            return True
        if structured_page and group.get('data-pptx-placeholder') is not None:
            return True
        return (
            canvas is not None
            and cls._is_off_canvas_morph_group(group, canvas)
        )

    @classmethod
    def _record_bounds_overflow(
        cls,
        result: Dict,
        *,
        subject: str,
        inner: Tuple[float, float, float, float],
        container: str,
        outer: Tuple[float, float, float, float],
        repair: str,
        width_diagnostic: str | None = None,
    ) -> None:
        """Record a warning through 5% overflow and an error above it."""
        from .checker import (
            _BOUNDS_OVERFLOW_ERROR_RATIO,
            math,
        )
        metrics = cls._bounds_overflow_metrics(inner, outer)
        if metrics is None:
            return
        axes, horizontal_ratio, vertical_ratio = metrics
        overflow_ratio = max(horizontal_ratio, vertical_ratio)
        exceeds_error_ratio = (
            overflow_ratio > _BOUNDS_OVERFLOW_ERROR_RATIO
            and not math.isclose(
                overflow_ratio,
                _BOUNDS_OVERFLOW_ERROR_RATIO,
                rel_tol=0.0,
                abs_tol=1e-9,
            )
        )
        bucket = (
            result['errors']
            if exceeds_error_ratio
            else result['warnings']
        )
        left, top, right, bottom = inner
        outer_left, outer_top, outer_right, outer_bottom = outer
        width_suffix = (
            f'; {width_diagnostic}'
            if horizontal_ratio > 0 and width_diagnostic
            else ''
        )
        bucket.append(
            f'{subject} exceeds {container} on the {axes} axis: '
            f'content ({left:.1f}, {top:.1f})-({right:.1f}, '
            f'{bottom:.1f}), container ({outer_left:.1f}, '
            f'{outer_top:.1f})-({outer_right:.1f}, '
            f'{outer_bottom:.1f}), overflow horizontal '
            f'{horizontal_ratio:.1%}, vertical {vertical_ratio:.1%} '
            f'(error above {_BOUNDS_OVERFLOW_ERROR_RATIO:.0%}); '
            f'{repair}{width_suffix}'
        )

    @classmethod
    def _record_canvas_text_overflow(
        cls,
        result: Dict,
        *,
        subject: str,
        inner: Tuple[float, float, float, float],
        canvas: Tuple[float, float, float, float],
        width_diagnostic: str | None = None,
    ) -> bool:
        """Record one page-boundary error and return whether it overflowed."""
        metrics = cls._bounds_overflow_metrics(inner, canvas)
        if metrics is None:
            return False
        axes, horizontal_ratio, vertical_ratio = metrics
        left, top, right, bottom = inner
        canvas_left, canvas_top, canvas_right, canvas_bottom = canvas
        width_suffix = (
            f'; {width_diagnostic}'
            if horizontal_ratio > 0 and width_diagnostic
            else ''
        )
        result['errors'].append(
            f'{subject} exceeds the root viewBox on the {axes} axis: '
            f'content ({left:.1f}, {top:.1f})-({right:.1f}, '
            f'{bottom:.1f}), canvas ({canvas_left:.1f}, '
            f'{canvas_top:.1f})-({canvas_right:.1f}, '
            f'{canvas_bottom:.1f}), overflow horizontal '
            f'{horizontal_ratio:.1%}, vertical {vertical_ratio:.1%}; '
            'move or reflow the text until its estimated bounds stay on-page'
            f'{width_suffix}'
        )
        return True

    @staticmethod
    def _text_diagnostic_label(text_element: ET.Element) -> str:
        """Return a locatable label for one SVG text carrier."""
        from .checker import (
            _element_label,
            re,
        )
        label = _element_label(text_element)
        if (text_element.get('id') or '').strip():
            return label

        details: List[str] = []
        raw_x = (text_element.get('x') or '').strip()
        raw_y = (text_element.get('y') or '').strip()
        if raw_x or raw_y:
            details.append(f'x={raw_x or "?"}, y={raw_y or "?"}')
        snippet = re.sub(r'\s+', ' ', ''.join(text_element.itertext())).strip()
        if snippet:
            preview = snippet[:20] + ('…' if len(snippet) > 20 else '')
            details.append(f'text={preview!r}')
        return f'{label} ({"; ".join(details)})' if details else label

