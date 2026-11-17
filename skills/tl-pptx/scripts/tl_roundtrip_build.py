"""Preserved native builder orchestration for regenerated private workspaces."""
from __future__ import annotations

import json
from pathlib import Path

from tl_guard import ContractError, bare_name
from tl_build import _sound_references
from tl_roundtrip_notes import find_notes_files
from tl_roundtrip_classify import (
    _roundtrip_animation_config_for_pages, _roundtrip_passthrough_candidates,
    _roundtrip_slide_patches,
)
from pptx_workspace import (
    load_roundtrip_manifest, native_structure_path, source_pptx_path, workspace_resource_specs,
)
from svg_to_pptx.pptx_package.template_structure import PptxStructureLock, load_native_structure_contract
from svg_to_pptx.pptx_package.builder import create_pptx_with_native_svg
from svg_to_pptx.animation_config import validate_animation_config_errors


def build_roundtrip(workspace, materialized, *, notes=None, animation_config=None):
    """Return bytes/evidence without publishing or scanning caller directories."""
    workspace = Path(workspace).resolve()
    pages = materialized.pages
    stems = {page.svg_stem for page in pages}
    if notes is not None:
        if not isinstance(notes, dict) or set(notes) - stems:
            raise ContractError('Notes must name output slide stems')
        for stem, text in notes.items():
            bare_name(stem + '.md', '.md')
            if not isinstance(text, str) or len(text) > 100000:
                raise ContractError('Notes must be bounded text')
            (workspace / 'notes').mkdir(exist_ok=True)
            (workspace / 'notes' / (stem + '.md')).write_text(text, encoding='utf-8')
    motion_path = workspace / 'animations.json'
    if animation_config is not None:
        if not isinstance(animation_config, dict):
            raise ContractError('Animation configuration must be an object')
        errors = validate_animation_config_errors(animation_config)
        if errors:
            raise ContractError('Invalid animation configuration: ' + '; '.join(map(str, errors)))
        for sound in _sound_references(animation_config):
            if not isinstance(sound, str) or '\\' in sound or ':' in sound:
                raise ContractError('Animation sound must be an existing local workspace resource')
            path = (workspace / sound).resolve()
            if not path.is_relative_to(workspace) or not path.is_file():
                raise ContractError('Animation sound is outside the regenerated resource inventory')
        motion_path.write_text(json.dumps(animation_config), encoding='utf-8')
    motion = json.loads(motion_path.read_text())
    passthrough = _roundtrip_passthrough_candidates(
        workspace, list(materialized.svg_files), pages, source_dir='authoring-svg-flat',
        authoring_report=materialized.report, changed_resource_pages=frozenset(),
    )
    patches = _roundtrip_slide_patches(
        workspace, materialized.report, passthrough, pages,
        changed_resource_pages=frozenset(), force_visual_changed=False,
        force_motion_changed=False, force_transition_changed=False,
        force_transition_replaced=False, force_animation_changed=False, force_notes_changed=False,
    )
    lock = PptxStructureLock(mode='preserve', source_template=source_pptx_path(workspace),
                             native_structure=native_structure_path(workspace))
    contract = load_native_structure_contract(lock)
    manifest = load_roundtrip_manifest(workspace)
    if manifest is None:
        raise ContractError('Regenerated roundtrip manifest missing')
    resources = workspace_resource_specs(workspace, manifest)
    output = workspace / 'rebuilt.pptx'
    succeeded = create_pptx_with_native_svg(
        list(materialized.svg_files), output, resource_root=workspace,
        verbose=False, use_native_shapes=True, workers=1, cache_dir=None,
        dangerous_nonconforming_export=False, pptx_structure='preserve',
        native_objects=True, transition=None, native_structure_contract=contract,
        roundtrip_passthrough_slides=passthrough, roundtrip_slide_patches=patches,
        roundtrip_resources=resources, roundtrip_page_sources=tuple(page.source_slide for page in pages),
        animation_config=_roundtrip_animation_config_for_pages(motion, pages),
        animation_resource_root=workspace,
        notes=find_notes_files(workspace, list(materialized.svg_files)), text_flow='preserve',
    )
    if not succeeded or not output.is_file() or output.is_symlink():
        raise ContractError('Preserve-mode builder did not produce a regular file')
    if output.stat().st_size > 128 * 1024 * 1024:
        raise ContractError('Native roundtrip output byte budget exceeded')
    return output.read_bytes(), {'builder': 'full-upstream-preserve', 'slide_count': len(pages),
                                'passthrough_pages': sorted(passthrough), 'patched_pages': sorted(patches)}
