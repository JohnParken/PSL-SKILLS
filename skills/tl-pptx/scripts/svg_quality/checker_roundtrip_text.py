"""Pinned PPT Master quality gate component.

MIT Copyright (c) 2025-2026 Hugo He.
Upstream 7e0df6705de0cd24345210d31f07fcb5a32b6900.
"""
from __future__ import annotations
from pathlib import Path
from typing import Dict, List, Tuple
from xml.etree import ElementTree as ET

class RoundtripTextQualityMixin:
    """Upstream roundtrip text checker methods."""

    @classmethod
    def _roundtrip_changed_font_families(
        cls,
        root: ET.Element,
        baseline_root: ET.Element,
        included_text_ids: set[int],
    ) -> List[str]:
        """Return resolved edited fonts that differ from the source baseline."""
        from .checker import (
            SVG_NS,
            _local_name,
            re,
        )
        from svg_authoring_view import SOURCE_REF_ATTRIBUTE

        parent_by_id = {
            id(child): parent
            for parent in root.iter()
            for child in list(parent)
        }
        baseline_parent_by_id = {
            id(child): parent
            for parent in baseline_root.iter()
            for child in list(parent)
        }
        baseline_by_ref = {
            source_ref: element
            for element in baseline_root.iter()
            if (source_ref := element.get(SOURCE_REF_ATTRIBUTE))
        }
        changed: List[str] = []
        seen: set[str] = set()
        for text_element in root.iter(f'{{{SVG_NS}}}text'):
            if id(text_element) not in included_text_ids:
                continue
            current_owner: ET.Element | None = text_element
            source_ref: str | None = None
            while current_owner is not None:
                source_ref = current_owner.get(SOURCE_REF_ATTRIBUTE)
                if source_ref:
                    break
                current_owner = parent_by_id.get(id(current_owner))
            baseline_owner = (
                baseline_by_ref.get(source_ref)
                if source_ref is not None
                else None
            )
            for carrier in text_element.iter():
                if _local_name(carrier) not in {'text', 'tspan'}:
                    continue
                if not re.sub(r'\s+', ' ', ''.join(carrier.itertext())).strip():
                    continue
                current_family = cls._roundtrip_resolved_font_family(
                    carrier,
                    parent_by_id,
                )
                if current_family is None:
                    continue
                baseline_context = baseline_root
                if current_owner is not None and baseline_owner is not None:
                    relative_path = cls._roundtrip_relative_path(
                        carrier,
                        current_owner,
                        parent_by_id,
                    )
                    if relative_path is not None:
                        baseline_context = cls._roundtrip_baseline_context(
                            current_owner,
                            baseline_owner,
                            relative_path,
                        )
                baseline_family = cls._roundtrip_resolved_font_family(
                    baseline_context,
                    baseline_parent_by_id,
                )
                normalized = cls._normalize_font_stack(current_family)
                if (
                    baseline_family is not None
                    and normalized
                    == cls._normalize_font_stack(baseline_family)
                ):
                    continue
                if normalized and normalized not in seen:
                    changed.append(current_family)
                    seen.add(normalized)
        return changed

    def _check_roundtrip_text_frames(
        self,
        root: ET.Element,
        result: Dict,
        included_text_ids: set[int],
        unchanged_text_ids: set[int],
        baseline_root: ET.Element | None = None,
    ) -> None:
        """Calibrate source text widths, then check edited owning frames."""
        from .checker import (
            SVG_NS,
            _ROUNDTRIP_TEXT_CALIBRATION_CAP,
            _estimate_single_line_text_frame_width,
            _parse_project_font_weight,
            _parse_project_geometry_length,
            _parse_project_text_anchor,
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
            result['warnings'].append(
                'Unable to import text metrics; skipped edited-text frame-fit check'
            )
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
        measured_unchanged = 0
        positive_overflow_ratios: List[float] = []
        for text_element in root.iter(f'{{{SVG_NS}}}text'):
            if id(text_element) not in unchanged_text_ids:
                continue
            if self._has_non_visual_ancestor(
                text_element,
                root,
                parent_by_id,
            ):
                continue
            if (
                self._is_hidden_element(text_element, parent_by_id)
                or self._has_zero_opacity(text_element, parent_by_id)
            ):
                continue
            visible_text = ''.join(text_element.itertext())
            if (
                not visible_text.strip()
                or ('{{' in visible_text and '}}' in visible_text)
            ):
                continue
            estimated = self._estimated_text_bounds(
                text_element,
                parent_by_id,
                font_sizes,
                letter_spacings,
                include_headroom=True,
            )
            if estimated is None:
                continue
            _frame_label, frame, _frame_error, frame_is_inferred = (
                self._roundtrip_text_frame(
                    text_element,
                    parent_by_id,
                )
            )
            if frame is None or frame_is_inferred:
                continue
            measured_unchanged += 1
            metrics = self._bounds_overflow_metrics(estimated, frame)
            if metrics is not None and metrics[1] > 0:
                positive_overflow_ratios.append(metrics[1])

        calibration = min(
            max(positive_overflow_ratios, default=0.0),
            _ROUNDTRIP_TEXT_CALIBRATION_CAP,
        )
        source_slot_overflow = self._roundtrip_source_slot_overflow(baseline_root)
        result['info']['roundtrip_text_calibration'] = {
            'factor': calibration,
            'measured_unchanged': measured_unchanged,
            'positive_unchanged': len(positive_overflow_ratios),
        }
        for text_element in root.iter(f'{{{SVG_NS}}}text'):
            if id(text_element) not in included_text_ids:
                continue
            if self._has_non_visual_ancestor(
                text_element,
                root,
                parent_by_id,
            ):
                continue
            if (
                self._is_hidden_element(text_element, parent_by_id)
                or self._has_zero_opacity(text_element, parent_by_id)
            ):
                continue
            visible_text = ''.join(text_element.itertext())
            if (
                not visible_text.strip()
                or ('{{' in visible_text and '}}' in visible_text)
            ):
                continue
            estimated = self._estimated_text_bounds(
                text_element,
                parent_by_id,
                font_sizes,
                letter_spacings,
                include_headroom=True,
            )
            if estimated is None:
                continue
            frame_label, frame, frame_error, frame_is_inferred = (
                self._roundtrip_text_frame(
                    text_element,
                    parent_by_id,
                )
            )
            text_label = self._text_diagnostic_label(text_element)
            if frame is None:
                detail = f': {frame_error}' if frame_error else ''
                result['warnings'].append(
                    f'Cannot verify owning frame for edited {text_label}{detail}; '
                    'set data-pptx-bounds on the authored text group or group '
                    'the text with its containing rect; preserve existing imported frames'
                )
                continue
            metrics = self._bounds_overflow_metrics(estimated, frame)
            if metrics is None or metrics[1] <= 0:
                continue
            _axes, horizontal_ratio, _vertical_ratio = metrics
            corrected_horizontal_ratio = max(
                horizontal_ratio - calibration,
                0.0,
            )
            if corrected_horizontal_ratio <= 0:
                continue
            # The source text of this same slot already reached this far.
            if horizontal_ratio <= source_slot_overflow.get(
                self._roundtrip_frame_source_ref(text_element, parent_by_id),
                0.0,
            ):
                continue
            left, top, right, bottom = estimated
            frame_left, frame_top, frame_right, frame_bottom = frame
            overflow_detail = (
                f'overflow horizontal {corrected_horizontal_ratio:.1%} after '
                f'{calibration:.1%} same-page source calibration '
                f'(raw {horizontal_ratio:.1%})'
                if calibration > 0
                else f'overflow horizontal {horizontal_ratio:.1%}'
            )
            finding = (
                f'{text_label} exceeds owning frame {frame_label} on the '
                f'horizontal axis: estimated text ({left:.1f}, {top:.1f})-'
                f'({right:.1f}, {bottom:.1f}), frame ({frame_left:.1f}, '
                f'{frame_top:.1f})-({frame_right:.1f}, {frame_bottom:.1f}), '
                f'{overflow_detail}; shorten or '
                'reflow the edited text, or '
                'enlarge its owning frame'
            )
            if frame_is_inferred:
                result['warnings'].append(
                    f'{finding}; frame inferred from a containing rect sibling; '
                    'set data-pptx-bounds on the authored text group to declare its bounds'
                )
            else:
                result['errors'].append(finding)

    @staticmethod
    def _roundtrip_frame_source_ref(
        text_element: ET.Element,
        parent_by_id: Dict[int, ET.Element],
    ) -> str | None:
        """Return the source ref of the object whose frame owns one text."""
        current: ET.Element | None = text_element
        while current is not None:
            if current.get('data-pptx-frame') is not None:
                return current.get('data-pptx-source-ref')
            if current.get('data-pptx-bounds') is not None:
                return None
            current = parent_by_id.get(id(current))
        return None

    def _roundtrip_source_slot_overflow(
        self,
        baseline_root: ET.Element | None,
    ) -> Dict[str | None, float]:
        """Measure how far each imported slot's own source text overflows it."""
        from .checker import (
            SVG_NS,
            _resolve_project_font_sizes,
            _resolve_project_letter_spacings,
        )
        if baseline_root is None:
            return {}
        try:
            font_sizes = _resolve_project_font_sizes(baseline_root)
            letter_spacings = _resolve_project_letter_spacings(
                baseline_root,
                font_sizes,
            )
        except ValueError:
            return {}
        parent_by_id = {
            id(child): parent
            for parent in baseline_root.iter()
            for child in list(parent)
        }
        overflow: Dict[str | None, float] = {}
        for text_element in baseline_root.iter(f'{{{SVG_NS}}}text'):
            source_ref = self._roundtrip_frame_source_ref(
                text_element,
                parent_by_id,
            )
            if source_ref is None:
                continue
            estimated = self._estimated_text_bounds(
                text_element,
                parent_by_id,
                font_sizes,
                letter_spacings,
                include_headroom=True,
            )
            _label, frame, _error, _inferred = self._roundtrip_text_frame(
                text_element,
                parent_by_id,
            )
            if estimated is None or frame is None:
                continue
            metrics = self._bounds_overflow_metrics(estimated, frame)
            if metrics is not None and metrics[1] > 0:
                overflow[source_ref] = max(
                    overflow.get(source_ref, 0.0),
                    metrics[1],
                )
        return overflow

    @classmethod
    def _roundtrip_text_frame(
        cls,
        text_element: ET.Element,
        parent_by_id: Dict[int, ET.Element],
    ) -> Tuple[
        str | None,
        Tuple[float, float, float, float] | None,
        str | None,
        bool,
    ]:
        """Resolve owning bounds or a containing rect in the text's own group."""
        from .checker import (
            _element_label,
            _local_name,
            _parse_positive_bounds,
            _parse_project_geometry_length,
            math,
        )
        current: ET.Element | None = text_element
        while current is not None:
            attribute = (
                'data-pptx-frame' if current.get('data-pptx-frame') is not None
                else 'data-pptx-bounds'
            )
            raw_frame = current.get(attribute)
            if raw_frame is not None:
                label = f'{_element_label(current)} {attribute}'
                try:
                    frame = _parse_positive_bounds(raw_frame)
                except ValueError as exc:
                    return label, None, f'{label} {exc}', False
                transformed = cls._transformed_rect_bounds(
                    current,
                    frame,
                    parent_by_id,
                )
                if transformed is None:
                    return (
                        label,
                        None,
                        f'{label} has an unresolved transform',
                        False,
                    )
                return label, transformed, None, False
            current = parent_by_id.get(id(current))

        if _parse_project_geometry_length is None:
            return None, None, 'the SVG length parser is unavailable', False
        parent = parent_by_id.get(id(text_element))
        if parent is None or _local_name(parent) != 'g':
            return None, None, None, False
        try:
            anchor_x = _parse_project_geometry_length(text_element.get('x') or '0', 'x')
            anchor_y = _parse_project_geometry_length(text_element.get('y') or '0', 'y')
        except (TypeError, ValueError):
            return None, None, 'the text anchor cannot be resolved', False
        anchor = cls._transformed_rect_bounds(
            text_element, (anchor_x, anchor_y, 0, 0), parent_by_id,
        )
        if anchor is None:
            return None, None, 'the text anchor has an unresolved transform', False
        rect_errors: List[str] = []
        siblings = list(parent)
        current_index = siblings.index(text_element)
        rects = sorted(
            (
                (abs(index - current_index), index, sibling)
                for index, sibling in enumerate(siblings)
                if _local_name(sibling) == 'rect'
                and sibling.get('data-pptx-role') not in {'decoration', 'background'}
            ),
            key=lambda item: (item[0], item[1]),
        )
        for _distance, _index, rect in rects:
            label = _element_label(rect)
            try:
                x = _parse_project_geometry_length(rect.get('x') or '0', 'x')
                y = _parse_project_geometry_length(rect.get('y') or '0', 'y')
                width = _parse_project_geometry_length(rect.get('width'), 'width')
                height = _parse_project_geometry_length(rect.get('height'), 'height')
                if (
                    not all(math.isfinite(value) for value in (x, y, width, height))
                    or width <= 0 or height <= 0
                ):
                    raise ValueError('must use finite positive width and height')
            except (TypeError, ValueError) as exc:
                rect_errors.append(f'{label} {exc}')
                continue
            transformed = cls._transformed_rect_bounds(rect, (x, y, width, height), parent_by_id)
            if transformed is None:
                rect_errors.append(f'{label} has an unresolved transform')
            elif (
                transformed[0] <= anchor[0] <= transformed[2]
                and transformed[1] <= anchor[1] <= transformed[3]
            ):
                return label, transformed, None, True
        return (
            None,
            None,
            rect_errors[0] if rect_errors else None,
            False,
        )

