"""PPT Master motion core; responsibility-split, with stable original API.

MIT Copyright (c) 2025-2026 Hugo He.
Upstream commit: 7e0df6705de0cd24345210d31f07fcb5a32b6900.
"""
from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from .animation_config import GroupTarget
import json
import re
from pathlib import Path
from typing import Any
from pptx_animations import normalize_animation_effect

def validate_animation_config(
    project_path: Path,
    config: dict[str, Any] | None = None,
    config_path: str | None = None,
    *,
    svg_files: list[Path] | None = None,
) -> list[str]:
    """Return sidecar-reference diagnostics for the selected SVG slides.

    Fatal field/type/value checks are owned by
    :func:`validate_animation_config_errors`. Anonymous groups are warnings;
    references to invalid sound files, missing slides/groups, and structural
    targets are fatal at export call sites. Slides omitted from a sparse
    sidecar inherit defaults.
    """
    from .animation_config import (
        _animation_sound_path_errors,
        _duplicate_target_error,
        _duplicate_target_ids,
        _lock_structure_mode,
        _resolve_morph_pairs,
        _transition_sound_path_errors,
        animation_group_effect_entries,
        json,
        load_animation_config,
        normalize_animation_effect,
        resolve_slide_animation_config,
        resolve_slide_svg_files,
        scan_project_targets,
        scan_root_primitives,
    )
    if config is None:
        config = load_animation_config(project_path, config_path)
    if not config:
        return []

    warnings = _animation_sound_path_errors(project_path, config)
    warnings.extend(_transition_sound_path_errors(project_path, config))
    targets_by_slide, anonymous_groups = scan_project_targets(
        project_path,
        svg_files=svg_files,
        include_hidden=True,
    )
    for item in anonymous_groups:
        warnings.append(f'{item} has no id and cannot be customized in animations.json')

    duplicates_by_slide: dict[str, tuple[str, ...]] = {}
    for slide_name, targets in targets_by_slide.items():
        duplicates = _duplicate_target_ids(targets)
        if duplicates:
            duplicates_by_slide[slide_name] = duplicates
    for slide_name, duplicates in duplicates_by_slide.items():
        warnings.append(_duplicate_target_error(slide_name, duplicates))

    known_slides = set(targets_by_slide)
    known_groups_by_slide: dict[str, dict[str, GroupTarget]] = {}
    for slide_name, slide_targets in targets_by_slide.items():
        ambiguous_ids = set(duplicates_by_slide.get(slide_name, ()))
        known_groups_by_slide[slide_name] = {
            target.group_id: target
            for target in slide_targets
            if target.group_id not in ambiguous_ids
        }
    default_animation: dict[str, Any] = {'effect': 'none'}
    defaults = config.get('defaults', {})
    if isinstance(defaults, dict):
        animation_value = defaults.get('animation', {})
        if isinstance(animation_value, dict):
            default_animation = resolve_slide_animation_config(
                default_animation,
                animation_value,
            )
    slides = config.get('slides', {})
    if not isinstance(slides, dict):
        return list(dict.fromkeys(warnings))
    for slide_name, slide_cfg in slides.items():
        if slide_name not in known_slides:
            warnings.append(f'animations.json references missing slide: {slide_name}')
            continue
        if not isinstance(slide_cfg, dict):
            continue

        slide_animation = default_animation
        animation_value = slide_cfg.get('animation', {})
        if isinstance(animation_value, dict):
            slide_animation = resolve_slide_animation_config(
                default_animation,
                animation_value,
            )
        slide_targets = targets_by_slide.get(slide_name, [])
        duplicate_ids = duplicates_by_slide.get(slide_name, ())
        ambiguous_ids = set(duplicate_ids)
        known_groups = known_groups_by_slide.get(slide_name, {})
        groups = slide_cfg.get('groups', {})
        if not isinstance(groups, dict):
            continue
        for group_id, group_cfg in groups.items():
            path = (
                f'slides[{json.dumps(str(slide_name), ensure_ascii=False)}]'
                f'.groups[{json.dumps(str(group_id), ensure_ascii=False)}]'
            )
            if group_id in ambiguous_ids:
                continue
            if group_id not in known_groups:
                warnings.append(
                    f'animations.json {path} references a missing group'
                )
                continue
            target = known_groups[group_id]
            if target.hidden_reason:
                warnings.append(
                    f'animations.json {path} references a group that is '
                    f'hidden, not exported ({target.hidden_reason})'
                )
                continue
            if not isinstance(group_cfg, dict):
                continue
            try:
                effect_entries = animation_group_effect_entries(
                    group_cfg,
                    path=path,
                )
            except ValueError:
                continue
            if (
                target.structurally_static
                and any(
                    normalize_animation_effect(
                        effect_cfg.get(
                            'effect',
                            slide_animation.get('effect', 'none'),
                        ),
                        allow_none=True,
                        allow_modes=True,
                    )
                    is not None
                    for _effect_path, effect_cfg in effect_entries
                )
            ):
                warnings.append(
                    f'animations.json {path} references a non-animatable '
                    'structural group'
                )
            for effect_path, effect_cfg in effect_entries:
                trigger_shape = effect_cfg.get('trigger_shape')
                if (
                    not isinstance(trigger_shape, str)
                    or not trigger_shape.strip()
                ):
                    continue
                if trigger_shape in ambiguous_ids:
                    warnings.append(
                        f'animations.json {effect_path}.trigger_shape '
                        f'references ambiguous group {trigger_shape!r}'
                    )
                    continue
                trigger_target = known_groups.get(trigger_shape)
                if trigger_target is None:
                    warnings.append(
                        f'animations.json {effect_path}.trigger_shape '
                        f'references missing group {trigger_shape!r}'
                    )
                elif trigger_target.hidden_reason:
                    warnings.append(
                        f'animations.json {effect_path}.trigger_shape '
                        f'references group {trigger_shape!r} that is hidden, '
                        f'not exported ({trigger_target.hidden_reason})'
                    )
                elif trigger_target.structurally_static:
                    warnings.append(
                        f'animations.json {effect_path}.trigger_shape '
                        f'references non-triggerable structural group '
                        f'{trigger_shape!r}'
                    )
                elif trigger_target.has_hyperlink:
                    warnings.append(
                        f'animations.json {effect_path}.trigger_shape '
                        f'references hyperlink-bearing group {trigger_shape!r}; '
                        'use an ordinary animation or a separate trigger'
                    )

    morph_pairs, morph_errors = _resolve_morph_pairs(
        list(targets_by_slide),
        config,
    )
    warnings.extend(morph_errors)
    root_primitives_by_slide: dict[str, dict[str, str]] = {}
    if morph_pairs:
        scan_files = svg_files
        if scan_files is None:
            scan_files, _error = resolve_slide_svg_files(project_path)
        for svg_path in scan_files:
            root_primitives_by_slide[svg_path.stem] = scan_root_primitives(svg_path)
    for pair in morph_pairs:
        for slide_name, group_id in (
            (pair.source_slide, pair.source_group_id),
            (pair.destination_slide, pair.destination_group_id),
        ):
            target = known_groups_by_slide.get(slide_name, {}).get(group_id)
            if target is None:
                primitive = root_primitives_by_slide.get(slide_name, {}).get(group_id)
                if primitive is not None:
                    warnings.append(
                        f'animations.json Morph endpoint {slide_name}/{group_id} '
                        f'is a root primitive ({primitive}), not a direct-root '
                        '<g>; wrap it in a group (drop a static role marker) '
                        'before pairing'
                    )
                else:
                    warnings.append(
                        'animations.json Morph references missing or ambiguous group: '
                        f'{slide_name}/{group_id}'
                    )
            elif target.hidden_reason:
                warnings.append(
                    f'animations.json Morph endpoint {slide_name}/{group_id} '
                    f'is hidden, not exported ({target.hidden_reason})'
                )
            elif target.structurally_static:
                warnings.append(
                    'animations.json Morph references structural group: '
                    f'{slide_name}/{group_id}'
                )
            elif (
                target.placeholder is not None
                and target.on_structured_page
                and _lock_structure_mode(project_path) != 'flat'
            ):
                warnings.append(
                    f'animations.json Morph endpoint {slide_name}/{group_id} '
                    f'is the placeholder slot {target.placeholder!r}: structured '
                    'export rewrites a slot into a layout placeholder, so it '
                    'cannot carry a Morph name; pair a Slide-local group instead'
                )
    return list(dict.fromkeys(warnings))


def _lock_structure_mode(project_path: Path) -> str | None:
    """Return ``spec_lock.md``'s ``pptx_structure.mode``, or None without one."""
    from .animation_config import (
        re,
    )
    lock_path = project_path / 'spec_lock.md'
    try:
        text = lock_path.read_text(encoding='utf-8-sig')
    except OSError:
        return None
    section = re.search(
        r'^##[ \t]+pptx_structure[ \t]*$(?P<body>.*?)(?=^##[ \t]|\Z)',
        text,
        flags=re.MULTILINE | re.DOTALL,
    )
    if section is None:
        return None
    mode = re.search(
        r'^-[ \t]+mode[ \t]*:[ \t]*([A-Za-z_-]+)',
        section.group('body'),
        flags=re.MULTILINE,
    )
    return mode.group(1).lower() if mode else None


def build_scaffold(project_path: Path) -> dict[str, Any]:
    """Build an editable animation override scaffold from current SVGs.

    Chrome groups are omitted — layer/slide-number placeholder semantics are
    authoritative, followed by an explicit structural role. ``is_chrome_id``
    remains only for marker-free legacy SVGs. Listing static page framing in
    the scaffold would be pure noise. A ``defaults`` stub is emitted up front
    to remind the editor that deck-wide overrides exist and most pages should
    inherit them.
    """
    from .animation_config import (
        _require_unique_target_ids,
        scan_project_targets,
    )
    transition_defaults = {
        'effect': 'fade',
        'duration': 0.4,
        'sound': None,
    }
    animation_defaults = {
        'effect': 'none',
        'duration': 0.4,
        'stagger': 0.5,
        'trigger': 'after-previous',
    }
    targets_by_slide, _anonymous = scan_project_targets(project_path)
    slides: dict[str, Any] = {}
    for slide_name, targets in targets_by_slide.items():
        _require_unique_target_ids(slide_name, targets)
        groups: dict[str, Any] = {}
        for target in targets:
            if target.chrome:
                continue
            groups[target.group_id] = {}
        slides[slide_name] = {
            'transition': {
                'effect': transition_defaults['effect'],
                'duration': transition_defaults['duration'],
            },
            'animation': dict(animation_defaults),
            'groups': groups,
        }
    return {
        'version': 1,
        'defaults': {
            'transition': transition_defaults,
            'animation': animation_defaults,
        },
        'slides': slides,
    }


def build_group_listing(project_path: Path) -> tuple[list[str], list[str]]:
    """Return one compact line per slide: ``<slide>: id1, id2, id3``.

    Chrome groups are excluded — matches ``build_scaffold``'s policy so the
    listing reflects exactly what an editor can override. Returns
    ``(lines, anonymous_warnings)``.
    """
    from .animation_config import (
        _require_unique_target_ids,
        scan_project_targets,
    )
    targets_by_slide, anonymous = scan_project_targets(project_path, include_hidden=True)
    lines: list[str] = []
    for slide_name, targets in targets_by_slide.items():
        _require_unique_target_ids(slide_name, targets)
        ids = [t.group_id for t in targets if not t.chrome and not t.hidden_reason]
        chrome_ids = [t.group_id for t in targets if t.chrome and not t.hidden_reason]
        hidden_ids = [t.group_id for t in targets if t.hidden_reason]
        if not ids:
            line = f'{slide_name}: (no animatable groups)'
        else:
            line = f'{slide_name}: {", ".join(ids)}'
        if chrome_ids:
            # Name what was dropped so an id like ``takeaway-rule`` is seen
            # as chrome-by-token rather than silently missing.
            line += f'  [chrome, animates only when named: {", ".join(chrome_ids)}]'
        if hidden_ids:
            line += f'  [hidden, not exported: {", ".join(hidden_ids)}]'
        lines.append(line)
    return lines, anonymous


def write_scaffold(
    project_path: Path,
    output_path: str | None = None,
    *,
    force: bool = False,
) -> Path:
    """Write ``animations.json`` scaffold and return its path."""
    from .animation_config import (
        Path,
        build_scaffold,
        default_config_path,
        json,
    )
    if output_path:
        path = Path(output_path)
    else:
        path = default_config_path(project_path)
    if output_path and not path.is_absolute():
        path = project_path / path
    if path.exists() and not force:
        raise FileExistsError(f'Animation config already exists: {path}')

    scaffold = build_scaffold(project_path)
    path.write_text(
        json.dumps(scaffold, ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8',
    )
    return path


