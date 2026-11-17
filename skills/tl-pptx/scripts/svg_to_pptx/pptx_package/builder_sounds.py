"""Core PPTX assembly: create_pptx_with_native_svg."""
# Adapted from hugohe3/ppt-master 7e0df6705de0cd24345210d31f07fcb5a32b6900.
# MIT; Copyright (c) 2025-2026 Hugo He. See references/LICENSE-PPT-MASTER.txt and references/upstream.md.

from __future__ import annotations
import hashlib
import re
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import (
    Path,
    PureWindowsPath,
)
from typing import Any
from .media import (
    convert_svg_to_png_cached,
)
from .narration import (
    AUDIO_CONTENT_TYPES,
    AUDIO_REL_TYPE,
)

def _next_relationship_id(rel_entries: list[dict[str, str]]) -> str:
    """Return the next slide relationship id, keeping rId1 for the layout."""
    used = {1}
    for rel in rel_entries:
        match = re.fullmatch(r'rId(\d+)', str(rel.get('id', '')))
        if match:
            used.add(int(match.group(1)))
    candidate = 2
    while candidate in used:
        candidate += 1
    return f'rId{candidate}'


def _materialize_slide_sound(
    project_path: Path,
    raw_sound: str,
    media_files: dict[str, bytes],
    rel_entries: list[dict[str, str]],
    audio_exts_used: set[str],
    packaged_by_source: dict[Path, tuple[str, str]],
    *,
    label: str,
    media_prefix: str,
    require_project_relative_wav: bool,
) -> dict[str, str]:
    """Package one slide sound and return its relationship descriptor."""
    if not isinstance(raw_sound, str) or not raw_sound.strip():
        raise ValueError(f'{label} sound must be a non-empty path string')
    sound_path = Path(raw_sound)
    if require_project_relative_wav:
        if sound_path.is_absolute() or PureWindowsPath(raw_sound).drive:
            raise ValueError(f'{label} sound must be project-relative: {raw_sound!r}')
        extension = sound_path.suffix.lower()
        if extension != '.wav':
            raise ValueError(f'{label} sound must use .wav')
        project_root = project_path.resolve()
        sound_path = (project_root / sound_path).resolve()
        try:
            sound_path.relative_to(project_root)
        except ValueError as exc:
            raise ValueError(
                f'{label} sound escapes the project root: {raw_sound!r}'
            ) from exc
    else:
        if not sound_path.is_absolute():
            sound_path = project_path / sound_path
        sound_path = sound_path.resolve()
        extension = sound_path.suffix.lower()

    if not sound_path.is_file():
        raise ValueError(f'{label} sound file not found: {sound_path}')
    if extension not in AUDIO_CONTENT_TYPES:
        valid = ', '.join(sorted(AUDIO_CONTENT_TYPES))
        raise ValueError(
            f'unsupported {label} sound format {extension or "(none)"}; '
            f'valid formats: {valid}'
        )

    packaged = packaged_by_source.get(sound_path)
    if packaged is None:
        payload = sound_path.read_bytes()
        if require_project_relative_wav and not (
            len(payload) >= 12
            and payload[:4] in {b'RIFF', b'RF64'}
            and payload[8:12] == b'WAVE'
        ):
            raise ValueError(f'{label} sound is not a valid WAV file: {sound_path}')
        digest = hashlib.sha256(payload).hexdigest()[:16]
        media_name = f'{media_prefix}_{digest}{extension}'
        relationship_id = _next_relationship_id(rel_entries)
        media_files.setdefault(media_name, payload)
        rel_entries.append(
            {
                'id': relationship_id,
                'type': AUDIO_REL_TYPE,
                'target': f'../media/{media_name}',
            }
        )
        packaged = (relationship_id, media_name)
        packaged_by_source[sound_path] = packaged
        audio_exts_used.add(extension)

    relationship_id, _media_name = packaged
    return {
        'relationship_id': relationship_id,
        'name': sound_path.name,
    }


def _materialize_transition_sound(
    project_path: Path,
    raw_sound: str | None,
    media_files: dict[str, bytes],
    rel_entries: list[dict[str, str]],
    audio_exts_used: set[str],
    packaged_by_source: dict[Path, tuple[str, str]],
) -> dict[str, str] | None:
    """Package one optional project-local WAV for a slide transition."""
    if raw_sound is None:
        return None
    return _materialize_slide_sound(
        project_path,
        raw_sound,
        media_files,
        rel_entries,
        audio_exts_used,
        packaged_by_source,
        label='transition',
        media_prefix='transition_sound',
        require_project_relative_wav=True,
    )


def _materialize_animation_sounds(
    project_path: Path,
    targets: list[dict[str, Any]],
    media_files: dict[str, bytes],
    rel_entries: list[dict[str, str]],
    audio_exts_used: set[str],
    packaged_by_source: dict[Path, tuple[str, str]] | None = None,
) -> list[dict[str, Any]]:
    """Package sidecar sound files and replace paths with OOXML relationships."""
    materialized: list[dict[str, Any]] = []
    packaged_by_source = packaged_by_source if packaged_by_source is not None else {}
    for index, raw_target in enumerate(targets, 1):
        target = dict(raw_target)
        raw_sound = target.pop('_sound_path', None)
        if raw_sound is None:
            materialized.append(target)
            continue
        target['sound'] = _materialize_slide_sound(
            project_path,
            raw_sound,
            media_files,
            rel_entries,
            audio_exts_used,
            packaged_by_source,
            label=f'animation target {index}',
            media_prefix='animation_sound',
            require_project_relative_wav=False,
        )
        materialized.append(target)
    return materialized


def _prerender_legacy_pngs(
    svg_files: list[Path],
    media_dir: Path,
    pixel_width: int,
    pixel_height: int,
    cache_dir: Path | None,
    workers: int,
    verbose: bool,
) -> dict[int, bool]:
    """Render every SVG→PNG into media_dir in parallel.

    Returns {1-based slide index: success}. Falls back to sequential when
    workers<=1 or len(svg_files)<=2.
    """
    results: dict[int, bool] = {}
    targets: list[tuple[int, Path, Path]] = [
        (i, svg, media_dir / f'image{i}.png')
        for i, svg in enumerate(svg_files, 1)
    ]

    if workers <= 1 or len(targets) <= 2:
        for i, svg, png in targets:
            ok = convert_svg_to_png_cached(svg, png, pixel_width, pixel_height, cache_dir)
            results[i] = ok
            if verbose:
                tag = 'cached/ok' if ok else 'failed'
                print(f"  [PNG {i}/{len(targets)}] {svg.name} - {tag}")
        return results

    with ProcessPoolExecutor(max_workers=workers) as pool:
        future_map = {
            pool.submit(
                convert_svg_to_png_cached,
                svg, png, pixel_width, pixel_height, cache_dir,
            ): (i, svg)
            for i, svg, png in targets
        }
        done = 0
        for future in as_completed(future_map):
            i, svg = future_map[future]
            try:
                ok = future.result()
            except Exception as exc:
                ok = False
                if verbose:
                    print(f"  [PNG] {svg.name} - worker error: {exc}")
            results[i] = ok
            done += 1
            if verbose:
                tag = 'cached/ok' if ok else 'failed'
                print(f"  [PNG {done}/{len(targets)}] {svg.name} - {tag}")

    return results
