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

    def test_adjacent_child_rows_join_or_split_by_stored_identity(self):
        for change, expected in ((b'', [2]), (b'\x69\x74' + struct.pack('<I', 7), [1, 1])):
            result = read_segments(('P\r', props(1)), ('A\r', props(2, cell=True)),
                                   ('\r', props(2, row=True)), ('B\r', props(2, cell=True)),
                                   ('\r', props(2, row=True) + change),
                                   ('Q\x07', props(1)), ('\x07', props(1, row=True)), ('\r', b''))
            cell = result['binary_table_structure']['nested_row_evidence']['rows'][0]['cells'][0]
            child = cell['nested_tables'][0]
            self.assertEqual([table['row_count'] for table in child['tables']], expected)
            self.assertEqual(child['table_grouping'], 'stored')
            for table in child['tables']:
                self.assertEqual(table['parent_cell_source'], cell['source'])
                self.assertEqual(table['parent_cell_status'], 'stored_boundaries')
                self.assertEqual(table['layout'], 'unknown')
                self.assertEqual(table['merges'], 'unknown')
                self.assertGreaterEqual(table['source']['cp_start'], cell['source']['cp_start'])
                self.assertLessEqual(table['source']['cp_end'], cell['source']['cp_end'])

    def test_unknown_group_properties_and_bookmarks_do_not_join(self):
        for bookmarks, unknown in ((True, False), (False, True)):
            reader = NestedRows(bookmarks=bookmarks)
            cp = 0
            for text, mark, depth, flags in (
                    ('P', '\r', 1, {}), ('A', '\r', 2, {'inner_cell': 1}),
                    ('', '\r', 2, {'inner_row_end': 1}), ('B', '\r', 2, {'inner_cell': 1}),
                    ('', '\r', 2, {'inner_row_end': 1, 'grouping_unknown': unknown}),
                    ('Q', '\x07', 1, {}), ('', '\x07', 1, {'row_end': 1})):
                end = cp + len(text) + 1
                reader.feed(text, mark, cp, end, {'depth': depth, 'in_table': 1, 'istd': 0, **flags})
                cp = end
            child = reader.finish(cp)['rows'][0]['cells'][0]['nested_tables'][0]
            self.assertEqual(child['table_grouping'], 'partial')
            self.assertEqual([t['row_count'] for t in child['tables']], [1, 1])
            self.assertTrue(all(t['grouping_status'] == 'partial' for t in child['tables']))

    def test_separate_child_runs_and_cells_have_distinct_ownership(self):
        result = read_segments(('P\r', props(1)), ('A\r', props(2, cell=True)), ('\r', props(2, row=True)),
                               ('M\r', props(1)), ('B\r', props(2, cell=True)), ('\r', props(2, row=True)),
                               ('Q\x07', props(1)), ('R\r', props(1)), ('C\r', props(2, cell=True)),
                               ('\r', props(2, row=True)), ('S\x07', props(1)), ('\x07', props(1, row=True)), ('\r', b''))
        cells = result['binary_table_structure']['nested_row_evidence']['rows'][0]['cells']
        self.assertEqual([len(c['nested_tables']) for c in cells], [2, 1])
        ids = []
        for cell in cells:
            for run in cell['nested_tables']:
                table = run['tables'][0]
                ids.append(table['table_id'])
                self.assertEqual(table['parent_cell_source'], cell['source'])
        self.assertEqual(len(set(ids)), 3)

    def test_unclosed_outer_row_never_publishes_child_tables(self):
        result = read_segments(('P\r', props(1)), ('A\r', props(2, cell=True)),
                               ('\r', props(2, row=True)), ('Q\x07', props(1)), ('\r', b''))
        self.assertEqual(result['binary_table_structure']['nested_row_evidence']['rows'], [])
        self.assertIn('A', result['text'])

    def test_inner_row_flag_takes_precedence_only_on_empty_terminator(self):
        for text in ('', 'not-empty'):
            result = read_segments(('P\r', props(1)), ('A\r', props(2, cell=True)),
                                   (text + '\r', props(2, cell=True, row=True)),
                                   ('Q\x07', props(1)), ('\x07', props(1, row=True)), ('\r', b''))
            evidence = result['binary_table_structure']['nested_row_evidence']
            if text:
                self.assertEqual(evidence['rows'], [])
                self.assertGreater(evidence['issue_count'], 0)
            else:
                child = evidence['rows'][0]['cells'][0]['nested_tables'][0]
                self.assertEqual(len(child['row_fragments'][0]['cells']), 1)
                self.assertEqual(child['row_fragments'][0]['cells'][0]['text'], 'A')
                self.assertEqual(child['tables'][0]['row_count'], 1)

    def test_nested_budget_is_explicit(self):
        reader = NestedRows()
        reader.feed('父', '\r', 0, 2, {'depth': 1, 'in_table': 1})
        with patch('wps_nested_tables.MAX_DEPTH', 1), self.assertRaises(ParseError):
            reader.feed('内', '\r', 2, 4, {'depth': 2, 'in_table': 1, 'inner_cell': 1})


if __name__ == '__main__':
    unittest.main()
