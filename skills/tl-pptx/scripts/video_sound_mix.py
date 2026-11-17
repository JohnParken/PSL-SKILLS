"""video_sound_mix main responsibilities split from pinned upstream."""
from __future__ import annotations

from video_sound_mix_base import (
    AUDIO_REL_TYPE as AUDIO_REL_TYPE,
    Any as Any,
    ET as ET,
    Fraction as Fraction,
    Path as Path,
    SoundCue as SoundCue,
    SoundMixResult as SoundMixResult,
    VideoTimelineCalibration as VideoTimelineCalibration,
    _DEFAULT_ANIMATION_GAIN_DB as _DEFAULT_ANIMATION_GAIN_DB,
    _DEFAULT_LIMITER_DBFS as _DEFAULT_LIMITER_DBFS,
    _DEFAULT_TRANSITION_GAIN_DB as _DEFAULT_TRANSITION_GAIN_DB,
    _MIN_SOUND_MIX_CORRELATION as _MIN_SOUND_MIX_CORRELATION,
    _MIN_VERIFICATION_WINDOW_SAMPLES as _MIN_VERIFICATION_WINDOW_SAMPLES,
    _OUTPUT_AUDIO_BITRATE as _OUTPUT_AUDIO_BITRATE,
    _OUTPUT_SAMPLE_RATE as _OUTPUT_SAMPLE_RATE,
    _PACKAGE_REL_NS as _PACKAGE_REL_NS,
    _RELATIONSHIP_TAG as _RELATIONSHIP_TAG,
    _SCRIPTS_DIR as _SCRIPTS_DIR,
    _START_TIME_TOLERANCE_MS as _START_TIME_TOLERANCE_MS,
    _VERIFICATION_SAMPLE_RATE as _VERIFICATION_SAMPLE_RATE,
    argparse as argparse,
    calibrate_video_timeline as calibrate_video_timeline,
    canonical_opc_part_path as canonical_opc_part_path,
    configure_utf8_stdio as configure_utf8_stdio,
    dataclass as dataclass,
    hashlib as hashlib,
    json as json,
    math as math,
    os as os,
    presentation_slide_members as presentation_slide_members,
    re as re,
    read_slide_animation_sequence as read_slide_animation_sequence,
    read_slide_transition_xml as read_slide_transition_xml,
    resolve_internal_opc_target as resolve_internal_opc_target,
    shutil as shutil,
    subprocess as subprocess,
    sys as sys,
    tempfile as tempfile,
    zipfile as zipfile,
)
from video_sound_mix_cues import (
    _trace_slides as _trace_slides,
    _shape_group_ids as _shape_group_ids,
    _package_part_map as _package_part_map,
    _relationships_part as _relationships_part,
    _slide_relationships as _slide_relationships,
    _extract_sound as _extract_sound,
    _require_equal as _require_equal,
    _resolve_sound_cues as _resolve_sound_cues,
    _render_stem as _render_stem,
    _mix_video as _mix_video,
    _temporary_output as _temporary_output,
    _publish_outputs as _publish_outputs,
    _cue_report as _cue_report,
)
from video_sound_mix_probe import (
    _read_json_object as _read_json_object,
    _sha256_path as _sha256_path,
    _project_input_path as _project_input_path,
    _project_output_path as _project_output_path,
    _require_tool as _require_tool,
    _run_command as _run_command,
    _probe_media as _probe_media,
    _duration_ms as _duration_ms,
    _stream_duration_ms as _stream_duration_ms,
    _stream_start_ms as _stream_start_ms,
    _frame_duration_ms as _frame_duration_ms,
    _stream_timing_signature as _stream_timing_signature,
    _audio_sample_count as _audio_sample_count,
    _target_sample_count as _target_sample_count,
    _streams as _streams,
    _stream_hash as _stream_hash,
    _maximum_volume_db as _maximum_volume_db,
    _true_peak_dbfs as _true_peak_dbfs,
    _decode_audio_f32 as _decode_audio_f32,
    _sound_mix_correlation as _sound_mix_correlation,
)


def mix_video_sounds(
    project_path: Path,
    *,
    pptx_path: Path,
    trace_path: Path,
    video_path: Path,
    audio_dir: Path,
    subtitle_dir: Path | None,
    output_path: Path,
    stem_path: Path,
    report_path: Path,
    transition_gain_db: float = _DEFAULT_TRANSITION_GAIN_DB,
    animation_gain_db: float = _DEFAULT_ANIMATION_GAIN_DB,
    limiter_dbfs: float = _DEFAULT_LIMITER_DBFS,
    force: bool = False,
) -> SoundMixResult:
    """Render and publish one calibrated animation-sound video mix."""
    project_path = project_path.resolve()
    pptx_path = pptx_path.resolve()
    trace_path = trace_path.resolve()
    video_path = video_path.resolve()
    audio_dir = audio_dir.resolve()
    subtitle_dir = subtitle_dir.resolve() if subtitle_dir is not None else None
    output_path = output_path.resolve()
    stem_path = stem_path.resolve()
    report_path = report_path.resolve()
    if not project_path.is_dir():
        raise FileNotFoundError(f"Project path does not exist: {project_path}")
    for label, path in (
        ("Final narrated PPTX", pptx_path),
        ("Final narrated conversion trace", trace_path),
        ("Raw PowerPoint video", video_path),
    ):
        if not path.is_file():
            raise FileNotFoundError(f"{label} does not exist: {path}")
    if pptx_path.suffix.lower() != ".pptx":
        raise ValueError(f"Final narrated PPTX must use .pptx: {pptx_path}")
    if video_path.suffix.lower() != ".mp4":
        raise ValueError(f"Raw PowerPoint video must use .mp4: {video_path}")
    if output_path.suffix.lower() != ".mp4":
        raise ValueError(f"Mixed video output must use .mp4: {output_path}")
    if stem_path.suffix.lower() != ".wav":
        raise ValueError(f"SFX stem output must use .wav: {stem_path}")
    if report_path.suffix.lower() != ".json":
        raise ValueError(f"Sound mix report must use .json: {report_path}")

    outputs = [output_path, stem_path, report_path]
    if len(set(outputs)) != len(outputs):
        raise ValueError("Mixed video, SFX stem, and report paths must differ")
    invalid_outputs = [
        path for path in outputs if path.exists() and not path.is_file()
    ]
    if invalid_outputs:
        raise ValueError(
            "Sound mix outputs must be regular files or unused paths: "
            + ", ".join(str(path) for path in invalid_outputs)
        )
    existing = [path for path in outputs if path.exists()]
    if existing and not force:
        raise FileExistsError(
            "Sound mix output already exists; use --force to replace it: "
            + ", ".join(str(path) for path in existing)
        )
    for label, value in (
        ("transition gain", transition_gain_db),
        ("animation gain", animation_gain_db),
    ):
        if not math.isfinite(value) or value < -60 or value > 0:
            raise ValueError(f"{label} must be finite and between -60 and 0 dB")
    if not math.isfinite(limiter_dbfs) or not -12 <= limiter_dbfs < 0:
        raise ValueError("limiter ceiling must be finite, at least -12, and below 0 dBFS")

    ffmpeg_path = _require_tool("ffmpeg")
    ffprobe_path = _require_tool("ffprobe")
    trace = _read_json_object(trace_path, "Conversion trace")
    trace_slides, slide_names = _trace_slides(trace)
    raw_probe = _probe_media(video_path, ffprobe_path)
    raw_format_duration_ms = _duration_ms(raw_probe, "raw PowerPoint video")
    raw_video_streams = _streams(raw_probe, "video")
    raw_audio_streams = _streams(raw_probe, "audio")
    if len(raw_video_streams) != 1 or len(raw_audio_streams) != 1:
        raise ValueError(
            "Raw PowerPoint video must contain one video stream and one "
            "narration audio stream"
        )
    raw_duration_ms = _stream_duration_ms(
        raw_video_streams[0],
        fallback_ms=raw_format_duration_ms,
    )
    raw_video_start_ms = _stream_start_ms(raw_video_streams[0])
    raw_audio_start_ms = _stream_start_ms(raw_audio_streams[0])
    start_delta_ms = abs(raw_video_start_ms - raw_audio_start_ms)
    frame_duration_ms = _frame_duration_ms(raw_video_streams[0])
    if (
        abs(raw_video_start_ms) > _START_TIME_TOLERANCE_MS
        or abs(raw_audio_start_ms) > _START_TIME_TOLERANCE_MS
        or start_delta_ms > _START_TIME_TOLERANCE_MS
    ):
        raise ValueError(
            "Raw PowerPoint video audio and video streams must both start at "
            f"zero: video={raw_video_start_ms}ms, audio={raw_audio_start_ms}ms"
        )
    raw_video_timing = _stream_timing_signature(raw_video_streams[0])

    calibration = calibrate_video_timeline(
        slide_names=slide_names,
        pptx_path=pptx_path,
        audio_dir=audio_dir,
        video_path=video_path,
        subtitle_dir=subtitle_dir,
    )
    protected_inputs = {
        pptx_path,
        trace_path,
        video_path,
        *(slide.audio_path.resolve() for slide in calibration.slides),
    }
    aliases = protected_inputs.intersection(outputs)
    if aliases:
        raise ValueError(
            "Sound mix outputs must not overwrite inputs: "
            + ", ".join(str(path) for path in sorted(aliases))
        )
    temporary_stem = _temporary_output(stem_path, ".wav")
    temporary_video = _temporary_output(output_path, ".mp4")
    temporary_report = _temporary_output(report_path, ".json")
    try:
        with tempfile.TemporaryDirectory(prefix="ppt-master-video-sounds-") as raw_dir:
            cues = _resolve_sound_cues(
                pptx_path=pptx_path,
                trace_slides=trace_slides,
                calibration=calibration,
                transition_gain_db=transition_gain_db,
                animation_gain_db=animation_gain_db,
                video_duration_ms=raw_duration_ms,
                temp_dir=Path(raw_dir),
                ffprobe_path=ffprobe_path,
            )
            _render_stem(
                cues=cues,
                duration_ms=raw_duration_ms,
                output_path=temporary_stem,
                ffmpeg_path=ffmpeg_path,
            )
            stem_peak_db = _maximum_volume_db(temporary_stem, ffmpeg_path)
            if stem_peak_db is None or stem_peak_db < -90:
                raise RuntimeError("Rendered SFX stem is silent")
            _mix_video(
                video_path=video_path,
                stem_path=temporary_stem,
                duration_ms=raw_duration_ms,
                limiter_dbfs=limiter_dbfs,
                output_path=temporary_video,
                ffmpeg_path=ffmpeg_path,
            )

        stem_probe = _probe_media(temporary_stem, ffprobe_path)
        mixed_probe = _probe_media(temporary_video, ffprobe_path)
        stem_duration_ms = _duration_ms(stem_probe, "SFX stem")
        mixed_duration_ms = _duration_ms(mixed_probe, "mixed video")
        mixed_video_streams = _streams(mixed_probe, "video")
        mixed_audio_streams = _streams(mixed_probe, "audio")
        if len(mixed_video_streams) != 1 or len(mixed_audio_streams) != 1:
            raise RuntimeError(
                "Mixed video must contain one copied video stream and one audio stream"
            )
        mixed_video_duration_ms = _stream_duration_ms(
            mixed_video_streams[0],
            fallback_ms=mixed_duration_ms,
        )
        mixed_audio_duration_ms = _stream_duration_ms(
            mixed_audio_streams[0],
            fallback_ms=mixed_duration_ms,
        )
        target_samples = _target_sample_count(raw_duration_ms)
        stem_samples = _audio_sample_count(_streams(stem_probe, "audio")[0])
        if stem_samples != target_samples:
            raise RuntimeError(
                "SFX stem sample count does not match the raw video target: "
                f"stem={stem_samples}, target={target_samples}"
            )
        mixed_video_timing = _stream_timing_signature(mixed_video_streams[0])
        if mixed_video_timing != raw_video_timing:
            raise RuntimeError(
                "Mixed output changed the PowerPoint video packet timeline: "
                f"raw={raw_video_timing!r}, mixed={mixed_video_timing!r}"
            )
        duration_tolerance_ms = max(
            frame_duration_ms,
            1024 * 1000 / _OUTPUT_SAMPLE_RATE,
        ) + 1
        if abs(mixed_audio_duration_ms - raw_duration_ms) > duration_tolerance_ms:
            raise RuntimeError(
                "Mixed audio duration does not match the raw video: "
                f"audio={mixed_audio_duration_ms}ms, raw={raw_duration_ms}ms"
            )

        raw_video_hash = _stream_hash(video_path, "0:v:0", ffmpeg_path)
        mixed_video_hash = _stream_hash(temporary_video, "0:v:0", ffmpeg_path)
        if raw_video_hash != mixed_video_hash:
            raise RuntimeError("Mixed output changed the PowerPoint video stream")
        raw_audio_hash = _stream_hash(video_path, "0:a:0", ffmpeg_path)
        mixed_audio_hash = _stream_hash(temporary_video, "0:a:0", ffmpeg_path)
        if raw_audio_hash == mixed_audio_hash:
            raise RuntimeError("Mixed output audio is unchanged from the raw video")
        mixed_peak_db = _maximum_volume_db(temporary_video, ffmpeg_path)
        true_peak_dbfs = _true_peak_dbfs(temporary_video, ffmpeg_path)
        if true_peak_dbfs is None or true_peak_dbfs >= 0:
            raise RuntimeError(
                "Mixed-video audio is silent or clips after encoding: "
                f"true_peak={true_peak_dbfs!r} dBFS"
            )
        verification_correlation = _sound_mix_correlation(
            raw_video_path=video_path,
            mixed_video_path=temporary_video,
            stem_path=temporary_stem,
            cues=cues,
            duration_ms=raw_duration_ms,
            ffmpeg_path=ffmpeg_path,
        )

        trace_output = trace.get("output")
        trace_output_matches_pptx = False
        if isinstance(trace_output, str) and trace_output.strip():
            trace_output_matches_pptx = Path(trace_output).expanduser().resolve() == pptx_path

        report = {
            "schema": "ppt-master.video-sound-mix-report.v1",
            "version": 1,
            "status": "passed",
            "inputs": {
                "pptx": {
                    "path": str(pptx_path),
                    "sha256": _sha256_path(pptx_path),
                },
                "conversion_trace": {
                    "path": str(trace_path),
                    "sha256": _sha256_path(trace_path),
                    "declared_output_matches_pptx": trace_output_matches_pptx,
                },
                "raw_video": {
                    "path": str(video_path),
                    "sha256": _sha256_path(video_path),
                    "duration_ms": raw_duration_ms,
                    "video_start_ms": _stream_start_ms(raw_video_streams[0]),
                    "audio_start_ms": _stream_start_ms(raw_audio_streams[0]),
                    "video_stream_sha256": raw_video_hash,
                    "video_timing": raw_video_timing,
                    "audio_stream_sha256": raw_audio_hash,
                    "video_codec": raw_video_streams[0].get("codec_name"),
                    "audio_codec": raw_audio_streams[0].get("codec_name"),
                },
            },
            "settings": {
                "transition_gain_db": transition_gain_db,
                "animation_gain_db": animation_gain_db,
                "limiter_dbfs": limiter_dbfs,
                "mix_normalize": False,
                "ducking": False,
                "background_music": False,
                "sample_rate": _OUTPUT_SAMPLE_RATE,
                "audio_bitrate": _OUTPUT_AUDIO_BITRATE,
            },
            "timeline": {
                "method": "page narration correlation against raw video audio",
                "powerpoint_timeline_ms": calibration.powerpoint_timeline_ms,
                "minimum_correlation": min(
                    slide.correlation for slide in calibration.slides
                ),
                "slides": [
                    {
                        "slide_num": index,
                        "slide_name": slide.slide_name,
                        "powerpoint_slide_start_ms": slide.powerpoint_slide_start_ms,
                        "powerpoint_narration_start_ms": (
                            slide.powerpoint_narration_start_ms
                        ),
                        "video_slide_start_ms": slide.video_slide_start_ms,
                        "video_narration_start_ms": slide.video_narration_start_ms,
                        "adjustment_ms": slide.adjustment_ms,
                        "correlation": slide.correlation,
                    }
                    for index, slide in enumerate(calibration.slides, 1)
                ],
            },
            "cues": [_cue_report(cue) for cue in cues],
            "outputs": {
                "sfx_stem": {
                    "path": str(stem_path),
                    "sha256": _sha256_path(temporary_stem),
                    "duration_ms": stem_duration_ms,
                    "sample_count": stem_samples,
                    "maximum_volume_db": stem_peak_db,
                },
                "mixed_video": {
                    "path": str(output_path),
                    "sha256": _sha256_path(temporary_video),
                    "duration_ms": mixed_duration_ms,
                    "video_duration_ms": mixed_video_duration_ms,
                    "audio_duration_ms": mixed_audio_duration_ms,
                    "video_stream_sha256": mixed_video_hash,
                    "video_timing": mixed_video_timing,
                    "audio_stream_sha256": mixed_audio_hash,
                    "video_codec": mixed_video_streams[0].get("codec_name"),
                    "audio_codec": mixed_audio_streams[0].get("codec_name"),
                    "maximum_volume_db": mixed_peak_db,
                    "true_peak_dbfs": true_peak_dbfs,
                },
            },
            "validation": {
                "trace_matches_pptx_motion": True,
                "cue_count": len(cues),
                "stem_non_silent": True,
                "mixed_audio_present": True,
                "video_stream_preserved": True,
                "duration_delta_ms": mixed_video_duration_ms - raw_duration_ms,
                "video_timeline_preserved": True,
                "limiter_applied": True,
                "sound_mix_correlation": verification_correlation,
                "minimum_sound_mix_correlation": _MIN_SOUND_MIX_CORRELATION,
            },
        }
        temporary_report.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        _publish_outputs(
            [
                (temporary_stem, stem_path),
                (temporary_video, output_path),
                (temporary_report, report_path),
            ]
        )
    finally:
        for temporary in (temporary_stem, temporary_video, temporary_report):
            temporary.unlink(missing_ok=True)

    return SoundMixResult(
        output_path=output_path,
        stem_path=stem_path,
        report_path=report_path,
        cue_count=len(cues),
        verification_correlation=verification_correlation,
        true_peak_dbfs=true_peak_dbfs,
    )



def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Mix PPTX transition/object-animation sounds into a "
            "PowerPoint-exported narrated video."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("project_path", help="PPT Master project directory")
    parser.add_argument(
        "--pptx",
        required=True,
        help="Final narrated PPTX; relative paths resolve under the project",
    )
    parser.add_argument(
        "--trace",
        required=True,
        help="Final narrated conversion trace; relative paths resolve under the project",
    )
    parser.add_argument(
        "--video",
        required=True,
        help="Raw PowerPoint-exported video; relative paths resolve under the project",
    )
    parser.add_argument(
        "--audio-dir",
        default=None,
        help="Page narration audio directory; default: <project>/audio",
    )
    parser.add_argument(
        "--subtitle-dir",
        default=None,
        help=(
            "Optional page-local SRT directory used to refine calibration; "
            "default: <project>/audio"
        ),
    )
    parser.add_argument(
        "-o",
        "--output",
        default=None,
        help="Final mixed MP4; default: beside raw video as *_mixed.mp4",
    )
    parser.add_argument(
        "--stem-output",
        default=None,
        help="Independent SFX WAV; default: beside raw video as *_sfx.wav",
    )
    parser.add_argument(
        "--report-output",
        default=None,
        help="Sound mix JSON receipt; default: beside raw video as *_sound_mix.json",
    )
    parser.add_argument(
        "--transition-gain-db",
        type=float,
        default=_DEFAULT_TRANSITION_GAIN_DB,
        help="Transition cue gain in dB (default: -9.1, about 35%%)",
    )
    parser.add_argument(
        "--animation-gain-db",
        type=float,
        default=_DEFAULT_ANIMATION_GAIN_DB,
        help="Object-animation cue gain in dB (default: -12, about 25%%)",
    )
    parser.add_argument(
        "--limiter-dbfs",
        type=float,
        default=_DEFAULT_LIMITER_DBFS,
        help="Final peak limiter ceiling in dBFS (default: -1)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Replace existing mixed video, stem, and report outputs",
    )
    return parser



def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    project_path = Path(args.project_path).expanduser().resolve()
    if not project_path.is_dir():
        parser.error(f"Project path does not exist: {project_path}")

    pptx_path = _project_input_path(project_path, args.pptx)
    trace_path = _project_input_path(project_path, args.trace)
    video_path = _project_input_path(project_path, args.video)
    audio_dir = _project_output_path(
        project_path,
        args.audio_dir,
        Path("audio"),
    )
    subtitle_dir = _project_output_path(
        project_path,
        args.subtitle_dir,
        Path("audio"),
    )
    default_output = video_path.with_name(f"{video_path.stem}_mixed.mp4")
    default_stem = video_path.with_name(f"{video_path.stem}_sfx.wav")
    default_report = video_path.with_name(f"{video_path.stem}_sound_mix.json")
    output_path = _project_output_path(project_path, args.output, default_output)
    stem_path = _project_output_path(
        project_path,
        args.stem_output,
        default_stem,
    )
    report_path = _project_output_path(
        project_path,
        args.report_output,
        default_report,
    )

    try:
        result = mix_video_sounds(
            project_path,
            pptx_path=pptx_path,
            trace_path=trace_path,
            video_path=video_path,
            audio_dir=audio_dir,
            subtitle_dir=subtitle_dir,
            output_path=output_path,
            stem_path=stem_path,
            report_path=report_path,
            transition_gain_db=args.transition_gain_db,
            animation_gain_db=args.animation_gain_db,
            limiter_dbfs=args.limiter_dbfs,
            force=args.force,
        )
    except (FileNotFoundError, FileExistsError, OSError, RuntimeError, ValueError) as exc:
        print(f"Video sound mix failed: {exc}", file=sys.stderr)
        return 1

    print(
        f"[POSTFLIGHT] status=passed cues={result.cue_count} "
        "video_stream=preserved audio_mix=verified "
        f"correlation={result.verification_correlation:.3f} "
        f"true_peak={result.true_peak_dbfs:.1f}dBFS"
    )
    print(f"[Done] Mixed video: {result.output_path}")
    print(f"[Done] SFX stem: {result.stem_path}")
    print(f"[REPORT] Video sound mix: {result.report_path}")
    return 0



if __name__ == "__main__":
    raise SystemExit(main())
