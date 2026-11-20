"""FIB, CLX, and bounded stream-region primitives for MS-DOC binary parsing."""
from dataclasses import dataclass
import struct
import sys
from typing import Any

from wps_access import ParseError
from wps_cfb import MAX_FILE_BYTES, MAX_STREAM_BYTES


# MS-DOC 2.5.1/2.5.11/2.5.14, corroborated by local C1/101/10C samples.
# (fc/lcb pairs, extension words including nFibNew, exclusive FIB end).
FIB_LAYOUTS = {0x00C1: (93, 0, 900), 0x00D9: (108, 2, 1024),
               0x0101: (136, 2, 1248), 0x010C: (164, 2, 1472),
               0x0112: (183, 5, 1630)}
CLX_PAIR_INDEX = 33
FC_LCB_START = 154
MAX_UNITS = 8 * 1024 * 1024
MAX_PIECES = 100000
MAX_CLX_BYTES = 4 * 1024 * 1024
MAX_PRC_RECORDS = 4096
MAX_PARAGRAPHS = 100000
MAX_FIELD_DEPTH = 64
MAX_FIELDS = 100000
MAX_OBJECT_MARKERS = 1000
MAX_TEXTBOX_COPY_UNITS = 65536
TEXTBOX_STORIES = {'textboxes': (56, 'main'), 'header_textboxes': (58, 'headers_footers')}
# MS-DOC FcCompressed substitutions recorded by the local binary_doc.py.
COMPRESSED_MAPPING = dict(zip(
    (0x82, 0x83, 0x84, 0x85, 0x86, 0x87, 0x88, 0x89, 0x8A, 0x8B, 0x8C,
     0x91, 0x92, 0x93, 0x94, 0x95, 0x96, 0x97, 0x98, 0x99, 0x9A, 0x9B, 0x9C, 0x9F),
    (0x201A, 0x0192, 0x201E, 0x2026, 0x2020, 0x2021, 0x02C6, 0x2030,
     0x0160, 0x2039, 0x0152, 0x2018, 0x2019, 0x201C, 0x201D, 0x2022,
     0x2013, 0x2014, 0x02DC, 0x2122, 0x0161, 0x203A, 0x0153, 0x0178)))


def require(condition, location, message, status='corrupt'):
    """Raise ParseError with normalized status and location when condition is false."""
    if not condition:
        raise ParseError(status, location, message)


class Region:
    """A bounded view retaining absolute stream offsets for diagnostics."""

    def __init__(self, data, stream, base=0):
        self.data = memoryview(data)
        self.stream = stream
        self.base = base

    def location(self, offset=0, **extra):
        return {'stream': self.stream, 'offset': self.base + offset, **extra}

    def view(self, offset, size):
        require(0 <= offset <= len(self.data) and 0 <= size <= len(self.data) - offset,
                self.location(offset), 'Structure outside stream/region')
        return self.data[offset:offset + size]

    def number(self, offset, width=4):
        return int.from_bytes(self.view(offset, width), 'little')

    def region(self, offset, size):
        return Region(self.view(offset, size), self.stream, self.base + offset)


def runtime_limit(name, default):
    """Look up dynamically patched module limits from read_wps when loaded."""
    facade = sys.modules.get('read_wps')
    return getattr(facade, name, default) if facade is not None else default


@dataclass
class FibHeader:
    """Validated File Information Block summary for MS-DOC streams."""

    word: Any
    table: Any
    table_name: str
    base_version: int
    version: int
    pair_count: int
    expected_new: int
    extension_offset: int
    missing_extension: bool
    fib_end: int
    cb_mac: int
    main_count: int
    other_counts: list
    total_count: int


def validate_stream_inputs(streams, *, text_policy, field_policy, object_policy,
                           hidden_text, binary_paragraph_sources, content_only,
                           maximum, include_image_bytes):
    """Validate parse_streams options and container stream budgets."""
    require(text_policy in ('strict', 'recover'), {'stage': 'options'}, 'Unknown binary text policy', 'unsupported')
    require(field_policy in ('reject', 'cached_result'), {'stage': 'options'},
            'Unknown MS-DOC field policy', 'unsupported')
    require(object_policy in ('reject', 'placeholder'), {'stage': 'options'},
            'Unknown MS-DOC object policy', 'unsupported')
    require(hidden_text in ('include', 'annotate'), {'stage': 'options'},
            'Effective binary visibility is unknown; exclude unsupported', 'unsupported')
    require(type(binary_paragraph_sources) is bool, {'stage': 'options'},
            'binary_paragraph_sources must be boolean', 'unsupported')
    require(type(content_only) is bool and (not content_only or maximum and not include_image_bytes),
            {'stage': 'options'}, 'Content-only binary reading requires maximum recovery without image bytes', 'unsupported')
    require(not include_image_bytes or maximum, {'stage': 'options'}, 'Image bytes require maximum reading', 'unsupported')
    require(len(streams) <= 4096, {'stage': 'streams'}, 'Stream count budget exceeded', 'resource_limit')
    aggregate = 0
    for path, data in streams.items():
        aggregate += len(data)
        require(len(data) <= MAX_STREAM_BYTES and aggregate <= MAX_FILE_BYTES,
                {'stream': '/'.join(path), 'offset': 0}, 'Stream byte budget exceeded', 'resource_limit')
    require(('WordDocument',) in streams, {'stream': 'WordDocument', 'offset': 0},
            'Missing MS-DOC stream', 'unsupported')


def parse_fib_header(streams, region_cls, maximum):
    """Validate the WordDocument FIB header and return a FibHeader."""
    word = region_cls(streams[('WordDocument',)], 'WordDocument')
    require(word.number(0, 2) == 0xA5EC, word.location(), 'Unknown stream signature', 'unsupported')
    base_version = word.number(2, 2)
    require(base_version in FIB_LAYOUTS, word.location(2),
            'Only evidenced C1/D9/101/10C/112 base FIB versions are supported', 'unsupported')
    flags = word.number(10, 2)
    require(not flags & 0x0100, word.location(10), 'Encrypted FIB', 'encrypted')
    require(word.number(14) == 0, word.location(14), 'Nonzero lKey in unencrypted FIB', 'corrupt')
    require(not flags & 3 and word.number(8, 2) == 0, word.location(8),
            'Templates/AutoText outside subset', 'unsupported')
    require(flags & 0x1000, word.location(10), 'Non-Unicode FIB outside subset', 'unsupported')
    require(word.number(12, 2) in (0x00BF, 0x00C1), word.location(12),
            'Invalid nFibBack for MS-DOC', 'corrupt')
    require(word.number(18, 1) == 0, word.location(18), 'Non-Windows environment outside subset', 'unsupported')
    require(word.number(32, 2) == 14, word.location(32), 'Unsupported csw', 'unsupported')
    require(word.number(62, 2) == 22, word.location(62), 'Unsupported cslw', 'unsupported')
    pair_count = word.number(152, 2)
    require(pair_count in (93, 108, 136, 164, 183), word.location(152),
            'Unsupported fc/lcb pair count', 'unsupported')
    extension_offset = FC_LCB_START + pair_count * 8
    csw_new = word.number(extension_offset, 2)
    require(csw_new in (0, 2, 5), word.location(extension_offset),
            'Unsupported FIB extension length', 'unsupported')
    version = word.number(extension_offset + 2, 2) if csw_new else base_version
    require(version in FIB_LAYOUTS, word.location(extension_offset + 2 if csw_new else 2),
            'Unsupported effective nFibNew version', 'unsupported')
    expected_pairs, expected_new, fib_end = FIB_LAYOUTS[version]
    missing_extension = maximum and csw_new == 0 and expected_new != 0 and pair_count == expected_pairs
    require(pair_count == expected_pairs and (csw_new == expected_new or missing_extension),
            word.location(152), 'Inconsistent FIB version/array/extension layout', 'unsupported')
    if missing_extension:
        fib_end = extension_offset + 2
    word.view(0, fib_end)
    if csw_new:
        require(word.number(extension_offset + 4, 2) <= 15,
                word.location(extension_offset + 4), 'Invalid cQuickSavesNew')
        if version == 0x0112:
            word.view(extension_offset + 6, 6)
    cb_mac = word.number(64)
    require(fib_end <= cb_mac <= len(word.data), word.location(64), 'Invalid cbMac')
    main_count = word.number(76)
    require(word.number(88) == 0, word.location(88), 'Nonzero reserved macro-story count', 'unsupported')
    other_counts = [word.number(offset) for offset in (80, 84, 92, 96, 100, 104)]
    total_count = main_count + sum(other_counts) + int(any(other_counts))
    max_units = runtime_limit('MAX_UNITS', MAX_UNITS)
    require(total_count <= max_units, word.location(76), 'Text unit budget exceeded', 'resource_limit')
    table_name = '1Table' if flags & 0x0200 else '0Table'
    require((table_name,) in streams, {'stream': table_name, 'offset': 0}, 'Missing selected table')
    table = region_cls(streams[(table_name,)], table_name)
    return FibHeader(
        word=word, table=table, table_name=table_name, base_version=base_version,
        version=version, pair_count=pair_count, expected_new=expected_new,
        extension_offset=extension_offset, missing_extension=missing_extension,
        fib_end=fib_end, cb_mac=cb_mac, main_count=main_count,
        other_counts=other_counts, total_count=total_count,
    )


def parse_clx_pieces(fib, maximum):
    """Validate and decode CLX/PlcPcd piece boundaries and PRM indices."""
    word, table = fib.word, fib.table
    pair_offset = FC_LCB_START + 8 * CLX_PAIR_INDEX
    clx_offset = word.number(pair_offset)
    clx_size = word.number(pair_offset + 4)
    require(clx_size <= MAX_CLX_BYTES, word.location(pair_offset + 4), 'CLX budget exceeded', 'resource_limit')
    clx = table.region(clx_offset, clx_size)
    cursor = 0
    prc_count = 0
    while cursor < len(clx.data) and clx.number(cursor, 1) == 1:
        prc_count += 1
        require(prc_count <= MAX_PRC_RECORDS, clx.location(cursor), 'Prc budget exceeded', 'resource_limit')
        length = clx.number(cursor + 1, 2)
        require(length <= 0x3FA2, clx.location(cursor + 1), 'Invalid Prc length')
        clx.view(cursor + 3, length)
        cursor += 3 + length
    require(cursor < len(clx.data) and clx.number(cursor, 1) == 2,
            clx.location(cursor), 'Missing Pcdt')
    length = clx.number(cursor + 1)
    require(length >= 4 and (length - 4) % 12 == 0 and cursor + 5 + length == len(clx.data),
            clx.location(cursor + 1), 'Invalid PlcPcd length/trailing CLX data')
    piece_count = (length - 4) // 12
    require(piece_count <= MAX_PIECES, clx.location(cursor + 1), 'Piece budget exceeded', 'resource_limit')
    plc = clx.region(cursor + 5, length)
    cp_bytes = 4 * (piece_count + 1)
    positions = struct.unpack_from('<' + 'I' * (piece_count + 1), plc.view(0, cp_bytes))
    require(positions[0] == 0, plc.location(), 'First CP must be zero')
    for index in range(piece_count):
        require(positions[index] < positions[index + 1], plc.location(4 * (index + 1), piece=index),
                'CP indexes must strictly increase')
    require(positions[-1] == fib.total_count, plc.location(4 * piece_count), 'CP/story length mismatch')
    pieces = []
    piece_properties = False
    piece_prms = [] if maximum else None
    for index in range(piece_count):
        descriptor = cp_bytes + 8 * index
        location = plc.location(descriptor + 2, piece=index)
        encoded_fc = plc.number(descriptor + 2)
        require(not encoded_fc & 0x80000000, location, 'Reserved FC flag')
        compressed = bool(encoded_fc & 0x40000000)
        file_offset = encoded_fc & 0x3FFFFFFF
        require(not compressed or file_offset % 2 == 0, location, 'Odd compressed FC')
        file_offset = file_offset // 2 if compressed else file_offset
        stride = 1 if compressed else 2
        units = positions[index + 1] - positions[index]
        end = file_offset + units * stride
        require(fib.fib_end <= file_offset < end <= fib.cb_mac, location,
                'Piece outside meaningful stream or overlaps FIB')
        prm = plc.number(descriptor + 6, 2)
        require(not prm & 1 or (prm >> 1) < prc_count,
                plc.location(descriptor + 6, piece=index), 'Invalid Prm1 Prc index')
        piece_properties = piece_properties or prm != 0
        if piece_prms is not None:
            piece_prms.append(prm)
        pieces.append((file_offset, end, stride, index))
    ordered = sorted(pieces)
    for left, right in zip(ordered, ordered[1:]):
        require(left[1] <= right[0], word.location(right[0], piece=right[3]), 'Overlapping text pieces')
    return clx_offset, clx_size, pieces, positions, piece_properties, piece_prms
