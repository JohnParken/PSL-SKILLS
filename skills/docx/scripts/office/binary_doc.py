"""Bounded, read-only CFB/MS-DOC main-story extraction; no office runtime."""

import os
import stat
import struct

SIGNATURE = b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'
FREE = 0xFFFFFFFF
END = 0xFFFFFFFE
FAT_SECTOR = 0xFFFFFFFD
MAX_FILE_BYTES = 32 * 1024 * 1024
MAX_DIRECTORY_ENTRIES = 4096
MAX_PIECES = 100_000
MAX_TEXT_UNITS = 8 * 1024 * 1024
MAX_CLX_BYTES = 4 * 1024 * 1024
MAX_PRC_RECORDS = 4096
VERSIONS = {0xC1: (93, 0), 0xD9: (108, 2), 0x101: (136, 2),
            0x10C: (164, 2), 0x112: (183, 5)}
COMPRESSED_MAPPING = dict(zip(
    (0x82, 0x83, 0x84, 0x85, 0x86, 0x87, 0x88, 0x89, 0x8A, 0x8B, 0x8C,
     0x91, 0x92, 0x93, 0x94, 0x95, 0x96, 0x97, 0x98, 0x99, 0x9A, 0x9B, 0x9C, 0x9F),
    (0x201A, 0x0192, 0x201E, 0x2026, 0x2020, 0x2021, 0x02C6, 0x2030, 0x0160, 0x2039, 0x0152,
     0x2018, 0x2019, 0x201C, 0x201D, 0x2022, 0x2013, 0x2014, 0x02DC, 0x2122, 0x0161, 0x203A, 0x0153, 0x0178)))


def _require(condition, message):
    if not condition:
        raise ValueError('unsupported or invalid CFB/MS-DOC: ' + message)


def _slice(data, offset, size):
    _require(0 <= offset <= len(data) and 0 <= size <= len(data) - offset,
             'truncated or out-of-range structure')
    return data[offset:offset + size]


def _u16(data, offset):
    return struct.unpack('<H', _slice(data, offset, 2))[0]


def _u32(data, offset):
    return struct.unpack('<I', _slice(data, offset, 4))[0]


class CompoundFile:
    """A bounded in-memory CFB subset, with chain and ownership validation."""

    def __init__(self, data):
        _require(512 <= len(data) <= MAX_FILE_BYTES, 'file size limit or truncated header')
        _require(data[:8] == SIGNATURE, 'not an OLE/CFB file')
        self.data = data
        major = _u16(data, 26)
        _require(major in (3, 4), 'unsupported CFB version')
        _require(_u16(data, 24) == 0x003E, 'unsupported CFB minor version')
        _require(_u16(data, 28) == 0xFFFE and _u16(data, 30) == (9 if major == 3 else 12)
                 and _u16(data, 32) == 6, 'unsupported byte order or sector sizes')
        _require(data[8:24] == bytes(16), 'nonzero CFB header CLSID is not supported')
        _require(data[34:40] == bytes(6), 'nonzero CFB reserved header bytes')
        _require(_u32(data, 56) == 4096, 'unsupported mini stream cutoff')
        self.sector_size = 1 << _u16(data, 30)
        _require(len(data) % self.sector_size == 0, 'truncated sector')
        _require(data[512:self.sector_size] == bytes(self.sector_size - 512), 'nonzero header padding')
        self.sector_count = len(data) // self.sector_size - 1
        self.used = set()
        _require(_u32(data, 72) == 0 and _u32(data, 68) == END,
                 'extended DIFAT is not supported')
        fat_count = _u32(data, 44)
        _require(0 < fat_count <= 109, 'unsupported FAT count')
        fat_ids = struct.unpack('<109I', data[76:512])
        _require(all(value == FREE for value in fat_ids[fat_count:]), 'invalid unused DIFAT entries')
        fat = bytearray()
        for sector in fat_ids[:fat_count]:
            self._claim(sector)
            fat.extend(self._sector(sector))
        self.fat = struct.unpack('<' + 'I' * (len(fat) // 4), fat)
        _require(len(self.fat) >= self.sector_count, 'FAT does not cover file')
        _require(all(self.fat[sector] == FAT_SECTOR for sector in fat_ids[:fat_count]), 'invalid FAT marker')
        directory_count = _u32(data, 40)
        _require(major != 3 or directory_count == 0, 'invalid v3 directory sector count')
        directory = self._chain(_u32(data, 48), None,
                                MAX_DIRECTORY_ENTRIES * 128 // self.sector_size)
        _require(major != 4 or len(directory) == directory_count, 'directory chain length mismatch')
        raw = b''.join(self._sector(sector) for sector in directory)
        _require(0 < len(raw) // 128 <= MAX_DIRECTORY_ENTRIES, 'directory entry limit')
        self.entries = []
        for offset in range(0, len(raw), 128):
            item = raw[offset:offset + 128]
            kind = item[66]
            if kind == 0:
                self.entries.append(None)
                continue
            _require(kind in (1, 2, 5), 'unsupported directory object type')
            name_size = _u16(item, 64)
            _require(2 <= name_size <= 64 and name_size % 2 == 0 and item[name_size - 2:name_size] == b'\0\0',
                     'invalid directory name length')
            try:
                name = item[:name_size - 2].decode('utf-16le')
            except UnicodeError as exc:
                raise ValueError('invalid CFB directory UTF-16 name') from exc
            _require(name and not any(char in name for char in '\0/\\:!'), 'invalid directory name')
            size = struct.unpack('<Q', item[120:128])[0]
            _require(size <= MAX_FILE_BYTES, 'stream size limit')
            _require(major != 3 or size <= 0xFFFFFFFF, 'invalid v3 stream size')
            self.entries.append((name, kind, _u32(item, 68), _u32(item, 72), _u32(item, 76),
                                 _u32(item, 116), size))
        root = self.entries[0]
        _require(root is not None and root[1] == 5 and root[2:4] == (FREE, FREE), 'invalid root directory')
        self.streams = {}
        seen = {0}
        pending = [(root[4], ())]
        siblings = {}
        while pending:
            index, parent = pending.pop()
            if index == FREE:
                continue
            _require(0 < index < len(self.entries) and index not in seen, 'directory cycle or invalid link')
            seen.add(index)
            entry = self.entries[index]
            _require(entry is not None and entry[1] != 5, 'invalid directory tree object')
            name, kind, left, right, child, start, size = entry
            key = name.upper()
            names = siblings.setdefault(parent, set())
            _require(key not in names, 'duplicate directory name')
            names.add(key)
            pending.extend(((left, parent), (right, parent)))
            if kind == 1:
                _require(size == 0, 'invalid storage size')
                _require(len(parent) < 64, 'directory nesting limit')
                pending.append((child, parent + (key,)))
            else:
                _require(child == FREE, 'stream has child directory')
                self.streams[parent + (key,)] = (start, size)
        _require(all(entry is None or index in seen for index, entry in enumerate(self.entries)),
                 'unreachable directory object')
        mini_count = _u32(data, 64)
        mini_ids = self._chain(_u32(data, 60), mini_count, self.sector_count)
        mini_raw = b''.join(self._sector(sector) for sector in mini_ids)
        self.mini_fat = struct.unpack('<' + 'I' * (len(mini_raw) // 4), mini_raw)
        self.mini_data = self._normal_stream(root[5], root[6])
        _require(len(self.mini_data) % 64 == 0, 'invalid root mini stream length')
        self.mini_used = set()
        # Claim all streams, even ignored ones, to reject overlapping allocation.
        self.stream_chains = {}
        total = 0
        for name, (start, size) in self.streams.items():
            total += size
            _require(total <= MAX_FILE_BYTES, 'aggregate stream size limit')
            if size < 4096:
                chain = self._mini_chain(start, size)
            else:
                chain = self._chain(start, (size + self.sector_size - 1) // self.sector_size,
                                    self.sector_count)
            self.stream_chains[name] = chain

    def _sector(self, sector):
        _require(0 <= sector < self.sector_count, 'sector outside file')
        offset = (sector + 1) * self.sector_size
        return self.data[offset:offset + self.sector_size]

    def _claim(self, sector):
        _require(0 <= sector < self.sector_count and sector not in self.used,
                 'cyclic, overlapping or out-of-range sector chain')
        self.used.add(sector)

    def _chain(self, start, count, limit):
        _require(count is None or 0 <= count <= limit, 'chain size limit')
        chain = []
        while start != END:
            _require(len(chain) < limit and (count is None or len(chain) < count), 'chain length mismatch or limit')
            self._claim(start)
            chain.append(start)
            _require(start < len(self.fat), 'FAT index outside table')
            start = self.fat[start]
        _require(count is None or len(chain) == count, 'truncated sector chain')
        return chain

    def _normal_stream(self, start, size):
        ids = self._chain(start, (size + self.sector_size - 1) // self.sector_size, self.sector_count)
        return b''.join(self._sector(sector) for sector in ids)[:size]

    def _mini_chain(self, start, size):
        count = (size + 63) // 64
        chain = []
        while start != END:
            _require(len(chain) < count and 0 <= start < len(self.mini_fat)
                     and start < len(self.mini_data) // 64 and start not in self.mini_used,
                     'cyclic, overlapping or out-of-range mini sector chain')
            self.mini_used.add(start)
            chain.append(start)
            start = self.mini_fat[start]
        _require(len(chain) == count, 'truncated mini sector chain')
        return chain

    def read_stream(self, name):
        key = (name.upper(),)
        _require(key in self.streams, 'missing required stream ' + name)
        size = self.streams[key][1]
        chain = self.stream_chains[key]
        if size < 4096:
            return b''.join(self.mini_data[index * 64:(index + 1) * 64] for index in chain)[:size]
        return b''.join(self._sector(index) for index in chain)[:size]


def _read_file(path):
    descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0) | getattr(os, 'O_BINARY', 0))
    try:
        info = os.fstat(descriptor)
        _require(stat.S_ISREG(info.st_mode), 'input must be a regular file')
        _require(512 <= info.st_size <= MAX_FILE_BYTES, 'file size limit or truncated header')
        with os.fdopen(descriptor, 'rb') as stream:
            descriptor = None
            data = stream.read(info.st_size + 1)
            _require(len(data) == info.st_size and os.fstat(stream.fileno()).st_size == info.st_size,
                     'input changed during read')
            return data
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _compressed_text(data):
    # MS-DOC FcCompressed has 24 substitutions; it is NOT Windows-1252.
    return data.decode('latin1').translate(COMPRESSED_MAPPING)


def read_ms_doc(path):
    """Return stored main-story text, including hidden/revision-marked text.

    Never interprets formatting or revisions. Fields and unknown control codes fail.
    """
    compound = CompoundFile(_read_file(path))
    _require(not any(name[-1] in ('ENCRYPTEDPACKAGE', 'ENCRYPTIONINFO', 'ENCRYPTEDSUMMARY', 'DRMCONTENT')
                     for name in compound.streams), 'encrypted document is not supported')
    word = compound.read_stream('WordDocument')
    _require(_u16(word, 0) == 0xA5EC, 'not an MS-DOC WordDocument stream')
    base_version = _u16(word, 2)
    _require(base_version in VERSIONS, f'unsupported MS-DOC version 0x{base_version:04X}')
    flags = _u16(word, 10)
    _require(not flags & 0x0100 and _u32(word, 14) == 0, 'encrypted or obfuscated MS-DOC is not supported')
    _require(not flags & 3 and _u16(word, 8) == 0, 'templates and AutoText documents are not supported')
    _require(flags & 0x1000 and _u16(word, 12) in (0xBF, 0xC1), 'unsupported FIB flags or back version')
    _require(_u16(word, 32) == 14 and _u16(word, 62) == 22, 'unsupported FIB section lengths')
    pair_count = _u16(word, 152)
    _require(pair_count in {value[0] for value in VERSIONS.values()}, 'unsupported FIB pair count')
    new_offset = 154 + pair_count * 8
    new_count = _u16(word, new_offset)
    _require(new_count in (0, 2, 5), 'unsupported FIB extension length')
    _slice(word, new_offset + 2, new_count * 2)
    version = _u16(word, new_offset + 2) if new_count else base_version
    _require(version in VERSIONS and VERSIONS[version] == (pair_count, new_count),
             f'unsupported or inconsistent effective MS-DOC version 0x{version:04X}')
    fib_end = new_offset + 2 + new_count * 2
    meaningful_size = _u32(word, 64)
    _require(fib_end <= meaningful_size <= len(word), 'invalid cbMac')
    word = word[:meaningful_size]
    main_count = _u32(word, 76)
    other_counts = [_u32(word, offset) for offset in (80, 84, 92, 96, 100, 104)]
    total_count = main_count + sum(other_counts) + (1 if any(other_counts) else 0)
    _require(0 < main_count <= total_count <= MAX_TEXT_UNITS, 'text character count limit or empty body')
    table = compound.read_stream('1Table' if flags & 0x0200 else '0Table')
    clx_size = _u32(word, 422)
    _require(clx_size <= MAX_CLX_BYTES, 'CLX size limit')
    clx = _slice(table, _u32(word, 418), clx_size)
    offset = 0
    prc_count = 0
    while offset < len(clx) and clx[offset] == 1:
        prc_count += 1
        _require(prc_count <= MAX_PRC_RECORDS, 'Prc record count limit')
        length = _u16(clx, offset + 1)
        _require(length <= 0x3FA2, 'unsupported Prc length')
        _slice(clx, offset + 3, length)
        offset += 3 + length
    _require(offset < len(clx) and clx[offset] == 2, 'missing or unsupported CLX piece table')
    length = _u32(clx, offset + 1)
    _require(offset + 5 + length == len(clx) and length >= 16 and (length - 4) % 12 == 0,
             'invalid piece table length')
    count = (length - 4) // 12
    _require(count <= MAX_PIECES, 'piece count limit')
    plc = _slice(clx, offset + 5, length)
    positions = struct.unpack('<' + 'I' * (count + 1), plc[:4 * (count + 1)])
    _require(positions[0] == 0 and positions[-1] == total_count
             and all(left < right for left, right in zip(positions, positions[1:])),
             'invalid character positions or story length')
    text_bytes = bytearray()
    for index in range(count):
        descriptor = 4 * (count + 1) + 8 * index
        encoded_offset = _u32(plc, descriptor + 2)
        _require(not encoded_offset & 0x80000000, 'invalid piece offset flag')
        compressed = bool(encoded_offset & 0x40000000)
        file_offset = encoded_offset & 0x3FFFFFFF
        _require(not compressed or file_offset % 2 == 0, 'invalid compressed piece offset')
        file_offset = file_offset // 2 if compressed else file_offset
        units = positions[index + 1] - positions[index]
        _require(file_offset >= fib_end, 'piece overlaps FIB')
        piece = _slice(word, file_offset, units * (1 if compressed else 2))
        body_units = max(0, min(positions[index + 1], main_count) - positions[index])
        if body_units:
            if compressed:
                text_bytes.extend(_compressed_text(piece[:body_units]).encode('utf-16le'))
            else:
                text_bytes.extend(piece[:2 * body_units])
    try:
        text = text_bytes.decode('utf-16le')
    except UnicodeError as exc:
        raise ValueError('invalid MS-DOC UTF-16 body text') from exc
    _require(text.endswith('\r'), 'main body lacks terminal paragraph mark')
    _require(all(ord(char) >= 32 or char in '\r\t\x07\x0b\x0c' for char in text),
             'fields, embedded-object markers or unsupported body control characters')
    return text.replace('\r', '\n').replace('\x0b', '\n').replace('\x07', '\t')
