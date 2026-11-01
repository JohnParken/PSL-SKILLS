"""Regression of unchanged MS-DOC content under selected rendering policies."""
import unittest

from read_wps import ParseError, parse_streams
from test_wps import document


class LazyRenderTests(unittest.TestCase):
    def test_selected_but_unused_policies_do_not_change_text_or_sources(self):
        stored = '业务正文\r' * 512
        streams = document([(stored.encode('utf-16le'), False)])
        baseline = parse_streams(streams)
        candidate = parse_streams(
            streams, object_policy='placeholder', field_policy='cached_result',
            binary_paragraph_sources=True)
        self.assertEqual(candidate['status'], baseline['status'])
        self.assertEqual(candidate['text'], baseline['text'])
        self.assertEqual(candidate['paragraphs'], baseline['paragraphs'])
        self.assertEqual(candidate['issues'], baseline['issues'])
        self.assertEqual(candidate['paragraph_source_status'], 'exact_stored_cp')
        self.assertEqual(len(candidate['paragraph_sources']), 512)

    def test_marker_replacement_still_occurs_with_exact_diagnostics(self):
        streams = document([('甲\x01乙\r'.encode('utf-16le'), False)])
        result = parse_streams(
            streams, object_policy='placeholder', binary_paragraph_sources=True)
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['text'], '甲\ufffc乙\n')
        self.assertEqual(result['paragraph_source_status'],
                         'unavailable_after_text_transform')
        self.assertIn('picture_or_data_marker_not_decoded',
                      [item['code'] for item in result['issues']])

    def test_field_caching_keeps_existing_rewrite_behavior(self):
        streams = document([(b'A\x13 FORMULA \x14B\x15\r', True)])
        result = parse_streams(streams, field_policy='cached_result')
        self.assertEqual(result['text'], 'AB\n')
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['issues'][0]['code'], 'stored_field_results_unverified')

    def test_unrecognized_control_remains_rejected(self):
        streams = document([('甲\x03乙\r'.encode('utf-16le'), False)])
        with self.assertRaises(ParseError) as raised:
            parse_streams(streams, object_policy='placeholder',
                          field_policy='cached_result')
        self.assertEqual(raised.exception.status, 'unsupported')


if __name__ == '__main__':
    unittest.main()
