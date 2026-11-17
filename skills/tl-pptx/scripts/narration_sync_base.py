#!/usr/bin/env python3
"""
PPT Master - Narration Sync Tool

Derive click-free narration timing from the canonical animation config and
page-local SRT cues, then merge those subtitles against timing values read
from the final narrated PPTX.
See workflows/stages/generate-audio.md for the owning stage.

Usage:
    python3 scripts/narration_sync.py fingerprint <project_path>
    python3 scripts/narration_sync.py animations <project_path>
    python3 scripts/narration_sync.py subtitles <project_path> --pptx <pptx> --force
    python3 scripts/narration_sync.py subtitles <project_path> --pptx <pptx> \
        --video <powerpoint_video> --force

Examples:
    python3 scripts/narration_sync.py fingerprint projects/demo
    python3 scripts/narration_sync.py animations projects/demo
    python3 scripts/narration_sync.py subtitles projects/demo --pptx exports/demo.pptx --force
    python3 scripts/narration_sync.py subtitles projects/demo \
        --pptx exports/demo.pptx --video exports/demo.mp4 --force

Dependencies:
    ffprobe for animation-window validation. Optional exported-video timeline
    calibration additionally requires ffmpeg and numpy.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import posixpath
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from console_encoding import configure_utf8_stdio  # noqa: E402
from slide_roster import sort_slide_paths  # noqa: E402
from pptx_animations import (  # noqa: E402
    ANIMATION_TIMING_OPTION_FIELDS,
    animation_seconds_to_milliseconds,
    normalize_animation_effect,
    normalize_animation_trigger,
)
from pptx_transitions import (  # noqa: E402
    DEFAULT_TRANSITION_DURATION,
    normalize_transition_effect_request,
    read_slide_transition_xml,
    validate_seconds,
)
from svg_to_pptx.animation_config import (  # noqa: E402
    animation_group_effect_entries,
    scan_project_targets,
    scan_svg_targets,
    validate_animation_config_errors,
    validate_transition_config,
)
from svg_to_pptx.pptx_package.narration import (  # noqa: E402
    DEFAULT_NARRATION_START_FLOOR,
    NARRATION_EXTENSIONS,
    narration_lead_in_seconds,
    probe_audio_duration,
    read_narration_start_delay_xml,
)

configure_utf8_stdio()


_PML_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_DOC_REL_NS = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
)
_TIMING_RE = re.compile(
    r"^(?P<start>\d+:\d{2}:\d{2},\d{3})\s+-->\s+"
    r"(?P<end>\d+:\d{2}:\d{2},\d{3})(?:\s+.*)?$"
)
_ALIGNMENT_SAMPLE_RATE = 8000
_ALIGNMENT_FINE_HZ = 1000
_ALIGNMENT_COARSE_FACTOR = 10
_ALIGNMENT_SEARCH_WINDOW_MS = 2000
_ALIGNMENT_REFINE_WINDOW_MS = 100
_ALIGNMENT_TEMPLATE_MAX_MS = 12_000
_ALIGNMENT_MIN_CORRELATION = 0.85
_ALIGNMENT_END_TOLERANCE_MS = 100


@dataclass(frozen=True)
class SubtitleCue:
    """One parsed SRT cue on a millisecond timeline."""

    start_ms: int
    end_ms: int
    text: str


@dataclass(frozen=True)
class TimingPlanEntry:
    """One ordered animation target and its optional SRT cue anchor."""

    group_id: str
    cue_number: int | None


@dataclass(frozen=True)
class AnimationGroupState:
    """One effective canonical animation row before narration timing."""

    group_id: str
    effect_index: int | None
    order: int
    source_index: int
    duration_ms: int
    original_delay_ms: int
    trigger: str


@dataclass(frozen=True)
class SlideAnimationSettings:
    """Effective animation settings inherited by one slide."""

    effect: str | None
    duration_ms: int
    stagger_ms: int
    trigger: str
    timing_options: dict[str, Any]


@dataclass(frozen=True)
class AnimationBuildResult:
    """Summary of one narration animation derivation."""

    slide_count: int
    group_count: int
    anchored_count: int
    fallback_count: int
    ignored_cue_count: int
    svg_fallback_slide_count: int
    timing_plan_written: bool


@dataclass(frozen=True)
class SubtitleMergeResult:
    """Summary of one page-local SRT merge."""

    slide_count: int
    cue_count: int
    powerpoint_timeline_ms: int
    minimum_video_adjustment_ms: int | None = None
    maximum_video_adjustment_ms: int | None = None
    minimum_video_correlation: float | None = None


@dataclass(frozen=True)
class PowerPointTiming:
    """One slide's transition, narration, and advance timing in milliseconds."""

    transition_ms: int
    narration_delay_ms: int
    advance_ms: int


@dataclass(frozen=True)
class VideoSlideTiming:
    """One slide mapped from the PPTX clock to an exported-video clock."""

    slide_name: str
    transition_ms: int
    narration_delay_ms: int
    advance_ms: int
    powerpoint_slide_start_ms: int
    powerpoint_narration_start_ms: int
    video_slide_start_ms: int
    video_narration_start_ms: int
    adjustment_ms: int
    correlation: float
    audio_path: Path


@dataclass(frozen=True)
class VideoTimelineCalibration:
    """Page-level calibration between one narrated PPTX and exported video."""

    slides: tuple[VideoSlideTiming, ...]
    powerpoint_timeline_ms: int

