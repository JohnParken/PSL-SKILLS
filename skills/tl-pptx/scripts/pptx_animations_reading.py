"""PPT Master motion core; responsibility-split, with stable original API.

MIT Copyright (c) 2025-2026 Hugo He.
Upstream commit: 7e0df6705de0cd24345210d31f07fcb5a32b6900.
"""
from __future__ import annotations
import re
from xml.etree import ElementTree as ET
from pptx_transitions import MAX_OOXML_MILLISECONDS, MAX_OOXML_UNSIGNED_INT, PML_NS

from pptx_animations_registry import ET, MAX_OOXML_MILLISECONDS, MAX_OOXML_UNSIGNED_INT, NATIVE_ANIMATIONS, PML_NS, _DML_NS, _PRESET_CLASS_BY_CATEGORY, re

def _int_attribute(
    element: ET.Element,
    name: str,
    label: str,
    errors: list[str],
    *,
    minimum: int = 0,
    maximum: int | None = None,
) -> int | None:
    from pptx_animations import (
        re,
    )
    value = element.get(name)
    if value is None or not re.fullmatch(r'\d+', value):
        errors.append(f'{label} must be an integer; found {value!r}')
        return None
    number = int(value)
    if number < minimum:
        errors.append(f'{label} must be at least {minimum}; found {number}')
        return None
    if maximum is not None and number > maximum:
        errors.append(f'{label} must be at most {maximum}; found {number}')
        return None
    return number


def _direct_conditions(ctn: ET.Element) -> list[ET.Element]:
    from pptx_animations import (
        PML_NS,
        _qn,
    )
    condition_list = ctn.find(_qn(PML_NS, 'stCondLst'))
    if condition_list is None:
        return []
    return [
        child for child in list(condition_list)
        if child.tag == _qn(PML_NS, 'cond')
    ]


def _shape_index(
    slide_root: ET.Element,
) -> tuple[dict[int, tuple[str, bool]], list[str]]:
    from pptx_animations import (
        MAX_OOXML_UNSIGNED_INT,
        PML_NS,
        _int_attribute,
        _local_name,
        _qn,
    )
    parent_map = {
        child: parent
        for parent in slide_root.iter()
        for child in list(parent)
    }
    index: dict[int, tuple[str, bool]] = {}
    errors: list[str] = []
    shape_tags = {'sp', 'grpSp', 'pic', 'graphicFrame', 'cxnSp', 'contentPart'}
    for non_visual in slide_root.iter(_qn(PML_NS, 'cNvPr')):
        shape_id = _int_attribute(
            non_visual,
            'id',
            'p:cNvPr@id',
            errors,
            minimum=1,
            maximum=MAX_OOXML_UNSIGNED_INT,
        )
        if shape_id is None:
            continue
        owner = parent_map.get(non_visual)
        while owner is not None and _local_name(owner.tag) not in shape_tags:
            owner = parent_map.get(owner)
        kind = _local_name(owner.tag) if owner is not None else 'unknown'
        has_text = bool(
            owner is not None
            and kind == 'sp'
            and any(
                _local_name(element.tag) == 't' and (element.text or '').strip()
                for element in owner.iter()
            )
        )
        if shape_id in index:
            errors.append(f'duplicate p:cNvPr@id {shape_id}')
        else:
            index[shape_id] = (kind, has_text)
    return index, errors


def _row_shape_id(row: ET.Element, errors: list[str]) -> int | None:
    from pptx_animations import (
        MAX_OOXML_UNSIGNED_INT,
        PML_NS,
        _int_attribute,
        _qn,
    )
    shape_ids: list[int] = []
    for target in row.iter(_qn(PML_NS, 'spTgt')):
        value = _int_attribute(
            target,
            'spid',
            'animation p:spTgt@spid',
            errors,
            minimum=1,
            maximum=MAX_OOXML_UNSIGNED_INT,
        )
        if value is not None:
            shape_ids.append(value)
    unique = sorted(set(shape_ids))
    if len(unique) != 1:
        errors.append(
            'one object-animation row must resolve to exactly one shape id; '
            f'found {unique or "none"}'
        )
        return None
    return unique[0]


def _row_filter(
    row: ET.Element,
    preset_class: str,
    errors: list[str],
) -> str | None:
    from pptx_animations import (
        PML_NS,
        _qn,
    )
    effects = list(row.iter(_qn(PML_NS, 'animEffect')))
    if len(effects) > 1:
        errors.append(
            f'object-animation row contains {len(effects)} p:animEffect nodes'
        )
    if not effects:
        return None
    effect = effects[0]
    expected_transition = {'entr': 'in', 'exit': 'out'}.get(preset_class)
    if expected_transition is not None and effect.get('transition') != expected_transition:
        errors.append(
            f'{preset_class} p:animEffect must set '
            f'transition="{expected_transition}"'
        )
    return effect.get('filter')


def _resolve_row_effect(
    row: ET.Element,
    filter_name: str | None,
    errors: list[str],
) -> tuple[tuple[str, ...], str | None, int | None, int | None]:
    from pptx_animations import (
        MAX_OOXML_UNSIGNED_INT,
        NATIVE_ANIMATIONS,
        _PRESET_CLASS_BY_CATEGORY,
        _int_attribute,
    )
    preset_class = row.get('presetClass')
    if preset_class not in set(_PRESET_CLASS_BY_CATEGORY.values()):
        errors.append(
            f'unsupported p:cTn@presetClass {preset_class!r}; expected '
            + ', '.join(sorted(set(_PRESET_CLASS_BY_CATEGORY.values())))
        )
        return (), preset_class, None, None
    preset_id = _int_attribute(
        row,
        'presetID',
        'object-animation p:cTn@presetID',
        errors,
        maximum=MAX_OOXML_UNSIGNED_INT,
    )
    preset_subtype = _int_attribute(
        row,
        'presetSubtype',
        'object-animation p:cTn@presetSubtype',
        errors,
        maximum=MAX_OOXML_UNSIGNED_INT,
    )
    if preset_id is None or preset_subtype is None:
        return (), preset_class, preset_id, preset_subtype
    matches = [
        key
        for key, info in NATIVE_ANIMATIONS.items()
        if info['presetClass'] == preset_class
        if int(info['presetID']) == preset_id
    ]
    return tuple(matches), preset_class, preset_id, preset_subtype


def _behavior_duration_ms(
    row: ET.Element,
    errors: list[str],
) -> int | None:
    from pptx_animations import (
        MAX_OOXML_MILLISECONDS,
        _row_effective_duration_ms,
    )
    duration = _row_effective_duration_ms(row)
    if duration is not None and duration > MAX_OOXML_MILLISECONDS:
        errors.append(
            'object-animation behavior duration exceeds the OOXML '
            f'millisecond limit: {duration}'
        )
    return duration


def _read_animation_color(
    row: ET.Element,
    errors: list[str],
    label: str,
) -> str | None:
    from pptx_animations import (
        _DML_NS,
        _qn,
    )
    colors = [
        element
        for element in row.iter()
        if element.tag in {
            _qn(_DML_NS, 'srgbClr'),
            _qn(_DML_NS, 'schemeClr'),
        }
    ]
    if not colors:
        errors.append(f'{label} is missing its color value')
        return None
    rendered = []
    for color in colors:
        if color.tag == _qn(_DML_NS, 'srgbClr'):
            value = color.get('val')
            rendered.append(f'#{value.upper()}' if value else '')
        else:
            value = color.get('val')
            rendered.append(f'theme:{value}' if value else '')
    unique = tuple(dict.fromkeys(value for value in rendered if value))
    if len(unique) != 1:
        errors.append(f'{label} contains inconsistent color values: {unique!r}')
        return None
    return unique[0] if unique else None


def _read_effect_options(
    row: ET.Element,
    effect: str | None,
    filter_name: str | None,
    errors: list[str],
) -> dict[str, object]:
    from pptx_animations import (
        ET,
        NATIVE_ANIMATIONS,
        PML_NS,
        _normalize_powerpoint_font_name,
        _qn,
        _read_animation_color,
        _row_filter,
        re,
    )
    if effect is None:
        return {}
    option_specs = NATIVE_ANIMATIONS[effect]['effectOptions']
    values: dict[str, object] = {}
    for name, spec in option_specs.items():
        option_type = spec['type']
        if option_type == 'enum':
            matches = []
            for value, variant_xml in spec['values'].items():
                variant = ET.fromstring(variant_xml)
                variant_filter = _row_filter(variant, variant.get('presetClass', ''), [])
                if variant.get('presetSubtype') != row.get('presetSubtype'):
                    continue
                if variant_filter != filter_name:
                    continue
                matches.append(value)
            if len(matches) != 1:
                errors.append(
                    f'animation effect {effect!r} option {name!r} could not be '
                    f'read from presetSubtype={row.get("presetSubtype")!r}, '
                    f'filter={filter_name!r}'
                )
                continue
            value: object = matches[0]
            if name == 'amount' and re.fullmatch(r'\d+', str(value)):
                value = int(str(value))
            values[name] = value
        elif name == 'amount' and effect == 'emphasis_spin':
            rotations = list(row.iter(_qn(PML_NS, 'animRot')))
            raw = rotations[0].get('by') if len(rotations) == 1 else None
            if raw is None or not re.fullmatch(r'-?\d+', raw):
                errors.append('emphasis_spin row has an invalid rotation amount')
            else:
                values[name] = int(raw) / 60000
        elif name == 'amount' and effect == 'emphasis_transparency':
            opacity_values = []
            for node in row.iter(_qn(PML_NS, 'set')):
                attributes = {
                    (attribute.text or '').strip()
                    for attribute in node.iter(_qn(PML_NS, 'attrName'))
                }
                if 'style.opacity' in attributes:
                    opacity_values.extend(
                        value.get('val')
                        for value in node.iter(_qn(PML_NS, 'strVal'))
                    )
            if len(opacity_values) != 1:
                errors.append('emphasis_transparency row has an invalid opacity')
            else:
                try:
                    values[name] = 1 - float(str(opacity_values[0]))
                except ValueError:
                    errors.append(
                        'emphasis_transparency row has a non-numeric opacity'
                    )
        elif name == 'color':
            color = _read_animation_color(
                row,
                errors,
                f'animation effect {effect!r} color option',
            )
            if color is not None:
                values[name] = color
        elif name == 'font_name':
            fonts = []
            for node in row.iter(_qn(PML_NS, 'set')):
                attributes = {
                    (attribute.text or '').strip()
                    for attribute in node.iter(_qn(PML_NS, 'attrName'))
                }
                if 'style.fontFamily' in attributes:
                    fonts.extend(
                        value.get('val')
                        for value in node.iter(_qn(PML_NS, 'strVal'))
                    )
            if len(fonts) != 1:
                errors.append(
                    'emphasis_change_font row must contain one font name'
                )
                continue
            try:
                values[name] = _normalize_powerpoint_font_name(
                    fonts[0],
                    'emphasis_change_font row font name',
                )
            except ValueError as exc:
                errors.append(str(exc))
        elif name == 'relative':
            motions = list(row.iter(_qn(PML_NS, 'animMotion')))
            if len(motions) != 1:
                errors.append(f'motion-path effect {effect!r} has no single path')
            else:
                values[name] = motions[0].get('pathEditMode') != 'fixed'
        elif name == 'size':
            scales = list(row.iter(_qn(PML_NS, 'animScale')))
            targets = (
                scales[0].findall(_qn(PML_NS, 'to'))
                if len(scales) == 1
                else []
            )
            raw = (
                targets[0].get('x')
                if len(targets) == 1
                else '100000' if not targets and len(scales) == 1 else None
            )
            if raw is None or not re.fullmatch(r'\d+', raw):
                errors.append('emphasis_grow_shrink row has an invalid size')
            else:
                values[name] = int(raw) / 1000
        else:
            errors.append(
                f'animation effect {effect!r} option {name!r} has no reader'
            )
    return values


