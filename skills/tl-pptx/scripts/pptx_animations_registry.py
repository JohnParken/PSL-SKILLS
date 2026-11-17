#!/usr/bin/env python3
"""
PPT Master - PPTX Animation Module

Provides one strict object-animation registry plus OOXML read/write helpers.

Supported transition effects:
    - Complete current PowerPoint gallery: Subtle, Exciting, Dynamic Content
    - Compatibility aliases: strips, circle, diamond, newsflash, plus, pull,
      wedge, wheel
    - none: no visual transition (handled by the shared transition core)

PowerPoint-native object animations:
    - 53 entrance effects (``entrance_*``)
    - 33 emphasis effects (``emphasis_*``)
    - 64 motion paths (``path_*``)
    - 53 exit effects (``exit_*``)

Legacy compatibility inputs (accepted but never selected for new output):
    appear, fade, fly, fly_left, fly_right, fly_top, cut, zoom, wipe,
    wipe_left, wipe_right, wipe_up, wipe_down, split, blinds, checkerboard,
    dissolve, random_bars, peek, wheel, box, circle, diamond, plus, strips,
    wedge, stretch, expand, swivel

The four media commands in MsoAnimEffect are intentionally excluded because
they require audio/video shapes or bookmarks rather than generated SVG groups.

Animation modes used by the builder:
    - single effect name (one of the above) — apply to every element
    - 'auto'   — pick effect from the group's SVG id. Image-like ids
                 (hero / figure- / image / img- / kpi) cycle through a
                 visual pool (``entrance_zoom`` / ``entrance_dissolve`` /
                 ``entrance_circle`` / ``entrance_box`` /
                 ``entrance_diamond`` / ``entrance_wheel``) so multiple
                 images vary across the deck. Other
                 semantic matches map to a single stable effect
                 (chart→``entrance_wipe``,
                 card-/step-/pillar-→``entrance_fly``,
                 title/takeaway→``entrance_fade``).
                 Unmatched ids cycle through a small modern pool
                 (``entrance_fade`` / ``entrance_wipe`` /
                 ``entrance_fly`` / ``entrance_zoom``).
    - 'mixed'  — compatible mode name: first element fades, the rest cycle
                 through a larger canonical PowerPoint entrance pool.
    - 'random' — pick a seeded canonical PowerPoint entrance per element

Generated animation rows are validated against their requested effect, target,
duration, order, and Start mode before a PPTX is published.  Package validation
also checks timing-tree placement, time-node identifiers, and shape references.

See references/animations.md for the public workflow contract.

Dependencies: None (standard-library XML generation and validation)

Usage:
    python3 scripts/pptx_animations.py --demo
    python3 scripts/pptx_animations.py --list
    python3 scripts/pptx_animations.py --describe entrance_fly
"""

import argparse
import copy
import hashlib
import json
import math
import posixpath
import random
import re
import zipfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping, Sequence
from xml.etree import ElementTree as ET


from pptx_transitions import (
    LEGACY_TRANSITION_KEYS,
    MAX_OOXML_MILLISECONDS,
    MAX_OOXML_UNSIGNED_INT,
    NATIVE_TRANSITION_KEYS,
    NATIVE_TRANSITIONS,
    PML_NS,
    TRANSITION_ALIAS_OPTIONS,
    TRANSITION_ALIASES,
    TRANSITION_CATEGORIES,
    create_transition_xml,
    describe_transition_effect,
    validate_seconds,
)




# ============================================================================
# Object animation definitions
# ============================================================================

# Compatibility names normalize to canonical PowerPoint-authored presets.
# ``cut`` has no current object-animation preset, so the compatibility name
# resolves to the standard instantaneous entrance, ``entrance_appear``.
ANIMATION_ALIASES: dict[str, str] = {
    'appear': 'entrance_appear',
    'fade': 'entrance_fade',
    'fly': 'entrance_fly',
    'fly_left': 'entrance_fly',
    'fly_right': 'entrance_fly',
    'fly_top': 'entrance_fly',
    'cut': 'entrance_appear',
    'zoom': 'entrance_zoom',
    'wipe': 'entrance_wipe',
    'wipe_left': 'entrance_wipe',
    'wipe_right': 'entrance_wipe',
    'wipe_up': 'entrance_wipe',
    'wipe_down': 'entrance_wipe',
    'split': 'entrance_split',
    'blinds': 'entrance_blinds',
    'checkerboard': 'entrance_checkerboard',
    'dissolve': 'entrance_dissolve',
    'random_bars': 'entrance_random_bars',
    'peek': 'entrance_peek',
    'wheel': 'entrance_wheel',
    'box': 'entrance_box',
    'circle': 'entrance_circle',
    'diamond': 'entrance_diamond',
    'plus': 'entrance_plus',
    'strips': 'entrance_strips',
    'wedge': 'entrance_wedge',
    'stretch': 'entrance_stretch',
    'expand': 'entrance_expand',
    'swivel': 'entrance_swivel',
}

LEGACY_ANIMATION_KEYS = tuple(ANIMATION_ALIASES)
ANIMATION_CATEGORIES = ('entrance', 'emphasis', 'path', 'exit')
_PRESET_CLASS_BY_CATEGORY = {
    'entrance': 'entr',
    'emphasis': 'emph',
    'path': 'path',
    'exit': 'exit',
}
_DML_NS = 'http://schemas.openxmlformats.org/drawingml/2006/main'
_REL_NS = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
_PACKAGE_REL_NS = 'http://schemas.openxmlformats.org/package/2006/relationships'
_AUDIO_REL_TYPE = (
    'http://schemas.openxmlformats.org/officeDocument/2006/relationships/audio'
)
_P14_NS = 'http://schemas.microsoft.com/office/powerpoint/2010/main'
_MC_NS = 'http://schemas.openxmlformats.org/markup-compatibility/2006'
ET.register_namespace('p', PML_NS)
ET.register_namespace('a', _DML_NS)
ET.register_namespace('r', _REL_NS)
ET.register_namespace('p14', _P14_NS)

ANIMATION_EFFECT_OPTION_FIELDS = (
    'direction',
    'amount',
    'color',
    'font_name',
    'relative',
    'size',
)
ANIMATION_TIMING_OPTION_FIELDS = (
    'repeat_count',
    'repeat_duration',
    'auto_reverse',
    'rewind',
    'accelerate',
    'decelerate',
    'bounce_end',
    'restart',
)
ANIMATION_RESTARTS = ('always', 'when-not-active', 'never')
ANIMATION_AFTER_EFFECTS = ('none', 'dim', 'hide', 'hide-on-next-click')
_INTERPOLATED_BEHAVIOR_TAGS = frozenset({
    'anim',
    'animClr',
    'animEffect',
    'animMotion',
    'animRot',
    'animScale',
})
_NON_CONCRETE_FONT_NAMES = frozenset({
    '-apple-system',
    'blinkmacsystemfont',
    'cursive',
    'emoji',
    'fantasy',
    'inherit',
    'initial',
    'math',
    'monospace',
    'revert',
    'revert-layer',
    'sans-serif',
    'serif',
    'system-ui',
    'ui-monospace',
    'ui-rounded',
    'ui-sans-serif',
    'ui-serif',
    'unset',
})

# Legacy directional names retain their historical semantics by desugaring
# into one canonical effect plus the matching PowerPoint EffectParameters
# value. New plans never select these aliases.
ANIMATION_ALIAS_OPTIONS: dict[str, dict[str, object]] = {
    'fly_left': {'direction': 'left'},
    'fly_right': {'direction': 'right'},
    'fly_top': {'direction': 'up'},
    'wipe_left': {'direction': 'left'},
    'wipe_right': {'direction': 'right'},
    'wipe_up': {'direction': 'up'},
    'wipe_down': {'direction': 'down'},
    'wheel': {'amount': 4},
}


def _load_native_animations() -> dict[str, dict[str, Any]]:
    """Load the PowerPoint-authored preset rows shipped with this module."""
    manifest_path = Path(__file__).with_name('pptx_animation_presets.json')
    try:
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            f'unable to load native animation presets from {manifest_path}: {exc}'
        ) from exc
    if manifest.get('version') != 2:
        raise RuntimeError(
            f'unsupported native animation preset version: {manifest.get("version")!r}'
        )
    raw_effects = manifest.get('effects')
    if not isinstance(raw_effects, list):
        raise RuntimeError('native animation preset manifest field "effects" must be a list')

    native: dict[str, dict[str, Any]] = {}
    category_counts = {category: 0 for category in ANIMATION_CATEGORIES}
    for raw in raw_effects:
        if not isinstance(raw, dict):
            raise RuntimeError('native animation preset entries must be objects')
        key = raw.get('key')
        category = raw.get('category')
        if not isinstance(key, str) or not key:
            raise RuntimeError(f'native animation preset has invalid key: {key!r}')
        if category not in ANIMATION_CATEGORIES:
            raise RuntimeError(
                f'native animation preset {key!r} has invalid category: {category!r}'
            )
        if key in native or key in ANIMATION_ALIASES:
            raise RuntimeError(f'duplicate animation preset key: {key}')
        row_xml = raw.get('row_xml')
        if not isinstance(row_xml, str):
            raise RuntimeError(f'native animation preset {key!r} is missing row_xml')
        try:
            row = ET.fromstring(row_xml)
        except ET.ParseError as exc:
            raise RuntimeError(
                f'native animation preset {key!r} contains invalid row_xml: {exc}'
            ) from exc
        if row.tag != f'{{{PML_NS}}}cTn':
            raise RuntimeError(f'native animation preset {key!r} is not a p:cTn row')

        spec = {
            'name': str(raw.get('name') or key),
            'filter': raw.get('filter'),
            'presetID': int(raw.get('preset_id')),
            'presetSubtype': int(raw.get('preset_subtype')),
            'presetClass': _PRESET_CLASS_BY_CATEGORY[category],
            'category': category,
            'msoEffectId': int(raw.get('mso_effect_id')),
            'defaultDurationMs': raw.get('default_duration_ms'),
            'durationScalable': bool(raw.get('duration_scalable')),
            'rowXml': row_xml,
            'effectOptions': raw.get('effect_options', {}),
        }
        if row.get('presetClass') != spec['presetClass']:
            raise RuntimeError(f'native animation preset {key!r} changed presetClass')
        if int(row.get('presetID', '-1')) != spec['presetID']:
            raise RuntimeError(f'native animation preset {key!r} changed presetID')
        if int(row.get('presetSubtype', '-1')) != spec['presetSubtype']:
            raise RuntimeError(f'native animation preset {key!r} changed presetSubtype')
        effect_options = spec['effectOptions']
        if not isinstance(effect_options, dict):
            raise RuntimeError(
                f'native animation preset {key!r} effect_options must be an object'
            )
        unknown_options = set(effect_options) - set(ANIMATION_EFFECT_OPTION_FIELDS)
        if unknown_options:
            raise RuntimeError(
                f'native animation preset {key!r} has unknown effect option(s): '
                + ', '.join(sorted(unknown_options))
            )
        for option_name, option_spec in effect_options.items():
            if not isinstance(option_spec, dict):
                raise RuntimeError(
                    f'native animation preset {key!r} option {option_name!r} '
                    'must be an object'
                )
            required = option_spec.get('required', False)
            if not isinstance(required, bool):
                raise RuntimeError(
                    f'native animation preset {key!r} option {option_name!r} '
                    'required must be a boolean'
                )
            if required and 'default' in option_spec:
                raise RuntimeError(
                    f'native animation preset {key!r} option {option_name!r} '
                    'cannot define both required and default'
                )
            option_type = option_spec.get('type')
            if option_type == 'enum':
                values = option_spec.get('values')
                if not isinstance(values, dict) or not values:
                    raise RuntimeError(
                        f'native animation preset {key!r} enum option '
                        f'{option_name!r} must define values'
                    )
                default = str(option_spec.get('default'))
                if default not in values:
                    raise RuntimeError(
                        f'native animation preset {key!r} enum option '
                        f'{option_name!r} has an unknown default'
                    )
                for option_value, variant_xml in values.items():
                    if not isinstance(option_value, str) or not isinstance(
                        variant_xml,
                        str,
                    ):
                        raise RuntimeError(
                            f'native animation preset {key!r} enum option '
                            f'{option_name!r} contains an invalid variant'
                        )
                    try:
                        variant = ET.fromstring(variant_xml)
                    except ET.ParseError as exc:
                        raise RuntimeError(
                            f'native animation preset {key!r} enum option '
                            f'{option_name!r}/{option_value!r} contains invalid XML: '
                            f'{exc}'
                        ) from exc
                    if variant.tag != f'{{{PML_NS}}}cTn':
                        raise RuntimeError(
                            f'native animation preset {key!r} enum option '
                            f'{option_name!r}/{option_value!r} is not a p:cTn row'
                        )
                    if variant.get('presetClass') != spec['presetClass']:
                        raise RuntimeError(
                            f'native animation preset {key!r} enum option '
                            f'{option_name!r}/{option_value!r} changed presetClass'
                        )
                    if int(variant.get('presetID', '-1')) != spec['presetID']:
                        raise RuntimeError(
                            f'native animation preset {key!r} enum option '
                            f'{option_name!r}/{option_value!r} changed presetID'
                        )
            elif option_type not in {'number', 'string', 'boolean', 'color'}:
                raise RuntimeError(
                    f'native animation preset {key!r} option {option_name!r} '
                    f'has unknown type: {option_type!r}'
                )
        native[key] = spec
        category_counts[category] += 1

    expected_counts = {'entrance': 53, 'emphasis': 33, 'path': 64, 'exit': 53}
    if category_counts != expected_counts:
        raise RuntimeError(
            'native animation preset category counts changed: '
            f'{category_counts!r}; expected {expected_counts!r}'
        )
    return native


NATIVE_ANIMATIONS = _load_native_animations()
NATIVE_ANIMATION_KEYS = tuple(NATIVE_ANIMATIONS)
ANIMATIONS = {
    **NATIVE_ANIMATIONS,
    **{
        alias: NATIVE_ANIMATIONS[canonical]
        for alias, canonical in ANIMATION_ALIASES.items()
    },
}

ANIMATION_MODES = ('auto', 'mixed', 'random')
ANIMATION_TRIGGERS = ('on-click', 'with-previous', 'after-previous')

_TRIGGER_NODE_TYPES = {
    'on-click': 'clickEffect',
    'with-previous': 'withEffect',
    'after-previous': 'afterEffect',
}
_NODE_TYPE_TRIGGERS = {
    value: key for key, value in _TRIGGER_NODE_TYPES.items()
}
