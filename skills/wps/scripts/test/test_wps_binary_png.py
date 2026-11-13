"""Synthetic stored and inline PNG BLIP records for MS-DOC."""

import sys
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parent.parent),
                str(Path(__file__).resolve().parents[2])]

import struct
import unittest
import zlib

from read_wps import ParseError, Region, require
from wps_binary_png import read_inline_png, read_stored_png


FBSE = 0xF007
PNG_BLIP = 0xF01E
SP_CONTAINER = 0xF004
PNG_SIGNATURE = b'\x89PNG\r\n\x1a\n'
PNG_IHDR = b'IHDR'
PNG_IDAT = b'IDAT'
PNG_IEND = b'IEND'


def record(record_type, version, instance, payload=b''):
    """Encode one complete OfficeArt record."""
    ver_instance = (instance << 4) | version
    return struct.pack('<HHI', ver_instance, record_type,
                       len(payload)) + payload


def png_chunk(chunk_type, data, *, corrupt_crc=False):
    """Encode one PNG chunk with a literal CRC."""
    crc = zlib.crc32(chunk_type)
    crc = zlib.crc32(data, crc) & 0xFFFFFFFF
    if corrupt_crc:
        crc ^= 1
    return (struct.pack('>I', len(data)) + chunk_type + data +
            struct.pack('>I', crc))


def png_bytes(width=3, height=2, *, corrupt_idat_crc=False):
    """Build a small framed PNG without relying on an image library."""
    ihdr = struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0)
    return (PNG_SIGNATURE + png_chunk(PNG_IHDR, ihdr) +
            png_chunk(PNG_IDAT, b'opaque-deflate-data',
                      corrupt_crc=corrupt_idat_crc) +
            png_chunk(PNG_IEND, b''))


def png_blip(image, instance=0x6E0):
    """Encode a PNG BLIP with the specified one- or two-UID variant."""
    prefix_size = 17 if instance == 0x6E0 else 33
    return record(PNG_BLIP, 0, instance, b'\x00' * prefix_size + image)


def fbse(blip, *, embedded=True, fo_delay=0xFFFFFFFF, name=b'',
         win32_type=6, mac_type=6, declared_size=None):
    """Encode an FBSE with either an embedded BLIP or a delayed reference."""
    size = len(blip) if declared_size is None else declared_size
    fixed = struct.pack('<BB16sHIII4B', win32_type, mac_type, b'\x00' * 16,
                        0, size, 1, fo_delay, 0, len(name), 0, 0)
    payload = fixed + name
    if embedded:
        payload += blip
    return record(FBSE, 2, 6, payload)


def picf(officeart, *, mm=0x64):
    """Wrap inline OfficeArt bytes in a synthetic 68-byte PICF header."""
    header = struct.pack('<IHH', 68 + len(officeart), 68, mm)
    return header + b'\x00' * 60 + officeart


def inline_sp_container(*, version=15, instance=0, payload=b'opaque shape bytes'):
    """Encode the inline SpContainer whose extent precedes the BLIP array."""
    return record(SP_CONTAINER, version, instance, payload)


class BinaryPNGTests(unittest.TestCase):
    def parse_stored(self, data, *, stream='1Table', base=0, **kwargs):
        return read_stored_png(Region(data, stream, base=base), require,
                               **kwargs)

    def assert_parse_status(self, callback, status):
        with self.assertRaises(ParseError) as caught:
            callback()
        self.assertEqual(caught.exception.status, status)

    def assert_result_shape(self, result, image, width, height):
        self.assertEqual(set(result), {
            'width', 'height', 'source', 'record_source', 'payload'})
        self.assertEqual((result['width'], result['height']), (width, height))
        self.assertEqual(result['source']['size'], len(image))
        self.assertEqual(bytes(result['payload']), image)

    def test_stored_direct_png_and_two_uid_variant(self):
        for instance in (0x6E0, 0x6E1):
            with self.subTest(instance=instance):
                image = png_bytes()
                data = png_blip(image, instance)
                result = self.parse_stored(data, base=900)
                self.assert_result_shape(result, image, 3, 2)
                prefix = 17 if instance == 0x6E0 else 33
                self.assertEqual(result['source'], {
                    'stream': '1Table', 'offset': 900 + 8 + prefix,
                    'size': len(image)})
                self.assertEqual(result['record_source'], {
                    'stream': '1Table', 'offset': 900, 'size': len(data)})

    def test_stored_embedded_fbse_uses_record_extent_not_delay_offset(self):
        image = png_bytes(7, 5)
        blip = png_blip(image)
        name = '圖.png'.encode('utf-16le') + b'\x00\x00'
        block = fbse(blip, name=name, fo_delay=1)

        result = self.parse_stored(block, base=1200)

        self.assert_result_shape(result, image, 7, 5)
        fbse_blip_offset = 8 + 36 + len(name)
        self.assertEqual(result['record_source'], {
            'stream': '1Table', 'offset': 1200 + fbse_blip_offset,
            'size': len(blip)})
        self.assertEqual(result['source']['offset'],
                         1200 + fbse_blip_offset + 8 + 17)

    def test_stored_delayed_fbse_uses_exact_worddocument_extent(self):
        image = png_bytes(9, 4)
        blip = png_blip(image)
        fo_delay = 24
        word_data = b'W' * fo_delay + blip + b'T' * 10
        block = fbse(blip, embedded=False, fo_delay=fo_delay)
        delay = Region(word_data, 'WordDocument')

        result = self.parse_stored(
            block, delay=delay,
            delay_exclusions=((0, fo_delay), (fo_delay + len(blip),
                                               len(word_data))))

        self.assert_result_shape(result, image, 9, 4)
        self.assertEqual(result['record_source'], {
            'stream': 'WordDocument', 'offset': fo_delay, 'size': len(blip)})
        self.assertEqual(result['source']['offset'], fo_delay + 8 + 17)

    def test_stored_delayed_fbse_rejects_missing_stream_and_protected_range(self):
        image = png_bytes()
        blip = png_blip(image)
        fo_delay = 10
        word_data = b'W' * fo_delay + blip + b'T'
        block = fbse(blip, embedded=False, fo_delay=fo_delay)

        self.assert_parse_status(lambda: self.parse_stored(block), 'unsupported')
        self.assert_parse_status(
            lambda: self.parse_stored(
                block, delay=Region(word_data, 'WordDocument'),
                delay_exclusions=((fo_delay + 2, fo_delay + 3),)),
            'corrupt')
        self.assert_parse_status(
            lambda: self.parse_stored(
                block, delay=Region(word_data, 'WordDocument', base=1)),
            'unsupported')

    def test_inline_picf_accepts_embedded_fbse_and_reports_whole_source(self):
        image = png_bytes(11, 8)
        blip = png_blip(image)
        stored = fbse(blip)
        container = inline_sp_container(payload=b'opaque non-record shape data')
        source_bytes = b'pre' + picf(container + stored) + b'post'
        data = Region(source_bytes, 'Data', base=2000)

        result = read_inline_png(data, 3, require)

        self.assert_result_shape(result, image, 11, 8)
        self.assertEqual(result['record_source'], {
            'stream': 'Data', 'offset': 2003,
            'size': len(source_bytes) - 7})

    def test_inline_picf_accepts_direct_png_blip(self):
        image = png_bytes(4, 6)
        officeart = inline_sp_container() + png_blip(image, 0x6E1)
        data = Region(picf(officeart), 'Data', base=700)

        result = read_inline_png(data, 0, require)

        self.assert_result_shape(result, image, 4, 6)
        self.assertEqual(result['record_source'], {
            'stream': 'Data', 'offset': 700, 'size': len(data.data)})
        self.assertEqual(result['source']['offset'],
                         700 + 68 + len(inline_sp_container()) + 8 + 33)

    def test_inline_picf_rejects_linked_picture_without_reading_path(self):
        linked = picf(inline_sp_container() + b'C:\\private\\image.png', mm=0x66)
        data = Region(linked, 'Data')

        self.assert_parse_status(
            lambda: read_inline_png(data, 0, require), 'unsupported')

    def test_inline_picf_requires_one_image_and_supported_spcontainer(self):
        image = png_bytes()
        direct = png_blip(image)
        multiple = picf(inline_sp_container() + direct + direct)
        wrong_container = picf(inline_sp_container(version=14) + direct)
        absent = picf(inline_sp_container())

        self.assert_parse_status(
            lambda: read_inline_png(Region(multiple, 'Data'), 0, require),
            'unsupported')
        self.assert_parse_status(
            lambda: read_inline_png(Region(wrong_container, 'Data'), 0, require),
            'unsupported')
        self.assert_parse_status(
            lambda: read_inline_png(Region(absent, 'Data'), 0, require),
            'unsupported')

    def test_png_checksum_and_chunk_bounds_are_checked(self):
        image = png_bytes(corrupt_idat_crc=True)
        bad_crc = png_blip(image)
        bad_length = (PNG_SIGNATURE + struct.pack('>I', 100) + PNG_IHDR +
                      b'\x00\x00\x00\x00')

        self.assert_parse_status(
            lambda: self.parse_stored(bad_crc), 'corrupt')
        self.assert_parse_status(
            lambda: self.parse_stored(png_blip(bad_length)), 'corrupt')

    def test_png_rejects_missing_idat_trailing_bytes_and_bad_dimensions(self):
        ihdr = struct.pack('>IIBBBBB', 0, 2, 8, 6, 0, 0, 0)
        missing_idat = (PNG_SIGNATURE + png_chunk(PNG_IHDR, ihdr) +
                        png_chunk(PNG_IEND, b''))
        trailing = png_bytes() + b'extra'

        self.assert_parse_status(
            lambda: self.parse_stored(png_blip(missing_idat)), 'corrupt')
        self.assert_parse_status(
            lambda: self.parse_stored(png_blip(trailing)), 'corrupt')

    def test_unsupported_and_malformed_officeart_records_are_distinguished(self):
        image = png_bytes()
        other_type = record(0xF01D, 0, 0x46A, b'opaque')
        truncated = struct.pack('<HHI', (0x6E0 << 4), PNG_BLIP, 100) + b'\x00'
        wrong_fbse_types = fbse(png_blip(image), win32_type=5, mac_type=5)
        delayed_bad_size = fbse(png_blip(image), embedded=False,
                                fo_delay=0, declared_size=999)

        self.assert_parse_status(lambda: self.parse_stored(other_type),
                                 'unsupported')
        self.assert_parse_status(lambda: self.parse_stored(truncated), 'corrupt')
        self.assert_parse_status(lambda: self.parse_stored(wrong_fbse_types),
                                 'unsupported')
        self.assert_parse_status(
            lambda: self.parse_stored(
                delayed_bad_size,
                delay=Region(png_blip(image), 'WordDocument')),
            'corrupt')


if __name__ == '__main__':
    unittest.main()
