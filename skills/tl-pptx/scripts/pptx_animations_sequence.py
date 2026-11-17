"""PPT Master motion core; responsibility-split, with stable original API.

MIT Copyright (c) 2025-2026 Hugo He.
Upstream commit: 7e0df6705de0cd24345210d31f07fcb5a32b6900.
"""
from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from pptx_animations import AnimationTarget
import random
import re
from typing import Sequence
from xml.etree import ElementTree as ET
from pptx_transitions import MAX_OOXML_MILLISECONDS, PML_NS

from pptx_animations_registry import ET, MAX_OOXML_MILLISECONDS, NATIVE_ANIMATIONS, PML_NS, Sequence, _MC_NS, _P14_NS, _TRIGGER_NODE_TYPES, random, re

def _instantiate_animation_row(
    target: AnimationTarget,
    node_type: str,
    row_id: int,
    first_behavior_id: int,
) -> tuple[str, int]:
    """Instantiate one PowerPoint-authored preset row for a generated shape."""
    from pptx_animations import (
        ET,
        NATIVE_ANIMATIONS,
        PML_NS,
        _animation_row_for_options,
        _append_after_effect,
        _append_animation_sound,
        _apply_timing_options,
        _qn,
        _scale_animation_row_duration,
    )
    animation = target.effect
    shape_id = target.shape_id
    duration_ms = target.duration_ms
    spec = NATIVE_ANIMATIONS[animation]
    row = _animation_row_for_options(animation, target.effect_options)
    row.set('id', str(row_id))
    row.set('nodeType', node_type)
    conditions = row.find(_qn(PML_NS, 'stCondLst'))
    if conditions is None:
        raise RuntimeError(f'animation preset {animation!r} lost p:stCondLst')
    direct_conditions = conditions.findall(_qn(PML_NS, 'cond'))
    if len(direct_conditions) != 1:
        raise RuntimeError(
            f'animation preset {animation!r} must have one start condition'
        )
    direct_conditions[0].attrib.clear()
    direct_conditions[0].set(
        'delay',
        str(target.delay_ms),
    )

    if spec['durationScalable']:
        base_duration_ms = int(spec['defaultDurationMs'])
        _scale_animation_row_duration(
            row,
            base_duration_ms=base_duration_ms,
            requested_duration_ms=duration_ms,
        )

    _apply_timing_options(row, target)
    _append_after_effect(row, target, row_id)
    _append_animation_sound(row, target)

    next_id = first_behavior_id
    for ctn in row.iter(_qn(PML_NS, 'cTn')):
        if ctn is row:
            continue
        ctn.set('id', str(next_id))
        next_id += 1
    for target in row.iter(_qn(PML_NS, 'spTgt')):
        target.set('spid', str(shape_id))
    return ET.tostring(row, encoding='unicode'), next_id


def _build_animation_row_xml(
    target: AnimationTarget,
    trigger: str,
    row_id: int,
    first_behavior_id: int,
) -> tuple[str, int]:
    """Build one canonical PowerPoint-authored animation-pane row."""
    from pptx_animations import (
        _TRIGGER_NODE_TYPES,
        _instantiate_animation_row,
    )
    node_type = _TRIGGER_NODE_TYPES[trigger]
    return _instantiate_animation_row(
        target,
        node_type,
        row_id,
        first_behavior_id,
    )


def _main_target_offsets(targets: Sequence[AnimationTarget]) -> list[int]:
    """Return each regular row's start offset within its click group."""
    from pptx_animations import (
        MAX_OOXML_MILLISECONDS,
    )
    offsets: list[int] = []
    beat_start_ms = 0
    timeline_end_ms = 0
    for target in targets:
        if target.trigger == 'on-click':
            beat_start_ms = 0
            timeline_end_ms = 0
        elif target.trigger == 'after-previous':
            beat_start_ms = timeline_end_ms
        # With-previous rows share the beat's trigger, before any row delay.
        start_ms = beat_start_ms + target.delay_ms
        if start_ms > MAX_OOXML_MILLISECONDS:
            raise ValueError(
                'animation sequence offset exceeds the OOXML millisecond '
                f'limit at target {len(offsets) + 1}: {start_ms}'
            )
        offsets.append(start_ms)
        timeline_end_ms = max(timeline_end_ms, start_ms + target.playback_duration_ms)
    return offsets


def _build_mixed_main_steps(
    targets: Sequence[AnimationTarget],
    next_id: int,
) -> tuple[str, int]:
    """Build click groups with one shared wrapper per semantic beat."""
    from pptx_animations import (
        _build_animation_row_xml,
        _main_target_offsets,
    )
    offsets = _main_target_offsets(targets)
    groups: list[list[tuple[AnimationTarget, int]]] = []
    for target, offset_ms in zip(targets, offsets):
        if not groups or target.trigger == 'on-click':
            groups.append([])
        groups[-1].append((target, offset_ms))

    rendered_groups: list[str] = []
    for group in groups:
        group_id = next_id
        next_id += 1
        first_target = group[0][0]
        if first_target.trigger == 'on-click':
            group_conditions = '<p:cond delay="indefinite"/>'
        else:
            group_conditions = (
                '<p:cond delay="indefinite"/>'
                '<p:cond evt="onBegin" delay="0"><p:tn val="2"/></p:cond>'
            )
        beats: list[list[tuple[AnimationTarget, int]]] = []
        for target, offset_ms in group:
            if not beats or target.trigger != 'with-previous':
                beats.append([])
            beats[-1].append((target, offset_ms))

        rendered_beats: list[str] = []
        for beat in beats:
            wrapper_id = next_id
            next_id += 1
            leader, offset_ms = beat[0]
            wrapper_offset_ms = offset_ms - leader.delay_ms
            rendered_rows: list[str] = []
            for target, _ in beat:
                row_xml, next_id = _build_animation_row_xml(
                    target,
                    target.trigger,
                    next_id,
                    next_id + 1,
                )
                rendered_rows.append(f'<p:par>{row_xml}</p:par>')
            rows_xml = '\n                      '.join(rendered_rows)
            rendered_beats.append(f'''<p:par>
                  <p:cTn id="{wrapper_id}" fill="hold">
                    <p:stCondLst><p:cond delay="{wrapper_offset_ms}"/></p:stCondLst>
                    <p:childTnLst>
                      {rows_xml}
                    </p:childTnLst>
                  </p:cTn>
                </p:par>''')
        beats_xml = '\n                    '.join(rendered_beats)
        rendered_groups.append(f'''<p:par>
                <p:cTn id="{group_id}" fill="hold">
                  <p:stCondLst>{group_conditions}</p:stCondLst>
                  <p:childTnLst>
                    {beats_xml}
                  </p:childTnLst>
                </p:cTn>
              </p:par>''')
    return '\n              '.join(rendered_groups), next_id


def create_sequence_timing_xml(
    targets: list,
    duration: float = 0.3,
    trigger: str = 'after-previous',
) -> str:
    """Generate a multi-target object-animation sequence.

    Args:
        targets: list of (shape_id, delay_ms, animation_name) or
            (shape_id, delay_ms, animation_name, duration_seconds) tuples, in
            animation-pane order. ``delay_ms`` is measured from the beat
            start, before its leader's own delay. Mapping targets
            may set an independent ``trigger``; otherwise they inherit the
            function-level ``trigger``. A target with ``trigger_shape_id``
            must use ``on-click`` and runs in an interactive sequence.
        duration: per-element animation duration in seconds. Instantaneous
            native presets retain their PowerPoint-authored duration.
        trigger: PowerPoint-standard Start mode for each element.
            ``'after-previous'`` — first element fires on slide entry,
            rest wait for all earlier rows in the click group to finish,
            then apply ``delay_ms`` spacing
            (default).
            ``'on-click'`` — one presenter click per element.
            ``'with-previous'`` — all elements share the slide-entry beat,
            each with its own ``delay_ms``.

    Returns:
        A ``<p:timing>`` element string. Returns an empty string when
        ``targets`` is empty.
    """
    from pptx_animations import (
        _MC_NS,
        _P14_NS,
        _build_animation_row_xml,
        _build_mixed_main_steps,
        _normalize_target,
        _seconds_to_ms,
        normalize_animation_trigger,
        re,
    )
    trigger = normalize_animation_trigger(trigger)
    default_dur_ms = _seconds_to_ms(
        duration,
        'animation duration',
        allow_zero=False,
    )
    if targets is None or isinstance(targets, (str, bytes)):
        raise ValueError('animation targets must be a sequence of target tuples')
    if not targets:
        return ''
    normalized_targets = [
        _normalize_target(target, default_dur_ms, trigger)
        for target in targets
    ]
    next_id = 3
    main_targets = [
        target
        for target in normalized_targets
        if target.trigger_shape_id is None
    ]
    interactive_targets = [
        target
        for target in normalized_targets
        if target.trigger_shape_id is not None
    ]

    all_steps, next_id = _build_mixed_main_steps(main_targets, next_id)

    if main_targets:
        main_sequence_xml = f'''<p:seq concurrent="1" nextAc="seek">
              <p:cTn id="2" dur="indefinite" nodeType="mainSeq">
                <p:childTnLst>
              {all_steps}
                </p:childTnLst>
              </p:cTn>
              <p:prevCondLst><p:cond evt="onPrev" delay="0"><p:tgtEl><p:sldTgt/></p:tgtEl></p:cond></p:prevCondLst>
              <p:nextCondLst><p:cond evt="onNext" delay="0"><p:tgtEl><p:sldTgt/></p:tgtEl></p:cond></p:nextCondLst>
            </p:seq>'''
    else:
        main_sequence_xml = ''
        next_id = 2

    interactive_sequences: list[str] = []
    for target in interactive_targets:
        sequence_id = next_id
        wrapper_id = next_id + 1
        inner_id = next_id + 2
        row_id = next_id + 3
        row_xml, next_id = _build_animation_row_xml(
            target,
            'on-click',
            row_id,
            next_id + 4,
        )
        trigger_shape_id = target.trigger_shape_id
        interactive_sequences.append(f'''<p:seq concurrent="1" nextAc="seek">
              <p:cTn id="{sequence_id}" restart="whenNotActive" fill="hold" evtFilter="cancelBubble" nodeType="interactiveSeq">
                <p:stCondLst><p:cond evt="onClick" delay="0"><p:tgtEl><p:spTgt spid="{trigger_shape_id}"/></p:tgtEl></p:cond></p:stCondLst>
                <p:endSync evt="end" delay="0"><p:rtn val="all"/></p:endSync>
                <p:childTnLst>
                  <p:par>
                    <p:cTn id="{wrapper_id}" fill="hold">
                      <p:stCondLst><p:cond delay="0"/></p:stCondLst>
                      <p:childTnLst>
                        <p:par>
                          <p:cTn id="{inner_id}" fill="hold">
                            <p:stCondLst><p:cond delay="0"/></p:stCondLst>
                            <p:childTnLst><p:par>{row_xml}</p:par></p:childTnLst>
                          </p:cTn>
                        </p:par>
                      </p:childTnLst>
                    </p:cTn>
                  </p:par>
                </p:childTnLst>
              </p:cTn>
              <p:nextCondLst><p:cond evt="onClick" delay="0"><p:tgtEl><p:spTgt spid="{trigger_shape_id}"/></p:tgtEl></p:cond></p:nextCondLst>
            </p:seq>''')
    sequence_xml = '\n            '.join(
        [
            value
            for value in (
                main_sequence_xml,
                *interactive_sequences,
            )
            if value
        ]
    )

    timing_xml = f'''  <p:timing>
    <p:tnLst>
      <p:par>
        <p:cTn id="1" dur="indefinite" restart="never" nodeType="tmRoot">
          <p:childTnLst>
            {sequence_xml}
          </p:childTnLst>
        </p:cTn>
      </p:par>
    </p:tnLst>
  </p:timing>'''
    if not any(target.bounce_end for target in normalized_targets):
        return timing_xml

    fallback_xml = re.sub(
        r'\s+p14:(?:presetBounceEnd|bounceEnd)="\d+"',
        '',
        timing_xml,
    )
    fallback_xml = fallback_xml.replace(
        f' xmlns:p14="{_P14_NS}"',
        '',
    )
    return f'''  <mc:AlternateContent xmlns:mc="{_MC_NS}">
    <mc:Choice xmlns:p14="{_P14_NS}" Requires="p14">
      {timing_xml}
    </mc:Choice>
    <mc:Fallback>
      {fallback_xml}
    </mc:Fallback>
  </mc:AlternateContent>'''


def pick_animation_effect(
    mode: str,
    idx: int,
    offset: int = 0,
    group_id: str | None = None,
    *,
    rng: random.Random | None = None,
) -> str:
    """Resolve a per-element effect name from a mode string.

    - A specific animation name returns itself (no variation).
    - 'auto': map ``group_id`` to an effect. Image-like ids
      (hero / figure- / image / img- / kpi) cycle through ``_IMAGE_POOL``
      (``entrance_zoom`` / ``entrance_dissolve`` / ``entrance_circle`` /
      ``entrance_box`` / ``entrance_diamond`` / ``entrance_wheel``) by
      ``idx + offset``
      so multiple images vary across the deck. Other semantic matches in
      ``_SEMANTIC_PATTERNS`` return a single stable effect
      (chart→``entrance_wipe``, card-/step-/pillar-→``entrance_fly``,
      title/takeaway→``entrance_fade``). When the id matches no pattern, cycle
      through ``_AUTO_POOL``.
    - 'mixed' (compatible mode name): first element fixed to
      ``entrance_fade``; the rest cycle through ``_MIXED_POOL`` plus
      ``offset`` so titles stay calm while content varies across slides.
    - 'random': uniform seeded choice from the same canonical preset pool.
    Unknown modes fail explicitly; no effect is silently substituted.
    """
    from pptx_animations import (
        NATIVE_ANIMATIONS,
        _AUTO_POOL,
        _MIXED_POOL,
        _semantic_effect,
        normalize_animation_effect,
        random,
    )
    mode = normalize_animation_effect(
        mode,
        allow_none=False,
        allow_modes=True,
    )
    if isinstance(idx, bool) or not isinstance(idx, int) or idx < 0:
        raise ValueError(f'animation index must be a non-negative integer: {idx!r}')
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError(
            f'animation offset must be a non-negative integer: {offset!r}'
        )
    if mode in NATIVE_ANIMATIONS:
        return mode
    if mode == 'auto':
        semantic = _semantic_effect(group_id, idx, offset)
        if semantic is not None:
            return semantic
        return _AUTO_POOL[(idx + offset) % len(_AUTO_POOL)]
    if mode == 'mixed':
        if idx == 0:
            return 'entrance_fade'
        return _MIXED_POOL[(idx - 1 + offset) % len(_MIXED_POOL)]
    if mode == 'random':
        chooser = rng if rng is not None else random
        return chooser.choice(_MIXED_POOL)
    raise AssertionError(f'unhandled animation mode: {mode}')


