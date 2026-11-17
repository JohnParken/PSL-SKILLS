"""PPT Master motion core; responsibility-split, with stable original API.

MIT Copyright (c) 2025-2026 Hugo He.
Upstream commit: 7e0df6705de0cd24345210d31f07fcb5a32b6900.
"""
from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from pptx_animations import AnimationTarget
from typing import Mapping
from xml.etree import ElementTree as ET
from pptx_transitions import PML_NS

from pptx_animations_registry import ET, Mapping, NATIVE_ANIMATIONS, PML_NS, _DML_NS, _INTERPOLATED_BEHAVIOR_TAGS, _P14_NS, _REL_NS

def _format_decimal(value: float) -> str:
    """Format one finite decimal without exponent notation or negative zero."""
    rendered = f'{value:.9f}'.rstrip('0').rstrip('.')
    return '0' if rendered in {'', '-0'} else rendered


def _color_element(value: str) -> ET.Element:
    from pptx_animations import (
        ET,
        _DML_NS,
        _qn,
    )
    if value.startswith('#'):
        return ET.Element(_qn(_DML_NS, 'srgbClr'), {'val': value[1:]})
    return ET.Element(_qn(_DML_NS, 'schemeClr'), {'val': value.split(':', 1)[1]})


def _replace_animation_colors(row: ET.Element, value: str) -> None:
    from pptx_animations import (
        _DML_NS,
        _color_element,
        _qn,
    )
    parent_map = {
        child: parent
        for parent in row.iter()
        for child in list(parent)
    }
    color_tags = {
        _qn(_DML_NS, 'srgbClr'),
        _qn(_DML_NS, 'schemeClr'),
        _qn(_DML_NS, 'hslClr'),
        _qn(_DML_NS, 'scrgbClr'),
        _qn(_DML_NS, 'sysClr'),
        _qn(_DML_NS, 'prstClr'),
    }
    colors = [element for element in row.iter() if element.tag in color_tags]
    if not colors:
        raise RuntimeError('color-capable animation preset lost its color node')
    for color in colors:
        parent = parent_map[color]
        index = list(parent).index(color)
        parent.remove(color)
        parent.insert(index, _color_element(value))


def _set_effect_option_value(
    row: ET.Element,
    animation: str,
    name: str,
    value: object,
) -> None:
    from pptx_animations import (
        ET,
        PML_NS,
        _format_decimal,
        _qn,
        _replace_animation_colors,
    )
    if name == 'amount' and animation == 'emphasis_spin':
        rotations = list(row.iter(_qn(PML_NS, 'animRot')))
        if len(rotations) != 1:
            raise RuntimeError('emphasis_spin preset lost its p:animRot node')
        rotations[0].set('by', str(round(float(value) * 60000)))
        return
    if name == 'amount' and animation == 'emphasis_transparency':
        matched = False
        for node in row.iter(_qn(PML_NS, 'set')):
            attributes = {
                (attribute.text or '').strip()
                for attribute in node.iter(_qn(PML_NS, 'attrName'))
            }
            if 'style.opacity' not in attributes:
                continue
            values = list(node.iter(_qn(PML_NS, 'strVal')))
            if len(values) != 1:
                raise RuntimeError(
                    'emphasis_transparency preset lost its opacity value'
                )
            values[0].set('val', _format_decimal(1 - float(value)))
            matched = True
        if not matched:
            raise RuntimeError(
                'emphasis_transparency preset lost its opacity behavior'
            )
        return
    if name == 'color':
        _replace_animation_colors(row, str(value))
        return
    if name == 'font_name':
        matched = False
        for node in row.iter(_qn(PML_NS, 'set')):
            attributes = {
                (attribute.text or '').strip()
                for attribute in node.iter(_qn(PML_NS, 'attrName'))
            }
            if 'style.fontFamily' not in attributes:
                continue
            values = list(node.iter(_qn(PML_NS, 'strVal')))
            if len(values) != 1:
                raise RuntimeError(
                    'emphasis_change_font preset lost its font value'
                )
            values[0].set('val', str(value))
            matched = True
        if not matched:
            raise RuntimeError(
                'emphasis_change_font preset lost its font behavior'
            )
        return
    if name == 'relative':
        motions = list(row.iter(_qn(PML_NS, 'animMotion')))
        if len(motions) != 1:
            raise RuntimeError('motion-path preset lost its p:animMotion node')
        motions[0].set('pathEditMode', 'relative' if value else 'fixed')
        return
    if name == 'size':
        scales = list(row.iter(_qn(PML_NS, 'animScale')))
        if len(scales) != 1:
            raise RuntimeError(
                'emphasis_grow_shrink preset lost its p:animScale node'
        )
        amount = str(round(float(value) * 1000))
        targets = scales[0].findall(_qn(PML_NS, 'to'))
        if len(targets) > 1:
            raise RuntimeError(
                'emphasis_grow_shrink preset lost its scale value'
            )
        target = (
            targets[0]
            if targets
            else ET.SubElement(scales[0], _qn(PML_NS, 'to'))
        )
        target.set('x', amount)
        target.set('y', amount)
        return
    raise AssertionError(f'unhandled continuous animation option: {name}')


def _animation_row_for_options(
    animation: str,
    effect_options: Mapping[str, object],
) -> ET.Element:
    """Return the authored variant row with continuous options applied."""
    from pptx_animations import (
        ET,
        NATIVE_ANIMATIONS,
        _set_effect_option_value,
    )
    spec = NATIVE_ANIMATIONS[animation]
    option_specs = spec['effectOptions']
    row_xml = spec['rowXml']
    for name, value in effect_options.items():
        option_spec = option_specs[name]
        if option_spec['type'] != 'enum':
            continue
        key = str(value)
        row_xml = option_spec['values'][key]
    row = ET.fromstring(row_xml)
    for name, value in effect_options.items():
        if option_specs[name]['type'] == 'enum':
            continue
        _set_effect_option_value(row, animation, name, value)
    return row


def _interpolated_behavior_nodes(row: ET.Element) -> tuple[ET.Element, ...]:
    """Return behavior nodes that can carry PowerPoint bounce metadata."""
    from pptx_animations import (
        _INTERPOLATED_BEHAVIOR_TAGS,
        _local_name,
    )
    return tuple(
        node
        for node in row.iter()
        if _local_name(node.tag) in _INTERPOLATED_BEHAVIOR_TAGS
    )


def animation_effect_supports_bounce_end(
    effect: object,
    effect_options: object = None,
) -> bool:
    """Return whether one concrete effect has an interpolated behavior."""
    from pptx_animations import (
        _animation_row_for_options,
        _interpolated_behavior_nodes,
        normalize_animation_effect_request,
    )
    animation, options = normalize_animation_effect_request(
        effect,
        effect_options,
        allow_none=False,
        allow_modes=False,
    )
    if animation is None:
        raise AssertionError('concrete animation normalization returned none')
    row = _animation_row_for_options(animation, options)
    return bool(_interpolated_behavior_nodes(row))


def _apply_timing_options(row: ET.Element, target: AnimationTarget) -> None:
    from pptx_animations import (
        _P14_NS,
        _interpolated_behavior_nodes,
        _qn,
        animation_effect_supports_bounce_end,
    )
    if target.repeat_count is not None:
        row.set('repeatCount', str(round(target.repeat_count * 1000)))
        row.attrib.pop('repeatDur', None)
    if target.repeat_duration_ms is not None:
        row.set('repeatDur', str(target.repeat_duration_ms))
        row.attrib.pop('repeatCount', None)
    if target.auto_reverse is not None:
        if target.auto_reverse:
            row.set('autoRev', '1')
        else:
            row.attrib.pop('autoRev', None)
    if target.rewind is not None:
        row.set('fill', 'remove' if target.rewind else 'hold')
    if target.accelerate is not None:
        if target.accelerate:
            row.set('accel', str(round(target.accelerate * 100000)))
        else:
            row.attrib.pop('accel', None)
    if target.decelerate is not None:
        if target.decelerate:
            row.set('decel', str(round(target.decelerate * 100000)))
        else:
            row.attrib.pop('decel', None)
    if target.bounce_end is not None:
        bounce_nodes = _interpolated_behavior_nodes(row)
        if target.bounce_end and not animation_effect_supports_bounce_end(
            target.effect,
            target.effect_options,
        ):
            raise ValueError(
                f'animation effect {target.effect!r} has no behavior that '
                'supports bounce_end'
            )
        if target.bounce_end:
            row.set(
                _qn(_P14_NS, 'presetBounceEnd'),
                str(round(target.bounce_end * 100000)),
            )
        else:
            row.attrib.pop(_qn(_P14_NS, 'presetBounceEnd'), None)
        for node in bounce_nodes:
            if target.bounce_end:
                node.set(
                    _qn(_P14_NS, 'bounceEnd'),
                    str(round(target.bounce_end * 100000)),
                )
            else:
                node.attrib.pop(_qn(_P14_NS, 'bounceEnd'), None)
    if target.restart is not None:
        row.set(
            'restart',
            {
                'always': 'always',
                'when-not-active': 'whenNotActive',
                'never': 'never',
            }[target.restart],
        )


def _append_after_effect(
    row: ET.Element,
    target: AnimationTarget,
    row_id: int,
) -> None:
    from pptx_animations import (
        ET,
        PML_NS,
        _color_element,
        _qn,
    )
    if target.after_effect == 'none':
        return
    sub_timing = row.find(_qn(PML_NS, 'subTnLst'))
    if sub_timing is None:
        sub_timing = ET.SubElement(row, _qn(PML_NS, 'subTnLst'))
    if target.after_effect == 'dim':
        animation = ET.SubElement(
            sub_timing,
            _qn(PML_NS, 'animClr'),
            {'clrSpc': 'rgb', 'dir': 'cw'},
        )
        behavior = ET.SubElement(
            animation,
            _qn(PML_NS, 'cBhvr'),
            {'override': 'childStyle'},
        )
        ET.SubElement(
            behavior,
            _qn(PML_NS, 'cTn'),
            {
                'dur': '1',
                'fill': 'hold',
                'display': '0',
                'masterRel': 'nextClick',
                'afterEffect': '1',
            },
        )
        target_element = ET.SubElement(behavior, _qn(PML_NS, 'tgtEl'))
        ET.SubElement(
            target_element,
            _qn(PML_NS, 'spTgt'),
            {'spid': str(target.shape_id)},
        )
        names = ET.SubElement(behavior, _qn(PML_NS, 'attrNameLst'))
        ET.SubElement(names, _qn(PML_NS, 'attrName')).text = 'ppt_c'
        destination = ET.SubElement(animation, _qn(PML_NS, 'to'))
        destination.append(_color_element(str(target.after_effect_color)))
        return

    setting = ET.SubElement(sub_timing, _qn(PML_NS, 'set'))
    behavior = ET.SubElement(
        setting,
        _qn(PML_NS, 'cBhvr'),
        {'override': 'childStyle'},
    )
    ctn_attributes = {
        'dur': '1',
        'fill': 'hold',
        'display': '0',
        'masterRel': (
            'sameClick'
            if target.after_effect == 'hide'
            else 'nextClick'
        ),
        'afterEffect': '1',
    }
    ctn = ET.SubElement(behavior, _qn(PML_NS, 'cTn'), ctn_attributes)
    if target.after_effect == 'hide':
        conditions = ET.SubElement(ctn, _qn(PML_NS, 'stCondLst'))
        condition = ET.SubElement(
            conditions,
            _qn(PML_NS, 'cond'),
            {'evt': 'end', 'delay': '0'},
        )
        ET.SubElement(condition, _qn(PML_NS, 'tn'), {'val': str(row_id)})
    target_element = ET.SubElement(behavior, _qn(PML_NS, 'tgtEl'))
    ET.SubElement(
        target_element,
        _qn(PML_NS, 'spTgt'),
        {'spid': str(target.shape_id)},
    )
    names = ET.SubElement(behavior, _qn(PML_NS, 'attrNameLst'))
    ET.SubElement(names, _qn(PML_NS, 'attrName')).text = 'style.visibility'
    destination = ET.SubElement(setting, _qn(PML_NS, 'to'))
    ET.SubElement(destination, _qn(PML_NS, 'strVal'), {'val': 'hidden'})


def _append_animation_sound(row: ET.Element, target: AnimationTarget) -> None:
    from pptx_animations import (
        ET,
        PML_NS,
        _REL_NS,
        _qn,
    )
    if target.sound_relationship_id is None:
        return
    sub_timing = row.find(_qn(PML_NS, 'subTnLst'))
    if sub_timing is None:
        sub_timing = ET.SubElement(row, _qn(PML_NS, 'subTnLst'))
    audio = ET.SubElement(sub_timing, _qn(PML_NS, 'audio'))
    media = ET.SubElement(audio, _qn(PML_NS, 'cMediaNode'))
    ctn = ET.SubElement(
        media,
        _qn(PML_NS, 'cTn'),
        {'display': '0', 'masterRel': 'sameClick'},
    )
    conditions = ET.SubElement(ctn, _qn(PML_NS, 'stCondLst'))
    condition = ET.SubElement(
        conditions,
        _qn(PML_NS, 'cond'),
        {'evt': 'begin', 'delay': '0'},
    )
    ET.SubElement(condition, _qn(PML_NS, 'tn'), {'val': str(row.get('id'))})
    end_conditions = ET.SubElement(ctn, _qn(PML_NS, 'endCondLst'))
    end_condition = ET.SubElement(
        end_conditions,
        _qn(PML_NS, 'cond'),
        {'evt': 'onStopAudio', 'delay': '0'},
    )
    target_element = ET.SubElement(end_condition, _qn(PML_NS, 'tgtEl'))
    ET.SubElement(target_element, _qn(PML_NS, 'sldTgt'))
    target_element = ET.SubElement(media, _qn(PML_NS, 'tgtEl'))
    ET.SubElement(
        target_element,
        _qn(PML_NS, 'sndTgt'),
        {
            _qn(_REL_NS, 'embed'): str(target.sound_relationship_id),
            'name': str(target.sound_name),
        },
    )


