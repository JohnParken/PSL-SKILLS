"""Bounded PNG/JPEG inspection without decoding or rewriting image pixels.

PNG checks include chunk CRCs and the bounded zlib scanline length. JPEG
checks cover its marker stream and frame/scan headers, not entropy decoding.
The original bytes remain the image embedded in the presentation.
"""

from __future__ import annotations

import binascii
import struct
import zlib
from dataclasses import dataclass


MAX_IMAGE_BYTES = 32 * 1024 * 1024
MAX_IMAGE_DIMENSION = 32768
MAX_IMAGE_PIXELS = 64_000_000
MAX_PNG_SCANLINE_BYTES = 128 * 1024 * 1024
MAX_IMAGE_PARTS = 10000
PNG_SIGNATURE = b'\x89PNG\r\n\x1a\n'
JPEG_SIGNATURE = b'\xff\xd8'
PNG_CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}
PNG_DEPTHS = {0: {1, 2, 4, 8, 16}, 2: {8, 16}, 3: {1, 2, 4, 8}, 4: {8, 16}, 6: {8, 16}}
PNG_PASSES = ((0, 0, 8, 8), (4, 0, 8, 8), (0, 4, 4, 8), (2, 0, 4, 4), (0, 2, 2, 4), (1, 0, 2, 2), (0, 1, 1, 2))


class ImageValidationError(ValueError):
    """Image data is outside the supported, bounded embedding contract."""


@dataclass(frozen=True)
class ImageInfo:
    """Intrinsic dimensions and format of a structurally checked image."""

    format: str
    width: int
    height: int
    has_alpha: bool


def _require_dimensions(width: int, height: int) -> None:
    if not 1 <= width <= MAX_IMAGE_DIMENSION or not 1 <= height <= MAX_IMAGE_DIMENSION:
        raise ImageValidationError(f'image dimensions must be 1..{MAX_IMAGE_DIMENSION} pixels per side')
    if width * height > MAX_IMAGE_PIXELS:
        raise ImageValidationError(f'image exceeds {MAX_IMAGE_PIXELS} pixels')


def _require_exif_orientation(data: bytes | memoryview) -> None:
    """Read only bounded TIFF IFD0 orientation; never walk arbitrary IFD chains."""
    if len(data) < 8 or bytes(data[:2]) not in {b'II', b'MM'}:
        raise ImageValidationError('invalid EXIF TIFF header')
    order = 'little' if bytes(data[:2]) == b'II' else 'big'
    if int.from_bytes(data[2:4], order) != 42:
        raise ImageValidationError('invalid EXIF TIFF signature')
    offset = int.from_bytes(data[4:8], order)
    if offset < 8 or offset + 2 > len(data):
        raise ImageValidationError('EXIF IFD0 is outside its segment')
    count = int.from_bytes(data[offset:offset + 2], order)
    if count > 1024 or offset + 2 + 12 * count + 4 > len(data):
        raise ImageValidationError('EXIF IFD0 is truncated or too large')
    seen = False
    for index in range(count):
        entry = offset + 2 + index * 12
        tag = int.from_bytes(data[entry:entry + 2], order)
        if tag != 274:
            continue
        value_type = int.from_bytes(data[entry + 2:entry + 4], order)
        value_count = int.from_bytes(data[entry + 4:entry + 8], order)
        if seen or value_type != 3 or value_count != 1:
            raise ImageValidationError('invalid or duplicate EXIF orientation')
        seen = True
        orientation = int.from_bytes(data[entry + 8:entry + 10], order)
        if orientation != 1:
            raise ImageValidationError('EXIF orientation must be 1; normalize the source image before embedding')


def _png_scanline_size(width: int, height: int, depth: int, color_type: int, interlace: int) -> int:
    bits_per_pixel = depth * PNG_CHANNELS[color_type]
    passes = PNG_PASSES if interlace else ((0, 0, 1, 1),)
    total = 0
    for start_x, start_y, step_x, step_y in passes:
        pass_width = max(0, (width - start_x + step_x - 1) // step_x)
        pass_height = max(0, (height - start_y + step_y - 1) // step_y)
        if pass_width and pass_height:
            total += pass_height * (1 + (pass_width * bits_per_pixel + 7) // 8)
    if total > MAX_PNG_SCANLINE_BYTES:
        raise ImageValidationError('PNG scanline data exceeds the 128 MiB limit')
    return total


def _validate_png_compression(parts: list[memoryview], expected_size: int) -> None:
    decoder = zlib.decompressobj()
    total = 0
    try:
        for part in parts:
            pending = part
            while pending:
                block = decoder.decompress(pending, 65536)
                total += len(block)
                if total > expected_size:
                    raise ImageValidationError('PNG compressed stream exceeds its declared dimensions')
                if decoder.unused_data:
                    raise ImageValidationError('PNG has extra data after its compressed stream')
                pending = decoder.unconsumed_tail
        while True:
            block = decoder.decompress(b'', 65536)
            total += len(block)
            if total > expected_size:
                raise ImageValidationError('PNG compressed stream exceeds its declared dimensions')
            if not block:
                break
    except zlib.error as exc:
        raise ImageValidationError('PNG compressed stream is corrupt') from exc
    if not decoder.eof or total != expected_size:
        raise ImageValidationError('PNG compressed stream is incomplete or has an invalid scanline length')


def _inspect_png(data: bytes) -> ImageInfo:
    view = memoryview(data)
    offset = len(PNG_SIGNATURE)
    width = height = depth = color_type = expected_size = 0
    palette_entries = 0
    seen: set[bytes] = set()
    image_parts: list[memoryview] = []
    data_ended = False
    part_count = 0
    while offset < len(data):
        part_count += 1
        if part_count > MAX_IMAGE_PARTS or offset + 12 > len(data):
            raise ImageValidationError('PNG chunk stream is truncated or too large')
        length = int.from_bytes(view[offset:offset + 4], 'big')
        chunk_type = bytes(view[offset + 4:offset + 8])
        chunk_end = offset + 12 + length
        if chunk_end > len(data) or not all(65 <= byte <= 90 or 97 <= byte <= 122 for byte in chunk_type):
            raise ImageValidationError('invalid PNG chunk header')
        if chunk_type[2] & 32:
            raise ImageValidationError('invalid PNG reserved chunk bit')
        chunk = view[offset + 8:chunk_end - 4]
        expected_crc = int.from_bytes(view[chunk_end - 4:chunk_end], 'big')
        if binascii.crc32(view[offset + 4:chunk_end - 4]) & 0xffffffff != expected_crc:
            raise ImageValidationError('PNG chunk CRC mismatch')
        if not seen and chunk_type != b'IHDR':
            raise ImageValidationError('PNG must begin with IHDR')
        if chunk_type in {b'IHDR', b'PLTE', b'tRNS', b'eXIf'} and chunk_type in seen:
            raise ImageValidationError('duplicate PNG header or metadata chunk')
        if chunk_type in {b'acTL', b'fcTL', b'fdAT'}:
            raise ImageValidationError('animated PNG is not supported')
        if image_parts and chunk_type != b'IDAT':
            data_ended = True
        if chunk_type == b'IHDR':
            if length != 13:
                raise ImageValidationError('PNG IHDR must contain 13 bytes')
            width, height, depth, color_type, compression, filtering, interlace = struct.unpack('>IIBBBBB', chunk)
            _require_dimensions(width, height)
            if depth not in PNG_DEPTHS.get(color_type, set()) or compression != 0 or filtering != 0 or interlace not in {0, 1}:
                raise ImageValidationError('unsupported PNG IHDR fields')
            expected_size = _png_scanline_size(width, height, depth, color_type, interlace)
        elif chunk_type == b'PLTE':
            if image_parts or color_type in {0, 4} or not 3 <= length <= 768 or length % 3:
                raise ImageValidationError('invalid PNG palette')
            palette_entries = length // 3
            if color_type == 3 and palette_entries > 2 ** depth:
                raise ImageValidationError('PNG palette exceeds its bit depth')
        elif chunk_type == b'tRNS':
            if image_parts or color_type in {4, 6}:
                raise ImageValidationError('invalid PNG transparency chunk order or color type')
            if color_type == 3:
                if not palette_entries or not 1 <= length <= palette_entries:
                    raise ImageValidationError('PNG transparency requires a matching palette')
            elif length != {0: 2, 2: 6}.get(color_type):
                raise ImageValidationError('invalid PNG transparent sample length')
            elif any(int.from_bytes(chunk[index:index + 2], 'big') >= 2 ** depth for index in range(0, length, 2)):
                raise ImageValidationError('PNG transparent sample exceeds its bit depth')
        elif chunk_type == b'eXIf':
            _require_exif_orientation(chunk)
        elif chunk_type == b'IDAT':
            if data_ended or color_type == 3 and not palette_entries:
                raise ImageValidationError('PNG IDAT chunks must be contiguous and follow the palette')
            image_parts.append(chunk)
        elif chunk_type == b'IEND':
            if length != 0 or not image_parts or chunk_end != len(data):
                raise ImageValidationError('PNG is incomplete or contains trailing data')
            _validate_png_compression(image_parts, expected_size)
            return ImageInfo('png', width, height, color_type in {4, 6} or b'tRNS' in seen)
        elif not chunk_type[0] & 32:
            raise ImageValidationError('unknown critical PNG chunk')
        seen.add(chunk_type)
        offset = chunk_end
    raise ImageValidationError('PNG is missing IEND')


def _jpeg_quantization_tables(segment: memoryview, tables: set[int]) -> None:
    offset = 0
    while offset < len(segment):
        descriptor = segment[offset]
        precision = descriptor >> 4
        table_id = descriptor & 15
        length = 64 * (precision + 1)
        if precision not in {0, 1} or table_id > 3 or offset + 1 + length > len(segment):
            raise ImageValidationError('invalid JPEG quantization table')
        tables.add(table_id)
        offset += 1 + length
    if offset == 0:
        raise ImageValidationError('empty JPEG quantization segment')


def _jpeg_huffman_tables(segment: memoryview, tables: set[tuple[int, int]]) -> None:
    offset = 0
    while offset < len(segment):
        if offset + 17 > len(segment):
            raise ImageValidationError('truncated JPEG Huffman table')
        descriptor = segment[offset]
        table_class, table_id = descriptor >> 4, descriptor & 15
        counts = segment[offset + 1:offset + 17]
        symbols = sum(counts)
        if table_class > 1 or table_id > 3 or not 1 <= symbols <= 256 or offset + 17 + symbols > len(segment):
            raise ImageValidationError('invalid JPEG Huffman table')
        available = 1
        for count in counts:
            available = available * 2 - count
            if available < 0:
                raise ImageValidationError('oversubscribed JPEG Huffman table')
        tables.add((table_class, table_id))
        offset += 17 + symbols
    if offset == 0:
        raise ImageValidationError('empty JPEG Huffman segment')


def _jpeg_frame(segment: memoryview) -> tuple[int, int, dict[int, int]]:
    if len(segment) < 6:
        raise ImageValidationError('truncated JPEG frame header')
    precision, height, width, count = struct.unpack('>BHHB', segment[:6])
    if precision != 8 or count not in {1, 3} or len(segment) != 6 + 3 * count:
        raise ImageValidationError('JPEG requires an 8-bit grayscale or three-component frame')
    _require_dimensions(width, height)
    components: dict[int, int] = {}
    for index in range(count):
        component_id, sampling, table_id = segment[6 + 3 * index:9 + 3 * index]
        if component_id in components or not 1 <= sampling >> 4 <= 4 or not 1 <= sampling & 15 <= 4 or table_id > 3:
            raise ImageValidationError('invalid JPEG frame component')
        components[component_id] = table_id
    return width, height, components


def _jpeg_scan(
    segment: memoryview,
    components: dict[int, int],
    progressive: bool,
    quantization_tables: set[int],
    huffman_tables: set[tuple[int, int]],
) -> set[int]:
    if not segment:
        raise ImageValidationError('empty JPEG scan header')
    count = segment[0]
    if not 1 <= count <= len(components) or len(segment) != 4 + 2 * count:
        raise ImageValidationError('invalid JPEG scan header length')
    spectral_start, spectral_end, approximation = segment[-3:]
    high, low = approximation >> 4, approximation & 15
    if not 0 <= spectral_start <= spectral_end <= 63 or high > 13 or low > 13:
        raise ImageValidationError('invalid JPEG spectral selection')
    if progressive:
        if spectral_start == 0 and spectral_end != 0 or spectral_start != 0 and count != 1 or high and high != low + 1:
            raise ImageValidationError('invalid progressive JPEG scan')
    elif (spectral_start, spectral_end, approximation) != (0, 63, 0):
        raise ImageValidationError('invalid sequential JPEG scan')
    selected: set[int] = set()
    for index in range(count):
        component_id, selectors = segment[1 + 2 * index:3 + 2 * index]
        dc_table, ac_table = selectors >> 4, selectors & 15
        if component_id not in components or component_id in selected or dc_table > 3 or ac_table > 3:
            raise ImageValidationError('invalid JPEG scan component')
        if components[component_id] not in quantization_tables:
            raise ImageValidationError('JPEG scan references a missing quantization table')
        if spectral_start == 0 and high == 0 and (0, dc_table) not in huffman_tables:
            raise ImageValidationError('JPEG scan references a missing DC Huffman table')
        if (not progressive or spectral_start != 0) and (1, ac_table) not in huffman_tables:
            raise ImageValidationError('JPEG scan references a missing AC Huffman table')
        selected.add(component_id)
    return selected if spectral_start == 0 else set()


def _inspect_jpeg(data: bytes) -> ImageInfo:
    view = memoryview(data)
    offset = 2
    part_count = 0
    scan_active = False
    scan_bytes = 0
    scan_count = 0
    width = height = 0
    components: dict[int, int] = {}
    covered: set[int] = set()
    quantization_tables: set[int] = set()
    huffman_tables: set[tuple[int, int]] = set()
    progressive = False
    saw_exif = False
    while offset < len(data):
        if scan_active:
            marker_start = data.find(b'\xff', offset)
            if marker_start < 0:
                raise ImageValidationError('JPEG scan is missing its closing marker')
            scan_bytes += marker_start - offset
            marker_position = marker_start + 1
            while marker_position < len(data) and data[marker_position] == 0xff:
                marker_position += 1
            if marker_position >= len(data):
                raise ImageValidationError('truncated JPEG scan marker')
            marker = data[marker_position]
            if marker == 0 or 0xd0 <= marker <= 0xd7:
                scan_bytes += int(marker == 0)
                offset = marker_position + 1
                continue
            if not scan_bytes:
                raise ImageValidationError('empty JPEG entropy scan')
            scan_active = False
            offset = marker_start
        part_count += 1
        if part_count > MAX_IMAGE_PARTS or data[offset] != 0xff:
            raise ImageValidationError('invalid or excessive JPEG markers')
        while offset < len(data) and data[offset] == 0xff:
            offset += 1
        if offset >= len(data):
            raise ImageValidationError('truncated JPEG marker')
        marker = data[offset]
        offset += 1
        if marker == 0xd9:
            if not components or not scan_count or covered != set(components) or offset != len(data):
                raise ImageValidationError('JPEG is incomplete or contains trailing data')
            return ImageInfo('jpg', width, height, False)
        if marker in {0, 1, 0xd8} or 0xd0 <= marker <= 0xd7 or offset + 2 > len(data):
            raise ImageValidationError('unexpected JPEG marker')
        length = int.from_bytes(view[offset:offset + 2], 'big')
        if length < 2 or offset + length > len(data):
            raise ImageValidationError('truncated JPEG segment')
        segment = view[offset + 2:offset + length]
        offset += length
        if marker in {0xc0, 0xc2}:
            if components:
                raise ImageValidationError('multiple JPEG frames are not supported')
            width, height, components = _jpeg_frame(segment)
            progressive = marker == 0xc2
        elif marker == 0xda:
            if not components:
                raise ImageValidationError('JPEG scan precedes its frame')
            covered.update(_jpeg_scan(segment, components, progressive, quantization_tables, huffman_tables))
            scan_active = True
            scan_bytes = 0
            scan_count += 1
        elif marker == 0xdb:
            _jpeg_quantization_tables(segment, quantization_tables)
        elif marker == 0xc4:
            _jpeg_huffman_tables(segment, huffman_tables)
        elif marker == 0xdd:
            if len(segment) != 2:
                raise ImageValidationError('invalid JPEG restart interval')
        elif marker == 0xe1 and bytes(segment[:6]) == b'Exif\x00\x00':
            if saw_exif:
                raise ImageValidationError('duplicate JPEG EXIF segment')
            _require_exif_orientation(segment[6:])
            saw_exif = True
        elif not (0xe0 <= marker <= 0xef or marker == 0xfe):
            raise ImageValidationError('unsupported JPEG coding marker')
    raise ImageValidationError('JPEG is missing EOI')


def inspect_image(data: bytes, declared_format: str | None = None) -> ImageInfo:
    """Inspect a bounded PNG/JPEG; reject other formats and orientation changes.

    This validates container and header structure, not the visual correctness
    of every pixel. JPEG entropy data is not decoded. No source data is changed.
    """
    if not isinstance(data, bytes) or not data or len(data) > MAX_IMAGE_BYTES:
        raise ImageValidationError('image must be non-empty bytes no larger than 32 MiB')
    expected = None
    if declared_format is not None:
        if not isinstance(declared_format, str):
            raise ImageValidationError('declared image format must be a string')
        expected = {'png': 'png', 'jpg': 'jpg', 'jpeg': 'jpg'}.get(declared_format.lower().lstrip('.'))
        if expected is None:
            raise ImageValidationError('only PNG and JPEG images are supported')
    if data.startswith(PNG_SIGNATURE):
        result = _inspect_png(data)
    elif data.startswith(JPEG_SIGNATURE):
        result = _inspect_jpeg(data)
    else:
        raise ImageValidationError('only PNG and JPEG image signatures are supported')
    if expected is not None and expected != result.format:
        raise ImageValidationError('image signature does not match its declared format')
    return result
