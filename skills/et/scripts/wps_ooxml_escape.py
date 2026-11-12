# SPDX-License-Identifier: MIT
"""Decode OOXML ``_xHHHH_`` saved-text escapes with one-pass protection.

SpreadsheetML text may encode control characters as ``_xHHHH_`` (UTF-16 code
unit) and protects literal text that looks like that sequence by replacing the
leading underscore with ``_x005F_``. Decoding is single-pass: the underscore
produced by ``_x005F_`` is never decoded again as the start of a new escape.
Surrogate pairs written as two adjacent escapes are combined; lone surrogates
stay literal and are reported for the caller to diagnose.
"""
import re


_ESCAPE = re.compile(r'_x([0-9A-Fa-f]{4})_')


def _sequence(text, index):
    match = _ESCAPE.match(text, index)
    return int(match.group(1), 16) if match else None


def _evidence(raw, sequences, literal_escapes, invalid_sequences):
    if not sequences and not literal_escapes and not invalid_sequences:
        return None
    evidence = {
        'scheme': 'ooxml_xhhh',
        'decoded': True,
        'escape_sequences': sequences,
        'literal_escapes': literal_escapes,
    }
    if invalid_sequences:
        evidence['invalid_sequences'] = invalid_sequences
    if len(raw) > 32767:
        evidence['raw_text'] = raw[:32767]
        evidence['raw_text_truncated'] = True
    else:
        evidence['raw_text'] = raw
    return evidence


def decode_ooxml_text(raw):
    """Return ``(decoded_text, evidence_or_none)`` for one saved XML text value."""
    if not isinstance(raw, str) or '_x' not in raw:
        return raw, None
    output = []
    index = 0
    length = len(raw)
    sequences = 0
    literal_escapes = 0
    invalid_sequences = 0
    while index < length:
        if raw[index] != '_' or index + 7 > length:
            output.append(raw[index])
            index += 1
            continue
        if raw.startswith('_x005F_', index):
            output.append('_')
            index += 7
            literal_escapes += 1
            continue
        code = _sequence(raw, index)
        if code is None:
            output.append(raw[index])
            index += 1
            continue
        if 0xD800 <= code <= 0xDBFF:
            low = _sequence(raw, index + 7) if index + 14 <= length else None
            if low is not None and 0xDC00 <= low <= 0xDFFF:
                value = 0x10000 + ((code - 0xD800) << 10) + (low - 0xDC00)
                output.append(chr(value))
                index += 14
                sequences += 2
                continue
        if 0xD800 <= code <= 0xDFFF:
            output.append(raw[index:index + 7])
            index += 7
            invalid_sequences += 1
            continue
        output.append(chr(code))
        index += 7
        sequences += 1
    text = ''.join(output)
    return text, _evidence(raw, sequences, literal_escapes, invalid_sequences)
