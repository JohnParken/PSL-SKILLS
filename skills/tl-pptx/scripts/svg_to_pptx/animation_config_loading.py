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
from pptx_animations import ANIMATIONS, ANIMATION_MODES, normalize_animation_effect
from pptx_transitions import normalize_transition_effect
from slide_roster import discover_slide_svgs

def resolve_slide_svg_files(project_path: Path) -> tuple[list[Path], str | None]:
    """Return the project's slide SVGs in output order, or an error message.

    A Generate project keeps its roster in ``svg_output/``. A round-trip
    workspace keeps it in ``authoring-svg-flat/``; when ``page_plan.json``
    exists its ``pages`` order is the output roster (each entry's ``svg``
    name, else the source page's ``slide_NN.svg``), which is the roster the
    round-trip exporter reads the sidecar against. A plan the exporter would
    reject falls back to the authoring files in filename order.
    """
    from .animation_config import (
        ROUNDTRIP_AUTHORING_DIR,
        ROUNDTRIP_PAGE_PLAN,
        discover_slide_svgs,
        json,
        re,
    )
    svg_dir = project_path / 'svg_output'
    if svg_dir.is_dir():
        return discover_slide_svgs(svg_dir), None
    authoring_dir = project_path / ROUNDTRIP_AUTHORING_DIR
    if not authoring_dir.is_dir():
        return [], f'svg_output directory not found: {svg_dir}'
    files = discover_slide_svgs(authoring_dir)
    plan_path = project_path / ROUNDTRIP_PAGE_PLAN
    if not plan_path.is_file():
        return files, None
    by_source: dict[int, Path] = {}
    for path in files:
        match = re.fullmatch(r'slide_(\d+)', path.stem)
        if match:
            by_source[int(match.group(1))] = path
    try:
        pages = json.loads(plan_path.read_text(encoding='utf-8')).get('pages')
    except (OSError, ValueError, AttributeError):
        return files, None
    if not isinstance(pages, list):
        return files, None
    ordered: list[Path] = []
    for raw in pages:
        if not isinstance(raw, dict):
            return files, None
        svg_name = raw.get('svg')
        if isinstance(svg_name, str) and svg_name:
            path = authoring_dir / svg_name
        else:
            path = by_source.get(raw.get('source_slide'))
        if path is None or not path.is_file():
            return files, None
        ordered.append(path)
    return (ordered or files), None


def scan_project_targets(
    project_path: Path,
    *,
    svg_files: list[Path] | None = None,
    include_hidden: bool = False,
) -> tuple[dict[str, list[GroupTarget]], list[str]]:
    """Scan selected SVG files, defaulting to the project's slide roster."""
    from .animation_config import (
        resolve_slide_svg_files,
        scan_svg_targets,
    )
    targets_by_slide: dict[str, list[GroupTarget]] = {}
    anonymous_groups: list[str] = []
    if svg_files is None:
        svg_files, error = resolve_slide_svg_files(project_path)
        if error is not None:
            return targets_by_slide, [error]

    for svg_path in svg_files:
        targets, anonymous = scan_svg_targets(svg_path, include_hidden=include_hidden)
        targets_by_slide[svg_path.stem] = targets
        anonymous_groups.extend(anonymous)

    return targets_by_slide, anonymous_groups


def default_config_path(project_path: Path) -> Path:
    return project_path / 'animations.json'


def load_animation_config(project_path: Path, config_path: str | None = None) -> dict[str, Any] | None:
    """Load animation config; only an absent default sidecar is optional."""
    from .animation_config import (
        Path,
        default_config_path,
        json,
    )
    if config_path is not None:
        if not config_path.strip():
            raise ValueError('Animation config path must be non-empty')
        path = Path(config_path)
    else:
        path = default_config_path(project_path)
    if config_path is not None and not path.is_absolute():
        path = project_path / path
    if not path.exists():
        if config_path is not None:
            raise FileNotFoundError(f'Animation config does not exist: {path}')
        return None

    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f'Animation config must be a JSON object: {path}')
    if data.get('version', 1) != 1:
        raise ValueError(f'Unsupported animation config version: {data.get("version")}')
    return data


def _valid_transition_effect(effect: str) -> bool:
    from .animation_config import (
        normalize_transition_effect,
    )
    try:
        normalize_transition_effect(effect)
    except ValueError:
        return False
    return True


def _animation_effect_error(effect: object, label: str) -> str | None:
    from .animation_config import (
        ANIMATIONS,
        ANIMATION_MODES,
        normalize_animation_effect,
    )
    if not isinstance(effect, str):
        return f'animations.json {label} animation effect must be a string'
    try:
        normalize_animation_effect(effect)
    except ValueError:
        valid = ', '.join((*ANIMATIONS, *ANIMATION_MODES, 'none'))
        return (
            f'animations.json {label} has unknown animation effect: {effect}; '
            f'valid effects: {valid}'
        )
    return None


def resolve_slide_animation_config(
    default_animation: dict[str, Any],
    slide_animation: dict[str, Any],
) -> dict[str, Any]:
    """Merge one slide animation over defaults using writer inheritance rules."""
    resolved = dict(default_animation)
    if 'effect' in slide_animation and 'effect_options' not in slide_animation:
        resolved.pop('effect_options', None)
    resolved.update(slide_animation)
    return resolved


def animation_group_effect_entries(
    group_cfg: dict[str, Any],
    *,
    path: str,
) -> tuple[tuple[str, dict[str, Any]], ...]:
    """Expand one legacy group block or one ordered multi-effect envelope."""
    if 'effects' not in group_cfg:
        return ((path, group_cfg),)

    extra_fields = sorted(set(group_cfg) - {'effects'})
    if extra_fields:
        rendered = ', '.join(repr(field) for field in extra_fields)
        raise ValueError(
            f'animations.json {path} cannot combine "effects" with '
            f'other group-level field(s): {rendered}'
        )
    effects = group_cfg['effects']
    if not isinstance(effects, list):
        raise ValueError(f'animations.json {path}.effects must be an array')
    if not effects:
        raise ValueError(
            f'animations.json {path}.effects must contain at least one effect'
        )

    entries: list[tuple[str, dict[str, Any]]] = []
    for index, effect_cfg in enumerate(effects):
        effect_path = f'{path}.effects[{index}]'
        if not isinstance(effect_cfg, dict):
            raise ValueError(
                f'animations.json {effect_path} must be an object'
            )
        if 'effect' not in effect_cfg:
            raise ValueError(
                f'animations.json {effect_path}.effect is required'
            )
        entries.append((effect_path, effect_cfg))
    return tuple(entries)


