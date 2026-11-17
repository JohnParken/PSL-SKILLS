"""Local narration container checks: no playlists or external media references."""
from __future__ import annotations

from pathlib import Path

from tl_guard import ContractError

AUDIO_FORMATS = {'.wav': 'wav', '.mp3': 'mp3', '.m4a': 'mov'}
CONTAINERS = {b'moov', b'trak', b'mdia', b'minf', b'dinf'}


def _mp4_boxes(payload, start, end, depth=0):
    if depth > 16:
        raise ContractError('Audio container nesting budget exceeded')
    position = start
    while position < end:
        if end - position < 8:
            raise ContractError('Truncated audio container box')
        size = int.from_bytes(payload[position:position + 4], 'big')
        kind = payload[position + 4:position + 8]
        header = 8
        if size == 1:
            if end - position < 16:
                raise ContractError('Truncated extended audio container box')
            size = int.from_bytes(payload[position + 8:position + 16], 'big')
            header = 16
        elif size == 0:
            size = end - position
        if size < header or position + size > end:
            raise ContractError('Invalid audio container box size')
        content = position + header
        stop = position + size
        if kind in CONTAINERS:
            _mp4_boxes(payload, content, stop, depth + 1)
        elif kind == b'dref':
            if stop - content < 8:
                raise ContractError('Invalid media data-reference table')
            count = int.from_bytes(payload[content + 4:content + 8], 'big')
            cursor = content + 8
            for _ in range(count):
                if stop - cursor < 12:
                    raise ContractError('Truncated media data reference')
                length = int.from_bytes(payload[cursor:cursor + 4], 'big')
                flags = int.from_bytes(payload[cursor + 9:cursor + 12], 'big')
                reference_kind = payload[cursor + 4:cursor + 8]
                if length < 12 or cursor + length > stop or reference_kind not in {b'url ', b'urn '} or not flags & 1:
                    raise ContractError('External media data references are forbidden')
                cursor += length
            if cursor != stop:
                raise ContractError('Invalid media data-reference count')
        position = stop


def validate_audio(payload, suffix):
    """Accept only genuine local WAV/MP3/M4A headers before forced demuxing."""
    suffix = suffix.lower()
    if suffix == '.wav':
        if len(payload) < 12 or payload[:4] != b'RIFF' or payload[8:12] != b'WAVE':
            raise ContractError('WAV input is not a RIFF/WAVE container')
        position = 12
        while position + 8 <= len(payload):
            kind = payload[position:position + 4]
            size = int.from_bytes(payload[position + 4:position + 8], 'little')
            content = position + 8
            if size > len(payload) - content:
                raise ContractError('Truncated WAV chunk')
            if kind == b'fmt ':
                if size < 16:
                    raise ContractError('Invalid WAV format chunk')
                channels = int.from_bytes(payload[content + 2:content + 4], 'little')
                rate = int.from_bytes(payload[content + 4:content + 8], 'little')
                if not 1 <= channels <= 32 or not 1 <= rate <= 768000:
                    raise ContractError('WAV stream resource budget exceeded')
            position = content + size + size % 2
    elif suffix == '.mp3':
        if not (payload.startswith(b'ID3') or len(payload) >= 2 and payload[0] == 255 and payload[1] & 224 == 224):
            raise ContractError('MP3 input has no MPEG audio or ID3 signature')
    elif suffix == '.m4a':
        if len(payload) < 12 or payload[4:8] != b'ftyp':
            raise ContractError('M4A input has no ISO media signature')
        _mp4_boxes(payload, 0, len(payload))
    else:
        raise ContractError('Unsupported narration audio container')
    return AUDIO_FORMATS[suffix]


def probe_options(path):
    """Constrain ffprobe to a known demuxer and local media, with no playlists."""
    suffix = Path(path).suffix.lower()
    if suffix not in AUDIO_FORMATS:
        raise ContractError('Unsupported narration audio container')
    result = ['-protocol_whitelist', 'file,pipe', '-format_whitelist', AUDIO_FORMATS[suffix]]
    if suffix == '.m4a':
        # FFmpeg MOV demuxer: explicitly disable external track references.
        result += ['-enable_drefs', '0', '-use_absolute_path', '0']
    return result + ['-f', AUDIO_FORMATS[suffix]]
