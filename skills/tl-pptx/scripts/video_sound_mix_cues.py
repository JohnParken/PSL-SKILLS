"""video_sound_mix cues responsibilities split from pinned upstream."""
from __future__ import annotations

from video_sound_mix_base import (
    AUDIO_REL_TYPE as AUDIO_REL_TYPE,
    Any as Any,
    ET as ET,
    Path as Path,
    SoundCue as SoundCue,
    VideoTimelineCalibration as VideoTimelineCalibration,
    _OUTPUT_AUDIO_BITRATE as _OUTPUT_AUDIO_BITRATE,
    _OUTPUT_SAMPLE_RATE as _OUTPUT_SAMPLE_RATE,
    _RELATIONSHIP_TAG as _RELATIONSHIP_TAG,
    canonical_opc_part_path as canonical_opc_part_path,
    hashlib as hashlib,
    os as os,
    presentation_slide_members as presentation_slide_members,
    read_slide_animation_sequence as read_slide_animation_sequence,
    read_slide_transition_xml as read_slide_transition_xml,
    resolve_internal_opc_target as resolve_internal_opc_target,
    tempfile as tempfile,
    zipfile as zipfile,
)
from video_sound_mix_probe import (
    _run_command as _run_command,
    _probe_media as _probe_media,
    _duration_ms as _duration_ms,
    _target_sample_count as _target_sample_count,
)


def _trace_slides(trace: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    raw_slides = trace.get("slides")
    if not isinstance(raw_slides, list) or not raw_slides:
        raise ValueError("Conversion trace must contain a non-empty slides array")
    slide_count = trace.get("slide_count")
    if slide_count != len(raw_slides):
        raise ValueError(
            "Conversion trace slide_count does not match its slides array"
        )

    slides: list[dict[str, Any]] = []
    slide_names: list[str] = []
    for expected_number, raw_slide in enumerate(raw_slides, 1):
        if not isinstance(raw_slide, dict):
            raise ValueError(
                f"Conversion trace slide {expected_number} must be an object"
            )
        if raw_slide.get("slide_num") != expected_number:
            raise ValueError(
                "Conversion trace slides must be numbered consecutively from 1"
            )
        raw_svg = raw_slide.get("svg")
        if not isinstance(raw_svg, str) or not raw_svg.strip():
            raise ValueError(
                f"Conversion trace slide {expected_number} has no SVG path"
            )
        slide_name = Path(raw_svg).stem
        if not slide_name:
            raise ValueError(
                f"Conversion trace slide {expected_number} has an invalid SVG path"
            )
        slides.append(raw_slide)
        slide_names.append(slide_name)
    if len(set(slide_names)) != len(slide_names):
        raise ValueError("Conversion trace slide names must be unique")
    return slides, slide_names



def _shape_group_ids(trace_slide: dict[str, Any]) -> dict[int, str]:
    events = trace_slide.get("events")
    if not isinstance(events, list):
        return {}
    mapping: dict[int, str] = {}
    for event in events:
        if not isinstance(event, dict):
            continue
        shape_id = event.get("shape_id")
        group_id = event.get("id")
        if (
            isinstance(shape_id, int)
            and not isinstance(shape_id, bool)
            and isinstance(group_id, str)
            and group_id
        ):
            mapping[shape_id] = group_id
    return mapping



def _package_part_map(package: zipfile.ZipFile) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for member in package.namelist():
        key = canonical_opc_part_path(member)
        if key is None:
            continue
        prior = mapping.get(key)
        if prior is not None and prior != member:
            raise ValueError(
                "PPTX contains OPC-equivalent duplicate package parts: "
                f"{prior!r}, {member!r}"
            )
        mapping[key] = member
    return mapping



def _relationships_part(slide_part: str) -> str:
    slide_path = Path(slide_part)
    return (
        slide_path.parent / "_rels" / f"{slide_path.name}.rels"
    ).as_posix()



def _slide_relationships(
    package: zipfile.ZipFile,
    rels_part: str,
) -> dict[str, ET.Element]:
    try:
        root = ET.fromstring(package.read(rels_part))
    except KeyError as exc:
        raise ValueError(f"PPTX is missing slide relationships: {rels_part}") from exc
    except ET.ParseError as exc:
        raise ValueError(f"Invalid slide relationships XML: {rels_part}") from exc
    relationships: dict[str, ET.Element] = {}
    for relationship in root.findall(_RELATIONSHIP_TAG):
        relationship_id = relationship.get("Id")
        if not relationship_id:
            continue
        if relationship_id in relationships:
            raise ValueError(
                f"Duplicate relationship id {relationship_id!r} in {rels_part}"
            )
        relationships[relationship_id] = relationship
    return relationships



def _extract_sound(
    *,
    package: zipfile.ZipFile,
    package_parts: dict[str, str],
    relationships: dict[str, ET.Element],
    rels_part: str,
    relationship_id: str,
    temp_dir: Path,
    ffprobe_path: str,
    extracted: dict[str, tuple[str, str, Path, int]],
) -> tuple[str, str, Path, int]:
    relationship = relationships.get(relationship_id)
    if relationship is None:
        raise ValueError(
            f"PPTX sound relationship is missing: {rels_part}#{relationship_id}"
        )
    if relationship.get("Type") != AUDIO_REL_TYPE:
        raise ValueError(
            f"PPTX sound relationship is not audio: {rels_part}#{relationship_id}"
        )
    if relationship.get("TargetMode", "Internal") == "External":
        raise ValueError(
            f"PPTX sound relationship must be internal: {rels_part}#{relationship_id}"
        )
    target = relationship.get("Target") or ""
    target_key = resolve_internal_opc_target(rels_part, target)
    if target_key is None:
        raise ValueError(
            f"PPTX sound relationship target is invalid: {rels_part}#{relationship_id}"
        )
    package_part = package_parts.get(target_key)
    if package_part is None:
        raise ValueError(
            f"PPTX sound relationship target is missing: {target!r}"
        )
    cached = extracted.get(package_part)
    if cached is not None:
        return cached

    payload = package.read(package_part)
    media_sha256 = hashlib.sha256(payload).hexdigest()
    extension = Path(package_part).suffix.lower()
    if extension not in {".m4a", ".mp3", ".wav"}:
        raise ValueError(
            f"PPTX animation sound uses an unsupported media type: {package_part}"
        )
    media_path = temp_dir / f"{media_sha256}{extension}"
    media_path.write_bytes(payload)
    source_duration_ms = _duration_ms(
        _probe_media(media_path, ffprobe_path),
        f"PPTX sound {package_part}",
    )
    resolved = (package_part, media_sha256, media_path, source_duration_ms)
    extracted[package_part] = resolved
    return resolved



def _require_equal(actual: object, expected: object, field: str) -> None:
    if actual != expected:
        raise ValueError(
            f"Final narrated trace does not match the PPTX at {field}: "
            f"trace={actual!r}, pptx={expected!r}"
        )



def _resolve_sound_cues(
    *,
    pptx_path: Path,
    trace_slides: list[dict[str, Any]],
    calibration: VideoTimelineCalibration,
    transition_gain_db: float,
    animation_gain_db: float,
    video_duration_ms: int,
    temp_dir: Path,
    ffprobe_path: str,
) -> list[SoundCue]:
    cues: list[SoundCue] = []
    extracted: dict[str, tuple[str, str, Path, int]] = {}
    with zipfile.ZipFile(pptx_path) as package:
        slide_members = presentation_slide_members(package)
        if len(slide_members) != len(trace_slides):
            raise ValueError(
                f"Final narrated PPTX has {len(slide_members)} slides, but "
                f"the trace has {len(trace_slides)}"
            )
        package_parts = _package_part_map(package)

        for slide_num, (slide_part, trace_slide, calibrated) in enumerate(
            zip(slide_members, trace_slides, calibration.slides),
            1,
        ):
            slide_xml = package.read(slide_part)
            transition = read_slide_transition_xml(slide_xml)
            animation = read_slide_animation_sequence(
                slide_xml,
                require_supported_effects=True,
            )
            trace_motion = trace_slide.get("motion")
            trace_animation = trace_slide.get("animation")
            if not isinstance(trace_motion, dict) or not isinstance(
                trace_animation, dict
            ):
                raise ValueError(
                    f"Conversion trace slide {slide_num} lacks resolved motion"
                )

            _require_equal(
                trace_motion.get("duration_ms"),
                transition.duration_ms,
                f"slide {slide_num} transition duration",
            )
            _require_equal(
                trace_motion.get("advance_after_ms"),
                transition.advance_after_ms,
                f"slide {slide_num} recorded advance",
            )
            _require_equal(
                trace_motion.get("sound_relationship_id"),
                transition.sound_relationship_id,
                f"slide {slide_num} transition sound relationship",
            )
            _require_equal(
                trace_motion.get("sound_name"),
                transition.sound_name,
                f"slide {slide_num} transition sound name",
            )
            _require_equal(
                trace_motion.get("fallback_sound_relationship_id"),
                transition.fallback_sound_relationship_id,
                f"slide {slide_num} fallback transition sound relationship",
            )
            _require_equal(
                trace_motion.get("fallback_sound_name"),
                transition.fallback_sound_name,
                f"slide {slide_num} fallback transition sound name",
            )
            if transition.fallback_sound_relationship_id not in {
                None,
                transition.sound_relationship_id,
            }:
                raise ValueError(
                    f"Slide {slide_num} transition primary and fallback "
                    "branches reference different sounds"
                )

            raw_rows = trace_animation.get("rows")
            if not isinstance(raw_rows, list):
                raise ValueError(
                    f"Conversion trace slide {slide_num} animation rows are invalid"
                )
            if len(raw_rows) != len(animation.rows):
                raise ValueError(
                    f"Final narrated trace has {len(raw_rows)} animation rows "
                    f"on slide {slide_num}; PPTX has {len(animation.rows)}"
                )
            for row_index, (raw_row, actual_row) in enumerate(
                zip(raw_rows, animation.rows),
                1,
            ):
                if not isinstance(raw_row, dict):
                    raise ValueError(
                        f"Conversion trace slide {slide_num} row {row_index} "
                        "must be an object"
                    )
                for key, expected in (
                    ("shape_id", actual_row.shape_id),
                    ("offset_ms", actual_row.offset_ms),
                    ("trigger", actual_row.trigger),
                    ("sound_relationship_id", actual_row.sound_relationship_id),
                    ("sound_name", actual_row.sound_name),
                ):
                    _require_equal(
                        raw_row.get(key),
                        expected,
                        f"slide {slide_num} animation row {row_index} {key}",
                    )

            sound_relationship_ids = {
                relationship_id
                for relationship_id in (
                    transition.sound_relationship_id,
                    *(
                        row.sound_relationship_id
                        for row in animation.rows
                    ),
                )
                if relationship_id is not None
            }
            relationships: dict[str, ET.Element] = {}
            rels_part = _relationships_part(slide_part)
            if sound_relationship_ids:
                relationships = _slide_relationships(package, rels_part)

            group_ids = _shape_group_ids(trace_slide)
            if transition.sound_relationship_id is not None:
                package_part, media_sha256, media_path, source_duration_ms = (
                    _extract_sound(
                        package=package,
                        package_parts=package_parts,
                        relationships=relationships,
                        rels_part=rels_part,
                        relationship_id=transition.sound_relationship_id,
                        temp_dir=temp_dir,
                        ffprobe_path=ffprobe_path,
                        extracted=extracted,
                    )
                )
                cues.append(
                    SoundCue(
                        kind="transition",
                        slide_num=slide_num,
                        slide_name=calibrated.slide_name,
                        row_index=None,
                        shape_id=None,
                        group_id=None,
                        relationship_id=transition.sound_relationship_id,
                        sound_name=transition.sound_name or Path(package_part).name,
                        package_part=package_part,
                        media_sha256=media_sha256,
                        media_path=media_path,
                        source_duration_ms=source_duration_ms,
                        powerpoint_time_ms=calibrated.powerpoint_slide_start_ms,
                        video_time_ms=calibrated.video_slide_start_ms,
                        gain_db=transition_gain_db,
                    )
                )

            for row_index, row in enumerate(animation.rows, 1):
                if row.sound_relationship_id is None:
                    continue
                if row.trigger not in {"after-previous", "with-previous"}:
                    raise ValueError(
                        f"Slide {slide_num} animation row {row_index} uses "
                        f"{row.trigger!r}; video sound timing requires a "
                        "click-free row"
                    )
                package_part, media_sha256, media_path, source_duration_ms = (
                    _extract_sound(
                        package=package,
                        package_parts=package_parts,
                        relationships=relationships,
                        rels_part=rels_part,
                        relationship_id=row.sound_relationship_id,
                        temp_dir=temp_dir,
                        ffprobe_path=ffprobe_path,
                        extracted=extracted,
                    )
                )
                powerpoint_time_ms = (
                    calibrated.powerpoint_slide_start_ms
                    + calibrated.transition_ms
                    + row.offset_ms
                )
                video_time_ms = (
                    calibrated.video_slide_start_ms
                    + calibrated.transition_ms
                    + row.offset_ms
                )
                cues.append(
                    SoundCue(
                        kind="animation",
                        slide_num=slide_num,
                        slide_name=calibrated.slide_name,
                        row_index=row_index,
                        shape_id=row.shape_id,
                        group_id=group_ids.get(row.shape_id),
                        relationship_id=row.sound_relationship_id,
                        sound_name=row.sound_name or Path(package_part).name,
                        package_part=package_part,
                        media_sha256=media_sha256,
                        media_path=media_path,
                        source_duration_ms=source_duration_ms,
                        powerpoint_time_ms=powerpoint_time_ms,
                        video_time_ms=video_time_ms,
                        gain_db=animation_gain_db,
                    )
                )

    cues.sort(
        key=lambda cue: (
            cue.video_time_ms,
            cue.slide_num,
            0 if cue.kind == "transition" else 1,
            cue.row_index or 0,
        )
    )
    if not cues:
        raise ValueError(
            "Final narrated PPTX and trace contain no transition or "
            "object-animation sound cues; use the PowerPoint video directly"
        )
    for cue in cues:
        if cue.video_time_ms < 0 or cue.video_time_ms >= video_duration_ms:
            raise ValueError(
                f"Sound cue falls outside the video: slide {cue.slide_num}, "
                f"time={cue.video_time_ms}ms, video={video_duration_ms}ms"
            )
        cue_end_ms = cue.video_time_ms + cue.source_duration_ms
        if cue_end_ms > video_duration_ms:
            raise ValueError(
                f"Sound cue would be truncated by the video end: slide "
                f"{cue.slide_num}, cue end={cue_end_ms}ms, "
                f"video={video_duration_ms}ms"
            )
    return cues



def _render_stem(
    *,
    cues: list[SoundCue],
    duration_ms: int,
    output_path: Path,
    ffmpeg_path: str,
) -> None:
    target_samples = _target_sample_count(duration_ms)
    command = [
        ffmpeg_path,
        "-nostdin",
        "-hide_banner",
        "-v",
        "error",
        "-f",
        "lavfi",
        "-i",
        f"anullsrc=r={_OUTPUT_SAMPLE_RATE}:cl=stereo",
    ]
    for cue in cues:
        command.extend(["-i", str(cue.media_path)])

    filters = [
        (
            f"[0:a:0]aformat=sample_fmts=fltp:sample_rates={_OUTPUT_SAMPLE_RATE}:"
            "channel_layouts=stereo,"
            f"atrim=end_sample={target_samples},asetpts=N/SR/TB[base]"
        )
    ]
    labels = ["[base]"]
    for input_index, cue in enumerate(cues, 1):
        label = f"cue{input_index}"
        delay_samples = int(
            round(cue.video_time_ms * _OUTPUT_SAMPLE_RATE / 1000)
        )
        filters.append(
            f"[{input_index}:a:0]aresample={_OUTPUT_SAMPLE_RATE},"
            "aformat=sample_fmts=fltp:"
            f"sample_rates={_OUTPUT_SAMPLE_RATE}:channel_layouts=stereo,"
            f"asetpts=N/SR/TB,volume={cue.gain_db:.3f}dB,"
            f"adelay={delay_samples}S:all=1,"
            f"apad=whole_len={target_samples},"
            f"atrim=end_sample={target_samples}[{label}]"
        )
        labels.append(f"[{label}]")
    filters.append(
        "".join(labels)
        + f"amix=inputs={len(labels)}:duration=first:"
        "dropout_transition=0:normalize=0,"
        f"atrim=end_sample={target_samples},asetpts=N/SR/TB[stem]"
    )
    command.extend(
        [
            "-filter_complex",
            ";".join(filters),
            "-map",
            "[stem]",
            "-c:a",
            "pcm_f32le",
            "-ar",
            str(_OUTPUT_SAMPLE_RATE),
            "-ac",
            "2",
            "-y",
            str(output_path),
        ]
    )
    _run_command(command, "SFX stem render")



def _mix_video(
    *,
    video_path: Path,
    stem_path: Path,
    duration_ms: int,
    limiter_dbfs: float,
    output_path: Path,
    ffmpeg_path: str,
) -> None:
    target_samples = _target_sample_count(duration_ms)
    limiter_ratio = 10 ** (limiter_dbfs / 20)
    filter_graph = (
        f"[0:a:0]aresample={_OUTPUT_SAMPLE_RATE}:first_pts=0,"
        "aformat=sample_fmts=fltp:channel_layouts=stereo,"
        f"asetpts=N/SR/TB,apad=whole_len={target_samples},"
        f"atrim=end_sample={target_samples}[narration];"
        f"[1:a:0]aresample={_OUTPUT_SAMPLE_RATE}:first_pts=0,"
        "aformat=sample_fmts=fltp:channel_layouts=stereo,"
        f"asetpts=N/SR/TB,apad=whole_len={target_samples},"
        f"atrim=end_sample={target_samples}[sfx];"
        "[narration][sfx]amix=inputs=2:duration=first:"
        "dropout_transition=0:normalize=0,"
        f"alimiter=limit={limiter_ratio:.9f}:attack=5:release=50:"
        "level=false:latency=true,"
        f"apad=whole_len={target_samples},"
        f"atrim=end_sample={target_samples},asetpts=N/SR/TB[mixed]"
    )
    _run_command(
        [
            ffmpeg_path,
            "-nostdin",
            "-hide_banner",
            "-v",
            "error",
            "-i",
            str(video_path),
            "-i",
            str(stem_path),
            "-filter_complex",
            filter_graph,
            "-map",
            "0:v:0",
            "-map",
            "[mixed]",
            "-map_metadata",
            "0",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            _OUTPUT_AUDIO_BITRATE,
            "-ar",
            str(_OUTPUT_SAMPLE_RATE),
            "-ac",
            "2",
            "-movflags",
            "+faststart",
            "-y",
            str(output_path),
        ],
        "Narration and SFX mix",
    )



def _temporary_output(path: Path, suffix: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw_path = tempfile.mkstemp(
        prefix=f".{path.stem}.",
        suffix=suffix,
        dir=path.parent,
    )
    os.close(descriptor)
    temporary = Path(raw_path)
    temporary.unlink()
    return temporary



def _publish_outputs(replacements: list[tuple[Path, Path]]) -> None:
    """Publish verified outputs together, restoring prior files on failure."""
    backups: dict[Path, Path] = {}
    published: list[Path] = []
    try:
        for _temporary, destination in replacements:
            if not destination.exists():
                continue
            backup = _temporary_output(destination, f".backup{destination.suffix}")
            os.replace(destination, backup)
            backups[destination] = backup
        for temporary, destination in replacements:
            os.replace(temporary, destination)
            published.append(destination)
    except OSError as exc:
        rollback_errors: list[str] = []
        for destination in reversed(published):
            backup = backups.pop(destination, None)
            try:
                if backup is None:
                    destination.unlink(missing_ok=True)
                else:
                    os.replace(backup, destination)
            except OSError as rollback_exc:
                rollback_errors.append(f"{destination}: {rollback_exc}")
        for destination, backup in backups.items():
            try:
                os.replace(backup, destination)
            except OSError as rollback_exc:
                rollback_errors.append(f"{destination}: {rollback_exc}")
        if rollback_errors:
            raise RuntimeError(
                "Output publication failed and rollback was incomplete: "
                + "; ".join(rollback_errors)
            ) from exc
        raise
    else:
        for backup in backups.values():
            backup.unlink(missing_ok=True)



def _cue_report(cue: SoundCue) -> dict[str, Any]:
    return {
        "kind": cue.kind,
        "slide_num": cue.slide_num,
        "slide_name": cue.slide_name,
        "row_index": cue.row_index,
        "shape_id": cue.shape_id,
        "group_id": cue.group_id,
        "relationship_id": cue.relationship_id,
        "sound_name": cue.sound_name,
        "package_part": cue.package_part,
        "media_sha256": cue.media_sha256,
        "source_duration_ms": cue.source_duration_ms,
        "powerpoint_time_ms": cue.powerpoint_time_ms,
        "video_time_ms": cue.video_time_ms,
        "gain_db": cue.gain_db,
    }

