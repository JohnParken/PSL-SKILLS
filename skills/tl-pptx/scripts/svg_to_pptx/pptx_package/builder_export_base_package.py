"""Native PPTX export phase; retains upstream control flow."""
# Adapted from hugohe3/ppt-master 7e0df6705de0cd24345210d31f07fcb5a32b6900.
# MIT; Copyright (c) 2025-2026 Hugo He. See references/LICENSE-PPT-MASTER.txt and references/upstream.md.

from __future__ import annotations
import json
import os
import random
import zipfile
from typing import Any
from pptx import Presentation
from pptx_ooxml.package import (
    PackagePartRegistry,
)
from ..drawingml.theme_colors import (
    apply_theme_color_spec,
)
from ..drawingml.theme_fonts import (
    apply_theme_font_spec,
)
from .media import (
    PNG_RENDERER,
)
from .template_structure import (
    TemplateStructureError,
)

from .builder import (
    _as_dict,
    _clear_preserved_slide_collections,
    _content_type_overrides,
    _create_preserved_base_pptx,
    _install_source_embedded_fonts,
    _install_source_theme_xml,
    _prerender_legacy_pngs,
    _read_slide_layout_targets,
    _roundtrip_resource_payloads,
    _set_slide_size_type,
)

from .builder_state import BuildState

def export_base_package(state: BuildState) -> None:
    """Execute upstream base package stage with explicit shared state."""
    state.base_pptx = state.temp_dir / 'base.pptx'
    state.preserved_source_slides = False
    state.page_plan_package_overrides = (
        _roundtrip_resource_payloads(state.resource_root, state.roundtrip_resources)
        if state.page_plan_export
        else None
    )
    state.late_roundtrip_resources = (
        () if state.page_plan_export else state.roundtrip_resources
    )
    if (
        state.use_native_shapes
        and state.pptx_structure == "preserve"
        and state.native_structure_contract is not None
        and state.template_specs is not None
    ):
        state.preserved_source_slides = _create_preserved_base_pptx(
            state.native_structure_contract,
            state.template_specs,
            state.base_pptx,
            (state.width_emu, state.height_emu),
            roundtrip_page_sources=state.roundtrip_page_sources,
            package_overrides=state.page_plan_package_overrides,
            slide_patches=state.slide_patches,
        )
    else:
        # Create the standard base PPTX with python-pptx.
        state.prs = Presentation()
        state.prs.slide_width = state.width_emu
        state.prs.slide_height = state.height_emu
        _set_slide_size_type(state.prs, state.width_emu, state.height_emu)

        state.blank_layout = state.prs.slide_layouts[6]
        for state._ in state.svg_files:
            state.prs.slides.add_slide(state.blank_layout)
        state.prs.save(str(state.base_pptx))

    # Extract PPTX
    state.extract_dir = state.temp_dir / 'pptx_content'
    with zipfile.ZipFile(state.base_pptx, 'r') as state.zf:
        state.zf.extractall(state.extract_dir)
    state.source_content_types_bytes = (
        (state.extract_dir / '[Content_Types].xml').read_bytes()
        if state.roundtrip_export
        else None
    )
    state.roundtrip_source_parts = (
        frozenset(
            path.relative_to(state.extract_dir).as_posix()
            for path in state.extract_dir.rglob('*')
            if path.is_file()
        )
        if state.roundtrip_export
        else frozenset()
    )
    if (
        state.use_native_shapes
        and state.pptx_structure == "preserve"
        and not state.preserved_source_slides
    ):
        _clear_preserved_slide_collections(state.extract_dir)
    if (
        state.passthrough_slides
        or state.overlay_slides
        or state.roundtrip_resources
        or state.page_plan_export
    ) and not state.preserved_source_slides:
        raise TemplateStructureError(
            "Round-trip export requires the original source package roster"
        )
    state.active_theme_font_spec = (
        state.theme_font_spec
        if state.use_native_shapes
        and state.pptx_structure in {"baseline", "flat", "structured"}
        else None
    )
    if state.active_theme_font_spec is not None:
        apply_theme_font_spec(state.extract_dir, state.active_theme_font_spec)
    state.active_theme_color_spec = (
        state.theme_color_spec
        if state.use_native_shapes
        and state.pptx_structure in {"baseline", "flat", "structured"}
        else None
    )
    if state.active_theme_color_spec is not None:
        apply_theme_color_spec(state.extract_dir, state.active_theme_color_spec)
    if (
        state.source_theme_xml is not None
        and state.native_structure_contract is None
    ):
        _install_source_theme_xml(state.extract_dir, state.source_theme_xml)
    if state.source_embedded_fonts is not None and not state.roundtrip_export:
        state.installed_typefaces = _install_source_embedded_fonts(
            state.extract_dir,
            state.source_embedded_fonts,
        )
        if state.verbose:
            print(
                "  Embedded fonts: preserved "
                f"{len(state.source_embedded_fonts.parts)} part(s) for "
                + ", ".join(state.installed_typefaces)
            )
    state.structure = _read_slide_layout_targets(state.extract_dir, len(state.svg_files))

    state.media_dir = state.extract_dir / 'ppt' / 'media'
    state.media_dir.mkdir(exist_ok=True)

    state.prerender_results: dict[int, bool] | None = None
    if not state.use_native_shapes and state.use_compat_mode and PNG_RENDERER is not None:
        if state.workers is None:
            state.resolved_workers = min(os.cpu_count() or 2, len(state.svg_files), 8)
        else:
            state.resolved_workers = max(0, state.workers)
        if state.verbose:
            state.cache_label = str(state.cache_dir) if state.cache_dir else 'disabled'
            state.mode = f'parallel x{state.resolved_workers}' if state.resolved_workers > 1 else 'sequential'
            print(f"  Pre-rendering PNGs ({state.mode}, cache: {state.cache_label})")
        state.prerender_results = _prerender_legacy_pngs(
            state.svg_files, state.media_dir, state.pixel_width, state.pixel_height,
            state.cache_dir, state.resolved_workers, state.verbose,
        )
        if state.verbose:
            print()

    state.success_count = 0
    state.has_any_image = False
    state.media_cache: dict[tuple[str, str], str] = {}
    state.image_exts_used: set[str] = set()
    state.package_exts_used: set[str] = set()
    state.package_content_overrides: dict[str, str] = {}
    state.package_parts = PackagePartRegistry(state.extract_dir, _content_type_overrides(state.extract_dir))
    state.notes_slides_created: set[int] = set()
    state.notes_master_parts_used: set[str] = set()
    state.notes_master_theme_parts_created: set[str] = set()
    state.narration_slides_created: set[int] = set()
    state.replaced_narration_parts: set[str] = set()
    state.audio_exts_used: set[str] = set()
    state.package_uses_timings = False
    state.mixed_animation_offset = 0
    state.config_defaults = _as_dict(_as_dict(state.animation_config).get('defaults'))
    state.transition_defaults_value = state.config_defaults.get('transition', {})
    if not isinstance(state.transition_defaults_value, dict):
        raise ValueError(
            'animations.json defaults transition must be an object'
        )
    state.default_transition_cfg = state.transition_defaults_value
    state.animation_defaults_value = state.config_defaults.get('animation', {})
    if not isinstance(state.animation_defaults_value, dict):
        raise ValueError(
            'animations.json defaults animation must be an object'
        )
    state.default_animation_cfg = state.animation_defaults_value
    state.animation_seed = json.dumps(
        {
            'animation': state.animation,
            'config': state.animation_config,
            'slides': [path.name for path in state.svg_files],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(',', ':'),
    )
    state.animation_rng = random.Random(state.animation_seed)
    state.conversion_trace: list[dict[str, Any]] | None = [] if state.conversion_trace_path else None
    state.structure_trace: list[dict[str, Any]] | None = (
        []
        if state.use_native_shapes and state.pptx_structure in {"baseline", "structured", "preserve"}
        else None
    )
