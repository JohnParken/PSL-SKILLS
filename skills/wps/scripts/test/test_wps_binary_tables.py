"""Literal PAPX row fixtures; synthetic, never WPS producer acceptance."""
import sys
from pathlib import Path

# Test-only bootstrap: support discovery and direct execution with -I.
sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]

import struct
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from read_wps import parse_streams, ParseError, read_bytes
from wps_binary_tables import initial_cell_properties, table_width_operand
from test_wps import document, synthetic_cfb


def fixture(text='甲\x07乙\x07\x07\r', compressed=False):
    raw = text.encode('latin1') if compressed else text.encode('utf-16le')
    streams = document([(raw, compressed)])
    word = bytearray(streams[('WordDocument',)])
    table = bytearray(streams[('0Table',)])
    step = 1 if compressed else 2
    # One property run per paragraph/cell/row mark, independent literal opcodes.
    ends = []
    cp = 0
    for char in text:
        cp += 2 if ord(char) > 0xffff else 1
        if char in '\r\x07':
            ends.append(4096 + cp * step)
    offsets = [4096] + ends
    page_offset = ((len(word) + 511) // 512 + 1) * 512
    word.extend(bytes(page_offset + 512 - len(word)))
    struct.pack_into('<' + 'I' * len(offsets), word, page_offset, *offsets)
    array_start = 4 * len(offsets)
    cursor = 256
    previous = ''
    marks = [char for char in text if char in '\r\x07']
    for i, char in enumerate(marks):
        is_row = char == '\x07' and previous == '\x07' and i == len(marks) - 2
        if char == '\r' and i == len(marks) - 1:
            props = b'\0\0'
        else:
            props = b'\0\0\x16\x24\1\x17\x24' + bytes((int(is_row),)) + b'\x49\x66\1\0\0\0'
        if len(props) % 2:
            raise AssertionError('Fixture requires even-length group')
        word[page_offset + array_start + 13 * i] = cursor // 2
        block = bytes((0, len(props) // 2)) + props
        word[page_offset + cursor:page_offset + cursor + len(block)] = block
        cursor += len(block)
        previous = char
    word[page_offset + 511] = len(ends)
    start = len(table)
    table.extend(struct.pack('<3I', offsets[0], offsets[-1], page_offset // 512))
    struct.pack_into('<II', word, 258, start, 12)
    return {('WordDocument',): bytes(word), ('0Table',): bytes(table)}, page_offset


class BinaryTableTests(unittest.TestCase):
    def test_local_width_operands_decode_or_preserve_invalid_without_guessing(self):
        source = {'stream': 'Data', 'offset': 29}
        width = table_width_operand(0xD635, b'\x01\x03\x03\xe8\x03', source)
        self.assertEqual((width['interpretation'], width['first_cell'], width['limit_cell'],
                          width['width_type'], width['width_raw']), ('decoded', 1, 3, 3, 1000))
        margin = table_width_operand(0xD634, b'\x00\x01\x05\x03\x0f\x00', source)
        self.assertEqual((margin['interpretation'], margin['sides_mask'], margin['width_raw']), ('decoded', 5, 15))
        for sprm, raw in [(0xD635, b'\0' * 4), (0xD635, b'\x02\x01\x03\0\0'),
                          (0xD635, b'\x00\x01\x03\xff\xff'),
                          (0xD634, b'\x00\x01\x10\x03\0\0'),
                          (0xD634, b'\x00\x01\x01\x00\x01\0')]:
            with self.subTest(sprm=sprm, raw=raw):
                result = table_width_operand(sprm, raw, source)
                self.assertEqual(result['interpretation'], 'invalid_or_unsupported')
                self.assertEqual(result['raw_hex'], raw.hex())
                self.assertEqual(result['source'], source)

    def test_initial_tc80_flags_are_bounded_and_not_final_merges(self):
        first = struct.pack('<Hh', 2 | (1 << 5), 123) + bytes(16)
        second = struct.pack('<Hh', 1 | (2 << 5), 456) + bytes(16)
        result = initial_cell_properties(first + second, 2)
        self.assertEqual([cell['horizontal_merge_initial'] for cell in result['cells']], [2, 1])
        self.assertEqual([cell['vertical_merge_initial'] for cell in result['cells']], [1, 2])
        self.assertEqual([cell['preferred_width_raw'] for cell in result['cells']], [123, 456])
        self.assertEqual(result['complete_tc80_records'], 2)
        short = initial_cell_properties(first + second[:3], 2)
        self.assertEqual(short['complete_tc80_records'], 1)
        self.assertEqual(short['incomplete_tc80_bytes'], 3)
        self.assertEqual(short['columns_without_complete_tc80'], 1)
        self.assertEqual(initial_cell_properties(first + second, 1)['complete_tc80_records'], 1)

    def test_explicit_cells_and_row_with_original_text_preserved(self):
        streams, _ = fixture()
        out = parse_streams(streams)
        self.assertEqual(out['text'], '甲\t乙\t\t\n')
        self.assertEqual(out['status'], 'partial')
        structure = out['binary_table_structure']
        self.assertEqual(structure['status'], 'rows_partial')
        self.assertEqual([cell['text'] for cell in structure['rows'][0]['cells']], ['甲', '乙'])
        self.assertEqual(structure['rows'][0]['source']['row_mark_cp'], 4)
        self.assertEqual(structure['rows'][0]['cells'][1]['source']['cp_start'], 2)
        self.assertEqual(structure['grid_and_merges'], 'unknown')

    def test_compressed_empty_cell_and_supplementary_cp(self):
        for text, compressed, expected in [('A\x07\x07\x07\r', True, ['A', '']),
                                            ('😀\x07乙\x07\x07\r', False, ['😀', '乙'])]:
            with self.subTest(text=text):
                streams, _ = fixture(text, compressed)
                cells = parse_streams(streams)['binary_table_structure']['rows'][0]['cells']
                self.assertEqual([cell['text'] for cell in cells], expected)
                self.assertEqual(cells[1]['source']['cp_start'], 2 if compressed else 3)

    def test_multiple_paragraphs_in_cell(self):
        streams, _ = fixture('一\r二\x07三\x07\x07\r')
        out = parse_streams(streams)['binary_table_structure']
        self.assertEqual([cell['text'] for cell in out['rows'][0]['cells']], ['一\n二', '三'])
        self.assertEqual(out['rows'][0]['cells'][0]['source']['cp_start'], 0)

    def test_missing_bte_never_guesses_from_tabs_or_markers(self):
        out = parse_streams(document([('甲\x07乙\x07\x07\r'.encode('utf-16le'), False)]))
        self.assertEqual(out['binary_table_structure']['rows'], [])
        self.assertEqual(len(out['binary_table_structure']['unresolved_markers']), 3)
        self.assertEqual(out['status'], 'partial')
        self.assertNotIn('binary_table_structure', parse_streams(document([(b'A\tB\r', True)])))

    def test_unknown_properties_do_not_scan_operand_or_guess_suffix_row(self):
        streams, page = fixture()
        word = bytearray(streams[('WordDocument',)])
        word[page + 260:page + 262] = b'\0\xc6'  # unknown variable paragraph operand
        streams[('WordDocument',)] = bytes(word)
        out = parse_streams(streams)['binary_table_structure']
        self.assertEqual(out['rows'], [])

    def test_bad_metadata_reports_partial_without_losing_body(self):
        streams, page = fixture()
        word = bytearray(streams[('WordDocument',)])
        word[page + 511] = 30
        streams[('WordDocument',)] = bytes(word)
        out = parse_streams(streams)
        self.assertEqual(out['text'], '甲\t乙\t\t\n')
        self.assertIn('binary_table_metadata_invalid', [item['code'] for item in out['issues']])
        self.assertEqual(out['binary_table_structure']['rows'], [])

    def test_row_definition_preserves_initial_edges_without_claiming_grid(self):
        streams, page = fixture()
        word = bytearray(streams[('WordDocument',)])
        # Third run is the row mark. Add a literal 2-column TDefTable with no TC80.
        last_pointer = word[page + 20 + 13 * 3] * 2
        word[page + 400:page + 404] = word[page + last_pointer:page + last_pointer + 4]
        word[page + 20 + 13 * 3] = 200
        pointer = word[page + 20 + 13 * 2] * 2
        original = bytes(word[page + pointer + 2:page + pointer + 16])
        definition = (b'\x08\xd6\x08\0\2\0\0\xe8\3\xd0\7\x15\x36\x01\x15\x36\x00'
                      b'\x35\xd6\x05\x00\x01\x03\xe8\x03'
                      b'\x34\xd6\x06\x00\x01\x01\x03\x0f\x00\x15\x36\x00')
        # Use nonzero cb encoding for the resulting odd-length grpprl.
        group = original + definition
        word[page + pointer:page + pointer + 1 + len(group)] = bytes(((len(group) + 1) // 2,)) + group
        streams[('WordDocument',)] = bytes(word)
        result = parse_streams(streams)['binary_table_structure']
        self.assertEqual(result['rows'][0]['initial_definition']['column_count'], 2)
        self.assertEqual(result['rows'][0]['initial_definition']['edges_twips'], [0, 1000, 2000])
        self.assertEqual(result['rows'][0]['merges'], 'unknown')
        self.assertEqual(result['rows'][0]['uninterpreted_table_modifier_ids'],
                         {'0x3615': 3, '0xd635': 1, '0xd634': 1})
        operands = result['rows'][0]['local_width_operands']
        self.assertEqual([(item['sprm'], item['width_raw']) for item in operands],
                         [('0xd635', 1000), ('0xd634', 15)])

    def test_budget_fails_explicitly(self):
        streams, _ = fixture()
        with patch('wps_binary_tables.MAX_MARKS', 1), self.assertRaises(ParseError) as error:
            parse_streams(streams)
        self.assertEqual(error.exception.status, 'resource_limit')

    def test_default_omitted_regions_have_explicit_cp_ranges(self):
        streams = document(other_stories={84: '秘密页眉\r'.encode('utf-16le')})
        out = parse_streams(streams)
        self.assertEqual(out['status'], 'partial')
        self.assertEqual(out['omitted_regions'][0]['type'], 'headers_footers')
        self.assertEqual(out['omitted_regions'][0]['cp_units'], 5)
        self.assertNotIn('秘密', str(out))
        self.assertNotIn('omitted_regions', parse_streams(streams, include_related=True))

    def test_nested_depth_piece_properties_and_overlap_remain_unresolved(self):
        streams, page = fixture()
        for kind in ('nested', 'piece', 'overlap', 'misaligned'):
            changed = dict(streams)
            word = bytearray(changed[('WordDocument',)])
            if kind == 'nested':
                word[page + 268] = 2  # depth operand, not a level-one table
            elif kind == 'piece':
                table = bytearray(changed[('0Table',)])
                table[19:21] = b'\2\0'  # simple nonzero PRM on only piece
                changed[('0Table',)] = bytes(table)
            elif kind == 'overlap':
                start = struct.unpack_from('<I', word, 258)[0]
                table = bytearray(changed[('0Table',)])
                struct.pack_into('<I', table, start + 8, 8)  # page aliases text at 4096
                changed[('0Table',)] = bytes(table)
            else:
                struct.pack_into('<I', word, page + 4, 4099)
            changed[('WordDocument',)] = bytes(word)
            with self.subTest(kind=kind):
                result = parse_streams(changed)
                self.assertEqual(result['binary_table_structure']['rows'], [])
                self.assertEqual(result['status'], 'partial')

    def test_field_filtering_does_not_expose_instructions_or_shifted_table_sources(self):
        streams = document([('前\x13秘密\x14结果\x15\x07乙\x07\x07\r'.encode('utf-16le'), False)])
        result = parse_streams(streams, field_policy='cached_result')
        self.assertNotIn('秘密', str(result))
        self.assertNotIn('binary_table_structure', result)
        self.assertIn('binary_table_fields_unresolved', [item['code'] for item in result['issues']])

    def test_public_api_adds_assessment(self):
        streams, _ = fixture()
        payload = synthetic_cfb({key[0]: value for key, value in streams.items()})
        out = read_bytes(payload)
        self.assertEqual(out['status'], 'partial')
        self.assertEqual(out['reading_assessment']['outcome'], 'partial')


if __name__ == '__main__':
    unittest.main()
