"""Bounded task manifest adapter for the complete upstream native builder.

Freeze explicit inputs, generate inside an owned private workspace, then return
verified bytes. The public runner owns publication and archival separately.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from tl_workspace import private_workspace

from tl_guard import ContractError, MAX_SLIDES, MAX_TOTAL_BYTES, bare_name, read_local, validate_svg
from tl_package_guard import MAX_MEMBER_BYTES, inspect_package

SCALAR_OPTIONS = {
    'canvas_format', 'transition', 'transition_duration', 'auto_advance', 'kiosk',
    'enable_notes', 'animation', 'animation_duration', 'animation_stagger',
    'animation_trigger', 'use_narration_timings', 'narration_padding',
    'merge_paragraphs', 'image_optimize', 'image_max_dimension', 'image_sizing',
    'image_scale', 'image_quality', 'native_objects', 'structure_name',
    'pptx_structure', 'use_layout_placeholder_frames', 'structured_baseline',
    'expected_viewbox', 'text_flow', 'primary_language', 'narration_start_floor',
}
OBJECT_OPTIONS = {'animation_config', 'animation_cli_overrides', 'doc_metadata', 'transition_effect_options'}
AUDIO_SUFFIXES = {'.wav', '.mp3', '.m4a'}


def _constant_error(value):
    raise ContractError(f'Non-finite JSON number is forbidden: {value}')


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ContractError('Duplicate JSON object key')
        result[key] = value
    return result


def load_manifest(name):
    """Read bounded JSON, rejecting ambiguous keys and excessive structures."""
    payload = read_local(name, '.json', 2 * 1024 * 1024)
    try:
        manifest = json.loads(payload, parse_constant=_constant_error, object_pairs_hook=_unique_object)
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ContractError('Invalid build manifest JSON') from exc
    pending = [(manifest, 0)]
    nodes = 0
    while pending:
        value, depth = pending.pop()
        nodes += 1
        if depth > 40 or nodes > 20000:
            raise ContractError('Manifest complexity budget exceeded')
        if isinstance(value, float) and not math.isfinite(value):
            raise ContractError('Non-finite JSON number is forbidden')
        if isinstance(value, dict):
            pending.extend((child, depth + 1) for child in value.values())
        elif isinstance(value, list):
            pending.extend((child, depth + 1) for child in value)
    if not isinstance(manifest, dict):
        raise ContractError('Build manifest must be an object')
    return manifest


def _snapshot(name, suffixes, snapshots, limit=MAX_MEMBER_BYTES):
    if not isinstance(name, str) or Path(name).suffix.lower() not in suffixes:
        raise ContractError('Resource filename has an unsupported type')
    bare_name(name, Path(name).suffix.lower())
    if name not in snapshots:
        snapshots[name] = read_local(name, Path(name).suffix.lower(), limit)
        if Path(name).suffix.lower() in AUDIO_SUFFIXES:
            from tl_audio_guard import validate_audio
            validate_audio(snapshots[name], Path(name).suffix)
        if sum(map(len, snapshots.values())) > 128 * 1024 * 1024:
            raise ContractError('Combined build input resource budget exceeded')
    return name


def _sound_references(config):
    """Find explicit sound values before any upstream path interpretation."""
    pending = [config]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            for key, child in value.items():
                if key == 'sound' and child is not None and child != '':
                    yield child
                pending.append(child)
        elif isinstance(value, list):
            pending.extend(value)


BOOL_OPTIONS = {
    'kiosk', 'enable_notes', 'use_narration_timings', 'merge_paragraphs',
    'image_optimize', 'native_objects', 'use_layout_placeholder_frames', 'structured_baseline',
}
NUMERIC_OPTIONS = {
    'transition_duration': (0, 4294967), 'auto_advance': (0, 4294967),
    'animation_duration': (0, 4294967), 'animation_stagger': (0, 4294967),
    'narration_padding': (0, 4294967), 'narration_start_floor': (0, 4294967),
    'image_max_dimension': (1, 16384), 'image_scale': (0.01, 16), 'image_quality': (1, 100),
}
NULLABLE_OPTIONS = {
    'canvas_format', 'transition', 'auto_advance', 'animation', 'merge_paragraphs',
    'image_max_dimension', 'structure_name', 'expected_viewbox', 'text_flow', 'primary_language',
}


def _validate_options(options):
    for key, value in options.items():
        if key in OBJECT_OPTIONS:
            if not isinstance(value, dict):
                raise ContractError('Builder object option must be an object')
        elif value is None and key in NULLABLE_OPTIONS:
            continue
        elif key in BOOL_OPTIONS:
            if type(value) is not bool:
                raise ContractError(f'{key} must be a boolean')
        elif key in NUMERIC_OPTIONS:
            low, high = NUMERIC_OPTIONS[key]
            if type(value) not in (int, float) or not low <= value <= high or not math.isfinite(value):
                raise ContractError(f'{key} is outside its bounded numeric contract')
            if key in {'image_max_dimension', 'image_quality'} and type(value) is not int:
                raise ContractError(f'{key} must be an integer')
        elif not isinstance(value, str) or len(value) > 256:
            raise ContractError(f'{key} must be bounded text')


def prepare_build(manifest):
    """Snapshot explicitly listed inputs; never scan task/user directories."""
    if set(manifest) - {'slides', 'layouts', 'options', 'transition_sound', 'spec_lock', 'resources'}:
        raise ContractError('Unknown build manifest field')
    slides = manifest.get('slides')
    if not isinstance(slides, list) or not 1 <= len(slides) <= MAX_SLIDES:
        raise ContractError('Build requires one to fifty explicit slides')
    options = manifest.get('options', {})
    if not isinstance(options, dict) or set(options) - SCALAR_OPTIONS - OBJECT_OPTIONS:
        raise ContractError('Unknown or filesystem-valued builder option')
    _validate_options(options)
    snapshots, notes, audio, names = {}, {}, {}, []
    for slide in slides:
        if not isinstance(slide, dict) or set(slide) - {'svg', 'notes', 'audio'}:
            raise ContractError('Invalid slide declaration')
        name = _snapshot(slide.get('svg'), {'.svg'}, snapshots, 4 * 1024 * 1024)
        if name in names:
            raise ContractError('Duplicate slide filename')
        names.append(name)
        if 'notes' in slide:
            if not isinstance(slide['notes'], str) or len(slide['notes']) > 100000:
                raise ContractError('Slide notes must be bounded text')
            notes[Path(name).stem] = slide['notes']
        if 'audio' in slide:
            audio[Path(name).stem] = _snapshot(slide['audio'], AUDIO_SUFFIXES, snapshots)
    layouts = manifest.get('layouts', [])
    if not isinstance(layouts, list) or len(layouts) > MAX_SLIDES:
        raise ContractError('Layouts must be a bounded filename list')
    for name in layouts:
        _snapshot(name, {'.svg'}, snapshots, 4 * 1024 * 1024)
    for name in _sound_references(options.get('animation_config', {})):
        _snapshot(name, AUDIO_SUFFIXES, snapshots)
    sound = manifest.get('transition_sound')
    if sound is not None:
        _snapshot(sound, {'.wav'}, snapshots)
    specification = manifest.get('spec_lock')
    if specification is not None:
        _snapshot(specification, {'.md'}, snapshots, 1024 * 1024)
        try:
            snapshots[specification].decode('utf-8')
        except UnicodeError as exc:
            raise ContractError('Specification lock must be UTF-8 text') from exc
    resources = manifest.get('resources', {})
    if not isinstance(resources, dict) or len(resources) > 1000:
        raise ContractError('Resources must be a bounded logical-name to task-filename map')
    if resources:
        from tl_svg_resources import _member, materialize_svg_resources
        roster = dict(snapshots)
        equivalents = {name.casefold(): name for name in roster}
        for logical, filename in resources.items():
            _member(logical)
            _snapshot(filename, {'.svg', '.png', '.jpg', '.jpeg'}, snapshots)
            if logical.casefold() in equivalents and roster[equivalents[logical.casefold()]] != snapshots[filename]:
                raise ContractError('Ambiguous or conflicting logical resource name')
            equivalents[logical.casefold()] = logical
            roster[logical] = snapshots[filename]
        expansion_budget = {}
        for name in dict.fromkeys(names + layouts):
            snapshots[name] = materialize_svg_resources(name, roster, expansion_budget)
    checks = [validate_svg(snapshots[name]) for name in dict.fromkeys(names + layouts)]
    if sum(item['resource_bytes'] for item in checks) > MAX_TOTAL_BYTES:
        raise ContractError('Combined SVG decoded resource budget exceeded')
    return {'snapshots': snapshots, 'slides': names, 'layouts': layouts, 'notes': notes,
            'audio': audio, 'options': dict(options), 'transition_sound': sound, 'spec_lock': specification}


def build_package(prepared):
    """Run preserved builder privately, return bytes and structural evidence."""
    from svg_to_pptx.pptx_package.builder import create_pptx_with_native_svg
    with private_workspace(prefix='tl-pptx-build-') as directory:
        root = Path(directory).resolve()
        for name, data in prepared['snapshots'].items():
            with (root / name).open('xb') as stream:
                stream.write(data)
        output = root / 'native-result.pptx'
        options = dict(prepared['options'])
        options.setdefault('pptx_structure', 'flat')
        options.setdefault('native_objects', True)
        options.setdefault('transition', None)
        if prepared['spec_lock'] is not None:
            from svg_to_pptx.drawingml.theme_colors import load_theme_color_spec
            from svg_to_pptx.drawingml.theme_fonts import load_master_text_style_spec, load_theme_font_spec
            if prepared['spec_lock'] != 'spec_lock.md':
                with (root / 'spec_lock.md').open('xb') as stream:
                    stream.write(prepared['snapshots'][prepared['spec_lock']])
            options['theme_font_spec'] = load_theme_font_spec(root, options.get('primary_language'))
            options['master_text_style_spec'] = load_master_text_style_spec(root)
            options['theme_color_spec'] = load_theme_color_spec(root)
        succeeded = create_pptx_with_native_svg(
            [root / name for name in prepared['slides']], output,
            resource_root=root, verbose=False, use_native_shapes=True,
            workers=1, cache_dir=None, dangerous_nonconforming_export=False,
            notes=prepared['notes'],
            narration_audio={name: root / audio for name, audio in prepared['audio'].items()},
            layout_definition_files=[root / name for name in prepared['layouts']] or None,
            animation_resource_root=root, transition_sound=prepared['transition_sound'],
            **options,
        )
        if not succeeded or not output.is_file() or output.is_symlink():
            raise ContractError('Native builder did not produce a regular PPTX')
        if output.stat().st_size > 128 * 1024 * 1024:
            raise ContractError('Native output byte budget exceeded')
        payload = output.read_bytes()
        evidence = inspect_package(payload)
        evidence.pop('entries')
        evidence.update({'slide_count': len(prepared['slides']), 'builder': 'full-upstream',
                         'sha256': hashlib.sha256(payload).hexdigest()})
    return payload, evidence
