"""Synthetic direct-PAPX nested fragments, not WPS producer acceptance."""
from pathlib import Path
import struct
import sys
import unittest
from unittest.mock import patch

sys.path[:0] = [str(Path(__file__).resolve().parent), str(Path(__file__).resolve().parents[1])]
from read_wps import read_bytes, ParseError
from test_wps_msdoc_maximum import papx_document, cfb_payload
from wps_nested_tables import NestedRows


def props(depth, *, cell=False, row=False):
    value = b'\x16\x24\1\x49\x66' + struct.pack('<I', depth)
    if cell:
        value += b'\x4b\x24\1'
    if row:
        value += b'\x4c\x24\1' if depth > 1 else b'\x17\x24\1'
    return value


def read_segments(*segments):
    return read_bytes(cfb_payload(papx_document(*segments)))


class NestedTableTests(unittest.TestCase):
    def test_public_nested_cells_use_cr_and_preserve_main_text(self):
        result = read_segments(('父段\r', props(1)), ('甲\r', props(2, cell=True)),
                               ('乙\r', props(2, cell=True)), ('\r', props(2, row=True)),
                               ('尾段\x07', props(1)), ('\x07', props(1, row=True)), ('\r', b''))
        self.assertIn('甲', result['text'])
        self.assertEqual(result['status'], 'partial')
        evidence = result['binary_table_structure']['nested_row_evidence']
        self.assertEqual(evidence['issue_count'], 0)
        cell = evidence['rows'][0]['cells'][0]
        self.assertEqual(cell['text'], '父段\n尾段')
        child = cell['nested_tables'][0]['row_fragments'][0]
        self.assertEqual([item['text'] for item in child['cells']], ['甲', '乙'])
        self.assertLessEqual(cell['source']['cp_start'], child['source']['cp_start'])
        self.assertGreaterEqual(cell['source']['cp_end'], child['source']['cp_end'])

    def test_depth_three_links_only_after_outer_cell_and_row_close(self):
        result = read_segments(('父\r', props(1)), ('中\r', props(2)), ('内\r', props(3, cell=True)),
                               ('\r', props(3, row=True)), ('\r', props(2, cell=True)),
                               ('\r', props(2, row=True)), ('\x07', props(1)), ('\x07', props(1, row=True)), ('\r', b''))
        first = result['binary_table_structure']['nested_row_evidence']['rows'][0]['cells'][0]
        middle = first['nested_tables'][0]['row_fragments'][0]['cells'][0]
        self.assertEqual(middle['nested_tables'][0]['row_fragments'][0]['cells'][0]['text'], '内')

    def test_missing_parent_bad_mark_and_depth_jump_do_not_guess(self):
        cases = [
            [('内\r', props(2, cell=True)), ('\r', props(2, row=True))],
            [('父\r', props(1)), ('内\x07', props(2, cell=True)), ('\r', props(2, row=True))],
            [('父\r', props(1)), ('内\r', props(3, cell=True)), ('\r', props(3, row=True))],
            [('父\r', props(1)), ('内\r', props(2, cell=True))],
        ]
        for segments in cases:
            with self.subTest(segments=segments):
                result = read_segments(*segments, ('\x07', props(1)), ('\x07', props(1, row=True)), ('\r', b''))
                evidence = result['binary_table_structure']['nested_row_evidence']
                self.assertEqual(evidence['rows'], [])
                self.assertGreater(evidence['issue_count'], 0)
                self.assertIn('内', result['text'])

    def test_field_or_unknown_properties_discard_pending_hierarchy(self):
        for change in ({'structure_unknown': True}, {'unsupported_cell_layout': True}, {'field_overlap': True}):
            reader = NestedRows()
            reader.feed('父', '\r', 0, 2, {'depth': 1, 'in_table': 1})
            field = change.pop('field_overlap', False)
            reader.feed('不应关联', '\r', 2, 8, {'depth': 2, 'in_table': 1, 'inner_cell': 1, **change}, field)
            self.assertFalse(reader.finish(8)['rows'])
            self.assertGreater(reader.issue_count, 0)

    def test_nested_budget_is_explicit(self):
        reader = NestedRows()
        reader.feed('父', '\r', 0, 2, {'depth': 1, 'in_table': 1})
        with patch('wps_nested_tables.MAX_DEPTH', 1), self.assertRaises(ParseError):
            reader.feed('内', '\r', 2, 4, {'depth': 2, 'in_table': 1, 'inner_cell': 1})


if __name__ == '__main__':
    unittest.main()
