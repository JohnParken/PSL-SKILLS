"""Synthetic CP-source tests; NOT native WPS producer acceptance."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from read_wps import ParseError, parse_streams, read_bytes
from test_wps import document, synthetic_cfb
from wps_paragraph_sources import paragraph_cp_ranges


class ParagraphSourcesTests(unittest.TestCase):
    def test_utf16_units_and_cr_boundaries(self):
        self.assertEqual(paragraph_cp_ranges('甲😀\r乙\r', 5), [
            {'index': 0, 'cp_start': 5, 'cp_end': 9, 'text_cp_end': 8},
            {'index': 1, 'cp_start': 9, 'cp_end': 11, 'text_cp_end': 10},
        ])
        self.assertEqual(paragraph_cp_ranges('', 0), [])
        self.assertEqual(paragraph_cp_ranges('\r', 0), [
            {'index': 0, 'cp_start': 0, 'cp_end': 1, 'text_cp_end': 0},
        ])
        self.assertEqual(paragraph_cp_ranges('a\rb', 10), [
            {'index': 0, 'cp_start': 10, 'cp_end': 12, 'text_cp_end': 11},
            {'index': 1, 'cp_start': 12, 'cp_end': 13, 'text_cp_end': 13},
        ])

    def test_stored_main_body_is_opt_in(self):
        streams = document()
        self.assertNotIn('paragraph_sources', parse_streams(streams))
        result = parse_streams(streams, include_paragraph_sources=True)
        self.assertEqual(result['text'], 'Hello 中文\n第二段\n')
        self.assertEqual(result['paragraph_sources'], {
            'schema_version': 'ms_doc_paragraph_cp_v1',
            'status': 'exact_stored_cp',
            'ranges': [
                {'index': 0, 'cp_start': 0, 'cp_end': 9, 'text_cp_end': 8},
                {'index': 1, 'cp_start': 9, 'cp_end': 13, 'text_cp_end': 12},
            ],
        })

    def test_non_bmp_piece_boundary(self):
        streams = document([(b'\x3d\xd8', False), (b'\x00\xde', False),
                            (b'\r', True)])
        result = parse_streams(streams, include_paragraph_sources=True)
        self.assertEqual(result['paragraph_sources']['ranges'], [
            {'index': 0, 'cp_start': 0, 'cp_end': 3, 'text_cp_end': 2},
        ])

    def test_related_region_uses_absolute_story_cp(self):
        streams = document(parts=[('主\r'.encode('utf-16le'), False)],
                           other_stories={80: '注释\r'.encode('utf-16le')})
        result = parse_streams(streams, include_related=True,
                               include_paragraph_sources=True)
        related = result['related_stories'][0]
        self.assertEqual(related['type'], 'footnotes')
        self.assertEqual(related['paragraph_sources']['ranges'], [
            {'index': 0, 'cp_start': 2, 'cp_end': 5, 'text_cp_end': 4},
        ])

    def test_filtered_output_never_claims_exact_original_locations(self):
        result = parse_streams(document(), field_policy='cached_result',
                               include_paragraph_sources=True)
        self.assertEqual(result['paragraph_sources']['status'],
                         'unavailable_transformed')
        self.assertEqual(result['paragraph_sources']['ranges'], [])

    def test_invalid_option_and_nonbinary_formats_fail_closed(self):
        with self.assertRaises(ParseError):
            parse_streams(document(), include_paragraph_sources='yes')
        with self.assertRaises(ParseError) as caught:
            read_bytes(b'PK\x03\x04bad package',
                       include_paragraph_sources=True)
        self.assertEqual(caught.exception.status, 'unsupported')
        with self.assertRaises(ValueError):
            paragraph_cp_ranges('x', True)

    def test_cli_rejects_text_output_with_sources(self):
        script = Path(__file__).with_name('read_wps.py')
        process = subprocess.run(
            [sys.executable, '-I', '-S', '-B', str(script),
             '--include-paragraph-sources', '--format', 'text'],
            capture_output=True, text=True, timeout=15)
        self.assertEqual(process.returncode, 2)
        self.assertEqual(process.stdout, '')
        self.assertIn('--include-paragraph-sources requires --format json',
                      process.stderr)


if __name__ == '__main__':
    unittest.main()
