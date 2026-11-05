"""Independent literal Data/PrcData fixtures and malicious reference chains."""
import sys
from pathlib import Path

# Test-only bootstrap: support discovery and direct execution with -I.
sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]

import struct
import unittest
from unittest.mock import patch

from test_wps_binary_tables import fixture
from read_wps import parse_streams, ParseError


def external_fixture():
    streams, page = fixture()
    word = bytearray(streams[('WordDocument',)])
    count = word[page + 511]
    arrays = 4 * (count + 1)
    data = bytearray()
    offsets = []
    for index in range(count - 1):
        pointer = page + word[page + arrays + 13 * index] * 2
        props = bytes(word[pointer + 4:pointer + 16])
        offsets.append(len(data))
        data.extend(struct.pack('<H', len(props)) + props)
        word[pointer:pointer + 10] = b'\0\4\0\0\x46\x66' + struct.pack('<I', offsets[-1])
    streams[('WordDocument',)] = bytes(word)
    streams[('Data',)] = bytes(data)
    return streams, page, offsets


class HugePapxTests(unittest.TestCase):
    def test_external_direct_cells_match_independent_literals(self):
        streams, _, _ = external_fixture()
        result = parse_streams(streams)
        table = result['binary_table_structure']
        self.assertEqual([cell['text'] for cell in table['rows'][0]['cells']], ['甲', '乙'])
        self.assertEqual(result['text'], '甲\t乙\t\t\n')
        self.assertEqual(table['external_reference_count'], 3)
        self.assertEqual(table['external_property_references'][0]['target']['stream'], 'Data')
        self.assertEqual(table['grid_and_merges'], 'unknown')
        self.assertEqual(result['status'], 'partial')

    def test_bad_lengths_missing_stream_offsets_and_cycles_keep_body(self):
        for kind in ('negative', 'short', 'oversized', 'truncated', 'missing', 'outside', 'cycle'):
            with self.subTest(kind=kind):
                streams, page, _ = external_fixture()
                data = bytearray(streams[('Data',)])
                if kind in ('negative', 'short', 'oversized'):
                    struct.pack_into('<H', data, 0, {'negative': 65535, 'short': 9, 'oversized': 0x3FA3}[kind])
                elif kind == 'truncated':
                    data = data[:5]
                elif kind == 'outside':
                    word = bytearray(streams[('WordDocument',)])
                    struct.pack_into('<I', word, page + 262, 0xFFFFFFF0)
                    streams[('WordDocument',)] = bytes(word)
                elif kind == 'cycle':
                    data = bytearray(struct.pack('<H', 12) + b'\x46\x66\0\0\0\0\x31\x24\0\x03\x24\0')
                streams[('Data',)] = bytes(data)
                if kind == 'missing':
                    del streams[('Data',)]
                result = parse_streams(streams)
                self.assertEqual(result['text'], '甲\t乙\t\t\n')
                self.assertEqual(result['binary_table_structure']['rows'], [])
                self.assertIn('binary_table_metadata_invalid', [item['code'] for item in result['issues']])

    def test_chain_and_referenced_array_suffix_rules(self):
        streams, _, _ = external_fixture()
        data = bytearray(streams[('Data',)])
        original = bytes(data[2:14])
        target = len(data)
        data.extend(struct.pack('<H', len(original)) + original)
        # First array switches to PTableProps. Its following fake flags must not run.
        data[:14] = struct.pack('<H', 12) + b'\x6b\x64' + struct.pack('<I', target) + b'\x16\x24\0\x17\x24\1'
        streams[('Data',)] = bytes(data)
        result = parse_streams(streams)['binary_table_structure']
        self.assertEqual([cell['text'] for cell in result['rows'][0]['cells']], ['甲', '乙'])
        self.assertEqual(result['external_reference_count'], 4)

    def test_byte_budget_still_explicitly_fails(self):
        streams, _, _ = external_fixture()
        with patch('wps_binary_tables.MAX_PROPERTY_BYTES', 20), self.assertRaises(ParseError) as caught:
            parse_streams(streams)
        self.assertEqual(caught.exception.status, 'resource_limit')

    def test_nonfirst_huge_is_ignored_without_following_missing_data(self):
        from test_wps_table_layout import decorated_fixture
        streams = decorated_fixture(b'\x31\x24\1\x46\x66\xff\xff\xff\xff')
        result = parse_streams(streams)['binary_table_structure']
        self.assertEqual([cell['text'] for cell in result['rows'][0]['cells']], ['甲', '乙'])
        self.assertEqual(result['external_reference_count'], 0)

    def test_deep_acyclic_chain_hits_explicit_resource_limit(self):
        streams, _, _ = external_fixture()
        data = bytearray(streams[('Data',)])
        first = len(data)
        for index in range(34):
            target = first + (index + 1) * 14
            data.extend(struct.pack('<H', 12) + b'\x46\x66' + struct.pack('<I', target) + b'\x31\x24\0\x03\x24\0')
        data[:14] = struct.pack('<H', 12) + b'\x46\x66' + struct.pack('<I', first) + b'\x31\x24\0\x03\x24\0'
        streams[('Data',)] = bytes(data)
        with self.assertRaises(ParseError) as caught:
            parse_streams(streams)
        self.assertEqual(caught.exception.status, 'resource_limit')

    def test_overlapping_external_arrays_are_not_combined(self):
        streams, page, _ = external_fixture()
        old = streams[('Data',)]
        first = b'\x12\x64\x0c\0\0\0' + old[2:14]
        streams[('Data',)] = struct.pack('<H', len(first)) + first + old[14:]
        word = bytearray(streams[('WordDocument',)])
        # First valid array contains a 4-byte layout operand beginning with 12.
        # The next group tries to reinterpret its interior as a second array.
        struct.pack_into('<I', word, page + 278, 4)
        struct.pack_into('<I', word, page + 294, 34)
        streams[('WordDocument',)] = bytes(word)
        result = parse_streams(streams)
        self.assertEqual(result['binary_table_structure']['rows'], [])
        self.assertTrue(any('Overlapping external' in item.get('message', '') for item in result['issues']))

    def test_inline_huge_with_nonzero_style_does_not_claim_rows(self):
        streams, page, _ = external_fixture()
        word = bytearray(streams[('WordDocument',)])
        struct.pack_into('<H', word, page + 258, 1)
        streams[('WordDocument',)] = bytes(word)
        result = parse_streams(streams)
        self.assertEqual(result['binary_table_structure']['rows'], [])
        self.assertTrue(any('zero style' in item.get('message', '') for item in result['issues']))


if __name__ == '__main__':
    unittest.main()
