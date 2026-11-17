"""PPT Master motion core; responsibility-split, with stable original API.

MIT Copyright (c) 2025-2026 Hugo He.
Upstream commit: 7e0df6705de0cd24345210d31f07fcb5a32b6900.
"""
from __future__ import annotations
from typing import Any, Mapping
from xml.etree import ElementTree as ET
from pptx_transitions import PML_NS

from pptx_animations_registry import Any, ET, Mapping, PML_NS

def _semantic_effect(group_id: str | None, idx: int = 0, offset: int = 0) -> str | None:
    """Return the effect mapped from a group id, or None if no pattern matches.

    Image-like ids cycle through ``_IMAGE_POOL`` using ``idx + offset`` so the
    same deck shows different effects across multiple images. Other semantic
    matches return a single stable effect because information-dense elements
    benefit from consistency, not variation.
    """
    from pptx_animations import (
        _IMAGE_KEYWORDS,
        _IMAGE_POOL,
        _SEMANTIC_PATTERNS,
    )
    if not group_id:
        return None
    lower = group_id.lower()
    if any(k in lower for k in _IMAGE_KEYWORDS):
        return _IMAGE_POOL[(idx + offset) % len(_IMAGE_POOL)]
    for substrings, effect in _SEMANTIC_PATTERNS:
        if any(s in lower for s in substrings):
            return effect
    return None


def create_timing_xml(
    animation: str = 'entrance_fade',
    duration: float = 1.0,
    delay: float = 0,
    shape_id: int = 2
) -> str:
    """
    Generate an object-animation timing XML fragment

    Args:
        animation: Canonical PowerPoint effect name
        duration: Animation duration (seconds)
        delay: Animation delay (seconds)
        shape_id: Target shape ID (SVG image is typically 2)

    Returns:
        A <p:timing> element string insertable into slide XML
    """
    from pptx_animations import (
        _positive_shape_id,
        _seconds_to_ms,
        create_sequence_timing_xml,
        normalize_animation_effect,
    )
    animation = normalize_animation_effect(
        animation,
        allow_none=False,
        allow_modes=False,
    )
    shape_id = _positive_shape_id(shape_id)
    delay_ms = _seconds_to_ms(
        delay,
        'animation delay',
        allow_zero=True,
    )
    return create_sequence_timing_xml(
        [(shape_id, delay_ms, animation, duration)],
        duration=duration,
        trigger='after-previous',
    )


def _ctn_numeric_delay(ctn: ET.Element) -> int:
    """Return the largest direct numeric start delay on one time node."""
    from pptx_animations import (
        PML_NS,
        _qn,
    )
    conditions = ctn.find(_qn(PML_NS, 'stCondLst'))
    if conditions is None:
        return 0
    values = [
        int(condition.get('delay', '0'))
        for condition in conditions.findall(_qn(PML_NS, 'cond'))
        if condition.get('delay', '').isdigit()
    ]
    return max(values, default=0)


def _row_effective_duration_ms(row: ET.Element) -> int | None:
    """Return the finite end time PowerPoint exposes for one preset row."""
    from pptx_animations import (
        PML_NS,
        _ctn_numeric_delay,
        _qn,
    )
    ends: list[int] = []
    for ctn in row.iter(_qn(PML_NS, 'cTn')):
        if ctn is row:
            continue
        raw_duration = ctn.get('dur')
        if raw_duration is None or not raw_duration.isdigit():
            continue
        ends.append(_ctn_numeric_delay(ctn) + int(raw_duration))
    return max(ends) if ends else None


def _scale_animation_row_duration(
    row: ET.Element,
    *,
    base_duration_ms: int,
    requested_duration_ms: int,
) -> None:
    """Scale all finite behavior durations/delays as one PowerPoint row."""
    from pptx_animations import (
        PML_NS,
        _ctn_numeric_delay,
        _qn,
        _row_effective_duration_ms,
    )
    ratio = requested_duration_ms / base_duration_ms
    for ctn in row.iter(_qn(PML_NS, 'cTn')):
        if ctn is row:
            continue
        raw_duration = ctn.get('dur')
        if raw_duration is not None and raw_duration.isdigit():
            numeric_duration = int(raw_duration)
            ctn.set(
                'dur',
                (
                    '1'
                    if numeric_duration == 1
                    else str(max(1, round(numeric_duration * ratio)))
                ),
            )
        conditions = ctn.find(_qn(PML_NS, 'stCondLst'))
        if conditions is None:
            continue
        for condition in conditions.findall(_qn(PML_NS, 'cond')):
            raw_delay = condition.get('delay')
            if raw_delay is not None and raw_delay.isdigit():
                condition.set('delay', str(round(int(raw_delay) * ratio)))

    actual_duration = _row_effective_duration_ms(row)
    if actual_duration is None or actual_duration == requested_duration_ms:
        return
    end_nodes = [
        ctn
        for ctn in row.iter(_qn(PML_NS, 'cTn'))
        if ctn is not row
        and (ctn.get('dur') or '').isdigit()
        and _ctn_numeric_delay(ctn) + int(ctn.get('dur', '0')) == actual_duration
    ]
    for ctn in end_nodes:
        delay = _ctn_numeric_delay(ctn)
        if delay >= requested_duration_ms:
            conditions = ctn.find(_qn(PML_NS, 'stCondLst'))
            numeric = (
                [
                    condition
                    for condition in conditions.findall(_qn(PML_NS, 'cond'))
                    if condition.get('delay', '').isdigit()
                ]
                if conditions is not None
                else []
            )
            if numeric:
                numeric[-1].set('delay', str(max(0, requested_duration_ms - 1)))
                delay = _ctn_numeric_delay(ctn)
        ctn.set('dur', str(max(1, requested_duration_ms - delay)))


def _row_semantic_signature(row: ET.Element) -> tuple[object, ...]:
    """Return an id/target/duration-independent preset behavior signature."""
    from pptx_animations import (
        ET,
        _local_name,
    )
    def canonical(element: ET.Element) -> tuple[object, ...]:
        attributes: list[tuple[str, str]] = []
        local_name = _local_name(element.tag)
        for name, value in sorted(element.attrib.items()):
            if name in {'id', 'nodeType', 'grpId'}:
                continue
            if local_name == 'spTgt' and name == 'spid':
                value = '#shape'
            elif local_name == 'cTn' and name == 'dur' and value.isdigit():
                value = '#duration'
            elif local_name == 'cond' and name == 'delay' and value.isdigit():
                value = '#delay'
            attributes.append((name, value))
        return (
            element.tag,
            tuple(attributes),
            (element.text or '').strip(),
            tuple(canonical(child) for child in list(element)),
        )

    return canonical(row)


def _row_timing_profile(
    row: ET.Element,
) -> tuple[tuple[str, float, float], ...]:
    """Return behavior timing ratios, excluding 1ms visibility bookkeeping."""
    from pptx_animations import (
        PML_NS,
        _ctn_numeric_delay,
        _local_name,
        _qn,
        _row_effective_duration_ms,
    )
    total = _row_effective_duration_ms(row)
    if total is None or total <= 1:
        return ()
    parent_map = {
        child: parent
        for parent in row.iter()
        for child in list(parent)
    }
    behavior_names = {
        'anim',
        'animClr',
        'animEffect',
        'animMotion',
        'animRot',
        'animScale',
        'set',
    }
    profile: list[tuple[str, float, float]] = []
    for ctn in row.iter(_qn(PML_NS, 'cTn')):
        if ctn is row:
            continue
        raw_duration = ctn.get('dur')
        if raw_duration is None or not raw_duration.isdigit():
            continue
        duration = int(raw_duration)
        owner = parent_map.get(ctn)
        while owner is not None and _local_name(owner.tag) not in behavior_names:
            owner = parent_map.get(owner)
        behavior = _local_name(owner.tag) if owner is not None else 'unknown'
        if (
            duration == 1
            and behavior == 'set'
            and owner is not None
            and any(
                (attribute.text or '') == 'style.visibility'
                for attribute in owner.iter(_qn(PML_NS, 'attrName'))
            )
        ):
            continue
        profile.append(
            (
                behavior,
                _ctn_numeric_delay(ctn) / total,
                duration / total,
            )
        )
    return tuple(profile)


def _animation_spec_matches_row(row: ET.Element, spec: Mapping[str, Any]) -> bool:
    """Require the PowerPoint-authored structure and internal timing ratios."""
    from pptx_animations import (
        ET,
        _row_effective_duration_ms,
        _row_semantic_signature,
        _row_timing_profile,
    )
    template = ET.fromstring(spec['rowXml'])
    if _row_semantic_signature(row) != _row_semantic_signature(template):
        return False
    actual_duration = _row_effective_duration_ms(row)
    if actual_duration is not None and actual_duration < 100:
        # Millisecond quantization necessarily collapses multi-step ratios at
        # sub-100ms speeds; structure and total duration remain authoritative.
        return True
    actual_profile = _row_timing_profile(row)
    template_profile = _row_timing_profile(template)
    if len(actual_profile) != len(template_profile):
        return False
    for actual, expected in zip(actual_profile, template_profile):
        if actual[0] != expected[0]:
            return False
        if abs(actual[1] - expected[1]) > 0.01:
            return False
        if abs(actual[2] - expected[2]) > 0.01:
            return False
    return True


