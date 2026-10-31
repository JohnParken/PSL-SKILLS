"""Synthetic-only regression for optional MS-DOC stored paragraph source ranges.

Nothing here is a real WPS-produced sample or producer verification.
"""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from read_wps import ParseError, parse_streams, read_bytes
from test_wps import document, synthetic_cfb
from wps_paragraph_sources import paragraph_cp_ranges


class ParagraphSourceTests(unittest.TestCase):
    def test_half_open_utf16_cp_and_cr_terminators(self):
        self.assertEqual(paragraph_cp_ranges('甲😀\r乙\r', 5), [
            {'index': 0, 'cp_start': 5, 'cp_end': 9, 'text_cp_end': 8},
            {'index': 1, 'cp_start': 9, 'cp_end': 11, 'text_cp_end': 10},
        ])
        self.assertEqual(paragraph_cp_ranges('\r', 0), [
            {'index': 0, 'cp_start': 0, 'cp_end': 1, 'text_cp_end': 0}])
        self.assertEqual(paragraph_cp_ranges('a\rb', 10), [
            {'index': 0, 'cp_start': 10, 'cp_end': 12, 'text_cp_end': 11},
            {'index': 1, 'cp_start': 12, 'cp_end': 13, 'text_cp_end': 13},
        ])

    def test_budget_and_boolean_validation(self):
        with self.assertRaises(ValueError):
            paragraph_cp_ranges('a', True)
        with self.assertRaises(ValueError):
            paragraph_cp_ranges('a\rb\r', 0, max_paragraphs=1)
        with self.assertRaises(ParseError) as caught:
            parse_streams(document(), binary_paragraph_sources='yes')
        self.assertEqual(caught.exception.status, 'unsupported')

    def test_default_read_contract_unchanged(self):
        result = parse_streams(document())
        self.assertEqual(result['paragraphs'], ['Hello 中文', '第二段'])
        self.assertNotIn('paragraph_sources', result)
        self.assertNotIn('paragraph_source_status', result)

    def test_opt_in_fragmented_and_supplementary_text(self):
        raw = '甲😀\r乙\r'.encode('utf-16le')
        result = parse_streams(document([(raw, False)]),
                               binary_paragraph_sources=True)
        self.assertEqual(result['paragraphs'], ['甲😀', '乙'])
        self.assertEqual(result['paragraph_source_status'], 'exact_stored_cp')
        self.assertEqual(result['paragraph_sources'], [
            {'index': 0, 'cp_start': 0, 'cp_end': 4, 'text_cp_end': 3},
            {'index': 1, 'cp_start': 4, 'cp_end': 6, 'text_cp_end': 5},
        ])

    def test_requested_related_story_has_own_cp_start(self):
        main = '甲\r'.encode('utf-16le')
        note = '注\r'.encode('utf-16le')
        result = parse_streams(document([(main, False)], {80: note}),
                               include_related=True, binary_paragraph_sources=True)
        self.assertEqual(result['paragraph_sources'][0]['cp_start'], 0)
        story = result['related_stories'][0]
        self.assertEqual(story['type'], 'footnotes')
        self.assertEqual(story['paragraph_source_status'], 'exact_stored_cp')
        self.assertEqual(story['paragraph_sources'], [
            {'index': 0, 'cp_start': 2, 'cp_end': 4, 'text_cp_end': 3}])

    def test_noop_transform_policies_preserve_exact_cp_sources(self):
        original = document([('甲😀\r乙\r'.encode('utf-16le'), False)])
        for policy in ({'field_policy': 'cached_result'},
                       {'object_policy': 'placeholder'}):
            with self.subTest(policy=policy):
                result = parse_streams(
                    original, binary_paragraph_sources=True, **policy)
                self.assertEqual(result['paragraph_source_status'],
                                 'exact_stored_cp')
                self.assertEqual(result['paragraph_sources'], [
                    {'index': 0, 'cp_start': 0, 'cp_end': 4, 'text_cp_end': 3},
                    {'index': 1, 'cp_start': 4, 'cp_end': 6, 'text_cp_end': 5},
                ])

    def test_transformed_field_output_cannot_claim_original_alignment(self):
        data = document([(b'A\x13 FORMULA \x14B\x15\r', True)])
        result = parse_streams(data, field_policy='cached_result',
                               binary_paragraph_sources=True)
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['paragraph_sources'], [])
        self.assertEqual(result['paragraph_source_status'],
                         'unavailable_after_text_transform')
        self.assertTrue(any(issue['code'] == 'binary_paragraph_source_alignment_unavailable'
                            for issue in result['issues']))

    def test_ooxml_branch_rejects_binary_only_option(self):
        with self.assertRaises(ParseError) as caught:
            read_bytes(b'PK\x03\x04not-a-valid-package',
                       binary_paragraph_sources=True)
        self.assertEqual(caught.exception.status, 'unsupported')

    def test_source_metadata_requires_json_output(self):
        source = Path(__file__).resolve().with_name('read_wps.py')
        result = subprocess.run(
            [sys.executable, '-I', '-S', '-B', str(source),
             'synthetic.wps', '--format', 'text', '--binary-paragraph-sources'],
            capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn('requires --format json', result.stderr)
        self.assertEqual(result.stdout, '')

    def test_synthetic_file_cli_and_source_preservation(self):
        raw = '甲😀\r乙\r'.encode('utf-16le')
        data = synthetic_cfb({path[0]: value
                              for path, value in document([(raw, False)]).items()})
        with tempfile.TemporaryDirectory() as work:
            source = Path(work) / 'synthetic.wps'
            source.write_bytes(data)
            command = [sys.executable, '-I', '-S', '-B',
                       str(Path(__file__).resolve().with_name('read_wps.py')),
                       str(source), '--binary-paragraph-sources']
            completed = subprocess.run(command, capture_output=True,
                                       text=True, timeout=15)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            result = json.loads(completed.stdout)
            self.assertEqual(result['paragraph_source_status'], 'exact_stored_cp')
            self.assertEqual(len(result['paragraph_sources']), 2)
            self.assertEqual(source.read_bytes(), data)


if __name__ == '__main__':
    unittest.main()
