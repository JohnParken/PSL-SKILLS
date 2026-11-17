"""narration_sync helpers responsibilities split from pinned upstream."""
from __future__ import annotations

from narration_sync_base import (
    ANIMATION_TIMING_OPTION_FIELDS as ANIMATION_TIMING_OPTION_FIELDS,
    AnimationGroupState as AnimationGroupState,
    Any as Any,
    DEFAULT_TRANSITION_DURATION as DEFAULT_TRANSITION_DURATION,
    NARRATION_EXTENSIONS as NARRATION_EXTENSIONS,
    Path as Path,
    SlideAnimationSettings as SlideAnimationSettings,
    SubtitleCue as SubtitleCue,
    TimingPlanEntry as TimingPlanEntry,
    _TIMING_RE as _TIMING_RE,
    animation_group_effect_entries as animation_group_effect_entries,
    animation_seconds_to_milliseconds as animation_seconds_to_milliseconds,
    hashlib as hashlib,
    json as json,
    math as math,
    normalize_animation_effect as normalize_animation_effect,
    normalize_animation_trigger as normalize_animation_trigger,
    normalize_transition_effect_request as normalize_transition_effect_request,
    os as os,
    re as re,
    scan_svg_targets as scan_svg_targets,
    sort_slide_paths as sort_slide_paths,
    sys as sys,
    tempfile as tempfile,
    validate_animation_config_errors as validate_animation_config_errors,
    validate_seconds as validate_seconds,
    validate_transition_config as validate_transition_config,
)


def _timestamp_to_ms(value: str) -> int:
    hours_text, minutes_text, remainder = value.split(":")
    seconds_text, milliseconds_text = remainder.split(",")
    hours = int(hours_text)
    minutes = int(minutes_text)
    seconds = int(seconds_text)
    milliseconds = int(milliseconds_text)
    if minutes >= 60 or seconds >= 60:
        raise ValueError(f"Invalid SRT timestamp: {value}")
    return (((hours * 60) + minutes) * 60 + seconds) * 1000 + milliseconds



def _ms_to_timestamp(value: int) -> str:
    if value < 0:
        raise ValueError(f"SRT timestamp cannot be negative: {value}")
    hours, remainder = divmod(value, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"



def _parse_srt(path: Path) -> list[SubtitleCue]:
    text = path.read_text(encoding="utf-8-sig")
    blocks = re.split(r"\r?\n\s*\r?\n", text.strip())
    cues: list[SubtitleCue] = []
    previous_end = -1

    for block_number, block in enumerate(blocks, 1):
        lines = block.splitlines()
        if len(lines) < 3:
            raise ValueError(f"{path}: malformed SRT block {block_number}")
        try:
            cue_number = int(lines[0].strip())
        except ValueError as exc:
            raise ValueError(
                f"{path}: invalid cue number in block {block_number}"
            ) from exc
        if cue_number != block_number:
            raise ValueError(
                f"{path}: cue numbers must be consecutive from 1; "
                f"block {block_number} is numbered {cue_number}"
            )
        timing_match = _TIMING_RE.match(lines[1].strip())
        if timing_match is None:
            raise ValueError(
                f"{path}: invalid cue timing in block {block_number}"
            )
        start_ms = _timestamp_to_ms(timing_match.group("start"))
        end_ms = _timestamp_to_ms(timing_match.group("end"))
        cue_text = "\n".join(lines[2:]).strip()
        if not cue_text:
            raise ValueError(f"{path}: empty cue text in block {block_number}")
        if end_ms <= start_ms:
            raise ValueError(
                f"{path}: cue {block_number} must end after it starts"
            )
        if start_ms < previous_end:
            raise ValueError(
                f"{path}: cue {block_number} overlaps the preceding cue"
            )
        cues.append(SubtitleCue(start_ms, end_ms, cue_text))
        previous_end = end_ms

    if not cues:
        raise ValueError(f"No subtitle cues found: {path}")
    return cues



def _format_srt(cues: list[SubtitleCue]) -> str:
    blocks = []
    for index, cue in enumerate(cues, 1):
        blocks.append(
            f"{index}\n"
            f"{_ms_to_timestamp(cue.start_ms)} --> "
            f"{_ms_to_timestamp(cue.end_ms)}\n"
            f"{cue.text}"
        )
    return "\n\n".join(blocks) + "\n"



def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    temporary_path = Path(temporary_name)
    try:
        stream = os.fdopen(descriptor, "w", encoding="utf-8", newline="\n")
        descriptor = -1
        with stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temporary_path.unlink(missing_ok=True)



def _project_path(project_path: Path, value: str | None, default: Path) -> Path:
    path = Path(value) if value else default
    return path if path.is_absolute() else project_path / path



def _project_input_path(project_path: Path, value: str) -> Path:
    """Resolve an input path: absolute, or existing as given, else project-relative."""
    path = Path(value)
    if path.is_absolute() or path.exists():
        return path
    return project_path / path



def _require_replaceable(path: Path, force: bool) -> None:
    if path.exists() and not force:
        raise FileExistsError(f"Output already exists: {path}; pass --force to replace it")



def _reject_output_alias(
    output_path: Path,
    input_paths: list[Path],
    *,
    label: str,
) -> None:
    output_resolved = output_path.resolve()
    for input_path in input_paths:
        if output_resolved == input_path.resolve():
            raise ValueError(
                f"{label} output must not overwrite an input file: {output_path}"
            )



def _subtitle_fingerprint(slide_names: list[str], subtitle_dir: Path) -> str:
    digest = hashlib.sha256()
    for slide_name in slide_names:
        path = subtitle_dir / f"{slide_name}.srt"
        digest.update(slide_name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()



def _finite_non_negative_seconds(value: object, field: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or value < 0
    ):
        raise ValueError(f'{field} must be a finite non-negative number')
    return float(value)



def _load_timing_plan(
    path: Path,
) -> tuple[str, float, float | None, dict[str, list[TimingPlanEntry]]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Narration timing plan must be a JSON object: {path}")
    unknown_top = set(raw) - {
        "version",
        "srt_sha256",
        "narration_padding",
        "narration_start_floor",
        "slides",
    }
    if unknown_top:
        raise ValueError(
            f"Narration timing plan has unknown top-level field(s): "
            f"{', '.join(sorted(unknown_top))}"
        )
    if raw.get("version") != 1:
        raise ValueError(
            f"Unsupported narration timing plan version: {raw.get('version')!r}"
        )
    srt_sha256 = raw.get("srt_sha256")
    if (
        not isinstance(srt_sha256, str)
        or re.fullmatch(r"[0-9a-f]{64}", srt_sha256) is None
    ):
        raise ValueError(
            'Narration timing plan field "srt_sha256" must be a lowercase '
            "SHA-256 digest of the ordered page-local SRT files"
        )
    narration_padding = _finite_non_negative_seconds(
        raw.get("narration_padding"),
        'Narration timing plan field "narration_padding"',
    )
    narration_start_floor = (
        _finite_non_negative_seconds(
            raw["narration_start_floor"],
            'Narration timing plan field "narration_start_floor"',
        )
        if "narration_start_floor" in raw
        else None
    )
    slides = raw.get("slides")
    if not isinstance(slides, dict):
        raise ValueError('Narration timing plan field "slides" must be an object')

    result: dict[str, list[TimingPlanEntry]] = {}
    for slide_name, slide_raw in slides.items():
        if not isinstance(slide_name, str) or not slide_name:
            raise ValueError("Narration timing plan slide names must be non-empty strings")
        if not isinstance(slide_raw, dict) or set(slide_raw) != {"groups"}:
            raise ValueError(
                f'Narration timing plan slide "{slide_name}" must contain only "groups"'
            )
        groups = slide_raw["groups"]
        if not isinstance(groups, list):
            raise ValueError(
                f'Narration timing plan slide "{slide_name}" groups must be a list'
            )

        entries: list[TimingPlanEntry] = []
        seen_groups: set[str] = set()
        for position, entry_raw in enumerate(groups, 1):
            if not isinstance(entry_raw, dict):
                raise ValueError(
                    f'Narration timing plan "{slide_name}" group #{position} '
                    "must be an object"
                )
            unknown_fields = set(entry_raw) - {"id", "cue"}
            if unknown_fields:
                raise ValueError(
                    f'Narration timing plan "{slide_name}" group #{position} '
                    f"has unknown field(s): {', '.join(sorted(unknown_fields))}"
                )
            group_id = entry_raw.get("id")
            if not isinstance(group_id, str) or not group_id.strip():
                raise ValueError(
                    f'Narration timing plan "{slide_name}" group #{position} '
                    'field "id" must be a non-empty string'
                )
            if group_id in seen_groups:
                raise ValueError(
                    f'Narration timing plan "{slide_name}" repeats group "{group_id}"'
                )
            cue_number = entry_raw.get("cue")
            if cue_number is not None and (
                isinstance(cue_number, bool)
                or not isinstance(cue_number, int)
                or cue_number <= 0
            ):
                raise ValueError(
                    f'Narration timing plan "{slide_name}/{group_id}" cue '
                    "must be a positive integer or null"
                )
            entries.append(TimingPlanEntry(group_id, cue_number))
            seen_groups.add(group_id)
        result[slide_name] = entries
    return srt_sha256, narration_padding, narration_start_floor, result



def _load_canonical_animation_config(path: Path) -> dict[str, Any]:
    """Load and field-validate the read-only canonical animation sidecar."""
    if not path.is_file():
        raise FileNotFoundError(
            f"Canonical animation config is missing: {path}. "
            "Complete the customize-animations stage first; narration sync "
            "does not create or replace animations.json."
        )
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Canonical animation config must be a JSON object: {path}")
    if raw.get("version", 1) != 1:
        raise ValueError(
            f"Unsupported canonical animation config version: {raw.get('version')!r}"
        )
    errors = list(
        dict.fromkeys(
            [
                *validate_transition_config(raw),
                *validate_animation_config_errors(raw),
            ]
        )
    )
    if errors:
        details = "\n".join(f"- {error}" for error in errors)
        raise ValueError(
            f"Canonical animation config is invalid: {path}\n{details}"
        )
    return raw



def _page_subtitle_names(subtitle_dir: Path) -> list[str]:
    """Return ordered page-local SRT stems, excluding the merged sidecar."""
    if not subtitle_dir.is_dir():
        raise FileNotFoundError(f"Page-local SRT directory not found: {subtitle_dir}")
    names = [
        path.stem
        for path in sort_slide_paths(subtitle_dir.glob("*.srt"))
        if path.stem != "total"
    ]
    if not names:
        raise FileNotFoundError(f"No page-local SRT files found under: {subtitle_dir}")
    return names



def _animation_slide_names(
    project_path: Path,
    config: dict[str, Any],
    subtitle_dir: Path,
) -> list[str]:
    """Resolve the page roster without parsing SVG unless a slide is missing."""
    slides = config.get("slides", {})
    if not isinstance(slides, dict):
        raise ValueError('Canonical animations.json field "slides" must be an object')
    canonical_names = list(slides)
    subtitle_names = _page_subtitle_names(subtitle_dir)
    subtitle_set = set(subtitle_names)

    missing_subtitles = [
        slide_name for slide_name in canonical_names if slide_name not in subtitle_set
    ]
    if missing_subtitles:
        raise FileNotFoundError(
            "Missing page-local SRT for canonical animation slide(s): "
            + ", ".join(missing_subtitles)
        )

    canonical_set = set(canonical_names)
    extra_subtitles = [
        slide_name for slide_name in subtitle_names if slide_name not in canonical_set
    ]
    unexpected = [
        slide_name
        for slide_name in extra_subtitles
        if not (project_path / "svg_output" / f"{slide_name}.svg").is_file()
    ]
    if unexpected:
        raise ValueError(
            "Page-local SRT has no matching canonical animation slide or SVG: "
            + ", ".join(unexpected)
        )
    if not extra_subtitles:
        return canonical_names
    return subtitle_names



def _animation_scope(
    scope: dict[str, Any],
    *,
    label: str,
) -> dict[str, Any]:
    value = scope.get("animation", {})
    if not isinstance(value, dict):
        raise ValueError(f'{label} field "animation" must be an object')
    return value



def _transition_scope(
    scope: dict[str, Any],
    *,
    label: str,
) -> dict[str, Any]:
    value = scope.get("transition", {})
    if not isinstance(value, dict):
        raise ValueError(f'{label} field "transition" must be an object')
    return value



def _effective_transition_duration_ms(
    config: dict[str, Any],
    slide_cfg: dict[str, Any],
) -> int:
    """Resolve the destination slide's effective transition duration."""
    defaults = config.get("defaults", {})
    if not isinstance(defaults, dict):
        raise ValueError('Canonical animations.json field "defaults" must be an object')
    default_transition = _transition_scope(
        defaults,
        label="Canonical animations.json defaults",
    )
    default_effect, _default_options = normalize_transition_effect_request(
        default_transition.get("effect", "fade"),
        default_transition.get("effect_options"),
    )
    default_duration = validate_seconds(
        default_transition.get("duration", DEFAULT_TRANSITION_DURATION),
        "canonical transition duration",
        allow_zero=default_effect is None,
    )

    slide_transition = _transition_scope(
        slide_cfg,
        label="Canonical animations.json slide",
    )
    if "effect" in slide_transition:
        effect, _effect_options = normalize_transition_effect_request(
            slide_transition["effect"],
            slide_transition.get("effect_options"),
        )
    else:
        effect = default_effect
    duration = validate_seconds(
        slide_transition.get("duration", default_duration),
        "canonical slide transition duration",
        allow_zero=effect is None,
    )
    return 0 if effect is None else round(duration * 1000)



def _effective_slide_animation(
    config: dict[str, Any],
    slide_cfg: dict[str, Any],
) -> SlideAnimationSettings:
    defaults = config.get("defaults", {})
    if not isinstance(defaults, dict):
        raise ValueError('Canonical animations.json field "defaults" must be an object')
    default_animation = _animation_scope(
        defaults,
        label="Canonical animations.json defaults",
    )
    slide_animation = _animation_scope(
        slide_cfg,
        label="Canonical animations.json slide",
    )
    effect = normalize_animation_effect(
        slide_animation.get(
            "effect",
            default_animation.get("effect", "none"),
        )
    )
    duration_ms = animation_seconds_to_milliseconds(
        slide_animation.get(
            "duration",
            default_animation.get("duration", 0.4),
        ),
        "canonical animation duration",
        allow_zero=False,
    )
    stagger_ms = animation_seconds_to_milliseconds(
        slide_animation.get(
            "stagger",
            default_animation.get("stagger", 0.5),
        ),
        "canonical animation stagger",
        allow_zero=True,
    )
    trigger = normalize_animation_trigger(
        slide_animation.get(
            "trigger",
            default_animation.get("trigger", "after-previous"),
        )
    )
    timing_options = {
        field: (
            slide_animation[field]
            if field in slide_animation
            else default_animation[field]
        )
        for field in ANIMATION_TIMING_OPTION_FIELDS
        if field in slide_animation or field in default_animation
    }
    return SlideAnimationSettings(
        effect=effect,
        duration_ms=duration_ms,
        stagger_ms=stagger_ms,
        trigger=trigger,
        timing_options=timing_options,
    )



def _animation_playback_duration_ms(
    duration_ms: int,
    timing_options: dict[str, Any],
    *,
    label: str,
) -> int:
    """Return schedule duration after repeat and auto-reverse parameters."""
    if 'repeat_duration' in timing_options:
        return animation_seconds_to_milliseconds(
            timing_options['repeat_duration'],
            f'{label} repeat_duration',
            allow_zero=False,
        )
    one_play = duration_ms * (
        2 if timing_options.get('auto_reverse') is True else 1
    )
    repeat_count = timing_options.get('repeat_count', 1)
    if (
        isinstance(repeat_count, bool)
        or not isinstance(repeat_count, (int, float))
        or not math.isfinite(float(repeat_count))
        or float(repeat_count) <= 0
    ):
        raise ValueError(f'{label} repeat_count must be a positive number')
    return max(1, round(one_play * float(repeat_count)))



def _active_group_effect_entries(
    slide_name: str,
    group_id: str,
    group_cfg: dict[str, Any],
    slide_effect: str | None,
) -> tuple[tuple[int | None, str, dict[str, Any]], ...]:
    """Return active legacy or multi-effect rows with stable write locations."""
    group_path = (
        f'slides[{json.dumps(slide_name, ensure_ascii=False)}]'
        f'.groups[{json.dumps(group_id, ensure_ascii=False)}]'
    )
    effect_entries = animation_group_effect_entries(
        group_cfg,
        path=group_path,
    )
    is_multi_effect = 'effects' in group_cfg
    active: list[tuple[int | None, str, dict[str, Any]]] = []
    for index, (effect_path, effect_cfg) in enumerate(effect_entries):
        effect = normalize_animation_effect(
            effect_cfg.get("effect", slide_effect)
        )
        if effect is None:
            continue
        active.append((
            index if is_multi_effect else None,
            effect_path,
            effect_cfg,
        ))
    return tuple(active)



def _group_is_animated(
    slide_name: str,
    group_id: str,
    group_cfg: dict[str, Any],
    slide_effect: str | None,
) -> bool:
    return bool(
        _active_group_effect_entries(
            slide_name,
            group_id,
            group_cfg,
            slide_effect,
        )
    )



def _active_group_order_requires_svg(
    slide_name: str,
    groups_cfg: dict[str, Any],
    group_ids: list[str],
    slide_effect: str | None,
) -> bool:
    """Return whether SVG group order is needed to break sequence ambiguity."""
    if len(group_ids) <= 1:
        return False
    order_owners: dict[int, str] = {}
    for group_id in group_ids:
        for _effect_index, _effect_path, effect_cfg in _active_group_effect_entries(
            slide_name,
            group_id,
            groups_cfg[group_id],
            slide_effect,
        ):
            order = effect_cfg.get("order")
            if order is None:
                return True
            previous_group = order_owners.setdefault(order, group_id)
            if previous_group != group_id:
                return True
    return False



def _needs_svg_group_resolution(
    slide_name: str,
    settings: SlideAnimationSettings,
    groups_cfg: dict[str, Any],
    plan_entries: list[TimingPlanEntry] | None,
) -> bool:
    """Return whether JSON alone cannot prove the effective group sequence."""
    active_explicit = [
        group_id
        for group_id, group_cfg in groups_cfg.items()
        if isinstance(group_cfg, dict)
        and _group_is_animated(
            slide_name,
            group_id,
            group_cfg,
            settings.effect,
        )
    ]
    if plan_entries is None:
        if settings.effect is not None:
            return True
        return _active_group_order_requires_svg(
            slide_name,
            groups_cfg,
            active_explicit,
            settings.effect,
        )

    candidate_ids = list(
        dict.fromkeys(
            [
                *(entry.group_id for entry in plan_entries),
                *active_explicit,
            ]
        )
    )
    active_candidates = [
        group_id
        for group_id in candidate_ids
        if group_id in groups_cfg
        and isinstance(groups_cfg[group_id], dict)
        and _group_is_animated(
            slide_name,
            group_id,
            groups_cfg[group_id],
            settings.effect,
        )
    ]
    return _active_group_order_requires_svg(
        slide_name,
        groups_cfg,
        active_candidates,
        settings.effect,
    )



def _resolve_animation_groups(
    project_path: Path,
    slide_name: str,
    slide_cfg: dict[str, Any],
    settings: SlideAnimationSettings,
    plan_entries: list[TimingPlanEntry] | None,
) -> tuple[list[AnimationGroupState], bool]:
    """Resolve one effective sequence, parsing only an ambiguous page SVG."""
    groups_value = slide_cfg.get("groups", {})
    if not isinstance(groups_value, dict):
        raise ValueError(
            f'Canonical animations.json slide "{slide_name}" groups must be an object'
        )
    groups_cfg: dict[str, dict[str, Any]] = {}
    for group_id, group_cfg in groups_value.items():
        if not isinstance(group_cfg, dict):
            raise ValueError(
                f'Canonical animations.json group "{slide_name}/{group_id}" '
                "must be an object"
            )
        groups_cfg[group_id] = group_cfg
    use_svg = _needs_svg_group_resolution(
        slide_name,
        settings,
        groups_cfg,
        plan_entries,
    )
    candidate_ids: list[str]
    if use_svg:
        svg_path = project_path / "svg_output" / f"{slide_name}.svg"
        if not svg_path.is_file():
            raise FileNotFoundError(
                f"Animation group mapping is ambiguous and requires the page SVG: "
                f"{svg_path}"
            )
        targets, anonymous_groups = scan_svg_targets(svg_path)
        duplicate_ids = sorted(
            group_id
            for group_id in {target.group_id for target in targets}
            if sum(target.group_id == group_id for target in targets) > 1
        )
        if duplicate_ids:
            raise ValueError(
                f'SVG slide "{slide_name}" has duplicate top-level group id(s): '
                + ", ".join(duplicate_ids)
            )
        for item in anonymous_groups:
            print(
                f"Warning: {item} has no id and cannot participate in narration timing",
                file=sys.stderr,
            )

        targets_by_id = {target.group_id: target for target in targets}
        referenced_ids = set(groups_cfg)
        if plan_entries is not None:
            referenced_ids.update(entry.group_id for entry in plan_entries)
        missing_ids = sorted(referenced_ids - set(targets_by_id))
        if missing_ids:
            raise ValueError(
                f'Animation mapping for slide "{slide_name}" references missing '
                f"top-level group(s): {', '.join(missing_ids)}"
            )
        structural_ids = sorted(
            group_id
            for group_id in referenced_ids
            if targets_by_id[group_id].structurally_static
            and (
                group_id not in groups_cfg
                or _group_is_animated(
                    slide_name,
                    group_id,
                    groups_cfg[group_id],
                    settings.effect,
                )
            )
        )
        if structural_ids:
            raise ValueError(
                f'Animation mapping for slide "{slide_name}" targets structural '
                f"group(s): {', '.join(structural_ids)}"
            )

        candidate_ids = []
        for target in targets:
            if target.structurally_static:
                continue
            group_cfg = groups_cfg.get(target.group_id, {})
            explicitly_animated = (
                target.group_id in groups_cfg
                and _group_is_animated(
                    slide_name,
                    target.group_id,
                    group_cfg,
                    settings.effect,
                )
            )
            if target.chrome and not explicitly_animated:
                continue
            candidate_ids.append(target.group_id)
    elif plan_entries is not None:
        candidate_ids = list(
            dict.fromkeys(
                [
                    *(entry.group_id for entry in plan_entries),
                    *groups_cfg,
                ]
            )
        )
    else:
        candidate_ids = list(groups_cfg)

    preliminaries: list[
        tuple[
            int,
            int,
            int,
            str,
            int | None,
            str,
            dict[str, Any],
            str,
        ]
    ] = []
    for source_index, group_id in enumerate(candidate_ids):
        group_cfg = groups_cfg.get(group_id, {})
        effect_entries = _active_group_effect_entries(
            slide_name,
            group_id,
            group_cfg,
            settings.effect,
        )
        for effect_position, (
            effect_index,
            effect_path,
            effect_cfg,
        ) in enumerate(effect_entries):
            order = effect_cfg.get("order", source_index + 1)
            if isinstance(order, bool) or not isinstance(order, int) or order <= 0:
                raise ValueError(
                    f'Canonical animation order for "{effect_path}" '
                    f"must be a positive integer: {order!r}"
                )
            if effect_cfg.get("trigger_shape") is not None:
                raise ValueError(
                    f'Recorded narration cannot synchronize trigger-shape '
                    f'animation "{effect_path}" on slide "{slide_name}"'
                )
            effect_trigger = normalize_animation_trigger(
                effect_cfg.get("trigger", settings.trigger)
            )
            if effect_trigger == "on-click":
                raise ValueError(
                    f'Recorded narration cannot synchronize on-click animation '
                    f'"{effect_path}" on slide "{slide_name}"'
                )
            preliminaries.append((
                order,
                source_index,
                effect_position,
                group_id,
                effect_index,
                effect_path,
                effect_cfg,
                effect_trigger,
            ))
    preliminaries.sort(key=lambda item: (item[0], item[1], item[2]))

    states: list[AnimationGroupState] = []
    for sequence_index, (
        order,
        source_index,
        _effect_position,
        group_id,
        effect_index,
        effect_path,
        effect_cfg,
        effect_trigger,
    ) in enumerate(preliminaries):
        duration_ms = animation_seconds_to_milliseconds(
            effect_cfg.get("duration", settings.duration_ms / 1000),
            f'canonical animation duration for "{effect_path}"',
            allow_zero=False,
        )
        timing_options = dict(settings.timing_options)
        timing_options.update(
            {
                field: effect_cfg[field]
                for field in ANIMATION_TIMING_OPTION_FIELDS
                if field in effect_cfg
            }
        )
        playback_duration_ms = _animation_playback_duration_ms(
            duration_ms,
            timing_options,
            label=f'canonical animation for "{effect_path}"',
        )
        if effect_trigger == "after-previous" and sequence_index > 0:
            default_delay = settings.stagger_ms / 1000
        else:
            default_delay = 0
        original_delay_ms = animation_seconds_to_milliseconds(
            effect_cfg.get("delay", default_delay),
            f'canonical animation delay for "{effect_path}"',
            allow_zero=True,
        )
        states.append(
            AnimationGroupState(
                group_id=group_id,
                effect_index=effect_index,
                order=order,
                source_index=source_index,
                duration_ms=playback_duration_ms,
                original_delay_ms=original_delay_ms,
                trigger=effect_trigger,
            )
        )
    return states, use_svg



def _find_audio(audio_dir: Path, slide_name: str) -> Path:
    matches = [
        path
        for path in audio_dir.iterdir()
        if path.is_file()
        and path.stem == slide_name
        and path.suffix.lower() in NARRATION_EXTENSIONS
    ]
    if not matches:
        raise FileNotFoundError(f"Missing narration audio for slide: {slide_name}")
    if len(matches) > 1:
        rendered = ", ".join(str(path) for path in matches)
        raise ValueError(f"Multiple narration audio files match {slide_name}: {rendered}")
    return matches[0]



def _seconds_from_ms(value: int) -> float:
    return round(value / 1000, 3)

