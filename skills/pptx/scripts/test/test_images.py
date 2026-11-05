"""Standard-library regressions for image inspection and native picture geometry."""

from __future__ import annotations
import sys
from pathlib import Path

# Test-only bootstrap: support discovery and direct execution with -I.
sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]


import base64
import binascii
import struct
import unittest
import zlib
from xml.etree import ElementTree as ET


SCRIPT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_ROOT))
sys.path.insert(0, str(SCRIPT_ROOT / '_engine'))

from pptx_core.images import ImageValidationError, inspect_image


# Original 4x2 RGB fixtures, encoded once during development. Tests need no
# encoder or decoder. The independently known dimensions are identical for
# the sequential and progressive forms of these eight colored pixels.
JPEG_BASELINE = base64.b64decode(
    '/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAUDBAQEAwUEBAQFBQUGBwwIBwcHBw8LCwkMEQ8SEhEPERETFhwXExQaFRERGCEYGh0d'
    'Hx8fExciJCIeJBweHx7/2wBDAQUFBQcGBw4ICA4eFBEUHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4e'
    'Hh4eHh4eHh7/wAARCAACAAQDASIAAhEBAxEB/8QAHwAAAQUBAQEBAQEAAAAAAAAAAAECAwQFBgcICQoL/8QAtRAAAgEDAwIEAwUF'
    'BAQAAAF9AQIDAAQRBRIhMUEGE1FhByJxFDKBkaEII0KxwRVS0fAkM2JyggkKFhcYGRolJicoKSo0NTY3ODk6Q0RFRkdISUpTVFVW'
    'V1hZWmNkZWZnaGlqc3R1dnd4eXqDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uHi'
    '4+Tl5ufo6erx8vP09fb3+Pn6/8QAHwEAAwEBAQEBAQEBAQAAAAAAAAECAwQFBgcICQoL/8QAtREAAgECBAQDBAcFBAQAAQJ3AAEC'
    'AxEEBSExBhJBUQdhcRMiMoEIFEKRobHBCSMzUvAVYnLRChYkNOEl8RcYGRomJygpKjU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVm'
    'Z2hpanN0dXZ3eHl6goOEhYaHiImKkpOUlZaXmJmaoqOkpaanqKmqsrO0tba3uLm6wsPExcbHyMnK0tPU1dbX2Nna4uPk5ebn6Onq'
    '8vP09fb3+Pn6/9oADAMBAAIRAxEAPwCODWtZ0Xwv4VXRtWv9NW78OaddXItLh4hNM1sgaR9pG5yFUFjyQo9KKKK+yyf/AJFuG/69'
    '0/8A0iJ+gZJ/uUfWX/pTP//Z'
)
JPEG_PROGRESSIVE = base64.b64decode(
    '/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAUDBAQEAwUEBAQFBQUGBwwIBwcHBw8LCwkMEQ8SEhEPERETFhwXExQaFRERGCEYGh0d'
    'Hx8fExciJCIeJBweHx7/2wBDAQUFBQcGBw4ICA4eFBEUHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4e'
    'Hh4eHh4eHh7/wgARCAACAAQDASIAAhEBAxEB/8QAFQABAQAAAAAAAAAAAAAAAAAAAAL/xAAVAQEBAAAAAAAAAAAAAAAAAAAFBv/a'
    'AAwDAQACEAMQAAABkM0H/8QAFhABAQEAAAAAAAAAAAAAAAAAAwEE/9oACAEBAAEFAozDl//EABkRAAEFAAAAAAAAAAAAAAAAAAAB'
    'BDOBsf/aAAgBAwEBPwFlCl6f/8QAGxEAAAcBAAAAAAAAAAAAAAAAAAECAwQ0crH/2gAIAQIBAT8Bh1m8p4Q//8QAHBAAAQQDAQAA'
    'AAAAAAAAAAAAAgMEERIAARMx/9oACAEBAAY/AmvFU07N0yKpRO6+5//EABkQAQEBAAMAAAAAAAAAAAAAAAERIQAxYf/aAAgBAQAB'
    'PyE1HuqsVTtw3zn/2gAMAwEAAgADAAAAEAP/xAAWEQADAAAAAAAAAAAAAAAAAAAAobH/2gAIAQMBAT8Qeof/xAAUEQEAAAAAAAAA'
    'AAAAAAAAAAAA/9oACAECAQE/EBL/xAAYEAEBAAMAAAAAAAAAAAAAAAABEQAhUf/aAAgBAQABPxAoU6WDpFQFbQcz/9k='
)
PNG_SIGNATURE = b'\x89PNG\r\n\x1a\n'
DRAWING_NS = 'http://schemas.openxmlformats.org/drawingml/2006/main'
PRESENTATION_NS = 'http://schemas.openxmlformats.org/presentationml/2006/main'
RELATIONSHIP_NS = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
SVG_NS = 'http://www.w3.org/2000/svg'


def png_chunk(kind: bytes, payload: bytes) -> bytes:
    return struct.pack('>I', len(payload)) + kind + payload + struct.pack('>I', binascii.crc32(kind + payload) & 0xffffffff)


def png_rgba(width: int = 4, height: int = 2, extra: bytes = b'', interlace: int = 0) -> bytes:
    header = struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, interlace)
    scanlines = (b'\x00' + b'\xff\x00\x00\x80' * width) * height
    return PNG_SIGNATURE + png_chunk(b'IHDR', header) + extra + png_chunk(b'IDAT', zlib.compress(scanlines)) + png_chunk(b'IEND', b'')


def exif_orientation(value: int, order: str = '<') -> bytes:
    signature = b'II' if order == '<' else b'MM'
    header = signature + struct.pack(order + 'HIH', 42, 8, 1)
    entry = struct.pack(order + 'HHIH', 274, 3, 1, value) + b'\x00\x00'
    return header + entry + struct.pack(order + 'I', 0)


def jpeg_segment(marker: int, payload: bytes) -> bytes:
    return bytes((0xff, marker)) + struct.pack('>H', len(payload) + 2) + payload


class ImageInspectionTests(unittest.TestCase):
    def test_png_dimensions_and_transparency(self) -> None:
        info = inspect_image(png_rgba())
        self.assertEqual((info.format, info.width, info.height, info.has_alpha), ('png', 4, 2, True))

    def test_png_palette_transparency(self) -> None:
        data = PNG_SIGNATURE + png_chunk(b'IHDR', struct.pack('>IIBBBBB', 2, 1, 1, 3, 0, 0, 0))
        data += png_chunk(b'PLTE', b'\xff\x00\x00\x00\x00\xff') + png_chunk(b'tRNS', b'\x00\xff')
        data += png_chunk(b'IDAT', zlib.compress(b'\x00\x40')) + png_chunk(b'IEND', b'')
        self.assertTrue(inspect_image(data).has_alpha)

    def test_small_interlaced_png(self) -> None:
        self.assertEqual(inspect_image(png_rgba(1, 1, interlace=1)).width, 1)

    def test_png_crc_corruption_is_rejected(self) -> None:
        data = bytearray(png_rgba())
        data[-1] ^= 1
        with self.assertRaisesRegex(ImageValidationError, 'CRC'):
            inspect_image(bytes(data))

    def test_png_truncated_and_trailing_data_are_rejected(self) -> None:
        for data in (png_rgba()[:-4], png_rgba() + b'trailing'):
            with self.subTest(length=len(data)), self.assertRaises(ImageValidationError):
                inspect_image(data)

    def test_png_decompression_is_bounded_by_dimensions(self) -> None:
        header = struct.pack('>IIBBBBB', 1, 1, 8, 6, 0, 0, 0)
        data = PNG_SIGNATURE + png_chunk(b'IHDR', header)
        data += png_chunk(b'IDAT', zlib.compress(b'\x00' * 70000)) + png_chunk(b'IEND', b'')
        with self.assertRaisesRegex(ImageValidationError, 'declared dimensions'):
            inspect_image(data)

    def test_png_corrupt_zlib_rejected_even_with_valid_chunk_crc(self) -> None:
        header = struct.pack('>IIBBBBB', 1, 1, 8, 6, 0, 0, 0)
        data = PNG_SIGNATURE + png_chunk(b'IHDR', header) + png_chunk(b'IDAT', b'not-zlib') + png_chunk(b'IEND', b'')
        with self.assertRaisesRegex(ImageValidationError, 'compressed stream'):
            inspect_image(data)

    def test_png_animation_and_unknown_critical_chunk_are_rejected(self) -> None:
        for kind, payload in ((b'acTL', struct.pack('>II', 2, 0)), (b'ABCD', b'')):
            with self.subTest(kind=kind), self.assertRaises(ImageValidationError):
                inspect_image(png_rgba(extra=png_chunk(kind, payload)))

    def test_png_pixel_limit_is_enforced_before_decompression(self) -> None:
        header = struct.pack('>IIBBBBB', 20000, 20000, 8, 6, 0, 0, 0)
        with self.assertRaisesRegex(ImageValidationError, 'pixels'):
            inspect_image(PNG_SIGNATURE + png_chunk(b'IHDR', header))

    def test_jpeg_baseline_and_progressive_have_known_dimensions(self) -> None:
        for data in (JPEG_BASELINE, JPEG_PROGRESSIVE):
            with self.subTest(size=len(data)):
                info = inspect_image(data, 'jpeg')
                self.assertEqual((info.format, info.width, info.height, info.has_alpha), ('jpg', 4, 2, False))

    def test_jpeg_truncation_and_trailing_data_are_rejected(self) -> None:
        for data in (JPEG_BASELINE[:-2], JPEG_BASELINE + b'trailing'):
            with self.subTest(length=len(data)), self.assertRaises(ImageValidationError):
                inspect_image(data)

    def test_jpeg_missing_quantization_table_is_rejected(self) -> None:
        start = JPEG_BASELINE.index(b'\xff\xdb')
        end = start + 2 + int.from_bytes(JPEG_BASELINE[start + 2:start + 4], 'big')
        with self.assertRaisesRegex(ImageValidationError, 'quantization table'):
            inspect_image(JPEG_BASELINE[:start] + JPEG_BASELINE[end:])

    def test_jpeg_unsupported_coding_is_rejected(self) -> None:
        with self.assertRaisesRegex(ImageValidationError, 'coding marker'):
            inspect_image(JPEG_BASELINE.replace(b'\xff\xc0', b'\xff\xc3', 1))

    def test_declared_format_must_match_and_other_formats_are_rejected(self) -> None:
        for data, declared in ((JPEG_BASELINE, 'png'), (png_rgba(), 'gif'), (b'GIF89a', None)):
            with self.subTest(declared=declared), self.assertRaises(ImageValidationError):
                inspect_image(data, declared)

    def test_exif_orientation_one_works_in_both_formats_and_endiannesses(self) -> None:
        for order in ('<', '>'):
            exif = exif_orientation(1, order)
            jpeg = JPEG_BASELINE[:2] + jpeg_segment(0xe1, b'Exif\x00\x00' + exif) + JPEG_BASELINE[2:]
            self.assertEqual(inspect_image(jpeg).width, 4)
            self.assertEqual(inspect_image(png_rgba(extra=png_chunk(b'eXIf', exif))).width, 4)

    def test_exif_rotated_or_malformed_images_fail_explicitly(self) -> None:
        for exif in (exif_orientation(6), b'II' + struct.pack('<HI', 42, 10000)):
            jpeg = JPEG_BASELINE[:2] + jpeg_segment(0xe1, b'Exif\x00\x00' + exif) + JPEG_BASELINE[2:]
            with self.assertRaisesRegex(ImageValidationError, 'EXIF'):
                inspect_image(jpeg)
            with self.assertRaisesRegex(ImageValidationError, 'EXIF'):
                inspect_image(png_rgba(extra=png_chunk(b'eXIf', exif)))


class PictureGeometryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from svg_to_pptx.drawingml.context import ConvertContext
        from svg_to_pptx.drawingml.elements import convert_image

        cls.context_class = ConvertContext
        cls.convert_image = staticmethod(convert_image)

    def convert(self, data: bytes, aspect: str, **attributes: str):
        element = ET.Element(f'{{{SVG_NS}}}image', {
            'x': '10', 'y': '20', 'width': '100', 'height': '100',
            'href': 'data:image/png;base64,' + base64.b64encode(data).decode('ascii'),
            'preserveAspectRatio': aspect,
            **attributes,
        })
        context = self.context_class(image_optimize=False)
        result = self.convert_image(element, context)
        xml = ET.fromstring(
            f'<root xmlns:a="{DRAWING_NS}" xmlns:p="{PRESENTATION_NS}" xmlns:r="{RELATIONSHIP_NS}">{result.xml}</root>'
        )
        return context, xml

    def test_slice_retains_source_bytes_and_native_crop(self) -> None:
        data = png_rgba()
        context, xml = self.convert(data, 'xMidYMid slice')
        self.assertEqual(list(context.media_files.values()), [data])
        crop = xml.find(f'.//{{{DRAWING_NS}}}srcRect')
        self.assertEqual(crop.attrib, {'l': '25000', 't': '0', 'r': '25000', 'b': '0'})
        self.assertEqual(len(xml.findall(f'.//{{{PRESENTATION_NS}}}pic')), 1)

    def test_meet_uses_intrinsic_aspect_ratio(self) -> None:
        _, xml = self.convert(png_rgba(), 'xMidYMid meet')
        transform = xml.find(f'.//{{{DRAWING_NS}}}xfrm')
        self.assertEqual(transform.find(f'{{{DRAWING_NS}}}off').attrib, {'x': str(10 * 9525), 'y': str(45 * 9525)})
        self.assertEqual(transform.find(f'{{{DRAWING_NS}}}ext').attrib, {'cx': str(100 * 9525), 'cy': str(50 * 9525)})

    def test_rotation_remains_native(self) -> None:
        _, xml = self.convert(png_rgba(), 'none', transform='rotate(30 60 70)')
        self.assertEqual(xml.find(f'.//{{{DRAWING_NS}}}xfrm').get('rot'), '1800000')

    def test_pixel_optimization_request_is_not_silently_ignored(self) -> None:
        element = ET.Element(f'{{{SVG_NS}}}image', {
            'width': '100', 'height': '100',
            'href': 'data:image/png;base64,' + base64.b64encode(png_rgba()).decode('ascii'),
        })
        context = self.context_class(image_optimize=True)
        with self.assertRaisesRegex(ValueError, 'image optimization is unavailable'):
            self.convert_image(element, context)
        self.assertFalse(context.media_files)


if __name__ == '__main__':
    unittest.main()
