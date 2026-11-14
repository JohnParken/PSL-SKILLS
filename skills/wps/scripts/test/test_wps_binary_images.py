"""Exercise public WPS image reading with synthetic MS-DOC streams."""

from base64 import b64decode
from hashlib import sha256
import json
from pathlib import Path
import struct
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]

from read_wps import ParseError, read_bytes
from test_wps import document
from test_wps_binary_officeart import fopt, sp
from test_wps_binary_png import fbse, inline_sp_container, picf, png_blip, png_bytes, record
from test_wps_msdoc_maximum import cfb_payload, set_pair, shape_plc


def install_chpx(streams, runs):
    """Append an independently built CHPX page and the associated BTE."""
    word = bytearray(streams[('WordDocument',)])
    page_offset = (len(word) + 511) // 512 * 512
    page = bytearray(512)
    boundaries = [4096, *(end for end, _ in runs)]
    struct.pack_into('<' + 'I' * len(boundaries), page, 0, *boundaries)
    array_end = len(boundaries) * 4
    pointer = 128
    for index, (_, prls) in enumerate(runs):
        if not prls:
            continue
        page[array_end + index] = pointer // 2
        page[pointer:pointer + len(prls) + 1] = bytes((len(prls),)) + prls
        pointer += (len(prls) + 2) // 2 * 2
    page[511] = len(runs)
    word.extend(bytes(page_offset - len(word)))
    struct.pack_into('<I', word, 64, page_offset + len(page))
    streams[('WordDocument',)] = bytes(word + page)
    set_pair(streams, 12, struct.pack('<3I', boundaries[0], boundaries[-1], page_offset // 512))
    return page_offset


def floating_fixture(blip):
    """Put two picture frames in one anchored group with a shared BLIP."""
    streams = document([(b'\x08\r', True)])
    set_pair(streams, 40, shape_plc((0,), (901,)))
    picture_option = fopt(((0x4104, 1),))
    group = record(0xF003, 15, 0, sp(901, 1)
                   + sp(902, 0x802, 75, options=(picture_option,))
                   + sp(903, 0x802, 75, options=(picture_option,)))
    bstore = record(0xF001, 15, 1, blip)
    dgg = record(0xF000, 15, 0, bstore)
    drawing = record(0xF002, 15, 0, record(0xF003, 15, 0, sp(900, 5) + group))
    set_pair(streams, 50, dgg + b'\0' + drawing)
    return streams


def inline_fixture(*, prls_extra=b'', field_instruction=False, trailing_span=False, mm=0x64):
    marker = b'\x13\x01\x14visible\x15\r' if field_instruction else b'\x01A\r'
    streams = document([(marker, True)])
    chpx = b'\x03\x6a\x00\x00\x00\x00\x55\x08\x01' + prls_extra
    if field_instruction:
        runs = [(4097, b''), (4098, chpx), (4096 + len(marker), b'')]
    else:
        runs = [(4098 if trailing_span else 4097, chpx), (4096 + len(marker), b'')]
    install_chpx(streams, runs)
    streams[('Data',)] = picf(inline_sp_container() + fbse(png_blip(png_bytes())), mm=mm)
    return cfb_payload(streams)


class BinaryImagesTests(unittest.TestCase):
    def test_floating_image_retains_each_group_child_reference_and_deduplicates_bytes(self):
        image = png_bytes()
        payload = cfb_payload(floating_fixture(png_blip(image)))
        standard = read_bytes(payload, reading_mode='standard')
        self.assertNotIn('binary_images', standard)
        maximum = read_bytes(payload, reading_mode='maximum')
        self.assertEqual(maximum['text'], standard['text'])
        report = maximum['binary_images']
        self.assertEqual(report['status'], 'extracted')
        self.assertEqual((report['image_count'], report['reference_count']), (1, 2))
        recovered = report['images'][0]
        self.assertEqual((recovered['width'], recovered['height']), (3, 2))
        self.assertEqual(recovered['sha256'], sha256(image).hexdigest())
        self.assertEqual([ref['shape_path'] for ref in recovered['references']], [[901, 902], [901, 903]])
        self.assertEqual([ref['anchor_cp'] for ref in recovered['references']], [0, 0])
        self.assertEqual(recovered['bytes_status'], 'not_requested')
        self.assertNotIn('data_base64', recovered)
        report = read_bytes(payload, reading_mode='maximum', include_image_bytes=True)['binary_images']
        self.assertEqual(report['bytes_emitted'], len(image))
        self.assertEqual(b64decode(report['images'][0]['data_base64'], validate=True), image)

    def test_inline_picture_requires_explicit_single_character_chpx_and_rejects_nonpictures(self):
        result = read_bytes(inline_fixture(), reading_mode='maximum')
        self.assertEqual(result['text'], '\ufffcA\n')
        self.assertEqual(result['binary_images']['image_count'], 1)
        reference = result['binary_images']['images'][0]['references'][0]
        self.assertEqual((reference['kind'], reference['anchor_cp']), ('inline', 0))
        self.assertEqual(reference['image_source']['stream'], 'Data')
        self.assertIn('inline_png_placeholder_not_rendered', [issue['code'] for issue in result['issues']])
        self.assertNotIn('picture_or_data_marker_not_decoded', [issue['code'] for issue in result['issues']])
        for options in ({'trailing_span': True}, {'prls_extra': b'\x06\x08\x01'},
                        {'prls_extra': b'\x03\x6a\x00\x00\x00\x00'}, {'mm': 0x66}):
            with self.subTest(options=options):
                result = read_bytes(inline_fixture(**options), reading_mode='maximum', include_image_bytes=True)
                self.assertEqual(result['text'], '\ufffcA\n')
                self.assertEqual(result['binary_images']['unresolved_reference_count'], 1)
                self.assertEqual(result['binary_images']['images'], [])

    def test_hidden_field_marker_is_never_followed(self):
        result = read_bytes(inline_fixture(field_instruction=True), reading_mode='maximum', include_image_bytes=True)
        self.assertEqual(result['text'], 'visible\n')
        self.assertNotIn('binary_images', result)

    def test_bad_image_keeps_text_and_explicit_payload_budget_keeps_metadata(self):
        invalid = cfb_payload(floating_fixture(png_blip(png_bytes(corrupt_idat_crc=True))))
        result = read_bytes(invalid, reading_mode='maximum')
        self.assertEqual(result['text'], '\ufffc\n')
        self.assertEqual(result['binary_images']['unresolved_reference_count'], 2)
        self.assertIn('binary_image_record_unread', [issue['code'] for issue in result['issues']])
        with patch('wps_binary_images.MAX_IMAGE_BYTES', 1):
            result = read_bytes(inline_fixture(), reading_mode='maximum', include_image_bytes=True)
        self.assertEqual(result['binary_images']['images_omitted_by_byte_budget'], 1)
        self.assertEqual(result['binary_images']['images'][0]['bytes_status'], 'omitted_budget')
        self.assertNotIn('data_base64', result['binary_images']['images'][0])
        with patch('wps_binary_images.MAX_IMAGE_SCAN_BYTES', 1), self.assertRaises(ParseError) as raised:
            read_bytes(inline_fixture(), reading_mode='maximum')
        self.assertEqual(raised.exception.status, 'resource_limit')

    def test_cli_image_bytes_require_maximum_binary_reading_and_preserve_input(self):
        with self.assertRaises(ParseError):
            read_bytes(b'{\\rtf1 control}', reading_mode='maximum', include_image_bytes=True)
        payload = inline_fixture()
        with TemporaryDirectory() as task:
            source = Path(task) / 'sample.wps'
            source.write_bytes(payload)
            cli = [sys.executable, '-I', '-S', '-B', str(Path(__file__).resolve().parents[1] / 'read_wps.py'),
                   source.name, '--include-image-bytes', '--reading-mode', 'standard']
            rejected = subprocess.run(cli, cwd=task, capture_output=True, check=False, timeout=10)
            self.assertEqual(rejected.returncode, 2)
            accepted = subprocess.run([*cli, '--reading-mode', 'maximum'], cwd=task, capture_output=True, check=False, timeout=10)
            self.assertEqual(accepted.returncode, 3, accepted.stderr)
            self.assertEqual(b64decode(json.loads(accepted.stdout)['binary_images']['images'][0]['data_base64']), png_bytes())
            self.assertEqual(source.read_bytes(), payload)


if __name__ == '__main__':
    unittest.main()
