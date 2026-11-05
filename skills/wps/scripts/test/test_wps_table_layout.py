"""Literal direct-PAPX operands: do not scan unknown bytes for table flags."""
import sys
from pathlib import Path

# Test-only bootstrap: support discovery and direct execution with -I.
sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]

import struct
import unittest

from test_wps_binary_tables import fixture
from read_wps import parse_streams
from wps_binary_tables import LAYOUT_PROPERTIES


def decorated_fixture(prefix, omit_false_row_end=False):
    streams, page = fixture()
    word = bytearray(streams[('WordDocument',)])
    count = word[page + 511]
    arrays = 4 * (count + 1)
    saved = []
    for index in range(count):
        at = page + word[page + arrays + 13 * index] * 2
        assert word[at] == 0
        size = 2 * word[at + 1]
        saved.append(bytes(word[at + 2:at + 2 + size]))
    cursor = 128
    for index, props in enumerate(saved):
        body = props[:2] + prefix + props[2:] if index < count - 1 else props
        if omit_false_row_end:
            body = body[:2] + body[2:].replace(b'\x17\x24\0', b'')
        word[page + arrays + 13 * index] = cursor // 2
        if len(body) % 2:
            block = bytes(((len(body) + 1) // 2,)) + body
        else:
            block = bytes((0, len(body) // 2)) + body
        assert cursor + len(block) < 511
        word[page + cursor:page + cursor + len(block)] = block
        cursor += len(block)
        cursor += cursor % 2
    streams[('WordDocument',)] = bytes(word)
    return streams


class LayoutOperandTests(unittest.TestCase):
    def test_false_row_end_default_does_not_require_redundant_operand(self):
        result = parse_streams(decorated_fixture(b'', omit_false_row_end=True))
        self.assertEqual([cell['text'] for cell in result['binary_table_structure']['rows'][0]['cells']], ['甲', '乙'])

    def test_each_allowlisted_fixed_operand_preserves_literal_cells(self):
        for sprm, (_, width) in LAYOUT_PROPERTIES.items():
            with self.subTest(sprm=hex(sprm)):
                streams = decorated_fixture(struct.pack('<H', sprm) + bytes(width))
                result = parse_streams(streams)
                table = result['binary_table_structure']
                self.assertEqual([cell['text'] for cell in table['rows'][0]['cells']], ['甲', '乙'])
                self.assertEqual(result['text'], '甲\t乙\t\t\n')
                self.assertEqual(result['status'], 'partial')
                self.assertGreater(table['layout_property_count'], 0)
                self.assertEqual(table['grid_and_merges'], 'unknown')

    def test_external_huge_properties_never_skipped_even_with_apparent_flags(self):
        result = parse_streams(decorated_fixture(b'\x46\x66\x16\x24\x17\x24'))
        table = result['binary_table_structure']
        self.assertEqual(table['rows'], [])
        self.assertIn('binary_table_metadata_invalid', [item['code'] for item in result['issues']])
        self.assertEqual(result['text'], '甲\t乙\t\t\n')

    def test_truncated_known_operand_keeps_body_and_marks_metadata_invalid(self):
        streams, page = fixture()
        word = bytearray(streams[('WordDocument',)])
        # A two-byte SPRM fits, its four-byte operand does not.
        word[page + 260:page + 262] = b'\x12\x64'
        word[page + 257] = 2
        streams[('WordDocument',)] = bytes(word)
        result = parse_streams(streams)
        self.assertEqual(result['text'], '甲\t乙\t\t\n')
        self.assertEqual(result['binary_table_structure']['rows'], [])
        self.assertIn('binary_table_metadata_invalid', [item['code'] for item in result['issues']])


if __name__ == '__main__':
    unittest.main()
