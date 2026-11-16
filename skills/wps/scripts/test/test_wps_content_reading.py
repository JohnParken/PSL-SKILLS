"""Content-first whole-document extraction, using only synthetic fixtures."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]

from read_wps import ParseError, read_bytes
from test_wps import document
from test_wps_binary_images import inline_fixture, floating_fixture
from test_wps_binary_png import png_blip, png_bytes
from test_wps_binary_officeart import complex_property, drawing, fopt, officeart, sp
from test_wps_maximum_reading import ooxml_with_textbox
from test_wps_msdoc_maximum import IN_TABLE, ROW_END, cfb_payload, papx_document, set_pair, shape_plc
from wps_maximum_pages import assemble_pages, read_page


class ContentReadingTests(unittest.TestCase):
    """Verify removed work as well as retained text, diagnostics and upgrades."""

    def test_default_preserves_binary_body_related_and_cached_fields(self):
        data = cfb_payload(document([(b'Body\x13CODE\x14Value\x15\r', True)], {80: 'Note\r'.encode('utf-16le')}))
        result = read_bytes(data)
        self.assertEqual(result['text'], 'BodyValue\n')
        self.assertEqual(result['related_stories'][0]['text'], 'Note\n')
        self.assertNotIn('CODE', json.dumps(result))
        self.assertEqual(result['reading_mode'], 'content')
        self.assertFalse(result['current_view_available'])
        self.assertEqual(result['status'], 'partial')
        self.assertIn('binary_optional_content_not_requested', [issue['code'] for issue in result['issues']])

    def test_binary_png_decoders_are_not_called_but_chart_scan_is_available(self):
        data = cfb_payload(floating_fixture(png_blip(png_bytes())))
        with (patch('wps_binary_images._floating_images', side_effect=AssertionError('image decode')) as images,
              patch('wps_binary_images.read_inline_png_preview', side_effect=AssertionError('image decode')) as preview):
            result = read_bytes(data)
        images.assert_not_called()
        preview.assert_not_called()
        self.assertEqual(result['text'], '\ufffc\n')
        self.assertNotIn('binary_images', result)
        self.assertNotIn('binary_charts', result)
        self.assertTrue(result['drawing_objects']['objects'])

    def test_maximum_keeps_opt_in_images_and_bytes(self):
        data = inline_fixture()
        content = read_bytes(data)
        maximum = read_bytes(data, reading_mode='maximum', include_image_bytes=True)
        self.assertEqual(content['text'], maximum['text'])
        self.assertEqual(maximum['binary_images']['image_count'], 1)
        self.assertIn('data_base64', maximum['binary_images']['images'][0])
        with self.assertRaises(ParseError):
            read_bytes(data, include_image_bytes=True)

    def test_object_wordart_text_is_retained_without_media_work(self):
        streams = document([(b'Before\x08After\r', True)])
        set_pair(streams, 40, shape_plc((6,), (71,)))
        options = fopt((complex_property(0x00C0, 'Object wording'),))
        region = officeart(((0, drawing(sp(71, 1 << 11, 136, options=(options,)))),))
        set_pair(streams, 50, bytes(region.data))
        result = read_bytes(cfb_payload(streams))
        self.assertEqual(result['text'], 'Before\ufffcAfter\n')
        self.assertEqual(result['drawing_objects']['objects'][0]['officeart']['wordart_text'], 'Object wording')

    def test_pure_paragraph_layout_is_not_inspected(self):
        data = cfb_payload(papx_document(('Body\r', b'\x61\x24\x01')))
        with patch('wps_binary_tables.inspect_tables', side_effect=AssertionError('layout-only parse')):
            result = read_bytes(data)
        self.assertEqual(result['text'], 'Body\n')
        self.assertNotIn('binary_paragraph_layout', result)
        maximum = read_bytes(data, reading_mode='maximum')
        self.assertTrue(maximum['binary_paragraph_layout']['paragraphs'])

    def test_table_text_and_grouping_retained_without_layout_payloads(self):
        layout = b'\x61\x24\x01'
        data = cfb_payload(papx_document(('A\x07', layout + IN_TABLE), ('B\x07', IN_TABLE), ('\x07', ROW_END), ('\r', b'')))
        with patch('wps_binary_tables.layout_operand', side_effect=AssertionError('layout operands')):
            result = read_bytes(data)
        maximum = read_bytes(data, reading_mode='maximum')
        self.assertEqual(result['text'], maximum['text'])
        structure = result['binary_table_structure']
        self.assertEqual([[cell['text'] for cell in row['cells']] for row in structure['rows']], [['A', 'B']])
        self.assertEqual(structure['tables'], maximum['binary_table_structure']['tables'])
        self.assertNotIn('paragraph_layout', structure)
        self.assertNotIn('binary_paragraph_layout', result)
        self.assertEqual(structure['layout_properties_not_rendered'], [])

    def test_ooxml_objects_and_revisions_remain_separate(self):
        result = read_bytes(ooxml_with_textbox())
        self.assertEqual(result['text'], 'MainNew\n')
        self.assertEqual(result['revision_content']['content']['text'], 'MainNewOld\n')
        self.assertEqual([s['text'] for s in result['related_stories'] if s['type'] == 'textboxes'], ['Box text'])

    def test_content_pages_are_lossless_and_cannot_switch_profile(self):
        data = b'{\\rtf1 Main\\par{\\header Header}}'
        pages = []
        cursor = None
        while True:
            page = read_page(data, reading_mode='content', page_size=800, cursor=cursor)
            pages.append(page)
            if not page['has_more']:
                break
            cursor = page['next_cursor']
        self.assertEqual(assemble_pages(pages), read_bytes(data))
        self.assertEqual(pages[0]['coverage'], 'complete_content_result_json_transport')
        with self.assertRaises(ParseError):
            read_page(data, reading_mode='maximum', page_size=800, cursor=pages[0]['next_cursor'])

    def test_content_keeps_input_and_structure_guards(self):
        data = cfb_payload(document())
        with self.assertRaises(ParseError):
            read_bytes(data[:28] + b'\x00\x00' + data[30:])
        with self.assertRaises(ParseError):
            read_bytes(data, reading_mode='content', object_policy='reject')

    def test_cli_default_content_full_json_and_explicit_image_guard(self):
        script = str(Path(__file__).resolve().parents[1] / 'read_wps.py')
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory, 'input.doc')
            data = inline_fixture()
            source.write_bytes(data)
            command = [sys.executable, '-I', '-S', '-B', script, source.name]
            process = subprocess.run(command, cwd=directory, capture_output=True, text=True, check=False)
            self.assertEqual(process.returncode, 3, process.stderr)
            result = json.loads(process.stdout)
            self.assertEqual(result['reading_mode'], 'content')
            self.assertEqual(result['text'], '\ufffcA\n')
            rejected = subprocess.run([*command, '--include-image-bytes'], cwd=directory, capture_output=True, text=True, check=False)
            self.assertEqual(rejected.returncode, 2)
            self.assertEqual(source.read_bytes(), data)


if __name__ == '__main__':
    unittest.main()
