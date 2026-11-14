"""Synthetic semantic anchor recovery; shared structures remain strict."""
from pathlib import Path
import struct
import sys
import unittest
from unittest.mock import patch

sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]
from read_wps import ParseError, parse_streams, read_bytes
from test_wps import document
from test_wps_msdoc_maximum import cfb_payload, set_pair, shape_plc


def fixture(text=b'A\r', header=False):
    streams = document([(text, True)], {84: 'H\r'.encode('utf-16le')} if header else None)
    set_pair(streams, 41 if header else 40, shape_plc((0,), (700,)))
    return streams


def maximum(streams):
    return parse_streams(streams, maximum=True, include_related=True, text_policy='recover',
                         object_policy='placeholder', field_policy='cached_result')


class AnchorRecoveryTests(unittest.TestCase):
    def test_public_entry_retains_body_and_reports_exact_location(self):
        streams = fixture()
        result = read_bytes(cfb_payload(streams), reading_mode='maximum')
        self.assertEqual(result['text'], 'A\n')
        self.assertEqual(result['status'], 'partial')
        issue = next(item for item in result['issues'] if item['code'] == 'binary_shape_anchor_unresolved')
        offset = struct.unpack_from('<I', streams[('WordDocument',)], 154 + 40 * 8)[0]
        self.assertEqual((issue['stream'], issue['offset'], issue['cp']), ('0Table', offset, 0))
        self.assertEqual(issue['anchor_status'], 'unverified')
        self.assertFalse(result.get('drawing_objects', {}).get('objects'))

    def test_standard_stays_strict(self):
        with self.assertRaises(ParseError) as caught:
            parse_streams(fixture(), object_policy='placeholder')
        self.assertEqual(str(caught.exception), 'PlcfSpa anchor does not point to U+0008')

    def test_header_mismatch_does_not_discard_header_text(self):
        result = maximum(fixture(header=True))
        self.assertEqual(result['text'], 'A\n')
        header = result['related_stories'][1]
        self.assertEqual(header['text'], 'H\n')
        self.assertEqual(header['status'], 'partial')
        self.assertTrue(any(item['code'] == 'binary_shape_anchor_unresolved' for item in header['issues']))

    def test_valid_anchors_remain_separate_from_bad_ones(self):
        streams = document([(b'A\x08\r', True)])
        set_pair(streams, 40, shape_plc((0, 1), (700, 701)))
        result = maximum(streams)
        self.assertEqual(result['text'], 'A\ufffc\n')
        locations = next(item for item in result['issues'] if item['code'] == 'drawn_shape_anchor_not_rendered')['locations']
        self.assertEqual([item['cp'] for item in locations], [1])
        self.assertEqual([item['shape_id'] for item in locations], [701])

    def test_rejected_anchors_still_reserve_shared_range(self):
        streams = fixture(header=True)
        word = bytearray(streams[('WordDocument',)])
        word[154 + 40 * 8:154 + 41 * 8] = word[154 + 41 * 8:154 + 42 * 8]
        streams[('WordDocument',)] = bytes(word)
        with self.assertRaises(ParseError) as caught:
            maximum(streams)
        self.assertIn('aliases', str(caught.exception))

    def test_resource_and_out_of_story_remain_fatal(self):
        with patch('wps_binary_shapes.MAX_SHAPES', 0), self.assertRaises(ParseError) as caught:
            maximum(fixture())
        self.assertEqual(caught.exception.status, 'resource_limit')
        streams = fixture()
        set_pair(streams, 40, shape_plc((2,), (700,)))
        with self.assertRaises(ParseError) as caught:
            maximum(streams)
        self.assertIn('outside its declared story', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
