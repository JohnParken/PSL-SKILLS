"""Pinned PPT Master quality gate component.

MIT Copyright (c) 2025-2026 Hugo He.
Upstream 7e0df6705de0cd24345210d31f07fcb5a32b6900.
"""
from __future__ import annotations
from pathlib import Path
from typing import Dict, List, Tuple
from xml.etree import ElementTree as ET

def check_forbidden_elements(
    content: str,
    root: ET.Element,
    result: Dict,
) -> None:
    """Check forbidden elements (blocklist)"""
    from .svg_contracts import (
        _local_name,
        _materialize_inline_geometry_properties,
        _validate_inline_geometry_properties,
        _validate_local_use_references,
        re,
    )
    content_lower = content.lower()
    elems = list(root.iter())
    local_names = {_local_name(elem).lower() for elem in elems}
    # Parsed attribute names, not raw text: "data-class" or a <text> reading
    # "class=Customer" / "onload=init" is not an attribute.
    attr_names = {
        name.rsplit("}", 1)[-1].lower() for elem in elems for name in elem.attrib
    }

    # ============================================================
    # Forbidden elements blocklist - PPT incompatible
    # ============================================================

    # Style system
    if 'style' in local_names:
        result['errors'].append("Detected forbidden <style> element (use inline attributes instead)")
    if 'class' in attr_names:
        result['errors'].append("Detected forbidden class attribute (use inline styles instead)")
    # id attribute: only report error when <style> also exists (id is harmful only with CSS selectors)
    # id inside <defs> for linearGradient/filter etc. is required, Inkscape also auto-adds id to elements,
    # standalone id attributes have no impact on PPT export
    if 'style' in local_names and 'id' in attr_names:
        result['errors'].append(
            "Detected id attribute used with <style> (CSS selectors forbidden, use inline styles instead)"
        )
    if re.search(r'<\?xml-stylesheet\b', content_lower):
        result['errors'].append("Detected forbidden xml-stylesheet (external CSS references forbidden)")
    if re.search(r'<link[^>]*rel\s*=\s*["\']stylesheet["\']', content_lower):
        result['errors'].append("Detected forbidden <link rel=\"stylesheet\"> (external CSS references forbidden)")
    # CSS only: a code slide whose visible text shows "@import url(...)" is fine.
    css_text = " ".join(
        "".join(elem.itertext()) if _local_name(elem).lower() == "style" else elem.get("style", "")
        for elem in elems
    )
    if re.search(r'@import\s+', css_text.lower()):
        result['errors'].append("Detected forbidden @import (external CSS references forbidden)")
    if _validate_inline_geometry_properties is None:
        result['warnings'].append(
            "Unable to import inline geometry validator; "
            "native export will still validate geometry styles."
        )
    else:
        geometry_errors = _validate_inline_geometry_properties(root)
        for error in geometry_errors:
            result['errors'].append(f"Invalid inline geometry property: {error}")
        if not geometry_errors:
            _materialize_inline_geometry_properties(root)

    # Structure / nesting
    if 'foreignobject' in local_names:
        result['errors'].append(
            "Detected forbidden <foreignObject> element (use <tspan> for manual line breaks)")
    has_generic_use = any(
        _local_name(elem).lower() == 'use' and elem.get('data-icon') is None
        for elem in elems
    )
    if has_generic_use:
        if _validate_local_use_references is None:
            result['warnings'].append(
                "Detected local <use> references, but the shared validator "
                "could not be imported; native export will still validate them."
            )
        else:
            for error in _validate_local_use_references(root):
                result['errors'].append(f"Invalid local <use> reference: {error}")
    # Text / fonts
    if 'textpath' in local_names:
        result['errors'].append("Detected forbidden <textPath> element (path text is incompatible with PPT)")
    if '@font-face' in content_lower:
        result['errors'].append("Detected forbidden @font-face (use system font stack)")

    # Animation / interaction
    if any(name.startswith('animate') for name in local_names):
        result['errors'].append(
            "Detected forbidden SMIL animation element <animate*> "
            "(SVG animations are not exported)"
        )
    if 'set' in local_names:
        result['errors'].append("Detected forbidden SMIL animation element <set> (SVG animations are not exported)")
    if 'script' in local_names:
        result['errors'].append("Detected forbidden <script> element (scripts and event handlers forbidden)")
    if any(re.fullmatch(r'on\w+', name) for name in attr_names):  # onclick, onload etc.
        result['errors'].append("Detected forbidden event attributes (e.g., onclick, onload)")

    # Other discouraged elements
    if 'iframe' in local_names:
        result['errors'].append("Detected <iframe> element (should not appear in SVG)")


def check_paint_compatibility(
    root: ET.Element,
    result: Dict,
) -> None:
    """Reject unsupported paint and advise one generated-SVG spelling.

    The exporter parser owns compatibility. Any paint it can parse remains
    valid input; the checker only warns when that spelling differs from the
    generated-SVG default (uppercase ``#RRGGBB`` plus explicit alpha).
    """
    from .svg_contracts import (
        Counter,
        Dict,
        _CANONICAL_PAINT_ALPHA_PROPERTY,
        _PAINT_PROPERTIES,
        _PERCENTAGE_OPACITY_PROPERTIES,
        _element_label,
        _format_project_opacity,
        _is_project_paint_default_form,
        _iter_project_paints,
        _parse_inline_style,
        _parse_project_opacity,
        _parse_project_paint,
        _project_paint_errors,
        defaultdict,
    )
    helpers = (
        _PAINT_PROPERTIES,
        _PERCENTAGE_OPACITY_PROPERTIES,
        _format_project_opacity,
        _is_project_paint_default_form,
        _iter_project_paints,
        _parse_inline_style,
        _parse_project_opacity,
        _parse_project_paint,
        _project_paint_errors,
    )
    if any(helper is None for helper in helpers):
        result['warnings'].append(
            "Unable to import svg_to_pptx paint parsers; skipped paint syntax check"
        )
        return

    result['errors'].extend(_project_paint_errors(root))
    recommendations: Counter[tuple[str, str, str]] = Counter()
    recommendation_examples: Dict[tuple[str, str, str], List[str]] = defaultdict(list)

    def remember_example(store: Dict, key: tuple, label: str) -> None:
        labels = store[key]
        if label not in labels and len(labels) < 3:
            labels.append(label)

    for elem, name, raw_value, source in _iter_project_paints(root):
        try:
            kind, normalized, color_alpha = _parse_project_paint(
                raw_value,
                name,
            )
        except ValueError:
            continue
        if _is_project_paint_default_form(raw_value, name):
            continue

        source_label = f'{_element_label(elem)} {source}'
        if kind == 'none':
            replacement = f'{name}="none"'
        elif kind == 'reference':
            replacement = f'{name}="url(#{normalized})"'
        elif name in {'fill', 'stroke'} and raw_value.strip().lower() == 'transparent':
            replacement = f'{name}="none"'
        else:
            replacement = f'{name}="#{normalized}"'
            alpha_name = _CANONICAL_PAINT_ALPHA_PROPERTY.get(name)
            if color_alpha < 1.0 and alpha_name is not None:
                style_values = _parse_inline_style(elem.get('style'))
                existing_alpha_raw = (
                    style_values.get(alpha_name) or elem.get(alpha_name)
                )
                if existing_alpha_raw is None:
                    existing_alpha = 1.0
                else:
                    try:
                        existing_alpha = _parse_project_opacity(
                            existing_alpha_raw,
                            allow_percentage=(
                                alpha_name in _PERCENTAGE_OPACITY_PROPERTIES
                            ),
                        )
                    except ValueError:
                        existing_alpha = None
                effective_alpha = (
                    color_alpha * existing_alpha
                    if existing_alpha is not None else color_alpha
                )
                replacement += (
                    f' {alpha_name}="'
                    f'{_format_project_opacity(effective_alpha)}"'
                )
            elif color_alpha < 1.0:
                replacement += (
                    '; put alpha on the matching pattern child fill/stroke '
                    'opacity'
                )

        key = (name, raw_value, replacement)
        recommendations[key] += 1
        remember_example(recommendation_examples, key, source_label)

    for (name, raw_value, replacement), count in sorted(recommendations.items()):
        examples = ', '.join(
            recommendation_examples[(name, raw_value, replacement)]
        )
        result['warnings'].append(
            f"Recommendation: {name}={raw_value!r} is converter-compatible "
            f"in {count} location(s) ({examples}); generated SVG should "
            f"prefer {replacement}. No change is required for export."
        )


def check_reference_spelling(root: ET.Element, result: Dict) -> None:
    """Recommend SVG 2 ``href`` while retaining legacy XLink input."""
    from .svg_contracts import (
        XLINK_NS,
        _element_label,
        _local_name,
    )
    labels = []
    xlink_href = f'{{{XLINK_NS}}}href'
    for elem in root.iter():
        if _local_name(elem).lower() not in {'a', 'image', 'use'}:
            continue
        if elem.get(xlink_href) is not None:
            labels.append(_element_label(elem))
    if labels:
        examples = ', '.join(labels[:3])
        suffix = f' (+{len(labels) - 3} more)' if len(labels) > 3 else ''
        result['warnings'].append(
            f"Recommendation: legacy xlink:href is supported on {len(labels)} "
            f"reference(s) ({examples}{suffix}); generated SVG should prefer "
            "href. No change is required for export."
        )


def check_opacity_values(
    root: ET.Element,
    result: Dict,
) -> None:
    """Reject malformed opacity and advise generated-SVG values."""
    from .svg_contracts import (
        Counter,
        _PERCENTAGE_OPACITY_PROPERTIES,
        _element_label,
        _format_project_opacity,
        _is_project_opacity_default_form,
        _iter_project_opacities,
        _local_name,
        _parse_inline_style,
        _parse_project_opacity,
        _project_opacity_errors,
        defaultdict,
    )
    helpers = (
        _PERCENTAGE_OPACITY_PROPERTIES,
        _format_project_opacity,
        _is_project_opacity_default_form,
        _iter_project_opacities,
        _parse_inline_style,
        _parse_project_opacity,
        _project_opacity_errors,
    )
    if any(helper is None for helper in helpers):
        result['warnings'].append(
            "Unable to import svg_to_pptx opacity validators; native "
            "export will still validate opacity syntax."
        )
        return

    result['errors'].extend(_project_opacity_errors(root))
    recommendations: Counter[tuple[str, str, str]] = Counter()
    examples: Dict[tuple[str, str, str], List[str]] = defaultdict(list)
    fidelity_warnings: set[str] = set()

    for elem, property_name, raw, source in _iter_project_opacities(root):
        try:
            value = _parse_project_opacity(
                raw,
                allow_percentage=(
                    property_name in _PERCENTAGE_OPACITY_PROPERTIES
                ),
            )
        except ValueError:
            continue
        if _is_project_opacity_default_form(raw):
            continue
        normalized = _format_project_opacity(value)
        key = (property_name, raw, normalized)
        recommendations[key] += 1
        label = f'{_element_label(elem)} {source}'
        if label not in examples[key] and len(examples[key]) < 3:
            examples[key].append(label)

    for elem in root.iter():
        if _local_name(elem).lower() != 'g':
            continue
        style_values = _parse_inline_style(elem.get('style'))
        raw_opacity = (
            style_values['opacity']
            if 'opacity' in style_values else elem.get('opacity')
        )
        if raw_opacity is None:
            continue
        try:
            opacity = _parse_project_opacity(raw_opacity)
        except ValueError:
            continue
        if opacity < 1.0:
            fidelity_warnings.add(
                f"Fidelity warning: {_element_label(elem)} uses group "
                f"opacity={raw_opacity!r}. The converter distributes this "
                "alpha to descendants and cannot preserve isolated group "
                "compositing; generated SVG should prefer descendant alpha. "
                "Existing input remains convertible and does not require "
                "modification."
            )

    for (property_name, raw, normalized), count in sorted(
        recommendations.items()
    ):
        shown_examples = ', '.join(
            examples[(property_name, raw, normalized)]
        )
        result['warnings'].append(
            f"Recommendation: {property_name}={raw!r} is "
            f"converter-compatible in {count} location(s) "
            f"({shown_examples}); generated SVG should prefer "
            f'{property_name}="{normalized}". No change is required '
            "for export."
        )
    result['warnings'].extend(sorted(fidelity_warnings))


def check_authoring_property_contract(
    root: ET.Element,
    result: Dict,
) -> None:
    """Validate inline CSS and attributes against the authoring surface."""
    from .svg_contracts import (
        _BAKE_REQUIRED_VISUAL_PROPERTIES,
        _OPACITY_PROPERTIES,
        _PAINT_PROPERTIES,
        _SHARED_FAIL_CLOSED_STYLE_PROPERTIES,
        _SUPPORTED_INLINE_STYLE_PROPERTIES,
        _element_label,
    )
    errors: set[str] = set()
    validated_value_properties = set(_OPACITY_PROPERTIES or ())
    validated_value_properties.update(_PAINT_PROPERTIES or ())
    for elem in root.iter():
        label = _element_label(elem)
        for fragment in (elem.get('style') or '').split(';'):
            fragment = fragment.strip()
            if not fragment:
                continue
            if ':' not in fragment:
                if fragment.lower() not in validated_value_properties:
                    errors.add(
                        f"{label} has malformed inline style declaration "
                        f"{fragment!r}"
                    )
                continue
            name, value = fragment.split(':', 1)
            name = name.strip().lower()
            value = value.strip()
            if not name or not value:
                if name not in validated_value_properties:
                    errors.add(
                        f"{label} has malformed inline style declaration "
                        f"{fragment!r}"
                    )
                continue
            if name in _BAKE_REQUIRED_VISUAL_PROPERTIES:
                errors.add(
                    f"{label} uses Bake-required visual property {name!r}; "
                    "bake the effect or rebuild it with supported geometry"
                )
            elif (
                name not in _SUPPORTED_INLINE_STYLE_PROPERTIES
                and name not in _SHARED_FAIL_CLOSED_STYLE_PROPERTIES
            ):
                errors.add(
                    f"{label} uses unsupported inline style property {name!r}; "
                    "native PPTX export would ignore it"
                )
            if '!important' in value.lower():
                errors.add(
                    f"{label} inline style property {name!r} cannot use !important"
                )

        for attr_name in elem.attrib:
            local_attr = attr_name.rsplit('}', 1)[-1]
            if local_attr in _BAKE_REQUIRED_VISUAL_PROPERTIES:
                errors.add(
                    f"{label} uses Bake-required visual attribute {local_attr!r}; "
                    "bake the effect or rebuild it with supported geometry"
                )

    result['errors'].extend(sorted(errors))


def check_text_property_contract(
    root: ET.Element,
    result: Dict,
) -> None:
    """Validate text property names and values with the export contract."""
    from .svg_contracts import (
        Counter,
        _project_text_property_diagnostics,
        defaultdict,
    )
    if _project_text_property_diagnostics is None:
        result['warnings'].append(
            "Unable to import the shared text-property validator; native "
            "export will still validate text properties."
        )
        return

    errors: set[str] = set()
    recommendations: Counter[tuple[str, str, str]] = Counter()
    examples: Dict[tuple[str, str, str], List[str]] = defaultdict(list)
    for diagnostic in _project_text_property_diagnostics(root):
        if diagnostic.severity == 'error':
            errors.add(diagnostic.message)
            continue
        if diagnostic.canonical is None:
            continue
        key = (
            diagnostic.name,
            diagnostic.raw,
            diagnostic.canonical,
        )
        recommendations[key] += 1
        if (
            diagnostic.label not in examples[key]
            and len(examples[key]) < 3
        ):
            examples[key].append(diagnostic.label)

    result['errors'].extend(sorted(errors))
    for (name, raw, canonical), count in sorted(recommendations.items()):
        shown_examples = ', '.join(examples[(name, raw, canonical)])
        result['warnings'].append(
            f"Recommendation: text property {name}={raw!r} is "
            f"converter-compatible in {count} location(s) "
            f"({shown_examples}); generated SVG should prefer "
            f'{name}="{canonical}". No change is required for export.'
        )


