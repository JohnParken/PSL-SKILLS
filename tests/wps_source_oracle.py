"""Test-only record oracle for pinned public samples; no skill parser imports.

Restricted CFB stream framing and plain SST/LabelSst/Number/numeric Formula
observations. It deliberately does not implement a general spreadsheet reader.
"""
from pathlib import Path
from decimal import Decimal, localcontext
import hashlib
import json
import struct

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def workbook_stream(data):
    assert data[:8] == bytes.fromhex('d0cf11e0a1b11ae1')
    sector_size = 1 << struct.unpack_from('<H', data, 30)[0]
    fat_count = struct.unpack_from('<I', data, 44)[0]
    fat_ids = [n for n in struct.unpack_from('<109I', data, 76) if n < 0xFFFFFFFA]
    assert fat_count <= 109 and len(fat_ids) >= fat_count
    def sector(number):
        block = data[(number + 1) * sector_size:(number + 2) * sector_size]
        assert len(block) == sector_size
        return block
    fat = []
    for number in fat_ids[:fat_count]:
        block = sector(number)
        fat.extend(struct.unpack('<' + 'I' * (len(block) // 4), block))
    def chain(first, allocation, block):
        output = bytearray()
        seen = set()
        while first != 0xFFFFFFFE:
            assert first < len(allocation) and first not in seen and len(seen) < 10000
            seen.add(first)
            output.extend(block(first))
            first = allocation[first]
        return bytes(output)
    directory = chain(struct.unpack_from('<I', data, 48)[0], fat, sector)
    entries = {}
    root = None
    for offset in range(0, len(directory), 128):
        entry = directory[offset:offset + 128]
        if entry[66] not in (2, 5):
            continue
        size = struct.unpack_from('<H', entry, 64)[0]
        name = entry[:size - 2].decode('utf-16le')
        item = (struct.unpack_from('<I', entry, 116)[0], struct.unpack_from('<Q', entry, 120)[0])
        if entry[66] == 5:
            root = item
        else:
            entries[name] = item
    start, length = entries.get('Workbook', entries.get('Book'))
    if length >= struct.unpack_from('<I', data, 56)[0]:
        return chain(start, fat, sector)[:length]
    assert root is not None
    mini_stream = chain(root[0], fat, sector)[:root[1]]
    raw = chain(struct.unpack_from('<I', data, 60)[0], fat, sector)
    mini_fat = struct.unpack('<' + 'I' * (len(raw) // 4), raw)
    return chain(start, mini_fat, lambda n: mini_stream[n * 64:(n + 1) * 64])[:length]


def records(stream, start):
    offset = start
    while offset + 4 <= len(stream):
        kind, size = struct.unpack_from('<HH', stream, offset)
        payload = stream[offset + 4:offset + 4 + size]
        assert len(payload) == size
        yield kind, payload, offset
        offset += 4 + size


def coordinate(row, column):
    name = ''
    column += 1
    while column:
        column, remainder = divmod(column - 1, 26)
        name = chr(65 + remainder) + name
    return name + str(row + 1)


def rk_number(raw):
    if raw & 2:
        value = Decimal(struct.unpack('<i', struct.pack('<I', raw))[0] >> 2)
    else:
        value = Decimal.from_float(struct.unpack('<d', struct.pack('<Q', (raw & ~3) << 32))[0])
    with localcontext() as context:
        context.prec = 2000
        return str(value / 100 if raw & 1 else value)


def oracle(data):
    stream = workbook_stream(data)
    strings, sheets = [], []
    for kind, payload, offset in records(stream, 0):
        if kind == 0x85:
            name_length, flags = payload[6:8]
            name = payload[8:8 + name_length * (2 if flags & 1 else 1)].decode('utf-16le' if flags & 1 else 'latin1')
            sheets.append((name, struct.unpack_from('<I', payload)[0]))
        elif kind == 0xFC:
            count = struct.unpack_from('<I', payload, 4)[0]
            cursor = 8
            for _ in range(count):
                length, flags = struct.unpack_from('<HB', payload, cursor)
                assert flags & ~1 == 0, 'Oracle only accepts plain same-record SST'
                cursor += 3
                end = cursor + length * (2 if flags & 1 else 1)
                assert end <= len(payload), 'Oracle does not infer continuation data'
                strings.append(payload[cursor:end].decode('utf-16le' if flags & 1 else 'latin1'))
                cursor = end
            assert cursor == len(payload)
        if kind == 0xA:
            break
    expected = {}
    for name, start in sheets:
        cells = {}
        chart_depth = 0
        for kind, payload, offset in records(stream, start):
            if kind == 0x809 and offset != start:
                chart_depth += 1
            if kind == 0xA:
                if chart_depth:
                    chart_depth -= 1
                    continue
                break
            if chart_depth:
                continue
            if kind == 0xBD:
                row, first = struct.unpack_from('<HH', payload)
                last = struct.unpack_from('<H', payload, len(payload) - 2)[0]
                assert len(payload) == 6 + (last - first + 1) * 6
                for column in range(first, last + 1):
                    raw = struct.unpack_from('<I', payload, 6 + (column - first) * 6)[0]
                    cells[coordinate(row, column)] = {'type': 'number', 'value': rk_number(raw), 'record_offset': offset, 'rk_raw': raw, 'formula': False}
                continue
            if kind not in (0xFD, 0x203, 0x6, 0x27E):
                continue
            row, column = struct.unpack_from('<HH', payload)
            address = coordinate(row, column)
            if kind == 0xFD:
                value = strings[struct.unpack_from('<I', payload, 6)[0]]
                cells[address] = {'type': 'text', 'value': value, 'record_offset': offset}
            elif kind == 0x27E:
                raw = struct.unpack_from('<I', payload, 6)[0]
                cells[address] = {'type': 'number', 'value': rk_number(raw), 'record_offset': offset, 'rk_raw': raw, 'formula': False}
            elif kind == 0x203 or payload[12:14] != b'\xff\xff':
                raw = payload[6:14]
                value = str(Decimal.from_float(struct.unpack('<d', raw)[0]))
                cells[address] = {'type': 'number', 'value': value, 'record_offset': offset,
                                  'ieee754_hex': raw.hex(), 'formula': kind == 0x6}
        expected[name] = cells
    return {'sha256': hashlib.sha256(data).hexdigest(), 'scope': 'plain LabelSst, Number, RK/MulRK and numeric Formula caches; not blank/style/layout/formula calculation semantics', 'expected': expected}


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    if any(p.is_absolute() or '..' in p.parts for p in (args.directory, args.output)):
        parser.error('Use task-relative input/output paths')
    manifest = json.loads((REPOSITORY_ROOT / 'skills/wps/references/et-real-samples.json').read_text(encoding='utf-8'))
    samples = {}
    for entry in manifest['samples']:
        path = args.directory / entry['filename']
        assert path.stat().st_size == entry['bytes'], 'Expected exact original byte length'
        data = path.read_bytes()
        assert hashlib.sha256(data).hexdigest() == entry['sha256']
        observation = oracle(data)
        samples[entry['filename']] = {'sha256': observation['sha256'], 'sheets': observation['expected']}
    with args.output.open('x', encoding='utf-8') as stream:
        json.dump({'samples': samples}, stream, ensure_ascii=False, indent=2)
    print('independent-source-observations-written')
