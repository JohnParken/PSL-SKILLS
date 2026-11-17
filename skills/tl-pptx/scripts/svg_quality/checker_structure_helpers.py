"""Pinned PPT Master quality gate component.

MIT Copyright (c) 2025-2026 Hugo He.
Upstream 7e0df6705de0cd24345210d31f07fcb5a32b6900.
"""
from __future__ import annotations
from pathlib import Path
from typing import Dict, List, Tuple
from xml.etree import ElementTree as ET

def count_carried_relationship_pages(design_spec_text: str) -> int:
    """Count §IX ``Relationships`` lines naming order, link, parent, or membership."""
    from .checker import (
        _CARRIED_RELATION_WORD_RE,
        _SPEC_RELATIONSHIPS_LINE_RE,
    )
    count = 0
    for line in design_spec_text.splitlines():
        match = _SPEC_RELATIONSHIPS_LINE_RE.match(line)
        if match and _CARRIED_RELATION_WORD_RE.search(match.group('value')):
            count += 1
    return count


def _compact_preset_ancestor_paint(
    root: ET.Element,
) -> list[tuple[str, tuple[str, ...]]]:
    """Return compact presets affected by compatible ancestor paint."""
    from .checker import (
        _authored_preset_encoding,
        _validate_authored_preset_group,
    )
    if (
        _authored_preset_encoding is None
        or _validate_authored_preset_group is None
    ):
        return []
    parents = {
        child: parent
        for parent in root.iter()
        for child in parent
    }
    affected: list[tuple[str, tuple[str, ...]]] = []
    for group in root.iter():
        if (
            _authored_preset_encoding(group) != 'compact'
            or _validate_authored_preset_group(group)
        ):
            continue
        relevant = {'opacity'}
        if group.get('fill') != 'none' and group.get('fill-opacity') is None:
            relevant.add('fill-opacity')
        if group.get('stroke') != 'none':
            for name in (
                'stroke-opacity',
                'stroke-dasharray',
                'stroke-linecap',
                'stroke-linejoin',
            ):
                if group.get(name) is None:
                    relevant.add(name)

        inherited: set[str] = set()
        ancestor = parents.get(group)
        while ancestor is not None:
            declarations = {
                name: ancestor.get(name) or ''
                for name in relevant
                if ancestor.get(name) is not None
            }
            for declaration in (ancestor.get('style') or '').split(';'):
                name, separator, value = declaration.partition(':')
                name = name.strip().lower()
                if separator and name in relevant:
                    declarations[name] = value.strip()
            for name, value in declarations.items():
                normalized = value.strip().lower()
                if name in {'opacity', 'fill-opacity', 'stroke-opacity'}:
                    try:
                        if float(normalized) == 1:
                            continue
                    except ValueError:
                        pass
                elif name == 'stroke-dasharray' and normalized == 'none':
                    continue
                elif name == 'stroke-linecap' and normalized == 'butt':
                    continue
                elif name == 'stroke-linejoin' and normalized == 'miter':
                    continue
                inherited.add(name)
            ancestor = parents.get(ancestor)
        if inherited:
            affected.append((
                group.get('id') or '(no id)',
                tuple(sorted(inherited)),
            ))
    return affected


def _declared_pptx_structure_mode(project_path: Path) -> str | None:
    """Return the explicitly locked SVG structure mode without a fallback."""
    from .checker import (
        _PPTX_STRUCTURE_MODE_RE,
        _PPTX_STRUCTURE_SECTION_RE,
    )
    lock_path = project_path / 'spec_lock.md'
    try:
        content = lock_path.read_text(encoding='utf-8')
    except OSError:
        return None
    section_match = _PPTX_STRUCTURE_SECTION_RE.search(content)
    if section_match is None:
        return None
    mode_match = _PPTX_STRUCTURE_MODE_RE.search(section_match.group(1))
    return mode_match.group(1).strip().lower() if mode_match else None


def _generated_theme_contract_errors(project_path: Path) -> List[str]:
    """Validate the current-project theme contract required by release export."""
    from .checker import (
        _ThemeColorError,
        _ThemeFontError,
        _load_master_text_style_spec,
        _load_theme_color_spec,
        _load_theme_font_spec,
    )
    if (
        _ThemeColorError is None
        or _ThemeFontError is None
        or _load_theme_color_spec is None
        or _load_master_text_style_spec is None
        or _load_theme_font_spec is None
    ):
        return [
            "PowerPoint theme contract validation is unavailable because the "
            "theme loader modules could not be imported."
        ]
    try:
        theme_font_spec = _load_theme_font_spec(project_path)
        _load_master_text_style_spec(project_path)
        theme_color_spec = _load_theme_color_spec(project_path)
    except (_ThemeFontError, _ThemeColorError) as exc:
        return [str(exc)]

    missing: List[str] = []
    if theme_font_spec is None:
        missing.append("typography font_family/title_family/body_family")
    if theme_color_spec is None:
        missing.append("colors")
    if not missing:
        return []
    return [
        "spec_lock.md generated PowerPoint theme contract is missing: "
        + ", ".join(missing)
    ]


def _parse_positive_bounds(
    value: str,
) -> Tuple[float, float, float, float]:
    """Parse one positive x/y/width/height boundary."""
    from .checker import (
        math,
        re,
    )
    raw_values = [item for item in re.split(r"[\s,]+", value.strip()) if item]
    if len(raw_values) != 4:
        raise ValueError("must contain exactly four numbers: x y width height")
    try:
        values = tuple(float(item) for item in raw_values)
    except ValueError as exc:
        raise ValueError("must contain only numeric values") from exc
    if not all(math.isfinite(item) for item in values):
        raise ValueError("must contain only finite values")
    if values[2] <= 0 or values[3] <= 0:
        raise ValueError("must use positive width and height")
    return values


def _placeholder_bounds_error(value: str) -> str | None:
    """Return a concise error for invalid design-zone bounds."""
    from .checker import (
        _parse_positive_bounds,
    )
    try:
        _parse_positive_bounds(value)
    except ValueError as exc:
        return str(exc)
    return None


def _local_pptx_structure_errors(
    root: ET.Element,
    svg_path: Path,
    *,
    require_structure: bool,
) -> List[str]:
    """Validate the authoring shape of the structured SVG contract."""
    from .checker import (
        _NON_VISUAL_SVG_TAGS,
        _PPTX_PLACEHOLDER_DETAIL_ATTRS,
        _PPTX_ROOT_STRUCTURE_ATTRS,
        _PPTX_ROOT_VISIBILITY_ATTRS,
        _is_authored_preset_atom,
        _placeholder_bounds_error,
    )
    errors: List[str] = []
    root_values = {
        attr: (root.get(attr) or '').strip()
        for attr in _PPTX_ROOT_STRUCTURE_ATTRS
    }
    has_root_structure = any(root_values.values())
    if require_structure or has_root_structure:
        missing = [attr for attr, value in root_values.items() if not value]
        if missing:
            errors.append(
                f"{svg_path.name}: structured SVG root is missing "
                + ', '.join(missing)
            )
    for attr in _PPTX_ROOT_VISIBILITY_ATTRS:
        raw = root.get(attr)
        if raw is not None and raw not in {'true', 'false'}:
            errors.append(
                f"{svg_path.name}: root {attr} must be exactly 'true' or 'false'"
            )

    parent_by_id = {
        id(child): parent
        for parent in root.iter()
        for child in list(parent)
    }
    for elem in root.iter():
        tag = elem.tag.rsplit('}', 1)[-1]
        element_id = elem.get('id') or f"<{tag}>"
        parent = parent_by_id.get(id(elem))

        if elem is not root:
            nested_root_attrs = [
                attr for attr in (
                    *_PPTX_ROOT_STRUCTURE_ATTRS,
                    *_PPTX_ROOT_VISIBILITY_ATTRS,
                )
                if elem.get(attr) is not None
            ]
            if nested_root_attrs:
                errors.append(
                    f"{svg_path.name}: {element_id} carries root-only metadata "
                    + ', '.join(nested_root_attrs)
                )

        if elem.get('data-pptx-layout-kind') is not None:
            errors.append(
                f"{svg_path.name}: data-pptx-layout-kind is a legacy distillation "
                "attribute; restore the page to the structured contract"
            )

        layer = (elem.get('data-pptx-layer') or '').strip().lower()
        placeholder = (elem.get('data-pptx-placeholder') or '').strip().lower()
        if layer in {'master', 'layout'}:
            if parent is not root:
                errors.append(
                    f"{svg_path.name}: {element_id} data-pptx-layer={layer!r} "
                    "must be a direct child of the root <svg>"
                )
            if tag == 'g' and not (
                _is_authored_preset_atom is not None
                and _is_authored_preset_atom(elem)
            ):
                errors.append(
                    f"{svg_path.name}: {element_id} is a <g> marked as {layer}; "
                    "Master/Layout fixed visuals must be root-level atomic elements"
                )
            if placeholder:
                errors.append(
                    f"{svg_path.name}: {element_id} cannot be both a fixed "
                    f"{layer} element and a placeholder slot"
                )

        detail_attrs = [
            attr for attr in _PPTX_PLACEHOLDER_DETAIL_ATTRS
            if elem.get(attr) is not None
        ]
        if detail_attrs and not placeholder:
            errors.append(
                f"{svg_path.name}: {element_id} uses placeholder detail metadata "
                "without data-pptx-placeholder"
            )

        if placeholder:
            if parent is not root:
                errors.append(
                    f"{svg_path.name}: placeholder slot {element_id} must be a "
                    "direct child of the root <svg>"
                )
            if tag != 'g':
                errors.append(
                    f"{svg_path.name}: placeholder slot {element_id} must be a "
                    "root-level <g>"
                )
            if not (elem.get('id') or '').strip():
                errors.append(
                    f"{svg_path.name}: every placeholder slot <g> requires a stable id"
                )
            wrapper_attrs = sorted(
                attr.rsplit('}', 1)[-1]
                for attr in elem.attrib
                if attr != 'id'
                and not attr.rsplit('}', 1)[-1].startswith('data-pptx-')
            )
            if wrapper_attrs:
                errors.append(
                    f"{svg_path.name}: placeholder slot {element_id} is an "
                    "authoring boundary and may carry only id/data-pptx-*; remove "
                    + ', '.join(wrapper_attrs)
                )
            bounds = (elem.get('data-pptx-bounds') or '').strip()
            if not bounds:
                errors.append(
                    f"{svg_path.name}: placeholder slot {element_id} requires "
                    "data-pptx-bounds"
                )
            else:
                bounds_error = _placeholder_bounds_error(bounds)
                if bounds_error:
                    errors.append(
                        f"{svg_path.name}: placeholder slot {element_id} bounds "
                        + bounds_error
                    )

            binding = (
                elem.get('data-pptx-binding') or 'carrier'
            ).strip().lower()
            if binding not in {'carrier', 'proxy'}:
                errors.append(
                    f"{svg_path.name}: placeholder slot {element_id} has unknown "
                    f"binding {binding!r}; use carrier or proxy"
                )
            carrier_descendants = [
                child for child in elem.iter()
                if child is not elem
                and child.get('data-pptx-carrier') is not None
            ]
            visual_children = [
                child for child in list(elem)
                if child.tag.rsplit('}', 1)[-1] not in _NON_VISUAL_SVG_TAGS
            ]
            direct_carriers = [
                child for child in visual_children
                if (child.get('data-pptx-carrier') or '').strip().lower()
                == 'true'
            ]
            nested_carriers = [
                child for child in carrier_descendants
                if parent_by_id.get(id(child)) is not elem
            ]
            if nested_carriers:
                names = ', '.join(
                    child.get('id') or f"<{child.tag.rsplit('}', 1)[-1]}>"
                    for child in nested_carriers
                )
                errors.append(
                    f"{svg_path.name}: placeholder slot {element_id} has nested "
                    f"carrier marker(s): {names}; the carrier must be a direct child"
                )
            if binding == 'carrier':
                if len(visual_children) != 1 or len(direct_carriers) != 1:
                    errors.append(
                        f"{svg_path.name}: placeholder slot {element_id} requires "
                        "exactly one visual direct child, marked "
                        "data-pptx-carrier=\"true\""
                    )
            if binding == 'proxy':
                if placeholder != 'object':
                    errors.append(
                        f"{svg_path.name}: proxy binding is allowed only for an "
                        f"object placeholder, not {placeholder!r}"
                    )
                if carrier_descendants:
                    errors.append(
                        f"{svg_path.name}: proxy placeholder slot {element_id} must "
                        "not declare a visible placeholder carrier"
                    )
                if not visual_children:
                    errors.append(
                        f"{svg_path.name}: proxy placeholder slot {element_id} must "
                        "contain visible Slide-local content"
                    )

        carrier_value = elem.get('data-pptx-carrier')
        if carrier_value is not None:
            if carrier_value.strip().lower() != 'true':
                errors.append(
                    f"{svg_path.name}: {element_id} "
                    "data-pptx-carrier must equal true"
                )
            if parent is None or not (
                parent.get('data-pptx-placeholder') or ''
            ).strip():
                errors.append(
                    f"{svg_path.name}: placeholder carrier {element_id} must be a "
                    "direct child of a root placeholder slot"
                )

        if tag in _NON_VISUAL_SVG_TAGS and (layer or placeholder):
            errors.append(
                f"{svg_path.name}: non-visual {element_id} cannot carry "
                "Master/Layout/placeholder ownership"
            )

    return list(dict.fromkeys(errors))


def _normalize_hex_rgb(value: str) -> str | None:
    """Normalize 3/4/6/8-digit HEX to alpha-free ``RRGGBB``."""
    from .checker import (
        HEX_VALUE_RE,
    )
    if not HEX_VALUE_RE.fullmatch(value):
        return None
    color = value[1:]
    if len(color) in {3, 4}:
        color = ''.join(channel * 2 for channel in color)
    return color[:6].upper()


# Cheap numeric envelope for font-size role enforcement. Semantic role assignment
# is prompt-owned; Checker only verifies that a used value is close to at least
# one declared size anchor.
