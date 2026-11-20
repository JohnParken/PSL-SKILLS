"""Native PPTX export phase; retains upstream control flow."""
# Adapted from hugohe3/ppt-master 7e0df6705de0cd24345210d31f07fcb5a32b6900.
# MIT; Copyright (c) 2025-2026 Hugo He. See references/LICENSE-PPT-MASTER.txt and references/upstream.md.

from __future__ import annotations
from dataclasses import dataclass, fields
import shutil
from pathlib import (
    Path,
)
from typing import Any
from pptx_embedded_fonts import (
    EmbeddedFontBundle,
)
from pptx_transitions import (
    MorphPairExpectation,
    NATIVE_TRANSITIONS,
    normalize_transition_effect_request,
)
from pptx_workspace import WorkspaceResourceSpec
from language_tags import (
    office_language_tag,
)
from ..animation_config import (
    MorphPair,
    resolve_morph_pairs,
)
from ..drawingml.context import resolve_text_flow
from ..drawingml.theme_colors import (
    ThemeColorError,
    ThemeColorSpec,
)
from ..drawingml.theme_fonts import (
    MasterTextStyleSpec,
    ThemeFontSpec,
)
from .dimensions import (
    CANVAS_FORMATS,
    resolve_svg_canvas,
)
from .media import (
    PNG_RENDERER,
    get_png_renderer_info,
)
from .narration import (
    DEFAULT_NARRATION_START_FLOOR,
)
from .template_structure import (
    NativeStructureContract,
    TemplateSlideSpec,
    TemplateStructureError,
    flat_structure_metadata_errors,
    parse_preserve_slides,
    parse_template_slides,
)

from .builder import (
    RoundtripSlidePatch,
    _create_writable_work_dir,
)

from .builder_state import BuildState

from .builder_export_base_package import export_base_package
from .builder_export_slides import export_slides
from .builder_export_structure import export_structure
from .builder_export_finalize import export_finalize

@dataclass(frozen=True)
class NativeBuildOptions:
    """Bundled optional settings for create_pptx_with_native_svg."""

    canvas_format: str | None = None
    verbose: bool = True
    transition: str | None = 'fade'
    transition_duration: float = 0.5
    auto_advance: float | None = None
    kiosk: bool = False
    use_compat_mode: bool = True
    notes: dict[str, str] | None = None
    enable_notes: bool = True
    use_native_shapes: bool = True
    animation: str | None = None
    animation_duration: float = 0.4
    animation_stagger: float = 0.5
    animation_trigger: str = 'after-previous'
    animation_config: dict[str, Any] | None = None
    animation_cli_overrides: dict[str, bool] | None = None
    narration_audio: dict[str, Path] | None = None
    use_narration_timings: bool = False
    narration_padding: float = 0.5
    cache_dir: Path | None = None
    workers: int | None = None
    merge_paragraphs: bool | None = None
    image_optimize: bool = True
    image_max_dimension: int | None = 2560
    image_sizing: str = 'cap'
    image_scale: float = 2.0
    image_quality: int = 85
    native_objects: bool = False
    conversion_trace_path: Path | None = None
    dangerous_nonconforming_export: bool = False
    doc_metadata: dict[str, Any] | None = None
    structure_name: str | None = None
    pptx_structure: str = 'structured'
    use_layout_placeholder_frames: bool = False
    native_structure_contract: NativeStructureContract | None = None
    roundtrip_passthrough_slides: set[int] | None = None
    roundtrip_slide_patches: dict[int, RoundtripSlidePatch] | None = None
    roundtrip_resources: tuple[WorkspaceResourceSpec, ...] = ()
    roundtrip_page_sources: tuple[int, ...] | None = None
    theme_font_spec: ThemeFontSpec | None = None
    master_text_style_spec: MasterTextStyleSpec | None = None
    theme_color_spec: ThemeColorSpec | None = None
    source_theme_xml: bytes | None = None
    source_theme_xml_by_master: dict[str, bytes] | None = None
    source_embedded_fonts: EmbeddedFontBundle | None = None
    structured_baseline: bool = False
    baseline_layout_specs: list[TemplateSlideSpec] | None = None
    layout_definition_files: list[Path] | None = None
    expected_viewbox: str | None = None
    animation_resource_root: Path | None = None
    transition_effect_options: dict[str, object] | None = None
    transition_sound: str | None = None
    text_flow: str | None = None
    primary_language: str | None = None
    narration_start_floor: float = DEFAULT_NARRATION_START_FLOOR


def _init_build_state(
    svg_files: list[Path],
    output_path: Path,
    resource_root: Path,
    opts: NativeBuildOptions,
) -> BuildState:
    if opts.narration_audio or opts.use_narration_timings:
        raise ValueError('Narration audio and timing generation have been removed from tl-pptx')
    state = BuildState()
    state.svg_files = svg_files
    state.output_path = output_path
    state.resource_root = resource_root
    for item in fields(NativeBuildOptions):
        setattr(state, item.name, getattr(opts, item.name))
    if state.source_theme_xml is not None and state.source_theme_xml_by_master is not None:
        raise ThemeColorError("Use either one diagnostic source Theme or per-Master mirror Themes")
    if state.source_theme_xml_by_master is not None and state.pptx_structure != "structured":
        raise ThemeColorError("Per-Master source Themes are allowed only in structured export")
    state.text_flow = resolve_text_flow(state.text_flow, state.merge_paragraphs)
    if state.primary_language is not None:
        state.primary_language = office_language_tag(state.primary_language)
    state.public_svg_files = list(state.svg_files)
    return state


def _configure_roundtrip_state(state: BuildState) -> None:
    state.passthrough_slides = set(state.roundtrip_passthrough_slides or set())
    state.slide_patches = dict(state.roundtrip_slide_patches or {})
    state.overlay_slides = set(state.slide_patches)
    state.page_plan_export = state.roundtrip_page_sources is not None
    state.roundtrip_export = bool(
        state.passthrough_slides
        or state.overlay_slides
        or state.roundtrip_resources
        or state.page_plan_export
    )
    state.source_motion_slides = state.passthrough_slides | {
        index for index, patch in state.slide_patches.items() if not patch.animation_changed
    }
    state.invalid_passthrough = sorted(
        index for index in state.passthrough_slides if index < 1 or index > len(state.public_svg_files)
    )
    if state.invalid_passthrough:
        raise ValueError(
            "Round-trip passthrough slide indices are outside the SVG roster: "
            + ", ".join(str(index) for index in state.invalid_passthrough)
        )
    state.invalid_overlays = sorted(
        index for index in state.overlay_slides if index < 1 or index > len(state.public_svg_files)
    )
    if state.invalid_overlays:
        raise ValueError(
            "Round-trip overlay slide indices are outside the SVG roster: "
            + ", ".join(str(index) for index in state.invalid_overlays)
        )
    state.overlap = sorted(state.passthrough_slides & state.overlay_slides)
    if state.overlap:
        raise ValueError(
            "Round-trip slides cannot be both passthrough and overlay: "
            + ", ".join(str(index) for index in state.overlap)
        )
    if state.passthrough_slides and state.pptx_structure != "preserve":
        raise ValueError("Round-trip slide passthrough is available only in preserve mode")
    if (state.overlay_slides or state.roundtrip_resources) and state.pptx_structure != "preserve":
        raise ValueError("Round-trip overlays and resource reinjection require preserve mode")
    if state.page_plan_export:
        if state.pptx_structure != "preserve":
            raise ValueError("Round-trip page plans are available only in preserve mode")
        if len(state.roundtrip_page_sources) != len(state.public_svg_files):
            raise ValueError("Round-trip page-plan source roster differs from the SVG roster")


def _configure_morph_and_definitions(state: BuildState) -> None:
    state.definition_svg_files = list(state.layout_definition_files or [])
    state.public_slide_names = [path.stem for path in state.public_svg_files]
    state.morph_pairs = resolve_morph_pairs(state.public_slide_names, state.animation_config)
    state.public_slide_numbers = {
        slide_name: slide_number for slide_number, slide_name in enumerate(state.public_slide_names, 1)
    }
    state.morph_expectations = tuple(
        MorphPairExpectation(
            source_slide_number=state.public_slide_numbers[pair.source_slide],
            destination_slide_number=state.public_slide_numbers[pair.destination_slide],
            key=pair.key,
        )
        for pair in state.morph_pairs
    )
    state.morph_pairs_by_destination: dict[str, list[MorphPair]] = {}
    state.morph_group_overrides_by_slide: dict[str, set[str]] = {}
    for state.pair in state.morph_pairs:
        state.morph_pairs_by_destination.setdefault(state.pair.destination_slide, []).append(state.pair)
        state.morph_group_overrides_by_slide.setdefault(state.pair.source_slide, set()).add(
            state.pair.source_group_id
        )
        state.morph_group_overrides_by_slide.setdefault(state.pair.destination_slide, set()).add(
            state.pair.destination_group_id
        )
    state.morph_shape_ids: dict[tuple[str, str], int] = {}
    state.morph_preserved_slides: set[int] = set()
    if state.definition_svg_files and state.pptx_structure != "structured":
        raise ValueError("unselected Layout prototypes require pptx_structure='structured'")
    state.public_paths = {path.resolve() for path in state.public_svg_files}
    state.seen_definition_paths: set[Path] = set()
    for state.path in state.definition_svg_files:
        state.resolved = state.path.resolve()
        if not state.path.is_file():
            raise ValueError(f"Layout prototype SVG does not exist: {state.path}")
        if state.resolved in state.public_paths:
            raise ValueError(f"Layout prototype SVG is already a generated page: {state.path}")
        if state.resolved in state.seen_definition_paths:
            raise ValueError(f"Layout prototype SVG is repeated: {state.path}")
        state.seen_definition_paths.add(state.resolved)
    state.public_slide_count = len(state.public_svg_files)
    state.svg_files = state.public_svg_files + state.definition_svg_files
    state.total_slide_count = len(state.svg_files)


def _validate_structure_and_canvas(state: BuildState) -> bool:
    if not state.use_native_shapes:
        raise ValueError(
            "SVG-image PPTX export is no longer supported; use svg_final/ "
            "directly for preview and native DrawingML PPTX for delivery"
        )
    if not state.public_svg_files:
        print("Error: No SVG files found")
        return False

    state.use_compat_mode = False
    if state.pptx_structure not in {"baseline", "structured", "preserve", "flat"}:
        raise ValueError(f"Unsupported pptx_structure: {state.pptx_structure}")
    state.requested_canvas_format = state.canvas_format
    state.canvas, state.detected_canvas_format = resolve_svg_canvas(
        state.svg_files, canvas_format=state.canvas_format, expected_viewbox=state.expected_viewbox,
    )
    if state.canvas_format is None:
        state.canvas_format = state.detected_canvas_format
    if state.pptx_structure == "flat":
        state.flat_errors = flat_structure_metadata_errors(state.public_svg_files)
        if state.flat_errors:
            state.details = "\n".join(f"  - {error}" for error in state.flat_errors)
            raise TemplateStructureError("Flat PPTX structure validation failed:\n" + state.details)
    if state.use_layout_placeholder_frames and state.pptx_structure != "structured":
        raise ValueError("use_layout_placeholder_frames requires pptx_structure='structured'")
    if state.structured_baseline:
        raise ValueError("structured_baseline is obsolete; use pptx_structure='structured'")
    if state.baseline_layout_specs is not None:
        raise ValueError("baseline_layout_specs is obsolete; structured export parses SVG metadata")
    if state.pptx_structure == "structured" and state.master_text_style_spec is None:
        raise ValueError(
            "Structured export requires declared or inferred typography "
            "title/body anchors in master_text_style_spec"
        )
    if state.use_native_shapes and state.pptx_structure == "structured":
        state.template_specs = parse_template_slides(state.svg_files)
        state.public_template_specs = state.template_specs[:state.public_slide_count]
    elif state.use_native_shapes and state.pptx_structure == "preserve":
        if state.native_structure_contract is None:
            raise TemplateStructureError("Preserve export requires a validated native structure contract")
        state.template_specs = parse_preserve_slides(state.svg_files)
        for state.spec in state.template_specs:
            state.native_structure_contract.layout(state.spec.layout_key)
        state.public_template_specs = state.template_specs
    else:
        state.template_specs = None
        state.public_template_specs = None
    state.template_background_expectations: dict[str, str | None] | None = None
    state.template_shape_roster_expectations: dict[str, tuple[str, ...]] | None = None
    state.template_layout_parts_by_key: dict[str, str] | None = None
    state.template_master_parts_by_key: dict[str, str] | None = None
    if state.template_specs is not None and not state.native_objects:
        state.native_placeholders = sorted({
            item.placeholder
            for spec in state.template_specs
            for item in spec.placeholders
            if item.placeholder in {"chart", "table"}
        })
        if state.native_placeholders:
            state.kinds = ", ".join(str(kind) for kind in state.native_placeholders)
            state.context = state.pptx_structure.capitalize()
            raise TemplateStructureError(
                f"{state.context} {state.kinds} placeholder(s) require "
                "--native-charts-and-tables so each marker becomes one native "
                "PowerPoint Chart/Table object"
            )
    state.renderer_name, state.renderer_status, state.renderer_hint = get_png_renderer_info()
    if not state.use_native_shapes and state.use_compat_mode and PNG_RENDERER is None:
        raise ValueError(
            "SVG PNG compatibility mode is unavailable in the declared dependency set; "
            "no silent fallback was performed"
        )
    state.width_emu, state.height_emu = state.canvas.emu_dimensions
    state.pixel_width, state.pixel_height = state.canvas.pixel_dimensions
    state.pixel_width_label, state.pixel_height_label = state.canvas.canonical.split()[2:]
    return True


def _log_build_banner(state: BuildState) -> None:
    if not state.verbose:
        return
    if state.requested_canvas_format is None:
        if state.canvas_format:
            state.format_name = CANVAS_FORMATS.get(state.canvas_format, {}).get('name', state.canvas_format)
            print(f"  Detected canvas format: {state.format_name}")
        else:
            print(f"  Using SVG viewBox dimensions: {state.canvas.canonical.removeprefix('0 0 ')} px")
    print(f"  Slide dimensions: {state.pixel_width_label} x {state.pixel_height_label} px")
    print(f"  SVG file count: {state.public_slide_count}")
    if state.definition_svg_files:
        print(f"  Unselected Layout prototype carriers: {len(state.definition_svg_files)}")
    if state.use_native_shapes:
        print("  Mode: Native DrawingML shapes (directly editable)")
        state.native_object_mode = (
            "Chart/Table replacement enabled" if state.native_objects else "Chart/Table replacement disabled"
        )
        print(f"  Native table/chart objects: {state.native_object_mode}")
        print(f"  PPTX structure: {state.pptx_structure}")
        if state.image_optimize:
            if state.image_sizing == 'display':
                state.image_mode = (
                    f"display scale {state.image_scale:g}, preferred max {state.image_max_dimension or 'unlimited'} px"
                )
            else:
                state.image_mode = f"preferred cap {state.image_max_dimension or 'unlimited'} px, unchanged bytes preserved"
            print(f"  Image optimization: Enabled ({state.image_mode}, JPEG q{state.image_quality} when re-encoded)")
        else:
            print("  Image optimization: Disabled (original bytes)")
    elif state.use_compat_mode:
        print("  Compatibility mode: Enabled (PNG + SVG dual format)")
        print(f"  PNG renderer: {state.renderer_name} {state.renderer_status}")
    else:
        print("  Compatibility mode: Disabled (pure SVG)")
    if state.transition:
        state.canonical_transition, state._transition_options = normalize_transition_effect_request(
            state.transition, state.transition_effect_options,
        )
        state.trans_name = (
            NATIVE_TRANSITIONS.get(state.canonical_transition, {}).get('name', state.canonical_transition)
            if state.canonical_transition else state.transition
        )
        print(f"  Transition effect: {state.trans_name}")
    if not state.roundtrip_export:
        if state.enable_notes and state.notes:
            print(f"  Speaker notes: {len(state.notes)} page(s)")
        elif state.enable_notes:
            print("  Speaker notes: Enabled (no notes files found)")
        else:
            print("  Speaker notes: Disabled")
    print()


def create_pptx_with_native_svg(
    svg_files: list[Path],
    output_path: Path,
    *,
    resource_root: Path,
    options: NativeBuildOptions | None = None,
    **legacy_options: Any,
) -> bool:
    """Create a PPTX file with native DrawingML shapes from bundled build options."""
    opts = options if options is not None else NativeBuildOptions(**legacy_options)
    state = _init_build_state(svg_files, output_path, resource_root, opts)
    _configure_roundtrip_state(state)
    _configure_morph_and_definitions(state)
    if not _validate_structure_and_canvas(state):
        return False
    _log_build_banner(state)
    state.animation_cli_overrides = state.animation_cli_overrides or {}
    state.temp_dir = _create_writable_work_dir(state.output_path)
    try:
        export_base_package(state)
        export_slides(state)
        export_structure(state)
        return export_finalize(state)
    finally:
        shutil.rmtree(state.temp_dir, ignore_errors=True)
