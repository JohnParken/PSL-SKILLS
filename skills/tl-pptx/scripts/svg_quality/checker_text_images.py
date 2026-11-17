"""Pinned PPT Master quality gate component.

MIT Copyright (c) 2025-2026 Hugo He.
Upstream 7e0df6705de0cd24345210d31f07fcb5a32b6900.
"""
from __future__ import annotations
from pathlib import Path
from typing import Dict, List, Tuple
from xml.etree import ElementTree as ET

class TextImagesQualityMixin:
    """Upstream text images checker methods."""

    def _check_unmergeable_leading_text(self, root: ET.Element, result: Dict) -> None:
        """Warn when leading text cannot be normalized into one PPT text frame."""
        from .checker import (
            SVG_NS,
        )
        risky = []
        for text_el in root.iter(f'{{{SVG_NS}}}text'):
            if not (text_el.text or "").strip():
                continue
            children = list(text_el)
            if not any(self._is_line_tspan(child) for child in children):
                continue

            reason = self._leading_text_normalizer_reject_reason(text_el)
            if reason is not None:
                risky.append(reason)

        if risky:
            sample = '; '.join(risky[:3])
            suffix = '' if len(risky) <= 3 else f"; +{len(risky) - 3} more"
            result['warnings'].append(
                "Detected multi-line <text> with leading direct text that cannot "
                f"be normalized into one PPT text frame ({sample}{suffix})"
            )

    def _check_fragmented_paragraph_text(
        self,
        root: ET.Element,
        result: Dict,
    ) -> None:
        """Warn on high-confidence prose lines split into sibling text frames."""
        from .checker import (
            Dict,
            ET,
            _PARAGRAPH_LINE_GAP_MAX_RATIO,
            _PARAGRAPH_LINE_GAP_MIN_RATIO,
            _PARAGRAPH_LINE_MIN_LONGEST_CHARS,
            _PARAGRAPH_LINE_MIN_TOTAL_CHARS,
            _PARAGRAPH_LINE_TERMINATOR_RE,
            _PARAGRAPH_LINE_X_TOLERANCE,
            _PARAGRAPH_LIST_MARKER_RE,
            _effective_presentation_value,
            _element_label,
            _local_name,
            _parse_project_geometry_length,
            _resolve_project_font_sizes,
            math,
            re,
        )
        helpers = (
            _parse_project_geometry_length,
            _resolve_project_font_sizes,
        )
        if any(helper is None for helper in helpers):
            return
        try:
            font_sizes = _resolve_project_font_sizes(root)
        except ValueError:
            return

        parent_by_id = {
            id(child): parent
            for parent in root.iter()
            for child in list(parent)
        }
        unchanged_groups = self._unchanged_txbody_group_ids(root)
        style_properties = (
            'fill',
            'fill-opacity',
            'font-family',
            'font-style',
            'font-weight',
            'letter-spacing',
            'opacity',
            'stroke',
            'stroke-opacity',
            'stroke-width',
            'text-decoration',
        )

        def line_record(element: ET.Element) -> Dict | None:
            if (
                _local_name(element) != 'text'
                or list(element)
                or element.get('x') is None
                or element.get('y') is None
                or any(element.get(name) is not None for name in ('dx', 'dy'))
                or element.get('transform') is not None
                or self._is_hidden_element(element, parent_by_id)
                or self._has_ancestor_id(
                    element,
                    parent_by_id,
                    unchanged_groups,
                )
            ):
                return None
            text = (element.text or '').strip()
            compact_text = re.sub(r'\s+', '', text)
            if (
                not compact_text
                or ('{{' in text and '}}' in text)
                or _PARAGRAPH_LIST_MARKER_RE.match(text)
            ):
                return None
            anchor = (
                _effective_presentation_value(
                    element,
                    'text-anchor',
                    parent_by_id,
                )
                or 'start'
            ).strip().lower()
            if anchor != 'start':
                return None
            try:
                x = _parse_project_geometry_length(element.get('x'), 'x')
                y = _parse_project_geometry_length(element.get('y'), 'y')
                font_size = float(font_sizes[id(element)])
            except (KeyError, TypeError, ValueError):
                return None
            if font_size <= 0:
                return None
            style = tuple(
                (
                    _effective_presentation_value(
                        element,
                        name,
                        parent_by_id,
                    )
                    or ''
                ).strip().lower()
                for name in style_properties
            )
            return {
                'chars': len(compact_text),
                'font_size': font_size,
                'style': style,
                'text': text,
                'x': x,
                'y': y,
            }

        suspects: List[str] = []
        for group in list(root):
            if (
                _local_name(group) != 'g'
                or self._is_hidden_element(group, parent_by_id)
            ):
                continue
            current_run: List[Dict] = []

            def flush_run() -> None:
                if len(current_run) < 2:
                    return
                total_chars = sum(line['chars'] for line in current_run)
                longest_line = max(line['chars'] for line in current_run)
                if (
                    total_chars < _PARAGRAPH_LINE_MIN_TOTAL_CHARS
                    or longest_line < _PARAGRAPH_LINE_MIN_LONGEST_CHARS
                ):
                    return
                first = current_run[0]
                last = current_run[-1]
                suspects.append(
                    f'{_element_label(group)} x={first["x"]:.1f}, '
                    f'y={first["y"]:.1f}..{last["y"]:.1f}, '
                    f'{len(current_run)} lines'
                )

            for child in list(group):
                line = line_record(child)
                if line is None:
                    flush_run()
                    current_run = []
                    continue
                if current_run:
                    previous = current_run[-1]
                    line_gap = line['y'] - previous['y']
                    same_frame = (
                        abs(line['x'] - previous['x'])
                        <= _PARAGRAPH_LINE_X_TOLERANCE
                        and line['style'] == previous['style']
                        and math.isclose(
                            line['font_size'],
                            previous['font_size'],
                            rel_tol=0.0,
                            abs_tol=1e-6,
                        )
                        and line_gap
                        >= line['font_size'] * _PARAGRAPH_LINE_GAP_MIN_RATIO
                        and line_gap
                        <= line['font_size'] * _PARAGRAPH_LINE_GAP_MAX_RATIO
                        and not _PARAGRAPH_LINE_TERMINATOR_RE.search(
                            previous['text']
                        )
                    )
                    if not same_frame:
                        flush_run()
                        current_run = []
                current_run.append(line)
            flush_run()

        if not suspects:
            return
        sample = '; '.join(suspects[:3])
        suffix = '' if len(suspects) <= 3 else f'; +{len(suspects) - 3} more'
        result['warnings'].append(
            f'Detected {len(suspects)} paragraph-like line run(s) split '
            f'across sibling <text> elements ({sample}{suffix}). If each run '
            'is one prose paragraph, combine it into one <text>: keep its '
            'first line as direct text and use direct <tspan> children with '
            'the parent x and positive relative dy values for later lines. '
            'An all-<tspan> form may start with dy="0". Keep semantically '
            'independent text frames separate.'
        )

    @staticmethod
    def _is_tspan(elem: ET.Element) -> bool:
        from .checker import (
            SVG_NS,
        )
        return elem.tag == f'{{{SVG_NS}}}tspan'

    @classmethod
    def _is_line_tspan(cls, elem: ET.Element) -> bool:
        from .checker import (
            re,
        )
        if not cls._is_tspan(elem):
            return False
        if elem.get('x') is not None or elem.get('y') is not None:
            return True
        dy = elem.get('dy')
        if dy is None:
            return False
        try:
            return float(re.match(r'^[\s,]*([+-]?(?:\d+\.?\d*|\d*\.\d+))', dy).group(1)) != 0
        except (AttributeError, ValueError):
            return True

    @classmethod
    def _leading_text_normalizer_reject_reason(cls, text_el: ET.Element) -> str | None:
        if text_el.get('x') is None:
            return '<text> has no x anchor'

        children = list(text_el)
        if any(not cls._is_tspan(child) for child in children):
            return '<text> has non-tspan child'

        # Mirror svg_finalize.flatten_tspan: tail text after an inline run
        # stays on its line, but a line made of one positioned <tspan> has
        # nowhere to keep a tail.
        groups: list[list[ET.Element]] = [[]]
        for child in children:
            if cls._is_line_tspan(child):
                groups.append([child])
            else:
                groups[-1].append(child)
        for group in groups[1:]:
            if len(group) == 1 and (group[0].tail or "").strip():
                return (
                    'text follows a positioned line <tspan> directly; '
                    'nest it inside that <tspan>'
                )

        return None

    def _check_image_references(self, root: ET.Element, svg_path: Path, result: Dict):
        """Check image file existence and effective rendered resolution."""
        from .checker import (
            IMAGE_DOWNSIZE_WARN_MIN_BYTES,
            IMAGE_DOWNSIZE_WARN_RATIO,
            IMAGE_UPSCALE_WARN_RATIO,
            SVG_NS,
            XLINK_NS,
            _parse_project_image_aspect_ratio,
            _resolve_external_image_reference,
        )
        svg_dir = svg_path.parent
        working_root, parent_by_id, images = self._visible_image_elements(root)

        for image in images:
            href = image.get('href') or image.get(f'{{{XLINK_NS}}}href')
            if not href or href.startswith('data:'):
                continue
            if self.template_mode and '{{' in href and '}}' in href:
                continue
            if _resolve_external_image_reference is None:
                result['warnings'].append(
                    "Detected image references, but shared image resolver could not be imported; "
                    "export will still validate them."
                )
                return

            img_path = _resolve_external_image_reference(svg_dir, href)
            if img_path is None:
                # The shared image-source contract already reports the
                # blocking resolution failure. This pass adds quality advice
                # only for valid, resolved images.
                continue

            # Check resolution vs display size
            display_owner = image
            parent = parent_by_id.get(id(image))
            if (
                parent is not None
                and parent is not working_root
                and parent.tag == f'{{{SVG_NS}}}svg'
            ):
                # Imported crops use a unit-frame inner image. Quality advice
                # must compare the source against the visible outer frame.
                display_owner = parent
            display_w_str = display_owner.get('width')
            display_h_str = display_owner.get('height')
            if not display_w_str or not display_h_str:
                continue

            try:
                display_x = float(display_owner.get('x') or '0')
                display_y = float(display_owner.get('y') or '0')
                local_display_w = float(display_w_str)
                local_display_h = float(display_h_str)
            except (ValueError, TypeError):
                continue
            if local_display_w <= 0 or local_display_h <= 0:
                continue
            display_w = local_display_w
            display_h = local_display_h
            transformed_size = self._transformed_rect_edge_lengths(
                display_owner,
                (display_x, display_y, local_display_w, local_display_h),
                parent_by_id,
            )
            if transformed_size is not None:
                display_w, display_h = transformed_size
            axis_scale_x = display_w / local_display_w
            axis_scale_y = display_h / local_display_h

            try:
                from PIL import Image as PILImage, ImageOps
                with PILImage.open(img_path) as img:
                    actual_w, actual_h = ImageOps.exif_transpose(img).size
                source_bytes = img_path.stat().st_size

                visible_w = float(actual_w)
                visible_h = float(actual_h)
                fit_owner = image
                if display_owner is not image:
                    fit_owner = display_owner
                    viewbox = (display_owner.get('viewBox') or '').split()
                    if len(viewbox) == 4:
                        try:
                            viewbox_w = float(viewbox[2])
                            viewbox_h = float(viewbox[3])
                        except ValueError:
                            pass
                        else:
                            if 0 < viewbox_w <= 1 and 0 < viewbox_h <= 1:
                                visible_w *= viewbox_w
                                visible_h *= viewbox_h

                raw_aspect = fit_owner.get('preserveAspectRatio')
                try:
                    align, mode = (
                        _parse_project_image_aspect_ratio(raw_aspect)
                        if _parse_project_image_aspect_ratio is not None
                        else ('xMidYMid', 'meet')
                    )
                except ValueError:
                    continue

                local_scale_x = local_display_w / visible_w
                local_scale_y = local_display_h / visible_h
                if align == 'none':
                    render_scale = max(
                        local_scale_x * axis_scale_x,
                        local_scale_y * axis_scale_y,
                    )
                    fit_label = 'none'
                elif mode == 'slice':
                    render_scale = (
                        max(local_scale_x, local_scale_y)
                        * max(axis_scale_x, axis_scale_y)
                    )
                    fit_label = 'slice'
                else:
                    render_scale = (
                        min(local_scale_x, local_scale_y)
                        * max(axis_scale_x, axis_scale_y)
                    )
                    fit_label = 'meet'

                if render_scale > IMAGE_UPSCALE_WARN_RATIO:
                    result['warnings'].append(
                        f"Image {href} is {actual_w}x{actual_h} and renders at "
                        f"{render_scale:.2f}x scale in a "
                        f"{int(display_w)}x{int(display_h)} {fit_label} frame "
                        f"— about {render_scale * 1.5:.1f}x on a 1080p projector, "
                        "visibly soft; use a larger source or a smaller frame"
                    )
                elif (
                    render_scale < 1.0 / IMAGE_DOWNSIZE_WARN_RATIO
                    and source_bytes >= IMAGE_DOWNSIZE_WARN_MIN_BYTES
                ):
                    source_mib = source_bytes / (1024 * 1024)
                    result['warnings'].append(
                        f"Image {href} is {actual_w}x{actual_h} and renders at "
                        f"{render_scale:.2f}x scale in a "
                        f"{int(display_w)}x{int(display_h)} {fit_label} frame; "
                        f"the source is {source_mib:.1f} MiB — file-size "
                        "advisory only, not an aspect-ratio warning; consider "
                        "a smaller source asset, or export with "
                        "svg_to_pptx.py --image-sizing display to downsize "
                        "at export time"
                    )
            except ImportError:
                pass  # PIL not available, skip resolution check
            except Exception:
                pass  # Image unreadable, skip resolution check

    def _check_icon_placeholders(self, root: ET.Element, svg_path: Path, result: Dict) -> None:
        """Check that <use data-icon="..."> placeholders resolve."""
        from .checker import (
            ET,
            NativePayloadError,
            _icon_dir_for_svg,
            _local_name,
            _project_mask_errors,
            _resolve_icon_path,
            _suggest_icon_name,
            hydrate_native_payload_refs,
        )
        placeholders = [
            elem for elem in root.iter()
            if _local_name(elem).lower() == 'use' and elem.get('data-icon') is not None
        ]
        if not placeholders:
            return

        if _resolve_icon_path is None:
            result['warnings'].append(
                "Detected data-icon placeholders, but icon resolver could not be imported; "
                "post-processing/export will still validate them."
            )
            return
        if _icon_dir_for_svg is None:
            result['warnings'].append(
                "Detected data-icon placeholders, but the project icon helper could not be imported; "
                "post-processing/export will still validate them."
            )
            return

        icons_dir = _icon_dir_for_svg(svg_path)
        seen = set()
        for elem in placeholders:
            icon_name = (elem.get('data-icon') or '').strip()
            if not icon_name:
                result['errors'].append("Icon placeholder has empty data-icon value")
                continue
            if icon_name in seen:
                continue
            seen.add(icon_name)

            try:
                icon_path, _ = _resolve_icon_path(icon_name, icons_dir)
            except ValueError as exc:
                result['errors'].append(str(exc))
                continue
            if not icon_path.exists():
                suggestion = (
                    _suggest_icon_name(icon_name, icons_dir)
                    if _suggest_icon_name is not None else None
                )
                hint = (
                    f"; identifiers are case-sensitive; use '{suggestion}'"
                    if suggestion else ""
                )
                result['errors'].append(
                    f"Project-local icon not found: {icon_name} "
                    f"(expected under {icons_dir}){hint}"
                )
                continue
            try:
                icon_root = ET.parse(icon_path).getroot()
                hydrated = hydrate_native_payload_refs(icon_root, icon_path)
            except (OSError, ET.ParseError, NativePayloadError) as exc:
                result['errors'].append(
                    f"Icon {icon_name} has invalid native payload metadata: {exc}"
                )
                continue
            if _project_mask_errors is not None:
                result['errors'].extend(
                    f'Icon {icon_name}: {error}'
                    for error in _project_mask_errors(icon_root)
                )
            if hydrated:
                result['info']['native_icon_payload_refs'] = (
                    result['info'].get('native_icon_payload_refs', 0) + hydrated
                )

