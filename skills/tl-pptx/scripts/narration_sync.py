"""narration_sync main responsibilities split from pinned upstream."""
from __future__ import annotations

from narration_sync_base import (
    ANIMATION_TIMING_OPTION_FIELDS as ANIMATION_TIMING_OPTION_FIELDS,
    AnimationBuildResult as AnimationBuildResult,
    AnimationGroupState as AnimationGroupState,
    Any as Any,
    DEFAULT_NARRATION_START_FLOOR as DEFAULT_NARRATION_START_FLOOR,
    DEFAULT_TRANSITION_DURATION as DEFAULT_TRANSITION_DURATION,
    ET as ET,
    NARRATION_EXTENSIONS as NARRATION_EXTENSIONS,
    Path as Path,
    PowerPointTiming as PowerPointTiming,
    SlideAnimationSettings as SlideAnimationSettings,
    SubtitleCue as SubtitleCue,
    SubtitleMergeResult as SubtitleMergeResult,
    TimingPlanEntry as TimingPlanEntry,
    VideoSlideTiming as VideoSlideTiming,
    VideoTimelineCalibration as VideoTimelineCalibration,
    _ALIGNMENT_COARSE_FACTOR as _ALIGNMENT_COARSE_FACTOR,
    _ALIGNMENT_END_TOLERANCE_MS as _ALIGNMENT_END_TOLERANCE_MS,
    _ALIGNMENT_FINE_HZ as _ALIGNMENT_FINE_HZ,
    _ALIGNMENT_MIN_CORRELATION as _ALIGNMENT_MIN_CORRELATION,
    _ALIGNMENT_REFINE_WINDOW_MS as _ALIGNMENT_REFINE_WINDOW_MS,
    _ALIGNMENT_SAMPLE_RATE as _ALIGNMENT_SAMPLE_RATE,
    _ALIGNMENT_SEARCH_WINDOW_MS as _ALIGNMENT_SEARCH_WINDOW_MS,
    _ALIGNMENT_TEMPLATE_MAX_MS as _ALIGNMENT_TEMPLATE_MAX_MS,
    _DOC_REL_NS as _DOC_REL_NS,
    _PML_NS as _PML_NS,
    _REL_NS as _REL_NS,
    _SCRIPTS_DIR as _SCRIPTS_DIR,
    _TIMING_RE as _TIMING_RE,
    animation_group_effect_entries as animation_group_effect_entries,
    animation_seconds_to_milliseconds as animation_seconds_to_milliseconds,
    argparse as argparse,
    configure_utf8_stdio as configure_utf8_stdio,
    copy as copy,
    dataclass as dataclass,
    hashlib as hashlib,
    json as json,
    math as math,
    narration_lead_in_seconds as narration_lead_in_seconds,
    normalize_animation_effect as normalize_animation_effect,
    normalize_animation_trigger as normalize_animation_trigger,
    normalize_transition_effect_request as normalize_transition_effect_request,
    os as os,
    posixpath as posixpath,
    probe_audio_duration as probe_audio_duration,
    re as re,
    read_narration_start_delay_xml as read_narration_start_delay_xml,
    read_slide_transition_xml as read_slide_transition_xml,
    scan_project_targets as scan_project_targets,
    scan_svg_targets as scan_svg_targets,
    shutil as shutil,
    sort_slide_paths as sort_slide_paths,
    subprocess as subprocess,
    sys as sys,
    tempfile as tempfile,
    validate_animation_config_errors as validate_animation_config_errors,
    validate_seconds as validate_seconds,
    validate_transition_config as validate_transition_config,
    zipfile as zipfile,
)
from narration_sync_helpers import (
    _timestamp_to_ms as _timestamp_to_ms,
    _ms_to_timestamp as _ms_to_timestamp,
    _parse_srt as _parse_srt,
    _format_srt as _format_srt,
    _atomic_write_text as _atomic_write_text,
    _project_path as _project_path,
    _project_input_path as _project_input_path,
    _require_replaceable as _require_replaceable,
    _reject_output_alias as _reject_output_alias,
    _subtitle_fingerprint as _subtitle_fingerprint,
    _finite_non_negative_seconds as _finite_non_negative_seconds,
    _load_timing_plan as _load_timing_plan,
    _load_canonical_animation_config as _load_canonical_animation_config,
    _page_subtitle_names as _page_subtitle_names,
    _animation_slide_names as _animation_slide_names,
    _animation_scope as _animation_scope,
    _transition_scope as _transition_scope,
    _effective_transition_duration_ms as _effective_transition_duration_ms,
    _effective_slide_animation as _effective_slide_animation,
    _animation_playback_duration_ms as _animation_playback_duration_ms,
    _active_group_effect_entries as _active_group_effect_entries,
    _group_is_animated as _group_is_animated,
    _active_group_order_requires_svg as _active_group_order_requires_svg,
    _needs_svg_group_resolution as _needs_svg_group_resolution,
    _resolve_animation_groups as _resolve_animation_groups,
    _find_audio as _find_audio,
    _seconds_from_ms as _seconds_from_ms,
)
from narration_sync_media import (
    presentation_slide_members as presentation_slide_members,
    _read_powerpoint_timings as _read_powerpoint_timings,
    _powerpoint_audio_starts as _powerpoint_audio_starts,
    _require_numpy as _require_numpy,
    _decode_audio_envelopes as _decode_audio_envelopes,
    _best_correlation as _best_correlation,
    _alignment_template_bounds as _alignment_template_bounds,
    _locate_audio_start as _locate_audio_start,
    _align_audio_starts_to_video as _align_audio_starts_to_video,
    calibrate_video_timeline as calibrate_video_timeline,
)


def rebuild_animations(
    project_path: Path,
    *,
    canonical_path: Path,
    plan_path: Path,
    subtitle_dir: Path,
    audio_dir: Path,
    output_path: Path,
    narration_padding: float,
    force: bool,
    narration_start_floor: float = DEFAULT_NARRATION_START_FLOOR,
) -> AnimationBuildResult:
    """Derive narration timing without modifying the canonical animation file."""
    narration_padding = _finite_non_negative_seconds(
        narration_padding,
        "Narration padding",
    )
    narration_start_floor = _finite_non_negative_seconds(
        narration_start_floor,
        "Narration start floor",
    )
    _reject_output_alias(
        output_path,
        [canonical_path, plan_path],
        label="Narration animation config",
    )
    canonical = _load_canonical_animation_config(canonical_path)
    slide_names = _animation_slide_names(project_path, canonical, subtitle_dir)
    subtitle_paths = [subtitle_dir / f"{slide_name}.srt" for slide_name in slide_names]
    _reject_output_alias(
        output_path,
        subtitle_paths,
        label="Narration animation config",
    )

    timing_plan: dict[str, list[TimingPlanEntry]] | None = None
    if plan_path.is_file():
        (
            expected_srt_sha256,
            planned_padding,
            planned_start_floor,
            loaded_plan,
        ) = _load_timing_plan(plan_path)
        if not math.isclose(
            narration_padding,
            planned_padding,
            rel_tol=0,
            abs_tol=1e-9,
        ):
            raise ValueError(
                "Narration padding differs from the timing plan: "
                f"plan={planned_padding}, command={narration_padding}"
            )
        if planned_start_floor is not None and not math.isclose(
            narration_start_floor,
            planned_start_floor,
            rel_tol=0,
            abs_tol=1e-9,
        ):
            raise ValueError(
                "Narration start floor differs from the timing plan: "
                f"plan={planned_start_floor}, command={narration_start_floor}"
            )
        current_srt_sha256 = _subtitle_fingerprint(slide_names, subtitle_dir)
        if current_srt_sha256 != expected_srt_sha256:
            raise ValueError(
                "Narration timing plan was authored for a different SRT set: "
                f"plan={expected_srt_sha256}, current={current_srt_sha256}"
            )
        if set(loaded_plan) != set(slide_names):
            raise ValueError(
                "Narration timing plan slides do not match animations.json"
            )
        timing_plan = loaded_plan

    derived = copy.deepcopy(canonical)
    canonical_slides = canonical.get("slides", {})
    derived_slides = derived.setdefault("slides", {})
    if not isinstance(canonical_slides, dict) or not isinstance(
        derived_slides,
        dict,
    ):
        raise ValueError('Canonical animations.json field "slides" must be an object')

    anchored_count = 0
    fallback_count = 0
    ignored_cue_count = 0
    svg_fallback_slide_count = 0
    drift_warnings: list[str] = []
    positional_slides: list[tuple[str, int, int]] = []
    audio_paths: list[Path] = []

    for slide_name in slide_names:
        cues = _parse_srt(subtitle_dir / f"{slide_name}.srt")
        audio_path = _find_audio(audio_dir, slide_name)
        audio_paths.append(audio_path)
        audio_duration = probe_audio_duration(audio_path)
        if audio_duration is None:
            raise RuntimeError(
                f"Unable to read narration duration with ffprobe: {audio_path}"
            )

        canonical_slide = canonical_slides.get(slide_name, {})
        if not isinstance(canonical_slide, dict):
            raise ValueError(
                f'Canonical animations.json slide "{slide_name}" must be an object'
            )
        derived_slide = derived_slides.setdefault(
            slide_name,
            copy.deepcopy(canonical_slide),
        )
        if not isinstance(derived_slide, dict):
            raise ValueError(
                f'Derived animation slide "{slide_name}" must be an object'
            )
        settings = _effective_slide_animation(canonical, canonical_slide)
        transition_duration_ms = _effective_transition_duration_ms(
            canonical,
            canonical_slide,
        )
        narration_lead_in_ms = round(
            narration_lead_in_seconds(
                transition_duration_ms / 1000,
                start_floor=narration_start_floor,
            )
            * 1000
        )
        plan_entries = timing_plan.get(slide_name) if timing_plan else None
        states, used_svg = _resolve_animation_groups(
            project_path,
            slide_name,
            canonical_slide,
            settings,
            plan_entries,
        )
        if used_svg:
            svg_fallback_slide_count += 1
        if timing_plan is None and states:
            positional_slides.append((
                slide_name,
                len({state.group_id for state in states}),
                len(cues),
            ))

        state_ids = {state.group_id for state in states}
        cue_by_group: dict[str, int | None] = {}
        if plan_entries is not None:
            for entry in plan_entries:
                if entry.group_id not in state_ids:
                    raise ValueError(
                        f'Narration timing plan references a non-animated group: '
                        f"{slide_name}/{entry.group_id}"
                    )
                if entry.cue_number is not None and entry.cue_number > len(cues):
                    raise ValueError(
                        f'Narration timing plan "{slide_name}/{entry.group_id}" '
                        f"references cue {entry.cue_number}, but the SRT has "
                        f"{len(cues)} cues"
                    )
                cue_by_group[entry.group_id] = entry.cue_number
        else:
            ordered_group_ids = list(
                dict.fromkeys(state.group_id for state in states)
            )
            cue_by_group = {
                group_id: index + 1 if index < len(cues) else None
                for index, group_id in enumerate(ordered_group_ids)
            }

        animation_value = derived_slide.setdefault("animation", {})
        if not isinstance(animation_value, dict):
            raise ValueError(
                f'Derived animation slide "{slide_name}" animation must be an object'
            )
        animation_value["trigger"] = "after-previous"
        groups_value = derived_slide.setdefault("groups", {})
        if not isinstance(groups_value, dict):
            raise ValueError(
                f'Derived animation slide "{slide_name}" groups must be an object'
            )

        # With-previous delays share a beat; after-previous waits for all rows.
        beat_start_ms = 0
        timeline_end_ms = 0
        referenced_cues: set[int] = set()
        seen_groups: set[str] = set()
        for state in states:
            first_group_effect = state.group_id not in seen_groups
            seen_groups.add(state.group_id)
            cue_number = (
                cue_by_group.get(state.group_id)
                if first_group_effect
                else None
            )
            if state.trigger == "after-previous":
                beat_start_ms = timeline_end_ms
            sequence_base_ms = beat_start_ms
            if cue_number is None:
                if first_group_effect:
                    fallback_count += 1
                delay_ms = state.original_delay_ms
                actual_start_ms = sequence_base_ms + delay_ms
            else:
                anchored_count += 1
                referenced_cues.add(cue_number)
                cue_start_ms = cues[cue_number - 1].start_ms
                desired_start_ms = narration_lead_in_ms + cue_start_ms
                actual_start_ms = max(desired_start_ms, sequence_base_ms)
                delay_ms = actual_start_ms - sequence_base_ms
                drift_ms = actual_start_ms - desired_start_ms
                if drift_ms > 500:
                    drift_warnings.append(
                        f"{slide_name}/{state.group_id}: cue {cue_number} "
                        f"starts at {_seconds_from_ms(cue_start_ms):.3f}s after "
                        f"a {_seconds_from_ms(narration_lead_in_ms):.3f}s lead-in; "
                        f"animation starts at {_seconds_from_ms(actual_start_ms):.3f}s "
                        f"({state.trigger} drift "
                        f"{_seconds_from_ms(drift_ms):.3f}s)"
                    )

            group_value = groups_value.setdefault(state.group_id, {})
            if not isinstance(group_value, dict):
                raise ValueError(
                    f'Derived animation group "{slide_name}/{state.group_id}" '
                    "must be an object"
                )
            if state.effect_index is None:
                effect_value = group_value
            else:
                group_path = (
                    f'slides[{json.dumps(slide_name, ensure_ascii=False)}]'
                    f'.groups[{json.dumps(state.group_id, ensure_ascii=False)}]'
                )
                derived_effect_entries = animation_group_effect_entries(
                    group_value,
                    path=group_path,
                )
                effect_value = derived_effect_entries[state.effect_index][1]
            effect_value["order"] = state.order
            effect_value["delay"] = _seconds_from_ms(delay_ms)
            effect_value["trigger"] = state.trigger
            timeline_end_ms = max(timeline_end_ms, actual_start_ms + state.duration_ms)

        ignored_cue_count += len(cues) - len(referenced_cues)

        advance_ms = round(
            (
                audio_duration
                + narration_padding
                + narration_lead_in_ms / 1000
            )
            * 1000
        )
        if timeline_end_ms > advance_ms:
            raise ValueError(
                f'Animations on slide "{slide_name}" end at '
                f"{_seconds_from_ms(timeline_end_ms):.3f}s, after the recorded "
                f"slide advance at {_seconds_from_ms(advance_ms):.3f}s"
            )

    _reject_output_alias(
        output_path,
        audio_paths,
        label="Narration animation config",
    )
    _require_replaceable(output_path, force)
    derived_errors = list(
        dict.fromkeys(
            [
                *validate_transition_config(derived),
                *validate_animation_config_errors(derived),
            ]
        )
    )
    if derived_errors:
        details = "\n".join(f"- {error}" for error in derived_errors)
        raise ValueError(f"Derived narration animation config is invalid:\n{details}")
    _atomic_write_text(
        output_path,
        json.dumps(derived, ensure_ascii=False, indent=2) + "\n",
    )

    for warning in drift_warnings:
        print(f"Warning: {warning}", file=sys.stderr)
    if positional_slides:
        print(
            "Warning: no narration_timing.json found — object reveals were mapped "
            "positionally (group N -> subtitle cue N). This mistimes any page whose "
            "narration is longer than its object count: later objects reveal early, "
            "while the narrator is still on an earlier point. Author "
            f"{plan_path} mapping each SVG group to the subtitle cue that speaks "
            "about it (omit a group's cue to keep its canonical delay), then re-run.",
            file=sys.stderr,
        )
        risky = [
            (name, group_count, cue_count)
            for name, group_count, cue_count in positional_slides
            if group_count > 1 and cue_count > group_count + 1
        ]
        for name, group_count, cue_count in risky:
            print(
                f"Warning:   {name}: {group_count} object(s) but {cue_count} "
                "subtitle cue(s) — later objects likely reveal too early",
                file=sys.stderr,
            )
    return AnimationBuildResult(
        slide_count=len(slide_names),
        group_count=anchored_count + fallback_count,
        anchored_count=anchored_count,
        fallback_count=fallback_count,
        ignored_cue_count=ignored_cue_count,
        svg_fallback_slide_count=svg_fallback_slide_count,
        timing_plan_written=False,
    )



def _merge_subtitles_result(
    project_path: Path,
    *,
    pptx_path: Path,
    subtitle_dir: Path,
    output_path: Path,
    force: bool,
    audio_dir: Path | None = None,
    video_path: Path | None = None,
) -> SubtitleMergeResult:
    """Merge local SRT files on the PPTX or exported-video timeline."""
    targets_by_slide, _anonymous_groups = scan_project_targets(project_path)
    slide_names = list(targets_by_slide)
    if not slide_names:
        raise ValueError(f"No SVG slides found under: {project_path / 'svg_output'}")

    local_subtitle_paths = [
        subtitle_dir / f"{slide_name}.srt"
        for slide_name in slide_names
    ]
    _reject_output_alias(
        output_path,
        [pptx_path, *local_subtitle_paths, *([video_path] if video_path else [])],
        label="Merged subtitle",
    )
    _require_replaceable(output_path, force)
    timings = _read_powerpoint_timings(pptx_path, len(slide_names))
    theoretical_starts, timeline_ms = _powerpoint_audio_starts(timings)
    local_cues = {
        slide_name: _parse_srt(subtitle_dir / f"{slide_name}.srt")
        for slide_name in slide_names
    }
    video_adjustments: list[int] = []
    correlations: list[float] = []

    if video_path is None:
        audio_starts = theoretical_starts
    else:
        resolved_audio_dir = audio_dir or project_path / "audio"
        calibration = calibrate_video_timeline(
            slide_names=slide_names,
            pptx_path=pptx_path,
            audio_dir=resolved_audio_dir,
            video_path=video_path,
            subtitle_dir=subtitle_dir,
        )
        if calibration.powerpoint_timeline_ms != timeline_ms:
            raise ValueError("Video calibration and subtitle timelines differ")
        audio_starts = [
            slide.video_narration_start_ms
            for slide in calibration.slides
        ]
        correlations = [slide.correlation for slide in calibration.slides]
        audio_paths = [slide.audio_path for slide in calibration.slides]
        _reject_output_alias(
            output_path,
            audio_paths,
            label="Merged subtitle",
        )
        video_adjustments = [
            actual - theoretical
            for actual, theoretical in zip(audio_starts, theoretical_starts)
        ]

    merged_cues: list[SubtitleCue] = []

    for slide_name, audio_start_ms, timing in zip(
        slide_names,
        audio_starts,
        timings,
    ):
        slide_cues = local_cues[slide_name]
        narration_window_ms = timing.advance_ms - timing.narration_delay_ms
        if slide_cues[-1].end_ms > narration_window_ms:
            raise ValueError(
                f"{slide_name}.srt ends at "
                f"{_seconds_from_ms(slide_cues[-1].end_ms):.3f}s, after the "
                "available narration window before PowerPoint advances at "
                f"{_seconds_from_ms(narration_window_ms):.3f}s"
            )
        for cue in slide_cues:
            merged_cue = SubtitleCue(
                cue.start_ms + audio_start_ms,
                cue.end_ms + audio_start_ms,
                cue.text,
            )
            if merged_cues and merged_cue.start_ms < merged_cues[-1].end_ms:
                raise ValueError(
                    f"Video-calibrated subtitle overlap before slide {slide_name}"
                )
            merged_cues.append(merged_cue)

    _atomic_write_text(output_path, _format_srt(merged_cues))
    return SubtitleMergeResult(
        slide_count=len(slide_names),
        cue_count=len(merged_cues),
        powerpoint_timeline_ms=timeline_ms,
        minimum_video_adjustment_ms=(
            min(video_adjustments) if video_adjustments else None
        ),
        maximum_video_adjustment_ms=(
            max(video_adjustments) if video_adjustments else None
        ),
        minimum_video_correlation=(
            min(correlations) if correlations else None
        ),
    )



def merge_subtitles(
    project_path: Path,
    *,
    pptx_path: Path,
    subtitle_dir: Path,
    output_path: Path,
    force: bool,
) -> tuple[int, int, int]:
    """Merge local SRT files using timing values read from the final PPTX."""
    result = _merge_subtitles_result(
        project_path,
        pptx_path=pptx_path,
        subtitle_dir=subtitle_dir,
        output_path=output_path,
        force=force,
    )
    return (
        result.slide_count,
        result.cue_count,
        result.powerpoint_timeline_ms,
    )



def merge_subtitles_to_video(
    project_path: Path,
    *,
    pptx_path: Path,
    subtitle_dir: Path,
    audio_dir: Path,
    video_path: Path,
    output_path: Path,
    force: bool,
) -> SubtitleMergeResult:
    """Merge local SRT files after calibrating page starts to an exported video."""
    return _merge_subtitles_result(
        project_path,
        pptx_path=pptx_path,
        subtitle_dir=subtitle_dir,
        output_path=output_path,
        force=force,
        audio_dir=audio_dir,
        video_path=video_path,
    )



def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Derive narrated object animation timings and merge page-local "
            "SRT files on PowerPoint's final timeline."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    fingerprint = subparsers.add_parser(
        "fingerprint",
        help="print the SHA-256 used to bind a timing plan to page-local SRT files",
    )
    fingerprint.add_argument("project_path", help="Project directory")
    fingerprint.add_argument(
        "--subtitle-dir",
        default=None,
        help="Page-local SRT directory; default: <project>/audio",
    )

    animations = subparsers.add_parser(
        "animations",
        help="derive narration_animations.json from animations.json and page-local SRT",
    )
    animations.add_argument("project_path", help="Project directory")
    animations.add_argument(
        "--animation-config",
        default=None,
        help="Read-only canonical config; default: <project>/animations.json",
    )
    animations.add_argument(
        "--plan",
        default=None,
        help="Timing plan; default: <project>/narration_timing.json",
    )
    animations.add_argument(
        "--subtitle-dir",
        default=None,
        help="Page-local SRT directory; default: <project>/audio",
    )
    animations.add_argument(
        "--audio-dir",
        default=None,
        help="Narration audio directory; default: <project>/audio",
    )
    animations.add_argument(
        "-o",
        "--output",
        default=None,
        help=(
            "Narration animation output; "
            "default: <project>/narration_animations.json"
        ),
    )
    animations.add_argument(
        "--narration-padding",
        type=float,
        default=0.5,
        help="Seconds added after each narration before slide advance (default: 0.5)",
    )
    animations.add_argument(
        "--narration-start-floor",
        type=float,
        default=DEFAULT_NARRATION_START_FLOOR,
        help=(
            "Minimum seconds from transition start to narration start; "
            "0 waits only for transition completion "
            f"(default: {DEFAULT_NARRATION_START_FLOOR:g})"
        ),
    )
    animations.add_argument(
        "--force",
        action="store_true",
        help="Replace an existing narration_animations.json",
    )

    subtitles = subparsers.add_parser(
        "subtitles",
        help="merge page-local SRT using timings read from a narrated PPTX",
    )
    subtitles.add_argument("project_path", help="Project directory")
    subtitles.add_argument(
        "--pptx",
        required=True,
        help=(
            "Final narrated PPTX whose recorded timing values define the "
            "timeline; relative paths are resolved under the project"
        ),
    )
    subtitles.add_argument(
        "--subtitle-dir",
        default=None,
        help="Page-local SRT directory; default: <project>/audio",
    )
    subtitles.add_argument(
        "--video",
        default=None,
        help=(
            "PowerPoint-exported video whose audio track calibrates page starts; "
            "relative paths are resolved under the project"
        ),
    )
    subtitles.add_argument(
        "--audio-dir",
        default=None,
        help=(
            "Page-local narration audio used with --video; "
            "default: <project>/audio"
        ),
    )
    subtitles.add_argument(
        "-o",
        "--output",
        default=None,
        help="Merged SRT output; default: <project>/audio/total.srt",
    )
    subtitles.add_argument(
        "--force",
        action="store_true",
        help="Replace an existing merged SRT",
    )
    return parser



def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    project_path = Path(args.project_path).resolve()
    if not project_path.is_dir():
        parser.error(f"Project path does not exist: {project_path}")

    try:
        if args.command == "fingerprint":
            subtitle_dir = _project_path(
                project_path,
                args.subtitle_dir,
                Path("audio"),
            )
            slide_names = _page_subtitle_names(subtitle_dir)
            for slide_name in slide_names:
                _parse_srt(subtitle_dir / f"{slide_name}.srt")
            print(_subtitle_fingerprint(slide_names, subtitle_dir))
            return 0

        if args.command == "animations":
            canonical_path = _project_path(
                project_path,
                args.animation_config,
                Path("animations.json"),
            )
            plan_path = _project_path(
                project_path,
                args.plan,
                Path("narration_timing.json"),
            )
            subtitle_dir = _project_path(
                project_path,
                args.subtitle_dir,
                Path("audio"),
            )
            audio_dir = _project_path(
                project_path,
                args.audio_dir,
                Path("audio"),
            )
            output_path = _project_path(
                project_path,
                args.output,
                Path("narration_animations.json"),
            )
            result = rebuild_animations(
                project_path,
                canonical_path=canonical_path,
                plan_path=plan_path,
                subtitle_dir=subtitle_dir,
                audio_dir=audio_dir,
                output_path=output_path,
                narration_padding=args.narration_padding,
                narration_start_floor=args.narration_start_floor,
                force=args.force,
            )
            print(f"Narration animation config written: {output_path}")
            print(
                f"Slides: {result.slide_count}; groups: {result.group_count}; "
                f"SRT-anchored: {result.anchored_count}; "
                f"canonical-delay fallback: {result.fallback_count}; "
                f"SVG fallback slides: {result.svg_fallback_slide_count}; "
                f"unused cues: {result.ignored_cue_count}"
            )
            return 0

        pptx_path = _project_input_path(project_path, args.pptx).resolve()
        subtitle_dir = _project_path(
            project_path,
            args.subtitle_dir,
            Path("audio"),
        )
        audio_dir = _project_path(
            project_path,
            args.audio_dir,
            Path("audio"),
        )
        video_path = (
            _project_input_path(project_path, args.video).resolve()
            if args.video
            else None
        )
        output_path = _project_path(
            project_path,
            args.output,
            Path("audio/total.srt"),
        )
        result = _merge_subtitles_result(
            project_path,
            pptx_path=pptx_path,
            subtitle_dir=subtitle_dir,
            audio_dir=audio_dir,
            video_path=video_path,
            output_path=output_path,
            force=args.force,
        )
        print(f"Merged subtitle written: {output_path}")
        print(
            f"Slides: {result.slide_count}; cues: {result.cue_count}; "
            "PowerPoint timeline: "
            f"{_seconds_from_ms(result.powerpoint_timeline_ms):.3f}s"
        )
        if result.minimum_video_correlation is not None:
            print(
                "Exported-video calibration: page adjustment "
                f"{_seconds_from_ms(result.minimum_video_adjustment_ms or 0):+.3f}s "
                "to "
                f"{_seconds_from_ms(result.maximum_video_adjustment_ms or 0):+.3f}s; "
                f"minimum correlation: {result.minimum_video_correlation:.3f}"
            )
        return 0
    except (
        ET.ParseError,
        OSError,
        OverflowError,
        ValueError,
        RuntimeError,
        zipfile.BadZipFile,
    ) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1



if __name__ == "__main__":
    raise SystemExit(main())
