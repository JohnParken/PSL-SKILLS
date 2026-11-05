"""Bounded MS-DOC recovery: synthetic inputs, never producer/display evidence."""
import sys
from pathlib import Path

# Test-only bootstrap: support discovery and direct execution with -I.
sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]

import json
import subprocess
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from read_wps import ParseError, parse_streams, read_bytes
from test_wps import document, replace_word
from test_wps_cfb import synthetic_cfb
from test_wps_binary_shapes import shaped_document


def streams(text):
    return document([(text.encode('utf-16le'), False)])


def package(text):
    return synthetic_cfb({key[0]: value for key, value in streams(text).items()})


class TextRecoveryTests(unittest.TestCase):
    def test_default_retains_surrounding_text_and_never_field_instructions(self):
        result = read_bytes(package('前😀\x13 INCLUDETEXT secret\x14保存值\x15中\x01后\x00尾\r'))
        self.assertEqual(result['text'], '前😀保存值中\ufffc后\ufffc尾\n')
        self.assertEqual(result['status'], 'partial')
        self.assertNotIn('secret', json.dumps(result, ensure_ascii=False))
        self.assertFalse(result['fields_evaluated'])
        self.assertFalse(result['objects_decoded'])
        controls = next(issue for issue in result['issues'] if issue['code'] == 'uninterpreted_control_markers_not_decoded')
        self.assertEqual(controls['locations'][0]['marker'], 'U+0000')
        self.assertEqual(controls['locations'][0]['cp'], 31)
        self.assertEqual(controls['locations'][0]['stream'], 'WordDocument')
        self.assertGreater(controls['locations'][0]['offset'], 4096)

    def test_all_unsupported_c0_are_marked_not_silently_dropped(self):
        for code in set(range(32)) - {1, 7, 8, 9, 11, 12, 13, 19, 20, 21}:
            with self.subTest(code=code):
                result = read_bytes(package('甲' + chr(code) + '乙\r'))
                self.assertEqual(result['text'], '甲\ufffc乙\n')
                self.assertEqual(result['status'], 'partial')

    def test_unanchored_shape_is_unknown_marker_not_validated_shape(self):
        result = read_bytes(package('前\x08后\r'))
        self.assertEqual(result['text'], '前\ufffc后\n')
        self.assertNotIn('drawn_shape_anchor_not_rendered', [item['code'] for item in result['issues']])
        issue = next(item for item in result['issues'] if item['code'] == 'uninterpreted_control_markers_not_decoded')
        self.assertEqual(issue['locations'][0]['anchor_status'], 'unverified')

    def test_strict_and_explicit_rejections_remain_available(self):
        for text, options in [('A\x00B\r', {'text_policy': 'strict'}),
                              ('A\x13X\x14Y\x15B\r', {'field_policy': 'reject'}),
                              ('A\x01B\r', {'object_policy': 'reject'}),
                              ('A\x08B\r', {'object_policy': 'reject'}),
                              ('A\x00B\r', {'compatibility': 'strict'})]:
            with self.subTest(options=options), self.assertRaises(ParseError):
                read_bytes(package(text), **options)
        with self.assertRaises(ParseError):
            parse_streams(streams('A\x00B\r'))

    def test_malformed_fields_and_utf16_and_terminal_still_rejected(self):
        for text in ('A\x13X\r', 'A\x14B\r', 'A\x15B\r', 'A\x13X\x14Y\x14Z\x15\r', 'A\x00B'):
            with self.subTest(text=repr(text)), self.assertRaises(ParseError) as caught:
                read_bytes(package(text))
            self.assertEqual(caught.exception.status, 'corrupt')
        with self.assertRaises(ParseError):
            parse_streams(document([(b'\x00\xd8\r\x00', False)]), text_policy='recover')

    def test_budgets_encryption_and_anchor_corruption_not_downgraded(self):
        with patch('read_wps.MAX_OBJECT_MARKERS', 1), self.assertRaises(ParseError) as caught:
            read_bytes(package('A\x00B\x00C\r'))
        self.assertEqual(caught.exception.status, 'resource_limit')
        encrypted = replace_word(streams('A\x00B\r'), 10, 0x0100, 2)
        with self.assertRaises(ParseError) as caught:
            read_bytes(synthetic_cfb({key[0]: value for key, value in encrypted.items()}))
        self.assertEqual(caught.exception.status, 'encrypted')
        damaged = shaped_document()
        damaged[('0Table',)] = damaged[('0Table',)][:-1]
        with self.assertRaises(ParseError):
            read_bytes(synthetic_cfb({key[0]: value for key, value in damaged.items()}))

    def test_instruction_controls_not_exposed_and_source_alignment_not_invented(self):
        result = read_bytes(package('前\x13SECRET\x00\x01\x14结果\x15后\r'), binary_paragraph_sources=True)
        self.assertEqual(result['text'], '前结果后\n')
        self.assertEqual(result['paragraph_sources'], [])
        self.assertEqual(result['paragraph_source_status'], 'unavailable_after_text_transform')
        self.assertNotIn('SECRET', json.dumps(result))

    def test_plain_text_and_et_default_unchanged(self):
        result = read_bytes(package('正文\r'))
        self.assertEqual(result['text'], '正文\n')
        self.assertNotIn('uninterpreted_control_markers_not_decoded', [issue['code'] for issue in result['issues']])
        data = synthetic_cfb({'Workbook': b'\t\x08\x10\x00\x00\x06\x05\x00\xbb\x01\xcc\x0c\x00\x00\x00\x00'})
        with self.assertRaises(ParseError):
            read_bytes(data)

    def test_cli_partial_json_text_and_strict_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'input.wps'
            path.write_bytes(package('前\x00后\r'))
            command = [sys.executable, '-I', '-S', '-B', str((Path(__file__).parents[1] / 'read_wps.py')), './input.wps']
            child = subprocess.run(command, cwd=temporary, capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(child.returncode, 3)
            self.assertEqual(json.loads(child.stdout)['text'], '前\ufffc后\n')
            child = subprocess.run(command + ['--format', 'text'], cwd=temporary, capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(child.returncode, 3)
            self.assertEqual(child.stdout, '前\ufffc后\n')
            self.assertEqual(json.loads(child.stderr)['status'], 'partial')
            child = subprocess.run(command + ['--text-policy', 'strict'], cwd=temporary, capture_output=True, text=True, encoding='utf-8')
            self.assertEqual(child.returncode, 1)
            self.assertIsNone(json.loads(child.stdout)['text'])


if __name__ == '__main__':
    unittest.main()
