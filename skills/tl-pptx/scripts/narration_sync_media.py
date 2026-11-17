"""narration_sync media responsibilities split from pinned upstream."""
from __future__ import annotations

from narration_sync_base import (
    Any as Any,
    ET as ET,
    Path as Path,
    PowerPointTiming as PowerPointTiming,
    SubtitleCue as SubtitleCue,
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
    posixpath as posixpath,
    read_narration_start_delay_xml as read_narration_start_delay_xml,
    read_slide_transition_xml as read_slide_transition_xml,
    shutil as shutil,
    subprocess as subprocess,
    zipfile as zipfile,
)
from narration_sync_helpers import (
    _parse_srt as _parse_srt,
    _find_audio as _find_audio,
    _seconds_from_ms as _seconds_from_ms,
)


def presentation_slide_members(package: zipfile.ZipFile) -> list[str]:
    """Return slide package members in presentation order."""
    try:
        presentation_root = ET.fromstring(package.read("ppt/presentation.xml"))
        relationships_root = ET.fromstring(
            package.read("ppt/_rels/presentation.xml.rels")
        )
    except KeyError as exc:
        raise ValueError(
            f"Narrated PPTX is missing presentation ordering data: {exc}"
        ) from exc

    relationship_targets: dict[str, str] = {}
    for relationship in relationships_root.iter(f"{{{_REL_NS}}}Relationship"):
        relationship_id = relationship.get("Id")
        target = relationship.get("Target")
        if (
            relationship_id
            and target
            and relationship.get("TargetMode", "Internal") != "External"
        ):
            relationship_targets[relationship_id] = target.replace("\\", "/")

    slide_list = presentation_root.find(f"{{{_PML_NS}}}sldIdLst")
    if slide_list is None:
        raise ValueError("Narrated PPTX presentation has no slide order")

    members: list[str] = []
    for slide_id in slide_list.findall(f"{{{_PML_NS}}}sldId"):
        relationship_id = slide_id.get(f"{{{_DOC_REL_NS}}}id")
        target = relationship_targets.get(relationship_id or "")
        if target is None:
            raise ValueError(
                "Narrated PPTX slide order references a missing relationship: "
                f"{relationship_id!r}"
            )
        if target.startswith("/"):
            member = posixpath.normpath(target.lstrip("/"))
        else:
            member = posixpath.normpath(posixpath.join("ppt", target))
        if member not in package.namelist():
            raise ValueError(
                f"Narrated PPTX slide relationship target is missing: {member}"
            )
        members.append(member)
    return members



def _read_powerpoint_timings(
    pptx_path: Path,
    slide_count: int,
) -> list[PowerPointTiming]:
    timings: list[PowerPointTiming] = []
    with zipfile.ZipFile(pptx_path) as package:
        slide_members = presentation_slide_members(package)
        if len(slide_members) != slide_count:
            raise ValueError(
                f"Narrated PPTX has {len(slide_members)} slides, "
                f"but the project has {slide_count}"
            )
        for slide_index, member in enumerate(slide_members, 1):
            slide_xml = package.read(member)
            summary = read_slide_transition_xml(slide_xml)
            if summary.logical_count != 1:
                raise ValueError(
                    f"Narrated PPTX slide {slide_index} has "
                    f"{summary.logical_count} logical transition carriers"
                )
            advance_ms = summary.advance_after_ms
            if advance_ms is None:
                raise ValueError(
                    f"Narrated PPTX slide {slide_index} has no recorded advance time"
                )
            transition_ms = summary.duration_ms or 0
            narration_delay_ms = read_narration_start_delay_xml(
                slide_xml.decode("utf-8")
            )
            if (
                advance_ms <= 0
                or transition_ms < 0
                or narration_delay_ms < 0
                or narration_delay_ms >= advance_ms
            ):
                raise ValueError(
                    f"Narrated PPTX slide {slide_index} has invalid timing values"
                )
            timings.append(
                PowerPointTiming(
                    transition_ms=transition_ms,
                    narration_delay_ms=narration_delay_ms,
                    advance_ms=advance_ms,
                )
            )
    return timings



def _powerpoint_audio_starts(
    timings: list[PowerPointTiming],
) -> tuple[list[int], int]:
    """Return theoretical narration starts and the complete PPTX timeline."""
    audio_starts: list[int] = []
    timeline_ms = 0
    for timing in timings:
        slide_start_ms = timeline_ms
        audio_start_ms = (
            slide_start_ms
            + timing.transition_ms
            + timing.narration_delay_ms
        )
        audio_starts.append(audio_start_ms)
        timeline_ms = (
            slide_start_ms + timing.transition_ms + timing.advance_ms
        )
    return audio_starts, timeline_ms



def _require_numpy() -> Any:
    try:
        import numpy as np
    except ImportError as exc:
        raise RuntimeError(
            "Exported-video timeline calibration requires numpy. "
            "Install it with: python3 -m pip install numpy"
        ) from exc
    return np



def _decode_audio_envelopes(path: Path, ffmpeg_path: str) -> tuple[Any, Any]:
    """Decode the first audio stream and return 1 ms and 10 ms RMS envelopes."""
    np = _require_numpy()
    command = [
        ffmpeg_path,
        "-v",
        "error",
        "-i",
        str(path),
        "-map",
        "0:a:0",
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(_ALIGNMENT_SAMPLE_RATE),
        "-f",
        "s16le",
        "-",
    ]
    result = subprocess.run(command, capture_output=True, check=False)
    if result.returncode != 0:
        details = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"Unable to decode audio with ffmpeg: {path}\n{details}")

    samples = np.frombuffer(result.stdout, dtype="<i2")
    samples_per_frame = _ALIGNMENT_SAMPLE_RATE // _ALIGNMENT_FINE_HZ
    usable_sample_count = len(samples) // samples_per_frame * samples_per_frame
    if usable_sample_count < samples_per_frame * 500:
        raise ValueError(f"Audio is too short for subtitle calibration: {path}")

    frames = (
        samples[:usable_sample_count]
        .astype(np.float32)
        .reshape(-1, samples_per_frame)
    )
    fine = np.sqrt(np.mean(frames * frames, axis=1) + 1e-12)
    fine = np.log1p(120 * fine)
    smooth_width = max(1, _ALIGNMENT_FINE_HZ // 200)
    fine = np.convolve(
        fine,
        np.ones(smooth_width, dtype=np.float64) / smooth_width,
        mode="same",
    ).astype(np.float64)

    coarse_count = len(fine) // _ALIGNMENT_COARSE_FACTOR
    coarse = fine[
        :coarse_count * _ALIGNMENT_COARSE_FACTOR
    ].reshape(coarse_count, _ALIGNMENT_COARSE_FACTOR).mean(axis=1)
    return fine, coarse



def _best_correlation(search: Any, template: Any) -> tuple[int, float]:
    """Return the best normalized-correlation index for one search window."""
    np = _require_numpy()
    if len(search) < len(template):
        raise ValueError("Video alignment search window is shorter than its template")

    centered_template = template - template.mean()
    template_norm = float(np.linalg.norm(centered_template))
    if template_norm <= 1e-9:
        raise ValueError("Narration audio has no usable variation for video alignment")

    numerators = np.correlate(search, centered_template, mode="valid")
    prefix = np.concatenate(([0.0], np.cumsum(search, dtype=np.float64)))
    squared_prefix = np.concatenate(
        ([0.0], np.cumsum(search * search, dtype=np.float64))
    )
    width = len(template)
    window_sums = prefix[width:] - prefix[:-width]
    window_squared_sums = squared_prefix[width:] - squared_prefix[:-width]
    window_variances = np.maximum(
        window_squared_sums - (window_sums * window_sums / width),
        1e-18,
    )
    scores = numerators / (np.sqrt(window_variances) * template_norm)
    best_index = int(np.argmax(scores))
    return best_index, float(scores[best_index])



def _alignment_template_bounds(
    fine_envelope: Any,
    cues: list[SubtitleCue] | None,
) -> tuple[int, int]:
    duration_ms = len(fine_envelope)
    if cues:
        start_ms = min(cues[0].start_ms, max(0, duration_ms - 500))
        cue_end_ms = min(cues[-1].end_ms, duration_ms)
    else:
        start_ms = 0
        cue_end_ms = duration_ms
    end_ms = min(duration_ms, start_ms + _ALIGNMENT_TEMPLATE_MAX_MS)
    end_ms = min(end_ms, max(start_ms + 1000, cue_end_ms))
    if end_ms - start_ms < 500:
        raise ValueError("Narration cue range is too short for video alignment")
    return start_ms, end_ms



def _locate_audio_start(
    video_fine: Any,
    video_coarse: Any,
    audio_fine: Any,
    audio_coarse: Any,
    cues: list[SubtitleCue] | None,
    predicted_start_ms: int,
) -> tuple[int, float]:
    """Locate one page narration near its predicted exported-video position."""
    start_ms, end_ms = _alignment_template_bounds(audio_fine, cues)
    coarse_start = start_ms // _ALIGNMENT_COARSE_FACTOR
    coarse_end = max(
        coarse_start + 50,
        end_ms // _ALIGNMENT_COARSE_FACTOR,
    )
    coarse_template = audio_coarse[coarse_start:coarse_end]
    predicted_coarse = predicted_start_ms // _ALIGNMENT_COARSE_FACTOR
    coarse_window = _ALIGNMENT_SEARCH_WINDOW_MS // _ALIGNMENT_COARSE_FACTOR
    search_start = max(
        0,
        predicted_coarse + coarse_start - coarse_window,
    )
    search_end = min(
        len(video_coarse),
        predicted_coarse + coarse_start + coarse_window + len(coarse_template),
    )
    coarse_index, _coarse_score = _best_correlation(
        video_coarse[search_start:search_end],
        coarse_template,
    )
    coarse_audio_start_ms = (
        search_start + coarse_index - coarse_start
    ) * _ALIGNMENT_COARSE_FACTOR

    fine_template = audio_fine[start_ms:end_ms]
    fine_search_start = max(
        0,
        coarse_audio_start_ms + start_ms - _ALIGNMENT_REFINE_WINDOW_MS,
    )
    fine_search_end = min(
        len(video_fine),
        coarse_audio_start_ms
        + start_ms
        + _ALIGNMENT_REFINE_WINDOW_MS
        + len(fine_template),
    )
    fine_index, fine_score = _best_correlation(
        video_fine[fine_search_start:fine_search_end],
        fine_template,
    )
    audio_start_ms = fine_search_start + fine_index - start_ms
    return audio_start_ms, fine_score



def _align_audio_starts_to_video(
    *,
    slide_names: list[str],
    local_cues: dict[str, list[SubtitleCue] | None],
    theoretical_starts: list[int],
    audio_dir: Path,
    video_path: Path,
) -> tuple[list[int], list[float], list[Path]]:
    """Align every page narration to the audio track of an exported video."""
    if not video_path.is_file():
        raise FileNotFoundError(f"Exported video does not exist: {video_path}")
    ffmpeg_path = shutil.which("ffmpeg")
    if ffmpeg_path is None:
        raise RuntimeError(
            "Exported-video timeline calibration requires ffmpeg. "
            "Install ffmpeg and make it available on PATH."
        )

    video_fine, video_coarse = _decode_audio_envelopes(video_path, ffmpeg_path)
    aligned_starts: list[int] = []
    correlations: list[float] = []
    audio_paths: list[Path] = []

    for index, slide_name in enumerate(slide_names):
        audio_path = _find_audio(audio_dir, slide_name)
        audio_paths.append(audio_path)
        audio_fine, audio_coarse = _decode_audio_envelopes(audio_path, ffmpeg_path)
        slide_cues = local_cues.get(slide_name)
        if (
            slide_cues
            and slide_cues[-1].end_ms
            > len(audio_fine) + _ALIGNMENT_END_TOLERANCE_MS
        ):
            raise ValueError(
                f"{slide_name}.srt ends after its narration audio: "
                f"cue end={_seconds_from_ms(slide_cues[-1].end_ms):.3f}s, "
                f"decoded audio={_seconds_from_ms(len(audio_fine)):.3f}s"
            )
        if index == 0:
            predicted_start_ms = theoretical_starts[index]
        else:
            predicted_start_ms = (
                aligned_starts[index - 1]
                + theoretical_starts[index]
                - theoretical_starts[index - 1]
            )
        aligned_start_ms, correlation = _locate_audio_start(
            video_fine,
            video_coarse,
            audio_fine,
            audio_coarse,
            slide_cues,
            predicted_start_ms,
        )
        if correlation < _ALIGNMENT_MIN_CORRELATION:
            raise ValueError(
                f"Exported-video audio match is unreliable for {slide_name}: "
                f"correlation={correlation:.3f}, "
                f"required>={_ALIGNMENT_MIN_CORRELATION:.2f}"
            )
        if aligned_starts and aligned_start_ms <= aligned_starts[-1]:
            raise ValueError(
                f"Exported-video audio order is invalid at slide {slide_name}"
            )
        final_audio_end_ms = aligned_start_ms + len(audio_fine)
        if (
            final_audio_end_ms
            > len(video_fine) + _ALIGNMENT_END_TOLERANCE_MS
        ):
            raise ValueError(
                f"Exported video ends before the narration on slide {slide_name}: "
                f"audio end={_seconds_from_ms(final_audio_end_ms):.3f}s, "
                f"decoded video audio={_seconds_from_ms(len(video_fine)):.3f}s"
            )
        aligned_starts.append(aligned_start_ms)
        correlations.append(correlation)

    return aligned_starts, correlations, audio_paths



def calibrate_video_timeline(
    *,
    slide_names: list[str],
    pptx_path: Path,
    audio_dir: Path,
    video_path: Path,
    subtitle_dir: Path | None = None,
) -> VideoTimelineCalibration:
    """Calibrate PPTX slide starts against narration in an exported video.

    Page-local SRT improves the correlation template when available. Audio-only
    narration remains supported by matching each complete page track.
    """
    if not slide_names:
        raise ValueError("Video timeline calibration requires at least one slide")
    if len(set(slide_names)) != len(slide_names):
        raise ValueError("Video timeline calibration slide names must be unique")

    timings = _read_powerpoint_timings(pptx_path, len(slide_names))
    theoretical_starts, timeline_ms = _powerpoint_audio_starts(timings)
    local_cues: dict[str, list[SubtitleCue] | None] = {}
    for slide_name in slide_names:
        subtitle_path = (
            subtitle_dir / f"{slide_name}.srt"
            if subtitle_dir is not None
            else None
        )
        local_cues[slide_name] = (
            _parse_srt(subtitle_path)
            if subtitle_path is not None and subtitle_path.is_file()
            else None
        )

    aligned_starts, correlations, audio_paths = _align_audio_starts_to_video(
        slide_names=slide_names,
        local_cues=local_cues,
        theoretical_starts=theoretical_starts,
        audio_dir=audio_dir,
        video_path=video_path,
    )

    slides: list[VideoSlideTiming] = []
    powerpoint_slide_start_ms = 0
    previous_video_slide_start_ms = -1
    for (
        slide_name,
        timing,
        powerpoint_narration_start_ms,
        video_narration_start_ms,
        correlation,
        audio_path,
    ) in zip(
        slide_names,
        timings,
        theoretical_starts,
        aligned_starts,
        correlations,
        audio_paths,
    ):
        raw_video_slide_start_ms = (
            video_narration_start_ms
            - timing.transition_ms
            - timing.narration_delay_ms
        )
        if raw_video_slide_start_ms < -_ALIGNMENT_END_TOLERANCE_MS:
            raise ValueError(
                f"Exported-video calibration places slide {slide_name} before "
                f"the video start: {raw_video_slide_start_ms}ms"
            )
        video_slide_start_ms = max(0, raw_video_slide_start_ms)
        if video_slide_start_ms <= previous_video_slide_start_ms:
            raise ValueError(
                f"Exported-video slide order is invalid at {slide_name}"
            )
        slides.append(
            VideoSlideTiming(
                slide_name=slide_name,
                transition_ms=timing.transition_ms,
                narration_delay_ms=timing.narration_delay_ms,
                advance_ms=timing.advance_ms,
                powerpoint_slide_start_ms=powerpoint_slide_start_ms,
                powerpoint_narration_start_ms=powerpoint_narration_start_ms,
                video_slide_start_ms=video_slide_start_ms,
                video_narration_start_ms=video_narration_start_ms,
                adjustment_ms=(
                    video_narration_start_ms - powerpoint_narration_start_ms
                ),
                correlation=correlation,
                audio_path=audio_path,
            )
        )
        previous_video_slide_start_ms = video_slide_start_ms
        powerpoint_slide_start_ms += timing.transition_ms + timing.advance_ms

    return VideoTimelineCalibration(
        slides=tuple(slides),
        powerpoint_timeline_ms=timeline_ms,
    )

