"""Synthetic literal FIB 101/field tests; never WPS producer evidence."""
from pathlib import Path
import struct
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from read_wps import ParseError, parse_streams
from test_wps import document, replace_word


def version_101(text='正文\r', base=0xC1):
    streams = document([(text.encode('utf-16le'), False)] if text else [])
    for offset, value in ((2, base), (152, 136), (1242, 2), (1244, 0x101), (1246, 0)):
        streams = replace_word(streams, offset, value, 2)
    return streams


class DocExtensions(unittest.TestCase):
    def error(self, streams, status, cp=None, **options):
        with self.assertRaises(ParseError) as caught:
            parse_streams(streams, **options)
        self.assertEqual(caught.exception.status, status)
        if cp is not None:
            self.assertEqual(caught.exception.location['cp'], cp)
        return caught.exception

    def test_101_effective_version_and_literal_layout(self):
        for base in (0xC1, 0x101):
            result = parse_streams(version_101(base=base))
            self.assertEqual(result['text'], '正文\n')
            self.assertEqual(result['fib_version'], '0x101')
            self.assertEqual(result['fib_base_version'], hex(base))
        self.assertEqual(parse_streams(version_101(''))['paragraphs'], [])

    def test_101_bad_extensions_and_other_versions(self):
        for offset, value in ((152, 93), (1242, 0), (1242, 5), (1244, 0x10C), (1244, 0x112)):
            self.error(replace_word(version_101(base=0x101), offset, value, 2), 'unsupported')
        exc = self.error(replace_word(version_101(), 1246, 16, 2), 'corrupt')
        self.assertEqual(exc.location['offset'], 1246)
        streams = version_101()
        streams[('WordDocument',)] = streams[('WordDocument',)][:1247]
        self.error(streams, 'corrupt')

    def test_101_piece_cannot_overlap_larger_fib(self):
        streams = version_101()
        table = bytearray(streams[('0Table',)])
        struct.pack_into('<I', table, 15, 1200)
        streams[('0Table',)] = table
        self.error(streams, 'corrupt')

    def test_10c_version_layout_and_mismatch(self):
        streams = version_101()
        for offset, value in ((152, 164), (1242, 0), (1244, 0), (1466, 2), (1468, 0x10C), (1470, 0)):
            streams = replace_word(streams, offset, value, 2)
        result = parse_streams(streams)
        self.assertEqual((result['fib_base_version'], result['fib_version'], result['text']),
                         ('0xc1', '0x10c', '正文\n'))
        for offset, value in ((1466, 0), (1466, 5), (1468, 0xD9), (1468, 0x112)):
            self.error(replace_word(streams, offset, value, 2), 'unsupported')
        self.error(replace_word(streams, 1470, 16, 2), 'corrupt')

    def test_cached_fields_nested_in_results_and_instructions(self):
        text = 'A\x13 IF \x13 DATE\x14HIDDEN\x15\x14甲\x13 PAGE\x142\x15乙\x15Z\r'
        streams = version_101(text)
        self.error(streams, 'unsupported')
        result = parse_streams(streams, field_policy='cached_result')
        self.assertEqual(result['text'], 'A甲2乙Z\n')
        self.assertEqual(result['paragraphs'], ['A甲2乙Z'])
        self.assertEqual(result['status'], 'partial')
        self.assertTrue(result['partial'])
        self.assertFalse(result['fields_evaluated'])
        self.assertEqual(result['issues'][0]['field_count'], 3)

    def test_no_result_empty_result_and_plain_policy(self):
        result = parse_streams(version_101('A\x13SECRET\x15B\x13X\x14\x15\r'), field_policy='cached_result')
        self.assertEqual(result['text'], 'AB\n')
        self.assertEqual(parse_streams(version_101(), field_policy='cached_result')['status'], 'ok')
        self.error(version_101(), 'unsupported', field_policy='execute')

    def test_malformed_field_delimiters_and_cp_positions(self):
        for text, cp in (('😀\x14\r', 2), ('\x15\r', 0), ('\x13X\x14Y\x14\r', 4), ('A\x13X\r', 1)):
            self.error(version_101(text), 'corrupt', cp, field_policy='cached_result')
        # Objects remain unsupported even in discarded instructions.
        self.error(version_101('\x13\x01\x14X\x15\r'), 'unsupported', 1, field_policy='cached_result')

    def test_field_budgets_before_unbounded_stack(self):
        with patch('read_wps.MAX_FIELD_DEPTH', 2):
            self.error(version_101('\x13\x13\x13\x15\x15\x15\r'), 'resource_limit', 2, field_policy='cached_result')
        with patch('read_wps.MAX_FIELDS', 1):
            self.error(version_101('\x13\x15\x13\x15\r'), 'resource_limit', 2, field_policy='cached_result')

    def test_object_placeholder_explicit_partial_and_locations(self):
        streams = version_101('😀\x01甲\r')
        self.error(streams, 'unsupported', 2)
        result = parse_streams(streams, object_policy='placeholder')
        self.assertEqual(result['text'], '😀\ufffc甲\n')
        self.assertEqual(result['status'], 'partial')
        self.assertFalse(result['objects_decoded'])
        issue = result['issues'][0]
        self.assertEqual(issue['count'], 1)
        self.assertEqual(issue['locations'], [{'stream': 'WordDocument', 'offset': 4100,
                                              'piece': 0, 'cp': 2, 'placeholder_emitted': True}])

    def test_objects_in_hidden_field_instructions_are_diagnosed(self):
        result = parse_streams(version_101('\x13X\x01\x14R\x01\x15\r'),
                               field_policy='cached_result', object_policy='placeholder')
        self.assertEqual(result['text'], 'R\ufffc\n')
        issue = result['issues'][1]
        self.assertEqual(issue['count'], 2)
        self.assertEqual([x['placeholder_emitted'] for x in issue['locations']], [False, True])
        for code in (0, 2, 8, 0x1F):
            self.error(version_101(chr(code)+'\r'), 'unsupported', 0,
                       field_policy='cached_result', object_policy='placeholder')

    def test_object_budget_and_unknown_policy(self):
        with patch('read_wps.MAX_OBJECT_MARKERS', 1):
            self.error(version_101('\x01\x01\r'), 'resource_limit', 1, object_policy='placeholder')
        self.error(version_101(), 'unsupported', object_policy='decode')


if __name__ == '__main__':
    unittest.main()
