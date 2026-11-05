"""Bounded asset inspection for fixed-layout OFD generation; never executes fonts/images."""
import bisect
import hashlib
import struct
import unicodedata
import zlib

MAX_FONT_BYTES = 10 * 1024 * 1024


class GenerationError(ValueError):
    """Unsupported input or violated generation profile; never silently repair."""


def require(condition, message):
    if not condition:
        raise GenerationError(message)


def number(value, name, low=0, high=2000):
    import math
    require(type(value) in (int, float) and low <= value <= high and math.isfinite(value),
            'invalid finite numeric field: ' + name)
    return float(value)


def plain_text(text, *, limit=2000):
    require(isinstance(text, str) and len(text) <= limit, 'text must be a bounded string')
    for char in text:
        code = ord(char)
        allowed = (0x20 <= code <= 0x7E or 0x2000 <= code <= 0x206F
                   or 0x3000 <= code <= 0x303F or 0x3400 <= code <= 0x9FFF
                   or 0xFF01 <= code <= 0xFF60 or 0xFFE0 <= code <= 0xFFE6)
        require(allowed and unicodedata.category(char)[0] not in 'CM'
                and unicodedata.bidirectional(char) not in ('R', 'AL', 'AN'),
                'unsupported horizontal character U+' + format(code, '04X'))
    return text


class TrueType:
    """Inspect static TrueType Unicode cmap 4/12, hmtx and conservative head bounds.

    This is not a font sanitizer, outline interpreter, shaper or license authority.
    The original complete font is embedded without modification or subsetting.
    """
    def __init__(self, data):
        require(12 <= len(data) <= MAX_FONT_BYTES and data[:4] == b'\x00\x01\x00\x00',
                'font requires a static glyf TrueType sfnt, at most 10 MiB')
        self.data = data
        self.sha256 = hashlib.sha256(data).hexdigest()
        count = struct.unpack_from('>H', data, 4)[0]
        require(1 <= count <= 128 and 12 + count * 16 <= len(data), 'invalid sfnt directory')
        self.tables = {}
        spans = []
        for index in range(count):
            tag, _, offset, size = struct.unpack_from('>4sIII', data, 12 + index * 16)
            require(tag not in self.tables and offset >= 12 + count * 16
                    and offset % 4 == 0 and offset + size <= len(data), 'invalid/duplicate font table')
            if size:
                require(all(offset + size <= start or offset >= end for start, end in spans), 'overlapping font tables')
                spans.append((offset, offset + size))
            self.tables[tag] = data[offset:offset + size]
        require(not set(self.tables) & {b'fvar', b'CFF ', b'CFF2', b'SVG ', b'COLR', b'CBDT', b'EBDT'},
                'variable, CFF, SVG and color fonts are outside the generation profile')
        head = self.table(b'head', 54)
        require(struct.unpack_from('>I', head, 12)[0] == 0x5F0F3CF5, 'invalid font head magic')
        self.units = struct.unpack_from('>H', head, 18)[0]
        self.bounds = struct.unpack_from('>hhhh', head, 36)
        require(16 <= self.units <= 16384 and self.bounds[0] <= self.bounds[2]
                and self.bounds[1] < self.bounds[3], 'invalid font units/global bounds')
        self.glyph_count = struct.unpack_from('>H', self.table(b'maxp', 6), 4)[0]
        self.metric_count = struct.unpack_from('>H', self.table(b'hhea', 36), 34)[0]
        require(0 < self.metric_count <= self.glyph_count <= 65535, 'invalid font metrics count')
        self.metrics = self.table(b'hmtx', self.metric_count * 4 + (self.glyph_count - self.metric_count) * 2)
        self.fs_type = struct.unpack_from('>H', self.table(b'OS/2', 10), 8)[0]
        require(self.fs_type in (0, 8, 0x100, 0x108),
                'font embedding requires installable/editable rights (optional no-subsetting) and explicit license permission')
        self.table(b'glyf', 1)
        location_format = struct.unpack_from('>h', head, 50)[0]
        require(location_format in (0, 1), 'invalid loca format')
        locations = self.table(b'loca', (self.glyph_count + 1) * (2 if location_format == 0 else 4))
        offsets = struct.unpack_from('>' + ('H' if location_format == 0 else 'I') * (self.glyph_count + 1), locations)
        if location_format == 0:
            offsets = tuple(value * 2 for value in offsets)
        require(list(offsets) == sorted(offsets) and offsets[-1] <= len(self.tables[b'glyf']), 'invalid glyph offsets')
        self.offsets = offsets
        cmap = self.table(b'cmap', 4)
        count = struct.unpack_from('>H', cmap, 2)[0]
        require(0 < count <= 64 and 4 + count * 8 <= len(cmap), 'invalid cmap directory')
        choices = []
        for index in range(count):
            platform, encoding, offset = struct.unpack_from('>HHI', cmap, 4 + index * 8)
            require(offset + 2 <= len(cmap), 'invalid cmap offset')
            form = struct.unpack_from('>H', cmap, offset)[0]
            if (platform == 0 or (platform == 3 and encoding in (1, 10))) and form in (4, 12):
                choices.append((form, offset))
        require(bool(choices), 'font has no supported Unicode cmap 4/12')
        form, offset = max(choices)
        self.form = form
        if form == 12:
            require(offset + 16 <= len(cmap), 'short cmap 12')
            length, _, groups = struct.unpack_from('>III', cmap, offset + 4)
            require(0 < groups <= 20000 and 16 + groups * 12 == length and offset + length <= len(cmap), 'invalid cmap 12')
            self.groups = [struct.unpack_from('>III', cmap, offset + 16 + index * 12) for index in range(groups)]
            previous = -1
            for start, end, glyph in self.groups:
                require(previous < start <= end <= 0x10FFFF and glyph + end - start < self.glyph_count, 'invalid cmap 12 group')
                previous = end
            self.ends = [group[1] for group in self.groups]
        else:
            require(offset + 14 <= len(cmap), 'short cmap 4')
            length, _, doubled = struct.unpack_from('>HHH', cmap, offset + 2)
            count = doubled // 2
            require(doubled % 2 == 0 and 0 < count <= 8192 and length >= 16 + count * 8
                    and offset + length <= len(cmap), 'invalid cmap 4')
            self.cmap = cmap[offset:offset + length]
            self.ends = list(struct.unpack_from('>' + 'H' * count, self.cmap, 14))
            self.starts = struct.unpack_from('>' + 'H' * count, self.cmap, 16 + count * 2)
            self.deltas = struct.unpack_from('>' + 'h' * count, self.cmap, 16 + count * 4)
            self.range_base = 16 + count * 6
            self.ranges = struct.unpack_from('>' + 'H' * count, self.cmap, self.range_base)
            previous = -1
            for start, end in zip(self.starts, self.ends):
                require(previous < start <= end, 'overlapping cmap 4 segments')
                previous = end
        name = self.table(b'name', 6)
        count, strings = struct.unpack_from('>HH', name, 2)
        require(6 + count * 12 <= len(name) and strings <= len(name), 'invalid font name directory')
        self.name = None
        for index in range(count):
            platform, _, _, name_id, length, offset = struct.unpack_from('>HHHHHH', name, 6 + index * 12)
            require(strings + offset + length <= len(name), 'invalid font name range')
            if name_id == 6 and platform in (0, 3):
                try:
                    value = name[strings + offset:strings + offset + length].decode('utf-16-be')
                except UnicodeError as exc:
                    raise GenerationError('invalid font PostScript name') from exc
                if value and len(value) <= 63 and all(33 <= ord(char) <= 126 and char not in '[](){}<>/%' for char in value):
                    self.name = value
                    break
        require(self.name is not None, 'font requires a bounded Unicode PostScript name')

    def table(self, tag, minimum):
        data = self.tables.get(tag, b'')
        require(len(data) >= minimum, 'missing/short font table: ' + tag.decode('ascii'))
        return data

    def glyph(self, code):
        index = bisect.bisect_left(self.ends, code)
        glyph = 0
        if index < len(self.ends):
            if self.form == 12:
                start, end, first = self.groups[index]
                if start <= code <= end:
                    glyph = first + code - start
            elif self.starts[index] <= code:
                distance = self.ranges[index]
                if distance:
                    address = self.range_base + index * 2 + distance + (code - self.starts[index]) * 2
                    require(address + 2 <= len(self.cmap), 'cmap glyph address outside subtable')
                    glyph = struct.unpack_from('>H', self.cmap, address)[0]
                    if glyph:
                        glyph = (glyph + self.deltas[index]) & 0xFFFF
                else:
                    glyph = (code + self.deltas[index]) & 0xFFFF
        require(0 < glyph < self.glyph_count, 'font coverage missing U+' + format(code, '04X'))
        if not chr(code).isspace():
            start, end = self.offsets[glyph:glyph + 2]
            require(end - start >= 10, 'mapped visible character has no glyph outline')
            _, left, bottom, right, top = struct.unpack_from('>hhhhh', self.tables[b'glyf'], start)
            require(self.bounds[0] <= left < right <= self.bounds[2]
                    and self.bounds[1] <= bottom < top <= self.bounds[3], 'glyph bounds missing/outside font global bounds')
        return glyph

    def measure(self, text, size):
        plain_text(text)
        require(bool(text), 'empty text run; use an empty table cell instead')
        glyphs = [self.glyph(ord(char)) for char in text]
        scale = size / self.units
        advances = [struct.unpack_from('>H', self.metrics, min(glyph, self.metric_count - 1) * 4)[0] * scale for glyph in glyphs]
        require(all(0 < value <= size * 8 for value in advances), 'unsupported zero/extreme font advance')
        left = min(0, self.bounds[0] * scale)
        width = max(sum(advances), sum(advances[:-1]) + self.bounds[2] * scale) - left
        return dict(glyphs=glyphs, advances=advances, left=left, top=self.bounds[3] * scale,
                    width=width, height=(self.bounds[3] - self.bounds[1]) * scale)


def inspect_image(data):
    require(0 < len(data) <= 8 * 1024 * 1024, 'image byte limit: 8 MiB')
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        offset = 8
        chunks = []
        compressed = bytearray()
        width = height = channels = 0
        idat_finished = False
        while offset < len(data):
            require(offset + 12 <= len(data) and len(chunks) < 4096, 'invalid PNG chunk count/length')
            size = struct.unpack_from('>I', data, offset)[0]
            kind = data[offset + 4:offset + 8]
            end = offset + size + 12
            require(end <= len(data) and zlib.crc32(data[offset + 4:end - 4]) == struct.unpack_from('>I', data, end - 4)[0], 'invalid PNG CRC/range')
            payload = data[offset + 8:end - 4]
            if not chunks:
                require(kind == b'IHDR' and size == 13, 'PNG requires IHDR first')
                width, height, depth, color, method, filter_method, interlace = struct.unpack('>IIBBBBB', payload)
                require(depth == 8 and color in (0, 2, 4, 6) and method == filter_method == interlace == 0,
                        'PNG profile: noninterlaced 8-bit gray/RGB with optional alpha only')
                channels = {0: 1, 2: 3, 4: 2, 6: 4}[color]
            elif kind == b'IHDR':
                raise GenerationError('duplicate PNG IHDR')
            elif kind == b'IDAT':
                require(not idat_finished, 'noncontiguous PNG IDAT')
                compressed.extend(payload)
            elif b'IDAT' in chunks:
                idat_finished = True
            require(kind in (b'IHDR', b'IDAT', b'IEND') or kind[0] & 32, 'unsupported PNG critical chunk')
            require(kind not in (b'acTL', b'fcTL', b'fdAT', b'eXIf'), 'animated/EXIF PNG not supported')
            chunks.append(kind)
            offset = end
            if kind == b'IEND':
                require(size == 0 and end == len(data), 'invalid PNG IEND/trailing bytes')
                break
        require(chunks[-1:] == [b'IEND'] and compressed, 'incomplete PNG')
        require(0 < width <= 10000 and 0 < height <= 10000 and width * height <= 16000000, 'image pixel limit')
        expected = height * (width * channels + 1)
        require(expected <= 64 * 1024 * 1024, 'PNG decoded scanline limit')
        try:
            decoder = zlib.decompressobj()
            raw = decoder.decompress(compressed, expected + 1)
        except zlib.error as exc:
            raise GenerationError('invalid PNG compressed scanlines') from exc
        require(len(raw) == expected and decoder.eof and not decoder.unused_data and not decoder.unconsumed_tail,
                'PNG scanline size/stream mismatch')
        stride = width * channels + 1
        require(all(raw[index] <= 4 for index in range(0, expected, stride)), 'invalid PNG scanline filter')
        return dict(format='PNG', width=width, height=height, validation='CRC and bounded scanlines; no visual decoding')
    require(data.startswith(b'\xff\xd8') and data.endswith(b'\xff\xd9'), 'image must be supported PNG or baseline JPEG')
    offset = 2
    dimensions = None
    scans = 0
    tables = set()
    components = 0
    while offset < len(data):
        require(data[offset] == 255, 'invalid JPEG marker')
        while offset < len(data) and data[offset] == 255:
            offset += 1
        require(offset < len(data), 'truncated JPEG marker')
        marker = data[offset]
        offset += 1
        if marker == 0xD9:
            require(offset == len(data) and dimensions and scans == 1, 'invalid JPEG end/scan count')
            break
        require(marker not in (0, 0xD8) and not 0xD0 <= marker <= 0xD7 and offset + 2 <= len(data), 'unexpected JPEG marker')
        size = struct.unpack_from('>H', data, offset)[0]
        require(size >= 2 and offset + size <= len(data), 'invalid JPEG segment size')
        payload = data[offset + 2:offset + size]
        offset += size
        require(marker != 0xE1, 'JPEG APP1/EXIF/XMP orientation is outside the profile')
        require(marker not in (0xC8, 0xCC), 'JPEG arithmetic/extensions outside profile')
        if marker in (0xDB, 0xC4):
            require(bool(payload), 'empty JPEG quantization/Huffman table')
            tables.add(marker)
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            require(marker == 0xC0 and dimensions is None and len(payload) >= 6, 'only baseline sequential JPEG supported')
            depth, height, width, channels = struct.unpack_from('>BHHB', payload)
            require(depth == 8 and channels in (1, 3) and len(payload) == 6 + 3 * channels, 'JPEG component profile')
            require(0 < width <= 10000 and 0 < height <= 10000 and width * height <= 16000000, 'image pixel limit')
            dimensions = (width, height)
            components = channels
        if marker == 0xDA:
            require(dimensions is not None and scans == 0, 'JPEG requires one baseline scan')
            require(tables == {0xDB, 0xC4} and len(payload) == 1 + 2 * components + 3
                    and payload[0] == components and payload[-3:] == b'\x00\x3f\x00', 'JPEG baseline scan/table profile')
            scans += 1
            entropy_start = offset
            while offset < len(data):
                if data[offset] == 255:
                    require(offset + 1 < len(data), 'truncated JPEG entropy marker')
                    if data[offset + 1] == 0 or 0xD0 <= data[offset + 1] <= 0xD7:
                        offset += 2
                        continue
                    break
                offset += 1
            require(offset > entropy_start, 'empty JPEG entropy data')
    require(dimensions is not None and scans == 1, 'incomplete JPEG')
    return dict(format='JPEG', width=dimensions[0], height=dimensions[1],
                validation='markers/dimensions only; entropy and visual decoding not performed')
