"""Maintenance-only independent MS-DOC main-text cross-check of a fixed public file.

Parses FIB/CLX/Pcdt directly using struct and a shared bounded CFB *container*
reader; does not call read_wps.py's document parser or rely on its text output.
This is algorithmic cross-check, not a manually certified WPS application export.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import sys

MAX_PIECES = 100000
MAX_MAIN_UNITS = 8 * 1024 * 1024
# Frozen from the independent story decoder for the pinned public fixture.
EXPECTED_TEXT_SHA256 = '54d875f79e02cac659a5298e78593773639f51d858e9a0daacb63652a940140f'
EXPECTED_PARAGRAPHS_SHA256 = '27b13210fe44425ed6158738804f581538c4313e7cfab661434112346232f95f'
COMPRESSED_TRANSLATE = dict(zip(
    range(0x80, 0xa0),
    [0x80, 0x81, 0x201a, 0x192, 0x201e, 0x2026, 0x2020, 0x2021, 0x2c6,
     0x2030, 0x160, 0x2039, 0x152, 0x8d, 0x17d, 0x8f, 0x90,
     0x2018, 0x2019, 0x201c, 0x201d, 0x2022, 0x2013, 0x2014,
     0x2dc, 0x2122, 0x161, 0x203a, 0x153, 0x9d, 0x17e, 0x178]
))


def _u32(buffer, offset):
    return struct.unpack_from('<I', buffer, offset)[0]


def independently_decode_main(sample, scripts):
    # Reuse only the checked CFB allocation layer; FIB and piece decoding
    # below deliberately do not call the production document reader.
    sys.path.insert(0, str(scripts))
    from wps_cfb import CompoundFile
    compound = CompoundFile(sample, compatibility='legacy_word_clsid')
    word = compound.streams[('WordDocument',)]
    flags = struct.unpack_from('<H', word, 10)[0]
    table = compound.streams[(('1Table' if flags & 0x0200 else '0Table'),)]
    main_units = _u32(word, 76)
    if main_units > MAX_MAIN_UNITS:
        raise ValueError('MS-DOC main-story budget exceeded')
    offset = _u32(word, 418)
    size = _u32(word, 422)
    if size > len(table) or offset > len(table) - size or size < 5:
        raise ValueError('CLX out of range')
    clx = memoryview(table)[offset:offset + size]
    cursor = 0
    while cursor < len(clx) and clx[cursor] == 1:
        if cursor + 3 > len(clx):
            raise ValueError('Short PRC')
        count = struct.unpack_from('<H', clx, cursor + 1)[0]
        cursor += 3 + count
    if cursor + 5 > len(clx) or clx[cursor] != 2:
        raise ValueError('Missing Pcdt')
    length = _u32(clx, cursor + 1)
    if length < 4 or (length - 4) % 12 or cursor + 5 + length != len(clx):
        raise ValueError('Invalid PlcPcd')
    count = (length - 4) // 12
    if count > MAX_PIECES:
        raise ValueError('Too many pieces')
    data = clx[cursor + 5:]
    cps = struct.unpack_from('<' + 'I' * (count + 1), data)
    if cps[0] != 0 or cps[-1] < main_units:
        raise ValueError('Invalid CP extent')
    text = []
    for i in range(count):
        cp_start = cps[i]
        cp_end = min(cps[i+1], main_units)
        if cp_start >= main_units:
            break
        if cp_end <= cp_start:
            raise ValueError('Bad CP ordering')
        descriptor = 4 * (count + 1) + i * 8
        fc = _u32(data, descriptor + 2)
        if fc & 0x80000000:
            raise ValueError('Reserved FC flag')
        compressed = bool(fc & 0x40000000)
        at = (fc & 0x3fffffff) // 2 if compressed else fc & 0x3fffffff
        units = cp_end - cp_start
        end = at + units * (1 if compressed else 2)
        if end > len(word):
            raise ValueError('Piece outside WordDocument')
        raw = bytes(word[at:end])
        if compressed:
            chars = ''.join(chr(COMPRESSED_TRANSLATE.get(byte, byte)) for byte in raw)
        else:
            chars = raw.decode('utf-16le')
        text.append(chars)
    stored = ''.join(text)
    if len(stored.encode('utf-16le')) // 2 != main_units:
        raise ValueError('Logical CP count mismatch')
    if not stored.endswith('\r'):
        raise ValueError('Stored main-story is not CR terminated')
    # Strictly limited to this public fixture's observable text controls.
    control = {ord(c) for c in stored if ord(c) < 32}
    if control - {1, 7, 9, 11, 12, 13}:
        raise ValueError('Unrecognized control; do not manufacture oracle')
    normalized = stored.replace('\x01', '\ufffc').replace('\x07', '\t')
    normalized = normalized.replace('\x0b', '\n').replace('\r', '\n')
    paragraphs = [p.replace('\x01', '\ufffc').replace('\x07', '\t').replace('\x0b', '\n')
                  for p in stored[:-1].split('\r')]
    return {'text': normalized, 'paragraphs': paragraphs,
            'main_units': main_units, 'piece_count': count,
            'text_sha256': hashlib.sha256(normalized.encode('utf-8')).hexdigest(),
            'paragraphs_sha256': hashlib.sha256(json.dumps(paragraphs, ensure_ascii=False, separators=(',', ':')).encode('utf-8')).hexdigest()}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('fixture')
    parser.add_argument('scripts')
    arguments=parser.parse_args()
    from subprocess import run
    sample_path=Path(arguments.fixture)
    sample=sample_path.read_bytes()
    expected=independently_decode_main(sample, Path(arguments.scripts).resolve())
    if (expected['text_sha256'] != EXPECTED_TEXT_SHA256 or
            expected['paragraphs_sha256'] != EXPECTED_PARAGRAPHS_SHA256):
        raise SystemExit('Frozen independent oracle digest mismatch')
    process=run([sys.executable,'-I','-S','-B',
                 str(Path(arguments.scripts).resolve()/'read_wps.py'),
                 str(sample_path.resolve()),
                 '--object-policy','placeholder'],capture_output=True,text=True,timeout=30)
    if process.returncode not in (0,3):
        raise SystemExit('Target reader rejected fixture')
    actual=json.loads(process.stdout)
    equal_text=actual.get('text')==expected['text']
    equal_paragraphs=actual.get('paragraphs')==expected['paragraphs']
    result={'algorithmic_text_equal':equal_text,'algorithmic_paragraphs_equal':equal_paragraphs,
            'oracle_text_sha256':expected['text_sha256'],
            'oracle_paragraphs_sha256':expected['paragraphs_sha256'],
            'main_units':expected['main_units'],'pieces':expected['piece_count'],
            'paragraph_count':len(expected['paragraphs']),
            'full_manual_wps_software_validation':False,
            'real_wps_producer_verified':False}
    print(json.dumps(result,ensure_ascii=False))
    if not equal_text or not equal_paragraphs:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
