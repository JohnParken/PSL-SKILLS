#!/usr/bin/env python3
"""
PPT Master - Video Sound Mix

Recover transition and object-animation sound cues from the final narrated
PPTX, calibrate them to a PowerPoint-exported video, render an independent SFX
stem, and mix that stem with the video's narration track.
See workflows/stages/generate-audio.md for the owning delivery stage.

Usage:
    python3 scripts/video_sound_mix.py <project_path> --pptx <pptx> \
        --trace <trace.json> --video <raw.mp4> [options]

Examples:
    python3 scripts/video_sound_mix.py projects/demo \
        --pptx exports/demo_narrated.pptx \
        --trace validation/demo_narrated.trace.json \
        --video exports/demo_raw.mp4 --force

Dependencies:
    ffmpeg, ffprobe, and numpy
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from console_encoding import configure_utf8_stdio  # noqa: E402
from narration_sync import (  # noqa: E402
    VideoTimelineCalibration,
    calibrate_video_timeline,
    presentation_slide_members,
)
from pptx_animations import read_slide_animation_sequence  # noqa: E402
from pptx_opc_validation import (  # noqa: E402
    canonical_opc_part_path,
    resolve_internal_opc_target,
)
from pptx_transitions import (  # noqa: E402
    AUDIO_REL_TYPE,
    read_slide_transition_xml,
)

configure_utf8_stdio()


_PACKAGE_REL_NS = (
    "http://schemas.openxmlformats.org/package/2006/relationships"
)
_RELATIONSHIP_TAG = f"{{{_PACKAGE_REL_NS}}}Relationship"
_DEFAULT_TRANSITION_GAIN_DB = -9.1
_DEFAULT_ANIMATION_GAIN_DB = -12.0
_DEFAULT_LIMITER_DBFS = -1.0
_OUTPUT_SAMPLE_RATE = 48_000
_OUTPUT_AUDIO_BITRATE = "192k"
_START_TIME_TOLERANCE_MS = 1
_VERIFICATION_SAMPLE_RATE = 8000
_MIN_VERIFICATION_WINDOW_SAMPLES = 2
_MIN_SOUND_MIX_CORRELATION = 0.20


@dataclass(frozen=True)
class SoundCue:
    """One resolved PPTX sound cue placed on the exported-video timeline."""

    kind: str
    slide_num: int
    slide_name: str
    row_index: int | None
    shape_id: int | None
    group_id: str | None
    relationship_id: str
    sound_name: str
    package_part: str
    media_sha256: str
    media_path: Path
    source_duration_ms: int
    powerpoint_time_ms: int
    video_time_ms: int
    gain_db: float


@dataclass(frozen=True)
class SoundMixResult:
    """Published files and cue count from one video sound mix."""

    output_path: Path
    stem_path: Path
    report_path: Path
    cue_count: int
    verification_correlation: float
    true_peak_dbfs: float

