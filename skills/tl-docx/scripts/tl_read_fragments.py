"""Opt-in lossless JSON-record fragments for byte-budgeted output, not parsing."""

from hashlib import sha256
import json


def _encode(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')


def fragment_records(records, metadata, budget):
    """Preserve records or emit ordered fragments that reassemble to original JSON.

    Offsets count Unicode characters in json_fragment, not original document
    characters. Parsing is still eager; this only bounds individual output units.
    """
    stable_metadata = {key: value for key, value in metadata.items() if key != 'cache'}
    frame = {**stable_metadata, 'start': 10**12, 'end': 10**12, 'total': 10**12, 'has_more': True,
             'next_cursor': 'x' * 256, 'records': []}
    allowance = budget - len(_encode(frame)) - 1024
    if allowance <= 0:
        raise ValueError('Byte budget cannot hold response metadata; increase --max-output-bytes')
    result = []
    fragmented = 0
    for position, record in enumerate(records):
        raw = _encode(record)
        if len(raw) <= allowance:
            result.append(record)
            continue
        source = raw.decode('utf-8')
        digest = sha256(raw).hexdigest()
        start = 0
        fragment_index = 0
        while start < len(source):
            low, high, best = start + 1, min(start + allowance, len(source)), None
            while low <= high:
                middle = (low + high) // 2
                candidate = {'type': 'record_fragment', 'source_record_index': position,
                             'source_type': record.get('type'), 'source_sha256': digest,
                             'fragment_index': fragment_index, 'character_start': start,
                             'character_end': middle, 'total_characters': len(source),
                             'final_fragment': middle == len(source), 'json_fragment': source[start:middle]}
                if len(_encode(candidate)) <= allowance:
                    best = candidate
                    low = middle + 1
                else:
                    high = middle - 1
            if best is None:
                raise ValueError('Byte budget cannot hold fragment metadata; increase --max-output-bytes')
            result.append(best)
            start = best['character_end']
            fragment_index += 1
        fragmented += 1
    return result, fragmented
