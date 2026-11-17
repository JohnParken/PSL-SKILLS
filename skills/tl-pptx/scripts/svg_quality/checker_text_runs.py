"""Pinned PPT Master quality gate component.

MIT Copyright (c) 2025-2026 Hugo He.
Upstream 7e0df6705de0cd24345210d31f07fcb5a32b6900.
"""
from __future__ import annotations
from pathlib import Path
from typing import Dict, List, Tuple
from xml.etree import ElementTree as ET

class TextRunsQualityMixin:
    """Upstream text runs checker methods."""

    def _check_fonts(
        self,
        content: str,
        result: Dict,
        *,
        font_families: List[str] | None = None,
    ):
        """Check font usage.

        PPTX stores concrete typefaces per run with no CSS fallback. The
        converter resolves each SVG font stack to exported latin / EA typefaces;
        validate those exported values rather than the visual-preview tail.
        """
        from .checker import (
            _unsafe_exported_font_faces,
        )
        font_matches = (
            self._font_family_values(content)
            if font_families is None
            else font_families
        )

        if not font_matches:
            return

        result['info']['fonts'] = sorted(set(font_matches))
        if _unsafe_exported_font_faces is None:
            result['warnings'].append(
                "Unable to import svg_to_pptx font resolver; skipped exported-font safety check"
            )
            return

        for font_family in font_matches:
            unsafe = [
                f"{role}={family}"
                for role, family in _unsafe_exported_font_faces(font_family).items()
            ]
            if unsafe:
                result['warnings'].append(
                    "Font stack exports non-PPT-safe typeface(s) to PPTX "
                    f"({', '.join(unsafe)}): {font_family}"
                )
                break

    @staticmethod
    def _font_family_values(content: str) -> List[str]:
        """Extract SVG font-family values from attributes and inline styles."""
        from .checker import (
            SVGQualityChecker,
        )
        return SVGQualityChecker._svg_property_values(content, 'font-family')

    @staticmethod
    def _svg_property_values(content: str, property_name: str) -> List[str]:
        """Extract a SVG property from direct attributes and inline styles."""
        from .checker import (
            html,
            re,
        )
        values: List[str] = []
        attr_re = re.compile(
            rf'\b{re.escape(property_name)}\s*=\s*(["\'])(.*?)\1',
            re.IGNORECASE | re.DOTALL,
        )
        for match in attr_re.finditer(content):
            values.append(html.unescape(match.group(2)).strip())

        for match in re.finditer(r'\bstyle\s*=\s*(["\'])(.*?)\1', content, re.IGNORECASE | re.DOTALL):
            style_value = html.unescape(match.group(2))
            for part in style_value.split(';'):
                if ':' not in part:
                    continue
                name, value = part.split(':', 1)
                if name.strip().lower() == property_name.lower():
                    values.append(value.strip())
        return [value for value in values if value]

    def _check_text_elements(self, content: str, root: ET.Element, result: Dict):
        """Check text elements and wrapping methods"""
        # Count text and tspan elements
        text_count = content.count('<text')
        tspan_count = content.count('<tspan')

        result['info']['text_elements'] = text_count
        result['info']['tspan_elements'] = tspan_count

        self._check_module_bounds_contract(root, result)
        self._check_text_output_geometry(root, result)
        self._check_text_bounds(root, result)
        self._check_fragmented_paragraph_text(root, result)
        self._check_unmergeable_leading_text(root, result)
        self._check_nested_positional_tspans(root, result)

    def _check_hyperlinks(self, root: ET.Element, result: Dict) -> None:
        """Validate the standard SVG anchor surface shared with export."""
        from .checker import (
            _SHAPE_HYPERLINK_ATTR,
            _local_name,
            _project_hyperlink_errors,
        )
        anchors = [
            elem for elem in root.iter()
            if _local_name(elem) == 'a'
        ]
        transports = [
            elem for elem in root.iter()
            if elem.get(_SHAPE_HYPERLINK_ATTR) is not None
        ]
        if not anchors and not transports:
            return
        result['info']['hyperlinks'] = len(anchors) + len(transports)
        if _project_hyperlink_errors is None:
            result['errors'].append(
                'Unable to import hyperlink validator; cannot verify SVG links'
            )
            return
        result['errors'].extend(
            f'Invalid SVG hyperlink: {error}'
            for error in _project_hyperlink_errors(
                root,
                slide_count=None if self.partial_roster else self._active_slide_count,
            )
        )

    def _check_nested_positional_tspans(
        self,
        root: ET.Element,
        result: Dict,
    ) -> None:
        """Reject nested baseline jumps that DrawingML runs cannot represent."""
        from .checker import (
            _nested_positional_tspan_errors,
        )
        if _nested_positional_tspan_errors is None:
            return
        result['errors'].extend(_nested_positional_tspan_errors(root))

    @classmethod
    def _single_line_text_runs(
        cls,
        text_el: ET.Element,
    ) -> List[Tuple[ET.Element, str]] | None:
        """Return normalized inline runs, or ``None`` for positioned text."""
        raw_runs = cls._inline_text_segments(text_el, 'default')
        if raw_runs is None:
            return None
        return cls._normalize_source_text_runs(raw_runs)

    @classmethod
    def _inline_text_segments(
        cls,
        container: ET.Element,
        inherited_xml_space: str,
        *,
        include_container_dx: bool = False,
    ) -> List[Tuple[ET.Element, str, str]] | None:
        """Collect inline text and empty dx markers, rejecting baseline jumps."""
        from .checker import (
            ET,
            _local_name,
            _normalize_project_text_segments,
            _parse_svg_length,
            _resolve_project_xml_space,
        )
        if (
            _normalize_project_text_segments is None
            or _resolve_project_xml_space is None
            or _parse_svg_length is None
        ):
            return None
        raw_runs: List[Tuple[ET.Element, str, str]] = []

        def append_run(owner: ET.Element, raw: str, xml_space: str) -> None:
            if raw:
                raw_runs.append((owner, xml_space, raw))

        def collect(element: ET.Element, inherited: str) -> bool:
            try:
                xml_space = _resolve_project_xml_space(
                    element,
                    inherited,
                )
            except ValueError:
                return False
            if (
                cls._is_tspan(element)
                and element.get('dx') is not None
                and (element is not container or include_container_dx)
            ):
                try:
                    dx = _parse_svg_length(element.get('dx'))
                except ValueError:
                    return False
                if dx:
                    raw_runs.append((element, xml_space, ''))
            if element.text:
                append_run(element, element.text, xml_space)
            for child in list(element):
                # An inline hyperlink (``<a>`` wrapping ``<tspan>`` runs, the
                # form native-hyperlinks.md requires) is a transparent style
                # container: its runs are measured like any other inline run.
                if not (cls._is_tspan(child) or _local_name(child) == 'a'):
                    return False
                if any(child.get(name) is not None for name in ('x', 'y', 'dy')):
                    return False
                if child.get('dx') is not None and not cls._is_tspan(child):
                    return False
                if any(
                    name.startswith('data-paragraph-')
                    for name in child.attrib
                ):
                    return False
                if not collect(child, xml_space):
                    return False
                if child.tail:
                    append_run(element, child.tail, xml_space)
            return True

        if not collect(container, inherited_xml_space):
            return None
        return raw_runs

    @staticmethod
    def _normalize_source_text_runs(
        raw_runs: List[Tuple[ET.Element, str, str]],
    ) -> List[Tuple[ET.Element, str]]:
        """Normalize collected segments while retaining their style owner."""
        from .checker import (
            _normalize_project_text_segments,
        )
        normalized = dict(_normalize_project_text_segments([
            (xml_space, raw)
            for _owner, xml_space, raw in raw_runs
        ]))
        return [
            (owner, normalized.get(index, ''))
            for index, (owner, _xml_space, raw) in enumerate(raw_runs)
            if not raw or index in normalized
        ]

    @classmethod
    def _paragraph_line_text_runs(
        cls,
        text_el: ET.Element,
        line_group: List[ET.Element],
        synthetic_first: ET.Element | None,
    ) -> List[Tuple[ET.Element, str]] | None:
        """Return one classified visual line's normalized source runs."""
        from .checker import (
            _normalize_project_text_segments,
            _resolve_project_xml_space,
        )
        if (
            _normalize_project_text_segments is None
            or _resolve_project_xml_space is None
        ):
            return None
        try:
            parent_xml_space = _resolve_project_xml_space(text_el, 'default')
        except ValueError:
            return None

        raw_runs: List[Tuple[ET.Element, str, str]] = []
        for member in line_group:
            if member is synthetic_first:
                if member.text:
                    raw_runs.append((text_el, parent_xml_space, member.text))
                continue
            member_runs = cls._inline_text_segments(
                member,
                parent_xml_space,
                include_container_dx=member is not line_group[0],
            )
            if member_runs is None:
                return None
            raw_runs.extend(member_runs)
            if member.tail:
                raw_runs.append((text_el, parent_xml_space, member.tail))
        return cls._normalize_source_text_runs(raw_runs)

    @staticmethod
    def _unchanged_txbody_group_ids(
        root: ET.Element,
    ) -> set[int]:
        """Return imported shape groups whose original text body will survive."""
        from .checker import (
            SVG_NS,
            _SvgNativeConversionError,
            _preserved_native_text_body,
        )
        if _preserved_native_text_body is None:
            return set()
        unchanged: set[int] = set()
        for group in root.iter(f'{{{SVG_NS}}}g'):
            try:
                if _preserved_native_text_body(
                    group,
                    trust_runtime_snapshot=False,
                ) is not None:
                    unchanged.add(id(group))
            except _SvgNativeConversionError:
                # The dedicated txBody contract check owns the diagnostic.
                continue
        return unchanged

    @staticmethod
    def _check_preserved_txbody_contract(
        root: ET.Element,
        result: Dict,
    ) -> None:
        """Validate imported txBody payloads independently of text geometry."""
        from .checker import (
            SVG_NS,
            _SvgNativeConversionError,
            _element_label,
            _preserved_native_text_body,
        )
        if _preserved_native_text_body is None:
            return
        errors: set[str] = set()
        for group in root.iter(f'{{{SVG_NS}}}g'):
            try:
                _preserved_native_text_body(
                    group,
                    trust_runtime_snapshot=False,
                )
            except _SvgNativeConversionError as exc:
                errors.add(
                    f'{_element_label(group)} cannot preserve source '
                    f'txBody: {exc}'
                )
        result['errors'].extend(sorted(errors))

    @staticmethod
    def _has_ancestor_id(
        elem: ET.Element,
        parent_by_id: Dict[int, ET.Element],
        ancestor_ids: set[int],
    ) -> bool:
        current = parent_by_id.get(id(elem))
        while current is not None:
            if id(current) in ancestor_ids:
                return True
            current = parent_by_id.get(id(current))
        return False

    @classmethod
    def _resolved_single_line_text_runs(
        cls,
        text_el: ET.Element,
        parent_by_id: Dict[int, ET.Element],
        font_sizes: Dict[int, float],
        letter_spacings: Dict[int, float],
    ) -> List[Dict] | None:
        """Resolve the same run metrics used by generated text-frame sizing."""
        source_runs = cls._single_line_text_runs(text_el)
        if source_runs is None:
            return None
        return cls._resolved_text_runs(
            source_runs,
            parent_by_id,
            font_sizes,
            letter_spacings,
        )

    @classmethod
    def _resolved_text_runs(
        cls,
        source_runs: List[Tuple[ET.Element, str]],
        parent_by_id: Dict[int, ET.Element],
        font_sizes: Dict[int, float],
        letter_spacings: Dict[int, float],
    ) -> List[Dict]:
        """Resolve run metrics shared by single and classified text lines."""
        from .checker import (
            _INLINE_FORMULA_ATTR,
            _effective_presentation_value,
            _parse_inline_style,
            _parse_project_font_weight,
            _parse_svg_length,
        )
        resolved: List[Dict] = []
        for owner, text in source_runs:
            raw_weight = (
                _effective_presentation_value(
                    owner,
                    'font-weight',
                    parent_by_id,
                )
                or 'normal'
            ).strip().lower()
            weight = _parse_project_font_weight(raw_weight).canonical
            family = (
                _effective_presentation_value(
                    owner,
                    'font-family',
                    parent_by_id,
                )
                or ''
            )
            opacity_chain: List[str] = []
            current: ET.Element | None = owner
            while current is not None:
                style_values = (
                    _parse_inline_style(current.get('style'))
                    if _parse_inline_style is not None else {}
                )
                raw_opacity = style_values.get('opacity')
                if raw_opacity is None:
                    raw_opacity = current.get('opacity')
                if raw_opacity is not None:
                    opacity_chain.append(raw_opacity.strip())
                current = parent_by_id.get(id(current))
            resolved.append({
                'owner': owner,
                'text': text,
                'font_size': font_sizes[id(owner)],
                'font_weight': weight,
                'font_family': family,
                'letter_spacing': letter_spacings[id(owner)],
                'font_style': _effective_presentation_value(
                    owner,
                    'font-style',
                    parent_by_id,
                ) or 'normal',
                'text_decoration': _effective_presentation_value(
                    owner,
                    'text-decoration',
                    parent_by_id,
                ) or 'none',
                'fill_raw': _effective_presentation_value(
                    owner,
                    'fill',
                    parent_by_id,
                ) or '#000000',
                'fill_opacity': _effective_presentation_value(
                    owner,
                    'fill-opacity',
                    parent_by_id,
                ) or '1',
                'stroke_raw': _effective_presentation_value(
                    owner,
                    'stroke',
                    parent_by_id,
                ) or 'none',
                'stroke_width': _effective_presentation_value(
                    owner,
                    'stroke-width',
                    parent_by_id,
                ) or '1',
                'stroke_opacity': _effective_presentation_value(
                    owner,
                    'stroke-opacity',
                    parent_by_id,
                ) or '1',
                'opacity_chain': tuple(reversed(opacity_chain)),
                'inline_formula': owner.get(_INLINE_FORMULA_ATTR),
            })
            if not text:
                resolved[-1]['_inline_dx'] = _parse_svg_length(
                    owner.get('dx'),
                    font_size=font_sizes[id(owner)],
                )
                resolved[-1]['inline_formula'] = None
        return cls._coalesce_checker_text_runs(resolved)

    @staticmethod
    def _coalesce_checker_text_runs(runs: List[Dict]) -> List[Dict]:
        """Join only runs whose resolved source styles are provably equal."""
        from .checker import (
            Dict,
            Tuple,
            _detect_text_lang,
        )
        if _detect_text_lang is None:
            return runs
        style_keys = (
            'font_size',
            'font_weight',
            'font_family',
            'letter_spacing',
            'font_style',
            'text_decoration',
            'fill_raw',
            'fill_opacity',
            'stroke_raw',
            'stroke_width',
            'stroke_opacity',
            'opacity_chain',
        )

        def signature(run: Dict) -> Tuple:
            return (
                _detect_text_lang(str(run.get('text', ''))),
                *(run.get(key) for key in style_keys),
            )

        merged: List[Dict] = []
        previous_signature: Tuple | None = None
        for run in runs:
            if run.get('inline_formula') is not None or '_inline_dx' in run:
                merged.append(run)
                previous_signature = None
                continue
            current_signature = signature(run)
            if merged and current_signature == previous_signature:
                candidate = {
                    **merged[-1],
                    'text': (
                        str(merged[-1].get('text', ''))
                        + str(run.get('text', ''))
                    ),
                }
                candidate_signature = signature(candidate)
                if candidate_signature == previous_signature:
                    merged[-1] = candidate
                    previous_signature = candidate_signature
                    continue
            merged.append(run)
            previous_signature = current_signature
        return merged

    @staticmethod
    def _text_line_vertical_extent(
        runs: List[Dict],
        font_size: float,
    ) -> Tuple[float, float]:
        """Return native-math-aware ascent/descent for checker bounds."""
        from .checker import (
            _estimate_inline_formula_vertical_extent,
        )
        ascent = font_size * 0.85
        descent = font_size * 0.35
        if _estimate_inline_formula_vertical_extent is None:
            return ascent, descent
        for run in runs:
            latex = run.get('inline_formula')
            if latex is None:
                continue
            try:
                run_font_size = float(run.get('font_size', font_size))
                extent = _estimate_inline_formula_vertical_extent(str(latex))
            except (TypeError, ValueError):
                continue
            ascent = max(ascent, run_font_size * extent.ascent_em)
            descent = max(descent, run_font_size * extent.descent_em)
        return ascent, descent

