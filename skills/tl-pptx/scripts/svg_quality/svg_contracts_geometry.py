"""Pinned PPT Master quality gate component.

MIT Copyright (c) 2025-2026 Hugo He.
Upstream 7e0df6705de0cd24345210d31f07fcb5a32b6900.
"""
from __future__ import annotations
from pathlib import Path
from typing import Dict, List, Tuple
from xml.etree import ElementTree as ET

def check_definition_contract(
    root: ET.Element,
    result: Dict,
) -> None:
    """Require conditional definitions to be direct, uniquely identified defs."""
    from .svg_contracts import (
        _project_definition_errors,
    )
    if _project_definition_errors is None:
        result['warnings'].append(
            "Unable to import the shared definition validator; native "
            "export will still validate local definitions."
        )
        return
    result['errors'].extend(_project_definition_errors(root))


def check_paint_reference_contract(
    root: ET.Element,
    result: Dict,
) -> None:
    """Validate paint-server resolution and native target contexts."""
    from .svg_contracts import (
        _project_paint_reference_errors,
    )
    if _project_paint_reference_errors is None:
        result['warnings'].append(
            "Unable to import the shared paint-reference validator; native "
            "export will still validate local paint references."
        )
        return
    result['errors'].extend(_project_paint_reference_errors(root))


def check_marker_contract(
    root: ET.Element,
    result: Dict,
) -> None:
    """Validate marker references against the native line-end contract."""
    from .svg_contracts import (
        _project_marker_errors,
    )
    if _project_marker_errors is None:
        result['warnings'].append(
            'Unable to import the shared marker validator; native export '
            'will still validate line-end markers.'
        )
        return
    result['errors'].extend(_project_marker_errors(root))


def check_clip_path_contract(
    root: ET.Element,
    result: Dict,
) -> None:
    """Validate image clip paths against the native picture geometry mapping."""
    from .svg_contracts import (
        _project_clip_path_errors,
    )
    if _project_clip_path_errors is None:
        result['errors'].append(
            'Unable to import the clip-path validator; cannot verify '
            'native picture geometry references'
        )
        return
    result['errors'].extend(_project_clip_path_errors(root))


def check_mask_contract(root: ET.Element, result: Dict) -> None:
    """Reject SVG masks through the native exporter's shared validator."""
    from .svg_contracts import (
        _project_mask_errors,
    )
    if _project_mask_errors is None:
        result['errors'].append(
            'Unable to import the shared mask validator; cannot verify '
            'that native PPTX export will preserve all visible effects'
        )
        return
    result['errors'].extend(_project_mask_errors(root))


def check_filter_effects(root: ET.Element, result: Dict) -> None:
    """Validate filters against the native shadow/glow approximation."""
    from .svg_contracts import (
        _project_filter_errors,
    )
    if _project_filter_errors is None:
        result['warnings'].append(
            "Unable to import the shared filter validator; native export "
            "will still validate shadow/glow filters."
        )
        return
    result['errors'].extend(_project_filter_errors(root))


def check_imported_effect_status(
    root: ET.Element,
    result: Dict,
) -> None:
    """Reject source PPTX effects that have no faithful SVG mapping."""
    from .svg_contracts import (
        _EFFECT_REASON_ATTR,
        _EFFECT_STATUS_ATTR,
        _project_effect_status_errors,
    )
    if _project_effect_status_errors is None:
        if any(
            elem.get(_EFFECT_STATUS_ATTR) is not None
            or elem.get(_EFFECT_REASON_ATTR) is not None
            for elem in root.iter()
        ):
            result['errors'].append(
                'Unable to import the PPTX effect-status validator; '
                'cannot verify imported effect fidelity'
            )
        return
    result['errors'].extend(_project_effect_status_errors(root))


def check_gradient_interfaces(root: ET.Element, result: Dict) -> None:
    """Validate the normalized native gradient authoring interface."""
    from .svg_contracts import (
        _UseExpansionError,
        _expand_local_use_references,
        _project_gradient_errors,
        _project_gradient_geometry_errors,
        copy,
    )
    if (
        _project_gradient_errors is None
        or _project_gradient_geometry_errors is None
    ):
        result['warnings'].append(
            "Unable to import the shared gradient validator; native export "
            "will still validate gradient definitions."
        )
        return
    gradient_errors = set(_project_gradient_errors(root))
    gradient_errors.update(_project_gradient_geometry_errors(root))
    if (
        _expand_local_use_references is not None
        and _UseExpansionError is not None
    ):
        expanded_root = copy.deepcopy(root)
        try:
            _expand_local_use_references(expanded_root)
        except _UseExpansionError:
            # The local-reference check owns the actionable diagnostic.
            pass
        else:
            gradient_errors.update(
                _project_gradient_geometry_errors(expanded_root)
            )
    result['errors'].extend(sorted(gradient_errors))


def check_geometry_length_values(
    root: ET.Element,
    result: Dict,
) -> None:
    """Reject invalid project geometry and advise the unitless spelling."""
    from .svg_contracts import (
        Counter,
        _element_label,
        _format_project_geometry_length,
        _is_canonical_project_geometry_length,
        _iter_project_geometry_lengths,
        _parse_project_geometry_length,
        defaultdict,
    )
    if (
        _format_project_geometry_length is None
        or _is_canonical_project_geometry_length is None
        or _iter_project_geometry_lengths is None
        or _parse_project_geometry_length is None
    ):
        result['warnings'].append(
            "Unable to import svg_to_pptx geometry length validators; "
            "native export will still validate project geometry."
        )
        return

    errors: set[str] = set()
    recommendations: Counter[tuple[str, str, str]] = Counter()
    examples: Dict[tuple[str, str, str], List[str]] = defaultdict(list)

    for elem, attribute, raw, source in _iter_project_geometry_lengths(root):
        label = f'{_element_label(elem)} {source}'
        try:
            value = _parse_project_geometry_length(raw, attribute)
        except ValueError as exc:
            errors.add(f"{label} {attribute}={raw!r}: {exc}")
            continue
        if _is_canonical_project_geometry_length(raw):
            continue
        normalized = _format_project_geometry_length(value)
        key = (attribute, raw, normalized)
        recommendations[key] += 1
        if label not in examples[key] and len(examples[key]) < 3:
            examples[key].append(label)

    result['errors'].extend(sorted(errors))
    for (attribute, raw, normalized), count in sorted(recommendations.items()):
        shown_examples = ', '.join(examples[(attribute, raw, normalized)])
        result['warnings'].append(
            f"Recommendation: project geometry {attribute}={raw!r} is "
            f"converter-compatible in {count} location(s) ({shown_examples}); "
            f"generated SVG should prefer the unitless px spelling "
            f'{attribute}="{normalized}". No change is required for export.'
        )


def check_stroke_style_values(
    root: ET.Element,
    result: Dict,
) -> None:
    """Reject invalid line styles and advise project-canonical spellings."""
    from .svg_contracts import (
        Counter,
        _element_label,
        _format_project_geometry_length,
        _is_canonical_project_geometry_length,
        _iter_project_stroke_styles,
        _noncanonical_stroke_dash_numbers,
        _parse_project_geometry_length,
        _parse_project_stroke_dasharray,
        _parse_project_stroke_enum,
        _project_stroke_style_errors,
        defaultdict,
    )
    helpers = (
        _format_project_geometry_length,
        _is_canonical_project_geometry_length,
        _iter_project_stroke_styles,
        _noncanonical_stroke_dash_numbers,
        _parse_project_geometry_length,
        _parse_project_stroke_dasharray,
        _parse_project_stroke_enum,
        _project_stroke_style_errors,
    )
    if any(helper is None for helper in helpers):
        result['warnings'].append(
            "Unable to import svg_to_pptx line-style validators; native "
            "export will still validate line-presentation syntax."
        )
        return

    result['errors'].extend(_project_stroke_style_errors(root))
    recommendations: Counter[tuple[str, str, str, str]] = Counter()
    examples: Dict[tuple[str, str, str, str], List[str]] = defaultdict(list)

    for elem, attribute, raw, source in _iter_project_stroke_styles(root):
        label = f'{_element_label(elem)} {source}'
        normalized = None
        reason = ''

        if attribute == 'stroke-dasharray':
            try:
                parsed = _parse_project_stroke_dasharray(
                    raw,
                    allow_zero_gap=True,
                )
                noncanonical = _noncanonical_stroke_dash_numbers(raw)
            except ValueError:
                continue
            if parsed is None:
                if raw != 'none':
                    normalized = 'none'
                    reason = 'remove surrounding whitespace'
            else:
                preset, values = parsed
                longer_custom = preset is None and len(values) > 2
                if noncanonical or longer_custom or raw != raw.strip():
                    kept_values = values[:2] if longer_custom else values
                    normalized = ' '.join(
                        _format_project_geometry_length(value)
                        for value in kept_values
                    )
                    reasons = []
                    if noncanonical:
                        reasons.append('use ordinary decimal numbers')
                    if longer_custom:
                        reasons.append(
                            'make the first-pair export normalization explicit'
                        )
                    if raw != raw.strip():
                        reasons.append('remove surrounding whitespace')
                    reason = '; '.join(reasons)
        elif attribute == 'stroke-dashoffset':
            try:
                value = _parse_project_geometry_length(raw, attribute)
            except ValueError:
                continue
            if not _is_canonical_project_geometry_length(raw):
                normalized = _format_project_geometry_length(value)
                reason = 'use the unitless px spelling'
        else:
            try:
                value = _parse_project_stroke_enum(attribute, raw)
            except ValueError:
                continue
            if raw != value:
                normalized = value
                reason = 'remove surrounding whitespace'

        if normalized is None:
            continue
        key = (attribute, raw, normalized, reason)
        recommendations[key] += 1
        if label not in examples[key] and len(examples[key]) < 3:
            examples[key].append(label)

    for (attribute, raw, normalized, reason), count in sorted(
        recommendations.items()
    ):
        shown_examples = ', '.join(
            examples[(attribute, raw, normalized, reason)]
        )
        result['warnings'].append(
            f"Recommendation: line style {attribute}={raw!r} is "
            f"converter-compatible in {count} location(s) "
            f"({shown_examples}); generated SVG should prefer "
            f'{attribute}="{normalized}" to {reason}. No change is '
            "required for export."
        )


def check_image_aspect_ratio_values(
    root: ET.Element,
    result: Dict,
) -> None:
    """Reject ambiguous image fit/crop values and advise canonical forms."""
    from .svg_contracts import (
        Counter,
        _element_label,
        _format_project_image_aspect_ratio,
        _iter_project_image_aspect_ratios,
        _parse_project_image_aspect_ratio,
        _project_image_aspect_ratio_errors,
        defaultdict,
    )
    helpers = (
        _format_project_image_aspect_ratio,
        _iter_project_image_aspect_ratios,
        _parse_project_image_aspect_ratio,
        _project_image_aspect_ratio_errors,
    )
    if any(helper is None for helper in helpers):
        result['warnings'].append(
            "Unable to import svg_to_pptx image aspect-ratio validators; "
            "native export will still validate image fit/crop syntax."
        )
        return

    result['errors'].extend(_project_image_aspect_ratio_errors(root))
    recommendations: Counter[tuple[str, str]] = Counter()
    examples: Dict[tuple[str, str], List[str]] = defaultdict(list)

    for elem, raw in _iter_project_image_aspect_ratios(root):
        try:
            align, mode = _parse_project_image_aspect_ratio(raw)
        except ValueError:
            continue
        normalized = _format_project_image_aspect_ratio(align, mode)
        if raw == normalized:
            continue
        key = (raw, normalized)
        recommendations[key] += 1
        label = _element_label(elem)
        if label not in examples[key] and len(examples[key]) < 3:
            examples[key].append(label)

    for (raw, normalized), count in sorted(recommendations.items()):
        shown_examples = ', '.join(examples[(raw, normalized)])
        result['warnings'].append(
            f"Recommendation: image preserveAspectRatio={raw!r} is "
            f"converter-compatible in {count} location(s) "
            f"({shown_examples}); generated SVG should prefer "
            f'preserveAspectRatio="{normalized}". No change is required '
            "for export."
        )


def check_nested_svg_crop_contract(
    root: ET.Element,
    result: Dict,
) -> None:
    """Reserve nested SVG for the imported picture-crop transport."""
    from .svg_contracts import (
        _project_nested_svg_crop_errors,
    )
    if _project_nested_svg_crop_errors is None:
        result['errors'].append(
            'Unable to import the nested SVG crop validator; cannot '
            'verify imported picture-crop wrappers'
        )
        return
    result['errors'].extend(_project_nested_svg_crop_errors(root))


def check_freeform_geometry_values(
    root: ET.Element,
    result: Dict,
) -> None:
    """Reject malformed path/points syntax and advise decimal spelling."""
    from .svg_contracts import (
        Counter,
        _element_label,
        _format_project_geometry_length,
        _iter_project_freeform_geometry,
        _local_name,
        _noncanonical_path_numbers,
        _noncanonical_points_numbers,
        defaultdict,
    )
    helpers = (
        _format_project_geometry_length,
        _iter_project_freeform_geometry,
        _noncanonical_path_numbers,
        _noncanonical_points_numbers,
    )
    if any(helper is None for helper in helpers):
        result['warnings'].append(
            "Unable to import svg_to_pptx freeform geometry validators; "
            "native export will still validate path and points syntax."
        )
        return

    errors: set[str] = set()
    recommendations: Counter[tuple[str, str, str]] = Counter()
    examples: Dict[tuple[str, str, str], List[str]] = defaultdict(list)

    for elem, attribute, raw, min_points in _iter_project_freeform_geometry(root):
        label = _element_label(elem)
        try:
            if raw is None:
                tag = _local_name(elem)
                raise ValueError(f'<{tag}> requires {attribute}')
            if attribute == 'd':
                compatible_numbers = _noncanonical_path_numbers(raw)
            else:
                required_points = min_points or 2
                compatible_numbers = _noncanonical_points_numbers(
                    raw,
                    min_points=required_points,
                )
        except ValueError as exc:
            errors.add(f'{label} {attribute}: {exc}')
            continue

        for number in compatible_numbers:
            normalized = _format_project_geometry_length(float(number))
            key = (attribute, number, normalized)
            recommendations[key] += 1
            if label not in examples[key] and len(examples[key]) < 3:
                examples[key].append(label)

    result['errors'].extend(sorted(errors))
    for (attribute, raw, normalized), count in sorted(recommendations.items()):
        shown_examples = ', '.join(examples[(attribute, raw, normalized)])
        result['warnings'].append(
            f"Recommendation: freeform geometry {attribute} numeric token "
            f"{raw!r} is converter-compatible in {count} occurrence(s) "
            f"({shown_examples}); generated SVG should prefer the ordinary "
            f"decimal spelling {normalized!r}. No change is required for export."
        )


def check_transform_values(
    root: ET.Element,
    result: Dict,
) -> None:
    """Reject invalid transforms and advise ordinary decimal spelling."""
    from .svg_contracts import (
        Counter,
        _UseExpansionError,
        _element_label,
        _expand_local_use_references,
        _format_project_geometry_length,
        _iter_project_transforms,
        _noncanonical_transform_numbers,
        _project_transform_errors,
        copy,
        defaultdict,
    )
    helpers = (
        _format_project_geometry_length,
        _iter_project_transforms,
        _noncanonical_transform_numbers,
        _project_transform_errors,
    )
    if any(helper is None for helper in helpers):
        result['warnings'].append(
            "Unable to import svg_to_pptx transform validators; "
            "native export will still validate transform syntax."
        )
        return

    transform_errors = set(_project_transform_errors(root))
    if (
        not transform_errors
        and _expand_local_use_references is not None
        and _UseExpansionError is not None
    ):
        expanded_root = copy.deepcopy(root)
        try:
            _expand_local_use_references(expanded_root)
        except _UseExpansionError:
            # The local-reference check owns the actionable diagnostic.
            pass
        else:
            transform_errors.update(_project_transform_errors(expanded_root))
    result['errors'].extend(
        f'Invalid SVG transform: {error}'
        for error in sorted(transform_errors)
    )

    recommendations: Counter[tuple[str, str]] = Counter()
    examples: Dict[tuple[str, str], List[str]] = defaultdict(list)
    for elem, raw in _iter_project_transforms(root):
        try:
            compatible_numbers = _noncanonical_transform_numbers(raw)
        except ValueError:
            continue
        for number in compatible_numbers:
            normalized = _format_project_geometry_length(float(number))
            key = (number, normalized)
            recommendations[key] += 1
            label = _element_label(elem)
            if label not in examples[key] and len(examples[key]) < 3:
                examples[key].append(label)

    for (raw, normalized), count in sorted(recommendations.items()):
        shown_examples = ', '.join(examples[(raw, normalized)])
        result['warnings'].append(
            f"Recommendation: transform numeric token {raw!r} is "
            f"converter-compatible in {count} occurrence(s) "
            f"({shown_examples}); generated SVG should prefer the ordinary "
            f"decimal spelling {normalized!r}. No change is required for export."
        )


def check_font_size_values(content: str, result: Dict) -> None:
    """Keep supported font-size units compatible and recommend unitless px."""
    from .svg_contracts import (
        _DRAWINGML_TEXT_FONT_SIZE_MAX,
        _DRAWINGML_TEXT_FONT_SIZE_MIN,
        _font_px_to_hpt,
        _parse_export_length,
        math,
        re,
    )
    canonical_re = re.compile(r'^(?:\d+(?:\.\d+)?|\.\d+)$')
    values = set()

    for match in re.finditer(r'\bfont-size\s*=\s*(["\'])(.*?)\1', content, re.IGNORECASE):
        values.add(match.group(2).strip())

    for match in re.finditer(r'\bfont-size\s*:\s*([^;"\']+)', content, re.IGNORECASE):
        values.add(match.group(1).strip())

    if _parse_export_length is None:
        result['warnings'].append(
            "Unable to import svg_to_pptx length parser; skipped font-size syntax check"
        )
        return

    unsupported = set()
    drawingml_out_of_range = set()
    compatible_noncanonical = set()
    for raw in values:
        try:
            parsed_px = _parse_export_length(raw, math.nan, font_size=16)
        except (TypeError, ValueError):
            unsupported.add(raw)
            continue
        if not math.isfinite(parsed_px) or parsed_px < 0:
            unsupported.add(raw)
            continue
        if _font_px_to_hpt is not None:
            try:
                _font_px_to_hpt(parsed_px)
            except ValueError:
                drawingml_out_of_range.add(raw)
                continue
        if not canonical_re.fullmatch(raw):
            compatible_noncanonical.add(raw)

    if unsupported:
        shown_values = sorted(unsupported)
        shown = ', '.join(shown_values[:5])
        more = len(shown_values) - 5
        suffix = f" (+{more} more)" if more > 0 else ""
        result['errors'].append(
            f"Unsupported font-size value(s): {shown}{suffix}. Use a finite "
            "non-negative SVG length supported by svg_to_pptx."
        )

    if drawingml_out_of_range:
        shown_values = sorted(drawingml_out_of_range)
        shown = ', '.join(shown_values[:5])
        more = len(shown_values) - 5
        suffix = f" (+{more} more)" if more > 0 else ""
        result['errors'].append(
            f"font-size value(s) {shown}{suffix} are outside the DrawingML "
            f"range sz={_DRAWINGML_TEXT_FONT_SIZE_MIN}.."
            f"{_DRAWINGML_TEXT_FONT_SIZE_MAX} (1..4000pt); PowerPoint would "
            "repair the exported file. Do not use tiny transparent text as "
            "a placeholder carrier: leave a text carrier blank or use the "
            "composite object proxy contract."
        )

    if compatible_noncanonical:
        shown_values = sorted(compatible_noncanonical)
        shown = ', '.join(shown_values[:5])
        more = len(shown_values) - 5
        suffix = f" (+{more} more)" if more > 0 else ""
        result['warnings'].append(
            f"Recommendation: font-size value(s) {shown}{suffix} are "
            "converter-compatible; generated SVG should prefer unitless px "
            "values such as font-size=\"28\". No change is required for export."
        )
