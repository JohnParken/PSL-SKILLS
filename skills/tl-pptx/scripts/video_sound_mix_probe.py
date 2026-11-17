"""video_sound_mix probe responsibilities split from pinned upstream."""
from __future__ import annotations

from video_sound_mix_base import (
    Any as Any,
    Fraction as Fraction,
    Path as Path,
    SoundCue as SoundCue,
    _MIN_SOUND_MIX_CORRELATION as _MIN_SOUND_MIX_CORRELATION,
    _MIN_VERIFICATION_WINDOW_SAMPLES as _MIN_VERIFICATION_WINDOW_SAMPLES,
    _OUTPUT_SAMPLE_RATE as _OUTPUT_SAMPLE_RATE,
    _VERIFICATION_SAMPLE_RATE as _VERIFICATION_SAMPLE_RATE,
    hashlib as hashlib,
    json as json,
    math as math,
    re as re,
    shutil as shutil,
    subprocess as subprocess,
)


def _read_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} is not valid JSON: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must contain one JSON object: {path}")
    return value



def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()



def _project_input_path(project_path: Path, value: str) -> Path:
    path = Path(value).expanduser()
    return (path if path.is_absolute() else project_path / path).resolve()



def _project_output_path(
    project_path: Path,
    value: str | None,
    default: Path,
) -> Path:
    path = Path(value).expanduser() if value else default
    return (path if path.is_absolute() else project_path / path).resolve()



def _require_tool(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise RuntimeError(
            f"Video sound mixing requires {name} on PATH. Install ffmpeg "
            "and make both ffmpeg and ffprobe available."
        )
    return path



def _run_command(command: list[str], label: str) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        capture_output=True,
        check=False,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        details = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"{label} failed:\n{details}")
    return result



def _probe_media(path: Path, ffprobe_path: str) -> dict[str, Any]:
    result = _run_command(
        [
            ffprobe_path,
            "-v",
            "error",
            "-show_entries",
            (
                "format=duration,start_time:"
                "stream=index,codec_type,codec_name,time_base,start_pts,"
                "start_time,duration_ts,duration,sample_rate,channels,"
                "width,height,avg_frame_rate"
            ),
            "-of",
            "json",
            str(path),
        ],
        f"ffprobe inspection for {path}",
    )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"ffprobe returned invalid JSON for {path}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"ffprobe returned an invalid payload for {path}")
    return payload



def _duration_ms(probe: dict[str, Any], label: str) -> int:
    format_value = probe.get("format")
    raw_duration = (
        format_value.get("duration")
        if isinstance(format_value, dict)
        else None
    )
    try:
        duration = float(raw_duration)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Unable to read {label} duration with ffprobe") from exc
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError(f"{label} duration must be positive and finite")
    return int(round(duration * 1000))



def _stream_duration_ms(
    stream: dict[str, Any],
    *,
    fallback_ms: int,
) -> int:
    try:
        duration = float(stream.get("duration"))
    except (TypeError, ValueError):
        return fallback_ms
    if not math.isfinite(duration) or duration <= 0:
        return fallback_ms
    return int(round(duration * 1000))



def _stream_start_ms(stream: dict[str, Any]) -> int:
    try:
        start = float(stream.get("start_time", 0))
    except (TypeError, ValueError):
        return 0
    if not math.isfinite(start):
        return 0
    return int(round(start * 1000))



def _frame_duration_ms(stream: dict[str, Any]) -> float:
    value = stream.get("avg_frame_rate")
    if not isinstance(value, str) or "/" not in value:
        return 1000 / 30
    numerator_text, denominator_text = value.split("/", 1)
    try:
        numerator = float(numerator_text)
        denominator = float(denominator_text)
    except ValueError:
        return 1000 / 30
    if numerator <= 0 or denominator <= 0:
        return 1000 / 30
    return 1000 * denominator / numerator



def _stream_timing_signature(stream: dict[str, Any]) -> dict[str, object]:
    """Return the packet-clock fields that must survive video stream copy."""
    signature: dict[str, object] = {}
    for key in ("time_base", "start_pts", "duration_ts"):
        value = stream.get(key)
        if value is None:
            raise ValueError(f"Video stream is missing {key}")
        signature[key] = value
    return signature



def _audio_sample_count(stream: dict[str, Any]) -> int:
    """Resolve an audio stream duration to exact samples from its time base."""
    try:
        sample_rate = int(stream.get("sample_rate"))
        duration_ts = int(stream.get("duration_ts"))
        time_base = Fraction(str(stream.get("time_base")))
    except (TypeError, ValueError, ZeroDivisionError) as exc:
        raise ValueError("Unable to read exact audio sample count") from exc
    samples = duration_ts * time_base * sample_rate
    if samples.denominator != 1:
        raise ValueError(
            "Audio duration does not resolve to a whole number of samples"
        )
    return samples.numerator



def _target_sample_count(duration_ms: int) -> int:
    return int(round(duration_ms * _OUTPUT_SAMPLE_RATE / 1000))



def _streams(probe: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    raw_streams = probe.get("streams")
    if not isinstance(raw_streams, list):
        return []
    return [
        stream
        for stream in raw_streams
        if isinstance(stream, dict) and stream.get("codec_type") == kind
    ]



def _stream_hash(path: Path, selector: str, ffmpeg_path: str) -> str:
    result = _run_command(
        [
            ffmpeg_path,
            "-v",
            "error",
            "-i",
            str(path),
            "-map",
            selector,
            "-c",
            "copy",
            "-f",
            "hash",
            "-hash",
            "sha256",
            "-",
        ],
        f"stream hash for {path}",
    )
    match = re.search(r"SHA256=([0-9a-fA-F]{64})", result.stdout)
    if match is None:
        raise RuntimeError(f"Unable to parse stream hash for {path}")
    return match.group(1).lower()



def _maximum_volume_db(path: Path, ffmpeg_path: str) -> float | None:
    result = subprocess.run(
        [
            ffmpeg_path,
            "-nostdin",
            "-hide_banner",
            "-i",
            str(path),
            "-map",
            "0:a:0",
            "-af",
            "volumedetect",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        check=False,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Volume inspection failed for {path}:\n{result.stderr.strip()}"
        )
    match = re.search(r"max_volume:\s*(-?inf|[-+]?\d+(?:\.\d+)?)\s*dB", result.stderr)
    if match is None:
        raise RuntimeError(f"Unable to read maximum volume for {path}")
    if match.group(1) == "-inf":
        return None
    return float(match.group(1))



def _true_peak_dbfs(path: Path, ffmpeg_path: str) -> float | None:
    result = subprocess.run(
        [
            ffmpeg_path,
            "-nostdin",
            "-hide_banner",
            "-nostats",
            "-i",
            str(path),
            "-map",
            "0:a:0",
            "-af",
            "ebur128=peak=true",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        check=False,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"True-peak inspection failed for {path}:\n{result.stderr.strip()}"
        )
    matches = re.findall(
        r"Peak:\s*(-?inf|[-+]?\d+(?:\.\d+)?)\s*dBFS",
        result.stderr,
    )
    if not matches:
        raise RuntimeError(f"Unable to read true peak for {path}")
    if matches[-1] == "-inf":
        return None
    return float(matches[-1])



def _decode_audio_f32(path: Path, ffmpeg_path: str) -> Any:
    try:
        import numpy as np
    except ImportError as exc:
        raise RuntimeError(
            "Video sound verification requires numpy. Install it with: "
            "python3 -m pip install numpy"
        ) from exc
    result = subprocess.run(
        [
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
            str(_VERIFICATION_SAMPLE_RATE),
            "-f",
            "f32le",
            "-",
        ],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        details = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"Unable to decode audio for verification: {path}\n{details}")
    return np.frombuffer(result.stdout, dtype="<f4").astype(np.float64)



def _sound_mix_correlation(
    *,
    raw_video_path: Path,
    mixed_video_path: Path,
    stem_path: Path,
    cues: list[SoundCue],
    duration_ms: int,
    ffmpeg_path: str,
) -> float:
    """Correlate the added final-audio component with the rendered SFX stem."""
    try:
        import numpy as np
    except ImportError as exc:
        raise RuntimeError(
            "Video sound verification requires numpy. Install it with: "
            "python3 -m pip install numpy"
        ) from exc

    target_samples = int(round(duration_ms * _VERIFICATION_SAMPLE_RATE / 1000))
    decoded = [
        _decode_audio_f32(path, ffmpeg_path)
        for path in (raw_video_path, mixed_video_path, stem_path)
    ]
    arrays: list[Any] = []
    for samples in decoded:
        if len(samples) < target_samples:
            samples = np.pad(samples, (0, target_samples - len(samples)))
        arrays.append(samples[:target_samples])
    raw_audio, mixed_audio, stem_audio = arrays

    cue_mask = np.zeros(target_samples, dtype=bool)
    for cue in cues:
        start = int(round(cue.video_time_ms * _VERIFICATION_SAMPLE_RATE / 1000))
        end = int(
            round(
                (cue.video_time_ms + cue.source_duration_ms)
                * _VERIFICATION_SAMPLE_RATE
                / 1000
            )
        )
        cue_mask[max(0, start):min(target_samples, end)] = True
    cue_indices = np.flatnonzero(cue_mask)
    if len(cue_indices) < _MIN_VERIFICATION_WINDOW_SAMPLES:
        raise RuntimeError("SFX cue windows are too short for mix verification")

    background = ~cue_mask
    raw_background = raw_audio[background]
    mixed_background = mixed_audio[background]
    denominator = float(np.dot(raw_background, raw_background))
    narration_scale = (
        float(np.dot(mixed_background, raw_background)) / denominator
        if denominator > 1e-12
        else 1.0
    )
    added_audio = mixed_audio - narration_scale * raw_audio

    best_correlation = -1.0
    maximum_lag = max(1, _VERIFICATION_SAMPLE_RATE // 200)
    for lag in range(-maximum_lag, maximum_lag + 1):
        shifted_indices = cue_indices + lag
        valid = (shifted_indices >= 0) & (shifted_indices < target_samples)
        if not valid.any():
            continue
        observed = added_audio[shifted_indices[valid]]
        expected = stem_audio[cue_indices[valid]]
        observed = observed - observed.mean()
        expected = expected - expected.mean()
        norm = float(np.linalg.norm(observed) * np.linalg.norm(expected))
        if norm <= 1e-12:
            continue
        correlation = float(np.dot(observed, expected) / norm)
        best_correlation = max(best_correlation, correlation)
    if best_correlation < _MIN_SOUND_MIX_CORRELATION:
        raise RuntimeError(
            "Mixed-video audio does not reliably contain the rendered SFX stem: "
            f"correlation={best_correlation:.3f}, "
            f"required>={_MIN_SOUND_MIX_CORRELATION:.2f}"
        )
    return best_correlation

