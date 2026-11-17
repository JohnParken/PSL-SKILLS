"""PPT Master motion core; responsibility-split, with stable original API.

MIT Copyright (c) 2025-2026 Hugo He.
Upstream commit: 7e0df6705de0cd24345210d31f07fcb5a32b6900.
"""
from __future__ import annotations
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

from pptx_animations_registry import (
    ANIMATIONS,
    ANIMATION_AFTER_EFFECTS,
    ANIMATION_ALIASES,
    ANIMATION_ALIAS_OPTIONS,
    ANIMATION_CATEGORIES,
    ANIMATION_EFFECT_OPTION_FIELDS,
    ANIMATION_MODES,
    ANIMATION_RESTARTS,
    ANIMATION_TIMING_OPTION_FIELDS,
    ANIMATION_TRIGGERS,
    Any,
    ET,
    LEGACY_ANIMATION_KEYS,
    LEGACY_TRANSITION_KEYS,
    MAX_OOXML_MILLISECONDS,
    MAX_OOXML_UNSIGNED_INT,
    Mapping,
    NATIVE_ANIMATIONS,
    NATIVE_ANIMATION_KEYS,
    NATIVE_TRANSITIONS,
    NATIVE_TRANSITION_KEYS,
    PML_NS,
    Path,
    Sequence,
    TRANSITION_ALIASES,
    TRANSITION_ALIAS_OPTIONS,
    TRANSITION_CATEGORIES,
    _AUDIO_REL_TYPE,
    _DML_NS,
    _INTERPOLATED_BEHAVIOR_TAGS,
    _MC_NS,
    _NODE_TYPE_TRIGGERS,
    _NON_CONCRETE_FONT_NAMES,
    _P14_NS,
    _PACKAGE_REL_NS,
    _PRESET_CLASS_BY_CATEGORY,
    _REL_NS,
    _TRIGGER_NODE_TYPES,
    _load_native_animations,
    argparse,
    copy,
    create_transition_xml,
    dataclass,
    describe_transition_effect,
    hashlib,
    json,
    math,
    posixpath,
    random,
    re,
    replace,
    validate_seconds,
    zipfile,
)

@dataclass(frozen=True)
class AnimationTarget:
    """Resolved object-animation request for one PowerPoint shape."""

    shape_id: int
    delay_ms: int
    effect: str
    duration_ms: int
    effect_options: Mapping[str, object]
    trigger: str = 'after-previous'
    trigger_shape_id: int | None = None
    repeat_count: float | None = None
    repeat_duration_ms: int | None = None
    auto_reverse: bool | None = None
    rewind: bool | None = None
    accelerate: float | None = None
    decelerate: float | None = None
    bounce_end: float | None = None
    restart: str | None = None
    after_effect: str = 'none'
    after_effect_color: str | None = None
    sound_relationship_id: str | None = None
    sound_name: str | None = None

    @property
    def playback_duration_ms(self) -> int:
        """Return the wall-clock duration used by after-previous scheduling."""
        one_play = self.duration_ms * (2 if self.auto_reverse else 1)
        if self.repeat_duration_ms is not None:
            return self.repeat_duration_ms
        if self.repeat_count is not None:
            return max(1, round(one_play * self.repeat_count))
        return one_play


@dataclass(frozen=True)
class AnimationRowSummary:
    """Read-back summary for one object-animation row in the animation pane."""

    shape_id: int
    effect: str | None
    supported_effects: tuple[str, ...]
    preset_class: str
    trigger: str
    duration_ms: int | None
    offset_ms: int
    preset_id: int
    preset_subtype: int
    filter_name: str | None
    effect_options: Mapping[str, object]
    trigger_shape_id: int | None
    repeat_count: float | None
    repeat_duration_ms: int | None
    auto_reverse: bool
    rewind: bool
    accelerate: float
    decelerate: float
    bounce_end: float
    restart: str
    after_effect: str
    after_effect_color: str | None
    sound_relationship_id: str | None
    sound_name: str | None
    playback_duration_ms: int | None


@dataclass(frozen=True)
class AnimationSequenceSummary:
    """Read-back summary for the logical object sequence on one slide."""

    timing_count: int
    trigger: str | None
    rows: tuple[AnimationRowSummary, ...]
    audio_target_ids: tuple[int, ...]


_MIXED_POOL = [
    'entrance_blinds', 'entrance_checkerboard', 'entrance_dissolve',
    'entrance_fly', 'entrance_ascend', 'entrance_random_bars',
    'entrance_box', 'entrance_split', 'entrance_strips', 'entrance_wedge',
    'entrance_wheel', 'entrance_wipe', 'entrance_expand', 'entrance_fade',
    'entrance_swivel', 'entrance_zoom',
]


_AUTO_POOL = [
    'entrance_fade',
    'entrance_wipe',
    'entrance_fly',
    'entrance_zoom',
]


_IMAGE_POOL = [
    'entrance_zoom',
    'entrance_dissolve',
    'entrance_circle',
    'entrance_box',
    'entrance_diamond',
    'entrance_wheel',
]


_IMAGE_KEYWORDS: tuple[str, ...] = ('hero', 'figure-', 'image', 'img-', 'kpi')


_SEMANTIC_PATTERNS: list[tuple[tuple[str, ...], str]] = [
    (
        ('title', 'chapter-', 'section-', 'cover-', 'tagline', 'subtitle'),
        'entrance_fade',
    ),
    (
        ('chart', 'table', 'legend', 'timeline', 'track'),
        'entrance_wipe',
    ),
    (('card-', 'pillar-', 'item-', 'step-', 'stage-', 'tier-',
      'principle-', 'q-', 'schema-'),                           'entrance_fly'),
    (('takeaway', 'callout', 'quote', 'source', 'conclusion', 'note',
      'try-at-home'),                                           'entrance_fade'),
]


from pptx_animations_validation import (
    _qn,
    _local_name,
    normalize_animation_effect,
    _finite_number,
    _normalize_animation_color,
    _normalize_powerpoint_font_name,
    normalize_animation_effect_options,
    normalize_animation_effect_request,
    normalize_animation_trigger,
    _seconds_to_ms,
    animation_seconds_to_milliseconds,
    _positive_shape_id,
    _non_negative_milliseconds,
    _optional_bool,
    _optional_ratio,
    _normalize_repeat_count,
    _normalize_after_effect,
    _normalize_sound,
)
from pptx_animations_targets import (
    _normalize_target_mapping,
    _normalize_target,
)
from pptx_animations_timing import (
    _semantic_effect,
    create_timing_xml,
    _ctn_numeric_delay,
    _row_effective_duration_ms,
    _scale_animation_row_duration,
    _row_semantic_signature,
    _row_timing_profile,
    _animation_spec_matches_row,
)
from pptx_animations_effects import (
    _format_decimal,
    _color_element,
    _replace_animation_colors,
    _set_effect_option_value,
    _animation_row_for_options,
    _interpolated_behavior_nodes,
    animation_effect_supports_bounce_end,
    _apply_timing_options,
    _append_after_effect,
    _append_animation_sound,
)
from pptx_animations_sequence import (
    _instantiate_animation_row,
    _build_animation_row_xml,
    _main_target_offsets,
    _build_mixed_main_steps,
    create_sequence_timing_xml,
    pick_animation_effect,
)
from pptx_animations_reading import (
    _int_attribute,
    _direct_conditions,
    _shape_index,
    _row_shape_id,
    _row_filter,
    _resolve_row_effect,
    _behavior_duration_ms,
    _read_animation_color,
    _read_effect_options,
)
from pptx_animations_summary import (
    _timing_summary,
    _after_effect_summary,
    _sound_summary,
    _row_trigger_shape_id,
    _row_offset_ms,
)
from pptx_animations_rows import (
    _row_matches_powerpoint_behavior,
    _animation_rows,
    _select_supported_timing_branch,
)
from pptx_animations_structure import (
    validate_slide_animation_structure,
    read_slide_click_dependencies,
    read_slide_animation_sequence,
)
from pptx_animations_package import (
    validate_generated_animation_xml,
    validate_pptx_animation_package,
)
from pptx_animations_inspection import (
    object_animation_fingerprint,
    entrance_animation_fingerprint,
    get_available_transitions,
    get_available_animations,
    get_transition_help,
    get_animation_help,
    describe_animation_effect,
    effective_animation_effect_options,
    main,
)

if __name__ == '__main__':
    main()


