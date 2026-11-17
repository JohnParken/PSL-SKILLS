"""PPT Master motion core; responsibility-split, with stable original API.

MIT Copyright (c) 2025-2026 Hugo He.
Upstream commit: 7e0df6705de0cd24345210d31f07fcb5a32b6900.
"""
from __future__ import annotations
import json
from pathlib import Path, PureWindowsPath
from typing import Any
from .pptx_package.narration import AUDIO_CONTENT_TYPES

def _declared_animation_sounds(
    config: dict[str, Any],
) -> tuple[tuple[str, object], ...]:
    """Return explicitly declared sidecar sound values with scope labels."""
    from .animation_config import (
        animation_group_effect_entries,
        json,
    )
    sounds: list[tuple[str, object]] = []
    defaults = config.get('defaults', {})
    if isinstance(defaults, dict):
        animation = defaults.get('animation', {})
        if isinstance(animation, dict) and 'sound' in animation:
            sounds.append(('defaults animation', animation['sound']))

    slides = config.get('slides', {})
    if not isinstance(slides, dict):
        return tuple(sounds)
    for slide_name, slide_cfg in slides.items():
        if not isinstance(slide_cfg, dict):
            continue
        animation = slide_cfg.get('animation', {})
        if isinstance(animation, dict) and 'sound' in animation:
            sounds.append((f'slide "{slide_name}" animation', animation['sound']))
        groups = slide_cfg.get('groups', {})
        if not isinstance(groups, dict):
            continue
        for group_id, group_cfg in groups.items():
            if not isinstance(group_cfg, dict):
                continue
            path = (
                f'slides[{json.dumps(str(slide_name), ensure_ascii=False)}]'
                f'.groups[{json.dumps(str(group_id), ensure_ascii=False)}]'
            )
            try:
                effect_entries = animation_group_effect_entries(
                    group_cfg,
                    path=path,
                )
            except ValueError:
                continue
            for effect_path, effect_cfg in effect_entries:
                if 'sound' in effect_cfg:
                    sounds.append((effect_path, effect_cfg['sound']))
    return tuple(sounds)


def _animation_sound_path_errors(
    project_path: Path,
    config: dict[str, Any],
) -> list[str]:
    """Validate declared animation sound files against the project root."""
    from .animation_config import (
        AUDIO_CONTENT_TYPES,
        Path,
        _declared_animation_sounds,
    )
    errors: list[str] = []
    project_root = project_path.resolve()
    for label, raw_sound in _declared_animation_sounds(config):
        if not isinstance(raw_sound, str) or not raw_sound.strip():
            continue
        sound_path = Path(raw_sound)
        if sound_path.suffix.lower() not in AUDIO_CONTENT_TYPES:
            errors.append(
                f'animations.json {label} sound must use .m4a, .mp3, or .wav'
            )
            continue
        if not sound_path.is_absolute():
            sound_path = project_root / sound_path
        sound_path = sound_path.resolve()
        if not sound_path.exists():
            errors.append(
                f'animations.json {label} sound file not found: {sound_path}'
            )
        elif not sound_path.is_file():
            errors.append(
                f'animations.json {label} sound path is not a regular file: '
                f'{sound_path}'
            )
    return errors


def _declared_transition_sounds(
    config: dict[str, Any],
) -> tuple[tuple[str, object], ...]:
    """Return explicitly declared non-null transition sound values."""
    sounds: list[tuple[str, object]] = []
    defaults = config.get('defaults', {})
    if isinstance(defaults, dict):
        transition = defaults.get('transition', {})
        if (
            isinstance(transition, dict)
            and transition.get('sound') is not None
        ):
            sounds.append(('defaults transition', transition['sound']))

    slides = config.get('slides', {})
    if not isinstance(slides, dict):
        return tuple(sounds)
    for slide_name, slide_cfg in slides.items():
        if not isinstance(slide_cfg, dict):
            continue
        transition = slide_cfg.get('transition', {})
        if (
            isinstance(transition, dict)
            and transition.get('sound') is not None
        ):
            sounds.append(
                (f'slide "{slide_name}" transition', transition['sound'])
            )
    return tuple(sounds)


def _transition_sound_path_errors(
    project_path: Path,
    config: dict[str, Any],
) -> list[str]:
    """Validate transition sounds as project-contained WAV files."""
    from .animation_config import (
        Path,
        PureWindowsPath,
        _declared_transition_sounds,
    )
    errors: list[str] = []
    project_root = project_path.resolve()
    for label, raw_sound in _declared_transition_sounds(config):
        if not isinstance(raw_sound, str) or not raw_sound.strip():
            continue
        sound_path = Path(raw_sound)
        if sound_path.is_absolute() or PureWindowsPath(raw_sound).drive:
            errors.append(
                f'animations.json {label} sound must be project-relative: '
                f'{raw_sound!r}'
            )
            continue
        if sound_path.suffix.lower() != '.wav':
            continue
        resolved_path = (project_root / sound_path).resolve()
        try:
            resolved_path.relative_to(project_root)
        except ValueError:
            errors.append(
                f'animations.json {label} sound escapes the project root: '
                f'{raw_sound!r}'
            )
            continue
        if not resolved_path.exists():
            errors.append(
                f'animations.json {label} sound file not found: {resolved_path}'
            )
        elif not resolved_path.is_file():
            errors.append(
                f'animations.json {label} sound path is not a regular file: '
                f'{resolved_path}'
            )
    return errors


