"""Pinned PPT Master quality gate component.

MIT Copyright (c) 2025-2026 Hugo He.
Upstream 7e0df6705de0cd24345210d31f07fcb5a32b6900.
"""
from __future__ import annotations
from pathlib import Path
from typing import Dict, List, Tuple
from xml.etree import ElementTree as ET

class CarriersQualityMixin:
    """Upstream carriers checker methods."""

    def _check_canonical_authoring(
        self,
        root: ET.Element,
        result: Dict,
    ) -> None:
        """Report SVG that was not compact when authored (advisory).

        The exporter accepts explicit declarations, so drift from the compact
        form never blocks. ``compact_svg_styles.py --inplace`` applies the
        deterministic normalization on request for authored project pages; it
        is not applied to structured template rosters, where per-slide
        compaction would make shared Master/Layout atoms diverge and shift
        native fallback hashes. Mirror materialization compacts its own tree
        before publication.
        """
        from .checker import (
            canonical_authoring_errors,
        )
        if not self.canonical_authoring:
            return
        errors = canonical_authoring_errors(
            root,
            # Authored-preset/native frames can intentionally retain the
            # helper's exact precision. Imported projections compact their
            # model-facing frames before publication, where provenance is
            # still known.
            compact_native_frames=False,
        )
        if not errors:
            return
        if self.template_mode:
            result['warnings'].extend(
                f"Noncanonical compact authoring: {error} "
                "(advisory; structured rosters keep their explicit form)"
                for error in errors
            )
            return
        def normalizer(error: str) -> str:
            if "page-space metadata" in error:
                return (
                    "`python3 scripts/compact_svg_coordinates.py <svg_output> "
                    "--inplace --keep-native-frames`"
                )
            return "`python3 scripts/compact_svg_styles.py <svg_output> --inplace`"

        result['warnings'].extend(
            f"Noncanonical compact authoring: {error} "
            f"(advisory; normalize with {normalizer(error)}, "
            "re-stamp pages that carry Chart/Table fallbacks, and rerun the "
            "final gate, or leave the explicit form)"
            for error in errors
        )

    def _parse_xml_root(self, content: str, result: Dict) -> ET.Element | None:
        """Parse the SVG content as well-formed XML.

        SVG is strict XML.  AI-generated decks frequently produce content that
        looks fine in HTML5-tolerant previews but fails strict XML parsing —
        common causes are HTML named entities (&nbsp; &mdash; &copy;…) and
        bare XML reserved characters in text (R&D, error < 5%).  Such pages
        cannot be exported to PPTX, so we surface them here as a hard error
        before any downstream check looks at them.

        Returns the parsed root when the document is well-formed; otherwise
        appends an error and returns None.
        """
        from .checker import (
            ET,
        )
        try:
            return ET.fromstring(content)
        except ET.ParseError as e:
            result['errors'].append(
                f"Invalid XML: {e} — SVG must be well-formed XML. "
                f"Use raw Unicode for typography (—, ©, →, NBSP); "
                f"escape XML reserved chars as &amp; &lt; &gt; &quot; &apos; "
                f"(see references/shared-standards-core.md §1)."
            )
            return None

    def _check_viewbox(
        self,
        root: ET.Element,
        svg_path: Path,
        result: Dict,
        expected_format: str = None,
        *,
        expected_viewbox: str | None = None,
        expected_viewbox_label: str = "expected canvas",
    ):
        """Validate the root page canvas and its project-level locks."""
        from .checker import (
            CANVAS_FORMATS,
            CanvasContractError,
            parse_project_svg_root,
            parse_project_viewbox,
        )
        viewbox = root.get('viewBox')
        try:
            parsed = parse_project_svg_root(
                root,
                context=svg_path.name,
            )
        except CanvasContractError as exc:
            result['errors'].append(str(exc))
            return
        assert viewbox is not None
        result['info']['viewbox'] = viewbox
        if viewbox != parsed.canonical or not parsed.has_integer_dimensions:
            if parsed.has_integer_dimensions:
                recommendation = f'write viewBox="{parsed.canonical}"'
            else:
                recommendation = (
                    "fractional dimensions are reserved for compatible imported "
                    "custom slide sizes; new authoring uses integer pixels"
                )
            result['warnings'].append(
                f"Compatible non-canonical root viewBox {viewbox!r}; {recommendation}."
            )

        contracts: list[tuple[str, str]] = []
        if expected_viewbox is not None:
            contracts.append((expected_viewbox_label, expected_viewbox))
        elif not self.template_mode:
            lock = self._get_spec_lock(svg_path)
            if lock is not None and 'canvas' in lock:
                locked_viewbox = lock.get('canvas', {}).get('viewBox')
                if not locked_viewbox:
                    result['errors'].append(
                        "spec_lock.md canvas section must declare viewBox"
                    )
                else:
                    contracts.append(("spec_lock canvas", locked_viewbox))

        if expected_format and expected_format in CANVAS_FORMATS:
            contracts.append((
                f"canvas format {expected_format!r}",
                CANVAS_FORMATS[expected_format]['viewbox'],
            ))
        elif expected_format:
            result['errors'].append(f"Unsupported canvas format: {expected_format}")

        seen_contracts: set[tuple[str, str]] = set()
        for label, raw_expected in contracts:
            contract_key = (label, raw_expected)
            if contract_key in seen_contracts:
                continue
            seen_contracts.add(contract_key)
            try:
                expected = parse_project_viewbox(
                    raw_expected,
                    context=f"{label} viewBox",
                )
            except CanvasContractError as exc:
                result['errors'].append(str(exc))
                continue
            if parsed != expected:
                result['errors'].append(
                    f"viewBox mismatch: {label} requires '{expected.canonical}', "
                    f"got '{parsed.canonical}'"
                )

    def _check_image_contract(
        self,
        root: ET.Element,
        svg_path: Path,
        result: Dict,
    ) -> None:
        """Validate picture frames, references, and bytes before export."""
        from .checker import (
            _project_image_errors,
        )
        if _project_image_errors is None:
            result['errors'].append(
                'Unable to import the image validator; cannot verify picture '
                'frames or media'
            )
            return
        _working_root, _parent_by_id, images = self._visible_image_elements(root)
        for image in images:
            result['errors'].extend(
                _project_image_errors(
                    image,
                    svg_path.parent,
                    allow_template_placeholders=self.template_mode,
                )
            )

    def _record_carrier_receipt(
        self,
        root: ET.Element,
        result: Dict,
    ) -> None:
        """Record factual visible-carrier use without grading the design."""
        from .checker import (
            Counter,
            _INLINE_FORMULA_ATTR,
            _local_name,
            _parse_inline_style,
        )
        parent_by_id = {
            id(child): parent
            for parent in root.iter()
            for child in list(parent)
        }
        geometry_tags = (
            'rect',
            'circle',
            'ellipse',
            'line',
            'polyline',
            'polygon',
            'path',
        )
        geometry_counts = Counter({tag: 0 for tag in geometry_tags})
        preset_names: Counter[str] = Counter()
        native_objects = Counter({
            'chart': 0,
            'table': 0,
            'formula_block': 0,
            'formula_inline': 0,
            'other': 0,
        })
        marker_counts = Counter({'start': 0, 'mid': 0, 'end': 0})
        text_count = 0
        icon_count = 0
        page_frame_geometry = 0

        for element in root.iter():
            if (
                element is root
                or self._is_hidden_element(element, parent_by_id)
                or self._has_non_visual_ancestor(element, root, parent_by_id)
                or self._has_zero_opacity(element, parent_by_id)
            ):
                continue

            tag = _local_name(element)
            if tag == 'text':
                text_count += 1
            elif tag == 'use' and element.get('data-icon') is not None:
                icon_count += 1

            if element.get(_INLINE_FORMULA_ATTR) is not None:
                native_objects['formula_inline'] += 1
            replacement_kind = self._carrier_native_replacement_kind(element)
            if replacement_kind:
                key = (
                    'formula_block'
                    if replacement_kind == 'formula'
                    else replacement_kind
                )
                native_objects[key if key in native_objects else 'other'] += 1

            preset = (element.get('data-pptx-prst') or '').strip()
            if preset:
                preset_names[preset] += 1
                if self._carrier_page_frame_role(element, root, parent_by_id):
                    page_frame_geometry += 1
                continue
            if tag not in geometry_counts or self._has_preset_ancestor(
                element,
                root,
                parent_by_id,
            ):
                continue

            geometry_counts[tag] += 1
            if self._carrier_page_frame_role(element, root, parent_by_id):
                page_frame_geometry += 1
            style_values = (
                _parse_inline_style(element.get('style'))
                if _parse_inline_style is not None
                else {}
            )
            for position in ('start', 'mid', 'end'):
                raw_marker = (
                    style_values.get(f'marker-{position}')
                    or element.get(f'marker-{position}')
                    or ''
                ).strip().lower()
                if raw_marker and raw_marker != 'none':
                    marker_counts[position] += 1

        image_receipt = self._carrier_image_receipt(root)
        result['info']['carrier_receipt'] = {
            'text_elements': text_count,
            'images': image_receipt,
            'icons': icon_count,
            'effects': self._carrier_effect_receipt(root, parent_by_id),
            'geometry': {
                'svg_elements': dict(geometry_counts),
                'preset_shapes': sum(preset_names.values()),
                'preset_names': dict(sorted(preset_names.items())),
                'page_frame_elements': page_frame_geometry,
                'marker_uses': dict(marker_counts),
            },
            'native_objects': dict(native_objects),
        }

    @classmethod
    def _carrier_effect_receipt(
        cls,
        root: ET.Element,
        parent_by_id: Dict[int, ET.Element],
    ) -> Dict:
        """Count factual visible effect declarations and resolved references."""
        from .checker import (
            Counter,
            Dict,
            ET,
            _local_name,
            _parse_inline_style,
            re,
        )
        ignored_tags = frozenset({
            'clippath',
            'defs',
            'marker',
            'mask',
            'pattern',
            'symbol',
        })
        emphasis_properties = (
            'fill',
            'font-weight',
            'font-size',
            'font-style',
            'text-decoration',
            'letter-spacing',
        )
        definition_kinds: Dict[str, str] = {}
        for element in root.iter():
            definition_id = (element.get('id') or '').strip()
            if definition_id:
                definition_kinds[definition_id] = _local_name(element).casefold()

        def declared_value(
            element: ET.Element,
            style_values: Dict[str, str],
            name: str,
        ) -> str | None:
            if name in style_values:
                return style_values[name]
            return element.get(name)

        def reference_kind(value: str | None) -> str:
            match = re.fullmatch(
                r'url\(\s*#([^)]+?)\s*\)',
                (value or '').strip(),
                re.IGNORECASE,
            )
            return definition_kinds.get(match.group(1), '') if match else ''

        def has_ignored_ancestor(element: ET.Element) -> bool:
            current: ET.Element | None = element
            while current is not None and current is not root:
                if _local_name(current).casefold() in ignored_tags:
                    return True
                current = parent_by_id.get(id(current))
            return False

        effects = Counter({
            'inline_emphasis_runs': 0,
            'gradient_uses': 0,
            'filter_uses': 0,
            'text_effects': 0,
        })
        gradient_kinds = {'lineargradient', 'radialgradient'}
        text_paint_kinds = gradient_kinds | {'pattern'}

        for element in root.iter():
            if (
                element is root
                or has_ignored_ancestor(element)
                or cls._has_non_visual_ancestor(element, root, parent_by_id)
                or cls._is_hidden_element(element, parent_by_id)
                or cls._has_zero_opacity(element, parent_by_id)
            ):
                continue

            style_values = (
                _parse_inline_style(element.get('style'))
                if _parse_inline_style is not None
                else {}
            )
            fill_kind = reference_kind(
                declared_value(element, style_values, 'fill')
            )
            raw_stroke = declared_value(element, style_values, 'stroke')
            stroke_kind = reference_kind(raw_stroke)
            effects['gradient_uses'] += sum(
                kind in gradient_kinds for kind in (fill_kind, stroke_kind)
            )

            raw_filter = declared_value(element, style_values, 'filter')
            if reference_kind(raw_filter) == 'filter':
                effects['filter_uses'] += 1

            tag = _local_name(element).casefold()
            if tag == 'tspan':
                current = parent_by_id.get(id(element))
                inside_text = False
                while current is not None:
                    if _local_name(current).casefold() == 'text':
                        inside_text = True
                        break
                    current = parent_by_id.get(id(current))
                if (
                    inside_text
                    and not any(
                        element.get(name) is not None
                        for name in ('x', 'y', 'dx', 'dy')
                    )
                    and any(
                        name in style_values or element.get(name) is not None
                        for name in emphasis_properties
                    )
                ):
                    effects['inline_emphasis_runs'] += 1

            if tag in {'text', 'tspan'}:
                has_filter = (
                    'filter' in style_values
                    or element.get('filter') is not None
                )
                has_stroke = bool(
                    raw_stroke
                    and raw_stroke.strip()
                    and raw_stroke.strip().casefold() != 'none'
                )
                if (
                    fill_kind in text_paint_kinds
                    or stroke_kind in text_paint_kinds
                    or has_filter
                    or has_stroke
                ):
                    effects['text_effects'] += 1

        return dict(effects)

    @staticmethod
    def _carrier_native_replacement_kind(element: ET.Element) -> str:
        """Return one native replacement kind without turning bad data into a check."""
        from .checker import (
            _native_replacement_kind,
        )
        if _native_replacement_kind is not None:
            try:
                return (_native_replacement_kind(element) or '').strip().lower()
            except ValueError:
                pass
        return (
            element.get('data-pptx-replace-with')
            or element.get('data-pptx-native')
            or ''
        ).strip().lower()

    @staticmethod
    def _has_preset_ancestor(
        element: ET.Element,
        root: ET.Element,
        parent_by_id: Dict[int, ET.Element],
    ) -> bool:
        """Return whether geometry is only the visible detail of a preset atom."""
        current = parent_by_id.get(id(element))
        while current is not None and current is not root:
            if (current.get('data-pptx-prst') or '').strip():
                return True
            current = parent_by_id.get(id(current))
        return False

    @staticmethod
    def _carrier_page_frame_role(
        element: ET.Element,
        root: ET.Element,
        parent_by_id: Dict[int, ET.Element],
    ) -> bool:
        """Return whether an element belongs to declared page framing."""
        current: ET.Element | None = element
        while current is not None:
            role = (current.get('data-pptx-role') or '').strip().lower()
            if role in {'background', 'decoration'}:
                return True
            if current is root:
                break
            current = parent_by_id.get(id(current))
        return False

    def _carrier_image_receipt(self, root: ET.Element) -> Dict:
        """Summarize visible image placements and their frame share."""
        from .checker import (
            Path,
            XLINK_NS,
            _local_name,
            _parse_viewbox_values,
            unquote,
            urlsplit,
        )
        working_root, parent_by_id, images = self._visible_image_elements(root)
        viewbox = _parse_viewbox_values(working_root.get('viewBox') or '')
        canvas_area = (
            abs(viewbox[2] * viewbox[3])
            if viewbox is not None and viewbox[2] and viewbox[3]
            else 0.0
        )
        frame_shares: List[float] = []
        filenames = set()

        for image in images:
            href = image.get('href') or image.get(f'{{{XLINK_NS}}}href') or ''
            if href.startswith('data:'):
                filenames.add('(embedded)')
            elif href:
                path_name = Path(unquote(urlsplit(href).path)).name
                filenames.add(path_name or href[:80])

            display_owner = image
            parent = parent_by_id.get(id(image))
            if (
                parent is not None
                and parent is not working_root
                and _local_name(parent) == 'svg'
            ):
                display_owner = parent
            try:
                x = float(display_owner.get('x') or '0')
                y = float(display_owner.get('y') or '0')
                width = float(display_owner.get('width') or '0')
                height = float(display_owner.get('height') or '0')
            except (TypeError, ValueError):
                continue
            if width <= 0 or height <= 0 or canvas_area <= 0:
                continue
            transformed = self._transformed_rect_edge_lengths(
                display_owner,
                (x, y, width, height),
                parent_by_id,
            )
            if transformed is not None:
                width, height = transformed
            frame_shares.append(abs(width * height) / canvas_area)

        return {
            'placements': len(images),
            'files': sorted(filenames),
            'max_frame_share': round(max(frame_shares), 4) if frame_shares else 0.0,
        }

