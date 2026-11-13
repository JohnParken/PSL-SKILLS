"""Read bounded PNG BLIP records from MS-DOC OfficeArt structures."""

import zlib


FBSE_RECORD = 0xF007
PNG_BLIP_RECORD = 0xF01E
INLINE_SP_CONTAINER = 0xF004
PNG_BLIP_TYPE = 6
PNG_BLIP_INSTANCES = {0x6E0: 17, 0x6E1: 33}
PNG_SIGNATURE = b'\x89PNG\r\n\x1a\n'
PNG_IHDR = b'IHDR'
PNG_IDAT = b'IDAT'
PNG_IEND = b'IEND'
PNG_HEADER_BYTES = 8
PNG_CHUNK_MAX = 8192
PICF_HEADER_BYTES = 0x44
MM_SHAPE_PICTURE = 0x64
MM_SHAPEFILE = 0x66
NO_DELAY_OFFSET = 0xFFFFFFFF
FBSE_FIXED_BYTES = 36


def _record_header(region, offset, parent_end, require):
    """Return one bounded OfficeArt header and its absolute record end."""
    require(0 <= offset <= parent_end <= len(region.data),
            region.location(offset), 'Invalid OfficeArt parent extent')
    require(parent_end - offset >= 8, region.location(offset),
            'Truncated OfficeArt record header')
    ver_instance = region.number(offset, 2)
    record_type = region.number(offset + 2, 2)
    payload_size = region.number(offset + 4, 4)
    payload_start = offset + 8
    require(payload_size <= parent_end - payload_start,
            region.location(offset + 4), 'OfficeArt record exceeds its parent')
    return (ver_instance & 0x0F, ver_instance >> 4, record_type,
            payload_start, payload_start + payload_size)


def _exact_record_header(region, require):
    """Validate that a Region contains exactly one complete OfficeArt record."""
    version, instance, record_type, payload_start, record_end = _record_header(
        region, 0, len(region.data), require)
    require(record_end == len(region.data), region.location(record_end),
            'Trailing bytes after OfficeArt record')
    return version, instance, record_type, payload_start, record_end


def _parse_png(png, require):
    """Validate PNG chunk framing and checksums without inflating image data."""
    require(len(png.data) >= PNG_HEADER_BYTES,
            png.location(), 'Truncated PNG signature')
    require(bytes(png.view(0, PNG_HEADER_BYTES)) == PNG_SIGNATURE,
            png.location(), 'Invalid PNG signature')

    cursor = PNG_HEADER_BYTES
    chunk_count = 0
    width = None
    height = None
    seen_idat = False
    seen_iend = False

    while cursor < len(png.data):
        chunk_count += 1
        require(chunk_count <= PNG_CHUNK_MAX, png.location(cursor),
                'PNG chunk budget exceeded', status='resource_limit')
        require(len(png.data) - cursor >= 12, png.location(cursor),
                'Truncated PNG chunk header or CRC')
        chunk_size = int.from_bytes(png.view(cursor, 4), 'big')
        chunk_type_offset = cursor + 4
        chunk_type = bytes(png.view(chunk_type_offset, 4))
        chunk_data_offset = cursor + 8
        require(chunk_size <= len(png.data) - cursor - 12,
                png.location(cursor), 'PNG chunk exceeds its payload')
        crc_offset = chunk_data_offset + chunk_size
        chunk_end = crc_offset + 4
        actual_crc = zlib.crc32(png.view(chunk_type_offset, 4))
        actual_crc = zlib.crc32(
            png.view(chunk_data_offset, chunk_size), actual_crc) & 0xFFFFFFFF
        expected_crc = int.from_bytes(png.view(crc_offset, 4), 'big')
        require(actual_crc == expected_crc, png.location(crc_offset),
                'PNG chunk CRC mismatch')

        if chunk_count == 1:
            require(chunk_type == PNG_IHDR and chunk_size == 13,
                    png.location(cursor), 'PNG must begin with one 13-byte IHDR')
            width = int.from_bytes(png.view(chunk_data_offset, 4), 'big')
            height = int.from_bytes(png.view(chunk_data_offset + 4, 4), 'big')
            require(0 < width <= 0x7FFFFFFF and 0 < height <= 0x7FFFFFFF,
                    png.location(chunk_data_offset), 'Invalid PNG dimensions')
        else:
            require(chunk_type != PNG_IHDR, png.location(cursor),
                    'PNG contains a repeated IHDR')

        if chunk_type == PNG_IDAT:
            seen_idat = True
        if chunk_type == PNG_IEND:
            require(chunk_size == 0, png.location(cursor),
                    'PNG IEND must be empty')
            require(seen_idat, png.location(cursor), 'PNG has no IDAT chunk')
            require(chunk_end == len(png.data), png.location(chunk_end),
                    'Trailing bytes after PNG IEND')
            seen_iend = True
            break
        cursor = chunk_end

    require(seen_iend, png.location(cursor), 'PNG is missing its final IEND')
    return width, height


def _parse_png_blip(blip, require, reserve):
    """Validate one OfficeArtBlipPNG record and its PNG byte range."""
    version, instance, record_type, payload_start, record_end = (
        _exact_record_header(blip, require))
    require(record_type == PNG_BLIP_RECORD, blip.location(2),
            'OfficeArt block is not a PNG BLIP', status='unsupported')
    require(version == 0 and instance in PNG_BLIP_INSTANCES,
            blip.location(), 'Unsupported OfficeArtBlipPNG variant',
            status='unsupported')
    prefix_size = PNG_BLIP_INSTANCES[instance]
    require(record_end - payload_start >= prefix_size,
            blip.location(payload_start), 'Truncated OfficeArtBlipPNG prefix')
    png_start = payload_start + prefix_size
    png = blip.region(png_start, record_end - png_start)
    if reserve is not None:
        reserve(len(png.data))
    width, height = _parse_png(png, require)
    return width, height, png, blip


def _validate_fbse_name(block, name_start, name_size, require):
    """Validate the bounded UTF-16 name framing without interpreting it."""
    require(name_size <= 0xFE and name_size % 2 == 0,
            block.location(8 + 33), 'Invalid FBSE name size')
    require(name_size <= len(block.data) - name_start,
            block.location(name_start), 'FBSE name exceeds its record')
    if not name_size:
        return name_start
    require(name_size >= 2 and bytes(block.view(name_start + name_size - 2, 2)) == b'\x00\x00',
            block.location(name_start), 'FBSE name lacks its UTF-16 terminator')
    for offset in range(name_start, name_start + name_size - 2, 2):
        require(bytes(block.view(offset, 2)) != b'\x00\x00',
                block.location(offset), 'FBSE name contains an early terminator')
    return name_start + name_size


def _parse_fbse(block, require, *, delay=None, delay_exclusions=(),
                allow_delay=True, reserve=None):
    """Read an embedded or delayed PNG from one exact OfficeArtFBSE."""
    version, instance, record_type, payload_start, record_end = (
        _exact_record_header(block, require))
    require(record_type == FBSE_RECORD, block.location(2),
            'OfficeArt block is not an FBSE', status='unsupported')
    require(version == 2 and instance == PNG_BLIP_TYPE,
            block.location(), 'Unsupported OfficeArtFBSE variant',
            status='unsupported')
    payload_size = record_end - payload_start
    require(payload_size >= FBSE_FIXED_BYTES, block.location(payload_start),
            'Truncated OfficeArtFBSE fixed header')
    fixed = block.view(payload_start, FBSE_FIXED_BYTES)
    win32_type = fixed[0]
    mac_type = fixed[1]
    require(PNG_BLIP_TYPE in (win32_type, mac_type),
            block.location(payload_start), 'OfficeArtFBSE is not PNG',
            status='unsupported')
    declared_blip_size = int.from_bytes(fixed[20:24], 'little')
    delayed_offset = int.from_bytes(fixed[28:32], 'little')
    name_size = fixed[33]
    require(declared_blip_size >= 8, block.location(payload_start + 20),
            'Invalid FBSE BLIP size')
    name_start = payload_start + FBSE_FIXED_BYTES
    blip_start = _validate_fbse_name(block, name_start, name_size, require)
    embedded_size = record_end - blip_start

    if embedded_size:
        require(embedded_size == declared_blip_size,
                block.location(blip_start),
                'Embedded FBSE BLIP size does not match its record')
        blip = block.region(blip_start, embedded_size)
        return _parse_png_blip(blip, require, reserve)

    require(delayed_offset != NO_DELAY_OFFSET, block.location(payload_start + 28),
            'FBSE has neither embedded nor delayed image data')
    require(allow_delay, block.location(payload_start + 28),
            'Inline FBSE must contain an embedded PNG', status='unsupported')
    require(delay is not None, block.location(payload_start + 28),
            'Delayed FBSE requires the WordDocument stream',
            status='unsupported')
    require(getattr(delay, 'base', None) == 0 and
            getattr(delay, 'stream', None) == 'WordDocument',
            block.location(payload_start + 28),
            'Delayed FBSE requires WordDocument at absolute offset zero',
            status='unsupported')
    require(delayed_offset <= len(delay.data) and
            declared_blip_size <= len(delay.data) - delayed_offset,
            delay.location(delayed_offset), 'Delayed FBSE BLIP exceeds WordDocument')
    delayed_end = delayed_offset + declared_blip_size
    require(all(delayed_end <= start or end <= delayed_offset
                for start, end in delay_exclusions),
            delay.location(delayed_offset),
            'Delayed FBSE BLIP overlaps a protected WordDocument range')
    blip = delay.region(delayed_offset, declared_blip_size)
    return _parse_png_blip(blip, require, reserve)


def _result(width, height, png, record_source):
    """Build the fixed result shape for callers."""
    return {
        'width': width,
        'height': height,
        'source': png.location(0, size=len(png.data)),
        'record_source': record_source,
        'payload': png.data,
    }


def read_stored_png(block, require, *, delay=None, delay_exclusions=(), reserve=None):
    """Read a complete FBSE or PNG BLIP using caller-validated protection ranges."""
    _, _, record_type, _, _ = _exact_record_header(block, require)
    if record_type == PNG_BLIP_RECORD:
        width, height, png, blip = _parse_png_blip(block, require, reserve)
    elif record_type == FBSE_RECORD:
        width, height, png, blip = _parse_fbse(
            block, require, delay=delay,
            delay_exclusions=delay_exclusions, reserve=reserve)
    else:
        require(False, block.location(2), 'Unsupported OfficeArt image block',
                status='unsupported')
        raise ValueError('Image validation callback must reject unsupported records')
    return _result(width, height, png,
                   blip.location(0, size=len(blip.data)))


def inline_picture_shape(data, offset, require):
    """Return the bounded PICF and its direct OfficeArt SpContainer."""
    require(type(offset) is int, data.location(), 'Invalid PICF offset')
    require(0 <= offset <= len(data.data) - PICF_HEADER_BYTES,
            data.location(offset), 'PICF header exceeds the Data Region')
    picf_size = int.from_bytes(data.view(offset, 4), 'little', signed=True)
    require(picf_size >= PICF_HEADER_BYTES,
            data.location(offset), 'Invalid PICF lcb')
    require(picf_size <= len(data.data) - offset,
            data.location(offset), 'PICF extent exceeds the Data Region')
    cb_header = data.number(offset + 4, 2)
    require(cb_header == PICF_HEADER_BYTES,
            data.location(offset + 4), 'Unsupported PICF cbHeader')
    mm = data.number(offset + 6, 2)
    if mm == MM_SHAPEFILE:
        require(False, data.location(offset + 6),
                'Linked picture paths are outside the supported subset',
                status='unsupported')
    require(mm == MM_SHAPE_PICTURE, data.location(offset + 6),
            'Unsupported PICF MFPF type', status='unsupported')
    picture = data.region(offset, picf_size)
    version, instance, record_type, _, container_end = _record_header(
        picture, cb_header, picf_size, require)
    require(record_type == INLINE_SP_CONTAINER,
            picture.location(cb_header + 2),
            'PICF picture does not begin with a SpContainer',
            status='unsupported')
    require(version == 15 and instance == 0,
            picture.location(cb_header), 'Unsupported inline SpContainer',
            status='unsupported')
    return picture, picture.region(cb_header, container_end - cb_header)


def read_inline_png_preview(picture, shape, require, *, reserve=None):
    """Read a single stored PNG from a validated inline picture."""
    container_end = shape.base - picture.base + len(shape.data)
    require(container_end < len(picture.data), picture.location(container_end),
            'Inline OfficeArt has no PNG image block', status='unsupported')
    _, _, child_type, _, child_end = _record_header(picture, container_end, len(picture.data), require)
    require(child_end == len(picture.data), picture.location(child_end),
            'Inline OfficeArt contains multiple image blocks', status='unsupported')
    child = picture.region(container_end, child_end - container_end)
    if child_type == FBSE_RECORD:
        width, height, png, _ = _parse_fbse(child, require, allow_delay=False, reserve=reserve)
    else:
        width, height, png, _ = _parse_png_blip(child, require, reserve)
    return _result(width, height, png, picture.location(size=len(picture.data)))


def read_inline_png(data, offset, require, *, reserve=None):
    """Read one PNG from a bounded MS-DOC PICFAndOfficeArtData range."""
    picture, shape = inline_picture_shape(data, offset, require)
    return read_inline_png_preview(picture, shape, require, reserve=reserve)
