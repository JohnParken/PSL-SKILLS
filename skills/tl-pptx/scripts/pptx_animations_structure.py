"""PPT Master motion core; responsibility-split, with stable original API.

MIT Copyright (c) 2025-2026 Hugo He.
Upstream commit: 7e0df6705de0cd24345210d31f07fcb5a32b6900.
"""
from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from pptx_animations import AnimationSequenceSummary
from xml.etree import ElementTree as ET
from pptx_transitions import MAX_OOXML_UNSIGNED_INT, PML_NS

from pptx_animations_registry import ET, MAX_OOXML_UNSIGNED_INT, PML_NS, _PRESET_CLASS_BY_CATEGORY

def validate_slide_animation_structure(
    slide_root: ET.Element,
    *,
    require_supported_effects: bool = False,
) -> list[str]:
    """Return root timing, target, and generated-object structure errors."""
    from pptx_animations import (
        MAX_OOXML_UNSIGNED_INT,
        PML_NS,
        _PRESET_CLASS_BY_CATEGORY,
        _animation_rows,
        _int_attribute,
        _local_name,
        _qn,
        _select_supported_timing_branch,
        _shape_index,
    )
    errors: list[str] = []
    if slide_root.tag != _qn(PML_NS, 'sld'):
        return ['animation validation requires a PresentationML p:sld root']
    slide_root = _select_supported_timing_branch(slide_root)

    direct_timings = [
        child for child in list(slide_root)
        if child.tag == _qn(PML_NS, 'timing')
    ]
    all_timings = list(slide_root.iter(_qn(PML_NS, 'timing')))
    nested_count = len(all_timings) - len(direct_timings)
    if nested_count:
        errors.append(
            f'slide contains {nested_count} nested p:timing element(s); '
            'timing must be a direct child of p:sld'
        )
    if len(direct_timings) > 1:
        errors.append(
            f'slide has {len(direct_timings)} root p:timing elements; expected at most 1'
        )
    if not direct_timings:
        return errors

    timing = direct_timings[0]
    root_children = list(slide_root)
    timing_index = root_children.index(timing)
    for required_before in ('cSld', 'clrMapOvr', 'transition'):
        sibling = next(
            (
                child for child in root_children
                if child.tag == _qn(PML_NS, required_before)
            ),
            None,
        )
        if sibling is not None and root_children.index(sibling) > timing_index:
            errors.append(f'p:{required_before} must precede p:timing')
    extension_list = next(
        (
            child for child in root_children
            if child.tag == _qn(PML_NS, 'extLst')
        ),
        None,
    )
    if extension_list is not None and root_children.index(extension_list) < timing_index:
        errors.append('root p:extLst must follow p:timing')

    timing_children = list(timing)
    timing_name_order = [_local_name(child.tag) for child in timing_children]
    if 'bldLst' in timing_name_order and 'tnLst' in timing_name_order:
        if timing_name_order.index('bldLst') < timing_name_order.index('tnLst'):
            errors.append('p:tnLst must precede p:bldLst')

    parent_map = {
        child: parent
        for parent in timing.iter()
        for child in list(parent)
    }
    ctn_ids: list[int] = []
    for ctn in timing.iter(_qn(PML_NS, 'cTn')):
        if ctn.get('id') is None:
            ancestor = parent_map.get(ctn)
            while ancestor is not None and ancestor is not timing:
                if ancestor.tag == _qn(PML_NS, 'subTnLst'):
                    break
                ancestor = parent_map.get(ancestor)
            if (
                ancestor is not None
                and ancestor.tag == _qn(PML_NS, 'subTnLst')
            ):
                continue
        value = _int_attribute(
            ctn,
            'id',
            'p:cTn@id',
            errors,
            maximum=MAX_OOXML_UNSIGNED_INT,
        )
        if value is not None:
            ctn_ids.append(value)
    duplicates = sorted(
        value for value in set(ctn_ids) if ctn_ids.count(value) > 1
    )
    if duplicates:
        errors.append(
            'duplicate p:cTn@id values: ' + ', '.join(map(str, duplicates))
        )

    roots = [
        node for node in timing.iter(_qn(PML_NS, 'cTn'))
        if node.get('nodeType') == 'tmRoot'
    ]
    if len(roots) != 1:
        errors.append(
            f'p:timing must contain exactly one tmRoot time node; found {len(roots)}'
        )

    shape_index, shape_errors = _shape_index(slide_root)
    errors.extend(shape_errors)
    for target in timing.iter(_qn(PML_NS, 'spTgt')):
        shape_id = _int_attribute(
            target,
            'spid',
            'p:spTgt@spid',
            errors,
            minimum=1,
            maximum=MAX_OOXML_UNSIGNED_INT,
        )
        if shape_id is not None and shape_id not in shape_index:
            errors.append(f'p:spTgt references missing shape id {shape_id}')

    build_keys: list[tuple[int, int]] = []
    for build in timing.iter(_qn(PML_NS, 'bldP')):
        shape_id = _int_attribute(
            build,
            'spid',
            'p:bldP@spid',
            errors,
            minimum=1,
            maximum=MAX_OOXML_UNSIGNED_INT,
        )
        group_id = _int_attribute(
            build,
            'grpId',
            'p:bldP@grpId',
            errors,
            maximum=MAX_OOXML_UNSIGNED_INT,
        )
        if shape_id is None or group_id is None:
            continue
        build_keys.append((shape_id, group_id))
        kind, has_text = shape_index.get(shape_id, ('missing', False))
        if kind == 'missing':
            errors.append(f'p:bldP references missing shape id {shape_id}')
        elif require_supported_effects and (kind != 'sp' or not has_text):
            errors.append(
                f'p:bldP shape id {shape_id} must reference a text-bearing p:sp; '
                f'found {kind}'
            )
    if len(build_keys) != len(set(build_keys)):
        errors.append('p:bldP (spid, grpId) pairs must be unique')

    animation_nodes = [
        node for node in timing.iter(_qn(PML_NS, 'cTn'))
        if node.get('presetClass') in set(_PRESET_CLASS_BY_CATEGORY.values())
    ]
    if require_supported_effects:
        rows = _animation_rows(
            slide_root,
            errors,
            require_behavior_signatures=True,
        )
        if not rows and animation_nodes:
            errors.append('generated object-animation rows could not be read back')
    else:
        rows = []
    if rows:
        regular_rows = [
            row for row in rows if row.trigger_shape_id is None
        ]
        interactive_rows = [
            row for row in rows if row.trigger_shape_id is not None
        ]
        main_sequences = [
            node for node in timing.iter(_qn(PML_NS, 'cTn'))
            if node.get('nodeType') == 'mainSeq'
        ]
        expected_main_sequences = 1 if regular_rows else 0
        if len(main_sequences) != expected_main_sequences:
            errors.append(
                'generated object-animation rows require '
                f'{expected_main_sequences} mainSeq time node(s); found '
                f'{len(main_sequences)}'
            )
        interactive_sequences = [
            node for node in timing.iter(_qn(PML_NS, 'cTn'))
            if node.get('nodeType') == 'interactiveSeq'
        ]
        if len(interactive_sequences) != len(interactive_rows):
            errors.append(
                'each generated trigger-shape animation must have one '
                'interactiveSeq time node'
            )
        for row in rows:
            if (
                row.trigger_shape_id is not None
                and row.trigger != 'on-click'
            ):
                errors.append(
                    'trigger-shape animation must use the on-click Start mode '
                    f'for shape {row.shape_id}'
                )
            if row.trigger_shape_id == row.shape_id:
                errors.append(
                    'animation target and trigger shape must differ for shape '
                    f'{row.shape_id}'
                )
            if not row.supported_effects:
                errors.append(
                    'unsupported object-animation effect tuple for shape '
                    f'{row.shape_id}: presetClass={row.preset_class}, '
                    f'presetID={row.preset_id}, '
                    f'presetSubtype={row.preset_subtype}, '
                    f'filter={row.filter_name!r}'
                )
    return errors


def read_slide_click_dependencies(slide_xml: str | bytes) -> tuple[int, ...]:
    """Read native click/interactive targets without requiring supported effects."""
    from pptx_animations import (
        ET,
        PML_NS,
        _qn,
    )
    root = ET.fromstring(slide_xml)
    targets: set[int] = set()
    for timing in root.iter(_qn(PML_NS, 'timing')):
        for node in timing.iter(_qn(PML_NS, 'cTn')):
            conditions = node.findall(f"{_qn(PML_NS, 'stCondLst')}/{_qn(PML_NS, 'cond')}")
            interactive = node.get('nodeType') in {'clickEffect', 'interactiveSeq'} or any(
                condition.get('evt') in {'onClick', 'onDblClick', 'onMouseOver', 'onMouseOut', 'onNext', 'onPrev'}
                for condition in conditions
            )
            if interactive:
                targets.update(
                    int(value) for target in node.iter(_qn(PML_NS, 'spTgt'))
                    if (value := target.get('spid', '')).isdigit()
                )
    return tuple(sorted(targets))


def read_slide_animation_sequence(
    slide_xml: str | bytes,
    *,
    require_supported_effects: bool = False,
) -> AnimationSequenceSummary:
    """Read and validate the logical object-animation sequence from slide XML."""
    from pptx_animations import (
        AnimationSequenceSummary,
        ET,
        PML_NS,
        _animation_rows,
        _qn,
        _select_supported_timing_branch,
        validate_slide_animation_structure,
    )
    data = slide_xml.encode('utf-8') if isinstance(slide_xml, str) else slide_xml
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise ValueError(f'invalid slide XML: {exc}') from exc
    root = _select_supported_timing_branch(root)
    errors = validate_slide_animation_structure(
        root,
        require_supported_effects=require_supported_effects,
    )
    row_errors: list[str] = []
    rows = _animation_rows(
        root,
        row_errors,
        require_behavior_signatures=require_supported_effects,
    )
    for error in row_errors:
        if error not in errors:
            errors.append(error)
    if errors:
        raise ValueError('; '.join(errors))
    direct_timings = [
        child for child in list(root)
        if child.tag == _qn(PML_NS, 'timing')
    ]
    audio_targets: list[int] = []
    for audio in root.iter(_qn(PML_NS, 'audio')):
        for target in audio.iter(_qn(PML_NS, 'spTgt')):
            value = target.get('spid')
            if value and value.isdigit():
                audio_targets.append(int(value))
    regular_triggers = {
        row.trigger for row in rows if row.trigger_shape_id is None
    }
    trigger = (
        next(iter(regular_triggers))
        if len(regular_triggers) == 1
        else ('on-click' if rows and not regular_triggers else None)
    )
    return AnimationSequenceSummary(
        timing_count=len(direct_timings),
        trigger=trigger,
        rows=tuple(rows),
        audio_target_ids=tuple(audio_targets),
    )


