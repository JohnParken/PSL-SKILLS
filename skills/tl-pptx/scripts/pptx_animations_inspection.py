"""PPT Master motion core; responsibility-split, with stable original API.

MIT Copyright (c) 2025-2026 Hugo He.
Upstream commit: 7e0df6705de0cd24345210d31f07fcb5a32b6900.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from typing import Any, Mapping
from xml.etree import ElementTree as ET
from pptx_transitions import (
    LEGACY_TRANSITION_KEYS,
    NATIVE_TRANSITION_KEYS,
    NATIVE_TRANSITIONS,
    PML_NS,
    TRANSITION_ALIAS_OPTIONS,
    TRANSITION_ALIASES,
    TRANSITION_CATEGORIES,
    create_transition_xml,
    describe_transition_effect,
)

from pptx_animations_registry import (
    ANIMATIONS,
    ANIMATION_AFTER_EFFECTS,
    ANIMATION_ALIASES,
    ANIMATION_ALIAS_OPTIONS,
    ANIMATION_CATEGORIES,
    ANIMATION_RESTARTS,
    ANIMATION_TRIGGERS,
    Any,
    ET,
    LEGACY_ANIMATION_KEYS,
    LEGACY_TRANSITION_KEYS,
    Mapping,
    NATIVE_ANIMATIONS,
    NATIVE_ANIMATION_KEYS,
    NATIVE_TRANSITIONS,
    NATIVE_TRANSITION_KEYS,
    PML_NS,
    TRANSITION_ALIASES,
    TRANSITION_ALIAS_OPTIONS,
    TRANSITION_CATEGORIES,
    argparse,
    create_transition_xml,
    describe_transition_effect,
    hashlib,
    json,
)

def object_animation_fingerprint(slide_xml: str | bytes) -> str | None:
    """Return a prefix/whitespace-independent fingerprint of object animation.

    Narration audio is intentionally excluded.  Direct-PPTX routes use this
    fingerprint before and after their allowed edits to prove that they did not
    take ownership of or rewrite existing object animations.
    """
    from pptx_animations import (
        ET,
        PML_NS,
        _qn,
        _select_supported_timing_branch,
        hashlib,
    )
    data = slide_xml.encode('utf-8') if isinstance(slide_xml, str) else slide_xml
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise ValueError(f'invalid slide XML: {exc}') from exc
    root = _select_supported_timing_branch(root)
    timings = [
        child for child in list(root)
        if child.tag == _qn(PML_NS, 'timing')
    ]
    if len(timings) > 1:
        raise ValueError(
            f'slide has {len(timings)} root p:timing elements; expected at most 1'
        )
    if not timings:
        return None
    timing = timings[0]
    behavior_tags = {
        _qn(PML_NS, name)
        for name in (
            'anim',
            'animClr',
            'animEffect',
            'animMotion',
            'animRot',
            'animScale',
            'cmd',
            'set',
        )
    }
    has_object_animation = any(
        element.tag in behavior_tags
        or (
            element.tag == _qn(PML_NS, 'cTn')
            and element.get('presetClass') is not None
        )
        for element in timing.iter()
    )
    if not has_object_animation:
        return None

    def without_audio(element: ET.Element) -> tuple[object, ...] | None:
        if element.tag == _qn(PML_NS, 'audio'):
            return None
        children = tuple(
            value
            for child in list(element)
            if (value := without_audio(child)) is not None
        )
        return (
            element.tag,
            tuple(sorted(element.attrib.items())),
            (element.text or '').strip(),
            children,
        )

    canonical = without_audio(timing)
    return hashlib.sha256(repr(canonical).encode('utf-8')).hexdigest()


def entrance_animation_fingerprint(slide_xml: str | bytes) -> str | None:
    """Compatibility alias for :func:`object_animation_fingerprint`."""
    from pptx_animations import (
        object_animation_fingerprint,
    )
    return object_animation_fingerprint(slide_xml)


def get_available_transitions() -> list:
    """Get native transition keys followed by compatibility inputs."""
    from pptx_animations import (
        LEGACY_TRANSITION_KEYS,
        NATIVE_TRANSITION_KEYS,
    )
    return [*NATIVE_TRANSITION_KEYS, *LEGACY_TRANSITION_KEYS]


def get_available_animations() -> list:
    """Get canonical object-animation keys followed by compatibility inputs."""
    from pptx_animations import (
        ANIMATIONS,
    )
    return list(ANIMATIONS.keys())


def get_transition_help() -> str:
    """Get categorized native transitions plus legacy compatibility inputs."""
    from pptx_animations import (
        LEGACY_TRANSITION_KEYS,
        NATIVE_TRANSITIONS,
        NATIVE_TRANSITION_KEYS,
        TRANSITION_ALIASES,
        TRANSITION_ALIAS_OPTIONS,
        TRANSITION_CATEGORIES,
    )
    lines = ["Available transition effects:"]
    for category in TRANSITION_CATEGORIES:
        lines.append(f"  PowerPoint-native {category} effects:")
        for key in NATIVE_TRANSITION_KEYS:
            info = NATIVE_TRANSITIONS[key]
            if info["category"] == category:
                lines.append(f"    {key}: {info['name']}")
    lines.append("  Legacy compatibility inputs (never selected for new output):")
    for key in LEGACY_TRANSITION_KEYS:
        canonical = TRANSITION_ALIASES[key]
        implied = TRANSITION_ALIAS_OPTIONS.get(key)
        option_suffix = f", implies {implied}" if implied else ""
        lines.append(
            f"    {key}: compatibility alias for {canonical} "
            f"({NATIVE_TRANSITIONS[canonical]['name']}{option_suffix})"
        )
    return '\n'.join(lines)


def get_animation_help() -> str:
    """Get categorized help text for every object-animation effect."""
    from pptx_animations import (
        ANIMATION_ALIASES,
        ANIMATION_ALIAS_OPTIONS,
        ANIMATION_CATEGORIES,
        LEGACY_ANIMATION_KEYS,
        NATIVE_ANIMATIONS,
        NATIVE_ANIMATION_KEYS,
    )
    lines = ['Available object animations:']
    for category in ANIMATION_CATEGORIES:
        lines.append(f'  PowerPoint-native {category} effects:')
        for key in NATIVE_ANIMATION_KEYS:
            info = NATIVE_ANIMATIONS[key]
            if info['category'] == category:
                lines.append(f"    {key}: {info['name']}")
    lines.append('  Legacy compatibility inputs (never selected for new output):')
    for key in LEGACY_ANIMATION_KEYS:
        canonical = ANIMATION_ALIASES[key]
        implied = ANIMATION_ALIAS_OPTIONS.get(key)
        option_suffix = (
            f', implies {implied}'
            if implied
            else ''
        )
        lines.append(
            f"    {key}: compatibility alias for {canonical} "
            f"({NATIVE_ANIMATIONS[canonical]['name']}{option_suffix})"
        )
    return '\n'.join(lines)


def describe_animation_effect(effect: object) -> dict[str, Any]:
    """Return the author-facing option contract for one animation effect."""
    from pptx_animations import (
        ANIMATION_AFTER_EFFECTS,
        ANIMATION_ALIASES,
        ANIMATION_ALIAS_OPTIONS,
        ANIMATION_RESTARTS,
        ANIMATION_TRIGGERS,
        NATIVE_ANIMATIONS,
        normalize_animation_effect,
    )
    canonical = normalize_animation_effect(
        effect,
        allow_none=False,
        allow_modes=False,
    )
    assert canonical is not None
    implied_options = (
        dict(ANIMATION_ALIAS_OPTIONS.get(effect, {}))
        if isinstance(effect, str)
        else {}
    )
    option_contract: dict[str, Any] = {}
    for name, raw_spec in NATIVE_ANIMATIONS[canonical]['effectOptions'].items():
        spec = {
            key: value
            for key, value in raw_spec.items()
            if key != 'values'
        }
        if raw_spec.get('type') == 'enum':
            spec['values'] = list(raw_spec['values'])
        if name == 'direction':
            spec['note'] = (
                'the direction the motion travels; PowerPoint names the origin '
                'edge, so right = "From Left", left = "From Right", down = '
                '"From Top", up = "From Bottom"'
            )
        option_contract[name] = spec
    return {
        'input': effect,
        'effect': canonical,
        'compatibility_alias': (
            effect if isinstance(effect, str) and effect in ANIMATION_ALIASES else None
        ),
        'implied_effect_options': implied_options,
        'effect_options': option_contract,
        'timing': {
            'duration': (
                'positive seconds; legacy group effect or effects[] row'
            ),
            'delay': (
                'non-negative seconds; legacy group effect or effects[] row'
            ),
            'stagger': 'non-negative seconds; animation scope only',
            'trigger': list(ANIMATION_TRIGGERS),
            'trigger_scope': (
                'animation default for a legacy group effect; each effects[] '
                'row may override it'
            ),
            'trigger_shape': (
                'other top-level SVG group id; legacy group effect or '
                'effects[] row; maps to PowerPoint "On Click of" and requires '
                'trigger on-click'
            ),
            'repeat_count': 'positive number; mutually exclusive with repeat_duration',
            'repeat_duration': 'positive seconds; mutually exclusive with repeat_count',
            'auto_reverse': 'boolean',
            'rewind': 'boolean',
            'accelerate': 'number from 0 to 1',
            'decelerate': 'number from 0 to 1',
            'bounce_end': (
                'number from 0 to 1; requires an interpolated behavior and '
                'is mutually exclusive with decelerate'
            ),
            'restart': list(ANIMATION_RESTARTS),
        },
        'after_effect': list(ANIMATION_AFTER_EFFECTS),
        'sound': 'project-relative or absolute .m4a, .mp3, or .wav path',
        'derived_not_configured': {
            'speed': 'derived from duration',
            'smooth_start': 'derived from accelerate',
            'smooth_end': 'derived from decelerate',
        },
    }


def effective_animation_effect_options(
    effect: object,
    effect_options: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Return the effective option values encoded by one registry request."""
    from pptx_animations import (
        NATIVE_ANIMATIONS,
        _animation_row_for_options,
        _read_effect_options,
        _row_filter,
        normalize_animation_effect_request,
    )
    canonical, normalized_options = normalize_animation_effect_request(
        effect,
        effect_options,
        allow_none=False,
        allow_modes=False,
    )
    assert canonical is not None
    row = _animation_row_for_options(canonical, normalized_options)
    errors: list[str] = []
    filter_name = _row_filter(
        row,
        str(NATIVE_ANIMATIONS[canonical]['presetClass']),
        errors,
    )
    effective = _read_effect_options(
        row,
        canonical,
        filter_name,
        errors,
    )
    if errors:
        raise RuntimeError(
            f'animation effect {canonical!r} option model failed: '
            + '; '.join(errors)
        )
    return effective


def main() -> None:
    """Run the CLI entry point."""
    from pptx_animations import (
        argparse,
        create_timing_xml,
        create_transition_xml,
        describe_animation_effect,
        describe_transition_effect,
        get_animation_help,
        get_transition_help,
        json,
    )
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="print sample XML for a fade transition and entrance_fade animation",
    )
    parser.add_argument(
        '--list',
        action='store_true',
        help='list available transitions and object animations',
    )
    parser.add_argument(
        '--describe',
        metavar='EFFECT',
        help='print the complete parameter contract for one object animation',
    )
    parser.add_argument(
        '--describe-transition',
        metavar='EFFECT',
        help='print the PowerPoint Effect Options for one page transition',
    )
    args = parser.parse_args()

    if args.describe:
        try:
            print(
                json.dumps(
                    describe_animation_effect(args.describe),
                    ensure_ascii=False,
                    indent=2,
                )
            )
        except ValueError as exc:
            parser.error(str(exc))
        return

    if args.describe_transition:
        try:
            print(
                json.dumps(
                    describe_transition_effect(args.describe_transition),
                    ensure_ascii=False,
                    indent=2,
                )
            )
        except ValueError as exc:
            parser.error(str(exc))
        return

    if args.list:
        print(get_transition_help())
        print()
        print(get_animation_help())
        return

    if args.demo:
        print("=== Transition Effect XML Example (fade, 500ms) ===")
        print(create_transition_xml('fade', 0.5))
        print()
        print("=== Entrance Animation XML Example (entrance_fade) ===")
        print(create_timing_xml('entrance_fade', 1.0))
        return

    parser.print_help()


