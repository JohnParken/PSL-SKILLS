"""PPT Master motion core; responsibility-split, with stable original API.

MIT Copyright (c) 2025-2026 Hugo He.
Upstream commit: 7e0df6705de0cd24345210d31f07fcb5a32b6900.
"""
from __future__ import annotations
import json
import math
import re
import statistics
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Any
from xml.etree import ElementTree as ET
from hyperlink_contract import SHAPE_HYPERLINK_ATTR
from pptx_animations import (
    ANIMATIONS,
    ANIMATION_AFTER_EFFECTS,
    ANIMATION_MODES,
    ANIMATION_RESTARTS,
    ANIMATION_TIMING_OPTION_FIELDS,
    ANIMATION_TRIGGERS,
    animation_effect_supports_bounce_end,
    animation_seconds_to_milliseconds,
    normalize_animation_effect,
    normalize_animation_effect_options,
    normalize_animation_effect_request,
    normalize_animation_trigger,
)
from pptx_transitions import (
    normalize_transition_effect,
    normalize_transition_effect_request,
    validate_seconds,
)
from slide_roster import discover_slide_svgs
from .drawingml.utils import SVG_NS, XLINK_NS
from .pptx_package.narration import AUDIO_CONTENT_TYPES
from .semantic_markers import is_static_page_frame

_NON_VISUAL_TAGS = frozenset(('defs', 'title', 'desc', 'metadata', 'style'))


_INHERITANCE_SENSITIVE_ANIMATION_FIELDS = frozenset({
    'effect',
    'effect_options',
    'repeat_count',
    'repeat_duration',
    'accelerate',
    'decelerate',
    'bounce_end',
})


_GROUP_EFFECT_FIELDS = frozenset({
    'effect',
    'effect_options',
    'duration',
    'delay',
    'order',
    'trigger',
    'trigger_shape',
    *ANIMATION_TIMING_OPTION_FIELDS,
    'after_effect',
    'sound',
})


_CHROME_ID_TOKENS = frozenset({
    'background', 'bg',
    'decoration', 'decorations', 'decor',
    'header', 'footer',
    'chrome', 'watermark',
    'pagenumber', 'pagenum', 'slidenumber', 'slidenum',
    'logo', 'nav',
})


_RULE_TOKENS = frozenset({'rule', 'rules'})


_RULE_QUALIFIERS = frozenset({
    'hairline', 'thin', 'horizontal', 'vertical', 'h', 'v',
    'top', 'bottom', 'left', 'right', 'mid', 'middle',
    'section', 'page', 'title', 'header', 'footer', 'divider',
})


@dataclass(frozen=True)
class GroupTarget:
    """Top-level SVG group available for PowerPoint animation anchoring."""

    slide: str
    group_id: str
    order: int
    chrome: bool = False
    structurally_static: bool = False
    has_hyperlink: bool = False
    hidden_reason: str | None = None
    placeholder: str | None = None
    on_structured_page: bool = False


@dataclass(frozen=True)
class MorphPair:
    """One explicit PowerPoint Morph identity across adjacent slides."""

    source_slide: str
    destination_slide: str
    key: str
    source_group_id: str
    destination_group_id: str

    @property
    def shape_name(self) -> str:
        """Return the Selection Pane name PowerPoint uses for forced matching."""
        return f'!!{self.key}'


_TITLE_BLOCK_TOKENS = frozenset({'header', 'footer'})


_FONT_SIZE_RE = re.compile(r'font-size\s*:\s*([0-9.]+)')


_ANCHOR_OWN_ATTRIBUTES = frozenset((
    'href',
    f'{{{XLINK_NS}}}href',
    'xlink:href',
))


ROUNDTRIP_AUTHORING_DIR = 'authoring-svg-flat'


ROUNDTRIP_PAGE_PLAN = 'page_plan.json'


from .animation_config_scanning import (
    _tag_name,
    is_chrome_id,
    _font_size_px,
    _text_sizes,
    _max_text_size,
    _page_reference_size,
    _holds_page_title,
    _names_title_block,
    scan_root_primitives,
    anchor_wrapped_group,
    effective_top_level,
    usable_animation_group_id,
    scan_svg_targets,
    _duplicate_target_ids,
    _duplicate_target_error,
    _require_unique_target_ids,
)
from .animation_config_loading import (
    resolve_slide_svg_files,
    scan_project_targets,
    default_config_path,
    load_animation_config,
    _valid_transition_effect,
    _animation_effect_error,
    resolve_slide_animation_config,
    animation_group_effect_entries,
)
from .animation_config_parameters import (
    _animation_parameter_errors,
    _animation_trigger_error,
    _unknown_field_errors,
    validate_transition_config,
    _transition_scope_errors,
)
from .animation_config_morph import (
    _morph_scope_errors,
    _resolve_morph_pairs,
    resolve_morph_pairs,
)
from .animation_config_validation import (
    validate_animation_config_errors,
    _animation_scope_errors,
    _animation_group_errors,
    _animation_effect_entry_errors,
    _bounce_support_error,
    _resolved_animation_parameter_errors,
)
from .animation_config_sounds import (
    _declared_animation_sounds,
    _animation_sound_path_errors,
    _declared_transition_sounds,
    _transition_sound_path_errors,
)
from .animation_config_project import (
    validate_animation_config,
    _lock_structure_mode,
    build_scaffold,
    build_group_listing,
    write_scaffold,
)

